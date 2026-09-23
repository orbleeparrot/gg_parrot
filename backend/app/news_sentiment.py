"""Durable news outbox and one automatic SemIf consumer across web processes."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace
import httpx
from sqlalchemy import BigInteger, Index, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Field, SQLModel, select
from .db import get_session
from .news_sentiment_model import MODEL, _article, _infer

logger = logging.getLogger(__name__)
_task = None
_cache_lock = threading.Lock()
_cache = None
_cache_until = 0.0


class NewsSentimentJob(SQLModel, table=True):
    __table_args__ = (
        Index("ix_sentiment_pending", "status", "next_run_ms", "created_ms"),
        Index("ix_sentiment_completed", "status", "completed_ms"),
    )
    article_id: str = Field(primary_key=True)
    article_json: str
    model: str = MODEL
    status: str = "pending"
    attempts: int = 0
    token: str = ""
    created_ms: int = Field(sa_type=BigInteger)
    started_ms: int = Field(default=0, sa_type=BigInteger)
    completed_ms: int = Field(default=0, sa_type=BigInteger)
    next_run_ms: int = Field(default=0, sa_type=BigInteger)
    result_json: str = "{}"
    error: str = ""


class NewsSentimentState(SQLModel, table=True):
    name: str = Field(default="semif", primary_key=True)
    token: str = ""
    lease_until_ms: int = Field(default=0, sa_type=BigInteger)
    next_run_ms: int = Field(default=0, sa_type=BigInteger)
    pending: int = 0  # Includes the currently processing job.
    completed: int = 0
    failed: int = 0
    total_ms: float = 0
    revision: int = Field(default=0, sa_type=BigInteger)
    last_success_ms: int = Field(default=0, sa_type=BigInteger)
    last_error: str = ""


def enabled() -> bool:
    switch = os.environ.get("SEMIF_NEWS_ENABLED", "true").lower() not in {"0", "false", "no"}
    return switch and bool((os.environ.get("SEMIF_API_BASE") or "").strip())


def _insert(db):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def enqueue(db, scope: str, items: dict, now_ms: int) -> int:
    """Part of the collector transaction: no HTTP and no commit of its own."""
    if not items:
        return 0
    insert = _insert(db)
    db.exec(insert(NewsSentimentState).values(name="semif").on_conflict_do_nothing())
    # Same lock order as claim/finish prevents counter races and deadlocks.
    state = db.exec(select(NewsSentimentState).where(NewsSentimentState.name == "semif")
                    .with_for_update().execution_options(populate_existing=True)).one()
    values = []
    for key, item in items.items():
        article = _article(SimpleNamespace(article_id=key, asset_symbol=scope, item_json=json.dumps(item)))
        values.append({"article_id": key, "article_json": json.dumps(article, ensure_ascii=False), "created_ms": now_ms})
    inserted = db.exec(insert(NewsSentimentJob).values(values).on_conflict_do_nothing(
        index_elements=[NewsSentimentJob.article_id]).returning(NewsSentimentJob.article_id)).all()
    state.pending += len(inserted)
    state.revision += int(bool(inserted))
    db.add(state)
    return len(inserted)


def claim(now_ms=None):
    """A global 120s fenced lease exceeds the 95s model request deadline."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    with get_session() as db:
        state = db.get(NewsSentimentState, "semif")
        if not state or not state.pending or state.next_run_ms > now or state.lease_until_ms > now:
            return None
        token = uuid.uuid4().hex
        locked = db.exec(update(NewsSentimentState).where(
            NewsSentimentState.name == "semif", NewsSentimentState.lease_until_ms <= now,
            NewsSentimentState.next_run_ms <= now,
        ).values(token=token, lease_until_ms=now + 120_000, revision=NewsSentimentState.revision + 1))
        if locked.rowcount != 1:
            db.rollback()
            return None
        # Recover work left by a crashed/expired owner; its result is fenced out.
        db.exec(update(NewsSentimentJob).where(NewsSentimentJob.status == "processing")
                .values(status="pending", token="", next_run_ms=now))
        job = db.exec(select(NewsSentimentJob).where(
            NewsSentimentJob.status == "pending", NewsSentimentJob.next_run_ms <= now,
        ).order_by(NewsSentimentJob.created_ms, NewsSentimentJob.article_id).limit(1)).first()
        if job is None:
            db.exec(update(NewsSentimentState).where(NewsSentimentState.name == "semif", NewsSentimentState.token == token)
                    .values(token="", lease_until_ms=0))
            db.commit()
            return None
        job.status, job.token, job.started_ms = "processing", token, now
        job.attempts += 1
        db.add(job)
        result = (job.article_id, token, json.loads(job.article_json))
        db.commit()
        return result


def finish(article_id, token, *, result=None, error="", provider_error=False, now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    with get_session() as db:
        state = db.exec(select(NewsSentimentState).where(NewsSentimentState.name == "semif")
                        .with_for_update().execution_options(populate_existing=True)).first()
        if not state or state.token != token or state.lease_until_ms <= now:
            return False
        job = db.get(NewsSentimentJob, article_id)
        if not job or job.token != token or job.status != "processing":
            return False
        if result is not None:
            job.status, job.result_json, job.error = "ready", json.dumps(result, ensure_ascii=False), ""
            job.completed_ms = now
            state.pending -= 1
            state.completed += 1
            state.total_ms += result["elapsed_ms"]
            state.last_success_ms, state.last_error, state.next_run_ms = now, "", 0
        elif not provider_error and job.attempts >= 3:
            job.status, job.error, job.completed_ms = "failed", error, now
            state.pending -= 1
            state.failed += 1
            state.last_error, state.next_run_ms = "", 0
        else:
            job.status, job.error = "pending", error
            job.next_run_ms = now + min(120, 10 * 2 ** min(job.attempts, 4)) * 1000
            # A provider outage pauses the entire queue to avoid hammering it.
            state.next_run_ms = job.next_run_ms if provider_error else 0
            state.last_error = error if provider_error else ""
        state.token, state.lease_until_ms, job.token = "", 0, ""
        state.revision += 1
        db.add(job)
        db.add(state)
        db.commit()
        return True


async def process_one():
    picked = await asyncio.to_thread(claim)
    if not picked:
        return False
    key, token, article = picked
    try:
        async with asyncio.timeout(95):
            result = await _infer(article)
    except asyncio.CancelledError:
        raise  # Restart recovers this job after its lease expires.
    except httpx.HTTPStatusError as exc:
        article_error = exc.response.status_code in {400, 413, 422}
        await asyncio.to_thread(finish, key, token,
                                error="이 기사를 모델이 처리하지 못했어요." if article_error else "모델 서버가 응답하지 못했어요. 자동으로 재시도합니다.",
                                provider_error=not article_error)
    except (httpx.HTTPError, TimeoutError):
        await asyncio.to_thread(finish, key, token, error="모델 서버에 연결하지 못했어요. 자동으로 재시도합니다.", provider_error=True)
    except (ValueError, KeyError, TypeError, AttributeError):
        await asyncio.to_thread(finish, key, token, error="모델 응답에서 판단 결과를 읽을 수 없어요.")
    else:
        await asyncio.to_thread(finish, key, token, result=result)
    return True


async def _run():
    while True:
        try:
            worked = await process_one()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic news sentiment worker failed")
            worked = False
        await asyncio.sleep(0 if worked else 2)


def start():
    global _task
    if enabled() and (_task is None or _task.done()):
        _task = asyncio.create_task(_run(), name="news-sentiment")


async def stop():
    global _task
    task, _task = _task, None
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _entry(job):
    return {"article": json.loads(job.article_json), "status": job.status, "started_at": job.started_ms,
            "completed_at": job.completed_ms, "result": json.loads(job.result_json) if job.status == "ready" else None,
            "error": job.error}


def _read_snapshot():
    with get_session() as db:
        state = db.get(NewsSentimentState, "semif") or NewsSentimentState()
        active = enabled()
        version = f"{state.revision}:{int(active)}"
        now = int(time.time() * 1000)
        # No new work/result: read only the small control row, not 61 article bodies.
        if _cache is not None and _cache.get("version") == version:
            return {**_cache, "server_now_ms": now}
        processing = db.exec(select(NewsSentimentJob).where(NewsSentimentJob.status == "processing")
                             .order_by(NewsSentimentJob.started_ms.desc()).limit(1)).first()
        recent = list(db.exec(select(NewsSentimentJob).where(NewsSentimentJob.status.in_(["ready", "failed"]))
                              .order_by(NewsSentimentJob.completed_ms.desc(), NewsSentimentJob.article_id.desc()).limit(61)).all())
        current = processing or (recent.pop(0) if recent else None)
        return {"version": version, "model": MODEL, "enabled": active, "connected": bool(state.last_success_ms and not state.last_error),
                "detail": state.last_error if active else "자동 판단 서버 연결이 필요해요. SEMIF_API_BASE 설정을 확인해 주세요.",
                "current": _entry(current) if current else None, "history": [_entry(job) for job in recent[:60]],
                "stats": {"completed": state.completed, "pending": max(0, state.pending - int(bool(processing))),
                          "failed": state.failed, "average_ms": state.total_ms / state.completed if state.completed else None},
                "server_now_ms": int(time.time() * 1000)}


def snapshot():
    """Read-only projection, shared for 1s per web process; never calls the model."""
    global _cache, _cache_until
    with _cache_lock:
        if _cache is None or time.monotonic() >= _cache_until:
            _cache = _read_snapshot()
            _cache_until = time.monotonic() + 1
        return _cache
