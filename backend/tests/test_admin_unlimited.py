"""관리자 계정: 포인트 무한(쓰는 일이 잔액 · 원장을 바꾸지 않는다), 물어볼까 횟수 무제한, 다이아 위 '관리자' 등급."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import points as points_mod
from app.db import MacroUnlock, PointLedger, User, get_session
from app.main import app
from tests.leaderboard_helpers import publish_ready_board

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"adm{tok}@ex.com", "username": f"adm_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_admin(user_id):
    with get_session() as db:
        row = db.get(User, user_id)
        row.is_admin = True
        db.add(row)
        db.commit()


def _ledger(user_id):
    with get_session() as db:
        return [(r.delta, r.reason) for r in db.exec(select(PointLedger).where(PointLedger.user_id == user_id)).all()]


def test_admin_spending_changes_neither_balance_nor_ledger():
    _, user = _signup()
    _make_admin(user["id"])
    with get_session() as db:
        row = db.get(User, user["id"])
        before = row.points_balance
        points_mod.apply(db, row, -10_000_000, "ask_extra", ref="x")  # 잔액보다 훨씬 커도 부족하지 않다
        db.commit()
        assert db.get(User, user["id"]).points_balance == before
    assert all(reason != "ask_extra" for _, reason in _ledger(user["id"]))


def test_admin_sees_every_macro_without_paying_or_counting_a_sale():
    seller_tok, seller = _signup()
    macro = {"symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
             "params": {"take_profit_pct": 5, "initial_capital": 1_000_000}, "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"}}
    entry = client.post("/api/leaderboard/register", json={"macro": macro, "username": "", "password": "", "user_id": "anon"},
                        headers=_auth(seller_tok)).json()["entry"]
    admin_tok, admin = _signup()
    _make_admin(admin["id"])
    publish_ready_board()
    listed = next(e for e in client.get("/api/leaderboard", headers=_auth(admin_tok)).json()["items"] if e["id"] == entry["id"])
    assert listed["locked"] is False and listed["macro"]
    seller_ledger = _ledger(seller["id"])
    res = client.post(f"/api/leaderboard/{entry['id']}/unlock", headers=_auth(admin_tok))
    assert res.status_code == 200, res.text
    copied = client.post(f"/api/me/macros/from-leaderboard/{entry['id']}", headers=_auth(admin_tok))
    assert copied.status_code == 200, copied.text
    with get_session() as db:
        assert db.exec(select(MacroUnlock).where(MacroUnlock.user_id == admin["id"])).first() is None
    assert all(reason not in {"unlock_spend"} for _, reason in _ledger(admin["id"]))
    assert _ledger(seller["id"]) == seller_ledger  # 만든 사람에게 주지 않는다


def test_admin_ask_is_unlimited_and_profile_shows_admin_tier():
    token, user = _signup()
    _make_admin(user["id"])
    status = client.get("/api/ask/status", headers=_auth(token)).json()
    assert status["unlimited"] is True and status["remaining_today"] >= 999
    me = client.get("/api/auth/me", headers=_auth(token)).json()["user"]
    assert me["points_unlimited"] is True
    assert client.get("/api/me/dashboard", headers=_auth(token)).json()["tier"]["name"] == "관리자"
    pid = client.post("/api/board/posts", data={"title": "관리자 글", "body": "내용"}, headers=_auth(token)).json()["id"]
    assert client.get(f"/api/board/posts/{pid}").json()["author_tier"] == "관리자"


def test_regular_member_keeps_limits_and_ledger():
    token, user = _signup()
    status = client.get("/api/ask/status", headers=_auth(token)).json()
    assert status["unlimited"] is False and status["remaining_today"] <= status["daily_limit"]
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["points_unlimited"] is False
