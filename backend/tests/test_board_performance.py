"""Query budgets and transaction regressions; conftest isolates all writes."""
from contextlib import contextmanager
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app import auth, board, db
from app.main import app


@contextmanager
def measured():
    result = {"queries": [], "checkouts": 0, "image_bytes": 0}
    def query(_conn, _cursor, statement, *_args):
        result["queries"].append(statement)
    def checkout(*_args):
        result["checkouts"] += 1
    def loaded(_session, row):
        if isinstance(row, (db.BoardImage, db.BoardPost, db.UserAvatar)):
            result["image_bytes"] += len(row.__dict__.get("image_data") or b"")
    event.listen(db._engine, "before_cursor_execute", query)
    event.listen(db._engine, "checkout", checkout)
    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        yield result
    finally:
        event.remove(db._engine, "before_cursor_execute", query)
        event.remove(db._engine, "checkout", checkout)
        event.remove(Session, "loaded_as_persistent", loaded)


def seed(authors=0):
    suffix = secrets.token_hex(5)
    with db.get_session() as session:
        users = [db.User(email=f"{suffix}-{i}@example.invalid", username=f"audit-{suffix}-{i}",
                         password_hash="", created_at="2026-09-10T00:00:00Z") for i in range(authors + 1)]
        session.add_all(users); session.flush()
        user_id = users[0].id
        token = auth.make_token(user_id, users[0].auth_version)
        post = db.BoardPost(author_user_id=user_id, author_name=users[0].username, title=suffix, body="body",
                            image_data=b"legacy-image", created_at="2026-09-10T00:00:00Z", created_ms=1)
        session.add(post); session.flush()
        pid = post.id
        session.add(db.BoardImage(post_id=pid, position=0, image_mime="image/png", image_data=b"x" * 2_000_000, created_ms=1))
        for user in users:
            session.add(db.UserAvatar(user_id=user.id, version="v1", image_data=b"photo"))
        for user in users[1:]:
            session.add(db.BoardComment(post_id=pid, author_user_id=user.id, username=user.username, text="comment",
                                        created_at="2026-09-10T00:00:00Z", created_ms=1))
        session.commit()
    return pid, user_id, token


def test_detail_budget_does_not_grow_with_comment_authors_or_image_bytes():
    pid, _, _ = seed(20)
    with measured() as cost:
        view = board.get_post(pid)
    assert len(cost["queries"]) <= 3
    assert cost["image_bytes"] == 0
    assert len(view["comments"]) == 20
    assert all(c["author_avatar_url"].endswith("?v=v1") for c in view["comments"])
    assert len(view["images"]) == 2 and view["images"][0]["id"] == 0


def test_list_does_not_query_auth_and_comment_reuses_auth_connection():
    pid, _, token = seed()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    with measured() as cost:
        response = client.get("/api/board/posts", headers=headers)
    assert response.status_code == 200
    assert len(cost["queries"]) == 1 and cost["checkouts"] == 1
    with measured() as cost:
        response = client.post(f"/api/board/posts/{pid}/comments", headers=headers, json={"text": "new"})
    assert response.status_code == 200, response.text
    assert len(cost["queries"]) <= 3 and cost["checkouts"] == 1
    assert cost["image_bytes"] == 0
    comment = response.json()["comment"]
    assert comment["author_avatar_url"].endswith("?v=v1")
    with measured() as cost:
        response = client.post(f"/api/board/posts/{pid}/comments", headers=headers,
                               json={"text": "reply", "parent_id": comment["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["comment"]["parent_id"] == comment["id"]
    assert len(cost["queries"]) <= 3 and cost["checkouts"] == 1


def test_failed_view_commit_can_retry_and_does_not_refresh_post(monkeypatch):
    pid, _, _ = seed()
    key = "failed-view-" + secrets.token_hex(5)
    with db.get_session() as session:
        def fail():
            raise RuntimeError("fixture commit failure")
        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError, match="fixture commit failure"):
            board.get_post(pid, view_key=key, db=session)
    assert (key, pid) not in board._seen_views
    with measured() as cost:
        view = board.get_post(pid, view_key=key)
    assert view["views"] == 1
    assert len(cost["queries"]) <= 4 and cost["checkouts"] == 1
    assert cost["image_bytes"] == 0
    assert board.get_post(pid, view_key=key)["views"] == 1


def test_create_failure_does_not_leave_partial_post_or_images(monkeypatch):
    _, uid, _ = seed()
    user = auth.get_user_by_id(uid)
    title = "rollback-" + secrets.token_hex(5)
    def fail(*_args):
        raise ValueError("fixture sanitizer failure")
    monkeypatch.setattr(board, "sanitize_body_html", fail)
    with pytest.raises(ValueError, match="fixture sanitizer failure"):
        board.create_post(user, title, '<p>text</p>', [(b"image", "image/png")], body_format="html")
    with db.get_session() as session:
        assert session.exec(select(db.BoardPost.id).where(db.BoardPost.title == title)).first() is None
