# 프로 빌더 코치 패널 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로 빌더 옆에서 좁혀 가는 대화로 조건 판을 채우는 코치 패널을 만든다.

**Architecture:** 대화의 뼈대(다음 질문·선택지·폼 패치)는 서버의 **결정론적 질문 그래프**가 정하고, AI 는 한 줄 말투와 선택지 순서만 손댄다 — 선택지의 값과 집합은 못 바꾼다. 그래서 AI 가 죽어도 코치가 돌고, 코치가 낼 수 있는 폼이 전부 유효하다는 것을 그래프 전수 경로 시험이 증명한다. 한도·동의·세션은 기존 `ask.py` 의 기계를 그대로 재사용한다.

**Tech Stack:** Python 3 / FastAPI / SQLModel / pydantic v2 (backend), React + Vite (frontend)

**Spec:** `docs/superpowers/specs/2026-10-07-pro-coach-panel-design.md`

## Global Constraints

- **시험 실행.** backend: `cd backend && .venv/Scripts/python.exe -m pytest tests/<file> -q`. 맨 `python` 은 `ModuleNotFoundError: korcen` 으로 죽는다. frontend: `cd frontend && node --test "tests/*.test.js"` — **글롭에 따옴표 필수**(Node v24.18 에서 `node --test tests/` 는 실패).
- **수집에서 실패하는 시험 파일 6개** — 전체 수트에 `--ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`.
- **기존 실패 4개는 회귀가 아니다** — `test_runner_release.py::test_all_release_surfaces_match_runner_source`, `test_openai_migration.py::test_no_old_provider_sdk_or_key_gates_remain`, `test_agent_collector_runner.py` 2개.
- **실행기는 건드리지 않는다.** `runner/` 아래 어떤 파일도 수정 금지. `RUNNER_VERSION` 고정.
- **DB 마이그레이션을 만들지 않는다.** 코치 세션은 기존 `AskMacroSession` 행이고 구분은 `request_json` 안의 `"kind": "coach"` 다. 새 열·새 테이블 금지.
- **`Macro` 에 필드를 더하지 않는다.** 코치는 폼만 만진다. (`Macro` 에 칸이 늘면 모든 서명이 바뀐다 — 1차에서 겪었다.)
- **AI 는 선택지의 값·집합·패치를 바꿀 수 없다.** 말투(`ask`)와 라벨 문구, 순서만. 이것이 스펙 원칙 2·3 을 지키는 유일한 장치다.
- **프런트에 라벨 사전을 두지 않는다.** 선택지 라벨과 근거는 서버가 보낸 것만 쓴다(1차의 문구 이중 구현을 반복하지 않는다).
- **한국어 문구.** 사용자에게 보이는 모든 문장은 한국어. 주석도 저장소 관례대로 한국어.
- 파일은 UTF-8 / LF. 파이썬 파일에 BOM 금지.

## Review Focus

1. **한도가 턴마다 깎이는 실수** — 좁혀 가는 대화에서 턴마다 깎으면 사용자가 되돌아가기를 두려워하고, 10턴 대화 한 번이 하루 한도를 다 쓴다. (Task 2 Step 1)
2. **한도를 깎고 세션 생성이 실패했을 때 돌려주지 않는 것** — 1차·2차에서 "경로 하나를 빠뜨려" 생긴 결함과 같은 자리. `ask.py` 가 이미 환불 패턴을 갖고 있다. (Task 2 Step 1)
3. **AI 가 선택지를 빠뜨리거나 끼워넣는 것** — `order` 가 그래프의 값 집합과 다를 때 그대로 쓰면 사용자가 고를 수 없는 선택지가 생기거나 없는 값이 폼에 들어간다. (Task 4 Step 1)
4. **턴 상한에서 반쯤 채운 폼을 던지고 멈추는 것** — 사용자에게는 "코치가 고장났다" 로 보인다. (Task 2 Step 1)
5. **그래프에 유효하지 않은 조합이 들어가는 것** — 코치가 채운 판이 `검증하기` 를 눌렀을 때 서버에서 거절되면 코치를 믿을 수 없게 된다. (Task 8 전체)

---

### Task 1: 질문 그래프

**Files:**
- Create: `backend/app/coach_graph.py`
- Test: `backend/tests/test_coach_graph.py` (create)

**Interfaces:**
- Produces: `Choice(value, label, patch, why)`, `Question(key, ask, kind, choices, field)`, `GRAPH: dict[str, Question]`, `FIRST_KEY: str`, `next_key(answers: dict) -> Optional[str]`, `choices_for(key, answers) -> tuple[Choice, ...]`, `patch_for(key, answer, answers) -> dict`, `defaults_for_remaining(answers) -> dict`, `PAGE_SIZE = 5`, `to_json() -> dict`

이 파일은 **순수 데이터와 순수 함수**다. DB·HTTP·AI·시계를 모르게 둔다 — 그래야 전수 시험이 싸다.

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_coach_graph.py`:

```python
"""질문 그래프 — 순수 데이터. 모든 경로가 유효한 폼을 내는지는 프런트 전수 시험이 본다."""
import pytest

from app.coach_graph import (
    FIRST_KEY, GRAPH, PAGE_SIZE, choices_for, defaults_for_remaining, next_key, patch_for, to_json,
)

ALL_KEYS = ("goal", "risk", "watch", "rule", "symbols", "weights", "period",
            "capital", "entry_filter", "bundle_risk")


def test_first_key_is_goal():
    assert FIRST_KEY == "goal"


def test_every_key_is_in_the_graph():
    assert set(GRAPH) == set(ALL_KEYS)


def test_choice_nodes_have_three_to_five_visible_options():
    """한 번에 보여 주는 것은 다섯 개까지 — 더 있으면 '다른 선택지 보기' 로 넘긴다."""
    for key, q in GRAPH.items():
        if q.kind != "choice":
            continue
        assert len(q.choices) >= 3, key
        assert len(q.choices[:PAGE_SIZE]) <= 5, key


def test_choice_values_are_unique_per_node():
    for key, q in GRAPH.items():
        values = [c.value for c in q.choices]
        assert len(values) == len(set(values)), key


def test_labels_are_short_enough_for_the_panel():
    """패널은 고정 폭 340px 다 — 라벨이 길면 잘린다(1차 교훈)."""
    for key, q in GRAPH.items():
        for c in q.choices:
            assert len(c.label) <= 28, (key, c.value, c.label)


def test_patch_keys_are_flat_form_keys():
    """패치는 평평한 폼 키만 담는다 — 프런트가 setForm 한 번으로 끝내야 한다."""
    for key, q in GRAPH.items():
        for c in q.choices:
            for k, v in c.patch.items():
                assert "." not in k and not isinstance(v, dict), (key, c.value, k)


# --- 흐름 -------------------------------------------------------------
def test_flow_reaches_the_end():
    answers, seen, key = {}, [], FIRST_KEY
    while key is not None:
        assert key not in seen, f"같은 질문을 두 번 묻는다: {key}"
        seen.append(key)
        q = GRAPH[key]
        answers[key] = choices_for(key, answers)[0].value if q.kind == "choice" else _input_for(key)
        key = next_key(answers)
    assert "rule" in seen and "symbols" in seen


def _input_for(key):
    return {"symbols": "BTCUSDT", "period": "1y", "capital": "1000"}[key]


def test_weights_is_skipped_for_a_single_symbol():
    answers = {"goal": GRAPH["goal"].choices[0].value, "risk": GRAPH["risk"].choices[0].value,
               "watch": GRAPH["watch"].choices[0].value}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers["symbols"] = "BTCUSDT"
    assert next_key(answers) != "weights"


def test_weights_is_asked_for_two_symbols():
    answers = {"goal": GRAPH["goal"].choices[0].value, "risk": GRAPH["risk"].choices[0].value,
               "watch": GRAPH["watch"].choices[0].value}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers["symbols"] = "BTCUSDT, ETHUSDT"
    assert next_key(answers) == "weights"


def test_bundle_risk_is_skipped_for_a_single_symbol():
    answers = {k: GRAPH[k].choices[0].value for k in ("goal", "risk", "watch")}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers.update({"symbols": "BTCUSDT", "period": "1y", "capital": "1000",
                    "entry_filter": choices_for("entry_filter", answers)[0].value})
    assert next_key(answers) != "bundle_risk"


# --- rule 노드가 앞 답에서 좁혀진다 -----------------------------------
def test_rule_choices_narrow_from_earlier_answers():
    """좁혀지지 않으면 코치가 아니라 목록이다 — 서로 다른 답에서 서로 다른 후보가 나와야 한다."""
    base = {"risk": GRAPH["risk"].choices[0].value, "watch": GRAPH["watch"].choices[0].value}
    seen = set()
    for goal in GRAPH["goal"].choices:
        got = tuple(c.value for c in choices_for("rule", {**base, "goal": goal.value}))
        assert 3 <= len(got) <= 5, (goal.value, got)
        seen.add(got)
    assert len(seen) > 1, "goal 을 바꿰도 규칙 후보가 그대로다 — 좁히지 않고 있다"


def test_rule_choices_are_valid_rule_types():
    from app.engine.schema import RuleType
    valid = {r.value for r in RuleType}
    for goal in GRAPH["goal"].choices:
        for risk in GRAPH["risk"].choices:
            for watch in GRAPH["watch"].choices:
                answers = {"goal": goal.value, "risk": risk.value, "watch": watch.value}
                for c in choices_for("rule", answers):
                    assert c.value in valid, c.value


# --- 패치 -------------------------------------------------------------
def test_patch_for_choice_returns_that_choice_patch():
    q = GRAPH["risk"]
    c = q.choices[0]
    assert patch_for("risk", c.value, {}) == c.patch


def test_patch_for_unknown_answer_raises():
    with pytest.raises(ValueError):
        patch_for("risk", "존재하지않는값", {})


def test_patch_for_input_node_fills_its_field():
    assert patch_for("capital", "2500", {}) == {"initial_capital": 2500}
    assert patch_for("symbols", "BTCUSDT, ETHUSDT", {}) == {"symbol": "BTCUSDT, ETHUSDT"}
    assert patch_for("period", "3m", {}) == {"preset": "3m"}


def test_capital_rejects_a_non_number():
    with pytest.raises(ValueError):
        patch_for("capital", "스물", {})


# --- 마무리(턴 상한) --------------------------------------------------
def test_defaults_for_remaining_fills_every_unanswered_node():
    answers = {"goal": GRAPH["goal"].choices[0].value}
    patch = defaults_for_remaining(answers)
    assert patch, "남은 칸이 있는데 기본값을 안 냈다"
    assert "rule_type" in patch or "symbol" in patch


def test_defaults_for_remaining_is_empty_when_everything_is_answered():
    answers = {k: GRAPH[k].choices[0].value for k in GRAPH if GRAPH[k].kind == "choice"}
    answers.update({"symbols": "BTCUSDT", "period": "1y", "capital": "1000"})
    assert defaults_for_remaining(answers) == {}


# --- 내보내기 ---------------------------------------------------------
def test_to_json_is_serializable_and_complete():
    import json
    data = to_json()
    json.dumps(data, ensure_ascii=False)       # 터지지 않아야 한다
    assert data["first"] == FIRST_KEY
    assert set(data["nodes"]) == set(ALL_KEYS)
    node = data["nodes"]["risk"]
    assert node["kind"] == "choice"
    assert all({"value", "label", "patch", "why"} <= set(c) for c in node["choices"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_coach_graph.py -q`
Expected: `ModuleNotFoundError: No module named 'app.coach_graph'`.

- [ ] **Step 3: `coach_graph.py` 를 쓴다**

틀은 아래와 같다. **노드의 실제 선택지와 패치는 `backend/app/engine/schema.py` 의 규칙별 params 모델과 `frontend/src/lib/macro.js` 의 `defaultForm()`·`TYPE_DEFAULTS` 를 읽고 맞춰라** — 폼 키를 짐작하지 마라. `patch` 에 쓰는 키는 전부 `defaultForm()` 에 있어야 한다.

```python
"""코치 질문 그래프 — 순수 데이터.

다음에 무엇을 묻는지, 선택지가 무엇인지, 폼에 무엇을 넣는지를 **여기서만** 정한다.
AI 는 말투와 순서만 손댄다(coach_ai.py). 그래서 AI 가 죽어도 코치가 돌고, 코치가 낼 수
있는 폼이 전부 유효하다는 것을 전수로 증명할 수 있다.

DB · HTTP · AI · 시계를 모른다 — 그 셋이 들어오면 전수 시험이 비싸진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

PAGE_SIZE = 5          # 한 번에 보여 주는 선택지 수. 더 있으면 "다른 선택지 보기".


@dataclass(frozen=True)
class Choice:
    value: str
    label: str              # 28자 이하 — 패널이 고정 폭 340px 다
    patch: dict             # 평평한 폼 키만
    why: str = ""           # 한 줄 근거(AI 없이도 보여 준다)


@dataclass(frozen=True)
class Question:
    key: str
    ask: str                # AI 가 없을 때 쓰는 기본 문구
    kind: Literal["choice", "number", "symbols", "period"]
    choices: tuple[Choice, ...] = ()
    field: str = ""         # kind != "choice" 일 때 채우는 폼 키
    # 선택지가 앞 답에 따라 좁혀지는 노드만 쓴다(rule).
    narrow: Optional[Callable[[dict], tuple[Choice, ...]]] = None
```

그래프의 모양(선택지 내용은 네가 채운다):

- `goal` — 5개. 각 선택지의 `patch` 는 비워도 된다(`rule` 을 좁히는 데만 쓴다).
- `risk` — 4개. `patch` 에 `stop_loss_pct`·`use_stop_loss` 를 넣는다. "상관없다" 는 `use_stop_loss: false`.
- `watch` — 3개. `patch` 에 `candle_interval` 을 넣는다.
- `rule` — `narrow` 로 `goal`·`risk`·`watch` 에서 3~5개를 고른다. `patch` 는 `{"rule_type": X}` 와 그 규칙의 `TYPE_DEFAULTS` 를 함께.
- `symbols` — `kind="symbols"`, `field="symbol"`.
- `weights` — 3개(`균등` / `첫 종목에 더` / `직접 정하기`). `patch` 는 `leg_weights`. 종목이 둘 이상일 때만 묻는다.
- `period` — `kind="period"`, `field="preset"`. 선택지 대신 입력으로 받되 프런트가 프리셋 버튼으로 그린다.
- `capital` — `kind="number"`, `field="initial_capital"`.
- `entry_filter` — 4개. `patch` 는 `use_entry_filter`·`filter_kind` 와 그 종류의 기본값들.
  **규칙이 `FILTERABLE_TYPES`(E~K)가 아니면 이 노드를 건너뛴다.**
- `bundle_risk` — 3개. 종목이 둘 이상이고 **모든 규칙이 E~K** 일 때만 묻는다.

함수:

```python
def next_key(answers: dict) -> Optional[str]:
    """다음에 물을 질문. 더 물을 것이 없으면 None.

    건너뛰기 규칙이 여기 한 곳에 모인다 — 종목이 하나면 weights · bundle_risk 를 묻지 않고,
    규칙이 E~K 가 아니면 entry_filter 를 묻지 않는다.
    """


def choices_for(key: str, answers: dict) -> tuple[Choice, ...]:
    """그 질문의 선택지 **전부**(페이지 나누기는 호출부가 한다)."""


def patch_for(key: str, answer, answers: dict) -> dict:
    """답 하나가 폼에 넣는 것. 모르는 답이면 ValueError — 위변조 차단."""


def defaults_for_remaining(answers: dict) -> dict:
    """턴 상한에 닿았을 때 남은 칸을 메꿀 패치. 각 노드의 첫 선택지(= 가장 보수적인 것)를 쓴다."""


def to_json() -> dict:
    """프런트 전수 시험이 읽는 모양. {"first": …, "nodes": {key: {kind, ask, field, choices:[…]}}}"""
```

`narrow` 가 함수라 `to_json` 에 담을 수 없다 — `rule` 노드는 **좁혀질 수 있는 모든 조합**을
미리 펼쳐 담는다:

```python
        "rule": {
            "kind": "choice", "ask": ..., "field": "",
            # (goal, risk, watch) -> 그 조합의 선택지. 프런트 전수 시험이 이것을 걷는다.
            "by": {f"{g}|{r}|{w}": [...] for ...},
        },
```

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_coach_graph.py -q`
Expected: PASS (19개).

- [ ] **Step 5: 돌연변이**

1. `rule` 의 `narrow` 를 "항상 같은 세 개" 로 바꾼다 → `test_rule_choices_narrow_from_earlier_answers` 가 실패해야 한다.
2. `patch_for` 의 모르는 답 검사를 지운다(그냥 `{}` 반환) → `test_patch_for_unknown_answer_raises` 가 실패해야 한다.
3. `next_key` 의 "종목 하나면 weights 건너뛰기" 를 지운다 → `test_weights_is_skipped_for_a_single_symbol` 이 실패해야 한다.

세 번 다 되돌린 뒤 수트 통과를 확인한다. 보고서에 세 결과를 적는다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/coach_graph.py backend/tests/test_coach_graph.py
git commit -m "feat(coach): 질문 그래프 - 다음 질문 · 선택지 · 폼 패치를 한 곳에서 정한다"
```

---

### Task 2: 코치 세션과 한도

**Files:**
- Create: `backend/app/coach.py`
- Test: `backend/tests/test_coach_session.py` (create)

**Interfaces:**
- Consumes: Task 1 의 그래프; `ask.py` 의 `remaining_today`, `free_remaining_today`, `_unused_credit`, `daily_limit`, `consented`, `DISCLAIMER_VERSION`, `_now`, `SESSION_TTL_MS`, `AskError`; `db.AskMacroSession`, `db.today_kst`
- Produces: `COACH_KIND = "coach"`, `COACH_MAX_TURNS = 14`, `start(db, user) -> dict`, `answer(db, user, session_id, key, value) -> dict`, `more(db, user, session_id, key) -> dict`, `back(db, user, session_id) -> dict`

- [ ] **Step 1: 시험을 쓴다**

`backend/tests/test_coach_session.py`:

```python
"""코치 세션 — 한도는 세션마다 1회, 턴 상한에서는 마무리, 실패하면 횟수를 돌려준다."""
import json

import pytest

from app import coach
from app.ask import AskError, daily_limit, remaining_today
from app.coach_graph import FIRST_KEY, GRAPH, choices_for
from app.db import AskMacroSession


# 이 시험들은 기존 ask 시험이 쓰는 db/user 픽스처를 그대로 쓴다.
# `tests/test_ask_*.py` 에서 픽스처 이름과 만드는 방식을 읽어 맞춰라 — 새로 만들지 마라.


def _first_choice(key, answers):
    return choices_for(key, answers)[0].value


# --- Review Focus 1: 한도는 세션마다 1회 -------------------------------
def test_quota_is_charged_once_per_session_not_per_turn(db, user):
    before = remaining_today(db, user)
    started = coach.start(db, user)
    after_start = remaining_today(db, user)
    assert after_start == before - 1

    answers = {}
    key = started["question"]["key"]
    for _ in range(4):
        value = _first_choice(key, answers)
        out = coach.answer(db, user, started["session_id"], key, value)
        answers[key] = value
        assert remaining_today(db, user) == after_start, "턴마다 한도를 깎고 있다"
        if out["done"]:
            break
        key = out["question"]["key"]


def test_more_and_back_do_not_charge(db, user):
    started = coach.start(db, user)
    left = remaining_today(db, user)
    coach.more(db, user, started["session_id"], started["question"]["key"])
    assert remaining_today(db, user) == left
    key = started["question"]["key"]
    coach.answer(db, user, started["session_id"], key, _first_choice(key, {}))
    coach.back(db, user, started["session_id"])
    assert remaining_today(db, user) == left


def test_start_refused_with_no_quota_left(db, user):
    for _ in range(daily_limit()):
        coach.start(db, user)
    with pytest.raises(AskError) as exc:
        coach.start(db, user)
    assert exc.value.status == 429


# --- Review Focus 2: 실패하면 횟수를 돌려준다 --------------------------
def test_quota_is_refunded_when_the_first_question_cannot_be_built(db, user, monkeypatch):
    """한도를 깎고 세션 생성이 실패하면 돌려준다 — 1차 · 2차에서 경로를 빠뜨려 생긴 결함과 같은 자리."""
    before = remaining_today(db, user)
    monkeypatch.setattr(coach, "_first_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        coach.start(db, user)
    assert remaining_today(db, user) == before, "한도를 깎고 돌려주지 않았다"
    rows = db.exec(__import__("sqlmodel").select(AskMacroSession)).all()
    assert all(json.loads(r.request_json).get("kind") != coach.COACH_KIND for r in rows), \
        "실패한 세션 행이 남았다"


# --- Review Focus 4: 턴 상한에서 마무리한다 ----------------------------
def test_turn_cap_wraps_up_instead_of_cutting_off(db, user, monkeypatch):
    monkeypatch.setattr(coach, "COACH_MAX_TURNS", 2)
    started = coach.start(db, user)
    key, answers, out = started["question"]["key"], {}, None
    for _ in range(3):
        value = _first_choice(key, answers)
        out = coach.answer(db, user, started["session_id"], key, value)
        answers[key] = value
        if out["done"]:
            break
        key = out["question"]["key"]
    assert out["done"] is True
    assert out["wrapped_up"] is True
    assert out["question"] is None
    # 마무리 패치가 남은 칸을 메꾼다 — 반쪽 폼을 던지고 멈추지 않는다.
    assert out["form_patch"], "마무리인데 패치가 비었다"
    assert "rule_type" in out["form_patch"] or "symbol" in out["form_patch"]


# --- 세션 분리 · 위변조 ------------------------------------------------
def test_coach_session_is_marked_as_coach(db, user):
    started = coach.start(db, user)
    row = db.get(AskMacroSession, started["session_id"])
    assert json.loads(row.request_json)["kind"] == coach.COACH_KIND


def test_another_users_session_is_refused(db, user, other_user):
    started = coach.start(db, user)
    with pytest.raises(AskError) as exc:
        coach.answer(db, other_user, started["session_id"], FIRST_KEY, "x")
    assert exc.value.status in (403, 404)


def test_an_unknown_answer_is_refused(db, user):
    started = coach.start(db, user)
    with pytest.raises(AskError):
        coach.answer(db, user, started["session_id"], started["question"]["key"], "존재하지않는값")


def test_answering_a_question_that_is_not_current_is_refused(db, user):
    """클라이언트가 순서를 건너뛰면 거절한다 — 그래프의 건너뛰기 규칙이 뚫린다."""
    started = coach.start(db, user)
    with pytest.raises(AskError):
        coach.answer(db, user, started["session_id"], "capital", "1000")


# --- 페이지 나누기 · 되돌아가기 ----------------------------------------
def test_more_returns_the_next_page_of_choices(db, user):
    """선택지가 PAGE_SIZE 보다 많은 노드에서만 뜻이 있다."""
    started = coach.start(db, user)
    key = started["question"]["key"]
    first = [c["value"] for c in started["question"]["choices"]]
    if not started["question"]["has_more"]:
        pytest.skip("첫 질문의 선택지가 한 페이지에 다 들어간다")
    nxt = coach.more(db, user, started["session_id"], key)
    assert [c["value"] for c in nxt["question"]["choices"]] != first


def test_back_drops_the_last_turn(db, user):
    started = coach.start(db, user)
    key = started["question"]["key"]
    after = coach.answer(db, user, started["session_id"], key, _first_choice(key, {}))
    assert after["question"]["key"] != key
    back = coach.back(db, user, started["session_id"])
    assert back["question"]["key"] == key
    assert back["turn"] == started["turn"]


def test_back_at_the_start_is_a_no_op(db, user):
    started = coach.start(db, user)
    back = coach.back(db, user, started["session_id"])
    assert back["question"]["key"] == started["question"]["key"]


# --- 응답 모양 --------------------------------------------------------
def test_response_shape(db, user):
    out = coach.start(db, user)
    assert set(out) >= {"session_id", "turn", "max_turns", "question", "form_patch",
                        "done", "wrapped_up", "remaining_today"}
    q = out["question"]
    assert set(q) >= {"key", "ask", "kind", "choices", "has_more", "field"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_coach_session.py -q`
Expected: `ModuleNotFoundError: No module named 'app.coach'`.

`db`·`user`·`other_user` 픽스처는 기존 `tests/test_ask_*.py` 가 쓰는 것을 그대로 쓴다. 없으면 그 파일들이 세션·사용자를 만드는 방식을 그대로 베껴 `conftest.py` 가 아니라 **이 시험 파일 안에** 픽스처로 둔다(다른 시험에 영향을 주지 않게).

- [ ] **Step 3: `coach.py` 를 쓴다**

```python
"""코치 세션 — 한도 · 턴 쌓기 · 되돌아가기.

한도와 동의는 ``ask.py`` 의 기계를 그대로 쓴다. 세션 행도 ``AskMacroSession`` 이고 구분은
``request_json`` 안의 ``{"kind": "coach"}`` 다 — 새 열을 더하면 마이그레이션이 생기고,
하루 한도 인덱스 ``(user_id, day_kst)`` 를 그대로 쓰는 것이 더 값싸다.

**한도는 세션을 열 때 한 번만 깎는다.** 턴마다 깎으면 좁혀 가는 대화에서 사용자가
되돌아가기를 두려워하고, 열 턴 대화 한 번이 하루 한도를 다 쓴다.
"""
```

구조:

- `COACH_KIND = "coach"`, `COACH_MAX_TURNS = 14`.
- `start(db, user)`:
  1. `remaining_today(db, user) <= 0` → `AskError(429, f"오늘은 {daily_limit()}번 다 썼어요. 내일 다시 와 주세요.")`.
  2. `ask.py` 의 `_run_candidates` 와 **같은 순서**로: 추가권(`_unused_credit`)을 고르고 행을 먼저 커밋한다(한도 검사와 저장 사이의 창을 닫는다).
  3. `try: 첫 질문을 만든다 / except: 행을 지우고 추가권을 '안 씀' 으로 되돌리고 raise` — `ask.py` 의 환불 패턴 그대로.
  4. 첫 질문은 `_first_question(...)` 으로 뽑는다(시험이 이 이름을 monkeypatch 한다).
- 턴 기록은 `candidates_json` 에 `{"turns": [{"key", "answer", "patch", "page"}], "ai": bool}` 로 쌓는다.
- `answer(db, user, session_id, key, value)`:
  1. 세션을 읽고 소유자·`kind`·`expires_ms` 를 검사한다(아니면 `AskError`).
  2. `key` 가 **지금 물어야 할 질문**인지 검사한다(`next_key(answers)` 와 같은지). 아니면 `AskError(400, …)`.
  3. `patch_for(key, value, answers)` — 모르는 답이면 `ValueError` 가 오므로 `AskError(400, "고를 수 없는 답이에요")` 로 바꾼다.
  4. 턴을 쌓고, 턴 수가 `COACH_MAX_TURNS` 에 닿았거나 `next_key` 가 `None` 이면 **마무리**:
     `done=True`, `wrapped_up=(턴 상한 때문인지)`, `form_patch = 이번 패치 + defaults_for_remaining(answers)`.
  5. 아니면 다음 질문을 담아 돌려준다.
- `more(db, user, session_id, key)`: 그 질문의 `page` 를 1 늘려 다음 묶음을 낸다. 한도를 깎지 않는다. 마지막 페이지에서 더 부르면 첫 페이지로 돌아간다(`has_more` 로 알려 준다).
- `back(db, user, session_id)`: 턴 배열의 마지막을 버리고 그 질문을 다시 낸다. 비어 있으면 첫 질문(아무 일도 안 한다).
- 질문을 응답 모양으로 만드는 함수 하나(`_question_view(key, answers, page, ai)`): `choices_for` 에서 `page * PAGE_SIZE` 만큼 잘라 담고 `has_more` 를 센다.

- [ ] **Step 4: 시험을 돌린다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_coach_session.py -q`
Expected: PASS.

- [ ] **Step 5: 돌연변이 — Review Focus 1·2·4 를 하나씩**

1. `answer` 안에서 한도를 한 번 더 깎게 만든다 → `test_quota_is_charged_once_per_session_not_per_turn` 이 실패해야 한다.
2. `start` 의 `except` 환불 블록을 지운다 → `test_quota_is_refunded_when_the_first_question_cannot_be_built` 가 실패해야 한다.
3. 턴 상한에서 `form_patch` 에 `defaults_for_remaining` 을 더하지 않게 한다 → `test_turn_cap_wraps_up_instead_of_cutting_off` 가 실패해야 한다.

세 번 다 되돌린 뒤 수트 통과를 확인한다.

- [ ] **Step 6: 기존 ask 시험이 안 깨졌는지 본다**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q -k "ask" --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py`
Expected: PASS. 코치 세션이 `used_today` 에 섞여 세는지 확인하라 — **섞여 세는 것이 맞다**(같은 하루 한도를 쓴다). 기존 시험이 깨지면 그 이유를 보고서에 적어라.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/coach.py backend/tests/test_coach_session.py
git commit -m "feat(coach): 세션과 한도 - 대화 한 번이 1회, 턴 상한에서는 마무리한다"
```

---

### Task 3: 그래프를 프런트로 내보내기

**Files:**
- Create: `backend/scripts/dump_coach_graph.py`
- Create: `frontend/src/lib/coachGraph.generated.json` (스크립트가 만든다)
- Test: `backend/tests/test_coach_graph_export.py` (create)

**Interfaces:**
- Consumes: Task 1 의 `to_json()`
- Produces: 커밋된 JSON 과 그것이 최신인지 보는 시험

- [ ] **Step 1: 시험을 쓴다**

```python
"""내보낸 그래프 JSON 이 코드와 맞는지 — 어긋나면 프런트 전수 시험이 거짓 안심을 준다."""
import json
import pathlib

from app.coach_graph import to_json

EXPORT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "coachGraph.generated.json"


def test_export_exists():
    assert EXPORT.exists(), f"없다: {EXPORT} — scripts/dump_coach_graph.py 를 돌려라"


def test_export_matches_the_code():
    """그래프를 고치고 내보내기를 안 하면 여기서 걸린다(test_runner_release 와 같은 패턴)."""
    on_disk = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert on_disk == to_json(), \
        "coachGraph.generated.json 이 낡았다 — backend/scripts/dump_coach_graph.py 를 다시 돌려라"
```

- [ ] **Step 2: 실패를 확인한다** — 파일이 없어 첫 시험이 실패한다.

- [ ] **Step 3: 스크립트를 쓴다**

```python
"""코치 질문 그래프를 프런트가 읽을 JSON 으로 내보낸다.

프런트 전수 경로 시험이 이 파일을 읽어 '코치가 낼 수 있는 모든 폼이 유효한가' 를 증명한다.
파이썬에 폼 빌더를 한 벌 더 베끼는 대신 이 길을 택했다 — 두 벌은 어긋나는 날이 온다.

쓰기: cd backend && .venv/Scripts/python.exe scripts/dump_coach_graph.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.coach_graph import to_json   # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "coachGraph.generated.json"


def main() -> None:
    OUT.write_text(json.dumps(to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 돌리고 시험을 통과시킨다**

```bash
cd backend && .venv/Scripts/python.exe scripts/dump_coach_graph.py
.venv/Scripts/python.exe -m pytest tests/test_coach_graph_export.py -q
```

- [ ] **Step 5: 돌연변이** — `coach_graph.py` 의 라벨 하나를 바꾸고 내보내기를 **안 한 채** 시험을 돌린다 → `test_export_matches_the_code` 가 실패해야 한다. 되돌린다.

- [ ] **Step 6: 커밋**

```bash
git add backend/scripts/dump_coach_graph.py frontend/src/lib/coachGraph.generated.json backend/tests/test_coach_graph_export.py
git commit -m "feat(coach): 질문 그래프를 프런트로 내보낸다"
```

---

### Task 4: AI 말투 턴

**Files:**
- Create: `backend/app/coach_ai.py`
- Modify: `backend/app/coach.py` (질문을 만들 때 말투를 얹는다)
- Test: `backend/tests/test_coach_ai.py` (create)

**Interfaces:**
- Consumes: `ai_runtime` 의 `ai_available`, `get_ai_client`, `ai_cache_key`, `get_ai_runtime`; Task 1 의 `Choice`
- Produces: `PROMPT_VERSION`, `ASK_MAX_LEN = 120`, `voice(key, ask, choices, answers, ask_ai=None) -> tuple[str, tuple[Choice, ...], bool]`

**AI 가 선택지의 값·집합·패치를 바꿀 수 없다는 것이 이 작업의 전부다.**

- [ ] **Step 1: 시험을 쓴다**

```python
"""AI 말투 — 말투와 순서만 손댈 수 있고, 선택지의 값 · 집합 · 패치는 못 바꾼다."""
import json

from app.coach_ai import ASK_MAX_LEN, voice
from app.coach_graph import Choice

CHOICES = (
    Choice(value="I", label="변동성 돌파", patch={"rule_type": "I"}, why="하루 한 번"),
    Choice(value="C", label="적립식", patch={"rule_type": "C"}, why="꾸준히"),
    Choice(value="D", label="그리드", patch={"rule_type": "D"}, why="자동 반복"),
)
VALUES = tuple(c.value for c in CHOICES)


def _ai(payload):
    return lambda _prompt: json.dumps(payload, ensure_ascii=False)


def test_without_ai_the_defaults_are_used():
    ask, choices, used = voice("rule", "기본 문구", CHOICES, {}, ask_ai=None)
    assert ask == "기본 문구"
    assert tuple(c.value for c in choices) == VALUES
    assert used is False


def test_ai_can_rewrite_the_prompt_and_the_labels():
    ask, choices, used = voice("rule", "기본 문구", CHOICES, {}, ask_ai=_ai(
        {"ask": "어느 쪽이 좋아요?", "labels": {"I": "돌파를 노려요"}, "order": ["C", "I", "D"]}))
    assert ask == "어느 쪽이 좋아요?"
    assert tuple(c.value for c in choices) == ("C", "I", "D")
    assert next(c.label for c in choices if c.value == "I") == "돌파를 노려요"
    assert used is True


def test_patches_survive_a_label_rewrite():
    """라벨만 바뀌고 폼에 들어가는 것은 그대로여야 한다."""
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {"I": "다른 이름"}, "order": list(VALUES)}))
    assert next(c.patch for c in choices if c.value == "I") == {"rule_type": "I"}


# --- Review Focus 3: 집합을 바꾸려는 시도를 버린다 ----------------------
def test_an_order_that_drops_a_choice_is_ignored():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {}, "order": ["I", "C"]}))
    assert tuple(c.value for c in choices) == VALUES, "선택지를 빠뜨린 순서를 받아들였다"


def test_an_order_that_adds_a_choice_is_ignored():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {}, "order": ["I", "C", "D", "Z"]}))
    assert tuple(c.value for c in choices) == VALUES


def test_an_order_that_repeats_a_choice_is_ignored():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {}, "order": ["I", "I", "C"]}))
    assert tuple(c.value for c in choices) == VALUES


def test_labels_for_unknown_values_are_dropped():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {"Z": "없는 것"}, "order": list(VALUES)}))
    assert all(c.value != "Z" for c in choices)
    assert len(choices) == len(CHOICES)


def test_an_empty_label_is_dropped():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {"I": "   "}, "order": list(VALUES)}))
    assert next(c.label for c in choices if c.value == "I") == "변동성 돌파"


# --- 길이 · 깨진 응답 ---------------------------------------------------
def test_a_long_prompt_is_cut():
    long = "가" * 400
    ask, _choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai({"ask": long, "labels": {}, "order": list(VALUES)}))
    assert len(ask) <= ASK_MAX_LEN


def test_an_empty_prompt_falls_back_to_the_default():
    ask, _choices, _ = voice("rule", "기본 문구", CHOICES, {}, ask_ai=_ai({"ask": "  ", "labels": {}, "order": list(VALUES)}))
    assert ask == "기본 문구"


def test_a_long_label_is_cut():
    _ask, choices, _ = voice("rule", "기본", CHOICES, {}, ask_ai=_ai(
        {"ask": "x", "labels": {"I": "가" * 90}, "order": list(VALUES)}))
    assert len(next(c.label for c in choices if c.value == "I")) <= 28


def test_broken_json_falls_back_without_raising():
    ask, choices, used = voice("rule", "기본 문구", CHOICES, {}, ask_ai=lambda _p: "이건 JSON 이 아니다")
    assert ask == "기본 문구"
    assert tuple(c.value for c in choices) == VALUES
    assert used is False


def test_an_ai_exception_falls_back_without_raising():
    def boom(_prompt):
        raise RuntimeError("provider down")

    ask, choices, used = voice("rule", "기본 문구", CHOICES, {}, ask_ai=boom)
    assert ask == "기본 문구"
    assert tuple(c.value for c in choices) == VALUES
    assert used is False


def test_json_in_a_code_fence_is_accepted():
    """모델이 ```json 으로 감싸는 일이 흔하다 — ask.py 의 _strip_fences 와 같은 처리."""
    payload = json.dumps({"ask": "좋아요?", "labels": {}, "order": list(VALUES)}, ensure_ascii=False)
    ask, _c, used = voice("rule", "기본", CHOICES, {}, ask_ai=lambda _p: f"```json\n{payload}\n```")
    assert ask == "좋아요?" and used is True
```

- [ ] **Step 2: 실패를 확인한다**

- [ ] **Step 3: `coach_ai.py` 를 쓴다**

```python
"""코치의 말투 — AI 는 한 줄 문구와 라벨 · 순서만 손댄다.

**AI 가 선택지의 값 · 집합 · 패치를 바꿀 수 없다.** 그래야 코치가 낸 폼이 언제나 유효하고
(설계 원칙 3), AI 가 죽어도 코치가 돈다(원칙 1). 서버가 받은 응답을 세 가지로 걸러낸다:

- ``ask`` 는 길이 상한으로 자르고, 비었으면 그래프의 기본 문구를 쓴다.
- ``labels`` 는 그래프에 있는 value 에 대한 것만 받는다. 빈 문구도 버린다.
- ``order`` 는 그래프의 value **집합과 정확히 같을 때만** 쓴다 — 빠뜨리기 · 끼워넣기 · 중복을 막는다.

캐시 · 재시도는 ``ask.py`` 와 같은 ``get_ai_runtime().call`` 을 쓴다.
"""
```

- `PROMPT_VERSION = "coach-voice-1"`, `ASK_MAX_LEN = 120`, `LABEL_MAX_LEN = 28`.
- `_strip_fences(text)` — `ask.py` 의 것과 같은 일을 한다. **`ask.py` 에서 import 해 쓸 수 있으면 그렇게 하라**(`from .ask import _strip_fences`); 순환이 생기면 이 파일에 둔다.
- `voice(key, ask, choices, answers, ask_ai=None)`:
  - `ask_ai` 가 `None` 이면 `(ask, choices, False)`.
  - 프롬프트를 만들어 부르고, `try/except Exception` 으로 감싸 **어떤 실패도 폴백**이 되게 한다.
  - 위 세 가지 필터를 적용해 `(ask, 새 choices, True)`.
- `default_ask_ai()` — `ai_available()` 이 거짓이면 `None`, 아니면 `ask.py` 의 `_candidate_ai` 와 같은 모양으로 `ai_cache_key("coach-voice", PROMPT_VERSION, model, payload)` + `get_ai_runtime().call(key, load, retries=0)`.

`coach.py` 의 `_question_view` 가 `voice(...)` 를 거쳐 `ask`·`choices` 를 만들고, `ai` 가 쓰였는지를 세션의 `ai_used` 에 기록한다.

- [ ] **Step 4: 시험을 돌린다** — Expected: PASS (14개).

- [ ] **Step 5: 돌연변이**

1. `order` 의 집합 검사를 "길이만 같으면 받기" 로 느슨하게 한다 → `test_an_order_that_repeats_a_choice_is_ignored` 가 실패해야 한다.
2. `labels` 의 "모르는 키 버리기" 를 지운다 → `test_labels_for_unknown_values_are_dropped` 가 실패해야 한다.
3. `voice` 의 `try/except` 를 지운다 → `test_an_ai_exception_falls_back_without_raising` 가 실패해야 한다.

되돌린 뒤 수트 통과를 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/coach_ai.py backend/app/coach.py backend/tests/test_coach_ai.py
git commit -m "feat(coach): AI 말투 - 문구와 순서만 손대고 선택지는 못 바꾼다"
```

---

### Task 5: 끝점 네 개

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_coach_api.py` (create)

**Interfaces:**
- Consumes: Task 2 의 `coach.start/answer/more/back`
- Produces: `POST /api/coach/start`, `/api/coach/answer`, `/api/coach/more`, `/api/coach/back`

- [ ] **Step 1: 기존 ask 끝점을 읽는다**

`grep -n "api/ask" backend/app/main.py` 로 기존 ask 끝점들을 찾아 **그 모양을 그대로 베낀다** — 인증(`Depends`), 세션(`Depends(get_session)`), 비율 제한(`_enforce_beacon_rate_limit`), `AskError` → `HTTPException` 변환이 이미 거기 있다. 새 방식을 만들지 마라.

- [ ] **Step 2: 시험을 쓴다**

`backend/tests/test_coach_api.py`. 기존 `tests/test_ask_api*.py` 의 `TestClient` 만드는 방식·로그인 방식을 그대로 쓴다.

```python
"""코치 끝점 — 인증 · 동의 · 한도 · 위변조."""


def test_start_requires_login(client):
    assert client.post("/api/coach/start").status_code in (401, 403)


def test_start_requires_consent(client, logged_in_without_consent):
    r = client.post("/api/coach/start")
    assert r.status_code == 403
    assert "동의" in r.json()["detail"]


def test_start_returns_the_first_question(client, logged_in):
    r = client.post("/api/coach/start")
    assert r.status_code == 200
    body = r.json()
    assert body["question"]["key"] == "goal"
    assert 3 <= len(body["question"]["choices"]) <= 5
    assert body["done"] is False
    assert body["turn"] == 1


def test_answer_advances_and_returns_a_patch(client, logged_in):
    sid = client.post("/api/coach/start").json()
    value = sid["question"]["choices"][0]["value"]
    r = client.post("/api/coach/answer",
                    json={"session_id": sid["session_id"], "key": "goal", "value": value})
    assert r.status_code == 200
    body = r.json()
    assert body["question"]["key"] != "goal"
    assert isinstance(body["form_patch"], dict)


def test_answer_with_an_unknown_value_is_400(client, logged_in):
    sid = client.post("/api/coach/start").json()
    r = client.post("/api/coach/answer",
                    json={"session_id": sid["session_id"], "key": "goal", "value": "없는값"})
    assert r.status_code == 400


def test_answer_on_someone_elses_session_is_refused(client, logged_in, other_logged_in):
    sid = client.post("/api/coach/start").json()["session_id"]
    r = other_logged_in.post("/api/coach/answer", json={"session_id": sid, "key": "goal", "value": "x"})
    assert r.status_code in (400, 403, 404)


def test_more_does_not_charge_quota(client, logged_in):
    sid = client.post("/api/coach/start").json()
    before = sid["remaining_today"]
    r = client.post("/api/coach/more", json={"session_id": sid["session_id"], "key": "goal"})
    assert r.status_code == 200
    assert r.json()["remaining_today"] == before


def test_back_returns_the_previous_question(client, logged_in):
    sid = client.post("/api/coach/start").json()
    value = sid["question"]["choices"][0]["value"]
    client.post("/api/coach/answer", json={"session_id": sid["session_id"], "key": "goal", "value": value})
    r = client.post("/api/coach/back", json={"session_id": sid["session_id"]})
    assert r.json()["question"]["key"] == "goal"


def test_quota_exhausted_is_429(client, logged_in, exhaust_quota):
    assert client.post("/api/coach/start").status_code == 429


def test_a_missing_session_is_not_a_500(client, logged_in):
    r = client.post("/api/coach/answer", json={"session_id": 999999, "key": "goal", "value": "x"})
    assert r.status_code in (400, 403, 404)
```

- [ ] **Step 3: 끝점을 더한다**

요청 모델 세 개(`CoachAnswerBody{session_id:int, key:str, value:str}`, `CoachMoreBody{session_id:int, key:str}`, `CoachBackBody{session_id:int}`)와 끝점 네 개. `AskError` 를 `HTTPException(status, detail)` 로 바꾸는 것은 기존 ask 끝점의 처리를 그대로 쓴다. 동의 검사도 기존 ask 끝점과 같은 자리에 둔다.

비율 제한: 기존 `_enforce_beacon_rate_limit` 에 코치용 리미터 하나(`_coach_limiter`)를 더하고 **`/start` 에만** 건다 — `/answer`·`/more`·`/back` 은 대화 중 자연히 자주 불린다.

- [ ] **Step 4: 시험을 돌린다** — Expected: PASS (10개).

- [ ] **Step 5: 돌연변이**

1. `/answer` 의 소유자 검사를 지운다 → `test_answer_on_someone_elses_session_is_refused` 가 실패해야 한다.
2. 동의 검사를 지운다 → `test_start_requires_consent` 가 실패해야 한다.

되돌린 뒤 수트 통과를 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/main.py backend/tests/test_coach_api.py
git commit -m "feat(api): 코치 끝점 네 개"
```

---

### Task 6: 코치 패널 컴포넌트

**Files:**
- Create: `frontend/src/components/CoachPanel.jsx`
- Create: `frontend/src/components/CoachPanel.css`
- Modify: `frontend/src/api.js` (끝점 네 개)
- Test: `frontend/tests/coachPanel.test.js` (create)

**Interfaces:**
- Consumes: `api.coach.start/answer/more/back`
- Produces: `<CoachPanel onPatch={(patch) => void} onDone={() => void} />`

- [ ] **Step 1: `api.js` 에 끝점을 더한다**

기존 `api.js` 의 `ask` 묶음을 찾아(`grep -n "ask:" frontend/src/api.js`) **그 모양을 그대로** 베껴 `coach` 묶음을 만든다.

- [ ] **Step 2: 시험을 쓴다**

`frontend/tests/coachPanel.test.js`. 서버 없이 props 로 상태를 넣어 렌더하는 방식이면 좋다 — 그러려면 `CoachPanel` 이 **상태를 prop 으로도 받을 수 있게** 설계한다(`initialState` 같은 것). 기존 시험들이 `renderComponent` 로 SSR 한 번만 그리므로, 클릭 흐름은 시험하지 못한다. 그 한계를 받아들이고 **그려지는 것**을 본다:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";

const QUESTION = {
  key: "goal", ask: "무엇을 하고 싶어요?", kind: "choice", has_more: true, field: "",
  choices: [
    { value: "steady", label: "꾸준히 조금씩", why: "매수 시점을 안 고민해요", patch: {} },
    { value: "trend", label: "추세를 타고 싶어요", why: "", patch: {} },
    { value: "dip", label: "흔들릴 때 사고 싶어요", why: "", patch: {} },
  ],
};

function render(state) {
  return renderComponent("src/components/CoachPanel.jsx",
    { onPatch: () => {}, onDone: () => {}, initialState: state });
}

test("시작 전에는 시작하기만 보인다", async () => {
  const text = textOf(await render(null));
  assert.match(text, /시작하기/);
  assert.doesNotMatch(text, /다른 선택지 보기/);
});

test("질문과 선택지를 그린다", async () => {
  const text = textOf(await render({ question: QUESTION, turn: 1, max_turns: 14, remaining_today: 4, turns: [] }));
  assert.match(text, /무엇을 하고 싶어요\?/);
  assert.match(text, /꾸준히 조금씩/);
  assert.match(text, /추세를 타고 싶어요/);
});

test("근거가 있으면 보여 준다", async () => {
  const text = textOf(await render({ question: QUESTION, turn: 1, max_turns: 14, remaining_today: 4, turns: [] }));
  assert.match(text, /매수 시점을 안 고민해요/);
});

test("has_more 면 다른 선택지 보기가 있다", async () => {
  const text = textOf(await render({ question: QUESTION, turn: 1, max_turns: 14, remaining_today: 4, turns: [] }));
  assert.match(text, /다른 선택지 보기/);
});

test("has_more 가 아니면 다른 선택지 보기가 없다", async () => {
  const q = { ...QUESTION, has_more: false };
  const text = textOf(await render({ question: q, turn: 1, max_turns: 14, remaining_today: 4, turns: [] }));
  assert.doesNotMatch(text, /다른 선택지 보기/);
});

test("남은 횟수와 턴을 보여 준다", async () => {
  const text = textOf(await render({ question: QUESTION, turn: 4, max_turns: 14, remaining_today: 2, turns: [] }));
  assert.match(text, /2회/);
  assert.match(text, /4\s*\/\s*14/);
});

test("지난 턴을 기록으로 보여 주고 되돌아갈 수 있다", async () => {
  const text = textOf(await render({
    question: QUESTION, turn: 2, max_turns: 14, remaining_today: 4,
    turns: [{ key: "goal", label: "꾸준히 조금씩" }],
  }));
  assert.match(text, /꾸준히 조금씩/);
  assert.match(text, /여기로|되돌리기/);
});

test("숫자 질문은 선택지 대신 입력칸을 그린다", async () => {
  const q = { key: "capital", ask: "얼마로 시작할까요?", kind: "number", choices: [], has_more: false, field: "initial_capital" };
  const html = await render({ question: q, turn: 8, max_turns: 14, remaining_today: 4, turns: [] });
  assert.match(html, /<input/);
  assert.match(textOf(html), /얼마로 시작할까요\?/);
});

test("마무리되면 끝났다고 말한다", async () => {
  const text = textOf(await render({
    question: null, done: true, wrapped_up: true, turn: 14, max_turns: 14, remaining_today: 4, turns: [],
  }));
  assert.match(text, /여기까지 정했어요/);
});

test("끝나면 선택지가 없다", async () => {
  const text = textOf(await render({ question: null, done: true, turn: 10, max_turns: 14, remaining_today: 4, turns: [] }));
  assert.doesNotMatch(text, /다른 선택지 보기/);
});

test("한도가 0이면 시작할 수 없다고 말한다", async () => {
  const text = textOf(await render({ question: null, remaining_today: 0, turn: 0, max_turns: 14, turns: [] }));
  assert.match(text, /오늘은|내일/);
});
```

- [ ] **Step 3: `CoachPanel.jsx` 를 쓴다**

- 상태: `{ sessionId, question, turn, max_turns, remaining_today, turns, done, wrapped_up, busy, error }`.
  시험이 넣을 수 있게 `initialState` prop 을 받아 그것으로 시작한다.
- 선택지 버튼을 누르면 `api.coach.answer` → 응답의 `form_patch` 를 `onPatch` 로 올리고 상태를 갱신.
- `"다른 선택지 보기"` → `api.coach.more`. `"여기로"`/`"되돌리기"` → `api.coach.back`.
- `kind` 가 `number`·`period`·`symbols` 면 입력을 그린다. `symbols` 는 **기존 `SymbolPicker` 를 끼운다**(종목 검증·최대 5개가 공짜로 따라온다).
- **프런트에 라벨 사전을 두지 않는다** — 라벨·근거·질문 문구는 서버가 보낸 것만 쓴다.
- `done` 이면 `onDone()` 을 한 번 부르고 마무리 문구를 보여 준다. `wrapped_up` 이면
  `"여기까지 정했어요 · 나머지는 기본값으로 뒀으니 판에서 고쳐요"`.
- `remaining_today <= 0` 이고 세션이 없으면 `"오늘은 다 썼어요 · 내일 다시 와 주세요"`.

- [ ] **Step 4: CSS**

```css
/* 코치 패널 — 넓은 화면에서 오른쪽 고정 폭. 선택지 라벨은 28자 상한이라 두 줄을 넘지 않는다. */
.coach { display: grid; gap: 10px; }
.coach-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.coach-ask { margin: 0; font-size: 14px; line-height: 1.5; }
.coach-choices { display: grid; gap: 6px; }
.coach-choice { display: grid; gap: 2px; text-align: left; padding: 9px 11px; border-radius: 9px;
                border: 1px solid rgb(var(--c-slate-200)); background: rgb(var(--c-surface)); }
.coach-choice b { font-size: 13px; font-weight: 600; }
.coach-choice small { color: rgb(var(--c-slate-500)); font-size: 11px; line-height: 1.4;
                      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.coach-more { font-size: 12px; text-decoration: underline; opacity: .75; justify-self: start; }
.coach-log { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.coach-log li { display: flex; justify-content: space-between; gap: 6px; font-size: 12px;
                color: rgb(var(--c-slate-500)); }
.coach-foot { display: flex; gap: 10px; }
```

- [ ] **Step 5: 시험을 돌린다**

Run: `cd frontend && node --test "tests/coachPanel.test.js"` then `node --test "tests/*.test.js"`
Expected: 새 11개 PASS, 기존 전부 PASS.

- [ ] **Step 6: 돌연변이**

1. `has_more` 분기를 지워 항상 `"다른 선택지 보기"` 를 그린다 → `test("has_more 가 아니면 …")` 가 실패해야 한다.
2. 선택지의 `why` 를 안 그리게 한다 → 근거 시험이 실패해야 한다.

되돌린 뒤 수트 통과를 확인한다.

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/components/CoachPanel.jsx frontend/src/components/CoachPanel.css frontend/src/api.js frontend/tests/coachPanel.test.js
git commit -m "feat(web): 코치 패널 - 질문 하나와 선택지 3~5개"
```

---

### Task 7: StudioPro 두 열 배치

**Files:**
- Modify: `frontend/src/pages/StudioPro.jsx`
- Modify: `frontend/src/pages/StudioPro.css`
- Test: `frontend/tests/studioProCoach.test.js` (create)

**Interfaces:**
- Consumes: Task 6 의 `<CoachPanel onPatch onDone />`
- Produces: 두 열 배치와 패치 적용 강조

- [ ] **Step 1: 시험을 쓴다**

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";

test("프로 빌더에 코치 패널이 있다", async () => {
  const text = textOf(await renderComponent("src/pages/StudioPro.jsx", {}, { router: true }));
  assert.match(text, /껄무새 코치/);
});

test("코치 패널이 조건 판과 함께 그려진다", async () => {
  const html = await renderComponent("src/pages/StudioPro.jsx", {}, { router: true });
  assert.match(html, /pro-side|coach/);
  assert.match(html, /pro-build/);
});

test("1행 주석의 자리맡김이 사라졌다", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync("src/pages/StudioPro.jsx", "utf8");
  assert.doesNotMatch(src, /코치 패널 자리는 별도 계획에서 채운다/);
});
```

- [ ] **Step 2: `StudioPro.jsx` 를 고친다**

- 지금의 쌓임을 `<div className="pro-main">` 으로 감싸고 그 옆에 `<aside className="pro-side">` 를 둔다. 둘을 `<div className="pro-cols">` 가 감싼다.
- `<CoachPanel onPatch={(patch) => setForm((f) => ({ ...f, ...patch }))} onDone={() => setStale(true)} />`.
  `setStale` 은 이미 있는 상태다(조건이 바뀌면 이전 결과가 낡았다고 알린다) — 코치가 판을 바꿨으니 같은 뜻이다.
- 패치가 적용된 칸을 잠깐 강조한다: 패치의 키를 짧게 상태로 들고(`highlight`), `Builder` 에 넘길
  방법이 없으면 **대신 코치 패널 안에 "방금 바꾼 것" 한 줄**로 보여 준다(예: `규칙 · 봉 간격을 바꿨어요`).
  `Builder` 의 내부 구조를 건드리지 않는 쪽을 고른다 — 그쪽이 훨씬 싸다.
- 1행 주석 `"코치 패널 자리는 별도 계획에서 채운다"` 를 지운다.

- [ ] **Step 3: CSS**

```css
/* 넓은 화면에서 두 열 — 코치는 스크롤을 따라온다. 좁은 화면에서는 위로 올라간다. */
.pro-cols { display: grid; gap: 16px; }
.pro-side { display: grid; gap: 10px; }
@media (min-width: 1100px) {
  .pro-cols { grid-template-columns: minmax(0, 1fr) 340px; align-items: start; }
  .pro-side { position: sticky; top: 16px; }
}
```

- [ ] **Step 4: 시험을 돌린다**

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: 새 3개 + 기존 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/pages/StudioPro.jsx frontend/src/pages/StudioPro.css frontend/tests/studioProCoach.test.js
git commit -m "feat(web): 프로 빌더를 두 열로 - 오른쪽에 코치 패널"
```

---

### Task 8: 그래프 전수 경로 시험 (Review Focus 5)

**Files:**
- Test: `frontend/tests/coachGraphPaths.test.js` (create)

**Interfaces:**
- Consumes: Task 3 의 `coachGraph.generated.json`; `macro.js` 의 `defaultForm`, `withTypeDefaults`, `validateDetailed`, `buildMacro`

**이 작업이 스펙 §6 의 보증을 만든다.** 코치가 낼 수 있는 폼이 전부 유효함을 **전수로** 증명한다 — 요청 때 검사하는 대신 빌드 때 못 박는다. 파이썬에 폼 빌더를 한 벌 더 베끼지 않는 이유가 이것이다.

- [ ] **Step 1: 시험을 쓴다**

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import graph from "../src/lib/coachGraph.generated.json" with { type: "json" };
import { buildMacro, defaultForm, validateDetailed } from "../src/lib/macro.js";

// 입력형 노드는 대표값 하나로 고정한다 — 선택형의 조합이 증명해야 할 것이다.
const INPUTS = {
  symbols: { single: "BTCUSDT", multi: "BTCUSDT, ETHUSDT, SOLUSDT" },
  period: "1y",
  capital: "1000",
};

function choicesOf(node, answers) {
  if (node.by) {
    const k = `${answers.goal}|${answers.risk}|${answers.watch}`;
    return node.by[k] || [];
  }
  return node.choices || [];
}

/** 그래프를 걸어 (답 묶음, 폼) 쌍을 전부 만든다. */
function* walk(symbolsMode) {
  const order = ["goal", "risk", "watch", "rule", "symbols", "weights",
                 "period", "capital", "entry_filter", "bundle_risk"];
  function* step(i, answers, form) {
    if (i >= order.length) { yield { answers, form }; return; }
    const key = order[i];
    const node = graph.nodes[key];
    if (!node) { yield* step(i + 1, answers, form); return; }
    if (node.kind !== "choice") {
      const value = key === "symbols" ? INPUTS.symbols[symbolsMode] : INPUTS[key];
      const patch = key === "symbols" ? { symbol: value }
        : key === "period" ? { preset: value }
        : { initial_capital: Number(value) };
      yield* step(i + 1, { ...answers, [key]: value }, { ...form, ...patch });
      return;
    }
    const opts = choicesOf(node, answers);
    if (opts.length === 0) { yield* step(i + 1, answers, form); return; }
    for (const c of opts) {
      // rule 노드는 규칙 타입을 바꾸므로 그 규칙의 기본값을 함께 얹는다(프런트가 하는 일과 같게).
      const next = key === "rule"
        ? { ...withTypeDefaultsSafe(form, c.patch.rule_type), ...c.patch }
        : { ...form, ...c.patch };
      yield* step(i + 1, { ...answers, [key]: c.value }, next);
    }
  }
  yield* step(0, {}, defaultForm());
}

function withTypeDefaultsSafe(form, rt) {
  // macro.js 의 withTypeDefaults 를 쓴다. 이름이 다르면 그 파일을 읽고 맞춘다.
  return require("../src/lib/macro.js").withTypeDefaults(form, rt);
}

test("코치가 낼 수 있는 모든 폼이 검증을 통과한다 (종목 하나)", () => {
  let n = 0;
  for (const { answers, form } of walk("single")) {
    const err = validateDetailed(form);
    assert.equal(err, null, `${JSON.stringify(answers)} -> ${JSON.stringify(err)}`);
    n += 1;
  }
  assert.ok(n > 100, `경로가 ${n}개뿐이다 — 그래프를 다 걷지 못했다`);
});

test("코치가 낼 수 있는 모든 폼이 검증을 통과한다 (종목 셋)", () => {
  let n = 0;
  for (const { answers, form } of walk("multi")) {
    const err = validateDetailed(form);
    assert.equal(err, null, `${JSON.stringify(answers)} -> ${JSON.stringify(err)}`);
    n += 1;
  }
  assert.ok(n > 100);
});

test("모든 폼이 buildMacro 로 조립된다", () => {
  for (const { answers, form } of walk("multi")) {
    let macro;
    assert.doesNotThrow(() => { macro = buildMacro(form); }, JSON.stringify(answers));
    assert.ok(macro.rule_type, JSON.stringify(answers));
    assert.ok(macro.params && Object.keys(macro.params).length > 0, JSON.stringify(answers));
    // NaN 이 폼을 통과해 매크로에 들어가면 서버가 422 를 낸다 — 여기서 잡는다.
    for (const [k, v] of Object.entries(macro.params)) {
      assert.ok(!(typeof v === "number" && Number.isNaN(v)), `${k} 가 NaN (${JSON.stringify(answers)})`);
    }
  }
});

test("그래프의 모든 패치 키가 폼에 있는 키다", () => {
  const known = new Set(Object.keys(defaultForm()));
  for (const node of Object.values(graph.nodes)) {
    const lists = node.by ? Object.values(node.by) : [node.choices || []];
    for (const list of lists) {
      for (const c of list) {
        for (const k of Object.keys(c.patch || {})) {
          assert.ok(known.has(k), `폼에 없는 키: ${k}`);
        }
      }
    }
  }
});
```

**`import ... with { type: "json" }` 이 이 Node 에서 안 되면** `fs.readFileSync` + `JSON.parse` 로 읽어라. `require` 와 ESM `import` 를 섞지 말고 파일 맨 위 import 로 통일하라 — 위 코드의 `withTypeDefaultsSafe` 는 그렇게 고쳐라.

- [ ] **Step 2: 돌려서 걸리는 조합을 전부 고친다**

Run: `cd frontend && node --test "tests/coachGraphPaths.test.js"`

**실패하면 시험을 느슨하게 하지 마라 — `coach_graph.py` 의 선택지나 패치를 고쳐라.** 그 뒤
`cd backend && .venv/Scripts/python.exe scripts/dump_coach_graph.py` 로 JSON 을 다시 내보낸다.
이 왕복이 이 작업의 본체다. 몇 바퀴 돌 수 있다.

걸린 조합과 고친 내용을 **하나씩** 보고서에 적어라 — 그것이 이 작업이 실제로 무언가를 증명했다는 기록이다.

- [ ] **Step 3: 경로 수를 보고한다**

시험이 센 경로 수(`n`)를 보고서에 적어라. 스펙이 추정한 10,800 과 크게 다르면 그 이유를 말해라
(건너뛰기 규칙 때문에 줄어드는 것은 정상이다).

- [ ] **Step 4: 돌연변이**

`coach_graph.py` 의 어느 선택지 `patch` 에 **폼에 없는 키**를 하나 넣고 JSON 을 다시 내보낸다 →
`test("그래프의 모든 패치 키가 폼에 있는 키다")` 가 실패해야 한다. 되돌리고 JSON 을 다시 내보낸다.

- [ ] **Step 5: 커밋**

```bash
git add frontend/tests/coachGraphPaths.test.js backend/app/coach_graph.py frontend/src/lib/coachGraph.generated.json
git commit -m "test(coach): 그래프 전수 경로 - 코치가 낼 수 있는 모든 폼이 유효하다"
```

---

## 마지막 확인 (모든 Task 뒤)

- [ ] 백엔드 전체 수트

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_collector_queue.py --ignore=tests/test_lightweight_collector_fairness.py --ignore=tests/test_lightweight_collectors.py --ignore=tests/test_news_translation_square_languages.py --ignore=tests/test_onchain_integration.py --ignore=tests/test_whale_activity_workflow.py
```

Expected: 기존 실패 4개만.

- [ ] 프런트 전체 수트

```bash
cd frontend && node --test "tests/*.test.js"
```

- [ ] 실행기 불변 · 매크로 스키마 불변

```bash
cd /c/Users/RHJ/Desktop/gg_parrot && git diff --stat main -- runner/ backend/app/engine/schema.py backend/app/macro_signing.py
```

Expected: `runner/` 0줄. `schema.py`·`macro_signing.py` 는 **이 계획에서 바뀌지 않아야 한다**(코치는 폼만 만진다). 바뀌었으면 그 이유를 적어라.

- [ ] AI 없이 코치가 도는지 손으로 한 번 확인한다(`ai_available()` 이 거짓인 상태) — 기본 문구로 10턴이 끝까지 가는지.
