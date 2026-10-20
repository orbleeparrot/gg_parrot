"""Unit tests for v9 leverage + isolated-margin liquidation.

Fees/slippage are zeroed so leveraged returns are exact and hand-checkable. The
maintenance-margin correction only nudges the *trigger* price; on liquidation the
whole committed margin is wiped (전액 손실), so the loss equals the margin exactly.
"""
from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from engine import Macro, human_summary, run_backtest
from engine.leverage import liquidation_price
from engine.schema import Fees, Period, PositionSide, Risk

NO_COST = Fees(commission_pct=0.0, slippage_pct=0.0, funding_pct=0.0)


def make_df(closes: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"timestamp": ts, "open": closes, "high": closes,
         "low": closes, "close": closes, "volume": [0.0] * len(closes)}
    )


def ohlc_df(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=len(bars), freq="h", tz="UTC")
    o, h, l, c = zip(*bars)
    return pd.DataFrame(
        {"timestamp": ts, "open": o, "high": h, "low": l, "close": c,
         "volume": [1.0] * len(bars)}
    )


def macro(rt, side, params, *, leverage=1, risk=None, fees=NO_COST, interval="1d"):
    return Macro(
        symbol="BTCUSDT", rule_type=rt, position_side=side, params=params,
        leverage=leverage, candle_interval=interval,
        risk=risk or Risk(invest_ratio=1.0, stop_loss_pct=None),
        period=Period(preset="custom"), fees=fees,
    )


# --- pure liquidation-price properties ----------------------------------
def test_liq_price_none_at_1x():
    assert liquidation_price(100.0, 1, PositionSide.LONG) is None


def test_liq_price_sides():
    lo = liquidation_price(100.0, 10, PositionSide.LONG)
    sh = liquidation_price(100.0, 10, PositionSide.SHORT)
    assert lo < 100.0 < sh


def test_higher_leverage_moves_liq_closer_to_entry():
    d5 = 100.0 - liquidation_price(100.0, 5, PositionSide.LONG)
    d10 = 100.0 - liquidation_price(100.0, 10, PositionSide.LONG)
    d20 = 100.0 - liquidation_price(100.0, 20, PositionSide.LONG)
    assert d5 > d10 > d20 > 0  # the core property: more leverage, nearer entry


# --- leverage 1 is a no-op (regression) ---------------------------------
def test_leverage_1_regression_matches_spot():
    m = macro("A", "long", {"take_profit_pct": 10.0, "initial_capital": 1000.0}, leverage=1)
    res = run_backtest(m, make_df([100, 111]))
    assert res.final_return_pct == pytest.approx(11.0, abs=1e-2)
    assert res.liquidation_count == 0
    assert res.liquidated_loss == 0.0


# --- profit / loss amplification ----------------------------------------
def test_leverage_amplifies_profit():
    m = macro("A", "long", {"take_profit_pct": 2.0, "initial_capital": 1000.0}, leverage=5)
    res = run_backtest(m, make_df([100, 102]))  # +2% move × 5x = +10%
    assert res.final_return_pct == pytest.approx(10.0, abs=1e-6)
    assert res.liquidation_count == 0


# --- liquidation wipes the margin (전액 손실) ----------------------------
def test_long_liquidation_wipes_full_margin():
    # 10x long, liq ≈ 100×(1−0.1+0.005)=90.5; a close at 85 breaches it.
    m = macro("A", "long", {"take_profit_pct": 100.0, "initial_capital": 1000.0}, leverage=10)
    res = run_backtest(m, make_df([100, 85]))
    assert res.liquidation_count == 1
    assert res.final_return_pct == pytest.approx(-100.0, abs=1e-6)
    assert res.liquidated_loss == pytest.approx(1000.0, abs=1e-6)


def test_short_liquidation_wipes_full_margin():
    # 10x short, liq ≈ 100×(1+0.1−0.005)=109.5; a close at 112 breaches it first
    # (before the 50% stop-loss), so liquidation wins.
    m = macro(
        "A", "short", {"take_profit_pct": 100.0, "initial_capital": 1000.0},
        leverage=10, risk=Risk(invest_ratio=1.0, stop_loss_pct=50.0),
    )
    res = run_backtest(m, make_df([100, 112]))
    assert res.liquidation_count == 1
    assert res.final_return_pct == pytest.approx(-100.0, abs=1e-6)


def test_isolated_margin_caps_loss_at_committed_margin():
    # invest_ratio 0.5 -> only half the equity is margin; liquidation loses that
    # half only (isolated), not the whole account.
    m = macro(
        "A", "long", {"take_profit_pct": 100.0, "initial_capital": 1000.0},
        leverage=10, risk=Risk(invest_ratio=0.5, stop_loss_pct=None),
    )
    res = run_backtest(m, make_df([100, 80]))
    assert res.liquidation_count == 1
    assert res.final_return_pct == pytest.approx(-50.0, abs=1e-6)
    assert res.liquidated_loss == pytest.approx(500.0, abs=1e-6)


# --- candle engine (D~J) leverage + liquidation -------------------------
def test_candle_type_liquidation():
    # E trailing, immediate entry at bar 0 (100). Bar 1 low 89 breaches the 10x
    # long liq (~90.5) -> full-margin liquidation.
    m = macro(
        "E", "long",
        {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3,
         "initial_capital": 1000.0},
        leverage=10, interval="1h",
    )
    df = ohlc_df([(100, 100, 100, 100), (95, 96, 89, 92)])
    res = run_backtest(m, df)
    assert res.liquidation_count == 1
    assert res.final_return_pct == pytest.approx(-100.0, abs=1e-6)


def test_candle_leverage_1_regression():
    params = {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3,
              "initial_capital": 1000.0}
    df = ohlc_df([(100, 101, 99, 100), (101, 108, 100, 107), (107, 109, 104, 105)])
    # Same candle_interval on both so the equality is purely about leverage
    # (interval only affects the Sharpe annualization, not the equity curve).
    spot = run_backtest(macro("E", "long", params, leverage=1, interval="1h"), df)
    # A macro with leverage omitted (defaults to 1) must match leverage=1 exactly.
    plain = run_backtest(
        Macro(symbol="BTCUSDT", rule_type="E", candle_interval="1h", params=params,
              risk=Risk(invest_ratio=1.0, stop_loss_pct=None),
              period=Period(preset="custom"), fees=NO_COST),
        df,
    )
    assert spot.model_dump() == plain.model_dump()
    assert spot.liquidation_count == 0


# --- schema validation --------------------------------------------------
def test_dca_rejects_leverage():
    with pytest.raises(ValidationError):
        Macro(rule_type="C", leverage=3,
              params={"amount_per_buy": 100.0, "interval_days": 7})


def test_leverage_over_cap_rejected():
    with pytest.raises(ValidationError):
        Macro(rule_type="A", leverage=999,
              params={"take_profit_pct": 5.0, "initial_capital": 1000.0})


# --- human summary ------------------------------------------------------
def test_human_summary_shows_leverage():
    m = macro("A", "long", {"take_profit_pct": 5.0, "initial_capital": 1000.0}, leverage=10)
    assert "10배 레버리지(격리)" in human_summary(m)


def test_human_summary_hides_1x():
    m = macro("A", "long", {"take_profit_pct": 5.0, "initial_capital": 1000.0}, leverage=1)
    assert "레버리지" not in human_summary(m)
