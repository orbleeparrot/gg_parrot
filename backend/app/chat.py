"""Daily KST chat with verified authors and member-scoped durable read state."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, text as sql_text
from sqlmodel import select

from . import avatars
from .db import ChatMessage, ChatReadState, LeaderboardEntry, User, UserAvatar, get_session
from .leaderboard import _kst_hhmm, _unlocked_ids_for, today_start_ms
from .moderation import require_clean_text

MAX_LEN = 300
MAX_LIST = 200
_RATE_MAX = 5
_RATE_WINDOW = 10.0


# 매크로 언급 — 리더보드 행의 '채팅에 붙여넣기'가 넣어 주는 토큰. 채팅 본문에 글자로 실려 오고,
# 화면에는 매크로 카드로 그린다(스티커의 [sticker:id] 와 같은 방식).
MACRO_TOKEN = re.compile(r"\[macro:(\d{1,9})\]")
_MAX_MACRO_CARDS = 3


class RateLimited(Exception):
    """Raised when a member sends messages too quickly."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _lock_member(db, user_id: int) -> User:
    """Serialize writes per member across workers, including SQLite tests/dev.

    PostgreSQL locks the account row; SQLite needs a write reservation before
    the first read because it does not implement SELECT FOR UPDATE. Call this
    before any queries in the transaction to avoid read-to-write upgrade races.
    """
    if db.get_bind().dialect.name == "sqlite":
        db.exec(sql_text("BEGIN IMMEDIATE"))
    account = db.exec(select(User).where(User.id == user_id).with_for_update()).first()
    if account is None:
        raise ValueError("계정을 찾을 수 없어요. 다시 로그인해 주세요.")
    return account


def _latest_id(db, *, start_ms: int | None = None) -> int:
    query = select(func.max(ChatMessage.id))
    if start_ms is not None:
        query = query.where(ChatMessage.created_ms >= start_ms)
    return int(db.exec(query).one() or 0)


def add_message(account: User, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("빈 메시지는 보낼 수 없습니다.")
    text = text[:MAX_LEN]
    require_clean_text(text, "메시지")
    with get_session() as db:
        author = _lock_member(db, int(account.id))
        # Timestamp after obtaining the lock: waiting senders must not insert an
        # old timestamp that falls outside the persisted rate-limit window.
        now = _now_utc()
        now_ms = int(now.timestamp() * 1000)
        recent_count = db.exec(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.user_id == author.id,
                ChatMessage.created_ms > now_ms - int(_RATE_WINDOW * 1000),
            )
        ).one()
        if recent_count >= _RATE_MAX:
            raise RateLimited("메시지를 너무 빠르게 보냈어요. 잠시 후 다시 시도하세요.")
        row = ChatMessage(
            user_id=author.id,
            username=author.username,
            text=text,
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            created_ms=now_ms,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _view(row, avatars.avatar_url(row.user_id, db=db),
                     _macro_cards(db, [row.text], int(account.id)))


def _member_seen_id(user_id: int) -> int:
    with get_session() as db:
        state = db.get(ChatReadState, user_id)
        if state is not None:
            return state.last_seen_id
    with get_session() as db:
        _lock_member(db, user_id)
        # Another request may have initialized this member while we waited.
        state = db.get(ChatReadState, user_id)
        if state is None:
            state = ChatReadState(user_id=user_id, last_seen_id=_latest_id(db))
            db.add(state)
            db.commit()
            db.refresh(state)
        return state.last_seen_id


def mark_read(account: User, last_seen_id: int) -> dict:
    with get_session() as db:
        _lock_member(db, int(account.id))
        target = min(max(0, last_seen_id), _latest_id(db))
        state = db.get(ChatReadState, account.id)
        if state is None:
            state = ChatReadState(user_id=int(account.id), last_seen_id=target)
        else:
            state.last_seen_id = max(state.last_seen_id, target)
        db.add(state)
        db.commit()
        db.refresh(state)
        return {"seen_id": state.last_seen_id}


def list_messages(
    account: User | None = None,
    *,
    before_id: int | None = None,
    seen_id: int | None = None,
) -> dict:
    start_ms = today_start_ms()
    server_seen = _member_seen_id(int(account.id)) if account is not None else None
    with get_session() as db:
        # Keep metadata independent of the requested historical page.
        latest_id = _latest_id(db, start_ms=start_ms)
        effective_seen = server_seen
        if seen_id is not None:
            supplied_seen = min(max(0, seen_id), _latest_id(db))
            effective_seen = max(server_seen or 0, supplied_seen)
        query = select(ChatMessage, UserAvatar.version).outerjoin(
            UserAvatar, UserAvatar.user_id == ChatMessage.user_id,
        ).where(
            ChatMessage.created_ms >= start_ms, ChatMessage.id <= latest_id,
        )
        if before_id is not None:
            query = query.where(ChatMessage.id < before_id)
        rows = db.exec(query.order_by(ChatMessage.id.desc()).limit(MAX_LIST + 1)).all()
        has_more = len(rows) > MAX_LIST
        page = list(reversed(rows[:MAX_LIST]))
        cards = _macro_cards(db, [row.text for row, _version in page],
                             int(account.id) if account is not None else None)
        items = [_view(row, avatars.public_url(row.user_id, version), cards)
                 for row, version in page]
        unseen_count = 0
        if effective_seen is not None:
            unseen_query = select(func.count(ChatMessage.id)).where(
                ChatMessage.created_ms >= start_ms,
                ChatMessage.id > effective_seen,
                ChatMessage.id <= latest_id,
            )
            if account is not None:
                unseen_query = unseen_query.where(or_(
                    ChatMessage.user_id.is_(None), ChatMessage.user_id != account.id,
                ))
            unseen_count = int(db.exec(unseen_query).one())
    return {
        "items": items,
        "has_more": has_more,
        "oldest_id": items[0]["id"] if items else None,
        "latest_id": latest_id,
        "day_start_ms": start_ms,
        "seen_id": effective_seen,
        "server_seen_id": server_seen,
        "unseen_count": unseen_count,
        "disclaimer": "채팅 내용은 투자 조언이 아니며, 매매 판단과 책임은 본인에게 있습니다.",
    }


def _macro_cards(db, texts: list[str], viewer_user_id: int | None) -> dict[int, dict]:
    """본문에 실린 [macro:id] 들을 카드 자료로. 잠긴 매크로는 전략을 빼고 잠김만 알린다."""
    ids: list[int] = []
    for text in texts:
        for found in MACRO_TOKEN.findall(text or ""):
            entry_id = int(found)
            if entry_id not in ids:
                ids.append(entry_id)
    if not ids:
        return {}
    rows = db.exec(select(LeaderboardEntry).where(LeaderboardEntry.id.in_(ids))).all()
    unlocked = _unlocked_ids_for(db, viewer_user_id, [row.id for row in rows])
    cards = {}
    for row in rows:
        has_owner = row.owner_user_id is not None
        is_owner = has_owner and viewer_user_id is not None and row.owner_user_id == viewer_user_id
        visible = (not has_owner) or is_owner or (row.id in unlocked)
        cards[row.id] = {
            "entry_id": row.id,
            "symbol": row.symbol,
            "username": row.username or row.nickname,
            "is_ai": bool(row.is_ai),
            "locked": not visible,
            "human_summary": row.human_summary if visible else "",
        }
    return cards


def _view(row: ChatMessage, avatar_url: str | None = None, cards: dict[int, dict] | None = None) -> dict:
    mentioned = []
    if cards:
        for found in MACRO_TOKEN.findall(row.text or ""):
            card = cards.get(int(found))
            if card is not None and card not in mentioned:
                mentioned.append(card)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": row.username,
        "avatar_url": avatar_url,
        "text": row.text,
        # 언급된 매크로 — 화면이 [macro:id] 자리에 이 카드를 그린다. 없으면 빈 목록.
        "macros": mentioned[:_MAX_MACRO_CARDS],
        "created_kst": _kst_hhmm(row.created_ms),
        "created_at": row.created_at,
    }
