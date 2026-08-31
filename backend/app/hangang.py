"""Hangang (한강) water-temperature proxy (fun reference widget, not a signal).

The frontend polls one backend endpoint (``/api/hangang-temp``) instead of hitting
the public API directly, which sidesteps browser CORS and shares a single server
cache across all viewers (spec §2/§5: never one external call per user). Every
external call is wrapped so a source failure degrades gracefully — the last good
cache is served (stale), otherwise ``ok: false`` and the UI hides itself.

Upstream: ``GET https://api.ivl.is/hangangtemp`` ->
    {"success": true, "date": "YYYYMMDD", "time": "HH:MM",
     "location": "중랑천", "temperature": 25.2}
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx

from .http_runtime import SingleFlightGroup, get_http_client

_HANGANG_URL = os.environ.get("HANGANG_API_URL", "https://api.ivl.is/hangangtemp")

# Server cache window (default 5 min) and upstream timeout. Env-configurable.
CACHE_SECONDS = float(os.environ.get("HANGANG_CACHE_SECONDS", "300"))
TIMEOUT_SECONDS = float(os.environ.get("HANGANG_TIMEOUT_SECONDS", "8"))

# Shared cache: (normalized_payload, expires_at). Single global entry.
_cache: Optional[tuple[dict, float]] = None
_refreshes = SingleFlightGroup()


def _fmt_updated(date: str, t: str) -> Optional[str]:
    """'YYYYMMDD' + 'HH:MM' -> 'MM/DD HH:MM' for display. None if unparseable."""
    if not date or len(date) != 8 or not date.isdigit():
        return t or None
    mm, dd = date[4:6], date[6:8]
    return f"{mm}/{dd} {t}".strip() if t else f"{mm}/{dd}"


def _fetch() -> Optional[dict]:
    """Fetch + normalize upstream, or None on any failure / success:false."""
    try:
        # The shared client follows redirects; upstream redirects this path once.
        resp = get_http_client().get(_HANGANG_URL, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    try:
        temperature = round(float(data["temperature"]), 1)
    except (KeyError, ValueError, TypeError):
        return None
    date = str(data.get("date", ""))
    obs_time = str(data.get("time", ""))
    return {
        "ok": True,
        "temperature": temperature,
        "location": str(data.get("location", "")) or "한강",
        "date": date,
        "time": obs_time,
        "observed_label": _fmt_updated(date, obs_time),
    }


def get_temp() -> dict:
    """Return the cached Hangang water temperature (fetches upstream at most once
    per cache window). Never raises; serves a stale cache on transient failure."""
    global _cache
    now = time.time()
    if _cache and _cache[1] > now:
        payload = dict(_cache[0])
        payload["cached"] = True
        return _envelope(payload)

    if _cache:
        fresh, refresh_state = _refreshes.run("hangang", _fetch, stale_value=None)
    else:
        fresh, refresh_state = _refreshes.run("hangang", _fetch)
    if refresh_state == "stale":
        payload = dict(_cache[0])
        payload["cached"] = True
        payload["stale"] = True
        return _envelope(payload)
    if fresh is not None:
        _cache = (fresh, now + CACHE_SECONDS)
        return _envelope({**fresh, "cached": refresh_state == "shared"})

    # Upstream failed: serve the last good value (flagged stale) if we have one.
    if _cache:
        payload = dict(_cache[0])
        payload["cached"] = True
        payload["stale"] = True
        return _envelope(payload)
    return _envelope({"ok": False, "error": "upstream", "temperature": None})


def _envelope(payload: dict) -> dict:
    payload.setdefault("updated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    payload.setdefault("disclaimer", "reference only; observed river temperature")
    return payload
