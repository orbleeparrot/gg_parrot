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


def _backdate_room(room_id, *, created_ago_ms=0, expires_in_ms=None):
    with get_session() as db:
        room = db.get(ChatRoom, room_id)
        now_ms = int(time.time() * 1000)
        room.created_ms = now_ms - created_ago_ms
        if expires_in_ms is not None:
            room.expires_ms = now_ms + expires_in_ms
        db.add(room)
        db.commit()


def test_join_paid_room_splits_70_30_and_free_room_writes_no_ledger():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=100).json()["room"]["id"]
    owner_before = _balance(owner_id)
    guest_tok, guest_id = _signup()
    guest_before = _balance(guest_id)
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert r.status_code == 200, r.text
    assert r.json()["points_balance"] == guest_before - 100
    assert r.json()["room"]["member_count"] == 2 and r.json()["room"]["is_member"]
    assert _balance(owner_id) == owner_before + 70
    g = _ledger(guest_id)[-1]
    o = _ledger(owner_id)[-1]
    assert (g.delta, g.reason, g.ref) == (-100, "room_join", f"room:{room_id}")
    assert (o.delta, o.reason, o.ref) == (70, "room_host_earn", f"room:{room_id}")
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)).paid == 100

    free_owner, _ = _signup()
    free_id = _create(free_owner, entry_fee=0).json()["room"]["id"]
    newbie_tok, newbie_id = _signup(age_days=0)  # 방금 가입 — 무료방은 들어갈 수 있다
    ledger_before = len(_ledger(newbie_id))
    assert client.post(f"/api/rooms/{free_id}/join", headers=_auth(newbie_tok)).status_code == 200
    assert len(_ledger(newbie_id)) == ledger_before


def test_join_rejects_duplicate_full_young_and_broke():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, capacity=2, entry_fee=50).json()["room"]["id"]
    guest_tok, guest_id = _signup()
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 200
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 409
    third_tok, _ = _signup()
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(third_tok))
    assert r.status_code == 409 and "정원" in r.json()["detail"]

    big_owner, _ = _signup()
    big_id = _create(big_owner, capacity=5, entry_fee=50).json()["room"]["id"]
    young_tok, _ = _signup(age_days=1)
    assert client.post(f"/api/rooms/{big_id}/join", headers=_auth(young_tok)).status_code == 403
    broke_tok, broke_id = _signup()
    with get_session() as db:
        user = db.get(User, broke_id)
        user.points_balance = 49
        db.add(user)
        db.commit()
    r = client.post(f"/api/rooms/{big_id}/join", headers=_auth(broke_tok))
    assert r.status_code == 402
    with get_session() as db:
        assert db.get(ChatRoomMember, (big_id, broke_id)) is None
    assert _balance(broke_id) == 49


def test_join_expired_or_closed_room_is_410():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    _backdate_room(room_id, expires_in_ms=-1)
    guest_tok, _ = _signup()
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert r.status_code == 410


def test_leave_frees_seat_without_refund_and_owner_cannot_leave():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=100).json()["room"]["id"]
    guest_tok, guest_id = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    after_join = _balance(guest_id)
    owner_after_join = _balance(owner_id)
    assert client.delete(f"/api/rooms/{room_id}/leave", headers=_auth(guest_tok)).json() == {"ok": True}
    assert _balance(guest_id) == after_join and _balance(owner_id) == owner_after_join
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)) is None
    # 다시 들어오면 다시 낸다
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 200
    assert _balance(guest_id) == after_join - 100
    r = client.delete(f"/api/rooms/{room_id}/leave", headers=_auth(owner_tok))
    assert r.status_code == 403


def test_extend_only_in_last_day_and_only_owner():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok)).status_code == 409
    _backdate_room(room_id, expires_in_ms=3600 * 1000)
    guest_tok, _ = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(guest_tok)).status_code == 403
    before = _balance(owner_id)
    r = client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok))
    assert r.status_code == 200, r.text
    assert r.json()["points_balance"] == before - 50
    room = r.json()["room"]
    assert room["extended_count"] == 1
    assert room["expires_ms"] - int(time.time() * 1000) > 7 * 24 * 3600 * 1000
    assert _ledger(owner_id)[-1].reason == "room_extend"


def test_admin_close_blocks_join_and_extend():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    admin_tok, admin_id = _signup()
    with get_session() as db:
        user = db.get(User, admin_id)
        user.is_admin = True
        db.add(user)
        db.commit()
    r = client.post(f"/api/admin/rooms/{room_id}/close", headers=_auth(admin_tok))
    assert r.status_code == 200 and r.json()["room"]["closed_reason"] == "admin"
    guest_tok, _ = _signup()
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 410
    _backdate_room(room_id, expires_in_ms=3600 * 1000)
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok)).status_code == 410
    plain_tok, _ = _signup()
    assert client.post(f"/api/admin/rooms/{room_id}/close", headers=_auth(plain_tok)).status_code == 403


def test_list_rooms_shows_open_rooms_and_my_rooms_including_expired():
    a_tok, a_id = _signup()
    open_id = _create(a_tok, title="열린 방", entry_fee=0).json()["room"]["id"]
    b_tok, _ = _signup()
    gone_id = _create(b_tok, title="끝난 방", entry_fee=0).json()["room"]["id"]
    me_tok, me_id = _signup()
    client.post(f"/api/rooms/{gone_id}/join", headers=_auth(me_tok))
    _backdate_room(gone_id, expires_in_ms=-1)
    r = client.get("/api/rooms", headers=_auth(me_tok))
    assert r.status_code == 200
    body = r.json()
    ids = [room["id"] for room in body["items"]]
    assert open_id in ids and gone_id not in ids
    mine = {room["id"]: room for room in body["mine"]}
    assert gone_id in mine and mine[gone_id]["is_open"] is False and mine[gone_id]["is_member"]
    assert open_id not in mine
    assert body["consented"] is False and body["create_cost"] == 100 and "disclaimer" in body
    assert client.get("/api/rooms").status_code == 401


def test_list_rooms_view_shape_matches_single_view():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    guest_tok, _ = _signup()
    joined_room = client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).json()["room"]
    listed = client.get("/api/rooms", headers=_auth(guest_tok)).json()
    item = next(room for room in listed["items"] if room["id"] == room_id)
    assert item == joined_room
    assert item["member_count"] == 2


def test_room_messages_are_member_only_and_scoped():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    # 공개 채팅 메시지는 방에 섞이지 않는다
    assert client.post("/api/chat", json={"text": "공개 안녕"}, headers=_auth(owner_tok)).status_code == 200
    r = client.post("/api/chat", json={"text": "방 안녕", "room_id": room_id}, headers=_auth(owner_tok))
    assert r.status_code == 200, r.text
    outsider_tok, _ = _signup()
    assert client.get(f"/api/chat?room_id={room_id}", headers=_auth(outsider_tok)).status_code == 403
    assert client.post("/api/chat", json={"text": "몰래", "room_id": room_id}, headers=_auth(outsider_tok)).status_code == 403
    assert client.get(f"/api/chat?room_id={room_id}").status_code == 401
    guest_tok, guest_id = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    body = client.get(f"/api/chat?room_id={room_id}", headers=_auth(guest_tok)).json()
    texts = [m["text"] for m in body["items"]]
    assert texts == ["방 안녕"]
    assert body["room"]["id"] == room_id and body["day_start_ms"] == body["room"]["created_ms"]
    public = client.get("/api/chat", headers=_auth(guest_tok)).json()
    assert "방 안녕" not in [m["text"] for m in public["items"]]
    assert "room" not in public
    # 방 읽음 커서는 멤버 행에 남는다
    last_id = body["items"][-1]["id"]
    assert client.put("/api/chat/read", json={"last_seen_id": last_id, "room_id": room_id}, headers=_auth(guest_tok)).json() == {"seen_id": last_id}
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)).last_seen_id == last_id
    again = client.get(f"/api/chat?room_id={room_id}", headers=_auth(guest_tok)).json()
    assert again["seen_id"] == last_id and again["unseen_count"] == 0


def test_room_messages_before_creation_are_hidden_and_expired_room_is_read_only():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    with get_session() as db:
        db.add(ChatMessage(user_id=owner_id, username="x", text="옛날", created_at="2020-01-01T00:00:00Z", created_ms=1, room_id=room_id))
        db.commit()
    client.post("/api/chat", json={"text": "지금", "room_id": room_id}, headers=_auth(owner_tok))
    body = client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok)).json()
    assert [m["text"] for m in body["items"]] == ["지금"]
    _backdate_room(room_id, expires_in_ms=-1)
    assert client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok)).status_code == 200
    r = client.post("/api/chat", json={"text": "늦음", "room_id": room_id}, headers=_auth(owner_tok))
    assert r.status_code == 410
    assert client.get("/api/chat?room_id=999999", headers=_auth(owner_tok)).status_code == 404


def test_reply_card_cannot_quote_a_room_message_in_public_chat():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    secret_id = client.post("/api/chat", json={"text": "비밀", "room_id": room_id}, headers=_auth(owner_tok)).json()["message"]["id"]
    # 공개 채팅에서 방 메시지를 인용하면 카드가 비어야 한다
    public = client.post("/api/chat", json={"text": f"[reply:{secret_id}] 공개"}, headers=_auth(owner_tok)).json()["message"]
    assert public["reply_to"] is None
    listed = client.get("/api/chat", headers=_auth(owner_tok)).json()
    assert next(m for m in listed["items"] if m["id"] == public["id"])["reply_to"] is None
    # 반대로 방 안에서 공개 메시지를 인용해도 카드가 비어야 한다
    room_msg = client.post("/api/chat", json={"text": f"[reply:{public['id']}] 방", "room_id": room_id}, headers=_auth(owner_tok)).json()["message"]
    assert room_msg["reply_to"] is None
    room_listed = client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok)).json()
    assert next(m for m in room_listed["items"] if m["id"] == room_msg["id"])["reply_to"] is None
    # 같은 방 안의 인용은 그대로 된다
    same = client.post("/api/chat", json={"text": f"[reply:{secret_id}] 같은 방", "room_id": room_id}, headers=_auth(owner_tok)).json()["message"]
    assert same["reply_to"]["id"] == secret_id


def test_create_room_rejects_profane_title_with_422():
    token, _ = _signup()
    r = _create(token, title="씨발 전략방")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail != ""


def test_admin_closed_room_rejects_writes_but_allows_reads():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    posted = client.post("/api/chat", json={"text": "닫히기 전", "room_id": room_id}, headers=_auth(owner_tok))
    assert posted.status_code == 200, posted.text
    admin_tok, admin_id = _signup()
    with get_session() as db:
        user = db.get(User, admin_id)
        user.is_admin = True
        db.add(user)
        db.commit()
    closed = client.post(f"/api/admin/rooms/{room_id}/close", headers=_auth(admin_tok))
    assert closed.status_code == 200 and closed.json()["room"]["closed_reason"] == "admin"
    r = client.post("/api/chat", json={"text": "닫힌 후", "room_id": room_id}, headers=_auth(owner_tok))
    assert r.status_code == 410
    body = client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok))
    assert body.status_code == 200
    assert "닫히기 전" in [m["text"] for m in body.json()["items"]]
