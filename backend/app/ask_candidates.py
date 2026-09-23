"""물어볼까 v2 — 종목 후보를 고른다.

법적 설계를 코드로 강제한다:
* AI 가 고를 수 있는 선택지는 ``build_pool`` 이 만든 목록뿐이다(풀 밖 심볼은 버린다).
* 순위·변동폭·등락률 같은 숫자는 전부 서버가 계산해 붙인다. AI 는 문장만 쓴다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보.
* AI 가 죽어도 규칙만으로 후보가 나온다.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

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


MIN_PICKS = 3
MAX_PICKS = 4
REASON_MAX = 80

# 이유 문구에 들어가면 안 되는 말 — 들어가면 그 이유만 규칙 문구로 바꾼다.
BANNED_WORDS = ("추천", "보장", "확실", "무조건", "급등", "필승", "몰빵")
_PROFIT_PROMISE = re.compile(r"\d+\s*%.*(수익|상승|오름|벌)")

_FALLBACK = {
    "stable": "거래가 가장 활발하고 하루 변동이 작은 편이에요.",
    "balanced": "거래가 활발하면서 하루 변동도 지나치지 않은 편이에요.",
    "aggressive": "거래가 활발하고 하루 변동이 큰 편이에요.",
    "scalper": "거래가 가장 활발해서 짧은 봉에서도 사고팔기 쉬운 편이에요.",
}


def fallback_reason(profile: str, coin: dict) -> str:
    return _FALLBACK.get(profile, _FALLBACK["balanced"])


def _clean_reason(text: object, profile: str, coin: dict) -> str:
    reason = str(text or "").strip().replace("\n", " ")
    if not reason:
        return fallback_reason(profile, coin)
    if any(word in reason for word in BANNED_WORDS) or _PROFIT_PROMISE.search(reason):
        return fallback_reason(profile, coin)
    return reason[:REASON_MAX]


def _view(coin: dict, reason: str) -> dict:
    return {"symbol": coin["symbol"], "base": coin["base"], "reason": reason,
            "volume_rank": coin["volume_rank"], "range_pct": coin["range_pct"],
            "change_pct": coin["change_pct"]}


def validate_picks(raw: object, pool: list[dict], profile: str) -> list[dict]:
    """AI 응답을 풀과 대조해 걸러낸다. 모자라면 풀 순서로 채우고, 넘치면 자른다."""
    by_symbol = {c["symbol"]: c for c in pool}
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        coin = by_symbol.get(symbol)
        if coin is None or symbol in seen:
            continue
        seen.add(symbol)
        out.append(_view(coin, _clean_reason(item.get("reason"), profile, coin)))
        if len(out) >= MAX_PICKS:
            break
    for coin in pool:
        if len(out) >= MIN_PICKS:
            break
        if coin["symbol"] in seen:
            continue
        seen.add(coin["symbol"])
        out.append(_view(coin, fallback_reason(profile, coin)))
    return out[:MAX_PICKS]


_HORIZON_WORDS = {"days": "며칠", "weeks": "몇 주", "months": "몇 달", "long": "길게"}
_WATCH_WORDS = {"rarely": "거의 못 봐요", "sometimes": "가끔 봐요", "often": "수시로 봐요"}


def build_prompt(pool: list[dict], *, profile: str, horizon: str, watch: str) -> str:
    lines = [
        f"- {c['symbol']} · 거래대금 {c['volume_rank']}위 · 하루 변동폭 {c['range_pct']}% "
        f"· 24시간 {c['change_pct']:+}%"
        for c in pool
    ]
    return (
        "아래 목록에서만 골라 주세요. 목록에 없는 종목은 절대 쓰지 마세요.\n"
        f"투자 성향: {profile} · 투자 기간: {_HORIZON_WORDS.get(horizon, horizon)} "
        f"· 시세를 보는 빈도: {_WATCH_WORDS.get(watch, watch)}\n\n"
        + "\n".join(lines)
        + f"\n\n이 사람에게 맞는 후보 {MIN_PICKS}~{MAX_PICKS}개를 고르고, 각각 왜 맞는지 "
          f"한 문장({REASON_MAX}자 이내)으로 써 주세요. 수익률이나 가격을 예측하지 말고, "
          "거래가 활발한 정도와 변동 폭이 이 사람에게 어떤 의미인지만 설명하세요.\n"
          '형식: [{"symbol": "...", "reason": "..."}] 만 출력하세요.'
    )


def _parse(text: str) -> object:
    body = str(text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no json array")
    return json.loads(body[start : end + 1])


def _matched_symbols(raw: object, pool: list[dict]) -> set[str]:
    """raw 안에서 풀에 실제로 있는 심볼만 골라낸다 — ai_used 판정에 쓴다."""
    pool_symbols = {c["symbol"] for c in pool}
    matched: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol in pool_symbols:
            matched.add(symbol)
    return matched


def choose(pool: list[dict], *, profile: str, horizon: str, watch: str,
           ask_ai: Optional[Callable[[str], str]] = None) -> tuple[list[dict], bool]:
    """후보와 ai_used 를 돌려준다. AI 가 죽거나 이상한 답을 하면 규칙만으로 채운다.

    ai_used 는 "AI 가 고른 것 중 하나라도 검증을 통과해 결과에 남았는가" 를 뜻한다.
    AI 응답이 JSON 으로는 파싱돼도 전부 풀 밖 심볼이라 하나도 못 살아남았다면,
    결과는 규칙 폴백과 똑같으므로 ai_used 는 False 여야 한다.
    """
    if not pool:
        return [], False
    if ask_ai is not None:
        try:
            parsed = _parse(ask_ai(build_prompt(
                pool, profile=profile, horizon=horizon, watch=watch)))
            picks = validate_picks(parsed, pool, profile)
            if _matched_symbols(parsed, pool):
                return picks, True
        except Exception:
            pass
    return validate_picks([], pool, profile), False
