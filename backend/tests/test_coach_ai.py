"""AI 말투 — 말투와 순서만 손댈 수 있고, 선택지의 값 · 집합 · 패치는 못 바꾼다."""
import json
import secrets

from fastapi.testclient import TestClient

from app import coach
from app.coach_ai import ASK_MAX_LEN, _clean_labels, default_ask_ai, voice
from app.coach_graph import GRAPH, INPUT_DEFAULTS, Choice, choices_for
from app.db import AskMacroSession, User, get_session
from app.main import app

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
    assert all(c.label != "없는 것" for c in choices)


def test_clean_labels_keeps_only_known_values():
    """voice 의 적용 단계도 모르는 키를 막지만, 걸러내기 자체가 제 몫을 하는지 따로 본다(이중 방어)."""
    assert _clean_labels({"I": "돌파", "Z": "없는 것", "C": 3, "D": " "}, list(VALUES)) == {"I": "돌파"}
    assert _clean_labels("문자열", list(VALUES)) == {}


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


# --- 이어 붙이기: coach.py 와 AI 없이 도는가 ---------------------------
client = TestClient(app)


def _signup() -> int:
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"coachai{tok}@ex.com", "username": f"coachai_{tok}", "password": "password123",
    }).json()
    return body["user"]["id"]


def _pick(key, answers):
    if GRAPH[key].kind in ("choice", "period"):
        return choices_for(key, answers)[0].value
    return INPUT_DEFAULTS[key]


def test_default_ask_ai_is_none_without_a_key():
    assert default_ask_ai() is None


def test_ten_turns_run_to_the_end_without_ai():
    """AI 가 없어도(원칙 1) 코치는 끝까지 돌고, 세션은 AI 를 썼다고 기록하지 않는다."""
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        out = coach.start(db, user)
        sid, answers, turns = out["session_id"], {}, 0
        while not out["done"]:
            q = out["question"]
            assert q["ai"] is False and q["ask"] == GRAPH[q["key"]].ask
            value = _pick(q["key"], answers)
            out = coach.answer(db, user, sid, q["key"], value)
            answers[q["key"]] = value
            turns += 1
            assert turns <= coach.COACH_MAX_TURNS
        assert out["wrapped_up"] is False
        assert db.get(AskMacroSession, sid).ai_used is False


def test_an_ai_voice_is_recorded_and_cannot_break_the_walk(monkeypatch):
    """말투에 AI 를 쓰면 ai_used 가 남고, 순서를 바꿔도 어떤 답이든 서버가 같은 값으로 받는다."""
    def reverse_ai(prompt):
        values = [c["value"] for c in json.loads(prompt.split("\n", 1)[1])["choices"]]
        return json.dumps({"ask": "새 문구", "labels": {}, "order": values[::-1]}, ensure_ascii=False)

    monkeypatch.setattr(coach, "default_ask_ai", lambda: reverse_ai)
    uid = _signup()
    with get_session() as db:
        user = db.get(User, uid)
        out = coach.start(db, user)
        sid = out["session_id"]
        assert out["question"]["ai"] is True and out["question"]["ask"] == "새 문구"
        assert db.get(AskMacroSession, sid).ai_used is True
        answers = {}
        while not out["done"]:
            q = out["question"]
            shown = [c["value"] for c in q["choices"]]
            graph_page = [c.value for c in choices_for(q["key"], {**answers, "exchange": "binance"})[:len(shown)]]
            if q["kind"] in ("choice", "period"):
                assert sorted(shown) == sorted(graph_page)       # 집합은 그래프 그대로
                value = shown[0]                                  # AI 가 뒤집은 순서의 첫 값
            else:
                value = INPUT_DEFAULTS[q["key"]]
            out = coach.answer(db, user, sid, q["key"], value)
            answers[q["key"]] = value
