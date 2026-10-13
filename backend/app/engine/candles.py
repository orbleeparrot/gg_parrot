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

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, List, Optional

from .leverage import liquidation_price
from .schema import Macro, PositionSide, RuleType
from .stepper import Fill, _fill_meta, buy_fill, sell_fill

_INTERVAL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


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
        self.stopped = False  # hard stop (band exit / reenter disabled)
        # 진입 필터 — 없으면 None. on_candle 이 봉이 끝난 뒤 갱신하고 _entry_blocked 가 묻는다.
        from .entry_filter import make_filter  # 늦은 import: entry_filter -> candles 순환 방지
        self.entry_filter = make_filter(macro)
        # 묶음 한도 — 묶음을 조립하는 쪽(백테스트 루프 · paper.start_session)이 밖에서 꽂는다.
        # 심은 자기가 묶음의 일부인지 모른다.
        self.bundle_gate = None

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
        return self.cash + margin + unreal

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
    def _open_long(self, margin: float, close: float, ts: datetime, mark: float, *, reason: str = "진입") -> Optional[Fill]:
        margin = min(margin, self.cash)
        if margin <= 1e-9:
            return None
        notional = margin * self.leverage
        f = buy_fill(close, self.slip)
        qty = notional / f
        self.cash -= margin + notional * self.comm / 100.0
        self.lots.append(_Lot(qty=qty, fill=f, margin=margin))
        if self._entry_time is None:
            self._entry_time = ts
        return self._fill("buy", f, qty, mark, reason)

    def _open_short(self, close: float, ts: datetime, mark: float, *, reason: str = "진입") -> Optional[Fill]:
        margin = self.invest_ratio * self.cash
        if margin <= 1e-9:
            return None
        notional = margin * self.leverage
        f = sell_fill(close, self.slip)
        qty = notional / f
        self.cash -= notional * self.comm / 100.0
        self.lots.append(_Lot(qty=qty, fill=f, margin=margin))
        self._entry_time = ts
        return self._fill("short", f, qty, mark, reason)

    def _close_all(self, close: float, mark: float, *, is_stop: bool, ts: datetime, reason: Optional[str] = None) -> Optional[Fill]:
        if not self.in_position():
            return None
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
        return self._fill(side, f, qty, mark, reason if reason is not None else ("손절" if is_stop else "청산"))

    def _reduce_long(self, fraction: float, close: float, mark: float, ts: datetime) -> Optional[Fill]:
        """Sell ``fraction`` (0<f<=1) of the long book at ``close``.

        Banks the freed margin + realized PnL back to cash and shrinks each lot
        proportionally (dust lots dropped). Long book only — used by the SAR
        defense partial exit. ``_close_all`` remains the full-close path.
        """
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
        return self._fill(side, px, qty, mark, "강제 청산")

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
                notional = self.per_grid * self.leverage
                f = buy_fill(buy_level, self.slip)
                qty = notional / f
                self.cash -= self.per_grid + notional * self.comm / 100.0
                lot = _Lot(qty=qty, fill=f, margin=self.per_grid)
                self.holdings[i] = lot
                self.lots.append(lot)
                if self._entry_time is None:
                    self._entry_time = ts
                fills.append(self._fill("buy", f, qty, c))

    def _remove_lot(self, lot: _Lot) -> None:
        try:
            self.lots.remove(lot)
        except ValueError:
            pass
        if not self.lots:
            self._entry_time = None


# --- H: martingale / safety orders (long, multi-order) ------------------
class MartingaleSim(CandleSim):
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

    def _reset_position_state(self) -> None:
        self._so_done = 0
        self._base_price = 0.0

    # 봉에서 다시 못 구한다 — 잊으면 추가매수 차수가 0 으로 돌아가 이미 채운 단계를 다시 산다.
    def strategy_state(self) -> dict:
        return {"so_done": int(self._so_done), "base_price": float(self._base_price)}

    def restore_strategy(self, state: Optional[dict]) -> None:
        if not state:
            return
        if state.get("so_done") is not None:
            self._so_done = int(state["so_done"])
        if state.get("base_price") is not None:
            self._base_price = float(state["base_price"])

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
            if both_touch:
                self.same_bar_sl += 1
            self._so_done = 0
            return
        if self.in_position():
            avg = self.avg_entry()
            tp_px = avg * (1 + self.tp / 100.0)
            if h >= tp_px:
                f = self._close_all(tp_px, tp_px, is_stop=False, ts=ts)
                if f:
                    fills.append(f)
                self._so_done = 0
                return
            # safety orders on the way down
            while self._so_done < self.max_so and not self._entries_held(ts):
                so_px = self._next_so_price()
                if l <= so_px and self.cash > 1e-9:
                    size = self.so_size * (self.vol_scale ** self._so_done)
                    f = self._open_long(size, so_px, ts, c)
                    if f:
                        fills.append(f)
                    self._so_done += 1
                else:
                    break
            return
        # flat -> base order
        if self._entry_blocked(ts):
            return
        f = self._open_long(self.base, c, ts, c)
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

    def step(self, price: float, ts: Optional[datetime] = None) -> Optional[Fill]:
        """큐의 Fill 하나를 꺼내 준다. 큐가 비었고 sim 에 '다음 시가' 의도가 걸려 있으면 이 틱 가격에 실행한다.

        마감봉만 오는 실시간에서는 봉 N 마감에 정한 의도를 봉 N+1 의 on_candle 까지 기다리면 한 간격
        늦게(N+1 마감 무렵) 체결된다. 봉이 온 뒤 첫 틱(≈ N+1 시가)에 실행해 백테스트의 "다음 시가" 와 맞춘다.
        """
        if not self._queue:
            execute = getattr(self.inner, "execute_pending", None)
            if execute is not None:
                self._queue.extend(execute(float(price), ts or datetime.now(timezone.utc)))
        return self._queue.popleft() if self._queue else None

    def on_candle(self, o: float, h: float, l: float, c: float, ts: datetime,
                  volume: Optional[float] = None) -> int:
        fills = self.inner.on_candle(o, h, l, c, ts, volume=volume)
        self._queue.extend(fills)
        return len(fills)

    def pending(self) -> int:
        return len(self._queue)

    def discard_queued(self) -> None:
        """서버 전략을 되돌릴 때 — 큐에 남은 체결은 되돌린 시간선의 것이라 버린다."""
        self._queue.clear()

    def warmup(self, candles) -> None:
        self.inner.warmup(candles)
        self._queue.clear()

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
