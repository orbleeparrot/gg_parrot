"""관리자 회원 관리 — 목록(+페이징)과 세 가지 조치: 메시지·차단·탈퇴.

조치의 경계를 분명히 둔다.
- **메시지**: 기존 알림 시스템(`notifications.notify`, kind ``admin``)으로 그 회원의 알림창에 넣는다. 실시간 전달도 그대로 따라온다.
- **차단**: `User.is_blocked` 하나. 채팅·게시글·댓글 쓰기만 막고(`auth.assert_can_write`) 로그인·열람·백테스트는 건드리지 않는다.
  발언 제한이라 되돌릴 수 있다.
- **탈퇴**: 회원이 스스로 하는 탈퇴와 같은 정리를 하고(`profile.close_account_rows` — 내용 익명화·개인 행 삭제·포인트 회수)
  거기에 **재가입 차단**을 더한다. 이메일 주소는 남기지 않고 서버 비밀과 섞은 해시(`auth.email_fingerprint`)만 남겨
  같은 주소로 다시 가입하는 것만 막는다. 되돌릴 수 없다.

관리자 계정과 자기 계정에는 차단·탈퇴를 걸 수 없다 — 실수로 전원이 잠기는 일을 막는 최소 안전장치다.
목록의 이메일은 마스킹해서 내려간다(운영에 필요한 만큼만 보여준다).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from . import auth, notifications
from .account import _tier
from .db import (BoardComment, BoardPost, MacroUnlock, PointLedger, RunSession, User, UserMacro, Visit)
from .admin import signup_method_of
from .profile import close_account_rows

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100
QUERY_MAX = 60
REASON_MAX = 200
TITLE_MAX = 80
BODY_MAX = 1000
LINK_MAX = 200

STATUSES = ("all", "active", "blocked", "deleted", "admin")
SORTS = ("recent", "oldest", "points", "username")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask_email(email: str) -> str:
    """``a***@gmail.com`` — 도메인은 그대로 두고 앞자리만 남긴다. 탈퇴 계정의 가짜 주소는 감춘다."""
    value = str(email or "").strip()
    if "@" not in value:
        return ""
    local, _, domain = value.partition("@")
    if domain.endswith("account.invalid"):
        return "(탈퇴)"
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def _counts(db: Session) -> dict:
    """상태 필터 칩에 붙는 수. 한 번에 세어 다섯 번 왕복하지 않는다."""
    row = db.exec(select(
        func.count(User.id),
        func.count(User.id).filter(User.is_deleted.is_(False), User.is_blocked.is_(False), User.is_admin.is_(False)),
        func.count(User.id).filter(User.is_blocked.is_(True), User.is_deleted.is_(False)),
        func.count(User.id).filter(User.is_deleted.is_(True)),
        func.count(User.id).filter(User.is_admin.is_(True)),
    )).one()
    total, active, blocked, deleted, admin = (int(value or 0) for value in row)
    return {"all": total, "active": active, "blocked": blocked, "deleted": deleted, "admin": admin}


def _page_stats(db: Session, ids: list[int]) -> dict:
    """페이지에 실린 회원들의 누적 수 — 행마다 질의하지 않고 묶어서 한 번씩(N+1 금지)."""
    if not ids:
        return {}
    stats: dict[int, dict] = {user_id: {"macros": 0, "posts": 0, "comments": 0, "unlocks_bought": 0,
                                        "sales": 0, "last_seen_ms": None} for user_id in ids}
    groups = (
        ("macros", select(UserMacro.user_id, func.count(UserMacro.id)).where(UserMacro.user_id.in_(ids)).group_by(UserMacro.user_id)),
        ("posts", select(BoardPost.author_user_id, func.count(BoardPost.id)).where(BoardPost.author_user_id.in_(ids)).group_by(BoardPost.author_user_id)),
        ("comments", select(BoardComment.author_user_id, func.count(BoardComment.id)).where(BoardComment.author_user_id.in_(ids)).group_by(BoardComment.author_user_id)),
        ("unlocks_bought", select(MacroUnlock.user_id, func.count(MacroUnlock.id)).where(MacroUnlock.user_id.in_(ids)).group_by(MacroUnlock.user_id)),
        ("sales", select(PointLedger.user_id, func.count(PointLedger.id)).where(
            PointLedger.user_id.in_(ids), PointLedger.reason == "unlock_earn").group_by(PointLedger.user_id)),
    )
    for key, statement in groups:
        for user_id, value in db.exec(statement).all():
            if user_id in stats:
                stats[user_id][key] = int(value or 0)
    for user_id, last in db.exec(select(Visit.user_id, func.max(Visit.created_ms)).where(
            Visit.user_id.in_(ids)).group_by(Visit.user_id)).all():
        if user_id in stats and last:
            stats[user_id]["last_seen_ms"] = int(last)
    return stats


def member_view(user: User, stats: Optional[dict] = None) -> dict:
    stats = stats or {}
    sales = int(stats.get("sales") or 0)
    return {
        "id": user.id,
        "username": user.username,
        "email_masked": mask_email(user.email),
        "created_at": user.created_at,
        "signup_method": signup_method_of(user.signup_method, user.password_hash, user.is_deleted),
        "is_admin": bool(user.is_admin),
        "is_blocked": bool(user.is_blocked),
        "is_deleted": bool(user.is_deleted),
        "blocked_at": user.blocked_at or "",
        "blocked_reason": user.blocked_reason or "",
        "points_balance": int(user.points_balance or 0),
        "tier_name": _tier(sales)["name"],
        "last_seen_ms": stats.get("last_seen_ms"),
        "macros": int(stats.get("macros") or 0),
        "posts": int(stats.get("posts") or 0),
        "comments": int(stats.get("comments") or 0),
        "unlocks_bought": int(stats.get("unlocks_bought") or 0),
        "sales": sales,
    }


def list_members(db: Session, *, page: int = 1, page_size: int = PAGE_SIZE_DEFAULT, q: str = "",
                 status: str = "all", sort: str = "recent") -> dict:
    page = max(1, int(page or 1))
    page_size = max(10, min(PAGE_SIZE_MAX, int(page_size or PAGE_SIZE_DEFAULT)))
    status = status if status in STATUSES else "all"
    sort = sort if sort in SORTS else "recent"
    term = str(q or "").strip()[:QUERY_MAX]

    filters = []
    if status == "active":
        filters += [User.is_deleted.is_(False), User.is_blocked.is_(False), User.is_admin.is_(False)]
    elif status == "blocked":
        filters += [User.is_blocked.is_(True), User.is_deleted.is_(False)]
    elif status == "deleted":
        filters.append(User.is_deleted.is_(True))
    elif status == "admin":
        filters.append(User.is_admin.is_(True))
    if term:
        # 이메일은 마스킹해서 보여주지만 검색은 원본으로 한다 — 운영자가 문의받은 주소로 찾아야 한다.
        like = f"%{term.lower()}%"
        filters.append(func.lower(User.username).like(like) | func.lower(User.email).like(like))

    total = int(db.exec(select(func.count(User.id)).where(*filters)).one() or 0)
    order = {"recent": User.id.desc(), "oldest": User.id.asc(),
             "points": User.points_balance.desc(), "username": User.username.asc()}[sort]
    rows = db.exec(select(User).where(*filters).order_by(order)
                   .offset((page - 1) * page_size).limit(page_size)).all()
    stats = _page_stats(db, [row.id for row in rows])
    return {
        "generated_at": _now_iso(),
        "page": page, "page_size": page_size, "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "counts": _counts(db),
        "items": [member_view(row, stats.get(row.id)) for row in rows],
    }


def _target(db: Session, user_id: int, admin: User, *, action: str) -> User:
    """조치 대상을 찾고 안전장치를 건다 — 관리자·자기 계정·이미 탈퇴한 계정은 막는다."""
    user = db.get(User, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없어요.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="자기 계정은 여기서 바꿀 수 없어요.")
    if user.is_deleted:
        raise HTTPException(status_code=400, detail="이미 탈퇴한 계정이에요.")
    if user.is_admin and action in {"block", "delete"}:
        raise HTTPException(status_code=400, detail="관리자 계정은 차단·탈퇴할 수 없어요.")
    return user


def send_message(db: Session, admin: User, user_id: int, *, title: str, body: str = "", link: str = "") -> dict:
    """그 회원의 알림창으로 관리자 메시지를 보낸다(기존 /api/admin/notifications 와 같은 종류)."""
    subject = str(title or "").strip()[:TITLE_MAX]
    if not subject:
        raise HTTPException(status_code=400, detail="제목을 입력해 주세요.")
    user = _target(db, user_id, admin, action="message")
    notifications.notify(db, user.id, "admin", subject, str(body or "")[:BODY_MAX], str(link or "")[:LINK_MAX],
                         data={"from": admin.username})
    db.commit()
    return {"ok": True, "recipient": user.username}


def set_blocked(db: Session, admin: User, user_id: int, *, blocked: bool, reason: str = "") -> dict:
    """차단·해제. 차단은 쓰기만 막으므로 세션을 끊지 않는다(로그인·열람은 그대로)."""
    user = _target(db, user_id, admin, action="block")
    user.is_blocked = bool(blocked)
    user.blocked_at = _now_iso() if blocked else ""
    user.blocked_reason = str(reason or "").strip()[:REASON_MAX] if blocked else ""
    db.add(user)
    if blocked:
        # 왜 막혔는지 모르면 문의가 늘어난다 — 사유가 있으면 본문에 담아 알림으로 알린다.
        detail = user.blocked_reason or "커뮤니티 이용 규칙 위반"
        notifications.notify(db, user.id, "admin", "커뮤니티 이용이 제한되었어요",
                             f"사유: {detail}\n채팅·게시글·댓글 작성이 제한됩니다. 문의하기로 연락해 주세요.",
                             "/support", data={"from": admin.username})
    else:
        notifications.notify(db, user.id, "admin", "이용 제한이 해제되었어요",
                             "채팅·게시글·댓글을 다시 작성할 수 있어요.", "/board", data={"from": admin.username})
    db.commit()
    db.refresh(user)
    return {"ok": True, "member": member_view(user, _page_stats(db, [user.id]).get(user.id))}


def remove_member(db: Session, admin: User, user_id: int, *, reason: str = "") -> dict:
    """관리자 탈퇴 — 자기 탈퇴와 같은 정리 + 같은 이메일 재가입 차단. 되돌릴 수 없다."""
    user = _target(db, user_id, admin, action="delete")
    if db.exec(select(RunSession.id).where(RunSession.user_id == user.id, RunSession.status == "running")).first() is not None:
        raise HTTPException(status_code=409, detail="실행 중인 매크로를 먼저 종료해 주세요.")
    fingerprint = auth.email_fingerprint(user.email)
    close_account_rows(db, user)
    user.banned_email_hash = fingerprint  # 주소는 지우고 해시만 남긴다 — 재가입 판정에만 쓴다
    user.blocked_reason = str(reason or "").strip()[:REASON_MAX]
    db.add(user)
    db.commit()
    return {"ok": True}
