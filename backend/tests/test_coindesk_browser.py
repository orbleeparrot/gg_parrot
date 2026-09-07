"""Offline browser interaction contracts from CoinDesk's public section/tag DOM."""
import asyncio
from types import SimpleNamespace

import pytest

from app.coindesk_browser import expand_coindesk_page, search_coindesk_page


def article(name):
    return f"https://www.coindesk.com/markets/2026/09/01/{name}"


class Control:
    def __init__(self, page, kind, *, present=True):
        self.page, self.kind, self.present = page, kind, present

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    async def count(self):
        return int(self.present)

    async def is_visible(self):
        if self.kind == "next":
            return self.present and self.page.dropdown
        return self.present

    async def is_enabled(self):
        return not self.page.transient_disabled

    async def wait_for(self, **_kwargs):
        if not self.present:
            if self.kind == "render_error":
                await asyncio.sleep(_kwargs.get("timeout", 5_000) / 1_000)
            raise TimeoutError("control absent")

    async def click(self, **kwargs):
        assert not kwargs.get("force"), "Use normal public controls, never force clicks"
        self.page.actions.append(self.kind)
        if self.kind == "preferences":
            self.page.preferences = True
            self.page.dropdown = False
        elif self.kind == "reject":
            self.page.consent = False
            self.page.preferences = False
        elif self.kind == "number":
            self.page.dropdown = True
        elif self.kind in {"more", "next"}:
            if self.kind == "next" and self.page.late_consent:
                self.page.late_consent = False
                self.page.consent = True
                raise TimeoutError("late privacy banner intercepts pointer")
            if self.kind == "next" and not self.page.dropdown:
                raise TimeoutError("page menu closed after rejecting consent")
            if self.page.consent:
                raise TimeoutError("privacy overlay intercepts pointer")
            if self.page.cancel:
                raise asyncio.CancelledError()
            if self.page.failure_status:
                self.page.respond(self.page.failure_status)
                return
            if self.page.server_responds:
                self.page.respond(200)
            if self.page.batches:
                batch = self.page.batches.pop(0)
                self.page.urls = batch if self.kind == "next" else self.page.urls + batch
            if self.kind == "next":
                self.page.number += 1
                self.page.url = f"https://www.coindesk.com/tag/bitcoin/{self.page.number}"
                self.page.dropdown = False

    async def input_value(self):
        return str(self.page.number)

    async def get_attribute(self, name):
        if name == "max":
            return str(self.page.maximum)
        if name == "href":
            return self.page.next_href or f"/tag/bitcoin/{self.page.number + 1}"
        return None

    async def fill(self, term, **_kwargs):
        self.page.term = term
        self.page.actions.append("fill")

    async def press(self, key, **_kwargs):
        assert key == "Enter"
        self.page.actions.append("Enter")
        self.page.respond(self.page.failure_status or 200, search=True)
        if not self.page.failure_status:
            self.page.urls = [article("ethena-pay")]

    async def evaluate_all(self, _script):
        return list(self.page.urls)


class Page:
    def __init__(self, *, kind="section", batches=(), consent=False):
        self.url = "https://www.coindesk.com/markets" if kind == "section" else "https://www.coindesk.com/tag/bitcoin"
        if kind == "search":
            self.url = "https://www.coindesk.com/search"
        self.kind = kind
        self.urls = [article("initial")]
        self.batches = list(batches)
        self.consent, self.preferences = consent, False
        self.dropdown = False
        self.number, self.maximum = 1, 3
        self.actions, self.listeners = [], []
        self.finished_listeners = []
        self.failure_status, self.cancel, self.next_href = None, False, None
        self.term = ""
        self.transient_disabled = False
        self.server_responds = True
        self.render_error = False
        self.search_data = [{"URL": article("ethena-pay")}]
        self.late_consent = False

    def locator(self, selector):
        if selector == "a[href]":
            return Control(self, "articles")
        if selector == "#onetrust-pc-btn-handler":
            return Control(self, "preferences", present=self.consent)
        if selector == "button.ot-pc-refuse-all-handler":
            return Control(self, "reject", present=self.preferences)
        if selector == 'input[aria-label="Page number"]':
            return Control(self, "number", present=self.kind == "tag")
        if selector == '#search-page':
            return Control(self, "search", present=self.kind == "search")
        return Control(self, selector, present=False)

    def get_by_role(self, role, *, name, exact=False):
        if name == "More stories":
            return Control(self, "more", present=self.kind == "section")
        if str(name).startswith("Go to page "):
            return Control(self, "next", present=self.dropdown)
        return Control(self, str(name), present=False)

    def get_by_text(self, text, *, exact=False):
        assert text == "Oops! Something went wrong."
        return Control(self, "render_error", present=self.render_error)

    async def wait_for_function(self, _script, *, arg=None, **_kwargs):
        if arg is not None and isinstance(arg, list) and not set(self.urls).difference(arg):
            raise TimeoutError("no new articles")

    async def wait_for_url(self, predicate, **_kwargs):
        assert predicate(self.url)

    def on(self, event, listener):
        target = self.listeners if event == "response" else self.finished_listeners
        target.append(listener)

    def remove_listener(self, event, listener):
        target = self.listeners if event == "response" else self.finished_listeners
        target.remove(listener)

    def respond(self, status, *, search=False):
        path = "/api/cc-data-proxy/news/v1/search?search_string=" + self.term if search else "/api/v1/articles/section?size=5"
        async def payload():
            return {"Data": self.search_data, "Err": {}}
        response = SimpleNamespace(url="https://www.coindesk.com" + path, status=status,
                                   headers={"retry-after": "120"}, request=SimpleNamespace(resource_type="fetch", url="https://www.coindesk.com" + path),
                                   json=payload)
        for listener in self.listeners:
            listener(response)
        for listener in self.finished_listeners:
            listener(response.request)


def test_section_appends_unique_articles_and_captures_every_page():
    page = Page(batches=[[article("initial"), article("second")], [article("third")]])
    captures = []
    async def capture():
        captures.append(list(page.urls))
    result = asyncio.run(expand_coindesk_page(page, on_page=capture))
    assert result["initial_count"] == 1
    assert result["final_count"] == 3
    assert result["clicks"] == result["pages_loaded"] == len(captures) == 2
    assert result["stop_reason"] == "max_clicks"
    assert not page.listeners


def test_privacy_preferences_are_rejected_before_normal_more_click():
    page = Page(batches=[[article("second")]], consent=True)
    result = asyncio.run(expand_coindesk_page(page, max_clicks=1))
    assert page.actions[:3] == ["preferences", "reject", "more"]
    assert result["final_count"] == 2
    assert "Accept" not in page.actions and "Allow All" not in page.actions


def test_tag_pages_accumulate_unique_urls_and_capture_before_next_navigation():
    page = Page(kind="tag", batches=[[article("second")], [article("third")]])
    captures = []
    async def capture():
        captures.append((page.url, list(page.urls)))
    result = asyncio.run(expand_coindesk_page(page, on_page=capture))
    assert page.actions == ["number", "next", "number", "next"]
    assert result["final_count"] == 3
    assert [urls for _, urls in captures] == [[article("second")], [article("third")]]


def test_tag_stops_at_last_page_without_clicking():
    page = Page(kind="tag")
    page.maximum = 1
    result = asyncio.run(expand_coindesk_page(page))
    assert result["stop_reason"] == "last_page"
    assert result["clicks"] == 0


def test_tag_refuses_unrelated_navigation_link():
    page = Page(kind="tag")
    page.next_href = "https://unrelated.example/login"
    result = asyncio.run(expand_coindesk_page(page))
    assert result["stop_reason"] == "unexpected_pagination_url"
    assert "next" not in page.actions


def test_duplicate_page_stops_without_consuming_entire_click_budget():
    page = Page(batches=[[article("initial")]])
    result = asyncio.run(expand_coindesk_page(page, max_clicks=5))
    assert result["stop_reason"] == "no_new_articles"
    assert result["clicks"] == 1 and result["pages_loaded"] == 0
    assert result["final_count"] == 1


@pytest.mark.parametrize("status", [403, 429, 503])
def test_pagination_http_errors_stop_with_safe_response_metadata(status):
    page = Page()
    page.failure_status = status
    result = asyncio.run(expand_coindesk_page(page))
    assert result["http_status"] == status
    assert result["retry_after"] == "120"
    assert result["response_url"] == "https://www.coindesk.com/api/v1/articles/section"
    assert result["error"] == "http_error"
    assert result["clicks"] == 1 and result["final_count"] == 1


def test_outer_cancellation_propagates_and_removes_response_listener():
    page = Page()
    page.cancel = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(expand_coindesk_page(page))
    assert not page.listeners


def test_zero_click_budget_preserves_initial_count_without_interaction():
    page = Page()
    result = asyncio.run(expand_coindesk_page(page, max_clicks=0))
    assert result["initial_count"] == result["final_count"] == 1
    assert page.actions == []


def test_search_waits_for_requested_term_response_and_replaces_top_news():
    page = Page(kind="search", consent=True)
    result = asyncio.run(search_coindesk_page(page, term="Ethena"))
    assert not result.get("error")
    assert page.actions == ["preferences", "reject", "fill", "Enter"]
    assert page.urls == [article("ethena-pay")]
    assert not page.listeners


def test_search_http_error_is_not_mistaken_for_initial_top_news():
    page = Page(kind="search")
    page.failure_status = 429
    result = asyncio.run(search_coindesk_page(page, term="Ethena"))
    assert result["error"] == "http_error"
    assert result["http_status"] == 429
    assert page.urls == [article("initial")]


def test_temporary_disabled_loading_button_is_allowed_to_become_clickable():
    page = Page(batches=[[article("second")]])
    page.transient_disabled = True
    result = asyncio.run(expand_coindesk_page(page, max_clicks=1))
    assert result["pages_loaded"] == 1


def test_click_timeout_without_server_response_is_an_error():
    page = Page()
    page.server_responds = False
    result = asyncio.run(expand_coindesk_page(page, max_clicks=1))
    assert result["error"] == "TimeoutError"
    assert result["stop_reason"] == "response_timeout"


def test_previous_click_response_does_not_hide_next_click_timeout():
    page = Page(batches=[[article("second")]])
    async def capture():
        page.server_responds = False
    result = asyncio.run(expand_coindesk_page(page, on_page=capture))
    assert result["error"] == "TimeoutError"
    assert result["pages_loaded"] == 1
    assert result["final_count"] == 2
    assert "http_status" not in result


def test_publisher_error_screen_is_not_a_normal_empty_page():
    page = Page(kind="tag")
    page.render_error = True
    result = asyncio.run(expand_coindesk_page(page, max_clicks=1))
    assert result["error"] == "publisher_render_error"
    assert result["clicks"] == 0


def test_search_successful_empty_response_is_reported_without_top_news():
    page = Page(kind="search")
    page.search_data = []
    result = asyncio.run(search_coindesk_page(page, term="zxqnonexistentticker739"))
    assert not result.get("error")
    assert result["empty"] is True and result["item_count"] == 0


def test_rate_limit_metadata_survives_later_successful_response():
    page = Page()
    page.failure_status = 429
    original = page.respond
    def rate_limit_then_success(status, **kwargs):
        original(status, **kwargs)
        original(200, **kwargs)
    page.respond = rate_limit_then_success
    result = asyncio.run(expand_coindesk_page(page))
    assert result["http_status"] == 429
    assert result["retry_after"] == "120"


def test_late_privacy_banner_closes_tag_menu_then_normal_retry_reopens_it():
    page = Page(kind="tag", batches=[[article("second")]])
    page.late_consent = True
    result = asyncio.run(expand_coindesk_page(page, max_clicks=1))
    assert result["pages_loaded"] == 1
    assert result["final_count"] == 2
    assert page.actions == ["number", "next", "preferences", "reject", "number", "next"]
