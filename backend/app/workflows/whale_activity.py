"""Independent Prefect deployment for shared public large-trade observations."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from prefect import flow, serve, task
from prefect.runtime import flow_run
from prefect.types.entrypoint import EntrypointType

from ..agent_features.whale_activity import collector, repository
from ..db import assert_shared_worker_database, init_db


class WhaleCollectionUnavailable(RuntimeError):
    pass


@task(retries=0, log_prints=True)
def discover_pairs_task():
    pairs = repository.discover_pairs()
    print(json.dumps({"event": "whale_discovery", "active_pair_count": len(pairs), "pairs": pairs}))
    return pairs


@task(retries=0, log_prints=True)
def collect_pair_task(symbol: str, market: str):
    result = collector.collect_pair(symbol, market)
    print(json.dumps({"event": "whale_source", **result}))
    return result


def _schedule_lag_seconds():
    scheduled = flow_run.scheduled_start_time
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - scheduled).total_seconds())


@flow(name="gg-parrot-whale-activity", retries=0, timeout_seconds=60, log_prints=True)
def collect_whale_activity_flow():
    config = collector.configuration()
    print(json.dumps({"configuration": config}))
    if _schedule_lag_seconds() > 120:
        return {"status": "skipped_late", "configuration": config}
    pairs = discover_pairs_task()
    results, started = [], time.monotonic()
    for pair in pairs:
        if time.monotonic() - started > 12:
            break
        results.append(collect_pair_task.submit(pair["symbol"], pair["market"]).result())
    pruned = repository.prune_inactive_states()
    summary = {"event": "whale_collection", "active_pair_count": len(pairs),
               "checked_pair_count": len(results), "failed_count": sum(row["status"] == "error" for row in results),
               "deferred_pair_count": len(pairs) - len(results), "pruned": pruned,
               "items": results, "configuration": config, "ai_calls": 0}
    print(json.dumps(summary))
    if summary["failed_count"]:
        raise WhaleCollectionUnavailable(f"공개 체결 수집 {summary['failed_count']}건 실패; 이전 관측 유지, DB 재시도 간격 적용")
    return summary


def create_deployment():
    deployment = collect_whale_activity_flow.to_deployment(
        name="shared-whale-trades", entrypoint_type=EntrypointType.MODULE_PATH,
        interval=timedelta(seconds=repository.collection_interval_seconds()), paused=False,
        version=os.environ.get("RENDER_GIT_COMMIT") or None, concurrency_limit=1,
        tags=["agents", "whale-activity", "central-collector"],
        description="실행 중인 거래쌍의 공개 대규모 체결을 공유 DB에 저장합니다. 뉴스·Playwright와 독립 실행하며 AI 호출이 없습니다.",
    )
    deployment.entrypoint = "app.workflows.whale_activity.collect_whale_activity_flow"
    return deployment


def main():
    parser = argparse.ArgumentParser(description="공유 대규모 체결 Prefect 수집기")
    parser.add_argument("mode", choices=("serve", "once"), default="serve", nargs="?")
    args = parser.parse_args()
    if args.mode == "serve" and not os.environ.get("PREFECT_API_URL"):
        raise RuntimeError("PREFECT_API_URL이 필요합니다.")
    assert_shared_worker_database()
    init_db()
    if args.mode == "once":
        collect_whale_activity_flow()
        return
    deployment = create_deployment()
    serve(deployment, limit=1, query_seconds=5, pause_on_shutdown=False)


if __name__ == "__main__":
    main()
