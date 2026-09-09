"""Best-effort article images for the market headlines (og:image).

Google News RSS hands us redirect links, not publisher URLs. The publisher URL is
recovered the way the news.google.com front-end does it — read the article stub's
signature/timestamp and ask the ``batchexecute`` endpoint for the target — then
the publisher page's ``og:image`` is read. Everything is optional: a missing image
leaves the headline without one, and nothing here blocks the market payload.

Resolution runs on one background thread per process and results live in memory
for a day (the market list changes daily and is at most a handful of items).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import threading
import time
from urllib.parse import quote, urljoin, urlsplit

from .http_runtime import get_http_client

logger = logging.getLogger(__name__)

_FRESH_SECONDS = 24 * 60 * 60
_MAX_ENTRIES = 256
_MAX_HTML_BYTES = 600_000
_MAX_ITEMS_PER_PASS = 12
_TIMEOUT = 8.0
# A desktop browser agent — the Google stub and several publishers serve the
# redirect-less fallback page (or block) for unfamiliar agents.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_GOOGLE_ARTICLE = re.compile(r"^https?://news\.google\.com/(?:rss/)?articles/([^/?#]+)")
_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP = re.compile(r'data-n-a-ts="([^"]+)"')
_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
_IMAGE_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")

_cache: dict[str, dict] = {}
_lock = threading.Lock()
_worker: threading.Thread | None = None
_pending: list[str] = []
# Google 이 자동화로 보고 429/sorry 페이지를 주면 한동안 쉰다 — 더 두드리면 차단만 길어진다.
_GOOGLE_COOLDOWN_SECONDS = 30 * 60
_POLITE_DELAY_SECONDS = 1.5
_google_retry_at = 0.0


class GoogleBlocked(RuntimeError):
    """Google answered with its automated-traffic page (429 / /sorry/)."""


def enabled() -> bool:
    return os.environ.get("NEWS_IMAGES_DISABLED", "").strip().lower() not in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# 순수 함수 — 테스트 대상
# ---------------------------------------------------------------------------
def google_article_id(url: str) -> str:
    match = _GOOGLE_ARTICLE.match(str(url or "").strip())
    return match.group(1) if match else ""


def _http_url(value: str, base: str) -> str:
    value = html_lib.unescape(str(value or "")).strip()
    if not value:
        return ""
    absolute = urljoin(base, value)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return absolute


def extract_og_image(page_html: str, base_url: str) -> str:
    """Return the first usable ``og:image``/``twitter:image`` as an absolute URL."""
    found: dict[str, str] = {}
    for tag in _META.findall(page_html[:_MAX_HTML_BYTES]):
        attrs = {}
        for name, double, single in _ATTR.findall(tag):
            attrs[name.lower()] = double if double else single
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        if key in _IMAGE_KEYS and key not in found:
            found[key] = attrs.get("content", "")
    for key in _IMAGE_KEYS:
        url = _http_url(found.get(key, ""), base_url)
        if url:
            return url
    return ""


def decode_request_body(article_id: str, signature: str, timestamp: str) -> str:
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        article_id, int(timestamp), signature,
    ], separators=(",", ":"))
    return "f.req=" + quote(json.dumps([[["Fbv4je", inner]]], separators=(",", ":")))


def parse_decode_response(text: str) -> str:
    """The batchexecute reply: an anti-XSSI prefix, a blank line, then JSON."""
    parts = str(text or "").split("\n\n", 1)
    if len(parts) < 2:
        return ""
    try:
        envelope = json.loads(parts[1])
        payload = json.loads(envelope[0][2])
        url = payload[1]
    except (ValueError, IndexError, KeyError, TypeError):
        return ""
    return _http_url(url, "") if isinstance(url, str) else ""


# ---------------------------------------------------------------------------
# 네트워크
# ---------------------------------------------------------------------------
def _get(url: str, **kwargs):
    return get_http_client().get(url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT, **kwargs)


def resolve_article_url(url: str) -> str:
    """Publisher URL behind a Google News link; other links pass through."""
    global _google_retry_at
    article_id = google_article_id(url)
    if not article_id:
        return _http_url(url, "")
    if time.time() < _google_retry_at:
        raise GoogleBlocked("cooldown")
    stub = _get(f"https://news.google.com/articles/{article_id}")
    if stub.status_code == 429 or "/sorry/" in str(stub.url):
        _google_retry_at = time.time() + _GOOGLE_COOLDOWN_SECONDS
        raise GoogleBlocked(f"http {stub.status_code}")
    stub.raise_for_status()
    signature = _SIGNATURE.search(stub.text)
    timestamp = _TIMESTAMP.search(stub.text)
    if not (signature and timestamp):
        return ""
    reply = get_http_client().post(
        _BATCH_URL,
        content=decode_request_body(article_id, signature.group(1), timestamp.group(1)),
        headers={"User-Agent": _USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=_TIMEOUT,
    )
    reply.raise_for_status()
    return parse_decode_response(reply.text)


def resolve_image(url: str) -> dict:
    """{"article_url", "image"} — empty strings when anything is missing."""
    article_url = resolve_article_url(url)
    if not article_url:
        return {"article_url": "", "image": ""}
    page = _get(article_url)
    if page.status_code >= 400:
        return {"article_url": article_url, "image": ""}
    return {"article_url": article_url, "image": extract_og_image(page.text, str(page.url))}


# ---------------------------------------------------------------------------
# 캐시 + 백그라운드 워커
# ---------------------------------------------------------------------------
def _fresh(entry: dict | None) -> bool:
    return bool(entry) and entry.get("expires_at", 0) > time.time()


def attach(items: list[dict]) -> str:
    """Set ``image`` on items whose image is known. Returns "ready" or "pending"."""
    if not enabled():
        return "ready"
    pending = 0
    with _lock:
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = _cache.get(str(item.get("url") or ""))
            if _fresh(entry):
                if entry.get("image"):
                    item["image"] = entry["image"]
                if entry.get("article_url"):
                    item["article_url"] = entry["article_url"]
            elif item.get("url"):
                pending += 1
    return "pending" if pending else "ready"


def ensure_resolving(items: list[dict]) -> None:
    """Queue unknown URLs and start the single worker if it is not running."""
    global _worker
    if not enabled():
        return
    with _lock:
        for item in items[:_MAX_ITEMS_PER_PASS]:
            url = str(item.get("url") or "") if isinstance(item, dict) else ""
            if url and not _fresh(_cache.get(url)) and url not in _pending:
                _pending.append(url)
        if not _pending or (_worker and _worker.is_alive()):
            return
        _worker = threading.Thread(target=_drain, name="news-images", daemon=True)
        _worker.start()


def _store(url: str, entry: dict) -> None:
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            oldest = sorted(_cache.items(), key=lambda pair: pair[1].get("expires_at", 0))[: _MAX_ENTRIES // 4]
            for key, _value in oldest:
                _cache.pop(key, None)
        _cache[url] = {**entry, "expires_at": time.time() + _FRESH_SECONDS}


def _drain() -> None:
    while True:
        with _lock:
            if not _pending:
                return
            url = _pending.pop(0)
        try:
            entry = resolve_image(url)
        except GoogleBlocked as exc:
            # 차단 중엔 실패를 캐시하지 않는다 — 쿨다운이 끝나면 다음 읽기에서 다시 시도한다.
            logger.info("news image lookup paused: google %s", exc)
            with _lock:
                _pending.clear()
            return
        except Exception as exc:  # 이미지는 있으면 좋은 것 — 실패는 기록만 하고 넘어간다
            logger.info("news image unresolved: %s", type(exc).__name__)
            entry = {"article_url": "", "image": ""}
        _store(url, entry)
        time.sleep(_POLITE_DELAY_SECONDS)


def resolve_now(items: list[dict]) -> str:
    """Synchronous variant for tests and one-off tools."""
    for item in items:
        url = str(item.get("url") or "") if isinstance(item, dict) else ""
        if url and not _fresh(_cache.get(url)):
            try:
                _store(url, resolve_image(url))
            except GoogleBlocked:
                break
            except Exception:
                _store(url, {"article_url": "", "image": ""})
    return attach(items)


def reset_for_tests() -> None:
    global _worker, _google_retry_at
    with _lock:
        _cache.clear()
        _pending.clear()
        _worker = None
        _google_retry_at = 0.0
