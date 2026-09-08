"""Read the shared public trade snapshot; HTTP requests never collect trades."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time

from ... import whales
from ...http_runtime import SingleFlightGroup
from . import repository

_cache = {}
_lock = threading.Lock()
_flights = SingleFlightGroup()


def clear_cache():
    with _lock:
        _cache.clear()


def _snapshot(symbol, market):
    key = (market, symbol)
    def load():
        now = time.time()
        with _lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return deepcopy(hit[1])
        value = repository.read_snapshot(symbol, market)
        with _lock:
            _cache[key] = (now + 2, deepcopy(value))
            while len(_cache) > 512:
                _cache.pop(next(iter(_cache)))
        return value
    return deepcopy(_flights.run(key, load)[0])


def _millis(stamp):
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0


def get_activity(symbol: str, market: str = "spot") -> dict:
    try:
        payload = whales.base_payload(symbol, market)
    except ValueError:
        return {"feature_key": "whale_activity", "symbol": str(symbol), "market": str(market),
                "status": "unavailable", "items": [], "stale": False, "refresh_seconds": 30,
                "error": "unsupported_pair"}
    now_ms = int(time.time() * 1000)
    try:
        snapshot = _snapshot(payload["symbol"], payload["market"])
    except Exception:
        return {**payload, "status": "unavailable", "error": "storage_unavailable"}
    if snapshot is None:
        return {**payload, "status": "pending", "data_source": "shared_db",
                "collection": {"status": "pending", "last_success_ms": 0}}

    last_success = int(snapshot.get("last_success_ms") or 0)
    status = snapshot.get("collection_status", "pending")
    failed = status in {"error", "rate_limited"}
    stale = bool(last_success and (failed or now_ms - last_success >= repository.stale_seconds() * 1000))
    # Return an explicit public projection. Lease tokens and raw provider errors
    # belong to the repository, never to a user's feed.
    for key in ("observed_at", "window_start", "sampled_trades", "source_type", "http_status"):
        if key in snapshot:
            payload[key] = snapshot[key]
    items = [item for item in snapshot.get("items", [])
             if isinstance(item, dict) and now_ms - 600_000 <= _millis(item.get("occurred_at")) <= now_ms + 5000
             and float(item.get("notional") or 0) >= payload["threshold_quote"]]
    payload.update(items=items[:30], stale=stale, data_source="shared_db",
                   status=("unavailable" if stale or failed else
                           "pending" if not last_success else "ready" if items else "empty"),
                   collection={"status": status, "last_success_ms": last_success,
                               "last_attempt_ms": int(snapshot.get("last_attempt_ms") or 0),
                               "next_collection_ms": int(snapshot.get("next_collection_ms") or 0)})
    return payload
