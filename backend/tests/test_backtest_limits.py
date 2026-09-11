from fastapi.testclient import TestClient
import pytest
from app.data import binance
from app.main import app


def test_metadata_exposes_effective_cap_without_market_io(monkeypatch):
    monkeypatch.setattr(binance, "MAX_BACKTEST_BARS", 1234)
    def no_io(*args, **kwargs):
        pytest.fail("limits metadata must not access market data")
    monkeypatch.setattr(binance, "_read_cache", no_io)
    monkeypatch.setattr(binance, "_fetch_binance", no_io)
    response = TestClient(app).get("/api/backtest/limits")
    assert response.status_code == 200
    data = response.json()
    assert data["max_bars"] == 1234
    assert data["interval_ms"]["1m"] == 60000
    assert data["preset_days"]["1y"] == 365
    assert data["preset_days"]["6m"] == 182


@pytest.mark.parametrize("preset,days", [("1d", 1), ("1w", 7), ("1m", 30)])
def test_short_presets_resolve_exactly(preset, days):
    start, end = binance.resolve_period(preset, None, None)
    assert end - start == days * 86400000
    assert binance.estimate_bar_count("1m", start, end) == days * 1440


def test_one_year_minute_limit_is_still_enforced(monkeypatch):
    monkeypatch.setattr(binance, "MAX_BACKTEST_BARS", 20000)
    start, end = binance.resolve_period("1y", None, None)
    assert binance.estimate_bar_count("1m", start, end) == 525600
    with pytest.raises(binance.TooManyBarsError, match="525,600"):
        binance.get_klines("BTCUSDT", start, end, "1m", allow_synthetic=False)
