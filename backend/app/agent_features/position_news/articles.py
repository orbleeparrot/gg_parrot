"""Durable article projections and monotonic cursors shared by news readers.

Snapshots fence expensive analysis; articles independently become visible as
soon as their headline is ready. Readers never schedule enrichment.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Index, delete, or_, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Field, Session, SQLModel, select

from ...db import get_session


class NewsArticleFeed(SQLModel, table=True):
    asset_symbol: str = Field(primary_key=True)
    revision: int = Field(default=0, sa_type=BigInteger)
    item_count: int = 0
    ready_count: int = 0
    updated_ms: int = Field(default=0, sa_type=BigInteger)
    metadata_json: str = "{}"


class NewsArticle(SQLModel, table=True):
    __table_args__ = (
        Index("ix_newsarticle_feed_revision", "asset_symbol", "ready", "revision"),
        Index("ix_newsarticle_last_seen", "last_seen_ms"),
        Index("ix_newsarticle_enrichment", "enrichment_pending", "last_seen_ms"),
    )
    asset_symbol: str = Field(primary_key=True)
    article_id: str = Field(primary_key=True)
    revision: int = Field(sa_type=BigInteger)
    ready: bool = False
    enrichment_pending: bool = False
    item_json: str
    assessment_json: str = "{}"
    analysis_source: str = "rule"
    analysis_status: str = "pending"
    first_seen_ms: int = Field(sa_type=BigInteger)
    last_seen_ms: int = Field(sa_type=BigInteger)


class NewsMaintenanceLease(SQLModel, table=True):
    name: str = Field(primary_key=True)
    next_run_ms: int = Field(default=0, sa_type=BigInteger)


def article_id(item: dict) -> str:
    if item.get("content_type") == "community":
        identity = "|".join(("community", str(item.get("source") or "Binance Square"),
                             str(item.get("community_post_id") or item.get("url") or "")))
    else:
        identity = "|".join((str(item.get("original_title") or item.get("title") or ""),
                             str(item.get("source") or ""))).strip("|")
        identity = identity or str(item.get("url") or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _ready(item: dict) -> bool:
    from ... import news
    title = str(item.get("title") or "")
    original = str(item.get("original_title") or "")
    return bool(re.search(r"[가-힣]", title)) and not news._title_needs_korean_translation(title) and (
        not original or not news._title_needs_korean_translation(original)
        or news._valid_title_translation(original, title)
    )


def _merge_item(old: dict, incoming: dict) -> dict:
    merged = {**old, **incoming}
    # Source rediscovery must not undo a completed translation or body summary.
    if _ready(old) and not _ready(incoming):
        merged["title"] = old["title"]
        if old.get("original_title"):
            merged["original_title"] = old["original_title"]
    if old.get("community_body_hash") == merged.get("community_body_hash"):
        if old.get("community_summary_status") == "ready" and incoming.get("community_summary_status") != "ready":
            for key in ("community_summary", "community_summary_status", "community_summary_partial"):
                if key in old:
                    merged[key] = old[key]
    elif incoming.get("content_type") == "community":
        for key in ("community_summary", "community_summary_status", "community_summary_partial"):
            if key not in incoming:
                merged.pop(key, None)
    return merged


def _insert(db):
    return postgres_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def upsert_articles(asset_symbol: str, items: list[dict], *, analysis: dict | None = None,
                    payload: dict | None = None, now_ms: int | None = None,
                    enrichment_only: bool = False,
                    db: Session | None = None) -> int:
    """Commit discovered/translated articles without waiting for sibling work.

    The feed lock serializes revisions. One batch upsert preserves stable IDs,
    completed translations and higher-quality analysis across source refreshes.
    """
    if db is None:
        with get_session() as owned:
            return upsert_articles(asset_symbol, items, analysis=analysis, payload=payload,
                                   now_ms=now_ms, enrichment_only=enrichment_only, db=owned)
    scope = str(asset_symbol).strip().upper()
    if not scope:
        raise ValueError("news feed scope is required")
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    insert = _insert(db)
    db.exec(insert(NewsArticleFeed).values(asset_symbol=scope).on_conflict_do_nothing(
        index_elements=[NewsArticleFeed.asset_symbol]))
    state = db.exec(select(NewsArticleFeed).where(NewsArticleFeed.asset_symbol == scope)
                    .with_for_update().execution_options(populate_existing=True)).one()
    incoming = {}
    assessments = list((analysis or {}).get("items") or [])
    for index, item in enumerate(items):
        if not item.get("title"):
            continue
        key = article_id(item)
        incoming[key] = (dict(item), assessments[index] if index < len(assessments) else None)
    rows = db.exec(select(NewsArticle).where(NewsArticle.asset_symbol == scope,
                                           NewsArticle.article_id.in_(list(incoming)))
                   .execution_options(populate_existing=True)).all() if incoming else []
    existing = {row.article_id: row for row in rows}
    values = []
    for key, (item, assessment) in incoming.items():
        old = existing.get(key)
        old_item = json.loads(old.item_json) if old else {}
        if enrichment_only and (old is None or
                old_item.get("community_body_hash") != item.get("community_body_hash") or
                (old_item.get("excerpt") and item.get("excerpt") and old_item["excerpt"] != item["excerpt"])):
            continue  # A late worker cannot overwrite a newer body/content version.
        merged = _merge_item(old_item, item)
        merged["id"] = key
        item_json = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        assessment_json = old.assessment_json if old else "{}"
        source = old.analysis_source if old else "rule"
        status = old.analysis_status if old else "pending"
        if assessment is not None and (not old or source != "ai" or (analysis or {}).get("analysis_source") == "ai"):
            assessment_json = json.dumps(assessment, ensure_ascii=False, sort_keys=True)
            source = str((analysis or {}).get("analysis_source") or "rule")
            status = str((analysis or {}).get("analysis_status") or "ready")
        ready = _ready(merged)
        enrichment_pending = not ready or bool(merged.get("content_type") == "community"
            and merged.get("community_body") and merged.get("community_summary_status") not in {"ready", "unavailable"})
        if old and (old.item_json, old.assessment_json, old.analysis_source, old.analysis_status, old.ready, old.enrichment_pending) == (
            item_json, assessment_json, source, status, ready, enrichment_pending
        ):
            continue
        state.revision += 1
        state.item_count += int(old is None)
        state.ready_count += int(ready) - int(bool(old and old.ready))
        values.append(dict(asset_symbol=scope, article_id=key, revision=state.revision,
                           ready=ready, enrichment_pending=enrichment_pending, item_json=item_json, assessment_json=assessment_json,
                           analysis_source=source, analysis_status=status,
                           first_seen_ms=old.first_seen_ms if old else millis, last_seen_ms=millis))
    if values:
        statement = insert(NewsArticle).values(values)
        db.exec(statement.on_conflict_do_update(
            index_elements=[NewsArticle.asset_symbol, NewsArticle.article_id],
            set_={key: getattr(statement.excluded, key) for key in values[0]
                  if key not in {"asset_symbol", "article_id", "first_seen_ms"}}))
    if incoming:
        db.exec(update(NewsArticle).where(NewsArticle.asset_symbol == scope,
                                         NewsArticle.article_id.in_(list(incoming)),
                                         NewsArticle.last_seen_ms < millis).values(last_seen_ms=millis))
    if payload is not None:
        metadata = json.loads(state.metadata_json)
        metadata.update({key: payload[key] for key in (
            "symbol", "coin_name", "label", "query", "sources", "browser_enrichment", "refresh_seconds",
            "as_of", "overview", "ai", "image_status", "collection_status", "disclaimer",
        ) if key in payload})
        state.metadata_json = json.dumps(metadata, ensure_ascii=False)
    state.updated_ms = max(state.updated_ms, millis)
    revision = state.revision
    db.add(state)
    db.commit()
    # A worker and an HTTP reader may share a process. External workers are
    # observed through the short read TTL; local publications are visible now.
    from ...public_news_cache import responses
    responses.invalidate(scope)
    return revision


def read_article_feed(asset_symbol: str, *, after_revision: int | None = None,
                      limit: int = 100, include_analysis: bool = True,
                      db: Session | None = None) -> dict | None:
    """Read prepared items and a resumable cursor in two indexed queries."""
    if db is None:
        with get_session() as owned:
            return read_article_feed(asset_symbol, after_revision=after_revision, limit=limit,
                                     include_analysis=include_analysis, db=owned)
    state = db.get(NewsArticleFeed, str(asset_symbol).strip().upper())
    if state is None:
        return None
    bound = max(1, min(500, int(limit)))
    cursor = state.revision
    reset = after_revision is None or after_revision > cursor
    projection = select(NewsArticle) if include_analysis else select(NewsArticle.item_json, NewsArticle.revision)
    statement = projection.where(NewsArticle.asset_symbol == state.asset_symbol,
                                          NewsArticle.ready.is_(True), NewsArticle.revision <= cursor)
    if not reset:
        statement = statement.where(NewsArticle.revision > max(0, after_revision))
    statement = statement.order_by(NewsArticle.revision.desc() if reset else NewsArticle.revision)
    rows = list(db.exec(statement.limit(bound + 1)).all())
    has_more = not reset and len(rows) > bound
    rows = rows[:bound]
    if has_more:
        cursor = rows[-1].revision
    items = [json.loads(row.item_json) for row in rows]
    pending = max(0, state.item_count - state.ready_count)
    result = {**json.loads(state.metadata_json), "items": items, "cursor": cursor,
            "reset": reset, "has_more": has_more,
            "updated_at": datetime.fromtimestamp(state.updated_ms / 1000, timezone.utc).isoformat(),
            "translation": {"status": "partial" if pending else "ready", "pending_count": pending,
                            "retry_after_seconds": 3}}
    if include_analysis:
        result["analysis"] = {"items": [json.loads(row.assessment_json) for row in rows],
                              "analysis_source": "ai" if any(row.analysis_source == "ai" for row in rows) else "rule",
                              "analysis_status": "ready", "ai": any(row.analysis_source == "ai" for row in rows)}
    return result


def update_article_image(asset_symbol: str, article_id: str, image_fields: dict, *, now_ms=None, db=None):
    """Apply a late image result to the current article without stale text writes."""
    if db is None:
        with get_session() as owned:
            return update_article_image(asset_symbol, article_id, image_fields, now_ms=now_ms, db=owned)
    scope = str(asset_symbol).strip().upper()
    state = db.exec(select(NewsArticleFeed).where(NewsArticleFeed.asset_symbol == scope).with_for_update()).first()
    if state is None:
        return None
    current = db.exec(select(NewsArticle).where(NewsArticle.asset_symbol == scope,
        NewsArticle.article_id == article_id).execution_options(populate_existing=True)).first()
    if current is None:
        return None
    item = json.loads(current.item_json)
    item.update({key: value for key, value in image_fields.items()
                 if key in {"image", "article_url", "image_resolved"}})
    return upsert_articles(scope, [item], now_ms=now_ms, db=db)


def claim_maintenance(name: str = "news-cache-prune", *, interval_seconds: int = 3600,
                      now_ms: int | None = None, db: Session | None = None) -> bool:
    """One maintenance winner across embedded collectors and external workers."""
    if db is None:
        with get_session() as owned:
            return claim_maintenance(name, interval_seconds=interval_seconds, now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    statement = _insert(db)(NewsMaintenanceLease).values(
        name=name, next_run_ms=millis + interval_seconds * 1000)
    changed = db.exec(statement.on_conflict_do_update(
        index_elements=[NewsMaintenanceLease.name], set_={"next_run_ms": statement.excluded.next_run_ms},
        where=NewsMaintenanceLease.next_run_ms <= millis))
    won = changed.rowcount == 1
    db.commit()
    return won


def pending_article_batches(*, limit=50, now_ms=None, db=None):
    """Bound retry work independently of source collection and HTTP traffic."""
    if db is None:
        with get_session() as owned:
            return pending_article_batches(limit=limit, now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    rows = db.exec(select(NewsArticle).outerjoin(NewsMaintenanceLease,
        NewsMaintenanceLease.name == ("article-enrichment:" + NewsArticle.asset_symbol)).where(
            NewsArticle.enrichment_pending.is_(True),
            or_(NewsMaintenanceLease.name.is_(None), NewsMaintenanceLease.next_run_ms <= millis),
        ).order_by(NewsArticle.last_seen_ms, NewsArticle.article_id).limit(max(1, min(100, limit)))).all()
    batches = {}
    for row in rows:
        batches.setdefault(row.asset_symbol, []).append(json.loads(row.item_json))
    return batches


def prune_articles(*, retention_days=30, limit=500, now_ms=None, db=None):
    """Bound observed-article retention while keeping feed counters consistent."""
    if db is None:
        with get_session() as owned:
            return prune_articles(retention_days=retention_days, limit=limit, now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    cutoff = millis - max(1, retention_days) * 86_400_000
    selected = db.exec(select(NewsArticle.asset_symbol, NewsArticle.article_id)
        .where(NewsArticle.last_seen_ms < cutoff).order_by(NewsArticle.last_seen_ms)
        .limit(max(1, min(1000, limit)))).all()
    if not selected:
        db.rollback()
        return 0
    states = db.exec(select(NewsArticleFeed).where(NewsArticleFeed.asset_symbol.in_(
        sorted({row.asset_symbol for row in selected}))).order_by(NewsArticleFeed.asset_symbol)
        .with_for_update()).all()
    keys = [(row.asset_symbol, row.article_id) for row in selected]
    # A source may have reobserved a candidate before we obtained its feed lock.
    rows = db.exec(select(NewsArticle).where(
        tuple_(NewsArticle.asset_symbol, NewsArticle.article_id).in_(keys),
        NewsArticle.last_seen_ms < cutoff)).all()
    by_scope = {state.asset_symbol: state for state in states}
    for row in rows:
        state = by_scope[row.asset_symbol]
        state.item_count = max(0, state.item_count - 1)
        state.ready_count = max(0, state.ready_count - int(row.ready))
        db.add(state)
    if rows:
        db.exec(delete(NewsArticle).where(tuple_(NewsArticle.asset_symbol, NewsArticle.article_id).in_(
            [(row.asset_symbol, row.article_id) for row in rows])))
    db.commit()
    from ...public_news_cache import responses
    for scope in by_scope:
        responses.invalidate(scope)
    return len(rows)
