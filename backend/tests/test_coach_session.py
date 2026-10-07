"""코치 세션 — 한도는 세션마다 1회, 턴 상한에서는 마무리, 실패하면 횟수를 돌려준다.

픽스처를 새로 만들지 않고 기존 ask 시험(tests/test_ask.py)의 모양을 그대로 쓴다:
모듈 수준 TestClient 로 /api/auth/signup 해 실제 사용자를 만들고, DB 는 get_session() 으로 연다.
"""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import coach
from app.ask import AskError, daily_limit, remaining_today
from app.coach_graph import FIRST_KEY, GRAPH, INPUT_DEFAULTS, choices_for
from app.db import AskMacroSession, User, get_session
from app.main import app

client = TestClient(app)


def _signup() -> int:
    """실제 사용자 하나를 만들어 user_id 를 돌려준다(test_ask.py 와 같은 방식)."""
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"coach{tok}@ex.com", "username": f"coach_{tok}", "password": "password123",
    }).json()
    return body["user"]["id"]


def _first_choice(key, answers):
    """그 질문의 첫 선택지(입력 노드는 그래프의 기본 입력값)."""
    if GRAPH[key].kind in ("choice", "period"):
        return choices_for(key, answers)[0].value
    return INPUT_DEFAULTS[key]


# --- Review Focus 1: 한도는 세션마다 1회 -------------------------------
def test_quota_is_charged_once_per_session_not_per_turn():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
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


def test_more_and_back_do_not_charge():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        left = remaining_today(db, user)
        coach.more(db, user, started["session_id"], started["question"]["key"])
        assert remaining_today(db, user) == left
        key = started["question"]["key"]
        coach.answer(db, user, started["session_id"], key, _first_choice(key, {}))
        coach.back(db, user, started["session_id"])
        assert remaining_today(db, user) == left


def test_start_refused_with_no_quota_left():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        for _ in range(daily_limit()):
            coach.start(db, user)
        with pytest.raises(AskError) as exc:
            coach.start(db, user)
        assert exc.value.status == 429


# --- Review Focus 2: 실패하면 횟수를 돌려준다 --------------------------
def test_quota_is_refunded_when_the_first_question_cannot_be_built(monkeypatch):
    """한도를 깎고 세션 생성이 실패하면 돌려준다 — 1차 · 2차에서 경로를 빠뜨려 생긴 결함과 같은 자리."""
    uid = _signup()
    monkeypatch.setattr(coach, "_first_question",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with get_session() as db:
        user = db.get(User, uid)
        before = remaining_today(db, user)
        with pytest.raises(RuntimeError):
            coach.start(db, user)
        assert remaining_today(db, user) == before, "한도를 깎고 돌려주지 않았다"
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == uid)).all()
        assert all(json.loads(r.request_json).get("kind") != coach.COACH_KIND for r in rows), \
            "실패한 세션 행이 남았다"


def test_quota_is_refunded_with_an_extra_credit_too():
    """무료를 다 쓴 뒤 추가권으로 연 세션이 실패하면 추가권도 '안 씀' 으로 돌아온다."""
    from app import ask
    from app.db import AskExtraCredit
    from app.quests import today_kst

    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        for _ in range(daily_limit()):
            coach.start(db, user)
        created_at, created_ms = ask._now()
        db.add(AskExtraCredit(user_id=uid, day_kst=today_kst(), price=0,
                              created_at=created_at, created_ms=created_ms))
        db.commit()
        assert remaining_today(db, user) == 1

        original = coach._first_question
        coach._first_question = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with pytest.raises(RuntimeError):
                coach.start(db, user)
        finally:
            coach._first_question = original
        assert remaining_today(db, user) == 1, "추가권을 돌려주지 않았다"
        credits = db.exec(select(AskExtraCredit).where(AskExtraCredit.user_id == uid)).all()
        assert [c.used_session_id for c in credits] == [None]


# --- Review Focus 4: 턴 상한에서 마무리한다 ----------------------------
def test_turn_cap_wraps_up_instead_of_cutting_off(monkeypatch):
    monkeypatch.setattr(coach, "COACH_MAX_TURNS", 2)
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
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


def test_a_full_walk_ends_without_wrapping_up():
    """끝까지 답하면 done 이지만 wrapped_up 은 거짓이다 — 상한에 걸린 것과 구분된다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        key, answers, out = started["question"]["key"], {}, None
        for _ in range(coach.COACH_MAX_TURNS):
            value = _first_choice(key, answers)
            out = coach.answer(db, user, started["session_id"], key, value)
            answers[key] = value
            if out["done"]:
                break
            key = out["question"]["key"]
        assert out["done"] is True and out["wrapped_up"] is False
        assert out["form"]["rule_type"] and out["form"]["symbol"]


# --- 세션 분리 · 위변조 ------------------------------------------------
def test_coach_session_is_marked_as_coach():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        row = db.get(AskMacroSession, started["session_id"])
        assert json.loads(row.request_json)["kind"] == coach.COACH_KIND
        assert json.loads(row.request_json)["exchange"] == "binance"


def test_another_users_session_is_refused():
    uid, other_uid = _signup(), _signup()
    with get_session() as db:
        user, other = db.get(User, uid), db.get(User, other_uid)
        started = coach.start(db, user)
        with pytest.raises(AskError) as exc:
            coach.answer(db, other, started["session_id"], FIRST_KEY, "x")
        assert exc.value.status in (403, 404)


def test_an_ask_session_cannot_be_driven_as_a_coach_session():
    """'물어볼까?' 흐름의 세션 id 를 코치 입구에 밀어 넣어도 안 열린다."""
    from app.quests import today_kst
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        row = AskMacroSession(user_id=uid, day_kst=today_kst(), request_json="{}",
                              candidate_count=0, results_json="[]", disclaimer_version="ask-v2",
                              ai_used=False, elapsed_ms=0, created_at="2026-10-07T00:00:00Z",
                              created_ms=1, expires_ms=4_000_000_000_000)
        db.add(row)
        db.commit()
        db.refresh(row)
        with pytest.raises(AskError) as exc:
            coach.answer(db, user, row.id, FIRST_KEY, "steady")
        assert exc.value.status == 404


def test_an_unknown_answer_is_refused():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        with pytest.raises(AskError):
            coach.answer(db, user, started["session_id"], started["question"]["key"], "존재하지않는값")


def test_answering_a_question_that_is_not_current_is_refused():
    """클라이언트가 순서를 건너뛰면 거절한다 — 그래프의 건너뛰기 규칙이 뚫린다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        with pytest.raises(AskError):
            coach.answer(db, user, started["session_id"], "capital", "1000")


def test_an_expired_session_is_refused():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        row = db.get(AskMacroSession, started["session_id"])
        row.expires_ms = 1
        db.add(row)
        db.commit()
        with pytest.raises(AskError) as exc:
            coach.answer(db, user, started["session_id"], started["question"]["key"], "steady")
        assert exc.value.status == 410


# --- 페이지 나누기 · 되돌아가기 ----------------------------------------
def test_more_returns_the_next_page_of_choices():
    """선택지가 PAGE_SIZE 보다 많은 노드에서만 뜻이 있다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        key = started["question"]["key"]
        first = [c["value"] for c in started["question"]["choices"]]
        if not started["question"]["has_more"]:
            pytest.skip("첫 질문의 선택지가 한 페이지에 다 들어간다")
        nxt = coach.more(db, user, started["session_id"], key)
        assert [c["value"] for c in nxt["question"]["choices"]] != first


def test_more_wraps_around_to_the_first_page():
    """마지막 페이지에서 더 부르면 첫 페이지로 돌아간다 — 막다른 길을 만들지 않는다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        key = started["question"]["key"]
        pages = started["question"]["pages"]
        out = started
        for _ in range(pages):
            out = coach.more(db, user, started["session_id"], key)
        assert out["question"]["page"] == 0
        assert [c["value"] for c in out["question"]["choices"]] == \
            [c["value"] for c in started["question"]["choices"]]


def test_back_drops_the_last_turn():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        key = started["question"]["key"]
        after = coach.answer(db, user, started["session_id"], key, _first_choice(key, {}))
        assert after["question"]["key"] != key
        back = coach.back(db, user, started["session_id"])
        assert back["question"]["key"] == key
        assert back["turn"] == started["turn"]


def test_back_at_the_start_is_a_no_op():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        back = coach.back(db, user, started["session_id"])
        assert back["question"]["key"] == started["question"]["key"]


def test_back_lets_a_different_answer_be_given():
    """되돌아간 뒤 다른 답을 내면 그 답이 쌓인 폼에 남는다 — 되돌리기가 기록까지 지운다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user)
        key = started["question"]["key"]
        choices = [c.value for c in choices_for(key, {})]
        coach.answer(db, user, started["session_id"], key, choices[0])
        coach.back(db, user, started["session_id"])
        out = coach.answer(db, user, started["session_id"], key, choices[1])
        state = json.loads(db.get(AskMacroSession, started["session_id"]).candidates_json)
        assert [t["answer"] for t in state["turns"]] == [choices[1]]
        assert out["turn"] == 1


# --- 거래소를 세션에 싣는다 --------------------------------------------
def test_a_domestic_session_never_offers_the_short_flip_rule():
    """국내(업비트)에서 K 를 내면 코치가 채운 폼이 저장도 안 된다 — 후보에 들어오면 안 된다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user, exchange="upbit")
        assert json.loads(db.get(AskMacroSession, started["session_id"]).request_json)["exchange"] == "upbit"
        assert started["exchange"] == "upbit"
        sid = started["session_id"]
        # 거친 규칙까지 열어 두고 trend 로 가면 바이낸스에서는 K 가 후보에 든다.
        coach.answer(db, user, sid, "goal", "trend")
        coach.answer(db, user, sid, "risk", "wide")
        out = coach.answer(db, user, sid, "watch", "daily")
        assert out["question"]["key"] == "rule"
        values = [c["value"] for c in out["question"]["choices"]]
        assert "K" not in values, values
        assert len(values) >= 3


def test_a_binance_session_still_offers_the_short_flip_rule():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user, exchange="binance")
        sid = started["session_id"]
        coach.answer(db, user, sid, "goal", "trend")
        coach.answer(db, user, sid, "risk", "wide")
        out = coach.answer(db, user, sid, "watch", "daily")
        assert "K" in [c["value"] for c in out["question"]["choices"]]


def test_a_domestic_wrap_up_patch_never_carries_the_short_flip_rule():
    """턴 상한 마무리도 거래소를 본다 — 기본값으로 K 를 집어넣으면 폼이 무효가 된다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        started = coach.start(db, user, exchange="bithumb")
        sid = started["session_id"]
        coach.answer(db, user, sid, "goal", "trend")
        coach.answer(db, user, sid, "risk", "wide")
        coach.COACH_MAX_TURNS, saved = 3, coach.COACH_MAX_TURNS
        try:
            out = coach.answer(db, user, sid, "watch", "daily")
        finally:
            coach.COACH_MAX_TURNS = saved
        assert out["done"] is True and out["wrapped_up"] is True
        assert out["form_patch"]["rule_type"] != "K"


def test_an_unknown_exchange_is_refused():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        with pytest.raises(AskError) as exc:
            coach.start(db, user, exchange="코인가게")
        assert exc.value.status == 400


# --- 응답 모양 --------------------------------------------------------
def test_response_shape():
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        out = coach.start(db, user)
        assert set(out) >= {"session_id", "turn", "max_turns", "question", "form_patch",
                            "done", "wrapped_up", "remaining_today"}
        q = out["question"]
        assert set(q) >= {"key", "ask", "kind", "choices", "has_more", "field"}
        assert out["turn"] == 0 and out["max_turns"] == coach.COACH_MAX_TURNS
        assert out["done"] is False and out["wrapped_up"] is False
        json.dumps(out, ensure_ascii=False)       # 그대로 응답으로 나갈 수 있어야 한다
