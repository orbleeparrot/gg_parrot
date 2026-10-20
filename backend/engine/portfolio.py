"""Portfolio (multi-symbol) aggregation.

Runs the SAME macro on each symbol with capital split evenly, then combines the
per-symbol equity curves into one portfolio curve. Reuses the existing
single-symbol backtest untouched — this is a pure aggregation layer, so every
rule type (A~K) works across symbols for free.

Returns the aggregated result in the SAME :class:`BacktestResult` shape (so the
UI renders it unchanged) plus a per-symbol breakdown (the "어느 코인에서 잘
먹혔나" comparison view).
"""
from __future__ import annotations

from typing import List, Tuple

from .backtest import _PERIODS_PER_YEAR, _sharpe, BacktestResult, EquityPoint


def aggregate(
    results: List[Tuple[str, BacktestResult]],
    *,
    candle_interval: str = "1d",
) -> Tuple[BacktestResult, List[dict]]:
    """Combine per-symbol results into (aggregated BacktestResult, per_symbol[])."""
    per_symbol = [
        {
            "symbol": sym,
            "final_return_pct": r.final_return_pct,
            "mdd_pct": r.mdd_pct,
            "total_trades": r.total_trades,
            "win_rate_pct": r.win_rate_pct,
            "final_equity": r.final_equity,
            "buy_hold_return_pct": r.buy_hold_return_pct,
            "liquidation_count": r.liquidation_count,
        }
        for sym, r in results
    ]

    total_initial = sum(r.initial_capital for _, r in results) or 1e-9

    # Union of timestamps; forward-fill each symbol's equity to that instant; sum.
    all_ts = sorted({pt.t for _, r in results for pt in r.equity_curve})
    combined: List[EquityPoint] = []
    if all_ts:
        idx = {sym: 0 for sym, _ in results}
        last = {sym: r.initial_capital for sym, r in results}
        curves = {sym: r.equity_curve for sym, r in results}
        for t in all_ts:
            total = 0.0
            for sym, _ in results:
                c = curves[sym]
                i = idx[sym]
                while i < len(c) and c[i].t <= t:
                    last[sym] = c[i].equity
                    i += 1
                idx[sym] = i
                total += last[sym]
            combined.append(EquityPoint(t=t, equity=round(total, 4)))

    final_equity = combined[-1].equity if combined else total_initial
    final_return = (final_equity - total_initial) / total_initial * 100.0

    peak = float("-inf")
    max_dd = 0.0
    for pt in combined:
        if pt.equity > peak:
            peak = pt.equity
        if peak > 0:
            dd = (pt.equity - peak) / peak
            if dd < max_dd:
                max_dd = dd
    mdd = -max_dd * 100.0

    # Capital-weighted averages for rate-style metrics.
    def _weighted(field: str):
        num = den = 0.0
        for _, r in results:
            v = getattr(r, field)
            if v is not None:
                num += v * r.initial_capital
                den += r.initial_capital
        return (num / den) if den > 0 else None

    agg = BacktestResult(
        final_return_pct=round(final_return, 4),
        win_rate_pct=round(_weighted("win_rate_pct") or 0.0, 4),
        mdd_pct=round(mdd, 4),
        total_trades=sum(r.total_trades for _, r in results),
        initial_capital=round(total_initial, 2),
        final_equity=round(final_equity, 2),
        equity_curve=combined,
        liquidation_count=sum(r.liquidation_count for _, r in results),
        liquidated_loss=round(sum(r.liquidated_loss for _, r in results), 2),
        buy_hold_return_pct=(
            round(_weighted("buy_hold_return_pct"), 4)
            if _weighted("buy_hold_return_pct") is not None
            else None
        ),
        sharpe=_sharpe(combined, _PERIODS_PER_YEAR.get(candle_interval, 365.0)),
        profit_factor=None,  # not aggregated across symbols; per-symbol shows detail
        max_consecutive_losses=max((r.max_consecutive_losses for _, r in results), default=0),
    )
    return agg, per_symbol
