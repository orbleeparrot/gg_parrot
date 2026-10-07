"""코치 끝점 — 인증 · 동의 · 한도 · 위변조.

픽스처를 새로 만들지 않고 기존 ask 시험(tests/test_ask.py)의 모양을 그대로 쓴다:
모듈 수준 TestClient, /api/auth/signup 으로 실제 사용자를 만들고 Bearer 헤더로 부른다.
"""
from __future__ import annotations

import secrets

from fastapi.testclient import TestClient

from app import main as main_mod
from app import observability
from app.main import app

client = TestClient(app)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"capi{tok}@ex.com", "username": f"capi_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _consented_user():
    """동의까지 마친 사용자의 인증 헤더."""
    token, _ = _signup()
    assert client.post("/api/ask/consent", headers=_auth(token)).status_code == 200
    return _auth(token)


def test_start_requires_login():
    assert client.post("/api/coach/start").status_code in (401, 403)


def test_start_requires_consent():
    token, _ = _signup()
    r = client.post("/api/coach/start", headers=_auth(token))
    assert r.status_code == 403
    assert "동의" in r.json()["detail"]


def test_start_returns_the_first_question():
    r = client.post("/api/coach/start", headers=_consented_user())
    assert r.status_code == 200
    body = r.json()
    assert body["question"]["key"] == "goal"
    assert 3 <= len(body["question"]["choices"]) <= 5
    assert body["done"] is False
    assert body["turn"] == 0  # turn 은 답이 쌓인 수 — 시작은 0 (브리프의 1 은 coach.py 와 어긋나 있었다)


def test_start_accepts_an_exchange_and_rejects_an_unknown_one():
    h = _consented_user()
    assert client.post("/api/coach/start", json={"exchange": "upbit"}, headers=h).status_code == 200
    r = client.post("/api/coach/start", json={"exchange": "없는거래소"}, headers=h)
    assert r.status_code == 400


def test_answer_advances_and_returns_a_patch():
    h = _consented_user()
    sid = client.post("/api/coach/start", headers=h).json()
    value = sid["question"]["choices"][0]["value"]
    r = client.post("/api/coach/answer", headers=h,
                    json={"session_id": sid["session_id"], "key": "goal", "value": value})
    assert r.status_code == 200
    body = r.json()
    assert body["question"]["key"] != "goal"
    assert isinstance(body["form_patch"], dict)


def test_answer_with_an_unknown_value_is_400():
    h = _consented_user()
    sid = client.post("/api/coach/start", headers=h).json()
    r = client.post("/api/coach/answer", headers=h,
                    json={"session_id": sid["session_id"], "key": "goal", "value": "없는값"})
    assert r.status_code == 400


def test_answer_on_someone_elses_session_is_refused():
    h = _consented_user()
    other = _consented_user()
    first = client.post("/api/coach/start", headers=h).json()
    value = first["question"]["choices"][0]["value"]  # 올바른 답이어도 남의 대화면 거절돼야 한다
    r = client.post("/api/coach/answer", headers=other,
                    json={"session_id": first["session_id"], "key": "goal", "value": value})
    assert r.status_code in (400, 403, 404)
    # 주인의 대화는 건드려지지 않았다 — 같은 질문이 그대로 남아 있다.
    again = client.post("/api/coach/back", headers=h, json={"session_id": first["session_id"]})
    assert again.json()["question"]["key"] == "goal"
    assert again.json()["turn"] == 0


def test_more_does_not_charge_quota():
    h = _consented_user()
    sid = client.post("/api/coach/start", headers=h).json()
    before = sid["remaining_today"]
    r = client.post("/api/coach/more", headers=h,
                    json={"session_id": sid["session_id"], "key": "goal"})
    assert r.status_code == 200
    assert r.json()["remaining_today"] == before


def test_back_returns_the_previous_question():
    h = _consented_user()
    sid = client.post("/api/coach/start", headers=h).json()
    value = sid["question"]["choices"][0]["value"]
    client.post("/api/coach/answer", headers=h,
                json={"session_id": sid["session_id"], "key": "goal", "value": value})
    r = client.post("/api/coach/back", headers=h, json={"session_id": sid["session_id"]})
    assert r.json()["question"]["key"] == "goal"


def test_quota_exhausted_is_429(monkeypatch):
    monkeypatch.setenv("ASK_DAILY_LIMIT", "1")
    h = _consented_user()
    assert client.post("/api/coach/start", headers=h).status_code == 200
    assert client.post("/api/coach/start", headers=h).status_code == 429


def test_a_missing_session_is_not_a_500():
    h = _consented_user()
    r = client.post("/api/coach/answer", headers=h,
                    json={"session_id": 999999, "key": "goal", "value": "x"})
    assert r.status_code in (400, 403, 404)


def test_start_is_rate_limited_but_answer_is_not(monkeypatch):
    # /start 에만 비율 제한이 걸린다 — 대화 중 /answer · /more · /back 은 자연히 자주 불린다.
    monkeypatch.setattr(main_mod, "_coach_limiter",
                        observability.SlidingWindowRateLimiter(limit=1, window_seconds=60.0, max_keys=10))
    h = _consented_user()
    sid = client.post("/api/coach/start", headers=h).json()
    r = client.post("/api/coach/start", headers=h)
    assert r.status_code == 429 and "Retry-After" in r.headers
    for _ in range(3):
        assert client.post("/api/coach/more", headers=h,
                           json={"session_id": sid["session_id"], "key": "goal"}).status_code == 200
