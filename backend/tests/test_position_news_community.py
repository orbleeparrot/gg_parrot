"""Community posts stay visible without buying or imitating editorial analysis."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.agent_features.position_news import classifier, collector
from tests.test_position_news_collector import FakeRepository


def post(post_id="one", title="가격 급등 전망"):
    return {"content_type": "community", "community_post_id": post_id,
            "title": title, "source": "Binance Square",
            "url": f"https://www.binance.com/en/square/post/{post_id}"}


def article(name):
    return {"title": f"프로토콜 {name} 소식", "source": "언론사",
            "url": f"https://publisher.example/{name}", "excerpt": f"{name} 기사 발췌"}


def test_community_only_uses_opinion_baseline_without_article_or_model_requests(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(classifier.news_mod, "enrich_article_excerpts",
                        lambda *_args, **_kwargs: pytest.fail("community must not fetch article bodies"))
    monkeypatch.setattr(classifier, "_generate_ai_analysis",
                        lambda *_args: pytest.fail("community must not invoke article analysis"))

    result = classifier.analyze_headlines([post()], "테스트코인")

    assert result["analysis_status"] == "ready"
    assert result["ai"] is False
    assert result["items"][0]["sentiment"] == "unclear"
    assert "의견" in result["items"][0]["summary"]
    assert "커뮤니티" in result["overview"] and "의견" in result["overview"]


def test_mixed_analysis_excludes_community_and_preserves_editorial_indexes(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    first, second = article("첫째"), article("둘째")
    seen = []

    def enrich(items, **kwargs):
        seen.append(("enrich", deepcopy(items)))
        return [{**item, "excerpt": "본문 발췌"} for item in items]

    def generate(items, _coin):
        seen.append(("generate", deepcopy(items)))
        return {"items": [{"sentiment": "positive", "summary": "첫 기사 요약", "confidence": "medium"},
                          {"sentiment": "negative", "summary": "둘째 기사 요약", "confidence": "medium"}]}

    monkeypatch.setattr(classifier.news_mod, "enrich_article_excerpts", enrich)
    monkeypatch.setattr(classifier, "_generate_ai_analysis", generate)
    result = classifier.analyze_headlines([post("one"), first, post("two"), second], "테스트")

    assert [[item["url"] for item in rows] for _, rows in seen] == [[first["url"], second["url"]]] * 2
    assert [row["sentiment"] for row in result["items"]] == ["unclear", "positive", "unclear", "negative"]
    assert result["items"][1]["summary"] == "첫 기사 요약"
    assert result["items"][3]["summary"] == "둘째 기사 요약"
    assert "의견" in result["items"][0]["summary"]


class LatestRepository(FakeRepository):
    def get_latest_snapshot(self, _asset):
        rows = [row for row in self.by_id.values() if row["completed"]]
        if not rows:
            return None
        row = max(rows, key=lambda value: value["id"])
        return {"snapshot_id": row["key"], "news_payload": deepcopy(row["news"]),
                "analysis": deepcopy(row["analysis"])}


def test_community_updates_store_new_snapshots_without_rebuying_unchanged_news(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "10")
    repo, calls = LatestRepository(), []
    first, second = article("첫째"), article("둘째")

    def analyze(items, _coin, *, allow_ai):
        calls.append(deepcopy(items))
        return {"items": [{"sentiment": "positive" if item["title"] == first["title"] else "negative",
                           "summary": item["title"], "confidence": "medium"} for item in items],
                "analysis_status": "ready", "analysis_source": "ai", "ai": True}

    def collect(items):
        return collector.collect_payload("CHIP", {"symbol": "CHIP", "items": items}, repo=repo,
                                         analyzer=analyze, localize=False)

    original = collect([first, second])
    added = collect([post("one"), second, first])
    changed = collect([first, post("two"), second])

    assert len({row["snapshot_key"] for row in (original, added, changed)}) == 3
    assert len(calls) == repo.budget_used == 1
    assert added["editorial_analysis_reused"] and changed["editorial_analysis_reused"]
    assert not added["used_ai_budget"] and not changed["used_ai_budget"]
    assert [item["sentiment"] for item in repo.by_id[2]["analysis"]["items"]] == ["unclear", "negative", "positive"]
    assert [item["sentiment"] for item in repo.by_id[3]["analysis"]["items"]] == ["positive", "unclear", "negative"]

    # Changed editorial evidence still receives a new paid analysis.
    refreshed = collect([{**first, "excerpt": "새로 확인된 기사 내용"}, post("three"), second])
    assert refreshed["used_ai_budget"] and not refreshed["editorial_analysis_reused"]
    assert len(calls) == repo.budget_used == 2


def test_community_only_collector_reserves_no_analysis_budget(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    repo = LatestRepository()
    monkeypatch.setattr(repo, "reserve_ai_budget", lambda **_: pytest.fail("community has no analysis charge"))
    monkeypatch.setattr(classifier, "_generate_ai_analysis", lambda *_: pytest.fail("no model"))

    result = collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [post()]},
                                       repo=repo, localize=False)

    assert result["status"] == "stored" and not result["used_ai_budget"]
    assert repo.by_id[1]["analysis"]["analysis_status"] == "ready"
    assert repo.by_id[1]["analysis"]["items"][0]["sentiment"] == "unclear"


def test_new_community_posts_publish_before_browser_and_keep_existing_editorial_analysis(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(collector.news_mod, "_coin_snapshot_is_stale", lambda _: False)
    repo = LatestRepository()
    first, browser_article = article("첫째"), article("브라우저 추가")
    analysis = {"items": [{"sentiment": "positive", "summary": "첫 기사 요약"},
                          {"sentiment": "negative", "summary": "추가 기사 요약"}],
                "analysis_status": "ready", "analysis_source": "ai", "ai": True}
    collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [first, browser_article]},
                              repo=repo, analyzer=lambda *_args, **_kwargs: analysis, localize=False)
    monkeypatch.setattr(collector, "_localize_collected_payload",
                        lambda *_args, **_kwargs: pytest.fail("early publish must not wait for translation"))

    # The fast source stage has only one of the already analyzed articles.
    fresh_post = {**post("new"), "published": datetime.now(timezone.utc).isoformat()}
    initial = {"symbol": "CHIP", "items": [first, fresh_post]}
    result = collector.publish_initial_payload("CHIP", initial, repo=repo)
    latest = repo.get_latest_snapshot("CHIP")

    assert result["status"] == "stored" and result["editorial_analysis_reused"]
    assert repo.budget_used == 1
    assert [item["url"] for item in latest["news_payload"]["items"]] == [
        post("new")["url"], first["url"], browser_article["url"]]
    assert latest["analysis"]["analysis_status"] == "ready"
    assert [row["summary"] for row in latest["analysis"]["items"][1:]] == ["첫 기사 요약", "추가 기사 요약"]
    assert latest["analysis"]["items"][0]["sentiment"] == "unclear"
    assert collector.publish_initial_payload("CHIP", initial, repo=repo)["status"] == "reused"
    assert collector.publish_initial_payload("CHIP", {"symbol": "CHIP", "items": [first]}, repo=repo)["status"] == "reused"
    assert len(repo.by_id) == 2


def test_community_identity_is_stable_across_order_but_distinguishes_new_posts():
    first, second = post("one"), post("two")
    assert collector.analysis_fingerprint("CHIP", [first]) != collector.analysis_fingerprint("CHIP", [second])
    assert collector.analysis_fingerprint("CHIP", [first, second]) == collector.analysis_fingerprint("CHIP", [second, first])
    assert collector.analysis_fingerprint("CHIP", [first]) != collector.analysis_fingerprint("CHIP", [{**first, "content_type": "article"}])


def test_editorial_reuse_does_not_cross_model_changes(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "model-one")
    repo = LatestRepository()
    item = article("첫째")
    analysis = lambda items, _coin, **_: {"items": [{"sentiment": "neutral", "summary": "소식"} for _ in items],
                                         "analysis_status": "ready", "analysis_source": "ai", "ai": True}
    collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [item]}, repo=repo,
                              analyzer=analysis, localize=False)
    monkeypatch.setenv("GEMINI_MODEL", "model-two")

    result = collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [post(), item]}, repo=repo,
                                       analyzer=analysis, localize=False)

    assert result["used_ai_budget"] and not result["editorial_analysis_reused"]
    assert repo.budget_used == 2


def test_community_refresh_uses_real_repository_snapshot_order_and_fencing(monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine, select
    from app.agent_features.position_news import repository
    from app.db import TickerNewsSnapshot, TickerNewsAiBudget

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repository, "get_session", lambda: Session(engine))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    item = article("첫째")
    analysis = {"items": [{"sentiment": "positive", "summary": "기사 요약", "confidence": "medium"}],
                "analysis_status": "ready", "analysis_source": "ai", "ai": True}
    first = collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [item]},
        repo=repository, analyzer=lambda *_args, **_kwargs: analysis, localize=False, now_ms=1_000_000)

    second = collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [post(), item]},
        repo=repository, analyzer=lambda *_args, **_kwargs: pytest.fail("reuse must bypass paid work"),
        localize=False, now_ms=1_300_000)
    latest = repository.get_latest_snapshot("CHIP")

    assert latest["snapshot_id"] == second["snapshot_key"] != first["snapshot_key"]
    assert [row.get("content_type") for row in latest["news_payload"]["items"]] == ["community", None]
    assert [row["sentiment"] for row in latest["analysis"]["items"]] == ["unclear", "positive"]
    with Session(engine) as db:
        snapshots = db.exec(select(TickerNewsSnapshot)).all()
        assert len(snapshots) == 2
        assert all(row.processing_status == "ready" and not row.claim_token for row in snapshots)
        assert sum(row.used for row in db.exec(select(TickerNewsAiBudget)).all()) == 1
    engine.dispose()
