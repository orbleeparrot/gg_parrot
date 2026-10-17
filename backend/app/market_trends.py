"""Exchange-specific public rankings over the shared market ticker snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from . import hotcoins
from .data import krw
from .exchanges import Exchange, normalize_exchange

router = APIRouter(prefix="/api/market", tags=["market"])
Sort = Literal["turnover", "gainers", "losers", "volume"]
Direction = Literal["all", "up", "down"]


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _asset_names(base: str) -> tuple[str, str]:
    """Use prepared identity metadata only; ranking reads never discover names."""
    from . import news
    from .news_asset_catalog import peek_asset_name
    from .news_exchange_names import peek_names

    aliases = news._COIN_ALIASES.get(base, ())
    exchange_ko, exchange_en = peek_names(base)
    korean = news._COIN_KO.get(base) or next(
        (alias for alias in aliases if re.search(r"[가-힣]", alias)), exchange_ko)
    english = peek_asset_name(base) or exchange_en or next(
        (alias for alias in aliases if re.search(r"[A-Za-z]", alias)), "")
    return korean, english


def _coin(row: dict, exchange: str) -> dict | None:
    symbol = str(row.get("symbol") or "")
    domestic = exchange != "binance"
    match = re.fullmatch(r"KRW-([A-Z0-9]{1,20})" if domestic else r"([A-Z0-9]{1,30})USDT", symbol)
    if not match or row.get("exchange", exchange) != exchange:
        return None
    price, change, turnover = (_number(row.get(field)) for field in
                               ("lastPrice", "priceChangePercent", "quoteVolume"))
    if price is None or price <= 0 or change is None or turnover is None or turnover < 0:
        return None
    volume = _number(row.get("volume"))
    korean, english = _asset_names(match.group(1))
    return {"symbol": symbol, "base": match.group(1), "exchange": exchange,
            "quote_currency": "KRW" if domestic else "USDT",
            "korean_name": str(row.get("korean_name") or korean),
            "english_name": str(row.get("english_name") or english),
            "last_price": price, "change_pct": change, "quote_volume": turnover,
            "volume": volume if volume is not None and volume >= 0 else None,
            "warning": bool(row.get("warning"))}


def _matches(coin: dict, needle: str) -> bool:
    if not needle:
        return True
    from . import news

    names = [str(coin.get(key) or "").casefold()
             for key in ("symbol", "base", "korean_name", "english_name")]
    names.extend(str(alias).casefold() for alias in news._COIN_ALIASES.get(coin.get("base"), ()))
    # T is Threshold, not every name containing a 't'. Short ticker identities
    # are exact-only; users can still search longer Korean/English name pieces.
    if re.fullmatch(r"[a-z0-9]{1,2}", needle):
        return needle in names
    return any(needle in name for name in names)


def resolve_assets(q: str, exchange: str, snapshot: dict | None = None) -> list[str]:
    """Resolve ordered base IDs from a caller-supplied prepared rankings page.

    No cache loader, name discovery, HTTP request or AI call is started here.
    The caller must pass the full unfiltered ranking payload if it needs every
    match; a provider's documented candidate cap still bounds that universe.
    Missing preparation returns no identities, never guesses from raw text.
    """
    exchange = normalize_exchange(exchange)
    needle = q.strip().casefold()
    bases = []
    for coin in (snapshot or {}).get("coins", []):
        if not isinstance(coin, dict) or coin.get("exchange") != exchange:
            continue
        base = str(coin.get("base") or "")
        symbol = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
        if (not re.fullmatch(r"[A-Z0-9]{1,30}" if exchange == "binance" else r"[A-Z0-9]{1,20}", base)
                or coin.get("symbol") != symbol or not _matches(coin, needle)):
            continue
        if base not in bases:
            bases.append(base)
    return bases


def filter_rankings(payload: dict, *, q: str = "", min_turnover: float = 0,
                    direction: Direction = "all", limit: int = 500) -> dict:
    """Filter an already sorted prepared response, without modifying its input.

    Display filters must not shrink the identity universe used by news and
    community search. Compose those sections from ``resolve_assets`` first,
    then apply this pure projection to the displayed rankings only.
    """
    needle = q.strip().casefold()
    matched = [coin for coin in payload.get("coins", [])
               if coin["quote_volume"] >= min_turnover
               and (direction == "all" or (coin["change_pct"] > 0
                                          if direction == "up" else coin["change_pct"] < 0))
               and _matches(coin, needle)]
    coins = [{**coin, "rank": index + 1} for index, coin in enumerate(matched[:limit])]
    return {**payload, "coins": coins, "total": len(matched), "rank_basis": "filtered_results",
            "coverage": {**payload.get("coverage", {}), "matched_count": len(matched),
                         "returned_count": len(coins), "result_truncated": len(matched) > len(coins)}}


def get_market_trends(*, exchange: str = "binance", sort: Sort = "turnover",
                      q: str = "", min_turnover: float = 0, direction: Direction = "all",
                      limit: int = 500) -> dict:
    exchange = normalize_exchange(exchange)
    snapshot = hotcoins.get_ticker_snapshot() if exchange == "binance" else krw.get_ticker_snapshot(exchange)
    rows = [_coin(row, exchange) for row in snapshot["items"] if isinstance(row, dict)]
    candidates = [coin for coin in rows if coin is not None]
    invalid_count = len(snapshot["items"]) - len(candidates) + snapshot.get("source_invalid_count", 0)
    coins = candidates
    if sort == "losers":
        coins.sort(key=lambda coin: (coin["change_pct"], -coin["quote_volume"], coin["symbol"]))
    else:
        field = {"turnover": "quote_volume", "gainers": "change_pct", "volume": "volume"}[sort]
        coins.sort(key=lambda coin: (-(coin[field] if coin[field] is not None else -1),
                                     -coin["quote_volume"], coin["symbol"]))
    source_count = snapshot.get("source_count", len(snapshot["items"]))
    source_truncated = snapshot.get("source_truncated", source_count > len(snapshot["items"]))
    status = ("error" if not candidates else "stale" if snapshot["stale"]
              else "degraded" if invalid_count else "ready")
    payload = {"exchange": exchange, "region": "global" if exchange == "binance" else "kr",
            "quote_currency": "USDT" if exchange == "binance" else "KRW",
            "coins": coins,
            "updated_at": datetime.fromtimestamp(snapshot["fetched_at"], timezone.utc).isoformat(),
            "stale": bool(snapshot["stale"]), "refresh_seconds": snapshot["refresh_seconds"],
            "cache_age_seconds": snapshot["cache_age_seconds"],
            "change_basis": "24h" if exchange == "binance" else "previous_close",
            "status": status, "rank_basis": "filtered_results",
            "coverage": {"source_count": source_count, "candidate_count": len(candidates),
                         "truncated": source_truncated,
                         "basis": "turnover_top" if source_truncated else "all",
                         "invalid_count": invalid_count}}
    return filter_rankings(payload, q=q, min_turnover=min_turnover, direction=direction, limit=limit)


@router.get("/trends")
def market_trends(
    response: Response,
    exchange: Exchange = "binance",
    sort: Sort = "turnover",
    q: str = Query(default="", max_length=100),
    min_turnover: float = Query(default=0, ge=0, allow_inf_nan=False),
    direction: Direction = "all",
    limit: int = Query(default=500, ge=1, le=500),
) -> dict:
    try:
        result = get_market_trends(exchange=exchange, sort=sort, q=q,
                                   min_turnover=min_turnover, direction=direction, limit=limit)
    except Exception as exc:
        raise HTTPException(503, "거래소 시세를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.") from exc
    response.headers["Cache-Control"] = "public, max-age=5"
    return result
