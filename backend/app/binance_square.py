"""Public community metadata from the request made by Square's Latest tab.

This is a website endpoint, not Binance's authenticated trading API. No account,
cookies, full post bodies, or private chat data are needed or persisted.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
_PREFIX = "binance-square-latest-v1:"
_COOLDOWN = _PREFIX + "cooldown"
_MAX_RESPONSE_BYTES = 2_000_000
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
        content = _plain(raw.get("content"))[:20_000]
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
                "categories": [asset], "language": str(raw.get("detectedLanguage") or "")[:20]}
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


def _read_page(symbol, index, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("request_budget_exhausted")
    with get_http_client().stream("GET", _ENDPOINT,
            params={"hashtag": f"#{symbol.lower()}", "pageIndex": index, "pageSize": 20, "orderBy": "LATEST"},
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
            or not isinstance(payload.get("data"), dict) or not isinstance(payload["data"].get("feedData"), list)):
        raise ValueError("invalid_response")
    return response, payload["data"]["feedData"]


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
        result["source"].update(fetched_count=len(rows), item_count=len(result["items"]),
                                elapsed_ms=round((time.monotonic() - started) * 1000))
        _store(key, result, ttl)
        return result

    result, state = _flights.run(key, load)
    result = deepcopy(result)
    if state == "shared":
        result["source"].update(cached=True, attempted=False, elapsed_ms=0)
    return result
