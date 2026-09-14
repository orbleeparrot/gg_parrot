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

from ..http_runtime import get_http_client

SPOT_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
FUTURES_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
CACHE_TTL_S = 6 * 3600
QUOTE = "USDT"

_lock = threading.Lock()
_cache: dict = {"items": None, "fetched_at": 0.0}


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
    client = get_http_client()
    spot_doc = client.get(SPOT_EXCHANGE_INFO, timeout=20.0)
    spot_doc.raise_for_status()
    fut_doc = client.get(FUTURES_EXCHANGE_INFO, timeout=20.0)
    fut_doc.raise_for_status()
    return _merge(_spot_rows(spot_doc.json()), _futures_rows(fut_doc.json()))


def list_symbols(*, now: Optional[float] = None) -> dict:
    """Cached symbol list: ``{"items": [...], "count": n, "fetched_at": epoch, "stale": bool}``."""
    ts = time.time() if now is None else now
    with _lock:
        items = _cache["items"]
        fresh = items is not None and ts - _cache["fetched_at"] < CACHE_TTL_S
        if fresh:
            return {"items": items, "count": len(items), "fetched_at": _cache["fetched_at"], "stale": False}
        try:
            items = fetch_symbols()
            _cache["items"] = items
            _cache["fetched_at"] = ts
            return {"items": items, "count": len(items), "fetched_at": ts, "stale": False}
        except Exception:
            if _cache["items"] is not None:  # serve the stale list rather than nothing
                return {"items": _cache["items"], "count": len(_cache["items"]), "fetched_at": _cache["fetched_at"], "stale": True}
            raise


def reset_cache() -> None:
    with _lock:
        _cache["items"] = None
        _cache["fetched_at"] = 0.0


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
