"""Telemetry is local projection only; never fetch providers for log readers."""
import json

import pytest

from app.agent_features.position_news import log_telemetry
from app.workflows import position_news as workflow


def test_missing_prefect_context_does_not_invent_identifiers(monkeypatch):
    from prefect.runtime import flow_run, task_run
    monkeypatch.setattr(flow_run, "id", None)
    monkeypatch.setattr(task_run, "id", None)
    payload = {"symbol": "BTC", "items": []}
    assert log_telemetry.attach_collection_trace(payload) is payload
    assert "operation_id" not in workflow._collector_event("news_source", {})


def test_actual_prefect_context_survives_delivery_projection(monkeypatch):
    from prefect.runtime import flow_run, task_run
    monkeypatch.setattr(flow_run, "id", "flow-123")
    monkeypatch.setattr(task_run, "id", "task-456")
    payload = log_telemetry.attach_collection_trace({"items": [
        {"source": "CoinDesk"}, {"source": "CoinDesk"}, {"source": "Decrypt"}]})
    assert log_telemetry.delivery_metadata(payload) == {
        "flow_run_id": "flow-123", "task_run_id": "task-456",
        "collection_run_id": "flow-123", "source_counts": {"CoinDesk": 2, "Decrypt": 1}}
    event = workflow._collector_event("news_source", {"http_status": 429}, status="error")
    assert event["operation_id"] == "collection:flow-123"
    assert event["component"] == "position_news_collector" and event["severity"] == "ERROR"
    assert event["http_status"] == 429


def test_diagnostics_redact_secrets_and_keep_real_limiting_fields():
    event = workflow._collector_event("browser_source", {
        "response_url": "https://www.coindesk.com/markets?token=secret",
        "response_headers": {"server": "cloudflare", "retry-after": "300", "authorization": "Bearer SECRET"},
        "error": "api_key=TOPSECRET postgres://user:password@example.com/db",
        "http_status": 429, "attempted": False, "phase": "publisher_cooldown"}, status="error")
    serialized = json.dumps(event)
    assert "TOPSECRET" not in serialized and "SECRET" not in serialized
    assert "user:password" not in serialized and "token=secret" not in serialized
    assert event["response_headers"] == {"server": "cloudflare", "retry-after": "300"}
    assert event["attempted"] is False and event["phase"] == "publisher_cooldown"


def test_unknown_metadata_is_not_copied_to_customer_receipts():
    payload = {"collection_trace": {"flow_run_id": "actual", "api_key": "SECRET", "headers": {"cookie": "SECRET"}},
               "items": [{"source": "CoinDesk"}]}
    assert log_telemetry.delivery_metadata(payload) == {"flow_run_id": "actual", "source_counts": {"CoinDesk": 1}}


def test_prepared_source_diagnostics_preserve_actual_attempt_and_http_status():
    facts = log_telemetry.delivery_metadata({"items": [], "sources": [
        {"name": "coindesk_topic", "status": "error", "phase": "publisher_cooldown", "attempted": False,
         "http_status": 429, "fetched_count": 0, "item_count": 0, "elapsed_ms": 5, "cached": True,
         "authorization": "SECRET", "response_headers": {"cookie": "SECRET"}},
        {"name": "google_rss", "status": "ready", "item_count": 2}]})
    assert facts["source_results"][0] == {"source": "coindesk_topic", "status": "error", "stage": "publisher_cooldown",
        "attempted": False, "http_status": 429, "fetched_count": 0, "count": 0, "duration_ms": 5, "cache_hit": True}
    assert "http_status" not in facts["source_results"][1]
    assert facts["source_counts"] == {}  # delta response has no delivered articles
    assert "SECRET" not in json.dumps(facts)


def test_trace_metadata_does_not_change_paid_analysis_work_identity(monkeypatch):
    from app.agent_features.position_news import collector
    monkeypatch.setattr(collector, "ai_available", lambda: True)
    items = [{"title": "비트코인 ETF 소식", "source": "CoinDesk", "excerpt": "기사 내용"}]
    plain = {"items": items}
    traced = {**plain, "collection_trace": {"flow_run_id": "later-run"}}
    assert collector.analysis_fingerprint("BTC", plain["items"]) == collector.analysis_fingerprint("BTC", traced["items"])


def test_trace_reuses_existing_prepared_feed_metadata_storage():
    from sqlmodel import Session, create_engine
    from app.agent_features.position_news.articles import NewsArticle, NewsArticleFeed, upsert_articles
    engine = create_engine("sqlite://")
    try:
        NewsArticleFeed.__table__.create(engine)
        NewsArticle.__table__.create(engine)
        with Session(engine) as db:
            upsert_articles("BTC", [{"title": "비트코인 신규 업데이트", "source": "CoinDesk",
                "url": "https://www.coindesk.com/markets/update", "published": "2026-10-13T00:00:00Z"}],
                payload={"symbol": "BTC", "collection_trace": {"collection_run_id": "flow-real", "task_run_id": "task-real"}},
                now_ms=1791853200000, db=db)
            assert json.loads(db.get(NewsArticleFeed, "BTC").metadata_json)["collection_trace"] == {
                "collection_run_id": "flow-real", "task_run_id": "task-real"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("base, completed_key, source_name", [
    ("BTC", "google", "google_news_rss"),
    ("EDEN", "coindesk", "coindesk_rss"),
    ("EDEN", "openeden", "openeden_official_rss"),
])
def test_partial_collector_never_reports_unfinished_sources_as_failures(monkeypatch, base, completed_key, source_name):
    from app import news
    monkeypatch.setattr(news, "_with_news_history", lambda value: value)
    recorded = []
    monkeypatch.setattr(news, "_record_collector_sources", lambda rows: recorded.append(rows))
    completed = {completed_key: ({"items": []}, True) if completed_key == "google" else ([], True)}
    payload = news._collected_news_envelope(base, base, "actual query", completed, partial=True)
    assert payload["sources"] == [{"name": source_name, "status": "ready", "item_count": 0, "fetched_count": 0}]
    assert recorded == [], "Intermediate publications must not increase final source counters"


def test_missing_prefect_context_removes_inherited_trace(monkeypatch):
    from prefect.runtime import flow_run, task_run
    monkeypatch.setattr(flow_run, "id", None)
    monkeypatch.setattr(task_run, "id", None)
    payload = {"items": [], "collection_trace": {"flow_run_id": "old-success"}}
    assert "collection_trace" not in log_telemetry.attach_collection_trace(payload)
    assert payload["collection_trace"] == {"flow_run_id": "old-success"}, "Projection must not mutate cached input"


@pytest.mark.parametrize("state", [
    {"status": "error", "last_attempt_ms": 2000, "last_success_ms": 1000},
    {"status": "empty", "last_attempt_ms": 2000, "last_success_ms": 1000},
    {"status": "pending", "last_attempt_ms": 2000, "last_success_ms": 1000},
    # A claimed next attempt retains ready until its result has been written.
    {"status": "ready", "last_attempt_ms": 2000, "last_success_ms": 1000},
])
def test_current_collection_state_never_inherits_old_success_trace(state):
    payload = {"collection_trace": {"flow_run_id": "old-flow", "collection_run_id": "old-flow"},
               "items": [{"source": "CoinDesk"}],
               "sources": [{"name": "coindesk_rss", "status": "ready", "item_count": 1}]}
    assert log_telemetry.delivery_metadata(payload, collection=state) == {"source_counts": {"CoinDesk": 1}}


def test_successful_current_collection_keeps_real_trace_without_comparing_publication_clocks():
    payload = {"collection_trace": {"flow_run_id": "actual-flow"},
               "items": [{"source": "CoinDesk"}],
               "sources": [{"name": "coindesk_rss", "status": "ready", "item_count": 1}]}
    facts = log_telemetry.delivery_metadata(payload, collection={
        "status": "ready", "last_attempt_ms": 2000, "last_success_ms": 2000})
    assert facts["flow_run_id"] == "actual-flow"
    assert facts["source_results"] == [{"source": "coindesk_rss", "status": "ready", "count": 1}]


def test_delivery_references_only_an_actual_publication_contained_in_returned_articles():
    payload = {"items": [{"id": "old-article", "source": "CoinDesk"}, {"id": "new-article", "source": "Decrypt"}],
        "article_publication": {"event_name": "news.articles_ready", "source_event_id": "news-ready:BTC:24",
            "occurred_at": "2026-10-14T00:00:00Z", "article_ids": ["new-article"], "count": 1, "cursor": 24}}
    facts = log_telemetry.delivery_metadata(payload)
    assert facts["source_event_id"] == "news-ready:BTC:24"
    assert facts["source_occurred_at"] == "2026-10-14T00:00:00+00:00"
    assert facts["source_counts"] == {"CoinDesk": 1, "Decrypt": 1}
    assert "count" not in facts, "A one-article source publication must not claim the entire response was that event"
    assert "source_event_id" not in log_telemetry.delivery_metadata({**payload, "items": payload["items"][:1]})
    assert "source_event_id" not in log_telemetry.delivery_metadata({**payload, "items": []})


def test_old_publication_reference_is_not_a_new_failed_collection_trace():
    payload = {"items": [{"id": "retained-article", "source": "CoinDesk"}],
        "collection_trace": {"flow_run_id": "old-flow"},
        "article_publication": {"event_name": "news.articles_ready", "source_event_id": "news-ready:BTC:4",
            "occurred_at": "2026-10-13T00:00:00Z", "article_ids": ["retained-article"]}}
    facts = log_telemetry.delivery_metadata(payload, collection={
        "status": "error", "last_attempt_ms": 2000, "last_success_ms": 1000})
    assert facts == {"source_counts": {"CoinDesk": 1}, "source_event_id": "news-ready:BTC:4",
                     "source_occurred_at": "2026-10-13T00:00:00+00:00"}


@pytest.mark.parametrize("field,value", [
    ("event_name", "news.response_received"), ("source_event_id", "api_key=secret"),
    ("occurred_at", "invalid"), ("article_ids", "not-a-list"),
])
def test_invalid_publication_metadata_is_not_projected(field, value):
    publication = {"event_name": "news.articles_ready", "source_event_id": "news-ready:BTC:4",
        "occurred_at": "2026-10-13T00:00:00Z", "article_ids": ["article"]}
    facts = log_telemetry.delivery_metadata({"items": [{"id": "article", "source": "CoinDesk"}],
                                            "article_publication": {**publication, field: value}})
    assert "source_event_id" not in facts and "source_occurred_at" not in facts
