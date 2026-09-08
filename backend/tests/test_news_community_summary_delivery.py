"""Public news keeps Korean titles visible while private bodies are summarized."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app import news
from app.agent_features.position_news import service


BODY = "The author discusses CHIP trading volume and uncertain market direction."
SUMMARY = (
    "작성자는 CHIP의 최근 거래량과 가격 흐름을 비교하며 거래량이 늘어나는 동안 기존 지지 구간이 유지되는지 "
    "지켜봐야 한다고 설명하고, 짧은 시간의 가격 변화만으로 시장 방향을 단정하기 어렵다는 개인적인 의견을 제시했어요. "
    "이 게시글은 확인된 프로젝트 발표나 확정적인 가격 예측이 아니라 작성자가 관찰한 시장 흐름에 대한 해석이며, "
    "추가 거래량과 가격 반응을 함께 확인해야 한다는 내용을 담고 있어요."
)


def post(**changes):
    return {
        "content_type": "community", "community_post_id": "123456789", "source": "Binance Square",
        "author": "시장기록자", "url": "https://www.binance.com/en/square/post/123456789",
        "title": "CHIP 커뮤니티 거래량 분석", "published": datetime.now(timezone.utc).isoformat(),
        "community_body": BODY, "community_body_hash": "body-hash",
        "community_body_status": "ready", "community_body_truncated": True,
        **changes,
    }


def summary_result(items, *, ready):
    enriched = [{**item, "community_summary": SUMMARY if ready else "",
                 "community_summary_status": "ready" if ready else "pending",
                 "community_summary_partial": True} if item.get("content_type") == "community"
                else dict(item) for item in items]
    pending = sum(item.get("community_summary_status") == "pending" for item in enriched)
    return enriched, {"status": "partial" if pending else "ready", "pending_count": pending,
                      **({"retry_after_seconds": 30} if pending else {})}


def assert_no_body(payload):
    for item in payload["items"]:
        assert not set(news._COMMUNITY_BODY_FIELDS).intersection(item)
    assert BODY not in str(payload)


@pytest.fixture
def summary_module():
    from app import community_summaries
    return community_summaries


def test_localization_enriches_only_ready_titles_without_waiting_or_exposing_bodies(monkeypatch, summary_module):
    raw = {"symbol": "CHIP", "items": [post(), post(community_post_id="123456790", title="CHIP market outlook",
                                                   url="https://www.binance.com/en/square/post/123456790")]}
    original = deepcopy(raw)
    monkeypatch.setattr(news, "_ensure_title_translations", lambda _: None)
    monkeypatch.setattr(news, "_title_translation_cache", {})
    calls = []

    def enrich(items, *, wait):
        assert wait is False
        assert len(items) == 1 and items[0]["community_body"] == BODY
        calls.append(items[0]["community_post_id"])
        return summary_result(items, ready=False)

    monkeypatch.setattr(summary_module, "enrich_items", enrich)
    result = news._localize_news_payload(raw)
    assert result["items"][0]["title"] == raw["items"][0]["title"]
    assert result["translation"] == {"status": "partial", "pending_count": 1, "retry_after_seconds": 30}
    assert result["community_summaries"] == {"status": "partial", "pending_count": 1, "retry_after_seconds": 30}
    assert calls == ["123456789"] and raw == original
    assert_no_body(result)


@pytest.mark.parametrize("market", [False, True])
def test_raw_public_cache_reuses_body_and_refreshes_summary_without_recrawling(monkeypatch, summary_module, market):
    original = post()
    fetched, enriched = [], []
    monkeypatch.setattr(news, "_coin_cache", {})
    monkeypatch.setattr(news, "_cache", {})
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _: None)

    def enrich(items, *, wait):
        assert wait is False and items[0]["community_body"] == BODY
        enriched.append(True)
        return summary_result(items, ready=len(enriched) > 1)

    monkeypatch.setattr(summary_module, "enrich_items", enrich)
    if market:
        monkeypatch.setattr(news, "_fetch_news", lambda *_a, **_k: fetched.append(True) or [original])
        monkeypatch.setattr(news, "_summarize", lambda *_a, **_k: None)
        monkeypatch.setattr(news, "_load_durable_market_summary", lambda _: None)
        read = news.get_market_news
    else:
        monkeypatch.setattr(news, "_coin_news_envelope", lambda *_a, **_k: fetched.append(True) or {"items": [original]})
        read = lambda: news.get_coin_news("CHIPUSDT")
    first, second = read(), read()
    assert first["translation"] == second["translation"] == {"status": "ready", "pending_count": 0}
    assert first["community_summaries"]["pending_count"] == 1
    assert second["community_summaries"] == {"status": "ready", "pending_count": 0}
    assert second["items"][0]["community_summary"] == SUMMARY
    assert second["items"][0]["community_summary_partial"] is True
    assert fetched == [True] and len(enriched) == 2
    assert original["community_body"] == BODY
    assert_no_body(first)
    assert_no_body(second)


def test_agent_projection_keeps_prepared_summary_without_private_fields_or_headline_truncation(monkeypatch, summary_module):
    assert len(SUMMARY) > 180
    raw = post(community_summary=SUMMARY, community_summary_status="ready", community_summary_partial=True)
    result = service.build_position_news(
        {"session_id": 41, "symbol": "CHIPUSDT", "position_side": "long"},
        {"symbol": "CHIP", "items": [raw]}, {"items": []},
    )
    assert result["items"][0]["community_summary"] == SUMMARY
    assert result["items"][0]["community_summary_status"] == "ready"
    assert result["items"][0]["community_summary_partial"] is True
    assert result["items"][0]["position_effect"] == "unclear"
    assert_no_body(result)
    monkeypatch.setattr(summary_module, "_schedule", lambda _: pytest.fail("prepared summary must not be regenerated"))
    public = news._localize_news_payload(result)
    assert public["items"][0]["community_summary"] == SUMMARY
    assert public["community_summaries"] == {"status": "ready", "pending_count": 0}
    assert_no_body(public)


def test_agent_read_passes_private_body_to_summary_then_strips_it_and_preserves_article_analysis(monkeypatch, summary_module):
    article = {"title": "공식 파트너십 체결 발표", "source": "CoinDesk", "url": "https://example.com/news"}
    stored = {"snapshot_id": "same-snapshot", "news_payload": {"symbol": "CHIP", "items": [post(), article]},
              "analysis": {"items": [{}, {"sentiment": "positive", "summary": "공식 파트너십 체결이 발표됐어요."}]},
              "collection": {"status": "ready", "last_success_ms": int(datetime.now(timezone.utc).timestamp() * 1000)}}
    original = deepcopy(stored)
    monkeypatch.setattr(service, "_load_latest_snapshot", lambda *_: stored)
    phases = []

    def enrich(items, *, wait):
        assert wait is False and items[0]["community_body"] == BODY
        phases.append(True)
        return summary_result(items, ready=len(phases) > 1)

    monkeypatch.setattr(summary_module, "enrich_items", enrich)
    title_check = news._title_needs_korean_translation

    def headline_only(value):
        assert value != SUMMARY, "Body summaries must not use headline translation heuristics"
        return title_check(value)

    monkeypatch.setattr(news, "_title_needs_korean_translation", headline_only)
    session = {"session_id": 41, "symbol": "CHIPUSDT", "position_side": "long"}
    pending, ready = service.get_position_news(session), service.get_position_news(session)
    assert pending["snapshot_id"] == ready["snapshot_id"]
    assert [item["id"] for item in pending["items"]] == [item["id"] for item in ready["items"]]
    assert pending["translation"]["pending_count"] == ready["translation"]["pending_count"] == 0
    assert pending["community_summaries"]["pending_count"] == 1
    assert ready["community_summaries"]["pending_count"] == 0
    assert ready["items"][0]["community_summary"] == SUMMARY
    assert ready["items"][0]["position_effect"] == "unclear"
    assert ready["items"][1]["position_effect"] == "favorable"
    assert "community_summary" not in ready["items"][1]
    assert stored == original
    assert_no_body(pending)
    assert_no_body(ready)
