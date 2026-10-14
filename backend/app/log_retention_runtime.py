"""Visitor-independent, bounded activity-history maintenance.

Uses the ordinary application pool, not the reserved trading heartbeat pool.
Each batch owns one short transaction; PostgreSQL's transaction advisory lock
prevents two rolling-deploy processes cleaning the same records concurrently.
Health counters are fixed-size process memory/stdout, not another DB log table.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

from sqlalchemy import text

from . import notifications, runner
from .db import get_session

# Uvicorn configures this logger at INFO in Render/local deployments. Reuse it
# so successful cleanup is visible even without a visitor-triggered health log.
logger = logging.getLogger("uvicorn.error")
INTERVAL_SECONDS = 3600
RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 900
BACKLOG_SECONDS = 5
_ADVISORY_LOCK = 7_147_147_140
_COUNTERS = ("deleted_sessions", "deleted_events", "deleted_commands", "deleted_notices", "deleted_receipts")
_runtime = None


def run_batch() -> dict:
    """Commit only a bounded batch. Exceptions roll back, releasing the lock.

    No schema/role changes, network fetches, AI calls or trading-state changes
    are performed. Pinned records and running sessions stay out of deletion.
    """
    with get_session() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT set_config('statement_timeout', '2000ms', true)"))
            db.execute(text("SELECT set_config('lock_timeout', '500ms', true)"))
            if not db.execute(text(f"SELECT pg_try_advisory_xact_lock({_ADVISORY_LOCK})")).scalar_one():
                return {"skipped": True, "has_more": False}
        ended = runner.prune_ended_sessions_batch(db, limit=200)
        notices = notifications.prune_expired_notices(db, limit=500)
        db.commit()
        return {**{key: int(ended.get(key, 0)) + int(notices.get(key, 0)) for key in _COUNTERS},
                "has_more": bool(ended.get("has_more") or notices.get("has_more")), "skipped": False}


class RetentionRuntime:
    def __init__(self, *, batch=None, initial_delay=10, interval_seconds=INTERVAL_SECONDS,
                 retry_seconds=RETRY_SECONDS, backlog_seconds=BACKLOG_SECONDS):
        self.batch = run_batch if batch is None else batch
        self.initial_delay = max(0, initial_delay)
        self.interval_seconds = max(.01, interval_seconds)
        self.retry_seconds = max(.01, retry_seconds)
        self.backlog_seconds = max(.01, backlog_seconds)
        self.task = None
        self._stop = None
        self._lock = threading.Lock()
        self._health = {"status": "not_started", "last_success_ms": 0, "last_failure_ms": 0,
                        "last_error_type": "", "consecutive_failures": 0, "failures": 0,
                        "successful_batches": 0, "next_attempt_ms": 0,
                        **{key: 0 for key in _COUNTERS}}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._health)

    def start(self) -> None:
        if self.task is None or self.task.done():
            self._stop = asyncio.Event()
            self.task = asyncio.create_task(self._run(), name="activity-log-retention")

    async def _wait(self, seconds: float) -> bool:
        with self._lock:
            self._health["next_attempt_ms"] = int(time.time() * 1000 + seconds * 1000)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return self._stop.is_set()

    async def _run(self) -> None:
        delay = self.initial_delay
        try:
            while not await self._wait(delay):
                with self._lock:
                    self._health["status"] = "running"
                try:
                    result = await asyncio.to_thread(self.batch)
                except Exception as exc:
                    with self._lock:
                        self._health["failures"] = min(2**63 - 1, self._health["failures"] + 1)
                        count = min(31, self._health["consecutive_failures"] + 1)
                        self._health.update(status="retrying", consecutive_failures=count,
                                            last_failure_ms=int(time.time() * 1000), last_error_type=type(exc).__name__[:80])
                    delay = min(MAX_RETRY_SECONDS, self.retry_seconds * 2 ** (count - 1))
                    # Raw SQL/exception text can contain credentials. Emit only
                    # the real exception class and retry schedule, never payloads.
                    logger.warning("Activity log retention failed error_type=%s retry_seconds=%s", type(exc).__name__, delay)
                else:
                    if result.get("skipped"):
                        delay = self.retry_seconds
                        with self._lock:
                            self._health["status"] = "busy"
                    else:
                        delay = self.backlog_seconds if result.get("has_more") else self.interval_seconds
                        with self._lock:
                            self._health.update(status="backlog" if result.get("has_more") else "waiting",
                                                last_success_ms=int(time.time() * 1000), consecutive_failures=0,
                                                last_error_type="")
                            self._health["successful_batches"] = min(2**63 - 1, self._health["successful_batches"] + 1)
                            for key in _COUNTERS:
                                self._health[key] = min(2**63 - 1, self._health[key] + int(result.get(key, 0)))
                        if any(result.get(key) for key in _COUNTERS):
                            logger.info("Activity log retention complete %s has_more=%s",
                                        " ".join(f"{key}={int(result.get(key, 0))}" for key in _COUNTERS), bool(result.get("has_more")))
        finally:
            with self._lock:
                self._health.update(status="stopped", next_attempt_ms=0)

    async def stop(self) -> None:
        if self.task is not None:
            self._stop.set()
            # Do not cancel to_thread: it must release its connection before
            # application shutdown disposes engines used by active macros.
            await self.task
            self.task = None


def start() -> None:
    global _runtime
    if os.environ.get("ACTIVITY_LOG_RETENTION_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _runtime is None:
        _runtime = RetentionRuntime()
        _runtime.start()


async def stop() -> None:
    global _runtime
    worker, _runtime = _runtime, None
    if worker is not None:
        await worker.stop()


def snapshot() -> dict:
    return _runtime.snapshot() if _runtime is not None else {"status": "not_running"}
