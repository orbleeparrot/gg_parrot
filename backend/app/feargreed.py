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

from .http_runtime import SingleFlightGroup, get_http_client

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
_cache: Optional[tuple[dict, float]] = None
_refreshes = SingleFlightGroup()


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
    global _cache
    now = time.time()
    if _cache and _cache[1] > now:
        return {**_cache[0], "cached": True}

    if _cache:
        data, refresh_state = _refreshes.run("fear-greed", _fetch, stale_value=None)
    else:
        data, refresh_state = _refreshes.run("fear-greed", _fetch)
    if refresh_state == "stale":
        return {**_cache[0], "cached": True, "stale": True}
    if data is None:
        # Serve the last good value rather than blanking the widget.
        if _cache:
            return {**_cache[0], "cached": True, "stale": True}
        return {"ok": False, "error": "upstream", "updated_at": _now_iso()}

    payload = {
        "ok": True,
        "scope": "market",  # market-wide, NOT per-coin
        "value": data["value"],
        "classification": data["classification"],
        "classification_ko": data["classification_ko"],
        "observed_ts": data["observed_ts"],
        "updated_at": _now_iso(),
        "disclaimer": "market-wide crypto sentiment; reference only, not a trading signal",
    }
    _cache = (payload, now + CACHE_SECONDS)
    return {**payload, "cached": refresh_state == "shared"}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
