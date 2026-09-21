"""전략방 — 회원이 만드는 소그룹 채팅(최대 10명, 포인트 입장료, 7일 만료).

돈이 아니라 포인트다(points.py "no real money yet"). 입장료의 70% 는 방장에게, 30% 는 플랫폼 싱크 —
매크로 잠금해제와 같은 경제. 환불 코드는 어디에도 없다: 나가기는 자리만 비우고, 방은 만료로만 끝난다
(관리자 폐쇄 제외). 포인트 현금화가 들어오면 이 모듈 전체를 다시 심사한다(스펙 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy import text as sql_text
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


# 잠금 순서: 방 행 → 회원 행(id 오름차순). 모든 쓰기가 같은 순서라 교착이 없다.


class RoomError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def _begin_write(db) -> None:
    """sqlite 는 SELECT FOR UPDATE 가 없어 트랜잭션 첫 문장에서 쓰기 예약을 잡는다(chat._lock_member 와 같은 이유). 반드시 첫 DB 문장."""
    if db.get_bind().dialect.name == "sqlite":
        db.exec(sql_text("BEGIN IMMEDIATE"))


def _lock_user(db, user_id: int) -> User:
    account = db.exec(select(User).where(User.id == int(user_id)).with_for_update()
                      .execution_options(populate_existing=True)).first()
    if account is None or account.is_deleted:
        raise RoomError(401, "계정을 찾을 수 없어요. 다시 로그인해 주세요.")
    return account


def _lock_room(db, room_id: int) -> ChatRoom:
    room = db.exec(select(ChatRoom).where(ChatRoom.id == int(room_id)).with_for_update()
                   .execution_options(populate_existing=True)).first()
    if room is None:
        raise RoomError(404, "전략방을 찾을 수 없어요.")
    return room


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
    _begin_write(db)
    owner = _lock_user(db, int(account.id))
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


def get_room(db, room_id: int) -> ChatRoom:
    room = db.get(ChatRoom, int(room_id))
    if room is None:
        raise RoomError(404, "전략방을 찾을 수 없어요.")
    return room


def _account_age_ms(user: User, now_ms: int) -> int:
    try:
        created = datetime.strptime(user.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0
    return now_ms - int(created.timestamp() * 1000)


def join_room(db: Session, account: User, room_id: int) -> dict:
    assert_can_write(account)
    _begin_write(db)
    room = _lock_room(db, room_id)  # 잠금 순서: 방 행 먼저
    joined_at, now_ms = _now()
    if not is_open(room, now_ms):
        raise RoomError(410, "이 전략방은 끝났어요.")
    if db.get(ChatRoomMember, (room.id, int(account.id))) is not None:
        raise RoomError(409, "이미 들어와 있는 방이에요.")
    if _member_count(db, room.id) >= room.capacity:
        raise RoomError(409, "정원이 다 찼어요.")
    # 이제 회원 행들 — id 오름차순으로 잠근다(extend_room 과 같은 전역 순서라 교착이 없다).
    ids = sorted({int(account.id), room.owner_id})
    locked: dict[int, User] = {}
    for uid in ids:
        if uid == room.owner_id:
            try:
                locked[uid] = _lock_user(db, uid)
            except RoomError:
                locked[uid] = None  # 방장 계정이 지워졌으면 정산만 건너뛴다 — 입장 자체는 막지 않는다
        else:
            locked[uid] = _lock_user(db, uid)
    guest = locked[int(account.id)]
    owner = locked.get(room.owner_id)
    if room.entry_fee > 0 and _account_age_ms(guest, now_ms) < MIN_ACCOUNT_AGE_FOR_PAID_MS:
        raise RoomError(403, "가입 3일 후부터 유료 전략방에 들어갈 수 있어요.")
    if room.entry_fee > 0:
        points.apply(db, guest, -room.entry_fee, "room_join", f"room:{room.id}")  # 부족하면 InsufficientPoints
        share = points.creator_share(room.entry_fee)
        if share > 0 and owner is not None:
            points.apply(db, owner, share, "room_host_earn", f"room:{room.id}")
    db.add(ChatRoomMember(room_id=room.id, user_id=guest.id, paid=room.entry_fee, joined_at=joined_at, joined_ms=now_ms))
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, guest.id, now_ms=now_ms), "points_balance": guest.points_balance}


def leave_room(db: Session, account: User, room_id: int) -> dict:
    room = get_room(db, room_id)
    if room.owner_id == account.id:
        raise RoomError(403, "방장은 나갈 수 없어요. 방은 만료일에 자동으로 끝나요.")
    member = db.get(ChatRoomMember, (room.id, int(account.id)))
    if member is None:
        raise RoomError(404, "들어와 있는 방이 아니에요.")
    db.delete(member)  # 환불 없음 — 포인트는 건드리지 않는다
    db.commit()
    return {"ok": True}


def extend_room(db: Session, account: User, room_id: int) -> dict:
    assert_can_write(account)
    _begin_write(db)
    room = _lock_room(db, room_id)  # 잠금 순서: 방 행 먼저, 그다음 회원 행
    if room.owner_id != int(account.id):
        raise RoomError(403, "방장만 연장할 수 있어요.")
    owner = _lock_user(db, int(account.id))
    _, now_ms = _now()
    if not is_open(room, now_ms):
        raise RoomError(410, "이 전략방은 끝났어요.")
    if room.expires_ms - now_ms > ROOM_EXTEND_WINDOW_MS:
        raise RoomError(409, "만료 하루 전부터 연장할 수 있어요.")
    points.apply(db, owner, -ROOM_EXTEND_COST, "room_extend", f"room:{room.id}")
    room.expires_ms += ROOM_TTL_MS
    room.extended_count += 1
    db.add(room)
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, owner.id, now_ms=now_ms), "points_balance": owner.points_balance}


def close_room_by_admin(db: Session, room_id: int) -> dict:
    room = get_room(db, room_id)
    closed_at, now_ms = _now()
    if not room.closed_reason:
        room.closed_reason = "admin"
        room.closed_at = closed_at
        db.add(room)
        db.commit()
        db.refresh(room)
    return {"room": room_view(db, room, None, now_ms=now_ms)}
