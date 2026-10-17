"""Binance Square 글을 코인동향 해외 커뮤니티 목록(trendarticle)으로 옮기기 위한 안전한 읽기.

포지션 뉴스 수집기가 newsarticle 에 코인 범위마다 따로 저장한 Square 글(948개가 72,000행)을, 바뀐 행만
last_seen_ms 인덱스로 골라 공개 필드만 읽는다. 크롤링 · AI · 쓰기 · 본문 전송은 없다. 예전엔 화면이 읽을 때마다
표 전체(11만 행 · 656MB)를 JSON 으로 풀어 첫 쪽이 최대 12.8초 걸렸다(2026-10-18) — 이제 regional_trends 수집기가
1분마다 바뀐 글만 옮기고 화면은 trendarticle 에서 20개씩 읽는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from sqlalchemy import case, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB

from .agent_features.position_news.articles import NewsArticle
from .binance_square import is_community_item, within_window
from .db import get_session
from .news_identity import news_identity

CANDIDATE_LIMIT = 1000
_FIELDS = {
    'title':500, 'original_title':500, 'url':2000, 'source':120, 'published':100,
    'community_post_id':30, 'community_summary':1200, 'community_summary_status':30,
    'asset_symbols':2048, 'categories':2048, 'translation_status':40,
}
_ASSET = re.compile(r'[A-Z0-9]{1,30}')


def _json_text(dialect, key):
    column = NewsArticle.item_json
    if dialect == 'postgresql':
        # IS JSON(Postgres 16+, 운영 17) — 깨진 행 하나가 쿼리 전체를 실패시키면 이어 읽기가 그 자리에서 멈춘다.
        safe = case((literal_column('newsarticle.item_json IS JSON'), column), else_='{}')
        return cast(safe, JSONB).op('->>')(key)
    if dialect == 'sqlite':
        # CASE prevents malformed legacy rows from poisoning a whole SQLite read.
        safe = case((func.json_valid(column) == 1, column), else_='{}')
        return func.json_extract(safe, '$.' + key)
    raise RuntimeError('unsupported prepared Square database')


def _statement(dialect, *, since_ms=None, limit=None):
    """since_ms 가 없으면 최근 순 첫 채우기, 있으면 그 뒤에 바뀐 행을 오래된 순으로(이어 읽기)."""
    projected = [func.substr(_json_text(dialect, key), 1, limit_).label(key)
                 for key, limit_ in _FIELDS.items()]
    statement = (select(NewsArticle.asset_symbol.label('feed_asset'), NewsArticle.last_seen_ms.label('last_seen_ms'),
                        *projected)
                 .where(NewsArticle.ready.is_(True),
                        _json_text(dialect, 'content_type') == 'community',
                        _json_text(dialect, 'source') == 'Binance Square'))
    if since_ms is None:
        statement = statement.order_by(NewsArticle.last_seen_ms.desc(), NewsArticle.asset_symbol, NewsArticle.article_id)
    else:
        statement = (statement.where(NewsArticle.last_seen_ms > since_ms)
                     .order_by(NewsArticle.last_seen_ms, NewsArticle.asset_symbol, NewsArticle.article_id))
    return statement.limit((limit or CANDIDATE_LIMIT) + 1)


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


def load_changed(since_ms=None, *, limit=None):
    """공개해도 되는 Square 글(코인 범위 별칭은 글 하나로 합침)과 읽은 행 중 가장 늦은 last_seen_ms.

    since_ms 가 없으면 최근 CANDIDATE_LIMIT 행(첫 채우기 — 표를 한 번 훑는다), 있으면 그 뒤에 저장 · 재발견된
    행만(인덱스). truncated 면 다음 호출이 last_seen_ms 부터 이어 읽는다.
    """
    cap = limit or CANDIDATE_LIMIT
    with get_session() as db:
        rows = db.execute(_statement(db.get_bind().dialect.name, since_ms=since_ms, limit=cap)).mappings().all()
    truncated = len(rows) > cap
    rows = rows[:cap]
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
    seen = [int(row['last_seen_ms']) for row in rows if row.get('last_seen_ms') is not None]
    return {'items':result, 'max_last_seen_ms':max(seen) if seen else None, 'truncated':truncated,
            'coverage':{'candidate_limit':cap, 'candidate_count':len(rows), 'truncated':truncated,
                        'deduplicated_count':duplicate, 'invalid_count':invalid, 'returned_count':len(result)}}
