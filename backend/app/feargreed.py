"""Crypto Fear & Greed Index (reference indicator only, NOT a trading signal).

Server-cached proxy of the public Alternative.me index
(``https://api.alternative.me/fng/``): a single 0~100 gauge of *overall* crypto
market sentiment (BTC-centric), updated ~once a day upstream. It is MARKET-WIDE,
not per-coin — the UI must label it as such so a beginner doesn't read it as the
sentiment of whatever symbol they happen to be building on.

Same shape as the other reference widgets (kimchi/hangang): one backend endpoint,
one shared in-memory cache (upstream hit at most once per window), graceful
degradation to a stale copy so the banner never breaks the page.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx

from .http_runtime import get_http_client
from .cache_runtime import ResponseCache

_FNG_URL = "https://api.alternative.me/fng/"

# Upstream refreshes about once a day, so polling faster is pure waste.
CACHE_SECONDS = float(os.environ.get("FEARGREED_CACHE_SECONDS", "3600"))  # 1h

# Map Alternative.me's English classification to Korean. Fall back to the raw
# string if they ever add a new tier.
_KO = {
    "Extreme Fear": "극단적 공포",
    "Fear": "공포",
    "Neutral": "중립",
    "Greed": "탐욕",
    "Extreme Greed": "극단적 탐욕",
}

# (payload, expires_at)
_cache = ResponseCache("fear-greed", max_entries=1, retry_seconds=30, max_retry_seconds=300)


def _classify_ko(value: int, upstream: str) -> str:
    if upstream in _KO:
        return _KO[upstream]
    # Defensive fallback if the upstream label is missing/unknown.
    if value < 25:
        return "극단적 공포"
    if value < 45:
        return "공포"
    if value < 55:
        return "중립"
    if value < 75:
        return "탐욕"
    return "극단적 탐욕"


def _fetch() -> Optional[dict]:
    try:
        resp = get_http_client().get(_FNG_URL, params={"limit": 1}, timeout=10.0)
        resp.raise_for_status()
        body = resp.json()
        row = (body.get("data") or [None])[0]
        if not row:
            return None
        value = int(row["value"])
        return {
            "value": value,
            "classification": row.get("value_classification", ""),
            "classification_ko": _classify_ko(value, row.get("value_classification", "")),
            "observed_ts": int(row.get("timestamp", 0)) or None,
        }
    except Exception:
        return None


def get_fear_greed() -> dict:
    """Cached market-wide Fear & Greed index (never raises)."""
    def load():
        data = _fetch()
        if data is None:
            raise RuntimeError("fear-greed source unavailable")
        return {"ok": True, "scope": "market", **data, "updated_at": _now_iso(),
                "disclaimer": "market-wide crypto sentiment; reference only, not a trading signal"}
    try:
        payload, state = _cache.get_or_load("index", load, ttl=CACHE_SECONDS, stale_ttl=86_400)
    except Exception:
        return {"ok": False, "error": "upstream", "updated_at": _now_iso()}
    return {**payload, "cached": state != "loaded", **({"stale": True} if state == "stale" else {})}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
