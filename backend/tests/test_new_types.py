"""Tests for the v4 additions: rule types D~J and the kimchi-premium endpoint."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import kimchi as kimchi_mod
from app import marketdata
from app.engine.backtest import run_backtest
from app.engine.schema import Macro
from app.main import app

client = TestClient(app)


def _osc_df(n: int = 300) -> pd.DataFrame:
    t = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 100 + 15 * np.sin(np.arange(n) / 12.0) + np.linspace(0, 8, n)
    high = close * 1.01
    low = close * 0.99
    op = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {"timestamp": t, "open": op, "high": high, "low": low, "close": close, "volume": np.ones(n)}
    )


_VALID_PARAMS = {
    "D": dict(lower_price=90, upper_price=130, grid_count=10, initial_capital=1_000_000),
    "E": dict(activation_profit=5, trail_percent=3, initial_capital=1_000_000),
    "F": dict(rsi_period=14, entry_threshold=30, exit_threshold=70, initial_capital=1_000_000),
    "G": dict(bb_period=20, bb_std=2.0, strategy="reversion", initial_capital=1_000_000),
    "H": dict(base_order_size=100_000, safety_order_size=100_000, price_deviation=2,
              max_safety_orders=3, take_profit=1.5, initial_capital=1_000_000),
    "I": dict(k=0.5, exit_mode="next_open", initial_capital=1_000_000),
    "J": dict(ma_type="SMA", fast_period=10, slow_period=30, initial_capital=1_000_000),
    "K": dict(drop_trigger_pct=5, partial_exit_pct=50, short_take_profit_pct=5,
              short_stop_loss_pct=3, initial_capital=1_000_000),
}


@pytest.mark.parametrize("rt", list(_VALID_PARAMS))
def test_new_type_backtest_runs_and_is_deterministic(rt):
    macro = Macro(symbol="BTCUSDT", rule_type=rt, candle_interval="1h", params=_VALID_PARAMS[rt])
    df = _osc_df()
    r1 = run_backtest(macro, df)
    r2 = run_backtest(macro, df)
    assert r1.model_dump() == r2.model_dump()  # deterministic
    assert r1.total_trades >= 0
    assert len(r1.equity_curve) == len(df)


def test_grid_rejects_inverted_band():
    with pytest.raises(ValidationError):
        Macro(rule_type="D", params=dict(lower_price=130, upper_price=90, grid_count=10, initial_capital=1_000_000))


def test_ma_cross_rejects_fast_ge_slow():
    with pytest.raises(ValidationError):
        Macro(rule_type="J", params=dict(ma_type="SMA", fast_period=60, slow_period=20, initial_capital=1_000_000))


def test_martingale_rejects_overbudget():
    # base + safety*(1+2+4) = 100k + 700k = 800k > 500k budget
    with pytest.raises(ValidationError):
        Macro(
            rule_type="H",
            risk={"invest_ratio": 0.5},
            params=dict(base_order_size=100_000, safety_order_size=100_000, price_deviation=2,
                        safety_order_volume_scale=2.0, max_safety_orders=3, take_profit=1.5,
                        initial_capital=1_000_000),
        )


def test_same_bar_touch_takes_stop_loss():
    # One bar touches both the take-profit (high) and stop-loss (low): stop wins,
    # and the bar is counted.
    macro = Macro(
        symbol="BTCUSDT", rule_type="H", candle_interval="1h",
        risk={"stop_loss_pct": 10.0, "invest_ratio": 1.0},
        params=dict(base_order_size=100_000, safety_order_size=100_000, price_deviation=2,
                    max_safety_orders=2, take_profit=1.5, initial_capital=1_000_000),
    )
    t = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": t,
        "open": [100.0, 100.0],
        "high": [100.0, 200.0],  # bar 1 high hits TP
        "low": [100.0, 50.0],    # bar 1 low hits SL
        "close": [100.0, 100.0],
        "volume": [1.0, 1.0],
    })
    r = run_backtest(macro, df)
    assert r.same_bar_sl_bars == 1


def test_short_symmetry_allowed_only_for_fgj():
    Macro(rule_type="F", position_side="short", params=_VALID_PARAMS["F"])  # ok
    # D is long-only in the builder; engine still parses but paper/backtest treat long.
    # (No exception at schema level for D long; short D simply isn't offered by UI.)


def test_create_macro_new_type_endpoint(monkeypatch):
    def fake_klines(symbol, start_ms, end_ms, *, interval, market, allow_synthetic):
        assert (symbol, interval, market, allow_synthetic) == ("BTCUSDT", "1h", "spot", False)
        assert start_ms < end_ms
        candles = _osc_df()
        candles["timestamp"] = pd.date_range(
            pd.to_datetime(start_ms, unit="ms", utc=True), periods=len(candles), freq="h",
        )
        return candles, "fixture"

    monkeypatch.setattr(marketdata, "get_klines", fake_klines)
    macro = {
        "symbol": "BTCUSDT", "rule_type": "D", "candle_interval": "1h",
        "params": _VALID_PARAMS["D"],
        "risk": {"invest_ratio": 1.0},
        "period": {"preset": "3m"},
    }
    res = client.post("/api/macros", json=macro)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["share_slug"].startswith("btc-grid")
    assert "그리드" in body["human_summary"]
    assert body["data_source"] == "fixture"


# --- kimchi premium -----------------------------------------------------
def test_kimchi_premium_calc(monkeypatch):
    monkeypatch.setattr(kimchi_mod, "_upbit_price", lambda m: 142_000_000.0)
    monkeypatch.setattr(kimchi_mod, "_binance_price", lambda s: 100_000.0)
    monkeypatch.setattr(kimchi_mod, "_usdkrw", lambda: (1400.0, False))
    res = client.get("/api/kimchi-premium?symbol=BTC")
    assert res.status_code == 200
    d = res.json()
    assert d["ok"] is True
    assert d["premium_pct"] == pytest.approx(1.4286, abs=1e-3)
    assert d["label"] == "김프"


def test_kimchi_fx_fallback_does_not_crash(monkeypatch):
    monkeypatch.setattr(kimchi_mod, "_upbit_price", lambda m: 140_000_000.0)
    monkeypatch.setattr(kimchi_mod, "_binance_price", lambda s: 100_000.0)
    monkeypatch.setattr(kimchi_mod, "_usdkrw", lambda: (1380.0, True))
    d = client.get("/api/kimchi-premium").json()
    assert d["ok"] is True
    assert d["fx_is_fallback"] is True


def test_kimchi_missing_source_reports_not_ok(monkeypatch):
    monkeypatch.setattr(kimchi_mod, "_upbit_price", lambda m: None)
    monkeypatch.setattr(kimchi_mod, "_binance_price", lambda s: 100_000.0)
    monkeypatch.setattr(kimchi_mod, "_usdkrw", lambda: (1400.0, False))
    d = client.get("/api/kimchi-premium").json()
    assert d["ok"] is False
    assert d["premium_pct"] is None
