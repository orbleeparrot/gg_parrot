"""Bounded interactions with CoinDesk's public, rendered news controls.

Section pages append five stories; tag pages replace their article list when the
page-number menu navigates. Callers must capture each tag page via ``on_page``.
No endpoint is called directly and publisher restrictions are never bypassed.
"""
from __future__ import annotations

import asyncio
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit


_ARTICLE_URLS = r"""anchors => [...new Set(anchors.map(a => {
  try {
    const u = new URL(a.href);
    return /^(www\.)?coindesk\.com$/.test(u.hostname)
      && /^\/(markets|business|policy|tech|web3|finance)\/\d{4}\/\d{2}\/\d{2}\//.test(u.pathname)
      ? u.origin + u.pathname : null;
  } catch (_) { return null; }
}).filter(Boolean))]"""
_NEW_ARTICLES = r"""previous => Array.from(document.querySelectorAll('a[href]')).some(a => {
  try {
    const u = new URL(a.href);
    return /^(www\.)?coindesk\.com$/.test(u.hostname)
      && /^\/(markets|business|policy|tech|web3|finance)\/\d{4}\/\d{2}\/\d{2}\//.test(u.pathname)
      && !previous.includes(u.origin + u.pathname);
  } catch (_) { return false; }
})"""


async def _article_urls(page) -> set[str]:
    return set(await page.locator("a[href]").evaluate_all(_ARTICLE_URLS))


class _PublisherRenderError(Exception):
    pass


def _retry_deadline(value: str) -> float:
    try:
        return time.time() + max(0, float(value))
    except (ValueError, TypeError):
        try:
            return parsedate_to_datetime(value).timestamp()
        except (ValueError, TypeError, OverflowError):
            return 0


class _Responses:
    """Observe only news responses, keeping no cookies, query strings or bodies."""

    def __init__(self, *, term: str | None = None):
        self.term = term
        self.ready = asyncio.Event()
        self.failed = asyncio.Event()
        self.completed = asyncio.Event()
        self.metadata: dict = {}
        self.failure_metadata: dict = {}
        self.response = None
        self.pending_urls: set[str] = set()

    def reset(self):
        self.ready.clear()
        self.failed.clear()
        self.completed.clear()
        self.metadata = {}
        self.failure_metadata = {}
        self.response = None
        self.pending_urls.clear()

    def request_finished(self, request):
        # This event means the response body completed. Unlike a cancelled
        # Response.finished() coroutine, it creates no Playwright child tasks.
        if request.url in self.pending_urls:
            self.completed.set()

    def __call__(self, response):
        parsed = urlsplit(response.url)
        if parsed.hostname not in {"coindesk.com", "www.coindesk.com"}:
            return
        if self.term is not None:
            if parsed.path != "/api/cc-data-proxy/news/v1/search":
                return
            if parse_qs(parsed.query).get("search_string") != [self.term]:
                return
        elif not (
            parsed.path == "/api/v1/articles/section"
            or re.fullmatch(r"/tag/[^/]+/\d+/?", parsed.path)
        ):
            return
        self.metadata = {
            "http_status": response.status,
            "response_url": urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
            "retry_after": str(response.headers.get("retry-after", ""))[:100],
        }
        self.ready.set()
        self.response = response
        self.pending_urls.add(response.url)
        if response.status >= 400:
            self.failed.set()
            old = self.failure_metadata
            if (not old or (response.status == 429 and old["http_status"] != 429)
                    or (response.status == old["http_status"] == 429
                        and _retry_deadline(self.metadata["retry_after"]) > _retry_deadline(old["retry_after"]))):
                self.failure_metadata = dict(self.metadata)

    def error(self) -> dict:
        return {**self.failure_metadata, "error": "http_error", "stop_reason": "http_error"}


async def _dismiss_overlays(page, timeout_ms: int):
    """Use the publisher's reject/dismiss UI; never accept marketing consent."""
    preferences = page.locator("#onetrust-pc-btn-handler")
    reject = page.locator("button.ot-pc-refuse-all-handler")
    if await preferences.count() and await preferences.first.is_visible():
        await preferences.first.click(timeout=timeout_ms)
        await reject.first.click(timeout=timeout_ms)
    elif await reject.count() and await reject.first.is_visible():
        await reject.first.click(timeout=timeout_ms)
    for name in ("Close advertisement", "Close banner"):
        controls = page.get_by_role("button", name=name, exact=True)
        for index in range(min(3, await controls.count())):
            control = controls.nth(index)
            if await control.is_visible() and await control.is_enabled():
                try:
                    await control.click(timeout=min(timeout_ms, 500))
                except Exception:
                    # Some ads have a countdown; do not wait out an advertisement.
                    pass


async def _click(page, control, timeout_ms: int, *, before_retry=None):
    try:
        await control.click(timeout=min(timeout_ms, 1_000))
    except Exception:
        # Consent may mount after DOMContentLoaded, between discovery and click.
        await _dismiss_overlays(page, timeout_ms)
        if before_retry is not None:
            await before_retry()
        await control.click(timeout=timeout_ms)


async def _wait_for_progress(page, previous: set[str], responses: _Responses, timeout_ms: int):
    """End immediately on publisher errors instead of awaiting a DOM timeout."""
    if responses.failed.is_set():
        return False
    changed = asyncio.create_task(page.wait_for_function(
        _NEW_ARTICLES, arg=list(previous), timeout=timeout_ms,
    ))
    failed = asyncio.create_task(responses.failed.wait())
    render_error = asyncio.create_task(page.get_by_text(
        "Oops! Something went wrong.", exact=True,
    ).wait_for(state="visible", timeout=timeout_ms))
    try:
        done, _ = await asyncio.wait({changed, failed, render_error}, return_when=asyncio.FIRST_COMPLETED)
        if responses.failed.is_set():
            return False
        if render_error in done and render_error.exception() is None:
            raise _PublisherRenderError()
        if changed in done:
            await changed
            return True
        await changed
        return True
    finally:
        for task in (changed, failed, render_error):
            if not task.done():
                task.cancel()
        await asyncio.gather(changed, failed, render_error, return_exceptions=True)


async def expand_coindesk_page(
    page, *, max_clicks: int = 2, timeout_ms: int = 5_000, on_page=None,
) -> dict:
    """Expand a loaded page; counts include unique article URLs across all pages.

    Each click, including overlay dismissal and result capture, has a deadline.
    Ordinary failures return diagnostics and leave already captured articles to
    the caller. Cancellation from the caller's overall budget propagates.
    """
    result = {"initial_count": 0, "final_count": 0, "clicks": 0,
              "pages_loaded": 0, "stop_reason": "no_controls", "phase": "initial"}
    responses = _Responses()
    page.on("response", responses)
    page.on("requestfinished", responses.request_finished)
    try:
        error_screen = page.get_by_text("Oops! Something went wrong.", exact=True)
        if await error_screen.count() and await error_screen.first.is_visible():
            raise _PublisherRenderError()
        seen = await _article_urls(page)
        result["initial_count"] = result["final_count"] = len(seen)
        parsed = urlsplit(page.url)
        if parsed.hostname not in {"coindesk.com", "www.coindesk.com"}:
            result.update(error="unsupported_page", stop_reason="unsupported_page")
            return result
        tag = re.fullmatch(r"/tag/([^/]+)(?:/\d+)?/?", parsed.path)
        section = parsed.path.rstrip("/") in {"/markets", "/policy", "/tech", "/business"}
        if not tag and not section:
            return result
        timeout_ms = max(1, int(timeout_ms))
        for _ in range(max(0, min(10, int(max_clicks)))):
            if responses.failed.is_set():
                result.update(responses.error())
                break
            responses.reset()
            clicked = False
            try:
                async with asyncio.timeout(timeout_ms / 1_000):
                    result["phase"] = "overlay"
                    await _dismiss_overlays(page, timeout_ms)
                    result["phase"] = "control"
                    if tag:
                        number = page.locator('input[aria-label="Page number"]').first
                        await number.wait_for(state="visible", timeout=timeout_ms)
                        current = int(await number.input_value())
                        maximum = int(await number.get_attribute("max") or current)
                        if current >= maximum:
                            result["stop_reason"] = "last_page"
                            break
                        result["phase"] = "menu"
                        await _click(page, number, timeout_ms)
                        control = page.get_by_role("link", name=f"Go to page {current + 1}", exact=True).first
                        await control.wait_for(state="visible", timeout=timeout_ms)
                        target = urlsplit(urljoin(page.url, await control.get_attribute("href") or ""))
                        if (target.hostname != parsed.hostname
                                or target.path.rstrip("/") != f"/tag/{tag.group(1)}/{current + 1}"):
                            result.update(error="unexpected_pagination_url", stop_reason="unexpected_pagination_url")
                            break
                    else:
                        control = page.get_by_role("button", name="More stories", exact=True).first
                        if result["clicks"] and not await control.count():
                            result["stop_reason"] = "no_controls"
                            break
                        await control.wait_for(state="visible", timeout=timeout_ms)
                    result["phase"] = "navigation"
                    async def reopen_tag_menu():
                        # Interacting with a late consent banner closes this
                        # dropdown. Reopen its normal UI before retrying a link.
                        if not await control.is_visible():
                            result["phase"] = "menu"
                            await number.click(timeout=timeout_ms)
                            await control.wait_for(state="visible", timeout=timeout_ms)
                            result["phase"] = "navigation"

                    await _click(page, control, timeout_ms,
                                 before_retry=reopen_tag_menu if tag else None)
                    clicked = True
                    result["clicks"] += 1
                    if tag and not responses.failed.is_set():
                        await page.wait_for_url(
                            lambda url: urlsplit(str(url)).path.rstrip("/") == target.path.rstrip("/"),
                            timeout=timeout_ms,
                        )
                        final_url = urlsplit(page.url)
                        if (final_url.hostname != parsed.hostname or final_url.scheme != parsed.scheme
                                or final_url.path.rstrip("/") != target.path.rstrip("/")):
                            result.update(error="unexpected_pagination_url", stop_reason="unexpected_pagination_url")
                            break
                        result["phase"] = "render"
                        await page.wait_for_function(
                            "expected => document.querySelector('input[aria-label=\"Page number\"]')?.value === String(expected)",
                            arg=current + 1, timeout=timeout_ms,
                        )
                    result["phase"] = "render"
                    if not await _wait_for_progress(page, seen, responses, timeout_ms):
                        result.update(responses.error())
                        break
                    result["phase"] = "capture"
                    current_urls = await _article_urls(page)
                    if not current_urls.difference(seen):
                        result["stop_reason"] = "no_new_articles"
                        break
                    seen.update(current_urls)
                    result["final_count"] = len(seen)
                    result["pages_loaded"] += 1
                    if on_page is not None:
                        await on_page()
                    result["phase"] = "complete"
                    result["stop_reason"] = "max_clicks"
            except TimeoutError:
                if clicked and responses.completed.is_set():
                    error_screen = page.get_by_text("Oops! Something went wrong.", exact=True)
                    if await error_screen.count() and await error_screen.first.is_visible():
                        result.update(error="publisher_render_error", stop_reason="publisher_render_error")
                    else:
                        result["stop_reason"] = "no_new_articles"
                else:
                    result["stop_reason"] = "response_timeout" if clicked else "interaction_timeout"
                    result["error"] = "TimeoutError"
                break
            except Exception as exc:
                kind = "publisher_render_error" if isinstance(exc, _PublisherRenderError) else type(exc).__name__
                result.update(error=kind, stop_reason=kind if isinstance(exc, _PublisherRenderError) else "interaction_error")
                break
        else:
            result["stop_reason"] = "max_clicks" if max_clicks else "disabled"
        if responses.failed.is_set():
            result.update(responses.error())
        elif responses.metadata:
            result.update(responses.metadata)
        return result
    except Exception as exc:
        kind = "publisher_render_error" if isinstance(exc, _PublisherRenderError) else type(exc).__name__
        return {**result, "error": kind, "stop_reason": kind if isinstance(exc, _PublisherRenderError) else "interaction_error"}
    finally:
        page.remove_listener("response", responses)
        page.remove_listener("requestfinished", responses.request_finished)


async def search_coindesk_page(page, *, term: str, timeout_ms: int = 5_000) -> dict:
    """Search through the visible input and wait for this term's news response."""
    term = str(term).strip()[:100]
    if not term:
        return {"error": "empty_search_term"}
    responses = _Responses(term=term)
    page.on("response", responses)
    page.on("requestfinished", responses.request_finished)
    try:
        async with asyncio.timeout(max(1, timeout_ms) / 1_000):
            error_screen = page.get_by_text("Oops! Something went wrong.", exact=True)
            if await error_screen.count() and await error_screen.first.is_visible():
                return {"error": "publisher_render_error", "stop_reason": "publisher_render_error"}
            previous = await _article_urls(page)
            box = page.locator("#search-page").first
            await box.wait_for(state="visible", timeout=timeout_ms)
            await _dismiss_overlays(page, timeout_ms)
            await box.fill(term, timeout=timeout_ms)
            await box.press("Enter", timeout=timeout_ms)
            await responses.ready.wait()
            if responses.failed.is_set():
                return responses.error()
            payload = await responses.response.json()
            if not isinstance(payload, dict) or payload.get("Err") or not isinstance(payload.get("Data"), list):
                return {**responses.metadata, "error": "invalid_search_response"}
            if not payload["Data"]:
                return {**responses.metadata, "empty": True, "item_count": 0}
            if not await _wait_for_progress(page, previous, responses, timeout_ms):
                return responses.error()
            return {**responses.metadata, "item_count": len(await _article_urls(page))}
    except Exception as exc:
        if responses.failed.is_set():
            return responses.error()
        return {**responses.metadata, "error": type(exc).__name__, "stop_reason": "search_timeout"}
    finally:
        page.remove_listener("response", responses)
        page.remove_listener("requestfinished", responses.request_finished)
