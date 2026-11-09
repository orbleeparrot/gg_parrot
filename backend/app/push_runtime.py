"""앱 알림을 웹 프로세스 안에서 보낸다 — 쌓인 알림을 꺼내 기기마다 전한다.

중계가 소식을 쌓고 커밋하면 ``request_dispatch`` 가 바로 깨운다. 깨움을 놓쳐도(다른 프로세스가 쌓은 것 등)
``scan_seconds`` 마다 한 번 확인한다 — 잠깐 막혀 미뤄 둔 기기별 전송(push_alerts.RETRY_WAIT_MS)도 이 확인 때 다시 나간다.
두 프로세스가 겹쳐 돌아도 전송마다 바퀴의 토큰으로 맡으므로 한 프로세스만 보낸다.

한 시간 정리(push_alerts.maintain — 만료 · 보관 삭제 · 집계)는 발송과 따로 돈다. 발송 바퀴는 정리를 기다리지 않고, 정리는
한 번에 짧게(CLEANUP_BUDGET_S) 하고 남았으면 ``MAINTAIN_BUSY_SECONDS`` 뒤 이어 한다. 둘 다 푸시 DB 몫(PUSH_DB_CONCURRENCY)
안에서 연결을 쓴다. VAPID 키가 없으면 돌지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import push_alerts, web_push

logger = logging.getLogger(__name__)
_runtime = None

MAINTAIN_SECONDS = 60       # 정리 바퀴를 시작할 때인지 보는 간격(시작은 한 시간에 한 번)
MAINTAIN_BUSY_SECONDS = 5   # 정리가 남았을 때 이어 하는 간격


class PushRuntime:
    def __init__(self, *, dispatch=push_alerts.dispatch, scan_seconds: float = 30, maintain=push_alerts.maintain,
                 maintain_seconds: float = MAINTAIN_SECONDS, busy_seconds: float = MAINTAIN_BUSY_SECONDS,
                 start_delay: float = 3):
        self.dispatch = dispatch
        self.start_delay = start_delay  # 부팅 직후 스키마가 먼저 자리 잡게
        self.scan_seconds = scan_seconds
        self.maintain = maintain
        self.maintain_seconds = maintain_seconds
        self.busy_seconds = busy_seconds
        self.loop = None
        self.changed = None
        self.stopped = None
        self.task = None
        self.upkeep = None
        self.stopping = False

    def start(self) -> None:
        if self.task is not None:
            return
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.stopped = asyncio.Event()
        self.task = self.loop.create_task(self._run(), name="push-dispatch")
        self.upkeep = self.loop.create_task(self._maintain(), name="push-maintain")

    def wake(self) -> None:
        if self.loop is not None and not self.loop.is_closed() and not self.stopping:
            self.loop.call_soon_threadsafe(self.changed.set)

    async def _run(self) -> None:
        await asyncio.sleep(self.start_delay)
        while not self.stopping:
            self.changed.clear()
            try:
                result = await asyncio.to_thread(self.dispatch)
                # 한 바퀴에 다 맡지 못했으면(엔드포인트 · 엔드포인트당 상한) 기다리지 않고 바로 다음 바퀴
                if result.get("more"):
                    continue
            except Exception:
                logger.exception("Push dispatch failed; retrying on the next scan")
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.changed.wait(), self.scan_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _maintain(self) -> None:
        delay = self.start_delay
        while not self.stopping:
            try:
                await asyncio.wait_for(self.stopped.wait(), delay)
                break
            except asyncio.TimeoutError:
                pass
            try:
                result = await asyncio.to_thread(self.maintain)
                delay = self.busy_seconds if result.get("active") else self.maintain_seconds
            except Exception:
                logger.exception("Push maintenance failed; retrying later")
                delay = self.maintain_seconds

    async def stop(self) -> None:
        self.stopping = True
        if self.changed is not None:
            self.changed.set()
        if self.stopped is not None:
            self.stopped.set()
        for task in (self.task, self.upkeep):
            if task is not None:
                await task
        self.task = self.upkeep = None


def start() -> None:
    global _runtime
    if os.environ.get("PUSH_DISPATCH_ENABLED", "true").lower() in {"0", "false", "no"} or not web_push.enabled():
        return
    if _runtime is None:
        _runtime = PushRuntime(scan_seconds=max(5, int(os.environ.get("PUSH_DISPATCH_SCAN_SECONDS", "30"))))
        _runtime.start()


def request_dispatch() -> None:
    if _runtime is not None:
        _runtime.wake()


async def stop() -> None:
    global _runtime
    current, _runtime = _runtime, None
    if current is not None:
        await current.stop()
