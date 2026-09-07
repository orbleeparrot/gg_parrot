"""Optional CoinDesk news search with shared caching and durable call limits."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from .http_runtime import SingleFlightGroup, get_http_client

_ENDPOINT = "https://data-api.coindesk.com/news/v1/search"
# Leave room below the repository's 256,000-byte serialized payload limit.
_CACHE_PAYLOAD_MAX_BYTES = 240_000
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_flights = SingleFlightGroup()


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _enabled_flag() -> bool:
    return os.environ.get("COINDESK_NEWS_API_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def configuration() -> dict:
    """Safe operational configuration; never return the credential or its hash."""
    return {
        "enabled": bool(os.environ.get("COINDESK_API_KEY", "").strip()) and _enabled_flag(),
        "max_calls_per_day": _integer("COINDESK_NEWS_MAX_CALLS_PER_DAY", 20, 0, 100_000),
        "max_total_calls": _integer("COINDESK_NEWS_MAX_TOTAL_CALLS", 100, 0, 10_000_000),
        "cache_seconds": _integer("COINDESK_NEWS_CACHE_SECONDS", 1800, 60, 86_400),
    }


def _repository():
    # The repository imports news.py, which also uses this provider.
    from .agent_features.position_news import repository

    return repository


def _result(query: str, status: str, *, error: str = "", http_status=None,
            attempted: bool = False, items=None, fetched_count: int = 0) -> dict:
    return {
        "items": items or [],
        "source": {
            "name": "coindesk_news_api", "status": status, "query": query,
            "attempted": attempted, "cached": False, "http_status": http_status,
            "error": error, "fetched_count": fetched_count, "elapsed_ms": 0,
        },
    }


def _plain(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()[:limit]


def _parse_articles(data: list) -> list[dict]:
    items, seen = [], set()
    for raw in data[:100]:
        if not isinstance(raw, dict) or raw.get("STATUS", "ACTIVE") != "ACTIVE":
            continue
        title, url = _plain(raw.get("TITLE"), 1000), raw.get("URL")
        if not title or not isinstance(url, str) or len(url) > 2048:
            continue
        try:
            parsed = urlsplit(url.strip())
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or re.search(r"[\s\x00-\x1f]", url)):
                continue
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
            timestamp = raw.get("PUBLISHED_ON")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            if not math.isfinite(timestamp) or timestamp <= 0:
                continue
            published = datetime.fromtimestamp(timestamp, timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if url in seen:
            continue
        seen.add(url)
        categories = []
        category_data = raw.get("CATEGORY_DATA")
        for category in (category_data[:50] if isinstance(category_data, list) else []):
            if isinstance(category, dict):
                category = category.get("NAME")
            label = _plain(category, 100)
            if label and label not in categories:
                categories.append(label)
        keywords = raw.get("KEYWORDS")
        if isinstance(keywords, str):
            for keyword in re.split(r"[,|]", keywords)[:50]:
                label = _plain(keyword, 100)
                if label and label not in categories:
                    categories.append(label)
        item = {
            "title": title, "url": url, "source": "CoinDesk",
            "published": published.isoformat(),
            "published_display": published.astimezone(timezone(timedelta(hours=9))).strftime("%m.%d %H:%M"),
            "categories": categories[:50], "feed_source": "coindesk_news_api",
        }
        excerpt = _plain(raw.get("SUBTITLE"), 1800)
        if excerpt:
            item["excerpt"] = excerpt
        items.append(item)
    return items


def _retry_seconds(response: httpx.Response, default: int) -> int:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_date = parsedate_to_datetime(value)
            retry_date = retry_date.replace(tzinfo=retry_date.tzinfo or timezone.utc)
            seconds = retry_date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = default
    if not math.isfinite(seconds):
        seconds = default
    return max(default, min(86_400, math.ceil(seconds)))


def _cached_result(payload: dict, query: str) -> dict:
    result = deepcopy(payload["result"])
    result["source"].update(query=query, cached=True, attempted=False, elapsed_ms=0)
    return result


def _load_cached(keys: list[str], query: str) -> dict | None:
    now = time.time()
    with _cache_lock:
        for key in keys:
            hit = _cache.get(key)
            if hit and hit.get("expires_at", 0) > now:
                return _cached_result(hit, query)
    try:
        shared = _repository().load_browser_pages(keys)
    except Exception:
        # The mandatory durable budget reservation below still fails closed.
        return None
    for key in keys:
        hit = shared.get(key)
        if (isinstance(hit, dict) and hit.get("expires_at", 0) > now
                and isinstance(hit.get("result"), dict)
                and isinstance(hit["result"].get("source"), dict)):
            with _cache_lock:
                _cache[key] = deepcopy(hit)
            return _cached_result(hit, query)
    return None


def _bounded_cache_payload(result: dict, expires_at: float) -> dict:
    """Fit UTF-8 metadata in durable storage, retaining article links first."""
    payload = {"result": result, "expires_at": expires_at}

    def fits() -> bool:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= _CACHE_PAYLOAD_MAX_BYTES

    if not fits():
        result["source"]["metadata_trimmed"] = True
        # Subtitles are supplementary: reduce them before touching headlines.
        for item in result["items"]:
            if item.get("excerpt"):
                item["excerpt"] = item["excerpt"][:400]
        if not fits():
            for item in result["items"]:
                item.pop("excerpt", None)
        if not fits():
            for item in result["items"]:
                item["title"] = item["title"][:200]
        if not fits():
            result["source"]["truncated_count"] = 0
            while result["items"] and not fits():
                result["items"].pop()
                result["source"]["truncated_count"] += 1
    return deepcopy(payload)


def _store(result: dict, keys: list[str], ttl: int) -> None:
    expires_at = time.time() + ttl
    payload = _bounded_cache_payload(result, expires_at)
    with _cache_lock:
        for key in keys:
            _cache[key] = deepcopy(payload)
        while len(_cache) > 512:
            _cache.pop(next(iter(_cache)))
    try:
        _repository().store_browser_pages({key: (payload, int(expires_at * 1000)) for key in keys})
    except Exception:
        # Keep usable news and the in-process cache if persisting metadata fails.
        result["source"]["cache_persisted"] = False


def fetch_news(search_term: str) -> dict:
    """Search once per cached query; never retry or call an unconfigured API."""
    query = re.sub(r"\s+", " ", str(search_term or "")).strip()[:1000]
    if not _enabled_flag():
        return _result(query, "disabled")
    credential = os.environ.get("COINDESK_API_KEY", "").strip()
    if not credential:
        return _result(query, "unconfigured", error="api_key_missing")
    if not query:
        return _result(query, "error", error="search_term_missing")
    settings = configuration()
    account = hashlib.sha256(credential.encode()).hexdigest()[:24]
    query_digest = hashlib.sha256(query.casefold().encode()).hexdigest()
    prefix = f"coindesk-api-v1:{account}"
    key, cooldown_key = f"{prefix}:search:{query_digest}", f"{prefix}:cooldown"

    def load() -> dict:
        cached = _load_cached([key, cooldown_key], query)
        if cached is not None:
            return cached
        try:
            reserved = _repository().reserve_news_api_budget(
                daily_limit=settings["max_calls_per_day"], total_limit=settings["max_total_calls"])
        except Exception:
            return _result(query, "error", error="budget_unavailable")
        if not reserved:
            return _result(query, "error", error="call_budget_exhausted")
        started = time.monotonic()
        ttl, account_cooldown, status = 300, False, None
        try:
            response = get_http_client().get(
                _ENDPOINT,
                params={"source_key": "coindesk", "search_string": query, "limit": 100, "lang": "EN"},
                headers={"Authorization": f"Apikey {credential}"},
                timeout=10.0, follow_redirects=False,
            )
            status = response.status_code
            if status != 200:
                error = {401: "authentication_required", 403: "access_denied", 429: "rate_limited"}.get(
                    status, "upstream_http_error")
                result = _result(query, "error", error=error, http_status=status, attempted=True)
                if status in {401, 403, 429}:
                    ttl = _retry_seconds(response, 3600 if status in {401, 403} else 300)
                    account_cooldown = True
                    result["source"]["retry_at"] = time.time() + ttl
            else:
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("Err") or not isinstance(payload.get("Data"), list):
                    result = _result(query, "error", error="invalid_api_response", http_status=200, attempted=True)
                else:
                    data = payload["Data"]
                    items = _parse_articles(data)
                    if data and not items:
                        result = _result(query, "error", error="invalid_articles", http_status=200,
                                         attempted=True, fetched_count=len(data))
                    else:
                        result = _result(query, "ready" if items else "empty", http_status=200,
                                         attempted=True, items=items, fetched_count=len(data))
                        ttl = settings["cache_seconds"]
        except httpx.TimeoutException:
            result = _result(query, "error", error="request_timeout", attempted=True)
        except httpx.HTTPError:
            result = _result(query, "error", error="request_failed", attempted=True)
        except (TypeError, ValueError):
            result = _result(query, "error", error="invalid_api_response", http_status=status, attempted=True)
        result["source"]["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        _store(result, [key, cooldown_key] if account_cooldown else [key], ttl)
        return result

    result, state = _flights.run(key, load)
    result = deepcopy(result)
    if state == "shared":
        result["source"].update(cached=True, attempted=False, elapsed_ms=0)
    return result
