"""시가총액 순위와 분야(범주) — 코인게코 공개 API. 인증도 주문도 없다.

왜 외부 자료인가: 우리가 가진 건 24시간 거래대금뿐이다. 시가총액은 유통량이 있어야
나오므로 거래소 API 로는 만들 수 없다.

**무료 등급은 분당 호출이 빡빡하다.** 연속으로 부르면 429 가 온다. 그래서 오래 캐시하고,
실패하면 묵은 값을 그대로 내준다 — 시가총액 순위는 분 단위로 바뀌는 값이 아니라 하루
묵어도 틀리지 않는다. 끝내 못 받으면 ``None`` 을 돌려주고, 부르는 쪽이 추천 묶음을 아예
안 보여 준다. 틀린 구성을 보여 주느니 없는 게 낫다.

설계: docs/superpowers/specs/2026-10-30-portfolio-presets-design.md
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from ..cache_runtime import ResponseCache
from ..http_runtime import get_http_client

log = logging.getLogger(__name__)

BASE = "https://api.coingecko.com/api/v3"
MARKETS = f"{BASE}/coins/markets"

# 순위는 하루 묵어도 쓸 만하다. 짧게 잡으면 429 만 더 받는다.
CACHE_TTL_S = int(os.environ.get("COINGECKO_TTL_S", 6 * 3600))
# 묵은 값을 언제까지 쓸 것인가. 사흘이 넘으면 상장 폐지된 종목이 남아 있을 수 있다.
STALE_TTL_S = int(os.environ.get("COINGECKO_STALE_TTL_S", 3 * 24 * 3600))
RETRY_SECONDS = 60          # 429 를 받고 바로 다시 부르면 또 429 다
MAX_RETRY_SECONDS = 15 * 60
TIMEOUT_S = 15.0

# 순위를 몇 위까지 들고 있을 것인가. 거래소 상장 목록과 교집합을 내면 많이 줄어들므로
# 넉넉히 받아 둔다 — 한 번 부를 때 250개가 최대다.
TOP_N = 250

# 분야별 묶음이 쓰는 범주. 코인게코의 범주 이름(slug)이다.
CATEGORY_DEFI = "decentralized-finance-defi"
CATEGORY_LAYER1 = "layer-1"
CATEGORY_MEME = "meme-token"
# 거르기용 범주 — 포장 자산과 스테이킹 파생은 **같은 자산을 한 번 더 담는 것**이라
# 분산이 아니다. WBTC 를 BTC 옆에 놓으면 BTC 를 두 번 사는 셈이다.
CATEGORY_WRAPPED = "wrapped-tokens"
CATEGORY_STAKED = "liquid-staking-tokens"

_cache = ResponseCache("coingecko", max_entries=16, max_bytes=4_000_000,
                       retry_seconds=RETRY_SECONDS, max_retry_seconds=MAX_RETRY_SECONDS)


class CoinGeckoUnavailable(RuntimeError):
    """한 번도 못 받았다 — 묵은 값조차 없다."""


def _fetch(params: dict) -> list[dict]:
    client = get_http_client()
    response = client.get(MARKETS, params=params, timeout=TIMEOUT_S,
                          headers={"accept": "application/json"})
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, list):
        # 429 · 오류는 목록이 아니라 {"status": {...}} 로 온다.
        raise CoinGeckoUnavailable(str(body)[:200])
    return body


def _rows(params: dict) -> list[dict]:
    """``{티커: 시가총액}`` 을 만들기 전의 날 행들. 꼭 필요한 칸만 남긴다."""
    out = []
    for entry in _fetch(params):
        symbol = str(entry.get("symbol") or "").upper()
        cap = entry.get("market_cap")
        if not symbol or not isinstance(cap, (int, float)) or cap <= 0:
            continue
        out.append({"symbol": symbol, "market_cap": float(cap),
                    "id": str(entry.get("id") or "")})
    return out


def _cached(key: str, params: dict) -> Optional[list[dict]]:
    """캐시를 거쳐 받는다. 묵은 값도 없고 받지도 못하면 ``None``."""
    try:
        payload, _state = _cache.get_or_load(key, lambda: _rows(params),
                                             ttl=CACHE_TTL_S, stale_ttl=STALE_TTL_S)
        return payload
    except Exception:
        log.warning("coingecko: %s 를 받지 못했다 — 추천 묶음을 내지 않는다", key, exc_info=True)
        return None


def ranked() -> Optional[list[dict]]:
    """시가총액 순 ``{symbol, id, market_cap}`` 목록(큰 것부터). 못 받으면 ``None``.

    **티커가 겹치면 시가총액이 큰 쪽이 그 티커의 주인이다.** 코인게코의 ``symbol`` 은
    고유하지 않다 — 실제로 ``wrapped-tokens`` 범주에 티커가 그냥 ``BTC`` 인 포장 토큰이
    있다. 티커만 보고 거르면 **진짜 비트코인이 지워진다.** 그래서 어디서든 ``id`` 로
    견주고, 티커는 주인 하나에만 붙인다. 순위가 이미 내림차순이라 먼저 본 것이 주인이다.
    """
    rows = _cached("top", {"vs_currency": "usd", "order": "market_cap_desc",
                           "per_page": TOP_N, "page": 1})
    if rows is None:
        return None
    out: list[dict] = []
    known: set[str] = set()
    for row in rows:
        if row["symbol"] in known:
            continue
        known.add(row["symbol"])
        out.append(row)
    return out


def category_ids(slug: str) -> Optional[set[str]]:
    """그 범주에 든 코인의 ``id`` 들. 못 받으면 ``None``.

    티커가 아니라 ``id`` 를 돌려주는 이유는 ``ranked`` 의 설명과 같다 — 같은 티커를 쓰는
    다른 코인이 범주에 들어 있을 수 있다.
    """
    rows = _cached(f"cat:{slug}", {"vs_currency": "usd", "category": slug,
                                   "order": "market_cap_desc", "per_page": 100, "page": 1})
    if rows is None:
        return None
    return {row["id"] for row in rows if row["id"]}


def derivative_ids() -> set[str]:
    """포장 자산 · 스테이킹 파생의 ``id`` 들. 못 받으면 **빈 집합** — 거르기는 최선 노력이다.

    왜 None 이 아닌가: 이걸 못 받았다고 추천 묶음 전체를 접을 이유는 없다. 순위 자체를
    못 받았을 때만 접는다(``ranked`` 가 None).
    """
    out: set[str] = set()
    for slug in (CATEGORY_WRAPPED, CATEGORY_STAKED):
        rows = category_ids(slug)
        if rows:
            out.update(rows)
    return out


def reset_cache() -> None:
    _cache.clear()
