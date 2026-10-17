"""코인동향 소식 목록 네 개(국내/해외 × 뉴스/커뮤니티)가 함께 쓰는 글–코인 연결과 커서 쪽 나누기.

목록은 DB 에서 최근 순으로 한 쪽(20개)만 읽는다. 코인 · 검색어 거르기는 이 연결 표로 SQL 안에서 하고,
다음 쪽은 마지막으로 본 글(시각 · id)을 커서로 받아 이어 읽는다 — 머리에 새 글이 쌓여도 쪽이 밀리지 않는다.
예전엔 요청마다 목록 전체(최대 1,000~2,000건)를 메모리에 만들고 잘라 줬다(2026-10-18 개편).
"""
from __future__ import annotations

import re

from sqlalchemy import BigInteger, Index, and_, delete, exists, func, insert, or_
from sqlmodel import Field, SQLModel, select


class FeedAsset(SQLModel, table=True):
    """보이는 글 하나가 언급한 코인 하나. 안 보이는 글(번역 대기 등)은 행이 없다."""
    __table_args__ = (Index("ix_feedasset_lookup", "feed", "asset", "published_ms", "item_id"),)
    feed: str = Field(primary_key=True)  # "kr:article" · "global:article" · "global:community" · "kr:community"
    item_id: str = Field(primary_key=True)
    asset: str = Field(primary_key=True)
    published_ms: int = Field(sa_type=BigInteger)


_ASSET = re.compile(r"[A-Z0-9]{1,30}")
_CURSOR = re.compile(r"(\d{1,16})\.([A-Za-z0-9:_-]{1,80})")


def feed_key(region: str, content_type: str) -> str:
    return f"{region}:{content_type}"


def clean_assets(values) -> list[str]:
    return sorted({str(value) for value in values or [] if _ASSET.fullmatch(str(value))})[:30]


def replace_assets(db, feed: str, item_id: str, assets, published_ms: int) -> None:
    """글의 코인 연결을 새로 쓴다(빈 목록이면 지운다 — 안 보이게 된 글)."""
    db.exec(delete(FeedAsset).where(FeedAsset.feed == feed, FeedAsset.item_id == item_id))
    rows = [{"feed": feed, "item_id": item_id, "asset": asset, "published_ms": int(published_ms)}
            for asset in clean_assets(assets)]
    if rows:
        db.exec(insert(FeedAsset).values(rows))


def drop_items(db, feed: str, item_ids) -> None:
    ids = list(item_ids)
    for start in range(0, len(ids), 500):
        db.exec(delete(FeedAsset).where(FeedAsset.feed == feed, FeedAsset.item_id.in_(ids[start:start + 500])))


def prune(db, feed: str, before_ms: int) -> None:
    db.exec(delete(FeedAsset).where(FeedAsset.feed == feed, FeedAsset.published_ms < before_ms))


def mentioning(feed: str, id_column, assets):
    """id_column 의 글이 assets 중 하나를 언급하는가(연결 표 EXISTS — 인덱스로 찾는다)."""
    return exists(select(FeedAsset.item_id).where(
        FeedAsset.feed == feed, FeedAsset.item_id == id_column, FeedAsset.asset.in_(sorted(set(assets)))))


def mention_counts(db, feed: str, since_ms: int, allowed=None) -> dict[str, int]:
    rows = db.exec(select(FeedAsset.asset, func.count()).where(
        FeedAsset.feed == feed, FeedAsset.published_ms >= since_ms).group_by(FeedAsset.asset)).all()
    return {asset: int(count) for asset, count in rows if allowed is None or asset in allowed}


def encode_cursor(published_ms: int, item_id: str) -> str:
    return f"{int(published_ms)}.{item_id}"


def decode_cursor(value: str | None) -> tuple[int, str] | None:
    if not value:
        return None
    match = _CURSOR.fullmatch(str(value))
    if not match:
        raise ValueError("다음 쪽 위치가 올바르지 않아요.")
    return int(match.group(1)), match.group(2)


def before(ms_column, id_column, cursor: tuple[int, str]):
    """최근 순(시각 · id 내림차순)에서 커서 다음 글들."""
    published_ms, item_id = cursor
    return or_(ms_column < published_ms, and_(ms_column == published_ms, id_column < item_id))


def like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
