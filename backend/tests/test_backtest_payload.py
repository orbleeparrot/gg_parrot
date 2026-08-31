"""Public backtest payload size contracts."""
from __future__ import annotations

from app.engine import backtest as backtest_mod
from app.engine.backtest import BacktestResult, EquityPoint


def _result(points: int) -> BacktestResult:
    return BacktestResult(
        final_return_pct=9.0,
        win_rate_pct=50.0,
        mdd_pct=4.0,
        total_trades=3,
        initial_capital=100.0,
        final_equity=109.0,
        equity_curve=[
            EquityPoint(t=f"2026-01-{(index % 28) + 1:02d}T00:00:00Z", equity=100.0 + index)
            for index in range(points)
        ],
    )


def test_compact_backtest_result_limits_curve_without_mutating_metrics():
    original = _result(101)
    assert hasattr(backtest_mod, "compact_backtest_result")

    compact = backtest_mod.compact_backtest_result(original, max_points=10)

    assert len(compact.equity_curve) == 10
    assert compact.equity_curve[0] == original.equity_curve[0]
    assert compact.equity_curve[-1] == original.equity_curve[-1]
    assert compact.final_return_pct == original.final_return_pct
    assert len(original.equity_curve) == 101


def test_compact_backtest_result_preserves_a_large_mid_curve_spike():
    original = _result(101)
    for point in original.equity_curve:
        point.equity = 100.0
    original.equity_curve[50].equity = 1_000.0

    compact = backtest_mod.compact_backtest_result(original, max_points=10)

    assert any(point.equity == 1_000.0 for point in compact.equity_curve)
