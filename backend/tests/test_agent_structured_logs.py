"""Same owner-only log API: explicit facts, literal search and bounded context."""
from __future__ import annotations

import json
import secrets

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, inspect
from sqlmodel import delete, select

from app import agent_logs, auth, db as db_mod
from app.agent_event_schema import normalize_event, safe_details, safe_url
from app.db import RunSession, RunSessionEvent, User, get_session
from app.main import app

client = TestClient(app)
NOW = "2026-10-13T02:01:02Z"


@pytest.fixture
def owner(monkeypatch):
    with get_session() as db:
        suffix = secrets.token_hex(6)
        user = User(email=f"structured-{suffix}@example.invalid", username=f"structured_{suffix}",
                    password_hash="not-a-login-password", created_at=NOW)
        db.add(user)
        db.flush()
        session = RunSession(user_id=user.id, started_at=NOW, symbol="KRW-BTC", mode="live",
                             market="spot", runner_version="10", macro_json='{"exchange":"upbit"}')
        db.add(session)
        db.commit()
        db.refresh(user)
        db.refresh(session)
        values = user.id, session.id, {"Authorization": f"Bearer {auth.make_token(user.id, 0)}"}
    forbidden = lambda *_a, **_kw: pytest.fail("Log reads must not perform HTTP or AI work")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    try:
        yield values
    finally:
        # Other suites enumerate running sessions in the shared isolated DB.
        # Remove only this fixture's identities, including its secondary
        # anti-IDOR session, rather than resetting unrelated test state.
        with get_session() as db:
            db.exec(delete(RunSessionEvent).where(RunSessionEvent.user_id == values[0]))
            db.exec(delete(RunSession).where(RunSession.user_id == values[0]))
            db.exec(delete(User).where(User.id == values[0]))
            db.commit()


def add(owner, **fields):
    uid, sid, _ = owner
    with get_session() as db:
        row = RunSessionEvent(session_id=sid, user_id=uid, ts=NOW, created_ms=100, **fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def read(owner, **params):
    _, sid, headers = owner
    return client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers, params=params)


def test_legacy_logs_have_unknown_level_and_no_inferred_operation(owner):
    add(owner, kind="error", message="ERROR 매수 가격 123.45 command:8")
    body = read(owner).json()
    item = body["items"][0]
    assert item["severity"] == "UNKNOWN"
    assert item["component"] == item["event_name"] == item["operation_id"] == ""
    assert item["event_key"] is None and item["details"] == {}
    assert item["ts"] == "2026-10-13T02:01:02+00:00"  # no fabricated milliseconds
    assert item["time_basis"] == "event" and item["occurred_at"] == item["ts"]
    assert body["context"] == {"session_id": owner[1], "symbol": "KRW-BTC", "exchange": "upbit",
                               "mode": "live", "market": "spot", "runner_version": "10", "macro_digest": ""}
    assert read(owner).headers["cache-control"] == "private, no-store"


def test_invalid_occurrence_does_not_relabel_server_reception_as_actual_event(owner):
    identifier = add(owner, message="기존 기록")
    with get_session() as db:
        row = db.get(RunSessionEvent, identifier)
        row.ts = "invalid"
        db.add(row)
        db.commit()
    item = read(owner).json()["items"][0]
    assert item["occurred_at"] == "" and item["time_basis"] == "server_reception"
    assert item["ts"] == item["received_at"]


def test_unknown_filter_matches_invalid_legacy_levels_as_displayed(owner):
    identifier = add(owner, severity="old_unstructured_level")
    body = read(owner, severity="UNKNOWN").json()
    assert [item["id"] for item in body["items"]] == [identifier]
    assert body["items"][0]["severity"] == "UNKNOWN"


def test_unknown_mode_and_exchange_are_not_guessed_from_symbol_or_testnet(owner):
    with get_session() as db:
        session = db.get(RunSession, owner[1])
        session.mode = ""
        session.macro_json = "not-json"
        session.testnet = True
        db.add(session)
        db.commit()
    context = read(owner).json()["context"]
    assert context["exchange"] == context["mode"] == ""


def test_structured_details_are_typed_allowlisted_and_credential_safe(owner):
    raw = {"command_id": 81, "attempt": 2, "max_attempts": 2, "http_status": 429,
           "executed_qty": 0.02, "fill_price": 120.5, "cache_hit": True, "reused": False,
           "error_code": "insufficient_quota", "error_type": "rate_limit_error",
           "reason": "api_key=do-not-leak Authorization: Bearer never-show",
           "request_id": "req_safe", "query": "BTC coin", "collection_run_id": 77,
           "source_counts": {"CoinDesk": 5}, "fee_currency": "KRW", "fee": 50,
           "password": "forbidden", "arbitrary": {"token": "no"},
           "threshold": float("inf"), "quantity": float("nan"),
           "examples": [{"title": "기사", "source": "CoinDesk", "url": "https://coindesk.com/a?token=private"}]}
    normalized = normalize_event({"severity": "ERROR", "kind": "error", "message": "제한 오류",
        "event_name": "command.failed", "operation_id": "command:81", "details": raw}, component="engine")
    assert json.loads(normalized["details_json"]) == safe_details(raw)
    add(owner, **{key: value for key, value in normalized.items() if key != "ts"})
    item = read(owner).json()["items"][0]
    assert item["severity"] == "ERROR" and item["component"] == "engine"
    assert item["operation_id"] == "command:81" and item["event_name"] == "command.failed"
    assert item["details"]["command_id"] == 81 and item["details"]["cache_hit"] is True
    assert item["details"]["examples"][0]["url"] == "https://coindesk.com/a"
    assert not {"password", "arbitrary", "quantity", "threshold"} & item["details"].keys()
    assert "do-not-leak" not in json.dumps(item) and "never-show" not in json.dumps(item)


@pytest.mark.parametrize("details", ["invalid-json", "null", "[]", '{"count":{},"source_counts":[],"examples":"bad"}'])
def test_malformed_details_never_break_read(owner, details):
    add(owner, details_json=details)
    assert read(owner).json()["items"][0]["details"] == {}


def test_normalizer_copes_with_invalid_fields_and_forces_writer_provenance():
    row = normalize_event({"kind": [], "severity": "critical", "component": "spoof",
        "event_name": "secret=abc", "operation_id": "Bearer private", "event_key": "sk-abcdefghijklmnop",
        "details_json": {"quantity": True, "count": -1, "attempts": "2", "error": "password=private"}}, component="runner")
    assert row["severity"] == "UNKNOWN" and row["kind"] == "info" and row["component"] == "runner"
    assert row["event_name"] == row["operation_id"] == "" and row["event_key"] is None
    assert json.loads(row["details_json"]) == {"attempts": 2, "error": "password=[redacted]"}


def test_filters_are_applied_before_keyset_pagination_and_unknown_is_legacy(owner):
    first = add(owner, severity="ERROR", component="engine", event_name="command.failed",
                operation_id="command:81", message="target first")
    add(owner, severity="INFO", component="runner", event_name="command.acked", operation_id="command:82")
    second = add(owner, severity="ERROR", component="engine", event_name="command.failed",
                 operation_id="command:81", message="target second")
    legacy = add(owner, kind="error", message="target legacy")
    params = dict(q="TARGET", severity="ERROR", component="engine", event_name="command.failed",
                  operation_id="command:81", limit=1)
    page = read(owner, **params).json()
    assert [item["id"] for item in page["items"]] == [second]
    assert page["has_more"] and page["next_before_id"] == second
    older = read(owner, **params, before_id=second).json()
    assert [item["id"] for item in older["items"]] == [first] and not older["has_more"]
    assert [item["id"] for item in read(owner, severity="UNKNOWN").json()["items"]] == [legacy]


def test_text_search_treats_sql_wildcards_and_injection_as_literal(owner):
    percentage = add(owner, message="진행률 100% 완료")
    underscore = add(owner, message="기사_under_score")
    backslash = add(owner, message="path\\part")
    add(owner, message="일반 로그")
    for query, expected in (("%", percentage), ("_", underscore), ("\\", backslash)):
        assert [item["id"] for item in read(owner, q=query).json()["items"]] == [expected]
    assert read(owner, q="' OR 1=1 --").json()["items"] == []


def test_time_filter_uses_server_reception_not_reported_occurrence(owner):
    ids = []
    for reception in (10, 20, 30):
        identifier = add(owner, message=f"event {reception}")
        ids.append(identifier)
        with get_session() as db:
            row = db.get(RunSessionEvent, identifier)
            row.created_ms = reception
            db.add(row)
            db.commit()
    assert [item["id"] for item in read(owner, since_ms=20, until_ms=20).json()["items"]] == [ids[1]]


def test_context_preserves_nonmatching_neighbors_and_same_session_owner_scope(owner):
    older = add(owner, severity="INFO", message="조건 판정")
    anchor = add(owner, severity="ERROR", message="주문 실패")
    newer = add(owner, severity="INFO", message="연결 복구")
    body = read(owner, context_id=anchor, severity="ERROR", q="불일치", limit=3).json()
    assert [item["id"] for item in body["items"]] == [newer, anchor, older]
    assert [item["id"] for item in body["items"] if item["context_anchor"]] == [anchor]
    assert body["context_window"] == {"anchor_id": anchor, "oldest_id": older,
                                       "newest_id": newer, "filters_ignored": True}
    assert body["has_more"] is False and body["next_before_id"] is None
    with get_session() as db:
        other_session = RunSession(user_id=owner[0], started_at=NOW)
        db.add(other_session)
        db.flush()
        other_event = RunSessionEvent(user_id=owner[0], session_id=other_session.id, ts=NOW, created_ms=0)
        db.add(other_event)
        db.commit()
        db.refresh(other_event)
        foreign_id = other_event.id
    assert read(owner, context_id=foreign_id).status_code == 404
    assert read(owner, context_id=2**63 - 1).status_code == 404


def test_context_window_fills_at_history_edges_and_is_bounded(owner):
    ids = [add(owner, message=f"event {index}") for index in range(5)]
    for anchor in (ids[0], ids[-1]):
        body = read(owner, context_id=anchor, limit=4).json()
        assert len(body["items"]) == 4 and anchor in [item["id"] for item in body["items"]]
    assert len(read(owner, context_id=ids[2], limit=1).json()["items"]) == 1


def test_invalid_queries_are_rejected_without_expanding_scope(owner):
    for params in ({"q": "x" * 201}, {"severity": "CRITICAL"}, {"operation_id": "a b"},
                   {"component": "x" * 49}, {"since_ms": 30, "until_ms": 10},
                   {"until_ms": -1}, {"context_id": 0}):
        assert read(owner, **params).status_code == 422


def test_search_and_context_are_read_only_and_operation_lookup_has_index(owner):
    identifier = add(owner, severity="INFO", component="engine", operation_id="command:7", message="test")
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())
    event.listen(db_mod._engine, "before_cursor_execute", capture)
    try:
        for params in ({"q": "test"}, {"operation_id": "command:7"}, {"context_id": identifier}):
            assert read(owner, **params).status_code == 200
    finally:
        event.remove(db_mod._engine, "before_cursor_execute", capture)
    assert statements and all(sql.startswith("SELECT") for sql in statements)
    indexes = {index["name"] for index in inspect(db_mod._engine).get_indexes("runsessionevent")}
    assert "ix_runsessionevent_session_operation_id_id" in indexes


def test_old_v1_signed_receipt_remains_accepted_without_invented_fields(owner):
    import base64
    import hashlib
    import hmac
    now = agent_logs._now_ms()
    claim = {"v": 1, "uid": owner[0], "sid": owner[1], "iat": now,
             "exp": now + 600000, "key": "news:" + "1" * 64, "kind": "news",
             "message": "기존 수신 확인", "details": {"count": 1}}
    raw = json.dumps(claim, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = hmac.new(auth.SECRET_KEY.encode(), agent_logs._DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
    response = client.post(f"/api/me/agents/sessions/{owner[1]}/news-receipts", headers=owner[2],
                           json={"receipt_token": encoded + "." + signature})
    assert response.status_code == 200
    item = read(owner).json()["items"][0]
    assert item["severity"] == "UNKNOWN" and item["operation_id"] == ""
    assert item["event_name"] == "news.response_received" and item["component"] == "news_delivery"
    assert item["details"]["response_issued_at"]


def test_new_receipts_link_actual_collection_and_do_not_promise_terminal_retry(owner):
    token = agent_logs.make_news_receipt(owner[0], {"session_id": owner[1], "status": "running"}, {
        "context": {"asset_symbol": "BTC"}, "items": [], "snapshot_id": "snapshot-BTC",
        "collection": {"status": "error", "collection_run_id": "actual-flow-id",
            "flow_run_id": "actual-flow-id", "task_run_id": "actual-task-id",
            "http_status": 429, "error_code": "insufficient_quota", "attempt": 2, "max_attempts": 2,
            "terminal": True, "retryable": False, "last_success_at": NOW},
        "translation": {"status": "error"}})["receipt_token"]
    agent_logs.record_news_receipt(owner[0], owner[1], token)
    item = read(owner).json()["items"][0]
    assert item["operation_id"] == "collection:actual-flow-id"
    assert item["severity"] == "ERROR" and item["details"]["http_status"] == 429
    assert item["details"]["flow_run_id"] == "actual-flow-id"
    assert item["details"]["terminal"] is True and item["details"]["retryable"] is False
    assert "자동 재" not in item["message"]


def test_explicit_timestamp_details_reject_naive_or_invalid_values():
    assert safe_details({"collected_at": "2026-10-13T10:00:00", "last_success_at": "bad", "retry_at": []}) == {}
    assert safe_details({"collected_at": NOW}) == {"collected_at": "2026-10-13T02:01:02+00:00"}


def test_source_results_are_bounded_leaf_facts_not_recursive_payloads():
    payload = {"source_results": [{"source": "CoinDesk", "status": "error", "http_status": 429,
        "attempted": True, "duration_ms": 15, "error": "api_key=private " + "x" * 400,
        "source_results": [{"source_results": [{}]}], "api_key": "forbidden"}] * 12}
    rows = safe_details(payload)["source_results"]
    assert len(rows) == 8 and rows[0]["http_status"] == 429 and rows[0]["attempted"] is True
    assert len(rows[0]["error"]) <= 200 and "private" not in rows[0]["error"]
    assert "source_results" not in rows[0] and "api_key" not in rows[0]


@pytest.mark.parametrize("url", ["https://localhost/a", "https://api.internal/a", "https://node.local/a",
    "https://node.localdomain/a", "https://node.localhost/a", "https://127.0.0.1/a", "https://127.1/a",
    "https://2130706433/a", "https://api/a", "https://coindesk.com/sk-abcdefghijklmnop"])
def test_private_links_and_credential_paths_are_not_exposed(url):
    assert safe_url(url) == ""
