"""물어볼까 v2 — 종목 후보를 고른다.

법적 설계를 코드로 강제한다:
* AI 가 고를 수 있는 선택지는 ``build_pool`` 이 만든 목록뿐이다(풀 밖 심볼은 버린다).
* 순위·변동폭·등락률 같은 숫자는 전부 서버가 계산해 붙인다. AI 는 문장만 쓴다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보.
* AI 가 죽어도 규칙만으로 후보가 나온다.
"""
from __future__ import annotations

from .hotcoins import MIN_QUOTE_VOLUME, select_hot_coins

POOL_SIZE = 10
# 거래대금 상위 몇 개 안에서 성향별 정렬을 할지 — 유동성 없는 코인이 후보에 오르지 않게 한다.
LIQUID_TOP = 40
SCALPER_LIQUID_TOP = 15


def build_pool(tickers: list[dict], *, profile: str, size: int = POOL_SIZE) -> list[dict]:
    """성향에 맞는 후보 풀. AI 는 이 목록 밖으로 나갈 수 없다."""
    coins = select_hot_coins(tickers, limit=500, min_quote_volume=MIN_QUOTE_VOLUME,
                             candidate_pool=500)
    coins.sort(key=lambda c: c["quote_volume"], reverse=True)
    for rank, coin in enumerate(coins, start=1):
        coin["volume_rank"] = rank

    top = SCALPER_LIQUID_TOP if profile == "scalper" else LIQUID_TOP
    pool = coins[:top]
    if not pool:
        return []

    if profile == "stable":
        pool.sort(key=lambda c: c["range_pct"])
    elif profile in ("aggressive", "scalper"):
        pool.sort(key=lambda c: c["range_pct"], reverse=True)
    else:  # balanced — 변동폭 중앙값에 가까운 순
        ordered = sorted(c["range_pct"] for c in pool)
        median = ordered[len(ordered) // 2]
        pool.sort(key=lambda c: abs(c["range_pct"] - median))
    return pool[: max(1, size)]
