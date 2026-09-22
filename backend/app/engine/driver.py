"""공용 전략 드라이버 — 페이퍼 세션과 실행기 세션이 같은 시뮬레이션 코어를 돈다.

DB·리더보드·주문 명령은 모른다. 레그(종목별 sim)·틱·봉·체결 요약·상태 저장/복구·웜업만 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .schema import Macro
from .stepper import EXIT_SIDES, Fill


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def sim_state(sim) -> dict:
    """state() 가 없는 시뮬레이터(테스트 더미 등)에는 '포지션 없음' 기본값."""
    getter = getattr(sim, "state", None)
    if getter is None:
        return {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    return getter()


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

    # -- 틱 --------------------------------------------------------------
    def tick(self, price: float, ts: Optional[datetime] = None, symbol: Optional[str] = None) -> Optional[Fill]:
        leg = self.leg_for(symbol)
        leg.last_price = price
        fill = leg.sim.step(price, ts)
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
        t_ms, o, h, l, c = candle
        ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc) if t_ms else datetime.now(timezone.utc)
        return int(on_candle(float(o), float(h), float(l), float(c), ts))

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
                         "entry_price": round(st["entry_price"], 4), "last_price": round(leg.last_price, 4),
                         "in_position": bool(st["in_position"])})
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
            "last_price": round(self.last_price, 4), "checkpoint_ms": self._now_ms(), "legs": legs,
        }

    def restore(self, state: dict, *, leg_equity: Dict[str, float], total_equity: float) -> None:
        """체크포인트 상태로 각 레그의 sim 을 되살린다. 캔들형은 warmup() 뒤에 불러야 한다(웜업이 장부를 비운다)."""
        leg_state = {leg.get("symbol"): leg for leg in (state.get("legs") or [])}
        for leg in self.legs:
            equity = leg_equity.get(leg.symbol) if self.is_portfolio() else total_equity
            if not equity or equity <= 0:
                equity = leg.initial
            st = leg_state.get(leg.symbol) or {}
            restore = getattr(leg.sim, "restore", None)
            if restore is not None:
                restore(equity, in_position=bool(st.get("in_position")), qty=float(st.get("qty") or 0.0),
                        entry_price=float(st.get("entry_price") or 0.0), last_price=float(st.get("last_price") or 0.0),
                        cooldown_until_ms=state.get("cooldown_until_ms"))
            leg.equity = float(equity)
            leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
            leg.last_price = float(st.get("last_price") or 0.0)
        self.aggregate()

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
