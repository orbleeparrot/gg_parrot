"""Concurrent retries acknowledge one real observation, not two insertions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from sqlmodel import Session, SQLModel, create_engine, select

from app import agent_logs
from app.db import RunSession, RunSessionEvent, User


def test_same_uuid_concurrent_observations_have_one_insert_and_one_recorded_ack(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'observations.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    first_reads = []
    gate = Barrier(2)

    class InterleavedSession(Session):
        """Pause after each real initial read, before either real INSERT.

        No database result or insert count is faked: both concurrent callers
        first observe the actual absence of the UUID. The unique index and
        ON CONFLICT then decide which request really created the one row.
        """
        observed_first_read = False

        def exec(self, statement, *args, **kwargs):
            result = super().exec(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            if (not self.observed_first_read and descriptions
                    and descriptions[0].get("entity") is RunSessionEvent):
                previous = result.first()
                first_reads.append(previous)
                self.observed_first_read = True
                gate.wait(timeout=10)
                return SimpleNamespace(first=lambda: previous)
            return result

    try:
        with Session(engine) as db:
            user = User(email="observation-race@example.invalid", username="observation_race",
                        password_hash="isolated-fixture-not-a-login", created_at="2026-10-14T00:00:00Z")
            db.add(user)
            db.flush()
            session = RunSession(user_id=user.id, symbol="BTCUSDT", status="running", mode="mock",
                                 started_at="2026-10-14T00:00:00Z")
            db.add(session)
            db.commit()
            user_id, session_id = user.id, session.id
        payload = {
            "client_event_id": str(uuid4()), "event_name": "browser.connection_observed",
            "observed_at": "2026-10-14T00:00:00Z",
            "details": {"title": "브라우저 연결 지연 관측", "state": "transport_error"},
        }

        def send():
            with InterleavedSession(engine) as db:
                return agent_logs.record_observation(user_id, session_id, payload, db=db)

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(send) for _ in range(2)]
            responses = [future.result(timeout=20) for future in pending]
        with Session(engine) as db:
            rows = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == session_id)).all()
            assert first_reads == [None, None], "Both requests must reach the real read-before-insert race"
            assert len(rows) == 1
            assert {response["event_id"] for response in responses} == {rows[0].id}
            assert all(response["event"]["event_key"] == f"browser:{payload['client_event_id']}"
                       for response in responses)
            assert sorted(response["recorded"] for response in responses) == [False, True]
    finally:
        engine.dispose()
