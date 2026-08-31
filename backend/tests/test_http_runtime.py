from __future__ import annotations

import threading
import time
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from inspect import getsource
from pathlib import Path

from app import chart, feargreed, hangang, hotcoins, kimchi, news
from app import http_runtime
from app.data import binance


def test_shared_http_client_reuses_one_connection_pool(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def close(self):
            pass

    http_runtime.close_shared_http_client()
    monkeypatch.setattr(http_runtime.httpx, "Client", FakeClient)

    first = http_runtime.get_http_client()
    second = http_runtime.get_http_client()

    assert first is second
    assert len(created) == 1
    http_runtime.close_shared_http_client()


def test_application_lifespan_closes_shared_http_resources():
    main_source = Path(http_runtime.__file__).with_name("main.py").read_text(encoding="utf-8")
    paper_shutdown = main_source.index("await paper_mod.shutdown_running_sessions()")
    optimize_shutdown = main_source.index("optimize_runtime_mod.shutdown()")
    assert paper_shutdown < optimize_shutdown
    assert "http_runtime_mod.close_http_runtime()" in main_source


def test_cold_singleflight_runs_loader_once_for_concurrent_callers():
    group = http_runtime.SingleFlightGroup()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(group.run, "market", loader)
        assert entered.wait(1)
        second = pool.submit(group.run, "market", loader)
        release.set()
        results = [first.result(timeout=1), second.result(timeout=1)]

    assert calls == 1
    assert [value for value, _state in results] == [{"value": 7}, {"value": 7}]
    assert {state for _value, state in results} == {"loaded", "shared"}


def test_stale_follower_returns_without_waiting_for_refresh():
    group = http_runtime.SingleFlightGroup()
    entered = threading.Event()
    release = threading.Event()

    def loader():
        entered.set()
        assert release.wait(1)
        return "fresh"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(group.run, "ticker", loader, stale_value="old")
        assert entered.wait(1)
        started = time.monotonic()
        stale_value, state = group.run("ticker", loader, stale_value="old")
        elapsed = time.monotonic() - started
        release.set()
        fresh_value, fresh_state = first.result(timeout=1)

    assert (stale_value, state) == ("old", "stale")
    assert elapsed < 0.1
    assert (fresh_value, fresh_state) == ("fresh", "loaded")


def test_nested_parallel_calls_do_not_starve_the_shared_executor():
    script = textwrap.dedent(
        """
        import os
        import threading
        from concurrent.futures import ThreadPoolExecutor

        os.environ["EXTERNAL_HTTP_PARALLEL_WORKERS"] = "8"

        from app import http_runtime

        callers = 8
        gate = threading.Barrier(callers)

        def outer_loader():
            gate.wait(timeout=2)
            return http_runtime.run_parallel({"inner": lambda: "ok"})["inner"]

        def invoke(_index):
            return http_runtime.run_parallel({"outer": outer_loader})["outer"]

        with ThreadPoolExecutor(max_workers=callers) as pool:
            results = list(pool.map(invoke, range(callers)))

        assert results == ["ok"] * callers
        http_runtime.close_http_runtime()
        """
    )

    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        import pytest

        pytest.fail("nested run_parallel calls deadlocked the shared executor")

    assert completed.returncode == 0, completed.stderr


def test_hotcoins_collapses_a_cold_cache_stampede(monkeypatch):
    hotcoins._cache.clear()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return [
            {
                "symbol": "BTCUSDT",
                "priceChangePercent": "1",
                "lastPrice": "60000",
                "quoteVolume": "1000000000",
            }
        ]

    monkeypatch.setattr(hotcoins, "_fetch_tickers", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hotcoins.get_hot_coins, 10)
        assert entered.wait(1)
        second = pool.submit(hotcoins.get_hot_coins, 10)
        release.set()
        results = [first.result(timeout=1), second.result(timeout=1)]

    assert calls == 1
    assert all(result["coins"][0]["base"] == "BTC" for result in results)


def test_kimchi_fetches_independent_sources_in_parallel(monkeypatch):
    gate = threading.Barrier(3)

    def upbit(_market):
        gate.wait(timeout=1)
        return 140_000_000.0

    def binance(_symbol):
        gate.wait(timeout=1)
        return 100_000.0

    def fx():
        gate.wait(timeout=1)
        return 1400.0, False

    monkeypatch.setattr(kimchi, "_upbit_price", upbit)
    monkeypatch.setattr(kimchi, "_binance_price", binance)
    monkeypatch.setattr(kimchi, "_usdkrw", fx)

    assert kimchi.get_premium("BTC")["ok"] is True


def test_kimchi_component_cache_collapses_concurrent_misses(monkeypatch):
    kimchi._cache.clear()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"trade_price": 140_000_000.0}]

    class Client:
        def get(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(1)
            return Response()

    monkeypatch.setattr(kimchi, "get_http_client", lambda: Client())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(kimchi._upbit_price, "KRW-BTC")
        assert entered.wait(1)
        second = pool.submit(kimchi._upbit_price, "KRW-BTC")
        release.set()
        assert first.result(timeout=1) == second.result(timeout=1) == 140_000_000.0
    assert calls == 1


def test_chart_collapses_concurrent_cache_misses(monkeypatch):
    chart._cache.clear()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "closed": False}]

    monkeypatch.setattr(chart, "get_recent_klines", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chart.get_candles, "BTCUSDT")
        assert entered.wait(1)
        second = pool.submit(chart.get_candles, "BTCUSDT")
        release.set()
        results = [first.result(timeout=1), second.result(timeout=1)]

    assert calls == 1
    assert all(result["candles"] for result in results)


def test_market_polling_modules_use_the_shared_http_pool():
    for module in (binance, hotcoins, kimchi, hangang, feargreed, news):
        source = getsource(module)
        assert "httpx.Client(" not in source, module.__name__
        assert "get_http_client" in source, module.__name__


def test_paper_ticker_collapses_concurrent_symbol_refreshes(monkeypatch):
    binance._price_cache.clear()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch(_symbol):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return 123.0

    monkeypatch.setattr(binance, "get_ticker_price", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(binance.get_ticker_price_cached, "BTCUSDT")
        assert entered.wait(1)
        second = pool.submit(binance.get_ticker_price_cached, "BTCUSDT")
        release.set()
        assert first.result(timeout=1) == second.result(timeout=1) == 123.0
    assert calls == 1


def test_hangang_collapses_concurrent_refreshes(monkeypatch):
    hangang._cache = None
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return {"ok": True, "temperature": 22.0}

    monkeypatch.setattr(hangang, "_fetch", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hangang.get_temp)
        assert entered.wait(1)
        second = pool.submit(hangang.get_temp)
        release.set()
        assert first.result(timeout=1)["temperature"] == 22.0
        assert second.result(timeout=1)["temperature"] == 22.0
    assert calls == 1


def test_eden_google_queries_run_in_parallel(monkeypatch):
    gate = threading.Barrier(3)

    def fetch(query, **_kwargs):
        gate.wait(timeout=1)
        return [{"title": query, "source": "test", "url": query, "published": ""}]

    monkeypatch.setattr(news, "_fetch_news", fetch)
    payload = news._coin_news_envelope("EDEN", strict=True)
    assert payload["candidate_count"] == 3


def test_news_collector_fetches_independent_sources_in_parallel(monkeypatch):
    gate = threading.Barrier(3)

    def google(*_args, **_kwargs):
        gate.wait(timeout=1)
        return {"items": [], "candidate_count": 0, "query": "EDEN"}

    def source(**_kwargs):
        gate.wait(timeout=1)
        return []

    monkeypatch.setattr(news, "_coin_news_envelope", google)
    monkeypatch.setattr(news, "_fetch_openeden_news", source)
    monkeypatch.setattr(news, "_fetch_coindesk_news", source)
    payload = news.fetch_coin_news_for_collector("EDEN")
    assert len(payload["sources"]) == 3


def test_coindesk_feed_collapses_concurrent_cache_misses(monkeypatch):
    news._coindesk_cache = None
    news._coindesk_error_cache = None
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class Response:
        text = """<rss><channel><item><title>Bitcoin market update</title>
        <link>https://example.com/btc</link><pubDate>Tue, 25 Aug 2026 01:00:00 GMT</pubDate>
        </item></channel></rss>"""

        def raise_for_status(self):
            pass

    class Client:
        def get(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(1)
            return Response()

    monkeypatch.setattr(news, "get_http_client", lambda: Client())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(news._fetch_coindesk_news, strict=True)
        assert entered.wait(1)
        second = pool.submit(news._fetch_coindesk_news, strict=True)
        release.set()
        assert first.result(timeout=1) == second.result(timeout=1)
    assert calls == 1
