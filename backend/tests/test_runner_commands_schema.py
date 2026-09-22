"""실행기 명령 테이블과 runsession.state_json 이 sqlite 개발 DB 에 생긴다."""
from sqlmodel import select

from app.db import RunnerCommand, RunSession, get_session


def test_runner_command_round_trip():
    with get_session() as db:
        row = RunnerCommand(session_id=1, seq=1, action="buy", notional_frac=0.5, signal_price=100.0,
                            reason="RSI 23.1 ≤ 25 · 진입", created_at="2026-09-22T00:00:00Z", created_ms=1, expires_ms=2)
        db.add(row)
        db.commit()
        got = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == 1)).first()
        assert got.status == "pending" and got.attempts == 0 and got.qty_frac == 0.0
        db.delete(got)
        db.commit()


def test_runsession_has_state_json_default_empty():
    assert RunSession.model_fields["state_json"].default == ""
