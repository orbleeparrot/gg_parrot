"""껄무새 중계를 웹 프로세스 안에서 돈다 — 실행 중인 세션을 몇 초마다 한 바퀴.

체결 · 오류가 들어오거나 세션이 시작 · 종료되면 ``request_cycle`` 이 다음 바퀴를 앞당긴다. 롤링 배포로 두
프로세스가 겹쳐도 한 바퀴는 한쪽만 돈다(이름 붙은 임대 · agent_commentary.run_cycle). 겹쳐 돌아도 말풍선은
``say_key`` 로 한 번만 남는다.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import agent_commentary

logger = logging.getLogger(__name__)
_runtime = None


class CommentaryRuntime:
    def __init__(self, *, cycle=agent_commentary.run_cycle, scan_seconds: float = 10):
        self.cycle = cycle
        self.scan_seconds = scan_seconds
        self.loop = None
        self.changed = None
        self.task = None
        self.stopping = False

    def start(self) -> None:
        if self.task is not None:
            return
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.task = self.loop.create_task(self._run(), name="agent-commentary")

    def wake(self) -> None:
        if self.loop is not None and not self.loop.is_closed() and not self.stopping:
            self.loop.call_soon_threadsafe(self.changed.set)

    async def _run(self) -> None:
        await asyncio.sleep(3)  # 부팅 직후 스키마 · 다른 런타임이 먼저 자리 잡게
        while not self.stopping:
            self.changed.clear()
            try:
                await asyncio.to_thread(self.cycle)
            except Exception:
                logger.exception("Agent commentary cycle failed; retrying on the next scan")
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.changed.wait(), self.scan_seconds)
                except asyncio.TimeoutError:
                    pass
                # 깨워도 너무 촘촘히 돌지 않게(체결 여러 건이 연달아 와도 한 바퀴로 모은다).
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self.stopping = True
        if self.changed is not None:
            self.changed.set()
        if self.task is not None:
            await self.task
            self.task = None


def start() -> None:
    global _runtime
    if os.environ.get("AGENT_COMMENTARY_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _runtime is None:
        _runtime = CommentaryRuntime(scan_seconds=max(3, int(os.environ.get("AGENT_COMMENTARY_SCAN_SECONDS", "10"))))
        _runtime.start()


def request_cycle() -> None:
    if _runtime is not None:
        _runtime.wake()


async def stop() -> None:
    global _runtime
    current, _runtime = _runtime, None
    if current is not None:
        await current.stop()
