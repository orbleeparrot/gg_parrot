"""Regression coverage for settled market data and bounded public cache work."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import chart, feargreed, hotcoins, kimchi
from app.cache_runtime import close_cache_runtime
from app.data import binance, symbols


def bar(open_time, close=10):
    return [open_time, "10", "15", "8", str(close), "1", open_time + 59999]


def test_open_candle_is_neither_persisted_nor_used_until_it_closes(monkeypatch):
    clock = [90]
    monkeypatch.setattr(binance.time, "time", lambda: clock[0])
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args[2:4])
        return [bar(0), bar(60000, 11 if clock[0] < 120 else 14)]
    monkeypatch.setattr(binance, "_fetch_binance", fetch)
    first, _ = binance.get_klines("BTCUSDT", 0, 90000, interval="1m", allow_synthetic=False)
    clock[0] = 91
    second, source = binance.get_klines("BTCUSDT", 0, 91000, interval="1m", allow_synthetic=False)
    assert first["close"].tolist() == second["close"].tolist() == [10]
    assert source == "cache" and calls == [(0, 60000)]
    with binance._conn() as db:
        assert db.execute("SELECT count(*) FROM klines WHERE open_time=60000").fetchone()[0] == 0
    clock[0] = 121
    settled, _ = binance.get_klines("BTCUSDT", 0, 121000, interval="1m", allow_synthetic=False)
    assert settled["close"].tolist() == [10, 14]
    assert calls == [(0, 60000), (60000, 120000)]


def test_legacy_unproven_prices_and_coverage_are_revalidated(monkeypatch):
    with sqlite3.connect(binance._DB_PATH) as db:
        db.execute("""CREATE TABLE klines(symbol TEXT, interval TEXT, open_time INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY(symbol, interval, open_time))""")
        db.execute("INSERT INTO klines VALUES ('BTCUSDT','1m',0,1,1,1,11,1)")
        db.execute("""CREATE TABLE kline_coverage(symbol TEXT, interval TEXT,
            window_start INTEGER, window_end INTEGER, checked_at_ms INTEGER,
            PRIMARY KEY(symbol,interval,window_start,window_end))""")
        db.execute("INSERT INTO kline_coverage VALUES ('BTCUSDT','1m',0,0,30000)")
    calls = []
    monkeypatch.setattr(binance, "_fetch_binance", lambda *args, **kwargs: calls.append(1) or [bar(0, 14)])
    result, source = binance.get_klines("BTCUSDT", 0, 60000, interval="1m", allow_synthetic=False)
    assert result["close"].tolist() == [14] and source == "binance" and calls == [1]
    again, source = binance.get_klines("BTCUSDT", 0, 60000, interval="1m", allow_synthetic=False)
    assert source == "cache" and again["close"].tolist() == [14] and calls == [1]


def test_history_extension_only_fetches_missing_tail_and_internal_holes(monkeypatch):
    calls = []
    def fetch(symbol, interval, start, end, **kwargs):
        calls.append((start, end))
        return [bar(t) for t in range(start, end, 60000)]
    monkeypatch.setattr(binance, "_fetch_binance", fetch)
    binance.get_klines("BTCUSDT", 0, 600000, interval="1m", allow_synthetic=False)
    result, _ = binance.get_klines("BTCUSDT", 0, 720000, interval="1m", allow_synthetic=False)
    assert len(result) == 12 and calls == [(0, 600000), (600000, 720000)]
    # Separate symbol with known bars but no coverage: fill the internal hole.
    binance._write_cache("ETHUSDT", "1m", [bar(0), bar(120000)])
    result, _ = binance.get_klines("ETHUSDT", 0, 180000, interval="1m", allow_synthetic=False)
    assert len(result) == 3 and calls[-1] == (60000, 120000)


def test_identical_cold_history_requests_share_one_provider_call(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return [bar(0)]
    monkeypatch.setattr(binance, "_fetch_binance", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(binance.get_klines, "BTCUSDT", 0, 60000, interval="1m", allow_synthetic=False)
        try:
            assert entered.wait(1)
            two = pool.submit(binance.get_klines, "BTCUSDT", 0, 60000, interval="1m", allow_synthetic=False)
        finally:
            release.set()
        assert len(one.result(timeout=2)[0]) == len(two.result(timeout=2)[0]) == 1
    assert calls == [1]


def test_sliding_history_windows_compact_coverage_and_reuse_subranges():
    for index in range(100):
        binance._mark_coverage("BTCUSDT", "1m", index * 60000, (index + 10) * 60000)
    with binance._conn() as db:
        assert db.execute("SELECT count(*) FROM kline_coverage WHERE settled_version=1").fetchone()[0] == 1
    assert binance._coverage_verified("BTCUSDT", "1m", 15 * 60000, 19 * 60000)


def test_funding_reuses_history_but_does_not_cache_partial_as_complete(monkeypatch):
    previous_errors = binance._funding_cache.statistics()["load_error"]
    calls = []
    monkeypatch.setattr(binance, "_fetch_funding_history",
                        lambda *args: calls.append(1) or [(1000, .001)])
    assert binance.get_funding_history("BTCUSDT", 0, 2000) == [(1000, .001)]
    assert binance.get_funding_history("btcusdt", 0, 2000) == [(1000, .001)]
    assert calls == [1]
    def incomplete(*args):
        calls.append(1)
        raise binance._PartialFundingError([(3000, .002)])
    monkeypatch.setattr(binance, "_fetch_funding_history", incomplete)
    for _ in range(5):
        assert binance.get_funding_history("ETHUSDT", 0, 4000) == [(3000, .002)]
    assert len(calls) == 2 and binance._funding_cache.statistics()["load_error"] == previous_errors + 1


def test_hotcoin_limit_variations_reuse_one_source_snapshot(monkeypatch):
    calls = []
    def fetch():
        calls.append(1)
        return [{"symbol": "BTCUSDT", "quoteVolume": "20000000", "lastPrice": "50000",
                 "priceChangePercent": "5"}]
    monkeypatch.setattr(hotcoins, "_fetch_tickers", fetch)
    for limit in (3, 10, 20, 3, 10):
        assert hotcoins.get_hot_coins(limit)["coins"]
    assert calls == [1]


def test_chart_limits_slice_one_shared_history_without_mutating_it(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs["limit"])
        return [{"t": t, "c": t} for t in range(kwargs["limit"])]
    monkeypatch.setattr(chart, "get_recent_klines", fetch)
    small = chart.get_candles("BTCUSDT", limit=10)
    small["candles"][-1]["c"] = -999
    large = chart.get_candles("BTCUSDT", limit=50)
    assert len(large["candles"]) == 50 and large["candles"][-1]["c"] != -999
    assert calls == [chart.MAX_LIMIT]
    assert chart._cache.statistics()["entries"] == 1


def test_feargreed_failure_cooldown_prevents_repeated_provider_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(feargreed, "_fetch", lambda: calls.append(1) or None)
    for _ in range(5):
        assert feargreed.get_fear_greed()["ok"] is False
    assert calls == [1]


def test_fx_refresh_uses_last_good_rate_and_a_separate_lifetime(monkeypatch):
    clock = [0]
    monkeypatch.setattr(kimchi._cache, "clock", lambda: clock[0])
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"rates": {"KRW": 1500}}
    class Client:
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(kimchi, "get_http_client", Client)
    first = kimchi.get_usdkrw()
    assert first["usdkrw"] == 1500 and not first["is_fallback"]
    clock[0] = kimchi.CACHE_SECONDS + 1
    assert not kimchi.get_usdkrw()["stale"]
    class FailedClient:
        def get(self, *args, **kwargs): raise RuntimeError("offline")
    monkeypatch.setattr(kimchi, "get_http_client", FailedClient)
    clock[0] = kimchi.FX_CACHE_SECONDS + 1
    stale = kimchi.get_usdkrw()
    assert stale["usdkrw"] == 1500 and stale["stale"] and not stale["is_fallback"]
    assert stale["updated_at"] == first["updated_at"]
    close_cache_runtime()


def test_paper_price_never_uses_stale_values_for_execution(monkeypatch):
    clock = [0]
    monkeypatch.setattr(binance._price_cache, "clock", lambda: clock[0])
    monkeypatch.setattr(binance, "get_ticker_price", lambda _: 1500)
    assert binance.get_ticker_price_cached("BTCUSDT") == 1500
    clock[0] = 3
    monkeypatch.setattr(binance, "get_ticker_price", lambda _: None)
    assert binance.get_ticker_price_cached("BTCUSDT") is None


def test_symbols_expired_request_does_not_hold_a_global_network_lock(monkeypatch):
    def rows(market, symbol):
        return {symbol: {"symbol": symbol, "spot": market == "spot", "futures": market == "futures"}}
    monkeypatch.setattr(symbols, "_fetch_market_rows", lambda market: rows(market, "BTCUSDT"))
    symbols.list_symbols(now=0)
    entered, release = threading.Event(), threading.Event()
    def delayed(market):
        entered.set()
        assert release.wait(2)
        return rows(market, "ETHUSDT")
    monkeypatch.setattr(symbols, "_fetch_market_rows", delayed)
    try:
        stale = symbols.list_symbols(now=symbols.CACHE_TTL_S + 1)
        assert stale["items"] == [{"symbol": "BTCUSDT", "spot": True, "futures": True}] and stale["stale"]
        assert entered.wait(1)
        assert symbols.list_symbols(now=symbols.CACHE_TTL_S + 2)["stale"]
    finally:
        release.set()
        close_cache_runtime()
    assert symbols.list_symbols(now=symbols.CACHE_TTL_S + 3)["items"] == [{"symbol": "ETHUSDT", "spot": True, "futures": True}]
