"""Durable article projections and monotonic cursors shared by news readers.

Snapshots fence expensive analysis; articles independently become visible as
soon as their headline is ready. Readers never schedule enrichment.
"""
from __future__ import annotations

import hashlib
import json
import os
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
        Index("ix_newsarticle_enrichment_due", "enrichment_pending", "enrichment_next_ms"),
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
    # 저장 전 비교용 해시 — 같은 원본 항목(source_hash)이나 같은 본문(content_hash)이 다시 오면
    # item_json 을 내려받지 않고 건너뛴다(egress 절감, 2026-09-16).
    source_hash: str = ""
    content_hash: str = ""
    # 보강 재시도 — 공급자(번역·요약)가 정상인데도 실패한 횟수와 다음 재시도 시각.
    # 간격은 30초 → 60초 → 120초에서 멈춘다(실시간이 중요하니 오래 미루지 않는다). 공급자가 막혔을 때의
    # 절약은 기사별 대기가 아니라 루프 전체의 '정체' 쉼(enrichment_stalled)이 맡는다.
    enrichment_attempts: int = 0
    enrichment_next_ms: int = Field(default=0, sa_type=BigInteger)


ENRICHMENT_RETRY_BASE_SECONDS = 30
ENRICHMENT_RETRY_MAX_SECONDS = 120
ENRICHMENT_STALL_LEASE = "article-enrichment-stall"
ENRICHMENT_PROBE_LIMIT = 5


def enrichment_max_attempts() -> int:
    """공급자가 정상(같은 회차의 다른 행은 진전)인데도 이 행만 계속 실패하면 이 횟수에 포기한다."""
    return max(1, int(os.environ.get("POSITION_NEWS_ENRICHMENT_MAX_ATTEMPTS", "5")))


def enrichment_max_age_ms() -> int:
    """이보다 오래된 기사는 보강을 포기한다 — 실시간 매매에 늦은 뉴스는 번역돼도 쓸모가 없다."""
    return max(1, int(os.environ.get("POSITION_NEWS_ENRICHMENT_MAX_AGE_HOURS", "6"))) * 3600 * 1000


def enrichment_stall_seconds() -> int:
    """한 회차에 아무 진전이 없으면(공급자 막힘) 루프 전체가 쉬는 시간. 복구는 이 안에 알아챈다."""
    return max(5, int(os.environ.get("POSITION_NEWS_ENRICHMENT_STALL_SECONDS", "60")))


def enrichment_retry_delay_ms(attempts: int) -> int:
    """``attempts`` 번째 실패 뒤 기다릴 시간: 30초 × 2^attempts, 최대 2분."""
    seconds = ENRICHMENT_RETRY_BASE_SECONDS * (2 ** max(0, int(attempts)))
    return min(ENRICHMENT_RETRY_MAX_SECONDS, seconds) * 1000


def item_hash(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


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
    # 1단계: 해시만 가볍게 읽어 같은 항목이 다시 온 행을 고른다 — 본문(item_json)은 내려받지 않는다.
    digests = {key: item_hash(item) for key, (item, _assessment) in incoming.items()}
    known = {row.article_id: (row.source_hash, row.content_hash) for row in db.exec(
        select(NewsArticle.article_id, NewsArticle.source_hash, NewsArticle.content_hash)
        .where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(list(incoming)))).all()} if incoming else {}
    skipped = {
        key for key, (item, assessment) in incoming.items()
        if key in known and assessment is None
        and (digests[key] == known[key][1] or (not enrichment_only and digests[key] == known[key][0]))
    }
    # 2단계: 새 행이거나 내용이 바뀐 행만 본문까지 읽는다.
    wanted = [key for key in incoming if key in known and key not in skipped]
    rows = db.exec(select(NewsArticle).where(NewsArticle.asset_symbol == scope,
                                           NewsArticle.article_id.in_(wanted))
                   .execution_options(populate_existing=True)).all() if wanted else []
    existing = {row.article_id: row for row in rows}
    values = []
    hash_fixes = []  # 내용은 그대로인데 해시가 비어 있는(예전) 행 — 해시만 채워 다음부터 건너뛴다
    # 헤더 알림용: 이번 저장으로 처음 읽을 수 있게 된(한국어 제목 준비) 기사 제목. 피드가
    # 비어 있던 첫 수집은 세지 않는다 — 세션을 켠 직후 30건이 한꺼번에 울리지 않게.
    prior_ready = state.ready_count
    fresh_titles = []
    for key, (item, assessment) in incoming.items():
        if key in skipped:
            continue
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
        content_hash = text_hash(item_json)
        source_hash = old.source_hash if (enrichment_only and old) else digests[key]
        if old and (old.item_json, old.assessment_json, old.analysis_source, old.analysis_status, old.ready, old.enrichment_pending) == (
            item_json, assessment_json, source, status, ready, enrichment_pending
        ):
            if (old.source_hash, old.content_hash) != (source_hash, content_hash):
                hash_fixes.append((key, source_hash, content_hash))
            continue
        # 백오프: 보강이 끝났거나 원본이 바뀌면 처음부터, 보강 재시도가 아직 못 끝낸 행은 예약을 유지한다.
        if not enrichment_pending or old is None or not enrichment_only:
            attempts, next_ms = 0, 0
        else:
            attempts, next_ms = old.enrichment_attempts, old.enrichment_next_ms
        state.revision += 1
        state.item_count += int(old is None)
        state.ready_count += int(ready) - int(bool(old and old.ready))
        if ready and not (old and old.ready):
            fresh_titles.append(str(merged.get("title") or ""))
        values.append(dict(asset_symbol=scope, article_id=key, revision=state.revision,
                           ready=ready, enrichment_pending=enrichment_pending, item_json=item_json, assessment_json=assessment_json,
                           analysis_source=source, analysis_status=status,
                           first_seen_ms=old.first_seen_ms if old else millis, last_seen_ms=millis,
                           source_hash=source_hash, content_hash=content_hash,
                           enrichment_attempts=attempts, enrichment_next_ms=next_ms))
    for key, source_hash, content_hash in hash_fixes:
        db.exec(update(NewsArticle).where(NewsArticle.asset_symbol == scope, NewsArticle.article_id == key)
                .values(source_hash=source_hash, content_hash=content_hash))
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
    if fresh_titles and prior_ready > 0:
        from ... import notifications as notifications_mod
        notifications_mod.notify_running_sessions(
            db, asset=scope, title=f"{scope} 새 기사 {len(fresh_titles)}건", body=fresh_titles[0],
            link="/agents", data={"event": "news", "asset": scope, "count": len(fresh_titles)},
            ref=f"news:{scope}:{state.revision}")
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
    # 승패는 RETURNING 으로 가른다. 운영 Postgres(psycopg)에서 INSERT … ON CONFLICT 의 rowcount 는 -1 이라
    # `rowcount == 1` 판정이 늘 졌고, 그 때문에 보강 재시도와 정리가 운영에서 한 번도 돌지 않았다(2026-09-16).
    won = db.exec(statement.on_conflict_do_update(
        index_elements=[NewsMaintenanceLease.name], set_={"next_run_ms": statement.excluded.next_run_ms},
        where=NewsMaintenanceLease.next_run_ms <= millis).returning(NewsMaintenanceLease.name)).first() is not None
    db.commit()
    return won


def has_pending_articles(*, now_ms=None, db=None) -> bool:
    """재시도 시각이 된 보강 대기 행이 하나라도 있는지 — 5초 스캔이 본문을 읽기 전에 묻는 가벼운 질문."""
    if db is None:
        with get_session() as owned:
            return has_pending_articles(now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    return db.exec(select(NewsArticle.article_id).where(
        NewsArticle.enrichment_pending.is_(True), NewsArticle.enrichment_next_ms <= millis).limit(1)).first() is not None


def enrichment_stall(*, now_ms=None, db=None) -> dict:
    """루프 전체의 '정체' 상태: {"stalled": 쉬는 중인가, "probing": 정체 뒤 첫 회차인가}.

    직전 회차에 아무 진전이 없었으면 ``enrichment_stall_seconds`` 동안 쉬고, 쉼이 끝난 첫 회차는
    작은 탐침(ENRICHMENT_PROBE_LIMIT 행)만 보내 공급자가 돌아왔는지 본다.
    """
    if db is None:
        with get_session() as owned:
            return enrichment_stall(now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    lease = db.get(NewsMaintenanceLease, ENRICHMENT_STALL_LEASE)
    until = int(lease.next_run_ms) if lease else 0
    return {"stalled": until > millis, "probing": until > 0}


def record_enrichment_pass(progressed: bool, *, now_ms=None, db=None) -> None:
    """회차 결과를 남긴다 — 진전이 있으면 정체를 풀고, 없으면 루프를 쉬게 한다."""
    if db is None:
        with get_session() as owned:
            return record_enrichment_pass(progressed, now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    next_run_ms = 0 if progressed else millis + enrichment_stall_seconds() * 1000
    statement = _insert(db)(NewsMaintenanceLease).values(name=ENRICHMENT_STALL_LEASE, next_run_ms=next_run_ms)
    db.exec(statement.on_conflict_do_update(index_elements=[NewsMaintenanceLease.name],
                                            set_={"next_run_ms": statement.excluded.next_run_ms}))
    db.commit()


def enrichment_snapshot(asset_symbol: str, article_ids, *, db=None) -> dict:
    """배치 행들의 (pending, content_hash) — 회차 전후를 비교해 어느 행이 진전했는지 잰다(본문은 안 읽는다)."""
    if db is None:
        with get_session() as owned:
            return enrichment_snapshot(asset_symbol, article_ids, db=owned)
    scope = str(asset_symbol).strip().upper()
    ids = sorted({str(value) for value in article_ids if value})
    if not ids:
        return {}
    return {row.article_id: (bool(row.enrichment_pending), row.content_hash) for row in db.exec(
        select(NewsArticle.article_id, NewsArticle.enrichment_pending, NewsArticle.content_hash)
        .where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(ids))).all()}


def settle_enrichment_batch(asset_symbol: str, before: dict, *, now_ms=None, db=None) -> dict:
    """회차가 끝난 뒤 배치의 행을 정리한다. 돌려주는 값은 {"progressed": n, "retry": n, "given_up": n}.

    진전한 행(pending 해제·본문 변경)은 백오프를 지운다. 아직 pending 인 행은 30초 × 2^시도(최대 2분) 뒤로
    미루되, 같은 배치에 진전한 행이 있을 때(공급자가 정상이라는 뜻)만 시도 횟수를 센다. 정상인데도
    ``enrichment_max_attempts`` 번 실패했거나 ``enrichment_max_age_ms`` 보다 오래된 행은 포기한다.
    """
    if db is None:
        with get_session() as owned:
            return settle_enrichment_batch(asset_symbol, before, now_ms=now_ms, db=owned)
    scope = str(asset_symbol).strip().upper()
    if not before:
        return {"progressed": 0, "retry": 0, "given_up": 0}
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    after = {row.article_id: row for row in db.exec(
        select(NewsArticle.article_id, NewsArticle.enrichment_pending, NewsArticle.content_hash,
               NewsArticle.enrichment_attempts, NewsArticle.first_seen_ms)
        .where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(sorted(before)))).all()}
    progressed = [key for key, (_was_pending, old_hash) in before.items()
                  if key in after and (not after[key].enrichment_pending or after[key].content_hash != old_hash)]
    healthy = bool(progressed)
    retry, give_up = {}, {}
    for key in before:
        row = after.get(key)
        if row is None or key in progressed:
            continue
        attempts = int(row.enrichment_attempts or 0) + (1 if healthy else 0)
        too_old = millis - int(row.first_seen_ms or millis) > enrichment_max_age_ms()
        if attempts >= enrichment_max_attempts() or too_old:
            give_up.setdefault(attempts, []).append(key)
        else:
            retry.setdefault(attempts, []).append(key)
    if progressed:
        db.exec(update(NewsArticle).where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(progressed),
                                          NewsArticle.enrichment_pending.is_(True))
                .values(enrichment_attempts=0, enrichment_next_ms=0))
    for attempts, keys in retry.items():
        db.exec(update(NewsArticle).where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(keys))
                .values(enrichment_attempts=attempts, enrichment_next_ms=millis + enrichment_retry_delay_ms(attempts)))
    for attempts, keys in give_up.items():
        db.exec(update(NewsArticle).where(NewsArticle.asset_symbol == scope, NewsArticle.article_id.in_(keys))
                .values(enrichment_pending=False, enrichment_attempts=attempts))
    db.commit()
    return {"progressed": len(progressed), "retry": sum(len(keys) for keys in retry.values()),
            "given_up": sum(len(keys) for keys in give_up.values())}


def pending_article_batches(*, limit=50, now_ms=None, db=None):
    """Bound retry work independently of source collection and HTTP traffic."""
    if db is None:
        with get_session() as owned:
            return pending_article_batches(limit=limit, now_ms=now_ms, db=owned)
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    rows = db.exec(select(NewsArticle).outerjoin(NewsMaintenanceLease,
        NewsMaintenanceLease.name == ("article-enrichment:" + NewsArticle.asset_symbol)).where(
            NewsArticle.enrichment_pending.is_(True),
            NewsArticle.enrichment_next_ms <= millis,
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
