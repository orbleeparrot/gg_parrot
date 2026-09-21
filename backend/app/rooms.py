"""전략방 — 회원이 만드는 소그룹 채팅(최대 10명, 포인트 입장료, 7일 만료).

돈이 아니라 포인트다(points.py "no real money yet"). 입장료의 70% 는 방장에게, 30% 는 플랫폼 싱크 —
매크로 잠금해제와 같은 경제. 환불 코드는 어디에도 없다: 나가기는 자리만 비우고, 방은 만료로만 끝난다
(관리자 폐쇄 제외). 포인트 현금화가 들어오면 이 모듈 전체를 다시 심사한다(스펙 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from . import avatars, points
from .auth import AuthError, assert_can_write
from .db import ChatRoom, ChatRoomMember, User
from .leaderboard import today_start_ms
from .moderation import require_clean_text

ROOM_CREATE_COST = 100
ROOM_EXTEND_COST = 50
ROOM_TTL_MS = 7 * 24 * 3600 * 1000
ROOM_EXTEND_WINDOW_MS = 24 * 3600 * 1000
MIN_CAPACITY = 2
MAX_CAPACITY = 10
MAX_ENTRY_FEE = 300
MIN_ACCOUNT_AGE_FOR_PAID_MS = 3 * 24 * 3600 * 1000
ROOMS_PER_DAY = 1
TITLE_MIN = 2
TITLE_MAX = 30
LIST_LIMIT = 50
MINE_GRACE_MS = ROOM_TTL_MS  # 만료 후 이만큼은 '내 방' 탭에 읽기용으로 남긴다

DISCLAIMER = "전략방 대화는 투자 조언이 아니며, 매매 판단과 책임은 본인에게 있습니다. 투자 권유·수익 보장 발언은 금지돼요."
CONSENT_TEXT = "전략방에서 특정 코인 매수·매도를 권유하거나 수익을 보장하는 발언을 하지 않겠습니다. 위반하면 방이 닫힐 수 있어요."


class RoomError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def _lock_member(db, user_id: int) -> User:
    from .chat import _lock_member as lock  # 같은 잠금 규칙(sqlite BEGIN IMMEDIATE / pg FOR UPDATE)

    return lock(db, user_id)


def is_open(room: ChatRoom, now_ms: int) -> bool:
    return not room.closed_reason and room.expires_ms > now_ms


def _member_count(db, room_id: int) -> int:
    return int(db.exec(select(func.count(ChatRoomMember.user_id)).where(ChatRoomMember.room_id == room_id)).one() or 0)


def room_view(db, room: ChatRoom, viewer_id: int | None, *, now_ms: int | None = None) -> dict:
    now_ms = now_ms if now_ms is not None else _now()[1]
    owner = db.get(User, room.owner_id)
    member = db.get(ChatRoomMember, (room.id, viewer_id)) if viewer_id is not None else None
    is_owner = viewer_id == room.owner_id
    return {
        "id": room.id,
        "title": room.title,
        "owner_username": owner.username if owner else "",
        "owner_avatar_url": avatars.avatar_url(room.owner_id, db=db),
        "capacity": room.capacity,
        "member_count": _member_count(db, room.id),
        "entry_fee": room.entry_fee,
        "created_ms": room.created_ms,
        "expires_ms": room.expires_ms,
        "extended_count": room.extended_count,
        "is_open": is_open(room, now_ms),
        "closed_reason": room.closed_reason,
        "is_member": member is not None,
        "is_owner": is_owner,
        "can_extend": is_owner and is_open(room, now_ms) and room.expires_ms - now_ms <= ROOM_EXTEND_WINDOW_MS,
        "last_seen_id": member.last_seen_id if member else 0,
    }


def _validate_create(title: str, capacity: int, entry_fee: int) -> str:
    title = (title or "").strip()
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise RoomError(422, f"방 제목은 {TITLE_MIN}~{TITLE_MAX}자로 적어 주세요.")
    require_clean_text(title, "방 제목")
    if not MIN_CAPACITY <= int(capacity) <= MAX_CAPACITY:
        raise RoomError(422, f"정원은 {MIN_CAPACITY}~{MAX_CAPACITY}명이에요.")
    if not 0 <= int(entry_fee) <= MAX_ENTRY_FEE:
        raise RoomError(422, f"입장료는 0~{MAX_ENTRY_FEE}포인트예요.")
    return title


def create_room(db: Session, account: User, *, title: str, capacity: int, entry_fee: int, consent: bool) -> dict:
    assert_can_write(account)
    title = _validate_create(title, capacity, entry_fee)
    owner = _lock_member(db, int(account.id))
    if not owner.room_consent_at and not consent:
        raise RoomError(422, "전략방을 만들려면 안내에 동의해 주세요.")
    made_today = db.exec(select(func.count(ChatRoom.id)).where(
        ChatRoom.owner_id == owner.id, ChatRoom.created_ms >= today_start_ms(),
    )).one()
    if int(made_today or 0) >= ROOMS_PER_DAY:
        raise RoomError(429, "전략방은 하루에 하나만 만들 수 있어요. 내일 다시 만들어 주세요.")
    created_at, now_ms = _now()
    room = ChatRoom(owner_id=owner.id, title=title, capacity=int(capacity), entry_fee=int(entry_fee),
                    created_at=created_at, created_ms=now_ms, expires_ms=now_ms + ROOM_TTL_MS)
    db.add(room)
    db.flush()
    points.apply(db, owner, -ROOM_CREATE_COST, "room_create", f"room:{room.id}")
    if not owner.room_consent_at:
        owner.room_consent_at = created_at
        db.add(owner)
    db.add(ChatRoomMember(room_id=room.id, user_id=owner.id, paid=0, joined_at=created_at, joined_ms=now_ms))
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, owner.id, now_ms=now_ms), "points_balance": owner.points_balance}
