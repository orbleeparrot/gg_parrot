"""껄무새 게시판 — 로그인 계정만 글 작성, 댓글은 일회성 아이디/비밀번호.

- 글(BoardPost): 로그인 계정만 작성. 제목/본문 + 이미지(jpg·png) 1장(선택).
  삭제는 작성자 본인만.
- 댓글(BoardComment): 리더보드 채팅처럼 계정 없이 이름+비밀번호를 그때그때 입력해
  단다. 비밀번호는 '본인 삭제' 확인용으로만 저장(해시)하고 응답엔 절대 안 담는다.
- 저장은 raw 텍스트(React가 렌더 시 이스케이프). 이미지 바이트는 Postgres에 저장.

투자자문/실거래 아님 — 서비스 전반과 동일한 면책이 적용된다.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional

from sqlalchemy import func
from sqlmodel import select

from . import avatars
from .db import BoardComment, BoardImage, BoardPost, User, UserAvatar, get_session
from .security import hash_password, verify_password

MAX_TITLE = 120
MAX_BODY = 5000
MAX_COMMENT = 500
MAX_NAME = 24
PAGE_SIZE_DEFAULT = 10
PAGE_SIZE_MAX = 30
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB
SNIPPET_LEN = 140

# 간단 레이트리밋(채팅과 동일 방식): client_key당 창(window)에 N회.
_RATE_MAX = 8
_RATE_WINDOW = 30.0
_recent: dict[str, Deque[float]] = defaultdict(deque)


class RateLimited(Exception):
    """너무 빠른 연속 작성."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _kst_display(created_ms: int) -> str:
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) + timedelta(hours=9)
    return dt.strftime("%Y-%m-%d %H:%M")


def _check_rate(client_key: str) -> None:
    now = time.time()
    q = _recent[client_key]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_MAX:
        raise RateLimited("너무 빠르게 작성했어요. 잠시 후 다시 시도해 주세요.")
    q.append(now)


# ---------------------------------------------------------------------------
# 이미지 검증 — jpg/png만, 매직 바이트로 실제 형식을 확인(확장자/헤더 위조 방지).
# ---------------------------------------------------------------------------
MAX_IMAGES = 10  # 글 하나에 붙일 수 있는 사진 수


def validate_image(data: bytes, content_type: Optional[str]) -> tuple[bytes, str]:
    """(bytes, mime) 반환. 형식/크기 위반 시 ValueError."""
    if not data:
        raise ValueError("빈 이미지예요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("이미지는 2MB 이하만 올릴 수 있어요.")
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    raise ValueError("JPG 또는 PNG 이미지만 올릴 수 있어요.")


# ---------------------------------------------------------------------------
# 글
# ---------------------------------------------------------------------------
def _image_views(row: BoardPost, images: list[BoardImage]) -> list[dict]:
    """사진 목록 — 새 표(BoardImage) 순서대로, 옛 글의 한 장(BoardPost.image_data)은 맨 앞에."""
    views = []
    if row.image_data:
        views.append({"id": 0, "url": f"/api/board/posts/{row.id}/image"})
    for img in sorted(images, key=lambda i: (i.position, i.id or 0)):
        views.append({"id": img.id, "url": f"/api/board/posts/{row.id}/images/{img.id}"})
    return views


def _image_counts(db, post_ids: list[int]) -> dict[int, int]:
    if not post_ids:
        return {}
    rows = db.exec(
        select(BoardImage.post_id, func.count(BoardImage.id))
        .where(BoardImage.post_id.in_(post_ids)).group_by(BoardImage.post_id)
    ).all()
    return {int(pid): int(n) for pid, n in rows}


def _post_list_view(row: BoardPost, comment_count: int, avatar_url: str | None = None, image_count: int = 0) -> dict:
    body = row.body or ""
    return {
        "id": row.id,
        "title": row.title,
        "snippet": body[:SNIPPET_LEN] + ("…" if len(body) > SNIPPET_LEN else ""),
        "author_name": row.author_name,
        "author_user_id": row.author_user_id,
        "author_avatar_url": avatar_url,
        "has_image": bool(row.image_data) or image_count > 0,
        "comment_count": comment_count,
        "created_kst": _kst_display(row.created_ms),
        "created_ms": row.created_ms,
    }


def _post_detail_view(row: BoardPost, comments: list[dict], avatar_url: str | None = None,
                      images: list[BoardImage] | None = None) -> dict:
    views = _image_views(row, images or [])
    return {
        "id": row.id,
        "title": row.title,
        "body": row.body or "",
        "author_name": row.author_name,
        "author_user_id": row.author_user_id,
        "author_avatar_url": avatar_url,
        "has_image": bool(views),
        "image_url": views[0]["url"] if views else None,  # 옛 화면 호환 — 첫 장
        "images": views,
        "created_kst": _kst_display(row.created_ms),
        "created_ms": row.created_ms,
        "comments": comments,
    }


def create_post(user: User, title: str, body: str, images: list[tuple[bytes, str]] | None = None) -> dict:
    """글 작성. ``images`` 는 validate_image 를 거친 (bytes, mime) 목록 — 순서대로 붙는다."""
    title = (title or "").strip()
    if not title:
        raise ValueError("제목을 입력해 주세요.")
    title = title[:MAX_TITLE]
    body = (body or "").strip()[:MAX_BODY]
    images = list(images or [])
    if len(images) > MAX_IMAGES:
        raise ValueError(f"사진은 {MAX_IMAGES}장까지 붙일 수 있어요.")
    now = _now_utc()
    now_ms = int(now.timestamp() * 1000)
    row = BoardPost(
        author_user_id=user.id,
        author_name=user.username,
        title=title,
        body=body,
        image_mime="",
        image_data=None,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_ms=now_ms,
    )
    with get_session() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        stored = [
            BoardImage(post_id=row.id, position=index, image_mime=mime, image_data=data, created_ms=now_ms)
            for index, (data, mime) in enumerate(images)
        ]
        for img in stored:
            db.add(img)
        if stored:
            db.commit()
            for img in stored:
                db.refresh(img)
        return _post_detail_view(row, [], avatars.avatar_url(row.author_user_id, db=db), stored)


def list_posts(page: int = 1, size: int = PAGE_SIZE_DEFAULT) -> dict:
    """한 쪽의 목록을 **쿼리 한 번**으로 만든다.

    예전엔 전체 id 목록 → 글 행(사진 원본 바이트까지) → 댓글 행, 세 번을 오갔다. Render→Supabase 왕복이
    한 번에 100ms 넘게 걸리니 목록 하나에 0.4초가 더 붙었고, 사진이 있는 글은 2MB 를 목록마다 실어 날랐다.
    이제 필요한 열만 고르고, 댓글 수는 GROUP BY 하위 쿼리로, 전체 글 수는 창 함수로 같은 행에 실어 온다.
    """
    page = max(1, int(page or 1))
    size = max(1, min(int(size or PAGE_SIZE_DEFAULT), PAGE_SIZE_MAX))
    comment_counts = (
        select(BoardComment.post_id.label("post_id"), func.count(BoardComment.id).label("n"))
        .group_by(BoardComment.post_id)
        .subquery()
    )
    image_counts = (
        select(BoardImage.post_id.label("post_id"), func.count(BoardImage.id).label("n"))
        .group_by(BoardImage.post_id)
        .subquery()
    )
    statement = (
        select(
            BoardPost.id,
            BoardPost.title,
            BoardPost.body,
            BoardPost.author_name,
            BoardPost.author_user_id,
            UserAvatar.version.label("avatar_version"),
            BoardPost.created_ms,
            ((func.coalesce(func.length(BoardPost.image_data), 0) > 0) | (func.coalesce(image_counts.c.n, 0) > 0)).label("has_image"),
            func.coalesce(comment_counts.c.n, 0).label("comment_count"),
            func.count().over().label("total"),
        )
        .outerjoin(comment_counts, comment_counts.c.post_id == BoardPost.id)
        .outerjoin(image_counts, image_counts.c.post_id == BoardPost.id)
        .outerjoin(UserAvatar, UserAvatar.user_id == BoardPost.author_user_id)
        .order_by(BoardPost.created_ms.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    with get_session() as db:
        rows = db.exec(statement).all()
        if rows:
            total = int(rows[0].total)
        else:
            # 범위 밖의 쪽(예: 삭제 뒤 남은 주소)은 행이 없어 창 함수도 없다 — 그때만 한 번 더 센다.
            total = int(db.exec(select(func.count(BoardPost.id))).one())
        items = [
            {
                "id": r.id,
                "title": r.title,
                "snippet": (r.body or "")[:SNIPPET_LEN] + ("…" if len(r.body or "") > SNIPPET_LEN else ""),
                "author_name": r.author_name,
                "author_user_id": r.author_user_id,
                "author_avatar_url": avatars.public_url(r.author_user_id, r.avatar_version),
                "has_image": bool(r.has_image),
                "comment_count": int(r.comment_count or 0),
                "created_kst": _kst_display(r.created_ms),
                "created_ms": r.created_ms,
            }
            for r in rows
        ]
    pages = max(1, (total + size - 1) // size)
    return {
        "items": items,
        "page": page,
        "size": size,
        "total": total,
        "pages": pages,
        "disclaimer": "게시판 내용은 투자 조언이 아니며, 판단과 책임은 본인에게 있습니다.",
    }


def get_post(post_id: int) -> Optional[dict]:
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None:
            return None
        crows = db.exec(
            select(BoardComment).where(BoardComment.post_id == post_id).order_by(BoardComment.id.asc())
        ).all()
        comments = [_comment_view(c) for c in crows]
        images = db.exec(select(BoardImage).where(BoardImage.post_id == post_id)).all()
        return _post_detail_view(row, comments, avatars.avatar_url(row.author_user_id, db=db), images)


def get_image(post_id: int) -> Optional[tuple[bytes, str]]:
    """옛 주소(`/image`) — 옛 글의 한 장, 없으면 새 표의 첫 장(옛 화면·캐시된 번들 호환)."""
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None:
            return None
        if row.image_data:
            return row.image_data, (row.image_mime or "image/jpeg")
        first = db.exec(
            select(BoardImage).where(BoardImage.post_id == post_id).order_by(BoardImage.position.asc(), BoardImage.id.asc())
        ).first()
        if first is None or not first.image_data:
            return None
        return first.image_data, (first.image_mime or "image/jpeg")


def get_post_image(post_id: int, image_id: int) -> Optional[tuple[bytes, str]]:
    """새 표의 사진 한 장 — 글 id 와 짝이 맞을 때만."""
    with get_session() as db:
        img = db.get(BoardImage, image_id)
        if img is None or img.post_id != post_id or not img.image_data:
            return None
        return img.image_data, (img.image_mime or "image/jpeg")


def delete_post(post_id: int, user_id: int) -> bool:
    """작성자 본인만 삭제. 댓글도 함께 지운다."""
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None or row.author_user_id != user_id:
            return False
        for c in db.exec(select(BoardComment).where(BoardComment.post_id == post_id)).all():
            db.delete(c)
        for img in db.exec(select(BoardImage).where(BoardImage.post_id == post_id)).all():
            db.delete(img)
        db.delete(row)
        db.commit()
        return True


def my_posts(user_id: int, limit: int = 50, db=None) -> list[dict]:
    from contextlib import nullcontext

    session_scope = nullcontext(db) if db is not None else get_session()
    with session_scope as db:
        rows = db.exec(
            select(BoardPost)
            .where(BoardPost.author_user_id == user_id)
            .order_by(BoardPost.created_ms.desc())
            .limit(limit)
        ).all()
        ids = [r.id for r in rows]
        counts: dict[int, int] = {}
        if ids:
            for c in db.exec(select(BoardComment.post_id).where(BoardComment.post_id.in_(ids))).all():
                pid = c if isinstance(c, int) else c[0]
                counts[pid] = counts.get(pid, 0) + 1
        avatar_url = avatars.avatar_url(user_id, db=db) if rows else None
        image_counts = _image_counts(db, ids)
        return [_post_list_view(r, counts.get(r.id, 0), avatar_url, image_count=image_counts.get(r.id, 0)) for r in rows]


# ---------------------------------------------------------------------------
# 댓글 (계정 없이 이름+비밀번호)
# ---------------------------------------------------------------------------
def _comment_view(row: BoardComment) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "username": row.username,
        # Anonymous comments have no verified account link, even when names match.
        "author_avatar_url": None,
        "text": row.text,
        "created_kst": _kst_display(row.created_ms),
    }


def add_comment(post_id: int, username: str, password: str, text: str, client_key: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("댓글 내용을 입력해 주세요.")
    name = (username or "").strip()[:MAX_NAME]
    if not name:
        raise ValueError("이름(아이디)을 입력해 주세요.")
    if not (password or "").strip():
        raise ValueError("비밀번호를 입력해 주세요.")
    _check_rate(client_key)
    with get_session() as db:
        if db.get(BoardPost, post_id) is None:
            raise LookupError("글을 찾을 수 없어요.")
        now = _now_utc()
        row = BoardComment(
            post_id=post_id,
            username=name,
            password_hash=hash_password(password),
            text=text[:MAX_COMMENT],
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            created_ms=int(now.timestamp() * 1000),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _comment_view(row)


def delete_comment(comment_id: int, password: str) -> bool:
    """작성 때 넣은 비밀번호가 맞아야 삭제."""
    with get_session() as db:
        row = db.get(BoardComment, comment_id)
        if row is None or not row.password_hash:
            return False
        if not verify_password(password or "", row.password_hash):
            return False
        db.delete(row)
        db.commit()
        return True
