"""Authenticated profile edits, committed together with the selected photo."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import delete, text as sql_text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import auth, avatars
from .db import (BoardComment, BoardPost, ChatMessage, ChatReadState, DailyQuestClaim,
                 LeaderboardEntry, NotificationMessage, NotificationReceipt, RunnerKey,
                 RunnerLaunchTicket, RunSession, RunSessionEvent, User, UserMacro, get_session)
from . import points
from .security import hash_password, verify_password

MAX_BIO_LENGTH = 160


def _lock_account(db: Session, user_id: int) -> User:
    if db.get_bind().dialect.name == "sqlite":
        db.exec(sql_text("BEGIN IMMEDIATE"))
    user = db.exec(select(User).where(User.id == user_id).with_for_update()).first()
    if user is None or user.is_deleted:
        raise auth.AuthError(401, "계정을 찾을 수 없어요.")
    return user


def update_profile(
    user_id: int, username: str, bio: str, *,
    image_data: bytes | None = None, remove_avatar: bool = False,
) -> dict:
    username = auth.validate_username(username)
    bio = (bio or "").strip()
    if len(bio) > MAX_BIO_LENGTH:
        raise auth.AuthError(400, "소개는 160자 이하로 입력해 주세요.")
    if image_data is not None and remove_avatar:
        raise auth.AuthError(400, "사진 변경과 삭제 중 하나만 선택해 주세요.")

    with get_session() as db:
        user = _lock_account(db, user_id)
        occupied = db.exec(select(User.id).where(User.username == username, User.id != user_id)).first()
        if occupied is not None:
            raise auth.AuthError(409, "이미 사용 중인 아이디예요.")
        renamed = user.username != username
        user.username = username
        user.bio = bio
        db.add(user)
        try:
            # Flush first so competing names are rejected before any photo changes.
            db.flush()
            if image_data is not None or remove_avatar:
                avatars.set_avatar_in_session(db, user_id, image_data)
            if renamed:
                db.exec(update(ChatMessage).where(ChatMessage.user_id == user_id).values(username=username))
                db.exec(update(BoardPost).where(BoardPost.author_user_id == user_id).values(author_name=username))
                db.exec(update(BoardComment).where(BoardComment.author_user_id == user_id).values(username=username))
                db.exec(update(LeaderboardEntry).where(LeaderboardEntry.owner_user_id == user_id).values(
                    username=username, nickname=username,
                ))
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise auth.AuthError(409, "이미 사용 중인 아이디예요.") from exc
        db.refresh(user)
        return {"user": auth.user_view(user, db=db)}


def change_password(user_id: int, current_password: str, new_password: str) -> dict:
    auth.validate_new_password(new_password)
    if not current_password or len(current_password) > auth.MAX_PASSWORD_LENGTH:
        raise auth.AuthError(400, "현재 비밀번호를 확인해 주세요.")
    with get_session() as db:
        user = _lock_account(db, user_id)
        if not user.password_hash:
            raise auth.AuthError(403, "Google로 가입한 계정은 비밀번호를 변경할 수 없어요.")
        if not verify_password(current_password, user.password_hash):
            raise auth.AuthError(400, "현재 비밀번호가 일치하지 않아요.")
        user.password_hash = hash_password(new_password)
        user.auth_version += 1
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"token": auth.make_token(user.id, user.auth_version), "user": auth.user_view(user, db=db)}


def delete_account(user_id: int, confirmation: str, password: str = "", credential: str = "") -> dict:
    if confirmation != "탈퇴":
        raise auth.AuthError(400, "확인란에 '탈퇴'를 입력해 주세요.")
    google_identity = auth._verify_google_credential(credential) if credential else None
    try:
        google_issued_at = float(google_identity.get("iat", 0)) if google_identity else 0
    except (TypeError, ValueError):
        google_issued_at = 0
    with get_session() as db:
        user = _lock_account(db, user_id)
        if user.password_hash:
            if not password or len(password) > auth.MAX_PASSWORD_LENGTH or not verify_password(password, user.password_hash):
                raise auth.AuthError(400, "현재 비밀번호가 일치하지 않아요.")
        else:
            if (not google_identity or str(google_identity.get("email", "")).lower() != user.email
                    or not 0 <= time.time() - google_issued_at <= 600):
                raise auth.AuthError(403, "가입한 Google 계정으로 다시 본인 확인을 해주세요.")
        # Revoking the runner key must never silently remove control of a live run.
        if db.exec(select(RunSession.id).where(RunSession.user_id == user_id, RunSession.status == "running")).first() is not None:
            raise auth.AuthError(409, "실행 중인 매크로를 먼저 종료해 주세요. 연결이 끊긴 실행 기록은 내 에이전트에서 정리할 수 있어요.")
        close_account_rows(db, user)
        db.commit()
    return {"ok": True}


def close_account_rows(db: Session, user: User) -> None:
    """계정을 닫는 정리 — 본인 탈퇴와 관리자 탈퇴(members.remove_member)가 같은 절차를 쓴다. 커밋은 호출자가 한다.

    남의 구매·원장이 가리키는 소유자 행은 지우지 않고 식별할 수 없게 바꿔 둔다. 재가입 차단은 여기서 하지 않는다
    — 스스로 떠난 회원은 돌아올 수 있어야 하고, 관리자 탈퇴만 members.py 에서 이메일 해시를 남긴다.
    """
    user_id = user.id
    avatars.set_avatar_in_session(db, user_id, None)
    for model in (ChatReadState, RunnerLaunchTicket, RunnerKey, RunSessionEvent,
                  RunSession, DailyQuestClaim, UserMacro, NotificationMessage, NotificationReceipt):
        db.exec(delete(model).where(model.user_id == user_id))
    db.exec(update(ChatMessage).where(ChatMessage.user_id == user_id).values(username="탈퇴한 회원"))
    db.exec(update(BoardPost).where(BoardPost.author_user_id == user_id).values(author_name="탈퇴한 회원"))
    db.exec(update(BoardComment).where(BoardComment.author_user_id == user_id).values(username="탈퇴한 회원"))
    db.exec(update(LeaderboardEntry).where(LeaderboardEntry.owner_user_id == user_id).values(username="탈퇴한 회원", nickname="탈퇴한 회원"))
    if user.points_balance:
        points.apply(db, user, -user.points_balance, "account_closed")
    user.username = f"탈퇴회원_{user_id}_{secrets.token_hex(4)}"
    user.email = f"deleted-{user_id}-{secrets.token_hex(8)}@account.invalid"
    user.bio = ""
    user.password_hash = ""
    user.auth_version += 1
    user.is_blocked = False  # 탈퇴하면 차단 상태는 의미가 없다
    user.is_deleted = True
    # 탈퇴한 날을 남긴다(관리자 가입 표의 날짜별 탈퇴 수). signup_method 는 지우지 않는다 — 해시를 비운 뒤에도 가입 방법은 사실이다.
    user.deleted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.add(user)
