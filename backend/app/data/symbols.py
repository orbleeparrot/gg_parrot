"""Tradable Binance USDT symbols (spot + USDT-M perpetual), cached in memory.

The builder's symbol search needs the real list so users can only add tickers
that exist; the leaderboard shows bases like ``CHIP`` and typing that verbatim
used to produce a phantom ``CHIP`` symbol. One fetch of both exchangeInfo
documents is reduced to small rows and kept for ``CACHE_TTL_S``. Each market
retains its own last good list, so one upstream failure cannot hide the other
market or erase previously verified market flags during a failed refresh.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from ..http_runtime import get_http_client, run_parallel
from ..cache_runtime import ResponseCache

SPOT_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
FUTURES_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
CACHE_TTL_S = 6 * 3600
STALE_TTL_S = 24 * 3600
RETRY_SECONDS = 5
QUOTE = "USDT"

_market_caches = {
    market: ResponseCache(f"symbols.{market}", max_entries=1, max_bytes=1_000_000,
                          retry_seconds=RETRY_SECONDS, max_retry_seconds=30,
                          clock=lambda: time.time())
    for market in ("spot", "futures")
}


def _spot_rows(doc: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for entry in doc.get("symbols") or []:
        if entry.get("quoteAsset") != QUOTE or entry.get("status") != "TRADING":
            continue
        permissions = entry.get("permissions") or []
        sets = entry.get("permissionSets") or []
        # permissionSets can be omitted to keep exchangeInfo small. The explicit
        # trading flag then remains authoritative, especially for margin-only
        # symbols; retain permission parsing for older response formats.
        if "isSpotTradingAllowed" in entry:
            spot_ok = entry["isSpotTradingAllowed"] is True
        else:
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


def _exchange_info_url(market: str) -> str:
    # Share the deployment's public-data hosts with candles and ticker prices.
    # In particular, Render uses Binance's public spot-data mirror.
    if market == "spot":
        base = os.environ.get("BINANCE_API_BASE", "").strip().rstrip("/")
        return f"{base}/api/v3/exchangeInfo" if base else SPOT_EXCHANGE_INFO
    base = os.environ.get("BINANCE_FAPI_BASE", "").strip().rstrip("/")
    return f"{base}/fapi/v1/exchangeInfo" if base else FUTURES_EXCHANGE_INFO


def _fetch_market_rows(market: str) -> dict[str, dict]:
    # Documented spot-only filters avoid transferring inactive symbols and
    # large permissionSets arrays. Quote filtering remains local (USDT).
    # https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-endpoints
    params = {"permissions": "SPOT", "showPermissionSets": "false", "symbolStatus": "TRADING"} if market == "spot" else None
    response = get_http_client().get(_exchange_info_url(market), params=params, timeout=20.0)
    response.raise_for_status()
    rows = (_spot_rows if market == "spot" else _futures_rows)(response.json())
    if not rows:
        raise ValueError(f"Exchange returned an empty {market} symbol list")
    return rows


def _market_symbols(market: str, now: Optional[float]):
    def load():
        return {"rows": _fetch_market_rows(market),
                "fetched_at": time.time() if now is None else now}
    try:
        payload, state = _market_caches[market].get_or_load(
            "usdt", load, ttl=CACHE_TTL_S, stale_ttl=STALE_TTL_S, now=now,
        )
        return payload, state, None
    except Exception as exc:
        # Keep each failure inside its market's job. run_parallel otherwise
        # raises for the whole request even when the other market succeeded.
        return None, "unavailable", exc


def list_symbols(*, now: Optional[float] = None) -> dict:
    """Return verified symbols from every available market, with source freshness."""
    results = run_parallel({market: lambda market=market: _market_symbols(market, now)
                            for market in _market_caches})
    available = {market: payload for market, (payload, _state, _error) in results.items()
                 if payload is not None}
    if not available:
        raise next(error for _payload, _state, error in results.values() if error is not None)
    items = _merge(available.get("spot", {}).get("rows", {}),
                   available.get("futures", {}).get("rows", {}))
    partial = len(available) != len(_market_caches)
    stale = partial or any(state == "stale" for _payload, state, _error in results.values())
    return {
        "items": items, "count": len(items),
        "fetched_at": min(payload["fetched_at"] for payload in available.values()),
        "stale": stale, "partial": partial,
        "sources": {
            market: {"status": "unavailable" if payload is None else "stale" if state == "stale" else "ready",
                     "fetched_at": payload["fetched_at"] if payload else None}
            for market, (payload, state, _error) in results.items()
        },
    }


def reset_cache() -> None:
    for cache in _market_caches.values():
        cache.clear()


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
