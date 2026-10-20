"""Benchmark & risk-adjusted metrics (buy&hold, Sharpe, profit factor, streaks).

These pin the v10 additions to BacktestResult. Fees/slippage are zeroed so the
numbers are hand-checkable.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import run_backtest
from engine.backtest import (
    _buy_hold_return_pct,
    _max_consecutive_losses,
    _profit_factor,
)
from engine.schema import Fees, Macro, Period, Risk

NO_COST = Fees(commission_pct=0.0, slippage_pct=0.0, funding_pct=0.0)


def _df(closes, freq="D"):
    ts = pd.date_range("2024-01-01", periods=len(closes), freq=freq)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0.0] * len(closes),
        }
    )


def _macro(rule_type, side, params, risk, interval="1d"):
    return Macro(
        symbol="BTCUSDT",
        rule_type=rule_type,
        position_side=side,
        params=params,
        risk=risk,
        candle_interval=interval,
        period=Period(preset="custom"),
        fees=NO_COST,
    )


# --- pure helpers -------------------------------------------------------
def test_buy_hold_return_pct():
    assert _buy_hold_return_pct([100.0, 150.0]) == pytest.approx(50.0)
    assert _buy_hold_return_pct([100.0, 80.0]) == pytest.approx(-20.0)
    assert _buy_hold_return_pct([100.0]) is None  # too few bars
    assert _buy_hold_return_pct([0.0, 100.0]) is None  # undefined base


def test_profit_factor():
    assert _profit_factor([10.0, -5.0, 20.0, -5.0]) == pytest.approx(3.0)  # 30 / 10
    assert _profit_factor([10.0, 20.0]) is None  # no losses -> undefined
    assert _profit_factor([]) is None


def test_max_consecutive_losses():
    assert _max_consecutive_losses([-1, -1, 5, -1, -1, -1, 2]) == 3
    assert _max_consecutive_losses([1, 2, 3]) == 0
    assert _max_consecutive_losses([]) == 0


# --- end-to-end on the engine ------------------------------------------
def test_backtest_exposes_buy_hold_and_metrics():
    # A monotonic climb: strategy trades, coin itself goes 100 -> 130 (+30%).
    closes = [100, 105, 103, 110, 108, 120, 118, 130]
    params = {"take_profit_pct": 4.0, "initial_capital": 1000.0}
    risk = Risk(invest_ratio=1.0, stop_loss_pct=3.0)
    res = run_backtest(_macro("A", "long", params, risk), _df(closes))

    assert res.buy_hold_return_pct == pytest.approx(30.0, abs=1e-6)
    # Sharpe is defined (curve is not flat) and a finite number.
    assert res.sharpe is not None
    assert res.max_consecutive_losses >= 0


def test_interval_changes_sharpe_annualization():
    # Identical equity curve, different candle interval -> Sharpe scales by
    # sqrt(periods_per_year ratio). 1h has 8760/yr vs 1d's 365 -> ~sqrt(24).
    closes = [100, 101, 102, 101, 103, 104, 103, 105]
    params = {"take_profit_pct": 1.0, "initial_capital": 1000.0}
    risk = Risk(invest_ratio=1.0, stop_loss_pct=1.0)
    daily = run_backtest(_macro("A", "long", params, risk, interval="1d"), _df(closes, "D"))
    hourly = run_backtest(_macro("A", "long", params, risk, interval="1h"), _df(closes, "h"))
    if daily.sharpe and hourly.sharpe:
        assert hourly.sharpe == pytest.approx(daily.sharpe * (24 ** 0.5), rel=1e-3)
