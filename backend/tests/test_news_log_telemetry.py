"""Telemetry is local projection only; never fetch providers for log readers."""
import json

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
