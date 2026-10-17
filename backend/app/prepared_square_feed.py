"""Bounded public Square projection over existing prepared articles.

No crawling, AI, work claims, writes, or per-visitor private-body transfer.
The database filters source/type before its candidate cap and selects only an
allowlist of public JSON fields. All searches share one short-lived snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import time

from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from .agent_features.position_news.articles import NewsArticle
from .binance_square import is_community_item, within_window
from .cache_runtime import ResponseCache
from .db import get_session
from .news_identity import news_identity

CANDIDATE_LIMIT = 1000
_responses = ResponseCache('prepared-square-public', max_entries=1, max_bytes=12_000_000,
                           retry_seconds=5, max_retry_seconds=30)
_FIELDS = {
    'title':500, 'original_title':500, 'url':2000, 'source':120, 'published':100,
    'community_post_id':30, 'community_summary':1200, 'community_summary_status':30,
    'asset_symbols':2048, 'categories':2048, 'translation_status':40,
}
_ASSET = re.compile(r'[A-Z0-9]{1,30}')


def _json_text(dialect, key):
    column = NewsArticle.item_json
    if dialect == 'postgresql':
        return cast(column, JSONB).op('->>')(key)
    if dialect == 'sqlite':
        # CASE prevents malformed legacy rows from poisoning a whole SQLite read.
        safe = case((func.json_valid(column) == 1, column), else_='{}')
        return func.json_extract(safe, '$.' + key)
    raise RuntimeError('unsupported prepared Square database')


def _statement(dialect):
    projected = [func.substr(_json_text(dialect, key), 1, limit).label(key)
                 for key, limit in _FIELDS.items()]
    return (select(NewsArticle.asset_symbol.label('feed_asset'), *projected)
            .where(NewsArticle.ready.is_(True),
                   _json_text(dialect, 'content_type') == 'community',
                   _json_text(dialect, 'source') == 'Binance Square')
            .order_by(NewsArticle.last_seen_ms.desc(), NewsArticle.asset_symbol, NewsArticle.article_id)
            .limit(CANDIDATE_LIMIT + 1))


def _assets(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return set()
    if not isinstance(value, list):
        return set()
    return {asset for asset in value[:100] if isinstance(asset, str) and _ASSET.fullmatch(asset)
            and asset != 'MARKET'}


def _clean(value, limit):
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value).strip()[:limit]


def _sanitize(row):
    item = {key:_clean(row.get(key), limit) for key, limit in _FIELDS.items()
            if key not in {'asset_symbols', 'categories'} and isinstance(row.get(key), str)}
    item['content_type'] = 'community'
    if not item.get('title') or not is_community_item(item) or not within_window(item):
        return None
    symbols = _assets(row.get('asset_symbols')) | _assets(row.get('categories'))
    scope = row.get('feed_asset')
    if isinstance(scope, str) and scope != 'MARKET' and _ASSET.fullmatch(scope):
        symbols.add(scope)
    item.update(id=sha256(news_identity(item).encode()).hexdigest()[:20],
                source_id='binance_square', publisher_region='global', asset_symbols=sorted(symbols)[:30])
    stamp = datetime.fromisoformat(item['published'].replace('Z', '+00:00')).astimezone(timezone.utc)
    item['published_ms'] = int(stamp.timestamp() * 1000)
    return item


def _load():
    with get_session() as db:
        rows = db.execute(_statement(db.get_bind().dialect.name)).mappings().all()
    truncated = len(rows) > CANDIDATE_LIMIT
    rows = rows[:CANDIDATE_LIMIT]
    items, invalid, duplicate = {}, 0, 0
    for row in rows:
        item = _sanitize(row)
        if item is None:
            invalid += 1
            continue
        prior = items.get(item['id'])
        if prior is not None:
            duplicate += 1
            prior['asset_symbols'] = sorted(set(prior['asset_symbols']) | set(item['asset_symbols']))[:30]
        else:
            items[item['id']] = item
    result = sorted(items.values(), key=lambda item:(-item['published_ms'], item['id']))
    return {'items':result, 'coverage':{
        'candidate_limit':CANDIDATE_LIMIT, 'candidate_count':len(rows), 'truncated':truncated,
        'basis':'recent_prepared_community_rows', 'deduplicated_count':duplicate,
        'invalid_count':invalid, 'returned_count':len(result)}}


def read_prepared_square():
    """Return safe prepared metadata; callers filter this declared candidate set.

    The 1000-row cap is applied after SQL source/type filtering, not to general
    news. Scope aliases are merged by canonical post identity. Date checks run
    again on cache hits so expired posts never stay visible through cache TTL.
    """
    result, state = _responses.get_or_load('shared', _load, ttl=10, stale_ttl=20)
    prepared_count = len(result['items'])
    result['items'] = [item for item in result['items'] if within_window(item)]
    result['coverage'].update(returned_count=len(result['items']),
                               expired_since_preparation=prepared_count-len(result['items']))
    result.update(status='ready' if result['items'] else 'pending',
                  cached=state in {'cached','stale','shared'}, stale=state == 'stale', refresh_seconds=30)
    return result
