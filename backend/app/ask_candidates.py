"""물어볼까 v2 — 종목 후보를 고른다.

법적 설계를 코드로 강제한다:
* AI 가 고를 수 있는 선택지는 ``build_pool`` 이 만든 목록뿐이다(풀 밖 심볼은 버린다).
* 순위·변동폭·등락률 같은 숫자는 전부 서버가 계산해 붙인다. AI 는 문장만 쓴다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보.
* AI 가 죽어도 규칙만으로 후보가 나온다.
"""
from __future__ import annotations

import json
import math
import re
from typing import Callable, Optional

from .hotcoins import MIN_QUOTE_VOLUME, select_hot_coins
from .exchanges import is_domestic, normalize_exchange, quote_currency

POOL_SIZE = 10
# 거래대금 상위 몇 개 안에서 고를지 — 유동성 없는 코인이 후보에 오르지 않게 한다.
LIQUID_TOP = 40
MIN_KRW_QUOTE_VOLUME = 1_000_000_000  # Separate KRW liquidity floor; never compare it with USDT.
_STABLE_BASES = frozenset({"USDT", "USDC", "TUSD", "DAI", "USDD", "FDUSD", "BUSD", "USDP", "USD1"})


def _krw_coins(tickers: list[dict]) -> list[dict]:
    coins = []
    for row in tickers:
        symbol = str(row.get("symbol", ""))
        if not re.fullmatch(r"KRW-[A-Z0-9]{1,20}", symbol) or symbol[4:] in _STABLE_BASES:
            continue
        try:
            volume, change, price, high, low = (float(row[key]) for key in
                ("quoteVolume", "priceChangePercent", "lastPrice", "highPrice", "lowPrice"))
        except (KeyError, ValueError, TypeError):
            continue
        if not all(math.isfinite(v) for v in (volume, change, price, high, low)):
            continue
        if volume < MIN_KRW_QUOTE_VOLUME or price <= 0 or low <= 0 or high < low:
            continue
        coins.append({"symbol": symbol, "base": symbol[4:], "quote_currency": "KRW",
                      "change_pct": round(change, 2), "last_price": price,
                      "quote_volume": round(volume, 2), "range_pct": round((high - low) / low * 100, 2)})
    return coins


def headroom_pct(coin: dict) -> float:
    """왕복 여지 — 하루 변동폭에서 한 방향으로 간 몫을 뺀 나머지(%).

    왜 이걸로 고르나: 변동폭만 보면 "한 방향으로 크게 간 것" 이 1등으로 올라오는데,
    급등주는 홀딩이 이긴다(측정: 매크로 +32.89% vs 홀딩 +91.41%, 통과 0장). 오르고
    내리기를 되풀이한 종목에서만 매크로가 홀딩을 넘을 여지가 생긴다.

    한계: 두 숫자 모두 거래소 티커의 24시간 값이라 백테스트 구간(1주~1년)과 시간 축이
    다르다. 워크포워드에서 이 지표가 실제로 카드 산출률을 올리는지 확인한다.
    """
    try:
        return float(coin.get("range_pct") or 0.0) - abs(float(coin.get("change_pct") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def build_pool(tickers: list[dict], *, profile: str, size: int = POOL_SIZE, exchange: str = "binance") -> list[dict]:
    """거래가 활발하고 왕복 여지가 큰 순서의 후보 풀. AI 는 이 목록 밖으로 나갈 수 없다.

    ``profile`` 은 정렬에 쓰지 않는다(2026-10-12) — 문구(fallback_reason·build_prompt)에만
    쓴다. 성향은 '어떤 종목' 이 아니라 '얼마나 흔들려도 되는가' 로 갈린다(ask.mdd_cap·score).
    """
    exchange = normalize_exchange(exchange)
    coins = _krw_coins(tickers) if is_domestic(exchange) else select_hot_coins(
        tickers, limit=500, min_quote_volume=MIN_QUOTE_VOLUME, candidate_pool=500)
    coins.sort(key=lambda c: c["quote_volume"], reverse=True)
    for rank, coin in enumerate(coins, start=1):
        coin["volume_rank"] = rank
        coin["exchange"] = exchange
        coin["quote_currency"] = quote_currency(exchange)

    pool = coins[:LIQUID_TOP]
    if not pool:
        return []

    # 2026-10-12: 성향별 정렬을 없앴다. 세 성향이 같은 목록을 본다 — 저변동 대형주도
    # 급등주도 홀딩이 이기는 자산이라, 거기서 "홀딩을 넘어라" 를 요구하는 것은 모순이었다.
    pool.sort(key=headroom_pct, reverse=True)
    return pool[: max(1, size)]


MIN_PICKS = 3
MAX_PICKS = 4
REASON_MAX = 80

# 이유 문구에 들어가면 안 되는 말 — 들어가면 그 이유만 규칙 문구로 바꾼다.
BANNED_WORDS = ("추천", "보장", "확실", "무조건", "급등", "필승", "몰빵")
_PROFIT_PROMISE = re.compile(r"\d+\s*%.*(수익|상승|오름|벌)")
# AI 가 지어낸 '통계' 를 막는다 — 화면의 숫자는 서버가 계산해 붙인 줄에만 있어야 한다.
# 퍼센트 기호, 또는 숫자 바로 뒤의 순위·배수·화폐 단위. '20일선' 같은 멀쩡한 숫자는 통과시킨다.
_FAKE_STAT = re.compile(r"[%％]|\d\s*(?:위|배|원|달러|퍼센트|프로|억)")


def fallback_reason(profile: str, coin: dict) -> str:
    """AI 없이 서버 숫자만으로 쓰는 한 줄 — 코인마다 달라야 화면이 고장 난 것처럼 보이지 않는다.

    성향은 이미 풀 정렬에 반영돼 있으므로, 문구는 그 코인의 거래대금 순위와
    하루 변동폭을 구간으로 나눠 고른다. 숫자 자체는 문장에 넣지 않는다(서버가 따로 붙인다).
    """
    try:
        rank = int(coin.get("volume_rank") or 0)
    except (TypeError, ValueError):
        rank = 0
    try:
        range_pct = float(coin.get("range_pct") or 0.0)
    except (TypeError, ValueError):
        range_pct = 0.0

    if 0 < rank <= 3:
        traded = "거래가 가장 활발한 편이라 사고팔 때 잘 밀리지 않아요"
    elif 0 < rank <= 10:
        traded = "거래가 활발한 편이라 사고팔기 어렵지 않아요"
    else:
        traded = "거래대금 상위권이라 유동성은 갖춘 편이에요"

    if range_pct < 3.0:
        moves = "하루 움직임이 작아 느긋하게 볼 수 있어요"
    elif range_pct < 8.0:
        moves = "하루 움직임이 지나치지 않은 편이에요"
    else:
        moves = "하루 움직임이 커서 신호가 자주 나오는 편이에요"
    return f"{traded} · {moves}"


def _clean_reason(text: object, profile: str, coin: dict) -> str:
    reason = str(text or "").strip().replace("\n", " ")
    if not reason:
        return fallback_reason(profile, coin)
    if any(word in reason for word in BANNED_WORDS) or _PROFIT_PROMISE.search(reason):
        return fallback_reason(profile, coin)
    if _FAKE_STAT.search(reason):
        return fallback_reason(profile, coin)
    return reason[:REASON_MAX]


def _view(coin: dict, reason: str) -> dict:
    return {"symbol": coin["symbol"], "base": coin["base"], "reason": reason,
            "exchange": coin.get("exchange", "binance"), "quote_currency": coin.get("quote_currency", "USDT"),
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
_WATCH_WORDS = {"rarely": "거의 못 봐요", "sometimes": "가끔 봐요", "often": "수시로 봐요",
                "constantly": "아주 자주 봐요"}


def build_prompt(pool: list[dict], *, profile: str, horizon: str, watch: str,
                 exchange: str = "binance", account_balance: float | None = None) -> str:
    lines = [
        f"- {c['symbol']} · 거래대금 {c['volume_rank']}위 · 하루 변동폭 {c['range_pct']}% "
        f"· 24시간 {c['change_pct']:+}%"
        for c in pool
    ]
    budget = f" · 사용자 입력 전략 예산: {account_balance!r}" if account_balance is not None else ""
    return (
        "아래 목록에서만 골라 주세요. 목록에 없는 종목은 절대 쓰지 마세요.\n"
        f"거래소: {exchange} · 거래 통화: {quote_currency(exchange)}{budget}\n"
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
           ask_ai: Optional[Callable[[str], str]] = None,
           exchange: str = "binance", account_balance: float | None = None) -> tuple[list[dict], bool]:
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
                pool, profile=profile, horizon=horizon, watch=watch,
                exchange=exchange, account_balance=account_balance)))
            picks = validate_picks(parsed, pool, profile)
            if _matched_symbols(parsed, pool):
                return picks, True
        except Exception:
            pass
    return validate_picks([], pool, profile), False
