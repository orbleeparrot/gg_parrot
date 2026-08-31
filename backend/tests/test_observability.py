from __future__ import annotations

import asyncio
import json
from contextvars import copy_context
from inspect import getsource

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlmodel import create_engine

from app import ai_runtime, db, http_runtime
from app import observability


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _asgi_request(
    application,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    client_host: str = "127.0.0.1",
    swallow_app_error: bool = False,
):
    sent = []
    delivered = False
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    try:
        await application(scope, receive, send)
    except Exception:
        if not swallow_app_error:
            raise
    return sent


def _response_start(messages):
    return next(message for message in messages if message["type"] == "http.response.start")


def test_request_id_is_sanitized_and_server_timing_aggregates_spans():
    assert observability.normalize_request_id("trace-123") == "trace-123"
    assert observability.normalize_request_id("bad value\n") != "bad value\n"

    token = observability.begin_trace("trace-123")
    try:
        observability.record_timing("db", 12.25)
        observability.record_timing("db", 7.75)
        observability.record_timing("ai", 50.0)
        header = observability.server_timing_header(100.0)
    finally:
        observability.end_trace(token)

    assert "app;dur=100.00" in header
    assert 'db;dur=20.00;desc="2 calls"' in header
    assert 'ai;dur=50.00;desc="1 call"' in header


def test_asgi_middleware_adds_trace_headers():
    sent = []

    async def downstream(_scope, _receive, send):
        observability.record_timing("db", 3.5)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = observability.ObservabilityMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "headers": [(b"x-request-id", b"from-client")],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    _run(middleware(scope, receive, send))
    start = sent[0]
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    assert headers["x-request-id"] == "from-client"
    assert "app;dur=" in headers["server-timing"]
    assert "db;dur=3.50" in headers["server-timing"]


def test_uncaught_500_keeps_trace_and_cors_headers():
    inner = FastAPI()

    @inner.get("/api/boom")
    async def boom():
        raise RuntimeError("boom")

    application = observability.observe_application(
        inner,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    messages = _run(_asgi_request(
        application,
        "/api/boom",
        headers={"Origin": "http://localhost:5173", "X-Request-Id": "boom-123"},
        swallow_app_error=True,
    ))
    start = _response_start(messages)
    headers = {key.decode(): value.decode() for key, value in start["headers"]}

    assert start["status"] == 500
    assert headers["x-request-id"] == "boom-123"
    assert "app;dur=" in headers["server-timing"]
    assert headers["access-control-allow-origin"] == "*"


def test_rum_payload_is_bounded_and_rejects_query_strings():
    payload = observability.RumBatch(
        page_id="page-123",
        route="/agents",
        metrics=[{"name": "CLS", "value": 0.03, "rating": "good"}],
    )
    assert payload.metrics[0].name == "CLS"
    with pytest.raises(ValidationError):
        observability.RumBatch(
            page_id="page-123",
            route="/agents?session=secret",
            metrics=[{"name": "CLS", "value": 0.03, "rating": "good"}],
        )


def test_rum_routes_are_normalized_to_a_bounded_template_set():
    shared = {
        "page_id": "page-123",
        "metrics": [{"name": "CLS", "value": 0.03, "rating": "good"}],
    }

    assert observability.RumBatch(route="/agents", **shared).route == "/agents"
    assert observability.RumBatch(route="/s/private-share-slug", **shared).route == "/s/:id"
    assert observability.RumBatch(route="/board/92831", **shared).route == "/board/:id"
    assert observability.RumBatch(route="/unexpected/private-value", **shared).route == "/other"


def _rum_app(monkeypatch, *, client_limit: int = 20, global_limit: int = 100):
    monkeypatch.setattr(
        observability,
        "_rum_client_limiter",
        observability.SlidingWindowRateLimiter(
            limit=client_limit,
            window_seconds=60,
            max_keys=100,
        ),
    )
    monkeypatch.setattr(
        observability,
        "_rum_global_limiter",
        observability.SlidingWindowRateLimiter(
            limit=global_limit,
            window_seconds=60,
            max_keys=1,
        ),
    )
    inner = FastAPI()
    inner.include_router(observability.router)
    return inner


def _post_rum(application, payload: dict, *, client_host: str = "127.0.0.1"):
    body = json.dumps(payload).encode("utf-8")
    messages = _run(_asgi_request(
        application,
        "/api/observability/rum",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
        body=body,
        client_host=client_host,
    ))
    return _response_start(messages)


def test_rum_endpoint_rejects_body_before_unbounded_json_parsing(monkeypatch):
    application = _rum_app(monkeypatch)
    response = _post_rum(application, {
        "page_id": "page-123",
        "route": "/agents",
        "metrics": [{"name": "CLS", "value": 0.03, "rating": "good"}],
        "ignored_padding": "x" * (observability.RUM_MAX_BODY_BYTES + 1),
    })

    assert response["status"] == 413


def test_rum_endpoint_has_a_server_side_per_client_rate_limit(monkeypatch):
    application = _rum_app(monkeypatch, client_limit=1)
    payload = {
        "page_id": "page-123",
        "route": "/agents",
        "metrics": [{"name": "CLS", "value": 0.03, "rating": "good"}],
    }

    assert _post_rum(application, payload)["status"] == 204
    limited = _post_rum(application, payload)
    headers = {key.decode(): value.decode() for key, value in limited["headers"]}
    assert limited["status"] == 429
    assert int(headers["retry-after"]) >= 1


def test_client_limited_rum_does_not_consume_the_global_budget(monkeypatch):
    application = _rum_app(monkeypatch, client_limit=1, global_limit=2)
    payload = {
        "page_id": "page-123",
        "route": "/agents",
        "metrics": [{"name": "CLS", "value": 0.03, "rating": "good"}],
    }

    assert _post_rum(application, payload, client_host="192.0.2.1")["status"] == 204
    assert _post_rum(application, payload, client_host="192.0.2.1")["status"] == 429
    assert _post_rum(application, payload, client_host="192.0.2.2")["status"] == 204


def test_db_http_and_ai_paths_emit_trace_spans():
    assert "before_cursor_execute" in getsource(db)
    assert 'record_timing("sql"' in getsource(db)
    assert 'record_timing("upstream"' in getsource(http_runtime)
    assert 'timed_operation("ai")' in getsource(ai_runtime)


def test_traced_session_records_sql_and_total_db_operation_time_once(monkeypatch):
    spans = []
    engine = create_engine("sqlite://", echo=False)
    event.listen(engine, "before_cursor_execute", db.before_cursor_execute)
    event.listen(engine, "after_cursor_execute", db.after_cursor_execute)
    event.listen(engine, "handle_error", db.handle_query_error)
    monkeypatch.setattr(db, "record_timing", lambda name, duration: spans.append((name, duration)))

    session = db.TracedSession(engine)
    session.exec(text("SELECT 1")).all()
    session.close()
    session.close()
    engine.dispose()

    assert [name for name, _duration in spans].count("sql") == 1
    assert [name for name, _duration in spans].count("db") == 2
    assert all(duration >= 0 for _name, duration in spans)


def test_background_context_stops_recording_after_request_finishes():
    token = observability.begin_trace("request-done")
    inherited = copy_context()
    observability.end_trace(token)
    inherited.run(observability.record_timing, "db", 99.0)
    assert "db;dur=" not in inherited.run(observability.server_timing_header, 1.0)
