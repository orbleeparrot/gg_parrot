"""Stateful, price-by-price execution machines shared by backtest and paper.

The backtest engine feeds these one *candle close* at a time; paper trading
feeds them one *live tick* at a time. Same fill/commission/slippage/condition
logic — only the data source differs. This is the single source of truth for
"가상 체결" so the two paths can never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .leverage import liquidation_price
from .schema import Macro, PositionSide, RuleType


# --- fill-price helpers (slippage in percent) ---------------------------
def buy_fill(close: float, slippage_pct: float) -> float:
    """Buying pays up: fill is worse (higher) than close."""
    return close * (1.0 + slippage_pct / 100.0)


def sell_fill(close: float, slippage_pct: float) -> float:
    """Selling gets less: fill is worse (lower) than close."""
    return close * (1.0 - slippage_pct / 100.0)


@dataclass
class Fill:
    """One simulated execution produced by a sim step."""

    side: str  # "buy" | "sell" | "short" | "cover"
    price: float
    qty: float
    equity_after: float
    return_pct: float
    # 실행기 명령·로그용 메타. 사유는 사람이 읽는 한 줄("RSI 23.1 ≤ 25 · 진입"), 직전 수량은
    # 청산 비율(qty / qty_before)을 낼 때 쓴다. 장부가 이미 바뀐 뒤 _fill 이 불리므로 되계산한다.
    reason: str = ""
    qty_before: float = 0.0


EXIT_SIDES = frozenset({"sell", "cover"})
_DEFAULT_REASON = {"buy": "진입", "short": "진입", "sell": "청산", "cover": "청산"}


def _fill_meta(side: str, qty: float, qty_after: float, reason: str) -> dict:
    before = qty_after + qty if side in EXIT_SIDES else max(0.0, qty_after - qty)
    return {"reason": reason or _DEFAULT_REASON.get(side, ""), "qty_before": float(before)}


class PositionSim:
    """Single-position machine for rule A (TP/SL re-entry) and B (band)."""

    def __init__(self, macro: Macro, initial_capital: Optional[float] = None) -> None:
        self.side = macro.position_side
        self.rule_type = macro.rule_type
        self.comm = macro.fees.commission_pct
        self.slip = macro.fees.slippage_pct
        self.funding = macro.fees.funding_pct
        self.invest_ratio = macro.risk.invest_ratio
        self.sl = macro.risk.stop_loss_pct  # may be None
        # Time-based common risk (same semantics as engine.candles). These only
        # act when a timestamp is supplied to ``step`` (backtest candle time /
        # paper wall-clock); with ts=None they stay inert so the legacy
        # price-only contract is unchanged.
        self.daily_max_loss = macro.risk.daily_max_loss_pct
        self.max_holding_hours = macro.risk.max_holding_hours
        self.cooldown_minutes = macro.risk.cooldown_minutes
        self.leverage = int(macro.leverage or 1)  # 1 == spot, no liquidation

        p = macro.params
        if self.rule_type is RuleType.A:
            self.tp = float(p["take_profit_pct"])
            self.buy_price = self.sell_price = None
        else:  # B
            self.tp = None
            self.buy_price = float(p["buy_price"])
            self.sell_price = float(p["sell_price"])

        self.initial_capital = float(
            initial_capital if initial_capital is not None else macro.initial_capital
        )
        self.cash = self.initial_capital
        self.in_pos = False
        self.qty = 0.0
        self.entry_fill = 0.0
        self.entry_commission = 0.0
        self.margin = 0.0  # equity committed to the open position (leverage bookkeeping)
        self.liq_price: Optional[float] = None  # isolated liquidation price (leverage > 1)
        self.closed_trades: List[float] = []
        # Liquidation stats surfaced to the result / paper status.
        self.liquidations = 0
        self.liquidated_loss = 0.0

        # Time-based common-risk state (mirrors engine.candles.CandleSim).
        self._day: Optional[str] = None
        self._day_start_equity = self.initial_capital
        self._halted_day: Optional[str] = None
        self._cooldown_until: Optional[datetime] = None
        self._entry_time: Optional[datetime] = None

    # -- exit / entry conditions (identical to the backtest semantics) --
    # Split into stop vs. profit so a stop-loss exit can trigger the re-entry
    # cooldown; their union reproduces the previous ``_should_exit``.
    def _stop_hit(self, c: float) -> bool:
        if not self.sl:
            return False
        if self.side is PositionSide.LONG:
            return c <= self.entry_fill * (1 - self.sl / 100.0)
        return c >= self.entry_fill * (1 + self.sl / 100.0)

    def _profit_hit(self, c: float) -> bool:
        if self.side is PositionSide.LONG:
            if self.rule_type is RuleType.A:
                return c >= self.entry_fill * (1 + self.tp / 100.0)
            return c >= self.sell_price  # B long
        # SHORT: price falling is profit
        if self.rule_type is RuleType.A:
            return c <= self.entry_fill * (1 - self.tp / 100.0)
        return c <= self.buy_price  # B short

    def _should_enter(self, c: float) -> bool:
        if self.rule_type is RuleType.A:
            return True  # always re-enter when flat
        if self.side is PositionSide.LONG:  # B long
            return c <= self.buy_price
        return c >= self.sell_price  # B short

    def _liquidated(self, c: float) -> bool:
        """True if this close breaches the isolated liquidation price."""
        if self.liq_price is None:
            return False
        if self.side is PositionSide.LONG:
            return c <= self.liq_price
        return c >= self.liq_price

    def _do_close(self, exec_price: float, mark: float, ts: Optional[datetime] = None, is_stop: bool = False) -> Fill:
        """Close the position at ``exec_price``.

        Returns the margin plus realized PnL to cash. With leverage the entry only
        locked ``margin`` (not the full notional), so the margin is added back here;
        at leverage 1 ``margin == notional`` and this reduces to the old spot math.

        A stop-loss exit (``is_stop``) arms the re-entry cooldown.
        """
        traded_qty = self.qty
        if self.side is PositionSide.LONG:
            f = sell_fill(exec_price, self.slip)
            exit_comm = self.qty * f * self.comm / 100.0
            self.cash += self.margin + self.qty * (f - self.entry_fill) - exit_comm
            pnl = self.qty * (f - self.entry_fill) - self.entry_commission - exit_comm
            side = "sell"
        else:
            f = buy_fill(exec_price, self.slip)  # cover
            exit_comm = self.qty * f * self.comm / 100.0
            self.cash += self.qty * (self.entry_fill - f) - exit_comm
            pnl = self.qty * (self.entry_fill - f) - self.entry_commission - exit_comm
            side = "cover"
        self.closed_trades.append(pnl)
        self.in_pos = False
        self.qty = 0.0
        self.margin = 0.0
        self.liq_price = None
        self._entry_time = None
        if is_stop and self.cooldown_minutes > 0 and ts is not None:
            self._cooldown_until = ts + timedelta(minutes=self.cooldown_minutes)
        return self._fill(side, f, traded_qty, mark, reason="손절" if is_stop else "청산")

    def _liquidate(self, mark: float, ts: Optional[datetime] = None) -> Fill:
        """Force-close a leveraged position: the whole committed margin is lost
        (isolated). The loss is capped at the margin — cash cannot go past it."""
        traded_qty = self.qty
        px = self.liq_price if self.liq_price is not None else self.entry_fill
        # Long removed the margin from cash at entry (so cash already floors the
        # loss); short kept it, so wipe it now. Either way equity == remaining cash.
        if self.side is PositionSide.SHORT:
            self.cash -= self.margin
        side = "sell" if self.side is PositionSide.LONG else "cover"
        self.closed_trades.append(-self.margin)
        self.liquidations += 1
        self.liquidated_loss += self.margin
        self.in_pos = False
        self.qty = 0.0
        self.margin = 0.0
        self.liq_price = None
        self._entry_time = None
        if self.cooldown_minutes > 0 and ts is not None:
            self._cooldown_until = ts + timedelta(minutes=self.cooldown_minutes)
        return self._fill(side, px, traded_qty, mark, reason="강제 청산")

    # -- time-based common risk (inert unless a timestamp is supplied) -----
    def _roll_day(self, c: float, ts: Optional[datetime]) -> None:
        if ts is None:
            return
        day = ts.strftime("%Y-%m-%d")
        if day != self._day:
            self._day = day
            self._day_start_equity = self.equity(c)
            if self._halted_day is not None and self._halted_day != day:
                self._halted_day = None

    def _entry_blocked(self, ts: Optional[datetime]) -> bool:
        if self._halted_day is not None and self._halted_day == self._day:
            return True
        if self._cooldown_until is not None and ts is not None and ts < self._cooldown_until:
            return True
        return False

    def _daily_loss_breached(self, c: float) -> bool:
        if not self.daily_max_loss or self._day is None or self._day_start_equity <= 0:
            return False
        dd = (self.equity(c) - self._day_start_equity) / self._day_start_equity * 100.0
        return dd <= -self.daily_max_loss

    def _max_holding_exceeded(self, ts: Optional[datetime]) -> bool:
        return (
            bool(self.max_holding_hours)
            and ts is not None
            and self._entry_time is not None
            and (ts - self._entry_time) >= timedelta(hours=self.max_holding_hours)
        )

    def step(self, price: float, ts: Optional[datetime] = None) -> Optional[Fill]:
        c = price
        self._roll_day(c, ts)

        # Funding accrues while holding a short.
        if self.in_pos and self.side is PositionSide.SHORT and self.funding > 0:
            self.cash -= self.qty * c * self.funding / 100.0

        fill: Optional[Fill] = None
        if self.in_pos:
            # Priority mirrors engine.candles: liquidation -> stop-loss ->
            # max-holding -> daily-loss halt -> take-profit / band exit.
            if self._liquidated(c):
                fill = self._liquidate(c, ts)
            elif self._stop_hit(c):
                fill = self._do_close(c, c, ts=ts, is_stop=True)
            elif self._max_holding_exceeded(ts):
                fill = self._do_close(c, c, ts=ts)
            elif self._daily_loss_breached(c):
                fill = self._do_close(c, c, ts=ts)
                self._halted_day = self._day
            elif self._profit_hit(c):
                fill = self._do_close(c, c, ts=ts)
        else:
            # A daily-loss breach halts new entries for the rest of the day even
            # when already flat.
            if self._daily_loss_breached(c):
                self._halted_day = self._day
            elif not self._entry_blocked(ts) and self._should_enter(c):
                margin = self.invest_ratio * self.cash  # cash == equity when flat
                notional = margin * self.leverage
                if self.side is PositionSide.LONG:
                    f = buy_fill(c, self.slip)
                    self.qty = notional / f
                    self.entry_commission = notional * self.comm / 100.0
                    self.cash -= margin + self.entry_commission
                    side = "buy"
                else:
                    f = sell_fill(c, self.slip)
                    self.qty = notional / f
                    self.entry_commission = notional * self.comm / 100.0
                    self.cash -= self.entry_commission
                    side = "short"
                self.entry_fill = f
                self.margin = margin
                self.liq_price = liquidation_price(
                    f, self.leverage, self.side, commission_pct=self.comm
                )
                self.in_pos = True
                self._entry_time = ts
                fill = self._fill(side, f, self.qty, c)
        return fill

    def equity(self, price: float) -> float:
        if not self.in_pos:
            return self.cash
        if self.side is PositionSide.LONG:
            # free cash + locked margin + unrealized PnL (== cash + qty*price at 1x)
            return self.cash + self.margin + self.qty * (price - self.entry_fill)
        return self.cash + self.qty * (self.entry_fill - price)

    def state(self) -> dict:
        """리더보드가 그리는 포지션 상태 — 조회만, 부작용 없음."""
        cooldown = self._cooldown_until
        return {
            "in_position": bool(self.in_pos),
            "dir": 1 if self.side is PositionSide.LONG else -1,
            "qty": float(self.qty),
            "entry_price": float(self.entry_fill) if self.in_pos else 0.0,
            "cooldown_until_ms": int(cooldown.timestamp() * 1000) if cooldown is not None else None,
            "halted_today": self._halted_day is not None and self._halted_day == self._day,
        }

    def restore(self, equity: float, *, in_position: bool, qty: float, entry_price: float,
                last_price: float, cooldown_until_ms: Optional[int] = None) -> None:
        """재기동 복구 — 마지막 체크포인트의 자산·포지션에서 이어 간다.

        포지션이 있으면 수량·진입가를 되살리고, 현금은 ``equity(last_price) == equity`` 가 되도록
        역산한다(수수료는 이미 체결 시점에 반영돼 있어 0 으로 둔다). 일일 손실 기준선·보유 시간은
        복구 시점부터 새로 센다.
        """
        self.in_pos = False
        self.qty = 0.0
        self.entry_fill = 0.0
        self.entry_commission = 0.0
        self.margin = 0.0
        self.liq_price = None
        self._entry_time = None
        self.cash = float(equity)
        if in_position and qty > 0 and entry_price > 0:
            self.in_pos = True
            self.qty = float(qty)
            self.entry_fill = float(entry_price)
            # 진입 때와 같은 장부: 롱은 마진을 현금에서 떼어 두고, 숏은 현금에 남긴다(_liquidate 가 그때 뺀다).
            self.margin = self.qty * self.entry_fill / self.leverage
            if self.side is PositionSide.LONG:
                self.cash = float(equity) - self.margin - self.qty * (float(last_price) - self.entry_fill)
            else:
                self.cash = float(equity) - self.qty * (self.entry_fill - float(last_price))
            self.liq_price = liquidation_price(self.entry_fill, self.leverage, self.side, commission_pct=self.comm)
            self._entry_time = datetime.now(timezone.utc)
        self._cooldown_until = (
            datetime.fromtimestamp(cooldown_until_ms / 1000, timezone.utc) if cooldown_until_ms else None
        )

    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, self.qty if self.in_pos else 0.0, reason))


class DcaSim:
    """Periodic DCA machine (rule C, long only). Buys every ``interval`` steps.

    Backtest passes ``max_buys`` (bars/interval) and the planned capital so the
    result matches the original engine; paper trading passes a virtual balance
    and buys until it runs out (``max_buys=None``).
    """

    def __init__(
        self,
        macro: Macro,
        initial_capital: float,
        max_buys: Optional[int] = None,
    ) -> None:
        self.comm = macro.fees.commission_pct
        self.slip = macro.fees.slippage_pct
        self.sl = macro.risk.stop_loss_pct
        # For DCA, daily-max-loss means "stop buying more today once down N%".
        # (max_holding / cooldown don't fit buy-and-accumulate and are gated off
        # in the builder UI for rule C.)
        self.daily_max_loss = macro.risk.daily_max_loss_pct
        p = macro.params
        self.amount_per_buy = float(p["amount_per_buy"])
        self.interval = max(1, int(p["interval_days"]))
        self.max_buys = max_buys

        self.initial_capital = float(initial_capital)
        self.cash = self.initial_capital
        self.qty = 0.0
        self.cost_basis = 0.0
        self.stopped = False
        self.buys_done = 0
        self._step = 0
        # DCA (rule C) never uses leverage; expose the fields for a uniform interface.
        self.liquidations = 0
        self.liquidated_loss = 0.0
        # daily-max-loss state
        self._day: Optional[str] = None
        self._day_start_equity = self.initial_capital
        self._halted_day: Optional[str] = None

    def step(self, price: float, ts: Optional[datetime] = None) -> Optional[Fill]:
        c = price
        fill: Optional[Fill] = None

        # Day roll for daily-max-loss (inert when ts is None).
        if ts is not None:
            day = ts.strftime("%Y-%m-%d")
            if day != self._day:
                self._day = day
                self._day_start_equity = self.equity(c)
                if self._halted_day is not None and self._halted_day != day:
                    self._halted_day = None

        # Optional stop loss on the accumulated position.
        if self.sl and self.qty > 0 and not self.stopped:
            if self.qty * c <= self.cost_basis * (1 - self.sl / 100.0):
                f = sell_fill(c, self.slip)
                proceeds = self.qty * f
                traded = self.qty
                self.cash += proceeds - proceeds * self.comm / 100.0
                self.qty = 0.0
                self.cost_basis = 0.0
                self.stopped = True
                fill = self._fill("sell", f, traded, c)

        # Daily-max-loss: once today's equity is down past the threshold, buy no
        # more for the rest of the day (position is kept — DCA holds).
        if self.daily_max_loss and self._day is not None and self._day_start_equity > 0:
            dd = (self.equity(c) - self._day_start_equity) / self._day_start_equity * 100.0
            if dd <= -self.daily_max_loss:
                self._halted_day = self._day

        # Scheduled buy.
        halted_today = self._halted_day is not None and self._halted_day == self._day
        can_buy = (
            not self.stopped
            and not halted_today
            and self._step % self.interval == 0
            and (self.max_buys is None or self.buys_done < self.max_buys)
            and self.cash >= self.amount_per_buy
        )
        if can_buy and fill is None:
            fee = self.amount_per_buy * self.comm / 100.0
            invest = self.amount_per_buy - fee
            f = buy_fill(c, self.slip)
            self.qty += invest / f
            self.cash -= self.amount_per_buy
            self.cost_basis += self.amount_per_buy
            self.buys_done += 1
            fill = self._fill("buy", f, invest / f, c)

        self._step += 1
        return fill

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def state(self) -> dict:
        """리더보드가 그리는 DCA 상태 — 조회만, 부작용 없음."""
        held = self.qty > 0
        return {
            "in_position": held,
            "dir": 1,
            "qty": float(self.qty),
            "entry_price": float(self.cost_basis / self.qty) if held else 0.0,
            "cooldown_until_ms": None,
            "halted_today": self._halted_day is not None and self._halted_day == self._day,
        }

    def restore(self, equity: float, *, in_position: bool, qty: float, entry_price: float,
                last_price: float, cooldown_until_ms: Optional[int] = None) -> None:
        """재기동 복구 — 누적 수량·평단을 되살리고 현금은 ``equity(last_price) == equity`` 로 역산."""
        self.qty = 0.0
        self.cost_basis = 0.0
        self.cash = float(equity)
        if in_position and qty > 0 and entry_price > 0:
            self.qty = float(qty)
            self.cost_basis = self.qty * float(entry_price)
            self.cash = float(equity) - self.qty * float(last_price)

    def _fill(self, side: str, price: float, qty: float, mark: float, reason: str = "") -> Fill:
        eq = self.equity(mark)
        ret = (eq - self.initial_capital) / self.initial_capital * 100.0
        return Fill(side=side, price=price, qty=qty, equity_after=eq, return_pct=ret,
                    **_fill_meta(side, qty, self.qty, reason))


def make_sim(macro: Macro, initial_capital: Optional[float] = None):
    """Build the right sim for a macro (paper trading entry point)."""
    from .schema import CANDLE_TYPES

    if macro.rule_type in CANDLE_TYPES:
        # Types D~J are candle-based; aggregate ticks into candles for paper.
        from .candles import CandleAggregatorSim

        return CandleAggregatorSim(macro, initial_capital=initial_capital)
    if macro.rule_type is RuleType.C:
        base = initial_capital if initial_capital is not None else 1_000_000.0
        return DcaSim(macro, initial_capital=base, max_buys=None)
    return PositionSim(macro, initial_capital=initial_capital)
