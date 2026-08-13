"""Live candle feed: input clamping, server cache, and stale-fallback.

The critical invariant is the split from the backtest loader — the chart must
always refetch (so bars actually move) and must never let the in-progress bar be
treated as settled history.
"""
from __future__ import annotations

import time

import pytest

from app import chart as chart_mod
from app.data import NoSpotDataError


@pytest.fixture(autouse=True)
def _clear_cache():
    chart_mod._cache.clear()
    chart_mod._live_cache.clear()
    yield
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
    assert seen["limit"] == 10  # floor


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
    key = ("BTCUSDT", "1m", 120, "spot")
    payload, _ = chart_mod._cache[key]
    chart_mod._cache[key] = (payload, time.time() - 1)
    chart_mod.get_candles("BTCUSDT", interval="1m")
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
    key = ("BTCUSDT", "1m", 120, "spot")
    payload, _ = chart_mod._cache[key]
    chart_mod._cache[key] = (payload, time.time() - 1)  # force a refetch

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
