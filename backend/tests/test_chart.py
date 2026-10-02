"""Live candle feed: input clamping, server cache, and stale-fallback.

The critical invariant is the split from the backtest loader — the chart must
always refetch (so bars actually move) and must never let the in-progress bar be
treated as settled history.
"""
from __future__ import annotations

import time

import pytest

from app import chart as chart_mod
from app.cache_runtime import close_cache_runtime
from app.data import NoSpotDataError


@pytest.fixture(autouse=True)
def _clear_cache():
    chart_mod._cache.clear()
    chart_mod._live_cache.clear()
    yield
    close_cache_runtime()  # Keep background refreshes inside their test mocks.
    chart_mod._cache.clear()
    chart_mod._live_cache.clear()


def _fake_candles(n=3, closed_last=False):
    base = 1_700_000_000_000
    out = []
    for i in range(n):
        out.append(
            {
                "t": base + i * 60_000,
                "o": 100.0 + i,
                "h": 101.0 + i,
                "l": 99.0 + i,
                "c": 100.5 + i,
                "v": 1.0,
                "closed": True if i < n - 1 else closed_last,
            }
        )
    return out


def _patch(monkeypatch, fn):
    monkeypatch.setattr(chart_mod, "get_recent_klines", fn)


# --- input handling -----------------------------------------------------
def test_unknown_interval_falls_back_to_default(monkeypatch):
    seen = {}

    def fake(symbol, interval, limit, market):
        seen["interval"] = interval
        return _fake_candles()

    _patch(monkeypatch, lambda symbol, interval, limit, market: fake(symbol, interval, limit, market))
    d = chart_mod.get_candles("BTCUSDT", interval="7y")
    assert d["interval"] == chart_mod.DEFAULT_INTERVAL
    assert seen["interval"] == chart_mod.DEFAULT_INTERVAL


def test_limit_is_clamped(monkeypatch):
    seen = {}

    def fake(symbol, interval, limit, market):
        seen["limit"] = limit
        return _fake_candles()

    _patch(monkeypatch, fake)
    chart_mod.get_candles("BTCUSDT", limit=99999)
    assert seen["limit"] == chart_mod.MAX_LIMIT
    chart_mod._cache.clear()
    chart_mod.get_candles("BTCUSDT", limit=1)
    assert seen["limit"] == chart_mod.MAX_LIMIT  # one buffer shared by display limits


def test_blank_symbol_rejected():
    with pytest.raises(NoSpotDataError):
        chart_mod.get_candles("   ")


def test_symbol_is_uppercased(monkeypatch):
    _patch(monkeypatch, lambda symbol, interval, limit, market: _fake_candles())
    assert chart_mod.get_candles("btcusdt")["symbol"] == "BTCUSDT"


# --- caching ------------------------------------------------------------
def test_second_call_is_served_from_cache(monkeypatch):
    calls = {"n": 0}

    def fake(symbol, interval, limit, market):
        calls["n"] += 1
        return _fake_candles()

    _patch(monkeypatch, fake)
    first = chart_mod.get_candles("BTCUSDT")
    second = chart_mod.get_candles("BTCUSDT")
    assert calls["n"] == 1  # upstream hit once for two viewers
    assert first["cached"] is False and second["cached"] is True


def test_cache_expires(monkeypatch):
    calls = {"n": 0}

    def fake(symbol, interval, limit, market):
        calls["n"] += 1
        return _fake_candles()

    _patch(monkeypatch, fake)
    chart_mod.get_candles("BTCUSDT", interval="1m")
    # Expire the entry rather than sleeping the suite.
    key = ("BTCUSDT", "1m", "spot")
    chart_mod._cache._entries[key].expires = chart_mod._cache.clock() - 1
    assert chart_mod.get_candles("BTCUSDT", interval="1m")["stale"] is True
    from app.cache_runtime import close_cache_runtime
    close_cache_runtime()
    assert calls["n"] == 2


def test_different_intervals_cache_separately(monkeypatch):
    calls = {"n": 0}

    def fake(symbol, interval, limit, market):
        calls["n"] += 1
        return _fake_candles()

    _patch(monkeypatch, fake)
    chart_mod.get_candles("BTCUSDT", interval="1m")
    chart_mod.get_candles("BTCUSDT", interval="5m")
    assert calls["n"] == 2


# --- degradation --------------------------------------------------------
def test_transient_failure_serves_stale_cache(monkeypatch):
    _patch(monkeypatch, lambda symbol, interval, limit, market: _fake_candles())
    chart_mod.get_candles("BTCUSDT", interval="1m")
    key = ("BTCUSDT", "1m", "spot")
    chart_mod._cache._entries[key].expires = chart_mod._cache.clock() - 1  # force a refetch

    def boom(symbol, interval, limit, market):
        raise RuntimeError("network down")

    _patch(monkeypatch, boom)
    d = chart_mod.get_candles("BTCUSDT", interval="1m")
    assert d["stale"] is True and d["candles"]  # chart keeps rendering


def test_failure_without_cache_raises(monkeypatch):
    def boom(symbol, interval, limit, market):
        raise RuntimeError("network down")

    _patch(monkeypatch, boom)
    with pytest.raises(NoSpotDataError):
        chart_mod.get_candles("BTCUSDT")


def test_missing_market_propagates(monkeypatch):
    def nope(symbol, interval, limit, market):
        raise NoSpotDataError("no such market")

    _patch(monkeypatch, nope)
    with pytest.raises(NoSpotDataError):
        chart_mod.get_candles("NOTREAL")


# --- payload contract ---------------------------------------------------
def test_payload_advertises_refresh_and_marks_open_bar(monkeypatch):
    _patch(monkeypatch, lambda symbol, interval, limit, market: _fake_candles(closed_last=False))
    d = chart_mod.get_candles("BTCUSDT", interval="1m")
    assert d["refresh_seconds"] > 0
    assert d["candles"][-1]["closed"] is False  # the bar still forming
    assert all(k["closed"] for k in d["candles"][:-1])


def test_live_edge_uses_two_candles_and_short_cache(monkeypatch):
    seen = {"calls": 0, "limit": None}

    def fake(symbol, interval, limit, market):
        seen["calls"] += 1
        seen["limit"] = limit
        return _fake_candles(n=2, closed_last=False)

    _patch(monkeypatch, fake)
    first = chart_mod.get_live_candles("btcusdt", interval="1d")
    second = chart_mod.get_live_candles("BTCUSDT", interval="1d")

    assert seen == {"calls": 1, "limit": 2}
    assert first["cached"] is False and second["cached"] is True
    assert first["symbol"] == "BTCUSDT"
    assert first["refresh_seconds"] == chart_mod._LIVE_REFRESH_SECONDS
    assert first["candles"][-1]["closed"] is False


@pytest.mark.parametrize("method,cache", [
    (chart_mod.get_candles, chart_mod._cache),
    (chart_mod.get_live_candles, chart_mod._live_cache),
])
def test_cached_chart_separates_response_time_from_upstream_fetch(monkeypatch, method, cache):
    clock = [1_800_000_010.0]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(cache, "clock", lambda: clock[0])
    candles = _fake_candles(n=2)
    candles[-1]["t"] = 1_800_000_000_000
    calls = []
    _patch(monkeypatch, lambda symbol, interval, limit, market: calls.append(market) or candles)

    first = method("BTCUSDT", interval="1m")
    clock[0] += 2
    cached = method("BTCUSDT", interval="1m")

    assert calls == ["spot"]
    assert first["fetched_at_ms"] == 1_800_000_010_000
    assert cached["fetched_at_ms"] == first["fetched_at_ms"]
    assert cached["server_time"] == 1_800_000_012_000
    assert cached["cache_age_ms"] == 2_000
    assert cached["latest_candle_open_time_ms"] == candles[-1]["t"]
    assert cached["source"] == "binance:spot" and cached["fallback"] is False
    assert cached["stale"] is False and cached["awaiting_candle_refresh"] is False


@pytest.mark.parametrize("method", [chart_mod.get_candles, chart_mod.get_live_candles])
def test_futures_fallback_explicitly_identifies_actual_spot_source(monkeypatch, method):
    calls = []
    def fetch(symbol, interval, limit, market):
        calls.append(market)
        if market == "futures":
            raise RuntimeError("futures unavailable")
        return _fake_candles()
    _patch(monkeypatch, fetch)

    payload = method("BTCUSDT", market="futures")

    assert calls == ["futures", "spot"]
    assert payload["market"] == "spot" and payload["requested_market"] == "futures"
    assert payload["source"] == "binance:spot" and payload["fallback"] is True
    assert payload["exchange"] == "binance" and payload["quote_currency"] == "USDT"


def test_history_and_live_sources_are_explicit_when_futures_recovers(monkeypatch):
    def fetch(symbol, interval, limit, market):
        if market == "futures" and limit == chart_mod.MAX_LIMIT:
            raise RuntimeError("history futures unavailable")
        candles = _fake_candles(n=2)
        candles[-1]["c"] = 200 if market == "futures" else 100
        return candles
    _patch(monkeypatch, fetch)

    history = chart_mod.get_candles("BTCUSDT", market="futures")
    independently_requested = chart_mod.get_live_candles("BTCUSDT", market="futures")
    pinned_to_history = chart_mod.get_live_candles("BTCUSDT", market=history["market"])

    assert history["source"] == "binance:spot" and history["fallback"] is True
    assert independently_requested["source"] == "binance:futures"
    assert independently_requested["fallback"] is False
    assert independently_requested["candles"][-1]["c"] != history["candles"][-1]["c"]
    assert pinned_to_history["source"] == history["source"]
    assert pinned_to_history["candles"][-1]["c"] == history["candles"][-1]["c"]


@pytest.mark.parametrize("method,cache,advance", [
    (chart_mod.get_candles, chart_mod._cache, 61),
    (chart_mod.get_live_candles, chart_mod._live_cache, 4),
])
def test_stale_open_bar_is_not_closed_or_presented_as_current(monkeypatch, method, cache, advance):
    from app.cache_runtime import close_cache_runtime
    clock = [1_800_000_059.0]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(cache, "clock", lambda: clock[0])
    candles = _fake_candles(n=2)
    candles[-1]["t"] = 1_800_000_000_000
    _patch(monkeypatch, lambda symbol, interval, limit, market: candles)
    first = method("BTCUSDT", interval="1m")
    clock[0] += advance
    def unavailable(symbol, interval, limit, market):
        raise RuntimeError("upstream unavailable")
    _patch(monkeypatch, unavailable)

    stale = method("BTCUSDT", interval="1m")
    close_cache_runtime()

    assert stale["stale"] is True and stale["awaiting_candle_refresh"] is True
    assert stale["fetched_at_ms"] == first["fetched_at_ms"]
    assert stale["server_time"] == int(clock[0] * 1000)
    assert stale["cache_age_ms"] == advance * 1000
    assert stale["candles"][-1]["closed"] is False
    assert stale["candles"] == first["candles"]


def test_fresh_cache_waits_for_upstream_confirmation_after_bar_close(monkeypatch):
    clock = [1_800_000_059.0]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(chart_mod._cache, "clock", lambda: clock[0])
    candles = _fake_candles(n=2)
    candles[-1]["t"] = 1_800_000_000_000
    _patch(monkeypatch, lambda symbol, interval, limit, market: candles)
    chart_mod.get_candles("BTCUSDT")
    clock[0] += 2

    payload = chart_mod.get_candles("BTCUSDT")

    assert payload["stale"] is False
    assert payload["awaiting_candle_refresh"] is True
    assert payload["candles"][-1]["closed"] is False


def test_native_bithumb_candle_open_timestamp_is_not_relabelled_as_fetch_time(monkeypatch):
    native_open = 1_791_039_600_000
    clock = [native_open / 1000 + 10]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    candles = [{**_fake_candles(n=1)[0], "t": native_open}]
    _patch(monkeypatch, lambda symbol, **kwargs: candles)

    payload = chart_mod.get_candles("KRW-BTC", exchange="bithumb", interval="1d")

    assert payload["latest_candle_open_time_ms"] == native_open
    assert payload["fetched_at_ms"] == native_open + 10_000
    assert payload["source"] == "bithumb:spot" and payload["quote_currency"] == "KRW"


def test_live_three_second_poll_returns_refreshed_prices_not_normal_swr_stale(monkeypatch):
    clock = [1_800_000_010.0]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(chart_mod._live_cache, "clock", lambda: clock[0])
    calls = []
    def fetch(symbol, **kwargs):
        calls.append(kwargs)
        return [{**_fake_candles(n=1)[0], "t": 1_800_000_000_000, "c": len(calls) * 100}]
    _patch(monkeypatch, fetch)

    for index in range(4):
        payload = chart_mod.get_live_candles("KRW-BTC", exchange="upbit")
        assert payload["candles"][-1]["c"] == (index + 1) * 100
        assert payload["fetched_at_ms"] == int(clock[0] * 1000)
        assert payload["cache_age_ms"] == 0
        assert payload["stale"] is False and payload["awaiting_candle_refresh"] is False
        clock[0] += 3
    assert len(calls) == 4


def test_live_real_upstream_failure_preserves_bounded_stale_and_retry_cooldown(monkeypatch):
    clock = [1_800_000_010.0]
    original = clock[0]
    monkeypatch.setattr(chart_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(chart_mod._live_cache, "clock", lambda: clock[0])
    _patch(monkeypatch, lambda symbol, **kwargs: [{**_fake_candles(n=1)[0], "t": 1_800_000_000_000}])
    first = chart_mod.get_live_candles("KRW-BTC", exchange="upbit")
    calls = []
    def unavailable(symbol, **kwargs):
        calls.append(1)
        raise NoSpotDataError("exchange unavailable")
    _patch(monkeypatch, unavailable)

    clock[0] += 3
    failed = chart_mod.get_live_candles("KRW-BTC", exchange="upbit")
    assert failed["stale"] and failed["awaiting_candle_refresh"]
    assert failed["fetched_at_ms"] == first["fetched_at_ms"]
    clock[0] += 1
    assert chart_mod.get_live_candles("KRW-BTC", exchange="upbit")["stale"] is True
    assert len(calls) == 1
    clock[0] = original + 17
    assert chart_mod.get_live_candles("KRW-BTC", exchange="upbit")["stale"] is True
    assert len(calls) == 2
    clock[0] += 1  # ttl=3 plus stale_ttl=15 is the original hard cache bound.
    with pytest.raises(NoSpotDataError, match="exchange unavailable"):
        chart_mod.get_live_candles("KRW-BTC", exchange="upbit")
    assert len(calls) == 2
