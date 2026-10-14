"""공용 전략 드라이버 — 페이퍼 세션과 실행기 세션이 같은 시뮬레이션 코어를 돈다.

DB·리더보드·주문 명령은 모른다. 레그(종목별 sim)·틱·봉·체결 요약·상태 저장/복구·웜업만 한다.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .schema import Macro
from .stepper import EXIT_SIDES, Fill


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _price(value: float) -> float:
    """체크포인트에 싣는 가격. 소수 자리로 자르면 아주 싼 코인(PEPEUSDT 0.0000089)이 0 이 되고, 재기동 복구가
    '진입가 0 = 포지션 없음' 으로 읽어 서버 전략이 보유를 잊는다. 유효숫자 10자리로 줄인다."""
    return float(f"{float(value):.10g}")


def sim_strategy_state(sim) -> dict:
    """봉에서 다시 못 구하는 전략 상태(E 의 기준가·고점·발동). 없는 sim 은 빈 칸."""
    getter = getattr(sim, "strategy_state", None)
    return dict(getter() or {}) if getter is not None else {}


def sim_state(sim) -> dict:
    """state() 가 없는 시뮬레이터(테스트 더미 등)에는 '포지션 없음' 기본값."""
    getter = getattr(sim, "state", None)
    if getter is None:
        return {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    return getter()


def _inner_sim(sim):
    """실시간 봉 래퍼(LiveCandleSim)면 안쪽 sim. 아니면 그대로."""
    return getattr(sim, "inner", sim)


class Leg:
    """One symbol of a session: its own sim and its share of the capital."""

    __slots__ = ("symbol", "sim", "initial", "last_price", "equity", "ret", "liquidations", "liquidated_loss", "replay_prices")

    def __init__(self, symbol: str, sim, initial: float):
        self.symbol = symbol
        self.sim = sim
        self.initial = initial
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.replay_prices: List[float] = []

    def view(self) -> dict:
        return {
            "symbol": self.symbol,
            "virtual_balance": round(self.initial, 2),
            "current_equity": round(self.equity, 2),
            "current_return": round(self.ret, 4),
            "last_price": round(self.last_price, 4),
            "liquidations": self.liquidations,
        }


class StrategyDriver:
    def __init__(self, legs: List[Leg], initial: float, *, macro: Optional[Macro] = None, now_ms=_now_ms) -> None:
        if not legs:
            raise ValueError("at least one leg")
        self.legs = legs
        self.initial = initial
        self.macro = macro
        self._now_ms = now_ms
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        # 체결 요약 — 리더보드 행 상태·실행기 로그가 읽는다.
        self.trade_count = 0
        self.last_fill: Optional[dict] = None
        # 종목별 진입 시점 누적 수익률 — 포트폴리오에서 다른 레그와 섞이지 않게 심볼로 나눈다.
        self.entry_returns: Dict[str, float] = {}
        # 진입 체결 직전 상태(되돌리기용). 실행기 세션에서 진입 주문이 실제로 나가지 않았을 때 서버 전략을
        # '사기 전' 으로 되돌린다 — 그러지 않으면 서버만 보유 중이 되어 다시 사지 않고 없는 물량을 팔려 한다.
        # 봉 전략은 sim 이 진입마다 그 직전 상태를 남긴다(한 봉의 둘째 · 셋째 진입도 각자의 직전 상태가 있다).
        # 틱 하나에 체결 하나인 sim(A · B · C)은 틱 직전 상태가 곧 그 체결의 직전 상태다.
        # 실행기 드라이버만 켠다(enable_fill_snapshots) — 모의 세션은 상태를 복사할 이유가 없다.
        self.track_fill_snapshots = False
        self.last_fill_snapshot: Optional[dict] = None
        # 되돌린 뒤 다시 진입하기 전까지 기다리는 시각(ms). 체크포인트에 실려 재기동에도 이어진다.
        self.entry_hold_until_ms: Optional[int] = None
        # 되돌린 횟수 — 저장 중에 되돌리기가 끼어들었는지 저장하는 쪽이 알아보게 한다(runner_engine._tick_once).
        self.rollbacks = 0

    # -- 접근자 -----------------------------------------------------------
    @property
    def symbol(self) -> str:
        return self.legs[0].symbol

    @property
    def sim(self):
        return self.legs[0].sim

    @property
    def symbols(self) -> List[str]:
        return [leg.symbol for leg in self.legs]

    def is_portfolio(self) -> bool:
        return len(self.legs) > 1

    def leg_for(self, symbol: Optional[str]) -> Leg:
        if symbol is None:
            return self.legs[0]
        for leg in self.legs:
            if leg.symbol == symbol:
                return leg
        raise KeyError(symbol)

    # -- 체결 직전 상태 -----------------------------------------------------
    def enable_fill_snapshots(self) -> None:
        """진입 체결마다 직전 상태를 잡는다(실행기 드라이버). 봉 전략 sim 에도 진입 기록을 켠다."""
        self.track_fill_snapshots = True
        for leg in self.legs:
            track = getattr(leg.sim, "track_entry_marks", None)
            if track is not None:
                track()

    def _snapshot_from_mark(self, leg: "Leg", mark: dict) -> dict:
        """sim 이 남긴 진입 직전 상태로 드라이버 스냅샷(snapshot() 과 같은 모양)을 만든다.

        장부 · 전략 상태는 그 진입 직전 것, 체결 요약(체결 수 · 마지막 체결)은 지금 것 — 이 체결을 아직 세기 전이고,
        같은 봉의 앞 체결은 이미 꺼내 세었다. 다른 레그는 지금 값 그대로다.
        """
        state = self.state()
        sim = mark["sim"]
        for row in state["legs"]:
            if row["symbol"] != leg.symbol:
                continue
            row.update(qty=float(sim["qty"]), dir=sim["dir"], entry_price=float(sim["entry_price"]),
                       last_price=float(mark["last_price"]), in_position=bool(sim["in_position"]))
            row.pop("strategy", None)
            if mark.get("strategy"):
                row["strategy"] = copy.deepcopy(mark["strategy"])
        state["in_position"] = any(row["in_position"] for row in state["legs"])
        state["cooldown_until_ms"] = sim.get("cooldown_until_ms")
        leg_equity = {other.symbol: (float(mark["equity"]) if other is leg else float(other.sim.equity(other.last_price)))
                      for other in self.legs}
        snap = {"state": state, "equity": float(sum(leg_equity.values())), "leg_equity": leg_equity,
                "trade_count": int(self.trade_count), "last_fill": copy.deepcopy(self.last_fill),
                "entry_returns": dict(self.entry_returns)}
        if mark.get("grid_lot"):
            snap["grid_lot"] = {**mark["grid_lot"], "symbol": leg.symbol}
        return snap

    def undo_grid_lot(self, info: dict, *, retry_at_ms: Optional[int] = None) -> bool:
        """그리드 칸 매수 하나만 되돌린다 — 그 칸 롯을 빼고 산 돈을 돌린다. 다른 칸의 체결 · 큐는 그대로 둔다.

        그 롯이 이미 팔렸거나 바뀌었으면 False. ``retry_at_ms`` 전에는 새 매수를 하지 않는다(재시도 대기).
        """
        try:
            leg = self.leg_for(info.get("symbol"))
        except KeyError:
            return False
        undo = getattr(_inner_sim(leg.sim), "undo_lot", None)
        if undo is None or not undo(info):
            return False
        if retry_at_ms:
            self.hold_entries_until(retry_at_ms)
        if leg.last_price > 0:
            leg.equity = leg.sim.equity(leg.last_price)
            leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
            self.aggregate()
        self.rollbacks += 1
        return True

    def snapshot(self) -> dict:
        """되돌리기에 쓰는 지금 상태 — 장부 · 전략 상태(state) 와 자산 · 체결 요약.

        state() 는 화면용으로 반올림한다(진입가 소수 4자리 — 아주 싼 코인은 0 이 된다). 되돌리기는 정확한
        값이어야 해서 수량 · 진입가 · 기준가를 원래 값으로 덮고, 자산도 그 기준가로 다시 잰다 — restore() 가
        현금을 ``자산 - 포지션 평가액`` 으로 되짚으므로 둘이 같은 가격이어야 현금이 그대로 돌아온다.
        """
        state = self.state()
        leg_equity = {}
        for leg, row in zip(self.legs, state["legs"]):
            exact = sim_state(leg.sim)
            row["qty"] = float(exact["qty"])
            row["entry_price"] = float(exact["entry_price"])
            row["last_price"] = float(leg.last_price)
            equity = getattr(leg.sim, "equity", None)
            leg_equity[leg.symbol] = float(equity(leg.last_price)) if equity is not None else float(leg.equity)
        return {"state": state, "equity": float(sum(leg_equity.values())), "leg_equity": leg_equity,
                "trade_count": int(self.trade_count), "last_fill": copy.deepcopy(self.last_fill),
                "entry_returns": dict(self.entry_returns)}

    def rollback(self, snapshot: dict, *, retry_at_ms: Optional[int] = None) -> None:
        """체결 직전 상태로 되돌린다. ``retry_at_ms`` 전에는 새 진입 · 추가매수를 하지 않는다(재시도 대기).

        봉 큐에 남은 체결(같은 봉에서 나온 뒤엣것)은 되돌린 시간선의 것이라 버린다. 웜업이 채운 지표는
        그대로 둔다 — restore() 는 장부와 전략 상태만 바꾼다.
        """
        state = copy.deepcopy(snapshot.get("state") or {})
        if retry_at_ms:
            state["entry_hold_until_ms"] = int(retry_at_ms)
        # restore() 는 보유 중이면 진입 시각을 '지금' 으로 둔다(재기동용). 추가매수만 되돌리는 경우 처음 진입한 시각은
        # 그대로여야 한다 — 아니면 최대 보유 시간이 다시 세어져 실행기(원래 시각 기준)와 다른 때에 강제 청산한다.
        clocks = [getattr(_inner_sim(leg.sim), "_entry_time", None) for leg in self.legs]
        self.restore(state, leg_equity=dict(snapshot.get("leg_equity") or {}),
                     total_equity=float(snapshot.get("equity") or self.initial))
        for leg, clock in zip(self.legs, clocks):
            inner = _inner_sim(leg.sim)
            if clock is not None and hasattr(inner, "_entry_time") and sim_state(leg.sim)["in_position"]:
                inner._entry_time = clock
        for leg in self.legs:
            discard = getattr(leg.sim, "discard_queued", None)
            if discard is not None:
                discard()
        self.trade_count = int(snapshot.get("trade_count") or 0)
        self.last_fill = copy.deepcopy(snapshot.get("last_fill"))
        self.entry_returns = dict(snapshot.get("entry_returns") or {})
        self.last_fill_snapshot = None
        self.rollbacks += 1

    # -- 틱 --------------------------------------------------------------
    def tick(self, price: float, ts: Optional[datetime] = None, symbol: Optional[str] = None) -> Optional[Fill]:
        leg = self.leg_for(symbol)
        leg.last_price = price
        track = self.track_fill_snapshots
        marked = track and hasattr(leg.sim, "last_mark")
        # 진입 기록이 없는 sim(A · B · C)은 틱 하나에 체결 하나 — 틱 직전 상태가 그 체결의 직전 상태다.
        before = self.snapshot() if track and not marked else None
        fill = leg.sim.step(price, ts)
        if track:
            if fill is None or fill.side in EXIT_SIDES:
                self.last_fill_snapshot = None  # 청산은 되돌리지 않는다(실패하면 다시 보낸다)
            elif marked:
                mark = leg.sim.last_mark
                self.last_fill_snapshot = self._snapshot_from_mark(leg, mark) if mark else None
            else:
                self.last_fill_snapshot = before
        leg.equity = leg.sim.equity(price)
        leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
        leg.liquidations = getattr(leg.sim, "liquidations", 0)
        leg.liquidated_loss = getattr(leg.sim, "liquidated_loss", 0.0)
        self.aggregate()
        if fill is not None:
            self.note_fill(fill, leg.symbol)
        return fill

    def aggregate(self) -> None:
        self.last_price = self.legs[0].last_price
        self.equity = sum(leg.equity for leg in self.legs)
        self.ret = (self.equity - self.initial) / self.initial * 100.0
        self.liquidations = sum(leg.liquidations for leg in self.legs)
        self.liquidated_loss = sum(leg.liquidated_loss for leg in self.legs)

    def note_fill(self, fill: Fill, symbol: str) -> None:
        self.trade_count += 1
        kind = ""
        if fill.side in EXIT_SIDES:
            entry_return = self.entry_returns.pop(symbol, None)
            kind = "exit" if entry_return is None else ("tp" if fill.return_pct > entry_return else "sl")
        else:
            self.entry_returns[symbol] = float(fill.return_pct)
        self.last_fill = {"ms": self._now_ms(), "side": fill.side, "return": round(float(fill.return_pct), 4), "kind": kind, "symbol": symbol}

    # -- 봉 --------------------------------------------------------------
    def is_candle_based(self) -> bool:
        return any(hasattr(leg.sim, "on_candle") for leg in self.legs)

    def candle_keys(self) -> List[tuple]:
        if self.macro is None or not self.is_candle_based():
            return []
        interval, market = self.macro.candle_interval, self.macro.resolved_market()
        return [(leg.symbol, interval, market) for leg in self.legs if hasattr(leg.sim, "on_candle")]

    def push_candle(self, symbol: str, candle) -> int:
        leg = self.leg_for(symbol)
        on_candle = getattr(leg.sim, "on_candle", None)
        if on_candle is None:
            return 0
        # (t, o, h, l, c) 와 (t, o, h, l, c, v) 를 모두 받는다 — 테스트 더미가 튜플이고,
        # 거래량을 싣지 않는 옛 호출이 남아 있다.
        t_ms, o, h, l, c = candle[0], candle[1], candle[2], candle[3], candle[4]
        volume = float(candle[5]) if len(candle) > 5 and candle[5] is not None else None
        ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc) if t_ms else datetime.now(timezone.utc)
        return int(on_candle(float(o), float(h), float(l), float(c), ts, volume=volume))

    def warmup(self, candles_by_symbol: Dict[str, list]) -> None:
        for leg in self.legs:
            warm = getattr(leg.sim, "warmup", None)
            if warm is not None and candles_by_symbol.get(leg.symbol):
                warm(candles_by_symbol[leg.symbol])

    # -- 상태 --------------------------------------------------------------
    def state(self) -> dict:
        legs, cooldowns = [], []
        for leg in self.legs:
            st = sim_state(leg.sim)
            legs.append({"symbol": leg.symbol, "qty": round(st["qty"], 8), "dir": st["dir"],
                         "entry_price": _price(st["entry_price"]), "last_price": _price(leg.last_price),
                         "in_position": bool(st["in_position"])})
            if "dca_next_buy_ms" in st:
                legs[-1]["dca_next_buy_ms"] = st["dca_next_buy_ms"]
                legs[-1]["dca_stopped"] = bool(st.get("dca_stopped"))
            strategy = sim_strategy_state(leg.sim)
            if strategy:
                # 비어 있으면 아예 안 싣는다 — 쓰지 않는 규칙의 state_json 을 바꾸지 않는다.
                legs[-1]["strategy"] = strategy
            if st["cooldown_until_ms"] is not None:
                cooldowns.append(int(st["cooldown_until_ms"]))
        last = self.last_fill or {}
        return {
            "in_position": any(l["in_position"] for l in legs),
            "halted_today": any(sim_state(leg.sim)["halted_today"] for leg in self.legs),
            "cooldown_until_ms": max(cooldowns) if cooldowns else None,
            "trade_count": self.trade_count,
            "last_fill_ms": last.get("ms"), "last_fill_side": last.get("side", ""),
            "last_fill_return": last.get("return"), "last_fill_kind": last.get("kind", ""),
            "last_price": _price(self.last_price), "checkpoint_ms": self._now_ms(), "legs": legs,
            # 대기가 없으면 칸을 싣지 않는다 — 모의 세션의 state_json 을 바꾸지 않는다.
            **({"entry_hold_until_ms": int(self.entry_hold_until_ms)} if self.entry_hold_until_ms else {}),
        }

    def restore(self, state: dict, *, leg_equity: Dict[str, float], total_equity: float) -> None:
        """체크포인트 상태로 각 레그의 sim 을 되살린다. 캔들형은 warmup() 뒤에 불러야 한다(웜업이 장부를 비운다)."""
        leg_state = {leg.get("symbol"): leg for leg in (state.get("legs") or [])}
        for leg in self.legs:
            equity = leg_equity.get(leg.symbol) if self.is_portfolio() else total_equity
            if not equity or equity <= 0:
                equity = leg.initial
            st = leg_state.get(leg.symbol) or {}
            # 전략 상태는 **장부 앞**에서 되돌린다 — restore() 가 현금을 되짚을 때 side 로
            # 분기하므로(숏은 식이 다르다), K 의 방어 전환 단계를 먼저 바로잡아야 한다.
            # 옛 state_json 에는 이 칸이 없고, 그때는 예전처럼 웜업이 남긴 값을 쓴다.
            restore_strategy = getattr(leg.sim, "restore_strategy", None)
            if restore_strategy is not None:
                restore_strategy(st.get("strategy"))
            restore = getattr(leg.sim, "restore", None)
            if restore is not None:
                restore(equity, in_position=bool(st.get("in_position")), qty=float(st.get("qty") or 0.0),
                        entry_price=float(st.get("entry_price") or 0.0), last_price=float(st.get("last_price") or 0.0),
                        cooldown_until_ms=state.get("cooldown_until_ms"))
            # 롯을 참조하는 전략 상태(D 의 칸별 롯)는 장부 **뒤**에 — restore() 가 여러 롯을
            # 평단 하나로 합치므로, 합친 뒤에 칸별로 다시 깔아야 한다.
            restore_lots = getattr(leg.sim, "restore_strategy_lots", None)
            if restore_lots is not None:
                restore_lots(st.get("strategy"))
            restore_schedule = getattr(leg.sim, "restore_dca_schedule", None)
            if restore_schedule is not None and "dca_next_buy_ms" in st:
                restore_schedule(st["dca_next_buy_ms"], stopped=bool(st.get("dca_stopped")))
            leg.equity = float(equity)
            leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
            leg.last_price = float(st.get("last_price") or 0.0)
        self.hold_entries_until(state.get("entry_hold_until_ms"))
        self.aggregate()

    def hold_entries_until(self, until_ms: Optional[int]) -> None:
        """모든 레그의 새 진입(추가매수 포함)을 ``until_ms`` 전까지 막는다. None 이면 푼다."""
        self.entry_hold_until_ms = int(until_ms) if until_ms else None
        for leg in self.legs:
            hold = getattr(leg.sim, "hold_entries_until", None)
            if hold is not None:
                hold(self.entry_hold_until_ms)

    def restore_fills(self, trades: List[dict]) -> None:
        """체결 요약을 DB 체결 행으로 다시 센다 — 메모리 카운터는 프로세스와 함께 사라졌다."""
        self.trade_count = len(trades)
        entry_returns: Dict[str, float] = {}
        last_kind = ""
        for t in trades:
            sym = t.get("symbol") or self.symbol
            if t["side"] in EXIT_SIDES:
                base = entry_returns.pop(sym, None)
                last_kind = "exit" if base is None else ("tp" if t["return_at_trade"] > base else "sl")
            else:
                entry_returns[sym] = t["return_at_trade"]
                last_kind = ""
        self.entry_returns = entry_returns
        self.last_fill = None
        if trades:
            last = trades[-1]
            self.last_fill = {"ms": _iso_ms(last.get("ts", "")), "side": last["side"],
                              "return": round(last["return_at_trade"], 4), "kind": last_kind,
                              "symbol": last.get("symbol") or self.symbol}


def _iso_ms(ts: str) -> int:
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0
