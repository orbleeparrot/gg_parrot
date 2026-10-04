"""근거용 제목 아카이브 — 제목·날짜·출처·URL 만 무기한 보관한다(저장 전용, 네트워크·AI 없음).

asset 인자는 거래소 마켓 심볼이 아니라 이미 코인으로 줄인 값("BTC")이다. 뉴스는 거래소가 아니라
코인에 대한 것이므로 KRW-BTC 와 BTCUSDT 는 같은 행을 쓴다. 줄이는 일은 호출하는 쪽이
engine.summary._coin 으로 한다 — 여기서는 대문자로 맞출 뿐이다.

근거 조회는 덤으로 붙는 기능이라 저장·조회가 실패해도 호출한 쪽(검증 응답)을 깨뜨리면 안 된다.
그래서 DB 오류는 경고 로그만 남기고 store 는 0, lookup 은 빈 목록을 돌려준다.
"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from .db import NewsHeadlineArchive, get_session

logger = logging.getLogger(__name__)

MAX_ROWS = 5
# NewsHeadlineArchive 의 열 길이와 같아야 한다.
_ASSET_MAX = 20
_TITLE_MAX = 500
_SOURCE_MAX = 120
_URL_MAX = 1000
# 한 문장에 묶는 행 수 — SQLite 의 바인딩 변수 상한(999)을 넘지 않게 6열 x 100 행으로 끊는다.
_CHUNK = 100


def _asset(value) -> str:
    return str(value or "").strip().upper()[:_ASSET_MAX]


def _key(asset: str, url: str) -> str:
    return hashlib.sha256(f"{asset}\n{url}".encode("utf-8")).hexdigest()


def store(asset: str, items: list[dict]) -> int:
    """제목과 URL 이 있는 것만 적재하고, 실제로 새로 들어간 행 수를 돌려준다.

    같은 코인·URL 은 한 번만 들어가며 이미 있는 행은 세지 않는다. 실패하면 0.
    """
    asset = _asset(asset)
    rows = []
    seen = set()
    for item in items or []:
        url = str(item.get("url") or "").strip()[:_URL_MAX]
        title = str(item.get("title") or "").strip()[:_TITLE_MAX]
        if not asset or not url or not title:
            continue
        key = _key(asset, url)
        if key in seen:
            continue
        seen.add(key)
        try:
            published_ms = int(item.get("published_ms") or 0)
        except (TypeError, ValueError):
            published_ms = 0
        rows.append(dict(archive_key=key, asset_symbol=asset, published_ms=published_ms,
                         title=title, source=str(item.get("source") or "").strip()[:_SOURCE_MAX],
                         url=url))
    if not rows:
        return 0
    try:
        inserted = 0
        with get_session() as db:
            insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
            for start in range(0, len(rows), _CHUNK):
                result = db.exec(insert(NewsHeadlineArchive).values(rows[start:start + _CHUNK])
                                 .on_conflict_do_nothing(index_elements=["archive_key"]))
                inserted += max(0, result.rowcount or 0)
            db.commit()
        return inserted
    except Exception:
        logger.warning("뉴스 제목 아카이브 저장 실패 (asset=%s, 건수=%d)", asset, len(rows), exc_info=True)
        return 0


def lookup(asset: str, *, start_ms: int, end_ms: int, limit: int = MAX_ROWS) -> list[dict]:
    """구간 안의 제목들을 오래된 순으로. 없거나 실패하면 빈 목록 — 그게 정상 경로다."""
    asset = _asset(asset)
    try:
        start_ms, end_ms, limit = int(start_ms), int(end_ms), max(1, int(limit))
    except (TypeError, ValueError):
        return []
    if not asset or end_ms < start_ms:
        return []
    try:
        with get_session() as db:
            rows = db.exec(select(NewsHeadlineArchive).where(
                NewsHeadlineArchive.asset_symbol == asset,
                NewsHeadlineArchive.published_ms >= start_ms,
                NewsHeadlineArchive.published_ms <= end_ms,
            ).order_by(NewsHeadlineArchive.published_ms).limit(limit)).all()
            return [{"title": row.title, "source": row.source, "url": row.url,
                     "published_ms": row.published_ms} for row in rows]
    except Exception:
        logger.warning("뉴스 제목 아카이브 조회 실패 (asset=%s)", asset, exc_info=True)
        return []
