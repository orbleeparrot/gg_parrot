"""알림 — 헤더 종 아이콘에 쌓이는 개인 알림과 전체 공지.

무엇이 오나(``kind``):

- ``quest``            일일 퀘스트 완료 · 포인트 획득(quests.complete)
- ``macro_sold``       내 리더보드 매크로가 언락돼 포인트 획득(leaderboard.unlock_entry)
- ``macro_registered`` 매크로를 리더보드에 등록(main.leaderboard_register)
- ``comment`` · ``reply``  내 글에 댓글 · 내 댓글에 답글(board.add_comment)
- ``agent``            실행 중인 내 매크로의 에이전트 소식 — 실행 시작·종료·오류, 신호·주문·
                       체결(runner), 새 기사(position_news), 대형 체결(whale_activity).
                       ``session_id`` 로 어느 실행 세션인지 가리킨다
- ``admin`` · ``notice``  관리자가 보낸 개인 메시지 / 전체 공지(POST /api/admin/notifications)

행은 Supabase 의 알림 전용 스키마 ``notifications`` (message · receipt) 에 쌓인다 —
``supabase/migrations/20260915021932_notifications_schema.sql``. SQLite(개발·테스트)는
스키마 없이 같은 테이블을 쓴다(db.NOTIFICATIONS_SCHEMA). 브라우저는 Data API 로
접근하지 않고 항상 ``/api/me/notifications`` 를 거친다.

쓰기 함수(:func:`notify`)는 커밋하지 않는다 — 호출자의 트랜잭션에 얹혀 원장·댓글과 함께
확정되거나 함께 되돌아간다. 세션이 없는 호출자는 :func:`notify_now` 를 쓴다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from .db import NotificationMessage, NotificationReceipt, RunSession, User, get_session

log = logging.getLogger(__name__)

KINDS = frozenset({
    "quest", "macro_sold", "macro_registered", "comment", "reply", "agent", "admin", "notice",
})
KEEP_PER_USER = 300  # 계정당 보관 상한 — 넘치면 오래된 개인 알림부터 지운다(공지는 회원별 행이 아니라 제외)
NOTICE_WINDOW_MS = 30 * 24 * 3600 * 1000  # 전체 공지는 최근 30일치만 보인다
PAGE_MAX = 50
TITLE_MAX, BODY_MAX, LINK_MAX, REF_MAX = 120, 300, 200, 120
# 이 시간 안에 heartbeat 를 보낸 세션만 '실행 중'으로 보고 에이전트 소식을 보낸다.
ACTIVE_SESSION_WINDOW = timedelta(minutes=10)


def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def _one_line(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def view(row: NotificationMessage, *, read: bool) -> dict:
    """API 응답 한 건. ``scope`` 는 personal(개인) | notice(전체 공지)."""
    try:
        data = json.loads(row.data_json or "{}")
    except ValueError:
        data = {}
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "link": row.link,
        "data": data if isinstance(data, dict) else {},
        "session_id": row.session_id,
        "scope": "notice" if row.user_id is None else "personal",
        "created_at": row.created_at,
        "created_ms": row.created_ms,
        "read": bool(read),
    }


# --- 쓰기 ------------------------------------------------------------------
def notify(
    db,
    user_id: Optional[int],
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    *,
    data: Optional[dict] = None,
    session_id: Optional[int] = None,
    ref: str = "",
    trim: bool = True,
) -> Optional[NotificationMessage]:
    """알림 한 건을 세션에 추가한다(커밋은 호출자). ``user_id`` 가 None 이면 전체 공지.

    ``ref`` 가 있으면 같은 (user_id, kind, ref) 알림이 이미 있을 때 만들지 않고 None 을
    돌려준다 — 수집기가 같은 기사·체결 묶음을 다시 볼 때 중복을 막는 열쇠.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind: {kind}")
    title = _one_line(title, TITLE_MAX)
    if not title:
        raise ValueError("notification title is required")
    ref = _one_line(ref, REF_MAX)
    if ref and _exists(db, user_id, kind, ref):
        return None
    created_at, created_ms = _now()
    row = NotificationMessage(
        user_id=user_id,
        kind=kind,
        title=title,
        body=_one_line(body, BODY_MAX),
        link=str(link or "")[:LINK_MAX],
        data_json=json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str),
        session_id=session_id,
        ref=ref,
        created_at=created_at,
        created_ms=created_ms,
    )
    db.add(row)
    db.flush()
    if trim and user_id is not None:
        trim_user(db, user_id)
    return row


def notify_now(
    user_id: Optional[int],
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    *,
    data: Optional[dict] = None,
    session_id: Optional[int] = None,
    ref: str = "",
) -> Optional[int]:
    """세션이 없는 호출자용 — 자기 트랜잭션으로 저장하고 커밋한다.

    알림은 부가 기능이라 실패해도 호출자를 막지 않는다(본 동작은 이미 끝났다).
    """
    try:
        with get_session() as db:
            row = notify(db, user_id, kind, title, body, link, data=data, session_id=session_id, ref=ref)
            db.commit()
            return row.id if row is not None else None
    except Exception:  # noqa: BLE001 — 로그만 남기고 조용히 넘어간다
        log.exception("notification write failed (kind=%s, user=%s)", kind, user_id)
        return None


def _exists(db, user_id: Optional[int], kind: str, ref: str) -> bool:
    statement = select(NotificationMessage.id).where(
        NotificationMessage.kind == kind, NotificationMessage.ref == ref
    )
    if user_id is None:
        statement = statement.where(NotificationMessage.user_id.is_(None))
    else:
        statement = statement.where(NotificationMessage.user_id == user_id)
    return db.exec(statement.limit(1)).first() is not None


def trim_user(db, user_id: int) -> int:
    """개인 알림이 KEEP_PER_USER 를 넘으면 오래된 것부터 지운다. 지운 수를 돌려준다."""
    total = int(db.exec(
        select(func.count(NotificationMessage.id)).where(NotificationMessage.user_id == user_id)
    ).one())
    overflow = total - KEEP_PER_USER
    if overflow <= 0:
        return 0
    oldest = db.exec(
        select(NotificationMessage.id)
        .where(NotificationMessage.user_id == user_id)
        .order_by(NotificationMessage.created_ms.asc(), NotificationMessage.id.asc())
        .limit(overflow)
    ).all()
    db.exec(delete(NotificationMessage).where(NotificationMessage.id.in_(list(oldest))))
    return len(oldest)


def notify_running_sessions(
    db,
    *,
    title: str,
    body: str = "",
    link: str = "/agents",
    data: Optional[dict] = None,
    ref: str = "",
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    asset: Optional[str] = None,
    kind: str = "agent",
) -> int:
    """실행 중(최근 heartbeat)인 세션의 주인에게 세션마다 한 건씩 보낸다.

    종목(``symbol`` [+ ``market``: spot|futures]) 또는 자산(``asset``: LINKUSDT → LINK)으로
    세션을 고른다. ``ref`` 는 세션 id 를 붙여 세션별로 한 번만 남긴다. 보낸 수를 돌려준다.
    """
    cutoff = (datetime.now(timezone.utc) - ACTIVE_SESSION_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.exec(
        select(RunSession).where(RunSession.status == "running", RunSession.last_heartbeat_at >= cutoff)
    ).all()
    wanted_symbol = (symbol or "").strip().upper()
    wanted_market = (market or "").strip().lower()
    wanted_asset = (asset or "").strip().upper()
    if not wanted_symbol and not wanted_asset:
        return 0
    sent = 0
    users: set[int] = set()
    for row in rows:
        if wanted_symbol:
            if row.symbol.upper() != wanted_symbol:
                continue
            if wanted_market and (row.market or "").lower() != wanted_market:
                continue
        else:
            from . import news as news_mod  # 지연 import — news 는 무거운 모듈

            if news_mod.asset_from_market_symbol(row.symbol) != wanted_asset:
                continue
        created = notify(
            db, row.user_id, kind, title, body, link,
            data={**(data or {}), "symbol": row.symbol, "session_id": row.id},
            session_id=row.id, ref=f"{ref}:{row.id}" if ref else "", trim=False,
        )
        if created is not None:
            sent += 1
            users.add(row.user_id)
    for user_id in users:
        trim_user(db, user_id)
    return sent


# --- 읽기 ------------------------------------------------------------------
def _notice_floor(now_ms: int) -> int:
    return now_ms - NOTICE_WINDOW_MS


def _seen_notices(user_id: int):
    return select(NotificationReceipt.message_id).where(NotificationReceipt.user_id == user_id)


def unread_count(db, user: User) -> int:
    """안 읽은 개인 알림 + 아직 읽음 기록이 없는 최근 공지."""
    _, now_ms = _now()
    personal = int(db.exec(
        select(func.count(NotificationMessage.id)).where(
            NotificationMessage.user_id == user.id, NotificationMessage.read_ms.is_(None)
        )
    ).one())
    notices = int(db.exec(
        select(func.count(NotificationMessage.id)).where(
            NotificationMessage.user_id.is_(None),
            NotificationMessage.created_ms >= _notice_floor(now_ms),
            NotificationMessage.id.not_in(_seen_notices(user.id)),
        )
    ).one())
    return personal + notices


def list_for(db, user: User, *, limit: int = 30, before_ms: Optional[int] = None) -> dict:
    """최신순 목록(개인 알림 + 최근 공지) 한 페이지와 안 읽은 수.

    ``before_ms`` 보다 오래된 것만 주면 다음 페이지. ``next_before`` 가 None 이면 끝.
    """
    limit = max(1, min(PAGE_MAX, int(limit)))
    _, now_ms = _now()
    personal = select(NotificationMessage).where(NotificationMessage.user_id == user.id)
    notices = select(NotificationMessage).where(
        NotificationMessage.user_id.is_(None),
        NotificationMessage.created_ms >= _notice_floor(now_ms),
    )
    if before_ms:
        personal = personal.where(NotificationMessage.created_ms < before_ms)
        notices = notices.where(NotificationMessage.created_ms < before_ms)
    order = (NotificationMessage.created_ms.desc(), NotificationMessage.id.desc())
    rows = [
        *db.exec(personal.order_by(*order).limit(limit)).all(),
        *db.exec(notices.order_by(*order).limit(limit)).all(),
    ]
    rows.sort(key=lambda row: (row.created_ms, row.id or 0), reverse=True)
    rows = rows[:limit]
    notice_ids = [row.id for row in rows if row.user_id is None]
    seen = set(db.exec(
        select(NotificationReceipt.message_id).where(
            NotificationReceipt.user_id == user.id, NotificationReceipt.message_id.in_(notice_ids)
        )
    ).all()) if notice_ids else set()
    items = [
        view(row, read=(row.read_ms is not None) if row.user_id is not None else (row.id in seen))
        for row in rows
    ]
    return {
        "items": items,
        "unread": unread_count(db, user),
        "now_ms": now_ms,
        "next_before": rows[-1].created_ms if len(rows) == limit else None,
    }


def mark_read(db, user: User, *, ids: Optional[Iterable[int]] = None, everything: bool = False) -> int:
    """읽음 처리 — ``ids`` 몇 개만, 또는 ``everything`` 으로 전부. 커밋하고 남은 안 읽은 수를 돌려준다."""
    _, now_ms = _now()
    personal = update(NotificationMessage).where(
        NotificationMessage.user_id == user.id, NotificationMessage.read_ms.is_(None)
    )
    pending_notices = select(NotificationMessage.id).where(
        NotificationMessage.user_id.is_(None), NotificationMessage.id.not_in(_seen_notices(user.id))
    )
    if everything:
        pending_notices = pending_notices.where(NotificationMessage.created_ms >= _notice_floor(now_ms))
    else:
        wanted = sorted({int(value) for value in (ids or [])})[:200]
        if not wanted:
            return unread_count(db, user)
        personal = personal.where(NotificationMessage.id.in_(wanted))
        pending_notices = pending_notices.where(NotificationMessage.id.in_(wanted))
    db.exec(personal.values(read_ms=now_ms).execution_options(synchronize_session=False))
    for message_id in db.exec(pending_notices).all():
        db.add(NotificationReceipt(message_id=message_id, user_id=user.id, read_ms=now_ms))
    try:
        db.commit()
    except IntegrityError:
        # 같은 공지를 두 요청이 동시에 읽음 처리했다 — 이미 기록됐으니 그대로 둔다.
        db.rollback()
    return unread_count(db, user)
