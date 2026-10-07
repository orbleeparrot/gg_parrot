"""화면 오류 모으기 — 같은 날 같은 오류는 한 행에 횟수만, 30일 지난 날은 지우고, 문장·경로에서 개인 값을 떼며,
엔드포인트는 모양이 틀리거나 너무 잦으면 받지 않고, 관리자만 모아 본다(2026-10-07 결정 8).
"""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from app import client_errors, observability
from app.db import ClientError, User, get_session
from app.main import app

client = TestClient(app)
DAY_MS = 86_400_000
NOON_KST = 1_791_342_000_000  # 2026-10-07 12:00 KST


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with get_session() as db:
        db.exec(delete(ClientError))
        db.commit()
    monkeypatch.setattr(observability, "_error_client_limiter", observability.SlidingWindowRateLimiter(limit=1_000, window_seconds=60, max_keys=100))
    monkeypatch.setattr(observability, "_error_global_limiter", observability.SlidingWindowRateLimiter(limit=1_000, window_seconds=60, max_keys=1))
    yield


def _rows():
    with get_session() as db:
        return db.exec(select(ClientError).order_by(ClientError.day_kst)).all()


def test_message_loses_query_strings_emails_and_extra_lines():
    text = client_errors.sanitize_message("Failed  to load\nhttps://x.com/a/b.js?token=secret#frag for kim@example.com")
    assert text == "Failed to load https://x.com/a/b.js for [email]"
    assert len(client_errors.sanitize_message("가" * 1_000)) == client_errors.MAX_MESSAGE


def test_routes_keep_screen_templates_and_hide_ids():
    assert client_errors.normalize_route("/agents") == "/agents"
    assert client_errors.normalize_route("/mypage/settings?tab=security") == "/mypage/settings"
    assert client_errors.normalize_route("/board/92831") == "/board/:id"
    assert client_errors.normalize_route("/board/92831/edit") == "/board/:id/edit"
    assert client_errors.normalize_route("/s/private-share-slug") == "/s/:id"
    assert client_errors.normalize_route("/reset/abc/def") == "/other"


def test_same_error_on_the_same_day_is_one_row_with_a_count():
    for offset in (0, 60_000, 120_000):
        client_errors.record("render", "/agents", "Cannot read properties of null", "abc1234", now_ms=NOON_KST + offset)
    client_errors.record("render", "/agents", "Cannot read properties of null", "abc1234", now_ms=NOON_KST + DAY_MS)
    rows = _rows()
    assert [(row.day_kst, row.count) for row in rows] == [("2026-10-07", 3), ("2026-10-08", 1)]
    assert rows[0].first_ms == NOON_KST and rows[0].last_ms == NOON_KST + 120_000


def test_rows_older_than_thirty_days_are_pruned(monkeypatch):
    client_errors.record("unhandled", "/news", "old error", now_ms=NOON_KST - 31 * DAY_MS)
    monkeypatch.setattr(client_errors, "_last_prune", 0.0)
    client_errors.record("unhandled", "/news", "new error", now_ms=NOON_KST)
    assert [row.message for row in _rows()] == ["new error"]


def test_report_merges_days_and_lists_latest_first():
    client_errors.record("chunk", "/board/1", "Failed to fetch dynamically imported module", "aaa1111", now_ms=NOON_KST - DAY_MS)
    client_errors.record("chunk", "/board/2", "Failed to fetch dynamically imported module", "bbb2222", now_ms=NOON_KST)
    client_errors.record("render", "/agents", "x is undefined", now_ms=NOON_KST - 2 * DAY_MS)
    with get_session() as db:
        report = client_errors.report(db, 7, now_ms=NOON_KST)
    assert report["total"] == 3 and report["kinds"]["chunk"] == 2
    first = report["items"][0]
    assert (first["route"], first["count"], first["days"], first["build"]) == ("/board/:id", 2, 2, "bbb2222")


def test_endpoint_records_a_valid_report_and_rejects_bad_shapes():
    ok = client.post("/api/observability/errors", json={"kind": "render", "route": "/agents", "message": "boom", "build": "abc1234"})
    assert ok.status_code == 204
    assert [(row.kind, row.route, row.message, row.count) for row in _rows()] == [("render", "/agents", "boom", 1)]
    for bad in ({"kind": "other", "route": "/agents", "message": "boom"},
                {"kind": "render", "route": "/agents?token=secret", "message": "boom"},
                {"kind": "render", "route": "/agents", "message": "boom", "build": "<script>"}):
        assert client.post("/api/observability/errors", json=bad).status_code == 422


def test_endpoint_has_a_per_client_rate_limit(monkeypatch):
    monkeypatch.setattr(observability, "_error_client_limiter", observability.SlidingWindowRateLimiter(limit=2, window_seconds=60, max_keys=10))
    body = json.dumps({"kind": "unhandled", "route": "/", "message": "loop"})
    codes = [client.post("/api/observability/errors", content=body, headers={"Content-Type": "application/json"}).status_code for _ in range(3)]
    assert codes == [204, 204, 429]


def test_only_admins_read_the_report():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={"email": f"e{tok}@ex.com", "username": f"e_{tok}", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/admin/client-errors", headers=headers).status_code == 403
    with get_session() as db:
        user = db.get(User, body["user"]["id"])
        user.is_admin = True
        db.add(user)
        db.commit()
    client.post("/api/observability/errors", json={"kind": "render", "route": "/news", "message": "boom"})
    report = client.get("/api/admin/client-errors?days=7", headers=headers).json()
    assert report["total"] == 1 and report["items"][0]["route"] == "/news"
