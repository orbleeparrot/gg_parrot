import asyncio
import threading

from app.agent_features.position_news import runtime


def test_background_collector_wakes_without_blocking_requests():
    async def scenario():
        calls = []
        completed = threading.Event()
        def cycle():
            calls.append(threading.current_thread().name)
            completed.set()
        worker = runtime.CollectionRuntime(cycle=cycle, scan_seconds=60)
        worker.start()
        try:
            assert await asyncio.to_thread(completed.wait, 2)
            completed.clear()
            worker.wake()
            assert await asyncio.to_thread(completed.wait, 2)
            assert len(calls) == 2
            assert all(name != threading.current_thread().name for name in calls)
        finally:
            await worker.stop()
    asyncio.run(scenario())


def test_collector_recovers_after_cycle_error():
    async def scenario():
        calls = []
        first = threading.Event()
        recovered = threading.Event()
        def cycle():
            calls.append(True)
            if len(calls) == 1:
                first.set()
                raise RuntimeError("temporary DB failure")
            recovered.set()
        worker = runtime.CollectionRuntime(cycle=cycle, scan_seconds=60)
        worker.start()
        try:
            assert await asyncio.to_thread(first.wait, 2)
            worker.wake()
            assert await asyncio.to_thread(recovered.wait, 2)
        finally:
            await worker.stop()
    asyncio.run(scenario())
