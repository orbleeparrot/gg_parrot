"""Notification retention is bounded, explicit and owned by the caller's transaction."""
from __future__ import annotations

import pytest
from sqlmodel import Session, create_engine, select

from app import notifications
from app.db import NotificationMessage, NotificationReceipt


NOW_MS = 1_800_000_000_000
CUTOFF_MS = NOW_MS - notifications.NOTICE_WINDOW_MS


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notification-retention.db'}")
    # These SQLite models intentionally have no FK/CASCADE. The application
    # must clean children itself, not accidentally rely on the production FK.
    NotificationMessage.__table__.create(engine)
    NotificationReceipt.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _message(db, *, created_ms=CUTOFF_MS - 1, user_id=None, kind="notice"):
    row = NotificationMessage(user_id=user_id, kind=kind, title="retention fixture",
                              created_at="2026-01-01T00:00:00Z", created_ms=created_ms)
    db.add(row)
    db.flush()
    return row.id


def _receipts(db, message_id, count, *, first_user=1):
    db.add_all(NotificationReceipt(message_id=message_id, user_id=user_id, read_ms=NOW_MS)
               for user_id in range(first_user, first_user + count))
    db.flush()


def _message_ids(db):
    return set(db.exec(select(NotificationMessage.id)).all())


def _receipt_keys(db):
    return set(db.exec(select(NotificationReceipt.message_id, NotificationReceipt.user_id)).all())


def test_only_expired_public_messages_and_their_receipts_are_removed(db):
    expired = _message(db)
    expired_other_kind = _message(db, kind="admin")
    boundary = _message(db, created_ms=CUTOFF_MS)
    recent = _message(db, created_ms=NOW_MS)
    # Scope is user_id NULL, not kind. Even an ancient personal 'notice' is safe.
    private = _message(db, user_id=7)
    zero_user = _message(db, user_id=0)
    for message_id in (expired, expired_other_kind, boundary, recent, private, zero_user):
        _receipts(db, message_id, 2)

    assert not NotificationReceipt.__table__.foreign_keys
    result = notifications.prune_expired_notices(db, now_ms=NOW_MS)

    assert result == {"deleted_notices": 2, "deleted_receipts": 4, "has_more": False}
    protected = {boundary, recent, private, zero_user}
    assert _message_ids(db) == protected
    assert _receipt_keys(db) == {(message_id, user_id)
                                for message_id in protected for user_id in (1, 2)}


def test_orphan_cleanup_is_bounded_and_preserves_receipts_with_existing_parents(db):
    current = _message(db, created_ms=NOW_MS)
    private = _message(db, user_id=3)
    _receipts(db, current, 1)
    _receipts(db, private, 1)
    _receipts(db, 999_999, 1_001)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 0, "deleted_receipts": 1_000, "has_more": True,
    }
    assert len(_receipt_keys(db)) == 3
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 0, "deleted_receipts": 1, "has_more": False,
    }
    assert _receipt_keys(db) == {(current, 1), (private, 1)}


def test_parent_with_more_than_receipt_budget_is_left_for_the_next_batch(db):
    expired = _message(db)
    _receipts(db, expired, 1_001)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 0, "deleted_receipts": 1_000, "has_more": True,
    }
    assert _message_ids(db) == {expired}
    assert len(_receipt_keys(db)) == 1
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 1, "deleted_receipts": 1, "has_more": False,
    }
    assert _message_ids(db) == set()
    assert _receipt_keys(db) == set()


def test_expired_children_and_orphans_share_one_receipt_budget(db):
    expired = _message(db)
    _receipts(db, expired, 950)
    _receipts(db, 999_999, 100)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 1, "deleted_receipts": 1_000, "has_more": True,
    }
    assert len(_receipt_keys(db)) == 50
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 0, "deleted_receipts": 50, "has_more": False,
    }


def test_small_notice_batches_progress_and_report_actual_remaining_work(db):
    expired = {_message(db) for _ in range(5)}
    protected = _message(db, created_ms=CUTOFF_MS)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS, limit=2) == {
        "deleted_notices": 2, "deleted_receipts": 0, "has_more": True,
    }
    assert len(_message_ids(db) & expired) == 3
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS, limit=2) == {
        "deleted_notices": 2, "deleted_receipts": 0, "has_more": True,
    }
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS, limit=2) == {
        "deleted_notices": 1, "deleted_receipts": 0, "has_more": False,
    }
    assert _message_ids(db) == {protected}
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 0, "deleted_receipts": 0, "has_more": False,
    }


def test_notice_limit_never_exceeds_500(db):
    for _ in range(501):
        _message(db)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS, limit=100_000) == {
        "deleted_notices": 500, "deleted_receipts": 0, "has_more": True,
    }
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 1, "deleted_receipts": 0, "has_more": False,
    }


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_still_makes_bounded_progress(db, limit):
    _message(db)
    _message(db)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS, limit=limit) == {
        "deleted_notices": 1, "deleted_receipts": 0, "has_more": True,
    }


def test_prune_flushes_but_never_commits_or_rolls_back_callers_work(db):
    expired = _message(db)
    _receipts(db, expired, 2)
    _receipts(db, 999_999, 1)
    db.commit()
    uncommitted_private = _message(db, user_id=7)

    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 1, "deleted_receipts": 3, "has_more": False,
    }
    assert _message_ids(db) == {uncommitted_private}
    db.rollback()

    assert _message_ids(db) == {expired}
    assert _receipt_keys(db) == {(expired, 1), (expired, 2), (999_999, 1)}


def test_private_cap_remains_300_without_age_deleting_personal_notifications(db):
    for _ in range(notifications.KEEP_PER_USER + 5):
        _message(db, user_id=17)
    expired = _message(db)

    assert notifications.trim_user(db, 17) == 5
    assert notifications.prune_expired_notices(db, now_ms=NOW_MS) == {
        "deleted_notices": 1, "deleted_receipts": 0, "has_more": False,
    }
    survivors = db.exec(select(NotificationMessage)).all()
    assert len(survivors) == 300
    assert all(row.user_id == 17 and row.id != expired for row in survivors)


def test_omitted_time_uses_current_clock(db, monkeypatch):
    _message(db)
    monkeypatch.setattr(notifications, "_now", lambda: ("2026-01-01T00:00:00Z", NOW_MS))

    assert notifications.prune_expired_notices(db) == {
        "deleted_notices": 1, "deleted_receipts": 0, "has_more": False,
    }
