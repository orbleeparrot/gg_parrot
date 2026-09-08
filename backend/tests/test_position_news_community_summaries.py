"""Body summaries survive collector publication, analysis reuse and maintenance."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app import community_summaries, community_summary_repository
from app.agent_features.position_news import collector
from tests.test_position_news_collector import FakeRepository
from tests.test_position_news_community import LatestRepository, article


def post(identifier="123", body_hash="a" * 64, **changes):
    return {"content_type": "community", "source": "Binance Square", "community_post_id": identifier,
            "title": f"CHIP observation {identifier}", "url": f"https://www.binance.com/en/square/post/{identifier}",
            "published": datetime.now(timezone.utc).isoformat(),
            "community_body": "$CHIP holds support at 0.05.", "community_body_hash": body_hash,
            "community_body_status": "ready", "community_body_truncated": False, **changes}


def test_collector_summarizes_all_bodies_including_untranslated_titles(monkeypatch):
    raw = [article("첫 기사"), post("123"), post("456")]
    def localize(items):
        # A title-only localizer must not erase the internal body fields.
        return [items[0], {"source": items[1]["source"], "url": items[1]["url"],
                          "original_title": items[1]["title"], "title": "CHIP 지지선 관찰"}]
    seen = []
    def enrich(items, *, wait):
        seen.append((deepcopy(items), wait))
        return [{**item, **({"community_summary": "작성자는 CHIP의 지지선을 설명합니다.",
                            "community_summary_status": "ready"} if item.get("content_type") == "community" else {})}
                for item in items], {"status": "ready", "pending_count": 0}
    monkeypatch.setattr(collector.news_mod, "_localize_coin_news_items", localize)
    monkeypatch.setattr(community_summaries, "enrich_items", enrich)
    result = collector._localize_collected_payload({"items": raw}, FakeRepository())
    assert seen[0][1] is True
    assert len(seen[0][0]) == len(result["items"]) == 3
    assert seen[0][0][2]["title"] == raw[2]["title"]
    assert result["items"][2]["community_body"] == raw[2]["community_body"]
    assert result["items"][2]["community_summary_status"] == "ready"
    assert result["translation"]["pending_count"] == 1
    assert result["community_summaries"]["ready_count"] == 2
    assert result["community_summaries"]["pending_count"] == 0


def test_summary_failure_preserves_all_raw_work_and_reports_pending(monkeypatch):
    raw = [post()]
    monkeypatch.setattr(collector.news_mod, "_localize_coin_news_items", lambda _: [])
    def fail(*args, **kwargs):
        raise RuntimeError("fixture provider unavailable")
    monkeypatch.setattr(community_summaries, "enrich_items", fail)
    result = collector._localize_collected_payload({"items": raw}, FakeRepository())
    assert result["items"] == raw
    assert result["translation"]["pending_count"] == 1
    assert result["community_summaries"]["pending_count"] == 1


def test_body_hash_invalidates_snapshot_but_empty_legacy_fields_preserve_old_key():
    legacy = post()
    legacy.pop("community_body_hash")
    old = collector.analysis_fingerprint("CHIP", [legacy])
    assert old == collector.analysis_fingerprint("CHIP", [{**legacy, "community_body_hash": ""}])
    assert old == collector.analysis_fingerprint("CHIP", [{**legacy, "community_body_hash": None}])
    assert collector.analysis_fingerprint("CHIP", [post()]) != old
    assert collector.analysis_fingerprint("CHIP", [post()]) != collector.analysis_fingerprint("CHIP", [post(body_hash="b" * 64)])
    editorial = article("언론사 기사")
    assert collector.analysis_fingerprint("CHIP", [editorial]) == collector.analysis_fingerprint("CHIP", [{**editorial, "community_body_hash": "irrelevant"}])


def test_same_post_body_edit_publishes_without_summary_wait_or_rebuying_editorial_analysis(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(collector.news_mod, "_coin_snapshot_is_stale", lambda _: False)
    repo = LatestRepository()
    original = post(community_summary="예전 본문 요약", community_summary_status="ready")
    story = article("기사")
    analysis = {"items": [{"sentiment": "unclear", "summary": "커뮤니티 의견"},
                          {"sentiment": "positive", "summary": "유료 기사 분석"}],
                "analysis_status": "ready", "analysis_source": "ai", "ai": True}
    initial = collector.collect_payload("CHIP", {"symbol": "CHIP", "items": [original, story],
        "community_summaries": {"status": "ready", "ready_count": 1, "pending_count": 0}},
        repo=repo, analyzer=lambda *args, **kwargs: analysis, localize=False)
    monkeypatch.setattr(collector, "_localize_collected_payload", lambda *args, **kwargs: pytest.fail("early AI"))
    changed = post(body_hash="b" * 64, community_body="$CHIP support moved to 0.06.")
    result = collector.publish_initial_payload("CHIP", {"symbol": "CHIP", "items": [changed]}, repo=repo)
    latest = repo.get_latest_snapshot("CHIP")
    assert result["snapshot_key"] != initial["snapshot_key"]
    assert result["editorial_analysis_reused"] is True
    assert repo.budget_used == 1 and result["used_ai_budget"] is False
    assert latest["news_payload"]["items"][0]["community_body"] == changed["community_body"]
    assert latest["news_payload"]["community_summaries"]["pending_count"] == 1
    assert any(item.get("summary") == "유료 기사 분석" for item in latest["analysis"]["items"])


def test_body_and_summary_progress_counters_do_not_log_raw_bodies():
    payload = {"items": [post("123", community_summary_status="ready"),
        post("456", community_body="", community_body_status="missing", community_summary_status="unavailable"),
        post("789", community_body="", community_body_status="error", community_summary_status="unavailable"),
        post("111", community_summary_status="pending")]}
    progress = collector.community_progress(payload)
    assert progress["community_bodies"] == {"ready": 2, "missing": 1, "error": 1}
    assert progress["community_summaries"]["ready_count"] == 1
    assert progress["community_summaries"]["pending_count"] == 1
    assert progress["community_summaries"]["unavailable_count"] == 2
    assert "holds support" not in str(progress)
    summary = collector.summarize_results([{"status": "stored", **progress}] * 2)
    assert summary["community_bodies"]["ready"] == 4
    assert summary["community_summaries"]["unavailable_count"] == 4


def test_cycle_prunes_old_summaries_in_bounded_worker_maintenance(monkeypatch):
    calls = []
    monkeypatch.setattr(community_summary_repository, "prune_summaries", lambda **kwargs: calls.append(kwargs) or 7)
    result = collector.run_collection_cycle(repo=FakeRepository(), symbols=[], now_ms=12345)
    assert calls == [{"retention_days": 30, "limit": 500, "now_ms": 12345}]
    assert result["community_summaries_pruned"] == 7


def test_ticker_body_snapshot_is_readable_before_final_summary_starts(monkeypatch):
    repo = LatestRepository()
    raw = post()
    events = []
    monkeypatch.setattr(collector.news_mod, "_localize_coin_news_items", lambda items: items)
    def enrich(items, *, wait):
        snapshot = repo.get_latest_snapshot("CHIP")
        assert wait is True and snapshot is not None
        assert snapshot["news_payload"]["items"][0]["community_body_hash"] == raw["community_body_hash"]
        assert "community_summary" not in snapshot["news_payload"]["items"][0]
        events.append("summary")
        return [{**item, "community_summary": "작성자는 CHIP 지지선을 설명합니다.",
                 "community_summary_status": "ready"} for item in items], {"status": "ready", "pending_count": 0}
    monkeypatch.setattr(community_summaries, "enrich_items", enrich)
    result = collector.collect_ticker("CHIP", repo=repo,
        fetcher=lambda _: {"symbol": "CHIP", "items": [raw]},
        enricher=lambda symbol, payload: events.append("browser") or payload)
    assert events == ["browser", "summary"]
    assert result["community_summaries"]["ready_count"] == 1
    assert result["used_ai_budget"] is False
