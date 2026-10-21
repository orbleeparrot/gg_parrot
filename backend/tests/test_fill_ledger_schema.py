"""체결 기준 장부(fill_ledger_v1) 스키마 — 세션 실행 방식 · 명령 주문 번호 · 체결 기록표(2026-10-21).

체결 기록표(runnerfill)는 실행기가 보고한 실제 체결의 원장이다. 같은 보고가 두 번 와도(응답 유실 · 재전송) 행은
하나여야 하고, 세션을 정리할 때 함께 지워져야 한다(남으면 지운 세션의 체결이 표에 쌓인다).
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import db, runner
from app.db import RunnerCommand, RunnerFill, RunSession, get_session

USER = 77021


def _ended_session(stopped_at: str) -> int:
    with get_session() as s:
        row = RunSession(user_id=USER, symbol="KRW-GTC", status="stopped", started_at=stopped_at,
                         last_heartbeat_at=stopped_at, stopped_at=stopped_at)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id


def _fill(session_id: int, key: str, **over) -> None:
    with get_session() as s:
        s.add(RunnerFill(session_id=session_id, fill_key=key, side="buy", executed_qty=1.0, price=427.0, **over))
        s.commit()


def _fills(session_id: int) -> list:
    with get_session() as s:
        return list(s.exec(select(RunnerFill).where(RunnerFill.session_id == session_id)).all())


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_session() as s:
        ids = list(s.exec(select(RunSession.id).where(RunSession.user_id == USER)).all())
        for row in s.exec(select(RunnerFill).where(RunnerFill.session_id.in_(ids + [9001, 9002]))).all():
            s.delete(row)
        for row in s.exec(select(RunSession).where(RunSession.user_id == USER)).all():
            s.delete(row)
        s.commit()


def test_fresh_schema_has_policy_order_ref_and_fill_journal():
    found = inspect(db._engine)
    assert "exec_policy" in {c["name"] for c in found.get_columns("runsession")}
    assert "order_ref" in {c["name"] for c in found.get_columns("runnercommand")}
    assert {"id", "session_id", "command_id", "fill_key", "exchange_order_id", "source", "side", "status",
            "executed_qty", "acquired_qty", "price", "fee_quote", "fee_known", "reason", "runner_ts_ms",
            "created_ms", "applied_ms"} <= {c["name"] for c in found.get_columns("runnerfill")}
    indexes = {i["name"]: i for i in found.get_indexes("runnerfill")}
    assert indexes["ux_runnerfill_session_key"]["unique"]
    assert indexes["ix_runnerfill_session_applied"]["column_names"] == ["session_id", "applied_ms", "id"]


def test_new_rows_default_to_the_old_path():
    """칸이 생겨도 옛 세션 · 옛 명령은 빈 값 — 실행 방식이 없는 세션은 지금 경로로 돈다(결정 4)."""
    assert RunSession(user_id=1, started_at="x").exec_policy == ""
    assert RunnerCommand(session_id=1).order_ref == ""
    fill = RunnerFill(session_id=1, fill_key="ggp-x")
    assert fill.applied_ms == 0 and fill.command_id is None and fill.fee_known is False


def test_same_fill_key_twice_in_one_session_is_refused():
    _fill(9001, "ggp-same")
    with get_session() as s:
        s.add(RunnerFill(session_id=9001, fill_key="ggp-same", side="buy", executed_qty=1.0, price=427.0))
        with pytest.raises(IntegrityError):
            s.commit()
    _fill(9002, "ggp-same")  # 다른 세션은 같은 키여도 따로다
    assert len(_fills(9001)) == 1 and len(_fills(9002)) == 1


def test_existing_sqlite_runner_tables_gain_the_columns_and_journal(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'older-runner.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(RunSession(id=5, user_id=1, symbol="KRW-GTC", started_at="2026-10-20T00:00:00Z", runner_version="20"))
        s.add(RunnerCommand(id=8, session_id=5, seq=1, action="buy", reason="진입"))
        s.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE runsession DROP COLUMN exec_policy")
        conn.exec_driver_sql("ALTER TABLE runnercommand DROP COLUMN order_ref")
        conn.exec_driver_sql("DROP TABLE runnerfill")
    try:
        db.init_db()
        db.init_db()  # 두 번 불러도 그대로
        with db.get_session() as s:
            assert s.get(RunSession, 5).exec_policy == "" and s.get(RunSession, 5).runner_version == "20"
            assert s.get(RunnerCommand, 8).order_ref == "" and s.get(RunnerCommand, 8).reason == "진입"
            s.add(RunnerFill(session_id=5, fill_key="ggp-new", executed_qty=1.0, price=1.0))
            s.commit()
    finally:
        engine.dispose()


def test_deleting_a_session_removes_its_fills_only():
    sid, other = _ended_session("2026-10-21T00:00:00Z"), _ended_session("2026-10-21T00:00:00Z")
    _fill(sid, "ggp-1")
    _fill(sid, "ggp-2", command_id=3)
    _fill(other, "ggp-1")
    with get_session() as s:
        runner._delete_session_rows(s, [sid])
        s.commit()
    assert _fills(sid) == [] and len(_fills(other)) == 1


def test_history_maintenance_removes_fills_with_the_session():
    sid = _ended_session("2026-08-01T00:00:00Z")  # 30일 지난 종료 기록
    _fill(sid, "ggp-old")
    with get_session() as s:
        runner.prune_all_ended_sessions(s, now=datetime(2026, 10, 21, tzinfo=timezone.utc))
    with get_session() as s:
        assert s.get(RunSession, sid) is None
    assert _fills(sid) == []
