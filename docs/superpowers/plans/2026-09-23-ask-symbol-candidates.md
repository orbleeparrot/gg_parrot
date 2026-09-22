# 껄무새에게 물어볼까 v2 — 종목 후보 제시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 카드에서 성향·시장·투자 기간·볼 빈도를 받아 종목 후보 3~4개를 이유와 함께 보여 주고, 하나를 고르면 매크로 후보 3개가 나오게 한다. 한 흐름 전체가 하루 횟수 1회.

**Architecture:** 후보 선정은 하이브리드다 — 바이낸스 24시간 티커(이미 캐시됨)로 규칙 풀을 만들고, 그 풀 안에서만 AI 가 3~4개를 골라 한 줄 이유를 쓴다. 풀 밖 심볼·금지어·개수는 서버가 강제로 걸러내고, AI 가 죽으면 규칙만으로 폴백한다. 차감은 후보를 내는 `POST /api/ask/candidates` 에서만 일어나고, 종목을 고르는 `POST /api/ask` 는 세션을 읽어 쓰므로 차감이 없다.

**Tech Stack:** FastAPI · SQLModel · pydantic · pytest (백엔드) / React · 순수 리듀서 · `node --test` (프론트)

**Spec:** [docs/superpowers/specs/2026-09-23-ask-symbol-candidates-design.md](../specs/2026-09-23-ask-symbol-candidates-design.md)

## Global Constraints

- **"추천" 이라는 낱말을 쓰지 않는다.** 화면·API·프롬프트·로그·테스트 이름 전부 "후보". ([askCopy.js:1](../../../frontend/src/lib/askCopy.js) 의 기존 규칙)
- **숫자는 AI 가 만들지 않는다.** 거래대금 순위·변동폭·등락률은 서버가 계산해 붙이고, AI 는 문장만 쓴다.
- **AI 는 규칙 풀 안에서만 고른다.** 풀에 없는 심볼은 서버가 버린다.
- **AI 가 죽어도 흐름은 멈추지 않는다.** 모든 AI 경로에 규칙 폴백이 있어야 한다.
- 고지 버전은 `DISCLAIMER_VERSION = "ask-v2"`.
- 구간(period)의 `1m` 은 **1개월**, 봉(interval)의 `1m` 은 **1분** 이다. 기존 코드 표기를 그대로 쓴다.
- 커밋은 노희재 이름으로 한다:
  `git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit`
  메시지는 한국어, 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 백엔드 테스트: `cd backend && .venv/Scripts/python -m pytest tests -q -p no:warnings`
  (기존 실패 2건이 `test_agent_collector_runner` 에 있고 수집 오류 6건이 있다 — 내 변경과 무관하면 그대로 둔다)
- 프론트 테스트: `cd frontend && node --test "tests/*.test.js"`
- 프론트 빌드 확인: `cd frontend && npx vite build --logLevel error`

---

### Task 1: 티커에 변동폭 붙이고 캐시된 티커를 꺼낼 수 있게 하기

후보 풀은 "하루에 얼마나 출렁이는가"가 필요한데, 지금 `select_hot_coins` 는 등락률만 남긴다. 같은 24시간 티커 응답에 `highPrice`/`lowPrice` 가 이미 들어 있으므로 **추가 API 호출 없이** 변동폭을 계산할 수 있다. 후보 모듈이 같은 캐시를 재사용하도록 원본 티커를 꺼내는 함수도 연다.

**Files:**
- Modify: `backend/app/hotcoins.py`
- Test: `backend/tests/test_hotcoins.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `hotcoins.ticker_range_pct(t: dict) -> float` — `(high - low) / low * 100`, 값이 없거나 이상하면 `0.0`
  - `select_hot_coins(...)` 결과 각 항목에 `"range_pct": float` 추가
  - `hotcoins.get_cached_tickers() -> list[dict] | None` — 캐시된 원본 24시간 티커 목록(없으면 `None`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_hotcoins.py` 끝에 추가한다. 기존 헬퍼 `_t` 는 high/low 가 없으므로, 없을 때 `0.0` 이 되는 것도 함께 확인한다.

```python
def _t2(symbol, change, price, qvol, high, low):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(price),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}


def test_range_pct_from_high_low():
    assert hc.ticker_range_pct(_t2("BTCUSDT", 1.0, 100, 1, 110, 100)) == 10.0
    # high/low 가 없거나 0 이면 0.0 (옛 응답·이상값 방어)
    assert hc.ticker_range_pct(_t("BTCUSDT", 1.0, 100, 1)) == 0.0
    assert hc.ticker_range_pct(_t2("BTCUSDT", 1.0, 100, 1, 110, 0)) == 0.0


def test_selection_carries_range_pct():
    coins = hc.select_hot_coins(
        [_t2("BTCUSDT", 1.0, 100, 1_000_000_000, 105, 100)],
        limit=10, min_quote_volume=10_000_000, candidate_pool=100)
    assert coins[0]["range_pct"] == 5.0


def test_get_cached_tickers_returns_none_when_load_fails(monkeypatch):
    # 캐시 자체를 비우지 않고, 캐시가 부를 로더를 실패하게 만들어 확인한다.
    monkeypatch.setattr(hc, "_load_ticker_payload", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(hc._cache, "get_or_load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert hc.get_cached_tickers() is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_hotcoins.py -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'app.hotcoins' has no attribute 'ticker_range_pct'`

- [ ] **Step 3: 최소 구현**

`backend/app/hotcoins.py` 에 추가한다.

```python
def ticker_range_pct(t: dict) -> float:
    """24시간 고가/저가로 본 하루 변동폭(%). 값이 없거나 이상하면 0.0 — 같은 티커 응답만 쓴다."""
    try:
        high = float(t["highPrice"])
        low = float(t["lowPrice"])
    except (KeyError, ValueError, TypeError):
        return 0.0
    if low <= 0 or high < low:
        return 0.0
    return round((high - low) / low * 100.0, 2)
```

`select_hot_coins` 의 `candidates.append({...})` 에 한 줄을 넣는다.

```python
                "quote_volume": round(quote_volume, 2),
                "range_pct": ticker_range_pct(t),
```

그리고 캐시된 원본 티커를 여는 함수를 추가한다. `get_hot_coins` 의 `load()` 가 `coins` 만 저장하므로, 원본도 같이 저장하도록 바꾼다.

```python
def _load_ticker_payload() -> dict:
    tickers = _fetch_tickers()
    if not tickers:
        raise RuntimeError("hot coin source unavailable")
    return {"tickers": tickers,
            "coins": select_hot_coins(tickers, limit=50),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def get_cached_tickers() -> Optional[list[dict]]:
    """캐시된 24시간 티커 원본. 후보 풀이 같은 캐시를 재사용하려고 쓴다(추가 호출 없음)."""
    try:
        payload, _state = _cache.get_or_load("binance:24h", _load_ticker_payload,
                                             ttl=CACHE_SECONDS, stale_ttl=300)
    except Exception:
        return None
    tickers = payload.get("tickers")
    return tickers if isinstance(tickers, list) else None
```

`get_hot_coins` 안의 `def load(): ...` 를 지우고 `_load_ticker_payload` 를 쓰게 바꾼다.

```python
    try:
        payload, state = _cache.get_or_load("binance:24h", _load_ticker_payload,
                                            ttl=CACHE_SECONDS, stale_ttl=300)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_hotcoins.py -q -p no:warnings`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/hotcoins.py backend/tests/test_hotcoins.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
핫코인 티커에 하루 변동폭 추가 — 후보 풀이 같은 캐시를 재사용하도록 원본 티커 노출

24시간 티커 응답에 이미 있는 고가·저가로 range_pct 를 계산한다(추가 호출 없음).
값이 없는 옛 응답은 0.0 으로 둔다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 성향별 후보 풀 만들기 (규칙만, AI 없음)

AI 가 고를 **선택지 자체**를 규칙으로 좁힌다. 이 함수가 법적 방어의 핵심이다 — AI 는 여기서 나온 목록 밖으로 나갈 수 없다.

**Files:**
- Create: `backend/app/ask_candidates.py`
- Test: `backend/tests/test_ask_candidates.py` (새 파일)

**Interfaces:**
- Consumes: `hotcoins.select_hot_coins`, `hotcoins.ticker_range_pct` (Task 1)
- Produces:
  - `ask_candidates.POOL_SIZE = 10`
  - `ask_candidates.build_pool(tickers: list[dict], *, profile: str, size: int = POOL_SIZE) -> list[dict]`
    항목: `{"symbol", "base", "change_pct", "last_price", "quote_volume", "range_pct", "volume_rank"}`
    `volume_rank` 는 거래대금 내림차순 1-based 순위.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask_candidates.py` 를 만든다.

```python
"""물어볼까 v2 — 종목 후보 풀과 AI 선별 검증."""
from __future__ import annotations

from app import ask_candidates as ac


def _t(symbol, qvol, high, low, change=1.0):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(low),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}


def _tickers():
    # 변동폭: CALM 1% · MID 5% · WILD 20%, 거래대금은 CALM > MID > WILD
    return [
        _t("CALMUSDT", 900_000_000, 101, 100),
        _t("MIDUSDT", 800_000_000, 105, 100),
        _t("WILDUSDT", 700_000_000, 120, 100),
        _t("USDCUSDT", 950_000_000, 101, 100),      # 스테이블 → 제외
        _t("BTCUPUSDT", 850_000_000, 130, 100),     # 레버리지 토큰 → 제외
        _t("TINYUSDT", 5_000, 200, 100),            # 거래대금 미달 → 제외
    ]


def test_pool_drops_stable_leverage_and_illiquid():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    symbols = [c["symbol"] for c in pool]
    assert "USDCUSDT" not in symbols
    assert "BTCUPUSDT" not in symbols
    assert "TINYUSDT" not in symbols


def test_pool_carries_volume_rank_by_quote_volume():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    ranks = {c["symbol"]: c["volume_rank"] for c in pool}
    assert ranks["CALMUSDT"] == 1
    assert ranks["MIDUSDT"] == 2
    assert ranks["WILDUSDT"] == 3


def test_stable_profile_prefers_calm_coins():
    pool = ac.build_pool(_tickers(), profile="stable", size=3)
    assert pool[0]["symbol"] == "CALMUSDT"


def test_aggressive_profile_prefers_wild_coins():
    pool = ac.build_pool(_tickers(), profile="aggressive", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_balanced_profile_prefers_middle_volatility():
    pool = ac.build_pool(_tickers(), profile="balanced", size=3)
    assert pool[0]["symbol"] == "MIDUSDT"


def test_scalper_profile_prefers_wild_among_most_traded():
    pool = ac.build_pool(_tickers(), profile="scalper", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_pool_size_is_capped():
    assert len(ac.build_pool(_tickers(), profile="balanced", size=2)) == 2
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ask_candidates'`

- [ ] **Step 3: 최소 구현**

`backend/app/ask_candidates.py` 를 만든다.

```python
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
```

`select_hot_coins` 는 등락률 내림차순으로 돌려주지만 `build_pool` 이 거래대금으로 다시 정렬하므로 순서는 여기서 결정된다. `limit`/`candidate_pool` 을 크게 줘서 필터링만 재사용한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py -q -p no:warnings`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/ask_candidates.py backend/tests/test_ask_candidates.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 성향별 종목 후보 풀(규칙만)

거래대금으로 유동성 있는 코인을 추린 뒤 성향에 따라 변동폭 오름차순(안정형)·
내림차순(공격형·단타형)·중앙값 근처(균형형)로 정렬한다. 스테이블·레버리지
토큰·거래대금 미달은 기존 핫코인 필터를 그대로 재사용해 걸러낸다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: AI 선별과 서버 검증·폴백

AI 에게 풀을 주고 3~4개를 고르게 하되, 돌아온 것을 전부 검증한다. 이 태스크가 "AI 가 지어낸 종목이 화면에 뜨지 않는다"를 보장한다.

**Files:**
- Modify: `backend/app/ask_candidates.py`
- Test: `backend/tests/test_ask_candidates.py`

**Interfaces:**
- Consumes: `build_pool` (Task 2)
- Produces:
  - `ask_candidates.MIN_PICKS = 3`, `MAX_PICKS = 4`, `REASON_MAX = 80`
  - `ask_candidates.BANNED_WORDS: tuple[str, ...]`
  - `ask_candidates.fallback_reason(profile: str, coin: dict) -> str`
  - `ask_candidates.validate_picks(raw: object, pool: list[dict], profile: str) -> list[dict]`
    항목: `{"symbol", "base", "reason", "volume_rank", "range_pct", "change_pct"}`
  - `ask_candidates.choose(pool, *, profile, horizon, watch, ask_ai=None) -> tuple[list[dict], bool]`
    `ask_ai` 는 `Callable[[str], str]` (프롬프트 → 원문). `None` 이면 규칙 폴백. 두 번째 값은 `ai_used`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask_candidates.py` 에 추가한다.

```python
import json


def _pool():
    return ac.build_pool(_tickers(), profile="balanced", size=10)


def _ai(payload):
    return lambda prompt: json.dumps(payload, ensure_ascii=False)


def test_picks_outside_pool_are_dropped():
    picks = ac.validate_picks(
        [{"symbol": "SCAMUSDT", "reason": "좋아 보여요"},
         {"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
        _pool(), "balanced")
    symbols = [p["symbol"] for p in picks]
    assert "SCAMUSDT" not in symbols
    assert "MIDUSDT" in symbols


def test_banned_words_are_replaced_with_fallback_reason():
    picks = ac.validate_picks(
        [{"symbol": "MIDUSDT", "reason": "무조건 오르는 종목이라 수익을 보장해요"}],
        _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert "보장" not in reason and "무조건" not in reason
    assert reason == ac.fallback_reason("balanced", next(c for c in _pool() if c["symbol"] == "MIDUSDT"))


def test_short_ai_answer_is_topped_up_from_pool_order():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
                              _pool(), "balanced")
    assert len(picks) >= ac.MIN_PICKS


def test_too_many_picks_are_cut():
    raw = [{"symbol": c["symbol"], "reason": "거래가 활발해요"} for c in _pool()]
    assert len(ac.validate_picks(raw, _pool(), "balanced")) == ac.MAX_PICKS


def test_long_reason_is_trimmed():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "가" * 200}], _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert len(reason) <= ac.REASON_MAX


def test_duplicate_symbols_are_collapsed():
    raw = [{"symbol": "MIDUSDT", "reason": "하나"}, {"symbol": "MIDUSDT", "reason": "둘"}]
    picks = ac.validate_picks(raw, _pool(), "balanced")
    assert [p["symbol"] for p in picks].count("MIDUSDT") == 1


def test_picks_carry_server_computed_numbers():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
                              _pool(), "balanced")
    mid = next(p for p in picks if p["symbol"] == "MIDUSDT")
    assert mid["range_pct"] == 5.0
    assert mid["volume_rank"] == 2
    assert mid["base"] == "MID"


def test_choose_uses_ai_when_it_answers():
    picks, ai_used = ac.choose(
        _pool(), profile="balanced", horizon="weeks", watch="sometimes",
        ask_ai=_ai([{"symbol": "WILDUSDT", "reason": "변동이 커서 신호가 자주 나와요"},
                    {"symbol": "MIDUSDT", "reason": "거래가 활발해요"},
                    {"symbol": "CALMUSDT", "reason": "하루 변동이 작아요"}]))
    assert ai_used is True
    assert [p["symbol"] for p in picks][:3] == ["WILDUSDT", "MIDUSDT", "CALMUSDT"]


def test_choose_falls_back_when_ai_raises():
    def boom(prompt):
        raise RuntimeError("gemini down")

    picks, ai_used = ac.choose(_pool(), profile="stable", horizon="months",
                               watch="rarely", ask_ai=boom)
    assert ai_used is False
    assert len(picks) >= ac.MIN_PICKS
    assert all(p["reason"] for p in picks)


def test_choose_falls_back_when_ai_returns_garbage():
    picks, ai_used = ac.choose(_pool(), profile="stable", horizon="months",
                               watch="rarely", ask_ai=lambda p: "not json at all")
    assert ai_used is False
    assert len(picks) >= ac.MIN_PICKS


def test_prompt_never_says_recommend():
    prompt = ac.build_prompt(_pool(), profile="balanced", horizon="weeks", watch="sometimes")
    assert "추천" not in prompt
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'app.ask_candidates' has no attribute 'validate_picks'`

- [ ] **Step 3: 최소 구현**

`backend/app/ask_candidates.py` 에 이어 붙인다.

```python
import json
import re
from typing import Callable, Optional

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


def choose(pool: list[dict], *, profile: str, horizon: str, watch: str,
           ask_ai: Optional[Callable[[str], str]] = None) -> tuple[list[dict], bool]:
    """후보와 ai_used 를 돌려준다. AI 가 죽거나 이상한 답을 하면 규칙만으로 채운다."""
    if not pool:
        return [], False
    if ask_ai is not None:
        try:
            picks = validate_picks(_parse(ask_ai(build_prompt(
                pool, profile=profile, horizon=horizon, watch=watch))), pool, profile)
            if picks:
                return picks, True
        except Exception:
            pass
    return validate_picks([], pool, profile), False
```

`test_choose_uses_ai_when_it_answers` 가 순서를 확인하므로 `validate_picks` 는 AI 가 준 순서를 지켜야 한다 — 위 구현이 그렇다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py -q -p no:warnings`
Expected: PASS (19 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/ask_candidates.py backend/tests/test_ask_candidates.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — AI 후보 선별과 서버 검증·폴백

AI 응답에서 풀 밖 심볼·중복을 버리고, 금지어나 수익 약속이 섞인 이유는 규칙
문구로 바꾼다. 모자라면 풀 순서로 채우고 넘치면 자른다. AI 가 죽거나 JSON 이
아닌 답을 하면 규칙만으로 후보를 만든다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 세션 컬럼 4개와 마이그레이션

`AskMacroSession` 은 지금 "끝난 질문 한 줄"인데, v2 에서는 후보를 낼 때 먼저 만들어 두고 나중에 결과를 채우는 **흐름 세션**이 된다.

**Files:**
- Modify: `backend/app/db.py` — 모델 `AskMacroSession` (652행 근처), `_migrate()` 의 `"askmacrosession"` 항목 (1107행 근처), PG 컬럼 표 (1248행 근처)
- Create: `supabase/migrations/20260923120000_ask_flow_session.sql`
- Test: `backend/tests/test_ask_candidates.py`

**Interfaces:**
- Consumes: 없음
- Produces: `AskMacroSession.candidates_json: str = "[]"`, `.chosen_symbol: str = ""`, `.expires_ms: int = 0`, `.ask_count: int = 0`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask_candidates.py` 에 추가한다.

```python
from app.db import AskMacroSession, get_session


def test_session_has_flow_columns():
    with get_session() as db:
        row = AskMacroSession(
            user_id=1, day_kst="2026-09-23", request_json="{}", candidate_count=0,
            results_json="[]", disclaimer_version="ask-v2", ai_used=False, elapsed_ms=0,
            created_at="2026-09-23T00:00:00Z", created_ms=1,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.candidates_json == "[]"
        assert row.chosen_symbol == ""
        assert row.expires_ms == 0
        assert row.ask_count == 0
        db.delete(row)
        db.commit()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py::test_session_has_flow_columns -q -p no:warnings`
Expected: FAIL — `AttributeError: 'AskMacroSession' object has no attribute 'candidates_json'`

- [ ] **Step 3: 최소 구현**

`backend/app/db.py` 의 `AskMacroSession` 끝(`paid: bool = False` 아래)에 붙인다.

```python
    # v2(2026-09-23) — 흐름 세션: 후보를 낼 때 행을 먼저 만들고 종목 선택 뒤 결과를 채운다.
    candidates_json: str = "[]"  # 보여 준 후보와 이유(문의 대응·감사)
    chosen_symbol: str = ""      # 사용자가 고른 종목
    expires_ms: int = Field(default=0, sa_type=BigInteger)  # 세션 유효 시한
    ask_count: int = 0           # 이 세션으로 매크로를 만든 횟수(상한)
```

`_migrate()` 의 `"askmacrosession"` 항목을 채운다.

```python
        "askmacrosession": {
            "paid": "ALTER TABLE askmacrosession ADD COLUMN paid BOOLEAN NOT NULL DEFAULT FALSE",
            "candidates_json": "ALTER TABLE askmacrosession ADD COLUMN candidates_json TEXT NOT NULL DEFAULT '[]'",
            "chosen_symbol": "ALTER TABLE askmacrosession ADD COLUMN chosen_symbol TEXT NOT NULL DEFAULT ''",
            "expires_ms": "ALTER TABLE askmacrosession ADD COLUMN expires_ms BIGINT NOT NULL DEFAULT 0",
            "ask_count": "ALTER TABLE askmacrosession ADD COLUMN ask_count INTEGER NOT NULL DEFAULT 0",
        },
```

PG 컬럼 표(1248행)도 맞춘다.

```python
    "askmacrosession": {
        "paid": "BOOLEAN NOT NULL DEFAULT FALSE",
        "candidates_json": "TEXT NOT NULL DEFAULT '[]'",
        "chosen_symbol": "TEXT NOT NULL DEFAULT ''",
        "expires_ms": "BIGINT NOT NULL DEFAULT 0",
        "ask_count": "INTEGER NOT NULL DEFAULT 0",
    },
```

`supabase/migrations/20260923120000_ask_flow_session.sql` 를 만든다.

```sql
-- 물어볼까 v2 — 흐름 세션 컬럼. 서버가 기동 때 자동으로도 적용하지만 기록을 남긴다.
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS candidates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS chosen_symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS expires_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS ask_count INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask_candidates.py tests/test_ask.py -q -p no:warnings`
Expected: PASS (기존 `test_ask.py` 도 그대로 통과해야 한다)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/db.py backend/tests/test_ask_candidates.py supabase/migrations/20260923120000_ask_flow_session.sql
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 흐름 세션 컬럼 추가(후보 목록·고른 종목·만료·호출 수)

후보를 낼 때 세션 행을 먼저 만들고 종목 선택 뒤 결과를 채우는 구조로 바꾸기
위한 컬럼이다. 하루 한도는 이 행을 세는 기존 방식을 그대로 쓴다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 사람 답변 → 백테스트 구간·봉 변환

카드가 기술 값 대신 사람 말을 받으므로, 서버가 변환한다. 순수 함수라 테스트가 쉽다.

**Files:**
- Modify: `backend/app/ask.py`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `ask.HORIZONS = ("days", "weeks", "months", "long")`
  - `ask.WATCH_LEVELS = ("rarely", "sometimes", "often")`
  - `ask.to_period(profile: str, horizon: str) -> str` — `"1w"|"1m"|"3m"|"6m"|"1y"`
  - `ask.to_interval(profile: str, watch: str, period: str) -> str` — `"1m"|"5m"|"15m"|"1h"|"4h"|"1d"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask.py` 끝에 추가한다.

```python
def test_horizon_maps_to_backtest_period():
    assert ask.to_period("balanced", "days") == "1w"
    assert ask.to_period("balanced", "weeks") == "3m"
    assert ask.to_period("balanced", "months") == "6m"
    assert ask.to_period("balanced", "long") == "1y"


def test_scalper_period_is_clamped_to_short_presets():
    # 단타형은 짧은 구간만 — 1m 은 1개월이다(봉의 1분이 아니다)
    assert ask.to_period("scalper", "days") == "1w"
    assert ask.to_period("scalper", "weeks") == "1m"
    assert ask.to_period("scalper", "months") == "1m"
    assert ask.to_period("scalper", "long") == "1m"


def test_watch_maps_to_interval():
    assert ask.to_interval("balanced", "rarely", "6m") == "1d"
    assert ask.to_interval("balanced", "sometimes", "6m") == "4h"
    assert ask.to_interval("balanced", "often", "6m") == "1h"


def test_scalper_watch_maps_to_short_intervals():
    assert ask.to_interval("scalper", "rarely", "1m") == "15m"
    assert ask.to_interval("scalper", "sometimes", "1m") == "5m"
    # 1분 봉은 구간이 1주일 때만 — 아니면 5분으로 낮춘다
    assert ask.to_interval("scalper", "often", "1w") == "1m"
    assert ask.to_interval("scalper", "often", "1m") == "5m"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py -q -p no:warnings -k "horizon or watch or period"`
Expected: FAIL — `AttributeError: module 'app.ask' has no attribute 'to_period'`

- [ ] **Step 3: 최소 구현**

`backend/app/ask.py` 의 `PROFILES` 아래(`LONG_INTERVALS` 근처)에 넣는다.

```python
# v2(2026-09-23) — 카드가 사람 말을 받고 서버가 기술 값으로 바꾼다.
HORIZONS = ("days", "weeks", "months", "long")
WATCH_LEVELS = ("rarely", "sometimes", "often")

_HORIZON_PERIOD = {"days": "1w", "weeks": "3m", "months": "6m", "long": "1y"}
# 단타형은 짧은 구간만 — SHORT_PERIODS = ("1w", "1m"). 여기의 1m 은 1개월이다.
_HORIZON_PERIOD_SHORT = {"days": "1w", "weeks": "1m", "months": "1m", "long": "1m"}
_WATCH_INTERVAL = {"rarely": "1d", "sometimes": "4h", "often": "1h"}
_WATCH_INTERVAL_SHORT = {"rarely": "15m", "sometimes": "5m", "often": "1m"}


def to_period(profile: str, horizon: str) -> str:
    table = _HORIZON_PERIOD_SHORT if PROFILES[profile]["short"] else _HORIZON_PERIOD
    return table.get(horizon, table["weeks"])


def to_interval(profile: str, watch: str, period: str) -> str:
    if not PROFILES[profile]["short"]:
        return _WATCH_INTERVAL.get(watch, _WATCH_INTERVAL["sometimes"])
    interval = _WATCH_INTERVAL_SHORT.get(watch, _WATCH_INTERVAL_SHORT["sometimes"])
    # 1분 봉은 최근 1주 구간에서만 쓸 수 있다(백테스트 봉 상한).
    return "5m" if interval == "1m" and period != "1w" else interval
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py -q -p no:warnings -k "horizon or watch or period"`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/ask.py backend/tests/test_ask.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 투자 기간·보는 빈도를 백테스트 구간과 봉으로 변환

카드가 기술 값 대신 사람 말을 받으므로 서버가 바꾼다. 단타형은 짧은 구간으로
clamp 하고, 1분 봉은 구간이 1주일 때만 쓰고 아니면 5분으로 낮춘다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 후보 흐름 시작 — `run_candidates` (차감이 일어나는 유일한 곳)

**Files:**
- Modify: `backend/app/ask.py`, `backend/app/main.py`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Consumes: `ask_candidates.build_pool`/`choose` (Task 2·3), `AskMacroSession` 새 컬럼 (Task 4), `to_period`/`to_interval` (Task 5)
- Produces:
  - `ask.DISCLAIMER_VERSION = "ask-v2"`
  - `ask.SESSION_TTL_MS = 30 * 60 * 1000`
  - `ask.MAX_ASKS_PER_SESSION = 6`
  - `ask.MANUAL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")`
  - 테스트는 `from app import ask_candidates as ac` 로 `ac.MIN_PICKS`/`ac.MAX_PICKS` 를 읽는다
  - `ask.CandidatesRequest` (pydantic): `risk_profile`, `market`, `leverage`, `invest_horizon`, `watch_frequency`
  - `ask.run_candidates(db: Session, user: User, req: CandidatesRequest) -> dict`
    반환: `{"session_id", "candidates", "manual_symbols", "remaining_today", "disclaimer"}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask.py` 에 추가한다. 파일 상단 import 에 `from app import ask_candidates as ac` 를 더한다.

```python
def _consent(tok):
    client.post("/api/ask/consent", headers=_auth(tok))


def _candidates_body(**over):
    body = {"risk_profile": "balanced", "market": "spot", "leverage": 1,
            "invest_horizon": "weeks", "watch_frequency": "sometimes"}
    body.update(over)
    return body


def _fake_tickers():
    def t(symbol, qvol, high, low):
        return {"symbol": symbol, "priceChangePercent": "1.0", "lastPrice": str(low),
                "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}
    return [t("AAAUSDT", 900_000_000, 101, 100), t("BBBUSDT", 800_000_000, 105, 100),
            t("CCCUSDT", 700_000_000, 120, 100), t("DDDUSDT", 600_000_000, 110, 100)]


def test_candidates_consume_exactly_one_use(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)  # AI 없이 규칙 폴백
    tok, user_id = _signup()
    _consent(tok)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    body = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["session_id"]
    assert ac.MIN_PICKS <= len(data["candidates"]) <= ac.MAX_PICKS
    assert all(c["reason"] for c in data["candidates"])
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before - 1


def test_candidates_survive_ai_failure(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())

    def boom():
        def ask_ai(prompt):
            raise RuntimeError("gemini down")
        return ask_ai

    monkeypatch.setattr(ask, "_candidate_ai", boom)
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 200
    assert len(r.json()["candidates"]) >= 3


def test_candidates_record_pool_and_expiry(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, user_id = _signup()
    _consent(tok)
    sid = client.post("/api/ask/candidates", json=_candidates_body(),
                      headers=_auth(tok)).json()["session_id"]
    with get_session() as db:
        row = db.get(AskMacroSession, sid)
        assert row.disclaimer_version == "ask-v2"
        assert json.loads(row.candidates_json)
        assert row.expires_ms > row.created_ms
        assert row.ask_count == 0


def test_candidates_need_consent():
    tok, _ = _signup()
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 403


def test_candidates_reject_futures_for_stable_profile(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/candidates",
                    json=_candidates_body(risk_profile="stable", market="futures", leverage=2),
                    headers=_auth(tok))
    assert r.status_code == 422


def test_candidates_refund_when_source_is_down(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: None)
    tok, _ = _signup()
    _consent(tok)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 503
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py -q -p no:warnings -k candidates`
Expected: FAIL — `/api/ask/candidates` 가 404

- [ ] **Step 3: 최소 구현**

`backend/app/ask.py` 상단 import 에 더한다.

```python
from . import ask_candidates, hotcoins
from .ask_candidates import MAX_PICKS, MIN_PICKS
```

상수를 바꾸고 더한다.

```python
DISCLAIMER_VERSION = "ask-v2"
SESSION_TTL_MS = 30 * 60 * 1000
MAX_ASKS_PER_SESSION = 6
MANUAL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
```

요청 모델과 AI 훅, 본체를 더한다.

```python
class CandidatesRequest(BaseModel):
    risk_profile: RiskProfile
    market: Literal["spot", "futures"]
    leverage: int = Field(default=1, ge=1, le=3)
    invest_horizon: Literal["days", "weeks", "months", "long"]
    watch_frequency: Literal["rarely", "sometimes", "often"]

    @model_validator(mode="after")
    def _profile_rules(self) -> "CandidatesRequest":
        if self.market == "futures" and not PROFILES[self.risk_profile]["futures"]:
            raise ValueError(f"{PROFILES[self.risk_profile]['label']}은 현물만 살펴봐요")
        if self.market == "spot" and self.leverage != 1:
            raise ValueError("현물은 레버리지를 쓰지 않아요")
        return self


_CANDIDATE_PROMPT_VERSION = "ask-cand-v1"
_CANDIDATE_SYSTEM = (
    "너는 주어진 목록 안에서만 종목 후보를 고르는 도우미다. "
    "목록에 없는 종목은 절대 쓰지 마라. 가격이나 수익률을 예측하지 마라. "
    "JSON 배열만 출력해라."
)


def _candidate_ai() -> Optional[Callable[[str], str]]:
    """후보 선별에 쓸 AI 호출자. 쓸 수 없으면 None(규칙 폴백).

    기존 ``propose_with_ai`` 와 같은 경로를 쓴다 — 클라이언트는 messages.create,
    호출은 ``get_ai_runtime().call`` 로 감싸 캐시·재시도 정책을 공유한다.
    """
    if not ai_available():
        return None

    def ask_ai(prompt: str) -> str:
        key = ai_cache_key("ask-candidates", _CANDIDATE_PROMPT_VERSION, _AI_MODEL,
                           {"system": _CANDIDATE_SYSTEM, "prompt": prompt,
                            "max_tokens": _AI_MAX_TOKENS})

        def load():
            response = get_ai_client().messages.create(
                model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=_CANDIDATE_SYSTEM,
                messages=[{"role": "user", "content": prompt}], purpose="ask-candidates",
                timeout=float(os.environ.get("ASK_AI_TIMEOUT_SEC", "6")),
            )
            text = next((b.text for b in response.content
                         if getattr(b, "type", None) == "text"), None)
            if not text:
                raise ValueError("empty candidate response")
            return text

        return get_ai_runtime().call(key, load, retries=0)[0]

    return ask_ai


def run_candidates(db: Session, user: User, req: CandidatesRequest) -> dict:
    if not consented(user):
        raise AskError(403, "먼저 안내에 동의해 주세요.")
    if remaining_today(db, user) <= 0:
        raise AskError(429, f"오늘은 {daily_limit()}번 다 물어봤어요. 내일 다시 물어봐 주세요.")

    created_at, created_ms = _now()
    credit = None if free_remaining_today(db, user) > 0 else _unused_credit(db, user)
    # 한도 검사와 저장 사이의 창을 닫으려고 행을 먼저 커밋한다(v1 과 같은 이유).
    row = AskMacroSession(
        user_id=user.id, day_kst=today_kst(),
        request_json=json.dumps(req.model_dump(), ensure_ascii=False),
        candidate_count=0, results_json="[]",
        disclaimer_version=DISCLAIMER_VERSION, ai_used=False, elapsed_ms=0,
        created_at=created_at, created_ms=created_ms, paid=credit is not None,
        candidates_json="[]", chosen_symbol="", expires_ms=created_ms + SESSION_TTL_MS,
        ask_count=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if credit is not None:
        credit.used_session_id = row.id
        db.add(credit)
        db.commit()

    try:
        tickers = hotcoins.get_cached_tickers()
        if not tickers:
            raise AskError(503, "지금 시세 목록을 불러오지 못했어요. 잠시 뒤 다시 물어봐 주세요.")
        pool = ask_candidates.build_pool(tickers, profile=req.risk_profile)
        if not pool:
            raise AskError(503, "지금 살펴볼 종목을 찾지 못했어요. 잠시 뒤 다시 물어봐 주세요.")
        candidates, ai_used = ask_candidates.choose(
            pool, profile=req.risk_profile, horizon=req.invest_horizon,
            watch=req.watch_frequency, ask_ai=_candidate_ai())
    except Exception:
        # 후보를 못 냈으면 횟수를 돌려준다 — 행을 지우고 추가권은 다시 '안 씀'으로.
        if credit is not None:
            credit.used_session_id = None
            db.add(credit)
        db.delete(row)
        db.commit()
        raise

    row.candidates_json = json.dumps(candidates, ensure_ascii=False)
    row.ai_used = ai_used
    db.add(row)
    db.commit()
    return {
        "session_id": row.id,
        "candidates": candidates,
        "manual_symbols": list(MANUAL_SYMBOLS),
        "remaining_today": remaining_today(db, user),
        "disclaimer": DISCLAIMER,
    }
```

- [ ] **Step 4: 라우트를 붙인다**

테스트가 HTTP 로 부르므로 라우트도 이 태스크에서 함께 만든다. `backend/app/main.py` 의 기존 `/api/ask` 핸들러 옆에 더한다.

```python
@app.post("/api/ask/candidates")
def ask_candidates_route(req: ask_mod.CandidatesRequest,
                         user: User = Depends(auth_mod.current_user)) -> dict:
    """카드 답변으로 종목 후보를 낸다 — 하루 횟수는 여기서만 차감된다."""
    with get_session() as db:
        user = db.merge(user)
        try:
            return ask_mod.run_candidates(db, user, req)
        except ask_mod.AskError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message)
```

`ask_mod` 라는 별칭이 `main.py` 에 없으면 기존 import 이름을 그대로 쓴다 (`grep -n "import ask" app/main.py` 로 확인).

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py -q -p no:warnings -k candidates`
Expected: PASS (6 passed)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/ask.py backend/app/main.py backend/tests/test_ask.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 후보 흐름 시작(run_candidates), 차감은 여기서만

카드 답변으로 세션 행을 먼저 만들어 하루 한도를 차지하고, 규칙 풀에서 AI 가
후보를 고르게 한다. 후보를 못 내면 행을 지우고 추가권도 돌려준다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `run_ask` 를 세션 기반으로 바꾸기 (차감 없음·6회 상한·30분 만료)

**Files:**
- Modify: `backend/app/ask.py` — `AskRequest`, `run_ask` · `backend/app/main.py` — `/api/ask`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Consumes: `run_candidates` 가 만든 세션 (Task 6)
- Produces:
  - `ask.AskRequest`: `session_id: int`, `symbol: str`
  - `ask.run_ask(db, user, req, run_backtest) -> dict` — 반환에 `remaining_today` 포함, 차감 없음
  - 갱신된 `POST /api/ask` — `session_id` 없는 옛 요청에 새로고침 안내(422)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_ask.py` 에 추가한다. 기존 v1 요청 모양을 쓰는 테스트는 이 태스크에서 새 모양으로 고친다.

```python
def _start_flow(tok, monkeypatch, **over):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    _consent(tok)
    return client.post("/api/ask/candidates", json=_candidates_body(**over),
                       headers=_auth(tok)).json()


def test_choosing_symbol_does_not_consume_a_use(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    symbol = flow["candidates"][0]["symbol"]
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": symbol},
                    headers=_auth(tok))
    assert r.status_code in (200, 422)  # 시세가 없으면 422 — 차감 여부만 본다
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before


def test_symbol_outside_candidates_and_manual_list_is_rejected(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": "SCAMUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 422


def test_manual_symbol_is_allowed(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code != 422 or "찾지 못" in r.text  # 목록 거부(422)는 아니어야 한다


def test_expired_session_is_rejected(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        row.expires_ms = 1
        db.add(row)
        db.commit()
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 410


def test_session_call_budget_is_enforced(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        row.ask_count = ask.MAX_ASKS_PER_SESSION
        db.add(row)
        db.commit()
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 409


def test_other_users_session_is_not_readable(monkeypatch):
    tok_a, _ = _signup()
    flow = _start_flow(tok_a, monkeypatch)
    tok_b, _ = _signup()
    _consent(tok_b)
    r = client.post("/api/ask", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok_b))
    assert r.status_code == 404


def test_old_request_shape_gets_refresh_hint(monkeypatch):
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask", json={"risk_profile": "balanced", "market": "spot",
                                      "leverage": 1, "symbols": ["BTCUSDT"],
                                      "period_preset": "6m", "interval": "4h"},
                    headers=_auth(tok))
    assert r.status_code == 422
    assert "새로고침" in r.text
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py -q -p no:warnings -k "session or symbol or refresh"`
Expected: FAIL — `AskRequest` 가 아직 `symbols` 를 요구한다

- [ ] **Step 3: 최소 구현**

`backend/app/ask.py` 의 `AskRequest` 를 통째로 바꾼다(기존 `symbols`·`period_preset`·`interval`·`_normalize_symbols`·`_profile_rules` 제거).

```python
class AskRequest(BaseModel):
    """v2 — 흐름 세션에서 답변을 꺼내 쓰므로 종목만 받는다(클라이언트 위변조 차단)."""

    session_id: int
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        sym = str(value or "").strip().upper()
        if not _SYMBOL_RE.match(sym):
            raise ValueError("종목 이름이 올바르지 않아요")
        return sym
```

세션을 꺼내는 헬퍼를 더한다.

```python
def _load_flow(db: Session, user: User, session_id: int) -> tuple[AskMacroSession, CandidatesRequest]:
    row = db.get(AskMacroSession, session_id)
    if row is None or row.user_id != user.id:
        raise AskError(404, "질문 기록을 찾지 못했어요. 처음부터 다시 물어봐 주세요.")
    _, now_ms = _now()
    if row.expires_ms and row.expires_ms <= now_ms:
        raise AskError(410, "질문한 지 오래됐어요. 처음부터 다시 물어봐 주세요.")
    if row.ask_count >= MAX_ASKS_PER_SESSION:
        raise AskError(409, "이번 질문에서 살펴볼 수 있는 종목을 다 봤어요. 다시 물어봐 주세요.")
    try:
        answers = CandidatesRequest(**json.loads(row.request_json))
    except Exception:
        raise AskError(410, "질문 기록이 오래된 형식이에요. 처음부터 다시 물어봐 주세요.")
    return row, answers


def _allowed_symbols(row: AskMacroSession) -> set[str]:
    try:
        picked = {c["symbol"] for c in json.loads(row.candidates_json)}
    except Exception:
        picked = set()
    return picked | set(MANUAL_SYMBOLS)
```

`run_ask` 의 앞부분(동의·한도·세션 생성)을 세션 조회로 갈아 끼운다. 백테스트·평가·저장 부분은 그대로 두되, `req` 대신 세션에서 만든 내부 요청을 쓴다. `build_templates`·`propose_with_ai`·`_make_macro`·`_label` 은 `symbols` 리스트와 `period_preset`·`interval` 을 읽으므로, 그 값을 담은 **내부 전용 객체**를 만들어 넘긴다.

```python
@dataclass
class _Plan:
    """세션 답변 + 고른 종목 → 기존 후보 생성 코드가 읽는 모양."""
    risk_profile: str
    market: str
    leverage: int
    symbols: list[str]
    period_preset: str
    interval: str


def _plan(answers: CandidatesRequest, symbol: str) -> _Plan:
    period = to_period(answers.risk_profile, answers.invest_horizon)
    return _Plan(
        risk_profile=answers.risk_profile, market=answers.market, leverage=answers.leverage,
        symbols=[symbol], period_preset=period,
        interval=to_interval(answers.risk_profile, answers.watch_frequency, period),
    )


def run_ask(db: Session, user: User, req: AskRequest,
            run_backtest: Callable[[Macro], BacktestResult]) -> dict:
    if not consented(user):
        raise AskError(403, "먼저 안내에 동의해 주세요.")
    row, answers = _load_flow(db, user, req.session_id)
    if req.symbol not in _allowed_symbols(row):
        raise AskError(422, "이번 질문에서 살펴볼 수 있는 종목이 아니에요.")

    with _IN_FLIGHT_LOCK:
        if user.id in _IN_FLIGHT:
            raise AskError(429, "아직 지난 질문을 돌리는 중이에요. 잠시만요.")
        _IN_FLIGHT.add(user.id)

    try:
        started = time.monotonic()
        plan = _plan(answers, req.symbol)
        failures: list[Exception] = []

        def guarded(macro: Macro) -> BacktestResult:
            try:
                return run_backtest(macro)
            except Exception as exc:
                failures.append(exc)
                raise

        ai_candidates = propose_with_ai(plan)[:TOP_N]
        candidates = (ai_candidates + build_templates(plan))[:MAX_CANDIDATES]
        evaluated = evaluate(candidates, guarded, time_budget_sec())
        if not evaluated and failures and all(isinstance(f, NoSpotDataError) for f in failures):
            raise AskError(422, "이 종목의 시세 데이터를 찾지 못했어요. 다른 종목을 골라 주세요.")
        top = select_top(evaluated, plan.risk_profile)
        results = [_result_view(e) for e in top]

        # 성공했을 때만 호출 수를 센다 — 실패한 시도로 예산을 깎지 않는다.
        row.ask_count += 1
        row.chosen_symbol = req.symbol
        row.candidate_count = len(evaluated)
        row.results_json = json.dumps(
            [{"label": r["label"], "rule_type": r["rule_type"], "macro": r["macro"],
              "metrics": r["metrics"]} for r in results], ensure_ascii=False)
        row.elapsed_ms = int((time.monotonic() - started) * 1000)
        db.add(row)
        db.commit()
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(user.id)

    return {"results": results, "remaining_today": remaining_today(db, user),
            "disclaimer": DISCLAIMER, "disclaimer_version": DISCLAIMER_VERSION}
```

`_Plan` 은 `req.symbols`/`req.period_preset`/`req.interval`/`req.risk_profile`/`req.market`/`req.leverage` 만 읽히므로 기존 `build_templates`·`_make_macro`·`_allowed_types`·`_presets_for`·`propose_with_ai`·`_label` 은 **그대로 둔다** (타입 힌트만 `AskRequest` → `_Plan` 으로 바꾼다).

- [ ] **Step 4: `/api/ask` 라우트를 새 요청 모양에 맞춘다**

`run_ask` 의 요청 모양이 바뀌었으므로 라우트도 이 태스크에서 같이 고친다. 옛 번들이 보내는 요청(`symbols` 가 있고 `session_id` 가 없음)은 pydantic 검증 전에 걸러야 안내 문구를 줄 수 있으므로, 원본 본문을 받아 판별한다.

`backend/app/main.py` 의 기존 `@app.post("/api/ask")` 핸들러를 바꾼다.

```python
@app.post("/api/ask")
def ask_route(body: dict, user: User = Depends(auth_mod.current_user)) -> dict:
    if "session_id" not in body:
        # 옛 번들이 캐시에 남아 있을 때 — 배포 직후 한 번 겪는다.
        raise HTTPException(status_code=422,
                            detail="화면을 새로고침한 뒤 다시 물어봐 주세요.")
    try:
        req = ask_mod.AskRequest(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422,
                            detail=str(exc.errors()[0].get("msg", "요청이 올바르지 않아요")))
    with get_session() as db:
        user = db.merge(user)
        try:
            return ask_mod.run_ask(db, user, req, _run_backtest_for)
        except ask_mod.AskError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message)
```

`from pydantic import ValidationError` 가 `main.py` 에 없으면 더한다. 백테스트 함수 이름(`_run_backtest_for`)은 기존 호출부에 쓰이던 이름을 그대로 쓴다.

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_ask.py tests/test_ask_candidates.py tests/test_hotcoins.py -q -p no:warnings`
Expected: PASS (전부)

그다음 전체를 돌려 회귀를 본다.

Run: `cd backend && .venv/Scripts/python -m pytest tests -q -p no:warnings --continue-on-collection-errors`
Expected: 기존에 있던 실패 2건(`test_agent_collector_runner`)과 수집 오류 6건 외에 새 실패가 없어야 한다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/ask.py backend/app/main.py backend/tests/test_ask.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 종목 선택은 세션을 읽어 쓰고 차감하지 않는다

요청은 session_id 와 symbol 만 받는다(성향·기간을 클라이언트가 다시 보내지
않아 위변조가 막힌다). 세션은 30분 만료, 매크로 생성은 세션당 6회까지이며
성공했을 때만 센다. 후보 목록과 직접 고르기 목록 밖 종목은 거부한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 프론트 상태 머신과 문구

**Files:**
- Modify: `frontend/src/lib/askFlow.js`, `frontend/src/lib/askCopy.js`
- Test: `frontend/tests/askFlow.test.js` (재작성)

**Interfaces:**
- Consumes: Task 6·7 의 응답 모양
- Produces:
  - `askFlow.STEPS = ["profile", "market", "horizon", "watch"]`
  - `askFlow.initialState()` → `{ step, answers, phase: "cards", session: null, candidates: [], manualSymbols: [], results: null, remaining: null, error: "" }`
  - `askFlow.reduce(state, action)` — 액션: `choose`, `chooseSymbol`, `loadingCandidates`, `candidates`, `loadingResults`, `results`, `back`, `followUp`, `error`
  - `askFlow.toCandidatesRequest(answers)` → `{ risk_profile, market, leverage, invest_horizon, watch_frequency }`
  - `askFlow.toAskRequest(state)` → `{ session_id, symbol }`
  - `askCopy.HORIZONS`, `askCopy.WATCH_LEVELS`, `askCopy.STEP_PROMPTS` 갱신

- [ ] **Step 1: `extraOffer` 와 그 테스트를 먼저 떼어 낸다**

`askFlow.test.js` 를 통째로 다시 쓰면 그 안에 있던 `extraOffer` 테스트가 사라진다. 먼저 옮겨 두어 커버리지를 지킨다.

1. `frontend/src/lib/askFlow.js` 끝의 `extraOffer` 함수(주석 포함)를 잘라 `frontend/src/lib/askExtra.js` 에 **그대로** 옮긴다. 내용은 한 글자도 바꾸지 않는다.
2. `frontend/tests/askFlow.test.js` 안의 `extraOffer` 관련 테스트를 잘라 `frontend/tests/askExtra.test.js` 로 옮기고, import 를 바꾼다:

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { extraOffer } from "../src/lib/askExtra.js";
```

3. 확인한다.

Run: `cd frontend && node --test "tests/askExtra.test.js"`
Expected: PASS (옮기기 전과 같은 개수)

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`frontend/tests/askFlow.test.js` 를 통째로 다시 쓴다.

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { STEPS, initialState, reduce, toCandidatesRequest, toAskRequest, canChooseFutures } from "../src/lib/askFlow.js";

const CANDS = [
  { symbol: "AAAUSDT", base: "AAA", reason: "거래가 활발해요", volume_rank: 1, range_pct: 1, change_pct: 1 },
  { symbol: "BBBUSDT", base: "BBB", reason: "변동이 적당해요", volume_rank: 2, range_pct: 5, change_pct: 1 },
  { symbol: "CCCUSDT", base: "CCC", reason: "변동이 커요", volume_rank: 3, range_pct: 20, change_pct: 1 },
];

function cards(profile = "balanced") {
  let s = initialState();
  s = reduce(s, { type: "choose", step: "profile", value: profile });
  s = reduce(s, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  s = reduce(s, { type: "choose", step: "horizon", value: "weeks" });
  s = reduce(s, { type: "choose", step: "watch", value: "sometimes" });
  return s;
}

function withCandidates() {
  return reduce(cards(), {
    type: "candidates",
    session: { id: 7, remaining: 4 },
    candidates: CANDS,
    manualSymbols: ["BTCUSDT"],
  });
}

test("cards are four and end ready", () => {
  assert.deepEqual(STEPS, ["profile", "market", "horizon", "watch"]);
  const s = cards();
  assert.equal(s.phase, "ready");
  assert.deepEqual(toCandidatesRequest(s.answers), {
    risk_profile: "balanced", market: "spot", leverage: 1,
    invest_horizon: "weeks", watch_frequency: "sometimes",
  });
});

test("stable profile cannot choose futures", () => {
  let s = reduce(initialState(), { type: "choose", step: "profile", value: "stable" });
  assert.equal(canChooseFutures(s.answers), false);
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  assert.equal(s.answers.market, null);
});

test("candidates phase holds the session and the list", () => {
  const s = withCandidates();
  assert.equal(s.phase, "candidates");
  assert.equal(s.session.id, 7);
  assert.equal(s.candidates.length, 3);
  assert.deepEqual(s.manualSymbols, ["BTCUSDT"]);
});

test("only one symbol can be chosen", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "aaausdt" });
  assert.equal(s.answers.symbol, "AAAUSDT");
  s = reduce(s, { type: "chooseSymbol", symbol: "BBBUSDT" });
  assert.equal(s.answers.symbol, "BBBUSDT");
  assert.deepEqual(toAskRequest(s), { session_id: 7, symbol: "BBBUSDT" });
});

test("a symbol outside the candidates and manual list is ignored", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "SCAMUSDT" });
  assert.equal(s.answers.symbol, null);
});

test("a manual symbol is accepted", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "BTCUSDT" });
  assert.equal(s.answers.symbol, "BTCUSDT");
});

test("going back to a card drops the session, candidates and results", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "back", step: "horizon" });
  assert.equal(s.phase, "cards");
  assert.equal(s.session, null);
  assert.deepEqual(s.candidates, []);
  assert.equal(s.results, null);
  assert.equal(s.answers.symbol, null);
  assert.equal(s.answers.horizon, null);
});

test("'다른 종목으로' returns to the candidates and keeps the session", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "followUp", kind: "symbols" });
  assert.equal(s.phase, "candidates");
  assert.equal(s.session.id, 7);
  assert.equal(s.candidates.length, 3);
  assert.equal(s.answers.symbol, null);
});

test("'안전하게' changes the profile and returns to the cards (a new use)", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(s.answers.profile, "stable");
  assert.equal(s.phase, "ready");   // 나머지 답은 남아 있어 바로 제출 가능
  assert.equal(s.session, null);    // 세션은 버린다 — 다시 제출하면 새로 차감
  assert.deepEqual(s.candidates, []);
});

test("changing to stable drops a futures answer", () => {
  let s = cards("balanced");
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  s = reduce(s, { type: "choose", step: "horizon", value: "weeks" });
  s = reduce(s, { type: "choose", step: "watch", value: "sometimes" });
  s = reduce(s, { type: "candidates", session: { id: 1, remaining: 1 }, candidates: CANDS, manualSymbols: [] });
  s = reduce(s, { type: "results", results: [], remaining: 1 });
  s = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(s.answers.profile, "stable");
  assert.equal(s.answers.market, "spot");
  assert.equal(s.answers.leverage, 1);
});

test("restart clears everything", () => {
  const s = reduce(withCandidates(), { type: "followUp", kind: "restart" });
  assert.deepEqual(s, initialState());
});
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd frontend && node --test "tests/askFlow.test.js"`
Expected: FAIL — `toCandidatesRequest is not a function`

- [ ] **Step 4: 최소 구현**

`frontend/src/lib/askCopy.js` 를 바꾼다. `POPULAR_SYMBOLS` 는 "직접 고를래요" 목록으로 그대로 쓴다.

```javascript
export const DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요";

export const STEP_PROMPTS = {
  profile: "손실은 어디까지 견딜 수 있어요?",
  market: "어느 시장에서요?",
  horizon: "얼마나 길게 굴릴 거예요?",
  watch: "하루에 얼마나 자주 볼 수 있어요?",
};

export const HORIZONS = [
  { value: "days", label: "며칠", hint: "짧게 보고 정리" },
  { value: "weeks", label: "몇 주", hint: "적당히" },
  { value: "months", label: "몇 달", hint: "느긋하게" },
  { value: "long", label: "길게", hint: "오래 들고" },
];

export const WATCH_LEVELS = [
  { value: "rarely", label: "거의 못 봐요", hint: "하루 한 번쯤" },
  { value: "sometimes", label: "가끔 봐요", hint: "몇 시간에 한 번" },
  { value: "often", label: "수시로 봐요", hint: "자주 확인" },
];

export const CANDIDATES_PROMPT = "성향에 맞는 종목을 골라 봤어요. 하나를 고르면 매크로 후보를 보여 드릴게요.";
export const MANUAL_PICK_LABEL = "직접 고를래요";
```

기존 `PERIODS`·`SHORT_PERIODS`·`INTERVALS`·`SHORT_INTERVALS`·`ONE_MINUTE_NEEDS_WEEK`·`symbolsPrompt` 는 더 쓰지 않으므로 지운다. `PROFILES`·`MARKETS`·`LEVERAGES`·`STABLE_NO_FUTURES`·`CONSENT_TEXT`·`POPULAR_SYMBOLS` 는 남긴다.

`frontend/src/lib/askFlow.js` 를 다시 쓴다.

```javascript
// 껄무새에게 물어볼까? v2 — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목은 후보/직접 고르기 목록에서 하나만 · 뒤로 가면 세션을 버린다 · 자유 입력 없음.
import { HORIZONS, LEVERAGES, MARKETS, PROFILES, WATCH_LEVELS } from "./askCopy.js";

export const STEPS = ["profile", "market", "horizon", "watch"];
export const PROFILE_ORDER = ["stable", "balanced", "aggressive", "scalper"];

export function canChooseFutures(answers) {
  return answers.profile !== "stable";
}

function emptyAnswers() {
  return { profile: null, market: null, leverage: 1, horizon: null, watch: null, symbol: null };
}

export function initialState() {
  return {
    step: "profile", answers: emptyAnswers(), phase: "cards",
    session: null, candidates: [], manualSymbols: [],
    results: null, remaining: null, error: "",
  };
}

function answered(step, answers) {
  return answers[step] != null;
}

export function nextStep(answers) {
  return STEPS.find((step) => !answered(step, answers)) ?? null;
}

function settle(state, answers) {
  const step = nextStep(answers);
  if (step === null) return { ...state, answers, step: "watch", phase: "ready", error: "" };
  return { ...state, answers, step, phase: "cards", error: "" };
}

// 카드 하나를 고치면 그 뒤 답과 세션·후보·결과를 전부 버린다.
function clearFrom(answers, step) {
  const next = { ...answers, symbol: null };
  for (const s of STEPS.slice(STEPS.indexOf(step))) {
    if (s === "market") { next.market = null; next.leverage = 1; }
    else next[s] = null;
  }
  return next;
}

function dropFlow(state) {
  return { ...state, session: null, candidates: [], manualSymbols: [], results: null };
}

function allowedSymbols(state) {
  return new Set([...state.candidates.map((c) => c.symbol), ...state.manualSymbols]);
}

export function reduce(state, action) {
  const { answers } = state;
  switch (action.type) {
    case "choose": {
      const { step, value } = action;
      if (step === "profile") {
        if (!PROFILES.some((p) => p.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "profile"), profile: value });
      }
      if (step === "market") {
        const market = value?.market;
        const leverage = market === "futures" ? Number(value?.leverage || 1) : 1;
        if (!MARKETS.some((m) => m.value === market)) return state;
        if (market === "futures" && !canChooseFutures(answers)) return state;
        if (!LEVERAGES.includes(leverage)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "market"), market, leverage });
      }
      if (step === "horizon") {
        if (!HORIZONS.some((h) => h.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "horizon"), horizon: value });
      }
      if (step === "watch") {
        if (!WATCH_LEVELS.some((w) => w.value === value)) return state;
        return settle(dropFlow(state), { ...answers, watch: value, symbol: null });
      }
      return state;
    }
    case "loadingCandidates":
      return { ...state, phase: "loadingCandidates", error: "" };
    case "candidates":
      return {
        ...state, phase: "candidates", error: "",
        session: action.session || null,
        candidates: action.candidates || [],
        manualSymbols: action.manualSymbols || [],
        remaining: action.session?.remaining ?? state.remaining,
        results: null,
      };
    case "chooseSymbol": {
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol || !allowedSymbols(state).has(symbol)) return state;
      return { ...state, answers: { ...answers, symbol }, error: "" };
    }
    case "loadingResults":
      return { ...state, phase: "loadingResults", error: "" };
    case "results":
      return { ...state, phase: "results", results: action.results || [],
               remaining: action.remaining ?? state.remaining, error: "" };
    case "error":
      return { ...state, phase: "error", error: String(action.message || "잠시 뒤 다시 물어봐 주세요.") };
    case "back": {
      if (!STEPS.includes(action.step)) return state;
      return { ...dropFlow(state), answers: clearFrom(answers, action.step),
               step: action.step, phase: "cards", error: "" };
    }
    case "followUp": {
      const { kind } = action;
      if (kind === "restart") return initialState();
      if (state.phase !== "results") return state;
      if (kind === "symbols") {
        // 세션을 그대로 두고 후보 목록으로 — 차감 없음.
        return { ...state, phase: "candidates", results: null,
                 answers: { ...answers, symbol: null }, error: "" };
      }
      if (kind === "safer" || kind === "riskier") {
        const idx = PROFILE_ORDER.indexOf(answers.profile);
        const nextIdx = kind === "safer" ? idx - 1 : idx + 1;
        if (nextIdx < 0 || nextIdx >= PROFILE_ORDER.length) return state;
        const profile = PROFILE_ORDER[nextIdx];
        const next = { ...answers, profile, symbol: null };
        if (profile === "stable" && next.market === "futures") { next.market = "spot"; next.leverage = 1; }
        // 성향이 바뀌면 후보가 달라진다 — 세션을 버리고 다시 제출하게 한다(새로 1회 차감).
        return settle(dropFlow(state), next);
      }
      return state;
    }
    default:
      return state;
  }
}

export function toCandidatesRequest(answers) {
  return {
    risk_profile: answers.profile,
    market: answers.market,
    leverage: answers.market === "futures" ? answers.leverage : 1,
    invest_horizon: answers.horizon,
    watch_frequency: answers.watch,
  };
}

export function toAskRequest(state) {
  return { session_id: state.session?.id, symbol: state.answers.symbol };
}

// 내 말풍선에 쓰는 답 라벨.
export function answerLabel(step, answers) {
  if (step === "profile") return PROFILES.find((p) => p.value === answers.profile)?.label ?? "";
  if (step === "market") {
    if (answers.market === "futures") return `선물 ${answers.leverage}x`;
    return MARKETS.find((m) => m.value === answers.market)?.label ?? "";
  }
  if (step === "horizon") return HORIZONS.find((h) => h.value === answers.horizon)?.label ?? "";
  if (step === "watch") return WATCH_LEVELS.find((w) => w.value === answers.watch)?.label ?? "";
  return "";
}

export { extraOffer } from "./askExtra.js";
```

마지막 줄의 재수출은 `AskParrotDialog.jsx` 가 `askFlow.js` 에서 `extraOffer` 를 import 하고 있어 그대로 두기 위한 것이다(Step 1 에서 함수 본체는 `askExtra.js` 로 옮겼다).

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && node --test "tests/askFlow.test.js"`
Expected: PASS (11 passed)

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: 기존 408개 중 `askFlow` 관련 외에 새 실패가 없어야 한다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/askFlow.js frontend/src/lib/askCopy.js frontend/src/lib/askExtra.js frontend/tests/askFlow.test.js frontend/tests/askExtra.test.js
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 카드 네 장과 후보 단계로 상태 머신 개편

종목 카드를 후보 단계로 옮기고 기간·봉 카드를 투자 기간·보는 빈도로 바꿨다.
종목은 후보나 직접 고르기 목록에서 하나만 고를 수 있고, 카드로 돌아가면
세션을 버린다. '다른 종목으로' 는 세션을 유지해 차감이 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 후보 화면 UI 와 API 호출

**Files:**
- Modify: `frontend/src/api.js`, `frontend/src/components/AskParrotDialog.jsx`, `frontend/src/components/AskParrotDialog.css`
- Test: 빌드와 기존 테스트로 확인 (컴포넌트 단위 테스트는 이 저장소에 없다)

**Interfaces:**
- Consumes: `askFlow` 액션 (Task 8), `/api/ask/candidates` (Task 6)
- Produces: `api.askCandidates(body)`, `api.ask(body)` (기존 이름 유지, 본문만 바뀜)

- [ ] **Step 1: API 함수를 더한다**

`frontend/src/api.js` 에서 기존 `ask` 옆에 더한다.

```javascript
  askCandidates: (body) => post("/api/ask/candidates", body),
```

기존 `ask` 는 그대로 두고 호출부에서 새 본문(`{ session_id, symbol }`)을 넘긴다.

- [ ] **Step 2: 대화 흐름을 새 액션에 맞춘다**

`frontend/src/components/AskParrotDialog.jsx` 에서:

- 카드 렌더링 목록을 `STEPS`(`profile`·`market`·`horizon`·`watch`)로 바꾸고, 선택지는 `PROFILES`·`MARKETS`·`HORIZONS`·`WATCH_LEVELS` 를 쓴다.
- `phase === "ready"` 일 때 버튼을 누르면:

```javascript
  async function submitCards() {
    dispatch({ type: "loadingCandidates" });
    try {
      const data = await api.askCandidates(toCandidatesRequest(state.answers));
      dispatch({
        type: "candidates",
        session: { id: data.session_id, remaining: data.remaining_today },
        candidates: data.candidates,
        manualSymbols: data.manual_symbols,
      });
    } catch (e) {
      dispatch({ type: "error", message: e.message });
    }
  }
```

- `phase === "candidates"` 에서 후보 카드를 그린다. 종목을 누르면 `chooseSymbol` 뒤 바로 조회한다.

```javascript
  async function pickSymbol(symbol) {
    const next = reduce(state, { type: "chooseSymbol", symbol });
    if (!next.answers.symbol) return;
    dispatch({ type: "chooseSymbol", symbol });
    dispatch({ type: "loadingResults" });
    try {
      const data = await api.ask({ session_id: next.session.id, symbol: next.answers.symbol });
      dispatch({ type: "results", results: data.results, remaining: data.remaining_today });
    } catch (e) {
      dispatch({ type: "error", message: e.message });
    }
  }
```

- 후보 카드 한 장의 모양:

```jsx
<button type="button" className="ask-cand" onClick={() => pickSymbol(c.symbol)}>
  <span className="ask-cand-head">
    <strong>{c.base}</strong>
    <span className="ask-cand-stats num">
      거래대금 {c.volume_rank}위 · 하루 변동 {c.range_pct}%
    </span>
  </span>
  <span className="ask-cand-reason">{c.reason}</span>
</button>
```

- 후보 목록 아래에 "직접 고를래요"를 접이식으로 두고 `manualSymbols` 를 칩으로 그린다.
- 후보 화면에도 `DISCLAIMER` 를 붙인다.
- 결과 화면의 "안전하게/공격적으로" 버튼 라벨 옆에 남은 횟수를 적는다: `` `안전하게 (횟수 1회 더 써요 · 남은 ${state.remaining}회)` ``

- [ ] **Step 3: 스타일을 더한다**

`frontend/src/components/AskParrotDialog.css` 끝에 더한다.

```css
.ask-cand {
  display: flex; flex-direction: column; gap: 4px; width: 100%;
  padding: 12px 14px; text-align: left;
  border: 1px solid var(--line, #e2e8f0); border-radius: 12px;
  background: #fff; cursor: pointer;
}
.ask-cand:hover { border-color: #94a3b8; background: #f8fafc; }
.ask-cand-head { display: flex; align-items: baseline; gap: 8px; justify-content: space-between; }
.ask-cand-head strong { font-size: 15px; }
.ask-cand-stats { font-size: 12px; color: #64748b; }
.ask-cand-reason { font-size: 13px; color: #334155; line-height: 1.5; }
.ask-cand-list { display: flex; flex-direction: column; gap: 8px; }
```

- [ ] **Step 4: 빌드와 테스트로 확인한다**

Run: `cd frontend && npx vite build --logLevel error`
Expected: 오류 없이 끝난다

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS

Run: `cd backend && .venv/Scripts/python -m pytest tests -q -p no:warnings --continue-on-collection-errors`
Expected: 기존 실패 2건·수집 오류 6건 외에 새 실패 없음

- [ ] **Step 5: 개발 서버로 눈으로 확인한다**

`preview_start` 로 `backend-dev`·`frontend-dev` 를 띄우고, 로그인 → 스튜디오 → "껄무새에게 물어볼까?" 로 카드 4장 → 후보 목록 → 종목 선택 → 매크로 3개까지 실제로 흐르는지 본다. 후보 카드에 이유와 숫자가 같이 보이는지, 후보 화면에도 고지가 붙는지 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/api.js frontend/src/components/AskParrotDialog.jsx frontend/src/components/AskParrotDialog.css
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "$(cat <<'EOF'
물어볼까 v2 — 종목 후보 화면

후보를 카드로 보여 준다: 종목 이름, 서버가 계산한 거래대금 순위·하루 변동폭,
그리고 왜 이 성향에 맞는지 한 줄. 아래에 '직접 고를래요' 를 두고, 후보 화면
에도 고지를 붙였다. 성향을 바꾸는 버튼에는 횟수가 더 든다고 적었다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 마무리 확인

전부 끝난 뒤 한 번에 돌린다.

```bash
cd backend && .venv/Scripts/python -m pytest tests -q -p no:warnings --continue-on-collection-errors
cd ../frontend && node --test "tests/*.test.js" && npx vite build --logLevel error
cd ../runner && python -m unittest discover -s . -p "test_*.py"
```

실행기는 이번 변경과 무관하지만, 같은 저장소라 회귀가 없는지 한 번 본다.
