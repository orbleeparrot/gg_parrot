"""Offline audit probes; run with the project's Python environment.

All database work uses temporary SQLite files. Outbound socket connections are
blocked before application imports. No application modules are modified.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
os.environ.update(
    DATABASE_URL="", GEMINI_API_KEY="", COINDESK_API_KEY="", PREFECT_API_URL="",
    SQLITE_PATH=tempfile.mktemp(prefix="agent-news-audit-", suffix=".db"),
    POSITION_NEWS_EMBEDDED_ENABLED="false", WHALE_TRADE_EMBEDDED_ENABLED="false",
    POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED="false",
    POSITION_NEWS_EXTRA_RSS_ENABLED="false", BINANCE_SQUARE_ENABLED="false",
    NEWS_IMAGES_DISABLED="1",
)


def blocked(*args, **kwargs):
    raise AssertionError("Audit prohibits external network")


socket.socket.connect = blocked
socket.create_connection = blocked

from sqlmodel import SQLModel, Session, create_engine  # noqa: E402
from sqlalchemy import event  # noqa: E402
from app import news, coindesk_api  # noqa: E402
from app.agent_features.position_news import collector, repository, service  # noqa: E402
from test_position_news_collector import FakeRepository, _payload, _analysis  # noqa: E402


def verify_delivery_barriers():
    with ThreadPoolExecutor(max_workers=1) as pool:
        fast, release = threading.Event(), threading.Event()

        def google(*args, **kwargs):
            fast.set()
            return _payload("BTC")

        def coindesk(*args, **kwargs):
            assert release.wait(3)
            return []

        with patch.object(news, "_prepare_asset_identity"), patch.object(
            news, "_coin_news_envelope", google
        ), patch.object(news, "_fetch_coindesk_news", coindesk), patch.object(
            coindesk_api, "configuration", return_value={"enabled": False}
        ), patch.object(news.binance_square, "configuration", return_value={"enabled": False}):
            future = pool.submit(news.fetch_coin_news_for_collector, "BTC")
            assert fast.wait(3)
            assert not future.done()
            print("SOURCE_BARRIER: fast source complete; collector fetch still pending")
            release.set()
            future.result(timeout=3)

        old, new = _payload("BTC", "기존 소식"), _payload("BTC", "새로운 현물 ETF 승인 소식")
        stored = {"news_payload": old, "collection": {"last_success_ms": int(time.time() * 1000)}}
        with patch.object(collector, "collect_payload") as publish:
            result = collector.publish_initial_payload(
                "BTC", new, repo=SimpleNamespace(get_latest_snapshot=lambda _: stored)
            )
            assert publish.call_count == 0 and result["status"] == "reused"
            print("FRESH_RSS_SUPPRESSED: one incoming editorial article; publish calls = 0")

        started, release = threading.Event(), threading.Event()
        repo = FakeRepository()

        def localize(payload, *args):
            started.set()
            assert release.wait(3)
            return payload

        with patch.object(collector, "_localize_collected_payload", localize):
            future = pool.submit(collector.collect_payload, "BTC", _payload("BTC"),
                                 repo=repo, analyzer=_analysis, allow_ai=False)
            assert started.wait(3)
            assert not repo.by_id and not future.done()
            print("LOCALIZATION_BARRIER: raw article ready; snapshot rows = 0")
            release.set()
            future.result(timeout=3)

        started, release = threading.Event(), threading.Event()
        rollbacks = []
        raw = {"symbol": "BTC", "items": [
            {"title": "비트코인 현물 ETF 승인 소식", "source": "뉴스"},
            {"title": "Bitcoin token rallies after approval", "source": "CoinDesk"},
        ]}
        stored = {"news_payload": raw, "analysis": _analysis(raw["items"], "BTC", allow_ai=False),
                  "collection": {"last_success_ms": int(time.time() * 1000)}}

        def ensure(titles):
            started.set()
            assert release.wait(3)
            raise news.NewsTranslationError("isolated translation stall")

        with patch.object(service, "_load_latest_snapshot", return_value=stored), patch.object(
            news, "_ensure_title_translations", ensure
        ), patch.object(news, "_title_translation_cache", {}):
            future = pool.submit(service.get_position_news,
                                 {"symbol": "BTCUSDT", "position_side": "long"},
                                 SimpleNamespace(rollback=lambda: rollbacks.append(1)))
            assert started.wait(3)
            assert not future.done() and rollbacks
            print("HTTP_READY_ITEM_BLOCKED: Korean article waits; request DB transaction already released")
            release.set()
            result = future.result(timeout=3)
            assert len(result["items"]) == 1 and result["translation"]["pending_count"] == 1


def verify_database_counts():
    with tempfile.TemporaryDirectory(prefix="agent-news-sql-") as directory:
        engine = create_engine(f"sqlite:///{directory}/audit.db")
        SQLModel.metadata.create_all(engine)
        statements = []

        def record(conn, cursor, statement, params, context, many):
            statements.append(statement.split()[0].upper())

        event.listen(engine, "before_cursor_execute", record)
        with Session(engine) as db:
            titles = [f"Audit Bitcoin headline {i}" for i in range(10)]
            claim = repository.claim_title_translations(titles, db=db, now_ms=1000)
            assert Counter(statements) == Counter(SELECT=10, SAVEPOINT=10, INSERT=10, RELEASE=10)
            print("TEN_TITLE_COLD_CLAIM_SQL:", dict(Counter(statements)), "total", len(statements))
            statements.clear()
            repository.store_title_translations({title: "비트코인 소식" for title in titles},
                                                claim_token=claim["claim_token"], db=db, now_ms=2000)
            assert statements == ["UPDATE"] * 10
            print("TEN_TITLE_STORE_SQL:", dict(Counter(statements)))
        with patch.object(repository, "get_session", lambda: Session(engine)):
            claim = repository.claim_snapshot(
                asset_symbol="BTC", snapshot_key="audit-btc",
                news_payload={"symbol": "BTC", "items": [{"title": "비트코인 소식"}]},
                prompt_version="audit", model="audit", retry_incomplete=False, now_ms=1000)
            repository.complete_snapshot(claim.snapshot_id, {"items": [], "analysis_status": "ready"},
                                         claim_token=claim.claim_token, now_ms=1001)
            statements.clear()
            assert repository.get_latest_snapshot("BTC") is not None
            assert statements == ["SELECT", "SELECT"]
            print("LATEST_SNAPSHOT_SQL:", dict(Counter(statements)))
        engine.dispose()

    clock, loads, sleeps = [0.0], [], []

    def load(titles):
        loads.append(clock[0])
        return {}

    def pause(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 0, sleep=pause)
    with patch.dict(os.environ, {"DATABASE_URL": "audit-not-a-real-database"}), patch.object(
        news, "time", fake_time
    ), patch.object(news, "_title_translation_cache", {}), patch.object(
        news, "_title_translation_retry_at", {}
    ), patch.object(news, "_TITLE_TRANSLATION_WAIT_SECONDS", 30), patch.object(
        news, "_load_durable_title_translations", load
    ), patch.object(news, "_claim_durable_title_translations", return_value={
        "waiting": ["Bitcoin token rallies"], "claimed": [], "cached": {}
    }), patch.object(news, "_translate_claimed_titles"):
        try:
            news._ensure_title_translations(["Bitcoin token rallies"])
        except news.NewsTranslationError:
            pass
        assert clock[0] == 30 and len(loads) == 18 and len(sleeps) == 17
        print("WAITING_TRANSLATION_DB_READS:", {"simulated_seconds": clock[0],
                                               "cache_load_calls": len(loads), "wait_polls": len(sleeps)})


if __name__ == "__main__":
    verify_delivery_barriers()
    verify_database_counts()
    print("All position news audit assertions passed.")
