"""관리자 대시보드 — is_admin 컬럼으로만 열리고, 방문 비콘은 해시만 남기며, 집계가 SQLite 에서도 돈다."""
from __future__ import annotations

import secrets

from fastapi.testclient import TestClient
from sqlmodel import select

from app import admin as admin_mod
from app.db import User, Visit, get_session
from app.main import app

client = TestClient(app)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={"email": f"a{tok}@ex.com", "username": f"a_{tok}", "password": "password123"}).json()
    return body["token"], body["user"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _make_admin(user_id: int):
    with get_session() as db:
        user = db.get(User, user_id)
        user.is_admin = True
        db.add(user)
        db.commit()


def test_admin_endpoints_closed_unless_flag_set():
    assert client.get("/api/admin/overview").status_code == 401
    token, user_id = _signup()
    assert client.get("/api/admin/overview", headers=_auth(token)).status_code == 403
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["is_admin"] is False
    _make_admin(user_id)
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["is_admin"] is True
    assert client.get("/api/admin/overview", headers=_auth(token)).status_code == 200
    assert client.get("/api/admin/news", headers=_auth(token)).status_code == 200


def test_visit_beacon_stores_hash_and_host_only():
    token, user_id = _signup()
    res = client.post("/api/visit", json={"path": "/board?x=1", "referrer": "https://www.google.com/search?q=gg", "utm_source": "naver blog!", "visitor": "abc-123"},
                      headers=_auth(token))
    assert res.status_code == 204
    res = client.post("/api/visit", json={"path": "/", "referrer": "https://gg-parrot.vercel.app/board", "visitor": ""})
    assert res.status_code == 204
    with get_session() as db:
        rows = db.exec(select(Visit).order_by(Visit.id.desc()).limit(2)).all()
    newest, older = rows
    assert (older.path, older.referrer_host, older.utm_source, older.user_id) == ("/board", "www.google.com", "naverblog", user_id)
    assert older.visitor_hash and older.visitor_hash != "abc-123" and len(older.visitor_hash) == 24
    assert (newest.referrer_host, newest.visitor_hash, newest.user_id) == ("", "", None)


def test_overview_counts_visits_signups_and_shape():
    token, user_id = _signup()
    _make_admin(user_id)
    client.post("/api/visit", json={"path": "/studio", "visitor": "v1"})
    client.post("/api/visit", json={"path": "/studio", "visitor": "v1"})
    client.post("/api/visit", json={"path": "/", "visitor": "v2", "referrer": "https://t.co/x"})
    body = client.get("/api/admin/overview?days=7", headers=_auth(token)).json()
    assert body["days"] == 7 and len(body["traffic"]["visits"]) == 7 and len(body["signups"]["by_day"]) == 7
    today = body["traffic"]["visits"][-1]
    assert today["count"] >= 3 and body["traffic"]["visitors"][-1]["count"] >= 2
    assert any(r["host"] == "t.co" for r in body["traffic"]["referrers"])
    assert body["signups"]["total"] >= 1 and body["signups"]["by_day"][-1]["count"] >= 1
    assert set(body["macros"]) >= {"leaderboard_total", "rule_types", "runs", "papers", "quests", "unlock_points"}
    news = client.get("/api/admin/news", headers=_auth(token)).json()
    assert set(news) >= {"tickers", "articles", "feeds", "public_news", "whales", "ai_budget"}


def test_prune_visits_drops_old_rows():
    with get_session() as db:
        db.add(Visit(day_kst="2020-01-01", path="/", created_ms=1_577_836_800_000))
        db.commit()
        removed = admin_mod.prune_visits(db)
        db.commit()
        assert removed >= 1
        assert db.exec(select(Visit).where(Visit.day_kst == "2020-01-01")).first() is None
