"""Tradable symbol list + coin logo proxy (builder search only accepts real tickers)."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.data import symbols
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
def _fresh_cache():
    symbols.reset_cache()
    symbols._logo_cache.clear()
    yield
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
