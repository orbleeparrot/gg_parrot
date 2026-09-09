"""Prefect collector for shared Blockscout and XRPScan holder snapshots."""
from __future__ import annotations
import argparse
from datetime import timedelta
import json
import os
from prefect import flow, serve, task
from prefect.types.entrypoint import EntrypointType
from ..agent_features.whale_activity import onchain_collector as collector
from ..agent_features.whale_activity import onchain_repository as repository
from ..db import assert_shared_worker_database, init_db
from .whale_activity import _schedule_lag_seconds


class OnchainCollectionUnavailable(RuntimeError):
    pass


@task(retries=0, log_prints=True)
def discover_coins_task():
    coins = repository.discover_coins()
    print(json.dumps({'event': 'onchain_discovery', 'due_coins': coins}))
    return coins


@task(retries=0, log_prints=True, persist_result=False)
def collect_coin_task(coin: str):
    result = collector.collect_coin(coin)
    print(json.dumps({'event': 'onchain_source', **result}))
    return result


@flow(name='gg-parrot-onchain-holders', retries=0, timeout_seconds=40, log_prints=True)
def collect_onchain_holders_flow():
    config = collector.configuration()
    print(json.dumps({'configuration': config}))
    if _schedule_lag_seconds() > 120:
        return {'status': 'skipped_late', 'configuration': config}
    coins = discover_coins_task()
    # One bounded request per minute leaves the shared whale runner available
    # for 30-second exchange observations. Durable oldest-first ordering is fair.
    results = [collect_coin_task(coins[0])] if coins else []
    summary = {'event': 'onchain_collection', 'due_coin_count': len(coins),
               'checked_coin_count': len(results), 'deferred_coin_count': max(0, len(coins)-len(results)),
               'failed_count': sum(row['status'] == 'error' for row in results),
               'items': results, 'configuration': config, 'ai_calls': 0}
    print(json.dumps(summary))
    if summary['failed_count']:
        raise OnchainCollectionUnavailable('온체인 잔고 수집 실패; 이전 관측 유지, DB 재시도 간격 적용')
    return summary


def create_deployment():
    deployment = collect_onchain_holders_flow.to_deployment(
        name='shared-onchain-holders', entrypoint_type=EntrypointType.MODULE_PATH,
        interval=timedelta(seconds=60), paused=False, concurrency_limit=1,
        version=os.environ.get('RENDER_GIT_COMMIT') or None,
        tags=['agents', 'whale-activity', 'onchain-holders', 'central-collector'],
        description='Blockscout PEPE·WETH 10분, XRPScan 6시간 간격의 공용 잔고 관측. 최초 기준·변화 없음은 알림 제외. AI 호출 없음.',
    )
    deployment.entrypoint = 'app.workflows.onchain_holders.collect_onchain_holders_flow'
    return deployment


def main():
    parser = argparse.ArgumentParser(description='공유 온체인 잔고 Prefect 수집기')
    parser.add_argument('mode', choices=('serve', 'once'), default='serve', nargs='?')
    args = parser.parse_args()
    if args.mode == 'serve' and not os.environ.get('PREFECT_API_URL'):
        raise RuntimeError('PREFECT_API_URL이 필요합니다.')
    assert_shared_worker_database()
    init_db()
    if args.mode == 'once':
        collect_onchain_holders_flow()
        return
    serve(create_deployment(), limit=1, query_seconds=5, pause_on_shutdown=False)


if __name__ == '__main__':
    main()
