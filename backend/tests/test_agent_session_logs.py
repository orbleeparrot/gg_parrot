"""Session audit history, signed news receipts and isolation (no provider I/O)."""
from __future__ import annotations

from copy import deepcopy
import json
import secrets

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select

from app import auth, db as db_mod, runner
from app.agent_features.position_news import router as news_router
from app.db import RunSession, RunSessionEvent, User, get_session
from app.main import app

client = TestClient(app)
_NOW = "2026-10-12T01:00:00+00:00"


@pytest.fixture
def account(monkeypatch):
    # All identities live in conftest's newly created temporary SQLite DB.
    suffix = secrets.token_hex(6)
    with get_session() as db:
        user = User(email=f"logs-{suffix}@example.invalid", username=f"logs_{suffix}",
                    password_hash="not-a-login-password", created_at=_NOW)
        db.add(user)
        db.commit()
        db.refresh(user)
        uid = user.id
        session = RunSession(user_id=uid, symbol="BTCUSDT", market="spot", mode="mock",
                             started_at=_NOW, last_heartbeat_at=_NOW)
        db.add(session)
        db.commit()
        db.refresh(session)
        sid = session.id
    monkeypatch.setattr("requests.sessions.Session.request", lambda *_a, **_kw: pytest.fail("provider HTTP forbidden"))
    return uid, sid, {"Authorization": f"Bearer {auth.make_token(uid, 0)}"}


def _payload(**changes):
    payload = {
        "context": {"session_id": 1, "asset_symbol": "BTC"}, "cursor": 21,
        "snapshot_id": "articles:BTC", "items": [
            {"id": "btc-a", "title": "비트코인 신규 ETF 승인 기사", "source": "CoinDesk",
             "url": "https://www.coindesk.com/markets/article?token=do-not-leak", "published": _NOW},
        ], "collection": {"status": "ready", "last_success_at": _NOW, "last_success_ms": 1791766800000},
        "translation": {"status": "ready", "pending_count": 0}, "updated_at": _NOW,
    }
    payload.update(changes)
    return payload


def _news(account, monkeypatch, payload=None):
    uid, sid, headers = account
    monkeypatch.setattr(news_router.service, "get_position_news", lambda *_a, **_kw: deepcopy(payload or _payload()))
    response = client.get(f"/api/me/agents/sessions/{sid}/position-news", headers=headers)
    assert response.status_code == 200, response.text
    return response


def _receipt(account, token):
    _uid, sid, headers = account
    return client.post(f"/api/me/agents/sessions/{sid}/news-receipts", headers=headers,
                       json={"receipt_token": token})


def _event(account, kind, message, **kwargs):
    uid, sid, _headers = account
    with get_session() as db:
        row = RunSessionEvent(session_id=sid, user_id=uid, kind=kind, message=message,
                              ts=kwargs.pop("ts", _NOW), created_ms=1791766800000, **kwargs)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_first_news_response_is_read_only_and_receipt_is_durable_idempotent(account, monkeypatch):
    response = _news(account, monkeypatch)
    token = response.json().get("receipt_token")
    assert token, "owner news GET must issue a receipt without writing history"
    assert response.json()["receipt_key"]
    assert response.headers["cache-control"] == "private, no-store"
    uid, sid, headers = account
    with get_session() as db:
        assert db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() == []
    first = _receipt(account, token)
    assert first.status_code == 200, first.text
    assert first.json()["recorded"] is True
    repeated = _receipt(account, token)
    assert repeated.json()["recorded"] is False
    assert repeated.json()["event_id"] == first.json()["event_id"]
    assert first.json()["receipt_key"] == response.json()["receipt_key"]
    assert first.json()["article_ids"] == ["btc-a"]
    logs = client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers)
    assert logs.status_code == 200
    assert logs.headers["cache-control"] == "private, no-store"
    assert len(logs.json()["items"]) == 1
    item = logs.json()["items"][0]
    assert item["kind"] == "news" and item["source"] == "news_receipt"
    assert item["details"]["count"] == 1
    assert "화면" in item["message"]
    assert item["received_at"] and item["ts_kst"]
    assert "do-not-leak" not in json.dumps(item)
    assert "읽" not in item["message"]


@pytest.mark.parametrize("bad_name", [[], {}, 123, None, "runner.order_result"])
def test_observation_endpoint_rejects_malformed_or_impersonated_event_names(account, bad_name):
    uid, sid, headers = account
    payload = {"client_event_id": "3e544af7-30fa-42ec-9fbb-4ca2c0635a3f", "event_name": bad_name,
               "observed_at": _NOW, "details": {"title": "실제 관측"}}
    response = client.post(f"/api/me/agents/sessions/{sid}/observations", headers=headers, json=payload)
    assert response.status_code == 422
    with get_session() as db:
        assert not db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()


def test_observation_endpoint_saves_exactly_one_source_and_returns_its_owner_scoped_identity(account):
    uid, sid, headers = account
    payload = {"client_event_id": "3e544af7-30fa-42ec-9fbb-4ca2c0635a3f",
               "event_name": "browser.connection_observed", "observed_at": _NOW,
               "details": {"title": "뉴스 조회 실패 관측", "summary": "HTTP 응답을 확인하지 못했어요.", "state": "transport_error"}}
    first = client.post(f"/api/me/agents/sessions/{sid}/observations", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/me/agents/sessions/{sid}/observations", headers=headers, json=payload)
    assert first.json()["event_id"] == second.json()["event_id"]
    assert first.json()["event"]["session_id"] == sid
    assert first.json()["event"]["component"] == "browser_observation"
    assert second.json()["recorded"] is False
    stored = client.get(f"/api/me/agents/sessions/{sid}/logs?context_id={first.json()['event_id']}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["items"][0]["id"] == first.json()["event_id"]


def test_observation_body_is_bounded_even_for_invalid_session_path(account):
    _uid, _sid, headers = account
    response = client.post("/api/me/agents/sessions/-1/observations", headers=headers, content=" " * 8193)
    assert response.status_code == 413


@pytest.mark.parametrize("exception_name,status", [("NewsTranslationBusyError", 429), ("NewsTranslationError", 503)])
def test_news_response_generation_failure_is_not_a_browser_receipt(account, monkeypatch, exception_name, status):
    from app import news
    def fail(*_args, **_kwargs):
        raise getattr(news, exception_name)("뉴스 처리 실패 api_key=private-key Bearer private-token")
    monkeypatch.setattr(news_router.service, "get_position_news", fail)
    _uid, sid, headers = account
    for _ in range(5):
        response = client.get(f"/api/me/agents/sessions/{sid}/position-news", headers=headers)
        assert response.status_code == status
        assert response.headers["cache-control"] == "private, no-store"
    logs = client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).json()["items"]
    assert len(logs) == 1, "Repeated failed polling must not amplify DB events"
    item = logs[0]
    assert item["event_name"] == "news.request_failed" and item["component"] == "server"
    assert item["source"] == "news_request" and item["severity"] == "ERROR"
    assert item["details"]["http_status"] == status
    assert item["details"]["outcome"] == "response_not_created"
    assert item["details"]["verification"] == "server_response_not_browser_receipt"
    assert not {"count", "examples", "flow_run_id", "task_run_id"} & item["details"].keys()
    assert item["operation_id"] == ""
    assert "private-key" not in json.dumps(item) and "private-token" not in json.dumps(item)
    assert "수신 확인" not in item["source_label"]


def test_news_generation_failure_does_not_write_to_ended_session(account, monkeypatch):
    from app import news
    _uid, sid, headers = account
    with get_session() as db:
        session = db.get(RunSession, sid)
        session.status = "stopped"
        db.add(session)
        db.commit()
    monkeypatch.setattr(news_router.service, "get_position_news", lambda *_a, **_kw: (_ for _ in ()).throw(news.NewsTranslationError("실패")))
    assert client.get(f"/api/me/agents/sessions/{sid}/position-news", headers=headers).status_code == 503
    assert client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).json()["items"] == []


def test_news_generation_log_failure_never_changes_http_error_or_retries_service(account, monkeypatch):
    from app import agent_logs, news
    called = []
    def fail(*_a, **_kw):
        called.append(True)
        raise news.NewsTranslationBusyError("요청 대기")
    monkeypatch.setattr(news_router.service, "get_position_news", fail)
    monkeypatch.setattr(agent_logs, "record_news_request_failure", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("sink unavailable")))
    _uid, sid, headers = account
    response = client.get(f"/api/me/agents/sessions/{sid}/position-news", headers=headers)
    assert response.status_code == 429 and len(called) == 1


def test_token_refresh_has_stable_key_and_repeated_poll_does_not_write(account, monkeypatch):
    from app import agent_logs
    monkeypatch.setattr(agent_logs, "_now_ms", lambda: 1791766800000)
    first = _news(account, monkeypatch).json()
    monkeypatch.setattr(agent_logs, "_now_ms", lambda: 1791766803000)
    second = _news(account, monkeypatch).json()
    assert first["receipt_key"] == second["receipt_key"]
    assert first["receipt_token"] != second["receipt_token"]
    assert _receipt(account, second["receipt_token"]).json()["recorded"] is True
    assert _receipt(account, first["receipt_token"]).json()["recorded"] is False


@pytest.mark.parametrize("status", ["pending", "empty", "error"])
def test_collection_statuses_have_safe_receipts_without_claiming_news_articles(account, monkeypatch, status):
    payload = _payload(items=[], collection={"status": status, "last_attempt_at": _NOW,
                         "last_error": "api_key=never-print https://secret.invalid/?key=private"})
    data = _news(account, monkeypatch, payload).json()
    assert _receipt(account, data["receipt_token"]).status_code == 200
    _uid, sid, headers = account
    item = client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).json()["items"][0]
    assert item["details"]["count"] == 0
    assert item["details"]["collection_status"] == status
    assert item["kind"] == ("news_error" if status == "error" else "news")
    assert "never-print" not in json.dumps(item) and "private" not in json.dumps(item)


def test_distinct_news_batch_and_status_are_not_deduped_as_one(account, monkeypatch):
    initial = _news(account, monkeypatch).json()
    assert _receipt(account, initial["receipt_token"]).json()["recorded"]
    changed = _news(account, monkeypatch, _payload(cursor=22)).json()
    assert initial["receipt_key"] != changed["receipt_key"]
    assert _receipt(account, changed["receipt_token"]).json()["recorded"]
    pending = _news(account, monkeypatch, _payload(items=[], translation={"status": "partial", "pending_count": 3})).json()
    assert _receipt(account, pending["receipt_token"]).json()["recorded"]


def test_receipts_bound_to_owner_and_session_and_unauthenticated_denied(account, monkeypatch):
    token = _news(account, monkeypatch).json()["receipt_token"]
    uid, sid, headers = account
    with get_session() as db:
        other = User(email=f"other-{secrets.token_hex(6)}@example.invalid", username=f"other_{secrets.token_hex(6)}",
                     password_hash="not-real", created_at=_NOW)
        db.add(other)
        other_session = RunSession(user_id=uid, symbol="ETHUSDT", started_at=_NOW)
        db.add(other_session)
        db.commit()
        db.refresh(other)
        db.refresh(other_session)
        other_headers = {"Authorization": f"Bearer {auth.make_token(other.id, 0)}"}
        other_sid = other_session.id
    assert client.get(f"/api/me/agents/sessions/{sid}/logs", headers=other_headers).status_code == 404
    assert client.post(f"/api/me/agents/sessions/{sid}/news-receipts", headers=other_headers,
                       json={"receipt_token": token}).status_code == 404
    assert client.post(f"/api/me/agents/sessions/{other_sid}/news-receipts", headers=headers,
                       json={"receipt_token": token}).status_code == 422
    assert client.get(f"/api/me/agents/sessions/{sid}/logs").status_code == 401


def test_expired_tampered_and_oversized_receipts_rejected_without_echo(account, monkeypatch):
    from app import agent_logs
    monkeypatch.setattr(agent_logs, "_now_ms", lambda: 1791766800000)
    token = _news(account, monkeypatch).json()["receipt_token"]
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    response = _receipt(account, tampered)
    assert response.status_code == 422 and tampered not in response.text
    monkeypatch.setattr(agent_logs, "_now_ms", lambda: 1791767400001)
    response = _receipt(account, token)
    assert response.status_code == 422 and token not in response.text
    response = _receipt(account, "private-token-" * 1000)
    assert response.status_code == 422 and "private-token" not in response.text


def test_ended_session_does_not_issue_or_record_today_news(account, monkeypatch):
    token = _news(account, monkeypatch).json()["receipt_token"]
    _uid, sid, _headers = account
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.status = "stopped"
        db.add(row)
        db.commit()
    assert not _news(account, monkeypatch).json().get("receipt_token")
    assert _receipt(account, token).status_code == 409


def test_logs_keyset_pages_are_stable_when_new_rows_arrive(account):
    ids = [_event(account, "fill", f"거래 {i}") for i in range(4)]
    _uid, sid, headers = account
    first = client.get(f"/api/me/agents/sessions/{sid}/logs?limit=2", headers=headers).json()
    assert [x["id"] for x in first["items"]] == ids[::-1][:2]
    assert first["has_more"] and first["next_before_id"] == ids[2]
    _event(account, "fill", "새 거래")
    second = client.get(f"/api/me/agents/sessions/{sid}/logs?limit=2&before_id={first['next_before_id']}", headers=headers).json()
    assert [x["id"] for x in second["items"]] == ids[::-1][2:]
    assert not second["has_more"] and second["next_before_id"] is None


@pytest.mark.parametrize("category,kinds", [
    ("trade", {"order", "fill"}), ("execution", {"start", "stop", "info", "signal", "warn"}),
    ("news", {"news", "news_error"}), ("error", {"error", "news_error"}),
])
def test_logs_filters_apply_before_pagination(account, category, kinds):
    for kind in ("start", "fill", "info", "news", "news_error", "order", "error", "signal", "stop", "warn"):
        _event(account, kind, kind)
    _uid, sid, headers = account
    body = client.get(f"/api/me/agents/sessions/{sid}/logs?category={category}&limit=100", headers=headers).json()
    assert {item["kind"] for item in body["items"]} == kinds


def test_logs_bounds_secret_scrubbing_and_original_time_fallback(account):
    _event(account, "error", "api_key=hidden-token Authorization: Bearer hidden-auth https://api.test/a?key=hidden-url", ts="not-a-timestamp")
    _uid, sid, headers = account
    response = client.get(f"/api/me/agents/sessions/{sid}/logs?limit=99999", headers=headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ts"] == item["received_at"]
    assert "hidden-" not in item["message"]
    assert response.json()["retention"]["event_cap"] == runner.EVENT_CAP
    for query in ("category=invalid", "before_id=-1", "limit=0"):
        assert client.get(f"/api/me/agents/sessions/{sid}/logs?{query}", headers=headers).status_code == 422


def test_news_cap_preserves_trade_logs_and_session_delete_cleans_up(account, monkeypatch):
    monkeypatch.setattr(runner, "EVENT_CAP", 10)
    _event(account, "order", "보존해야 할 매수")
    _event(account, "fill", "보존해야 할 매도")
    for i in range(12):
        data = _news(account, monkeypatch, _payload(cursor=100 + i)).json()
        assert _receipt(account, data["receipt_token"]).status_code == 200
    uid, sid, headers = account
    items = client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).json()["items"]
    assert sum(item["kind"] == "news" for item in items) == 2
    assert {item["kind"] for item in items} == {"news", "order", "fill"}
    with get_session() as db:
        runner._delete_session_rows(db, [sid])
        db.commit()
        assert db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() == []


def test_runner_upload_does_not_gain_news_receipt_privileges(account):
    uid, sid, _headers = account
    with get_session() as db:
        row = db.get(RunSession, sid)
        runner._append_events(db, row, [{"kind": "news", "message": "fake receipt"}])
        db.commit()
        event = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).one()
        assert event.kind == "info" and event.event_key is None


def test_legacy_runner_events_do_not_duplicate_new_news_receipts(account, monkeypatch):
    _event(account, "fill", "실행기 체결")
    token = _news(account, monkeypatch).json()["receipt_token"]
    assert _receipt(account, token).status_code == 200
    _uid, sid, headers = account
    data = client.get(f"/api/me/runner/sessions/{sid}/events", headers=headers).json()
    assert [item["kind"] for item in data["events"]] == ["fill"]
    assert set(data["events"][0]) == {"id", "ts", "ts_kst", "kind", "message"}


def test_concurrent_replayed_receipts_have_one_atomic_insert(account, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    token = _news(account, monkeypatch).json()["receipt_token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: _receipt(account, token), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["recorded"] for response in responses) == [False, True]
    assert len({response.json()["event_id"] for response in responses}) == 1


def test_non_actionable_delta_clears_prior_receipt(account, monkeypatch):
    data = _news(account, monkeypatch, _payload(items=[])).json()
    assert "receipt_token" in data and data["receipt_token"] is None
    assert "receipt_key" in data and data["receipt_key"] is None


def test_bad_receipt_bodies_are_not_echoed_and_do_not_accept_freeform_logs(account):
    _uid, sid, headers = account
    for body in (None, "private-opaque-token", {"receipt_token": ["private-opaque-token"]},
                 {"receipt_token": "private-opaque-token", "message": "fake log"}):
        response = client.post(f"/api/me/agents/sessions/{sid}/news-receipts", headers=headers, json=body)
        assert response.status_code == 422
        assert "private-opaque-token" not in response.text and "fake log" not in response.text


@pytest.mark.parametrize("chunked", [False, True])
def test_receipt_body_is_bounded_before_authentication_or_json_parsing(account, chunked):
    from app import agent_logs
    _uid, sid, _headers = account
    raw = b"private-token" * agent_logs.RECEIPT_MAX_BODY_BYTES
    content = iter([raw[:100], raw[100:]]) if chunked else raw
    response = client.post(f"/api/me/agents/sessions/{sid}/news-receipts", content=content,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.headers["cache-control"] == "private, no-store"
    assert "private-token" not in response.text


def test_receipt_metadata_failure_does_not_block_news(account, monkeypatch, caplog):
    from app import agent_logs
    monkeypatch.setattr(agent_logs, "make_news_receipt", lambda *_a: (_ for _ in ()).throw(RuntimeError("api_key=secret")))
    response = _news(account, monkeypatch)
    assert response.json()["items"][0]["id"] == "btc-a"
    assert response.json()["receipt_token"] is None
    assert "api_key=secret" not in caplog.text


def test_log_get_has_no_writes_and_prepared_news_does_not_call_providers(account, monkeypatch):
    from sqlalchemy import event
    from app.agent_features.position_news import repository, classifier
    from app import news as news_mod
    from app import agent_logs
    uid, sid, headers = account
    feed = _payload()
    feed["symbol"] = "BTC"
    feed["coin_name"] = "비트코인"
    feed.update(reset=True, has_more=False, analysis={"items": [{}], "analysis_source": "rule", "analysis_status": "ready"})
    monkeypatch.setattr(repository, "read_article_feed", lambda *_a, **_kw: deepcopy(feed))
    monkeypatch.setattr(repository, "get_collection_state", lambda *_a, **_kw: deepcopy(feed["collection"]))
    forbidden = lambda *_a, **_kw: pytest.fail("read endpoint must not call source/AI")
    monkeypatch.setattr(news_mod, "_request_korean_title_translations", forbidden)
    monkeypatch.setattr(classifier, "_generate_ai_analysis", forbidden)
    monkeypatch.setattr(repository, "reserve_ai_budget", forbidden)
    monkeypatch.setattr(repository, "claim_collection", forbidden)
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())
    event.listen(db_mod._engine, "before_cursor_execute", capture)
    try:
        assert client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).status_code == 200
        response = client.get(f"/api/me/agents/sessions/{sid}/position-news", headers=headers)
        assert response.status_code == 200 and response.json()["receipt_token"]
    finally:
        event.remove(db_mod._engine, "before_cursor_execute", capture)
    assert statements and all(sql.startswith("SELECT") for sql in statements)


def test_receipt_storage_failure_is_safe_and_trade_events_are_untouched(account, monkeypatch, caplog):
    from app import agent_logs
    _event(account, "order", "existing trade")
    token = _news(account, monkeypatch).json()["receipt_token"]
    monkeypatch.setattr(runner, "_trim_events", lambda *_a: (_ for _ in ()).throw(RuntimeError("provider-key=secret")))
    response = _receipt(account, token)
    assert response.status_code == 503
    assert "provider-key" not in response.text and "provider-key" not in caplog.text
    uid, sid, headers = account
    data = client.get(f"/api/me/agents/sessions/{sid}/logs", headers=headers).json()
    assert [item["message"] for item in data["items"]] == ["existing trade"]


def test_postgres_additive_schema_orders_columns_before_indexes_and_preserves_private_access():
    columns = {(table.name, column.name): "text" for table in db_mod.SQLModel.metadata.tables.values()
               for column in table.columns}
    for table, names in db_mod._PG_BIGINT_COLUMNS.items():
        columns.update({(table, column): "bigint" for column in names})
    state = {"tables": {table.name: table.name in db_mod._PG_PRIVATE_CACHE_TABLES
                        for table in db_mod.SQLModel.metadata.tables.values()},
             "columns": columns, "indexes": set(db_mod._PG_INDEXES) | set(db_mod._PG_UNIQUE_INDEXES),
             "grants": set()}
    for column in ("event_key", "details_json"):
        state["columns"].pop(("runsessionevent", column))
    for index in ("ux_runsessionevent_session_key", "ix_runsessionevent_session_id_id"):
        state["indexes"].discard(index)
    statements = db_mod._pg_migration_statements(state)
    assert statements == [
        "ALTER TABLE runsessionevent ADD COLUMN IF NOT EXISTS event_key TEXT",
        "ALTER TABLE runsessionevent ADD COLUMN IF NOT EXISTS details_json TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_runsessionevent_session_id_id ON runsessionevent (session_id, id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_runsessionevent_session_key ON runsessionevent (session_id, event_key)",
    ]
    assert not any("DROP" in statement or "GRANT" in statement or "DISABLE" in statement for statement in statements)


def test_additive_schema_upgrade_preserves_legacy_events(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect
    engine = create_engine(f"sqlite:///{tmp_path / 'old-events.db'}")
    monkeypatch.setattr(db_mod, "_engine", engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE runsessionevent (
          id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          ts VARCHAR NOT NULL, kind VARCHAR NOT NULL, message VARCHAR NOT NULL, created_ms BIGINT NOT NULL)""")
        conn.exec_driver_sql("INSERT INTO runsessionevent VALUES (1,1,1,'2026-10-12T01:00:00Z','fill','preserved trade',100)")
    try:
        db_mod.init_db()
        db_mod.init_db()
        with db_mod.get_session() as db:
            row = db.get(RunSessionEvent, 1)
            assert row.message == "preserved trade" and row.event_key is None and row.details_json == ""
        indexes = {item["name"] for item in inspect(engine).get_indexes("runsessionevent")}
        assert {"ux_runsessionevent_session_key", "ix_runsessionevent_session_id_id"}.issubset(indexes)
    finally:
        engine.dispose()
