"""Domestic catalogue HTTP TTL must not amplify the server refresh window."""
import pytest
from fastapi import Response

from app import main


@pytest.mark.parametrize("exchange", ["upbit", "bithumb"])
def test_domestic_symbol_http_cache_is_short_and_retains_freshness(monkeypatch, exchange):
    payload = {"items": [{"symbol": "KRW-BTC"}], "stale": False,
               "fetched_at": 1_800_000_000, "sources": {"spot": {"status": "ready", "fetched_at": 1_800_000_000}}}
    monkeypatch.setattr(main.symbols_mod, "list_symbols", lambda **kwargs: payload)
    response = Response()

    result = main.symbols(response, exchange=exchange)

    assert result == payload
    assert response.headers["cache-control"] == "public, max-age=15, s-maxage=15, stale-while-revalidate=45"


@pytest.mark.parametrize("payload", [{"items": [{"symbol": "KRW-BTC"}], "stale": True}, {"items": []}])
def test_domestic_stale_or_empty_list_is_only_cached_for_five_seconds(monkeypatch, payload):
    monkeypatch.setattr(main.symbols_mod, "list_symbols", lambda **kwargs: payload)
    response = Response()
    assert main.symbols(response, exchange="upbit") == payload
    assert response.headers["cache-control"] == "public, max-age=5, s-maxage=5"


def test_binance_default_retains_no_argument_provider_and_existing_http_cache(monkeypatch):
    payload = {"items": [{"symbol": "BTCUSDT"}], "stale": False}
    monkeypatch.setattr(main.symbols_mod, "list_symbols", lambda: payload)
    response = Response()
    assert main.symbols(response) == payload
    assert response.headers["cache-control"] == "public, max-age=300, s-maxage=300"
