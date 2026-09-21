"""전략방 — 소그룹 유료 채팅(방 생성·입장·정산·만료·방 메시지)."""
from __future__ import annotations

import secrets
import time

from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import ChatMessage, ChatRoom, ChatRoomMember, PointLedger, User, get_session
from app.main import app

client = TestClient(app)


def _signup(*, age_days: float = 10):
    """가입한 계정(토큰, id). 기본으로 가입 10일 전으로 되돌려 유료방 입장 조건(3일)을 만족시킨다."""
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"room{tok}@ex.com", "username": f"room_{tok}", "password": "password123",
    }).json()
    user_id = body["user"]["id"]
    if age_days:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_days * 86400))
        with get_session() as db:
            user = db.get(User, user_id)
            user.created_at = stamp
            db.add(user)
            db.commit()
    return body["token"], user_id


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _balance(user_id):
    with get_session() as db:
        return db.get(User, user_id).points_balance


def _ledger(user_id):
    with get_session() as db:
        return db.exec(select(PointLedger).where(PointLedger.user_id == user_id).order_by(PointLedger.id)).all()


def test_room_tables_and_columns_exist():
    _, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        assert user.room_consent_at == ""
        room = ChatRoom(owner_id=user_id, title="테스트", capacity=3, entry_fee=0,
                        created_at="2026-09-21T00:00:00Z", created_ms=1, expires_ms=2)
        db.add(room)
        db.commit()
        db.refresh(room)
        db.add(ChatRoomMember(room_id=room.id, user_id=user_id, paid=0, joined_at="2026-09-21T00:00:00Z", joined_ms=1))
        db.add(ChatMessage(user_id=user_id, username="x", text="hi", created_at="2026-09-21T00:00:00Z", created_ms=1, room_id=room.id))
        db.commit()
        member = db.get(ChatRoomMember, (room.id, user_id))
        assert member is not None and member.last_seen_id == 0
        assert db.exec(select(ChatMessage).where(ChatMessage.room_id == room.id)).one().text == "hi"


def _create(token, **overrides):
    body = {"title": "비트 단타 토론", "capacity": 3, "entry_fee": 100, "consent": True}
    body.update(overrides)
    return client.post("/api/rooms", json=body, headers=_auth(token))


def test_create_room_charges_100_and_seats_owner():
    token, owner_id = _signup()
    before = _balance(owner_id)
    r = _create(token)
    assert r.status_code == 200, r.text
    room = r.json()["room"]
    assert room["title"] == "비트 단타 토론" and room["capacity"] == 3 and room["entry_fee"] == 100
    assert room["member_count"] == 1 and room["is_owner"] and room["is_member"]
    assert room["expires_ms"] - room["created_ms"] == 7 * 24 * 3600 * 1000
    assert r.json()["points_balance"] == before - 100
    rows = _ledger(owner_id)
    assert rows[-1].delta == -100 and rows[-1].reason == "room_create" and rows[-1].ref == f"room:{room['id']}"
    with get_session() as db:
        assert db.get(ChatRoomMember, (room["id"], owner_id)).paid == 0
        assert db.get(User, owner_id).room_consent_at != ""


def test_create_room_requires_consent_once():
    token, _ = _signup()
    assert _create(token, consent=False).status_code == 422
    assert _create(token).status_code == 200
    # 두 번째 날 이후에는 consent 를 빼도 된다 — 여기서는 하루 1개 규칙에 걸리므로 429 로 "동의 통과"를 확인한다.
    assert _create(token, consent=False).status_code == 429


def test_create_room_one_per_day_and_validation():
    token, _ = _signup()
    assert _create(token).status_code == 200
    r = _create(token)
    assert r.status_code == 429 and "하루" in r.json()["detail"]
    other, _ = _signup()
    assert _create(other, title="a").status_code == 422
    assert _create(other, title="x" * 31).status_code == 422
    assert _create(other, capacity=1).status_code == 422
    assert _create(other, capacity=11).status_code == 422
    assert _create(other, entry_fee=-1).status_code == 422
    assert _create(other, entry_fee=301).status_code == 422


def test_create_room_needs_points_and_open_account():
    token, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        user.points_balance = 99
        db.add(user)
        db.commit()
    r = _create(token)
    assert r.status_code == 402
    assert _ledger(user_id)[-1].reason != "room_create"
    with get_session() as db:
        user = db.get(User, user_id)
        user.points_balance = 1000
        user.is_blocked = True
        db.add(user)
        db.commit()
    assert _create(token).status_code == 403
