"""코치 세션 — 한도 · 턴 쌓기 · 되돌아가기.

한도와 동의는 ``ask.py`` 의 기계를 그대로 쓴다. 세션 행도 ``AskMacroSession`` 이고 구분은
``request_json`` 안의 ``{"kind": "coach"}`` 다 — 새 열을 더하면 마이그레이션이 생기고,
하루 한도 인덱스 ``(user_id, day_kst)`` 를 그대로 쓰는 것이 더 값싸다.

**한도는 세션을 열 때 한 번만 깎는다.** 턴마다 깎으면 좁혀 가는 대화에서 사용자가
되돌아가기를 두려워하고, 열 턴 대화 한 번이 하루 한도를 다 쓴다. 그래서 ``answer`` ·
``more`` · ``back`` 은 한도를 건드리지 않는다.

**한도를 깎고 세션 생성이 실패하면 돌려준다.** ``ask.py`` 의 ``_run_candidates`` 와 같은
순서다: 한도 검사 → 추가권 고르기 → 행 먼저 커밋 → ``try/except`` 에서 행 삭제 + 추가권
되돌리기. 행을 먼저 커밋하는 것은 한도 검사와 저장 사이의 창을 닫기 위해서다.

**턴 상한에 닿으면 마무리한다, 끊지 않는다.** 남은 칸을 그래프의 기본값으로 채워
``done`` · ``wrapped_up`` 과 함께 완성된 폼 패치를 낸다. 반쪽 폼을 던지고 멈추면
사용자에게는 "코치가 고장났다" 로 보인다.

거래소는 세션에 싣는다(``request_json["exchange"]``). 국내(업비트 · 빗썸)에서 서버는
규칙 K 를 거절하므로, 턴마다 그래프에 거래소를 넘겨 낼 수 있는 폼만 내게 한다 —
"코치가 내는 폼은 언제나 유효하다" 가 깨지지 않도록.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlmodel import Session

from .ask import (
    DISCLAIMER,
    DISCLAIMER_VERSION,
    AskError,
    SESSION_TTL_MS,
    _now,
    _unused_credit,
    consented,
    daily_limit,
    free_remaining_today,
    remaining_today,
    release_read_session,
    resume_external_user,
    refund_external_reservation,
)
from .coach_ai import default_ask_ai, voice
from .coach_graph import (
    FIRST_KEY,
    GRAPH,
    PAGE_SIZE,
    choices_for,
    defaults_for_remaining,
    next_key,
    patch_for,
)
from .db import AskMacroSession, User
from .exchanges import normalize_exchange
from .quests import today_kst

COACH_KIND = "coach"
# 열 개 노드를 다 물어도 열 턴이다. 상한은 "되돌아가기 · 다시 고르기" 몇 번까지는 받아 주되
# 끝없이 늘어나지는 않게 두는 숫자다. 상수를 모듈 속성으로 읽는 것이 중요하다 — 시험이
# monkeypatch 로 이 값을 바꿔 마무리 경로를 본다(지역 바인딩하면 조용히 통과한다).
COACH_MAX_TURNS = 14


# --- 세션 상태 ---------------------------------------------------------
def _empty_state(exchange: str) -> dict:
    """candidates_json 에 담는 대화 상태. 새 열을 만들지 않으려고 이 칸에 쌓는다."""
    return {"turns": [], "pages": {}, "ai": False, "exchange": exchange}


def _load_state(row: AskMacroSession, exchange: str) -> dict:
    try:
        state = json.loads(row.candidates_json)
    except Exception:
        state = None
    if not isinstance(state, dict):
        return _empty_state(exchange)
    state.setdefault("turns", [])
    state.setdefault("pages", {})
    state.setdefault("ai", False)
    state["exchange"] = exchange
    if not isinstance(state["turns"], list):
        state["turns"] = []
    if not isinstance(state["pages"], dict):
        state["pages"] = {}
    return state


def _save_state(db: Session, row: AskMacroSession, state: dict) -> None:
    row.candidates_json = json.dumps(state, ensure_ascii=False)
    db.add(row)
    db.commit()


def _answers(state: dict) -> dict:
    """지금까지의 답 — 그래프에 넘기는 꾸러미. 거래소도 같이 싣는다(규칙 좁히기가 읽는다)."""
    answers = {t["key"]: t["answer"] for t in state["turns"] if isinstance(t, dict) and "key" in t}
    answers["exchange"] = state.get("exchange") or "binance"
    return answers


def _merged_form(state: dict) -> dict:
    """쌓인 패치 전부 — 되돌아간 뒤 폼을 다시 맞출 때 프런트가 이것을 쓴다."""
    form: dict = {}
    for turn in state["turns"]:
        if isinstance(turn, dict) and isinstance(turn.get("patch"), dict):
            form.update(turn["patch"])
    return form


# --- 질문 모양 ---------------------------------------------------------
def _page_count(total: int) -> int:
    return max(1, -(-total // PAGE_SIZE))


def _question_view(key: str, answers: dict, page: int = 0) -> dict:
    """한 질문을 응답 모양으로. 선택지는 PAGE_SIZE 씩 잘라 담는다."""
    question = GRAPH.get(key)
    if question is None:
        raise AskError(500, "코치가 모르는 질문이에요. 처음부터 다시 시작해 주세요.")
    everything = choices_for(key, answers)
    pages = _page_count(len(everything))
    page = page % pages if pages else 0
    shown = everything[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    # 말투 — 보이는 한 페이지의 문구 · 라벨 · 순서만 AI 가 손댄다(값 · 집합 · 패치는 못 바꾼다).
    ask, shown, ai_used = voice(key, question.ask, shown, answers, ask_ai=default_ask_ai())
    return {
        "key": key,
        "ask": ask,
        "ai": ai_used,
        "kind": question.kind,
        "field": question.field,
        "choices": [{"value": c.value, "label": c.label, "patch": dict(c.patch), "why": c.why}
                    for c in shown],
        # 선택지가 한 페이지에 다 안 들어가면 "다른 선택지 보기" 를 띄운다. 마지막 페이지에서
        # 더 부르면 첫 페이지로 돌아가므로, 페이지가 둘 이상이면 늘 참이다.
        "has_more": pages > 1,
        "page": page,
        "pages": pages,
        "total_choices": len(everything),
    }


def _first_question(answers: dict) -> dict:
    """첫 질문. 시험이 이 이름을 monkeypatch 해 '세션 생성 실패' 를 만든다."""
    return _question_view(FIRST_KEY, answers, 0)


def _reply(db: Session, user: User, row: AskMacroSession, state: dict, *,
           question: Optional[dict], form_patch: dict, done: bool, wrapped_up: bool) -> dict:
    if question and question.get("ai") and not row.ai_used:
        row.ai_used = True      # 말투에 AI 를 한 번이라도 썼으면 감사 기록에 남긴다(ask.py 와 같은 칸)
        db.add(row)
        db.commit()
    return {
        "session_id": row.id,
        "turn": len(state["turns"]),
        "max_turns": COACH_MAX_TURNS,
        "question": question,
        # 이번 수에 폼에 더할 것. 마무리면 남은 칸을 메꾼 값까지 들어 있다.
        "form_patch": form_patch,
        # 지금까지 쌓인 전부 — 되돌아간 뒤 폼을 처음부터 다시 맞출 때 쓴다.
        "form": _merged_form(state),
        "done": done,
        "wrapped_up": wrapped_up,
        "exchange": state.get("exchange") or "binance",
        "remaining_today": remaining_today(db, user),
        "daily_limit": daily_limit(),
        # 동의 여부는 알려만 준다 — 막는 것은 경로(route)의 몫이다.
        "consented": consented(user),
        "disclaimer": DISCLAIMER,
        "disclaimer_version": DISCLAIMER_VERSION,
    }


# --- 시작 -------------------------------------------------------------
def start(db: Session, user: User, exchange: str = "binance") -> dict:
    """대화를 열고 첫 질문을 낸다 — **한도는 여기서 한 번만** 깎인다."""
    identity = (user.id, user.auth_version)
    try:
        exchange = normalize_exchange(exchange)
    except ValueError:
        raise AskError(400, "모르는 거래소예요.") from None

    if remaining_today(db, user) <= 0:
        raise AskError(429, f"오늘은 {daily_limit()}번 다 썼어요. 내일 다시 와 주세요.")

    created_at, created_ms = _now()
    # 무료가 남아 있으면 무료로, 다 썼으면 아직 안 쓴 추가권으로 — ask.py 와 같은 순서다.
    credit = None if free_remaining_today(db, user) > 0 else _unused_credit(db, user)
    row = AskMacroSession(
        user_id=user.id, day_kst=today_kst(),
        request_json=json.dumps({"kind": COACH_KIND, "exchange": exchange}, ensure_ascii=False),
        candidate_count=0, results_json="[]",
        disclaimer_version=DISCLAIMER_VERSION, ai_used=False, elapsed_ms=0,
        created_at=created_at, created_ms=created_ms, paid=credit is not None,
        candidates_json=json.dumps(_empty_state(exchange), ensure_ascii=False),
        chosen_symbol="", expires_ms=created_ms + SESSION_TTL_MS, ask_count=0,
    )
    # 한도 검사와 저장 사이의 창을 닫으려고 행을 먼저 커밋한다(ask.py 와 같은 이유).
    db.add(row)
    db.commit()
    db.refresh(row)
    if credit is not None:
        credit.used_session_id = row.id
        db.add(credit)
        db.commit()

    state = _empty_state(exchange)
    release_read_session(db, user, row, credit)
    reservation_id = row.id
    credit_id = credit.id if credit is not None else None
    try:
        user, row, question = _external_question(
            db, user, row, lambda: _first_question(_answers(state)), identity=identity)
    except Exception:
        # 첫 질문을 못 만들었으면 횟수를 돌려준다 — 행을 지우고 추가권은 다시 '안 씀' 으로.
        refund_external_reservation(db, reservation_id, credit_id)
        raise

    return _reply(db, user, row, state, question=question, form_patch={},
                  done=False, wrapped_up=False)


# --- 세션 읽기 --------------------------------------------------------
def _load(db: Session, user: User, session_id: int) -> tuple[AskMacroSession, dict]:
    """세션 행을 찾아 소유자 · kind · 만료를 검사하고 대화 상태를 돌려준다."""
    row = db.get(AskMacroSession, session_id)
    if row is None or row.user_id != user.id:
        raise AskError(404, "코치 대화를 찾지 못했어요. 처음부터 다시 시작해 주세요.")
    try:
        request = json.loads(row.request_json)
    except Exception:
        request = {}
    if not isinstance(request, dict) or request.get("kind") != COACH_KIND:
        # 다른 흐름(물어볼까?)의 세션을 코치로 쓰려는 시도 — 404 로 덮는다.
        raise AskError(404, "코치 대화를 찾지 못했어요. 처음부터 다시 시작해 주세요.")
    _, now_ms = _now()
    # expires_ms 가 0 인 행도 만료로 본다 — 영원한 세션을 만들지 않는다.
    if not row.expires_ms or row.expires_ms <= now_ms:
        raise AskError(410, "대화가 오래됐어요. 처음부터 다시 시작해 주세요.")
    return row, _load_state(row, normalize_exchange(request.get("exchange") or "binance"))


def _external_question(db, user, row, generate, *, identity):
    """Voice generation never holds a checkout; reload before recording it."""
    release_read_session(db, user, row)
    expected = row.candidates_json
    question = generate()
    current_user = resume_external_user(db, user, identity=identity)
    current, _state = _load(db, current_user, row.id)
    if current.candidates_json != expected:
        raise AskError(409, "대화가 다른 요청으로 바뀌었어요. 다시 불러와 주세요.")
    return current_user, current, question


# --- 답하기 -----------------------------------------------------------
def answer(db: Session, user: User, session_id: int, key: str, value) -> dict:
    """답 하나를 쌓고 다음 질문을 낸다. **한도를 깎지 않는다.**"""
    identity = (user.id, user.auth_version)
    row, state = _load(db, user, session_id)
    answers = _answers(state)

    want = next_key(answers)
    if want is None:
        raise AskError(409, "이미 다 물었어요. 폼에서 이어 고쳐 주세요.")
    if key != want:
        # 클라이언트가 순서를 건너뛰면 거절한다 — 그래프의 건너뛰기 규칙이 뚫린다.
        raise AskError(400, "지금 물어본 질문이 아니에요. 화면을 새로 고쳐 주세요.")

    try:
        patch = patch_for(key, value, answers)
    except ValueError as exc:
        raise AskError(400, f"고를 수 없는 답이에요: {exc}") from None

    page = int(state["pages"].get(key, 0) or 0)
    state["turns"].append({"key": key, "answer": value, "patch": patch, "page": page})
    state["pages"].pop(key, None)
    answers = _answers(state)

    nxt = next_key(answers)
    at_cap = len(state["turns"]) >= COACH_MAX_TURNS
    if nxt is None or at_cap:
        # 마무리 — 남은 칸을 그래프의 기본값으로 메꾼다. 반쪽 폼을 던지고 멈추지 않는다.
        form_patch = dict(patch)
        form_patch.update(defaults_for_remaining(answers))
        state["turns"][-1]["patch"] = {**patch, **form_patch}
        _save_state(db, row, state)
        return _reply(db, user, row, state, question=None, form_patch=form_patch,
                      done=True, wrapped_up=nxt is not None)

    _save_state(db, row, state)
    user, row, question = _external_question(
        db, user, row, lambda: _question_view(nxt, answers, int(state["pages"].get(nxt, 0) or 0)),
        identity=identity)
    return _reply(db, user, row, state,
                  question=question,
                  form_patch=patch, done=False, wrapped_up=False)


# --- 다른 선택지 보기 --------------------------------------------------
def more(db: Session, user: User, session_id: int, key: str) -> dict:
    """그 질문의 다음 선택지 묶음. **한도를 깎지 않는다.** 마지막 페이지에서 더 부르면 첫 페이지로."""
    identity = (user.id, user.auth_version)
    row, state = _load(db, user, session_id)
    answers = _answers(state)
    want = next_key(answers)
    if want is None:
        raise AskError(409, "이미 다 물었어요. 폼에서 이어 고쳐 주세요.")
    if key != want:
        raise AskError(400, "지금 물어본 질문이 아니에요. 화면을 새로 고쳐 주세요.")

    page = int(state["pages"].get(key, 0) or 0) + 1
    user, row, view = _external_question(
        db, user, row, lambda: _question_view(key, answers, page), identity=identity)
    state["pages"][key] = view["page"]
    _save_state(db, row, state)
    return _reply(db, user, row, state, question=view, form_patch={},
                  done=False, wrapped_up=False)


# --- 되돌아가기 -------------------------------------------------------
def back(db: Session, user: User, session_id: int) -> dict:
    """마지막 턴을 버리고 그 질문을 다시 낸다. 비어 있으면 첫 질문(아무 일도 안 한다)."""
    identity = (user.id, user.auth_version)
    row, state = _load(db, user, session_id)
    if state["turns"]:
        dropped = state["turns"].pop()
        if isinstance(dropped, dict) and dropped.get("key"):
            # 그 질문을 보던 페이지로 돌려 준다.
            state["pages"][dropped["key"]] = int(dropped.get("page") or 0)
        _save_state(db, row, state)

    answers = _answers(state)
    key = next_key(answers) or FIRST_KEY
    user, row, view = _external_question(
        db, user, row, lambda: _question_view(key, answers, int(state["pages"].get(key, 0) or 0)),
        identity=identity)
    # 되돌아가면 그 턴의 패치는 무효다 — 프런트는 form(쌓인 전부)으로 폼을 다시 맞춘다.
    return _reply(db, user, row, state, question=view, form_patch={},
                  done=False, wrapped_up=False)
