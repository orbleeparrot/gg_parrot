"""Exchange-specific public rankings over the shared market ticker snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
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
    high, low = _number(row.get("highPrice")), _number(row.get("lowPrice"))
    korean, english = _asset_names(match.group(1))
    return {"symbol": symbol, "base": match.group(1), "exchange": exchange,
            "quote_currency": "KRW" if domestic else "USDT",
            "korean_name": str(row.get("korean_name") or korean),
            "english_name": str(row.get("english_name") or english),
            "last_price": price, "change_pct": change, "quote_volume": turnover,
            "volume": volume if volume is not None and volume >= 0 else None,
            # 오늘 저가·고가 — 둘 다 있고 순서가 맞을 때만. 없으면 화면이 범위 막대를 그리지 않는다.
            "high": high if high and low and 0 < low <= high else None,
            "low": low if high and low and 0 < low <= high else None,
            "warning": bool(row.get("warning"))}


# 경주마 — 거래대금 상위 후보 안에서 오늘 많이 오른 코인. 바이낸스는 기존 '오늘의 경주마'
# (hotcoins.select_hot_coins)와 같은 규칙, 국내는 원화 거래대금 상위 DOMESTIC_RACER_POOL 개.
DOMESTIC_RACER_POOL = int(os.environ.get("DOMESTIC_RACER_POOL", "50"))
RACER_LIMIT = 30
_RACER_EXCLUDED = hotcoins._STABLE_BASES | {"USDT"}


def racer_rule(exchange: str) -> dict:
    binance = exchange == "binance"
    return {"pool": hotcoins.CANDIDATE_POOL if binance else DOMESTIC_RACER_POOL,
            "min_turnover": hotcoins.MIN_QUOTE_VOLUME if binance else 0,
            "change_basis": "24h" if binance else "previous_close"}


def select_racers(coins: list[dict], exchange: str, *, limit: int = RACER_LIMIT) -> list[str]:
    """Bases of today's racers from unfiltered ranking rows (pure; no I/O)."""
    rule = racer_rule(exchange)

    def eligible(coin):
        if coin["base"] in _RACER_EXCLUDED or hotcoins._is_leverage_token(coin["base"]):
            return False
        if coin["quote_volume"] < rule["min_turnover"]:
            return False
        # 1.0 에 붙어 있는 페그 자산(EUR·U 등)은 바이낸스에서만 USDT 기준으로 걸러진다.
        return not (exchange == "binance"
                    and abs(coin["last_price"] - 1.0) <= hotcoins._PEG_PRICE_TOLERANCE
                    and abs(coin["change_pct"]) <= hotcoins._PEG_CHANGE_TOLERANCE)

    pool = sorted((coin for coin in coins if eligible(coin)),
                  key=lambda coin: (-coin["quote_volume"], coin["symbol"]))[:max(1, rule["pool"])]
    gainers = sorted((coin for coin in pool if coin["change_pct"] > 0),
                     key=lambda coin: (-coin["change_pct"], -coin["quote_volume"], coin["symbol"]))
    return [coin["base"] for coin in gainers[:max(1, limit)]]


def get_racer_coins(exchange: str, limit: int = 10) -> dict:
    """사이트 하단 '오늘의 경주마' 띠 — 국내 거래소는 코인 순위의 경주마 탭과 같은 목록(같은 시세 캐시).

    바이낸스는 기존 hotcoins.get_hot_coins 를 그대로 쓴다(이 함수는 국내 전용).
    """
    limit = max(1, min(int(limit), 50))
    exchange = normalize_exchange(exchange)
    try:
        payload = get_market_trends(exchange=exchange)
    except Exception:
        return {**hotcoins._envelope([], cached=False, error=exchange), "exchange": exchange}
    by_base = {coin["base"]: coin for coin in payload["coins"]}
    coins = [by_base[base] for base in payload["racers"] if base in by_base][:limit]
    return {**hotcoins._envelope(coins, cached=True, stale=payload["stale"]),
            "updated_at": payload["updated_at"], "exchange": exchange,
            "quote_currency": payload["quote_currency"]}


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
            # 표시 필터(q·최소 거래대금·등락)와 상관없이 전체 후보에서 고른다.
            "racers": select_racers(candidates, exchange), "racer_rule": racer_rule(exchange),
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
