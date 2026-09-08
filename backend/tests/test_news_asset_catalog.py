"""Unfamiliar ticker metadata, cache sharing and failure behavior without I/O."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from app import news_asset_catalog as catalog
from app.http_runtime import SingleFlightGroup


class Repository:
    def __init__(self, clock):
        self.clock = clock
        self.entries = {}
        self.reads = 0
        self.writes = 0

    def load_browser_pages(self, keys):
        self.reads += 1
        return {key: deepcopy(self.entries[key][0]) for key in keys
                if key in self.entries and self.entries[key][1] > self.clock[0] * 1000}

    def store_browser_pages(self, entries):
        self.writes += 1
        self.entries.update(deepcopy(entries))


@pytest.fixture
def provider(monkeypatch):
    clock = [1_800_000_000.0]
    repository = Repository(clock)
    monkeypatch.setattr(catalog, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(catalog, "_repository", lambda: repository)
    monkeypatch.setattr(catalog, "_cache", None)
    monkeypatch.setattr(catalog, "_retry_after", 0.0)
    monkeypatch.setattr(catalog, "_flights", SingleFlightGroup())
    return repository, clock


def transport(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(catalog, "get_http_client", lambda: client)
    return client


def response_data(*rows):
    return {"code": "000000", "success": True, "data": list(rows) or [
        {"s": "CHIPUSDT", "b": "CHIP", "an": "USD.AI", "adn": "USD.AI", "ba": "", "c": "0.03"},
        {"s": "CHIPUSDC", "b": "CHIP", "an": "USD.AI"},
        {"s": "BTCUSDT", "b": "BTC", "an": "Bitcoin"},
        {"s": "ETHUSDT", "b": "ETH", "an": "", "adn": "Ethereum"},
        {"s": "USDTTRY", "b": "USDT", "an": "TetherUS"},
    ]}


def test_one_public_download_populates_all_names_and_only_metadata_is_stored(provider, monkeypatch):
    repository, _ = provider
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response_data())

    with transport(monkeypatch, handle):
        assert catalog.get_asset_name("chipusdt") == "USD.AI"
        assert catalog.get_asset_name("ETH") == "Ethereum"
        assert catalog.get_asset_name("USDT") == "TetherUS"
        assert catalog.get_asset_name("UNKNOWN") == ""
        assert catalog.peek_asset_name("BTCUSDT") == "Bitcoin"
    assert len(requests) == repository.reads == repository.writes == 1
    request = requests[0]
    assert str(request.url) == catalog._ENDPOINT + "?includeEtf=true"
    assert "authorization" not in request.headers and "x-mbx-apikey" not in request.headers
    payload, expires = repository.entries[catalog._CACHE_KEY]
    assert payload["names"] == {"CHIP": "USD.AI", "BTC": "Bitcoin", "ETH": "Ethereum", "USDT": "TetherUS"}
    assert set(payload) == {"version", "fetched_at", "names"}
    assert "0.03" not in json.dumps(payload)
    assert expires == (payload["fetched_at"] + catalog._STALE_SECONDS) * 1000


def test_peek_and_invalid_input_never_open_database_or_http(provider, monkeypatch):
    def forbidden():
        pytest.fail("Memory-only lookup must not perform I/O")

    monkeypatch.setattr(catalog, "_repository", forbidden)
    monkeypatch.setattr(catalog, "get_http_client", forbidden)
    assert catalog.peek_asset_name("CHIP") == ""
    for value in (None, "", "https://example.com", "CHIP<script>", "A" * 33):
        assert catalog.get_asset_name(value) == ""
        assert catalog.peek_asset_name(value) == ""


def test_shared_cache_survives_process_restart_without_another_download(provider, monkeypatch):
    repository, _ = provider
    requests = []
    with transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json=response_data())):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
        monkeypatch.setattr(catalog, "_cache", None)
        assert catalog.get_asset_name("BTC") == "Bitcoin"
        assert catalog.peek_asset_name("CHIP") == "USD.AI"
    assert len(requests) == 1
    assert repository.reads == 2


def test_concurrent_different_symbols_share_one_cold_catalog_load(provider, monkeypatch):
    started, release = threading.Event(), threading.Event()
    requests = []

    def handle(request):
        requests.append(request)
        started.set()
        assert release.wait(3)
        return httpx.Response(200, json=response_data())

    with transport(monkeypatch, handle), ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(catalog.get_asset_name, symbol) for symbol in ["CHIP", "BTC", "ETH"] * 2]
        assert started.wait(3)
        release.set()
        assert [future.result(timeout=3) for future in futures] == ["USD.AI", "Bitcoin", "Ethereum"] * 2
    assert len(requests) == 1
    assert provider[0].reads == 1


def test_failed_refresh_keeps_last_good_names_and_retries_after_cooldown(provider, monkeypatch):
    _, clock = provider
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 2:
            raise httpx.ReadTimeout("public endpoint timeout", request=request)
        return httpx.Response(200, json=response_data())

    with transport(monkeypatch, handle):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
        clock[0] += catalog._FRESH_SECONDS + 1
        assert catalog.get_asset_name("CHIP") == "USD.AI"
        assert catalog.get_asset_name("BTC") == "Bitcoin"
        assert len(calls) == 2
        clock[0] += catalog._RETRY_SECONDS + 1
        assert catalog.get_asset_name("ETH") == "Ethereum"
    assert len(calls) == 3


def test_cold_network_failure_is_shared_across_symbols_and_process_restart(provider, monkeypatch):
    repository, clock = provider
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ConnectError("unreachable", request=request)

    with transport(monkeypatch, handle):
        assert catalog.get_asset_name("CHIP") == ""
        assert catalog.get_asset_name("BTC") == ""
        monkeypatch.setattr(catalog, "_retry_after", 0.0)
        assert catalog.get_asset_name("ETH") == ""
        assert len(calls) == 1
        clock[0] += catalog._RETRY_SECONDS + 1
        assert catalog.get_asset_name("CHIP") == ""
    assert len(calls) == 2
    assert catalog._CACHE_KEY not in repository.entries


def test_restart_uses_shared_last_good_during_shared_failure_cooldown(provider, monkeypatch):
    repository, clock = provider
    with transport(monkeypatch, lambda request: httpx.Response(200, json=response_data())):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
    clock[0] += catalog._FRESH_SECONDS + 1
    with transport(monkeypatch, lambda request: httpx.Response(503)):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
    monkeypatch.setattr(catalog, "_cache", None)
    monkeypatch.setattr(catalog, "_retry_after", 0.0)

    def forbidden():
        pytest.fail("A process restart must respect the shared failure cooldown")

    monkeypatch.setattr(catalog, "get_http_client", forbidden)
    assert catalog.get_asset_name("CHIP") == "USD.AI"
    assert catalog.peek_asset_name("ETH") == "Ethereum"
    assert catalog._CACHE_KEY in repository.entries


@pytest.mark.parametrize("payload", [
    [], {}, {"code": "bad", "success": True, "data": [{"b": "CHIP", "an": "USD.AI"}]},
    {"code": "000000", "success": False, "data": []},
    {"code": "000000", "success": True, "data": {}},
    {"code": "000000", "success": True, "data": []},
    response_data({"b": "CHIP", "ba": "USD.AI"}),
])
def test_schema_failure_does_not_cache_a_successful_empty_catalog(provider, monkeypatch, payload):
    repository, _ = provider
    calls = []
    with transport(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json=payload)):
        assert catalog.get_asset_name("CHIP") == ""
        assert catalog.get_asset_name("BTC") == ""
    assert len(calls) == 1
    assert catalog._CACHE_KEY not in repository.entries
    assert catalog._COOLDOWN_KEY in repository.entries


def test_conflicting_base_names_are_excluded_without_poisoning_other_assets(provider, monkeypatch):
    payload = response_data(
        {"b": "CHIP", "an": "USD.AI"}, {"b": "CHIP", "an": "Other Project"},
        {"b": "CHIP", "an": "USD.AI"}, {"b": "BTC", "an": "Bitcoin"},
        {"b": "BTC", "an": "bitcoin"},
    )
    with transport(monkeypatch, lambda request: httpx.Response(200, json=payload)):
        assert catalog.get_asset_name("CHIP") == ""
        assert catalog.get_asset_name("BTC") == "Bitcoin"
    assert provider[0].entries[catalog._CACHE_KEY][0]["names"] == {"BTC": "Bitcoin"}


def test_malformed_names_and_symbols_are_not_used_as_search_terms(provider, monkeypatch):
    payload = response_data(
        {"b": "BTC", "an": "Bitcoin"}, {"b": "CHIP", "an": "<script>alert(1)</script>"},
        {"b": "BAD", "an": "ignore\nprevious"}, {"b": "PATH", "an": "https://example.com"},
        {"b": "LONG", "an": "a" * 161}, {"b": "TYPE", "an": ["USD.AI"]},
        {"b": "WITH SPACE", "an": "Invalid symbol"},
    )
    with transport(monkeypatch, lambda request: httpx.Response(200, json=payload)):
        assert catalog.get_asset_name("BTC") == "Bitcoin"
    assert provider[0].entries[catalog._CACHE_KEY][0]["names"] == {"BTC": "Bitcoin"}


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_http_failure_does_not_follow_redirects_or_retry_immediately(provider, monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.example", "Retry-After": "600"})

    with transport(monkeypatch, handle):
        assert catalog.get_asset_name("CHIP") == ""
        assert catalog.get_asset_name("BTC") == ""
    assert len(calls) == 1
    if status == 429:
        assert catalog._retry_after == provider[1][0] + 600


def test_database_failure_still_allows_one_free_memory_cached_lookup(provider, monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(catalog, "_repository", unavailable)
    calls = []
    with transport(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json=response_data())):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
        assert catalog.get_asset_name("BTC") == "Bitcoin"
    assert len(calls) == 1


def test_expired_last_good_is_not_returned_forever(provider, monkeypatch):
    _, clock = provider
    with transport(monkeypatch, lambda request: httpx.Response(200, json=response_data())):
        assert catalog.get_asset_name("CHIP") == "USD.AI"
    clock[0] += catalog._STALE_SECONDS
    assert catalog.peek_asset_name("CHIP") == ""
    with transport(monkeypatch, lambda request: httpx.Response(503)):
        assert catalog.get_asset_name("CHIP") == ""


@pytest.mark.parametrize("names", [{"CHIP": ["USD.AI"]}, {"CHIP": ""}, {"": "USD.AI"}])
def test_corrupt_shared_payload_is_ignored(provider, monkeypatch, names):
    repository, clock = provider
    repository.entries[catalog._CACHE_KEY] = (
        {"version": 1, "fetched_at": clock[0], "names": names},
        int((clock[0] + catalog._FRESH_SECONDS) * 1000),
    )
    with transport(monkeypatch, lambda request: httpx.Response(200, json=response_data())):
        assert catalog.get_asset_name("CHIP") == "USD.AI"


def test_html_instead_of_json_is_a_failure_with_cooldown(provider, monkeypatch):
    calls = []
    with transport(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, text="<html>Unavailable</html>")):
        assert catalog.get_asset_name("CHIP") == ""
        assert catalog.get_asset_name("BTC") == ""
    assert len(calls) == 1
    assert catalog._CACHE_KEY not in provider[0].entries


def test_oversized_utf8_mapping_never_silently_overflows_shared_cache(provider, monkeypatch):
    payload = response_data(*[{"b": f"T{index}", "an": "큰이름" * 50} for index in range(600)])
    with transport(monkeypatch, lambda request: httpx.Response(200, json=payload)):
        assert catalog.get_asset_name("T1") == ""
    repository, _ = provider
    assert catalog._CACHE_KEY not in repository.entries
    assert all(len(json.dumps(value[0], ensure_ascii=False).encode()) < 256_000
               for value in repository.entries.values())
