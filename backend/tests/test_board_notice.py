"""[공지] — 관리자만 올리고, 일반 목록에서는 빠진 채 모든 쪽·정렬·검색 맨 위에 고정된다."""
from datetime import datetime

import pytest

from app import board
from app.db import User, get_session


def _user(admin=False):
    stamp = str(datetime.now().timestamp()).replace(".", "")
    with get_session() as db:
        user = User(email=f"notice-{stamp}@example.invalid", username=f"notice{stamp[-8:]}", password_hash="x",
                    created_at="2026-10-01T00:00:00Z", is_admin=admin)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_only_admins_can_post_or_toggle_notices():
    member, admin = _user(), _user(admin=True)
    with pytest.raises(PermissionError):
        board.create_post(member, "공지인 척", "본문", is_notice=True)
    post = board.create_post(member, "일반 글", "본문")
    with pytest.raises(PermissionError):
        board.update_post(post["id"], member, "일반 글", "본문", [], is_notice=True)
    notice = board.create_post(admin, "점검 안내", "본문", is_notice=True)
    assert notice["is_notice"] is True
    # 관리자는 자기 글을 공지에서 내릴 수 있다
    lowered = board.update_post(notice["id"], admin, "점검 안내", "본문", [], is_notice=False)
    assert lowered["is_notice"] is False


def test_notices_are_pinned_on_every_page_sort_and_search_and_not_duplicated():
    # 다른 테스트의 글이 남아 있어도 되게 — 이 테스트의 글만 고유한 낱말로 찾는다(전역 삭제는 id 재사용으로 남의 사진을 붙인다).
    member, admin = _user(), _user(admin=True)
    tag = "핀고정" + str(datetime.now().timestamp()).replace(".", "")[-6:]
    for index in range(5):
        board.create_post(member, f"{tag} 일반 {index}", "본문")
    notice = board.create_post(admin, f"{tag} 필독 공지", "본문", is_notice=True)
    for page in (1, 2):
        for sort in ("new", "likes", "views"):
            data = board.list_posts(page=page, size=2, sort=sort)
            assert notice["id"] in [n["id"] for n in data["notices"]]
            assert all(not item["is_notice"] for item in data["items"])
            assert notice["id"] not in [i["id"] for i in data["items"]]
    searched = board.list_posts(q=f"{tag} 일반 3")
    assert notice["id"] in [n["id"] for n in searched["notices"]]
    assert [i["title"] for i in searched["items"]] == [f"{tag} 일반 3"]
    assert board.list_posts(q=tag, size=50)["total"] == 5  # 공지는 일반 글 수에 넣지 않는다
