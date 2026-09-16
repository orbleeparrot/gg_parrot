"""Pure central collection use-case; Prefect is only an orchestration adapter."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
import time
from copy import deepcopy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from ... import news as news_mod
from ...ai_runtime import ai_available, default_model
from . import classifier


class NewsCollectionError(RuntimeError):
    pass


def _localize_collected_payload(payload: dict, repo, now_ms=None, *, on_progress=None) -> dict:
    """Translate every title through the public shared cache, retaining raw work.

    Display responses omit unfinished translations. Collector storage keeps the
    original items so a temporary provider failure cannot lose an article.
    """
    items = [dict(item) for item in payload.get("items") or []]
    def translated(values):
        ready = []
        for item in items:
            original = item.get("original_title") or item.get("title")
            if original in values:
                ready.append({**item, "original_title": original, "title": values[original]})
        if ready and on_progress:
            on_progress({**payload, "items": ready})
    try:
        localized = _call_with_progress(news_mod._localize_coin_news_items, items,
                                        on_progress=translated if on_progress else None)
    except Exception as exc:
        localized = []
        logging.getLogger(__name__).warning("Ticker title translation pending (%s); retaining raw articles for retry",
                                           type(exc).__name__)
    ready = {_article_identity(item): item for item in localized}
    merged = [{**item, **ready.get(_article_identity(item), {})} for item in items]
    if on_progress:
        on_progress({**payload, "items": merged})
    metadata = {}
    if any(classifier.is_community_item(item) for item in merged):
        try:
            from ... import community_summaries
            enriched, metadata = community_summaries.enrich_items(merged, wait=True)
            by_identity = {_article_identity(item): item for item in enriched}
            merged = [{**item, **by_identity.get(_article_identity(item), {})} for item in merged]
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Community body summaries pending (%s); retaining raw posts for retry", type(exc).__name__)
    result = {**payload, "items": merged,
            "translation": {"status": "partial" if len(localized) < len(items) else "ready",
                            "pending_count": max(0, len(items) - len(localized)),
                            "retry_after_seconds": 30}}
    result["community_summaries"] = community_progress({"items": merged, "community_summaries": metadata})["community_summaries"]
    return result


def _call_with_progress(function, *args, on_progress=None, **kwargs):
    """Keep injected source adapters compatible with the optional callback."""
    parameters = inspect.signature(function).parameters.values()
    if on_progress is not None and any(parameter.name == "on_progress" or
                                       parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        kwargs["on_progress"] = on_progress
    return function(*args, **kwargs)


def _publish_articles(asset, payload, repo, *, analysis=None, on_publish=None, now_ms=None, enrichment_only=False):
    writer = getattr(repo, "publish_articles", None)
    if writer:
        writer(asset, payload, analysis=analysis, now_ms=now_ms, enrichment_only=enrichment_only)
    if on_publish:
        on_publish(deepcopy(payload))


class ArticlePublisher:
    """Persist source completions immediately; translate on a bounded worker."""
    def __init__(self, asset, repo, *, on_publish=None, now_ms=None, executor=None):
        self.asset, self.repo = asset, repo
        self.on_publish, self.now_ms = on_publish, now_ms
        self.seen = set()
        self.executor = executor
        self.owns_executor = executor is None
        self.futures = []
        self.latest = {}
        self.lock = threading.RLock()
        self.closed = False

    def prepared(self, payload, *, enrichment_only=False):
        from .articles import _merge_item
        with self.lock:
            items = {_article_identity(item): item for item in self.latest.get("items") or []}
            for item in payload.get("items") or []:
                key = _article_identity(item)
                items[key] = _merge_item(items.get(key, {}), item)
            self.latest = {**self.latest, **payload, "items": list(items.values())}
            _publish_articles(self.asset, self.latest, self.repo, on_publish=self.on_publish, now_ms=self.now_ms,
                               enrichment_only=enrichment_only)

    def __call__(self, payload):
        self.prepared(payload)
        if not getattr(self.repo, "publish_articles", None):
            return
        with self.lock:
            if self.closed:
                return
            fresh = []
            for item in payload.get("items") or []:
                key = (_article_identity(item), str(item.get("community_body_hash") or ""))
                if key not in self.seen:
                    self.seen.add(key)
                    fresh.append(item)
            if not fresh:
                return
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="news-article-enrich")
            def work():
                result = _localize_collected_payload({**payload, "items": fresh}, self.repo, self.now_ms,
                    on_progress=lambda value: self.prepared(value, enrichment_only=True))
                self.prepared(result, enrichment_only=True)
            self.futures.append(self.executor.submit(work))

    def finish(self):
        with self.lock:
            self.closed = True
            futures = list(self.futures)
        try:
            for future in futures:
                try:
                    future.result()
                except Exception:
                    logging.getLogger(__name__).exception("Article enrichment will retry for %s", self.asset)
        finally:
            if self.owns_executor and self.executor is not None:
                self.executor.shutdown(wait=True)


def retry_article_enrichment(*, repo=None, now_ms=None):
    """보강(제목 번역·커뮤니티 요약)이 끝나지 않은 기사를 다시 시도한다 — 5초마다 불린다.

    egress 를 지키는 세 겹: ① 재시도 시각이 된 행이 없으면 EXISTS 한 번으로 끝난다. ② 직전 회차에
    아무 진전이 없었으면(공급자 막힘) 루프 전체가 쉬고, 쉼이 끝나면 작은 탐침만 보낸다 — 탐침이 성공하면
    바로 전체 재시도로 돌아가므로 복구를 늦게 알아채지 않는다. ③ 행마다 30초 → 2분 간격(더 미루지 않는다).
    """
    repo = repo or _default_repository()
    has_pending = getattr(repo, "has_pending_articles", None)
    if has_pending is not None and not has_pending(now_ms=now_ms):
        return
    stall = getattr(repo, "enrichment_stall", None)
    state = stall(now_ms=now_ms) if stall is not None else {"stalled": False, "probing": False}
    if state["stalled"]:
        return
    from .articles import ENRICHMENT_PROBE_LIMIT
    batches = repo.pending_article_batches(now_ms=now_ms, limit=ENRICHMENT_PROBE_LIMIT if state["probing"] else 50)
    snapshot = getattr(repo, "enrichment_snapshot", None)
    settle = getattr(repo, "settle_enrichment_batch", None)
    progressed_total = 0
    for asset, items in batches.items():
        if not repo.claim_article_enrichment(asset, now_ms=now_ms):
            continue
        ids = [item.get("id") or _article_identity_key(item) for item in items]
        before = snapshot(asset, ids) if snapshot is not None else {}
        publish = lambda payload: _publish_articles(asset, payload, repo, now_ms=now_ms, enrichment_only=True)
        payload = {"symbol": asset, "items": items}
        try:
            result = _localize_collected_payload(payload, repo, now_ms, on_progress=publish)
            publish(result)
        finally:
            if settle is not None and before:
                # 진전한 행은 백오프를 지우고, 남은 행은 30초~2분 뒤로 미룬다(같은 배치에 진전이 있을 때만 실패로 센다).
                progressed_total += settle(asset, before, now_ms=now_ms)["progressed"]
        if state["probing"] and progressed_total:
            break  # 탐침 성공 — 다음 스캔(5초 뒤)부터 전체 배치로 돌아간다
    record = getattr(repo, "record_enrichment_pass", None)
    if record is not None:
        record(progressed_total > 0, now_ms=now_ms)


def community_progress(payload: dict) -> dict:
    """Safe counters for collector/Prefect logs; never include raw post bodies."""
    posts = [item for item in payload.get("items") or [] if classifier.is_community_item(item)]
    bodies = Counter(str(item.get("community_body_status") or
                         ("ready" if item.get("community_body") else "missing")) for item in posts)
    summaries = Counter(str(item.get("community_summary_status") or
                            ("pending" if item.get("community_body") else "unavailable")) for item in posts)
    return {"community_bodies": {status: bodies[status] for status in ("ready", "missing", "error")},
            "community_summaries": {
                "status": "partial" if summaries["pending"] else "ready",
                "ready_count": summaries["ready"], "pending_count": summaries["pending"],
                "unavailable_count": summaries["unavailable"], "retry_after_seconds": 30,
                **dict(payload.get("community_summaries") or {})}}


def _article_identity_key(item: dict) -> str:
    from .articles import article_id
    return article_id(item)


def _article_identity(item: dict) -> tuple[str, str, str]:
    return (str(item.get("original_title") or item.get("title") or ""),
            str(item.get("source") or ""), str(item.get("url") or ""))


def _analysis_model() -> str:
    return default_model()


def analysis_fingerprint(asset_symbol: str, items: list[dict]) -> str:
    """Stable shared-work key; user, session and position are intentionally absent."""
    normalized_items = sorted(
        (
            {
                "title": str(item.get("original_title") or item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "excerpt": str(item.get("excerpt") or "").strip(),
                **({"content_type": "community",
                    "community_post_id": str(item.get("community_post_id") or ""),
                    "url": str(item.get("url") or "").strip(),
                    **({"community_body_hash": str(item["community_body_hash"]).strip()}
                       if str(item.get("community_body_hash") or "").strip() else {})}
                   if classifier.is_community_item(item) else {}),
            }
            for item in items
        ),
        key=lambda item: (item["title"].casefold(), item["source"].casefold(),
                          item.get("content_type", ""), item.get("community_post_id", ""), item.get("url", ""),
                          item.get("community_body_hash", "")),
    )
    material = {
        "asset_symbol": news_mod.canonical_asset_symbol(asset_symbol),
        "prompt_version": classifier.PROMPT_VERSION,
        "model": _analysis_model(),
        "ai_enabled": ai_available(),
        "items": normalized_items,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reuse_editorial_analysis(asset: str, items: list[dict], snapshot_key: str,
                             coin_name: str, repo) -> dict | None:
    """Community changes create a new snapshot without rebuying unchanged news analysis."""
    reader = getattr(repo, "get_latest_snapshot", None)
    if reader is None:
        return None
    previous = reader(asset)
    if not previous or previous.get("snapshot_id") == snapshot_key:
        return None  # Preserve retries of this snapshot's incomplete analysis.
    old_items = list((previous.get("news_payload") or {}).get("items") or [])
    if not any(classifier.is_community_item(item) for item in [*items, *old_items]):
        return None
    # Recompute the old full key to reject reuse after a model/prompt change.
    if previous.get("snapshot_id") != analysis_fingerprint(asset, old_items):
        return None
    editorial = [item for item in items if not classifier.is_community_item(item)]
    old_editorial = [item for item in old_items if not classifier.is_community_item(item)]
    if not editorial or analysis_fingerprint(asset, editorial) != analysis_fingerprint(asset, old_editorial):
        return None
    previous_analysis = previous.get("analysis") or {}
    assessed = previous_analysis.get("items") or []
    if (previous_analysis.get("analysis_status") not in {"ready", "degraded", "rate_limited"}
            or len(assessed) != len(old_items)):
        return None
    by_identity = {}
    for item, assessment in zip(old_items, assessed):
        if classifier.is_community_item(item):
            continue
        identity = _article_identity(item)
        if identity in by_identity or not isinstance(assessment, dict):
            return None
        if assessment.get("sentiment") not in classifier.SENTIMENTS:
            return None
        by_identity[identity] = assessment
    if any(_article_identity(item) not in by_identity for item in editorial):
        return None
    result = deepcopy(previous_analysis)
    result["items"] = [classifier.community_analysis() if classifier.is_community_item(item)
                       else deepcopy(by_identity[_article_identity(item)]) for item in items]
    result["overview"] = classifier._fallback_overview(items, coin_name)
    return result


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
    on_publish=None,
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
    # A raw article is durable before any translation or model dependency.
    _publish_articles(asset, news_payload, repo, on_publish=on_publish, now_ms=now_ms)
    # Translation has its own shared cache and retry lifecycle. Reused analysis
    # must not prevent an earlier unfinished translation from being retried.
    if localize:
        news_payload = _call_with_progress(_localize_collected_payload, news_payload, repo, now_ms,
            on_progress=lambda payload: _publish_articles(asset, payload, repo,
                                                          on_publish=on_publish, now_ms=now_ms, enrichment_only=True))
        _publish_articles(asset, news_payload, repo, on_publish=on_publish, now_ms=now_ms, enrichment_only=True)
    progress = community_progress(news_payload)
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
            **progress,
        }

    claimed_payload = getattr(claim, "news_payload", None) or news_payload
    if localize:
        translated = {_article_identity(item): item for item in news_payload.get("items") or []}
        claimed_payload = {**claimed_payload,
            "items": [translated.get(_article_identity(item), item) for item in claimed_payload.get("items") or []],
            "translation": dict(news_payload.get("translation") or {}),
            "community_summaries": dict(news_payload.get("community_summaries") or {})}
    claimed_items = list(claimed_payload.get("items") or [])
    coin_name = str(claimed_payload.get("coin_name") or asset)
    reused_analysis = _reuse_editorial_analysis(asset, claimed_items, snapshot_key, coin_name, repo)
    has_editorial = any(not classifier.is_community_item(item) for item in claimed_items)
    wants_ai = ai_available() and allow_ai and has_editorial and reused_analysis is None
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
            **progress,
        }

    try:
        # Publish headlines before any paid/slow enrichment. The same claim
        # stays fenced until final analysis, so reads can already show articles.
        if wants_ai and not claim.had_usable_analysis:
            baseline = classifier.analyze_headlines(claimed_items, coin_name, allow_ai=False)
            baseline_saved = repo.complete_snapshot(claim.snapshot_id, baseline,
                claim_token=claim.claim_token, keep_claim=True, now_ms=now_ms)
            if baseline_saved is not False:
                _publish_articles(asset, claimed_payload, repo, analysis=baseline,
                                   on_publish=on_publish, now_ms=now_ms)
        analysis = reused_analysis if reused_analysis is not None else analyzer(
            claimed_items,
            coin_name,
            allow_ai=reserved_ai,
        )
        completed = repo.complete_snapshot(
            claim.snapshot_id,
            analysis,
            claim_token=claim.claim_token,
            news_payload=claimed_payload,
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
            **progress,
        }
    _publish_articles(asset, claimed_payload, repo, analysis=analysis,
                       on_publish=on_publish, now_ms=now_ms, enrichment_only=True)
    return {
        "asset_symbol": asset,
        "snapshot_key": snapshot_key,
        "status": "stored",
        "analysis_status": analysis.get("analysis_status"),
        "editorial_analysis_reused": reused_analysis is not None,
        "used_ai_budget": reserved_ai,
        **progress,
    }


def browser_enrichment_enabled() -> bool:
    return os.environ.get("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true").lower() not in {
        "0", "false", "no",
    }


def publish_initial_payload(symbol: str, payload: dict, *, repo=None, now_ms=None, on_publish=None) -> dict:
    """Make the first headlines visible without a browser or a paid API call."""
    repo = repo or _default_repository()
    stored = repo.get_latest_snapshot(symbol)
    if stored:
        _publish_articles(symbol, stored.get("news_payload") or {}, repo,
                           analysis=stored.get("analysis"), now_ms=now_ms)
    _publish_articles(symbol, payload, repo, on_publish=on_publish, now_ms=now_ms)
    usable = [item for item in (stored or {}).get("news_payload", {}).get("items", [])
              if news_mod._within_coin_news_window(item) and news_mod._is_news_article_candidate(
                  {**item, "title": item.get("original_title") or item.get("title")})]
    if stored and usable and not news_mod._coin_snapshot_is_stale(stored):
        incoming_community = [item for item in payload.get("items") or []
                              if classifier.is_community_item(item)]
        incoming_ids = {(str(item.get("community_post_id") or item.get("url") or ""),
                         str(item.get("community_body_hash") or ""))
                        for item in incoming_community}
        stored_ids = {(str(item.get("community_post_id") or item.get("url") or ""),
                       str(item.get("community_body_hash") or ""))
                      for item in stored.get("news_payload", {}).get("items", [])
                      if classifier.is_community_item(item)}
        if incoming_ids and incoming_ids != stored_ids:
            # Fast community updates must not wait for the browser. Preserve the
            # existing editorial set here so its paid analysis remains reusable;
            # the final enrichment pass still publishes newly discovered news.
            initial = {**stored.get("news_payload", {}), **payload,
                       "items": news_mod._sort_news_items_newest_first(
                           [item for item in usable if not classifier.is_community_item(item)]
                           + incoming_community)}
            initial["community_summaries"] = community_progress({"items": initial["items"]})["community_summaries"]
            return collect_payload(symbol, initial, repo=repo, allow_ai=False,
                                   localize=False, now_ms=now_ms)
        previous = {_article_identity(item): item for item in usable}
        incoming = {_article_identity(item): item for item in payload.get("items") or []}
        if any(key not in previous for key in incoming):
            from .articles import _merge_item
            merged = dict(previous)
            merged.update({key: _merge_item(previous.get(key, {}), item) for key, item in incoming.items()})
            initial = {**payload, "items": news_mod._sort_news_items_newest_first(list(merged.values()))}
            assessments = {_article_identity(item): assessment for item, assessment in zip(
                stored.get("news_payload", {}).get("items", []), stored.get("analysis", {}).get("items", []))}
            def retain_analysis(items, coin_name, **_kwargs):
                baseline = classifier.analyze_headlines(items, coin_name, allow_ai=False)
                baseline["items"] = [assessments.get(_article_identity(item), assessed)
                                     for item, assessed in zip(items, baseline["items"])]
                return baseline
            return collect_payload(symbol, initial, repo=repo, analyzer=retain_analysis,
                                   allow_ai=False, localize=False, now_ms=now_ms, on_publish=on_publish)
        # Keep the richer snapshot when this discovery added no articles.
        return {"asset_symbol": symbol, "status": "reused", "used_ai_budget": False,
                **community_progress(stored.get("news_payload") or {})}
    return collect_payload(symbol, payload, repo=repo, allow_ai=False,
                           localize=False, now_ms=now_ms, on_publish=on_publish)


def enrich_payload(symbol: str, payload: dict, *, enricher=None, on_progress=None) -> dict:
    """A browser outage must never remove already collected RSS articles."""
    if enricher is None and not browser_enrichment_enabled():
        return payload
    enricher = enricher or news_mod.enrich_coin_news_for_collector
    try:
        return _call_with_progress(enricher, symbol, payload, on_progress=on_progress)
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
    on_publish=None,
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
    publisher = ArticlePublisher(asset, repo, on_publish=on_publish, now_ms=now_ms)
    try:
        try:
            news_payload = _call_with_progress(fetcher, asset, on_progress=publisher)
            publisher(news_payload)
        except Exception as exc:
            repo.mark_collection_outcome(asset, "error", error=str(exc), now_ms=now_ms)
            if enricher is None and not browser_enrichment_enabled():
                raise NewsCollectionError(f"{asset} 뉴스 수집 실패") from exc
            news_payload = {"symbol": asset, "coin_name": asset, "items": [],
                            "sources": [{"name": "rss", "status": "error"}]}
        if enricher is not None or browser_enrichment_enabled():
            publish_initial_payload(asset, news_payload, repo=repo, now_ms=now_ms, on_publish=on_publish)
            if not repo.renew_collection(asset, token, now_ms=now_ms):
                return {"asset_symbol": asset, "status": "superseded", "used_ai_budget": False}
            news_payload = enrich_payload(asset, news_payload, enricher=enricher, on_progress=publisher)
            publisher(news_payload)
        publisher.finish()
        return collect_payload(asset, news_payload, repo=repo, analyzer=analyzer,
                               allow_ai=allow_ai, now_ms=now_ms, on_publish=on_publish)
    finally:
        publisher.finish()
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
        "community_bodies": {status: sum(int((item.get("community_bodies") or {}).get(status) or 0)
                                           for item in results) for status in ("ready", "missing", "error")},
        "community_summaries": {key: sum(int((item.get("community_summaries") or {}).get(key) or 0)
                                          for item in results)
                                for key in ("ready_count", "pending_count", "unavailable_count")},
        "items": results,
    }


def prune_community_summaries(*, now_ms=None) -> int:
    """Bounded worker maintenance, never part of an HTTP read or summary claim."""
    from ... import community_summary_repository
    return community_summary_repository.prune_summaries(retention_days=30, limit=500, now_ms=now_ms)


def run_maintenance(*, retention_days=30, repo=None, now_ms=None):
    repo = repo or _default_repository()
    claim = getattr(repo, "claim_maintenance", None)
    if claim and not claim(now_ms=now_ms):
        return {"snapshots": 0, "community_summaries": 0}
    prune_articles = getattr(repo, "prune_articles", None)
    if prune_articles:
        prune_articles(retention_days=retention_days, now_ms=now_ms)
    return {"snapshots": repo.prune_snapshots(retention_days=retention_days, now_ms=now_ms),
            "community_summaries": prune_community_summaries(now_ms=now_ms)}


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
    publishers = {}
    enrichment_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="news-cycle-enrich")
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
            publisher = ArticlePublisher(asset, repo, now_ms=now_ms, executor=enrichment_pool)
            publishers[asset] = publisher
            try:
                try:
                    progress = publisher
                    payload = _call_with_progress(fetcher, asset, on_progress=progress)
                    progress(payload)
                except Exception:
                    if not expand:
                        raise
                    payload = {"symbol": asset, "coin_name": asset, "items": [],
                               "sources": [{"name": "rss", "status": "error"}]}
                if bootstrap_only:
                    initial = collect_payload(asset, payload, repo=repo, allow_ai=False,
                                              localize=False, now_ms=now_ms)
                    results.append({**initial, "collection_stage": "rss_bootstrap"})
                    publisher.finish()
                    repo.finish_collection(asset, token, now_ms=now_ms, next_delay_seconds=0)
                    leases.pop(asset)
                else:
                    initial = publish_initial_payload(asset, payload, repo=repo, now_ms=now_ms) if expand else None
                    pending.append((asset, payload, initial))
            except Exception as exc:
                repo.mark_collection_outcome(asset, "error", error=str(exc), now_ms=now_ms)
                results.append({"asset_symbol": asset, "status": "error",
                                "error": f"{asset} 뉴스 수집 실패", "used_ai_budget": False})
                repo.finish_collection(asset, token, now_ms=now_ms,
                                       next_delay_seconds=0 if bootstrap_only else None)
                leases.pop(asset)

        for asset, payload, initial in pending:
            publisher = publishers[asset]
            if time.monotonic() >= deadline:
                results.append({**(initial or {"asset_symbol": asset, "status": "skipped",
                                               "used_ai_budget": False}),
                                "reason": "cycle_deadline", "browser_status": "deferred"})
                publisher.finish()
                repo.finish_collection(asset, leases.pop(asset), now_ms=now_ms, next_delay_seconds=60)
                continue
            if not repo.renew_collection(asset, leases[asset], now_ms=now_ms):
                results.append({"asset_symbol": asset, "status": "superseded", "used_ai_budget": False})
                continue
            try:
                if expand:
                    payload = enrich_payload(asset, payload, enricher=enricher, on_progress=publisher)
                    publisher(payload)
                publisher.finish()
                result = collect_payload(asset, payload, repo=repo, analyzer=analyzer,
                                         allow_ai=ai_used < ai_limit, now_ms=now_ms)
                if payload.get("browser_enrichment"):
                    result["browser_status"] = payload["browser_enrichment"].get("status")
            except NewsCollectionError as exc:
                result = {"asset_symbol": asset, "status": "error", "error": str(exc),
                          "used_ai_budget": ai_available() and ai_used < ai_limit}
            if result.get("used_ai_budget"):
                ai_used += 1
            results.append(result)
            repo.finish_collection(asset, leases.pop(asset), now_ms=now_ms)
    finally:
        for publisher in publishers.values():
            publisher.finish()
        enrichment_pool.shutdown(wait=True)
        for asset, token in leases.items():
            repo.finish_collection(asset, token, now_ms=now_ms)

    keep_days = retention_days
    if keep_days is None:
        keep_days = max(
            1,
            int(os.environ.get("POSITION_NEWS_RETENTION_DAYS", "30")),
        )
    maintenance = {"snapshots": 0, "community_summaries": 0} if keep_days == 0 else run_maintenance(
        retention_days=keep_days, repo=repo, now_ms=now_ms)
    removed = maintenance["snapshots"]
    summary = summarize_results(results, removed=removed)
    summary["community_summaries_pruned"] = maintenance["community_summaries"]
    return summary
