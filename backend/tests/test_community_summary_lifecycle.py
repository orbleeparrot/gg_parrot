import threading
from concurrent.futures import ThreadPoolExecutor

from app import community_summaries as summaries


def test_background_work_is_deduplicated_bounded_and_shutdown_cancels_queue(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    stopping = threading.Event()
    calls = []
    class ObservedExecutor(ThreadPoolExecutor):
        def shutdown(self, **options):
            assert options == {"wait": True, "cancel_futures": True}
            stopping.set()
            return super().shutdown(**options)
    def execute(jobs, *, background=False):
        assert background is True
        calls.append(jobs)
        entered.set()
        assert release.wait(5)
    summaries.shutdown()
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(summaries, "ThreadPoolExecutor", ObservedExecutor)
    monkeypatch.setattr(summaries, "_execute", execute)
    monkeypatch.setattr(summaries, "_QUEUE_MAX", 2)
    summaries.start()
    closer = None
    try:
        summaries._schedule([{"summary_key": "first"}])
        assert entered.wait(2)
        summaries._schedule([{"summary_key": "first"}, {"summary_key": "second"}, {"summary_key": "third"}])
        assert summaries._queued == {"first", "second"}
        closer = threading.Thread(target=summaries.shutdown)
        closer.start()
        assert stopping.wait(2)
        summaries._schedule([{"summary_key": "after-shutdown"}])
        release.set()
        closer.join(timeout=3)
        assert not closer.is_alive()
        assert len(calls) == 1
        assert not summaries._queued and summaries._executor is None
    finally:
        release.set()
        if closer:
            closer.join(timeout=3)
        summaries.shutdown()
        summaries.start()


def test_application_closes_summary_work_before_shared_ai_client(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    order = []
    monkeypatch.setattr(main.community_summaries_mod, "start", lambda: order.append("start"))
    monkeypatch.setattr(main.community_summaries_mod, "shutdown", lambda: order.append("summary"))
    monkeypatch.setattr(main.ai_runtime_mod, "close_ai_runtime", lambda: order.append("ai"))
    monkeypatch.setattr(main.http_runtime_mod, "close_http_runtime", lambda: order.append("http"))
    with TestClient(main.app):
        assert order == ["start"]
    assert order == ["start", "summary", "ai", "http"]
