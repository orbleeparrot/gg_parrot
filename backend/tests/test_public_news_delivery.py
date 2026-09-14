"""Readers stay DB-only while source/translation/overview workers are slow."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlmodel import delete

from app import db as database, news, public_news
from app.agent_features.position_news import articles


@pytest.fixture(autouse=True)
def isolated_feed(monkeypatch):
    with database.get_session() as db:
        for model in (articles.NewsArticle, articles.NewsArticleFeed):
            db.exec(delete(model).where(model.asset_symbol.in_(["MARKET", "PUBLICFIX", "BTC"])))
        db.exec(delete(public_news.PublicNewsLease))
        db.commit()
    monkeypatch.setattr(public_news, "request_refresh", lambda scope: None)
    monkeypatch.setattr(news, "_title_translation_cache", {})


def korean(title="비트코인 현물 ETF 승인 소식"):
    return {"title": title, "url": "https://example.test/news", "source": "뉴스"}


def test_readers_never_collect_translate_or_write(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("HTTP reads must not start external work")
    articles.upsert_articles("MARKET", [korean()], payload={"overview": "준비된 시장 요약", "ai": True})
    articles.upsert_articles("PUBLICFIX", [korean()], payload={"symbol": "PUBLICFIX"})
    for name in ("_fetch_public_news_payload", "_localize_news_payload", "_ensure_title_translations", "_summarize"):
        monkeypatch.setattr(news, name, forbidden)
    statements = []
    def capture(_conn, _cursor, sql, *_args):
        statements.append(sql)
    event.listen(database._engine, "before_cursor_execute", capture)
    try:
        market = public_news.get_market_news()
        coin = public_news.get_coin_news("PUBLICFIXUSDT")
    finally:
        event.remove(database._engine, "before_cursor_execute", capture)
    assert len(statements) == 4
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert market["overview"] == "준비된 시장 요약"
    assert market["items"][0]["title"] == coin["items"][0]["title"] == korean()["title"]


def test_empty_read_returns_pending_without_waiting(monkeypatch):
    monkeypatch.setattr(news, "_fetch_public_news_payload", lambda *a: pytest.fail("cold GET fetched news"))
    result = public_news.get_market_news()
    assert result["items"] == []
    assert result["collection"]["status"] == "pending"
    assert result["refresh_seconds"] == 3


def test_ready_market_article_is_visible_before_overview_completes(monkeypatch):
    entered, release = Event(), Event()
    articles.upsert_articles("MARKET", [korean()], payload={"as_of": "2000-01-01", "overview": "어제의 시장 요약", "ai": True})
    monkeypatch.setattr(news, "_fetch_public_news_payload", lambda **kwargs: {"items": [korean()]})
    monkeypatch.setattr(news, "_load_durable_market_summary", lambda day: None)
    monkeypatch.setattr(news, "_store_durable_market_summary", lambda *args: None)
    def summary(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return "완료된 시장 요약"
    monkeypatch.setattr(news, "_summarize", summary)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(public_news.collect_market)
        try:
            assert entered.wait(5)
            result = public_news.get_market_news()
            assert result["items"][0]["title"] == korean()["title"]
            assert result["overview"] is None and result["as_of"] == news._kst_date()
            assert not task.done()
        finally:
            release.set()
        task.result(timeout=5)
    assert public_news.get_market_news()["overview"] == "완료된 시장 요약"


def test_untranslated_item_does_not_hide_ready_article(monkeypatch):
    articles.upsert_articles("PUBLICFIX", [korean(), {"title": "Bitcoin price rises", "source": "CoinDesk"}])
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *a, **k: pytest.fail("reader waited"))
    result = public_news.get_coin_news("PUBLICFIXUSDT")
    assert len(result["items"]) == 1
    assert result["translation"]["pending_count"] == 1
    assert result["refresh_seconds"] == 3


def test_cold_market_work_is_shared_and_old_owner_cannot_publish(monkeypatch):
    first = public_news.claim_work("MARKET", now_ms=1)
    assert first
    assert public_news.claim_work("MARKET", now_ms=2) is None
    second = public_news.claim_work("MARKET", now_ms=120_002)
    assert second and second != first
    monkeypatch.setattr(news, "_fetch_public_news_payload", lambda **kwargs: {"items": [korean()]})
    with pytest.raises(RuntimeError, match="stale"):
        public_news.collect_market(claim_token=first)
    assert public_news.get_market_news()["items"] == []


def test_public_payload_never_contains_community_body_or_analysis():
    item = {**korean(), "content_type": "community", "community_body": "private source excerpt",
            "published": datetime.now(timezone.utc).isoformat(), "community_post_id": "123",
            "community_body_hash": "secret", "community_summary": "작성자는 현황을 설명했어요.",
            "community_summary_status": "ready"}
    articles.upsert_articles("PUBLICFIX", [item])
    result = public_news.get_coin_news("PUBLICFIXUSDT")
    assert "community_body" not in result["items"][0]
    assert "community_body_hash" not in result["items"][0]
    assert result["items"][0]["community_summary"] == item["community_summary"]
    assert "analysis" not in result


def test_source_progress_is_available_before_slow_sibling(monkeypatch):
    fast, release = Event(), Event()
    monkeypatch.setattr(news, "_prepare_asset_identity", lambda *a: None)
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *a, **k: {"items": [korean()]})
    def slow(**kwargs):
        assert release.wait(5)
        return []
    monkeypatch.setattr(news, "_fetch_coindesk_news", slow)
    def publish(payload):
        if payload["items"]:
            articles.upsert_articles("BTC", payload["items"], payload=payload)
            fast.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(news.fetch_coin_news_for_collector, "BTC", on_progress=publish)
        try:
            assert fast.wait(5)
            assert public_news.get_coin_news("BTCUSDT")["items"]
            assert not task.done()
        finally:
            release.set()
        task.result(timeout=5)


def test_market_fallback_publishes_fast_publisher_before_slow_publisher(monkeypatch):
    published, release = Event(), Event()
    monkeypatch.setattr(news, "_fetch_news", lambda *a, **k: [])
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **k: [korean()])
    monkeypatch.setattr(news, "_EXTRA_RSS_SOURCES", {"slow": {}})
    def slow(*args, **kwargs):
        assert release.wait(5)
        return []
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", slow)
    def publish(payload):
        if payload["items"]:
            articles.upsert_articles("MARKET", payload["items"], payload=payload)
            published.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(news._fetch_public_news_payload, on_progress=publish)
        try:
            assert published.wait(5)
            assert public_news.get_market_news()["items"]
            assert not task.done()
        finally:
            release.set()
        task.result(timeout=5)


def test_known_missing_thumbnail_is_complete(monkeypatch):
    from app import news_images
    monkeypatch.setattr(news_images, "enabled", lambda: True)
    monkeypatch.setattr(news_images, "_cache", {korean()["url"]: {
        "image": "", "article_url": "", "expires_at": float("inf")}})
    rows = [korean()]
    assert news_images.attach(rows) == "ready"
    articles.upsert_articles("MARKET", rows)
    assert public_news.get_market_news()["image_status"] == "ready"
