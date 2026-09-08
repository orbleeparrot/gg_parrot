"""Prefect deployment for the shared ticker news collector."""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from prefect import flow, get_run_logger, serve, task
from prefect.exceptions import MissingContextError
from prefect.runtime import flow_run
from prefect.types.entrypoint import EntrypointType

from .. import coindesk_api, news as news_mod
from ..agent_features.position_news import collector, repository
from ..db import init_db

_FLOW_TIMEOUT_SECONDS = max(
    90,
    int(os.environ.get("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")) + 30,
)


class NewsSourceCircuitOpen(RuntimeError):
    pass


class BrowserEnrichmentUnavailable(RuntimeError):
    """Report an unavailable required source or an explicit browser probe."""

    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


def _schedule_lag_seconds() -> float:
    scheduled = flow_run.scheduled_start_time
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - scheduled).total_seconds())



@task(
    retries=2,
    retry_delay_seconds=[5, 15],
    log_prints=True,
)
def fetch_ticker_news_task(asset_symbol: str) -> dict:
    """Read shared RSS/API caches; paid model calls are a separate task."""
    payload = news_mod.fetch_coin_news_for_collector(asset_symbol)
    for source in payload.get("sources") or []:
        print(json.dumps({"event": "news_source", "asset_symbol": asset_symbol, **source},
                         ensure_ascii=False))
    return payload


@task(retries=0, log_prints=True)
def publish_initial_news_task(asset_symbol: str, news_payload: dict) -> dict:
    """Expose RSS headlines before browsers and paid model calls start."""
    result = collector.publish_initial_payload(asset_symbol, news_payload)
    print(json.dumps(result, ensure_ascii=False))
    return result


def _log_browser_sources(sources: list[dict], *, asset_symbol: str | None = None) -> None:
    # Keep each public page independently searchable in Prefect. A queued page
    # that never navigated must remain distinguishable from a publisher error.
    source_fields = (
        "name", "source_type", "source_page", "scope", "search_term",
        "status", "fetched_count", "item_count", "excluded_age_or_date_count",
        "cached", "attempted", "http_status", "response_url", "response_headers",
        "error", "phase", "message", "retry_at", "queue_ms", "elapsed_ms", "startup_ms", "batch_elapsed_ms",
        "timings_ms", "pagination", "publisher", "replaced_by",
    )
    for source in sources:
        if not str(source.get("source_type") or "").endswith("_playwright"):
            continue
        print(json.dumps({"event": "browser_source", "asset_symbol": asset_symbol,
                          **{key: source[key] for key in source_fields if key in source}},
                         ensure_ascii=False))


def _browser_unavailable(payload: dict) -> bool:
    browser = payload.get("browser_enrichment") or {}
    return browser.get("status") != "disabled" and (
        browser.get("status") == "error"
        or (int(browser.get("source_count") or 0) > 0
            and int(browser.get("successful_sources") or 0) == 0)
    )


def _has_usable_primary_result(payload: dict) -> bool:
    # A successful RSS/API response with no relevant headlines is a legitimate
    # empty result. It is different from every retrieval attempt failing.
    return bool(payload.get("items")) or any(
        source.get("status") in {"ready", "empty"}
        and not str(source.get("source_type") or "").endswith("_playwright")
        for source in payload.get("sources") or []
    )


@task(retries=0, log_prints=True)
def enrich_ticker_news_task(asset_symbol: str, news_payload: dict,
                           browser_budget_seconds: float | None = None) -> dict:
    """One bounded browser stage; source failures retain the RSS snapshot."""
    if browser_budget_seconds is None:
        result = collector.enrich_payload(asset_symbol, news_payload)
    else:
        budget = news_mod._browser_batch_budget_seconds(browser_budget_seconds)
        result = collector.enrich_payload(asset_symbol, news_payload, enricher=lambda symbol, payload:
            news_mod.enrich_coin_news_for_collector(symbol, payload, browser_budget_seconds=budget))
    print(json.dumps({"asset_symbol": asset_symbol,
                      "browser_enrichment": result.get("browser_enrichment", {}),
                      "sources": result.get("sources", [])}, ensure_ascii=False))
    _log_browser_sources(result.get("sources", []), asset_symbol=asset_symbol)
    browser = result.get("browser_enrichment") or {}
    status = browser.get("status")
    source_count = int(browser.get("source_count") or 0)
    successful = int(browser.get("successful_sources") or 0)
    unavailable = _browser_unavailable(result)
    if unavailable and not _has_usable_primary_result(result):
        raise BrowserEnrichmentUnavailable(
            f"{asset_symbol} Playwright 전체 소스 수집 실패 ({successful}/{source_count}). "
            "사용 가능한 RSS/API 결과도 없습니다.",
            result,
        )
    if unavailable or status == "partial":
        try:
            logger = get_run_logger()
        except MissingContextError:
            logger = logging.getLogger(__name__)
        logger.warning("%s Playwright 보강 소스 수집 실패: 성공 %s/%s, RSS/API 결과 유지",
                       asset_symbol, successful, source_count)
    return result


@task(retries=0, log_prints=True)
def process_ticker_news_task(
    asset_symbol: str,
    news_payload: dict,
    allow_ai: bool,
) -> dict:
    """No task retry: a model call must not repeat after a persistence error."""
    result = collector.collect_payload(
        asset_symbol,
        news_payload,
        allow_ai=allow_ai,
    )
    if news_payload.get("browser_enrichment"):
        result["browser_status"] = news_payload["browser_enrichment"].get("status")
    print(json.dumps(result, ensure_ascii=False))
    return result


@task(retries=0, log_prints=True)
def record_fetch_error_task(asset_symbol: str, error: str) -> None:
    repository.mark_collection_outcome(
        asset_symbol,
        "error",
        error=error,
    )


@task(retries=0, log_prints=True)
def discover_tickers_task() -> dict:
    selection = repository.discover_ticker_selection()
    result = {"active_symbols": selection["active"], "due_symbols": selection["due"]}
    print(json.dumps({"event": "ticker_discovery", **result,
                      "active_ticker_count": len(result["active_symbols"]),
                      "due_ticker_count": len(result["due_symbols"])}, ensure_ascii=False))
    return result


@task(retries=0, log_prints=True)
def prune_snapshots_task(retention_days: int) -> int:
    return repository.prune_snapshots(retention_days=retention_days)


def effective_config(browser_budget_seconds: float | None = None) -> dict:
    """Safe operational values visible in Prefect logs; never credentials."""
    return {
        "version": os.environ.get("RENDER_GIT_COMMIT", "local"),
        "collector_mode": "rss_api_then_playwright",
        "coindesk_api": coindesk_api.configuration(),
        "title_translation": {"daily_call_limit": None, "scope": "all_articles", "shared_cache": True},
        "news_history": {"archive_max_age_days": news_mod._news_archive_days(),
                         "recent_first": True, "historical_articles_notify": False},
        "collection_seconds": int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")),
        "schedule_seconds": max(60, int(os.environ.get("POSITION_NEWS_SCHEDULE_SECONDS", "60"))),
        "max_ai_per_run": int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "2")),
        "max_ai_per_day": int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "10")),
        "browser_enabled": collector.browser_enrichment_enabled(),
        "browser_budget_seconds": (news_mod._browser_batch_budget_seconds() if browser_budget_seconds is None
                                   else news_mod._browser_batch_budget_seconds(browser_budget_seconds)),
        "browser_page_budget_seconds": news_mod._browser_page_budget_seconds(),
        "browser_max_load_more_clicks": news_mod._browser_max_load_more_clicks(),
        "browser_concurrency": min(4, max(1, int(os.environ.get(
            "POSITION_NEWS_BROWSER_CONCURRENCY", "1" if os.environ.get("RENDER") else "3")))),
    }


@flow(name="gg-parrot-coindesk-source-probe", retries=0,
      timeout_seconds=210, log_prints=True)
def coindesk_source_probe_flow(browser_budget_seconds: float | None = None) -> dict:
    """Manually inspect eight fixed public pages using normal cache and backoff.

    No parameters accept arbitrary URLs, positions, or paid model requests.
    This flow has no schedule and shares the worker's one execution slot.
    """
    configuration = {**effective_config(browser_budget_seconds), "collector_mode": "browser_source_probe"}
    print(json.dumps({"configuration": configuration}, ensure_ascii=False))
    descriptors = [
        {"name": name, "publisher": "CoinDesk", "kind": kind, "scope": scope, "url": url}
        for name, kind, scope, _query, url in news_mod._COINDESK_DISCOVERY_SOURCES
    ]
    started = time.monotonic()
    results = (news_mod._cached_browser_pages(descriptors) if browser_budget_seconds is None else
               news_mod._cached_browser_pages(descriptors, budget_seconds=configuration["browser_budget_seconds"]))
    sources = [news_mod._browser_source_report(descriptor, results[news_mod._browser_page_key(descriptor)])
               for descriptor in descriptors]
    _log_browser_sources(sources)
    successful = sum(source["status"] in {"ready", "empty"} for source in sources)
    summary = {
        "event": "browser_source_probe", "configuration": configuration,
        "status": "ready" if successful == len(sources) else "partial" if successful else "error",
        "source_count": len(sources), "successful_sources": successful,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "sources": sources,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if successful != len(sources):
        raise BrowserEnrichmentUnavailable(
            f"CoinDesk 공개 페이지 진단: {len(sources) - successful}/{len(sources)}개 소스를 가져오지 못했습니다. "
            "소스별 HTTP 응답·대기 시간·출판사 cooldown 로그를 확인하세요.", summary)
    return summary


@flow(
    name="gg-parrot-position-news",
    retries=0,
    timeout_seconds=_FLOW_TIMEOUT_SECONDS,
    log_prints=True,
)
def collect_position_news_flow(browser_budget_seconds: float | None = None) -> dict:
    """Collect each shared ticker once within a bounded central cycle."""
    config = effective_config(browser_budget_seconds)
    print(json.dumps({"configuration": config}, ensure_ascii=False))
    collection_seconds = max(
        60,
        int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")),
    )
    max_schedule_lag = max(
        collection_seconds,
        int(os.environ.get("POSITION_NEWS_MAX_SCHEDULE_LAG_SECONDS", "600")),
    )
    schedule_lag = _schedule_lag_seconds()
    if schedule_lag > max_schedule_lag:
        summary = collector.summarize_results([])
        summary.update(run_status="skipped_late", schedule_lag_seconds=int(schedule_lag), configuration=config)
        print(json.dumps(summary, ensure_ascii=False))
        return summary
    max_tickers = max(
        1,
        int(os.environ.get("POSITION_NEWS_MAX_TICKERS_PER_RUN", "100")),
    )
    max_ai = max(
        0,
        int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "2")),
    )
    retention_days = max(
        1,
        int(os.environ.get("POSITION_NEWS_RETENTION_DAYS", "30")),
    )
    max_cycle_seconds = max(
        60,
        int(os.environ.get("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")),
    )
    max_fetch_failures = max(
        1,
        int(os.environ.get(
            "POSITION_NEWS_MAX_CONSECUTIVE_FETCH_FAILURES",
            "3",
        )),
    )

    selection = discover_tickers_task()
    symbols = list(selection["due_symbols"])[:max_tickers]
    started = time.monotonic()
    results: list[dict] = []
    ai_used = 0
    consecutive_fetch_failures = 0
    source_circuit_open = False
    browser_failures = []
    source_failures = []
    expand = collector.browser_enrichment_enabled()
    pending = []
    leases = {}

    try:
        for index, asset_symbol in enumerate(symbols):
            if time.monotonic() - started >= max_cycle_seconds:
                results.extend({
                    "asset_symbol": skipped,
                    "status": "skipped",
                    "reason": "cycle_deadline",
                    "used_ai_budget": False,
                } for skipped in symbols[index:])
                break

            token = repository.claim_collection(asset_symbol)
            if not token:
                results.append({"asset_symbol": asset_symbol, "status": "skipped", "used_ai_budget": False})
                continue
            leases[asset_symbol] = token
            try:
                try:
                    news_payload = fetch_ticker_news_task.submit(asset_symbol).result()
                except Exception as exc:
                    record_fetch_error_task.submit(
                        asset_symbol,
                        str(exc),
                    ).result()
                    if expand:
                        # Browsers are an independent source, so an RSS outage
                        # still gets one chance to recover through public pages.
                        news_payload = {"symbol": asset_symbol, "coin_name": asset_symbol,
                                        "items": [], "sources": [{"name": "rss", "status": "error"}]}
                        pending.append((asset_symbol, news_payload, None))
                        continue
                    results.append({
                        "asset_symbol": asset_symbol,
                        "status": "error",
                        "error": f"{asset_symbol} 뉴스 수집 실패",
                        "used_ai_budget": False,
                    })
                    consecutive_fetch_failures += 1
                    if consecutive_fetch_failures >= max_fetch_failures:
                        source_circuit_open = True
                        results.extend({
                            "asset_symbol": skipped,
                            "status": "skipped",
                            "reason": "source_circuit_open",
                            "used_ai_budget": False,
                        } for skipped in symbols[index + 1:])
                        break
                    continue

                consecutive_fetch_failures = 0
                initial = publish_initial_news_task.submit(asset_symbol, news_payload).result() if expand else None
                pending.append((asset_symbol, news_payload, initial))
            finally:
                if not any(item[0] == asset_symbol for item in pending):
                    repository.finish_collection(asset_symbol, leases.pop(asset_symbol))

        for asset_symbol, news_payload, initial in pending:
            elapsed = time.monotonic() - started
            if elapsed >= max_cycle_seconds:
                results.append({**(initial or {"asset_symbol": asset_symbol, "status": "skipped",
                                               "used_ai_budget": False}),
                                "reason": "cycle_deadline", "browser_status": "deferred"})
                repository.finish_collection(asset_symbol, leases.pop(asset_symbol), next_delay_seconds=60)
                continue
            if expand and max_cycle_seconds - elapsed < config["browser_budget_seconds"] + 15:
                results.append({**(initial or {"asset_symbol": asset_symbol, "status": "skipped",
                                               "used_ai_budget": False}),
                                "reason": "browser_budget_deferred", "browser_status": "deferred"})
                repository.finish_collection(asset_symbol, leases.pop(asset_symbol), next_delay_seconds=60)
                continue
            if not repository.renew_collection(asset_symbol, leases[asset_symbol]):
                results.append({"asset_symbol": asset_symbol, "status": "superseded", "used_ai_budget": False})
                continue
            allow_ai = ai_used < max_ai
            try:
                if expand:
                    try:
                        if browser_budget_seconds is None:
                            news_payload = enrich_ticker_news_task.submit(asset_symbol, news_payload).result()
                        else:
                            news_payload = enrich_ticker_news_task.submit(
                                asset_symbol, news_payload, config["browser_budget_seconds"]).result()
                    except BrowserEnrichmentUnavailable as exc:
                        # Retain source diagnostics. A total retrieval outage
                        # must not be persisted as a successful empty snapshot.
                        news_payload = exc.payload
                    if _browser_unavailable(news_payload):
                        browser_failures.append(asset_symbol)
                if _browser_unavailable(news_payload) and not _has_usable_primary_result(news_payload):
                    source_failures.append(asset_symbol)
                    record_fetch_error_task.submit(
                        asset_symbol, "RSS/API 및 Playwright 소스 수집 실패",
                    ).result()
                    result = {"asset_symbol": asset_symbol, "status": "error", "used_ai_budget": False,
                              "browser_status": "error", "reason": "all_sources_unavailable"}
                else:
                    result = process_ticker_news_task.submit(asset_symbol, news_payload, allow_ai).result()
            except Exception as exc:
                # Paid tasks have no retries. Count an uncertain model attempt
                # conservatively if final persistence failed.
                result = {"asset_symbol": asset_symbol, "status": "error", "error": str(exc),
                          "used_ai_budget": bool(os.environ.get("ANTHROPIC_API_KEY")) and allow_ai}
            if result.get("used_ai_budget"):
                ai_used += 1
            results.append(result)
            repository.finish_collection(asset_symbol, leases.pop(asset_symbol))
    finally:
        for asset_symbol, token in leases.items():
            repository.finish_collection(asset_symbol, token)

    removed = prune_snapshots_task.submit(retention_days).result()
    summary = collector.summarize_results(results, removed=removed)
    summary["active_ticker_count"] = len(selection["active_symbols"])
    summary["due_ticker_count"] = len(selection["due_symbols"])
    summary["configuration"] = config
    summary["browser_failed_tickers"] = browser_failures
    summary["browser_failed_count"] = len(browser_failures)
    summary["source_failed_tickers"] = source_failures
    summary["source_failed_count"] = len(source_failures)
    if source_failures:
        summary["run_status"] = "source_unavailable"
    elif browser_failures:
        summary["run_status"] = "degraded"
    print(json.dumps(summary, ensure_ascii=False))
    if source_circuit_open:
        raise NewsSourceCircuitOpen(
            "Google News RSS 및 CoinDesk RSS 연속 수집 실패"
        )
    if source_failures:
        raise BrowserEnrichmentUnavailable(
            f"RSS/API 및 Playwright 전체 소스 수집 실패: {', '.join(source_failures)}. "
            "기존 스냅샷을 유지합니다. 소스별 수집 상태를 확인하세요.",
            {"summary": summary},
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="껄무새 중앙 뉴스 수집 워커")
    parser.add_argument(
        "mode",
        choices=("serve", "once"),
        nargs="?",
        default="serve",
    )
    args = parser.parse_args()

    if args.mode == "serve" and not os.environ.get("PREFECT_API_URL"):
        raise RuntimeError(
            "PREFECT_API_URL이 필요합니다. Prefect Cloud workspace API URL을 설정하세요."
        )

    # Validate the shared durable store before any schema DDL, then initialize
    # once per worker process rather than once per five-minute flow run.
    repository.assert_worker_database()
    init_db()

    if args.mode == "once":
        collect_position_news_flow()
        return

    interval_seconds = max(
        60,
        int(os.environ.get("POSITION_NEWS_SCHEDULE_SECONDS", "60")),
    )
    print(json.dumps({"configuration": effective_config()}, ensure_ascii=False))
    collection_deployment = collect_position_news_flow.to_deployment(
        name="shared-ticker-news",
        entrypoint_type=EntrypointType.MODULE_PATH,
        parameters={"browser_budget_seconds": 90},
        interval=timedelta(seconds=interval_seconds),
        paused=False,
        version=os.environ.get("RENDER_GIT_COMMIT") or None,
        concurrency_limit=1,
        tags=["agents", "position-news", "central-collector"],
        description=(
            "RSS를 먼저 공개하고 Playwright 공개 웹 탐색을 추가한 뒤 공용 DB에 분석을 저장합니다."
        ),
    )
    probe_deployment = coindesk_source_probe_flow.to_deployment(
        name="coindesk-source-probe",
        entrypoint_type=EntrypointType.MODULE_PATH,
        parameters={"browser_budget_seconds": 90},
        version=os.environ.get("RENDER_GIT_COMMIT") or None,
        concurrency_limit=1,
        tags=["agents", "position-news", "source-diagnostics"],
        description="수동 실행 전용: CoinDesk 4개 섹션과 4개 코인 태그의 HTTP·수집·더보기 진단",
    )
    # `python -m` defines the startup flows in __main__. Use their importable
    # package paths explicitly so Prefect subprocesses retain relative imports.
    collection_deployment.entrypoint = "app.workflows.position_news.collect_position_news_flow"
    probe_deployment.entrypoint = "app.workflows.position_news.coindesk_source_probe_flow"
    # During rolling deploys the old runner must not pause the new schedule.
    # One shared process slot also prevents probes competing with collection.
    serve(collection_deployment, probe_deployment, limit=1, pause_on_shutdown=False)


if __name__ == "__main__":
    main()
