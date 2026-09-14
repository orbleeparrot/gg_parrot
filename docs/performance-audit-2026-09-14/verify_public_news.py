"""Reproduce response barriers offline; this is not a production latency benchmark.

Run with the backend's Python environment from any working directory.
No app.main import, production credentials, external requests, or database writes.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch
import json
import os
import socket
import sys
import tempfile
import time

for key in ("DATABASE_URL", "GEMINI_API_KEY", "COINDESK_API_KEY", "PREFECT_API_URL"):
    os.environ[key] = ""
for key in ("POSITION_NEWS_EMBEDDED_ENABLED", "WHALE_TRADE_EMBEDDED_ENABLED",
            "POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "POSITION_NEWS_EXTRA_RSS_ENABLED",
            "BINANCE_SQUARE_ENABLED"):
    os.environ[key] = "false"
os.environ["NEWS_IMAGES_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def forbidden_network(*args, **kwargs):
    raise AssertionError("External network is forbidden during this audit")


def assert_response_barrier(read, entered, release, validate):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(read)
        try:
            assert entered.wait(5), "Expected blocking stage was not reached"
            assert not future.done(), "Response returned before the gated stage completed"
        finally:
            release.set()
        result = future.result(timeout=5)
        validate(result)
        return True


with tempfile.TemporaryDirectory(prefix="ggp-news-audit-") as scratch:
    os.environ["SQLITE_PATH"] = str(Path(scratch) / "unused.db")
    with patch.object(socket.socket, "connect", forbidden_network):
        from app import news, news_images, community_summaries

        korean = {"title": "비트코인 새 소식", "url": "https://example.test/korean"}
        english = {"title": "Arbitrum token rises", "url": "https://example.test/english"}
        output = {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(news, "_cache", {}))
            stack.enter_context(patch.object(news, "_coin_cache", {}))
            stack.enter_context(patch.object(news, "_coin_envelope_cache", {}))
            stack.enter_context(patch.object(news, "_title_translation_cache", {}))
            stack.enter_context(patch.object(news_images, "attach", lambda items: "ready"))
            stack.enter_context(patch.object(community_summaries, "enrich_items",
                lambda items, **kwargs: (items, {"status": "ready"})))
            stack.enter_context(patch.object(news, "_load_durable_market_summary", lambda day: None))
            stack.enter_context(patch.object(news, "_fetch_public_news_payload",
                lambda *args: {"items": [deepcopy(korean)]}))

            # Already collected Korean articles still wait for the market overview.
            entered, release = Event(), Event()

            def gated_summary(*args, **kwargs):
                entered.set()
                assert release.wait(5)
                return None

            with patch.object(news, "_summarize", gated_summary):
                def validate_market(result):
                    assert [item["title"] for item in result["items"]] == [korean["title"]]

                output["market_ready_articles_wait_for_summary"] = assert_response_barrier(
                    news.get_market_news, entered, release, validate_market)

            # Even the fresh-DB path waits for pending translation before delivering
            # its Korean article. The real filtering/envelope code remains in use.
            stored = {"snapshot_id": "audit", "news_payload": {"items": [korean, english]},
                      "collection": {"last_success_ms": int(time.time() * 1000)}}
            entered, release = Event(), Event()

            def gated_translation(titles):
                entered.set()
                assert release.wait(5)

            with patch.object(news, "_load_latest_coin_snapshot", lambda symbol: deepcopy(stored)), \
                 patch.object(news, "_ensure_title_translations", gated_translation):
                def validate_coin(result):
                    assert result["data_source"] == "prefect_db"
                    assert [item["title"] for item in result["items"]] == [korean["title"]]
                    assert result["translation"]["pending_count"] == 1

                output["fresh_db_snapshot_ready_article_waits_for_translation"] = assert_response_barrier(
                    lambda: news.get_coin_news("BTCUSDT"), entered, release, validate_coin)

        print(json.dumps(output, indent=2))
