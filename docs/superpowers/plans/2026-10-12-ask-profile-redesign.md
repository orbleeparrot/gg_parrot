# 껄무새 성향 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 껄무새에게 물어볼까의 성향을 셋(안정·균형·공격)으로 줄이고, 성향이 종목이 아니라 위험 기준으로만 갈리게 바꿔 "골랐는데 아무것도 안 나온다" 를 없앤다.

**Architecture:** 세 성향이 **같은 종목 풀**(거래대금 상위 40 → 왕복 여지 내림차순)을 보고, 성향은 기간별 MDD 상한 + 점수 함수로만 갈린다. 최소 거래 수는 봉 수에 비례시키고, 매매 방식 화이트리스트를 없애고, 단타형 대신 확인 빈도에 15분봉 칸을 더한다. 상한·점수의 최종 값은 마지막 과제의 워크포워드 측정이 고른다.

**Tech Stack:** Python 3.14 · FastAPI · SQLModel · pydantic v2 · pytest (백엔드) / React 18 · Vite · node:test · oxlint (프런트)

**Spec:** [docs/superpowers/specs/2026-10-12-ask-profile-redesign-design.md](../specs/2026-10-12-ask-profile-redesign-design.md)

## Global Constraints

- `RiskProfile = Literal["stable", "balanced", "aggressive"]` — `scalper` 는 코드·시험·문구에서 모두 사라진다.
- `watch_frequency: Literal["rarely", "sometimes", "often", "constantly"]` — 칸 넷.
- 확인 빈도 → 봉: `rarely=1d` · `sometimes=4h` · `often=1h` · `constantly=15m`. 단, `constantly` + 기간 `1y` 는 `1h` 로 내린다(15분봉 × 1년 = 35,040봉 > `MAX_BACKTEST_BARS` 20,000).
- 최소 거래 수: `max(2, 봉 수 // 150)`. 고정 3 · 10 을 쓰지 않는다.
- MDD 상한 적용식: `min(절대 상한, max(5.0, 홀딩MDD × 계수))`. 절대 표와 계수는 아래 그대로.
  - 안정형 절대: `1w 8 · 1m 12 · 3m 18 · 6m 25 · 1y 30` (%), 계수 `0.6`
  - 균형형 절대: `1w 15 · 1m 22 · 3m 32 · 6m 40 · 1y 50` (%), 계수 `1.0`
  - 공격형: 상한 없음(`None`)
- 매매 방식은 세 성향 모두 `("C", "J", "G", "A", "F", "E", "I", "H")` 여덟 가지. 선물 시장에서만 `C` 를 뺀다.
- 홀딩 초과 문턱(`MIN_EXCESS_PCT_BY_PERIOD`, `min_excess_pct`, `worth_the_macro`)은 **건드리지 않는다.**
- 저장된 매크로 · 실행 중 세션 · `.ggm.json` 파일 · DPAPI 엔트로피 · 실행기 프로토콜은 **건드리지 않는다.**
- 사용자에게 보이는 문구에 '추천' 이라는 낱말을 쓰지 않는다(후보 · 상위 조합). 모든 문구는 한국어, 한자 금지.
- 주석은 '무엇' 이 아니라 '왜' 를 적는다 — 이 저장소의 기존 주석 밀도와 말투를 따른다.
- 백엔드 시험: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/<파일> -q`
- 프런트 시험: `cd frontend && node --test "tests/*.test.js"` (따옴표 필수) · 린트: `cd frontend && npm run lint`

## Review Focus

- **`buy_hold_mdd_pct` 가 `None` 인 결과** (묶음 집계 · 봉 2개 미만) — 상대 상한을 못 구해도 절대 상한으로 거르고 터지지 않아야 한다. → 과제 2 Step 1
- **`constantly` + `long`** — 15분봉 × 1년은 봉 상한을 넘는다. 거절이 아니라 1시간봉으로 내려가야 한다. → 과제 4 Step 1
- **저장된 세션의 `risk_profile: "scalper"`** — 검증 예외가 그대로 올라가면 화면이 500 으로 죽는다. 410 + 안내 문구 + 횟수 환불이어야 한다. → 과제 5 Step 1
- **15분봉에서 `C`·`H`·`I` 프리셋** — 짧은 봉 표에 없는 방식이다. 겹쳐 쓰지 않으면 `build_templates` 가 `KeyError` 로 터진다. → 과제 4 Step 3
- **빈 화면 숫자에서 `hold_return_pct` 가 `None`** — 홀딩 기준을 못 구한 결과가 1등일 수 있다. 숫자 줄이 "null%" 를 찍지 말고 홀딩 쪽만 빠져야 한다. → 과제 7 Step 1

---

## File Structure

**백엔드**

| 파일 | 하는 일 | 이 계획에서 |
|---|---|---|
| `backend/app/engine/backtest.py` | 백테스트 지표 계산 | 홀딩 MDD 지표 추가 (과제 1) |
| `backend/app/ask.py` | 성향 표 · 기간/봉 번역 · 후보 생성 · 선별 | 상한·거래 수·화이트리스트·단타형·세션 이관·빈 화면 숫자 (과제 2·4·5·7) |
| `backend/app/ask_candidates.py` | 종목 풀·정렬 | 풀 통합 (과제 3) |
| `backend/app/main.py` | HTTP 끝점 | `/api/explain/ai` 제거 (과제 8) |
| `backend/app/api_usage.py` | AI 사용량 표 | `ai_explain` 줄 제거 (과제 8) |
| `backend/app/ai_explain.py` | 백테스트 AI 해설 | 파일 삭제 (과제 8) |
| `backend/scripts/ask_walkforward.py` | 워크포워드 측정기 | 신설 (과제 10) |

**프런트**

| 파일 | 하는 일 | 이 계획에서 |
|---|---|---|
| `frontend/src/lib/askCopy.js` | 물어볼까 문구·선택지 | 성향 셋 · 빈도 넷 · 짧은 봉 경고 · 홀딩 표현 (과제 6·7) |
| `frontend/src/lib/askFlow.js` | 카드 상태 머신 | `PROFILE_ORDER` · `noEdgeBest` (과제 6·7) |
| `frontend/src/lib/askView.js` | 결과 표시용 순수 함수 | `noEdgeDetail` (과제 7) |
| `frontend/src/components/AskParrotDialog.jsx` | 물어볼까 화면 | 경고 위치 · 빈 화면 숫자 (과제 6·7) |
| `frontend/src/lib/glossary.js` | 용어집 | `buy_hold` 라벨·뜻 (과제 7) |
| `frontend/src/components/ResultView.jsx` · `StudioDock.jsx` · `pages/Studio.jsx` · `hooks/useHeroBacktest.js` · `api.js` · `lib/resultLayout.js` | 결과 화면 | AI 해설 층 제거 (과제 9) |

**시험**

| 파일 | 이 계획에서 |
|---|---|
| `backend/tests/test_ask_profiles.py` | 신설 — 과제 1·2·4·5·7 의 새 시험 |
| `backend/tests/test_ask.py` · `test_ask_candidates.py` | 단타형 시험 교체 |
| `backend/tests/test_ai_explain.py` | 파일 삭제 (과제 8) |
| `frontend/tests/askProfiles.test.js` | 신설 — 과제 6 |
| `frontend/tests/askNoEdge.test.js` | 문구·경고 위치 교체 (과제 6·7) |

---

### Task 1: 홀딩 MDD 지표

성향별 MDD 상한을 "같은 구간 홀딩이 흔들린 만큼" 에 상대적으로 잡으려면 홀딩의 최대 낙폭이 필요하다. 지금 `BacktestResult` 에는 홀딩 **수익률**만 있다.

**Files:**
- Modify: `backend/app/engine/backtest.py` (필드 추가 · `_metrics` 인자 추가 · 도우미 추가 · 호출 세 곳)
- Test: `backend/tests/test_ask_profiles.py` (신설)

**Interfaces:**
- Consumes: 없음(첫 과제)
- Produces: `BacktestResult.buy_hold_mdd_pct: Optional[float]` — 같은 구간 '첫 종가에 사서 끝까지 보유' 의 최대 낙폭(양수 %). 봉이 2개 미만이면 `None`. 모듈 함수 `_buy_hold_mdd_pct(closes) -> Optional[float]`.

- [ ] **Step 1: 실패하는 시험을 쓴다**

새 파일 `backend/tests/test_ask_profiles.py`:

```python
"""성향 재설계(2026-10-12) — 상한 · 거래 수 · 봉 · 세션 이관 · 빈 화면 숫자.

왜 따로 두나: test_ask.py 는 이미 1,200줄이 넘고 끝점 흐름을 다룬다. 이 파일은 성향을
가르는 **순수 함수**들만 본다 — 값이 바뀌면 여기만 보면 된다.
"""
from __future__ import annotations

import pandas as pd

from app.engine.backtest import _buy_hold_mdd_pct, run_backtest
from app.engine.schema import Macro


def _df(closes: list[float]) -> pd.DataFrame:
    stamps = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": stamps, "open": closes, "high": closes,
        "low": closes, "close": closes, "volume": [1.0] * len(closes),
    })


def _macro(**over) -> Macro:
    body = {
        "symbol": "KRW-BTC", "exchange": "upbit", "quote_currency": "KRW",
        "rule_type": "A", "position_side": "long", "market": "spot",
        "leverage": 1, "candle_interval": "1h",
        "params": {"take_profit_pct": 3, "initial_capital": 1_000_000},
        "risk": {"stop_loss_pct": 2}, "period": {"preset": "1w"},
        "fees": {"commission_pct": 0.05, "slippage_pct": 0.05},
    }
    body.update(over)
    return Macro.model_validate(body)


def test_hold_mdd_is_the_peak_to_trough_of_the_price_itself():
    # 100 → 120 → 60 → 90. 고점 120 에서 저점 60 이니 -50%.
    assert _buy_hold_mdd_pct([100.0, 120.0, 60.0, 90.0]) == 50.0
    # 쭉 올랐으면 낙폭이 없다.
    assert _buy_hold_mdd_pct([100.0, 110.0, 130.0]) == 0.0
    # 봉이 하나면 '보유' 를 정의할 수 없다 — 0 이라고 말하면 거짓이다.
    assert _buy_hold_mdd_pct([100.0]) is None
    assert _buy_hold_mdd_pct([]) is None


def test_backtest_reports_hold_mdd_beside_hold_return():
    result = run_backtest(_macro(), _df([100.0, 120.0, 60.0, 90.0]))
    assert result.buy_hold_mdd_pct == 50.0
    assert result.buy_hold_return_pct is not None, "홀딩 수익률과 같이 나와야 한다"
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: FAIL — `ImportError: cannot import name '_buy_hold_mdd_pct'`

- [ ] **Step 3: 지표를 더한다**

`backend/app/engine/backtest.py` 의 `BacktestResult` 에서 `buy_hold_return_pct` 필드 바로 아래에 더한다:

```python
    # 같은 구간 '첫 종가에 사서 끝까지 보유' 의 최대 낙폭(양수 %). 봉이 2개 미만이면 None.
    #
    # 왜 필요한가: 성향별 MDD 상한을 고정 숫자로 두면 기간이 길어질수록 전부 탈락한다
    # (측정: 균형형 6개월에서 후보 87개 중 72개가 상한에서 죽었다). 시장이 얼마나
    # 흔들렸는지를 같이 알아야 "홀딩보다 덜 흔들렸다" 를 말할 수 있다.
    buy_hold_mdd_pct: Optional[float] = None
```

`_metrics` 의 키워드 인자에 `buy_hold_return_pct` 다음 줄로 더한다:

```python
    buy_hold_mdd_pct: Optional[float] = None,
```

`_metrics` 의 `return BacktestResult(` 안, `buy_hold_return_pct=...` 다음 줄에 더한다:

```python
        buy_hold_mdd_pct=(round(buy_hold_mdd_pct, 4) if buy_hold_mdd_pct is not None else None),
```

`_buy_hold_return_pct` 함수 바로 아래에 더한다:

```python
def _buy_hold_mdd_pct(closes) -> Optional[float]:
    """첫 종가에 사서 보유했을 때의 최대 낙폭(양수 %).

    전략 MDD 와 같은 방식(고점 갱신 → 고점 대비 하락의 최악)으로 재야 두 숫자를 나란히
    비교할 수 있다. 다만 여기 '자산' 은 가격 자체다 — 수수료도 거래도 없다.
    """
    if len(closes) < 2:
        return None
    peak = float("-inf")
    worst = 0.0
    for value in closes:
        price = float(value)
        if price > peak:
            peak = price
        if peak > 0:
            drop = (price - peak) / peak
            if drop < worst:
                worst = drop
    return -worst * 100.0
```

- [ ] **Step 4: 세 엔진에 모두 싣는다**

`backend/app/engine/backtest.py` 안에서 `buy_hold_return_pct=_buy_hold_return_pct(closes),` 가 쓰인 곳은 셋이다(`_run_single_position` · `_run_dca` · `_run_candle_engine`). 세 곳 모두 그 줄 **바로 아래**에 더한다:

```python
        buy_hold_mdd_pct=_buy_hold_mdd_pct(closes),
```

확인: `cd backend && grep -c "buy_hold_mdd_pct=_buy_hold_mdd_pct(closes)," app/engine/backtest.py` → `3`

- [ ] **Step 5: 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: 기존 백테스트 시험이 그대로인지 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_backtest_payload.py tests/test_backtest_trade_share.py tests/test_bundle_gate.py -q`
Expected: PASS (새 필드는 기본값 `None` 이라 기존 응답 모양을 깨지 않는다)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/engine/backtest.py backend/tests/test_ask_profiles.py
git commit -m "feat(engine): 홀딩의 최대 낙폭도 같이 낸다 — 성향 상한을 시장에 상대적으로 잡으려고

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 성향별 MDD 상한 · 봉 수 비례 최소 거래 수

성향을 가르는 두 관문을 기간에 따라 움직이게 만든다. 지금은 둘 다 고정값이라 기간이 길면 상한에서, 기간이 짧으면 거래 수에서 전멸한다.

**Files:**
- Modify: `backend/app/ask.py` (상수 · `mdd_cap` · `min_trades_for` · `expected_bars` · `score` · `select_top` · 호출 한 곳)
- Modify: `backend/tests/test_ask.py` (고정 거래 수를 보던 시험 교체)
- Test: `backend/tests/test_ask_profiles.py`

**Interfaces:**
- Consumes: `BacktestResult.buy_hold_mdd_pct` (과제 1)
- Produces:
  - `MDD_CAP_BY_PERIOD: dict[str, dict[str, float]]` · `MDD_HOLD_FACTOR: dict[str, float]` · `MDD_CAP_FLOOR: float`
  - `mdd_cap(profile: str, period: str, hold_mdd_pct: Optional[float] = None) -> Optional[float]`
  - `expected_bars(period: str, interval: str) -> int` · `min_trades_for(bars: int) -> int`
  - `select_top(evaluated, profile, n=TOP_N, period="", interval="") -> list[Evaluated]`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`backend/tests/test_ask_profiles.py` 끝에 붙인다(파일 머리의 import 줄에 `from app import ask` 를 더한다):

```python
def test_mdd_cap_grows_with_the_period():
    # 코인이 6개월간 20% 안 빠지는 일은 거의 없다. 1주와 6개월에 같은 숫자를 요구하면
    # 긴 기간이 전멸한다 — 측정: 균형형 6개월에서 상한 사망 72/87.
    assert ask.mdd_cap("stable", "1w") == 8.0
    assert ask.mdd_cap("stable", "6m") == 25.0
    assert ask.mdd_cap("balanced", "1w") == 15.0
    assert ask.mdd_cap("balanced", "6m") == 40.0
    assert ask.mdd_cap("aggressive", "6m") is None, "공격형은 상한이 없다"


def test_mdd_cap_follows_the_market_when_holding_barely_moved():
    # 홀딩이 10% 밖에 안 흔들린 구간에서 안정형이 18% 까지 허용하면 '안정' 이 아니다.
    assert ask.mdd_cap("stable", "3m", 10.0) == 6.0          # min(18, max(5, 10×0.6))
    assert ask.mdd_cap("balanced", "3m", 10.0) == 10.0       # min(32, max(5, 10×1.0))
    # 홀딩이 60% 빠진 구간이라도 절대 상한을 넘기지는 않는다.
    assert ask.mdd_cap("stable", "3m", 60.0) == 18.0
    # 홀딩이 거의 안 흔들린 구간에서 상한이 0 으로 수렴해 전부 탈락하는 것을 막는다.
    assert ask.mdd_cap("stable", "3m", 1.0) == 5.0
    assert ask.mdd_cap("aggressive", "3m", 1.0) is None


def test_mdd_cap_falls_back_to_the_absolute_table_without_a_hold_reading():
    # 묶음 집계와 봉 2개 미만은 홀딩 MDD 를 못 구한다. 그래도 걸러야 하고, 터지면 안 된다.
    assert ask.mdd_cap("stable", "3m", None) == 18.0
    assert ask.mdd_cap("stable", "3m", float("nan")) == 18.0
    assert ask.mdd_cap("stable", "3m", "몰라") == 18.0
    # 표에 없는 기간은 가운데(3개월) 값으로 둔다 — 조용히 상한을 풀지 않는다.
    assert ask.mdd_cap("balanced", "2d") == 32.0


def test_min_trades_follows_the_bar_count():
    # 4시간봉 1주 = 42봉인데 3회를 요구하던 것이 안정형 '며칠' 전멸의 원인이었다(50/57).
    assert ask.expected_bars("1w", "4h") == 42
    assert ask.min_trades_for(42) == 2
    assert ask.expected_bars("3m", "4h") == 546
    assert ask.min_trades_for(546) == 3
    assert ask.expected_bars("6m", "4h") == 1092
    assert ask.min_trades_for(1092) == 7
    assert ask.min_trades_for(0) == 2, "봉이 없어도 바닥은 2 회다"


def test_select_top_counts_trades_against_the_window_length():
    short_window = [_evaluated("J", trades=2, mdd=5.0, ret=20.0, hold=1.0)]
    assert [e.candidate.macro.rule_type.value
            for e in ask.select_top(short_window, "balanced", period="1w", interval="4h")] == ["J"]
    # 같은 2회가 6개월 창에서는 모자라다(7회 필요).
    assert ask.select_top(short_window, "balanced", period="6m", interval="4h") == []


def test_balanced_score_orders_differently_from_aggressive():
    # 측정에서 균형형(수익 − 0.5×MDD)과 공격형(수익)이 상위 3장을 똑같이 냈다 —
    # 초과 수익이 +25~+128%p 로 벌어진 자리에서 10~20 을 빼도 순서가 안 바뀌기 때문이다.
    calm = _evaluated("J", trades=9, mdd=9.0, ret=60.0, hold=0.0).result
    wild = _evaluated("A", trades=9, mdd=81.0, ret=75.0, hold=0.0).result
    assert ask.score("aggressive", wild) > ask.score("aggressive", calm), "공격형은 수익만 본다"
    assert ask.score("balanced", calm) > ask.score("balanced", wild), "균형형은 낙폭을 나눈다"
    assert ask.score("stable", calm) > ask.score("stable", wild)
```

같은 파일에 시험용 도우미를 더한다(파일 안의 다른 시험들이 다 쓴다):

```python
def _evaluated(rule_type: str, *, trades: int, mdd: float, ret: float,
               hold: float | None, hold_mdd: float | None = None) -> ask.Evaluated:
    """백테스트를 돌리지 않고 선별 관문만 보기 위한 가짜 결과.

    rule_type 은 "A" 와 "J" 만 받는다 — MAX_PER_TYPE 가 1 이라 같은 유형이 두 칸을
    차지하지 못하는 것을 보려면 서로 다른 유형 둘이면 충분하다.
    """
    assert rule_type in ("A", "J"), "A 와 J 만 쓴다"
    result = BacktestResult(
        final_return_pct=ret, win_rate_pct=50.0, mdd_pct=mdd, total_trades=trades,
        initial_capital=1_000_000.0, final_equity=1_000_000.0 * (1 + ret / 100.0),
        equity_curve=[], buy_hold_return_pct=hold, buy_hold_mdd_pct=hold_mdd,
    )
    macro = _macro() if rule_type == "A" else _macro(
        rule_type="J", params={"ma_type": "SMA", "fast_period": 20, "slow_period": 60,
                               "initial_capital": 1_000_000}, risk={})
    return ask.Evaluated(ask.Candidate(f"{rule_type} 시험", macro, "template"), result)
```

파일 머리의 import 를 다음으로 맞춘다:

```python
from app import ask
from app.engine.backtest import BacktestResult, _buy_hold_mdd_pct, run_backtest
from app.engine.schema import Macro
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: FAIL — `AttributeError: module 'app.ask' has no attribute 'mdd_cap'`

- [ ] **Step 3: 상한·거래 수 표와 함수를 더한다**

`backend/app/ask.py` 의 import 블록에서 `from .data import NoSpotDataError` 아래에 더한다:

```python
from .data.binance import PERIOD_PRESET_DAYS
```

`MIN_EXCESS_PCT_BY_PERIOD` 줄 아래에 더한다:

```python
# 성향별 MDD 상한 — **기간마다 다르다.** 고정 10% · 20% 를 1주든 6개월이든 똑같이 쓰다가
# 긴 기간이 전멸했다(균형형 6개월 72/87 · 안정형 6개월 43/57 이 여기서 죽었다). 같은 결함을
# min_excess_pct 에서는 2026-10-08 에 기간별 스케일로 고쳤는데 MDD 쪽에 그대로 남아 있었다.
# 공격형은 표에 없다 = 상한 없음.
MDD_CAP_BY_PERIOD: dict[str, dict[str, float]] = {
    "stable": {"1w": 8.0, "1m": 12.0, "3m": 18.0, "6m": 25.0, "1y": 30.0},
    "balanced": {"1w": 15.0, "1m": 22.0, "3m": 32.0, "6m": 40.0, "1y": 50.0},
}
# 절대 상한만으로는 "시장이 10% 밖에 안 흔들린 구간에서 18% 를 허용" 하게 된다. 같은 구간
# 홀딩이 흔들린 만큼에도 곱해 보고 **둘 중 엄한 쪽**을 쓴다.
MDD_HOLD_FACTOR: dict[str, float] = {"stable": 0.6, "balanced": 1.0}
# 홀딩이 거의 안 흔들린 구간에서 상대 상한이 0 으로 수렴해 전부 탈락하는 것을 막는다.
MDD_CAP_FLOOR = 5.0
# 최소 거래 수를 봉 수에 비례시킨다. 150봉에 1회 = "쉬지 않고 일했다" 의 기준.
# 고정 3회였을 때 4시간봉 1주(42봉)에서 안정형 후보 57개 중 50개(88%)가 여기서 죽었다.
MIN_TRADES_FLOOR = 2
BARS_PER_TRADE = 150
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}
```

`min_excess_pct` 함수 바로 **위**에 더한다:

```python
def expected_bars(period: str, interval: str) -> int:
    """이 기간 × 봉에서 백테스트가 보게 될 봉 수(대략).

    equity_curve 길이로 세면 안 된다 — 긴 창은 BACKTEST_EQUITY_MAX_POINTS(1,000)로
    줄어든 뒤라 1,092봉이 1,000 으로 보인다.
    """
    days = PERIOD_PRESET_DAYS.get(str(period), PERIOD_PRESET_DAYS["3m"])
    return max(1, int(days * _BARS_PER_DAY.get(str(interval), 24)))


def min_trades_for(bars: int) -> int:
    """이 창에서 '한 번이라도 일한 매크로' 로 인정할 최소 거래 수."""
    try:
        count = int(bars)
    except (TypeError, ValueError):
        count = 0
    return max(MIN_TRADES_FLOOR, count // BARS_PER_TRADE)


def mdd_cap(profile: str, period: str, hold_mdd_pct: Optional[float] = None) -> Optional[float]:
    """이 성향 · 이 기간에서 허용할 최대 낙폭(%). None 이면 상한이 없다(공격형).

    적용식은 ``min(절대 상한, max(MDD_CAP_FLOOR, 홀딩MDD × 계수))`` 다. 홀딩 MDD 를 못
    구한 결과(묶음 집계 · 봉 2개 미만)는 절대 상한만 쓴다 — 모른다고 상한을 풀면
    "안정형" 이 뜻을 잃는다.
    """
    table = MDD_CAP_BY_PERIOD.get(profile)
    if table is None:
        return None
    absolute = table.get(str(period), table["3m"])
    factor = MDD_HOLD_FACTOR.get(profile)
    if factor is None or hold_mdd_pct is None:
        return absolute
    try:
        hold = float(hold_mdd_pct)
    except (TypeError, ValueError):
        return absolute
    if not math.isfinite(hold):
        return absolute
    return min(absolute, max(MDD_CAP_FLOOR, hold * factor))
```

- [ ] **Step 4: 균형형 점수를 바꾼다**

`backend/app/ask.py` 의 `score` 를 다음으로 바꾼다:

```python
def score(profile: str, result: BacktestResult) -> float:
    """성향이 순서를 가르는 유일한 자리.

    상한이 아니라 **점수**가 성향을 갈라야 한다. 측정: 후보 풀이 같을 때 균형형
    `수익 − 0.5×MDD` 는 공격형 `수익` 과 상위 3장이 똑같았다 — 초과 수익이 +25~+128%p 로
    벌어진 자리에서 10~20 을 빼도 순서가 안 바뀐다. 나누기로 바꾸면 낙폭이 비율로 작용해
    순서가 실제로 달라진다. √ 를 쓰는 이유는 안정형(`수익/MDD`)과도 달라야 하기 때문이다.
    """
    ret = excess_over_hold(result)
    mdd = float(result.mdd_pct)
    if profile == "stable":
        return ret / max(mdd, 1.0)
    if profile == "balanced":
        return ret / math.sqrt(max(mdd, 1.0))
    return ret
```

- [ ] **Step 5: `select_top` 이 두 관문을 새 함수로 묻게 한다**

`backend/app/ask.py` 의 `select_top` 머리와 거르기 부분을 바꾼다. `cap`·`min_trades` 를 `PROFILES` 에서 읽던 두 줄을 지우고 다음으로 바꾼다:

```python
def select_top(evaluated: list[Evaluated], profile: str, n: int = TOP_N,
               period: str = "", interval: str = "") -> list[Evaluated]:
    """MDD 상한 · 최소 거래 수 · 홀딩 문턱으로 거르고 성향 점수로 정렬해 상위 n개.

    같은 rule_type 은 MAX_PER_TYPE 개까지. **넘긴 후보가 적으면 적게 돌려준다** — 세 칸을
    채우려고 홀딩만큼도 못 한 후보를 끼워 넣지 않는다. 하나도 없으면 빈 목록이고, 그때
    화면은 추천 대신 "보여 드릴 만큼 이득이 난 조합이 없었다" 고 말한다.

    상한과 거래 수는 성향 표의 고정값이 아니라 **기간 · 봉 · 그 구간의 홀딩**에서 나온다
    (mdd_cap · min_trades_for). 봉을 안 받으면 거래 수 바닥(MIN_TRADES_FLOOR)만 쓴다.
    """
    min_trades = min_trades_for(expected_bars(period, interval)) if interval else MIN_TRADES_FLOOR
    pool = [
        e for e in evaluated
        if e.result.total_trades >= min_trades
        and _within_mdd_cap(profile, period, e.result)
        and worth_the_macro(e.result, period)
    ]
```

`select_top` 바로 **위**에 도우미를 더한다:

```python
def _within_mdd_cap(profile: str, period: str, result: BacktestResult) -> bool:
    cap = mdd_cap(profile, period, result.buy_hold_mdd_pct)
    return cap is None or float(result.mdd_pct) <= cap
```

- [ ] **Step 6: 호출 자리에 봉을 넘긴다**

`backend/app/ask.py` 의 `run_ask` 안에서 바꾼다:

```python
        top = select_top(evaluated, plan.risk_profile, period=plan.period_preset,
                         interval=plan.interval)
```

- [ ] **Step 7: 새 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: PASS (8 passed)

- [ ] **Step 8: 고정 거래 수를 보던 기존 시험을 바꾼다**

`backend/tests/test_ask.py` 의 `test_select_top_uses_profile_min_trades`(단타형 10회를 보던 시험)를 다음으로 **교체**한다. 함수 이름도 바꾼다:

```python
def test_select_top_counts_trades_against_the_window_not_the_profile():
    # 2026-10-12: 성향마다 고정 거래 수를 요구하던 것을 봉 수 비례로 바꿨다. 같은 후보가
    # 짧은 창에서는 통과하고 긴 창에서는 모자란다 — 성향이 아니라 창이 가른다.
    ev = [_ev("A", trades=2, mdd=4.0, ret=30.0), _ev("J", trades=9, mdd=4.0, ret=30.0)]
    assert [e.candidate.macro.rule_type.value
            for e in ask.select_top(ev, "balanced", period="1w", interval="4h")] == ["A", "J"]
    assert [e.candidate.macro.rule_type.value
            for e in ask.select_top(ev, "balanced", period="6m", interval="4h")] == ["J"]
```

같은 파일에서 이 시험이 쓰는 도우미 이름(`_ev` 또는 그 파일의 기존 이름)과 인자를 파일 안의 다른 시험들과 맞춘다. 기존 도우미가 `buy_hold_return_pct` 를 받지 않으면, 이 시험이 홀딩 문턱을 넘도록 `ret` 를 30.0 으로 두고 `hold` 기본값이 0.0 인지 확인한다(아니면 `hold=0.0` 을 명시한다).

- [ ] **Step 9: 전체 ask 시험을 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask.py tests/test_ask_profiles.py tests/test_ask_exchanges.py -q`
Expected: 단타형 관련 시험만 실패한다(과제 4 에서 지운다). `test_select_top_*` · `test_*_min_trades` · MDD 상한 관련 시험은 모두 PASS. 실패 목록을 과제 4 로 가져간다.

- [ ] **Step 10: 커밋**

```bash
git add backend/app/ask.py backend/tests/test_ask.py backend/tests/test_ask_profiles.py
git commit -m "feat(ask): MDD 상한과 최소 거래 수를 기간·봉에 맞춘다 — 긴 창이 상한에서, 짧은 창이 거래 수에서 전멸했다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 종목 풀 통합 — 왕복 여지 순

성향이 종목을 고르는 일을 없앤다. 성향별 정렬(안정형=저변동 → BTC·ETH, 공격형·단타형=고변동 → 급등주)이 둘 다 홀딩이 이기는 자산을 올렸다.

**Files:**
- Modify: `backend/app/ask_candidates.py` (`build_pool` · `SCALPER_LIQUID_TOP` 제거)
- Modify: `backend/tests/test_ask_candidates.py` (성향별 정렬을 보던 시험 교체)

**Interfaces:**
- Consumes: 없음
- Produces: `build_pool(tickers, *, profile, size=POOL_SIZE, exchange="binance")` — 서명은 그대로(`profile` 은 `fallback_reason` 문구에 계속 쓰인다), 정렬만 성향과 무관해진다. 모듈 함수 `headroom_pct(coin: dict) -> float`.

- [ ] **Step 1: 실패하는 시험을 쓴다**

`backend/tests/test_ask_candidates.py` 의 `test_scalper_profile_prefers_wild_among_most_traded`(57행 근처) · `test_stable_profile_prefers_calm...`(42행 근처) · `test_aggressive_profile_prefers_wild...`(47행 근처) · `test_balanced_profile_prefers_median...`(52행 근처) 네 시험을 지우고 다음을 넣는다:

```python
def test_pool_order_is_the_same_for_every_profile():
    # 2026-10-12: 성향별 정렬을 없앴다. 안정형엔 저변동 대형주(홀딩이 가장 잘 먹히는 자산)가,
    # 공격형엔 급등주(홀딩이 이기는 자산)가 올라와 둘 다 "홀딩을 넘어라" 와 모순이었다.
    orders = [[c["symbol"] for c in ac.build_pool(_tickers(), profile=p, size=10)]
              for p in ("stable", "balanced", "aggressive")]
    assert orders[0] == orders[1] == orders[2]


def test_pool_prefers_oscillation_headroom_over_raw_range():
    # 한 방향으로 크게 간 종목(변동폭 30% · 순변동 +27%)은 매크로가 +33% 를 벌어도
    # 홀딩 +91% 에 진다. 왕복 여지 = 변동폭 − |순변동| 로 재서 뒤로 보낸다.
    pumped = _ticker("KRW-PUMP", volume=9e11, change=27.0, high=130.0, low=100.0)
    swinging = _ticker("KRW-SWING", volume=8e11, change=1.0, high=112.0, low=100.0)
    pool = ac.build_pool([pumped, swinging], profile="balanced", size=10)
    assert [c["symbol"] for c in pool] == ["KRW-SWING", "KRW-PUMP"]
    assert ac.headroom_pct(pool[0]) > ac.headroom_pct(pool[1])


def test_pool_still_cuts_to_the_most_traded_forty():
    # 유동성 없는 코인이 후보에 오르면 안 된다 — 거래대금 상위 LIQUID_TOP 안에서만 고른다.
    assert ac.LIQUID_TOP == 40
    assert not hasattr(ac, "SCALPER_LIQUID_TOP"), "단타형 전용 상한은 사라졌다"
```

`_ticker` 도우미가 그 파일에 없으면 `_tickers` 가 만드는 행 모양에 맞춰 더한다:

```python
def _ticker(symbol: str, *, volume: float, change: float, high: float, low: float) -> dict:
    return {"symbol": symbol, "quoteVolume": volume, "priceChangePercent": change,
            "lastPrice": (high + low) / 2, "highPrice": high, "lowPrice": low}
```

`test_every_profile_fills_the_pool`(223행 근처)의 성향 목록에서 `"scalper"` 를 뺀다:

```python
    for profile in ("stable", "balanced", "aggressive"):
```

`test_stable_profile_skips_the_pumped_coin`(282행 근처)이 `profile="stable"` 의 저변동 정렬에 기대고 있으면, 그 시험의 뜻을 위의 `test_pool_prefers_oscillation_headroom_over_raw_range` 가 대신하므로 지운다.

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_candidates.py -q`
Expected: FAIL — `AttributeError: module 'app.ask_candidates' has no attribute 'headroom_pct'`

- [ ] **Step 3: 풀을 통합한다**

`backend/app/ask_candidates.py` 에서 `SCALPER_LIQUID_TOP = 15` 줄을 지우고, `LIQUID_TOP` 주석을 바꾼다:

```python
# 거래대금 상위 몇 개 안에서 고를지 — 유동성 없는 코인이 후보에 오르지 않게 한다.
LIQUID_TOP = 40
```

`_krw_coins` 아래에 더한다:

```python
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
```

`build_pool` 의 docstring 과 정렬 부분을 바꾼다. `top = SCALPER_LIQUID_TOP if ... else LIQUID_TOP` 줄부터 `return pool[: max(1, size)]` 까지를 다음으로 **교체**한다:

```python
    pool = coins[:LIQUID_TOP]
    if not pool:
        return []

    # 2026-10-12: 성향별 정렬을 없앴다. 성향은 '어떤 종목' 이 아니라 '얼마나 흔들려도
    # 되는가' 로만 갈린다(ask.mdd_cap · ask.score). 세 성향이 같은 목록을 본다.
    pool.sort(key=headroom_pct, reverse=True)
    return pool[: max(1, size)]
```

`build_pool` 의 docstring 첫 줄을 바꾼다:

```python
    """거래가 활발하고 왕복 여지가 큰 순서의 후보 풀. AI 는 이 목록 밖으로 나갈 수 없다."""
```

- [ ] **Step 4: 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_candidates.py tests/test_ask_exchanges.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/ask_candidates.py backend/tests/test_ask_candidates.py
git commit -m "feat(ask): 종목 풀을 성향과 떼고 왕복 여지 순으로 고른다 — 저변동 대형주도 급등주도 홀딩이 이겼다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 단타형 제거 · 화이트리스트 제거 · 15분봉 개방

성향 넷을 셋으로 줄이고, 짧은 봉을 세 성향 모두에 연다. 매매 방식 제한도 없앤다(안정형이 8가지 중 4가지만 보던 탓에 후보 풀이 57개 vs 공격형 114개였다).

**Files:**
- Modify: `backend/app/ask.py` (`RiskProfile` · `PROFILES` · `ALL_RULE_TYPES` · 번역 표 · `to_period` · `to_interval` · `_plan` · `_presets_for` · `_allowed_types` · `_SCALPER_PRESETS` 이름)
- Modify: `backend/app/ask_candidates.py` (`_WATCH_WORDS` 에 새 칸)
- Modify: `backend/tests/test_ask.py` (단타형 시험 제거 · 봉 표 시험 교체)
- Test: `backend/tests/test_ask_profiles.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `RiskProfile = Literal["stable", "balanced", "aggressive"]`
  - `PROFILES: dict[str, dict]` — 칸은 `label` · `futures` 둘만
  - `ALL_RULE_TYPES: tuple[str, ...]` = `("C", "J", "G", "A", "F", "E", "I", "H")`
  - `WATCH_LEVELS = ("rarely", "sometimes", "often", "constantly")`
  - `to_period(horizon: str) -> str` · `to_interval(watch: str, period: str) -> str` — **성향 인자가 없어진다**
  - `_SHORT_BAR_PRESETS` (옛 `_SCALPER_PRESETS`)

- [ ] **Step 1: 실패하는 시험을 쓴다**

`backend/tests/test_ask_profiles.py` 끝에 붙인다:

```python
def test_three_profiles_and_four_watch_levels():
    assert tuple(ask.PROFILES) == ("stable", "balanced", "aggressive")
    assert ask.WATCH_LEVELS == ("rarely", "sometimes", "often", "constantly")
    # 성향 표에 남는 칸은 화면 이름과 선물 허용 여부뿐이다 — 종목·방식·상한은 다른 데서 온다.
    for rules in ask.PROFILES.values():
        assert set(rules) == {"label", "futures"}


def test_watch_frequency_alone_decides_the_candle():
    assert ask.to_interval("rarely", "6m") == "1d"
    assert ask.to_interval("sometimes", "6m") == "4h"
    assert ask.to_interval("often", "6m") == "1h"
    assert ask.to_interval("constantly", "6m") == "15m"
    # 15분봉 × 1년 = 35,040봉으로 봉 상한(20,000)을 넘는 유일한 조합이다. 거절이 아니라
    # 한 칸 넓힌다 — 사용자는 '아주 자주 봐요' 를 고른 것이지 1년을 포기한 게 아니다.
    assert ask.to_interval("constantly", "1y") == "1h"
    # 모르는 값은 가운데로 — 조용히 가장 짧은 봉을 주면 안 된다.
    assert ask.to_interval("뭐라고요", "6m") == "4h"


def test_horizon_alone_decides_the_period():
    assert ask.to_period("days") == "1w"
    assert ask.to_period("weeks") == "3m"
    assert ask.to_period("months") == "6m"
    assert ask.to_period("long") == "1y"


def test_every_profile_sees_all_eight_rule_types():
    # 안정형이 8가지 중 4가지만 보던 탓에 후보가 57개(공격형 114개)였다.
    for profile in ask.PROFILES:
        plan = ask._Plan(risk_profile=profile, market="spot", leverage=1, symbols=["KRW-BTC"],
                         period_preset="3m", interval="4h", exchange="upbit")
        assert set(ask._allowed_types(plan)) == set(ask.ALL_RULE_TYPES)
    # C(적립식)는 레버리지·선물을 못 쓴다 — 이 제약만 남는다.
    futures = ask._Plan(risk_profile="aggressive", market="futures", leverage=2,
                        symbols=["BTCUSDT"], period_preset="3m", interval="4h")
    assert "C" not in ask._allowed_types(futures)


def test_short_bar_presets_layer_over_the_normal_table():
    # 짧은 봉 표는 A·E·F·G·J 다섯뿐이다. 그것만 쓰면 15분봉을 고른 순간 방식이 다섯으로
    # 줄어 화이트리스트를 없앤 효과가 사라지고, build_templates 가 C 를 찾다 KeyError 로 터진다.
    short = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                      period_preset="1m", interval="15m", exchange="upbit")
    presets = ask._presets_for(short)
    assert set(ask.ALL_RULE_TYPES) <= set(presets), "여덟 방식이 다 살아 있어야 한다"
    assert presets["A"] is ask._SHORT_BAR_PRESETS["A"], "짧은 봉 표가 있는 방식은 그 값을"
    assert presets["C"] is ask._PRESETS["C"], "없는 방식(C·H·I)은 일반 표의 값을"

    long_bars = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                          period_preset="3m", interval="4h", exchange="upbit")
    assert ask._presets_for(long_bars) is ask._PRESETS


def test_short_bar_templates_build_for_all_eight_types():
    short = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                      period_preset="1m", interval="15m", exchange="upbit",
                      account_balance=1_000_000.0)
    kinds = {c.macro.rule_type.value for c in ask.build_templates(short)}
    assert kinds == set(ask.ALL_RULE_TYPES)


def test_no_scalper_left_in_the_module():
    assert "scalper" not in str(ask.RiskProfile)
    for name in ("_SCALPER_PRESETS", "_HORIZON_PERIOD_SHORT", "_WATCH_INTERVAL_SHORT"):
        assert not hasattr(ask, name), f"{name} 이 남아 있다"
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -k "profile or watch or horizon or preset or scalper" -q`
Expected: FAIL — `AssertionError: tuple(ask.PROFILES) == ('stable','balanced','aggressive','scalper')`

- [ ] **Step 3: 성향 표와 번역 표를 바꾼다**

`backend/app/ask.py` 의 `RiskProfile` · `PROFILES` 블록을 다음으로 **교체**한다:

```python
RiskProfile = Literal["stable", "balanced", "aggressive"]

# 성향별 규칙 — 화면 이름과 선물 허용 여부만 남는다.
#
# 2026-10-12: rule_types(매매 방식 화이트리스트) · mdd_cap(기간과 무관한 고정 상한) ·
# short(단타형 전용 봉) · min_trades(봉 수와 무관한 고정값) · max_symbols(쓰이지 않던 칸)를
# 모두 뺐다. 성향은 "어떤 종목 · 어떤 방식을 보는가" 가 아니라 "얼마나 흔들려도 되는가" 로만
# 갈린다 — 상한은 mdd_cap, 순서는 score 가 맡는다. 사람이 '안정형' 을 고르는 이유는 내 돈이
# 덜 흔들렸으면 하는 것이지 비트코인을 사고 싶어서가 아니다.
PROFILES: dict[str, dict] = {
    "stable": {"label": "안정형", "futures": False},
    "balanced": {"label": "균형형", "futures": True},
    "aggressive": {"label": "공격형", "futures": True},
}

# 세 성향이 여덟 방식을 다 본다. 순서는 템플릿 우선순위(앞쪽이 시간 예산에서 먼저 평가된다).
ALL_RULE_TYPES = ("C", "J", "G", "A", "F", "E", "I", "H")
```

`HORIZONS` · `WATCH_LEVELS` · 번역 표와 두 함수를 다음으로 **교체**한다:

```python
HORIZONS = ("days", "weeks", "months", "long")
WATCH_LEVELS = ("rarely", "sometimes", "often", "constantly")

_HORIZON_PERIOD = {"days": "1w", "weeks": "3m", "months": "6m", "long": "1y"}
# 확인 빈도가 곧 봉 간격이다. 'constantly'(15분)는 2026-10-12 에 더했다 — 단타형을 없애는
# 대신, 짧게 보고 싶은 사람이 성향을 포기하지 않아도 되게 하려고.
_WATCH_INTERVAL = {"rarely": "1d", "sometimes": "4h", "often": "1h", "constantly": "15m"}


def to_period(horizon: str) -> str:
    return _HORIZON_PERIOD.get(horizon, _HORIZON_PERIOD["weeks"])


def to_interval(watch: str, period: str) -> str:
    interval = _WATCH_INTERVAL.get(watch, _WATCH_INTERVAL["sometimes"])
    # 15분봉 × 1년 = 35,040봉으로 백테스트 봉 상한(20,000)을 넘는 **유일한** 조합이다.
    # 거절하지 않고 봉을 한 칸 넓힌다 — 사용자는 자주 보겠다고 한 것이지 1년을 포기한 게 아니다.
    return "1h" if interval == "15m" and period == "1y" else interval
```

`CandidatesRequest` 의 `watch_frequency` 를 바꾼다:

```python
    watch_frequency: Literal["rarely", "sometimes", "often", "constantly"]
```

`_plan` 의 두 호출에서 성향 인자를 뺀다:

```python
def _plan(answers: CandidatesRequest, symbol: str) -> _Plan:
    period = to_period(answers.invest_horizon)
    return _Plan(
        risk_profile=answers.risk_profile, market=answers.market, leverage=answers.leverage,
        symbols=[symbol], period_preset=period,
        interval=to_interval(answers.watch_frequency, period),
        exchange=answers.exchange, account_balance=answers.account_balance,
    )
```

- [ ] **Step 4: 프리셋을 봉 간격으로 고르게 하고 화이트리스트를 없앤다**

`backend/app/ask.py` 에서 `_SCALPER_PRESETS` 를 `_SHORT_BAR_PRESETS` 로 이름을 바꾸고 그 위 주석을 바꾼다:

```python
# 짧은 봉 전용 프리셋 — 좁은 익절·손절과 짧은 지표 기간. 성향이 아니라 **봉의 성질**에
# 딸린 표다(그래서 이름이 SHORT_BAR 다). 담긴 방식은 A·E·F·G·J 다섯이고, 나머지
# (C·H·I)는 일반 표에서 가져와 겹쳐 쓴다 — _presets_for 참고.
_SHORT_BAR_PRESETS: dict[str, list[dict]] = {
```

`_presets_for` 와 `_allowed_types` 를 다음으로 **교체**한다:

```python
def _presets_for(req: _Plan) -> dict[str, list[dict]]:
    """프리셋은 성향이 아니라 **봉 간격**으로 고른다.

    예전에는 PROFILES[성향]["short"] 로 갈랐는데 단타형이 없어지면서 기준이 사라졌다.
    짧은 봉 표는 다섯 방식뿐이라 그것만 쓰면 15분봉을 고른 순간 방식이 여덟 → 다섯으로
    줄어 화이트리스트를 없앤 효과가 사라지고, build_templates 가 C 를 찾다 터진다.
    그래서 **겹쳐 쓴다** — 짧은 봉 표에 있는 방식은 그 값을, 없는 방식은 일반 표의 값을.
    """
    if req.interval not in SHORT_INTERVALS:
        return _PRESETS
    return {**_PRESETS, **_SHORT_BAR_PRESETS}


def _allowed_types(req: _Plan) -> tuple[str, ...]:
    """세 성향 모두 여덟 방식을 본다(2026-10-12). 남은 제약은 시장뿐이다."""
    types = ALL_RULE_TYPES
    # C(적립식) 는 레버리지·선물을 못 쓴다.
    if req.market == "futures":
        types = tuple(t for t in types if t != "C")
    return types
```

- [ ] **Step 5: AI 프롬프트의 빈도 낱말에도 칸을 더한다**

`backend/app/ask_candidates.py` 의 `_WATCH_WORDS` 에 새 칸을 더한다. 빠뜨리면 프롬프트에 `constantly` 라는 영문이 그대로 들어간다:

```python
_WATCH_WORDS = {"rarely": "거의 못 봐요", "sometimes": "가끔 봐요", "often": "수시로 봐요",
                "constantly": "아주 자주 봐요"}
```

- [ ] **Step 6: 남은 참조를 지운다**

Run: `cd backend && grep -rn "scalper\|SCALPER\|_HORIZON_PERIOD_SHORT\|_WATCH_INTERVAL_SHORT\|max_symbols\|rule_types\|MIN_TRADES\b" app/ | grep -v __pycache__`
Expected: 결과가 없거나, `MIN_TRADES_FLOOR` 만 나온다. `MIN_TRADES = 3`(ask.py 48행 근처)은 쓰이지 않는 상수이므로 함께 지운다. 남은 것이 있으면 각각 지운다.

- [ ] **Step 7: 새 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: PASS (15 passed)

- [ ] **Step 8: 기존 시험에서 단타형을 지운다**

`backend/tests/test_ask.py` 에서 다음 시험을 **지운다**(단타형이 없어졌으므로 뜻을 잃는다. 대체 시험은 `test_ask_profiles.py` 에 있다):

- `test_scalper_profile_candidates_use_short_presets_and_skip_slow_types` (377행 근처)
- `test_scalper_ask_end_to_end` (411행 근처)
- `test_scalper_ask_returns_short_macros_when_trades_suffice` (431행 근처)
- `test_scalper_period_is_clamped_to_short_presets` (571행 근처)
- `test_scalper_watch_maps_to_short_intervals` (585행 근처)

`test_horizon_maps_to_backtest_period`(565행 근처)와 `test_watch_maps_to_interval`(579행 근처)의 호출에서 성향 인자를 뺀다:

```python
def test_horizon_maps_to_backtest_period():
    assert ask.to_period("days") == "1w"
    assert ask.to_period("weeks") == "3m"
    assert ask.to_period("months") == "6m"
    assert ask.to_period("long") == "1y"


def test_watch_maps_to_interval():
    assert ask.to_interval("rarely", "6m") == "1d"
    assert ask.to_interval("sometimes", "6m") == "4h"
    assert ask.to_interval("often", "6m") == "1h"
```

`test_every_reachable_pair_fits_backtest_bar_cap`(462행 근처)을 성향 없는 모양으로 바꾼다:

```python
def test_every_reachable_pair_fits_backtest_bar_cap():
    # v2 — 봉×기간 짝을 사람이 직접 보내지 않고 to_period·to_interval 이 답변(투자 기간·볼
    # 빈도)에서 계산한다. 그 계산이 만들 수 있는 모든 조합이 이 상한을 넘지 않는지 본다.
    now_ms = int(time.time() * 1000)
    for horizon in ask.HORIZONS:
        period = ask.to_period(horizon)
        for watch in ask.WATCH_LEVELS:
            interval = ask.to_interval(watch, period)
            days = PERIOD_PRESET_DAYS[period]
            start_ms = now_ms - days * 86_400_000
            bars = _expected_bar_count(interval, start_ms, now_ms)
            assert bars <= MAX_BACKTEST_BARS, f"{horizon}·{watch} → {interval}×{period} = {bars}"
```

1032행 근처의 프리셋 표를 도는 시험에서 `(ask._SCALPER_PRESETS, "scalper", "1w", "5m")` 줄을 `(ask._SHORT_BAR_PRESETS, "balanced", "1m", "15m")` 로 바꾼다.

- [ ] **Step 9: 전체 백엔드 ask 시험을 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask.py tests/test_ask_profiles.py tests/test_ask_candidates.py tests/test_ask_exchanges.py tests/test_db_external_work.py -q`
Expected: PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/app/ask.py backend/app/ask_candidates.py backend/tests/test_ask.py backend/tests/test_ask_profiles.py
git commit -m "feat(ask): 단타형을 없애고 15분봉을 세 성향에 연다 — 매매 방식 제한도 걷어낸다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 진행 중 세션 이관

저장된 세션에 `risk_profile: "scalper"` 가 남아 있으면 `CandidatesRequest` 검증이 터진다. 지금도 `_load_flow` 의 포괄 `except` 가 410 으로 바꿔 주지만, 문구가 "오래된 형식" 이라 왜 그런지 알 수 없고 **횟수가 돌아오지 않는다.**

**Files:**
- Modify: `backend/app/ask.py` (`_load_flow` · `_retire_flow`)
- Test: `backend/tests/test_ask_profiles.py`

**Interfaces:**
- Consumes: `AskError` · `AskMacroSession` · `_credits_today` (기존)
- Produces: `PROFILE_GONE_MESSAGE: str` · `_retire_flow(db, user, row) -> None`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`backend/tests/test_ask_profiles.py` 끝에 붙인다. 이 시험은 DB 가 필요하므로 `test_ask.py` 가 쓰는 conftest 의 세션 도우미를 그대로 쓴다:

```python
def test_a_stored_scalper_session_refunds_instead_of_crashing():
    """단타형으로 만들어 둔 세션이 성향 삭제 뒤에 열리면 — 500 이 아니라 안내 + 환불.

    세션 수명이 30분(SESSION_TTL_MS)이라 창은 짧지만 0 은 아니다. 배포 순간에 물어보던
    사람이 '종목 고르기' 를 누르면 저장된 scalper 가 CandidatesRequest 검증에서 터진다.
    """
    import json

    import pytest
    from sqlmodel import select

    from app.db import AskMacroSession, get_session

    # 1) 정상 흐름으로 세션 하나를 만든다 — 준비 코드는 test_ask.py 의 방식을 따른다.
    session_id = _make_flow_session()

    # 2) 저장된 답변의 성향만 옛 값으로 되돌린다(배포 직전에 만들어진 세션 그대로).
    with get_session() as db:
        row = db.get(AskMacroSession, session_id)
        saved = json.loads(row.request_json)
        saved["risk_profile"] = "scalper"
        row.request_json = json.dumps(saved, ensure_ascii=False)
        row.refunded = False
        db.add(row)
        db.commit()

    # 3) 읽으려 하면 안내와 함께 거절하고 횟수를 돌려준다.
    with get_session() as db:
        user = db.exec(select(User)).first()
        with pytest.raises(ask.AskError) as caught:
            ask._load_flow(db, user, session_id)
    assert caught.value.status == 410
    assert "성향" in caught.value.message, "왜 못 쓰는지 말해야 한다"

    with get_session() as db:
        assert db.get(AskMacroSession, session_id).refunded is True, "횟수를 돌려줘야 한다"
```

이 시험은 `_make_flow_session()` 과 `User` 를 쓴다. **구현자가 할 일:**

1. `backend/tests/test_ask.py` 에서 `/api/ask/candidates` 를 불러 세션을 만드는 기존 시험 하나(예: `test_candidates_creates_a_flow_session` 또는 그에 준하는 것 — `_candidates_body` 를 쓰는 첫 시험)를 읽는다.
2. 그 준비 코드(fixture · `_candidates_body` · `monkeypatch` 로 티커·AI를 꽂는 방식)를 `test_ask_profiles.py` 로 옮겨 `_make_flow_session() -> int` 도우미로 묶는다. 세션 행의 `id` 를 돌려준다.
3. `from app.db import User` 를 파일 머리의 import 에 더한다.

확인할 것은 셋이다 — 상태 410 · 문구에 "성향" · `refunded is True`. 없는 도우미를 새로 만들지 않는다. `AskError` 의 속성 이름(`status` · `message`)은 `cd backend && grep -n "class AskError" -A 6 app/ask.py` 로 확인해 맞춘다.

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -k scalper_session -q`
Expected: FAIL — 410 은 나오지만 `detail` 에 "성향" 이 없고 `refunded` 가 `False` 다

- [ ] **Step 3: 이관 경로를 더한다**

`backend/app/ask.py` 의 `_load_flow` **위**에 더한다:

```python
# 2026-10-12 단타형 삭제 — 저장된 세션에 남은 성향을 만났을 때 하는 말.
PROFILE_GONE_MESSAGE = "성향 구성이 바뀌었어요. 처음부터 다시 물어봐 주세요."
# 더 쓸 수 없게 된 성향들. 이 목록이 비면 이 경로는 아무 일도 하지 않는다.
RETIRED_PROFILES = frozenset({"scalper"})


def _retire_flow(db: Session, user: User, row: AskMacroSession) -> None:
    """옛 성향으로 만든 세션을 거두고 횟수를 돌려준다.

    받을 이유가 없는 차감이다 — 사용자가 잘못한 것이 없고, 그 세션으로는 아무것도 못 본다.
    추가권으로 물어본 세션이면 추가권도 같이 풀어 준다(run_ask 의 환불과 같은 방식).
    """
    if row.refunded:
        return
    row.refunded = True
    db.add(row)
    if row.paid:
        for credit in _credits_today(db, user):
            if credit.used_session_id == row.id:
                credit.used_session_id = None
                db.add(credit)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # 환불에 실패해도 세션은 못 쓴다 — 아래에서 410 을 던진다. 조용히 삼키지 말고 남긴다.
        log.warning("ask: 옛 성향 세션 환불 실패")
```

`_load_flow` 의 `try: answers = CandidatesRequest(...)` 블록을 다음으로 **교체**한다:

```python
    try:
        saved = json.loads(row.request_json)
    except Exception:
        raise AskError(410, "질문 기록이 오래된 형식이에요. 처음부터 다시 물어봐 주세요.")
    # 없어진 성향으로 만든 세션은 검증에서 터져 화면이 500 으로 죽는다. 왜 못 쓰는지
    # 말해 주고 횟수를 돌려준다 — "오래된 형식" 으로 뭉개면 사용자는 자기 잘못인 줄 안다.
    if isinstance(saved, dict) and saved.get("risk_profile") in RETIRED_PROFILES:
        _retire_flow(db, user, row)
        raise AskError(410, PROFILE_GONE_MESSAGE)
    try:
        answers = CandidatesRequest(**saved)
    except Exception:
        raise AskError(410, "질문 기록이 오래된 형식이에요. 처음부터 다시 물어봐 주세요.")
    return row, answers
```

`_credits_today` 가 `_load_flow` 보다 아래에 정의돼 있어도 괜찮다(호출 시점에 이미 모듈이 다 읽힌다). 함수가 없으면 `grep -n "_credits_today" app/ask.py` 로 실제 이름을 확인해 맞춘다.

- [ ] **Step 4: 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -q`
Expected: PASS

- [ ] **Step 5: 끝점 시험을 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask.py tests/test_db_external_work.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/app/ask.py backend/tests/test_ask_profiles.py
git commit -m "fix(ask): 없어진 성향으로 만든 세션은 횟수를 돌려주고 안내한다 — 검증 예외가 화면을 500 으로 죽였다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 프런트 카드 — 성향 셋 · 빈도 넷 · 짧은 봉 경고

화면의 선택지를 서버와 맞춘다. 단타형 칸을 지우고 확인 빈도에 '아주 자주 봐요' 를 더하고, 짧은 봉 경고를 **고르는 자리**(확인 빈도 단계)로 옮긴다.

**Files:**
- Modify: `frontend/src/lib/askCopy.js`
- Modify: `frontend/src/lib/askFlow.js:7`
- Modify: `frontend/src/components/AskParrotDialog.jsx` (import · 589행 근처 · 665행 근처)
- Modify: `frontend/tests/askNoEdge.test.js` (단타형 경고를 보던 시험 교체)
- Test: `frontend/tests/askProfiles.test.js` (신설)

**Interfaces:**
- Consumes: 없음
- Produces: `PROFILES`(셋) · `WATCH_LEVELS`(넷) · `SHORT_BAR_WARN` — `SCALPER_NOTE` · `SCALPER_PICK_WARN` 은 사라진다.

- [ ] **Step 1: 실패하는 시험을 쓴다**

새 파일 `frontend/tests/askProfiles.test.js`:

```js
// 성향 재설계(2026-10-12) — 화면 선택지가 서버와 같은가, 경고가 고르는 자리에 있는가.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PROFILES, SHORT_BAR_WARN, WATCH_LEVELS } from "../src/lib/askCopy.js";
import { PROFILE_ORDER } from "../src/lib/askFlow.js";

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("성향은 셋이고 순서가 위험 순이다", () => {
  assert.deepEqual(PROFILES.map((p) => p.value), ["stable", "balanced", "aggressive"]);
  assert.deepEqual(PROFILE_ORDER, ["stable", "balanced", "aggressive"],
    "'더 안정적으로 · 더 공격적으로' 가 이 순서를 타고 움직인다");
});

test("성향 힌트가 고정 숫자를 약속하지 않는다", () => {
  // 상한이 기간마다 달라졌다(1주 8% ~ 1년 30%). "-10%까지" 는 이제 거짓이다.
  for (const profile of PROFILES) {
    assert.ok(!/-?\d+\s*%/.test(profile.hint), `${profile.label} 힌트에 숫자가 남았다: ${profile.hint}`);
  }
});

test("확인 빈도에 15분봉 칸이 있다", () => {
  assert.deepEqual(WATCH_LEVELS.map((w) => w.value),
    ["rarely", "sometimes", "often", "constantly"]);
  assert.match(WATCH_LEVELS[3].label, /자주/);
});

test("짧은 봉 경고는 봉을 고르는 자리에 있고 숫자를 댄다", () => {
  assert.match(SHORT_BAR_WARN, /15분/, "어느 봉인지 말해야 한다");
  assert.match(SHORT_BAR_WARN, /64/, "얼마나 어려운지 숫자로 말해야 한다");
  assert.match(SHORT_BAR_WARN, /수수료|슬리피지/, "왜 어려운지 말해야 한다");
  const dialog = source("../src/components/AskParrotDialog.jsx");
  assert.match(dialog, /state\.step === "watch" \?[\s\S]{0,200}SHORT_BAR_WARN/,
    "성향 단계가 아니라 빈도 단계에서 보여야 한다 — 짧은 봉은 빈도가 정한다");
});

test("단타형 흔적이 화면에 남지 않았다", () => {
  for (const path of ["../src/lib/askCopy.js", "../src/lib/askFlow.js",
                      "../src/components/AskParrotDialog.jsx"]) {
    const text = source(path);
    assert.ok(!/scalper/i.test(text), `${path} 에 scalper 가 남았다`);
    assert.ok(!text.includes("단타형"), `${path} 에 단타형이 남았다`);
  }
});
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd frontend && node --test "tests/askProfiles.test.js"`
Expected: FAIL — `SyntaxError: The requested module '../src/lib/askCopy.js' does not provide an export named 'SHORT_BAR_WARN'`

- [ ] **Step 3: 문구와 선택지를 바꾼다**

`frontend/src/lib/askCopy.js` 의 `PROFILES` 를 바꾼다:

```js
// 성향은 종목이 아니라 '얼마나 흔들려도 되는가' 다(2026-10-12). 힌트에 고정 숫자를 쓰지
// 않는다 — 허용 낙폭이 기간마다 다르다(1주 8% ~ 1년 30%).
export const PROFILES = [
  { value: "stable", label: "안정형", hint: "낙폭을 가장 좁게" },
  { value: "balanced", label: "균형형", hint: "그 사이에서" },
  { value: "aggressive", label: "공격형", hint: "낙폭 제한 없음" },
];
```

`WATCH_LEVELS` 를 바꾼다:

```js
export const WATCH_LEVELS = [
  { value: "rarely", label: "거의 못 봐요", hint: "하루 한 번쯤" },
  { value: "sometimes", label: "가끔 봐요", hint: "몇 시간에 한 번" },
  { value: "often", label: "수시로 봐요", hint: "한 시간에 한 번" },
  { value: "constantly", label: "아주 자주 봐요", hint: "15분에 한 번" },
];
```

`SCALPER_NOTE` 와 `SCALPER_PICK_WARN` 두 줄(주석 포함)을 지우고 그 자리에 넣는다:

```js
// 15분봉을 세 성향 모두에 연 뒤(2026-10-12) 고르는 자리에서 미리 말한다. 실측: 짧은 봉
// 후보의 손실 비율이 1분봉 89% · 5분봉 78% · 15분봉 64% 였다. 왕복 마찰(수수료+슬리피지)을
// 짧은 봉에서 넘기기 어렵다. 고르고 나서 빈 화면을 보는 것보다 고를 때 아는 쪽이 낫다.
export const SHORT_BAR_WARN = "'아주 자주 봐요' 는 15분봉이에요 — 과거 데이터에서 15분봉 후보의 64%가 손실이었어요. 사고팔 때마다 수수료·슬리피지가 붙기 때문이에요.";
```

- [ ] **Step 4: 성향 순서와 경고 위치를 바꾼다**

`frontend/src/lib/askFlow.js:7`:

```js
export const PROFILE_ORDER = ["stable", "balanced", "aggressive"];
```

`frontend/src/components/AskParrotDialog.jsx`:
- import 에서 `SCALPER_PICK_WARN` → `SHORT_BAR_WARN` 으로, `SCALPER_NOTE` 는 **삭제**한다(13~16행 근처).
- 589행 근처의 성향 단계 경고를 빈도 단계로 바꾼다:

```jsx
        {state.step === "watch" ? <p className="ask-note ask-in" style={{ "--i": options.length + 1 }} role="note">{SHORT_BAR_WARN}</p> : null}
```

- 665행 근처의 결과 화면 단타형 주석 줄을 **삭제**한다:

```jsx
        {state.answers.profile === "scalper" ? <p className="ask-note ask-in" ...>{SCALPER_NOTE}</p> : null}
```

이 줄을 지우면 아래 `extraBlock(results.length + 5)` · `followUps(results.length + 6)` 의 `--i` 번호는 그대로 둔다(애니메이션 순서일 뿐이고 빈 번호가 있어도 보이지 않는다).

- [ ] **Step 5: 단타형 경고를 보던 기존 시험을 바꾼다**

`frontend/tests/askNoEdge.test.js` 에서 `SCALPER_PICK_WARN` 을 import 에서 빼고, 그것을 쓰는 시험(77~80행 근처)을 **지운다** — 대체 시험이 `askProfiles.test.js` 에 있다.

- [ ] **Step 6: 시험과 린트를 돌린다**

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS (askProfiles 5개 통과, askNoEdge 통과)

Run: `cd frontend && npm run lint`
Expected: 통과 (쓰이지 않는 import 가 남으면 oxlint 가 잡는다)

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/lib/askCopy.js frontend/src/lib/askFlow.js frontend/src/components/AskParrotDialog.jsx frontend/tests/askProfiles.test.js frontend/tests/askNoEdge.test.js
git commit -m "feat(web): 성향을 셋으로 줄이고 '아주 자주 봐요'(15분봉)를 더한다 — 경고는 봉을 고르는 자리로

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 홀딩 비교 표현 · 빈 화면에 숫자

"그냥 들고 있는 게 나았어요" 는 비대칭을 숨긴다. 홀딩의 매수일은 기간을 고른 순간 **자동으로 정해진 날**(구간 첫 봉)이고, 매크로는 살 날을 스스로 찾는다. 그리고 지금 빈 화면은 한 줄만 보여 주고 끝나서 매크로가 +34.82% 를 벌었다는 사실이 사라진다.

**Files:**
- Modify: `backend/app/ask.py` (`_no_edge_best` · `run_ask` 응답)
- Modify: `frontend/src/lib/askCopy.js` (`NO_EDGE_TEXT` · `NO_EDGE_NOTE` · `HOLD_LABEL` · `VS_HOLD_LABEL`)
- Modify: `frontend/src/lib/askView.js` (`noEdgeDetail`)
- Modify: `frontend/src/lib/askFlow.js` (`noEdgeBest` 상태)
- Modify: `frontend/src/components/AskParrotDialog.jsx` (504행 근처 · 631행 근처)
- Modify: `frontend/src/lib/glossary.js` (`buy_hold`)
- Test: `backend/tests/test_ask_profiles.py` · `frontend/tests/askNoEdge.test.js`

**Interfaces:**
- Consumes: `Evaluated` (기존)
- Produces:
  - 백엔드: `_no_edge_best(evaluated: list[Evaluated]) -> Optional[dict]` — `{"return_pct": float, "hold_return_pct": float | None, "rule_label": str}`. `/api/ask/macros` 응답에 `no_edge_best` 키.
  - 프런트: `noEdgeDetail(best: object | null) -> string`

- [ ] **Step 1: 실패하는 시험을 쓴다 — 백엔드**

`backend/tests/test_ask_profiles.py` 끝에 붙인다:

```python
def test_no_edge_best_carries_the_numbers_that_would_be_lost():
    # 빈 화면이 한 줄로 끝나면 "매크로가 +34.82% 를 벌었다" 는 사실이 사라진다.
    evaluated = [
        _evaluated("A", trades=5, mdd=10.0, ret=34.82, hold=38.67),
        _evaluated("J", trades=5, mdd=8.0, ret=12.0, hold=38.67),
    ]
    best = ask._no_edge_best(evaluated)
    assert best["return_pct"] == 34.82
    assert best["hold_return_pct"] == 38.67
    assert best["rule_label"], "어떤 방식이었는지도 말해 준다"


def test_no_edge_best_omits_a_hold_figure_it_does_not_have():
    # 홀딩 기준을 못 구한 결과가 1등일 수 있다. 화면이 "null%" 를 찍으면 안 된다.
    best = ask._no_edge_best([_evaluated("A", trades=5, mdd=10.0, ret=5.0, hold=None)])
    assert best["return_pct"] == 5.0
    assert best["hold_return_pct"] is None
    assert ask._no_edge_best([]) is None
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -k no_edge_best -q`
Expected: FAIL — `AttributeError: module 'app.ask' has no attribute '_no_edge_best'`

- [ ] **Step 3: 빈 화면용 숫자를 낸다**

`backend/app/ask.py` 의 `_result_view` **위**에 더한다:

```python
def _no_edge_best(evaluated: list[Evaluated]) -> Optional[dict]:
    """홀딩 문턱을 넘은 게 없어 빈 화면이 될 때, 가장 나았던 조합의 숫자.

    왜 싣나: 지금 빈 화면은 "넘긴 조합이 없었어요" 한 줄로 끝난다. 그 사이에 매크로가
    +34.82% 를 벌었고 같은 기간 보유가 +38.67% 였다는 사실이 사라진다 — 사용자는
    "아무것도 안 됐다" 로 읽는다. 둘을 나란히 보여 주면 '왜 안 보여 주는지' 가 보인다.
    """
    if not evaluated:
        return None
    best = max(evaluated, key=lambda e: float(e.result.final_return_pct))
    hold = best.result.buy_hold_return_pct
    return {
        "return_pct": round(float(best.result.final_return_pct), 2),
        "hold_return_pct": (round(float(hold), 2) if hold is not None else None),
        "rule_label": RULE_LABELS.get(best.candidate.macro.rule_type.value, ""),
    }
```

`run_ask` 의 `no_edge = ...` 줄 아래에 더한다:

```python
        no_edge_best = _no_edge_best(evaluated) if no_edge else None
```

그리고 반환 dict 의 `"no_edge": no_edge,` 다음 줄에 더한다:

```python
        # 빈 화면이 숫자를 싣는다 — 가장 나았던 조합과 같은 기간 '첫날 사서 보유'.
        "no_edge_best": no_edge_best,
```

- [ ] **Step 4: 백엔드 시험이 통과하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py tests/test_ask.py -q`
Expected: PASS

- [ ] **Step 5: 실패하는 시험을 쓴다 — 프런트**

`frontend/tests/askNoEdge.test.js` 끝에 붙인다. 파일 머리의 import 두 줄을 이렇게 맞춘다 — `noEdgeDetail` 은 `askView.js`, `HOLD_LABEL` 은 `askCopy.js` 에서 온다:

```js
import { noEdgeDetail, resultsHeadline } from "../src/lib/askView.js";
import { HOLD_LABEL, NO_EDGE_NOTE, NO_EDGE_TEXT, NO_RESULTS_TEXT, REFUNDED_TEXT } from "../src/lib/askCopy.js";
```

```js
test("빈 화면이 숫자를 싣는다", () => {
  // 한 줄로 끝나면 매크로가 벌었던 사실이 사라진다.
  const line = noEdgeDetail({ return_pct: 34.82, hold_return_pct: 38.67, rule_label: "트레일링 스탑" });
  assert.match(line, /34\.8/, "매크로가 얼마 벌었는지");
  assert.match(line, /38\.7/, "같은 기간 보유는 얼마였는지");
  assert.match(line, /첫날/, "그 기준이 첫날 매수라는 전제를 드러내야 한다");
});

test("없는 홀딩 숫자를 지어내지 않는다", () => {
  const line = noEdgeDetail({ return_pct: 5.0, hold_return_pct: null, rule_label: "RSI 조건" });
  assert.match(line, /5\.0/);
  assert.ok(!/null|NaN|—%/.test(line), `지어낸 값이 섞였다: ${line}`);
  assert.equal(noEdgeDetail(null), "");
});

test("홀딩 라벨이 전제를 드러낸다", () => {
  // '그냥 들고 있기' 는 매수일이 공짜로 주어진 것을 숨긴다 — 구간 첫 봉이 그 날이다.
  assert.match(HOLD_LABEL, /첫날/);
  assert.ok(!NO_EDGE_TEXT.includes("그냥 들고 있는 게 나았"),
    "매수지점도 같은 기준으로 정해진 날이라 '들고 있는 게 나았다' 는 헷갈린다");
});
```

- [ ] **Step 6: 시험이 실패하는 것을 확인한다**

Run: `cd frontend && node --test "tests/askNoEdge.test.js"`
Expected: FAIL — `does not provide an export named 'noEdgeDetail'`

- [ ] **Step 7: 문구와 표시 함수를 바꾼다**

`frontend/src/lib/askCopy.js` 의 네 줄을 바꾼다:

```js
// 보여 준 조합이 전부 '첫날 사서 보유' 에 졌을 때 (2026-09-23) — 매크로가 늘 답은 아니라고 먼저 말한다.
// 2026-10-12: "그냥 들고 있는 게 나았어요" 를 버렸다. 홀딩의 매수일은 사용자가 기간을 고른
// 순간 자동으로 정해진 날(구간 첫 봉)이고, 매크로는 살 날을 스스로 찾는다 — 그 비대칭을
// 숨기는 말이었다. 그리고 빈 화면이 숫자를 싣는다(askView.noEdgeDetail).
export const NO_EDGE_TEXT = "이번 조건에선 보여 드릴 만큼 이득이 난 조합이 없었어요.";
export const NO_EDGE_NOTE = "기준은 같은 기간을 '첫날 사서 그냥 두기' 예요 — 그 날짜는 기간을 고르면 자동으로 정해지고, 매크로는 살 날을 스스로 찾아야 해요. 기간이나 종목, 보는 빈도를 바꿔 다시 물어봐 주세요.";
```

```js
export const HOLD_LABEL = "첫날 사서 보유";
export const VS_HOLD_LABEL = "첫날 보유 대비";
```

`frontend/src/lib/askView.js` 의 `resultsHeadline` 아래에 더한다:

```js
// 빈 화면에 싣는 숫자 — 가장 나았던 조합과 같은 기간 '첫날 사서 보유'.
// 서버가 준 값만 쓴다. 홀딩 기준을 못 구한 결과가 1등일 수 있어 그때는 그 쪽만 뺀다.
export function noEdgeDetail(best) {
  const ret = num(best?.return_pct);
  if (ret == null) return "";
  const hold = num(best?.hold_return_pct);
  const what = best?.rule_label ? `가장 나았던 조합(${best.rule_label})이` : "가장 나았던 조합이";
  if (hold == null) return `${what} ${signedPct(ret, 1)} 였어요. 같은 기간 보유 수익률은 구하지 못했어요.`;
  return `${what} ${signedPct(ret, 1)} 였는데, 같은 기간 그 종목을 첫날 사서 그냥 뒀을 때 ${signedPct(hold, 1)} 였어요.`;
}
```

`frontend/src/lib/glossary.js` 의 `buy_hold` 두 곳을 바꾼다:

```js
  buy_hold:
    "기간 **첫날**에 사서 끝까지 들고 있었을 때(HODL)의 수익률. 그 날짜는 기간을 고르면 자동으로 정해져요 — 매크로는 살 날을 스스로 찾아야 하니 서로 공평한 비교는 아니에요. 그래도 '복잡한 매매가 값을 했는지' 의 기준으로는 쓸 만해요.",
```

```js
  slippage: "슬리피지", mdd: "MDD", win_rate: "승률", buy_hold: "첫날 사서 보유", sharpe: "샤프지수",
```

- [ ] **Step 8: 상태에 숫자를 싣는다**

`frontend/src/lib/askFlow.js` 의 `initialState()` 에서:

```js
    results: null, noEdge: false, noEdgeBest: null, refunded: false, remaining: null, error: "",
```

135행 근처의 `results` 처리에서:

```js
               noEdge: !!action.noEdge, noEdgeBest: action.noEdgeBest || null,
               refunded: !!action.refunded,
```

`frontend/src/components/AskParrotDialog.jsx` 504행 근처의 dispatch 에서:

```jsx
      dispatch({ type: "results", results: data.results, noEdge: data.no_edge,
                 noEdgeBest: data.no_edge_best,
                 refunded: data.refunded, remaining: data.remaining_today });
```

631행 근처의 빈 화면 줄에서(import 에 `noEdgeDetail` 을 더한다):

```jsx
        {state.noEdge ? <p className="ask-hold ask-in" style={{ "--i": 1 }} role="note">{[noEdgeDetail(state.noEdgeBest), NO_EDGE_NOTE].filter(Boolean).join(" ")}</p> : null}
```

- [ ] **Step 9: 시험과 린트를 돌린다**

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS

Run: `cd frontend && npm run lint`
Expected: 통과

- [ ] **Step 10: 커밋**

```bash
git add backend/app/ask.py backend/tests/test_ask_profiles.py frontend/src/lib/askCopy.js frontend/src/lib/askView.js frontend/src/lib/askFlow.js frontend/src/lib/glossary.js frontend/src/components/AskParrotDialog.jsx frontend/tests/askNoEdge.test.js
git commit -m "feat(ask): 빈 화면에 숫자를 싣고 홀딩 표현을 '첫날 사서 보유' 로 고친다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: AI 해설 걷어내기 — 백엔드

`/api/explain/ai` 의 AI 층을 없애고 규칙 기반 `engine/explain.py` 만 남긴다. 해설 자체는 그대로 나온다 — `/api/backtest` 가 이미 같은 스키마로 `explanation` 을 함께 내보낸다.

**Files:**
- Delete: `backend/app/ai_explain.py` · `backend/tests/test_ai_explain.py`
- Modify: `backend/app/main.py` (import · `ExplainAiRequest` · `explain_ai` 끝점)
- Modify: `backend/app/api_usage.py:56`

**Interfaces:**
- Consumes: `engine.explain.explain_result` (그대로)
- Produces: 없음 — `/api/explain/ai` 끝점과 `ai_explain` 모듈이 사라진다. `/api/backtest` 의 `explanation` 모양은 바뀌지 않는다.

- [ ] **Step 1: 지금 무엇이 남는지 확인한다**

Run: `cd backend && grep -rn "ai_explain\|AI_EXPLAIN\|explain/ai\|ExplainAiRequest" app/ tests/ --include=*.py | grep -v __pycache__`
Expected: `app/ai_explain.py` 자신 · `app/main.py`(import · 끝점 · `ExplainAiRequest`) · `app/api_usage.py:56` · `app/ai_runtime.py:199`(주석) · `app/db.py:1141`(주석) · `app/ask.py:794`(주석) · `tests/test_ai_explain.py`. 다른 곳이 나오면 그것도 이 과제에서 다룬다.

- [ ] **Step 2: 실패하는 시험을 쓴다**

`backend/tests/test_ask_profiles.py` 끝에 붙인다:

```python
def test_the_ai_explanation_layer_is_gone_but_the_explanation_is_not():
    """AI 층만 벗긴다 — 해설은 규칙 기반으로 그대로 나온다.

    왜 빼나: 하루 20회 한도가 걸려 같은 화면이 아침엔 AI 말투, 오후엔 규칙 말투였다.
    한쪽으로 고정하는 편이 낫다. 2026-10-08 대시보드는 677/677 실패였다.
    """
    import importlib

    import pytest

    from app import main as main_mod
    from app.engine.explain import explain_result

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.ai_explain")
    assert not hasattr(main_mod, "ai_explain_mod")
    assert not any(getattr(route, "path", "") == "/api/explain/ai"
                   for route in main_mod.app.routes), "끝점이 남아 있다"

    # 해설은 여전히 같은 스키마로 나온다.
    result = run_backtest(_macro(), _df([100.0, 101.0, 99.0, 103.0, 102.0]))
    out = explain_result(_macro(), result).model_dump()
    for key in ("mood", "headline", "points", "lesson", "source", "disclaimer"):
        assert key in out
    assert out["source"] == "rule"
```

- [ ] **Step 3: 시험이 실패하는 것을 확인한다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py -k ai_explanation -q`
Expected: FAIL — `Failed: DID NOT RAISE <class 'ModuleNotFoundError'>`

- [ ] **Step 4: 끝점과 모듈을 지운다**

`backend/app/main.py`:
- 71행 근처 `from . import ai_explain as ai_explain_mod` 를 지운다.
- 358행 근처 `class ExplainAiRequest(BaseModel):` 블록 전체를 지운다.
- 1555~1591행 근처 `@app.post("/api/explain/ai")` 와 `def explain_ai(...)` 함수 전체를 지운다.

파일을 지운다:

```bash
git rm backend/app/ai_explain.py backend/tests/test_ai_explain.py
```

`backend/app/api_usage.py:56` 의 줄을 지운다:

```python
    ("ai_explain", "백테스트 AI 해설", ("AI_EXPLAIN_MAX_CALLS_PER_DAY", 20)),
```

`backend/app/ask.py:794` 근처의 주석에서 없어진 모듈을 가리키는 문장을 고친다:

```python
    # 해설은 규칙 기반 explain_result 만 쓴다 — 2026-10-12 에 AI 해설 층을 걷어냈다
    # (하루 한도 때문에 같은 화면이 때에 따라 다른 말투였다).
```

`backend/app/ai_runtime.py:199` 와 `backend/app/db.py:1141` 의 주석에서 `ai_explain` 이라는 용례를 지운다(둘 다 목적 코드 예시를 나열한 주석이다. 다른 예시는 그대로 둔다).

- [ ] **Step 5: 시험을 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py tests/test_api_usage.py tests/test_api.py tests/test_ai_runtime.py -q`
Expected: PASS

Run: `cd backend && grep -rn "ai_explain\|AI_EXPLAIN\|explain/ai\|ExplainAiRequest" app/ tests/ --include=*.py | grep -v __pycache__`
Expected: 결과 없음

- [ ] **Step 6: 커밋**

```bash
git add -A backend/app backend/tests
git commit -m "refactor(api): 백테스트 AI 해설 층을 걷어낸다 — 규칙 기반 해설만 남긴다

같은 화면이 하루 20회 한도에 따라 다른 말투였다. 해설 자체는 그대로 나온다
(engine/explain.py, 같은 스키마).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: AI 해설 걷어내기 — 프런트

버튼과 호출을 없애고 해설은 그대로 보여 준다. `/api/backtest` 응답의 `explanation` 이 이미 화면에 들어와 있으므로 보여 줄 내용은 줄지 않는다.

**Files:**
- Modify: `frontend/src/api.js` (`explainAi` 제거)
- Modify: `frontend/src/hooks/useHeroBacktest.js` (`explain` · `aiBusy` · `aiError` 제거)
- Modify: `frontend/src/pages/Studio.jsx` (`enrichExplanation` · `aiBusy` · `aiError` · 독 탭 라벨)
- Modify: `frontend/src/components/ResultView.jsx` (`ParrotExplain`)
- Modify: `frontend/src/components/StudioDock.jsx` (`StudioAiExplain` → `StudioExplain`)
- Modify: `frontend/src/components/HeroGuideScreens.jsx:497-499` (ResultView 에 넘기던 세 소품)
- Modify: `frontend/src/lib/resultLayout.js:23` (블록 라벨)
- Test: `frontend/tests/askProfiles.test.js` 에 한 시험 추가

**Interfaces:**
- Consumes: `explanation` (백테스트 응답, 기존)
- Produces: `ParrotExplain({ explanation })` · `StudioExplain({ explanation })` — `onAiExplain` · `aiBusy` · `aiError` 소품이 사라진다. 블록 id `"ai"` 는 **그대로 둔다**(브라우저에 저장된 배치가 그 id 로 들어 있다).

- [ ] **Step 1: 실패하는 시험을 쓴다**

`frontend/tests/askProfiles.test.js` 끝에 붙인다:

```js
test("AI 해설 호출이 화면에서 사라졌다", () => {
  // AI 층만 벗긴 것이다 — 해설은 백테스트 응답에 실려 그대로 보인다.
  for (const path of ["../src/api.js", "../src/pages/Studio.jsx",
                      "../src/hooks/useHeroBacktest.js",
                      "../src/components/ResultView.jsx",
                      "../src/components/StudioDock.jsx",
                      "../src/components/HeroGuideScreens.jsx"]) {
    const text = source(path);
    assert.ok(!/explainAi|explain\/ai/.test(text), `${path} 에 AI 해설 호출이 남았다`);
    assert.ok(!/aiBusy|aiError|onAiExplain/.test(text), `${path} 에 AI 전용 상태가 남았다`);
  }
  const view = source("../src/components/ResultView.jsx");
  assert.match(view, /explanation\.headline/, "해설은 그대로 보여 줘야 한다");
  assert.ok(!view.includes("AI 해설"), "라벨에서 'AI' 가 빠져야 한다");
});
```

- [ ] **Step 2: 시험이 실패하는 것을 확인한다**

Run: `cd frontend && node --test "tests/askProfiles.test.js"`
Expected: FAIL — `../src/api.js 에 AI 해설 호출이 남았다`

- [ ] **Step 3: 호출을 없앤다**

`frontend/src/api.js` 의 `explainAi:` 항목(290~292행 근처) 전체를 지운다.

`frontend/src/hooks/useHeroBacktest.js`:
- `aiBusy: false` 가 들어간 상태 초기화 세 곳(20 · 65 · 84행 근처)에서 `aiBusy` 와 `aiError` 칸을 지운다.
- `const explain = useCallback(...)` 함수 전체(105~145행 근처)를 지운다.
- 반환 객체(150행 근처)에서 `explain,` 을 지운다.
- `aiRequestIdRef` · `aiPendingRef` 선언이 더 이상 쓰이지 않으면 지운다.

`frontend/src/components/HeroGuideScreens.jsx` 의 `<ResultView ...>` 에서 세 줄(497~499행 근처)을 지운다 — 훅이 더 이상 그 값을 내보내지 않으므로 남기면 `undefined` 가 들어간다:

```jsx
              onAiExplain={backtest.explain}
              aiBusy={backtest.aiBusy}
              aiError={backtest.aiError}
```

`frontend/src/pages/Studio.jsx`:
- `const [aiBusy, setAiBusy] = useState(false);`(196행 근처)와 `aiError` 상태 선언을 지운다.
- `async function enrichExplanation() { ... }`(661~681행 근처) 전체를 지운다.
- `aiRequestIdRef` 선언이 더 이상 쓰이지 않으면 지운다.
- 독 탭(718행 근처)을 바꾼다:

```jsx
    { id: "ai", label: "껄무새 해설", enabled: !!result, dot: explanation ? "ok" : "" },
```

- 928행 근처를 바꾼다:

```jsx
              <StudioExplain explanation={explanation} />
```

그리고 그 파일의 `StudioAiExplain` import 를 `StudioExplain` 으로 바꾼다.
- 700행 근처 주석의 "백테스트 → AI 해설 → …" 을 "백테스트 → 껄무새 해설 → …" 으로 고친다(216행 근처 주석도 같이).

- [ ] **Step 4: 해설 카드에서 버튼을 뺀다**

`frontend/src/components/ResultView.jsx` 의 `AI_EXPLAIN_MASCOT` 상수 이름을 `EXPLAIN_MASCOT` 으로 바꾸고, `ParrotExplain` 전체를 다음으로 **교체**한다:

```jsx
// 껄무새 해설 — 머리말 + 요점 + 이 매크로를 쓴다면. 백테스트 응답에 실려 오는 규칙 기반
// 해설을 그대로 그린다(2026-10-12 에 AI 층을 걷어냈다 — 하루 한도에 따라 같은 화면이
// 다른 말투였다). 해설이 없으면 자리도 비운다.
export function ParrotExplain({ explanation }) {
  if (!explanation?.headline) return null;
  const accent = MOOD_ACCENT[explanation.mood] || MOOD_ACCENT.idle;
  return (
    <div className="notice py-2">
      <div className="flex items-start gap-3">
        <img
          src={EXPLAIN_MASCOT}
          alt=""
          width="256"
          height="256"
          className="h-10 w-10 shrink-0 object-contain"
          aria-hidden="true"
          draggable="false"
        />
        <div className="min-w-0 flex-1">
          <div className="t-caption text-slate-500 mb-1">껄무새 해설</div>
          <div className={"t-title " + accent}>{explanation.headline}</div>
          {explanation.points?.length > 0 && (
            <ul className="mt-3 space-y-2 t-small text-slate-700 list-disc pl-5">
              {explanation.points.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}
          {explanation.lesson && (
            <div className="mt-3 t-small text-slate-700">
              <span className="font-bold text-slate-900">이 매크로를 쓴다면 · </span>
              {explanation.lesson}
            </div>
          )}
          {explanation.disclaimer && (
            <div className="mt-3 t-caption text-slate-500">{explanation.disclaimer}</div>
          )}
        </div>
      </div>
    </div>
  );
}
```

같은 파일에서 `ResultView` 의 소품 목록(292~295행 근처)에서 `onAiExplain` · `aiBusy` · `aiError` 를 지우고, 464행 근처의 `ai:` 블록을 바꾼다:

```jsx
    ai: <ParrotExplain explanation={explanation} />,
```

305행 근처 주석의 "AI 해설" 을 "껄무새 해설" 로 고친다.

- [ ] **Step 5: 독 패널에서 버튼을 뺀다**

`frontend/src/components/StudioDock.jsx` 의 `StudioAiExplain` 을 다음으로 **교체**한다(이름도 바뀐다):

```jsx
// ── 껄무새 해설 — 얼굴 + 머리말 + 요점 + 이 매크로를 쓴다면. 백테스트 응답의 규칙 기반
// 해설을 그대로 그린다(2026-10-12 에 AI 층을 걷어냈다).
export function StudioExplain({ explanation }) {
  return (
    <div className="sd-ai">
      <img src={AI_MASCOT} alt="" width="256" height="256" className="sd-ai-face" aria-hidden="true" draggable="false" />
      <div className="sd-ai-body">
        <div className="sd-ai-who">
          껄무새 해설
          <small>이 결과가 왜 이렇게 나왔는지</small>
        </div>
        {explanation?.headline ? (
          <>
            <p className="sd-ai-head">{explanation.headline}</p>
            {explanation.points?.length > 0 && (
              <ul className="sd-ai-points">
                {explanation.points.map((point, index) => <li key={index}>{point}</li>)}
              </ul>
            )}
            {explanation.lesson && (
              <p className="sd-ai-lesson"><b>이 매크로를 쓴다면 · </b>{explanation.lesson}</p>
            )}
            {explanation.disclaimer && <p className="sd-note">{explanation.disclaimer}</p>}
          </>
        ) : (
          <p className="sd-ai-empty">백테스트를 돌리면 결과를 짚어 드려요.</p>
        )}
      </div>
    </div>
  );
}
```

`AI_MASCOT` 상수 이름은 그대로 둔다(그림 파일 이름이 그렇다).

`frontend/src/lib/resultLayout.js:23` 의 라벨을 바꾼다:

```js
  { id: "ai", label: "껄무새 해설" },
```

- [ ] **Step 6: 시험과 린트를 돌린다**

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: PASS

Run: `cd frontend && npm run lint`
Expected: 통과 (쓰이지 않게 된 import·ref 가 남으면 여기서 잡힌다)

Run: `cd frontend && npm run build`
Expected: 통과 (`StudioAiExplain` 을 가리키는 import 가 남아 있으면 빌드가 깨진다)

참고: `frontend/src/components/ResultView.jsx:426` 근처의 "그냥 홀딩(HODL)했다면" 과 `StudioDock.jsx:84` 의 "홀딩했다면" 은 **직접 만들기(빌더)** 의 결과 화면이다. 사양 §3 이 빌더를 건드리지 않기로 했으므로 그대로 둔다.

- [ ] **Step 7: 커밋**

```bash
git add frontend/src frontend/tests/askProfiles.test.js
git commit -m "refactor(web): 껄무새 해설에서 AI 분석 버튼을 걷어낸다 — 해설은 그대로 보인다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 워크포워드 검증 — 상한과 점수를 측정으로 고른다

지금까지의 측정은 전부 당일 한 장면이다. 그 장면에 숫자를 맞추면 그 장면에만 맞는 숫자가 나온다. 특히 **균형형과 공격형을 무엇으로 가를 것인가**(사양 §1-5)는 하루치로 고르면 안 된다.

**Files:**
- Create: `backend/scripts/ask_walkforward.py`
- Create: `docs/superpowers/notes/2026-10-12-ask-profile-walkforward.md`
- Modify: `backend/app/ask.py` (측정이 더 나은 값을 찾으면 `MDD_CAP_BY_PERIOD` · `MDD_HOLD_FACTOR` · `score` 만)

**Interfaces:**
- Consumes: `ask._Plan` · `ask.build_templates` · `ask.select_top` · `ask.mdd_cap` · `ask.score` · `ask_candidates.build_pool` · `app.marketdata.fetch_klines_for_macro` · `app.engine.backtest.run_backtest`
- Produces: 측정 스크립트와 노트. 코드 변경은 상수와 `score` 의 균형형 분기뿐이다.

- [ ] **Step 1: 측정기를 쓴다**

새 파일 `backend/scripts/ask_walkforward.py`:

```python
"""껄무새 성향 재설계 — 워크포워드 측정기 (2026-10-12).

왜: 당일 한 장면에 숫자를 맞추면 그 장면에만 맞는 숫자가 나온다. 최근 8주를 1주씩
굴려서, 창 t 에서 고른 설정이 창 t+1 에서도 버티는지 본다.

구조: **백테스트는 한 번만 돌린다.** 성향·설정은 select_top 안에서만 갈리므로(순수 함수)
후보 결과를 캐시해 두고 설정 격자를 그 위에 덮어쓴다. 그래서 설정을 늘려도 비용이 안 든다.

돌리는 법 (백엔드 디렉터리에서):
    ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 8
    ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 2   # 빨리 확인만
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask, ask_candidates  # noqa: E402
from app.data import krw  # noqa: E402
from app.data.binance import PERIOD_PRESET_DAYS  # noqa: E402
from app.engine.backtest import BacktestResult, run_backtest  # noqa: E402
from app.marketdata import fetch_klines_for_macro  # noqa: E402

EXCHANGE = "upbit"
WATCH = "sometimes"          # 4시간봉 — 사양 §1-1 의 성적표와 같은 자
HORIZONS = ("days", "weeks", "months")
PROFILES = ("stable", "balanced", "aggressive")
SYMBOLS_PER_WINDOW = 3
WEEK_MS = 7 * 86_400_000


@dataclass(frozen=True)
class Config:
    """설정 후보 — 상한 표 배수와 균형형 점수 함수."""
    name: str
    cap_scale: float
    balanced_score: str

    def apply(self) -> None:
        """ask 모듈의 상한·점수를 이 설정으로 갈아 끼운다(측정 중에만)."""
        ask.MDD_CAP_BY_PERIOD = {
            profile: {period: value * self.cap_scale for period, value in table.items()}
            for profile, table in _BASE_CAPS.items()
        }
        ask.score = _score_with(self.balanced_score)


_BASE_CAPS = {profile: dict(table) for profile, table in ask.MDD_CAP_BY_PERIOD.items()}
_BASE_SCORE = ask.score


def _score_with(kind: str):
    def scored(profile: str, result: BacktestResult) -> float:
        ret = ask.excess_over_hold(result)
        mdd = float(result.mdd_pct)
        if profile == "stable":
            return ret / max(mdd, 1.0)
        if profile == "balanced":
            if kind == "minus_half":
                return ret - 0.5 * mdd
            if kind == "minus_one_half":
                return ret - 1.5 * mdd
            if kind == "sharpe":
                return float(result.sharpe) if result.sharpe is not None else -math.inf
            return ret / math.sqrt(max(mdd, 1.0))   # "over_sqrt"
        return ret
    return scored


CONFIGS = [
    Config("표 그대로 · 수익/√MDD", 1.0, "over_sqrt"),
    Config("표 그대로 · 수익−0.5×MDD", 1.0, "minus_half"),
    Config("표 그대로 · 수익−1.5×MDD", 1.0, "minus_one_half"),
    Config("표 그대로 · 샤프", 1.0, "sharpe"),
    Config("상한 1.25배 · 수익/√MDD", 1.25, "over_sqrt"),
    Config("상한 0.8배 · 수익/√MDD", 0.8, "over_sqrt"),
]


def _symbols() -> list[str]:
    """종목 풀 — 지금 티커로 만든다.

    한계: 거래소 티커는 과거 시점을 주지 않으므로 창마다 같은 종목을 본다. 창이 가르는
    것은 **시세 구간**이고 종목 선택은 아니다. 노트에 이 한계를 적는다.
    """
    tickers = krw.list_tickers(EXCHANGE) if hasattr(krw, "list_tickers") else krw.tickers(EXCHANGE)
    pool = ask_candidates.build_pool(tickers, profile="balanced", exchange=EXCHANGE)
    return [c["symbol"] for c in pool[:SYMBOLS_PER_WINDOW]]


def _run_candidates(symbol: str, horizon: str, end_ms: int) -> list[ask.Evaluated]:
    """이 종목 · 이 호흡 · 이 끝시각에서 모든 템플릿 후보를 한 번씩 돌린다."""
    period = ask.to_period(horizon)
    interval = ask.to_interval(WATCH, period)
    plan = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=[symbol],
                     period_preset=period, interval=interval, exchange=EXCHANGE,
                     account_balance=1_000_000.0)
    start_ms = end_ms - PERIOD_PRESET_DAYS[period] * 86_400_000
    out: list[ask.Evaluated] = []
    frames: dict[str, object] = {}
    for candidate in ask.build_templates(plan):
        key = candidate.macro.candle_interval
        if key not in frames:
            try:
                frames[key], _src = fetch_klines_for_macro(candidate.macro, start_ms, end_ms)
            except Exception as exc:
                print(f"    시세 실패 {symbol} {key}: {type(exc).__name__}")
                frames[key] = None
        df = frames[key]
        if df is None or len(df) < 5:
            continue
        try:
            out.append(ask.Evaluated(candidate, run_backtest(candidate.macro, df)))
        except Exception:
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=8)
    args = parser.parse_args()

    symbols = _symbols()
    print(f"종목: {symbols}")
    now_ms = int(time.time() * 1000)
    # (주, 호흡, 종목) → 후보 결과. 한 번만 돌린다.
    cache: dict[tuple[int, str, str], list[ask.Evaluated]] = {}
    for week in range(args.weeks, 0, -1):
        end_ms = now_ms - (week - 1) * WEEK_MS
        for horizon in HORIZONS:
            for symbol in symbols:
                started = time.monotonic()
                cache[(week, horizon, symbol)] = _run_candidates(symbol, horizon, end_ms)
                print(f"  {args.weeks - week + 1}주째 · {horizon} · {symbol} "
                      f"→ 후보 {len(cache[(week, horizon, symbol)])}개 "
                      f"({time.monotonic() - started:.1f}초)")

    rows = []
    for config in CONFIGS:
        config.apply()
        empty = 0
        combos = 0
        excesses: list[float] = []
        distinct = 0
        mdd_ok = 0
        for week in range(args.weeks, 0, -1):
            for horizon in HORIZONS:
                combos += 1
                period = ask.to_period(horizon)
                interval = ask.to_interval(WATCH, period)
                tops = {}
                for profile in PROFILES:
                    picked = []
                    for symbol in symbols:
                        got = ask.select_top(cache[(week, horizon, symbol)], profile,
                                             period=period, interval=interval)
                        if got:
                            picked.append((symbol, got))
                    tops[profile] = picked
                if not tops["stable"] and not tops["balanced"] and not tops["aggressive"]:
                    empty += 1
                for picked in tops.values():
                    for _symbol, got in picked:
                        excesses.extend(ask.excess_over_hold(e.result) for e in got)
                keys = {p: [(s, e.candidate.label) for s, got in tops[p] for e in got][:3]
                        for p in PROFILES}
                if keys["balanced"] != keys["aggressive"]:
                    distinct += 1
                stable_mdd = max((float(e.result.mdd_pct) for s, got in tops["stable"] for e in got),
                                 default=0.0)
                wild_mdd = max((float(e.result.mdd_pct) for s, got in tops["aggressive"] for e in got),
                               default=float("inf"))
                if stable_mdd <= wild_mdd:
                    mdd_ok += 1
        rows.append((config.name, empty, combos, distinct, mdd_ok,
                     statistics.median(excesses) if excesses else 0.0))

    ask.MDD_CAP_BY_PERIOD = {p: dict(t) for p, t in _BASE_CAPS.items()}
    ask.score = _BASE_SCORE

    print("\n| 설정 | 전무 | 조합 | 균형≠공격 | 안정MDD≤공격 | 품질(중앙값 %p) |")
    print("|---|---|---|---|---|---|")
    for name, empty, combos, distinct, mdd_ok, quality in rows:
        print(f"| {name} | {empty} | {combos} | {distinct}/{combos} | {mdd_ok}/{combos} | {quality:+.2f} |")

    # 고르는 규칙 — 완료 기준 3·4 를 먼저 통과시키고 그중 품질이 가장 좋은 것.
    ok = [r for r in rows if r[3] >= math.ceil(r[2] * 7 / 9) and r[4] == r[2]]
    pick = max(ok or rows, key=lambda r: (r[5], -r[1]))
    print(f"\n고른 설정: {pick[0]}  (기준 3·4 통과: {'예' if ok else '아니오 — 전부 미달'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 짧게 돌려 터지지 않는지 본다**

Run: `cd backend && ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 1`
Expected: 종목 셋을 찍고, 호흡 세 개 × 종목 셋의 후보 수를 찍고, 설정 여섯 줄의 표를 찍는다. `krw` 모듈의 티커 함수 이름이 달라 `AttributeError` 가 나면 `cd backend && grep -n "^def .*ticker" app/data/krw.py` 로 실제 이름을 확인해 `_symbols()` 를 고친다.

- [ ] **Step 3: 8주로 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 8`
Expected: 완주. 1주 창에서 호흡마다 봉 수가 다르므로(1주/3개월/6개월 × 4시간봉 = 42/546/1,092봉) 전체 20~60분이 걸릴 수 있다. 출력을 파일로 받아 둔다:

```bash
cd backend && ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 8 > ../docs/superpowers/notes/walkforward-raw.txt
```

- [ ] **Step 4: 노트를 쓴다**

새 파일 `docs/superpowers/notes/2026-10-12-ask-profile-walkforward.md` 에 다음 뼈대로 적는다. **숫자는 Step 3 출력에서 그대로 옮긴다.**

```markdown
# 성향 재설계 워크포워드 결과 (2026-10-12)

측정: 업비트 원화 현물 · 최근 8주를 1주씩 · 확인 빈도 '가끔'(4시간봉) · 호흡 셋.
돌린 명령: `./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 8`

## 고른 설정

<스크립트가 찍은 '고른 설정' 줄과, 그것을 고른 이유 한 문장>

## 설정별 성적

<스크립트가 찍은 표를 그대로>

## 완료 기준 대조

| 기준 | 목표 | 결과 |
|---|---|---|
| §4-1 전무 | 9조합 중 1개 이하 | |
| §4-2 품질 | 지금 설정보다 낮지 않다 | |
| §4-3 균형≠공격 | 9조합 중 7 이상 | |
| §4-4 안정형 MDD < 공격형 | 항상 | |

## 한계

- 종목 풀은 **지금** 티커로 만든다. 거래소가 과거 시점의 24시간 통계를 주지 않기 때문이다.
  창이 가르는 것은 시세 구간이고 종목 선택은 아니다.
- 왕복 여지(`range_pct − |change_pct|`)도 같은 이유로 24시간 축이다. 구간 길이에 맞춘
  지표로 바꾸는 것은 다음 일이다(사양 §6).
- 바이낸스는 보지 않았다(주 사용처가 업비트다).
```

- [ ] **Step 5: 측정이 고른 값을 코드에 반영한다**

Step 3 이 고른 설정이 `표 그대로 · 수익/√MDD` 가 아니면, `backend/app/ask.py` 에서 **그 설정에 해당하는 것만** 바꾼다:

- 상한 배수가 1.0 이 아니면 `MDD_CAP_BY_PERIOD` 의 값에 그 배수를 곱해 **상수 표 자체를 새 숫자로** 적는다(배수를 코드에 남기지 않는다). 주석에 "워크포워드(2026-10-12)가 고른 값" 과 바뀐 이유를 적는다.
- 균형형 점수가 `over_sqrt` 가 아니면 `score` 의 균형형 분기를 그 식으로 바꾸고, 주석의 "√ 를 쓰는 이유" 문장을 실제 식의 이유로 고친다. 샤프가 뽑혔다면 `result.sharpe` 가 `None` 일 때를 `-math.inf` 로 처리해야 한다(`select_top` 의 정렬이 터진다).

기준 3·4 를 통과한 설정이 **하나도 없으면** 바꾸지 말고 노트에 그렇게 적는다. 그때는 사양 §6 의 대비책대로 안정형에만 방식 제한을 일부 되살리는 것이 다음 일이고, 이 계획의 범위 밖이다.

- [ ] **Step 6: 반영한 값으로 시험을 다시 돌린다**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ask_profiles.py tests/test_ask.py -q`
Expected: PASS. Step 5 에서 상한 표나 점수 식을 바꿨으면 과제 2 의 `test_mdd_cap_*` · `test_balanced_score_*` 시험의 기대값도 새 숫자로 고친다(시험이 **값**을 못 박는 것이 목적이다 — 다음에 누가 바꾸면 여기서 걸린다).

- [ ] **Step 7: 커밋**

```bash
git add backend/scripts/ask_walkforward.py docs/superpowers/notes/ backend/app/ask.py backend/tests/test_ask_profiles.py
git commit -m "test(ask): 워크포워드로 상한과 점수를 고른다 — 8주 1주 창 측정 결과를 노트에 남긴다

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: 마무리 — 전체 시험 · 변형 시험 · 흔적 확인

**Files:**
- Modify: 필요하면 위 과제들의 파일

- [ ] **Step 1: `scalper` 가 어디에도 없는지 확인한다 (완료 기준 5)**

Run: `cd "C:/Users/RHJ/Desktop/gg_parrot" && grep -rn "scalper\|단타형" backend/app backend/tests frontend/src frontend/tests | grep -v __pycache__ | grep -v node_modules`
Expected: 결과 없음. `docs/` 아래의 옛 사양·계획 문서에는 남아 있어도 된다(그때의 기록이다).

- [ ] **Step 2: 백엔드 전체 시험**

Run:
```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests -q -p no:randomly
```
Expected: 이 저장소에 원래 있던 4~5개 실패만 남는다. **새로 생긴 실패가 있으면 전부 고친다.** 실패가 원래 있던 것인지 확실하지 않으면 `git stash` 로 작업을 잠시 치우고 같은 명령을 돌려 비교한다.

- [ ] **Step 3: 프런트 전체 시험 · 린트 · 빌드**

Run:
```bash
cd frontend && node --test "tests/*.test.js"
```
Run: `cd frontend && npm run lint`
Run: `cd frontend && npm run build`
Expected: 셋 다 통과

- [ ] **Step 4: 변형 시험 — 새 시험이 실제로 무는지 (완료 기준 8)**

새 시험이 통과하는 것만으로는 그 시험이 **무엇을 막는지** 알 수 없다. 아래 다섯 가지를 하나씩 심고, 해당 시험이 깨지는 것을 확인한 뒤 **반드시 되돌린다.**

| 심을 변형 | 깨져야 하는 시험 |
|---|---|
| `ask.MDD_CAP_FLOOR` 를 `0.0` 으로 | `test_mdd_cap_follows_the_market_when_holding_barely_moved` |
| `ask.mdd_cap` 의 `hold_mdd_pct is None` 분기에서 `None` 을 돌려주게 | `test_mdd_cap_falls_back_to_the_absolute_table_without_a_hold_reading` |
| `ask.to_interval` 의 1년 내림 조건을 지워서 `15m` 을 그대로 돌려주게 | `test_watch_frequency_alone_decides_the_candle` · `test_every_reachable_pair_fits_backtest_bar_cap` |
| `ask._presets_for` 가 짧은 봉에서 `_SHORT_BAR_PRESETS` 만 돌려주게 | `test_short_bar_presets_layer_over_the_normal_table` · `test_short_bar_templates_build_for_all_eight_types` |
| `askView.noEdgeDetail` 이 `hold == null` 일 때도 홀딩 문장을 쓰게 | `없는 홀딩 숫자를 지어내지 않는다` |

되돌리기는 **반드시** 변형을 심은 직후에 한다. 이전에 이 일을 하다 변형이 남은 파일을 `git checkout` 으로 치우면서 진짜 수정까지 같이 날린 적이 있다 — 변형은 한 번에 하나만 심고, 확인하면 바로 `git checkout -- <그 파일>` 이 아니라 **심은 편집을 손으로 되돌린다**(작업이 아직 커밋 안 됐을 수 있다).

확인 뒤 `git diff` 가 비어 있는지(또는 의도한 변경만 있는지) 본다.

- [ ] **Step 5: 완료 기준 대조**

사양 §4 의 여덟 기준을 하나씩 확인해 결과를 적는다. 1·2·3·4 는 과제 10 의 노트가 답하고, 5·6·7·8 은 위 단계들이 답한다. 통과하지 못한 기준이 있으면 숨기지 말고 노트에 적는다.

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "chore(ask): 성향 재설계 마무리 — 전체 시험 · 변형 시험 · scalper 흔적 확인

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 범위 밖 (이 계획에서 하지 않는다)

- 숏 · 선물 매크로의 재설계. 업비트 롱 전용 제약은 그대로.
- `engine/explain.py` 의 규칙 확장.
- 종목 추천의 AI 층(`ask_candidates.choose` 의 `ask_ai`) — 풀과 정렬만 바꾼다.
- 프로 빌더.
- 왕복 여지 지표를 구간 길이에 맞춘 것으로 바꾸기(과제 10 의 노트가 필요성을 기록한다).
- 홀딩 초과 문턱(`min_excess_pct`) 조정.
