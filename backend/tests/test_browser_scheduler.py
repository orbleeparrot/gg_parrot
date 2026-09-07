"""Browser scheduling regressions without a browser, network or paid API."""
from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from app import news


def page(name, publisher="Decrypt", kind="topic"):
    host = {"Decrypt": "decrypt.co", "CoinDesk": "www.coindesk.com", "CryptoSlate": "cryptoslate.com"}[publisher]
    return {"name": name, "publisher": publisher, "kind": kind,
            "scope": "bittensor" if kind != "section" else "news",
            "url": f"https://{host}/{name}"}


class FakePlaywright:
    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.navigated = []
        self.closed = []
        self.created = 0

    def install(self, monkeypatch):
        from playwright import async_api

        owner = self

        class Locator:
            def __init__(self, current_page):
                self.page = current_page

            @property
            def first(self):
                return self

            async def evaluate_all(self, _script):
                return [{"title": "Bittensor releases a verified network update",
                         "url": f"https://example.invalid/{self.page.name}/article",
                         "published": "2026-09-07T00:00:00Z"}]

            async def count(self):
                return 0

            async def is_visible(self, **_kwargs):
                return False

        class Page:
            name = "unopened"
            url = "about:blank"

            async def goto(self, url, **_kwargs):
                self.url = url
                self.name = url.rsplit("/", 1)[-1]
                owner.navigated.append(self.name)
                behavior = owner.behavior.get(self.name, {})
                if behavior.get("stall_navigation"):
                    await asyncio.Event().wait()
                return SimpleNamespace(status=behavior.get("status", 200),
                                       headers=behavior.get("headers", {}))

            def locator(self, _selector):
                return Locator(self)

            def get_by_role(self, *_args, **_kwargs):
                return Locator(self)

            def get_by_text(self, *_args, **_kwargs):
                return Locator(self)

            async def wait_for_load_state(self, *_args, **_kwargs):
                pass

            async def close(self):
                if owner.behavior.get(self.name, {}).get("stall_close"):
                    await asyncio.Event().wait()
                if owner.behavior.get(self.name, {}).get("fail_close"):
                    raise RuntimeError("Page target already closed")
                owner.closed.append(self.name)

        class Context:
            def set_default_timeout(self, _timeout):
                pass

            async def route(self, *_args):
                pass

            async def new_page(self):
                owner.created += 1
                if owner.behavior.get("stall_first_create") and owner.created == 1:
                    await asyncio.Event().wait()
                return Page()

        class Browser:
            async def new_context(self, **_kwargs):
                return Context()

            async def close(self):
                owner.closed.append("browser")

        class Driver:
            @property
            def chromium(self):
                return self

            async def launch(self, **_kwargs):
                return Browser()

            async def stop(self):
                owner.closed.append("driver")

        class Manager:
            async def start(self):
                return Driver()

        async def complete_pagination(*_args, **_kwargs):
            return {"clicks": 0, "stop_reason": "no_more"}

        async def complete_search(*_args, **_kwargs):
            return {"submitted": True}

        # Site selectors and click behavior have their own helper tests. Keep
        # this file focused on scheduler budgets, state and cache semantics.
        monkeypatch.setitem(sys.modules, "app.coindesk_browser", SimpleNamespace(
            expand_coindesk_page=complete_pagination, search_coindesk_page=complete_search))
        monkeypatch.setattr(async_api, "async_playwright", Manager)
        monkeypatch.setattr(news, "_parse_public_browser_links", lambda raw, _descriptor: raw)
        monkeypatch.setenv("POSITION_NEWS_BROWSER_CONCURRENCY", "1")
        monkeypatch.setattr(news, "_BROWSER_PAGE_CLOSE_SECONDS", 0.01, raising=False)
        return self


def test_asset_pages_have_priority_and_publishers_share_each_tier():
    descriptors = [page("section-a", "CoinDesk", "section"),
                   page("topic-a1", "CoinDesk"), page("topic-a2", "CoinDesk"),
                   page("search-a", "CoinDesk", "asset_search"),
                   page("topic-b", "Decrypt"), page("topic-c", "CryptoSlate"),
                   page("section-b", "Decrypt", "section")]
    ordered = news._prioritize_browser_pages(descriptors)
    assert [item["kind"] for item in ordered] == ["topic"] * 4 + ["asset_search", "section", "section"]
    assert len({item["publisher"] for item in ordered[:3]}) == 3
    assert sorted(item["name"] for item in ordered) == sorted(item["name"] for item in descriptors)
    assert descriptors[0]["name"] == "section-a"  # no mutation of the caller's plan


def test_ticker_plan_is_already_prioritized():
    descriptors = news._browser_news_pages("TAO", "비텐서")
    priorities = {"topic": 0, "asset_search": 1, "section": 2}
    ranks = [priorities[item["kind"]] for item in descriptors]
    assert ranks == sorted(ranks)
    assert ranks[0] == 0


def test_batch_prioritizes_arbitrary_input_before_opening_tabs(monkeypatch):
    browser = FakePlaywright().install(monkeypatch)
    descriptors = [page("global", "CoinDesk", "section"), page("asset", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=0.3)
    assert browser.navigated == ["asset", "global"]
    assert all(results[news._browser_page_key(item)]["status"] == "ready" for item in descriptors)


@pytest.mark.parametrize("cleanup_failure", ["stall_close", "fail_close"])
def test_slow_429_page_cleanup_does_not_starve_another_publisher(monkeypatch, cleanup_failure):
    browser = FakePlaywright({"blocked": {"status": 429, "headers": {"retry-after": "300"},
                                         cleanup_failure: True}}).install(monkeypatch)
    descriptors = [page("blocked", "CoinDesk"), page("also-blocked", "CoinDesk"),
                   page("healthy", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=0.3)
    assert "blocked" in browser.navigated and "healthy" in browser.navigated
    assert "also-blocked" not in browser.navigated
    assert results[news._browser_page_key(descriptors[0])]["error"] == "rate_limited"
    assert results[news._browser_page_key(descriptors[2])]["status"] == "ready"


def test_a_stalled_page_uses_its_own_budget_and_later_publisher_still_runs(monkeypatch):
    browser = FakePlaywright({"stalled": {"stall_navigation": True}}).install(monkeypatch)
    monkeypatch.setattr(news, "_browser_page_budget_seconds", lambda: 0.03)
    descriptors = [page("stalled", "CoinDesk"), page("healthy", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=0.3)
    assert browser.navigated == ["stalled", "healthy"]
    assert results[news._browser_page_key(descriptors[0])]["status"] == "error"
    assert results[news._browser_page_key(descriptors[1])]["status"] == "ready"


def test_page_budget_also_covers_tab_creation(monkeypatch):
    browser = FakePlaywright({"stall_first_create": True}).install(monkeypatch)
    monkeypatch.setattr(news, "_browser_page_budget_seconds", lambda: 0.03)
    descriptors = [page("stalled", "CoinDesk"), page("healthy", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=0.3)
    assert browser.navigated == ["healthy"]
    assert results[news._browser_page_key(descriptors[0])]["phase"] == "page_create"
    assert results[news._browser_page_key(descriptors[1])]["status"] == "ready"


@pytest.mark.parametrize("page_budget, batch_budget, stop_reason", [
    (0.03, 0.3, "page_timeout"), (1.0, 0.03, "batch_timeout"),
])
def test_pagination_timeout_preserves_articles_already_collected(monkeypatch, page_budget, batch_budget, stop_reason):
    browser = FakePlaywright().install(monkeypatch)
    monkeypatch.setattr(news, "_browser_page_budget_seconds", lambda: page_budget)

    async def stalled_pagination(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setitem(sys.modules, "app.coindesk_browser",
                        SimpleNamespace(expand_coindesk_page=stalled_pagination))
    descriptors = [page("asset", "CoinDesk"), page("healthy", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=batch_budget)
    first = results[news._browser_page_key(descriptors[0])]
    assert first["status"] == "partial"
    assert len(first["items"]) == 1
    assert first["pagination"]["stop_reason"] == stop_reason
    assert first["pagination"]["error"] == "TimeoutError"
    if stop_reason == "page_timeout":
        assert browser.navigated == ["asset", "healthy"]
        assert results[news._browser_page_key(descriptors[1])]["status"] == "ready"


def test_unattempted_pages_report_queue_budget_exhaustion(monkeypatch):
    browser = FakePlaywright({"stalled": {"stall_navigation": True}}).install(monkeypatch)
    monkeypatch.setattr(news, "_browser_page_budget_seconds", lambda: 1.0)
    descriptors = [page("stalled", "CoinDesk"), page("queued", "Decrypt")]
    results = news._fetch_browser_page_batch(descriptors, budget_seconds=0.03)
    assert browser.navigated == ["stalled"]
    queued = results[news._browser_page_key(descriptors[1])]
    assert queued["status"] == "error"
    assert queued["error"] == "budget_exhausted"
    assert queued["phase"] == "queue"
    assert queued["attempted"] is False


@pytest.mark.parametrize("cached_error", [
    {"items": [], "status": "error", "error": "budget_exhausted", "phase": "queue", "attempted": False},
    {"items": [], "status": "error", "error": "TimeoutError", "phase": "queue"},
])
@pytest.mark.parametrize("cache_location", ["memory", "durable"])
def test_queued_failure_from_old_cache_does_not_suppress_a_real_attempt(monkeypatch, cached_error, cache_location):
    descriptor = page("asset")
    key = news._browser_page_key(descriptor)
    monkeypatch.setattr(news.time, "time", lambda: 1000.0)
    monkeypatch.setattr(news, "_browser_collection_lock", threading.Lock())
    monkeypatch.setattr(news, "_browser_page_cache", {key: (cached_error, 1300)} if cache_location == "memory" else {})
    monkeypatch.setattr(news, "_load_durable_browser_pages", lambda _keys: {key: cached_error} if cache_location == "durable" else {})
    monkeypatch.setattr(news, "_store_durable_browser_pages", lambda _entries: None)
    attempted = []

    def fetch(descriptors, **_kwargs):
        attempted.extend(descriptors)
        return {key: {"items": [], "status": "empty", "attempted": True}}

    monkeypatch.setattr(news, "_fetch_browser_page_batch", fetch)
    results = news._cached_browser_pages([descriptor])
    assert attempted == [descriptor]
    assert results[key]["status"] == "empty"


def test_new_queue_exhaustion_is_not_written_to_memory_or_durable_cache(monkeypatch):
    descriptor = page("queued")
    key = news._browser_page_key(descriptor)
    monkeypatch.setattr(news, "_browser_collection_lock", threading.Lock())
    monkeypatch.setattr(news, "_browser_page_cache", {})
    monkeypatch.setattr(news, "_load_durable_browser_pages", lambda _keys: {})
    stored = []
    monkeypatch.setattr(news, "_store_durable_browser_pages", lambda entries: stored.append(entries))
    attempted = []

    def fetch(descriptors, **_kwargs):
        attempted.extend(descriptors)
        return {key: {"items": [], "status": "error", "error": "budget_exhausted",
                      "phase": "queue", "attempted": False}}

    monkeypatch.setattr(news, "_fetch_browser_page_batch", fetch)
    for _ in range(2):
        assert news._cached_browser_pages([descriptor])[key]["attempted"] is False
    assert attempted == [descriptor, descriptor]
    assert key not in news._browser_page_cache
    assert all(key not in batch for batch in stored)


@pytest.mark.parametrize("cache_location", ["memory", "durable"])
def test_partial_page_cache_preserves_articles_without_repeating_failed_pagination(monkeypatch, cache_location):
    descriptor = page("asset")
    key = news._browser_page_key(descriptor)
    partial = {"items": [{"title": "Bittensor network upgrade", "url": "https://example.invalid/article"}],
               "status": "partial", "phase": "pagination", "pagination": {"stop_reason": "page_timeout"}}
    monkeypatch.setattr(news.time, "time", lambda: 1000.0)
    monkeypatch.setattr(news, "_browser_collection_lock", threading.Lock())
    monkeypatch.setattr(news, "_browser_page_cache", {key: (partial, 1300)} if cache_location == "memory" else {})
    monkeypatch.setattr(news, "_load_durable_browser_pages", lambda _keys: {key: partial} if cache_location == "durable" else {})
    monkeypatch.setattr(news, "_fetch_browser_page_batch", lambda *_args, **_kwargs: pytest.fail("partial result should be reused"))
    results = news._cached_browser_pages([descriptor])
    assert results[key]["status"] == "partial"
    assert results[key]["items"] == partial["items"]
    assert results[key]["cached"] is True
    assert results[key]["attempted"] is False


@pytest.mark.parametrize("value, expected", [(None, 15), ("1", 5), ("40", 30), ("12", 12)])
def test_browser_page_budget_has_a_bounded_default(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("POSITION_NEWS_BROWSER_PAGE_BUDGET_SECONDS", raising=False)
    else:
        monkeypatch.setenv("POSITION_NEWS_BROWSER_PAGE_BUDGET_SECONDS", value)
    assert news._browser_page_budget_seconds() == expected
