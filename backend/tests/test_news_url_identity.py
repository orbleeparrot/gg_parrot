import hashlib
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import news
from app.agent_features.position_news import articles
from app.news_identity import news_identity


def item(url="https://example.test/news?id=1", **extra):
    return {"title": "비트코인 거래 소식", "source": "뉴스", "url": url, **extra}


def test_same_url_aliases_share_article_id_but_same_title_different_urls_do_not():
    first = item()
    alias = item("http://EXAMPLE.test:80/news/?utm_source=rss&id=1#top", source="example.test", title="비트코인 거래 추가 소식")
    assert articles.article_id(first) == articles.article_id(alias)
    assert articles.article_id(first) != articles.article_id(item("https://example.test/news?id=2"))
    assert news_identity(item("https://example.test/#/news/1")) != news_identity(item("https://example.test/#/news/2"))


def test_source_merge_deduplicates_urls_not_distinct_articles_with_same_title():
    rows = news._merge_news_items([item()], [item(source="example.test"), item("https://example.test/news?id=2")])
    assert len(rows) == 2


def test_public_candidates_preserve_id_queries_and_fallback_hides_aliases():
    from app import public_news
    published = datetime.now(timezone.utc).isoformat()
    rows = [item(published=published), item(source="example.test", published=published),
            item("https://example.test/news?id=2", published=published)]
    assert len(news._public_news_candidates(rows, limit=10)) == 2
    assert len(public_news._public_payload("BTC", {"items": rows})["items"]) == 2


def test_community_post_identity_does_not_coalesce_identical_titles():
    first = item(content_type="community", community_post_id="1")
    assert news_identity(first) != news_identity({**first, "community_post_id": "2"})


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_new_storage_coalesces_source_alias_and_keeps_cursor_analysis(engine):
    with Session(engine) as db:
        articles.upsert_articles("BTC", [item()], analysis={"items": [{"sentiment": "positive"}], "analysis_source": "ai"}, db=db)
        first = articles.read_article_feed("BTC", db=db)
        articles.upsert_articles("BTC", [item(source="example.test")], db=db)
        assert len(db.exec(select(articles.NewsArticle)).all()) == 1
        updated = articles.read_article_feed("BTC", after_revision=first["cursor"], db=db)
        assert len(updated["items"]) == 1
        assert updated["analysis"]["items"][0]["sentiment"] == "positive"


def test_legacy_duplicate_reads_keep_raw_cursor_and_matching_analysis(engine):
    with Session(engine) as db:
        articles.upsert_articles("BTC", [item(), item("https://example.test/news?id=2")], db=db)
        alias = item(source="example.test", id="legacy-alias")
        db.add(articles.NewsArticle(asset_symbol="BTC", article_id="legacy-alias", revision=3, ready=True,
            item_json=json.dumps(alias), assessment_json='{"sentiment":"negative"}', first_seen_ms=1, last_seen_ms=1))
        feed = db.get(articles.NewsArticleFeed, "BTC")
        feed.revision = 3
        db.add(feed)
        db.commit()
        result = articles.read_article_feed("BTC", after_revision=0, limit=3, db=db)
        assert result["cursor"] == 3
        assert len(result["items"]) == 2
        for row, assessment in zip(result["items"], result["analysis"]["items"]):
            if row["url"] == alias["url"]:
                assert row["id"] == "legacy-alias" and assessment["sentiment"] == "negative"


def test_legacy_enrichment_keeps_original_row_id(engine):
    raw = item("https://example.test/legacy", title="Bitcoin trading news")
    legacy_id = hashlib.sha256((raw["title"] + "|" + raw["source"]).encode()).hexdigest()[:20]
    raw["id"] = legacy_id
    with Session(engine) as db:
        db.add(articles.NewsArticleFeed(asset_symbol="BTC", revision=1, item_count=1))
        db.add(articles.NewsArticle(asset_symbol="BTC", article_id=legacy_id, revision=1, ready=False,
            enrichment_pending=True, item_json=json.dumps(raw), first_seen_ms=1, last_seen_ms=1))
        db.commit()
        articles.upsert_articles("BTC", [{**raw, "title": "비트코인 거래 소식", "original_title": raw["title"]}], enrichment_only=True, db=db)
        assert len(db.exec(select(articles.NewsArticle)).all()) == 1
        assert articles.read_article_feed("BTC", db=db)["items"][0]["id"] == legacy_id
        articles.update_article_image("BTC", legacy_id, {"image_resolved": True}, db=db)
        assert len(db.exec(select(articles.NewsArticle)).all()) == 1
        assert articles.read_article_feed("BTC", db=db)["items"][0]["image_resolved"] is True
