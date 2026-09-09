"""Fenced public holder collection; never invoked by a site request."""
from __future__ import annotations
import os
import time
from ... import whales
from . import onchain_repository as repository


def configuration():
    return {**whales.onchain_configuration(), 'shared_cache': True, 'http_collects': False,
            'check_seconds': 60, 'max_coins_per_cycle': 1,
            'version': os.environ.get('RENDER_GIT_COMMIT', 'local')}


def collect_coin(coin, *, fetcher=None):
    token = repository.claim_collection(coin)
    if not token:
        return {'coin': coin, 'status': 'skipped', 'reason': 'not_due_or_claimed'}
    started = time.monotonic()
    try:
        payload = (fetcher or whales.fetch_holder_observation)(coin)
    except whales.OnchainSourceError as exc:
        stored = repository.record_failure(coin, token, error_code=exc.code,
                                            delay_seconds=exc.retry_after_seconds)
        return {'coin': coin, 'status': 'error' if stored else 'superseded',
                'error_code': exc.code, 'http_status': exc.http_status,
                'retry_after_seconds': exc.retry_after_seconds,
                'elapsed_ms': round((time.monotonic()-started)*1000)}
    except Exception:
        stored = repository.record_failure(coin, token, error_code='invalid_response')
        return {'coin': coin, 'status': 'error' if stored else 'superseded', 'error_code': 'invalid_response'}
    # Storage failures propagate to Prefect instead of masquerading as HTTP success.
    try:
        stored = repository.store_result(coin, token, payload)
    except ValueError:
        stored = repository.record_failure(coin, token, error_code='invalid_response')
        return {'coin': coin, 'status': 'error' if stored else 'superseded', 'error_code': 'invalid_response'}
    return {'coin': coin, 'source': payload.get('source'), 'status': 'ready' if stored else 'superseded',
            'tracked_count': payload.get('tracked_count', 0), 'excluded_count': payload.get('excluded_count', 0),
            'fetched_count': payload.get('fetched_count', 0), 'http_status': payload.get('http_status'),
            'observed_at': payload.get('observed_at'), 'elapsed_ms': round((time.monotonic()-started)*1000)}
