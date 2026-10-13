"""Read-only news provenance and safe Prefect correlation (no new I/O)."""
from collections import Counter

from ...agent_event_schema import safe_details, safe_identifier


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
    return {**payload, "collection_trace": trace} if trace else payload


def delivery_metadata(payload: dict) -> dict:
    """Project existing prepared feed metadata, not a new collection attempt.

    Source counts here mean articles in this response. A cached RSS page can
    have more entries and a delta response can have zero; neither is a failure.
    """
    trace = payload.get("collection_trace") or {}
    trace = trace if isinstance(trace, dict) else {}
    metadata = safe_details({**trace, "source_counts": dict(Counter(
        str(item.get("source") or "unknown") for item in payload.get("items") or []
        if isinstance(item, dict)))})
    summaries = []
    for source in (payload.get("sources") or [])[:8]:
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
    return metadata
