"""Public KRW market adapters are exchange-isolated and never synthesize prices."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from app.data import krw
from app.data.binance import IncompleteMarketDataError, NoSpotDataError, TooManyBarsError


def _row(t, price=100):
    return {"candle_date_time_utc": datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": price, "high_price": price + 1, "low_price": price - 1,
            "trade_price": price, "candle_acc_trade_volume": 2}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(krw, "REQUEST_GAP_SECONDS", 0)
    krw.reset_cache()


def test_recent_pages_300_bars_sorts_deduplicates_and_marks_open(monkeypatch):
    now = 1_800_000_060_000
    monkeypatch.setattr(krw.time, "time", lambda: now / 1000)
    edge = now // 60_000 * 60_000
    rows = [_row(edge - i * 60_000) for i in range(300)]
    calls = []
    def request(exchange, path, params):
        calls.append(params)
        return rows[:200] if len(calls) == 1 else rows[200:]
    monkeypatch.setattr(krw, "_request", request)
    out = krw.get_recent_klines("KRW-BTC", limit=300, exchange="upbit")
    assert len(out) == 300 and len(calls) == 2
    assert out == sorted(out, key=lambda row: row["t"])
    assert out[-1]["closed"] is False and out[-2]["closed"] is True
    assert calls[0]["count"] == 200 and calls[1]["count"] == 100


def test_history_sparse_trade_gaps_are_verified_without_repeat_fetch(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 600_000) / 1000)
    calls = []
    def request(exchange, path, params):
        calls.append(params)
        return [_row(start + 180_000), _row(start)]
    monkeypatch.setattr(krw, "_request", request)
    first, source = krw.get_klines("KRW-BTC", start, start + 300_000, "1m", exchange="upbit")
    second, _ = krw.get_klines("KRW-BTC", start, start + 300_000, "1m", exchange="upbit")
    assert len(first) == 2 and first.equals(second)
    assert source == "upbit:spot" and len(calls) == 1


def test_same_symbol_history_is_separate_per_exchange(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 600_000) / 1000)
    monkeypatch.setattr(krw, "_request", lambda exchange, path, params: [_row(start, 100 if exchange == "upbit" else 200)])
    upbit, _ = krw.get_klines("KRW-BTC", start, start + 60_000, "1m", exchange="upbit")
    bithumb, _ = krw.get_klines("KRW-BTC", start, start + 60_000, "1m", exchange="bithumb")
    assert upbit.iloc[0]["close"] == 100 and bithumb.iloc[0]["close"] == 200


def test_sliding_history_fetches_only_unverified_tail(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 60_000 * 500) / 1000)
    rows = [_row(start + 60_000 * i) for i in range(450)]
    calls = []
    def request(exchange, path, params):
        cursor = datetime.fromisoformat(params["to"]).timestamp() * 1000
        calls.append(cursor)
        return [row for row in reversed(rows) if datetime.fromisoformat(row["candle_date_time_utc"]).replace(tzinfo=timezone.utc).timestamp() * 1000 < cursor][:200]
    monkeypatch.setattr(krw, "_request", request)
    first, _ = krw.get_klines("KRW-BTC", start, start + 60_000 * 400, "1m", exchange="upbit")
    assert len(first) == 400 and len(calls) == 2
    calls.clear()
    next_frame, _ = krw.get_klines("KRW-BTC", start, start + 60_000 * 401, "1m", exchange="upbit")
    assert len(next_frame) == 401 and calls == [start + 60_000 * 401]


def test_duplicate_rows_in_full_page_do_not_verify_unfetched_history(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 60_000 * 500) / 1000)
    rows = [_row(start + i * 60_000) for i in range(300)]
    calls = []
    def request(exchange, path, params):
        calls.append(params)
        return list(reversed(rows[102:])) + [rows[-1], rows[-1]] if len(calls) == 1 else list(reversed(rows[:102]))
    monkeypatch.setattr(krw, "_request", request)
    frame, _ = krw.get_klines("KRW-BTC", start, start + 60_000 * 300, "1m", exchange="upbit")
    assert len(frame) == 300 and len(calls) == 2


def test_to_cursor_is_explicit_utc_for_upbit_and_kst_for_bithumb():
    stamp = 1_800_000_000_000
    upbit = datetime.fromisoformat(krw._to_cursor(stamp, "upbit"))
    bithumb_naive = datetime.fromisoformat(krw._to_cursor(stamp, "bithumb"))
    assert bithumb_naive.tzinfo is None
    bithumb = bithumb_naive.replace(tzinfo=timezone(timedelta(hours=9)))
    assert int(upbit.timestamp() * 1000) == stamp
    assert int(bithumb.timestamp() * 1000) == stamp
    assert upbit.utcoffset().total_seconds() == 0
    assert bithumb.utcoffset().total_seconds() == 9 * 3600


def test_history_request_budget_is_checked_before_network(monkeypatch):
    monkeypatch.setattr(krw, "_request", lambda *args: pytest.fail("must reject before network"))
    with pytest.raises(TooManyBarsError):
        krw.get_klines("KRW-BTC", 0, 60_000 * 20_001, "1m", exchange="upbit")


def test_domestic_futures_and_invalid_symbol_rejected_before_network(monkeypatch):
    monkeypatch.setattr(krw, "_request", lambda *args: pytest.fail("must reject before network"))
    with pytest.raises(ValueError):
        krw.get_recent_klines("KRW-BTC", exchange="upbit", market="futures")
    with pytest.raises(ValueError):
        krw.get_recent_klines("BTCUSDT", exchange="upbit")


def test_repeated_page_cannot_silently_mark_history_verified(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 60_000 * 500) / 1000)
    rows = [_row(start + 60_000 * (400 - i)) for i in range(200)]
    monkeypatch.setattr(krw, "_request", lambda *args: rows)
    with pytest.raises(IncompleteMarketDataError):
        krw.get_klines("KRW-BTC", start, start + 60_000 * 450, "1m", exchange="upbit")


def test_rate_limit_retry_is_bounded_and_never_honors_long_sleep(monkeypatch):
    request = httpx.Request("GET", "https://api.upbit.com/v1/ticker")
    response = httpx.Response(429, request=request, headers={"Retry-After": "100"})
    class Client:
        def __init__(self): self.calls = 0
        def get(self, *args, **kwargs): self.calls += 1; return response
    client = Client()
    monkeypatch.setattr(krw, "get_http_client", lambda: client)
    monkeypatch.setattr(krw.time, "sleep", lambda delay: pytest.fail("long retry must not sleep"))
    with pytest.raises(httpx.HTTPStatusError):
        krw._request("upbit", "/v1/ticker", {"markets": "KRW-BTC"})
    assert client.calls == 1


def test_empty_market_raises_without_synthetic_prices(monkeypatch):
    monkeypatch.setattr(krw, "_request", lambda *args: [])
    with pytest.raises(NoSpotDataError):
        krw.get_recent_klines("KRW-BTC", exchange="upbit")


@pytest.mark.parametrize("exchange,key", [("upbit", "is_details"), ("bithumb", "isDetails")])
def test_symbol_catalog_uses_exchange_documented_details_parameter(monkeypatch, exchange, key):
    def request(actual_exchange, path, params):
        assert actual_exchange == exchange and params == {key: "true"}
        return [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin",
                 "market_event": {"warning": True}}, {"market": "USDT-BTC"}]
    monkeypatch.setattr(krw, "_request", request)
    rows = krw.list_symbols(exchange)["items"]
    assert len(rows) == 1 and rows[0]["warning"] is True
    assert rows[0]["spot"] and not rows[0]["futures"] and rows[0]["quote"] == "KRW"


def test_ticker_price_cache_never_trades_with_stale_fallback(monkeypatch):
    monkeypatch.setattr(krw, "_request", lambda *args: [{"market": "KRW-BTC", "trade_price": 123_000_000}])
    assert krw.get_ticker_price_cached("KRW-BTC", "upbit") == 123_000_000
    key = ("upbit", ("KRW-BTC",))
    after_expiry = krw._prices_cache._entries[key].expires + 1
    monkeypatch.setattr(krw._prices_cache, "clock", lambda: after_expiry)
    monkeypatch.setattr(krw, "_request", lambda *args: [])
    assert krw.get_ticker_price_cached("KRW-BTC", "upbit") is None


def test_all_tickers_keep_native_krw_quote_volume_and_change_units(monkeypatch):
    def request(exchange, path, params):
        if path == "/v1/market/all":
            return [{"market": "KRW-BTC"}]
        return [{"market": "KRW-BTC", "trade_price": 100_000_000, "high_price": 101_000_000,
                 "low_price": 99_000_000, "signed_change_rate": .02, "acc_trade_price_24h": 20_000_000_000}]
    monkeypatch.setattr(krw, "_request", request)
    rows = krw.get_all_tickers("bithumb")
    assert rows[0]["symbol"] == "KRW-BTC" and rows[0]["lastPrice"] == 100_000_000
    assert rows[0]["priceChangePercent"] == 2 and rows[0]["quoteVolume"] == 20_000_000_000
    assert rows[0]["exchange"] == "bithumb" and rows[0]["quote"] == "KRW"


def test_simultaneous_same_history_uses_one_shared_fetch(monkeypatch):
    start = 1_800_000_000_000 // 60_000 * 60_000
    monkeypatch.setattr(krw.time, "time", lambda: (start + 600_000) / 1000)
    started, release, calls = threading.Event(), threading.Event(), []
    def request(*args):
        calls.append(args)
        started.set()
        assert release.wait(2)
        return [_row(start)]
    monkeypatch.setattr(krw, "_request", request)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(krw.get_klines, "KRW-BTC", start, start + 60_000, "1m", exchange="upbit")
        assert started.wait(2)
        others = [pool.submit(krw.get_klines, "KRW-BTC", start, start + 60_000, "1m", exchange="upbit") for _ in range(2)]
        release.set()
        assert len(first.result()[0]) == 1
        assert all(len(future.result()[0]) == 1 for future in others)
    assert len(calls) == 1


@pytest.mark.parametrize("interval,open_time", [("1d", "2026-10-01T15:00:00"), ("4h", "2026-10-02T03:00:00")])
def test_bithumb_kst_grid_preserves_native_utc_open_and_verified_coverage(monkeypatch, interval, open_time):
    step = krw.binance._INTERVAL_MS[interval]
    start = int(datetime.fromisoformat(open_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(krw.time, "time", lambda: (start + step * 3) / 1000)
    calls = []
    def request(exchange, path, params):
        calls.append(params)
        return [_row(start + step), _row(start)]
    monkeypatch.setattr(krw, "_request", request)
    first, _ = krw.get_klines("KRW-BTC", start, start + step * 2, interval, exchange="bithumb")
    second, _ = krw.get_klines("KRW-BTC", start, start + step * 2, interval, exchange="bithumb")
    assert first.equals(second) and len(calls) == 1
    assert first.iloc[0]["timestamp"].value // 1_000_000 == start
    assert first.iloc[-1]["timestamp"].value // 1_000_000 == start + step


@pytest.mark.parametrize("exchange", ["upbit", "bithumb"])
def test_symbol_catalog_refreshes_listing_and_delisting_after_sixty_seconds(monkeypatch, exchange):
    from app.cache_runtime import close_cache_runtime
    clock = [1_800_000_000.0]
    monkeypatch.setattr(krw.time, "time", lambda: clock[0])
    monkeypatch.setattr(krw._symbols_cache, "clock", lambda: clock[0])
    markets = ["KRW-BTC", "KRW-OLD"]
    calls = []
    def request(actual_exchange, path, params):
        calls.append(actual_exchange)
        return [{"market": market} for market in markets]
    monkeypatch.setattr(krw, "_request", request)

    original = krw.list_symbols(exchange)
    markets[:] = ["KRW-BTC", "KRW-NEW"]
    clock[0] += 59
    assert krw.list_symbols(exchange)["items"] == original["items"]
    assert calls == [exchange]
    clock[0] += 2
    refreshing = krw.list_symbols(exchange)
    close_cache_runtime()
    updated = krw.list_symbols(exchange)

    assert refreshing["stale"] is True
    assert refreshing["sources"]["spot"]["status"] == "stale"
    assert refreshing["fetched_at"] == original["fetched_at"]
    assert refreshing["cache_age_seconds"] == 61
    assert updated["stale"] is False
    assert [row["symbol"] for row in updated["items"]] == ["KRW-BTC", "KRW-NEW"]
    assert updated["fetched_at"] == clock[0] and updated["cache_age_seconds"] == 0
    assert updated["refresh_seconds"] == 60 and updated["max_age_seconds"] == 300
    assert calls == [exchange, exchange]


def test_symbol_catalog_outage_cannot_extend_last_good_list_beyond_five_minutes(monkeypatch):
    from app.cache_runtime import close_cache_runtime
    clock = [1_800_000_000.0]
    monkeypatch.setattr(krw.time, "time", lambda: clock[0])
    monkeypatch.setattr(krw._symbols_cache, "clock", lambda: clock[0])
    monkeypatch.setattr(krw, "_request", lambda *args: [{"market": "KRW-BTC"}])
    original = krw.list_symbols("upbit")
    def unavailable(*args):
        raise RuntimeError("exchange unavailable")
    monkeypatch.setattr(krw, "_request", unavailable)
    clock[0] += 61
    stale = krw.list_symbols("upbit")
    close_cache_runtime()

    assert stale["stale"] is True and stale["fetched_at"] == original["fetched_at"]
    assert stale["cache_age_seconds"] == 61
    clock[0] = original["fetched_at"] + 299
    almost_expired = krw.list_symbols("upbit")
    close_cache_runtime()
    assert almost_expired["stale"] is True and almost_expired["cache_age_seconds"] == 299
    clock[0] = original["fetched_at"] + 300
    with pytest.raises(RuntimeError, match="exchange unavailable"):
        krw.list_symbols("upbit")


def test_symbol_catalog_refresh_isolated_by_exchange(monkeypatch):
    from app.cache_runtime import close_cache_runtime
    clock = [1_800_000_000.0]
    monkeypatch.setattr(krw.time, "time", lambda: clock[0])
    monkeypatch.setattr(krw._symbols_cache, "clock", lambda: clock[0])
    ready = {"upbit": True, "bithumb": True}
    markets = {"upbit": ["KRW-BTC"], "bithumb": ["KRW-BTC", "KRW-OLD"]}
    def request(exchange, path, params):
        if not ready[exchange]:
            raise RuntimeError("exchange unavailable")
        return [{"market": market} for market in markets[exchange]]
    monkeypatch.setattr(krw, "_request", request)
    upbit = krw.list_symbols("upbit")
    bithumb = krw.list_symbols("bithumb")
    clock[0] += 61
    markets["upbit"].append("KRW-NEW")
    ready["bithumb"] = False
    krw.list_symbols("upbit")
    stale = krw.list_symbols("bithumb")
    close_cache_runtime()

    refreshed = krw.list_symbols("upbit")
    assert refreshed["stale"] is False and refreshed["count"] == 2
    assert refreshed["fetched_at"] != upbit["fetched_at"]
    assert stale["stale"] is True and stale["items"] == bithumb["items"]
    assert krw.list_symbols("bithumb")["sources"]["spot"]["status"] == "stale"
