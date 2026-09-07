"""CoinDesk credential, quota, caching and response handling without live API calls."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from app import coindesk_api as api
from app.http_runtime import SingleFlightGroup


class Repository:
    def __init__(self):
        self.entries = {}
        self.reads = 0
        self.budgets = []
        self.allowed = True

    def load_browser_pages(self, keys):
        self.reads += 1
        return {key: deepcopy(self.entries[key][0]) for key in keys
                if key in self.entries and self.entries[key][1] > time.time() * 1000}

    def store_browser_pages(self, entries):
        self.entries.update(deepcopy(entries))

    def reserve_news_api_budget(self, **limits):
        self.budgets.append(limits)
        return self.allowed


@pytest.fixture
def provider(monkeypatch):
    repository = Repository()
    monkeypatch.setattr(api, "_repository", lambda: repository)
    monkeypatch.setattr(api, "_cache", {})
    monkeypatch.setattr(api, "_flights", SingleFlightGroup())
    monkeypatch.setenv("COINDESK_API_KEY", "secret-test-credential")
    monkeypatch.setenv("COINDESK_NEWS_API_ENABLED", "true")
    for name in ("COINDESK_NEWS_MAX_CALLS_PER_DAY", "COINDESK_NEWS_MAX_TOTAL_CALLS", "COINDESK_NEWS_CACHE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    return repository


def transport(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(api, "get_http_client", lambda: client)
    return client


def article(**overrides):
    return {
        "TITLE": "Ethereum &amp; Bitcoin update", "URL": "https://www.coindesk.com/markets/2026/09/07/news#top",
        "PUBLISHED_ON": 1788753600, "SUBTITLE": "<p>Public publisher subtitle.</p>",
        "BODY": "PRIVATE FULL ARTICLE BODY MUST NEVER BE PERSISTED",
        "CATEGORY_DATA": [{"NAME": "ETH"}, {"NAME": "BTC"}], "KEYWORDS": "ethereum, BTC",
        **overrides,
    }


@pytest.mark.parametrize("enabled,key,status", [
    ("true", "", "unconfigured"), ("false", "secret-test-credential", "disabled"),
    ("false", "", "disabled"),
])
def test_missing_key_or_disabled_never_opens_http_or_db(monkeypatch, enabled, key, status):
    monkeypatch.setenv("COINDESK_NEWS_API_ENABLED", enabled)
    monkeypatch.setenv("COINDESK_API_KEY", key)

    def forbidden():
        pytest.fail("Unconfigured provider must not access HTTP or DB")

    monkeypatch.setattr(api, "get_http_client", forbidden)
    monkeypatch.setattr(api, "_repository", forbidden)
    result = api.fetch_news("Ethena")
    assert result["source"]["status"] == status
    assert result["source"]["attempted"] is False
    assert api.configuration()["enabled"] is False


def test_configuration_is_safe_and_invalid_values_have_defaults(provider, monkeypatch):
    monkeypatch.setenv("COINDESK_NEWS_MAX_CALLS_PER_DAY", "invalid")
    monkeypatch.setenv("COINDESK_NEWS_MAX_TOTAL_CALLS", "-1")
    monkeypatch.setenv("COINDESK_NEWS_CACHE_SECONDS", "1")
    assert api.configuration() == {
        "enabled": True, "max_calls_per_day": 20, "max_total_calls": 0, "cache_seconds": 60,
    }
    assert "secret-test-credential" not in json.dumps(api.configuration())


def test_search_uses_fixed_endpoint_header_and_normalized_bounded_metadata(provider, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"Data": [article(), article()], "Err": {}})

    with transport(monkeypatch, handle):
        result = api.fetch_news("  Ethereum  ecosystem ")
    request = requests[0]
    assert str(request.url).startswith("https://data-api.coindesk.com/news/v1/search?")
    assert dict(request.url.params) == {
        "source_key": "coindesk", "search_string": "Ethereum ecosystem", "limit": "100", "lang": "EN",
    }
    assert request.headers["Authorization"] == "Apikey secret-test-credential"
    assert "secret-test-credential" not in str(request.url)
    assert result["source"]["status"] == "ready"
    assert result["source"]["fetched_count"] == 2
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["title"] == "Ethereum & Bitcoin update"
    assert item["url"].endswith("/news")
    assert item["published"].endswith("+00:00")
    assert item["published_display"] == "09.07 13:00"
    assert item["categories"] == ["ETH", "BTC", "ethereum"]
    assert item["excerpt"] == "Public publisher subtitle."
    stored = json.dumps(provider.entries)
    assert "PRIVATE FULL ARTICLE BODY" not in stored
    assert "secret-test-credential" not in stored
    assert provider.budgets == [{"daily_limit": 20, "total_limit": 100}]


def test_redirect_never_forwards_credential(provider, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/steal"})

    with transport(monkeypatch, handle):
        result = api.fetch_news("Bitcoin")
    assert len(requests) == 1
    assert result["source"]["http_status"] == 302
    assert result["source"]["status"] == "error"


def test_invalid_article_links_dates_and_deleted_records_are_discarded(provider, monkeypatch):
    rows = [article(URL="javascript:alert(1)"), article(URL="https://user:pass@www.coindesk.com/news"),
            article(PUBLISHED_ON="bad"), article(PUBLISHED_ON=True), article(PUBLISHED_ON=10**50),
            article(STATUS="DELETED"), article(TITLE={"not": "text"}),
            article(URL="https://www.coindesk.com/valid", CATEGORY_DATA={"wrong": "shape"}, SUBTITLE="x" * 2500)]
    with transport(monkeypatch, lambda _request: httpx.Response(200, json={"Data": rows, "Err": {}})):
        result = api.fetch_news("Ethereum")
    assert len(result["items"]) == 1
    assert len(result["items"][0]["excerpt"]) == 1800


def test_empty_results_are_successful_and_cached_across_memory_restart(provider, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"Data": [], "Err": {}})

    with transport(monkeypatch, handle):
        first = api.fetch_news("Ethena")
        second = api.fetch_news("ethena")
        monkeypatch.setattr(api, "_cache", {})
        third = api.fetch_news("Ethena")
    assert first["source"]["status"] == "empty"
    assert first["source"]["cached"] is False
    assert second["source"]["cached"] is third["source"]["cached"] is True
    assert third["source"]["attempted"] is False
    assert len(requests) == len(provider.budgets) == 1


@pytest.mark.parametrize("payload,error", [
    ({"Data": {}, "Err": {}}, "invalid_api_response"),
    ({"Data": [], "Err": {"message": "secret-test-credential"}}, "invalid_api_response"),
    ([{"Data": []}], "invalid_api_response"),
    ({"Data": [{"wrong": "shape"}], "Err": {}}, "invalid_articles"),
])
def test_malformed_api_payload_is_not_reported_as_empty(provider, monkeypatch, payload, error):
    with transport(monkeypatch, lambda _request: httpx.Response(200, json=payload)):
        result = api.fetch_news("Ethereum")
    assert result["source"]["status"] == "error"
    assert result["source"]["error"] == error
    assert "secret-test-credential" not in json.dumps(result)
    assert "secret-test-credential" not in json.dumps(provider.entries)


def test_non_json_200_is_an_error(provider, monkeypatch):
    with transport(monkeypatch, lambda _request: httpx.Response(200, text="<html>bad gateway</html>")):
        result = api.fetch_news("Ethereum")
    assert result["source"]["error"] == "invalid_api_response"
    assert result["source"]["http_status"] == 200


@pytest.mark.parametrize("status,error", [(401, "authentication_required"), (403, "access_denied"), (429, "rate_limited")])
def test_account_failure_stops_other_search_terms_and_survives_restart(provider, monkeypatch, status, error):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Retry-After": "7200"},
                              json={"Err": {"message": "secret-test-credential"}})

    with transport(monkeypatch, handle):
        first = api.fetch_news("Ethereum")
        monkeypatch.setattr(api, "_cache", {})
        second = api.fetch_news("Bitcoin")
    assert first["source"]["error"] == error
    assert first["source"]["retry_at"] >= time.time() + 7100
    assert second["source"]["query"] == "Bitcoin"
    assert second["source"]["cached"] is True
    assert second["source"]["attempted"] is False
    assert len(requests) == len(provider.budgets) == 1
    assert "secret-test-credential" not in json.dumps(provider.entries)


def test_retry_after_http_date_is_respected(provider, monkeypatch):
    retry_date = datetime.fromtimestamp(time.time() + 5400, timezone.utc)
    with transport(monkeypatch, lambda _request: httpx.Response(
            429, headers={"Retry-After": format_datetime(retry_date, usegmt=True)})):
        result = api.fetch_news("Bitcoin")
    assert result["source"]["retry_at"] >= time.time() + 5300


def test_credential_rotation_does_not_reuse_previous_authentication_failure(provider, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return (httpx.Response(401) if len(requests) == 1 else
                httpx.Response(200, json={"Data": [], "Err": {}}))

    with transport(monkeypatch, handle):
        api.fetch_news("Bitcoin")
        monkeypatch.setenv("COINDESK_API_KEY", "replacement-credential")
        result = api.fetch_news("Bitcoin")
    assert len(requests) == 2
    assert result["source"]["status"] == "empty"
    assert "replacement-credential" not in json.dumps(provider.entries)


@pytest.mark.parametrize("unavailable", [False, True])
def test_exhausted_or_unavailable_durable_budget_prevents_outbound_request(provider, monkeypatch, unavailable):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    if unavailable:
        def reserve(**_limits):
            raise RuntimeError("Database unavailable; secret-test-credential")
        monkeypatch.setattr(provider, "reserve_news_api_budget", reserve)
    else:
        provider.allowed = False
    monkeypatch.setattr(api, "get_http_client", lambda: pytest.fail("Quota must fail closed"))
    result = api.fetch_news("Bitcoin")
    assert result["source"]["error"] == ("budget_unavailable" if unavailable else "call_budget_exhausted")
    assert result["source"]["attempted"] is False
    assert "secret-test-credential" not in json.dumps(result)


def test_timeout_has_no_automatic_retry_and_is_cached(provider, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("secret-test-credential", request=request)

    with transport(monkeypatch, handle):
        first = api.fetch_news("Ethereum")
        second = api.fetch_news("Ethereum")
    assert first["source"]["error"] == "request_timeout"
    assert second["source"]["cached"] is True
    assert len(requests) == 1
    assert "secret-test-credential" not in json.dumps(first)


def test_concurrent_same_query_uses_one_request_and_budget(provider, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    requests = []

    def handle(request):
        requests.append(request)
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, json={"Data": [article()], "Err": {}})

    with transport(monkeypatch, handle), ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(api.fetch_news, "Ethereum")
        assert entered.wait(1)
        second = pool.submit(api.fetch_news, "Ethereum")
        release.set()
        results = [first.result(timeout=2), second.result(timeout=2)]
    assert len(requests) == len(provider.budgets) == 1
    assert sorted(result["source"]["attempted"] for result in results) == [False, True]
    results[0]["items"][0]["title"] = "Mutated caller copy"
    assert api.fetch_news("Ethereum")["items"][0]["title"] != "Mutated caller copy"


def test_cache_write_failure_preserves_usable_news_and_memory_cache(provider, monkeypatch):
    def fail_write(_entries):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(provider, "store_browser_pages", fail_write)
    with transport(monkeypatch, lambda _request: httpx.Response(200, json={"Data": [article()], "Err": {}})):
        first = api.fetch_news("Ethereum")
        second = api.fetch_news("Ethereum")
    assert first["source"]["status"] == "ready"
    assert first["source"]["cache_persisted"] is False
    assert second["source"]["cached"] is True
    assert len(provider.budgets) == 1


@pytest.mark.parametrize("large_metadata", [False, True])
def test_large_unicode_response_fits_real_db_and_avoids_repeat_call_after_restart(
        provider, monkeypatch, tmp_path, large_metadata):
    from sqlmodel import Session, create_engine, select

    from app.agent_features.position_news import repository
    from app.db import BrowserNewsPageCache

    engine = create_engine(f"sqlite:///{tmp_path / 'coindesk-cache.db'}")
    BrowserNewsPageCache.__table__.create(engine)

    def read(keys):
        with Session(engine) as db:
            return repository.load_browser_pages(keys, db=db)

    def write(entries):
        with Session(engine) as db:
            repository.store_browser_pages(entries, db=db)

    monkeypatch.setattr(provider, "load_browser_pages", read)
    monkeypatch.setattr(provider, "store_browser_pages", write)
    rows = [article(
        TITLE=f"{index}: " + "긴기사제목" * 250, SUBTITLE="긴기사발췌" * 500,
        URL=f"https://www.coindesk.com/markets/{index}/" + ("경로" * 500 if large_metadata else "news"),
        CATEGORY_DATA=([{"NAME": f"{category}: " + "가" * 95} for category in range(50)]
                       if large_metadata else [{"NAME": "ETH"}]),
    ) for index in range(100)]
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"Data": rows, "Err": {}})

    with transport(monkeypatch, handle):
        first = api.fetch_news("Ethereum")
        with Session(engine) as db:
            stored = db.exec(select(BrowserNewsPageCache)).all()
            assert len(stored) == 1, "The repository must not silently discard oversized metadata"
            assert len(stored[0].payload_json.encode("utf-8")) <= 240_000
            assert json.loads(stored[0].payload_json)["result"]["items"] == first["items"]
        monkeypatch.setattr(api, "_cache", {})
        restarted = api.fetch_news("Ethereum")

    assert len(requests) == len(provider.budgets) == 1
    assert restarted["items"] == first["items"]
    assert restarted["source"]["cached"] is True
    assert restarted["source"]["attempted"] is False
    assert first["source"]["metadata_trimmed"] is True
    assert first["source"]["fetched_count"] == 100
    if large_metadata:
        assert 0 < len(first["items"]) < 100
        assert first["source"]["truncated_count"] == 100 - len(first["items"])
        assert restarted["source"]["truncated_count"] == first["source"]["truncated_count"]
    else:
        assert len(first["items"]) == 100, "Trim supplementary metadata before dropping articles"
        assert first["source"].get("truncated_count", 0) == 0
