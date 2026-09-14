"""Profile/author cache consistency. conftest forces a temporary SQLite DB."""
import secrets

from sqlalchemy import event

from app import board, db, profile
from app.security import hash_password

PASSWORD = "test-password-123"


def seed():
    suffix = secrets.token_hex(5)
    with db.get_session() as session:
        user = db.User(email=f"identity-{suffix}@example.invalid", username=f"old_{suffix}",
                       password_hash=hash_password(PASSWORD), created_at="2026-09-14T00:00:00Z")
        session.add(user)
        session.flush()
        post = db.BoardPost(author_user_id=user.id, author_name=user.username, title="identity",
                            created_at=user.created_at, created_ms=1)
        session.add(post)
        session.flush()
        comments = [db.BoardComment(post_id=post.id, author_user_id=owner, username=user.username,
                                    text="fixture", created_at=user.created_at, created_ms=index)
                    for index, owner in enumerate([user.id, None], 1)]
        session.add_all(comments)
        session.flush()
        result = user.id, post.id, comments[0].id, comments[1].id, user.username
        session.commit()
        return result


def test_rename_updates_owned_comment_snapshots_and_preserves_anonymous_names():
    uid, pid, cid, anonymous_id, old_name = seed()
    renamed = "new_" + secrets.token_hex(5)
    profile.update_profile(uid, renamed, "")
    with db.get_session() as session:
        assert session.get(db.BoardComment, cid).username == renamed
        assert session.get(db.BoardComment, anonymous_id).username == old_name
    comments = board.get_post(pid)["comments"]
    assert [row["username"] for row in comments] == [renamed, old_name]


def test_reads_repair_historical_comment_identity_without_additional_queries():
    uid, pid, cid, _, old_name = seed()
    renamed = "new_" + secrets.token_hex(5)
    with db.get_session() as session:
        user = session.get(db.User, uid)
        user.username = renamed
        session.add(user)
        # Represents old rows created before rename propagation was added.
        session.commit()
    statements = []
    def observe(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(db._engine, "before_cursor_execute", observe)
    try:
        detail = board.get_post(pid)
    finally:
        event.remove(db._engine, "before_cursor_execute", observe)
    assert [row["username"] for row in detail["comments"]] == [renamed, old_name]
    assert len(statements) <= 3
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    with db.get_session() as session:
        user = session.get(db.User, uid)
    assert board.edit_comment(cid, user, "edited")["username"] == renamed


def test_withdrawal_anonymizes_owned_comments_and_historical_deleted_accounts():
    uid, pid, cid, anonymous_id, old_name = seed()
    profile.delete_account(uid, "탈퇴", PASSWORD)
    with db.get_session() as session:
        assert session.get(db.BoardComment, cid).username == "탈퇴한 회원"
        assert session.get(db.BoardComment, anonymous_id).username == old_name
        comment = session.get(db.BoardComment, cid)
        comment.username = old_name
        session.add(comment)
        session.commit()
    comments = board.get_post(pid)["comments"]
    assert comments[0]["username"] == "탈퇴한 회원"
    assert comments[0]["author_avatar_url"] is None
    assert comments[1]["username"] == old_name
