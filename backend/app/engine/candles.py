"""Incremental candle-driven execution machines for rule types D~J.

Single source of truth (same idea as ``stepper.py`` for A/B/C): the backtest
feeds these one *closed candle* at a time; paper trading aggregates live ticks
into candles and feeds them the same way. Because everything is driven by closed
candles, look-ahead bias is structurally impossible for the indicator types:

  * Indicator types (F/G/J): the signal is decided on a bar's CLOSE and the fill
    happens at the NEXT bar's OPEN (a one-bar ``_pending`` order). Never on the
    in-progress bar. Live (``LiveCandleSim``) executes that pending order on the
    first tick after the closed bar arrives via ``execute_pending`` — same "next
    open" semantics, seconds instead of a full interval late.
  * Price-level types (D/E/H/I): levels are known in advance, so fills happen
    intrabar against the bar high/low. When a long bar touches both a take-profit
    (high) and a stop-loss (low) in the same candle, the stop wins (conservative)
    and the bar is counted in ``same_bar_sl``.

All sims expose the same tiny contract used by both drivers::

    fills = sim.on_candle(o, h, l, c, ts)   # list[Fill]
    eq    = sim.equity(mark_price)          # float
"""
from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, List, Optional

from ..exchanges import min_order_floor
from .fill_ledger import Ledger, buy_numbers, sell_numbers
from .leverage import liquidation_price
from .schema import Macro, PositionSide, RuleType
from .stepper import Fill, _fill_meta, buy_fill, sell_fill

_INTERVAL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _report_time(report: dict) -> datetime:
    """체결 보고의 실행기 시각(ms). 없으면 지금."""
    try:
        ms = int(report.get("ts_ms") or 0)
    except (TypeError, ValueError):
        ms = 0
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms > 0 else datetime.now(timezone.utc)


def _ms_time(ms) -> datetime:
    return _report_time({"ts_ms": ms})


def _close(a, b) -> bool:
    """롯 값이 같은가 — 체크포인트(JSON) 를 거쳐도 같은 float 지만 여유를 조금 둔다."""
    a, b = float(a or 0.0), float(b or 0.0)
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _interval_seconds(interval: str) -> int:
    """'5m' · '1h' · '1d' 같은 봉 간격을 초로. 모르는 모양이면 0."""
    text = str(interval or "").strip()
    unit = _INTERVAL_UNIT_SECONDS.get(text[-1:], 0)
    return int(text[:-1]) * unit if unit and text[:-1].isdigit() else 0


# --- incremental indicators (shared by backtest + paper) ----------------
class RSIState:
    """Wilder's RSI, updated one close at a time. Returns None until warmed."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._prev: Optional[float] = None
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._count = 0

    def update(self, close: float) -> Optional[float]:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._count += 1
        if self._count <= self.period:
            self._avg_gain += gain / self.period
            self._avg_loss += loss / self.period
            if self._count < self.period:
                return None
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


class BollingerState:
    """Rolling mean/stdev bands over a fixed window of closes."""

    def __init__(self, period: int, num_std: float) -> None:
        self.period = period
        self.num_std = num_std
        self._win: Deque[float] = deque(maxlen=period)

    def update(self, close: float) -> Optional[tuple[float, float, float]]:
        self._win.append(close)
        if len(self._win) < self.period:
            return None
        mean = sum(self._win) / self.period
        var = sum((x - mean) ** 2 for x in self._win) / self.period
        sd = var ** 0.5
        return mean, mean + self.num_std * sd, mean - self.num_std * sd


class MAState:
    """Simple or exponential moving average, one close at a time."""

    def __init__(self, ma_type: str, period: int) -> None:
        self.ma_type = ma_type
        self.period = period
        self._win: Deque[float] = deque(maxlen=period)
        self._ema: Optional[float] = None
        self._k = 2.0 / (period + 1.0)
        self._count = 0

    def update(self, close: float) -> Optional[float]:
        self._count += 1
        if self.ma_type == "EMA":
            if self._ema is None:
                self._win.append(close)
                if len(self._win) < self.period:
                    return None
                self._ema = sum(self._win) / self.period  # seed with SMA
                return self._ema
            self._ema = close * self._k + self._ema * (1.0 - self._k)
            return self._ema
        self._win.append(close)
        if len(self._win) < self.period:
            return None
        return sum(self._win) / self.period


# --- book keeping (long lots; short only used by single-position types) --
@dataclass
class _Lot:
    qty: float
    fill: float  # entry fill price (already slippage-adjusted)
    margin: float  # equity committed to this lot (notional / leverage; == notional at 1x)
    ref: str = ""  # 체결 기준 장부 세션만: 이 롯을 만든 주문 번호 — 체결 보고가 그 롯을 실제 값으로 바꾼다


class CandleSim:
    """Base: cash accounting, common risk controls, Fill construction.

    Subclasses implement ``_strategy(o, h, l, c, ts, fills)`` and use the shared
    ``_open_long`` / ``_close_all`` / stop-loss helpers.
    """

    def __init__(self, macro: Macro, initial_capital: Optional[float] = None) -> None:
        self.rule_type = macro.rule_type
        self.side = macro.position_side
        self.comm = macro.fees.commission_pct
        self.slip = macro.fees.slippage_pct
        self.funding = macro.fees.funding_pct
        self.invest_ratio = macro.risk.invest_ratio
        self.sl = macro.risk.stop_loss_pct
        self.daily_max_loss = macro.risk.daily_max_loss_pct
        self.max_holding_hours = macro.risk.max_holding_hours
        self.cooldown_minutes = macro.risk.cooldown_minutes
        self.leverage = int(macro.leverage or 1)  # 1 == spot, no liquidation
        self.p = dict(macro.params)

        self.initial_capital = float(
            initial_capital if initial_capital is not None else (macro.initial_capital or 1_000_000.0)
        )
        self.cash = self.initial_capital
        self.lots: List[_Lot] = []
        self.closed_trades: List[float] = []
        self.same_bar_sl = 0
        # Isolated-margin liquidation stats (leverage > 1).
        self.liquidations = 0
        self.liquidated_loss = 0.0

        # common-risk state
        self._day: Optional[str] = None
        self._day_start_equity = self.initial_capital
        self._halted_day: Optional[str] = None
        self._cooldown_until: Optional[datetime] = None
        self._entry_time: Optional[datetime] = None
        # 실행기 진입 재시도 대기(stepper.PositionSim 과 같은 뜻) — 백테스트·모의는 쓰지 않는다.
        self._entry_hold_until: Optional[datetime] = None
        # 봉의 시각은 '여는' 시각인데 실시간 판단은 봉이 닫힌 뒤에 한다. 대기는 닫힌 시각으로 견줘야
        # 대기가 끝난 뒤 처음 닫히는 봉에서 다시 판단한다(여는 시각으로 견주면 한 봉을 통째로 더 건너뛴다).
        self._candle_seconds = _interval_seconds(getattr(macro, "candle_interval", ""))
        self._decision_at: Optional[datetime] = None
        # 실행기 세션만: 진입마다 '그 진입 직전' 상태를 (체결, 상태) 로 남긴다 — 한 봉이 체결을 여럿 내도 둘째
        # 진입부터 정확히 되돌릴 수 있게(봉 직전 상태로 되돌리면 앞 체결까지 지워진다). None 이면 끈 상태 —
        # 백테스트 · 모의는 끄고 돈다. LiveCandleSim 이 체결과 짝지어 꺼내 간다.
        self._entry_marks: Optional[list] = None
        self.stopped = False  # hard stop (band exit / reenter disabled)
        # 진입 필터 — 없으면 None. on_candle 이 봉이 끝난 뒤 갱신하고 _entry_blocked 가 묻는다.
        from .entry_filter import make_filter  # 늦은 import: entry_filter -> candles 순환 방지
        self.entry_filter = make_filter(macro)
        # 묶음 한도 — 묶음을 조립하는 쪽(백테스트 루프 · paper.start_session)이 밖에서 꽂는다.
        # 심은 자기가 묶음의 일부인지 모른다.
        self.bundle_gate = None
        # 체결 기준 장부 — 실행기 정책 세션만 켠다(enable_fill_ledger). None 이면 지금 동작 그대로(백테스트 · 모의).
        self.ledger: Optional[Ledger] = None
        self.exchange = getattr(macro, "exchange", "binance") or "binance"  # 재주문 불가 금액(최소 주문)을 고른다
        self.market = macro.resolved_market()

    # -- 체결 기준 장부(fill_ledger_v1) ----------------------------------------
    # 신호 순간에는 가상 체결로 장부를 바꾸고(진입 · 현금 예약), 실행기가 보고한 실제 체결로 그 주문의 몫을 고친다.
    # 1단계는 롱 · 현물 · 레버리지 1 의 마틴게일만 — 다른 규칙은 포지션 끝 처리 · 전략 상태를 하나씩 확인한 뒤 연다.
    supports_fill_ledger = False

    def enable_fill_ledger(self) -> None:
        if not self.supports_fill_ledger:
            raise ValueError(f"fill ledger is not available for rule {self.rule_type}")
        if self.side is not PositionSide.LONG or self.leverage != 1:
            raise ValueError("fill ledger needs a long spot book at leverage 1")
        self.ledger = Ledger()

    def fold(self, fills=(), resolutions=()) -> dict:
        """실행기가 보고한 체결 · 끝난 주문을 장부에 접는다. 같은 체결 키는 한 번만.

        순서: ① 체결 없이 끝난 진입(가상 롯을 뺀다) → ② 체결(보고 순서 그대로) → ③ 나머지 끝난 주문.
        ①을 먼저 하는 까닭 — 로컬 손절이 판 수량은 실행기가 실제로 가진 것뿐이라, 안 산 진입의 가상 롯이 남은 채로
        팔면 그 가상 몫이 보유로 남는다. 체결이 있는 주문은 그 체결이 장부에 들어간 뒤에 정리한다.
        돌려주는 값: 접은 키 · 정리한 주문 번호 · 사람이 읽을 사건(``events``) · 진입 대기(``hold_until_ms``).
        """
        ledger = self.ledger
        if ledger is None:
            raise RuntimeError("fill ledger is off")
        out = {"applied": [], "resolved": [], "events": [], "hold_until_ms": None}
        fill_refs = {str(report.get("ref") or "") for report in fills}

        def unfilled_entry(item) -> bool:
            order = ledger.orders.get(str(item.get("ref") or ""))
            return (order is not None and order.get("side") == "buy" and not order.get("actual")
                    and str(item.get("ref") or "") not in fill_refs)

        first = [item for item in resolutions if unfilled_entry(item)]
        for item in first:
            self._fold_resolution(item, out)
        for report in fills:
            key = str(report.get("key") or "")
            if not key or ledger.seen(key):
                continue
            ledger.remember(key)
            out["applied"].append(key)
            side = str(report.get("side") or "")
            if side == "buy":
                event = self._fold_buy(str(report.get("ref") or ""), report)
            elif side == "sell":
                event = self._fold_sell(str(report.get("ref") or ""), report)
            else:
                continue  # 모르는 방향 — 장부에 넣지 않는다(키는 기억해 같은 행을 다시 보지 않는다)
            if event:
                out["events"].append(event)
        for item in resolutions:
            if item not in first:
                self._fold_resolution(item, out)
        return out

    def _fold_resolution(self, item: dict, out: dict) -> None:
        ref = str(item.get("ref") or "")
        if ref not in self.ledger.orders:
            return
        status = str(item.get("status") or "")
        event = self._resolve_order(ref, status)
        out["resolved"].append(ref)
        if event:
            out["events"].append(event)
        hold = item.get("hold_until_ms")
        if hold and status in ("failed", "expired"):
            out["hold_until_ms"] = max(int(hold), int(out["hold_until_ms"] or 0))

    def _fold_buy(self, ref: str, report: dict) -> Optional[dict]:
        """매수 체결 한 건 — 그 주문의 가상 롯을 실제 값으로 바꾸거나(첫 체결), 같은 롯에 더한다(뒤 체결).

        이미 정리한 주문의 체결(늦은 체결)도 넣는다 — 실행기가 산 것은 산 것이다. 비어 있던 장부면 새 포지션이 된다.
        """
        numbers = buy_numbers(report)
        if numbers is None:
            return None
        order = self.ledger.orders.get(ref)
        avg_before = self.avg_entry()
        lot = next((lot for lot in self.lots if ref and lot.ref == ref), None)
        late = order is None
        opens = not self._confirmed_lots() and (late or (order.get("kind") == "base" and not order.get("actual")))
        if opens and self.ledger.dust:
            # 결정 3: 팔 수 없던 잔량을 새 포지션 평단에 합친다(원가 가중 평균) — 새 매수분만 익절인데 합치면 손실인 일이 없게.
            dust = self.ledger.dust
            self.lots.insert(0, _Lot(qty=float(dust["qty"]), fill=float(dust["fill"]), margin=float(dust["margin"]),
                                     ref="dust"))
            self.ledger.dust = None
        if order is not None and not order.get("actual") and lot is not None:
            self.cash += float(order["margin"]) + float(order["fee"])  # 신호 때 잡아 둔 가상 금액을 풀고
            lot.qty, lot.fill, lot.margin = numbers.qty, numbers.price, numbers.qty * numbers.price
        elif lot is not None:
            total = lot.qty + numbers.qty
            lot.fill = (lot.qty * lot.fill + numbers.qty * numbers.price) / total
            lot.qty = total
            lot.margin += numbers.qty * numbers.price
        else:
            lot = _Lot(qty=numbers.qty, fill=numbers.price, margin=numbers.qty * numbers.price, ref=ref or "late")
            self.lots.append(lot)
            if opens:
                self._entry_time = _report_time(report)
                self._on_base_filled(numbers.price)  # 비어 있던 장부에 산 것 — 새 포지션의 첫 매수가
        self.cash -= numbers.cash_out
        if order is not None:
            order["actual"] = True
            order["filled_qty"] = float(order.get("filled_qty") or 0.0) + numbers.qty
            order["spent"] = float(order.get("spent") or 0.0) + numbers.cash_out
            if order.get("kind") == "base":
                self._on_base_filled(lot.fill)
        else:
            self._late_entry_spent(ref, numbers.cash_out)
        return {"kind": "entry_filled", "ref": ref, "late": late,
                "virtual_price": order["price"] if order else None, "price": numbers.price, "qty": numbers.qty,
                "fee": numbers.fee, "avg_before": avg_before, "avg_after": self.avg_entry()}

    def _resolve_order(self, ref: str, status: str) -> Optional[dict]:
        """끝난 주문 하나를 정리한다(명령의 끝 상태 acked · failed · expired · cancelled).

        진입: 체결이 하나도 없었으면 가상 롯을 빼고 잡아 둔 돈을 돌린다. 이전 포지션(epoch)의 주문이면 차수는 건드리지 않는다.
        """
        ledger = self.ledger
        order = ledger.orders[ref]
        if order.get("side") == "sell":
            # 청산 주문이 끝났는데 포지션이 남았다(0 체결 · 일부 체결로 끝남) — 대기만 푼다. 다음 봉에서 다시 판단한다.
            # 다 팔렸다면 그 체결이 이미 포지션을 끝내며 이 주문을 정리했다(여기 오지 않는다).
            ledger.close_order(ref)
            return {"kind": "exit_unfilled" if not order.get("sold") else "exit_partial", "ref": ref,
                    "status": status, "sold": float(order.get("sold") or 0.0), "left": self.total_qty()}
        event = None
        if not order.get("actual"):
            lot = next((lot for lot in self.lots if lot.ref == ref), None)
            if lot is not None:
                self.lots = [other for other in self.lots if other is not lot]
                self.cash += float(order["margin"]) + float(order["fee"])
            if not self.lots:
                self._entry_time = None
                if order.get("kind") == "base":
                    self._reset_position_state()  # 사지 못한 첫 매수 — 포지션은 없었다
            event = {"kind": "entry_unfilled", "ref": ref, "status": status, "order_kind": order.get("kind"),
                     "tier": order.get("tier"), "price": order.get("price")}
        if int(order.get("epoch", ledger.epoch)) == ledger.epoch:
            remaining = float(order["margin"]) - float(order.get("spent") or 0.0)
            reopened = self._entry_left(order, remaining, status)
            if reopened and order.get("actual"):
                event = {"kind": "entry_partial", "ref": ref, "tier": order.get("tier"), "line": order.get("line"),
                         "remaining": remaining}
        ledger.close_order(ref)
        return event

    def _confirmed_lots(self) -> List["_Lot"]:
        """실행기가 실제로 가진 몫 — 보고 전 진입(가상 롯)을 뺀 롯."""
        virtual = {ref for ref, order in self.ledger.orders.items() if order.get("side") == "buy" and not order.get("actual")}
        return [lot for lot in self.lots if lot.ref not in virtual]

    def _fold_sell(self, ref: str, report: dict) -> Optional[dict]:
        """매도 체결 한 건(서버 청산 명령이든 실행기 로컬 손절이든) — 실제로 가진 몫을 같은 비율로 줄인다.

        남은 평단은 그대로다(실행기 장부와 같은 규칙). 남은 평가액이 재주문 불가 금액 미만이면 포지션 끝 — 남은 것은 먼지.
        """
        numbers = sell_numbers(report)
        if numbers is None:
            return None
        ledger = self.ledger
        held = self._confirmed_lots()
        total = sum(lot.qty for lot in held)
        if total <= 1e-12:
            return {"kind": "sell_without_position", "ref": ref, "qty": numbers.qty, "price": numbers.price}
        sold = min(numbers.qty, total)
        fraction = sold / total
        pnl = -numbers.fee
        for lot in held:
            qty, margin = lot.qty * fraction, lot.margin * fraction
            self.cash += margin + qty * (numbers.price - lot.fill)
            pnl += qty * (numbers.price - lot.fill)
            lot.qty -= qty
            lot.margin -= margin
        self.cash -= numbers.fee
        self.closed_trades.append(pnl)
        held_ids = {id(lot) for lot in held}
        self.lots = [lot for lot in self.lots if lot.qty > 1e-12 or id(lot) not in held_ids]
        exit_ref = ref if ref in ledger.orders and ledger.orders[ref].get("side") == "sell" else ledger.exit
        order = ledger.orders.get(exit_ref) if exit_ref else None
        if order is not None:
            order["sold"] = float(order.get("sold") or 0.0) + sold
        left = sum(lot.qty for lot in self._confirmed_lots())
        ended = left <= 1e-12 or left * numbers.price < self._reorder_floor()
        if ended:
            if order is not None:
                is_stop, at = bool(order.get("is_stop")), _ms_time(order.get("signal_ms"))
            else:
                is_stop, at = str(report.get("reason") or "") == "stop_loss", _report_time(report)
            self._end_position(is_stop=is_stop, at=at)
        return {"kind": "exit_filled", "ref": ref, "source": str(report.get("source") or ""), "qty": sold,
                "price": numbers.price, "fee": numbers.fee, "pnl": pnl, "ended": ended,
                "dust_qty": float(ledger.dust["qty"]) if ended and ledger.dust else 0.0, "left": 0.0 if ended else left}

    def _end_position(self, *, is_stop: bool, at: datetime) -> None:
        """다 팔렸다 — 남은 실제 몫은 먼지로 옮기고 포지션 끝 처리를 한다(백테스트의 _close_all 끝과 같은 일)."""
        ledger = self.ledger
        held = self._confirmed_lots()
        rest = [lot for lot in held if lot.qty > 1e-12]
        if rest:
            qty = sum(lot.qty for lot in rest) + float((ledger.dust or {}).get("qty") or 0.0)
            cost = sum(lot.qty * lot.fill for lot in rest) + float((ledger.dust or {}).get("qty") or 0.0) * float(
                (ledger.dust or {}).get("fill") or 0.0)
            margin = sum(lot.margin for lot in rest) + float((ledger.dust or {}).get("margin") or 0.0)
            ledger.dust = {"qty": qty, "fill": cost / qty, "margin": margin}
        held_ids = {id(lot) for lot in held}
        self.lots = [lot for lot in self.lots if id(lot) not in held_ids]
        self._entry_time = None
        if is_stop and self.cooldown_minutes > 0:
            self._cooldown_until = at + timedelta(minutes=self.cooldown_minutes)
        self._after_close()
        if ledger.exit:
            ledger.close_order(ledger.exit)
        ledger.epoch += 1

    def _after_close(self) -> None:
        """포지션이 끝났다(청산이 다 됐다) — 포지션에 묶인 전략 상태를 비운다. H 는 물타기 차수."""

    def _ledger_holds_candle(self, o, h, l, c, ts: datetime, volume) -> bool:
        """미결 주문이 있으면 이 봉을 들고만 있는다(True). 들고 있던 묵은 봉은 판단 없이 진입 필터만 갱신하고 버린다.

        가장 최근 봉 하나만 판단한다 — 재기동 되살리기와 같은 규칙. 묵은 봉의 신호로 뒤늦게 사고팔지 않는다.
        """
        ledger = self.ledger
        t_ms = int(ts.timestamp() * 1000)
        old = ledger.deferred
        if old is not None and int(old[0]) < t_ms:
            if self.entry_filter is not None:
                self.entry_filter.update(float(old[4]), volume=old[5])
            ledger.deferred = None
        if not ledger.unresolved():
            ledger.deferred = None
            return False
        ledger.deferred = [t_ms, float(o), float(h), float(l), float(c), volume]
        return True

    def take_deferred(self) -> Optional[list]:
        """주문이 다 정리됐으면 들고 있던 봉을 내준다(그 봉으로 판단할 차례). 아니면 None."""
        ledger = self.ledger
        if ledger is None or ledger.deferred is None or ledger.unresolved():
            return None
        candle, ledger.deferred = ledger.deferred, None
        return candle

    def _dust_value(self, price: float) -> float:
        dust = self.ledger.dust if self.ledger is not None else None
        if not dust:
            return 0.0
        return float(dust["margin"]) + float(dust["qty"]) * (price - float(dust["fill"]))

    def _entry_left(self, order: dict, remaining: float, status: str) -> bool:
        """덜 찬 진입의 남은 예산 — 차수를 쓰는 규칙(H 물타기)이 그 가격선에 다시 연다. 다시 열었으면 True."""
        return False

    def _late_entry_spent(self, ref: str, spent: float) -> None:
        """정리한 뒤 온 매수 체결이 쓴 돈 — 다시 연 차수의 남은 예산에서 뺀다(H)."""

    def _reorder_floor(self) -> float:
        """새 주문을 낼 수 있는 최소 금액(견적 통화, 여유 포함). 이보다 적은 남은 예산 · 잔량은 재주문 불가다."""
        return float(min_order_floor(self.exchange, self.market) or 0.0)

    def _on_base_filled(self, price: float) -> None:
        """첫 매수가 실제로 체결된 값 — 기준가를 쓰는 규칙(H 물타기선)이 덮어쓴다."""

    def _register_entry(self, lot: "_Lot", fill: Fill, *, margin: float, fee: float, ts: datetime,
                        kind: str, tier: int, line: float) -> None:
        ref = self.ledger.next_ref()
        lot.ref = ref
        fill.order_ref = ref
        self.ledger.orders[ref] = {
            "side": "buy", "kind": kind, "tier": int(tier), "line": float(line), "epoch": self.ledger.epoch,
            "qty": float(lot.qty), "price": float(lot.fill), "margin": float(margin), "fee": float(fee),
            "signal_ms": int(ts.timestamp() * 1000), "actual": False, "filled_qty": 0.0, "spent": 0.0,
        }

    def _ledger_state(self) -> dict:
        return {"ledger": self.ledger.to_state(self.lots)} if self.ledger is not None else {}

    def _restore_ledger(self, state: Optional[dict]) -> None:
        """체크포인트의 장부 칸으로 롯을 번호째 다시 깐다 — restore() 가 합친 평단 하나를 바꿔 놓는다(현금은 같다)."""
        if self.ledger is None or not state:
            return
        lots = self.ledger.load(state.get("ledger"))
        if lots is None:
            return
        self.lots = [_Lot(qty=float(row["qty"]), fill=float(row["fill"]), margin=float(row["margin"]),
                          ref=str(row.get("ref") or "")) for row in lots]
        if self.ledger.dust:
            # restore() 는 현금을 '자산 − 롯 평가액' 으로 되짚었다 — 먼지도 자산에 들어가므로 그 몫을 현금에서 뺀다.
            self.cash -= self._dust_value(float(getattr(self, "_restored_price", 0.0) or self.ledger.dust["fill"]))

    # -- position helpers (long book) ------------------------------------
    def total_qty(self) -> float:
        return sum(l.qty for l in self.lots)

    def in_position(self) -> bool:
        return abs(self.total_qty()) > 1e-12

    def avg_entry(self) -> float:
        q = self.total_qty()
        if q <= 1e-12:
            return 0.0
        return sum(l.qty * l.fill for l in self.lots) / q

    def committed_margin(self) -> float:
        """이 포지션에 묶인 자본. 레버리지 걸린 명목금액이 아니라 실제로 넣은 돈이다.

        1배에서는 ``수량 × 진입가`` 와 같다. 묶음 노출 한도가 이 값을 센다 — "내 자금의 몇
        퍼센트까지 시장에 넣을까" 가 사용자가 뜻하는 것이고, 명목으로 세면 3배에서 한도를
        100% 로 열어 둬도 레그가 사라진다.
        """
        return sum(l.margin for l in self.lots)

    def equity(self, price: float) -> float:
        if self.side is PositionSide.SHORT:
            # single short lot only
            if not self.lots:
                return self.cash
            lot = self.lots[0]
            return self.cash + lot.qty * (lot.fill - price)
        # long book: free cash + committed margin + unrealized PnL.
        # At leverage 1 (margin == qty*fill) this equals cash + total_qty*price.
        margin = sum(l.margin for l in self.lots)
        unreal = sum(l.qty * (price - l.fill) for l in self.lots)
        return self.cash + margin + unreal + self._dust_value(price)

    def state(self) -> dict:
        """리더보드가 그리는 포지션 상태 — 조회만, 부작용 없음(stepper.PositionSim.state 와 같은 키)."""
        cooldown = self._cooldown_until
        held = self.in_position()
        return {
            "in_position": held,
            "dir": 1 if self.side is PositionSide.LONG else -1,
            "qty": float(abs(self.total_qty())),
            "entry_price": float(self.avg_entry()) if held else 0.0,
            "cooldown_until_ms": int(cooldown.timestamp() * 1000) if cooldown is not None else None,
            "halted_today": self._halted_day is not None and self._halted_day == self._day,
        }

    # -- 전략 상태 체크포인트 ---------------------------------------------
    # 2026-10-12: 장부(수량·평단·현금)만 저장하던 것을 고친다. 지표는 웜업이 과거 봉으로
    # 다시 채우지만, **봉에서 다시 못 구하는 전략 상태**는 웜업으로 복원되지 않는다 —
    # E 의 눌림목 기준가·고점·트레일 발동이 그렇다. 그래서 재배포 뒤 트레일링이 고점을
    # 잊고 발동 전으로 돌아가, 복원된 평단 기준으로 다시 활성화%를 찍어야 켜졌다.
    #
    # 기본은 빈 칸이다 — 쓰는 규칙만 덮어쓴다(지금은 E). 다른 규칙의 상태는 후속 작업.
    def strategy_state(self) -> dict:
        return {}

    def restore_strategy(self, state: Optional[dict]) -> None:
        """체크포인트의 전략 상태로 되돌린다 — **장부 복구 앞**에서 부른다.

        왜 앞인가: ``restore()`` 가 현금을 되짚을 때 ``self.side`` 로 분기한다(숏은 식이
        다르다). K(하락 방어)는 방어 전환으로 side 가 SHORT 로 바뀌는데, 웜업 재생이
        평평하게 끝나면 LONG 으로 돌아와 있다. 그 상태로 장부를 복구하면 **현금이 틀린
        식으로** 되살아난다. 그래서 방향부터 바로잡고 장부를 복구한다.

        없는 칸과 ``None`` 은 '모름' 으로 보고 지금 값(웜업이 남긴 값)을 그대로 둔다 —
        옛 state_json 으로 복구할 때 예전 동작과 같아야 하기 때문이다.
        """
        return None

    def restore_strategy_lots(self, state: Optional[dict]) -> None:
        """롯을 참조하는 전략 상태 — **장부 복구 뒤**에 부른다.

        D(그리드)만 쓴다. ``restore()`` 가 여러 롯을 평단 하나로 합치므로 칸별 롯은
        그때 사라진다. 합친 뒤에 칸별로 다시 깔아야 한다(앞에서 하면 restore 가 덮는다).
        """
        return None

    # -- 실봉 웜업 ---------------------------------------------------------
    def reset_book(self) -> None:
        """장부·포지션·공통 리스크 상태를 시작값으로. 지표 상태는 건드리지 않는다(웜업용)."""
        self.cash = self.initial_capital
        self.lots = []
        self.closed_trades = []
        self.same_bar_sl = 0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self._day = None
        self._day_start_equity = self.initial_capital
        self._halted_day = None
        self._cooldown_until = None
        self._entry_time = None
        self.stopped = False
        self._reset_position_state()

    def _reset_position_state(self) -> None:
        """포지션이 있어야 의미 있는 전략 상태를 되돌린다. 기본은 없음 — 하위 sim 이 덮어쓴다."""

    def warmup(self, candles) -> None:
        """과거 마감봉으로 지표·전략 상태를 채운다. 그동안의 가상 체결은 버리고 장부만 되돌린다.

        _IndicatorSim 의 ``_pending`` 은 남긴다 — 백테스트가 다음 봉 시가에 실행하는 의도와 같다.
        """
        for row in candles:
            # (t, o, h, l, c) 또는 (t, o, h, l, c, v) — 거래량 없는 옛 봉 모양도 받는다.
            t_ms, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
            volume = float(row[5]) if len(row) > 5 and row[5] is not None else None
            ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc)
            self.on_candle(float(o), float(h), float(l), float(c), ts, volume=volume)
        self.reset_book()

    def restore(self, equity: float, *, in_position: bool, qty: float, entry_price: float,
                last_price: float, cooldown_until_ms: Optional[int] = None) -> None:
        """재기동 복구 — 마지막 체크포인트의 자산·포지션에서 이어 간다.

        여러 롯을 평단 하나로 합쳐 되살리고, 현금은 ``equity(last_price) == equity`` 가 되도록 역산한다.
        수수료는 체결 때 이미 반영됐으므로 0. 일일 손실 기준선·보유 시간은 복구 시점부터 새로 센다.
        """
        self.lots = []
        self.cash = float(equity)
        self._entry_time = None
        self._restored_price = float(last_price)  # 체결 기준 장부가 먼지 몫의 현금을 맞출 때 쓴다(_restore_ledger)
        if in_position and qty > 0 and entry_price > 0:
            margin = float(qty) * float(entry_price) / self.leverage
            self.lots.append(_Lot(qty=float(qty), fill=float(entry_price), margin=margin))
            if self.side is PositionSide.SHORT:
                self.cash = float(equity) - float(qty) * (float(entry_price) - float(last_price))
            else:
                self.cash = float(equity) - margin - float(qty) * (float(last_price) - float(entry_price))
            self._entry_time = datetime.now(timezone.utc)
        self._cooldown_until = (
            datetime.fromtimestamp(cooldown_until_ms / 1000, timezone.utc) if cooldown_until_ms else None
        )

    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, abs(self.total_qty()), reason))

    # -- long open / short open / close ----------------------------------
    # ``margin`` is the equity the caller commits; the position controls
    # ``margin × leverage`` of notional. At leverage 1 the two are equal, so the
    # cash math below is byte-for-byte the old spot behaviour.
    def _entry_mark(self, price: float) -> Optional[dict]:
        """진입 직전 상태 — 장부(state) · 전략 상태 · 그 가격의 자산. 끈 상태면 None.

        restore() 는 현금을 ``자산 - 포지션 평가액`` 으로 되짚으므로 자산과 기준가를 같은 가격으로 잡는다.
        지표(RSI · 이평 등)는 싣지 않는다 — 시장 사실이라 되돌리지 않는다(restore 도 건드리지 않는다).
        """
        if self._entry_marks is None:
            return None
        return {"sim": self.state(), "strategy": copy.deepcopy(self.strategy_state()),
                "equity": float(self.equity(price)), "last_price": float(price)}

    def _remember_entry(self, fill: Optional[Fill], mark: Optional[dict]) -> Optional[Fill]:
        if fill is not None and mark is not None and self._entry_marks is not None:
            self._entry_marks.append((fill, mark))
        return fill

    def _open_long(self, margin: float, close: float, ts: datetime, mark: float, *, reason: str = "진입",
                   kind: str = "entry", tier: int = 0, line: float = 0.0) -> Optional[Fill]:
        """``kind`` · ``tier`` · ``line`` 은 체결 기준 장부만 쓴다(기본 매수 base · 물타기 so 와 그 차수 · 가격선)."""
        margin = min(margin, self.cash)
        if margin <= 1e-9:
            return None
        before = self._entry_mark(mark)
        notional = margin * self.leverage
        f = buy_fill(close, self.slip)
        qty = notional / f
        self.cash -= margin + notional * self.comm / 100.0
        lot = _Lot(qty=qty, fill=f, margin=margin)
        self.lots.append(lot)
        if self._entry_time is None:
            self._entry_time = ts
        fill = self._fill("buy", f, qty, mark, reason)
        if self.ledger is not None:
            self._register_entry(lot, fill, margin=margin, fee=notional * self.comm / 100.0, ts=ts,
                                 kind=kind, tier=tier, line=line)
        return self._remember_entry(fill, before)

    def _open_short(self, close: float, ts: datetime, mark: float, *, reason: str = "진입") -> Optional[Fill]:
        margin = self.invest_ratio * self.cash
        if margin <= 1e-9:
            return None
        before = self._entry_mark(mark)
        notional = margin * self.leverage
        f = sell_fill(close, self.slip)
        qty = notional / f
        self.cash -= notional * self.comm / 100.0
        self.lots.append(_Lot(qty=qty, fill=f, margin=margin))
        self._entry_time = ts
        return self._remember_entry(self._fill("short", f, qty, mark, reason), before)

    def _close_all(self, close: float, mark: float, *, is_stop: bool, ts: datetime, reason: Optional[str] = None) -> Optional[Fill]:
        if not self.in_position():
            return None
        if self.ledger is not None:
            return self._signal_exit(close, mark, is_stop=is_stop, ts=ts, reason=reason)
        qty = self.total_qty()
        avg = self.avg_entry()
        margin = sum(l.margin for l in self.lots)
        if self.side is PositionSide.SHORT:
            f = buy_fill(close, self.slip)  # cover
            exit_comm = qty * f * self.comm / 100.0
            self.cash += qty * (avg - f) - exit_comm
            pnl = qty * (avg - f) - exit_comm  # entry comm already paid out of cash
            side = "cover"
        else:
            f = sell_fill(close, self.slip)
            exit_comm = qty * f * self.comm / 100.0
            # return committed margin + realized PnL (== qty*f at leverage 1)
            self.cash += margin + qty * (f - avg) - exit_comm
            pnl = qty * (f - avg) - exit_comm
            side = "sell"
        self.closed_trades.append(pnl)
        self.lots.clear()
        self._entry_time = None
        if is_stop and self.cooldown_minutes > 0:
            self._cooldown_until = ts + timedelta(minutes=self.cooldown_minutes)
        fill = self._fill(side, f, qty, mark, reason if reason is not None else ("손절" if is_stop else "청산"))
        self._after_close()
        return fill

    def _signal_exit(self, close: float, mark: float, *, is_stop: bool, ts: datetime,
                     reason: Optional[str]) -> Optional[Fill]:
        """체결 기준 장부의 청산 신호 — 장부는 그대로 두고 '청산 대기' 주문을 남긴다. 체결 보고만큼 줄인다(_fold_sell).

        Fill 의 직전 수량은 지금 보유 그대로다(명령 비율 1.0) — _fill_meta 는 장부가 이미 줄었다고 보고 두 배를 낸다.
        """
        ledger = self.ledger
        if ledger.exit is not None:
            return None  # 청산 대기 중 — 두 번째 청산 신호는 없다
        qty = self.total_qty()
        f = sell_fill(close, self.slip)
        fill = self._fill("sell", f, qty, mark, reason if reason is not None else ("손절" if is_stop else "청산"))
        fill.qty_before = qty
        ref = ledger.next_ref()
        fill.order_ref = ref
        ledger.orders[ref] = {"side": "sell", "kind": "stop" if is_stop else "exit", "epoch": ledger.epoch,
                              "qty": float(qty), "price": float(f), "is_stop": bool(is_stop),
                              "signal_ms": int(ts.timestamp() * 1000), "sold": 0.0}
        ledger.exit = ref
        return fill

    def _reduce_long(self, fraction: float, close: float, mark: float, ts: datetime) -> Optional[Fill]:
        """Sell ``fraction`` (0<f<=1) of the long book at ``close``.

        Banks the freed margin + realized PnL back to cash and shrinks each lot
        proportionally (dust lots dropped). Long book only — used by the SAR
        defense partial exit. ``_close_all`` remains the full-close path.
        """
        if self.ledger is not None:
            raise RuntimeError("partial reduce is not part of the fill ledger")
        if fraction <= 0 or self.side is PositionSide.SHORT or not self.in_position():
            return None
        fraction = min(fraction, 1.0)
        f = sell_fill(close, self.slip)
        sold_qty = 0.0
        pnl = 0.0
        for lot in self.lots:
            q = lot.qty * fraction
            m = lot.margin * fraction
            exit_comm = q * f * self.comm / 100.0
            self.cash += m + q * (f - lot.fill) - exit_comm
            pnl += q * (f - lot.fill) - exit_comm
            lot.qty -= q
            lot.margin -= m
            sold_qty += q
        self.closed_trades.append(pnl)
        self.lots = [l for l in self.lots if l.qty > 1e-12]
        if not self.lots:
            self._entry_time = None
        return self._fill("sell", f, sold_qty, mark, "부분 청산")

    def _liquidate_all(self, px: float, mark: float, ts: datetime) -> Optional[Fill]:
        """Isolated liquidation: the whole committed margin is lost (전액 손실).

        Long lots removed their margin from cash at entry, so equity already floors
        at cash; short kept its margin in cash, so wipe it here. Loss is capped at
        the committed margin."""
        if self.ledger is not None:
            raise RuntimeError("liquidation is not part of the fill ledger (spot only)")
        if not self.in_position():
            return None
        qty = self.total_qty()
        margin = sum(l.margin for l in self.lots)
        if self.side is PositionSide.SHORT:
            self.cash -= margin
            side = "cover"
        else:
            side = "sell"
        self.closed_trades.append(-margin)
        self.liquidations += 1
        self.liquidated_loss += margin
        self.lots.clear()
        self._entry_time = None
        if self.cooldown_minutes > 0:
            self._cooldown_until = ts + timedelta(minutes=self.cooldown_minutes)
        fill = self._fill(side, px, qty, mark, "강제 청산")
        self._after_close()
        return fill

    # -- common risk ------------------------------------------------------
    def hold_entries_until(self, until_ms: Optional[int]) -> None:
        self._entry_hold_until = datetime.fromtimestamp(int(until_ms) / 1000, timezone.utc) if until_ms else None

    def _entries_held(self, ts: datetime) -> bool:
        """진입 재시도 대기 중인가. 추가매수(그리드 칸 · 마틴게일 안전주문)도 이걸 본다 — 쿨다운과 달리
        '진입이 실제로 나가지 않았다' 는 뜻이라, 잔고가 모자란 채로 같은 주문을 봉마다 다시 내지 않게 한다.
        봉 안의 판단은 봉이 닫힌 시각으로, 틱의 판단(execute_pending)은 틱 시각으로 견준다."""
        if self._entry_hold_until is None:
            return False
        return (self._decision_at or ts) < self._entry_hold_until

    def _entry_blocked(self, ts: datetime) -> bool:
        if self.stopped:
            return True
        if self._entries_held(ts):
            return True
        if self._halted_day is not None and self._halted_day == self._day:
            return True
        if self._cooldown_until is not None and ts < self._cooldown_until:
            return True
        if self.entry_filter is not None and not self.entry_filter.allows():
            return True
        if self.bundle_gate is not None and self.bundle_gate.blocks(self):
            return True
        return False

    def _common_risk(self, o: float, h: float, l: float, c: float, ts: datetime, fills: List[Fill]) -> bool:
        """Apply day-roll, daily-max-loss halt, max-holding force close, and the
        shared stop-loss. Returns True if a forced exit happened this bar."""
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
        if day != self._day:
            self._day = day
            self._day_start_equity = self.equity(o)
            if self._halted_day is not None and self._halted_day != day:
                self._halted_day = None

        forced = False

        # Isolated-margin liquidation (leverage > 1) — checked FIRST, before the
        # user stop-loss / take-profit, so a bar that touches both liquidates
        # (conservative). Uses the quantity-weighted average entry.
        if self.leverage > 1 and self.in_position():
            avg = self.avg_entry()
            liq = liquidation_price(avg, self.leverage, self.side, commission_pct=self.comm)
            if liq is not None:
                hit = h >= liq if self.side is PositionSide.SHORT else l <= liq
                if hit:
                    f = self._liquidate_all(liq, liq, ts)
                    if f:
                        fills.append(f)
                        return True

        # Shared stop-loss (average-entry based). Checked before profit exits.
        if self.sl and self.in_position():
            avg = self.avg_entry()
            if self.side is PositionSide.SHORT:
                hit = h >= avg * (1 + self.sl / 100.0)
                px = avg * (1 + self.sl / 100.0)
            else:
                hit = l <= avg * (1 - self.sl / 100.0)
                px = avg * (1 - self.sl / 100.0)
            if hit:
                f = self._close_all(px, px, is_stop=True, ts=ts)
                if f:
                    fills.append(f)
                    forced = True

        # Max holding time -> force close at close.
        if (
            not forced
            and self.max_holding_hours
            and self.in_position()
            and self._entry_time is not None
            and (ts - self._entry_time) >= timedelta(hours=self.max_holding_hours)
        ):
            f = self._close_all(c, c, is_stop=False, ts=ts)
            if f:
                fills.append(f)
                forced = True

        # Daily max loss -> liquidate and halt for the rest of the day.
        if self.daily_max_loss:
            eq_now = self.equity(c)
            dd = (eq_now - self._day_start_equity) / self._day_start_equity * 100.0
            if dd <= -self.daily_max_loss:
                if self.in_position():
                    f = self._close_all(c, c, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)
                        forced = True
                self._halted_day = day
        return forced

    # -- driver entry point ----------------------------------------------
    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime,
                  volume: Optional[float] = None) -> List[Fill]:
        if self.ledger is not None and self._ledger_holds_candle(o, h, l, c, ts, volume):
            return []
        fills: List[Fill] = []
        # Funding on held shorts (per bar, prorated only for 1d; kept simple).
        if self.side is PositionSide.SHORT and self.in_position() and self.funding > 0:
            self.cash -= self.total_qty() * c * self.funding / 100.0
        if self._entry_hold_until is not None and self._candle_seconds:
            self._decision_at = ts + timedelta(seconds=self._candle_seconds)
        try:
            self._strategy(o, h, l, c, ts, fills)
        finally:
            self._decision_at = None
        # 전략 '뒤' 에 갱신한다 — 그래야 이 봉의 진입 판단은 직전 마감봉의 필터 값을 본다.
        # 순서를 바꾸면 진행 중인 봉으로 자기 자신을 판정하는 미래 참조가 된다.
        if self.entry_filter is not None:
            self.entry_filter.update(c, volume=volume)
        return fills

    def _strategy(self, o, h, l, c, ts, fills):  # pragma: no cover - overridden
        raise NotImplementedError


# --- E: trailing stop (long, single) ------------------------------------
class TrailingSim(CandleSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.entry_mode = self.p.get("entry_mode", "immediate")
        self.entry_dip = float(self.p.get("entry_dip", 3.0))
        self.activation = float(self.p.get("activation_profit", 5.0))
        self.trail = float(self.p["trail_percent"])
        self.reenter = bool(self.p.get("reenter_after_exit", True))
        self._peak = 0.0
        self._armed = False
        self._ref: Optional[float] = None  # reference for dip entry

    def _reset_position_state(self) -> None:
        self._peak = 0.0
        self._armed = False
        self._ref = None

    # 봉에서 다시 구할 수 없는 셋 — 체크포인트가 실어 보내야 재기동에서 살아남는다.
    def strategy_state(self) -> dict:
        return {"dip_ref": self._ref, "peak": float(self._peak), "armed": bool(self._armed)}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if state.get("dip_ref") is not None:
            self._ref = float(state["dip_ref"])
        if state.get("peak") is not None:
            self._peak = float(state["peak"])
        if state.get("armed") is not None:
            self._armed = bool(state["armed"])

    def _strategy(self, o, h, l, c, ts, fills):
        if self._common_risk(o, h, l, c, ts, fills):
            if not self.reenter:
                self.stopped = True
            return
        if self.in_position():
            avg = self.avg_entry()
            self._peak = max(self._peak, h)
            if not self._armed and self._peak >= avg * (1 + self.activation / 100.0):
                self._armed = True
            if self._armed:
                trigger = self._peak * (1 - self.trail / 100.0)
                if l <= trigger:
                    f = self._close_all(trigger, trigger, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)
                    if not self.reenter:
                        self.stopped = True
                    self._ref = c
            return
        # flat -> maybe enter
        # 눌림목 기준가는 관문 **앞**에서 잡는다 — 관문 뒤에 두면 필터가 막는 동안 기준가가 비어 있다가
        # "필터가 처음 허락한 봉" 의 시가로 잡히고, 오르는 구간에서는 문턱이 함께 올라가 필터를 켠 쪽이
        # 끄면 없던 매수를 한다(필터는 거래를 줄이는 쪽으로만 작동한다 — 설계 원칙 1).
        if self.entry_mode == "dip" and self._ref is None:
            self._ref = o
        if self._entry_blocked(ts):
            return
        if self.entry_mode == "dip":
            if l <= self._ref * (1 - self.entry_dip / 100.0):
                px = self._ref * (1 - self.entry_dip / 100.0)
                f = self._open_long(self.invest_ratio * self.cash, px, ts, c)
                if f:
                    fills.append(f)
                    self._peak = c
                    self._armed = False
        else:
            f = self._open_long(self.invest_ratio * self.cash, c, ts, c)
            if f:
                fills.append(f)
                self._peak = c
                self._armed = False


# --- D: grid trading (long, multi-order) --------------------------------
class GridSim(CandleSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        lo = float(self.p["lower_price"])
        hi = float(self.p["upper_price"])
        n = int(self.p["grid_count"])
        mode = self.p.get("grid_mode", "arithmetic")
        if mode == "geometric":
            r = (hi / lo) ** (1.0 / n)
            self.levels = [lo * r ** i for i in range(n + 1)]
        else:
            step = (hi - lo) / n
            self.levels = [lo + step * i for i in range(n + 1)]
        self.lower, self.upper = lo, hi
        self.band_exit = self.p.get("band_exit_action", "stop")
        budget = self.invest_ratio * self.initial_capital
        pg = self.p.get("per_grid_invest")
        self.per_grid = float(pg) if pg else budget / n
        # holdings[i] = lot bought at level i, awaiting sell at level i+1
        self.holdings: dict[int, _Lot] = {}

    def _reset_position_state(self) -> None:
        self.holdings = {}

    # holdings 는 self.lots 와 **같은 롯 객체를 공유**한다(_remove_lot 가 둘을 함께 뺀다).
    # 그래서 칸별 롯을 통째로 싣고, 장부 복구 뒤에 다시 깐다. 안 그러면 재기동 뒤
    # "어느 칸을 사 뒀는지" 를 잊어 같은 칸을 다시 사거나 팔 칸을 모른다.
    def strategy_state(self) -> dict:
        return {"grid": [{"level": int(i), "qty": float(lot.qty), "fill": float(lot.fill),
                          "margin": float(lot.margin)}
                         for i, lot in sorted(self.holdings.items())],
                "stopped": bool(self.stopped)}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if state and state.get("stopped") is not None:
            # 밴드를 벗어나 멈춘 그리드가 재기동으로 되살아나면 안 된다.
            self.stopped = bool(state["stopped"])

    def restore_strategy_lots(self, state: Optional[dict]) -> None:
        rows = (state or {}).get("grid")
        if rows is None:
            return
        # 합쳐 둔 롯을 칸별 롯으로 바꿔 깐다. 자산은 유지된다 — 체크포인트의 평단이 이
        # 롯들의 수량가중 평균이라 sum(qty_i·fill_i) == qty·평단 이고 margin 합도 같다.
        self.lots = []
        self.holdings = {}
        for row in rows:
            lot = _Lot(qty=float(row["qty"]), fill=float(row["fill"]), margin=float(row["margin"]))
            self.lots.append(lot)
            self.holdings[int(row["level"])] = lot

    def _strategy(self, o, h, l, c, ts, fills):
        # Stop-loss / daily / holding first (may liquidate whole book).
        if self._common_risk(o, h, l, c, ts, fills):
            self.holdings.clear()
            self.lots.clear()
            return
        # Band exit.
        if c > self.upper or c < self.lower:
            if self.band_exit == "stop":
                f = self._close_all(c, c, is_stop=False, ts=ts)
                if f:
                    fills.append(f)
                self.holdings.clear()
                self.stopped = True
                return
            # hold: stop trading new grids but keep bags
            return
        if self.stopped:
            return
        # Sells: any held lot whose sell level (one grid up) is reached by high.
        for i in sorted(list(self.holdings.keys())):
            sell_level = self.levels[i + 1]
            if h >= sell_level:
                lot = self.holdings.pop(i)
                f = sell_fill(sell_level, self.slip)
                exit_comm = lot.qty * f * self.comm / 100.0
                # return this grid lot's margin + realized PnL (== qty*f at leverage 1)
                self.cash += lot.margin + lot.qty * (f - lot.fill) - exit_comm
                self.closed_trades.append(lot.qty * (f - lot.fill) - exit_comm)
                self._remove_lot(lot)
                fills.append(self._fill("sell", f, lot.qty, c, f"격자 {sell_level:g} 도달 · 매도"))
        # Buys: any buy level reached by low that we don't already hold. ``per_grid``
        # is the committed margin; the lot controls ``per_grid × leverage`` notional.
        if self._entries_held(ts):
            return
        for i in range(len(self.levels) - 1):
            if i in self.holdings:
                continue
            buy_level = self.levels[i]
            if l <= buy_level <= self.upper and self.cash >= self.per_grid:
                before = self._entry_mark(c)
                notional = self.per_grid * self.leverage
                f = buy_fill(buy_level, self.slip)
                qty = notional / f
                self.cash -= self.per_grid + notional * self.comm / 100.0
                lot = _Lot(qty=qty, fill=f, margin=self.per_grid)
                self.holdings[i] = lot
                self.lots.append(lot)
                if self._entry_time is None:
                    self._entry_time = ts
                if before is not None:
                    # 칸끼리는 서로 독립이다 — 이 칸 매수가 안 나가면 이 롯만 빼면 된다(undo_lot).
                    before["grid_lot"] = {"level": i, "qty": qty, "fill": f, "margin": self.per_grid,
                                          "fee": notional * self.comm / 100.0}
                fills.append(self._remember_entry(self._fill("buy", f, qty, c), before))

    def undo_lot(self, info: dict) -> bool:
        """주문이 나가지 않은 칸 하나만 되돌린다 — 그 칸의 롯을 빼고 산 돈(증거금 + 수수료)을 현금으로 돌린다.

        다른 칸의 매수 · 매도는 그대로 둔다(칸끼리 독립). 그 롯이 이미 팔렸거나 다른 롯으로 바뀌었으면 False.
        """
        level = int(info.get("level", -1))
        lot = self.holdings.get(level)
        if lot is None or not (_close(lot.qty, info.get("qty")) and _close(lot.fill, info.get("fill"))):
            return False
        del self.holdings[level]
        self._remove_lot(lot)
        self.cash += float(info.get("margin") or 0.0) + float(info.get("fee") or 0.0)
        return True

    def _remove_lot(self, lot: _Lot) -> None:
        try:
            self.lots.remove(lot)
        except ValueError:
            pass
        if not self.lots:
            self._entry_time = None


# --- H: martingale / safety orders (long, multi-order) ------------------
class MartingaleSim(CandleSim):
    supports_fill_ledger = True

    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.base = float(self.p["base_order_size"])
        self.so_size = float(self.p["safety_order_size"])
        self.dev = float(self.p["price_deviation"])
        self.step_scale = float(self.p.get("safety_order_step_scale", 1.0))
        self.vol_scale = float(self.p.get("safety_order_volume_scale", 1.0))
        self.max_so = int(self.p.get("max_safety_orders", 5))
        self.tp = float(self.p["take_profit"])
        self._so_done = 0
        self._base_price = 0.0  # price of the base order (deviation reference)
        # 체결 기준 장부만: 덜 찬 물타기 차수 → {가격선, 남은 예산}. 그 가격선 이하에서만 남은 예산을 다시 쓴다(결정 1).
        # 백테스트 · 모의는 주문이 늘 다 차므로 비어 있다.
        self._reopen: dict = {}

    def _reset_position_state(self) -> None:
        self._so_done = 0
        self._base_price = 0.0
        self._reopen = {}

    # 봉에서 다시 못 구한다 — 잊으면 추가매수 차수가 0 으로 돌아가 이미 채운 단계를 다시 산다.
    def strategy_state(self) -> dict:
        state = {"so_done": int(self._so_done), "base_price": float(self._base_price), **self._ledger_state()}
        if self.ledger is not None:
            state["reopen"] = {str(tier): dict(slot) for tier, slot in self._reopen.items()}
        return state

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if state.get("so_done") is not None:
            self._so_done = int(state["so_done"])
        if state.get("base_price") is not None:
            self._base_price = float(state["base_price"])
        if state.get("reopen") is not None:
            self._reopen = {int(tier): {"line": float(slot["line"]), "remaining": float(slot["remaining"])}
                            for tier, slot in state["reopen"].items()}

    def restore_strategy_lots(self, state: Optional[dict]) -> None:
        self._restore_ledger(state)

    def _on_base_filled(self, price: float) -> None:
        # 물타기선은 최초 매수가 기준 — 그 값을 가상 체결가에서 실제 체결가로(설계 §3: 출처만 바뀐다).
        self._base_price = float(price)

    def _after_close(self) -> None:
        self._so_done = 0
        self._reopen = {}

    def _entry_left(self, order: dict, remaining: float, status: str) -> bool:
        # 결정 1: 덜 찬 물타기는 그 차수 가격선에 남은 예산(배정 − 실제 체결 금액 − 수수료)으로 다시 연다.
        # 재주문 불가(최소 주문 미만)면 그 차수 완료. 일부러 취소한 주문(로컬 손절 등)은 다시 열지 않는다.
        if order.get("kind") != "so" or status == "cancelled":
            return False
        if remaining < max(self._reorder_floor(), 1e-9):
            return False
        self._reopen[int(order["tier"])] = {"line": float(order["line"]), "remaining": float(remaining)}
        return True

    def _late_entry_spent(self, ref: str, spent: float) -> None:
        info = self.ledger.recent.get(ref) or {}
        slot = self._reopen.get(int(info.get("tier") or 0)) if info.get("kind") == "so" else None
        if slot is None or int(info.get("epoch", -1)) != self.ledger.epoch:
            return
        slot["remaining"] -= spent
        if slot["remaining"] < max(self._reorder_floor(), 1e-9):
            del self._reopen[int(info["tier"])]

    def _rebuy_reopened(self, l: float, c: float, ts: datetime, fills: List[Fill]) -> None:
        """다시 연 차수 — 봉 저가가 그 가격선 이하일 때만 남은 예산을 산다(비싸게 쫓아 사지 않는다)."""
        for tier in sorted(self._reopen):
            slot = self._reopen[tier]
            if l <= slot["line"] and self.cash > 1e-9:
                del self._reopen[tier]
                f = self._open_long(slot["remaining"], slot["line"], ts, c, kind="so", tier=tier, line=slot["line"])
                if f:
                    fills.append(f)

    def _next_so_price(self) -> float:
        # cumulative deviation with step scaling
        cum = 0.0
        step = self.dev
        for _ in range(self._so_done + 1):
            cum += step
            step *= self.step_scale
        return self._base_price * (1 - cum / 100.0)

    def _strategy(self, o, h, l, c, ts, fills):
        # Same-bar priority: stop-loss (avg based, via _common_risk) evaluated
        # before the take-profit below. If both would trigger, count it.
        both_touch = False
        if self.in_position() and self.sl:
            avg = self.avg_entry()
            sl_px = avg * (1 - self.sl / 100.0)
            tp_px = avg * (1 + self.tp / 100.0)
            both_touch = l <= sl_px and h >= tp_px
        forced = self._common_risk(o, h, l, c, ts, fills)
        if forced:
            # 차수는 포지션 끝 훅(_after_close)이 비운다 — 백테스트는 청산 즉시, 체결 기준 장부는 실제로 다 팔린 뒤.
            if both_touch:
                self.same_bar_sl += 1
            return
        if self.in_position():
            avg = self.avg_entry()
            tp_px = avg * (1 + self.tp / 100.0)
            if h >= tp_px:
                f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                if f:
                    fills.append(f)
                return
            if self._reopen and not self._entries_held(ts):
                self._rebuy_reopened(l, c, ts, fills)
            # safety orders on the way down
            while self._so_done < self.max_so and not self._entries_held(ts):
                so_px = self._next_so_price()
                if l <= so_px and self.cash > 1e-9:
                    size = self.so_size * (self.vol_scale ** self._so_done)
                    f = self._open_long(size, so_px, ts, c, kind="so", tier=self._so_done + 1, line=so_px)
                    if f:
                        fills.append(f)
                    self._so_done += 1
                else:
                    break
            return
        # flat -> base order
        if self._entry_blocked(ts):
            return
        f = self._open_long(self.base, c, ts, c, kind="base")
        if f:
            fills.append(f)
            self._base_price = self.avg_entry()
            self._so_done = 0


# --- I: volatility breakout (long, single) ------------------------------
class BreakoutSim(CandleSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.k = float(self.p.get("k", 0.5))
        self.exit_mode = self.p.get("exit_mode", "next_open")
        self.trail = float(self.p.get("trail_percent", 2.0))
        self.tp = self.p.get("take_profit")
        self.ma_period = self.p.get("ma_filter_period")
        self._prev_range: Optional[float] = None
        self._ma = MAState("SMA", int(self.ma_period)) if self.ma_period else None
        self._ma_val: Optional[float] = None
        self._peak = 0.0
        self._exit_next_open = False

    def _reset_position_state(self) -> None:
        self._peak = 0.0
        self._exit_next_open = False

    # _prev_range · 이동평균은 웜업이 과거 봉에서 다시 만든다. 못 만드는 둘만 싣는다:
    # 고점(트레일링 기준)과 "다음 봉 시가에 청산" 예약 — 예약을 잊으면 청산이 사라진다.
    def strategy_state(self) -> dict:
        return {"peak": float(self._peak), "exit_next_open": bool(self._exit_next_open)}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if state.get("peak") is not None:
            self._peak = float(state["peak"])
        if state.get("exit_next_open") is not None:
            self._exit_next_open = bool(state["exit_next_open"])

    def execute_pending(self, price: float, ts: datetime) -> List[Fill]:
        """어제 돌파 진입의 '다음 봉 시가 청산' 을 ``price`` 에 실행하고 플래그를 지운다.

        백테스트는 다음 봉의 시가(``_strategy`` 의 ``o``)로 부르고, 실시간(LiveCandleSim.step)은 봉이
        도착한 뒤 첫 3초 틱 가격으로 부른다 — 마감봉만 오는 실시간에서 다음 ``on_candle`` 을 기다리면
        한 간격을 통째로 늦게 체결하기 때문이다.
        """
        fills: List[Fill] = []
        if self._exit_next_open and self.in_position():
            f = self._close_all(price, price, is_stop=False, ts=ts)
            if f:
                fills.append(f)
        self._exit_next_open = False
        return fills

    def _strategy(self, o, h, l, c, ts, fills):
        # next_open exit: close yesterday's breakout at today's open.
        fills += self.execute_pending(o, ts)

        if self._common_risk(o, h, l, c, ts, fills):
            self._exit_next_open = False
            self._roll(h, l, c)
            return

        if self.in_position():
            self._peak = max(self._peak, h)
            if self.exit_mode == "trailing":
                trigger = self._peak * (1 - self.trail / 100.0)
                if l <= trigger:
                    f = self._close_all(trigger, trigger, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)
            elif self.exit_mode == "take_profit" and self.tp:
                avg = self.avg_entry()
                tp_px = avg * (1 + float(self.tp) / 100.0)
                if h >= tp_px:
                    f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)
            # next_open handled at top of next bar
        else:
            # entry: breakout above open + k*prev_range, optional MA filter
            if not self._entry_blocked(ts) and self._prev_range is not None:
                target = o + self.k * self._prev_range
                ma_ok = self._ma_val is None or c >= self._ma_val
                if h >= target and ma_ok:
                    f = self._open_long(self.invest_ratio * self.cash, target, ts, c)
                    if f:
                        fills.append(f)
                        self._peak = c
                        if self.exit_mode == "next_open":
                            self._exit_next_open = True
        self._roll(h, l, c)

    def _roll(self, h, l, c):
        self._prev_range = h - l
        if self._ma is not None:
            self._ma_val = self._ma.update(c)


# --- indicator base: decide on close, fill on next open -----------------
class _IndicatorSim(CandleSim):
    """F/G/J share: a ``_pending`` intent set at close[i], executed at open[i+1]."""

    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self._pending: Optional[str] = None  # "enter" | "exit" | None
        self.tp = self.p.get("take_profit")
        self._signal_note = ""  # 마지막 판정의 한 줄 근거 — Fill.reason 에 실린다

    # 지표(연속 횟수 · 직전 차이 · 밴드폭)는 웜업이 과거 봉으로 다시 만든다. 못 만드는 것은
    # **의도** 하나다: _pending 은 보유 여부로 갈리므로(보유 중이면 exit, 비었으면 enter)
    # 재생의 포지션이 실제와 다르면 통째로 뒤바뀐다. execute_pending 이 양쪽을 막아 주어
    # 중복 매수·없는 물량 매도는 안 나지만, **정해 둔 청산이 조용히 사라진다.**
    #
    # "의도 없음"(None)도 상태다. 그래서 pending 은 칸이 있으면 그대로 쓴다 — 다른 칸들의
    # "None 은 모름" 규칙과 다르고, 그래서 키의 유무로 가른다.
    def strategy_state(self) -> dict:
        return {"pending": self._pending, "signal_note": str(self._signal_note or "")}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if "pending" in state:
            value = state["pending"]
            self._pending = value if value in ("enter", "exit") else None
        if state.get("signal_note") is not None:
            self._signal_note = str(state["signal_note"])

    def execute_pending(self, price: float, ts: datetime, mark: Optional[float] = None) -> List[Fill]:
        """직전 마감봉에서 정한 ``_pending`` 의도를 ``price`` 에 실행하고 의도를 지운다.

        백테스트는 다음 봉의 시가(``_strategy`` 의 ``o``)로 부른다. 실시간(LiveCandleSim.step)은 마감봉만
        받으므로 다음 ``on_candle`` 을 기다리면 한 간격을 통째로 늦게 체결한다 — 봉이 도착한 뒤 첫 3초 틱
        가격으로 불러 "다음 시가" 의미를 몇 초 안에 맞춘다. ``mark`` 는 Fill 의 평가 기준가(기본 ``price``).
        """
        mark = price if mark is None else mark
        fills: List[Fill] = []
        if self._pending == "exit" and self.in_position():
            f = self._close_all(price, price, is_stop=False, ts=ts,
                                reason=f"{self._signal_note} · 청산" if self._signal_note else None)
            if f:
                fills.append(f)
        elif self._pending == "enter" and not self.in_position() and not self._entry_blocked(ts):
            reason = f"{self._signal_note} · 진입" if self._signal_note else "진입"
            if self.side is PositionSide.SHORT:
                f = self._open_short(price, ts, mark, reason=reason)
            else:
                f = self._open_long(self.invest_ratio * self.cash, price, ts, mark, reason=reason)
            if f:
                fills.append(f)
        self._pending = None
        return fills

    def _strategy(self, o, h, l, c, ts, fills):
        # 1) execute the pending decision from the previous closed bar, at open (mark = close, as before).
        fills += self.execute_pending(o, ts, mark=c)

        # 2) common risk (stop-loss / daily / holding) intrabar.
        if self._common_risk(o, h, l, c, ts, fills):
            return

        # 3) intrabar take-profit (optional) before recomputing the signal.
        if self.in_position() and self.tp:
            avg = self.avg_entry()
            if self.side is PositionSide.SHORT:
                tp_px = avg * (1 - float(self.tp) / 100.0)
                if l <= tp_px:
                    f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)
            else:
                tp_px = avg * (1 + float(self.tp) / 100.0)
                if h >= tp_px:
                    f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                    if f:
                        fills.append(f)

        # 4) evaluate the indicator on this CLOSE and set next-bar intent.
        self._signal(c)

    def _signal(self, close: float) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class RSISim(_IndicatorSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.rsi = RSIState(int(self.p.get("rsi_period", 14)))
        self.entry_th = float(self.p.get("entry_threshold", 30))
        self.exit_th = float(self.p.get("exit_threshold", 70))
        self.confirm = int(self.p.get("confirm_candles", 1))
        self.exit_mode = self.p.get("exit_mode", "indicator")
        self._entry_streak = 0
        self._exit_streak = 0

    def _signal(self, close: float) -> None:
        v = self.rsi.update(close)
        if v is None:
            return
        short = self.side is PositionSide.SHORT
        # entry condition (mirrored for short)
        entry_hit = v >= self.exit_th if short else v <= self.entry_th
        exit_hit = v <= self.entry_th if short else v >= self.exit_th
        self._entry_streak = self._entry_streak + 1 if entry_hit else 0
        self._exit_streak = self._exit_streak + 1 if exit_hit else 0
        if not self.in_position():
            if self._entry_streak >= self.confirm:
                self._signal_note = f"RSI {v:.1f} {'≥' if short else '≤'} {self.exit_th if short else self.entry_th:g}"
                self._pending = "enter"
        else:
            if self.exit_mode in ("indicator", "both") and self._exit_streak >= self.confirm:
                self._signal_note = f"RSI {v:.1f} {'≤' if short else '≥'} {self.entry_th if short else self.exit_th:g}"
                self._pending = "exit"


class BollingerSim(_IndicatorSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.bb = BollingerState(int(self.p.get("bb_period", 20)), float(self.p.get("bb_std", 2.0)))
        self.strategy = self.p.get("strategy", "reversion")
        self.exit_target = self.p.get("exit_target", "mid")
        self.squeeze_filter = bool(self.p.get("squeeze_filter", False))
        self.squeeze_lb = int(self.p.get("squeeze_lookback", 50))
        self._bw: Deque[float] = deque(maxlen=self.squeeze_lb)

    def _signal(self, close: float) -> None:
        bands = self.bb.update(close)
        if bands is None:
            return
        mid, upper, lower = bands
        bw = (upper - lower) / mid if mid else 0.0
        self._bw.append(bw)
        squeezed = True
        if self.squeeze_filter and len(self._bw) >= 2:
            squeezed = bw <= min(self._bw) * 1.05
        short = self.side is PositionSide.SHORT
        if not self.in_position():
            if self.strategy == "reversion":
                long_entry = close <= lower
                short_entry = close >= upper
            else:  # breakout
                long_entry = close >= upper
                short_entry = close <= lower
            hit = short_entry if short else long_entry
            if hit and squeezed:
                self._signal_note = f"볼린저 {'상단' if short else '하단'} {(upper if short else lower):.4g} 이탈"
                self._pending = "enter"
        else:
            if self.exit_target == "mid":
                exit_hit = close >= mid if not short else close <= mid
            else:  # opposite band
                exit_hit = close >= upper if not short else close <= lower
            if exit_hit:
                level = mid if self.exit_target == "mid" else (upper if not short else lower)
                self._signal_note = f"볼린저 {'중심' if self.exit_target == 'mid' else ('하단' if short else '상단')} {level:.4g} 도달"
                self._pending = "exit"


class MACrossSim(_IndicatorSim):
    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        ma_type = self.p.get("ma_type", "SMA")
        self.fast = MAState(ma_type, int(self.p["fast_period"]))
        self.slow = MAState(ma_type, int(self.p["slow_period"]))
        self.exit_signal = self.p.get("exit_signal", "dead_cross")
        self.confirm = int(self.p.get("confirm_candles", 1))
        self._prev_diff: Optional[float] = None
        self._golden_streak = 0
        self._dead_streak = 0

    def _signal(self, close: float) -> None:
        f = self.fast.update(close)
        s = self.slow.update(close)
        if f is None or s is None:
            return
        diff = f - s
        golden = dead = False
        if self._prev_diff is not None:
            golden = self._prev_diff <= 0 < diff
            dead = self._prev_diff >= 0 > diff
        self._prev_diff = diff
        # streak on the state (fast above/below), confirm consecutive bars
        self._golden_streak = self._golden_streak + 1 if diff > 0 else 0
        self._dead_streak = self._dead_streak + 1 if diff < 0 else 0
        short = self.side is PositionSide.SHORT
        enter_now = self._dead_streak >= self.confirm if short else self._golden_streak >= self.confirm
        exit_now = self._golden_streak >= self.confirm if short else self._dead_streak >= self.confirm
        if not self.in_position():
            if enter_now:
                self._signal_note = "데드크로스" if short else "골든크로스"
                self._pending = "enter"
        else:
            if self.exit_signal in ("dead_cross", "both") and exit_now:
                self._signal_note = "골든크로스" if short else "데드크로스"
                self._pending = "exit"


# --- K: SAR defense reversal (long -> partial exit -> flip short) --------
class SarSim(CandleSim):
    r"""Long-primary strategy with a defensive stop-and-reverse.

    Lifecycle (single position at a time)::

        FLAT --enter--> LONG --+long_tp%--> take-profit --> FLAT (re-enter)
                          |
             -drop_trigger% from avg entry (defense)
                          v
              sell partial_exit% of the long
                          |
             flip_to_short? close remainder + open SHORT   (self.side -> SHORT)
                          v
                        SHORT --(-short_tp% further drop)--> take-profit
                          |   \--(+short_sl% bounce)-------> stop-loss (bounded)
                          v
                        FLAT --reenter_long_after?--> LONG

    The shared price stop-loss (``self.sl``) is neutralized because the drawdown
    trigger IS the long-leg stop here; the short leg carries its own mandatory
    stop. Liquidation / daily-max-loss / max-holding from ``_common_risk`` stay
    active and follow ``self.side`` as it flips.
    """

    def __init__(self, macro, initial_capital=None):
        super().__init__(macro, initial_capital)
        self.sl = None  # drawdown trigger replaces the shared long stop-loss
        self.long_tp = self.p.get("long_take_profit_pct")
        self.drop_trigger = float(self.p["drop_trigger_pct"])
        self.partial_exit = float(self.p.get("partial_exit_pct", 50.0)) / 100.0
        self.flip = bool(self.p.get("flip_to_short", True))
        self.short_tp = float(self.p["short_take_profit_pct"])
        self.short_sl = float(self.p["short_stop_loss_pct"])
        self.reenter = bool(self.p.get("reenter_long_after", True))
        self._phase = "long"  # "long" | "short"
        self._defended = False  # partial exit already taken for the current long leg

    def _reset_position_state(self) -> None:
        self._reset_after_flat()

    # side 가 가장 중요하다 — restore() 가 현금을 되짚을 때 숏/롱으로 분기하므로,
    # 숏 단계였던 세션을 LONG 으로 복구하면 현금이 틀린 식으로 되살아난다.
    def strategy_state(self) -> dict:
        return {"side": "short" if self.side is PositionSide.SHORT else "long",
                "phase": str(self._phase), "defended": bool(self._defended)}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if state.get("side") is not None:
            self.side = PositionSide.SHORT if str(state["side"]) == "short" else PositionSide.LONG
        if state.get("phase") is not None:
            self._phase = str(state["phase"])
        if state.get("defended") is not None:
            self._defended = bool(state["defended"])

    def _reset_after_flat(self) -> None:
        self.side = PositionSide.LONG
        self._phase = "long"
        self._defended = False

    def _end_short(self) -> None:
        self.side = PositionSide.LONG
        self._phase = "long"
        self._defended = False
        if not self.reenter:
            self.stopped = True  # stay flat for good

    def _strategy(self, o, h, l, c, ts, fills):
        # Common risk (liquidation / daily-loss / max-holding). self.sl is None so
        # the shared price stop-loss is inert; the checks that remain follow side.
        if self._common_risk(o, h, l, c, ts, fills):
            self._reset_after_flat()
            return

        if self.in_position():
            if self.side is PositionSide.SHORT:
                self._manage_short(o, h, l, c, ts, fills)
            else:
                self._manage_long(o, h, l, c, ts, fills)
            return

        # flat -> (re)enter long
        if self._entry_blocked(ts):
            return
        # 비어 있으면 늘 롱이다 — 다만 숏 전환 진입을 되돌린 상태(실행기 세션)는 side 가 숏으로 남아 있을 수 있어
        # 롱 장부 식을 쓰도록 먼저 맞춘다. 백테스트에서는 이미 롱이라 아무것도 바뀌지 않는다.
        self.side = PositionSide.LONG
        f = self._open_long(self.invest_ratio * self.cash, c, ts, c)
        if f:
            fills.append(f)
            self.side = PositionSide.LONG
            self._phase = "long"
            self._defended = False

    def _manage_long(self, o, h, l, c, ts, fills):
        avg = self.avg_entry()
        # Long take-profit (intrabar high) — re-enters on a later flat bar.
        if self.long_tp:
            tp_px = avg * (1 + float(self.long_tp) / 100.0)
            if h >= tp_px:
                f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                if f:
                    fills.append(f)
                self._reset_after_flat()
                return
        # Defense trigger (intrabar low): drawdown of drop_trigger% from avg entry.
        trig_px = avg * (1 - self.drop_trigger / 100.0)
        if not self._defended and l <= trig_px:
            pf = self._reduce_long(self.partial_exit, trig_px, trig_px, ts)
            if pf:
                fills.append(pf)
            self._defended = True
            if self.flip:
                if self.in_position():
                    cf = self._close_all(trig_px, trig_px, is_stop=False, ts=ts)
                    if cf:
                        fills.append(cf)
                self.side = PositionSide.SHORT
                sf = self._open_short(trig_px, ts, trig_px)
                if sf:
                    fills.append(sf)
                    self._phase = "short"
                else:  # no cash to short -> stay flat/long
                    self.side = PositionSide.LONG
                    self._phase = "long"

    def _manage_short(self, o, h, l, c, ts, fills):
        avg = self.avg_entry()
        tp_px = avg * (1 - self.short_tp / 100.0)  # profit: price falls
        sl_px = avg * (1 + self.short_sl / 100.0)  # stop: price rises (bounce)
        both = l <= tp_px and h >= sl_px
        # Same-bar priority: stop-loss wins (conservative) and is counted.
        if h >= sl_px:
            f = self._close_all(sl_px, sl_px, is_stop=True, ts=ts)
            if f:
                fills.append(f)
            if both:
                self.same_bar_sl += 1
            self._end_short()
            return
        if l <= tp_px:
            f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
            if f:
                fills.append(f)
            self._end_short()


_SIM_BY_TYPE = {
    RuleType.D: GridSim,
    RuleType.E: TrailingSim,
    RuleType.F: RSISim,
    RuleType.G: BollingerSim,
    RuleType.H: MartingaleSim,
    RuleType.I: BreakoutSim,
    RuleType.J: MACrossSim,
    RuleType.K: SarSim,
}


def make_candle_sim(macro: Macro, initial_capital: Optional[float] = None) -> CandleSim:
    """Construct the candle sim for a D~J macro (shared by backtest + paper)."""
    cls = _SIM_BY_TYPE[macro.rule_type]
    return cls(macro, initial_capital=initial_capital)


# --- paper/runner adapter: real closed candles ---------------------------
# 캔들형 전략은 봉 마감에만 판단한다. 실시간에서는 CandleFeed 가 바이낸스 마감봉을 밀어 주고
# (on_candle), 3초 틱은 평가 갱신과 체결 큐 드레인만 한다(step). 이전의 "3틱=1봉" 합성은 없다.
class LiveCandleSim:
    """Wrap a :class:`CandleSim` behind the stepper's ``step(price)`` contract, fed by real candles."""

    def __init__(self, macro: Macro, initial_capital: Optional[float] = None) -> None:
        self.inner = make_candle_sim(macro, initial_capital=initial_capital)
        self._queue: deque[Fill] = deque()
        # 큐의 체결마다 그 진입 직전 상태(진입이 아니거나 끈 상태면 None). step() 이 꺼낸 체결의 것을 last_mark 에 둔다.
        self._queue_marks: deque = deque()
        self.last_mark: Optional[dict] = None

    def track_entry_marks(self) -> None:
        """진입 직전 상태를 남기기 시작한다(실행기 드라이버만)."""
        self.inner._entry_marks = []

    def _enqueue(self, fills: List[Fill]) -> None:
        recorded = self.inner._entry_marks
        marks = {id(fill): mark for fill, mark in (recorded or [])}
        for fill in fills:
            self._queue.append(fill)
            self._queue_marks.append(marks.get(id(fill)))
        if recorded is not None:
            recorded.clear()

    def step(self, price: float, ts: Optional[datetime] = None) -> Optional[Fill]:
        """큐의 Fill 하나를 꺼내 준다. 큐가 비었고 sim 에 '다음 시가' 의도가 걸려 있으면 이 틱 가격에 실행한다.

        마감봉만 오는 실시간에서는 봉 N 마감에 정한 의도를 봉 N+1 의 on_candle 까지 기다리면 한 간격
        늦게(N+1 마감 무렵) 체결된다. 봉이 온 뒤 첫 틱(≈ N+1 시가)에 실행해 백테스트의 "다음 시가" 와 맞춘다.
        """
        if not self._queue:
            execute = getattr(self.inner, "execute_pending", None)
            if execute is not None:
                self._enqueue(execute(float(price), ts or datetime.now(timezone.utc)))
        if not self._queue:
            self.last_mark = None
            return None
        self.last_mark = self._queue_marks.popleft()
        return self._queue.popleft()

    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime,
                  volume: Optional[float] = None) -> int:
        fills = self.inner.on_candle(o, h, l, c, ts, volume=volume)
        self._enqueue(fills)
        return len(fills)

    def pending(self) -> int:
        return len(self._queue)

    def discard_queued(self) -> None:
        """서버 전략을 되돌릴 때 — 큐에 남은 체결은 되돌린 시간선의 것이라 버린다."""
        self._queue.clear()
        self._queue_marks.clear()
        if self.inner._entry_marks is not None:
            self.inner._entry_marks.clear()

    def fold(self, fills=(), resolutions=()) -> dict:
        """체결 기준 장부 — 실행기가 보고한 체결 · 끝난 주문을 안쪽 sim 장부에 접는다.

        주문이 다 정리되면 미뤄 둔 봉으로 판단하고, 나온 체결은 큐에 넣는다(다음 틱이 명령으로 만든다).
        """
        result = self.inner.fold(fills, resolutions)
        candle = self.inner.take_deferred()
        result["judged_deferred"] = None
        if candle is not None:
            t_ms, o, h, l, c, volume = candle
            ts = datetime.fromtimestamp(int(t_ms) / 1000, timezone.utc)
            self._enqueue(self.inner.on_candle(o, h, l, c, ts, volume=volume))
            result["judged_deferred"] = int(t_ms)
        return result

    def warmup(self, candles) -> None:
        # 웜업의 가상 체결은 버린다 — 진입 기록도 끈 채로 돈다(그리드 칸이 많으면 수백 봉 동안 버릴 기록이 쌓여 루프를 막는다).
        # 체결 기준 장부도 끈다 — 지난 봉의 가상 주문이 장부에 남으면 이후 판단이 계속 보류된다.
        marks, ledger = self.inner._entry_marks, self.inner.ledger
        self.inner._entry_marks = None
        self.inner.ledger = None
        try:
            self.inner.warmup(candles)
        finally:
            self.inner._entry_marks = [] if marks is not None else None
            self.inner.ledger = ledger
        self.discard_queued()

    def equity(self, price: float) -> float:
        return self.inner.equity(price)

    def state(self) -> dict:
        return self.inner.state()

    # 래퍼에 안 달면 실시간 세션에서 전략 상태가 조용히 빈 칸이 된다(bundle_gate 와 같은 자리).
    def strategy_state(self) -> dict:
        return self.inner.strategy_state()

    def restore_strategy(self, state: Optional[dict]) -> None:
        self.inner.restore_strategy(state)

    def restore_strategy_lots(self, state: Optional[dict]) -> None:
        self.inner.restore_strategy_lots(state)

    def restore(self, equity: float, *, in_position: bool, qty: float, entry_price: float,
                last_price: float, cooldown_until_ms: Optional[int] = None) -> None:
        self.inner.restore(equity, in_position=in_position, qty=qty, entry_price=entry_price,
                           last_price=last_price, cooldown_until_ms=cooldown_until_ms)

    def hold_entries_until(self, until_ms: Optional[int]) -> None:
        self.inner.hold_entries_until(until_ms)

    @property
    def liquidations(self) -> int:
        return self.inner.liquidations

    @property
    def liquidated_loss(self) -> float:
        return self.inner.liquidated_loss

    @property
    def bundle_gate(self):
        """묶음 한도는 안쪽 심이 집행한다 — 래퍼에 꽂아도 같은 곳에 닿아야 한다.

        위임이 없으면 래퍼에 꽂은 관문이 조용히 무시되고, 그 레그만 한도를 벗어난다.
        게다가 ``BundleGate.register`` 는 ``.inner`` 를 벗겨 담으므로 그 레그의 장부는
        여전히 합계에 들어간다 — 다른 레그는 막히니 한도가 작동하는 듯 보인다.
        """
        return self.inner.bundle_gate

    @bundle_gate.setter
    def bundle_gate(self, gate) -> None:
        self.inner.bundle_gate = gate
