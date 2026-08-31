"""Prefect deployment for the shared ticker news collector."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from prefect import flow, task
from prefect.runtime import flow_run

from .. import news as news_mod
from ..agent_features.position_news import collector, repository
from ..db import init_db

_FLOW_TIMEOUT_SECONDS = max(
    90,
    int(os.environ.get("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")) + 30,
)


class NewsSourceCircuitOpen(RuntimeError):
    pass


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
    """Retry only the idempotent RSS read, never the model call."""
    return news_mod.fetch_coin_news_for_collector(asset_symbol)


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
def discover_tickers_task() -> list[str]:
    return repository.discover_tracked_symbols()


@task(retries=0, log_prints=True)
def prune_snapshots_task(retention_days: int) -> int:
    return repository.prune_snapshots(retention_days=retention_days)


@flow(
    name="gg-parrot-position-news",
    retries=0,
    timeout_seconds=_FLOW_TIMEOUT_SECONDS,
    log_prints=True,
)
def collect_position_news_flow() -> dict:
    """Collect each shared ticker once within a bounded central cycle."""
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
        summary.update(run_status="skipped_late", schedule_lag_seconds=int(schedule_lag))
        print(json.dumps(summary, ensure_ascii=False))
        return summary
    max_tickers = max(
        1,
        int(os.environ.get("POSITION_NEWS_MAX_TICKERS_PER_RUN", "100")),
    )
    max_ai = max(
        0,
        int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "12")),
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

    symbols = list(discover_tickers_task())[:max_tickers]
    started = time.monotonic()
    results: list[dict] = []
    ai_used = 0
    consecutive_fetch_failures = 0
    source_circuit_open = False

    for index, asset_symbol in enumerate(symbols):
        if time.monotonic() - started >= max_cycle_seconds:
            results.extend({
                "asset_symbol": skipped,
                "status": "skipped",
                "reason": "cycle_deadline",
                "used_ai_budget": False,
            } for skipped in symbols[index:])
            break

        try:
            news_payload = fetch_ticker_news_task.submit(asset_symbol).result()
        except Exception as exc:
            record_fetch_error_task.submit(
                asset_symbol,
                str(exc),
            ).result()
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
        allow_ai = ai_used < max_ai
        try:
            result = process_ticker_news_task.submit(
                asset_symbol,
                news_payload,
                allow_ai,
            ).result()
        except Exception as exc:
            # The durable daily budget is authoritative. This conservative
            # cycle count prevents another ticker from spending the same slot
            # when a model call succeeded but persistence later failed.
            attempted = bool(os.environ.get("ANTHROPIC_API_KEY")) and allow_ai
            result = {
                "asset_symbol": asset_symbol,
                "status": "error",
                "error": str(exc),
                "used_ai_budget": attempted,
            }
        if result.get("used_ai_budget"):
            ai_used += 1
        results.append(result)

    removed = prune_snapshots_task.submit(retention_days).result()
    summary = collector.summarize_results(results, removed=removed)
    print(json.dumps(summary, ensure_ascii=False))
    if source_circuit_open:
        raise NewsSourceCircuitOpen(
            "Google News RSS 및 CoinDesk RSS 연속 수집 실패"
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
        int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")),
    )
    collect_position_news_flow.serve(
        name="shared-ticker-news",
        interval=timedelta(seconds=interval_seconds),
        pause_on_shutdown=False,
        limit=1,
        global_limit=1,
        tags=["agents", "position-news", "central-collector"],
        description=(
            "활성 매크로 티커의 뉴스를 한 번 수집·분석해 공용 DB에 저장합니다."
        ),
    )


if __name__ == "__main__":
    main()
