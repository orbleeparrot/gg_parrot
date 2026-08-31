"""Market selection (spot vs USDT-M futures) + funding-rate helpers."""
from __future__ import annotations

import pandas as pd
import pytest

from app.data import binance
from app.engine.schema import Macro, Risk


def _macro(side="long", leverage=1, market="auto"):
    return Macro(
        symbol="BTCUSDT",
        rule_type="A",
        position_side=side,
        leverage=leverage,
        market=market,
        params={"take_profit_pct": 5.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=3.0),
    )


# --- resolved_market (mirrors the real bot's auto rule) -----------------
def test_auto_long_1x_is_spot():
    assert _macro("long", 1).resolved_market() == "spot"


def test_auto_short_is_futures():
    assert _macro("short", 1).resolved_market() == "futures"


def test_auto_leverage_is_futures():
    assert _macro("long", 3).resolved_market() == "futures"


def test_explicit_market_is_honored():
    assert _macro("short", 5, market="spot").resolved_market() == "spot"
    assert _macro("long", 1, market="futures").resolved_market() == "futures"


# --- average daily funding (magnitude, 3 settlements/day) ---------------
def test_average_daily_funding_pct(monkeypatch):
    # 3 rates whose mean abs is 0.0002 -> ×3/day ×100 = 0.06% per day.
    monkeypatch.setattr(
        binance, "get_funding_history",
        lambda *a, **k: [(1, 0.0001), (2, -0.0002), (3, 0.0003)],
    )
    assert binance.average_daily_funding_pct("BTCUSDT", 0, 1) == pytest.approx(0.06)


def test_average_daily_funding_none_when_empty(monkeypatch):
    monkeypatch.setattr(binance, "get_funding_history", lambda *a, **k: [])
    assert binance.average_daily_funding_pct("BTCUSDT", 0, 1) is None


# --- historical pagination / cache coverage ----------------------------
def _raw_kline(open_time: int) -> list:
    return [open_time, "1", "1", "1", "1", "1", open_time + 59_999]


def test_historical_pagination_continues_after_last_open_without_interval_gap(monkeypatch):
    first_page = [_raw_kline(i * 60_000) for i in range(1000)]
    second_page = [_raw_kline(1000 * 60_000)]
    requested_starts: list[int] = []

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            self._pages = iter((first_page, second_page))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, _url, *, params, **_kwargs):
            requested_starts.append(params["startTime"])
            return _Response(next(self._pages))

    monkeypatch.setattr(binance.httpx, "Client", _Client)

    rows = binance._fetch_binance("BTCUSDT", "1m", 0, 1001 * 60_000)

    assert len(rows) == 1001
    assert requested_starts == [0, first_page[-1][0] + 1]


def test_hourly_cache_with_only_daily_count_is_refetched(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    start_ms = 0
    end_ms = 30 * binance._MS_DAY
    cached = pd.DataFrame(
        {
            "timestamp": pd.date_range("1970-01-01", periods=30, freq="h", tz="UTC"),
            "open": [1.0] * 30,
            "high": [1.0] * 30,
            "low": [1.0] * 30,
            "close": [1.0] * 30,
            "volume": [1.0] * 30,
        }
    )
    fetches: list[tuple] = []
    monkeypatch.setattr(binance, "_read_cache", lambda *args: cached)
    monkeypatch.setattr(
        binance,
        "_fetch_binance",
        lambda *args, **kwargs: fetches.append((args, kwargs)) or [],
    )

    result, source = binance.get_klines(
        "BTCUSDT", start_ms, end_ms, interval="1h", allow_synthetic=False,
    )

    assert source == "binance"
    assert len(result) == 30
    assert len(fetches) == 1


def test_cache_count_without_end_coverage_is_refetched(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    end_ms = binance._MS_DAY
    cached = pd.DataFrame(
        {
            "timestamp": pd.date_range("1970-01-01", periods=24, freq="30min", tz="UTC"),
            "open": [1.0] * 24,
            "high": [1.0] * 24,
            "low": [1.0] * 24,
            "close": [1.0] * 24,
            "volume": [1.0] * 24,
        }
    )
    fetches: list[bool] = []
    monkeypatch.setattr(binance, "_read_cache", lambda *args: cached)
    monkeypatch.setattr(
        binance,
        "_fetch_binance",
        lambda *args, **kwargs: fetches.append(True) or [],
    )

    binance.get_klines("BTCUSDT", 0, end_ms, interval="1h", allow_synthetic=False)

    assert fetches == [True]


def test_complete_contiguous_hourly_cache_skips_network(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    cached = pd.DataFrame(
        {
            "timestamp": pd.date_range("1970-01-01", periods=24, freq="h", tz="UTC"),
            "open": [1.0] * 24,
            "high": [1.0] * 24,
            "low": [1.0] * 24,
            "close": [1.0] * 24,
            "volume": [1.0] * 24,
        }
    )
    monkeypatch.setattr(binance, "_read_cache", lambda *args: cached)
    monkeypatch.setattr(
        binance,
        "_fetch_binance",
        lambda *args, **kwargs: pytest.fail("complete cache must not hit Binance"),
    )

    result, source = binance.get_klines(
        "BTCUSDT", 0, binance._MS_DAY, interval="1h", allow_synthetic=False,
    )

    assert source == "cache"
    assert len(result) == 24


def test_excessive_bar_window_is_rejected_before_cache_or_network(monkeypatch):
    monkeypatch.setattr(binance, "MAX_BACKTEST_BARS", 100, raising=False)
    touched: list[str] = []
    monkeypatch.setattr(
        binance,
        "_read_cache",
        lambda *args: touched.append("cache") or pd.DataFrame(),
    )
    monkeypatch.setattr(
        binance,
        "_fetch_binance",
        lambda *args, **kwargs: touched.append("network") or [],
    )

    with pytest.raises(ValueError, match="100개"):
        binance.get_klines(
            "BTCUSDT", 0, 101 * 60_000, interval="1m", allow_synthetic=False,
        )

    assert touched == []


def test_default_bar_budget_keeps_one_year_hourly_but_rejects_three_month_five_minute():
    one_year_hourly = binance._expected_bar_count("1h", 0, 365 * binance._MS_DAY)
    three_month_five_minute = binance._expected_bar_count("5m", 0, 91 * binance._MS_DAY)

    assert one_year_hourly <= binance.MAX_BACKTEST_BARS
    assert three_month_five_minute > binance.MAX_BACKTEST_BARS


def test_verified_recent_listing_window_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    start_ms = 0
    end_ms = 30 * binance._MS_DAY
    raw = [_raw_kline(day * binance._MS_DAY) for day in range(20, 30)]
    fetches: list[bool] = []

    def _fetch(*args, **kwargs):
        fetches.append(True)
        return raw

    monkeypatch.setattr(binance, "_fetch_binance", _fetch)

    first, first_source = binance.get_klines(
        "NEWUSDT", start_ms, end_ms, interval="1d", allow_synthetic=False,
    )
    second, second_source = binance.get_klines(
        "NEWUSDT", start_ms, end_ms, interval="1d", allow_synthetic=False,
    )

    assert len(first) == len(second) == 10
    assert first_source == "binance"
    assert second_source == "cache"
    assert fetches == [True]


def test_failed_refresh_does_not_verify_partial_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    start_ms = 0
    end_ms = 30 * binance._MS_DAY
    binance._write_cache("BTCUSDT", "1d", [_raw_kline(29 * binance._MS_DAY)])
    monkeypatch.setattr(
        binance,
        "_fetch_binance",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upstream down")),
    )

    expected_error = getattr(binance, "IncompleteMarketDataError", RuntimeError)
    with pytest.raises(expected_error):
        binance.get_klines(
            "BTCUSDT", start_ms, end_ms, interval="1d", allow_synthetic=False,
        )

    assert hasattr(binance, "_coverage_verified")
    assert not binance._coverage_verified("BTCUSDT", "1d", start_ms, end_ms)


def test_coverage_is_specific_to_market_symbol_and_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(binance, "_DB_PATH", str(tmp_path / "market.db"))
    assert hasattr(binance, "_mark_coverage")
    binance._mark_coverage("BTCUSDT", "1h", 0, binance._MS_DAY)

    assert binance._coverage_verified("BTCUSDT", "1h", 0, binance._MS_DAY)
    assert not binance._coverage_verified("BTCUSDT#FUT", "1h", 0, binance._MS_DAY)
    assert not binance._coverage_verified("BTCUSDT", "1d", 0, binance._MS_DAY)
