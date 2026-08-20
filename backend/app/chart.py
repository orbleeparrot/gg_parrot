"""Live candle feed for the chart widget (public market data only).

Thin cached wrapper over :func:`data.get_recent_klines`. The cache is GLOBAL and
short (a few seconds), so N browsers polling the same symbol collapse into at
most one Binance call per window — the same pattern used by hot-coins/kimchi.

Why a separate path from the backtest loader: ``data.get_klines`` is cache-first
and persists whatever it fetched, including the still-forming bar. That is
correct for settled history but would freeze a live chart, so the chart reads
:func:`get_recent_klines`, which always refetches and never stores the open bar.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .data import NoSpotDataError, get_recent_klines

# Supported intervals -> how long a chart response stays fresh. This value is
# both the server cache TTL and the poll interval the client is told to use.
#
# The 300-bar history only changes when a bar closes, and the moving edge is
# already served separately by /api/candles/live (325 B every 3 s). Refreshing
# the whole history faster than the bar interval therefore re-sends ~29 KB of
# bytes the client already has — that is what exhausted the 5 GB free tier
# (1m at 3 s = 33.6 MB/hour per open chart). Roughly one refresh per bar, capped
# so long intervals still correct themselves within a few minutes.
_INTERVALS: dict[str, float] = {
    "1m": 60.0,
    "3m": 120.0,
    "5m": 120.0,
    "15m": 180.0,
    "1h": 300.0,
    "4h": 600.0,
    "1d": 900.0,
}
DEFAULT_INTERVAL = "1m"
MAX_LIMIT = int(os.environ.get("CHART_MAX_LIMIT", "300"))

# (symbol, interval, limit, market) -> (payload, expires_at)
_cache: dict[tuple[str, str, int, str], tuple[dict, float]] = {}
_LIVE_REFRESH_SECONDS = 3.0
# The latest two bars use a separate, short cache.  A 1d chart may refresh its
# 300-bar history only once a minute, but its open candle still has to move.
_live_cache: dict[tuple[str, str, str], tuple[dict, float]] = {}


def supported_intervals() -> list[str]:
    return list(_INTERVALS)


def get_candles(
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    limit: int = 120,
    market: str = "spot",
) -> dict:
    """Cached recent candles for ``symbol``.

    Raises :class:`NoSpotDataError` when the symbol has no market and there is no
    cached copy to fall back on (surfaced as a 422 by the route).
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise NoSpotDataError("종목(symbol)을 입력하세요.")
    if interval not in _INTERVALS:
        interval = DEFAULT_INTERVAL
    if market not in ("spot", "futures"):
        market = "spot"
    limit = max(10, min(int(limit), MAX_LIMIT))

    key = (symbol, interval, limit, market)
    hit = _cache.get(key)
    if hit and hit[1] > time.time():
        return {**hit[0], "cached": True}

    # 선물 호스트(fapi)는 배포 리전에서 차단될 수 있다 — 현물은 미러
    # (BINANCE_API_BASE)로 우회하지만 선물엔 대응 미러가 없다. 그래서 선물을
    # 못 받으면 현물로 떨어뜨린다. 백테스트(fetch_klines_for_macro)가 auto 에서
    # 하는 것과 같은 처리로, 레버리지를 올렸다고 차트가 통째로 사라지는 것보다
    # 기준 시세라도 보여주는 편이 낫다. 실제로 쓴 시장은 payload 의 market 이 알린다.
    used_market = market
    try:
        candles = get_recent_klines(symbol, interval=interval, limit=limit, market=market)
    except Exception as first_error:
        candles = None
        if market == "futures":
            try:
                candles = get_recent_klines(symbol, interval=interval, limit=limit, market="spot")
                used_market = "spot"
            except Exception:
                candles = None
        if candles is None:
            # Transient upstream failure: serve the last good copy rather than
            # blanking a chart the user is watching.
            if hit:
                return {**hit[0], "cached": True, "stale": True}
            if isinstance(first_error, NoSpotDataError):
                raise
            raise NoSpotDataError("시세를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")

    payload = {
        "symbol": symbol,
        "interval": interval,
        "market": used_market,
        "requested_market": market,
        "candles": candles,
        "server_time": int(time.time() * 1000),
        "refresh_seconds": _INTERVALS[interval],
        "disclaimer": "public market data; reference only",
    }
    _cache[key] = (payload, time.time() + _INTERVALS[interval])
    return {**payload, "cached": False}


def get_live_candles(
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    market: str = "spot",
) -> dict:
    """Return only the latest two candles on a fixed live cadence.

    Keeping this separate from :func:`get_candles` avoids downloading the full
    chart buffer every few seconds while still updating the open bar and
    detecting a newly-opened bar for long intervals such as 1d.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise NoSpotDataError("종목(symbol)을 입력하세요.")
    if interval not in _INTERVALS:
        interval = DEFAULT_INTERVAL
    if market not in ("spot", "futures"):
        market = "spot"

    key = (symbol, interval, market)
    hit = _live_cache.get(key)
    now = time.time()
    if hit and hit[1] > now:
        return {**hit[0], "cached": True}

    # get_candles 와 같은 선물→현물 폴백. 히스토리는 현물로 떨어졌는데 움직이는
    # 봉만 선물을 고집하면 두 시세가 섞여 캔들이 튄다.
    used_market = market
    try:
        candles = get_recent_klines(symbol, interval=interval, limit=2, market=market)
    except Exception as first_error:
        candles = None
        if market == "futures":
            try:
                candles = get_recent_klines(symbol, interval=interval, limit=2, market="spot")
                used_market = "spot"
            except Exception:
                candles = None
        if candles is None:
            if hit:
                return {**hit[0], "cached": True, "stale": True}
            if isinstance(first_error, NoSpotDataError):
                raise
            raise NoSpotDataError("실시간 시세를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")

    payload = {
        "symbol": symbol,
        "interval": interval,
        "market": used_market,
        "requested_market": market,
        "candles": candles,
        "server_time": int(now * 1000),
        "refresh_seconds": _LIVE_REFRESH_SECONDS,
    }
    _live_cache[key] = (payload, now + _LIVE_REFRESH_SECONDS)
    return {**payload, "cached": False}
