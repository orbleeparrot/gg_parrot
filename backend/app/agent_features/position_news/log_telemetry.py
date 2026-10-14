"""Read-only news provenance and safe Prefect correlation (no new I/O)."""
from collections import Counter

from ...agent_event_schema import safe_details, safe_identifier, safe_timestamp


def collection_trace() -> dict:
    # Runtime fields are empty outside a Prefect run; never make up trace IDs.
    from prefect.runtime import flow_run, task_run
    result = {}
    for key, value in (("flow_run_id", flow_run.id), ("task_run_id", task_run.id)):
        if identifier := safe_identifier(value):
            result[key] = identifier
    if result.get("flow_run_id"):
        result["collection_run_id"] = result["flow_run_id"]
    return result


def attach_collection_trace(payload: dict) -> dict:
    trace = collection_trace()
    if trace:
        return {**payload, "collection_trace": trace}
    # Embedded collection is not a Prefect run. A merged older payload must
    # not borrow its previous successful flow/task IDs for this publication.
    return {key: value for key, value in payload.items() if key != "collection_trace"} if "collection_trace" in payload else payload


def delivery_metadata(payload: dict, *, collection: dict | None = None) -> dict:
    """Project existing prepared feed metadata, not a new collection attempt.

    Source counts here mean articles in this response. A cached RSS page can
    have more entries and a delta response can have zero; neither is a failure.
    """
    # Prepared articles survive an empty/failed next attempt. Their source
    # diagnostics and Prefect trace describe their last publication, not that
    # newer attempt. A ready row can also be claimed while keeping old status;
    # the repository advances last_attempt_ms before I/O and makes it equal to
    # last_success_ms on success. These are durable observation counters, not a
    # guessed comparison against publication/worker wall clocks.
    current_publication = not collection or (
        collection.get("status") == "ready"
        and int(collection.get("last_attempt_ms") or 0) <= int(collection.get("last_success_ms") or 0)
    )
    trace = (payload.get("collection_trace") or {}) if current_publication else {}
    trace = trace if isinstance(trace, dict) else {}
    metadata = safe_details({**trace, "source_counts": dict(Counter(
        str(item.get("source") or "unknown") for item in payload.get("items") or []
        if isinstance(item, dict)))})
    summaries = []
    for source in ((payload.get("sources") or [])[:8] if current_publication else []):
        if not isinstance(source, dict):
            continue
        facts = {"source": source.get("name"), "status": source.get("status"), "stage": source.get("phase"),
                 **{key: source[key] for key in ("http_status", "fetched_count", "retry_at", "error", "attempted") if key in source}}
        for origin, target in (("item_count", "count"), ("elapsed_ms", "duration_ms"), ("cached", "cache_hit")):
            if origin in source:
                facts[target] = source[origin]
        summaries.append(safe_details(facts))
    if summaries:
        metadata["source_results"] = summaries
    publication = payload.get("article_publication")
    if isinstance(publication, dict) and publication.get("event_name") == "news.articles_ready":
        source_id = safe_identifier(publication.get("source_event_id"))
        occurred_at = safe_timestamp(publication.get("occurred_at"))
        article_ids = publication.get("article_ids")
        if source_id and occurred_at and isinstance(article_ids, list):
            published = {value for value in article_ids if isinstance(value, str)}
            # The feed remembers its latest publication, while a reset/page
            # can also return articles from older publications. Reference the
            # actual source fact only if this final response includes one of
            # its articles; never apply its count/time to every delivered item.
            if any(isinstance(item, dict) and item.get("id") in published
                   for item in payload.get("items") or []):
                metadata.update(source_event_id=source_id, source_occurred_at=occurred_at)
    return metadata
