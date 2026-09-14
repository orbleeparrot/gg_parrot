"""Audit profile rename propagation using a temporary SQLite database only.

Run with the project's Python environment. The existing test bootstrap blanks
production credentials before any application import. All socket connections
are forbidden. No production or development database is accessed.
"""
from pathlib import Path
import hashlib
import json
import os
import runpy
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
runpy.run_path(str(ROOT / "backend/tests/conftest.py"))


def forbidden_network(*args, **kwargs):
    raise AssertionError("Network forbidden in cache audit")


socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
assert os.environ["DATABASE_URL"] == ""
temporary_db = Path(os.environ["SQLITE_PATH"])
assert temporary_db.name.startswith("ggp-test-")

from app.db import init_db, get_session, User, BoardPost, BoardComment
from app import board, profile

init_db()
stamp = "2026-09-14T00:00:00Z"
with get_session() as db:
    user = User(email="cache-audit@example.invalid", username="audit_before",
                password_hash="unused", created_at=stamp)
    db.add(user)
    db.commit()
    db.refresh(user)
    uid = user.id
    post = BoardPost(author_user_id=uid, author_name=user.username, title="fixture",
                     body="", created_ms=1, created_at=stamp)
    db.add(post)
    db.commit()
    db.refresh(post)
    pid = post.id
    db.add(BoardComment(post_id=pid, author_user_id=uid, username=user.username,
                        text="fixture", created_ms=1, created_at=stamp))
    db.commit()

profile.update_profile(uid, "audit_after", "")
detail = board.get_post(pid)
observed = {"post_author": detail["author_name"], "comment_author": detail["comments"][0]["username"]}
assert observed == {"post_author": "audit_after", "comment_author": "audit_before"}, (
    "Audited defect no longer reproduces; review the new behavior", observed
)
report = {
    "audit": "profile-rename-leaves-comment-author-snapshot",
    "scope": "Temporary SQLite and socket connections forbidden",
    "source": "backend/app/profile.py",
    "source_sha256": hashlib.sha256((ROOT / "backend/app/profile.py").read_bytes()).hexdigest(),
    "expected": {"post_author": "audit_after", "comment_author": "audit_after"},
    "observed": observed,
    "defect_reproduced": True,
}
Path(__file__).with_name("profile-comment-snapshot-result.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False))
temporary_db.unlink(missing_ok=True)
