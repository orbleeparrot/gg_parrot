"""Two independent Prefect slots in one process, with one shutdown owner.

News/probes share one runner slot and public trades have another. Runners share a
background asyncio loop so Prefect cannot replace the main thread's SIGTERM
handler. Only actual flow runs create subprocesses; idle runners share imports.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import math
import signal
import threading
import time


async def _register_and_start(runner, deployments):
    for deployment in deployments:
        await runner.aadd_deployment(deployment)
    await runner.start()


async def _wait_for_stop(stopping, poll_seconds):
    while not stopping.is_set():
        await asyncio.sleep(poll_seconds)


async def _stop_runners(runners, run_tasks, shutdown_seconds):
    stop_tasks = []
    for runner, task in zip(runners, run_tasks):
        if runner.started:
            stop_tasks.append(asyncio.create_task(runner.astop()))
        elif not task.done():
            # Registration or __aenter__ can still be waiting on the API.
            task.cancel()
    all_tasks = [*run_tasks, *stop_tasks]
    # Leave a short final cancellation window inside the platform's deadline.
    _, pending = await asyncio.wait(all_tasks, timeout=max(.001, shutdown_seconds - .05))
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.wait(pending, timeout=min(.025, shutdown_seconds / 4))
        for task in all_tasks:
            if task.done() and not task.cancelled():
                task.exception()
        raise RuntimeError("Prefect collectors shutdown timed out")
    # Retrieve every result even if both runners failed at the same instant.
    for task in run_tasks:
        if not task.cancelled():
            task.exception()
    for task in stop_tasks:
        if not task.cancelled() and task.exception() is not None:
            raise RuntimeError("Prefect collector shutdown failed") from task.exception()


async def _serve_async(news_deployments, whale_deployments, *, runner_factory,
                       stopping, poll_seconds, shutdown_seconds):
    runners = [
        runner_factory(name="gg-parrot-news", limit=1, pause_on_shutdown=False),
        runner_factory(name="gg-parrot-whales", limit=1, query_seconds=5, pause_on_shutdown=False),
    ]
    run_tasks = [asyncio.create_task(_register_and_start(runner, deployments))
                 for runner, deployments in zip(runners, (news_deployments, whale_deployments))]
    stop_watcher = asyncio.create_task(_wait_for_stop(stopping, poll_seconds))
    try:
        done, _ = await asyncio.wait([*run_tasks, stop_watcher], return_when=asyncio.FIRST_COMPLETED)
        for task in run_tasks:
            if task in done:
                # A poller disappearing, even with exit code zero, leaves one
                # feature unmanaged. Let Render restart the complete worker.
                if task.cancelled():
                    raise RuntimeError("Prefect collector exited unexpectedly")
                if task.exception() is not None:
                    raise task.exception()
                raise RuntimeError("Prefect collector exited unexpectedly")
    finally:
        stopping.set()
        stop_watcher.cancel()
        await asyncio.gather(stop_watcher, return_exceptions=True)
        await _stop_runners(runners, run_tasks, shutdown_seconds)


def serve_collectors(news_deployments, whale_deployments, *, runner_factory=None,
                     poll_seconds=.1, shutdown_seconds=270):
    """Block the main thread while both independently limited runners serve."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Collector supervisor must own the main thread's shutdown signals")
    if runner_factory is None:
        from prefect.runner import Runner
        runner_factory = Runner
    if not math.isfinite(shutdown_seconds) or not 0 < shutdown_seconds <= 270:
        raise ValueError("Collector shutdown must fit within 270 seconds")
    poll_seconds = max(.001, min(.1, poll_seconds))
    stopping = threading.Event()
    finished = threading.Event()
    result = Future()
    previous = {}
    shutdown_started = None

    def request_stop(_signum, _frame):
        stopping.set()

    def run():
        try:
            asyncio.run(_serve_async(news_deployments, whale_deployments,
                runner_factory=runner_factory, stopping=stopping,
                poll_seconds=poll_seconds, shutdown_seconds=shutdown_seconds))
        except BaseException as exc:
            result.set_exception(exc)
        else:
            result.set_result(None)
        finally:
            finished.set()

    thread = threading.Thread(target=run, name="agent-prefect-runners", daemon=True)
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, request_stop)
        thread.start()
        while not finished.wait(poll_seconds):
            if stopping.is_set():
                shutdown_started = shutdown_started or time.monotonic()
                if time.monotonic() - shutdown_started >= shutdown_seconds:
                    raise RuntimeError("Prefect collectors shutdown timed out")
        return result.result()
    finally:
        stopping.set()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
