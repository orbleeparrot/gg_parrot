"""Unit tests for the deterministic backtest engine.

Fees/slippage/funding are zeroed in most cases so expected returns are exact
and hand-checkable; a couple of tests assert costs actually reduce returns.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import Macro, run_backtest
from engine.schema import Fees, Period, Risk


def make_df(closes: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=len(closes), freq="D")
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


NO_COST = Fees(commission_pct=0.0, slippage_pct=0.0, funding_pct=0.0)


def macro(rule_type, side, params, risk=None, fees=NO_COST):
    return Macro(
        symbol="BTCUSDT",
        rule_type=rule_type,
        position_side=side,
        params=params,
        risk=risk or Risk(invest_ratio=1.0, stop_loss_pct=None),
        period=Period(preset="custom"),
        fees=fees,
    )


# --- Rule A ------------------------------------------------------------
def test_a_long_take_profit():
    m = macro("A", "long", {"take_profit_pct": 10.0, "initial_capital": 1000.0})
    res = run_backtest(m, make_df([100, 111]))  # clears the +10% threshold at 111
    assert res.total_trades == 1
    assert res.final_return_pct == pytest.approx(11.0, abs=1e-2)


def test_a_short_take_profit():
    m = macro(
        "A",
        "short",
        {"take_profit_pct": 10.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=20.0),
    )
    res = run_backtest(m, make_df([100, 90]))  # price falls -> short profit
    assert res.total_trades == 1
    assert res.final_return_pct == pytest.approx(10.0, abs=1e-6)


def test_a_short_stop_loss():
    m = macro(
        "A",
        "short",
        {"take_profit_pct": 10.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=20.0),
    )
    res = run_backtest(m, make_df([100, 121]))  # price rises -> short stopped out
    assert res.total_trades == 1
    assert res.final_return_pct == pytest.approx(-21.0, abs=1e-6)


# --- Rule B ------------------------------------------------------------
def test_b_long_band():
    m = macro("B", "long", {"buy_price": 100, "sell_price": 110, "initial_capital": 1000.0})
    res = run_backtest(m, make_df([105, 99, 108, 111]))
    assert res.total_trades == 1
    # entered at 99, exited at 111
    assert res.final_return_pct == pytest.approx((111 / 99 - 1) * 100, abs=1e-2)


def test_b_short_band():
    m = macro(
        "B",
        "short",
        {"buy_price": 100, "sell_price": 110, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=50.0),
    )
    res = run_backtest(m, make_df([95, 112, 98]))  # short at 112, cover at 98
    assert res.total_trades == 1
    assert res.final_return_pct == pytest.approx((112 - 98) / 112 * 100, abs=1e-6)


# --- Rule C ------------------------------------------------------------
def test_c_dca_long():
    m = macro("C", "long", {"amount_per_buy": 100.0, "interval_days": 1})
    res = run_backtest(m, make_df([100, 110, 120]))
    assert res.total_trades == 3
    qty = 100 / 100 + 100 / 110 + 100 / 120
    expected = (qty * 120 - 300) / 300 * 100
    assert res.final_return_pct == pytest.approx(expected, abs=1e-2)


def test_c_dca_stop_loss():
    m = macro(
        "C",
        "long",
        {"amount_per_buy": 100.0, "interval_days": 1},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=30.0),
    )
    res = run_backtest(m, make_df([100, 50, 60]))  # deep drop triggers stop
    assert res.final_return_pct < 0


# --- common risk: invest_ratio -----------------------------------------
def test_invest_ratio_scales_exposure():
    m = macro(
        "A",
        "long",
        {"take_profit_pct": 10.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=0.5, stop_loss_pct=None),
    )
    res = run_backtest(m, make_df([100, 110]))
    # only 50% deployed -> half the return
    assert res.final_return_pct == pytest.approx(5.0, abs=1e-6)


# --- fees / slippage reduce returns ------------------------------------
def test_fees_reduce_return():
    params = {"take_profit_pct": 10.0, "initial_capital": 1000.0}
    clean = run_backtest(macro("A", "long", params), make_df([100, 110]))
    costly = run_backtest(
        macro("A", "long", params, fees=Fees(commission_pct=0.1, slippage_pct=0.05)),
        make_df([100, 110]),
    )
    assert costly.final_return_pct < clean.final_return_pct


# --- determinism -------------------------------------------------------
def test_deterministic():
    m = macro("A", "long", {"take_profit_pct": 5.0, "initial_capital": 1000.0})
    df = make_df([100, 103, 98, 106, 101, 110])
    r1 = run_backtest(m, df)
    r2 = run_backtest(m, df)
    assert r1.model_dump() == r2.model_dump()


# --- validators --------------------------------------------------------
def test_short_ab_requires_stop_loss():
    with pytest.raises(ValueError):
        Macro(rule_type="A", position_side="short",
              params={"take_profit_pct": 5.0, "initial_capital": 1000.0})


def test_c_short_rejected():
    with pytest.raises(ValueError):
        Macro(rule_type="C", position_side="short",
              params={"amount_per_buy": 100.0, "interval_days": 7})


def test_missing_params_rejected():
    with pytest.raises(ValueError):
        Macro(rule_type="A", position_side="long", params={"initial_capital": 1000.0})
