"""Tradable Binance USDT symbols (spot + USDT-M perpetual), cached in memory.

The builder's symbol search needs the real list so users can only add tickers
that exist; the leaderboard shows bases like ``CHIP`` and typing that verbatim
used to produce a phantom ``CHIP`` symbol. One fetch of both exchangeInfo
documents (~18MB spot, ~1MB futures) is reduced to ~660 small rows and kept for
``CACHE_TTL_S``; a failed refresh serves the stale list rather than nothing.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from ..http_runtime import get_http_client, run_parallel
from ..cache_runtime import ResponseCache

SPOT_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
FUTURES_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
CACHE_TTL_S = 6 * 3600
QUOTE = "USDT"

_cache = ResponseCache("symbols", max_entries=1, max_bytes=1_000_000,
                       retry_seconds=30, max_retry_seconds=300, clock=lambda: time.time())


def _spot_rows(doc: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for entry in doc.get("symbols") or []:
        if entry.get("quoteAsset") != QUOTE or entry.get("status") != "TRADING":
            continue
        permissions = entry.get("permissions") or []
        sets = entry.get("permissionSets") or []
        spot_ok = "SPOT" in permissions or any("SPOT" in (s or []) for s in sets) or (not permissions and not sets)
        if not spot_ok:
            continue
        rows[entry["symbol"]] = {"symbol": entry["symbol"], "base": entry.get("baseAsset") or "", "quote": QUOTE, "spot": True, "futures": False}
    return rows


def _futures_rows(doc: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for entry in doc.get("symbols") or []:
        if entry.get("quoteAsset") != QUOTE or entry.get("status") != "TRADING" or entry.get("contractType") != "PERPETUAL":
            continue
        rows[entry["symbol"]] = {"symbol": entry["symbol"], "base": entry.get("baseAsset") or "", "quote": QUOTE, "spot": False, "futures": True}
    return rows


def _merge(spot: dict[str, dict], fut: dict[str, dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for symbol, row in spot.items():
        merged[symbol] = dict(row)
    for symbol, row in fut.items():
        if symbol in merged:
            merged[symbol]["futures"] = True
        else:
            merged[symbol] = dict(row)
    return sorted(merged.values(), key=lambda r: r["symbol"])


def fetch_symbols() -> list[dict]:
    """Hit both exchangeInfo endpoints and return the merged rows (no cache)."""
    def fetch(url):
        response = get_http_client().get(url, timeout=20.0)
        response.raise_for_status()
        return response.json()
    docs = run_parallel({"spot": lambda: fetch(SPOT_EXCHANGE_INFO),
                         "futures": lambda: fetch(FUTURES_EXCHANGE_INFO)})
    return _merge(_spot_rows(docs["spot"]), _futures_rows(docs["futures"]))


def list_symbols(*, now: Optional[float] = None) -> dict:
    """Serve a bounded last-good list while refreshing outside the request."""
    def load():
        items = fetch_symbols()
        if not items:
            raise ValueError("Exchange returned an empty symbol list")
        return {"items": items, "count": len(items),
                "fetched_at": time.time() if now is None else now}
    payload, state = _cache.get_or_load("usdt", load, ttl=CACHE_TTL_S, stale_ttl=86_400, now=now)
    return {**payload, "stale": state == "stale"}


def reset_cache() -> None:
    _cache.clear()


# ── coin logo proxy (same-origin copy of Binance's public logo, for the share-card capture) ──
LOGO_BASE = "https://bin.bnbstatic.com/static/assets/logos"
_LOGO_MAX = 400
_logo_cache: dict[str, bytes] = {}
_logo_lock = threading.Lock()


def coin_logo_png(base: str) -> Optional[bytes]:
    """Return the PNG bytes for ``base`` (e.g. ``BTC``) or None when Binance has none."""
    key = "".join(ch for ch in base.upper() if ch.isalnum())[:20]
    if not key:
        return None
    with _logo_lock:
        if key in _logo_cache:
            return _logo_cache[key]
    client = get_http_client()
    try:
        resp = client.get(f"{LOGO_BASE}/{key}.png", timeout=10.0, headers={"Referer": ""})
    except Exception:
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    with _logo_lock:
        if len(_logo_cache) >= _LOGO_MAX:
            _logo_cache.pop(next(iter(_logo_cache)))
        _logo_cache[key] = resp.content
    return resp.content
