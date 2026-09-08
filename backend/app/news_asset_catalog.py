"""Shared, keyless Binance asset names for resolving unfamiliar news tickers.

The public website catalog is a best-effort metadata source, not a trading API.
Only the base-symbol/name mapping is retained; prices and user data are unused.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata

from .http_runtime import SingleFlightGroup, get_http_client

_ENDPOINT = "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products"
_CACHE_KEY = "news-asset-catalog:binance:v1"
_COOLDOWN_KEY = f"{_CACHE_KEY}:cooldown"
_FRESH_SECONDS = 86_400
_STALE_SECONDS = 30 * _FRESH_SECONDS
_RETRY_SECONDS = 120
_MAX_PAYLOAD_BYTES = 240_000
_SYMBOL = re.compile(r"[A-Z0-9]{1,32}\Z")
_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "TRY", "EUR")
_cache: dict | None = None
_retry_after = 0.0
_lock = threading.Lock()
_flights = SingleFlightGroup()


def _repository():
    # The repository also imports news.py; defer this import until an actual read.
    from .agent_features.position_news import repository

    return repository


def _symbol(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip().upper()
    return value if _SYMBOL.fullmatch(value) else ""


def _name(value) -> str:
    if not isinstance(value, str) or len(value) > 160:
        return ""
    if any(unicodedata.category(char).startswith("C") for char in value):
        return ""
    value = " ".join(value.split())
    if not value or any(char in value for char in "<>\\") or "://" in value:
        return ""
    return value if any(char.isalpha() for char in value) else ""


def _parse_catalog(raw) -> dict[str, str]:
    if (not isinstance(raw, dict) or raw.get("code") != "000000"
            or raw.get("success") is not True or not isinstance(raw.get("data"), list)
            or not 0 < len(raw["data"]) <= 20_000):
        raise ValueError("Invalid Binance asset catalog schema")
    names, conflicts = {}, set()
    for row in raw["data"]:
        if not isinstance(row, dict):
            continue
        base = _symbol(row.get("b"))
        name = _name(row.get("an")) or _name(row.get("adn"))
        if not base or not name or base in conflicts:
            continue
        if base in names and names[base].casefold() != name.casefold():
            names.pop(base)
            conflicts.add(base)
        else:
            names.setdefault(base, name)
    if not names:
        raise ValueError("Binance asset catalog has no unambiguous names")
    return names


def _valid_payload(payload, now: float) -> bool:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return False
    fetched = payload.get("fetched_at")
    names = payload.get("names")
    if (isinstance(fetched, bool) or not isinstance(fetched, (int, float))
            or not math.isfinite(fetched) or not 0 < fetched <= now + 300
            or now - fetched >= _STALE_SECONDS
            or not isinstance(names, dict) or not 0 < len(names) <= 20_000):
        return False
    return all(base and name and _symbol(base) == base and _name(name) == name
               for base, name in names.items())


def _usable_cache(now: float) -> dict | None:
    # Memory only contains already validated immutable-by-convention payloads.
    return _cache if _cache and now - _cache["fetched_at"] < _STALE_SECONDS else None


def _lookup(payload: dict | None, symbol: str) -> str:
    if not payload:
        return ""
    names = payload["names"]
    if symbol in names:
        return names[symbol]
    for quote in _QUOTES:
        if symbol.endswith(quote) and symbol[:-len(quote)] in names:
            return names[symbol[:-len(quote)]]
    return ""


def peek_asset_name(symbol: str) -> str:
    """Read a base asset or concatenated pair from memory, without any I/O."""
    symbol = _symbol(symbol)
    if not symbol:
        return ""
    with _lock:
        return _lookup(_usable_cache(time.time()), symbol)


def _persist(entries: dict) -> None:
    try:
        _repository().store_browser_pages(entries)
    except Exception:
        # Free metadata may still be used from memory during a DB outage.
        pass


def _refresh() -> dict | None:
    global _cache, _retry_after
    now = time.time()
    with _lock:
        cached = _usable_cache(now)
        if (cached and now - cached["fetched_at"] < _FRESH_SECONDS) or now < _retry_after:
            return cached
    try:
        shared = _repository().load_browser_pages([_CACHE_KEY, _COOLDOWN_KEY])
    except Exception:
        shared = {}
    if not isinstance(shared, dict):
        shared = {}
    candidate = shared.get(_CACHE_KEY)
    cooldown = shared.get(_COOLDOWN_KEY)
    with _lock:
        if _valid_payload(candidate, now) and (not cached or candidate["fetched_at"] > cached["fetched_at"]):
            _cache = cached = candidate
        until = cooldown.get("retry_after") if isinstance(cooldown, dict) else None
        if (isinstance(until, (int, float)) and not isinstance(until, bool)
                and math.isfinite(until) and now < until <= now + 86_400):
            _retry_after = max(_retry_after, until)
        if (cached and now - cached["fetched_at"] < _FRESH_SECONDS) or now < _retry_after:
            return cached
    retry_seconds = _RETRY_SECONDS
    try:
        response = get_http_client().get(
            _ENDPOINT, params={"includeEtf": "true"}, timeout=5.0,
            follow_redirects=False,
        )
        if response.status_code == 429:
            try:
                delay = float(response.headers.get("Retry-After", ""))
                if math.isfinite(delay):
                    retry_seconds = max(_RETRY_SECONDS, min(86_400, math.ceil(delay)))
            except ValueError:
                pass
        if response.status_code != 200 or len(response.content) > 5_000_000:
            raise ValueError("Binance asset catalog unavailable")
        payload = {"version": 1, "fetched_at": time.time(), "names": _parse_catalog(response.json())}
        # BrowserNewsPageCache drops oversized rows; never pretend such a cache
        # was persisted and download it repeatedly in each collector process.
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Binance asset catalog metadata exceeds cache capacity")
    except Exception:
        with _lock:
            _retry_after = time.time() + retry_seconds
            until = _retry_after
            cached = _usable_cache(time.time())
        _persist({_COOLDOWN_KEY: ({"retry_after": until}, int(until * 1000))})
        return cached
    with _lock:
        _cache = payload
        _retry_after = 0.0
    _persist({_CACHE_KEY: (payload, int((payload["fetched_at"] + _STALE_SECONDS) * 1000))})
    return payload


def get_asset_name(symbol: str) -> str:
    """Resolve a name with a daily shared catalog refresh and last-good fallback.

    Unknown or unavailable assets return an empty string, allowing callers to
    keep their original ticker. Concurrent symbols share a single catalog load.
    """
    symbol = _symbol(symbol)
    if not symbol:
        return ""
    with _lock:
        now = time.time()
        cached = _usable_cache(now)
        if (cached and now - cached["fetched_at"] < _FRESH_SECONDS) or now < _retry_after:
            return _lookup(cached, symbol)
    if cached:
        payload, _ = _flights.run(_CACHE_KEY, _refresh, stale_value=cached)
    else:
        payload, _ = _flights.run(_CACHE_KEY, _refresh)
    return _lookup(payload, symbol)
