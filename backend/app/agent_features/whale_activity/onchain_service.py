"""Read-only, short-cached projection of shared on-chain holder observations."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from ... import whales
from ...http_runtime import SingleFlightGroup
from . import onchain_repository as repository

_cache = {}
_lock = threading.Lock()
_flights = SingleFlightGroup()
_PUBLIC_FIELDS = ('coin', 'source', 'source_label', 'source_url', 'scope', 'daily_source',
                  'observed_at', 'tracked_count', 'excluded_count', 'fetched_count', 'http_status',
                  'last_success_ms', 'last_attempt_ms', 'next_collection_ms', 'collection_status', 'error_code')
_EVENT_FIELDS = ('id', 'occurred_at', 'previous_observed_at', 'increased_count', 'decreased_count',
                 'compared_count', 'tracked_count', 'source', 'source_label', 'source_url', 'scope', 'daily_source')


def clear_cache():
    with _lock:
        _cache.clear()


def _millis(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()*1000)
    except (ValueError, TypeError, OverflowError):
        return 0


def _snapshot(coin):
    def load():
        now = time.time()
        with _lock:
            hit = _cache.get(coin)
            if hit and hit[0] > now:
                return deepcopy(hit[1])
        value = repository.read_snapshot(coin)
        with _lock:
            _cache[coin] = (now+2, deepcopy(value))
        return value
    return deepcopy(_flights.run(coin, load)[0])


def _get_coin(coin, *, session_started_at=None):
    cfg = whales.COINS[coin]
    base = {'coin': coin, 'source': cfg['source'], 'status': 'pending', 'items': [],
            'refresh_seconds': int(cfg['ttl']), 'daily_source': coin == 'XRP', 'stale': False,
            'data_source': 'shared_db'}
    try:
        snapshot = _snapshot(coin)
    except Exception:
        return {**base, 'status': 'unavailable', 'error_code': 'storage_unavailable'}
    if snapshot is None:
        return base
    public = {key: snapshot[key] for key in _PUBLIC_FIELDS if key in snapshot}
    now_ms = int(time.time()*1000)
    success = int(snapshot.get('last_success_ms') or 0)
    failed = snapshot.get('collection_status') in {'error', 'rate_limited'}
    stale = bool(success and (failed or now_ms-success >= int(cfg['ttl'])*2000))
    start = max(_millis(session_started_at), now_ms-int(cfg['ttl'])*2000)
    items = [{key: item[key] for key in _EVENT_FIELDS if key in item}
             for item in snapshot.get('items', []) if isinstance(item, dict)
             and start <= _millis(item.get('occurred_at')) <= now_ms+5000]
    status = ('unavailable' if stale or failed else 'pending' if not success
              else 'baseline' if snapshot.get('status') == 'baseline' else 'ready')
    return {**base, **public, 'status': status, 'stale': stale, 'items': items[:20]}


def get_activity(symbol, *, session_started_at=None):
    coin = whales.supported_coin_for_symbol(symbol)
    if coin is None:
        return {'status': 'unsupported', 'items': []}
    return _get_coin(coin, session_started_at=session_started_at)


def get_all_activity():
    coins = [_get_coin(coin) for coin in whales.COINS]
    return {'ok': any(item['status'] in {'baseline', 'ready'} for item in coins), 'coins': coins,
            'data_source': 'shared_db', 'description': '상위 주소의 잔고 변화이며 매수·매도 체결을 뜻하지 않습니다.'}
