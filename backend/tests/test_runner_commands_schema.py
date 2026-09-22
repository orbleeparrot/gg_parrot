"""실행기 명령 테이블과 runsession.state_json 이 sqlite 개발 DB 에 생긴다."""
import secrets

from sqlmodel import select

from app.db import RunnerCommand, RunSession, get_session


def test_runner_command_round_trip():
    # 다른 테스트가 남긴 세션 id 와 겹치지 않게 큰 난수 id 를 쓴다(session_id=1 + first() 는 남은 행을 집어 흔들렸다).
    session_id = 10_000_000 + secrets.randbelow(1_000_000)
    with get_session() as db:
        try:
            row = RunnerCommand(session_id=session_id, seq=1, action="buy", notional_frac=0.5, signal_price=100.0,
                                reason="RSI 23.1 ≤ 25 · 진입", created_at="2026-09-22T00:00:00Z", created_ms=1, expires_ms=2)
            db.add(row)
            db.commit()
            got = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == session_id)).all()
            assert len(got) == 1
            assert got[0].status == "pending" and got[0].attempts == 0 and got[0].qty_frac == 0.0
        finally:
            for r in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == session_id)).all():
                db.delete(r)
            db.commit()


def test_runsession_has_state_json_default_empty():
    assert RunSession.model_fields["state_json"].default == ""
