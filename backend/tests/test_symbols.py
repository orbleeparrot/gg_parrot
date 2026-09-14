"""Tradable symbol list + coin logo proxy (builder search only accepts real tickers)."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.data import symbols
from app.cache_runtime import close_cache_runtime
from app.main import app

client = TestClient(app)


SPOT_DOC = {"symbols": [
    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "permissions": ["SPOT", "MARGIN"]},
    {"symbol": "PEPEUSDT", "baseAsset": "PEPE", "quoteAsset": "USDT", "status": "TRADING", "permissions": [], "permissionSets": [["SPOT"]]},
    {"symbol": "OLDUSDT", "baseAsset": "OLD", "quoteAsset": "USDT", "status": "BREAK", "permissions": ["SPOT"]},
    {"symbol": "BTCBUSD", "baseAsset": "BTC", "quoteAsset": "BUSD", "status": "TRADING", "permissions": ["SPOT"]},
]}
FUT_DOC = {"symbols": [
    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "contractType": "PERPETUAL"},
    {"symbol": "1000PEPEUSDT", "baseAsset": "1000PEPE", "quoteAsset": "USDT", "status": "TRADING", "contractType": "PERPETUAL"},
    {"symbol": "BTCUSDT_260327", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "contractType": "CURRENT_QUARTER"},
]}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.delenv("BINANCE_API_BASE", raising=False)
    monkeypatch.delenv("BINANCE_FAPI_BASE", raising=False)
    symbols.reset_cache()
    symbols._logo_cache.clear()
    yield
    close_cache_runtime()
    symbols.reset_cache()
    symbols._logo_cache.clear()


def test_list_symbols_merges_spot_and_perpetual(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        doc = SPOT_DOC if request.url.host == "api.binance.com" else FUT_DOC
        return httpx.Response(200, json=doc)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    out = symbols.list_symbols(now=1000.0)
    rows = {r["symbol"]: r for r in out["items"]}
    assert set(rows) == {"BTCUSDT", "PEPEUSDT", "1000PEPEUSDT"}  # BREAK, BUSD, quarterly are out
    assert rows["BTCUSDT"] == {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT", "spot": True, "futures": True}
    assert rows["PEPEUSDT"]["futures"] is False and rows["1000PEPEUSDT"]["spot"] is False
    assert out["count"] == 3 and out["stale"] is False
    # cached: a second call inside the TTL does not hit Binance again
    symbols.list_symbols(now=1000.0 + 60)
    assert len(calls) == 2


def test_list_symbols_serves_stale_on_failure(monkeypatch):
    ok = lambda request: httpx.Response(200, json=SPOT_DOC if request.url.host == "api.binance.com" else FUT_DOC)
    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(ok))
    symbols.list_symbols(now=0.0)
    boom = lambda request: httpx.Response(451)
    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(boom))
    out = symbols.list_symbols(now=symbols.CACHE_TTL_S + 1)
    assert out["stale"] is True and out["count"] == 3


def test_list_symbols_raises_when_nothing_cached(monkeypatch):
    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(lambda r: httpx.Response(451)))
    with pytest.raises(httpx.HTTPStatusError):
        symbols.list_symbols(now=0.0)


def test_configured_public_hosts_are_used_for_both_markets(monkeypatch):
    monkeypatch.setenv("BINANCE_API_BASE", "https://data-api.binance.vision/")
    monkeypatch.setenv("BINANCE_FAPI_BASE", "https://futures.example.invalid/")
    urls = []

    def handler(request):
        urls.append(f"{request.url.scheme}://{request.url.host}{request.url.path}")
        return httpx.Response(200, json=SPOT_DOC if request.url.host == "data-api.binance.vision" else FUT_DOC)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    out = symbols.list_symbols(now=0)
    assert out["count"] == 3 and not out["partial"] and not out["stale"]
    assert set(urls) == {"https://data-api.binance.vision/api/v3/exchangeInfo",
                         "https://futures.example.invalid/fapi/v1/exchangeInfo"}


def test_spot_query_reduces_payload_without_changing_futures_request(monkeypatch):
    queries = {}

    def handler(request):
        spot = request.url.host == "api.binance.com"
        queries["spot" if spot else "futures"] = dict(request.url.params)
        return httpx.Response(200, json=SPOT_DOC if spot else FUT_DOC)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    assert symbols.list_symbols(now=0)["count"] == 3
    assert queries == {
        "spot": {"permissions": "SPOT", "showPermissionSets": "false", "symbolStatus": "TRADING"},
        "futures": {},
    }


def test_hidden_permissions_use_explicit_spot_trading_eligibility():
    common = {"baseAsset": "TEST", "quoteAsset": "USDT", "status": "TRADING", "permissions": []}
    doc = {"symbols": [
        {**common, "symbol": "SPOTUSDT", "isSpotTradingAllowed": True},
        {**common, "symbol": "MARGINUSDT", "isSpotTradingAllowed": False, "isMarginTradingAllowed": True},
        {**common, "symbol": "DISABLEDUSDT", "isSpotTradingAllowed": False, "permissionSets": [["SPOT"]]},
        {**common, "symbol": "LEGACYUSDT", "permissionSets": [["SPOT"]]},
        {**common, "symbol": "LEGACYMARGINUSDT", "permissions": ["MARGIN"]},
        {**common, "symbol": "HALTUSDT", "status": "HALT", "isSpotTradingAllowed": True},
        {**common, "symbol": "SPOTBTC", "quoteAsset": "BTC", "isSpotTradingAllowed": True},
    ]}
    rows = symbols._spot_rows(doc)
    assert set(rows) == {"SPOTUSDT", "LEGACYUSDT"}
    assert all(row["spot"] and not row["futures"] for row in rows.values())


@pytest.mark.parametrize("unavailable", ["spot", "futures"])
def test_one_market_failure_keeps_available_symbols_searchable(monkeypatch, unavailable):
    def handler(request):
        market = "spot" if request.url.host == "api.binance.com" else "futures"
        if market == unavailable:
            return httpx.Response(451)
        return httpx.Response(200, json=SPOT_DOC if market == "spot" else FUT_DOC)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    out = symbols.list_symbols(now=0)
    rows = {row["symbol"]: row for row in out["items"]}
    available = "futures" if unavailable == "spot" else "spot"
    assert out["partial"] and out["stale"]
    assert rows["BTCUSDT"][available] is True
    assert rows["BTCUSDT"][unavailable] is False
    assert out["sources"][unavailable] == {"status": "unavailable", "fetched_at": None}
    assert out["sources"][available]["status"] == "ready"


def test_failed_market_recovers_soon_without_refetching_the_healthy_market(monkeypatch):
    calls = {"spot": 0, "futures": 0}
    recovering = [False]

    def handler(request):
        market = "spot" if request.url.host == "api.binance.com" else "futures"
        calls[market] += 1
        if market == "futures" and not recovering[0]:
            return httpx.Response(451)
        return httpx.Response(200, json=SPOT_DOC if market == "spot" else FUT_DOC)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    assert symbols.list_symbols(now=0)["partial"]
    recovering[0] = True
    assert symbols.list_symbols(now=symbols.RETRY_SECONDS - 1)["partial"]
    assert calls == {"spot": 1, "futures": 1}
    recovered = symbols.list_symbols(now=symbols.RETRY_SECONDS + 1)
    assert not recovered["partial"] and not recovered["stale"]
    assert recovered["count"] == 3
    assert calls == {"spot": 1, "futures": 2}


@pytest.mark.parametrize("failed_response", [451, "empty"])
def test_failed_refresh_preserves_each_markets_last_good_flags(monkeypatch, failed_response):
    refreshing = [False]

    def handler(request):
        spot = request.url.host == "api.binance.com"
        if refreshing[0] and not spot:
            return httpx.Response(200, json={"symbols": []}) if failed_response == "empty" else httpx.Response(451)
        return httpx.Response(200, json=SPOT_DOC if spot else FUT_DOC)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    original = symbols.list_symbols(now=0)
    refreshing[0] = True
    assert symbols.list_symbols(now=symbols.CACHE_TTL_S + 1)["stale"]
    close_cache_runtime()  # Finish the fixture refresh, including the failed source.
    result = symbols.list_symbols(now=symbols.CACHE_TTL_S + 2)
    assert result["items"] == original["items"]
    assert result["stale"] and not result["partial"]
    assert result["sources"]["spot"]["status"] == "ready"
    assert result["sources"]["futures"] == {"status": "stale", "fetched_at": 0}


def test_production_mirror_keeps_dot_searchable_when_futures_is_unavailable(monkeypatch):
    monkeypatch.setenv("BINANCE_API_BASE", "https://data-api.binance.vision")
    dot = {"symbols": [{"symbol": "DOTUSDT", "baseAsset": "DOT", "quoteAsset": "USDT",
                         "status": "TRADING", "permissions": ["SPOT"]}]}

    def handler(request):
        if request.url.host == "data-api.binance.vision":
            return httpx.Response(200, json=dot)
        return httpx.Response(451)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    response = client.get("/api/symbols")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=5, s-maxage=5"
    body = response.json()
    assert body["items"] == [{"symbol": "DOTUSDT", "base": "DOT", "quote": "USDT", "spot": True, "futures": False}]
    assert body["partial"] and body["stale"]


def test_symbols_endpoint(monkeypatch):
    monkeypatch.setattr(symbols, "list_symbols", lambda: {"items": [{"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT", "spot": True, "futures": True}], "count": 1, "fetched_at": 1.0, "stale": False})
    resp = client.get("/api/symbols")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["symbol"] == "BTCUSDT"


def test_symbols_endpoint_503_when_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("down")
    monkeypatch.setattr(symbols, "list_symbols", boom)
    resp = client.get("/api/symbols")
    assert resp.status_code == 503


def test_coin_logo_proxy_caches_and_404s(monkeypatch):
    hits = []

    def handler(request):
        hits.append(request.url.path)
        if request.url.path.endswith("/BTC.png"):
            return httpx.Response(200, content=b"\x89PNG-fake", headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(symbols, "get_http_client", lambda: _client(handler))
    resp = client.get("/api/coin-logo/btc.png")
    assert resp.status_code == 200 and resp.content == b"\x89PNG-fake"
    assert resp.headers["cache-control"].startswith("public")
    client.get("/api/coin-logo/BTC.png")
    assert hits == ["/static/assets/logos/BTC.png"]  # second call served from memory
    assert client.get("/api/coin-logo/NOPE.png").status_code == 404
    assert client.get("/api/coin-logo/..%2F..png").status_code == 404
