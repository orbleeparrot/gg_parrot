"""'오늘의 경주마' — hot-coin aggregator (reference only, NOT a trading signal).

Picks coins that are both *surging* and *actively traded* from Binance's public
24h ticker, so the bottom marquee can nudge users toward building a macro on a
trending symbol. The result is GLOBAL and SERVER-CACHED: the exchange is hit at
most once per cache window no matter how many clients poll (spec §1.5).

Selection (spec §1.2):
  1. keep USDT-quoted pairs only; drop leverage tokens and stable/fiat pairs;
  2. drop anything below a 24h quote-volume floor (illiquid noise);
  3. take the top-N by quote volume (= "actively traded");
  4. of those, take the top ``limit`` by 24h price change (= "surging").
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .http_runtime import get_http_client
from .cache_runtime import ResponseCache

# Env-configurable base so a US-hosted deploy can use data-api.binance.vision
# (api.binance.com is geo-blocked from US IPs). Same public data either way.
_BINANCE_BASE = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
_TICKER_24H = f"{_BINANCE_BASE}/api/v3/ticker/24hr"

# Tunables (env-configurable).
MIN_QUOTE_VOLUME = float(os.environ.get("HOTCOINS_MIN_QUOTE_VOLUME", "10000000"))  # 10M USDT
CANDIDATE_POOL = int(os.environ.get("HOTCOINS_CANDIDATE_POOL", "100"))
CACHE_SECONDS = float(os.environ.get("HOTCOINS_CACHE_SECONDS", "45"))

# Leverage-token suffixes (e.g. BTCUP / ETHDOWN / XRPBULL / SOLBEAR).
_LEV_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "HALF", "HEDGE")
# Stable/fiat bases whose *USDT pair is effectively a currency peg, not a coin.
# 새 페그 자산이 계속 생기므로 목록만 믿지 않는다 — 아래 _is_pegged 로 한 번 더 거른다.
_STABLE_BASES = frozenset(
    {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "UST", "USTC", "PAX", "GUSD",
     "EUR", "GBP", "AUD", "TRY", "BRL", "RUB", "JPY", "NGN", "ZAR",
     # 2024~2026 에 상장된 페그 자산들
     "USDE", "USD1", "PYUSD", "RLUSD", "USDD", "USDS", "USDG", "USDY", "USDF",
     "USDX", "USDJ", "USDB", "LUSD", "SUSD", "CRVUSD", "XUSD", "AEUR", "EURI"}
)

# 1.0 에 붙어 있는 자산 판정 — 값이 거의 안 움직이는 페그를 매매 후보로 내보내지 않는다.
_PEG_PRICE_TOLERANCE = 0.02   # 1.0 에서 ±2%
_PEG_CHANGE_TOLERANCE = 0.5   # 24시간 등락 ±0.5%


def _is_pegged(t: dict, change_pct: float, last_price: float) -> bool:
    """1.0 근처에 머물면서 하루 등락이 거의 없으면 페그로 본다.

    한 번 찍힌 lastPrice 보다 24시간 가중평균(weightedAvgPrice)이 안정적이라 그쪽을 먼저 쓴다.
    """
    try:
        price = float(t["weightedAvgPrice"])
    except (KeyError, ValueError, TypeError):
        price = last_price
    if price <= 0:
        return False
    return (abs(price - 1.0) <= _PEG_PRICE_TOLERANCE
            and abs(change_pct) <= _PEG_CHANGE_TOLERANCE)


def _is_leverage_token(base: str) -> bool:
    """True for leverage tokens (BTCUP), but not real coins that merely end in a
    suffix (e.g. JUP -> 'J' underlying is too short to be one)."""
    for suf in _LEV_SUFFIXES:
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            return True
    return False


def ticker_range_pct(t: dict) -> float:
    """하루 변동폭(%) — '대칭 반폭' 과 '하루 등락' 중 큰 값.

    두 측정을 같이 쓴다. 어느 한쪽만으로는 아래 두 가지를 동시에 못 가린다.

    * 대칭 반폭 ``2 × min(고가-가중평균, 가중평균-저가) / 가중평균 × 100``
      — 값이 하루 종일 한자리에 붙어 있었는데 저가만 한 번 튀어나온 티커(스테이블에서 흔하다)를
      걸러낸다. 좁은 쪽 반폭이 거의 0 이기 때문이다.
      그런데 **한쪽으로만 쭉 간 날**(갭 상승 뒤 고가 부근에서 마감)도 좁은 쪽 반폭이 작아
      이 값만 쓰면 하루 30% 오른 코인이 0.8% 로 나온다.
    * 하루 등락 ``|priceChangePercent|`` — 한쪽으로 간 날에는 크고, 붙어 있던 페그의
      일회성 꼬리에는 0 에 가깝다. 딱 반폭이 못 보는 자리를 메운다.

    그래서 둘 중 큰 값을 쓴다. 하루에 실제로 움직인 폭은 적어도 순매수·순매도만큼은 되므로
    등락률은 하한으로서 타당하고, 반폭은 꼬리를 뺀 '몸통' 폭이라 그보다 클 때만 이긴다.
    가중평균이 없거나 고가/저가 밖에 있는 응답은 예전처럼 고가/저가 원본을 몸통으로 쓴다.
    고가/저가가 없거나 이상하면 0.0 — 같은 티커 응답만 쓴다.
    """
    try:
        high = float(t["highPrice"])
        low = float(t["lowPrice"])
    except (KeyError, ValueError, TypeError):
        return 0.0
    if low <= 0 or high < low:
        return 0.0
    try:
        weighted = float(t["weightedAvgPrice"])
    except (KeyError, ValueError, TypeError):
        weighted = 0.0
    if weighted <= 0 or not (low <= weighted <= high):
        body = (high - low) / low * 100.0
    else:
        body = 2.0 * min(high - weighted, weighted - low) / weighted * 100.0
    try:
        change = abs(float(t["priceChangePercent"]))
    except (KeyError, ValueError, TypeError):
        change = 0.0
    return round(max(body, change), 2)


def select_hot_coins(
    tickers: list[dict],
    *,
    limit: int = 10,
    min_quote_volume: float = MIN_QUOTE_VOLUME,
    candidate_pool: int = CANDIDATE_POOL,
) -> list[dict]:
    """Pure selection over raw Binance 24h ticker dicts (testable, no I/O)."""
    candidates: list[dict] = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        if not base or base in _STABLE_BASES or _is_leverage_token(base):
            continue
        try:
            quote_volume = float(t["quoteVolume"])
            change_pct = float(t["priceChangePercent"])
            last_price = float(t["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
        if quote_volume < min_quote_volume:
            continue
        if _is_pegged(t, change_pct, last_price):
            continue
        candidates.append(
            {
                "symbol": symbol,
                "base": base,
                "change_pct": round(change_pct, 2),
                "last_price": last_price,
                "quote_volume": round(quote_volume, 2),
                "range_pct": ticker_range_pct(t),
            }
        )

    # 1) most actively traded -> 2) biggest gainers among them.
    candidates.sort(key=lambda c: c["quote_volume"], reverse=True)
    pool = candidates[: max(1, candidate_pool)]
    pool.sort(key=lambda c: c["change_pct"], reverse=True)
    return pool[: max(1, limit)]


# 캐시에 넣을 티커 수 — 거래대금 상위 이만큼만 남긴다. 바이낸스 24시간 응답 원본은
# 1.6MB 라 캐시 상한(max_bytes)에 걸려 통째로 버려졌고, 그러면 모든 요청이 거래소를
# 그대로 때린다(weight-80 → 418/차단 위험). 투영을 저장해 "캐시 창마다 한 번" 을 지킨다.
CACHE_TOP_SYMBOLS = int(os.environ.get("HOTCOINS_CACHE_TOP", "500"))
# 캐시에 남기는 필드 — 소비자(select_hot_coins·ask_candidates.build_pool)가 읽는 것만.
_KEEP_FIELDS = ("lastPrice", "priceChangePercent", "highPrice", "lowPrice", "weightedAvgPrice")


def trim_tickers(tickers: list[dict], *, top: int = CACHE_TOP_SYMBOLS) -> list[dict]:
    """캐시에 넣을 투영 — USDT 페어 중 거래대금 상위 ``top`` 개, 쓰는 필드만 숫자로.

    고른 뒤에도 원본 티커와 같은 열쇠 이름을 쓰므로 ``select_hot_coins`` 는 그대로 읽는다.
    """
    rows: list[dict] = []
    for t in tickers:
        symbol = str(t.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue
        try:
            quote_volume = float(t["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        row: dict = {"symbol": symbol, "quoteVolume": round(quote_volume, 2)}
        for field in _KEEP_FIELDS:
            try:
                row[field] = float(t[field])
            except (KeyError, ValueError, TypeError):
                continue
        rows.append(row)
    rows.sort(key=lambda r: r["quoteVolume"], reverse=True)
    return rows[: max(1, top)]


# One normalized source result; different list lengths reuse it.
# max_bytes 는 투영(상위 500개 ≒ 110KB)이 넉넉히 들어가되 원본(1.6MB)은 여전히 거부하는 값.
_cache = ResponseCache("hot-coins", max_entries=1, max_bytes=400_000, retry_seconds=15)

def _fetch_tickers() -> Optional[list[dict]]:
    try:
        resp = get_http_client().get(_TICKER_24H)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception:
        return None


def _load_ticker_payload() -> dict:
    tickers = _fetch_tickers()
    if not tickers:
        raise RuntimeError("hot coin source unavailable")
    trimmed = trim_tickers(tickers)
    return {"tickers": trimmed,
            "coins": select_hot_coins(trimmed, limit=50),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def get_cached_tickers() -> Optional[list[dict]]:
    """캐시된 24시간 티커(거래대금 상위 투영). 후보 풀이 같은 캐시를 재사용한다(추가 호출 없음)."""
    try:
        payload, _state = _cache.get_or_load("binance:24h", _load_ticker_payload,
                                             ttl=CACHE_SECONDS, stale_ttl=300)
    except Exception:
        return None
    tickers = payload.get("tickers")
    return tickers if isinstance(tickers, list) else None


def get_hot_coins(limit: int = 10) -> dict:
    """Return the cached hot-coins list (fetches Binance at most once per window)."""
    limit = max(1, min(int(limit), 50))
    try:
        payload, state = _cache.get_or_load("binance:24h", _load_ticker_payload,
                                            ttl=CACHE_SECONDS, stale_ttl=300)
    except Exception:
        return _envelope([], cached=False, error="binance")
    return {**_envelope(payload["coins"][:limit], cached=state != "loaded", stale=state == "stale"),
            "updated_at": payload["updated_at"]}


def _envelope(coins: list[dict], *, cached: bool, stale: bool = False, error: str | None = None) -> dict:
    out: dict = {
        "coins": coins,
        "cached": cached,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disclaimer": "reference only; not investment advice",
    }
    if stale:
        out["stale"] = True
    if error:
        out["error"] = error
    return out
