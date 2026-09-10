"""Account editing/withdrawal use only the isolated conftest database."""
from io import BytesIO
import secrets
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from PIL import Image
from sqlmodel import SQLModel, create_engine, select

from app import auth, avatars, db as database, runner
from app.db import (BoardPost, ChatMessage, LeaderboardEntry, MacroUnlock, PointLedger,
                    RunnerKey, RunSession, User, UserMacro, get_session)
from app.main import app

client = TestClient(app)
PASSWORD = "old-password-123"


def signup():
    suffix = secrets.token_hex(5)
    response = client.post("/api/auth/signup", json={"email": f"profile-{suffix}@example.com", "username": f"user_{suffix}", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def headers(member):
    return {"Authorization": "Bearer " + member["token"]}


def image_file():
    output = BytesIO()
    Image.new("RGB", (20, 30), "blue").save(output, "PNG")
    return {"image": ("photo.png", output.getvalue(), "image/png")}


def withdraw(member, **overrides):
    return client.request("DELETE", "/api/me/account", headers=headers(member), json={"confirmation": "탈퇴", "password": PASSWORD, **overrides})


def test_profile_edits_are_atomic_and_rename_only_verified_authors():
    member, other = signup(), signup()
    user = member["user"]
    message = client.post("/api/chat", headers=headers(member), json={"text": "hello"}).json()["message"]
    post = client.post("/api/board/posts", headers=headers(member), data={"title": "test"}).json()
    with get_session() as db:
        guest = ChatMessage(user_id=None, username=user["username"], text="guest", created_at=user["created_at"], created_ms=int(time.time() * 1000))
        listing = LeaderboardEntry(user_id="fixture", owner_user_id=user["id"], username=user["username"], nickname=user["username"], symbol="BTCUSDT", macro_json="{}", human_summary="fixture", created_at=user["created_at"], created_ms=int(time.time()*1000))
        db.add_all([guest, listing]); db.commit(); db.refresh(guest); db.refresh(listing)
        guest_id, listing_id = guest.id, listing.id
    renamed = "new_" + secrets.token_hex(5)
    response = client.patch("/api/me/profile", headers=headers(member), data={"username": renamed, "bio": "소개입니다"}, files=image_file())
    assert response.status_code == 200, response.text
    saved = response.json()["user"]
    assert saved["username"] == renamed and saved["bio"] == "소개입니다" and saved["can_change_password"]
    assert saved["avatar_url"] and "password_hash" not in saved and "auth_version" not in saved
    for fields, files, status in [
        ({"username": other["user"]["username"], "bio": "rejected"}, image_file(), 409),
        ({"username": renamed, "bio": "x"*161}, {}, 400),
        ({"username": renamed, "bio": "rejected"}, {"image": ("x.png", b"broken", "image/png")}, 400),
        ({"username": renamed, "remove_avatar": "true"}, image_file(), 400),
    ]:
        assert client.patch("/api/me/profile", headers=headers(member), data=fields, files=files).status_code == status
        assert client.get("/api/auth/me", headers=headers(member)).json()["user"] == saved
    with get_session() as db:
        assert db.get(ChatMessage, message["id"]).username == renamed
        assert db.get(ChatMessage, guest_id).username == user["username"]
        assert db.get(BoardPost, post["id"]).author_name == renamed
        assert db.get(LeaderboardEntry, listing_id).nickname == renamed
    removed = client.patch("/api/me/profile", headers=headers(member), data={"username": renamed, "bio": "", "remove_avatar": "true"})
    assert removed.json()["user"]["avatar_url"] is None
    assert removed.json()["user"]["bio"] == ""


def test_password_change_verifies_old_secret_and_revokes_previous_tokens():
    member = signup()
    user = member["user"]
    reset = auth._make_reset_token(user["id"])
    stream = auth.make_runner_session_stream_token(user["id"])["token"]
    for body in [
        {"current_password": "wrong", "new_password": "new-password-123"},
        {"current_password": PASSWORD, "new_password": "short"},
    ]:
        assert client.post("/api/me/password", headers=headers(member), json=body).status_code == 400
    changed = client.post("/api/me/password", headers=headers(member), json={"current_password": PASSWORD, "new_password": "new-password-123"})
    assert changed.status_code == 200, changed.text
    fresh = changed.json()
    assert client.get("/api/auth/me", headers=headers(member)).status_code == 401
    assert client.get("/api/auth/me", headers=headers(fresh)).status_code == 200
    assert client.post("/api/auth/reset", json={"token": reset, "password": "attempt-reuse"}).status_code == 400
    with pytest.raises(auth.AuthError): auth.decode_runner_session_stream_token(stream)
    assert client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": user["email"], "password": "new-password-123"}).status_code == 200


def test_legacy_tokens_expire_on_password_reset_and_reset_tokens_are_single_use():
    member = signup(); uid = member["user"]["id"]
    payload = jwt.decode(member["token"], auth.SECRET_KEY, algorithms=["HS256"]); payload.pop("ver")
    legacy = {"token": jwt.encode(payload, auth.SECRET_KEY, algorithm="HS256")}
    assert client.get("/api/auth/me", headers=headers(legacy)).status_code == 200
    reset = auth._make_reset_token(uid)
    assert client.post("/api/auth/reset", json={"token": reset, "password": "new-password-123"}).status_code == 200
    assert client.get("/api/auth/me", headers=headers(legacy)).status_code == 401
    assert client.post("/api/auth/reset", json={"token": reset, "password": "new-password-123"}).status_code == 400


def test_withdrawal_requires_confirmation_and_password_and_preserves_other_members_records():
    member, other = signup(), signup(); uid = member["user"]["id"]
    post = client.post("/api/board/posts", headers=headers(member), data={"title": "retained"}).json()
    client.patch("/api/me/profile", headers=headers(member), data={"username": member["user"]["username"], "bio": "private"}, files=image_file())
    photo = avatars.avatar_url(uid)
    key = runner.get_or_create_key(uid)["key"]
    reset = auth._make_reset_token(uid)
    with get_session() as db:
        # Another member's purchase and this member's past purchase both survive.
        for user_id in (uid, other["user"]["id"]):
            db.add(MacroUnlock(user_id=user_id, entry_id=post["id"], price=100, created_at=member["user"]["created_at"]))
            db.add(UserMacro(user_id=user_id, macro_json="{}", source_type="upload", created_at=member["user"]["created_at"], updated_at=member["user"]["created_at"], symbol="BTCUSDT"))
        db.commit()
    assert withdraw(member, confirmation="").status_code == 400
    assert withdraw(member, password="wrong").status_code == 400
    assert client.get("/api/auth/me", headers=headers(member)).status_code == 200
    assert withdraw(member).status_code == 200
    assert client.get("/api/auth/me", headers=headers(member)).status_code == 401
    assert client.get(photo).status_code == 404
    assert client.get(f'/api/board/posts/{post["id"]}').json()["author_name"] == "탈퇴한 회원"
    assert client.post("/api/auth/reset", json={"token": reset, "password": "another-password"}).status_code == 400
    with pytest.raises(Exception) as error: runner.user_for_key(key)
    assert error.value.status_code == 401
    with pytest.raises(Exception): runner.start_session(User(id=uid), {})
    with get_session() as db:
        user = db.get(User, uid)
        assert user.is_deleted and not user.bio and not user.password_hash and user.points_balance == 0
        assert user.email != member["user"]["email"] and user.username != member["user"]["username"]
        assert db.exec(select(UserMacro).where(UserMacro.user_id == uid)).first() is None
        assert db.exec(select(UserMacro).where(UserMacro.user_id == other["user"]["id"])).first() is not None
        assert len(db.exec(select(MacroUnlock).where(MacroUnlock.entry_id == post["id"])).all()) == 2
        assert db.exec(select(PointLedger).where(PointLedger.user_id == uid, PointLedger.reason == "account_closed")).first() is not None
    assert client.get("/api/auth/me", headers=headers(other)).status_code == 200


def test_withdrawal_keeps_runner_access_until_running_sessions_are_stopped():
    member = signup(); uid = member["user"]["id"]
    key = runner.get_or_create_key(uid)["key"]
    with get_session() as db:
        running = RunSession(user_id=uid, started_at=member["user"]["created_at"])
        db.add(running); db.commit(); db.refresh(running); sid = running.id
    assert withdraw(member).status_code == 409
    assert runner.user_for_key(key).id == uid
    with get_session() as db:
        row = db.get(RunSession, sid); row.status = "stopped"; db.add(row); db.commit()
    assert withdraw(member).status_code == 200


def test_google_only_account_requires_matching_fresh_google_identity(monkeypatch):
    member = signup(); uid = member["user"]["id"]
    with get_session() as db:
        user = db.get(User, uid); user.password_hash = ""; db.add(user); db.commit()
    assert client.get("/api/auth/me", headers=headers(member)).json()["user"]["can_change_password"] is False
    assert client.post("/api/me/password", headers=headers(member), json={"current_password": PASSWORD, "new_password": "new-password-123"}).status_code == 403
    assert withdraw(member).status_code == 403
    identity = {"email": "other@example.com", "iat": time.time()}
    monkeypatch.setattr(auth, "_verify_google_credential", lambda credential: identity)
    assert withdraw(member, credential="fixture").status_code == 403
    identity.update(email=member["user"]["email"], iat=time.time()-700)
    assert withdraw(member, credential="fixture").status_code == 403
    identity["iat"] = time.time()
    assert withdraw(member, credential="fixture").status_code == 200


def test_mutations_cannot_run_without_authentication():
    assert client.patch("/api/me/profile", data={"username": "someone"}).status_code == 401
    assert client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 401
    assert client.request("DELETE", "/api/me/account", json={"confirmation": "탈퇴"}).status_code == 401


def test_existing_sqlite_accounts_migrate_without_losing_data(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT, username TEXT, password_hash TEXT, points_balance INTEGER, created_at TEXT)')
        conn.exec_driver_sql('INSERT INTO "user" VALUES (1, \'legacy@example.com\', \'legacy\', \'hash\', 321, \'2026-01-01\')')
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "_engine", engine)
    database._migrate(); database._migrate()
    with get_session() as db:
        user = db.get(User, 1)
        assert user.username == "legacy" and user.points_balance == 321
        assert user.bio == "" and user.auth_version == 0 and user.is_deleted is False
    engine.dispose()


def test_postgres_profile_migration_quotes_reserved_user_table():
    state = {"tables": {"user": False}, "columns": {}, "indexes": set(), "grants": set()}
    statements = database._pg_migration_statements(state)
    assert 'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS bio TEXT NOT NULL DEFAULT \'\'' in statements
    assert 'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0' in statements
    assert 'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE' in statements


@pytest.mark.parametrize("action", ["password", "withdrawal"])
def test_open_session_stream_loses_access_when_credentials_change(action):
    member = signup()
    token = client.post("/api/me/runner/sessions/stream-token", headers=headers(member)).json()["token"]
    with client.websocket_connect("/api/me/runner/sessions/stream", subprotocols=["ggparrot.sessions.v1", f"ggp-auth.{token}"]) as websocket:
        assert websocket.receive_json()["type"] == "sessions.snapshot"
        if action == "password":
            response = client.post("/api/me/password", headers=headers(member), json={"current_password": PASSWORD, "new_password": "new-password-123"})
        else:
            response = withdraw(member)
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 4401
