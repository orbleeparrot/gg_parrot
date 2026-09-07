"""Web-owned background collection; durable ticker leases also cover Prefect."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import collector, repository

logger = logging.getLogger(__name__)
_runtime = None
_last_pruned = 0.0


def _bootstrap_only():
    default = "true" if os.environ.get("RENDER") else "false"
    return os.environ.get("POSITION_NEWS_EMBEDDED_BOOTSTRAP_ONLY", default).lower() not in {
        "0", "false", "no",
    }


def _cycle():
    global _last_pruned
    # The DB is authoritative across web instances and the optional worker.
    # Native Render web instances serve the first RSS result without Chromium.
    # Prefect owns regular browser/AI work; stale snapshots still receive RSS
    # recovery here if the external worker is temporarily unavailable.
    result = collector.run_collection_cycle(retention_days=0, bootstrap_only=_bootstrap_only())
    if time.monotonic() - _last_pruned > 3600:
        repository.prune_snapshots(retention_days=max(1, int(os.environ.get("POSITION_NEWS_RETENTION_DAYS", "30"))))
        _last_pruned = time.monotonic()
    if result["ticker_count"]:
        logger.info("position news cycle: %s", result)


class CollectionRuntime:
    def __init__(self, *, cycle=_cycle, scan_seconds=5):
        self.cycle = cycle
        self.scan_seconds = scan_seconds
        self.loop = None
        self.changed = None
        self.task = None
        self.stopping = False

    def start(self):
        if self.task is not None:
            return
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.task = self.loop.create_task(self._run(), name="position-news-collector")

    def wake(self):
        if self.loop is not None and not self.loop.is_closed() and not self.stopping:
            self.loop.call_soon_threadsafe(self.changed.set)

    async def _run(self):
        while not self.stopping:
            self.changed.clear()
            try:
                await asyncio.to_thread(self.cycle)
            except Exception:
                logger.exception("Position news cycle failed; retrying on the next scan")
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.changed.wait(), self.scan_seconds)
                except asyncio.TimeoutError:
                    pass

    async def stop(self):
        self.stopping = True
        self.changed.set()
        if self.task is not None:
            # Finish in-flight work before closing its HTTP/AI resources.
            await self.task
            self.task = None


def start():
    global _runtime
    if os.environ.get("POSITION_NEWS_EMBEDDED_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _runtime is None:
        logger.info("Starting embedded news collector: version=%s bootstrap_only=%s collection_seconds=%s",
                    os.environ.get("RENDER_GIT_COMMIT", "local"), _bootstrap_only(),
                    os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300"))
        _runtime = CollectionRuntime(scan_seconds=max(1, int(os.environ.get("POSITION_NEWS_SCAN_SECONDS", "5"))))
        _runtime.start()


def request_collection():
    if _runtime is not None:
        _runtime.wake()


async def stop():
    global _runtime
    current, _runtime = _runtime, None
    if current is not None:
        await current.stop()
