"""Desktop restart reattaches to one session only after position reconciliation."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import runner as runner_mod, runner_engine
from app.db import RunSession, RunSessionEvent, RunnerCommand, get_session
from app.main import app
from app import macro_signing
from engine import Macro

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_own_sessions():
    with get_session() as db:
        before = set(db.exec(select(RunSession.id)).all())
    yield
    with get_session() as db:
        created = set(db.exec(select(RunSession.id)).all()) - before
        runner_mod._delete_session_rows(db, list(created))
        db.commit()


def _account():
    uid = uuid.uuid4().hex
    token = client.post("/api/auth/signup", json={"email": f"recovery{uid}@example.com",
        "username": f"recovery{uid[:12]}", "password": "password123"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    key = client.get("/api/me/runner/key", headers=auth).json()["key"]
    return auth, {"X-Runner-Key": key}


def _start(headers, *, recovery=True, local=False):
    binding = str(uuid.uuid4()) if recovery else ""
    body = {"symbol": "BTCUSDT", "runner_version": "24", "engine_version": "1", "mode": "mock",
            "recovery_id": binding, "macro": {"symbol": "BTCUSDT", "rule_type": "A",
            "params": {"take_profit_pct": 3, "initial_capital": 100}, "risk": {"invest_ratio": .5}}}
    if local:
        body["decides"] = "local"
    response = client.post("/api/runner/start", json=body, headers=headers)
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    assert response.json()["generation"] == 0
    with get_session() as db:
        row = db.get(RunSession, sid)
        if recovery:
            assert response.json()["macro"] == json.loads(row.macro_json)
        row.last_heartbeat_at = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        row.state_json = json.dumps({"in_position": False, "legs": []})
        db.add(row)
        db.commit()
    return sid, binding


def _payload(headers, sid, binding):
    read = client.post("/api/runner/recovery", json={"session_id": sid, "recovery_id": binding}, headers=headers)
    assert read.status_code == 200, read.text
    data = read.json()
    return {"session_id": sid, "recovery_id": binding, "generation": data["generation"], "resume_id": str(uuid.uuid4()),
            "runner_version": "24", "engine_version": data["engine_version"],
            "macro_digest": data["macro_digest"], "snapshot": data["snapshot"],
            "strategy_state": data["strategy_state"]}


def test_recovery_preserves_identity_history_settings_and_cancels_commands():
    auth, headers = _account()
    sid, binding = _start(headers)
    with get_session() as db:
        row = db.get(RunSession, sid)
        original_start, original_macro = row.started_at, row.macro_json
        row.settings_changed_count = 2
        row.pending_macro_json = row.macro_json
        row.pending_change_id = 123
        db.add(row)
        db.add(RunnerCommand(session_id=sid, seq=1, action="buy", status="pending"))
        db.commit()
    payload = _payload(headers, sid, binding)
    response = client.post("/api/runner/resume", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == sid
    assert response.json()["generation"] == 1
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert row.started_at == original_start and row.macro_json == original_macro
        assert row.settings_changed_count == 2 and row.pending_change_id == 123
        command = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).one()
        assert command.status == "cancelled" and command.executed_qty == 0
        assert "실행 여부" in command.error
        events = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
        assert {e.event_name for e in events} >= {"session.started", "session.resumed"}
    sessions = client.get("/api/me/runner/sessions", headers=auth).json()
    assert [s["session_id"] for s in sessions["active"]] == [sid]


def test_sparse_macro_start_returns_same_canonical_macro_and_digest_as_recovery():
    _, headers = _account()
    binding = str(uuid.uuid4())
    sparse = {"symbol": "BTCUSDT", "rule_type": "A", "params": {"take_profit_pct": 3, "initial_capital": 100},
              "human_summary": "downloaded file annotation"}
    response = client.post("/api/runner/start", headers=headers, json={"symbol": "BTCUSDT", "macro": sparse,
                           "runner_version": "24", "recovery_id": binding})
    assert response.status_code == 200, response.text
    start = response.json()
    read = client.post("/api/runner/recovery", headers=headers,
                       json={"session_id": start["session_id"], "recovery_id": binding}).json()
    assert start["macro"] != sparse
    assert start["macro"] == read["macro"]
    assert start["macro_digest"] == read["macro_digest"] == macro_signing.digest(Macro.model_validate(start["macro"]))


def test_fencing_rejects_missing_and_old_reports_without_overwriting_position():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 200
    for generation in (None, 0):
        body = {"session_id": sid, "last_price": 999, "events": [{"kind": "error", "message": "obsolete"}]}
        if generation is not None:
            body["generation"] = generation
        response = client.post("/api/runner/heartbeat", json=body, headers=headers)
        assert response.json()["action"] == "stop_only"
        response = client.post("/api/runner/stopped", json=body, headers=headers)
        assert response.status_code == 409
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert row.status == "running" and row.last_price == 0
    current = client.post("/api/runner/heartbeat", json={"session_id": sid, "generation": 1, "last_price": 100}, headers=headers)
    assert current.status_code == 200 and current.json()["action"] == "continue"
    settings = {"session_id": sid, "generation": 0, "changes": {"risk": {"stop_loss_pct": 1}}}
    assert client.post("/api/runner/settings", json=settings, headers=headers).status_code == 409


@pytest.mark.parametrize("status,note,stop_mode", [
    ("running", "", "stop_only"), ("stopped", "user stopped", ""), ("error", "crashed order", ""),
])
def test_manual_stop_and_error_cannot_resume(status, note, stop_mode):
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.status, row.note, row.stop_mode = status, note, stop_mode
        db.add(row)
        db.commit()
    response = client.post("/api/runner/resume", json=payload, headers=headers)
    assert response.status_code == 409


def test_only_stale_automatic_end_can_resume():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.status, row.note, row.stopped_at = "stopped", runner_engine.STALE_RUNNER_NOTE, runner_mod._now_iso()
        db.add(row)
        db.commit()
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 200


def test_online_and_wrong_binding_account_and_versions_are_rejected():
    _, headers = _account()
    _, stranger = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    assert client.post("/api/runner/recovery", json={"session_id": sid, "recovery_id": binding}, headers=stranger).status_code == 404
    assert client.post("/api/runner/recovery", json={"session_id": sid, "recovery_id": "가" * 36}, headers=headers).status_code == 404
    assert client.post("/api/runner/resume", json=payload, headers=stranger).status_code == 404
    assert client.post("/api/runner/resume", json={**payload, "recovery_id": str(uuid.uuid4())}, headers=headers).status_code == 404
    assert client.post("/api/runner/resume", json={**payload, "runner_version": "23"}, headers=headers).status_code == 426
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.last_heartbeat_at = runner_mod._now_iso()
        db.add(row)
        db.commit()
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 409


def test_concurrent_resumes_have_one_winner():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post("/api/runner/resume", json={**payload, "resume_id": str(uuid.uuid4())}, headers=headers), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with get_session() as db:
        assert db.get(RunSession, sid).generation == 1


def test_lost_resume_response_retry_is_idempotent_and_does_not_bypass_stop():
    auth, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    first = client.post("/api/runner/resume", json=payload, headers=headers)
    retry = client.post("/api/runner/resume", json=payload, headers=headers)
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json() and retry.json()["generation"] == 1
    other = client.post("/api/runner/resume", json={**payload, "resume_id": str(uuid.uuid4())}, headers=headers)
    assert other.status_code == 409
    assert client.post(f"/api/me/runner/sessions/{sid}/request-stop", json={"mode": "stop_only"}, headers=auth).status_code == 200
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 409


def test_reconnect_ticket_cannot_launch_new_macro_or_expose_local_binding():
    auth, headers = _account()
    sid, binding = _start(headers)
    response = client.post(f"/api/me/runner/sessions/{sid}/reconnect", headers=auth)
    assert response.status_code == 200, response.text
    ticket = parse_qs(urlsplit(response.json()["launch_url"]).query)["ticket"][0]
    old = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "23"})
    assert old.status_code == 426
    claim = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "24"})
    assert claim.status_code == 200, claim.text
    assert claim.json()["recovery"] == {"session_id": sid}
    assert set(claim.json()) == {"launch_id", "runner_key", "recovery"}
    assert binding not in claim.text and "macro" not in claim.json()
    assert client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "24"}).status_code == 409


def test_legacy_sessions_remain_compatible_but_cannot_claim_recovery():
    auth, headers = _account()
    sid, _ = _start(headers, recovery=False)
    assert client.post(f"/api/me/runner/sessions/{sid}/reconnect", headers=auth).status_code == 409
    assert client.post("/api/runner/heartbeat", json={"session_id": sid}, headers=headers).json()["action"] == "continue"
    assert client.post("/api/runner/stopped", json={"session_id": sid}, headers=headers).status_code == 200


def test_generation_zero_is_required_on_initial_recovery_session():
    _, headers = _account()
    sid, _ = _start(headers)
    assert client.post("/api/runner/heartbeat", json={"session_id": sid}, headers=headers).json()["action"] == "stop_only"
    assert client.post("/api/runner/heartbeat", json={"session_id": sid, "generation": 0}, headers=headers).json()["action"] == "continue"


def test_recovery_read_does_not_change_state_and_conflicts_are_preserved():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    changed = {**payload, "macro_digest": "new-settings"}
    assert client.post("/api/runner/resume", json=changed, headers=headers).status_code == 409
    snapshot = {**payload["snapshot"], "in_position": True, "position_qty": 2, "entry_price": 10}
    assert client.post("/api/runner/resume", json={**payload, "snapshot": snapshot}, headers=headers).status_code == 409
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert row.generation == 0 and row.in_position is False


def test_old_server_driver_checkpoint_cannot_stop_or_overwrite_resumed_session():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 200
    live = runner_engine._Live(sid, None)
    runner_engine._persist_state(live, '{"in_position": true, "obsolete": true}')
    runner_engine._note_start_failure(sid, 0)
    runner_engine._refuse_portfolio_session(sid, 0)
    runner_engine._note_loop_error(sid, 0)
    assert live.stop_flag is True
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert row.status == "running" and row.note == "" and "obsolete" not in row.state_json


def test_local_decider_restores_state_and_requires_identical_engine_version(monkeypatch):
    monkeypatch.setattr(runner_mod, "LOCAL_DECISIONS_ENABLED", True)
    _, headers = _account()
    sid, binding = _start(headers, local=True)
    payload = _payload(headers, sid, binding)
    assert client.post("/api/runner/resume", json={**payload, "engine_version": "2"}, headers=headers).status_code == 426
    payload["strategy_state"] = {"in_position": False, "checkpoint_ms": 1234, "legs": []}
    response = client.post("/api/runner/resume", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["decides"] == "local"
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert json.loads(row.state_json)["checkpoint_ms"] == 1234


def test_position_uncertain_and_nonfinite_snapshots_cannot_resume():
    _, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    uncertain = {**payload["snapshot"], "position_uncertain": True}
    assert client.post("/api/runner/resume", json={**payload, "snapshot": uncertain}, headers=headers).status_code == 409
    invalid = {**payload["snapshot"], "last_price": "nan"}
    assert client.post("/api/runner/resume", json={**payload, "snapshot": invalid}, headers=headers).status_code == 422


def test_reconnect_rechecks_stop_at_claim_time_and_cross_account():
    auth, headers = _account()
    stranger, _ = _account()
    sid, _ = _start(headers)
    assert client.post(f"/api/me/runner/sessions/{sid}/reconnect", headers=stranger).status_code == 404
    issued = client.post(f"/api/me/runner/sessions/{sid}/reconnect", headers=auth).json()
    ticket = parse_qs(urlsplit(issued["launch_url"]).query)["ticket"][0]
    client.post(f"/api/me/runner/sessions/{sid}/request-stop", json={"mode": "stop_only"}, headers=auth)
    assert client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "24"}).status_code == 409


def test_user_stop_revokes_recovery_after_automatic_stale_end():
    auth, headers = _account()
    sid, binding = _start(headers)
    payload = _payload(headers, sid, binding)
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.status, row.note = "stopped", runner_engine.STALE_RUNNER_NOTE
        db.add(row)
        db.commit()
    client.post(f"/api/me/runner/sessions/{sid}/request-stop", json={"mode": "stop_only"}, headers=auth)
    assert client.post("/api/runner/resume", json=payload, headers=headers).status_code == 409
