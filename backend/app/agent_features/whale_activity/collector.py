"""Central public-trade collection, shared by Prefect and first-run bootstrap."""
from __future__ import annotations

import os
import time

from ... import whales
from . import repository


def configuration():
    return {**whales.configuration(), "shared_cache": True, "http_collects": False,
            "collection_seconds": repository.collection_interval_seconds(),
            "stale_seconds": repository.stale_seconds(), "cycle_budget_seconds": 20,
            "version": os.environ.get("RENDER_GIT_COMMIT", "local")}


def collect_pair(symbol, market, *, fetcher=None):
    token = repository.claim_collection(symbol, market)
    if not token:
        return {"symbol": symbol, "market": market, "status": "skipped", "reason": "not_due_or_claimed"}
    started = time.monotonic()
    try:
        payload = (fetcher or whales.fetch_large_trade_activity)(symbol, market)
    except whales.LargeTradeSourceError as exc:
        stored = repository.record_failure(symbol, market, token, error_code=exc.code,
                                           delay_seconds=exc.retry_after_seconds)
        return {"symbol": symbol, "market": market, "status": "error" if stored else "superseded",
                "error_code": exc.code, "http_status": exc.http_status,
                "retry_after_seconds": exc.retry_after_seconds,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except Exception:
        repository.record_failure(symbol, market, token, error_code="invalid_response")
        return {"symbol": symbol, "market": market, "status": "error", "error_code": "invalid_response"}
    # A storage exception must fail the Prefect task; do not label it a provider
    # success or retry the same external request inside this run.
    stored = repository.store_result(symbol, market, token, payload)
    return {"symbol": symbol, "market": market,
            "status": payload["status"] if stored else "superseded",
            "sampled_trades": payload.get("sampled_trades", 0),
            "large_trade_count": len(payload.get("items") or []),
            "http_status": payload.get("http_status", 200),
            "elapsed_ms": round((time.monotonic() - started) * 1000)}


def run_collection_cycle(*, bootstrap_only=False, collect=None):
    pairs = repository.discover_pairs(due_only=True, bootstrap_only=bootstrap_only)
    started = time.monotonic()
    results = []
    for pair in pairs:
        # Do not begin another 8-second HTTP call without room in this cycle.
        if time.monotonic() - started > 12:
            break
        results.append((collect or collect_pair)(pair["symbol"], pair["market"]))
    return {"eligible_pair_count": len(pairs), "collected_count": len(results),
            "failed_count": sum(row["status"] == "error" for row in results),
            "items": results, "ai_calls": 0}
