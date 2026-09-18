"""관리자 회원 관리 — 목록(+페이징·검색·필터)과 메시지·차단·탈퇴.

차단은 '발언만 막는다'가 핵심이라 읽기·로그인이 살아 있는지도 같이 못 박는다.
탈퇴는 '같은 이메일 재가입 불가'가 요구사항이므로 비밀번호 가입·구글 가입 양쪽을 확인한다.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import members as members_mod
from app.db import BoardPost, User, get_session
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)


def _signup(prefix="mb"):
    token = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"{prefix}{token}@ex.com", "username": f"{prefix}_{token}", "password": "password123",
    }).json()
    return body["token"], body["user"], f"{prefix}{token}@ex.com"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_admin(user_id: int):
    with get_session() as db:
        user = db.get(User, user_id)
        user.is_admin = True
        db.add(user)
        db.commit()


def _admin():
    token, user, _ = _signup("adm")
    _make_admin(user["id"])
    return _auth(token), user


# --- 목록 -------------------------------------------------------------------------------------
def test_member_list_requires_admin():
    assert client.get("/api/admin/members").status_code == 401
    token, _user, _email = _signup()
    assert client.get("/api/admin/members", headers=_auth(token)).status_code == 403


def test_member_list_pages_and_masks_email():
    headers, admin = _admin()
    token, target, email = _signup("row")
    body = client.get("/api/admin/members?page=1&page_size=10", headers=headers).json()
    assert body["page"] == 1 and body["page_size"] == 10
    assert body["total"] >= 2 and body["total_pages"] >= 1
    assert set(body["counts"]) == {"all", "active", "blocked", "deleted", "admin"} and body["counts"]["admin"] >= 1
    row = next(item for item in body["items"] if item["id"] == target["id"])
    # 이메일은 마스킹해서만 내려간다 — 원본이 그대로 나가면 안 된다.
    assert row["email_masked"].endswith("@ex.com") and row["email_masked"].startswith(email[0] + "***")
    assert email not in str(body)
    assert (row["is_admin"], row["is_blocked"], row["is_deleted"]) == (False, False, False)
    assert row["signup_method"] == "email" and row["tier_name"] == "새싹"
    assert row["macros"] == 0 and row["posts"] == 0 and row["last_seen_ms"] is None


def test_member_list_page_size_is_respected_and_pages_do_not_overlap():
    headers, _admin_user = _admin()
    for _ in range(11):
        _signup("pg")
    first = client.get("/api/admin/members?page=1&page_size=10", headers=headers).json()
    second = client.get("/api/admin/members?page=2&page_size=10", headers=headers).json()
    assert len(first["items"]) == 10 and first["total"] > 10 and first["total_pages"] >= 2
    assert not ({row["id"] for row in first["items"]} & {row["id"] for row in second["items"]})
    assert len(second["items"]) >= 1


def test_member_list_search_and_status_filter():
    headers, _admin_user = _admin()
    _token, target, email = _signup("needle")
    found = client.get(f"/api/admin/members?q={target['username']}", headers=headers).json()
    assert [row["id"] for row in found["items"]] == [target["id"]] and found["total"] == 1
    # 검색은 원본 이메일로도 된다(운영자가 문의받은 주소로 찾는다).
    by_email = client.get(f"/api/admin/members?q={email}", headers=headers).json()
    assert [row["id"] for row in by_email["items"]] == [target["id"]]
    admins = client.get("/api/admin/members?status=admin", headers=headers).json()
    assert admins["items"] and all(row["is_admin"] for row in admins["items"])
    assert client.get("/api/admin/members?q=존재하지않는아이디zzz", headers=headers).json()["items"] == []


# --- 메시지 -----------------------------------------------------------------------------------
def test_message_lands_in_the_member_notification_panel():
    headers, admin = _admin()
    token, target, _email = _signup()
    res = client.post(f"/api/admin/members/{target['id']}/message",
                      json={"title": "문의 답변", "body": "확인했어요.", "link": "/support"}, headers=headers)
    assert res.status_code == 200 and res.json() == {"ok": True, "recipient": target["username"]}
    listing = client.get("/api/me/notifications", headers=_auth(token)).json()
    top = listing["items"][0]
    assert (top["kind"], top["title"], top["read"]) == ("admin", "문의 답변", False)
    assert top["data"]["from"] == admin["username"]


def test_message_needs_a_title():
    headers, _admin_user = _admin()
    _token, target, _email = _signup()
    assert client.post(f"/api/admin/members/{target['id']}/message", json={"title": "  "}, headers=headers).status_code == 400


# --- 차단 -------------------------------------------------------------------------------------
def test_block_stops_writing_but_not_reading_or_login():
    headers, _admin_user = _admin()
    token, target, email = _signup()
    res = client.post(f"/api/admin/members/{target['id']}/block", json={"blocked": True, "reason": "욕설"}, headers=headers)
    assert res.status_code == 200
    member = res.json()["member"]
    assert member["is_blocked"] is True and member["blocked_reason"] == "욕설" and member["blocked_at"]

    # 쓰기 세 경로 모두 막힌다.
    assert client.post("/api/chat", json={"text": "안녕하세요"}, headers=_auth(token)).status_code == 403
    post = client.post("/api/board/posts", data={"title": "제목", "body": "본문"}, headers=_auth(token))
    assert post.status_code == 403
    with get_session() as db:
        db.add(BoardPost(author_user_id=999_999, author_name="누군가", title="원글", body="", created_at="2026-09-18T00:00:00Z", created_ms=1_789_000_000_000))
        db.commit()
        post_id = db.exec(select(BoardPost.id).where(BoardPost.title == "원글")).first()
    assert client.post(f"/api/board/posts/{post_id}/comments", json={"text": "댓글"}, headers=_auth(token)).status_code == 403

    # 읽기·로그인·본인 정보는 그대로다 — 차단은 발언 제한이다.
    assert client.get("/api/board/posts", headers=_auth(token)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200
    assert client.post("/api/auth/login", json={"email": email, "password": "password123"}).status_code == 200
    # 왜 막혔는지 알림으로 알려 준다.
    assert "이용이 제한" in client.get("/api/me/notifications", headers=_auth(token)).json()["items"][0]["title"]


def test_unblock_restores_writing():
    headers, _admin_user = _admin()
    token, target, _email = _signup()
    client.post(f"/api/admin/members/{target['id']}/block", json={"blocked": True}, headers=headers)
    assert client.post("/api/chat", json={"text": "차단 중"}, headers=_auth(token)).status_code == 403
    res = client.post(f"/api/admin/members/{target['id']}/block", json={"blocked": False}, headers=headers)
    assert res.status_code == 200 and res.json()["member"]["is_blocked"] is False
    assert res.json()["member"]["blocked_reason"] == "" and res.json()["member"]["blocked_at"] == ""
    assert client.post("/api/chat", json={"text": "돌아왔어요"}, headers=_auth(token)).status_code == 200


# --- 탈퇴 -------------------------------------------------------------------------------------
def test_remove_member_blocks_resignup_with_the_same_email():
    headers, _admin_user = _admin()
    token, target, email = _signup("gone")
    assert client.post(f"/api/admin/members/{target['id']}/remove", json={"reason": "반복 위반"}, headers=headers).json() == {"ok": True}

    with get_session() as db:
        row = db.get(User, target["id"])
        # 주소는 남기지 않고 해시만 남긴다.
        assert row.is_deleted is True and row.email != email and "@account.invalid" in row.email
        assert row.banned_email_hash and email not in row.banned_email_hash
        assert row.password_hash == "" and row.username.startswith("탈퇴회원_")

    # 같은 이메일로는 비밀번호 가입도, 구글 가입도 막힌다.
    again = client.post("/api/auth/signup", json={"email": email, "username": "again_user", "password": "password123"})
    assert again.status_code == 403 and "다시 가입할 수 없" in again.json()["detail"]
    # 토큰도 무효(auth_version 증가) — 탈퇴한 계정으로 계속 쓸 수 없다.
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    # 다른 이메일은 정상 가입된다.
    assert client.post("/api/auth/signup", json={"email": f"other{secrets.token_hex(3)}@ex.com",
                                                 "username": f"other_{secrets.token_hex(3)}", "password": "password123"}).status_code == 200


def test_google_signup_also_refuses_a_banned_email(monkeypatch):
    headers, _admin_user = _admin()
    _token, target, email = _signup("ggone")
    client.post(f"/api/admin/members/{target['id']}/remove", json={}, headers=headers)
    monkeypatch.setattr("app.auth._verify_google_credential", lambda credential: {"email": email, "name": "다시"})
    res = client.post("/api/auth/google", json={"credential": "fake"})
    assert res.status_code == 403 and "다시 가입할 수 없" in res.json()["detail"]


def test_self_withdrawal_still_allows_coming_back():
    """본인 탈퇴는 재가입을 막지 않는다 — 관리자 탈퇴만 막는다(정책 차이를 못 박는다)."""
    token, _target, email = _signup("back")
    assert client.request("DELETE", "/api/me/account", json={"confirmation": "탈퇴", "password": "password123"},
                          headers=_auth(token)).status_code == 200
    again = client.post("/api/auth/signup", json={"email": email, "username": f"back2_{secrets.token_hex(3)}",
                                                 "password": "password123"})
    assert again.status_code == 200


# --- 안전장치 ---------------------------------------------------------------------------------
def test_admin_and_self_are_protected():
    headers, admin = _admin()
    other_headers, other_admin = _admin()
    for path, method in ((f"/api/admin/members/{admin['id']}/block", "post"), (f"/api/admin/members/{admin['id']}/remove", "post")):
        res = getattr(client, method)(path, json={"blocked": True}, headers=headers)
        assert res.status_code == 400 and "자기 계정" in res.json()["detail"]
    # 다른 관리자도 차단·탈퇴할 수 없다.
    res = client.post(f"/api/admin/members/{other_admin['id']}/block", json={"blocked": True}, headers=headers)
    assert res.status_code == 400 and "관리자 계정" in res.json()["detail"]
    assert client.post(f"/api/admin/members/{other_admin['id']}/remove", json={}, headers=headers).status_code == 400
    # 하지만 관리자에게 메시지는 보낼 수 있다.
    assert client.post(f"/api/admin/members/{other_admin['id']}/message", json={"title": "안녕"}, headers=headers).status_code == 200
    assert client.post(f"/api/admin/members/{admin['id']}/message", json={"title": "나에게"}, headers=other_headers).status_code == 200


def test_unknown_and_already_removed_members():
    headers, _admin_user = _admin()
    assert client.post("/api/admin/members/99999999/block", json={"blocked": True}, headers=headers).status_code == 404
    _token, target, _email = _signup("twice")
    client.post(f"/api/admin/members/{target['id']}/remove", json={}, headers=headers)
    res = client.post(f"/api/admin/members/{target['id']}/block", json={"blocked": True}, headers=headers)
    assert res.status_code == 400 and "이미 탈퇴" in res.json()["detail"]


def test_running_agent_blocks_removal():
    headers, _admin_user = _admin()
    _token, target, _email = _signup("busy")
    from app.db import RunSession
    with get_session() as db:
        db.add(RunSession(user_id=target["id"], status="running", symbol="BTCUSDT",
                          started_at="2026-09-18T00:00:00Z"))
        db.commit()
    res = client.post(f"/api/admin/members/{target['id']}/remove", json={}, headers=headers)
    assert res.status_code == 409 and "실행 중" in res.json()["detail"]


def test_mask_email_helper():
    assert members_mod.mask_email("hsrohsro1234@gmail.com") == "h***@gmail.com"
    assert members_mod.mask_email("deleted-9-abc@account.invalid") == "(탈퇴)"
    assert members_mod.mask_email("") == "" and members_mod.mask_email("broken") == ""
