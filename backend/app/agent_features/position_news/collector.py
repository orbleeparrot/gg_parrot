"""Pure central collection use-case; Prefect is only an orchestration adapter."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter
from typing import Callable, Iterable

from ... import news as news_mod
from . import classifier


class NewsCollectionError(RuntimeError):
    pass


def _localize_collected_payload(payload: dict, repo, now_ms=None) -> dict:
    """One bounded shared translation batch; failures preserve source titles."""
    items = [dict(item) for item in payload.get("items") or []]
    titles = list(dict.fromkeys(
        str(item.get("title") or "") for item in items
        if not item.get("original_title")
        and news_mod._title_needs_korean_translation(str(item.get("title") or ""))
    ))[:10]
    if not titles:
        return payload
    token = ""
    claimed = []
    translations = {}
    try:
        cached = repo.get_title_translations(titles)
        rejected = [title for title, value in cached.items()
                    if not news_mod._valid_title_translation(title, value)]
        translations = {title: news_mod._normalize_title_translation(title, value)
                        for title, value in cached.items() if title not in rejected}
        missing = [title for title in titles if title not in translations]
        if missing and os.environ.get("ANTHROPIC_API_KEY"):
            claim = repo.claim_title_translations(missing, rejected_titles=rejected, now_ms=now_ms)
            translations.update(claim.get("cached") or {})
            claimed = claim.get("claimed") or []
            token = claim.get("claim_token") or ""
            if claimed and repo.reserve_ai_budget(
                daily_limit=max(0, int(os.environ.get("POSITION_NEWS_TRANSLATION_MAX_CALLS_PER_DAY", "10"))),
                namespace="position_news_translation", now_ms=now_ms,
            ):
                translated = news_mod._request_korean_title_translations(claimed)
                translated = {title: news_mod._normalize_title_translation(title, value)
                              for title, value in translated.items()
                              if title in claimed and news_mod._valid_title_translation(title, value)}
                repo.store_title_translations(translated, claim_token=token, now_ms=now_ms)
                translations.update(translated)
    except Exception as exc:
        logging.getLogger(__name__).warning("Ticker title translation unavailable (%s); retaining original titles",
                                           type(exc).__name__)
    finally:
        if token:
            repo.release_title_translation_claims(claimed, claim_token=token)
    for item in items:
        original = item.get("title") or ""
        translated = translations.get(original)
        if translated and news_mod._valid_title_translation(original, translated):
            item.update(title=translated, original_title=original)
    return {**payload, "items": items}


def _analysis_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-haiku-4-5"


def analysis_fingerprint(asset_symbol: str, items: list[dict]) -> str:
    """Stable shared-work key; user, session and position are intentionally absent."""
    normalized_items = sorted(
        (
            {
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "excerpt": str(item.get("excerpt") or "").strip(),
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
    localize: bool = True,
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
            int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "10")),
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
        # Publish headlines before any paid/slow enrichment. The same claim
        # stays fenced until final analysis, so reads can already show articles.
        if os.environ.get("ANTHROPIC_API_KEY") and not claim.had_usable_analysis:
            baseline = classifier.analyze_headlines(claimed_items, coin_name, allow_ai=False)
            repo.complete_snapshot(claim.snapshot_id, baseline,
                claim_token=claim.claim_token, keep_claim=True, now_ms=now_ms)
        analysis = analyzer(
            claimed_items,
            coin_name,
            allow_ai=reserved_ai,
        )
        localized_payload = (
            _localize_collected_payload(claimed_payload, repo, now_ms)
            if localize else claimed_payload
        )
        completed = repo.complete_snapshot(
            claim.snapshot_id,
            analysis,
            claim_token=claim.claim_token,
            news_payload=localized_payload,
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


def browser_enrichment_enabled() -> bool:
    return os.environ.get("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true").lower() not in {
        "0", "false", "no",
    }


def publish_initial_payload(symbol: str, payload: dict, *, repo=None, now_ms=None) -> dict:
    """Make the first headlines visible without a browser or a paid API call."""
    repo = repo or _default_repository()
    if repo.get_latest_snapshot(symbol):
        # Do not replace an existing complete browser/AI snapshot with a
        # temporary RSS-only view on every refresh.
        return {"asset_symbol": symbol, "status": "reused", "used_ai_budget": False}
    return collect_payload(symbol, payload, repo=repo, allow_ai=False,
                           localize=False, now_ms=now_ms)


def enrich_payload(symbol: str, payload: dict, *, enricher=None) -> dict:
    """A browser outage must never remove already collected RSS articles."""
    if enricher is None and not browser_enrichment_enabled():
        return payload
    enricher = enricher or news_mod.enrich_coin_news_for_collector
    try:
        return enricher(symbol, payload)
    except Exception:
        logging.getLogger(__name__).exception("Browser enrichment failed for %s", symbol)
        return {**payload, "browser_enrichment": {"status": "error", "item_count": 0}}


def collect_ticker(
    symbol: str,
    *,
    repo=None,
    fetcher: Callable[[str], dict] | None = None,
    enricher: Callable[[str, dict], dict] | None = None,
    analyzer: Callable[..., dict] | None = None,
    allow_ai: bool = True,
    now_ms: int | None = None,
) -> dict:
    """Publish RSS first, expand browser sources, then analyze the merged batch."""
    repo = repo or _default_repository()
    fetcher = fetcher or news_mod.fetch_coin_news_for_collector
    asset = news_mod.canonical_asset_symbol(symbol)
    if not asset:
        return {"asset_symbol": "", "status": "invalid", "used_ai_budget": False}

    token = repo.claim_collection(asset, now_ms=now_ms)
    if not token:
        return {"asset_symbol": asset, "status": "skipped", "used_ai_budget": False}
    try:
        try:
            news_payload = fetcher(asset)
        except Exception as exc:
            repo.mark_collection_outcome(asset, "error", error=str(exc), now_ms=now_ms)
            if enricher is None and not browser_enrichment_enabled():
                raise NewsCollectionError(f"{asset} 뉴스 수집 실패") from exc
            news_payload = {"symbol": asset, "coin_name": asset, "items": [],
                            "sources": [{"name": "rss", "status": "error"}]}
        if enricher is not None or browser_enrichment_enabled():
            publish_initial_payload(asset, news_payload, repo=repo, now_ms=now_ms)
            if not repo.renew_collection(asset, token, now_ms=now_ms):
                return {"asset_symbol": asset, "status": "superseded", "used_ai_budget": False}
            news_payload = enrich_payload(asset, news_payload, enricher=enricher)
        return collect_payload(asset, news_payload, repo=repo, analyzer=analyzer,
                               allow_ai=allow_ai, now_ms=now_ms)
    finally:
        repo.finish_collection(asset, token, now_ms=now_ms)


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
    enricher: Callable[[str, dict], dict] | None = None,
    analyzer: Callable[..., dict] | None = None,
    max_tickers: int | None = None,
    max_ai_analyses: int | None = None,
    retention_days: int | None = None,
    bootstrap_only: bool = False,
    now_ms: int | None = None,
) -> dict:
    """Network-free-testable cycle used by CLI and non-Prefect fallbacks."""
    repo = repo or _default_repository()
    if symbols is None:
        discovered = repo.discover_tracked_symbols(
            due_only=True, bootstrap_only=bootstrap_only, now_ms=now_ms,
        )
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
    selected = list(discovered)[:ticker_limit] if symbols is None else select_ticker_window(
        discovered,
        limit=ticker_limit,
        now_ms=now_ms,
    )
    ai_limit = max_ai_analyses
    if ai_limit is None:
        ai_limit = max(
            0,
            int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "2")),
        )

    results: list[dict] = []
    ai_used = 0
    deadline = time.monotonic() + max(10, int(os.environ.get("POSITION_NEWS_MAX_CYCLE_SECONDS", "60")))
    pending = []
    leases = {}
    fetcher = fetcher or news_mod.fetch_coin_news_for_collector
    expand = not bootstrap_only and (enricher is not None or browser_enrichment_enabled())
    try:
        # Publish all new ticker RSS snapshots before the first slow browser
        # task. One user's broad crawl must not hide another user's first news.
        for index, asset in enumerate(selected):
            if time.monotonic() >= deadline:
                results.extend({"asset_symbol": skipped, "status": "skipped",
                                "reason": "cycle_deadline", "used_ai_budget": False}
                               for skipped in selected[index:])
                break
            token = repo.claim_collection(asset, now_ms=now_ms)
            if not token:
                results.append({"asset_symbol": asset, "status": "skipped", "used_ai_budget": False})
                continue
            leases[asset] = token
            try:
                try:
                    payload = fetcher(asset)
                except Exception:
                    if not expand:
                        raise
                    payload = {"symbol": asset, "coin_name": asset, "items": [],
                               "sources": [{"name": "rss", "status": "error"}]}
                if bootstrap_only:
                    initial = collect_payload(asset, payload, repo=repo, allow_ai=False,
                                              localize=False, now_ms=now_ms)
                    results.append({**initial, "collection_stage": "rss_bootstrap"})
                    repo.finish_collection(asset, token, now_ms=now_ms, next_delay_seconds=0)
                    leases.pop(asset)
                else:
                    initial = publish_initial_payload(asset, payload, repo=repo, now_ms=now_ms) if expand else None
                    pending.append((asset, payload, initial))
            except Exception as exc:
                repo.mark_collection_outcome(asset, "error", error=str(exc), now_ms=now_ms)
                results.append({"asset_symbol": asset, "status": "error",
                                "error": f"{asset} 뉴스 수집 실패", "used_ai_budget": False})
                repo.finish_collection(asset, token, now_ms=now_ms)
                leases.pop(asset)

        for asset, payload, initial in pending:
            if time.monotonic() >= deadline:
                results.append({**(initial or {"asset_symbol": asset, "status": "skipped",
                                               "used_ai_budget": False}),
                                "reason": "cycle_deadline", "browser_status": "deferred"})
                repo.finish_collection(asset, leases.pop(asset), now_ms=now_ms, next_delay_seconds=60)
                continue
            if not repo.renew_collection(asset, leases[asset], now_ms=now_ms):
                results.append({"asset_symbol": asset, "status": "superseded", "used_ai_budget": False})
                continue
            try:
                if expand:
                    payload = enrich_payload(asset, payload, enricher=enricher)
                result = collect_payload(asset, payload, repo=repo, analyzer=analyzer,
                                         allow_ai=ai_used < ai_limit, now_ms=now_ms)
                if payload.get("browser_enrichment"):
                    result["browser_status"] = payload["browser_enrichment"].get("status")
            except NewsCollectionError as exc:
                result = {"asset_symbol": asset, "status": "error", "error": str(exc),
                          "used_ai_budget": bool(os.environ.get("ANTHROPIC_API_KEY")) and ai_used < ai_limit}
            if result.get("used_ai_budget"):
                ai_used += 1
            results.append(result)
            repo.finish_collection(asset, leases.pop(asset), now_ms=now_ms)
    finally:
        for asset, token in leases.items():
            repo.finish_collection(asset, token, now_ms=now_ms)

    keep_days = retention_days
    if keep_days is None:
        keep_days = max(
            1,
            int(os.environ.get("POSITION_NEWS_RETENTION_DAYS", "30")),
        )
    removed = 0 if keep_days == 0 else repo.prune_snapshots(
        retention_days=keep_days,
        now_ms=now_ms,
    )
    return summarize_results(results, removed=removed)
