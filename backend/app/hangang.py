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

from .http_runtime import get_http_client
from .cache_runtime import ResponseCache

_HANGANG_URL = os.environ.get("HANGANG_API_URL", "https://api.ivl.is/hangangtemp")

# Server cache window (default 5 min) and upstream timeout. Env-configurable.
CACHE_SECONDS = float(os.environ.get("HANGANG_CACHE_SECONDS", "300"))
TIMEOUT_SECONDS = float(os.environ.get("HANGANG_TIMEOUT_SECONDS", "8"))

# Shared cache: (normalized_payload, expires_at). Single global entry.
_cache = ResponseCache("hangang", max_entries=1, retry_seconds=30, max_retry_seconds=300)


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
    def load():
        data = _fetch()
        if data is None:
            raise RuntimeError("temperature source unavailable")
        return _envelope(data)
    try:
        payload, state = _cache.get_or_load("temperature", load, ttl=CACHE_SECONDS, stale_ttl=3600)
    except Exception:
        return _envelope({"ok": False, "error": "upstream", "temperature": None})
    return {**payload, "cached": state != "loaded", **({"stale": True} if state == "stale" else {})}


def _envelope(payload: dict) -> dict:
    payload.setdefault("updated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    payload.setdefault("disclaimer", "reference only; observed river temperature")
    return payload
