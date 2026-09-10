"""껄무새 게시판 — 로그인 계정만 글 작성, 댓글은 일회성 아이디/비밀번호.

- 글(BoardPost): 로그인 계정만 작성. 제목/본문 + 이미지(jpg·png) 1장(선택).
  삭제는 작성자 본인만.
- 댓글(BoardComment): 리더보드 채팅처럼 계정 없이 이름+비밀번호를 그때그때 입력해
  단다. 비밀번호는 '본인 삭제' 확인용으로만 저장(해시)하고 응답엔 절대 안 담는다.
- 저장은 raw 텍스트(React가 렌더 시 이스케이프). 이미지 바이트는 Postgres에 저장.

투자자문/실거래 아님 — 서비스 전반과 동일한 면책이 적용된다.
"""
from __future__ import annotations

import html as html_mod
import re

import nh3

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional

from sqlalchemy import func, or_
from sqlmodel import select

from . import avatars
from .db import BoardComment, BoardImage, BoardPost, BoardPostVote, User, UserAvatar, get_session
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
# 본문 안 사진 자리 — `[사진1]` 처럼 첨부 순서(1부터)를 가리킨다. 화면이 이 자리에 사진을 끼워 넣는다.
IMAGE_MARK = re.compile(r"\[사진\s*(\d+)\]")


def _snippet(body: str, body_format: str = "") -> str:
    if body_format == "html":
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", body or ""))
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = IMAGE_MARK.sub("", body or "").strip()
    return text[:SNIPPET_LEN] + ("…" if len(text) > SNIPPET_LEN else "")


# ---------------------------------------------------------------------------
# 본문 HTML — 편집기(TipTap)가 만든 HTML 을 저장 전에 정제한다. 허용 목록 밖은 전부 버린다.
# 사진은 우리 서버 주소만(외부 이미지·추적 픽셀 금지). 새 사진은 `data-key="new:N"` 자리로 오고
# 저장할 때 실제 주소로 바꾼다.
# ---------------------------------------------------------------------------
_HTML_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "strike", "h2", "h3", "h4", "ul", "ol", "li",
              "blockquote", "a", "img", "span", "mark", "code", "pre", "hr"}
_STYLED = {"p", "h2", "h3", "h4", "li", "blockquote", "span", "img"}
_HTML_ATTRS = {
    "a": {"href", "target"},  # rel 은 nh3 가 link_rel 로 붙인다
    "img": {"src", "alt", "width", "height", "style", "data-align"},
    **{tag: {"style"} for tag in _STYLED if tag != "img"},
}
_STYLE_RULES = {
    "text-align": re.compile(r"^(left|center|right|justify)$"),
    "color": re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d.,\s%]+\)|[a-zA-Z]{3,20})$"),
    "background-color": re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d.,\s%]+\)|[a-zA-Z]{3,20})$"),
    "font-size": re.compile(r"^\d{1,3}(\.\d+)?(px|em|rem|%)$"),
    "width": re.compile(r"^\d{1,4}(px|%)$"),
    "height": re.compile(r"^(auto|\d{1,4}(px|%))$"),
}
_OWN_IMAGE_SRC = re.compile(r"^/api/board/posts/(\d+)/(image|images/(\d+))$")


def _clean_style(value: str) -> str:
    kept = []
    for part in (value or "").split(";"):
        if ":" not in part:
            continue
        prop, _, val = part.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        rule = _STYLE_RULES.get(prop)
        if rule and rule.match(val):
            kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def _html_attribute_filter(tag: str, attr: str, value: str):
    if attr == "style":
        cleaned = _clean_style(value)
        return cleaned or None
    if tag == "img" and attr == "src":
        return value if _OWN_IMAGE_SRC.match(value or "") else None
    if tag == "img" and attr == "data-align":
        return value if value in {"left", "center", "right"} else None
    if tag == "img" and attr in {"width", "height"}:
        return value if re.match(r"^\d{1,4}$", value or "") else None
    if tag == "a" and attr == "target":
        return "_blank"
    return value


def sanitize_body_html(raw: str) -> str:
    cleaned = nh3.clean(
        raw or "",
        tags=_HTML_TAGS,
        attributes=_HTML_ATTRS,
        attribute_filter=_html_attribute_filter,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    # 주소가 지워진 사진(외부 주소·자리 못 찾음)은 통째로 뺀다.
    cleaned = re.sub(r"<img(?![^>]*\ssrc=)[^>]*>", "", cleaned)
    return cleaned.strip()


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""")


def _img_attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(5) or "")
            for m in _ATTR.finditer(tag[4:])}


def _resolve_new_images(raw: str, post_id: int, stored: list) -> str:
    """`data-key="new:N"` 사진에 저장된 N번째 새 사진의 주소를 붙인다(N 은 올린 순서, 0부터)."""
    def swap(match: re.Match) -> str:
        attrs = _img_attrs(match.group(0))
        key = attrs.get("data-key", "")
        if key.startswith("new:"):
            try:
                img = stored[int(key[4:])]
            except (ValueError, IndexError):
                return ""
            attrs["src"] = f"/api/board/posts/{post_id}/images/{img.id}"
        attrs.pop("data-key", None)
        return "<img " + " ".join(f'{k}="{html_mod.escape(v, quote=True)}"' for k, v in attrs.items()) + ">"
    return _IMG_TAG.sub(swap, raw or "")


def _referenced_image_ids(body_html: str, post_id: int) -> set[int]:
    """본문이 가리키는 이 글의 사진 id 들(옛 한 장은 0)."""
    ids: set[int] = set()
    for match in _IMG_TAG.finditer(body_html or ""):
        src = _img_attrs(match.group(0)).get("src", "")
        m = _OWN_IMAGE_SRC.match(src)
        if m and int(m.group(1)) == post_id:
            ids.add(int(m.group(3)) if m.group(3) else 0)
    return ids


def _legacy_html(body: str, views: list[dict]) -> str:
    """옛 글(글자 + [사진n]) 을 화면이 같은 방식으로 그리게 HTML 로 바꾼다."""
    parts: list[str] = []
    text = body or ""
    last = 0
    def paragraphs(chunk: str) -> None:
        for para in re.split(r"\n{2,}", chunk.strip("\n")):
            if para.strip():
                parts.append("<p>" + html_mod.escape(para).replace("\n", "<br>") + "</p>")
    used: set[int] = set()
    for match in IMAGE_MARK.finditer(text):
        index = int(match.group(1))
        if not (1 <= index <= len(views)):
            continue
        paragraphs(text[last:match.start()])
        parts.append(f'<img src="{html_mod.escape(views[index - 1]["url"], quote=True)}" alt="">')
        used.add(index)
        last = match.end()
    paragraphs(text[last:])
    for index, view in enumerate(views, start=1):
        if index not in used:
            parts.append(f'<img src="{html_mod.escape(view["url"], quote=True)}" alt="">')
    return "".join(parts)


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
        "snippet": _snippet(body, row.body_format),
        "author_name": row.author_name,
        "author_user_id": row.author_user_id,
        "author_avatar_url": avatar_url,
        "has_image": bool(row.image_data) or image_count > 0,
        "comment_count": comment_count,
        "views": int(row.views or 0),
        "likes": int(row.likes or 0),
        "dislikes": int(row.dislikes or 0),
        "created_kst": _kst_display(row.created_ms),
        "created_ms": row.created_ms,
    }


def _post_detail_view(row: BoardPost, comments: list[dict], avatar_url: str | None = None,
                      images: list[BoardImage] | None = None, my_vote: int = 0) -> dict:
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
        "views": int(row.views or 0),
        "likes": int(row.likes or 0),
        "dislikes": int(row.dislikes or 0),
        "my_vote": my_vote,
        "body_format": row.body_format or "text",
        "body_html": (row.body or "") if row.body_format == "html" else _legacy_html(row.body or "", views),
        "created_kst": _kst_display(row.created_ms),
        "created_ms": row.created_ms,
        "comments": comments,
    }


def create_post(user: User, title: str, body: str, images: list[tuple[bytes, str]] | None = None,
                body_format: str = "text") -> dict:
    """글 작성. ``images`` 는 validate_image 를 거친 (bytes, mime) 목록 — 순서대로 붙는다.

    ``body_format="html"`` 이면 본문은 편집기 HTML — 새 사진 자리(`data-key="new:N"`)에 주소를 붙인 뒤 정제해 저장한다.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("제목을 입력해 주세요.")
    title = title[:MAX_TITLE]
    is_html = body_format == "html"
    body = (body or "").strip()[:MAX_BODY * (4 if is_html else 1)]
    images = list(images or [])
    if len(images) > MAX_IMAGES:
        raise ValueError(f"사진은 {MAX_IMAGES}장까지 붙일 수 있어요.")
    now = _now_utc()
    now_ms = int(now.timestamp() * 1000)
    row = BoardPost(
        author_user_id=user.id,
        author_name=user.username,
        title=title,
        body="" if is_html else body,
        body_format="html" if is_html else "",
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
        if is_html:
            row.body = sanitize_body_html(_resolve_new_images(body, row.id, stored))
            # 본문이 가리키지 않는 새 사진은 남기지 않는다.
            referenced = _referenced_image_ids(row.body, row.id)
            kept = []
            for img in stored:
                if img.id in referenced:
                    kept.append(img)
                else:
                    db.delete(img)
            stored = kept
            db.add(row)
            db.commit()
            db.refresh(row)
        return _post_detail_view(row, [], avatars.avatar_url(row.author_user_id, db=db), stored)


SORTS = {"new", "likes", "views", "comments"}
FILTERS = {"all", "image", "mine"}


def list_posts(page: int = 1, size: int = PAGE_SIZE_DEFAULT, sort: str = "new", q: str = "",
               filter: str = "all", viewer_id: int | None = None) -> dict:
    """한 쪽의 목록을 **쿼리 한 번**으로 만든다.

    예전엔 전체 id 목록 → 글 행(사진 원본 바이트까지) → 댓글 행, 세 번을 오갔다. Render→Supabase 왕복이
    한 번에 100ms 넘게 걸리니 목록 하나에 0.4초가 더 붙었고, 사진이 있는 글은 2MB 를 목록마다 실어 날랐다.
    이제 필요한 열만 고르고, 댓글 수는 GROUP BY 하위 쿼리로, 전체 글 수는 창 함수로 같은 행에 실어 온다.
    """
    page = max(1, int(page or 1))
    size = max(1, min(int(size or PAGE_SIZE_DEFAULT), PAGE_SIZE_MAX))
    sort = sort if sort in SORTS else "new"
    filter = filter if filter in FILTERS else "all"
    q = (q or "").strip()[:80]
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
            BoardPost.body_format,
            BoardPost.author_name,
            BoardPost.author_user_id,
            UserAvatar.version.label("avatar_version"),
            BoardPost.created_ms,
            BoardPost.views,
            BoardPost.likes,
            BoardPost.dislikes,
            ((func.coalesce(func.length(BoardPost.image_data), 0) > 0) | (func.coalesce(image_counts.c.n, 0) > 0)).label("has_image"),
            func.coalesce(comment_counts.c.n, 0).label("comment_count"),
            func.count().over().label("total"),
        )
        .outerjoin(comment_counts, comment_counts.c.post_id == BoardPost.id)
        .outerjoin(image_counts, image_counts.c.post_id == BoardPost.id)
        .outerjoin(UserAvatar, UserAvatar.user_id == BoardPost.author_user_id)
    )
    if q:
        needle = f"%{q}%"
        statement = statement.where(or_(BoardPost.title.ilike(needle), BoardPost.body.ilike(needle)))
    if filter == "image":
        statement = statement.where((func.coalesce(func.length(BoardPost.image_data), 0) > 0) | (func.coalesce(image_counts.c.n, 0) > 0))
    elif filter == "mine":
        statement = statement.where(BoardPost.author_user_id == (viewer_id if viewer_id is not None else -1))
    order = {
        "new": (BoardPost.created_ms.desc(),),
        "likes": (BoardPost.likes.desc(), BoardPost.created_ms.desc()),
        "views": (BoardPost.views.desc(), BoardPost.created_ms.desc()),
        "comments": (func.coalesce(comment_counts.c.n, 0).desc(), BoardPost.created_ms.desc()),
    }[sort]
    statement = statement.order_by(*order).offset((page - 1) * size).limit(size)
    with get_session() as db:
        rows = db.exec(statement).all()
        if rows:
            total = int(rows[0].total)
        else:
            count_stmt = select(func.count(BoardPost.id)).outerjoin(image_counts, image_counts.c.post_id == BoardPost.id)
            if q:
                needle = f"%{q}%"
                count_stmt = count_stmt.where(or_(BoardPost.title.ilike(needle), BoardPost.body.ilike(needle)))
            if filter == "image":
                count_stmt = count_stmt.where((func.coalesce(func.length(BoardPost.image_data), 0) > 0) | (func.coalesce(image_counts.c.n, 0) > 0))
            elif filter == "mine":
                count_stmt = count_stmt.where(BoardPost.author_user_id == (viewer_id if viewer_id is not None else -1))
            total = int(db.exec(count_stmt).one() or 0)
        items = [
            {
                "id": r.id,
                "title": r.title,
                "snippet": _snippet(r.body or "", r.body_format or ""),
                "author_name": r.author_name,
                "author_user_id": r.author_user_id,
                "author_avatar_url": avatars.public_url(r.author_user_id, r.avatar_version),
                "has_image": bool(r.has_image),
                "views": int(r.views or 0),
                "likes": int(r.likes or 0),
                "dislikes": int(r.dislikes or 0),
                "comment_count": int(r.comment_count or 0),
                "created_kst": _kst_display(r.created_ms),
                "created_ms": r.created_ms,
            }
            for r in rows
        ]
    pages = max(1, (total + size - 1) // size)
    return {
        "items": items,
        "sort": sort,
        "q": q,
        "filter": filter,
        "page": page,
        "size": size,
        "total": total,
        "pages": pages,
        "disclaimer": "게시판 내용은 투자 조언이 아니며, 판단과 책임은 본인에게 있습니다.",
    }


# 조회수 — 같은 방문자(키)는 30분에 한 번만 센다. 프로세스 메모리라 재시작하면 잊지만 그 정도는 감수한다.
_VIEW_WINDOW_SECONDS = 30 * 60
_seen_views: dict[tuple[str, int], float] = {}


def _count_view(db, row: BoardPost, view_key: str | None) -> None:
    if not view_key:
        return
    now = time.time()
    key = (view_key, int(row.id))
    last = _seen_views.get(key)
    if last is not None and now - last < _VIEW_WINDOW_SECONDS:
        return
    _seen_views[key] = now
    if len(_seen_views) > 20000:
        for stale in [k for k, ts in _seen_views.items() if now - ts >= _VIEW_WINDOW_SECONDS][:5000]:
            _seen_views.pop(stale, None)
    row.views = int(row.views or 0) + 1
    db.add(row)
    db.commit()
    db.refresh(row)


def get_post(post_id: int, viewer_id: int | None = None, view_key: str | None = None) -> Optional[dict]:
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None:
            return None
        _count_view(db, row, view_key)
        my_vote = 0
        if viewer_id is not None:
            found = db.exec(select(BoardPostVote).where(BoardPostVote.post_id == post_id, BoardPostVote.user_id == viewer_id)).first()
            my_vote = int(found.value) if found else 0
        crows = db.exec(
            select(BoardComment).where(BoardComment.post_id == post_id).order_by(BoardComment.id.asc())
        ).all()
        comments = [_comment_view(c) for c in crows]
        images = db.exec(select(BoardImage).where(BoardImage.post_id == post_id)).all()
        return _post_detail_view(row, comments, avatars.avatar_url(row.author_user_id, db=db), images, my_vote=my_vote)


def vote_post(post_id: int, user_id: int, value: int) -> Optional[dict]:
    """추천(+1)/비추천(-1). 같은 표를 다시 누르면 취소. 글이 없으면 None."""
    value = 1 if value > 0 else -1
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None:
            return None
        existing = db.exec(select(BoardPostVote).where(BoardPostVote.post_id == post_id, BoardPostVote.user_id == user_id)).first()
        if existing is None:
            db.add(BoardPostVote(post_id=post_id, user_id=user_id, value=value, created_ms=int(_now_utc().timestamp() * 1000)))
        elif existing.value == value:
            db.delete(existing)
        else:
            existing.value = value
            db.add(existing)
        db.commit()
        votes = db.exec(select(BoardPostVote.value).where(BoardPostVote.post_id == post_id)).all()
        values = [int(v if isinstance(v, int) else v[0]) for v in votes]
        row.likes = sum(1 for v in values if v > 0)
        row.dislikes = sum(1 for v in values if v < 0)
        db.add(row)
        db.commit()
        mine = db.exec(select(BoardPostVote).where(BoardPostVote.post_id == post_id, BoardPostVote.user_id == user_id)).first()
        return {"post_id": post_id, "likes": row.likes, "dislikes": row.dislikes, "my_vote": int(mine.value) if mine else 0}


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


def update_post(post_id: int, user: User, title: str, body: str,
                keep_image_ids: list[int], new_images: list[tuple[bytes, str]] | None = None,
                body_format: str = "text") -> Optional[dict]:
    """작성자 본인만 수정. 새 사진은 뒤에 순서대로 붙인다.

    옛 형식은 남길 사진 id 목록(``keep_image_ids``)에 없는 사진을 지운다. HTML 형식은 **본문이 가리키는 사진이 곧 남길 사진**이라
    목록을 보지 않는다. 반환: 수정된 상세 뷰. 글이 없으면 None, 남의 글이면 PermissionError.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("제목을 입력해 주세요.")
    title = title[:MAX_TITLE]
    is_html = body_format == "html"
    body = (body or "").strip()[:MAX_BODY * (4 if is_html else 1)]
    new_images = list(new_images or [])
    with get_session() as db:
        row = db.get(BoardPost, post_id)
        if row is None:
            return None
        if row.author_user_id != user.id:
            raise PermissionError("본인이 쓴 글만 고칠 수 있어요.")
        existing = sorted(db.exec(select(BoardImage).where(BoardImage.post_id == post_id)).all(),
                          key=lambda i: (i.position, i.id or 0))
        if len(existing) + (1 if row.image_data else 0) + len(new_images) > MAX_IMAGES:
            raise ValueError(f"사진은 {MAX_IMAGES}장까지 붙일 수 있어요.")
        now_ms = int(_now_utc().timestamp() * 1000)
        added = [
            BoardImage(post_id=post_id, position=len(existing) + index, image_mime=mime, image_data=data, created_ms=now_ms)
            for index, (data, mime) in enumerate(new_images)
        ]
        for img in added:
            db.add(img)
        if added:
            db.commit()
            for img in added:
                db.refresh(img)
        if is_html:
            body = sanitize_body_html(_resolve_new_images(body, post_id, added))
            keep = _referenced_image_ids(body, post_id)
        else:
            keep = {int(i) for i in keep_image_ids} | {img.id for img in added}
        if row.image_data and 0 not in keep:
            row.image_data = None
            row.image_mime = ""
        kept = []
        for img in [*existing, *added]:
            if img.id in keep:
                kept.append(img)
            else:
                db.delete(img)
        for index, img in enumerate(kept):
            img.position = index
            db.add(img)
        row.title = title
        row.body = body
        row.body_format = "html" if is_html else ""
        db.add(row)
        db.commit()
        db.refresh(row)
        for img in kept:
            db.refresh(img)
        return _post_detail_view(row, [_comment_view(c) for c in db.exec(
            select(BoardComment).where(BoardComment.post_id == post_id).order_by(BoardComment.id.asc())).all()],
            avatars.avatar_url(row.author_user_id, db=db), kept)


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
        for v in db.exec(select(BoardPostVote).where(BoardPostVote.post_id == post_id)).all():
            db.delete(v)
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
        "created_ms": int(row.created_ms),
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
