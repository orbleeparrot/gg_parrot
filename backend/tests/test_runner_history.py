"""종료 기록 보관 — 최근 30건 · 30일만 남기고, 보관(pinned)한 기록은 10건까지 정리에서 뺀다."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import runner as runner_mod
from app.db import RunnerCommand, RunSession, RunSessionEvent, User, get_session

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def user_id():
    with get_session() as db:
        stamp = str(datetime.now().timestamp()).replace(".", "")
        user = User(email=f"history-{stamp}@example.invalid", username=f"history-{stamp}", password_hash="x", created_at="2026-10-01T00:00:00Z")
        db.add(user)
        db.commit()
        db.refresh(user)
        yield user.id
        for row in db.exec(select(RunSession).where(RunSession.user_id == user.id)).all():
            db.delete(row)
        db.delete(user)
        db.commit()


def _ended(db, user_id, *, days_ago=0.0, pinned=False, status="stopped"):
    ended = NOW - timedelta(days=days_ago)
    row = RunSession(user_id=user_id, symbol="BTCUSDT", status=status, pinned=pinned,
                     started_at=_iso(ended - timedelta(minutes=5)), stopped_at=_iso(ended))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def test_keeps_only_the_latest_30_unpinned_ended_sessions(user_id):
    with get_session() as db:
        ids = [_ended(db, user_id, days_ago=1) for _ in range(35)]
        running = RunSession(user_id=user_id, symbol="ETHUSDT", status="running", started_at=_iso(NOW))
        db.add(running)
        db.commit()
        db.add(RunSessionEvent(session_id=ids[0], user_id=user_id, kind="signal", ts=_iso(NOW)))
        db.add(RunnerCommand(session_id=ids[0], seq=1, action="buy"))
        db.commit()
        assert runner_mod.prune_ended_sessions(user_id, db, now=NOW) == 5
        left = [row.id for row in db.exec(select(RunSession).where(
            RunSession.user_id == user_id, RunSession.status != "running")).all()]
        assert sorted(left) == sorted(ids[5:])  # 가장 오래된 5건이 지워진다
        # 실행 중 세션은 건드리지 않는다
        assert db.get(RunSession, running.id) is not None
        # 지운 세션의 이벤트·실행 명령도 같이 지운다
        assert not db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == ids[0])).all()
        assert not db.exec(select(RunnerCommand).where(RunnerCommand.session_id == ids[0])).all()


def test_drops_sessions_older_than_30_days_even_under_the_count(user_id):
    with get_session() as db:
        fresh = _ended(db, user_id, days_ago=29)
        old = _ended(db, user_id, days_ago=31)
        assert runner_mod.prune_ended_sessions(user_id, db, now=NOW) == 1
        assert db.get(RunSession, fresh) is not None
        assert db.get(RunSession, old) is None


def test_pinned_sessions_survive_count_and_age(user_id):
    with get_session() as db:
        pinned = _ended(db, user_id, days_ago=90, pinned=True)
        for _ in range(31):
            _ended(db, user_id, days_ago=1)
        runner_mod.prune_ended_sessions(user_id, db, now=NOW)
        assert db.get(RunSession, pinned) is not None


def test_pin_limit_is_ten_and_running_sessions_cannot_be_pinned(user_id):
    with get_session() as db:
        ids = [_ended(db, user_id) for _ in range(11)]
        running = RunSession(user_id=user_id, symbol="ETHUSDT", status="running", started_at=_iso(NOW))
        db.add(running)
        db.commit()
        running_id = running.id
    for session_id in ids[:10]:
        assert runner_mod.set_pinned(user_id, session_id, True)["pinned"] is True
    with pytest.raises(HTTPException) as full:
        runner_mod.set_pinned(user_id, ids[10], True)
    assert full.value.status_code == 409 and "10건" in full.value.detail
    # 하나를 풀면 다시 보관할 수 있다
    runner_mod.set_pinned(user_id, ids[0], False)
    assert runner_mod.set_pinned(user_id, ids[10], True)["pinned"] is True
    with pytest.raises(HTTPException) as live:
        runner_mod.set_pinned(user_id, running_id, True)
    assert live.value.status_code == 409


def test_list_sessions_reports_pin_state_and_policy(user_id):
    with get_session() as db:
        _ended(db, user_id, pinned=True)
    data = runner_mod.list_sessions(user_id)
    assert data["history_policy"] == {"keep": 30, "days": 30, "pin_limit": 10}
    assert data["recent"][0]["pinned"] is True
    assert data["recent"][0]["stopped_at"]


def test_stopping_a_session_prunes_the_31st_oldest(user_id):
    with get_session() as db:
        for _ in range(30):
            _ended(db, user_id, days_ago=1)
        oldest = db.exec(select(RunSession.id).where(RunSession.user_id == user_id).order_by(RunSession.id)).first()
        live = RunSession(user_id=user_id, symbol="ETHUSDT", status="running", started_at=_iso(NOW))
        db.add(live)
        db.commit()
        db.refresh(live)
        user = db.get(User, user_id)
    runner_mod.mark_stopped(user, live.id, note="포지션 없이 종료")
    with get_session() as db:
        assert db.get(RunSession, oldest) is None
        assert db.get(RunSession, live.id) is not None


def _ended_at(db, user_id, ended, pinned=False):
    row = RunSession(user_id=user_id, symbol="BTCUSDT", status="stopped", pinned=pinned,
                     started_at=_iso(ended - timedelta(minutes=5)), stopped_at=_iso(ended))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def test_list_hides_expired_records_before_cleanup_runs(user_id):
    real_now = datetime.now(timezone.utc)
    with get_session() as db:
        fresh = _ended_at(db, user_id, real_now - timedelta(days=2))
        expired = _ended_at(db, user_id, real_now - timedelta(days=31))
        kept = _ended_at(db, user_id, real_now - timedelta(days=60), pinned=True)
    shown = {row["session_id"] for row in runner_mod.list_sessions(user_id)["recent"]}
    assert fresh in shown and kept in shown and expired not in shown
    with get_session() as db:
        assert db.get(RunSession, expired) is not None  # 숨겼을 뿐 아직 지우지 않았다


def test_daily_cleanup_removes_expired_records_across_accounts(user_id):
    with get_session() as db:
        expired = _ended(db, user_id, days_ago=40)
        pinned = _ended(db, user_id, days_ago=40, pinned=True)
        for _ in range(32):
            _ended(db, user_id, days_ago=1)
        removed = runner_mod.prune_all_ended_sessions(db, now=NOW)
        assert removed >= 3  # 기간 1건 + 30건 초과 2건
        assert db.get(RunSession, expired) is None
        assert db.get(RunSession, pinned) is not None
        left = db.exec(select(RunSession).where(
            RunSession.user_id == user_id, RunSession.pinned == False)).all()  # noqa: E712
        assert len(left) == 30
