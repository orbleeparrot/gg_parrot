"""Deterministic backtest engine.

Pure functions only: same macro + same OHLCV data always yields the same
result. No randomness, no I/O, no wall-clock dependence.

Execution assumptions (also documented in README):
  * Daily (1d) bars, sequential simulation.
  * Signals are evaluated on the bar's CLOSE and fills happen at that close
    (adjusted for slippage). Intrabar high/low is not used to trigger TP/SL.
  * Commission is charged per side; slippage worsens each fill; funding
    (shorts only, default 0) accrues per held day on notional.
  * Leverage (``macro.leverage``, default 1) uses an isolated-margin model: the
    position controls margin×leverage of notional and is force-closed at an
    approximate liquidation price (see ``engine.leverage``). At leverage 1 there
    is no liquidation and behaviour is identical to the original spot engine.
  * Short PnL is sign-reversed: price down = profit, price up = loss.
"""
from __future__ import annotations

import math
import os
from typing import List, Optional

import pandas as pd
from pydantic import BaseModel

from .candles import make_candle_sim
from .schema import CANDLE_TYPES, Macro, RuleType
from .stepper import DcaSim, PositionSim


class EquityPoint(BaseModel):
    t: str  # ISO timestamp
    equity: float


class BacktestResult(BaseModel):
    final_return_pct: float
    win_rate_pct: float
    mdd_pct: float
    total_trades: int
    initial_capital: float
    final_equity: float
    equity_curve: List[EquityPoint]
    # bars where a take-profit and stop-loss both triggered in one candle
    # (stop taken, conservative). 0 for the legacy A/B/C engines.
    same_bar_sl_bars: int = 0
    # leverage (isolated) liquidation summary: how many times the position was
    # force-closed at the liquidation price, and the total money wiped. Both 0
    # when leverage == 1 (no liquidation).
    liquidation_count: int = 0
    liquidated_loss: float = 0.0
    # --- benchmark & risk-adjusted metrics --------------------------------
    # "그냥 홀딩" baseline: the coin's own return over the same window (buy at the
    # first close, hold to the last). Lets the UI show strategy vs HODL. None
    # when there are too few bars to define it.
    buy_hold_return_pct: Optional[float] = None
    # Annualized Sharpe ratio from the equity curve's per-bar returns (risk-free
    # rate assumed 0). None when the curve is flat or too short to be meaningful.
    sharpe: Optional[float] = None
    # Profit factor = gross profit / gross loss across closed trades. None when
    # there were no losing trades (or no closed trades) to divide by.
    profit_factor: Optional[float] = None
    # Longest run of consecutive losing trades (a streak-risk read-out).
    max_consecutive_losses: int = 0


BACKTEST_EQUITY_MAX_POINTS = max(
    2,
    int(os.environ.get("BACKTEST_EQUITY_MAX_POINTS", "1000")),
)


def _lttb_equity_points(curve: List[EquityPoint], limit: int) -> List[EquityPoint]:
    """Largest-Triangle-Three-Buckets sampling using point order as the x-axis."""
    if limit >= len(curve) or limit <= 0:
        return list(curve)
    if limit == 2:
        return [curve[0], curve[-1]]

    bucket_width = (len(curve) - 2) / (limit - 2)
    sampled = [curve[0]]
    anchor_index = 0

    for bucket in range(limit - 2):
        average_start = int(math.floor((bucket + 1) * bucket_width)) + 1
        average_end = int(math.floor((bucket + 2) * bucket_width)) + 1
        average_end = min(average_end, len(curve))
        if average_start >= average_end:
            average_x = float(len(curve) - 1)
            average_y = curve[-1].equity
        else:
            count = average_end - average_start
            average_x = sum(range(average_start, average_end)) / count
            average_y = sum(curve[index].equity for index in range(average_start, average_end)) / count

        range_start = int(math.floor(bucket * bucket_width)) + 1
        range_end = int(math.floor((bucket + 1) * bucket_width)) + 1
        range_end = min(range_end, len(curve) - 1)
        anchor_y = curve[anchor_index].equity
        largest_area = -1.0
        selected_index = range_start
        for index in range(range_start, max(range_start + 1, range_end)):
            area = abs(
                (anchor_index - average_x) * (curve[index].equity - anchor_y)
                - (anchor_index - index) * (average_y - anchor_y)
            )
            if area > largest_area:
                largest_area = area
                selected_index = index
        sampled.append(curve[selected_index])
        anchor_index = selected_index

    sampled.append(curve[-1])
    return sampled


def compact_backtest_result(
    result: BacktestResult,
    *,
    max_points: Optional[int] = None,
) -> BacktestResult:
    """Copy a result with a bounded public equity curve.

    Metrics are calculated from the full curve before this function is called.
    Evenly spaced sampling keeps the first and final values and never mutates
    the engine result used by portfolio aggregation or AI explanations.
    """
    limit = BACKTEST_EQUITY_MAX_POINTS if max_points is None else max(2, int(max_points))
    curve = result.equity_curve
    if len(curve) <= limit:
        return result.model_copy(deep=True)
    sampled = _lttb_equity_points(curve, limit)
    return result.model_copy(update={"equity_curve": sampled}, deep=True)


# Fill-price / execution logic lives in stepper.py (shared with paper trading).

# Bars per year per candle interval, used to annualize the Sharpe ratio.
_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 525_600.0,
    "5m": 105_120.0,
    "15m": 35_040.0,
    "1h": 8_760.0,
    "4h": 2_190.0,
    "1d": 365.0,
}


def _sharpe(equity_curve: List[EquityPoint], periods_per_year: float) -> Optional[float]:
    """Annualized Sharpe from per-bar equity returns (risk-free rate = 0).

    None when the curve is too short or perfectly flat (std == 0), where the
    ratio is undefined rather than zero.
    """
    rets: List[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1].equity
        if prev > 0:
            rets.append(equity_curve[i].equity / prev - 1.0)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std <= 0:
        return None
    return round(mean / std * math.sqrt(periods_per_year), 3)


def _profit_factor(closed_trades: List[float]) -> Optional[float]:
    """Gross profit / gross loss over closed trades; None if there is no loss."""
    gross_profit = sum(p for p in closed_trades if p > 0)
    gross_loss = -sum(p for p in closed_trades if p < 0)
    if gross_loss <= 0:
        return None
    return round(gross_profit / gross_loss, 3)


def _max_consecutive_losses(closed_trades: List[float]) -> int:
    """Longest run of consecutive losing (PnL < 0) trades."""
    worst = run = 0
    for p in closed_trades:
        if p < 0:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    return worst


def _metrics(
    equity_curve: List[EquityPoint],
    closed_trades: List[float],
    total_trades: int,
    initial_capital: float,
    *,
    win_rate_override: Optional[float] = None,
    same_bar_sl_bars: int = 0,
    liquidation_count: int = 0,
    liquidated_loss: float = 0.0,
    buy_hold_return_pct: Optional[float] = None,
    periods_per_year: float = 365.0,
) -> BacktestResult:
    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    final_return_pct = (final_equity - initial_capital) / initial_capital * 100.0

    # Max drawdown on the equity curve (reported as a positive magnitude).
    peak = float("-inf")
    max_dd = 0.0
    for pt in equity_curve:
        if pt.equity > peak:
            peak = pt.equity
        if peak > 0:
            dd = (pt.equity - peak) / peak
            if dd < max_dd:
                max_dd = dd
    mdd_pct = -max_dd * 100.0

    if win_rate_override is not None:
        win_rate_pct = win_rate_override
    elif closed_trades:
        wins = sum(1 for p in closed_trades if p > 0)
        win_rate_pct = 100.0 * wins / len(closed_trades)
    else:
        win_rate_pct = 0.0

    return BacktestResult(
        final_return_pct=round(final_return_pct, 4),
        win_rate_pct=round(win_rate_pct, 4),
        mdd_pct=round(mdd_pct, 4),
        total_trades=total_trades,
        initial_capital=round(initial_capital, 2),
        final_equity=round(final_equity, 2),
        equity_curve=equity_curve,
        same_bar_sl_bars=same_bar_sl_bars,
        liquidation_count=liquidation_count,
        liquidated_loss=round(liquidated_loss, 2),
        buy_hold_return_pct=(round(buy_hold_return_pct, 4) if buy_hold_return_pct is not None else None),
        sharpe=_sharpe(equity_curve, periods_per_year),
        profit_factor=_profit_factor(closed_trades),
        max_consecutive_losses=_max_consecutive_losses(closed_trades),
    )


def _buy_hold_return_pct(closes) -> Optional[float]:
    """Coin's own return over the window: buy at first close, hold to last."""
    if len(closes) < 2:
        return None
    first, last = float(closes[0]), float(closes[-1])
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Rule A / B: single-position long or short --------------------------
def _run_single_position(macro: Macro, df: pd.DataFrame) -> BacktestResult:
    """Drive the shared PositionSim over candle closes (same machine paper uses)."""
    closes = df["close"].to_numpy(dtype=float)
    times = df["timestamp"].tolist()

    sim = PositionSim(macro)
    equity_curve: List[EquityPoint] = []
    for i in range(len(closes)):
        c = closes[i]
        sim.step(c, pd.Timestamp(times[i]).to_pydatetime())
        equity_curve.append(EquityPoint(t=_iso(times[i]), equity=round(sim.equity(c), 4)))

    return _metrics(
        equity_curve,
        sim.closed_trades,
        len(sim.closed_trades),
        sim.initial_capital,
        liquidation_count=sim.liquidations,
        liquidated_loss=sim.liquidated_loss,
        buy_hold_return_pct=_buy_hold_return_pct(closes),
        periods_per_year=_PERIODS_PER_YEAR.get(macro.candle_interval, 365.0),
    )


# --- Rule C: periodic DCA (long only) -----------------------------------
def _run_dca(macro: Macro, df: pd.DataFrame) -> BacktestResult:
    closes = df["close"].to_numpy(dtype=float)
    times = df["timestamp"].tolist()
    n = len(closes)

    amount_per_buy = float(macro.params["amount_per_buy"])
    interval = max(1, int(macro.params["interval_days"]))

    # Plan the buys up front so the return base matches the original engine,
    # then drive the shared DcaSim (same machine paper uses).
    num_buys = (n - 1) // interval + 1 if n > 0 else 0
    initial_capital = max(num_buys * amount_per_buy, 1e-9)

    sim = DcaSim(macro, initial_capital=initial_capital, max_buys=num_buys)
    equity_curve: List[EquityPoint] = []
    for i in range(n):
        c = closes[i]
        sim.step(c, pd.Timestamp(times[i]).to_pydatetime())
        equity_curve.append(EquityPoint(t=_iso(times[i]), equity=round(sim.equity(c), 4)))

    final_return = (
        (equity_curve[-1].equity - initial_capital) / initial_capital * 100.0
        if equity_curve
        else 0.0
    )
    # DCA is buy-and-hold: no round trips, so win rate reflects the final outcome.
    win_rate = 100.0 if final_return > 0 else 0.0
    return _metrics(
        equity_curve,
        closed_trades=[],
        total_trades=num_buys,
        initial_capital=initial_capital,
        win_rate_override=win_rate,
        buy_hold_return_pct=_buy_hold_return_pct(closes),
        periods_per_year=_PERIODS_PER_YEAR.get(macro.candle_interval, 365.0),
    )


# --- Rule D~J: shared candle engine (multi-order + indicator strategies) -
def _run_candle_engine(macro: Macro, df: pd.DataFrame) -> BacktestResult:
    """Drive the incremental candle sim over OHLC bars (same machine paper uses).

    Look-ahead safety, same-bar stop-loss priority and multi-order accounting
    all live in ``engine.candles``; this just feeds it closed bars.
    """
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = df["timestamp"].tolist()

    sim = make_candle_sim(macro)
    equity_curve: List[EquityPoint] = []
    for i in range(len(closes)):
        ts = pd.Timestamp(times[i]).to_pydatetime()
        sim.on_candle(opens[i], highs[i], lows[i], closes[i], ts)
        equity_curve.append(EquityPoint(t=_iso(times[i]), equity=round(sim.equity(closes[i]), 4)))

    return _metrics(
        equity_curve,
        sim.closed_trades,
        len(sim.closed_trades),
        sim.initial_capital,
        same_bar_sl_bars=sim.same_bar_sl,
        liquidation_count=sim.liquidations,
        liquidated_loss=sim.liquidated_loss,
        buy_hold_return_pct=_buy_hold_return_pct(closes),
        periods_per_year=_PERIODS_PER_YEAR.get(macro.candle_interval, 365.0),
    )


def run_backtest(macro: Macro, df: pd.DataFrame) -> BacktestResult:
    """Run a deterministic backtest of ``macro`` over OHLCV ``df``.

    ``df`` columns: timestamp, open, high, low, close, volume (chronological).
    """
    if df is None or len(df) == 0:
        raise ValueError("no price data provided")
    df = df.sort_values("timestamp").reset_index(drop=True)

    if macro.rule_type in CANDLE_TYPES:
        return _run_candle_engine(macro, df)
    if macro.rule_type is RuleType.C:
        return _run_dca(macro, df)
    return _run_single_position(macro, df)
