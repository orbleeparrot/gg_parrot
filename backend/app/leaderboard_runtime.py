"""Leased background preparation of the daily challenge and public leaderboard."""
from __future__ import annotations

import asyncio
import logging
import os

from . import challenge, leaderboard, leaderboard_snapshot as snapshots

log = logging.getLogger(__name__)
_task: asyncio.Task | None = None
REFRESH_SECONDS = max(2.0, float(os.environ.get("LEADERBOARD_REFRESH_SECONDS", "5")))


def _build(token: str, date_kst: str) -> None:
    if leaderboard._today_kst() != date_kst:
        raise RuntimeError("leaderboard date changed during preparation")
    items = leaderboard.compute_entries()["items"]
    if leaderboard._today_kst() != date_kst:
        raise RuntimeError("leaderboard date changed during snapshot computation")
    snapshots.publish_snapshot(token, date_kst, items)


async def _renew(token: str) -> None:
    while True:
        await asyncio.sleep(snapshots.LEASE_MS / 3000)
        if not await asyncio.to_thread(snapshots.renew_refresh, token):
            raise RuntimeError("leaderboard background lease lost")


async def _prepare(token: str) -> None:
    date_kst = leaderboard._today_kst()
    await leaderboard.ensure_today_carryover()
    await challenge.ensure_today()
    await asyncio.to_thread(_build, token, date_kst)


async def refresh_once() -> bool:
    token = await asyncio.to_thread(snapshots.claim_refresh)
    if token is None:
        return False
    work = asyncio.create_task(_prepare(token))
    renewal = asyncio.create_task(_renew(token))
    succeeded = False
    try:
        done, _ = await asyncio.wait((work, renewal), return_when=asyncio.FIRST_COMPLETED)
        if renewal in done:
            await renewal
            raise RuntimeError("leaderboard lease renewal stopped")
        await work
        succeeded = True
        return True
    finally:
        work.cancel()
        renewal.cancel()
        await asyncio.gather(work, renewal, return_exceptions=True)
        await asyncio.to_thread(snapshots.release_refresh, token,
                                delay_seconds=REFRESH_SECONDS if succeeded else 5)


async def _run() -> None:
    while True:
        try:
            await refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("leaderboard background preparation failed; retaining last completed board")
        await asyncio.sleep(1)


def start() -> None:
    global _task
    if os.environ.get("LEADERBOARD_BACKGROUND_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="leaderboard-snapshot-worker")


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
