"""알림 실시간 전달 — "알림이 바뀌었다"를 브라우저까지 밀어 주는 길.

세 겹으로 동작한다(useNotifications 와 짝):

1. 같은 프로세스: ``mark_changed(db, user_id)`` 가 세션에 표시해 두고, 그 세션이 **커밋된 뒤**
   (SQLAlchemy ``after_commit``) 허브를 깨운다. 커밋 전에 깨우면 브라우저가 아직 안 보이는
   행을 세러 오는 경합이 생긴다.
2. 다른 프로세스(Prefect 워커의 기사·고래 수집기 등): 같은 트랜잭션에 ``pg_notify`` 를 실어
   보내고, 웹 프로세스의 LISTEN 연결이 받아 허브를 깨운다. Postgres 의 NOTIFY 는 커밋될 때만
   전달되므로 1 과 같은 의미다. SQLite(개발·테스트)에는 이 층이 없다.
3. 브라우저: ``event_stream`` 이 SSE 로 안 읽은 수(``event: unread``)를 보낸다. 연결 직후 한 번,
   그 뒤 허브가 깨울 때마다. 25초마다 주석 한 줄로 연결을 살린다(프록시 idle timeout).

허브는 값을 들지 않는다 — 깨어난 스트림이 DB 에서 안 읽은 수를 다시 센다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from . import auth
from . import db as db_mod
from .db import get_session
from .stream_hub import StreamHub

log = logging.getLogger(__name__)

CHANNEL = "ggp_notifications"
BROADCAST = "*"
KEEPALIVE_SECONDS = 25.0
RECONNECT_MAX_SECONDS = 60.0
_PENDING_KEY = "ggp_notification_changes"

hub = StreamHub()
_listener_task: Optional[asyncio.Task] = None


# --- 쓰기 쪽: 바뀌었다고 표시 ------------------------------------------------
def _is_postgres(db) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001 - 바인드가 없는 특수 세션
        return False


def mark_changed(db, user_id: Optional[int]) -> None:
    """이 세션이 커밋되면 ``user_id``(None = 전체) 의 알림이 바뀐 것으로 알린다.

    같은 세션 안에서 같은 대상은 한 번만 표시한다. Postgres 에서는 pg_notify 도 함께 실어
    다른 프로세스에도 전해진다(커밋 시점에 전달, 롤백이면 사라진다).
    """
    key = BROADCAST if user_id is None else str(int(user_id))
    pending = db.info.setdefault(_PENDING_KEY, set())
    if key in pending:
        return
    pending.add(key)
    if _is_postgres(db):
        db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": key})


def dispatch(payload: str) -> int:
    """알림 채널 payload("12" | "*") 를 허브 깨우기로 바꾼다. 깨운 구독 수를 돌려준다."""
    value = str(payload or "").strip()
    if value == BROADCAST:
        return hub.notify_all()
    try:
        return hub.notify(int(value))
    except ValueError:
        log.warning("ignoring malformed notification payload: %r", value[:40])
        return 0


@event.listens_for(Session, "after_commit")
def _after_commit(session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    for key in pending:
        dispatch(key)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session) -> None:
    session.info.pop(_PENDING_KEY, None)


# --- 다른 프로세스에서 온 변경: LISTEN ------------------------------------------
def _dsn() -> str:
    url = db_mod._DATABASE_URL
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def _connect(dsn: str):
    import psycopg

    return await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, connect_timeout=10,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )


async def listen_forever(dsn: str, *, connect=None, on_connect=None, sleep=asyncio.sleep) -> None:
    """LISTEN 연결을 유지한다. 끊기면 1초부터 두 배씩(최대 60초) 기다렸다가 다시 붙는다."""
    delay = 1.0
    while True:
        try:
            conn = await (connect or _connect)(dsn)
            try:
                await conn.execute(f"LISTEN {CHANNEL}")
                delay = 1.0
                log.info("notification listener connected")
                if on_connect:
                    on_connect()
                async for note in conn.notifies():
                    dispatch(note.payload)
            finally:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 연결 문제는 폴링이 메운다; 로그만 남기고 재시도
            log.warning("notification listener dropped (%s: %s); retry in %.0fs", type(exc).__name__, str(exc)[:120], delay)
            await sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)


def start() -> Optional[asyncio.Task]:
    """웹 프로세스 시작 때 부른다. Postgres 일 때만 LISTEN 작업을 띄운다."""
    global _listener_task
    if db_mod.database_dialect() != "postgresql" or _listener_task is not None:
        return None
    _listener_task = asyncio.get_running_loop().create_task(listen_forever(_dsn()), name="notification-listener")
    return _listener_task


async def stop() -> None:
    global _listener_task
    task, _listener_task = _listener_task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


# --- 읽기 쪽: SSE ---------------------------------------------------------------
def _unread(user_id: int) -> int:
    from . import notifications  # 지연 import — notifications 가 이 모듈을 import 한다

    with get_session() as db:
        return notifications.unread_count_for(db, user_id)


def unread_event(unread: int) -> str:
    return f"event: unread\ndata: {json.dumps({'unread': int(unread)})}\n\n"


async def event_stream(user_id: int, token: str):
    """한 계정의 안 읽은 수를 SSE 로 보낸다. 끝나면(로그아웃·탈퇴) 조용히 닫는다."""
    subscription_id, changed = hub.subscribe(user_id)
    try:
        yield "retry: 3000\n\n"
        yield unread_event(await asyncio.to_thread(_unread, user_id))
        while True:
            try:
                await asyncio.wait_for(changed.wait(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # 토큰의 계정이 아직 유효한지(로그아웃·탈퇴하면 auth_version 이 바뀐다) 확인하고 살려 둔다.
                try:
                    await asyncio.to_thread(auth.decode_stream_token, token, auth.NOTIFICATION_STREAM_PURPOSE, check_expiry=False)
                except HTTPException:
                    return
                yield ": ping\n\n"
                continue
            changed.clear()
            yield unread_event(await asyncio.to_thread(_unread, user_id))
    finally:
        hub.unsubscribe(user_id, subscription_id)
