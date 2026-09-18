# 껄무새에게 물어볼까? (Ask Parrot v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Studio(직접 만들기)에 "껄무새에게 물어볼까?" 버튼을 달아, 카드 5장으로 받은 답(성향·시장·종목·기간·빈도)으로 백테스트 상위 3개 조합을 보여 주고 조건 판에 싣는다.

**Architecture:** 백엔드 `app/ask.py` 가 성향별 템플릿 + Gemini 제안으로 후보 매크로(≤24)를 만들고, 기존 `_run_any` 백테스트로 전부 돌린 뒤 성향별 점수로 상위 3개를 고른다(AI 는 숫자를 만들지 않는다). 결과·요청은 `askmacrosession` 에 기록되고 하루 5회로 제한된다. 프론트는 순수 리듀서 `lib/askFlow.js` 가 카드 상태를 들고, `AskParrotDialog` 가 말풍선/칩 UI 를 그리며, 결과는 `macroToForm` 으로 조건 판에 싣는다.

**Tech Stack:** FastAPI + SQLModel(Postgres/Supabase, sqlite dev) · pydantic v2 · Gemini via `ai_runtime` · React 18 + Vite · `node --test` (frontend) · pytest (backend)

**Spec:** `docs/superpowers/specs/2026-09-18-ask-parrot-design.md`

## Global Constraints

- 고지 문구(그대로): `AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요`, 버전 `ask-v1`
- 화면·API·프롬프트·로그 어디에도 "추천" 표현 금지 → "후보", "상위 3개 조합"
- 자유 텍스트 입력은 종목 검색창 하나뿐. 채팅 입력창 없음
- 성향: stable(MDD≤10, 현물만, 유형 C·J·G·A) / balanced(MDD≤20, +F·E) / aggressive(제한 없음, +I·H)
- 후보 ≤ 24, 결과 ≤ 3, 같은 rule_type 최대 1개, `total_trades ≥ 3`
- 한도 `ASK_DAILY_LIMIT` 기본 5 / 계정 / KST 날짜, 시간 예산 `ASK_TIME_BUDGET_SEC` 기본 20
- 로그인 필수. 무료(플랜 게이팅 없음)
- 커밋 메시지는 한국어, 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 테스트 실행: 백엔드 `backend/.venv/Scripts/python -m pytest backend/tests/<file> -q` (repo 루트에서), 프론트 `cd frontend && node --test "tests/*.test.js"`
- Bash 로 Python 패치 스크립트를 쓸 때 `'''` 는 heredoc 을 깨뜨린다 — 파일로 저장해 실행하거나 `"""` 만 쓴다. 긴 파일은 Write 도구로 만든다(Bash 인자 길이 제한).

## File Map

| 파일 | 역할 |
|---|---|
| `backend/app/db.py` (modify) | `User.ask_consent_version/ask_consent_at`, `AskMacroSession` 테이블, sqlite/PG 마이그레이션, 비공개 테이블 목록 |
| `supabase/migrations/20260918120000_ask_macro_sessions.sql` (create) | Postgres 마이그레이션 + RLS/권한 회수 |
| `supabase/tests/gg_parrot_rls.sql` (modify) | RLS 테스트 테이블 목록에 추가 |
| `backend/app/ask.py` (create) | 요청 모델, 성향 설정, 후보 생성, 평가·선별, Gemini 제안, 기록, 한도 |
| `backend/app/main.py` (modify) | `GET /api/ask/status`, `POST /api/ask/consent`, `POST /api/ask/macros` |
| `backend/tests/test_ask.py` (create) | 위 전부 |
| `frontend/src/lib/askCopy.js` (create) | 카드 문구·선택지·고지 문구 한 곳 |
| `frontend/src/lib/askFlow.js` (create) | 카드 상태 리듀서 + `toRequest` |
| `frontend/tests/askFlow.test.js` (create) | 리듀서 테스트 |
| `frontend/src/api.js` (modify) | `askStatus`, `askConsent`, `askMacros` |
| `frontend/src/components/AskParrotDialog.jsx` + `.css` (create) | 말풍선/칩 모달, 결과 카드 |
| `frontend/src/pages/Studio.jsx` (modify) | 버튼·모달 배선, `?ask=1`, 결과를 조건 판에 싣기 |

---

### Task 1: DB — 동의 컬럼과 `askmacrosession` 테이블

**Files:**
- Modify: `backend/app/db.py` (User 모델 ~L256-280, `DailyQuestClaim` 아래 ~L595, sqlite `_migrate` 의 `"user"` 사전 ~L933, `_PG_ADDED_COLUMNS["user"]` ~L1073, `_PG_PRIVATE_CACHE_TABLES` ~L1140)
- Create: `supabase/migrations/20260918120000_ask_macro_sessions.sql`
- Modify: `supabase/tests/gg_parrot_rls.sql` (테이블 목록, 알파벳순)
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Produces: `db.AskMacroSession(id, user_id, day_kst, request_json, candidate_count, results_json, disclaimer_version, ai_used, elapsed_ms, created_at, created_ms)`; `User.ask_consent_version: str`, `User.ask_consent_at: str`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_ask.py` 를 새로 만든다:

```python
"""껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합을 찾는다."""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import select

from app import ask
from app.db import AskMacroSession, User, get_session
from app.engine import BacktestResult
from app.main import app

client = TestClient(app)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"ask{tok}@ex.com", "username": f"ask_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_user_has_consent_columns_and_session_table_exists():
    _, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        assert user.ask_consent_version == ""
        assert user.ask_consent_at == ""
        db.add(AskMacroSession(
            user_id=user_id, day_kst="2026-09-18", request_json="{}", candidate_count=0,
            results_json="[]", disclaimer_version="ask-v1", ai_used=False, elapsed_ms=1,
            created_at="2026-09-18T00:00:00Z", created_ms=1,
        ))
        db.commit()
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert len(rows) == 1 and rows[0].day_kst == "2026-09-18"
```

(`from app import ask` 는 Task 2 에서 생기므로 Task 1 에서는 그 줄과 `ValidationError`, `BacktestResult` import 를 잠시 주석 처리해 두고 Task 2 에서 푼다.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: FAIL — `ImportError: cannot import name 'AskMacroSession'`

- [ ] **Step 3: Add the model and columns**

`backend/app/db.py` — `User` 클래스의 `is_admin: bool = False` 바로 아래에:

```python
    # 껄무새에게 물어볼까? — 고지 동의(버전·시각). 동의 버전이 현재 고지 버전과 다르면 다시 받는다.
    ask_consent_version: str = ""
    ask_consent_at: str = ""
```

`DailyQuestClaim` 클래스 정의 바로 아래에:

```python
class AskMacroSession(SQLModel, table=True):
    """'껄무새에게 물어볼까?' 한 번의 요청·결과 기록 — 하루 한도 계산과 문의 대응(무엇을 보여 줬는지)용."""

    __table_args__ = (
        Index("ix_askmacrosession_user_day", "user_id", "day_kst"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    day_kst: str  # YYYY-MM-DD (KST) — 하루 한도 키
    request_json: str = "{}"  # 카드 답변(ask.AskRequest)
    candidate_count: int = 0  # 백테스트한 후보 수
    results_json: str = "[]"  # 보여 준 상위 3개(매크로 + 지표)
    disclaimer_version: str = ""  # 그때 화면에 붙은 고지 버전
    ai_used: bool = False  # Gemini 제안이 후보에 들어갔는지
    elapsed_ms: int = 0
    created_at: str
    created_ms: int = Field(default=0, sa_type=BigInteger)
```

sqlite `_migrate` 의 `"user": {` 사전(`"is_admin": 'ALTER TABLE "user" ADD COLUMN is_admin ...'` 줄) 아래에 두 줄:

```python
            "ask_consent_version": 'ALTER TABLE "user" ADD COLUMN ask_consent_version TEXT NOT NULL DEFAULT \'\'',
            "ask_consent_at": 'ALTER TABLE "user" ADD COLUMN ask_consent_at TEXT NOT NULL DEFAULT \'\'',
```

`_PG_ADDED_COLUMNS["user"]` 사전의 `"is_admin": "BOOLEAN NOT NULL DEFAULT FALSE"` 뒤에:

```python
             "ask_consent_version": "VARCHAR NOT NULL DEFAULT ''", "ask_consent_at": "VARCHAR NOT NULL DEFAULT ''",
```

`_PG_PRIVATE_CACHE_TABLES` 튜플의 `"dailyquestclaim", "runsessionevent",` 줄을 `"dailyquestclaim", "runsessionevent", "askmacrosession",` 으로.

- [ ] **Step 4: Supabase migration + RLS test list**

`supabase/migrations/20260918120000_ask_macro_sessions.sql`:

```sql
-- 껄무새에게 물어볼까? (2026-09-18)
-- ① user.ask_consent_version / ask_consent_at — 고지 동의 버전·시각.
-- ② askmacrosession — 요청(카드 답변)·보여 준 상위 3개 기록. 하루 한도는 (user_id, day_kst) 로 센다.
-- 서버 전용: RLS 켜고 anon/authenticated 권한 회수.

alter table public."user" add column if not exists ask_consent_version varchar not null default '';
alter table public."user" add column if not exists ask_consent_at varchar not null default '';

create table if not exists public.askmacrosession (
  id bigserial primary key,
  user_id integer not null,
  day_kst varchar not null,
  request_json varchar not null default '{}',
  candidate_count integer not null default 0,
  results_json varchar not null default '[]',
  disclaimer_version varchar not null default '',
  ai_used boolean not null default false,
  elapsed_ms integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0
);
create index if not exists ix_askmacrosession_user_id on public.askmacrosession (user_id);
create index if not exists ix_askmacrosession_user_day on public.askmacrosession (user_id, day_kst);

alter table public.askmacrosession enable row level security;
revoke all privileges on table public.askmacrosession from public, anon, authenticated;
revoke all privileges on sequence public.askmacrosession_id_seq from public, anon, authenticated;
```

`supabase/tests/gg_parrot_rls.sql` 의 테이블 배열에 `'askmacrosession',` 를 알파벳순 자리(`'apiusagedaily',` 근처, 없으면 배열 맨 앞)에 추가.

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_ask.py supabase/migrations/20260918120000_ask_macro_sessions.sql supabase/tests/gg_parrot_rls.sql
git commit -m "껄무새에게 물어볼까? DB: 고지 동의 컬럼과 askmacrosession 기록 테이블

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `ask.py` — 요청 모델·성향 설정·후보 생성

**Files:**
- Create: `backend/app/ask.py`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Produces:
  - `AskRequest(risk_profile, market, leverage, symbols, period_preset, interval)` pydantic 모델(검증 포함)
  - `PROFILES: dict[str, dict]` — `label`, `mdd_cap`, `rule_types`, `futures`
  - `RULE_LABELS: dict[str, str]`
  - `DISCLAIMER`, `DISCLAIMER_VERSION = "ask-v1"`, `MAX_CANDIDATES = 24`, `TOP_N = 3`, `MIN_TRADES = 3`
  - `Candidate(label: str, macro: Macro, source: str)` dataclass (`source` = `"template" | "ai"`)
  - `_PRESETS`, `_make_macro(req, rule_type, preset, symbols) -> Optional[Macro]`, `_label(rule_type, req, symbols)`, `_allowed_types(req)`
  - `build_templates(req: AskRequest) -> list[Candidate]`

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_ask.py`; 상단 import 주석을 푼다)

```python
def test_request_normalizes_symbols_and_rejects_bad_combos():
    req = ask.AskRequest(risk_profile="balanced", symbols=[" btcusdt ", "BTCUSDT", "ethusdt"])
    assert req.symbols == ["BTCUSDT", "ETHUSDT"]
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="stable", market="futures", leverage=2, symbols=["BTCUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", market="spot", leverage=2, symbols=["BTCUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTC-KRW"])


def test_templates_follow_profile_rules():
    stable = ask.build_templates(ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"], interval="4h"))
    types = {c.macro.rule_type.value for c in stable}
    assert types == {"C", "J", "G", "A"}
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in stable)
    assert all(c.macro.candle_interval == "4h" and c.macro.period.preset == "3m" for c in stable)
    assert all(c.source == "template" for c in stable)

    aggressive = ask.build_templates(ask.AskRequest(
        risk_profile="aggressive", market="futures", leverage=2, symbols=["BTCUSDT"]))
    types = {c.macro.rule_type.value for c in aggressive}
    assert "I" in types and "H" in types and "C" not in types  # C 는 레버리지를 못 쓴다
    assert all(c.macro.leverage == 2 and c.macro.market == "futures" for c in aggressive)


def test_templates_cover_every_symbol_first_and_add_a_portfolio():
    req = ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands = ask.build_templates(req)
    assert len(cands) <= ask.MAX_CANDIDATES
    first_preset = cands[: 3 * 6]  # 3 종목 × 6 유형의 첫 프리셋이 먼저
    assert {c.macro.symbol for c in first_preset} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    portfolios = [c for c in cands if c.macro.symbols]
    assert portfolios and portfolios[0].macro.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all("추천" not in c.label for c in cands)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: FAIL — `ImportError: cannot import name 'ask'`

- [ ] **Step 3: Create `backend/app/ask.py`**

```python
"""껄무새에게 물어볼까? — 카드 답변(성향·시장·종목·기간·빈도)으로 백테스트 상위 3개 조합을 찾는다.

법적 설계를 코드로 강제한다:
* 종목은 요청에 온 것만 쓴다(AI 가 종목을 고르는 경로 없음).
* AI 는 매크로 '뼈대'(rule_type·params)만 제안하고 성과 숫자는 전부 백테스트가 계산한다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보, 상위 조합.
* 성향이 안정형이면 선물·H(세이프티 주문) 후보를 만들지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlmodel import Session, select

from . import ai_explain as ai_explain_mod
from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime
from .db import AskMacroSession, User
from .engine.backtest import BacktestResult
from .engine.explain import explain_result
from .engine.schema import Macro
from .quests import today_kst

DISCLAIMER_VERSION = "ask-v1"
DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요"
MAX_CANDIDATES = 24
TOP_N = 3
MIN_TRADES = 3
CAPITAL = 1_000_000

RiskProfile = Literal["stable", "balanced", "aggressive"]

# 성향별 규칙 — 후보에 넣는 유형, MDD 상한, 선물 허용 여부. 순서는 템플릿 우선순위.
PROFILES: dict[str, dict] = {
    "stable": {"label": "안정형", "mdd_cap": 10.0, "rule_types": ("C", "J", "G", "A"), "futures": False},
    "balanced": {"label": "균형형", "mdd_cap": 20.0, "rule_types": ("C", "J", "G", "A", "F", "E"), "futures": True},
    "aggressive": {"label": "공격형", "mdd_cap": None, "rule_types": ("C", "J", "G", "A", "F", "E", "I", "H"), "futures": True},
}

RULE_LABELS = {
    "A": "익절/손절 후 재진입", "C": "정기 분할매수", "E": "트레일링 스탑", "F": "RSI 조건",
    "G": "볼린저밴드 회귀", "H": "세이프티 주문", "I": "변동성 돌파", "J": "이동평균 크로스",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class AskRequest(BaseModel):
    risk_profile: RiskProfile
    market: Literal["spot", "futures"] = "spot"
    leverage: int = Field(default=1, ge=1, le=3)
    symbols: list[str] = Field(min_length=1, max_length=3)
    period_preset: Literal["3m", "6m", "1y"] = "3m"
    interval: Literal["1h", "4h", "1d"] = "1h"

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            sym = str(raw).strip().upper()
            if not _SYMBOL_RE.match(sym):
                raise ValueError(f"종목은 USDT 페어여야 해요: {raw}")
            if sym not in seen:
                seen.append(sym)
        if len(seen) > 3:
            raise ValueError("종목은 최대 3개까지예요")
        return seen

    @model_validator(mode="after")
    def _profile_rules(self) -> "AskRequest":
        if self.market == "futures" and not PROFILES[self.risk_profile]["futures"]:
            raise ValueError("안정형은 현물만 살펴봐요")
        if self.market == "spot" and self.leverage != 1:
            raise ValueError("현물은 레버리지를 쓸 수 없어요")
        return self


@dataclass
class Candidate:
    label: str
    macro: Macro
    source: str  # "template" | "ai"


# 유형별 파라미터 프리셋. 절대 가격이 필요한 B·D 는 없다. C 는 initial_capital 을 스스로 계산한다.
_PRESETS: dict[str, list[dict]] = {
    "C": [
        {"params": {"amount_per_buy": 50_000, "interval_days": 1}},
        {"params": {"amount_per_buy": 100_000, "interval_days": 7}},
    ],
    "A": [
        {"params": {"take_profit_pct": 3, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 2}},
        {"params": {"take_profit_pct": 5, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 3}},
    ],
    "J": [
        {"params": {"ma_type": "SMA", "fast_period": 20, "slow_period": 60, "initial_capital": CAPITAL}},
        {"params": {"ma_type": "EMA", "fast_period": 10, "slow_period": 30, "initial_capital": CAPITAL}},
    ],
    "G": [
        {"params": {"bb_period": 20, "bb_std": 2.0, "strategy": "reversion", "exit_target": "mid", "initial_capital": CAPITAL}},
        {"params": {"bb_period": 20, "bb_std": 2.5, "strategy": "reversion", "exit_target": "opposite", "initial_capital": CAPITAL}},
    ],
    "F": [
        {"params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": CAPITAL}},
        {"params": {"rsi_period": 14, "entry_threshold": 25, "exit_threshold": 65, "exit_mode": "both", "take_profit": 5, "initial_capital": CAPITAL}},
    ],
    "E": [
        {"params": {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3, "initial_capital": CAPITAL}},
        {"params": {"entry_mode": "dip", "entry_dip": 3, "activation_profit": 4, "trail_percent": 2, "initial_capital": CAPITAL}},
    ],
    "I": [
        {"params": {"k": 0.5, "exit_mode": "next_open", "initial_capital": CAPITAL}},
        {"params": {"k": 0.6, "exit_mode": "trailing", "trail_percent": 2, "ma_filter_period": 20, "initial_capital": CAPITAL}},
    ],
    "H": [
        {"params": {"base_order_size": 100_000, "safety_order_size": 100_000, "price_deviation": 2,
                    "max_safety_orders": 5, "take_profit": 2, "initial_capital": CAPITAL}},
    ],
}


def _allowed_types(req: AskRequest) -> tuple[str, ...]:
    types = PROFILES[req.risk_profile]["rule_types"]
    # C(DCA) 는 레버리지·선물을 못 쓴다.
    if req.market == "futures":
        types = tuple(t for t in types if t != "C")
    return types


def _make_macro(req: AskRequest, rule_type: str, preset: dict, symbols: list[str]) -> Optional[Macro]:
    body = {
        "symbol": symbols[0],
        "symbols": symbols if len(symbols) > 1 else None,
        "rule_type": rule_type,
        "position_side": "long",
        "candle_interval": req.interval,
        "market": req.market,
        "leverage": req.leverage if rule_type != "C" else 1,
        "params": dict(preset["params"]),
        "risk": dict(preset.get("risk", {})),
        "period": {"preset": req.period_preset},
    }
    try:
        return Macro(**body)
    except Exception:
        return None


def _label(rule_type: str, req: AskRequest, symbols: list[str]) -> str:
    where = "포트폴리오" if len(symbols) > 1 else symbols[0]
    return f"{RULE_LABELS.get(rule_type, rule_type)} · {req.interval} · {where}"


def build_templates(req: AskRequest) -> list[Candidate]:
    """성향별 템플릿 후보. '각 유형의 첫 프리셋 × 모든 종목' 을 먼저 채우고, 두 번째 프리셋은 뒤에 붙인다."""
    out: list[Candidate] = []
    types = _allowed_types(req)
    depth = max(len(_PRESETS[t]) for t in types)
    for preset_idx in range(depth):
        for sym in req.symbols:
            for rule_type in types:
                presets = _PRESETS[rule_type]
                if preset_idx >= len(presets):
                    continue
                macro = _make_macro(req, rule_type, presets[preset_idx], [sym])
                if macro is not None:
                    out.append(Candidate(_label(rule_type, req, [sym]), macro, "template"))
        if preset_idx == 0 and len(req.symbols) > 1:
            # 종목 2개 이상이면 자본을 나눠 함께 돌리는 포트폴리오 후보도 한 벌.
            for rule_type in types:
                macro = _make_macro(req, rule_type, _PRESETS[rule_type][0], req.symbols)
                if macro is not None:
                    out.append(Candidate(_label(rule_type, req, req.symbols), macro, "template"))
    return out[:MAX_CANDIDATES]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ask.py backend/tests/test_ask.py
git commit -m "껄무새에게 물어볼까?: 요청 모델·성향 규칙·템플릿 후보 생성

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `ask.py` — 평가·선별(점수·MDD 상한·다양성)

**Files:**
- Modify: `backend/app/ask.py`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Produces:
  - `Evaluated(candidate: Candidate, result: BacktestResult)` dataclass
  - `evaluate(candidates, run: Callable[[Macro], BacktestResult], time_budget_sec: float) -> list[Evaluated]`
  - `score(profile: str, result: BacktestResult) -> float`
  - `select_top(evaluated: list[Evaluated], profile: str, n: int = TOP_N) -> list[Evaluated]`

- [ ] **Step 1: Write the failing tests** (append)

```python
def _result(ret, mdd, trades=10, win=50.0):
    return BacktestResult(
        initial_capital=1.0, final_equity=1.0 + ret / 100, final_return_pct=ret, mdd_pct=mdd,
        win_rate_pct=win, total_trades=trades, trades=[], equity_curve=[],
    )


def _cand(rule_type, symbol="BTCUSDT"):
    req = ask.AskRequest(risk_profile="aggressive", symbols=[symbol])
    preset = ask._PRESETS[rule_type][0]
    return ask.Candidate(f"{rule_type} · 1h · {symbol}", ask._make_macro(req, rule_type, preset, [symbol]), "template")


def test_evaluate_skips_failures_and_respects_time_budget(monkeypatch):
    def run(macro):
        if macro.rule_type.value == "F":
            raise RuntimeError("no data")
        return _result(5, 3)

    out = ask.evaluate([_cand("A"), _cand("F"), _cand("J")], run, time_budget_sec=60)
    assert [e.candidate.macro.rule_type.value for e in out] == ["A", "J"]

    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(ask.time, "monotonic", lambda: next(clock))
    out = ask.evaluate([_cand("A"), _cand("J"), _cand("E")], run, time_budget_sec=10)
    assert len(out) == 1  # 예산이 끝나면 남은 후보는 건너뛴다


def test_select_top_filters_scores_and_keeps_rule_types_distinct():
    ev = [
        ask.Evaluated(_cand("A"), _result(30, 25)),   # 균형형 MDD 상한(20) 초과 → 탈락
        ask.Evaluated(_cand("J"), _result(12, 4)),
        ask.Evaluated(_cand("J", "ETHUSDT"), _result(11, 3)),  # 같은 유형 두 번째 → 다양성으로 탈락
        ask.Evaluated(_cand("G"), _result(8, 2)),
        ask.Evaluated(_cand("E"), _result(9, 2, trades=2)),    # 거래 2회 → 탈락
        ask.Evaluated(_cand("F"), _result(3, 1)),
    ]
    top = ask.select_top(ev, "balanced")
    assert [e.candidate.macro.rule_type.value for e in top] == ["J", "G", "F"]

    # 안정형은 수익률 ÷ MDD, 공격형은 수익률만 본다.
    assert ask.score("stable", _result(10, 5)) == pytest.approx(2.0)
    assert ask.score("stable", _result(10, 0.2)) == pytest.approx(10.0)  # MDD 는 1 아래로 나누지 않는다
    assert ask.score("balanced", _result(10, 4)) == pytest.approx(8.0)
    assert ask.score("aggressive", _result(10, 40)) == pytest.approx(10.0)
    assert ask.select_top([], "stable") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: FAIL — `AttributeError: module 'app.ask' has no attribute 'evaluate'`

- [ ] **Step 3: Implement** (append to `backend/app/ask.py`)

```python
@dataclass
class Evaluated:
    candidate: Candidate
    result: BacktestResult


def evaluate(
    candidates: list[Candidate],
    run: Callable[[Macro], BacktestResult],
    time_budget_sec: float,
) -> list[Evaluated]:
    """후보를 전부 백테스트한다. 실패한 후보는 건너뛰고, 시간 예산이 끝나면 남은 후보도 건너뛴다."""
    started = time.monotonic()
    out: list[Evaluated] = []
    for cand in candidates:
        if time.monotonic() - started > time_budget_sec:
            break
        try:
            out.append(Evaluated(cand, run(cand.macro)))
        except Exception:
            continue
    return out


def score(profile: str, result: BacktestResult) -> float:
    ret = float(result.final_return_pct)
    mdd = float(result.mdd_pct)
    if profile == "stable":
        return ret / max(mdd, 1.0)
    if profile == "balanced":
        return ret - 0.5 * mdd
    return ret


def select_top(evaluated: list[Evaluated], profile: str, n: int = TOP_N) -> list[Evaluated]:
    """MDD 상한·최소 거래 수로 거르고 성향 점수로 정렬해 상위 n개 — 같은 rule_type 은 하나만."""
    cap = PROFILES[profile]["mdd_cap"]
    pool = [
        e for e in evaluated
        if e.result.total_trades >= MIN_TRADES and (cap is None or float(e.result.mdd_pct) <= cap)
    ]
    pool.sort(key=lambda e: (score(profile, e.result), e.result.total_trades), reverse=True)
    picked: list[Evaluated] = []
    seen_types: set[str] = set()
    for e in pool:
        rt = e.candidate.macro.rule_type.value
        if rt in seen_types:
            continue
        seen_types.add(rt)
        picked.append(e)
        if len(picked) >= n:
            break
    return picked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ask.py backend/tests/test_ask.py
git commit -m "껄무새에게 물어볼까?: 후보 평가와 성향 점수·MDD 상한·유형 다양성 선별

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `ask.py` — Gemini 뼈대 제안(검증·폐기)

**Files:**
- Modify: `backend/app/ask.py`
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Consumes: `ai_runtime.ai_available/get_ai_client/get_ai_runtime/ai_cache_key/default_model` (`ai_challenge.py` 가 쓰는 것과 같은 API)
- Produces: `propose_with_ai(req: AskRequest) -> list[Candidate]` — 키 없음·오류·형식 불량이면 빈 리스트. 허용 유형 밖·스키마 불량은 폐기. `source == "ai"`.

- [ ] **Step 1: Write the failing tests** (append)

```python
class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeMessages:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"content": [_FakeBlock(self.text)]})()


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _fake_runtime():
    class RT:
        def call(self, key, load):
            return load(), "miss"
    return RT()


def test_ai_proposals_are_validated_and_filtered(monkeypatch):
    req = ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"], interval="1d")
    monkeypatch.setattr(ask, "ai_available", lambda: False)
    assert ask.propose_with_ai(req) == []

    text = json.dumps({"macros": [
        {"rule_type": "J", "params": {"ma_type": "EMA", "fast_period": 12, "slow_period": 26, "initial_capital": 1000000}},
        {"rule_type": "H", "params": {"base_order_size": 1, "safety_order_size": 1, "price_deviation": 1, "take_profit": 1, "initial_capital": 1000000}},  # 안정형 밖 → 폐기
        {"rule_type": "J", "symbol": "SOLUSDT", "params": {"ma_type": "SMA", "fast_period": 5, "slow_period": 20, "initial_capital": 1000000}},  # 종목 바꿔치기 → 요청 종목으로 고정
        {"rule_type": "F", "params": {"rsi_period": "x"}},  # 스키마 불량 → 폐기
    ]})
    client_ok = _FakeClient(text)
    monkeypatch.setattr(ask, "ai_available", lambda: True)
    monkeypatch.setattr(ask, "get_ai_client", lambda: client_ok)
    monkeypatch.setattr(ask, "get_ai_runtime", _fake_runtime)
    out = ask.propose_with_ai(req)
    assert [c.macro.rule_type.value for c in out] == ["J", "J"]
    assert all(c.macro.symbol == "BTCUSDT" and c.macro.candle_interval == "1d" and c.source == "ai" for c in out)
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in out)
    system = client_ok.messages.calls[0]["system"]
    assert "추천" not in system and "C, J, G, A" in system

    client_bad = _FakeClient("not json")
    monkeypatch.setattr(ask, "get_ai_client", lambda: client_bad)
    assert ask.propose_with_ai(req) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'propose_with_ai'`

- [ ] **Step 3: Implement** (append to `backend/app/ask.py`)

```python
_AI_MODEL = default_model()
_AI_MAX_TOKENS = int(os.environ.get("GEMINI_ASK_MAX_TOKENS", "2048"))
_AI_PROMPT_VERSION = "ask-v1"


def _ai_system(req: AskRequest) -> str:
    types = ", ".join(_allowed_types(req))
    return (
        "너는 코인 백테스트 교육 도구의 매크로 뼈대 생성기야. 사용자가 고른 종목과 조건으로 "
        "서로 다른 스타일의 매크로 3개를 JSON 으로만 출력해(코드펜스 없이). 형식은 "
        '{"macros":[{"rule_type":"J","params":{...},"risk":{"stop_loss_pct":3}}, ...]}. '
        f"rule_type 은 {types} 중에서만 고르고 각 params 는 그 타입 스키마대로 채워. "
        "initial_capital 은 1000000 으로. 종목·봉 간격·시장·레버리지는 서버가 정하니 넣지 마. "
        "수익률이나 전망 같은 숫자를 지어내지 말고, 조언·권유 문구를 넣지 마."
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                t = rest
    return t.strip()


def propose_with_ai(req: AskRequest) -> list[Candidate]:
    """Gemini 가 제안한 뼈대를 요청 조건(종목·봉·시장·레버리지)에 고정하고 스키마로 검증한다. 실패는 빈 리스트."""
    if not ai_available():
        return []
    system = _ai_system(req)
    prompt = (
        f"종목: {', '.join(req.symbols)} · 성향: {PROFILES[req.risk_profile]['label']} · "
        f"봉 간격: {req.interval} · 기간: {req.period_preset}. 매크로 3개를 JSON 으로."
    )
    key = ai_cache_key("ask", _AI_PROMPT_VERSION, _AI_MODEL,
                       {"req": req.model_dump(), "system": system, "prompt": prompt, "max_tokens": _AI_MAX_TOKENS})

    def load():
        response = get_ai_client().messages.create(
            model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": prompt}], purpose="ask",
        )
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise ValueError("empty ask response")
        obj = json.loads(_strip_fences(text))
        macros = obj.get("macros", obj if isinstance(obj, list) else [])
        if not isinstance(macros, list):
            raise ValueError("invalid ask response")
        return macros

    try:
        proposed = get_ai_runtime().call(key, load)[0]
    except Exception:
        return []

    allowed = set(_allowed_types(req))
    out: list[Candidate] = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        rule_type = str(item.get("rule_type", "")).upper()
        if rule_type not in allowed:
            continue
        preset = {"params": item.get("params") or {}, "risk": item.get("risk") or {}}
        macro = _make_macro(req, rule_type, preset, [req.symbols[0]])
        if macro is None:
            continue
        out.append(Candidate(_label(rule_type, req, [req.symbols[0]]) + " · AI 제안", macro, "ai"))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ask.py backend/tests/test_ask.py
git commit -m "껄무새에게 물어볼까?: Gemini 뼈대 제안 — 요청 조건에 고정하고 스키마 밖은 폐기

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `ask.py` — 오케스트레이션·한도·기록 + API 3개

**Files:**
- Modify: `backend/app/ask.py`
- Modify: `backend/app/main.py` (`from . import quests as quests_mod` 옆에 import; `@app.get("/api/me/quests")` 바로 위에 라우트 3개)
- Test: `backend/tests/test_ask.py`

**Interfaces:**
- Consumes: `main._run_any(macro) -> (result, per_symbol, source, period_label)`, `auth_mod.current_user_in_session`, `request_session`, `ai_explain_mod.enrich(macro, result, base=None) -> Explanation`, `explain_result(macro, result) -> Explanation`
- Produces:
  - `class AskError(Exception)`: `.status: int`, `.message: str`
  - `daily_limit() -> int`, `time_budget_sec() -> float`, `used_today(db, user)`, `remaining_today(db, user) -> int`, `consented(user) -> bool`, `status(db, user) -> dict`
  - `give_consent(db, user) -> dict`
  - `run_ask(db, user, req, run_backtest: Callable[[Macro], BacktestResult]) -> dict`
  - HTTP: `GET /api/ask/status`, `POST /api/ask/consent`, `POST /api/ask/macros`

- [ ] **Step 1: Write the failing tests** (append)

```python
_RETURNS = {"A": (4, 3, 8), "C": (2, 1, 12), "J": (9, 4, 6), "G": (6, 2, 5), "F": (7, 3, 2), "E": (5, 2, 4)}


@pytest.fixture
def _fake_backtest(monkeypatch):
    """유형별로 정해진 수치를 돌려주는 가짜 백테스트 — 네트워크 없이 선별 결과를 예측할 수 있다."""
    def fake_run_any(macro):
        ret, mdd, trades = _RETURNS.get(macro.rule_type.value, (1, 1, 5))
        return _result(ret, mdd, trades=trades), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    monkeypatch.setattr(ask, "ai_available", lambda: False)
    monkeypatch.setattr(ask.ai_explain_mod, "ai_available", lambda: False)


_BODY = {"risk_profile": "balanced", "market": "spot", "leverage": 1,
         "symbols": ["BTCUSDT"], "period_preset": "3m", "interval": "1h"}


def test_status_and_consent_flow(_fake_backtest):
    token, _ = _signup()
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st == {"consented": False, "remaining_today": 5, "daily_limit": 5, "disclaimer_version": "ask-v1"}

    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 403

    ok = client.post("/api/ask/consent", headers=_auth(token)).json()
    assert ok == {"ok": True, "version": "ask-v1"}
    assert client.get("/api/ask/status", headers=_auth(token)).json()["consented"] is True
    assert client.get("/api/ask/status").status_code == 401


def test_macros_returns_top3_distinct_types_and_records(_fake_backtest):
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # 균형형 점수 = 수익률 - 0.5·MDD: J 7 > G 5 > E 4 > A 2.5 > C 1.5 ; F 는 거래 2회라 탈락
    assert [r["rule_type"] for r in data["results"]] == ["J", "G", "E"]
    first = data["results"][0]
    assert first["metrics"] == {"final_return_pct": 9, "mdd_pct": 4, "win_rate_pct": 50.0, "total_trades": 6}
    assert first["macro"]["symbol"] == "BTCUSDT" and first["macro"]["rule_type"] == "J"
    assert first["explanation"]["headline"] and first["ai_generated"] is False
    assert "추천" not in json.dumps(data, ensure_ascii=False)
    assert data["disclaimer"] == ask.DISCLAIMER and data["disclaimer_version"] == "ask-v1"
    assert data["remaining_today"] == 4 and data["ai_used"] is False and data["candidate_count"] > 3

    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert len(rows) == 1
        assert json.loads(rows[0].request_json)["symbols"] == ["BTCUSDT"]
        assert [r["rule_type"] for r in json.loads(rows[0].results_json)] == ["J", "G", "E"]
        assert rows[0].day_kst == ask.today_kst()


def test_macros_validation_and_daily_limit(_fake_backtest, monkeypatch):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    bad = dict(_BODY, risk_profile="stable", market="futures", leverage=2)
    assert client.post("/api/ask/macros", json=bad, headers=_auth(token)).status_code == 422
    assert client.post("/api/ask/macros", json=dict(_BODY, symbols=["A", "B", "C", "D"]), headers=_auth(token)).status_code == 422

    monkeypatch.setenv("ASK_DAILY_LIMIT", "2")
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()["remaining_today"] == 1
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()["remaining_today"] == 0
    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 429 and "내일" in res.json()["detail"]


def test_macros_with_no_candidates_returns_empty_results(_fake_backtest, monkeypatch):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))

    def boom(macro):
        raise RuntimeError("no data")

    monkeypatch.setattr("app.main._run_any", boom)
    data = client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()
    assert data["results"] == [] and data["remaining_today"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: FAIL — `/api/ask/status` 가 404 라 status dict 비교가 실패

- [ ] **Step 3: Implement orchestration** (append to `backend/app/ask.py`)

```python
class AskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("ASK_DAILY_LIMIT", "5")))
    except ValueError:
        return 5


def time_budget_sec() -> float:
    try:
        return float(os.environ.get("ASK_TIME_BUDGET_SEC", "20"))
    except ValueError:
        return 20.0


def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def used_today(db: Session, user: User) -> int:
    return int(db.exec(
        select(func.count()).select_from(AskMacroSession)
        .where(AskMacroSession.user_id == user.id, AskMacroSession.day_kst == today_kst())
    ).one())


def remaining_today(db: Session, user: User) -> int:
    return max(0, daily_limit() - used_today(db, user))


def consented(user: User) -> bool:
    return getattr(user, "ask_consent_version", "") == DISCLAIMER_VERSION


def status(db: Session, user: User) -> dict:
    return {
        "consented": consented(user),
        "remaining_today": remaining_today(db, user),
        "daily_limit": daily_limit(),
        "disclaimer_version": DISCLAIMER_VERSION,
    }


def give_consent(db: Session, user: User) -> dict:
    user.ask_consent_version = DISCLAIMER_VERSION
    user.ask_consent_at = _now()[0]
    db.add(user)
    db.commit()
    return {"ok": True, "version": DISCLAIMER_VERSION}


def _metrics(result: BacktestResult) -> dict:
    return {
        "final_return_pct": result.final_return_pct,
        "mdd_pct": result.mdd_pct,
        "win_rate_pct": result.win_rate_pct,
        "total_trades": result.total_trades,
    }


def _result_view(e: Evaluated) -> dict:
    macro = e.candidate.macro
    base = explain_result(macro, e.result)
    explanation = ai_explain_mod.enrich(macro, e.result, base=base)
    return {
        "label": e.candidate.label,
        "rule_type": macro.rule_type.value,
        "source": e.candidate.source,
        "macro": macro.model_dump(mode="json"),
        "metrics": _metrics(e.result),
        "explanation": explanation.model_dump(),
        "ai_generated": explanation.source == "ai",
    }


def run_ask(db: Session, user: User, req: AskRequest, run_backtest: Callable[[Macro], BacktestResult]) -> dict:
    if not consented(user):
        raise AskError(403, "먼저 안내에 동의해 주세요.")
    if remaining_today(db, user) <= 0:
        raise AskError(429, f"오늘은 {daily_limit()}번 다 물어봤어요. 내일 다시 물어봐 주세요.")

    started = time.monotonic()
    ai_candidates = propose_with_ai(req)
    candidates = (ai_candidates + build_templates(req))[:MAX_CANDIDATES]
    evaluated = evaluate(candidates, run_backtest, time_budget_sec())
    top = select_top(evaluated, req.risk_profile)
    results = [_result_view(e) for e in top]
    elapsed_ms = int((time.monotonic() - started) * 1000)

    created_at, created_ms = _now()
    db.add(AskMacroSession(
        user_id=user.id, day_kst=today_kst(), request_json=json.dumps(req.model_dump(), ensure_ascii=False),
        candidate_count=len(evaluated),
        results_json=json.dumps(
            [{"label": r["label"], "rule_type": r["rule_type"], "macro": r["macro"], "metrics": r["metrics"]} for r in results],
            ensure_ascii=False),
        disclaimer_version=DISCLAIMER_VERSION, ai_used=bool(ai_candidates), elapsed_ms=elapsed_ms,
        created_at=created_at, created_ms=created_ms,
    ))
    db.commit()
    return {
        "results": results,
        "candidate_count": len(evaluated),
        "ai_used": bool(ai_candidates),
        "disclaimer": DISCLAIMER,
        "disclaimer_version": DISCLAIMER_VERSION,
        "remaining_today": remaining_today(db, user),
        "elapsed_ms": elapsed_ms,
    }
```

- [ ] **Step 4: Add the routes** (`backend/app/main.py`)

import 줄 `from . import quests as quests_mod` 바로 아래에:

```python
from . import ask as ask_mod
```

`@app.get("/api/me/quests")` 라우트 바로 위에:

```python
# ── 껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합. 로그인 필수, 하루 한도, 고지 동의. ──
@app.get("/api/ask/status")
def ask_status(
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    return ask_mod.status(db, account)


@app.post("/api/ask/consent")
def ask_consent(
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    return ask_mod.give_consent(db, account)


@app.post("/api/ask/macros")
def ask_macros(
    req: ask_mod.AskRequest,
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    try:
        return ask_mod.run_ask(db, account, req, lambda macro: _run_any(macro)[0])
    except ask_mod.AskError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q`
Expected: 11 passed

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_quests.py backend/tests/test_ai_explain.py -q`
Expected: all passed (기존 동작 그대로)

- [ ] **Step 6: Commit**

```bash
git add backend/app/ask.py backend/app/main.py backend/tests/test_ask.py
git commit -m "껄무새에게 물어볼까? API: 상태·동의·상위 3개 조합 (하루 한도, 기록)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 프론트 — `askCopy.js` + `askFlow.js` 리듀서

**Files:**
- Create: `frontend/src/lib/askCopy.js`
- Create: `frontend/src/lib/askFlow.js`
- Test: `frontend/tests/askFlow.test.js`

**Interfaces:**
- Produces (`askCopy.js`): `STEP_PROMPTS`, `PROFILES`, `MARKETS`, `LEVERAGES`, `STABLE_NO_FUTURES`, `POPULAR_SYMBOLS`, `PERIODS`, `INTERVALS`, `DISCLAIMER`, `CONSENT_TEXT`, `FOLLOW_UPS`, `RUNNING_TEXT`, `FEW_RESULTS_TEXT`, `NO_RESULTS_TEXT`, `LOADED_TEXT`
- Produces (`askFlow.js`): `STEPS`, `MAX_SYMBOLS`, `initialState()`, `reduce(state, action)`, `nextStep(answers)`, `canChooseFutures(answers)`, `toRequest(answers)`, `answerLabel(step, answers)`
- 상태 모양: `{ step, answers: { profile, market, leverage, symbols, period, interval, [symbolsConfirmed] }, phase: "cards"|"ready"|"loading"|"results"|"error", results, remaining, error }`

- [ ] **Step 1: Write the failing tests** — `frontend/tests/askFlow.test.js`

```js
import assert from "node:assert/strict";
import test from "node:test";
import { STEPS, initialState, reduce, toRequest, canChooseFutures, answerLabel } from "../src/lib/askFlow.js";

const walk = (...actions) => actions.reduce(reduce, initialState());

test("cards run in order and the state becomes ready after the last answer", () => {
  assert.deepEqual(STEPS, ["profile", "market", "symbols", "period", "interval"]);
  let s = initialState();
  assert.equal(s.step, "profile");
  s = reduce(s, { type: "choose", step: "profile", value: "balanced" });
  assert.equal(s.step, "market");
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  assert.equal(s.step, "symbols");
  s = reduce(s, { type: "toggleSymbol", symbol: "btcusdt" });
  s = reduce(s, { type: "toggleSymbol", symbol: "ETHUSDT" });
  assert.deepEqual(s.answers.symbols, ["BTCUSDT", "ETHUSDT"]);
  assert.equal(s.step, "symbols");
  s = reduce(s, { type: "confirmSymbols" });
  assert.equal(s.step, "period");
  s = reduce(s, { type: "choose", step: "period", value: "6m" });
  s = reduce(s, { type: "choose", step: "interval", value: "4h" });
  assert.equal(s.phase, "ready");
  assert.deepEqual(toRequest(s.answers), {
    risk_profile: "balanced", market: "futures", leverage: 2, symbols: ["BTCUSDT", "ETHUSDT"], period_preset: "6m", interval: "4h",
  });
  assert.equal(answerLabel("market", s.answers), "선물 2x");
});

test("stable profile cannot choose futures", () => {
  let s = reduce(initialState(), { type: "choose", step: "profile", value: "stable" });
  assert.equal(canChooseFutures(s.answers), false);
  const rejected = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 1 } });
  assert.equal(rejected, s);
  s = reduce(s, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  assert.equal(s.step, "symbols");
});

test("symbols: max three, dedupe, confirm needs at least one", () => {
  let s = walk({ type: "choose", step: "profile", value: "aggressive" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  const empty = reduce(s, { type: "confirmSymbols" });
  assert.equal(empty, s);
  for (const sym of ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BTCUSDT"]) s = reduce(s, { type: "toggleSymbol", symbol: sym });
  assert.deepEqual(s.answers.symbols, ["ETHUSDT", "SOLUSDT"]);  // 4번째 거부, 마지막 BTC 토글로 제거
});

test("going back clears later answers; follow-ups jump to the right card", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "balanced" },
    { type: "choose", step: "market", value: { market: "spot", leverage: 1 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1d" },
  );
  assert.equal(s.phase, "ready");
  const back = reduce(s, { type: "back", step: "market" });
  assert.equal(back.step, "market");
  assert.equal(back.phase, "cards");
  assert.deepEqual(back.answers, { profile: "balanced", market: null, leverage: 1, symbols: [], period: null, interval: null });

  const results = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 4 });
  assert.equal(results.phase, "results");
  const safer = reduce(results, { type: "followUp", kind: "safer" });
  assert.equal(safer.answers.profile, "stable");
  assert.equal(safer.phase, "ready");
  const riskier = reduce(results, { type: "followUp", kind: "riskier" });
  assert.equal(riskier.answers.profile, "aggressive");
  const other = reduce(results, { type: "followUp", kind: "symbols" });
  assert.equal(other.step, "symbols");
  assert.deepEqual(other.answers.symbols, []);
  assert.equal(other.answers.period, "3m");  // 뒤 카드 답은 유지
  const again = reduce(other, { type: "toggleSymbol", symbol: "ETHUSDT" });
  assert.equal(reduce(again, { type: "confirmSymbols" }).phase, "ready");
  assert.deepEqual(reduce(results, { type: "followUp", kind: "restart" }), initialState());
});

test("safer from stable is a no-op, and stable clears futures", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "balanced" },
    { type: "choose", step: "market", value: { market: "futures", leverage: 3 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1h" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 1 });
  const safer = reduce(s, { type: "followUp", kind: "safer" });
  assert.deepEqual([safer.answers.profile, safer.answers.market, safer.answers.leverage], ["stable", "spot", 1]);
  assert.equal(reduce(safer, { type: "followUp", kind: "safer" }), safer);
  assert.equal(reduce(s, { type: "error", message: "boom" }).phase, "error");
  assert.equal(reduce(s, { type: "loading" }).phase, "loading");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && node --test tests/askFlow.test.js`
Expected: FAIL — `Cannot find module '../src/lib/askFlow.js'`

- [ ] **Step 3: Create `frontend/src/lib/askCopy.js`**

```js
// 껄무새에게 물어볼까? — 카드 문구·선택지·고지 문구를 한 곳에. '추천' 이라는 말은 쓰지 않는다.
export const DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요";

export const CONSENT_TEXT = "과거 데이터로 조합을 찾아 주는 도구예요. 투자 권유가 아니고, 결과가 미래 수익을 뜻하지 않아요.";

export const STEP_PROMPTS = {
  profile: "손실은 어디까지 견딜 수 있어요?",
  market: "어느 시장에서요?",
  symbols: "어떤 종목이 궁금해요? (최대 3개)",
  period: "어느 기간을 살펴볼까요?",
  interval: "얼마나 자주 사고팔고 싶어요?",
};

export const PROFILES = [
  { value: "stable", label: "안정형", hint: "-10%까지" },
  { value: "balanced", label: "균형형", hint: "-20%까지" },
  { value: "aggressive", label: "공격형", hint: "제한 없음" },
];

export const MARKETS = [
  { value: "spot", label: "현물" },
  { value: "futures", label: "선물" },
];
export const LEVERAGES = [1, 2, 3];
export const STABLE_NO_FUTURES = "안정형은 현물만 살펴봐요";

export const POPULAR_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"];

export const PERIODS = [
  { value: "3m", label: "최근 3개월" },
  { value: "6m", label: "최근 6개월" },
  { value: "1y", label: "최근 1년" },
];

export const INTERVALS = [
  { value: "1h", label: "자주", hint: "1시간 봉" },
  { value: "4h", label: "보통", hint: "4시간 봉" },
  { value: "1d", label: "느긋하게", hint: "하루 봉" },
];

export const FOLLOW_UPS = [
  { kind: "safer", label: "더 안정적으로" },
  { kind: "riskier", label: "더 공격적으로" },
  { kind: "symbols", label: "다른 종목으로" },
  { kind: "restart", label: "처음부터" },
];

export const RUNNING_TEXT = "돌려 볼게요… 후보를 백테스트하는 중이에요";
export const FEW_RESULTS_TEXT = "이 조건에선 후보가 적었어요 — 기간이나 빈도를 바꿔 보세요";
export const NO_RESULTS_TEXT = "이 조건으론 살아남은 후보가 없었어요. 조건을 바꿔 다시 물어봐요.";
export const LOADED_TEXT = "조건 판에 불러왔어요. 숫자 한 번 보고 백테스트부터 돌려 보세요";
```

- [ ] **Step 4: Create `frontend/src/lib/askFlow.js`**

```js
// 껄무새에게 물어볼까? — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목 최대 3개 · 뒤로 가면 그 뒤 답은 지운다 · 자유 입력 없음.
import { INTERVALS, LEVERAGES, MARKETS, PERIODS, PROFILES } from "./askCopy.js";

export const STEPS = ["profile", "market", "symbols", "period", "interval"];
export const MAX_SYMBOLS = 3;
const PROFILE_ORDER = ["stable", "balanced", "aggressive"];

function emptyAnswers() {
  return { profile: null, market: null, leverage: 1, symbols: [], period: null, interval: null };
}

export function initialState() {
  return { step: "profile", answers: emptyAnswers(), phase: "cards", results: null, remaining: null, error: "" };
}

export function canChooseFutures(answers) {
  return answers.profile !== "stable";
}

function answered(step, answers) {
  if (step === "symbols") return answers.symbols.length > 0 && answers.symbolsConfirmed === true;
  return answers[step] != null;
}

// 아직 답하지 않은 첫 카드. 전부 답했으면 null.
export function nextStep(answers) {
  return STEPS.find((step) => !answered(step, answers)) ?? null;
}

function settle(state, answers) {
  const step = nextStep(answers);
  if (step === null) return { ...state, answers, step: "interval", phase: "ready", error: "" };
  return { ...state, answers, step, phase: "cards", error: "" };
}

// symbolsConfirmed 는 종목을 확정했을 때만 키가 있다(뒤로 가기 테스트가 answers 모양을 정확히 비교한다).
function clearFrom(answers, step) {
  const next = { ...answers };
  for (const s of STEPS.slice(STEPS.indexOf(step))) {
    if (s === "symbols") { next.symbols = []; delete next.symbolsConfirmed; }
    else if (s === "market") { next.market = null; next.leverage = 1; }
    else next[s] = null;
  }
  return next;
}

export function reduce(state, action) {
  const { answers } = state;
  switch (action.type) {
    case "choose": {
      const { step, value } = action;
      if (step === "profile") {
        if (!PROFILES.some((p) => p.value === value)) return state;
        return settle(state, { ...clearFrom(answers, "profile"), profile: value });
      }
      if (step === "market") {
        const market = value?.market;
        const leverage = market === "futures" ? Number(value?.leverage || 1) : 1;
        if (!MARKETS.some((m) => m.value === market)) return state;
        if (market === "futures" && !canChooseFutures(answers)) return state;
        if (!LEVERAGES.includes(leverage)) return state;
        return settle(state, { ...clearFrom(answers, "market"), market, leverage });
      }
      if (step === "period") {
        if (!PERIODS.some((p) => p.value === value)) return state;
        return settle(state, { ...clearFrom(answers, "period"), period: value });
      }
      if (step === "interval") {
        if (!INTERVALS.some((i) => i.value === value)) return state;
        return settle(state, { ...answers, interval: value });
      }
      return state;
    }
    case "toggleSymbol": {
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol) return state;
      const has = answers.symbols.includes(symbol);
      if (!has && answers.symbols.length >= MAX_SYMBOLS) return state;
      const symbols = has ? answers.symbols.filter((s) => s !== symbol) : [...answers.symbols, symbol];
      const next = { ...answers, symbols };
      delete next.symbolsConfirmed;
      return { ...state, answers: next, step: "symbols", phase: "cards", error: "" };
    }
    case "confirmSymbols": {
      if (answers.symbols.length === 0) return state;
      return settle(state, { ...answers, symbolsConfirmed: true });
    }
    case "back": {
      if (!STEPS.includes(action.step)) return state;
      return { ...state, answers: clearFrom(answers, action.step), step: action.step, phase: "cards", results: null, error: "" };
    }
    case "loading":
      return { ...state, phase: "loading", error: "" };
    case "results":
      return { ...state, phase: "results", results: action.results || [], remaining: action.remaining ?? state.remaining, error: "" };
    case "error":
      return { ...state, phase: "error", error: String(action.message || "잠시 뒤 다시 물어봐 주세요.") };
    case "followUp": {
      const { kind } = action;
      if (kind === "restart") return initialState();
      if (kind === "symbols") {
        const next = { ...answers, symbols: [] };
        delete next.symbolsConfirmed;
        return { ...state, answers: next, step: "symbols", phase: "cards", results: null, error: "" };
      }
      if (kind === "safer" || kind === "riskier") {
        const idx = PROFILE_ORDER.indexOf(answers.profile);
        const nextIdx = kind === "safer" ? idx - 1 : idx + 1;
        if (nextIdx < 0 || nextIdx >= PROFILE_ORDER.length) return state;
        const profile = PROFILE_ORDER[nextIdx];
        const next = { ...answers, profile };
        if (profile === "stable" && next.market === "futures") { next.market = "spot"; next.leverage = 1; }
        return { ...settle(state, next), results: null };
      }
      return state;
    }
    default:
      return state;
  }
}

export function toRequest(answers) {
  return {
    risk_profile: answers.profile,
    market: answers.market,
    leverage: answers.market === "futures" ? answers.leverage : 1,
    symbols: answers.symbols,
    period_preset: answers.period,
    interval: answers.interval,
  };
}

// 내 말풍선에 쓰는 답 라벨.
export function answerLabel(step, answers) {
  if (step === "profile") return PROFILES.find((p) => p.value === answers.profile)?.label ?? "";
  if (step === "market") {
    if (answers.market === "futures") return `선물 ${answers.leverage}x`;
    return MARKETS.find((m) => m.value === answers.market)?.label ?? "";
  }
  if (step === "symbols") return answers.symbols.join(", ");
  if (step === "period") return PERIODS.find((p) => p.value === answers.period)?.label ?? "";
  if (step === "interval") {
    const found = INTERVALS.find((i) => i.value === answers.interval);
    return found ? `${found.label} (${found.hint})` : "";
  }
  return "";
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && node --test tests/askFlow.test.js`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/askCopy.js frontend/src/lib/askFlow.js frontend/tests/askFlow.test.js
git commit -m "껄무새에게 물어볼까? 프론트: 카드 문구와 순수 리듀서(순서·되돌리기·성향 규칙)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 프론트 — API 함수 + `AskParrotDialog`

**Files:**
- Modify: `frontend/src/api.js` (`myQuests:` 줄 아래)
- Create: `frontend/src/components/AskParrotDialog.jsx`
- Create: `frontend/src/components/AskParrotDialog.css`

**Interfaces:**
- Consumes: `askFlow.js` 전부, `askCopy.js` 전부, `api.askStatus/askConsent/askMacros`, `RULE_TYPES` (`lib/macro.js`), 모달 마크업 패턴(`RegisterMacroModal.jsx` 의 `.scrim` / `.dialog`)
- Produces: `<AskParrotDialog open onClose onLoad(macro, label) />` — `onLoad` 는 결과 카드의 "조건 판에 불러오기"

- [ ] **Step 1: API 함수** — `frontend/src/api.js` 의 `myQuests: ...` 줄 아래에:

```js
  // 껄무새에게 물어볼까? — 고지 동의 상태·남은 횟수 / 동의 / 상위 3개 조합
  askStatus: (options = {}) => req("/api/ask/status", options),
  askConsent: () => req("/api/ask/consent", { method: "POST" }),
  askMacros: (body, options = {}) => req("/api/ask/macros", { method: "POST", body: JSON.stringify(body), timeoutMs: 45_000, ...options }),
```

(`req` 는 `timeoutMs` 옵션을 이미 받는다 — `const { signal: callerSignal, timeoutMs, ... } = opts;`)

- [ ] **Step 2: Dialog CSS** — `frontend/src/components/AskParrotDialog.css`

```css
/* 껄무새에게 물어볼까? — 말풍선 + 칩 카드 모달 */
.ask-thread { display: flex; flex-direction: column; gap: 12px; }
.ask-bubble { max-width: 88%; padding: 10px 14px; border-radius: 16px; line-height: 1.5; }
.ask-bubble-parrot { align-self: flex-start; background: var(--surface-2, #f1f5f9); color: var(--text, #0f172a); border-bottom-left-radius: 4px; }
.ask-bubble-me { align-self: flex-end; background: var(--brand-600, #4f46e5); color: #fff; border-bottom-right-radius: 4px; cursor: pointer; border: 0; }
.ask-bubble-me:hover { filter: brightness(1.08); }
.ask-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; align-items: center; }
.ask-chip { border: 1px solid var(--border, #cbd5e1); border-radius: 999px; padding: 6px 12px; background: var(--surface, #fff); cursor: pointer; }
.ask-chip[aria-pressed="true"] { background: var(--brand-50, #eef2ff); border-color: var(--brand-600, #4f46e5); }
.ask-chip:disabled { opacity: .45; cursor: not-allowed; }
.ask-chip-hint { margin-left: 6px; opacity: .7; }
.ask-symbol-search { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.ask-symbol-search input { flex: 1; min-width: 140px; }
.ask-results { display: grid; gap: 12px; margin-top: 8px; }
.ask-card { border: 1px solid var(--border, #e2e8f0); border-radius: 14px; padding: 14px; background: var(--surface, #fff); }
.ask-card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.ask-card-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 10px 0; }
.ask-card-metrics div { text-align: center; }
.ask-card-metrics dt { font-size: 12px; opacity: .7; }
.ask-card-metrics dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.ask-card-settings { margin: 6px 0; padding-left: 16px; font-size: 13px; color: var(--text-2, #334155); }
.ask-card-why { margin: 8px 0; font-size: 13px; }
.ask-card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.ask-disclaimer { margin-top: 12px; padding: 8px 10px; border-radius: 10px; background: var(--surface-2, #f8fafc); font-size: 12px; color: var(--text-2, #475569); }
.ask-followups { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
@media (max-width: 480px) { .ask-card-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .ask-bubble { max-width: 96%; } }
```

- [ ] **Step 3: Dialog component** — `frontend/src/components/AskParrotDialog.jsx`

```jsx
// 껄무새에게 물어볼까? — 카드(칩) 다섯 장으로 답을 받아 백테스트 상위 3개 조합을 보여 준다.
// 자유 입력은 종목 검색 하나뿐. 결과의 숫자는 전부 서버 백테스트가 계산한 값이다.
import { useEffect, useId, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import {
  CONSENT_TEXT, DISCLAIMER, FEW_RESULTS_TEXT, FOLLOW_UPS, INTERVALS, LEVERAGES, MARKETS, NO_RESULTS_TEXT,
  PERIODS, POPULAR_SYMBOLS, PROFILES, RUNNING_TEXT, STABLE_NO_FUTURES, STEP_PROMPTS,
} from "../lib/askCopy.js";
import { MAX_SYMBOLS, STEPS, answerLabel, canChooseFutures, initialState, reduce, toRequest } from "../lib/askFlow.js";
import { RULE_TYPES } from "../lib/macro.js";
import "./AskParrotDialog.css";

const PCT = (v) => `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const RECENT_KEY = "ggparrot.ask.recentSymbols";

function readRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter(Boolean).slice(0, 5); } catch { return []; }
}
function writeRecent(symbols) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([...new Set([...symbols, ...readRecent()])].slice(0, 5))); } catch { /* 저장 못 해도 동작 */ }
}

// 결과 카드의 핵심 설정 3줄 — 파라미터를 사람이 읽는 문장으로.
function settingLines(macro) {
  const p = macro.params || {};
  const r = macro.risk || {};
  const lines = [];
  switch (macro.rule_type) {
    case "A": lines.push(`익절 +${p.take_profit_pct}%`, r.stop_loss_pct != null ? `손절 -${r.stop_loss_pct}%` : "손절 없음"); break;
    case "C": lines.push(`${p.interval_days}일마다 ${Number(p.amount_per_buy).toLocaleString()}원씩 매수`); break;
    case "E": lines.push(`+${p.activation_profit}% 뒤 트레일링 시작`, `고점 대비 -${p.trail_percent}% 에 청산`); break;
    case "F": lines.push(`RSI(${p.rsi_period}) ${p.entry_threshold} 아래 매수`, `${p.exit_threshold} 위 매도`); break;
    case "G": lines.push(`볼린저(${p.bb_period}, ${p.bb_std}σ) 하단 매수`, p.exit_target === "mid" ? "중심선 매도" : "상단 매도"); break;
    case "H": lines.push(`기본 ${Number(p.base_order_size).toLocaleString()} + 세이프티 ${p.max_safety_orders}회`, `평단 +${p.take_profit}% 익절`); break;
    case "I": lines.push(`변동성 돌파 k=${p.k}`, p.exit_mode === "next_open" ? "다음 봉 시가 청산" : `청산: ${p.exit_mode}`); break;
    case "J": lines.push(`${p.ma_type} ${p.fast_period}/${p.slow_period} 골든크로스 매수`, "데드크로스 매도"); break;
    default: break;
  }
  lines.push(`${macro.candle_interval} 봉 · ${macro.market === "futures" ? `선물 ${macro.leverage}x` : "현물"}`);
  return lines.slice(0, 3);
}

function Chips({ options, value, onPick }) {
  return (
    <div className="ask-chips" role="group">
      {options.map((opt) => (
        <button key={opt.value} type="button" className="ask-chip t-small" aria-pressed={value === opt.value} onClick={() => onPick(opt.value)}>
          {opt.label}{opt.hint ? <span className="ask-chip-hint">{opt.hint}</span> : null}
        </button>
      ))}
    </div>
  );
}

function SymbolsCard({ answers, dispatch }) {
  const [query, setQuery] = useState("");
  const recent = readRecent().filter((s) => !POPULAR_SYMBOLS.includes(s));
  const add = () => {
    const sym = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!sym) return;
    dispatch({ type: "toggleSymbol", symbol: sym.endsWith("USDT") ? sym : `${sym}USDT` });
    setQuery("");
  };
  const chips = [...recent, ...POPULAR_SYMBOLS, ...answers.symbols.filter((s) => !recent.includes(s) && !POPULAR_SYMBOLS.includes(s))];
  const full = answers.symbols.length >= MAX_SYMBOLS;
  return (
    <>
      <div className="ask-chips">
        {chips.map((sym) => (
          <button key={sym} type="button" className="ask-chip t-small" aria-pressed={answers.symbols.includes(sym)}
            disabled={!answers.symbols.includes(sym) && full}
            onClick={() => dispatch({ type: "toggleSymbol", symbol: sym })}>{sym.replace(/USDT$/, "")}</button>
        ))}
      </div>
      <div className="ask-symbol-search">
        <input className="input" value={query} placeholder="종목 검색 (예: AVAX)" aria-label="종목 검색" disabled={full}
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        <button type="button" className="btn btn-s btn-secondary" onClick={add} disabled={full}>추가</button>
        <button type="button" className="btn btn-s btn-primary" disabled={answers.symbols.length === 0}
          onClick={() => dispatch({ type: "confirmSymbols" })}>이 종목으로</button>
      </div>
    </>
  );
}

function StepCard({ step, answers, dispatch }) {
  if (step === "profile") return <Chips options={PROFILES} value={answers.profile} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  if (step === "market") {
    const futuresOk = canChooseFutures(answers);
    return (
      <div className="ask-chips">
        <button type="button" className="ask-chip t-small" onClick={() => dispatch({ type: "choose", step, value: { market: "spot", leverage: 1 } })}>{MARKETS[0].label}</button>
        {LEVERAGES.map((lev) => (
          <button key={lev} type="button" className="ask-chip t-small" disabled={!futuresOk} title={futuresOk ? undefined : STABLE_NO_FUTURES}
            onClick={() => dispatch({ type: "choose", step, value: { market: "futures", leverage: lev } })}>{MARKETS[1].label} {lev}x</button>
        ))}
        {!futuresOk ? <span className="t-caption text-slate-500">{STABLE_NO_FUTURES}</span> : null}
      </div>
    );
  }
  if (step === "symbols") return <SymbolsCard answers={answers} dispatch={dispatch} />;
  if (step === "period") return <Chips options={PERIODS} value={answers.period} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  if (step === "interval") return <Chips options={INTERVALS} value={answers.interval} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  return null;
}

function ResultCard({ item, onLoad }) {
  const m = item.metrics;
  return (
    <article className="ask-card" aria-label={item.label}>
      <div className="ask-card-head">
        <strong className="t-title">{RULE_TYPES[item.rule_type]?.label ?? item.label}</strong>
        <span className="t-caption text-slate-500">{item.label}</span>
      </div>
      <dl className="ask-card-metrics">
        <div><dt>수익률</dt><dd className="num">{PCT(m.final_return_pct)}</dd></div>
        <div><dt>최대 낙폭</dt><dd className="num">-{Number(m.mdd_pct).toFixed(2)}%</dd></div>
        <div><dt>승률</dt><dd className="num">{Number(m.win_rate_pct).toFixed(0)}%</dd></div>
        <div><dt>거래</dt><dd className="num">{m.total_trades}회</dd></div>
      </dl>
      <ul className="ask-card-settings">{settingLines(item.macro).map((line) => <li key={line}>{line}</li>)}</ul>
      {item.explanation ? (
        <div className="ask-card-why">
          <strong>왜 이 조합?</strong> <span className="badge badge-flat">{item.ai_generated ? "AI 생성" : "규칙 기반"}</span>
          <p className="mt-1">{item.explanation.headline}</p>
          <ul className="ask-card-settings">{(item.explanation.points || []).slice(0, 3).map((pt) => <li key={pt}>{pt}</li>)}</ul>
        </div>
      ) : null}
      <div className="ask-card-actions">
        <button type="button" className="btn btn-s btn-primary" onClick={() => onLoad(item.macro, item.label)}>조건 판에 불러오기</button>
      </div>
    </article>
  );
}

export default function AskParrotDialog({ open, onClose, onLoad }) {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [status, setStatus] = useState(null); // {consented, remaining_today, daily_limit} | {error:true}
  const [consentBusy, setConsentBusy] = useState(false);
  const titleId = useId();
  const threadRef = useRef(null);

  // 열 때 동의 상태·남은 횟수를 읽는다. 닫으면 상태를 버린다.
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    api.askStatus().then((s) => alive && setStatus(s)).catch(() => alive && setStatus({ error: true }));
    return () => { alive = false; setStatus(null); dispatch({ type: "followUp", kind: "restart" }); };
  }, [open]);

  // 카드를 다 답하면 서버에 묻는다.
  useEffect(() => {
    if (state.phase !== "ready") return undefined;
    let alive = true;
    dispatch({ type: "loading" });
    writeRecent(state.answers.symbols);
    api.askMacros(toRequest(state.answers))
      .then((data) => {
        if (!alive) return;
        dispatch({ type: "results", results: data.results, remaining: data.remaining_today });
        setStatus((s) => (s && !s.error ? { ...s, remaining_today: data.remaining_today } : s));
      })
      .catch((err) => { if (alive) dispatch({ type: "error", message: err?.message || "잠시 뒤 다시 물어봐 주세요." }); });
    return () => { alive = false; };
  }, [state.phase, state.answers]);

  useEffect(() => { threadRef.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" }); }, [state.step, state.phase]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const consent = async () => {
    setConsentBusy(true);
    try {
      const r = await api.askConsent();
      setStatus((s) => ({ ...(s || {}), consented: r.version != null }));
    } catch {
      setStatus({ error: true });
    } finally {
      setConsentBusy(false);
    }
  };

  const isAnswered = (s) => (s === "symbols" ? state.answers.symbolsConfirmed === true : state.answers[s] != null);
  const answeredSteps = STEPS.filter(isAnswered).filter((s) => state.phase !== "cards" || STEPS.indexOf(s) < STEPS.indexOf(state.step));
  const noQuota = status && !status.error && status.remaining_today <= 0;

  return createPortal(
    <div className="scrim fixed inset-x-0 bottom-0 top-16 z-[80] flex items-start justify-center p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className="dialog w-full max-w-2xl my-4 sm:my-8">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 sticky top-0 bg-surface rounded-t-[20px] z-10">
          <h2 id={titleId} className="t-h4 text-slate-900">껄무새에게 물어볼까?</h2>
          <div className="flex items-center gap-3">
            {status && !status.error ? <span className="t-caption text-slate-500">오늘 {status.remaining_today}/{status.daily_limit}번 남음</span> : null}
            <button type="button" onClick={onClose} className="btn btn-s btn-ghost text-xl leading-none" aria-label="닫기">×</button>
          </div>
        </div>

        <div className="px-6 py-6">
          {status == null ? <p className="t-small text-slate-500">잠깐만요…</p> : status.error ? (
            <p className="t-small text-slate-700">지금은 물어볼 수 없어요. 잠시 뒤 다시 열어 주세요.</p>
          ) : !status.consented ? (
            <div className="ask-thread">
              <div className="ask-bubble ask-bubble-parrot t-small">{CONSENT_TEXT}</div>
              <div><button type="button" className="btn btn-m btn-primary" disabled={consentBusy} onClick={consent}>알겠어요</button></div>
            </div>
          ) : (
            <div className="ask-thread" ref={threadRef}>
              {answeredSteps.map((s) => (
                <div key={s} className="contents">
                  <div className="ask-bubble ask-bubble-parrot t-small">{STEP_PROMPTS[s]}</div>
                  <button type="button" className="ask-bubble ask-bubble-me t-small" title="다시 고르기" onClick={() => dispatch({ type: "back", step: s })}>{answerLabel(s, state.answers)}</button>
                </div>
              ))}

              {state.phase === "cards" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">{STEP_PROMPTS[state.step]}</div>
                  <StepCard step={state.step} answers={state.answers} dispatch={dispatch} />
                </>
              ) : null}

              {state.phase === "loading" || state.phase === "ready" ? <div className="ask-bubble ask-bubble-parrot t-small" role="status">{RUNNING_TEXT}</div> : null}

              {state.phase === "error" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small" role="alert">{state.error}</div>
                  <div className="ask-followups"><button type="button" className="btn btn-s btn-secondary" onClick={() => dispatch({ type: "followUp", kind: "restart" })}>처음부터</button></div>
                </>
              ) : null}

              {state.phase === "results" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">
                    {state.results.length === 0 ? NO_RESULTS_TEXT : state.results.length < 3 ? FEW_RESULTS_TEXT : "과거 데이터로 돌려 본 후보 중 상위 3개예요."}
                  </div>
                  {state.results.length ? (
                    <div className="ask-results">
                      {state.results.map((item) => <ResultCard key={item.label + item.rule_type} item={item} onLoad={onLoad} />)}
                    </div>
                  ) : null}
                  <div className="ask-disclaimer" role="note">{DISCLAIMER}</div>
                  <div className="ask-followups">
                    {FOLLOW_UPS.map((f) => (
                      <button key={f.kind} type="button" className="btn btn-s btn-secondary"
                        disabled={noQuota && f.kind !== "restart"} title={noQuota && f.kind !== "restart" ? "오늘 횟수를 다 썼어요" : undefined}
                        onClick={() => dispatch({ type: "followUp", kind: f.kind })}>{f.label}</button>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
```

- [ ] **Step 4: Build check**

Run: `cd frontend && npx vite build --logLevel error`
Expected: 빌드 성공. 실패하면 import 이름(`RULE_TYPES` 는 `lib/macro.js`)과 CSS 경로를 확인.

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: 기존 + 5개 통과

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/components/AskParrotDialog.jsx frontend/src/components/AskParrotDialog.css
git commit -m "껄무새에게 물어볼까? 모달: 말풍선·칩 카드, 결과 3장, 고지·후속 버튼

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Studio 배선 — 버튼·모달·`?ask=1`·조건 판에 싣기

**Files:**
- Modify: `frontend/src/pages/Studio.jsx` (imports L1-39; state 근처 L214-250 `const [tourOpen, setTourOpen]`; 조건 판 헤더 `studio-head-right` ~L790-805; `<Builder .../>` 바로 위; 렌더 끝 `<RegisterMacroModal>` 옆)
- Modify: `frontend/src/pages/Studio.css` (버튼 스타일 1개)

**Interfaces:**
- Consumes: `AskParrotDialog`, `ConfirmDialog(open, title, description, confirmLabel, onConfirm, onCancel)`, `macroToForm`, `defaultForm` (`lib/macro.js`), 기존 `setLoadedFrom`, `recordEvent` (`lib/visit.js`), `LOADED_TEXT`, `searchParams/setSearchParams` (이미 있음), `token` (이미 있음), `slug`, `busy`

- [ ] **Step 1: imports** — `import RegisterMacroModal from "../components/RegisterMacroModal.jsx";` 아래에:

```jsx
import AskParrotDialog from "../components/AskParrotDialog.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { LOADED_TEXT } from "../lib/askCopy.js";
```

`../lib/macro.js` import 목록(L15-25)에 `macroToForm` 은 있다. `defaultForm` 이 없으면 목록에 추가한다.

- [ ] **Step 2: state + handlers** — `const [tourOpen, setTourOpen] = useState(false);` 아래에:

```jsx
  // 껄무새에게 물어볼까? — 모달, 덮어쓰기 확인, 불러온 뒤 안내
  const [askOpen, setAskOpen] = useState(() => searchParams.get("ask") === "1");
  const [askPending, setAskPending] = useState(null); // {macro, label} — 조건 판에 입력이 있을 때 확인 대기
  const [askNotice, setAskNotice] = useState("");

  const applyAskMacro = useCallback((macro, label) => {
    setForm(macroToForm(macro));
    setLoadedFrom(`껄무새가 고른 후보 · ${label}`);
    setAskPending(null);
    setAskOpen(false);
    setAskNotice(LOADED_TEXT);
    recordEvent("ask_load");
    if (searchParams.get("ask") === "1") {
      const next = new URLSearchParams(searchParams);
      next.delete("ask");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const onAskLoad = useCallback((macro, label) => {
    const untouched = JSON.stringify(form) === JSON.stringify(defaultForm());
    if (untouched) applyAskMacro(macro, label);
    else setAskPending({ macro, label });
  }, [form, applyAskMacro]);

  useEffect(() => {
    if (!askNotice) return undefined;
    const id = setTimeout(() => setAskNotice(""), 8000);
    return () => clearTimeout(id);
  }, [askNotice]);
```

`setLoadedFrom` 은 이미 이 컴포넌트에 있다(L359 에서 씀). `setForm`·`setLoadedFrom` 이 `useCallback` 의존성 경고를 내면 배열에 넣는다(둘 다 안정적인 setter).

- [ ] **Step 3: 버튼** — `studio-head-right` div 안, 매크로 업로드 블록(`{!slug && (token ? (` 로 시작) **앞**에:

```jsx
              {/* 껄무새에게 물어볼까? — 카드 다섯 장으로 후보 조합 3개. 로그인 전엔 로그인으로(기록을 남겨야 해서). */}
              {!slug && (token ? (
                <button type="button" className="studio-cond-upload studio-cond-ask t-caption" onClick={() => { setAskOpen(true); recordEvent("ask_open"); }} disabled={busy} aria-haspopup="dialog" aria-expanded={askOpen}>
                  <span aria-hidden="true">🦜</span><span>껄무새에게 물어볼까?</span>
                </button>
              ) : (
                <Link to="/login?next=%2Fbuilder%3Fask%3D1" className="studio-cond-upload studio-cond-ask t-caption" title="물어보려면 로그인이 필요해요"><span aria-hidden="true">🦜</span><span>껄무새에게 물어볼까?</span></Link>
              ))}
```

- [ ] **Step 4: 안내 + 모달 렌더** — `<Builder form={form} setForm={setForm} variant="dense" ... />` 바로 위에:

```jsx
            {askNotice ? <div className="notice t-small text-slate-700 mb-3" role="status">{askNotice}</div> : null}
```

컴포넌트 return 의 `<RegisterMacroModal ... />` 옆에:

```jsx
      <AskParrotDialog open={askOpen} onClose={() => setAskOpen(false)} onLoad={onAskLoad} />
      <ConfirmDialog
        open={askPending != null}
        title="지금 조건이 바뀌어요"
        description="조건 판에 입력한 값을 껄무새가 고른 후보로 덮어써요. 계속할까요?"
        confirmLabel="불러오기"
        onConfirm={() => askPending && applyAskMacro(askPending.macro, askPending.label)}
        onCancel={() => setAskPending(null)}
      />
```

- [ ] **Step 5: CSS** — `frontend/src/pages/Studio.css` 의 `.studio-cond-upload` 규칙 아래에:

```css
/* 껄무새에게 물어볼까? — 업로드 버튼과 같은 꼴, 앞에 앵무새만 */
.studio-cond-ask { margin-right: 6px; }
```

- [ ] **Step 6: 로컬에서 확인**

1. `preview_start` 로 `.claude/launch.json` 의 dev 서버를 띄운다. 백엔드는 `--reload` 가 없으니 Task 5 이후 재시작한다.
2. 로그인 → `/builder` → "껄무새에게 물어볼까?" 클릭 → 동의 카드 → 카드 5장 → 결과 3장 → "조건 판에 불러오기" → 조건 판이 바뀌고 안내 문구가 뜨는지.
3. 안정형에서 선물 칩이 비활성인지, 내 말풍선을 눌러 되돌아가는지, 후속 버튼 4개가 동작하는지.
4. `read_console_messages` 로 오류 없음 확인, 스크린샷 1장.
5. `/builder?ask=1` 로 들어오면 바로 열리는지.

- [ ] **Step 7: 전체 테스트**

Run: `cd frontend && node --test "tests/*.test.js"` — Expected: 전부 통과
Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py backend/tests/test_quests.py -q` — Expected: 전부 통과

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Studio.jsx frontend/src/pages/Studio.css
git commit -m "직접 만들기에 '껄무새에게 물어볼까?' 버튼 — 후보를 조건 판에 싣고 ?ask=1 로 바로 열기

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 안내 문구·환경 변수 문서 + 마무리 회귀

**Files:**
- Modify: `frontend/src/pages/Guide.jsx` (FAQ 목록 — "종목을 여러 개 고르면 매매가 어떻게 되나요?" 항목 옆, 같은 구조로)
- Modify: `DEPLOY.md` (환경 변수 표; 파일이 없으면 `render.yaml` 의 env 주석)

- [ ] **Step 1: Guide FAQ 한 항목**

기존 FAQ 항목 구조를 그대로 따라 추가한다(질문/답 필드명은 옆 항목과 같게):

```jsx
  {
    q: "껄무새에게 물어볼까? 는 투자 추천인가요?",
    a: "아니에요. 고른 종목과 조건으로 여러 조합을 과거 데이터에 돌려 본 뒤 상위 3개를 보여 주는 탐색 도구예요. 종목은 직접 고른 것만 쓰고, 숫자는 전부 백테스트 결과예요. 투자 권유가 아니고 과거 성과가 미래 수익을 보장하지 않아요. 하루 5번까지 물어볼 수 있어요.",
  },
```

- [ ] **Step 2: 환경 변수 기록**

`DEPLOY.md` 환경 변수 표에 두 줄:

```
| ASK_DAILY_LIMIT | 5 | 껄무새에게 물어볼까? 하루 횟수/계정 |
| ASK_TIME_BUDGET_SEC | 20 | 후보 백테스트 시간 예산(초). 넘으면 남은 후보는 건너뛴다 |
```

- [ ] **Step 3: 전체 회귀**

Run: `backend/.venv/Scripts/python -m pytest backend/tests -q --ignore=backend/tests/test_agent_collector_runner.py --ignore=backend/tests/test_news.py --ignore=backend/tests/test_http_runtime.py`
Expected: 통과 (제외한 세 파일은 이전부터 네트워크/prefect 의존으로 실패하던 것)

Run: `cd frontend && node --test "tests/*.test.js" && npx vite build --logLevel error`
Expected: 통과, 빌드 성공

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Guide.jsx DEPLOY.md
git commit -m "껄무새에게 물어볼까? 안내: 가이드 FAQ 와 환경 변수 문서

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 배포 메모 (계획 밖, 실행자가 알아야 할 것)

- Supabase 마이그레이션은 main 머지 흐름에서 적용된다. Render 는 main 자동 배포. 환경 변수는 기본값이면 추가할 것 없음.
- Gemini 키(`GEMINI_API_KEY`)가 없으면 템플릿 후보만으로 동작한다 — 기능은 켜져 있다.
- 푸시는 사용자가 "노희재 이름으로 메인 푸쉬" 라고 할 때만.
