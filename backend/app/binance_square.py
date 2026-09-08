"""Public community posts from Square's Latest tab and public article detail.

This is a website endpoint, not Binance's authenticated trading API. No account,
cookies, or private chat data are used. Bounded plain bodies stay internal for
summarization; public response serialization must remove them.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from .http_runtime import SingleFlightGroup, get_http_client

_ENDPOINT = "https://www.binance.com/bapi/composite/v4/friendly/pgc/content/queryByHashtag"
_DETAIL_ENDPOINT = "https://www.binance.com/bapi/composite/v3/friendly/pgc/special/content/detail/"
_PREFIX = "binance-square-latest-v2:"
# An upgrade must not discard an upstream's existing access/rate-limit cooldown.
_COOLDOWN = "binance-square-latest-v1:cooldown"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_BODY_CHARACTERS = 20_000
_MAX_BODY_JSON_BYTES = 20_000
_cache: dict[str, dict] = {}
_lock = threading.Lock()
_flights = SingleFlightGroup()


def _integer(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def configuration() -> dict:
    return {"enabled": os.environ.get("BINANCE_SQUARE_ENABLED", "true").lower() not in {"false", "0", "no", "off"},
            "cache_seconds": _integer("BINANCE_SQUARE_CACHE_SECONDS", 300, 60, 3600),
            "max_items": _integer("BINANCE_SQUARE_MAX_ITEMS", 5, 1, 10),
            "max_age_days": _integer("BINANCE_SQUARE_MAX_AGE_DAYS", 7, 1, 30),
            "page_limit": 2, "request_budget_seconds": 6}


def _repository():
    from .agent_features.position_news import repository
    return repository


def _symbol(value: str) -> str:
    value = str(value or "").strip().upper()
    return value if re.fullmatch(r"[A-Z0-9]{1,30}", value) else ""


def _page(symbol):
    return f"https://www.binance.com/en/square/hashtag/{symbol.lower()}"


def _result(symbol, status, **source):
    return {"items": [], "source": {"name": "binance_square", "publisher": "Binance Square",
        "source_type": "binance_square_public_json", "source_page": _page(symbol),
        "content_type": "community", "order": "LATEST", "status": status,
        "attempted": False, "cached": False, "fetched_count": 0, "item_count": 0,
        "elapsed_ms": 0, "pages_fetched": 0, **source}}


def _plain(value):
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.I | re.S)
    text = re.sub(r"<(?:br\b[^>]*|/p|/div)>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    text = re.sub(r"\{(?:future|spot)\}\([^)]*\)", " ", text)
    return text.strip()


def _body_fields(value, *, status=None):
    text = _plain(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    original_length = len(text)
    text = text[:_MAX_BODY_CHARACTERS]
    # Ten posts plus metadata must fit BrowserNewsPageCache's 256 KB row limit,
    # including non-ASCII characters and JSON-escaped line breaks/quotes.
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > _MAX_BODY_JSON_BYTES:
        lower, upper = 0, len(text)
        while lower < upper:
            middle = (lower + upper + 1) // 2
            if len(json.dumps(text[:middle], ensure_ascii=False).encode("utf-8")) <= _MAX_BODY_JSON_BYTES:
                lower = middle
            else:
                upper = middle - 1
        text = text[:lower].rstrip()
    return {"community_body": text,
            "community_body_hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
            "community_body_truncated": len(text) < original_length or not text,
            "community_body_status": status or ("ready" if text else "missing")}


def _substantive_lines(text):
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        # A footer can be on the same line as an unrelated post, too.
        line = re.sub(r"(?:\s+[#$@][\w.]+)+\s*$", "", line).strip()
        # Tag-only footers and ticker widgets do not establish relevance.
        without_tags = re.sub(r"(?<!\w)[#$@][\w.]+", "", line)
        without_links = re.sub(r"https?://\S+", "", without_tags)
        if sum(ch.isalpha() for ch in without_links) >= 6:
            yield line


def _headline(raw, content):
    title = _plain(raw.get("title"))
    lines = list(_substantive_lines(title or content))
    if not lines:
        return ""
    text = lines[0]
    if len(text) <= 240:
        return text
    shortened = text[:239]
    if " " in shortened[-40:]:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip() + "…"


def is_community_item(item: dict) -> bool:
    """Validate the typed boundary before exempting posts from article filters."""
    if item.get("content_type") != "community" or item.get("source") != "Binance Square":
        return False
    identifier = str(item.get("community_post_id") or "")
    if not re.fullmatch(r"\d{1,30}", identifier):
        return False
    try:
        parsed = urlsplit(str(item.get("url") or ""))
        return (parsed.scheme == "https" and parsed.netloc == "www.binance.com"
                and parsed.path == f"/en/square/post/{identifier}"
                and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def within_window(item: dict) -> bool:
    try:
        stamp = datetime.fromisoformat(str(item.get("published") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return False
        return time.time() - configuration()["max_age_days"] * 86400 <= stamp.timestamp() <= time.time() + 300
    except (ValueError, TypeError, OverflowError):
        return False


def parse_posts(rows: list, symbol: str) -> list[dict]:
    asset = _symbol(symbol)
    if not asset:
        return []
    candidates = []
    for raw in rows[:40]:
        if not isinstance(raw, dict) or raw.get("contentStatus") != 2:
            continue
        identifier = str(raw.get("id") or "")
        if not re.fullmatch(r"\d{1,30}", identifier):
            continue
        stamp = raw.get("date")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            continue
        try:
            published = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            parsed = urlsplit(str(raw.get("webLink") or ""))
        except (ValueError, OverflowError, OSError):
            continue
        if (parsed.scheme != "https" or parsed.netloc != "www.binance.com"
                or not re.fullmatch(rf"/[a-z]{{2}}(?:-[A-Za-z]{{2,4}})?/square/post/{identifier}", parsed.path)):
            continue
        content = _plain(raw.get("content"))[:_MAX_BODY_CHARACTERS]
        title = _headline(raw, content)
        meaningful = "\n".join(_substantive_lines(_plain(raw.get("title")) + "\n" + content))
        if not title or not re.search(rf"(?<![A-Za-z0-9]){re.escape(asset)}(?:USDT|USDC)?(?![A-Za-z0-9])", meaningful, re.I):
            continue
        # Private-group advertisements are not a community discussion item.
        if re.search(r"\b(?:join|dm|contact)\b.{0,60}\b(?:vip|telegram|whatsapp|premium\s+group)\b", content, re.I):
            continue
        item = {"title": title, "source": "Binance Square", "content_type": "community",
                "community_post_id": identifier, "author": re.sub(r"\s+", " ", _plain(raw.get("authorName")))[:100],
                "url": f"https://www.binance.com/en/square/post/{identifier}", "published": published,
                "published_display": datetime.fromtimestamp(stamp, timezone(timedelta(hours=9))).strftime("%Y.%m.%d %H:%M"),
                "feed_source": "binance_square_public_json", "source_page": _page(asset),
                "categories": [asset], "language": str(raw.get("detectedLanguage") or "")[:20],
                # Short-post content equals detail.bodyTextOnly. Article content
                # may be absent or a preview; only its detail is a trusted body.
                **_body_fields(raw.get("content") if raw.get("contentType") == 1 else "")}
        if within_window(item):
            candidates.append(item)
    selected, ids, titles, authors = [], set(), set(), Counter()
    for item in sorted(candidates, key=lambda i: i["published"], reverse=True):
        title = item["title"].casefold()
        author = item["author"].casefold()
        if item["community_post_id"] in ids or title in titles or (author and authors[author] >= 2):
            continue
        ids.add(item["community_post_id"])
        titles.add(title)
        authors[author] += 1
        selected.append(item)
        if len(selected) >= configuration()["max_items"]:
            break
    return selected


def _load(keys):
    with _lock:
        found = {key: deepcopy(_cache[key]) for key in keys if key in _cache}
    missing = [key for key in keys if found.get(key, {}).get("expires_at", 0) <= time.time()]
    if missing:
        try:
            found.update(_repository().load_browser_pages(missing))
        except Exception:
            pass
    return {key: value for key, value in found.items() if isinstance(value, dict)}


def _store(key, result, ttl):
    payload = {"expires_at": time.time() + ttl, "result": deepcopy(result)}
    with _lock:
        _cache[key] = payload
        while len(_cache) > 512:
            _cache.pop(next(iter(_cache)))
    try:
        # Keep last-good posts available after the refresh deadline for outages.
        _repository().store_browser_pages({key: (payload, int((time.time() + max(ttl, 86400)) * 1000))})
    except Exception:
        result["source"]["cache_persisted"] = False


def _retry_seconds(response):
    value = response.headers.get("retry-after", "")
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = 300
    return max(300, min(86400, math.ceil(delay))) if math.isfinite(delay) else 300


def _read_json(url, deadline, *, params=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("request_budget_exhausted")
    with get_http_client().stream("GET", url, params=params,
            headers={"Accept": "application/json"}, timeout=min(3.0, remaining), follow_redirects=False) as response:
        if response.status_code != 200:
            return response, None
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                raise ValueError("response_too_large")
            if time.monotonic() >= deadline:
                raise TimeoutError("request_budget_exhausted")
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
    if (not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != "000000"
            or not isinstance(payload.get("data"), dict)):
        raise ValueError("invalid_response")
    return response, payload["data"]


def _read_page(symbol, index, deadline):
    response, data = _read_json(_ENDPOINT, deadline, params={
        "hashtag": f"#{symbol.lower()}", "pageIndex": index, "pageSize": 20, "orderBy": "LATEST"})
    if data is None:
        return response, None
    if not isinstance(data.get("feedData"), list):
        raise ValueError("invalid_response")
    return response, data["feedData"]


def _fetch_body(identifier, symbol, deadline):
    key = _PREFIX + "body:" + identifier

    def load():
        saved = _load([key, _COOLDOWN])
        hit = saved.get(key) or {}
        previous = hit.get("result") or {}
        if hit.get("expires_at", 0) > time.time() and isinstance(previous.get("body"), dict):
            result = deepcopy(previous)
            result["source"].update(cached=True, attempted=False)
            return result
        cooldown = saved.get(_COOLDOWN) or {}
        if cooldown.get("expires_at", 0) > time.time():
            if previous.get("body", {}).get("community_body_status") == "ready":
                return {**deepcopy(previous), "source": {
                    "attempted": False, "error": "detail_cooldown", "stale": True}}
            return {"body": _body_fields("", status="error"), "source": {
                "attempted": False, "error": "detail_cooldown"}}
        result = {"body": _body_fields("", status="error"), "source": {"attempted": False},
                  "last_attempt_ms": previous.get("last_attempt_ms", 0)}
        ttl = 60
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("request_budget_exhausted")
            result["source"]["attempted"] = True
            result["last_attempt_ms"] = int(time.time() * 1000)
            response, data = _read_json(_DETAIL_ENDPOINT + identifier, deadline)
            result["source"]["http_status"] = response.status_code
            if response.status_code != 200:
                result["source"]["error"] = "detail_http_error"
                if response.status_code in {401, 403, 429}:
                    ttl = _retry_seconds(response)
                    reason = {401: "authentication_required", 403: "access_denied", 429: "rate_limited"}[response.status_code]
                    _store(_COOLDOWN, _result(symbol, "error", error=reason,
                        http_status=response.status_code, retry_at=time.time() + ttl), ttl)
            elif (str(data.get("id")) != identifier or data.get("contentStatus") != 2
                    or data.get("contentType") not in {1, 2}):
                raise ValueError("invalid_detail")
            else:
                result["body"] = _body_fields(data.get("bodyTextOnly"))
                if result["body"]["community_body_status"] == "ready":
                    ttl = configuration()["cache_seconds"]
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result["source"]["error"] = ("detail_timeout" if isinstance(exc, (httpx.TimeoutException, TimeoutError))
                                          else "invalid_detail" if isinstance(exc, ValueError) else "detail_failed")
        if result["body"]["community_body_status"] != "ready" and previous.get("body", {}).get("community_body_status") == "ready":
            result["body"] = deepcopy(previous["body"])
            result["source"]["stale"] = True
        _store(key, result, ttl)
        return result

    # A different ticker may already be loading this body. Do not wait on its
    # independent deadline and overrun this collection's six-second budget.
    pending = {"body": _body_fields("", status="error"),
               "source": {"attempted": False, "error": "detail_in_flight"}}
    result, state = _flights.run(key, load, stale_value=pending)
    result = deepcopy(result)
    if state != "loaded":
        result["source"].update(cached=True, attempted=False)
    return result


def _fill_bodies(result, symbol, deadline):
    items = result["items"]
    # Refresh the least recently attempted detail first, including expired
    # successes. Otherwise a six-second budget keeps refreshing the first two
    # articles forever while edited bodies later in the list stay stale.
    missing = [item for item in items if item.get("community_body_status") != "ready"]
    keys = [_PREFIX + "body:" + item["community_post_id"] for item in missing]
    saved = _load(keys) if keys else {}
    missing.sort(key=lambda item: saved.get(_PREFIX + "body:" + item["community_post_id"], {})
                 .get("result", {}).get("last_attempt_ms", 0))
    for item in missing:
        body = _fetch_body(item["community_post_id"], symbol, deadline)
        item.update(body["body"])
        if body["source"].get("attempted"):
            result["source"]["details_fetched"] = result["source"].get("details_fetched", 0) + 1
        if body["source"].get("error"):
            result["source"].setdefault("detail_error", body["source"]["error"])
        if body["source"].get("stale"):
            result["source"]["stale"] = True
        if body["source"].get("http_status"):
            result["source"]["detail_http_status"] = body["source"]["http_status"]
    result["source"]["body_pending_count"] = sum(item.get("community_body_status") != "ready" for item in items)
    result["source"]["body_truncated_count"] = sum(bool(item.get("community_body_truncated")) for item in items)
    if result["source"]["body_pending_count"] and result["source"]["status"] == "ready":
        result["source"]["status"] = "partial"


def fetch_posts(symbol: str) -> dict:
    asset = _symbol(symbol)
    if not configuration()["enabled"]:
        return _result(asset, "disabled")
    if not asset:
        return _result(asset, "error", error="invalid_symbol")
    key = _PREFIX + asset

    def load():
        saved = _load([key, _COOLDOWN])
        previous = saved.get(key, {}).get("result") or {}
        for cache_key in (key, _COOLDOWN):
            hit = saved.get(cache_key) or {}
            if hit.get("expires_at", 0) > time.time() and isinstance(hit.get("result"), dict):
                result = deepcopy(hit["result"])
                result["source"].update(source_page=_page(asset), cached=True, attempted=False, elapsed_ms=0)
                if cache_key == _COOLDOWN:
                    result["items"] = [i for i in previous.get("items", []) if within_window(i)]
                    if result["items"]:
                        result["source"].update(status="partial", stale=True)
                result["source"]["item_count"] = len(result["items"])
                return result
        started = time.monotonic()
        deadline = started + configuration()["request_budget_seconds"]
        result, rows = _result(asset, "empty", attempted=True), []
        ttl = configuration()["cache_seconds"]
        for index in range(1, configuration()["page_limit"] + 1):
            try:
                response, batch = _read_page(asset, index, deadline)
                result["source"]["http_status"] = response.status_code
                if response.status_code != 200:
                    error = {401: "authentication_required", 403: "access_denied", 429: "rate_limited"}.get(response.status_code, "upstream_http_error")
                    result["source"].update(status="error", error=error)
                    ttl = _retry_seconds(response) if response.status_code in {401, 403, 429} else 60
                    if response.status_code in {401, 403, 429}:
                        result["source"]["retry_at"] = time.time() + ttl
                        _store(_COOLDOWN, result, ttl)
                    break
                rows.extend(batch[:20])
                result["source"]["pages_fetched"] += 1
                result["items"] = parse_posts(rows, asset)
                if len(result["items"]) >= configuration()["max_items"] or len(batch) < 20:
                    break
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                result["source"].update(status="error", error=("request_timeout" if isinstance(exc, (httpx.TimeoutException, TimeoutError)) else "invalid_response" if isinstance(exc, ValueError) else "request_failed"))
                ttl = 60
                break
        if result["source"]["status"] == "error":
            if not result["items"]:
                result["items"] = [i for i in previous.get("items", []) if within_window(i)]
                if result["items"]:
                    result["source"]["stale"] = True
            if result["items"]:
                result["source"]["status"] = "partial"
        else:
            result["source"]["status"] = "ready" if result["items"] else "empty"
        _fill_bodies(result, asset, deadline)
        if result["source"].get("body_pending_count"):
            ttl = min(ttl, 60)
        result["source"].update(fetched_count=len(rows), item_count=len(result["items"]),
                                elapsed_ms=round((time.monotonic() - started) * 1000))
        _store(key, result, ttl)
        return result

    result, state = _flights.run(key, load)
    result = deepcopy(result)
    if state == "shared":
        result["source"].update(cached=True, attempted=False, elapsed_ms=0)
    return result
