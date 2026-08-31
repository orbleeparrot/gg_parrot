"""Pure central collection use-case; Prefect is only an orchestration adapter."""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from typing import Callable, Iterable

from ... import news as news_mod
from . import classifier


class NewsCollectionError(RuntimeError):
    pass


def _analysis_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-opus-5"


def analysis_fingerprint(asset_symbol: str, items: list[dict]) -> str:
    """Stable shared-work key; user, session and position are intentionally absent."""
    normalized_items = sorted(
        (
            {
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
            }
            for item in items
        ),
        key=lambda item: (item["title"].casefold(), item["source"].casefold()),
    )
    material = {
        "asset_symbol": news_mod.canonical_asset_symbol(asset_symbol),
        "prompt_version": classifier.PROMPT_VERSION,
        "model": _analysis_model(),
        "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "items": normalized_items,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_ticker_window(
    symbols: Iterable[str],
    *,
    limit: int,
    now_ms: int | None = None,
) -> list[str]:
    """Rotate a bounded list of already-derived asset tickers."""
    normalized = sorted({
        asset
        for symbol in symbols
        if (asset := news_mod.canonical_asset_symbol(symbol))
    })
    bounded = max(1, limit)
    if len(normalized) <= bounded:
        return normalized
    cycle_ms = max(
        60,
        int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")),
    ) * 1000
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    cycle_index = current_ms // cycle_ms
    start = (cycle_index * bounded) % len(normalized)
    return [
        normalized[(start + offset) % len(normalized)]
        for offset in range(bounded)
    ]


def _default_repository():
    from . import repository

    return repository


def collect_payload(
    symbol: str,
    news_payload: dict,
    *,
    repo=None,
    analyzer: Callable[..., dict] | None = None,
    allow_ai: bool = True,
    now_ms: int | None = None,
) -> dict:
    """Claim, analyze at most once, and persist one already-fetched ticker."""
    repo = repo or _default_repository()
    analyzer = analyzer or classifier.analyze_headlines
    asset = news_mod.canonical_asset_symbol(
        str(news_payload.get("symbol") or symbol)
    )
    if not asset:
        return {"asset_symbol": "", "status": "invalid", "used_ai_budget": False}

    items = list(news_payload.get("items") or [])
    if not items:
        repo.mark_collection_outcome(asset, "empty", now_ms=now_ms)
        return {
            "asset_symbol": asset,
            "status": "empty",
            "used_ai_budget": False,
        }

    snapshot_key = analysis_fingerprint(asset, items)
    claim = repo.claim_snapshot(
        asset_symbol=asset,
        snapshot_key=snapshot_key,
        news_payload=news_payload,
        prompt_version=classifier.PROMPT_VERSION,
        model=_analysis_model(),
        retry_incomplete=allow_ai,
        now_ms=now_ms,
    )
    if claim.status != "claimed":
        return {
            "asset_symbol": asset,
            "snapshot_key": snapshot_key,
            "status": claim.status,
            "used_ai_budget": False,
        }

    claimed_payload = getattr(claim, "news_payload", None) or news_payload
    claimed_items = list(claimed_payload.get("items") or [])
    coin_name = str(claimed_payload.get("coin_name") or asset)
    wants_ai = bool(os.environ.get("ANTHROPIC_API_KEY")) and allow_ai
    reserved_ai = False
    if wants_ai:
        daily_limit = max(
            0,
            int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "120")),
        )
        reserved_ai = repo.reserve_ai_budget(
            daily_limit=daily_limit,
            now_ms=now_ms,
        )

    if (
        wants_ai
        and not reserved_ai
        and bool(getattr(claim, "had_usable_analysis", False))
    ):
        repo.release_usable_claim(claim.snapshot_id, claim.claim_token)
        return {
            "asset_symbol": asset,
            "snapshot_key": snapshot_key,
            "status": "reused",
            "budget_status": "daily_limit",
            "used_ai_budget": False,
        }

    try:
        analysis = analyzer(
            claimed_items,
            coin_name,
            allow_ai=reserved_ai,
        )
        completed = repo.complete_snapshot(
            claim.snapshot_id,
            analysis,
            claim_token=claim.claim_token,
            now_ms=now_ms,
        )
    except Exception as exc:
        repo.fail_snapshot(
            claim.snapshot_id,
            str(exc),
            claim_token=claim.claim_token,
            now_ms=now_ms,
        )
        raise NewsCollectionError(f"{asset} 뉴스 분석 실패") from exc

    if completed is False:
        return {
            "asset_symbol": asset,
            "snapshot_key": snapshot_key,
            "status": "superseded",
            "used_ai_budget": reserved_ai,
        }
    return {
        "asset_symbol": asset,
        "snapshot_key": snapshot_key,
        "status": "stored",
        "analysis_status": analysis.get("analysis_status"),
        "used_ai_budget": reserved_ai,
    }


def collect_ticker(
    symbol: str,
    *,
    repo=None,
    fetcher: Callable[[str], dict] | None = None,
    analyzer: Callable[..., dict] | None = None,
    allow_ai: bool = True,
    now_ms: int | None = None,
) -> dict:
    """Fetch then delegate to the retry-free claim/analyze/persist boundary."""
    repo = repo or _default_repository()
    fetcher = fetcher or news_mod.fetch_coin_news_for_collector
    asset = news_mod.canonical_asset_symbol(symbol)
    if not asset:
        return {"asset_symbol": "", "status": "invalid", "used_ai_budget": False}

    try:
        news_payload = fetcher(asset)
    except Exception as exc:
        repo.mark_collection_outcome(
            asset,
            "error",
            error=str(exc),
            now_ms=now_ms,
        )
        raise NewsCollectionError(f"{asset} 뉴스 수집 실패") from exc
    return collect_payload(
        asset,
        news_payload,
        repo=repo,
        analyzer=analyzer,
        allow_ai=allow_ai,
        now_ms=now_ms,
    )


def summarize_results(results: list[dict], *, removed: int = 0) -> dict:
    counts = Counter(str(item.get("status") or "unknown") for item in results)
    return {
        "ticker_count": len(results),
        "stored": counts["stored"],
        "reused": counts["reused"],
        "pending": counts["pending"],
        "superseded": counts["superseded"],
        "skipped": counts["skipped"],
        "empty": counts["empty"],
        "invalid": counts["invalid"],
        "error": counts["error"],
        "ai_budget_used": sum(
            1 for item in results if item.get("used_ai_budget")
        ),
        "pruned": removed,
        "items": results,
    }


def run_collection_cycle(
    *,
    repo=None,
    symbols: Iterable[str] | None = None,
    fetcher: Callable[[str], dict] | None = None,
    analyzer: Callable[..., dict] | None = None,
    max_tickers: int | None = None,
    max_ai_analyses: int | None = None,
    retention_days: int | None = None,
    now_ms: int | None = None,
) -> dict:
    """Network-free-testable cycle used by CLI and non-Prefect fallbacks."""
    repo = repo or _default_repository()
    if symbols is None:
        discovered = repo.discover_tracked_symbols()
    else:
        discovered = [
            asset
            for symbol in symbols
            if (asset := news_mod.asset_from_market_symbol(symbol))
        ]
    ticker_limit = max_tickers or max(
        1,
        int(os.environ.get("POSITION_NEWS_MAX_TICKERS_PER_RUN", "100")),
    )
    selected = select_ticker_window(
        discovered,
        limit=ticker_limit,
        now_ms=now_ms,
    )
    ai_limit = max_ai_analyses
    if ai_limit is None:
        ai_limit = max(
            0,
            int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "12")),
        )

    results: list[dict] = []
    ai_used = 0
    for asset in selected:
        try:
            result = collect_ticker(
                asset,
                repo=repo,
                fetcher=fetcher,
                analyzer=analyzer,
                allow_ai=ai_used < ai_limit,
                now_ms=now_ms,
            )
        except NewsCollectionError as exc:
            result = {
                "asset_symbol": asset,
                "status": "error",
                "error": str(exc),
                "used_ai_budget": False,
            }
        if result.get("used_ai_budget"):
            ai_used += 1
        results.append(result)

    keep_days = retention_days
    if keep_days is None:
        keep_days = max(
            1,
            int(os.environ.get("POSITION_NEWS_RETENTION_DAYS", "30")),
        )
    removed = repo.prune_snapshots(
        retention_days=keep_days,
        now_ms=now_ms,
    )
    return summarize_results(results, removed=removed)
