"""알림 실시간 전달: 커밋 뒤 허브 깨우기, LISTEN 브리지, SSE 스트림, 스트림 토큰."""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, notification_stream, notifications
from app.db import get_session
from app.main import app
from app.stream_hub import StreamHub

client = TestClient(app)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"ns{tok}@ex.com", "username": f"ns_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- 허브 ------------------------------------------------------------------
def test_hub_wakes_only_the_subscribed_account_and_broadcast_wakes_everyone():
    async def scenario():
        hub = StreamHub()
        sid_a, ev_a = hub.subscribe(1)
        sid_b, ev_b = hub.subscribe(2)
        assert hub.subscriber_count() == 2
        assert hub.notify(1) == 1
        await asyncio.sleep(0)
        assert ev_a.is_set() and not ev_b.is_set()
        ev_a.clear()
        assert hub.notify_all() == 2
        await asyncio.sleep(0)
        assert ev_a.is_set() and ev_b.is_set()
        hub.unsubscribe(1, sid_a)
        assert hub.notify(1) == 0 and hub.subscriber_count(1) == 0
        hub.unsubscribe(2, sid_b)
        assert hub.subscriber_count() == 0
    asyncio.run(scenario())


def test_hub_notify_is_safe_from_another_thread():
    async def scenario():
        hub = StreamHub()
        _, event = hub.subscribe(7)
        threading.Thread(target=hub.notify, args=(7,)).start()
        await asyncio.wait_for(event.wait(), timeout=2)
    asyncio.run(scenario())


# --- 커밋 뒤에만 깨운다 ------------------------------------------------------
def test_changes_are_dispatched_after_commit_and_dropped_on_rollback(monkeypatch):
    calls = []
    monkeypatch.setattr(notification_stream, "dispatch", lambda payload: calls.append(payload) or 1)
    _, user = _signup()
    with get_session() as db:
        notifications.notify(db, user["id"], "admin", "커밋 전")
        notifications.notify(db, user["id"], "admin", "같은 계정 두 번째")  # 같은 세션·같은 대상은 한 번만
        assert calls == []
        db.commit()
    assert calls == [str(user["id"])]
    calls.clear()
    with get_session() as db:
        notifications.notify(db, user["id"], "admin", "되돌릴 알림")
        notifications.notify(db, None, "notice", "되돌릴 공지")
        db.rollback()
        assert calls == []
        db.commit()  # 아무것도 없는 커밋은 깨우지 않는다
    assert calls == []
    with get_session() as db:
        notifications.notify(db, None, "notice", "전체 공지")
        db.commit()
    assert calls == [notification_stream.BROADCAST]


def test_mark_read_wakes_the_account_so_other_tabs_drop_the_badge(monkeypatch):
    calls = []
    monkeypatch.setattr(notification_stream, "dispatch", lambda payload: calls.append(payload) or 1)
    token, user = _signup()
    with get_session() as db:
        notifications.notify(db, user["id"], "admin", "읽을 알림")
        db.commit()
    calls.clear()
    assert client.post("/api/me/notifications/read", json={"all": True}, headers=_auth(token)).json() == {"unread": 0}
    assert calls == [str(user["id"])]


def test_dispatch_parses_channel_payloads():
    hub = notification_stream.hub
    woken = []
    orig_notify, orig_all = hub.notify, hub.notify_all
    hub.notify = lambda uid: woken.append(uid) or 1
    hub.notify_all = lambda: woken.append("*") or 1
    try:
        assert notification_stream.dispatch("12") == 1
        assert notification_stream.dispatch(" * ") == 1
        assert notification_stream.dispatch("garbage") == 0
        assert woken == [12, "*"]
    finally:
        hub.notify, hub.notify_all = orig_notify, orig_all


# --- LISTEN 브리지 -------------------------------------------------------------
def test_listener_reconnects_with_backoff_and_dispatches_payloads(monkeypatch):
    received = []
    monkeypatch.setattr(notification_stream, "dispatch", lambda payload: received.append(payload) or 1)
    attempts = []
    sleeps = []

    class Note:
        def __init__(self, payload):
            self.payload = payload

    class FakeConn:
        def __init__(self, payloads, fail_after):
            self.payloads, self.fail_after = payloads, fail_after
            self.executed = []
            self.closed = False

        async def execute(self, statement):
            self.executed.append(statement)

        async def notifies(self):
            for payload in self.payloads:
                yield Note(payload)
            if self.fail_after:
                raise ConnectionResetError("dropped")
            await asyncio.Event().wait()  # 두 번째 연결은 계속 열려 있다

        async def close(self):
            self.closed = True

    conns = []

    async def connect(dsn):
        attempts.append(dsn)
        if len(attempts) == 1:
            raise OSError("refused")
        conn = FakeConn(["5", "*"] if len(attempts) == 2 else ["9"], fail_after=len(attempts) == 2)
        conns.append(conn)
        return conn

    async def sleep(seconds):
        sleeps.append(seconds)

    async def scenario():
        task = asyncio.create_task(notification_stream.listen_forever("dsn", connect=connect, sleep=sleep))
        for _ in range(200):
            await asyncio.sleep(0.005)
            if received == ["5", "*", "9"]:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert received == ["5", "*", "9"]
    assert sleeps == [1.0, 1.0], "첫 실패 뒤 1초, 연결 성공으로 초기화된 뒤 다시 1초"
    assert conns[0].executed == [f"LISTEN {notification_stream.CHANNEL}"] and conns[0].closed
    assert len(attempts) == 3


def test_listener_starts_only_on_postgres():
    async def scenario():
        assert notification_stream.start() is None  # 테스트는 SQLite
        await notification_stream.stop()
    asyncio.run(scenario())


# --- 스트림 토큰 + SSE ------------------------------------------------------------
def test_stream_token_is_purpose_bound_and_short_lived():
    token, user = _signup()
    assert client.post("/api/me/notifications/stream-token").status_code == 401
    r = client.post("/api/me/notifications/stream-token", headers=_auth(token))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    stream_token = r.json()["token"]
    assert r.json()["expires_in"] == auth._STREAM_TOKEN_TTL_SECONDS
    assert auth.decode_stream_token(stream_token, auth.NOTIFICATION_STREAM_PURPOSE) == user["id"]
    with pytest.raises(auth.AuthError):
        auth.decode_stream_token(stream_token, "runner_sessions_stream")
    with pytest.raises(auth.AuthError):
        auth.decode_stream_token(token, auth.NOTIFICATION_STREAM_PURPOSE)  # 일반 로그인 토큰은 거절
    # 스트림 토큰으로는 일반 API 를 못 쓴다
    assert client.get("/api/me/notifications", headers=_auth(stream_token)).status_code == 401
    # 스트림 엔드포인트는 토큰 없이는 401
    assert client.get("/api/me/notifications/stream").status_code == 401
    assert client.get("/api/me/notifications/stream", params={"token": token}).status_code == 401


async def _next(gen, timeout=3.0):
    """다음 SSE 조각. 스트림이 스스로 끝나면 None."""
    try:
        return await asyncio.wait_for(gen.__anext__(), timeout)
    except StopAsyncIteration:
        return None


def test_event_stream_sends_the_count_now_pings_when_idle_and_wakes_after_a_commit(monkeypatch):
    # TestClient 는 끝나지 않는 응답에 http.disconnect 를 보내지 못하므로 생성기를 직접 돌린다.
    monkeypatch.setattr(notification_stream, "KEEPALIVE_SECONDS", 0.3)
    token, user = _signup()
    with get_session() as db:
        notifications.notify(db, user["id"], "admin", "이미 있던 알림")
        db.commit()
    stream_token = client.post("/api/me/notifications/stream-token", headers=_auth(token)).json()["token"]
    with get_session() as db:
        before = notifications.unread_count_for(db, user["id"])  # 다른 테스트의 공지가 섞여 있을 수 있다
    assert before >= 1

    def later():
        time.sleep(0.15)
        with get_session() as db:
            notifications.notify(db, user["id"], "quest", "백테스트 퀘스트 완료", data={"points": 10})
            db.commit()  # 커밋 뒤 after_commit → 허브 → 스트림이 깨어난다

    def parsed(chunk):
        return json.loads(chunk.split("data: ", 1)[1].strip())

    async def scenario():
        gen = notification_stream.event_stream(user["id"], stream_token)
        assert await _next(gen) == "retry: 3000\n\n"
        first = parsed(await _next(gen))
        assert first["unread"] == before and first["latest_id"] >= 1
        assert notification_stream.hub.subscriber_count(user["id"]) == 1
        idle = await _next(gen)  # 아무 일 없어도 keepalive 로 현재 상태를 다시 보낸다
        assert idle.startswith("event: unread\n") and parsed(idle) == first
        threading.Thread(target=later).start()
        pushed = await _next(gen)  # 새 알림 본문(토스트용) → 그 다음 안 읽은 수
        assert pushed.startswith("event: notification\nid: ")
        payload = parsed(pushed)
        assert payload["title"] == "백테스트 퀘스트 완료" and payload["kind"] == "quest" and payload["data"]["points"] == 10
        assert payload["read"] is False
        after = parsed(await _next(gen))
        assert after["unread"] == before + 1 and after["latest_id"] == payload["id"]
        await gen.aclose()
        assert notification_stream.hub.subscriber_count(user["id"]) == 0

    asyncio.run(scenario())


def test_event_stream_ends_when_the_login_is_invalidated(monkeypatch):
    monkeypatch.setattr(notification_stream, "KEEPALIVE_SECONDS", 0.2)
    token, user = _signup()
    stream_token = client.post("/api/me/notifications/stream-token", headers=_auth(token)).json()["token"]

    async def scenario():
        gen = notification_stream.event_stream(user["id"], stream_token)
        await _next(gen)
        first = await _next(gen)
        assert first.startswith("event: unread\ndata: ")
        assert "latest_id" in first
        from app.db import User
        with get_session() as db:
            row = db.get(User, user["id"])
            row.auth_version += 1  # 로그아웃·비밀번호 변경이 하는 일
            db.add(row)
            db.commit()
        assert await _next(gen) is None, "계정 토큰이 무효가 되면 다음 keepalive 때 스스로 끝난다"
        assert notification_stream.hub.subscriber_count(user["id"]) == 0

    asyncio.run(scenario())


def test_stream_endpoint_streams_events_with_sse_headers(monkeypatch):
    async def finite(user_id, stream_token):
        yield "retry: 3000\n\n"
        yield notification_stream.unread_event(5)

    monkeypatch.setattr(notification_stream, "event_stream", finite)
    token, _ = _signup()
    stream_token = client.post("/api/me/notifications/stream-token", headers=_auth(token)).json()["token"]
    response = client.get("/api/me/notifications/stream", params={"token": stream_token})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    cache_control = response.headers["cache-control"]
    assert "no-store" in cache_control or "no-cache" in cache_control  # 회원 응답 미들웨어가 private, no-store 로 바꾼다
    assert response.headers.get("x-accel-buffering") == "no"
    assert response.text == "retry: 3000\n\n" + notification_stream.unread_event(5)
