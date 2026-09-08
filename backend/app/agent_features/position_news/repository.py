"""Durable repository for centrally collected, position-independent news."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update, or_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ... import news as news_mod
from ...db import (
    BrowserNewsPageCache,
    MarketNewsSummary,
    NewsTitleTranslation,
    RunSession,
    TickerNewsAiBudget,
    TickerNewsSnapshot,
    TickerNewsState,
    assert_shared_worker_database,
    database_dialect,
    get_session,
)


_USABLE_STATUSES = {"ready", "degraded", "rate_limited"}
_CLAIM_TIMEOUT_MS = max(
    30,
    int(os.environ.get("POSITION_NEWS_CLAIM_TIMEOUT_SECONDS", "300")),
) * 1000
_DEGRADED_RETRY_MS = max(
    30,
    int(os.environ.get("POSITION_NEWS_DEGRADED_RETRY_SECONDS", "300")),
) * 1000
_MAX_DEGRADED_RETRY_MS = 6 * 60 * 60 * 1000
_TITLE_TRANSLATION_CLAIM_LEASE_MS = max(
    30,
    int(os.environ.get("NEWS_TITLE_TRANSLATION_CLAIM_LEASE_SECONDS", "180")),
) * 1000
_BROWSER_PAGE_CACHE_MAX_ROWS = 512
_BROWSER_PAGE_CACHE_MAX_PAYLOAD_BYTES = 256_000


@dataclass(frozen=True)
class SnapshotClaim:
    status: str
    snapshot_id: int
    claim_token: str = ""
    news_payload: dict | None = None
    had_usable_analysis: bool = False


def _clock(now_ms: int | None = None) -> tuple[int, str]:
    millis = int(now_ms if now_ms is not None else time.time() * 1000)
    stamp = datetime.fromtimestamp(millis / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return millis, stamp


def _iso_millis(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _state(db: Session, asset_symbol: str, now_iso: str) -> TickerNewsState:
    row = db.get(TickerNewsState, asset_symbol)
    if row is not None:
        return row

    candidate = TickerNewsState(asset_symbol=asset_symbol, updated_at=now_iso)
    try:
        # A savepoint keeps a concurrent primary-key insert from invalidating
        # the caller's outer claim transaction.
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate
    except IntegrityError:
        db.expire_all()
        row = db.get(TickerNewsState, asset_symbol)
        if row is None:
            raise
        return row


def _lock_state(
    db: Session,
    *,
    asset_symbol: str,
    now_iso: str,
) -> TickerNewsState:
    """Lock state before any snapshot write to keep one global lock order."""
    _state(db, asset_symbol, now_iso)
    return db.exec(
        select(TickerNewsState)
        .where(TickerNewsState.asset_symbol == asset_symbol)
        .with_for_update()
    ).one()


def _decoded_news(row: TickerNewsSnapshot) -> dict | None:
    try:
        value = json.loads(row.news_json)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _next_observation_seq(
    db: Session,
    *,
    asset_symbol: str,
    observed_ms: int,
    observed_at: str,
) -> int:
    """Issue one per-asset observation sequence inside the caller transaction."""
    _state(db, asset_symbol, observed_at)
    result = db.exec(
        update(TickerNewsState)
        .where(TickerNewsState.asset_symbol == asset_symbol)
        .values(
            observation_seq=TickerNewsState.observation_seq + 1,
            last_attempt_at=observed_at,
            last_attempt_ms=observed_ms,
            updated_at=observed_at,
        )
        .returning(TickerNewsState.observation_seq)
    )
    return int(result.scalar_one())


def _observe_snapshot(
    db: Session,
    snapshot_id: int,
    *,
    observation_seq: int,
    observed_ms: int,
    observed_at: str,
    news_payload: dict | None = None,
) -> bool:
    values = {
        "last_observation_seq": observation_seq,
        "last_observed_ms": observed_ms,
        "last_observed_at": observed_at,
    }
    if news_payload is not None:
        values.update({
            "coin_name": str(news_payload.get("coin_name") or ""),
            "query": str(news_payload.get("query") or ""),
            "news_json": json.dumps(news_payload, ensure_ascii=False),
            "item_count": len(news_payload.get("items") or []),
        })
    result = db.exec(
        update(TickerNewsSnapshot)
        .where(
            TickerNewsSnapshot.id == snapshot_id,
            TickerNewsSnapshot.last_observation_seq < observation_seq,
        )
        .values(**values)
    )
    return result.rowcount == 1


def _record_success(
    db: Session,
    *,
    asset_symbol: str,
    snapshot_id: int,
    observation_seq: int,
    observed_ms: int,
    now_iso: str,
) -> bool:
    _state(db, asset_symbol, now_iso)
    result = db.exec(
        update(TickerNewsState)
        .where(
            TickerNewsState.asset_symbol == asset_symbol,
            TickerNewsState.latest_observation_seq < observation_seq,
        )
        .values(
            latest_snapshot_id=snapshot_id,
            latest_observation_seq=observation_seq,
            latest_observed_ms=observed_ms,
            collection_status="ready",
            last_error="",
            consecutive_failures=0,
            last_attempt_at=now_iso,
            last_attempt_ms=observed_ms,
            last_success_at=now_iso,
            last_success_ms=observed_ms,
            updated_at=now_iso,
        )
    )
    return result.rowcount == 1


def _retry_delay_ms(attempts: int) -> int:
    exponent = max(0, min(12, attempts - 1))
    return min(_MAX_DEGRADED_RETRY_MS, _DEGRADED_RETRY_MS * (2 ** exponent))


def discover_tracked_symbols(
    db: Session | None = None,
    *, due_only: bool = False, bootstrap_only: bool = False,
    now_ms: int | None = None,
) -> list[str]:
    """Return canonical assets backed by a live runner heartbeat."""
    selection = discover_ticker_selection(db, bootstrap_only=bootstrap_only, now_ms=now_ms)
    return selection["due" if due_only else "eligible"]


def discover_ticker_selection(
    db: Session | None = None, *, bootstrap_only: bool = False,
    now_ms: int | None = None,
) -> dict[str, list[str]]:
    """Read live and due assets together so deferred work stays observable."""
    if db is None:
        with get_session() as owned:
            return discover_ticker_selection(owned, bootstrap_only=bootstrap_only, now_ms=now_ms)

    assets: set[str] = set()
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    active_window_ms = max(
        30,
        int(os.environ.get("POSITION_NEWS_ACTIVE_SESSION_SECONDS", "60")),
    ) * 1000
    running_sessions = db.exec(
        select(RunSession.symbol, RunSession.last_heartbeat_at).where(
            RunSession.status == "running",
            RunSession.last_heartbeat_at >= _clock(now_ms - active_window_ms)[1],
        )
    ).all()
    assets.update(
        asset
        for row in running_sessions
        if now_ms - active_window_ms
        <= _iso_millis(row.last_heartbeat_at)
        <= now_ms + 30_000
        if (asset := news_mod.asset_from_market_symbol(row.symbol))
    )

    if not assets:
        return {"active": [], "eligible": [], "due": []}
    states = db.exec(
        select(TickerNewsState).where(
            TickerNewsState.asset_symbol.in_(sorted(assets))
        )
    ).all()
    last_attempt_by_asset = {
        row.asset_symbol: int(row.last_attempt_ms or 0)
        for row in states
    }
    def order(symbol):
        return (last_attempt_by_asset.get(symbol, 0), symbol)

    active = sorted(assets, key=order)
    if bootstrap_only:
        stale_before = now_ms - max(
            120, int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")) * 2,
        ) * 1000
        # Empty/error RSS bootstrap still counts as an attempt. Without this
        # grace period the web's five-second scanner repeatedly wins the lease
        # before Prefect's one-minute schedule can try independent sources.
        assets.difference_update(
            row.asset_symbol for row in states
            if (row.latest_snapshot_id is not None and row.last_success_ms > stale_before)
            or (row.last_attempt_ms > 0 and row.last_attempt_ms > stale_before)
        )
    eligible = sorted(assets, key=order)
    assets.difference_update(
        row.asset_symbol for row in states
        if row.next_collection_ms > now_ms or (
            row.collection_claim_token
            and row.collection_claimed_ms > now_ms - _CLAIM_TIMEOUT_MS
        )
    )
    return {"active": active, "eligible": eligible, "due": sorted(assets, key=order)}


def claim_collection(asset_symbol: str, *, now_ms=None, db=None) -> str | None:
    """Claim before RSS I/O, with CAS fencing across web/Prefect workers."""
    if db is None:
        with get_session() as owned:
            return claim_collection(asset_symbol, now_ms=now_ms, db=owned)
    asset = news_mod.canonical_asset_symbol(asset_symbol)
    if not asset:
        return None
    millis, stamp = _clock(now_ms)
    _state(db, asset, stamp)
    token = uuid.uuid4().hex
    result = db.exec(update(TickerNewsState).where(
        TickerNewsState.asset_symbol == asset,
        TickerNewsState.next_collection_ms <= millis,
        or_(TickerNewsState.collection_claim_token == "",
            TickerNewsState.collection_claimed_ms <= millis - _CLAIM_TIMEOUT_MS),
    ).values(collection_claim_token=token, collection_claimed_ms=millis,
             last_attempt_at=stamp, last_attempt_ms=millis))
    db.commit()
    return token if result.rowcount == 1 else None


def finish_collection(asset_symbol: str, token: str, *, now_ms=None, db=None,
                      next_delay_seconds: int | None = None) -> bool:
    if db is None:
        with get_session() as owned:
            return finish_collection(asset_symbol, token, now_ms=now_ms, db=owned,
                                     next_delay_seconds=next_delay_seconds)
    millis, _ = _clock(now_ms)
    row = db.get(TickerNewsState, asset_symbol)
    if row is None or not token or row.collection_claim_token != token:
        return False
    delay = max(60, int(os.environ.get("POSITION_NEWS_COLLECTION_SECONDS", "300")))
    if next_delay_seconds is not None:
        # A web bootstrap publishes fast RSS, then yields the same durable
        # lease even when empty so the scheduled worker can try more sources.
        delay = max(0, int(next_delay_seconds))
    elif row.collection_status in {"error", "empty", "pending"}:
        delay = min(delay, 60 * 2 ** min(4, max(0, row.consecutive_failures - 1)))
    result = db.exec(update(TickerNewsState).where(
        TickerNewsState.asset_symbol == asset_symbol,
        TickerNewsState.collection_claim_token == token,
    ).values(collection_claim_token="", next_collection_ms=millis + delay * 1000))
    db.commit()
    return result.rowcount == 1


def renew_collection(asset_symbol: str, token: str, *, now_ms=None, db=None) -> bool:
    """Fence the slow final stage after a queue of fast RSS publications."""
    if not token:
        return False
    if db is None:
        with get_session() as owned:
            return renew_collection(asset_symbol, token, now_ms=now_ms, db=owned)
    millis, _ = _clock(now_ms)
    result = db.exec(update(TickerNewsState).where(
        TickerNewsState.asset_symbol == asset_symbol,
        TickerNewsState.collection_claim_token == token,
        TickerNewsState.collection_claimed_ms > millis - _CLAIM_TIMEOUT_MS,
    ).values(collection_claimed_ms=millis))
    db.commit()
    return result.rowcount == 1


def get_collection_state(symbol: str, db=None) -> dict | None:
    if db is None:
        with get_session() as owned:
            return get_collection_state(symbol, owned)
    row = db.get(TickerNewsState, news_mod.canonical_asset_symbol(symbol))
    if row is None:
        return None
    return {
        "status": row.collection_status,
        "last_attempt_at": row.last_attempt_at,
        "last_success_at": row.last_success_at,
        "last_success_ms": row.last_success_ms,
        "consecutive_failures": row.consecutive_failures,
        "next_collection_ms": row.next_collection_ms,
        "last_error": "최근 뉴스 수집에 실패했어요. 자동으로 재시도합니다." if row.last_error else "",
    }


def reserve_ai_budget(
    *,
    daily_limit: int,
    namespace: str = "",
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    """Atomically reserve one namespaced KST-day call across all instances."""
    if daily_limit <= 0:
        return False
    if db is None:
        with get_session() as owned:
            return reserve_ai_budget(
                daily_limit=daily_limit,
                namespace=namespace,
                now_ms=now_ms,
                db=owned,
            )

    millis, now_iso = _clock(now_ms)
    day = datetime.fromtimestamp(
        millis / 1000,
        timezone.utc,
    ).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    budget_namespace = str(namespace or "").strip()
    budget_key = f"{budget_namespace}:{day}" if budget_namespace else day

    result = db.exec(
        update(TickerNewsAiBudget)
        .where(
            TickerNewsAiBudget.budget_date_kst == budget_key,
            TickerNewsAiBudget.used < daily_limit,
        )
        .values(
            used=TickerNewsAiBudget.used + 1,
            updated_at=now_iso,
        )
    )
    if result.rowcount == 1:
        db.commit()
        return True
    db.rollback()

    try:
        db.add(
            TickerNewsAiBudget(
                budget_date_kst=budget_key,
                used=1,
                updated_at=now_iso,
            )
        )
        db.commit()
        return True
    except IntegrityError:
        db.rollback()

    result = db.exec(
        update(TickerNewsAiBudget)
        .where(
            TickerNewsAiBudget.budget_date_kst == budget_key,
            TickerNewsAiBudget.used < daily_limit,
        )
        .values(
            used=TickerNewsAiBudget.used + 1,
            updated_at=now_iso,
        )
    )
    reserved = result.rowcount == 1
    db.commit()
    return reserved


def reserve_news_api_budget(
    *, daily_limit: int, total_limit: int, now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    """Reserve one CoinDesk HTTP call against daily and lifetime app limits.

    Both counters commit together. A rejected lifetime reservation cannot spend
    the daily allowance, and concurrent workers cannot exceed either ceiling.
    These counters are separate from paid model budgets and never auto-reset
    when an API key rotates. Provider/account-wide limits still apply.
    """
    if daily_limit <= 0 or total_limit <= 0:
        return False
    if db is None:
        with get_session() as owned:
            return reserve_news_api_budget(daily_limit=daily_limit,
                total_limit=total_limit, now_ms=now_ms, db=owned)
    millis, now_iso = _clock(now_ms)
    day = datetime.fromtimestamp(millis / 1000, timezone.utc).astimezone(
        timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    insert = postgres_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    for key, limit in (("coindesk_news:lifetime", total_limit),
                       (f"coindesk_news:{day}", daily_limit)):
        statement = insert(TickerNewsAiBudget).values(
            budget_date_kst=key, used=1, updated_at=now_iso,
        ).on_conflict_do_update(
            index_elements=[TickerNewsAiBudget.budget_date_kst],
            set_={"used": TickerNewsAiBudget.used + 1, "updated_at": now_iso},
            where=TickerNewsAiBudget.used < limit,
        )
        if db.exec(statement).rowcount != 1:
            db.rollback()
            return False
    db.commit()
    return True


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def get_title_translations(
    titles: list[str],
    db: Session | None = None,
) -> dict[str, str]:
    """Return durable translations for exact normalized source titles."""
    if db is None:
        with get_session() as owned:
            return get_title_translations(titles, db=owned)

    unique_titles = list(
        dict.fromkeys(
            str(title or "").strip()
            for title in titles
            if str(title or "").strip()
        )
    )
    if not unique_titles:
        return {}
    requested = set(unique_titles)
    hashes = [_title_hash(title) for title in unique_titles]
    rows = db.exec(
        select(NewsTitleTranslation).where(
            NewsTitleTranslation.title_hash.in_(hashes),
            NewsTitleTranslation.processing_status == "ready",
        )
    ).all()
    return {
        row.original_title: row.translated_title
        for row in rows
        if row.original_title in requested and row.translated_title
    }


def claim_title_translations(
    titles: list[str],
    *,
    rejected_titles: list[str] | None = None,
    lease_ms: int = _TITLE_TRANSLATION_CLAIM_LEASE_MS,
    retry_ms: int = 300_000,
    now_ms: int | None = None,
    db: Session | None = None,
) -> dict:
    """Claim uncached titles before AI so Render instances cannot double-charge."""
    if db is None:
        with get_session() as owned:
            return claim_title_translations(
                titles,
                rejected_titles=rejected_titles,
                lease_ms=lease_ms,
                retry_ms=retry_ms,
                now_ms=now_ms,
                db=owned,
            )

    unique_titles = list(
        dict.fromkeys(
            str(title or "").strip()
            for title in titles
            if str(title or "").strip()
        )
    )
    rejected = {
        str(title or "").strip()
        for title in (rejected_titles or [])
        if str(title or "").strip()
    }
    millis, now_iso = _clock(now_ms)
    stale_before = millis - max(1, int(lease_ms))
    claim_token = uuid.uuid4().hex
    claimed: list[str] = []
    waiting: list[str] = []
    deferred: list[str] = []
    cached: dict[str, str] = {}

    for title in unique_titles:
        title_hash = _title_hash(title)
        row = db.get(NewsTitleTranslation, title_hash)
        if row is None:
            candidate = NewsTitleTranslation(
                title_hash=title_hash,
                original_title=title,
                processing_status="pending",
                claim_token=claim_token,
                claimed_ms=millis,
                updated_at=now_iso,
                updated_ms=millis,
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                claimed.append(title)
                continue
            except IntegrityError:
                db.expire_all()
                row = db.get(NewsTitleTranslation, title_hash)

        if row is None or row.original_title != title:
            waiting.append(title)
            continue
        if (
            row.processing_status == "ready"
            and row.translated_title
            and title not in rejected
        ):
            cached[title] = row.translated_title
            continue

        # A rejected/failed title must not consume a fresh paid batch on every
        # page refresh. Unlike an active claim, this is not work to wait for.
        if (row.processing_status == "error" and title not in rejected
                and int(row.updated_ms or 0) + max(0, int(retry_ms)) > millis):
            deferred.append(title)
            continue

        can_reclaim = (
            title in rejected
            or row.processing_status != "pending"
            or int(row.claimed_ms or 0) <= stale_before
        )
        if not can_reclaim:
            waiting.append(title)
            continue
        previous_token = row.claim_token
        previous_claimed_ms = int(row.claimed_ms or 0)
        result = db.exec(
            update(NewsTitleTranslation)
            .where(
                NewsTitleTranslation.title_hash == title_hash,
                NewsTitleTranslation.original_title == title,
                NewsTitleTranslation.claim_token == previous_token,
                NewsTitleTranslation.claimed_ms == previous_claimed_ms,
            )
            .values(
                translated_title="",
                processing_status="pending",
                claim_token=claim_token,
                claimed_ms=millis,
                updated_at=now_iso,
                updated_ms=millis,
            )
        )
        if result.rowcount == 1:
            claimed.append(title)
        else:
            waiting.append(title)

    db.commit()
    return {
        "claim_token": claim_token if claimed else "",
        "claimed": claimed,
        "waiting": waiting,
        "deferred": deferred,
        "cached": cached,
    }


def store_title_translations(
    translations: dict[str, str],
    *,
    claim_token: str = "",
    now_ms: int | None = None,
    db: Session | None = None,
) -> None:
    """Upsert validated title translations for cross-process reuse."""
    if db is None:
        with get_session() as owned:
            store_title_translations(
                translations,
                claim_token=claim_token,
                now_ms=now_ms,
                db=owned,
            )
            return

    millis, now_iso = _clock(now_ms)
    for raw_original, raw_translated in translations.items():
        original = str(raw_original or "").strip()
        translated = str(raw_translated or "").strip()
        if not original or not translated:
            continue
        title_hash = _title_hash(original)
        if claim_token:
            db.exec(
                update(NewsTitleTranslation)
                .where(
                    NewsTitleTranslation.title_hash == title_hash,
                    NewsTitleTranslation.original_title == original,
                    NewsTitleTranslation.processing_status == "pending",
                    NewsTitleTranslation.claim_token == claim_token,
                )
                .values(
                    translated_title=translated,
                    processing_status="ready",
                    claim_token="",
                    claimed_ms=0,
                    updated_at=now_iso,
                    updated_ms=millis,
                )
            )
            continue
        row = db.get(NewsTitleTranslation, title_hash)
        if row is None:
            candidate = NewsTitleTranslation(
                title_hash=title_hash,
                original_title=original,
                translated_title=translated,
                processing_status="ready",
                updated_at=now_iso,
                updated_ms=millis,
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                continue
            except IntegrityError:
                db.expire_all()
                row = db.get(NewsTitleTranslation, title_hash)
        if row is None or row.original_title != original:
            continue
        row.translated_title = translated
        row.processing_status = "ready"
        row.claim_token = ""
        row.claimed_ms = 0
        row.updated_at = now_iso
        row.updated_ms = millis
        db.add(row)
    db.commit()


def renew_title_translation_claims(
    titles: list[str],
    *,
    claim_token: str,
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    """Refresh one fenced claim before each potentially paid AI request."""
    if not claim_token or not titles:
        return False
    if db is None:
        with get_session() as owned:
            return renew_title_translation_claims(
                titles,
                claim_token=claim_token,
                now_ms=now_ms,
                db=owned,
            )

    unique_titles = list(
        dict.fromkeys(
            str(title or "").strip()
            for title in titles
            if str(title or "").strip()
        )
    )
    if not unique_titles:
        return False
    millis, now_iso = _clock(now_ms)
    hashes = [_title_hash(title) for title in unique_titles]
    result = db.exec(
        update(NewsTitleTranslation)
        .where(
            NewsTitleTranslation.title_hash.in_(hashes),
            NewsTitleTranslation.processing_status == "pending",
            NewsTitleTranslation.claim_token == claim_token,
        )
        .values(
            claimed_ms=millis,
            updated_at=now_iso,
            updated_ms=millis,
        )
    )
    renewed = result.rowcount == len(hashes)
    db.commit()
    return renewed


def release_title_translation_claims(
    titles: list[str],
    *,
    claim_token: str,
    retry_immediately: bool = False,
    now_ms: int | None = None,
    db: Session | None = None,
) -> None:
    """Release claims, distinguishing local capacity from a provider failure."""
    if not claim_token or not titles:
        return
    if db is None:
        with get_session() as owned:
            release_title_translation_claims(
                titles,
                claim_token=claim_token,
                retry_immediately=retry_immediately,
                now_ms=now_ms,
                db=owned,
            )
            return
    millis, now_iso = _clock(now_ms)
    hashes = [_title_hash(str(title or "").strip()) for title in titles]
    db.exec(
        update(NewsTitleTranslation)
        .where(
            NewsTitleTranslation.title_hash.in_(hashes),
            NewsTitleTranslation.processing_status == "pending",
            NewsTitleTranslation.claim_token == claim_token,
        )
        .values(
            processing_status="retryable" if retry_immediately else "error",
            claim_token="",
            claimed_ms=0,
            updated_at=now_iso,
            updated_ms=millis,
        )
    )
    db.commit()


def _refresh_reused_payload(stored: dict, incoming: dict) -> dict:
    """Keep article/assessment ordering, but observe new summaries by body ID."""
    def identity(item):
        if item.get("content_type") != "community" or not item.get("community_post_id"):
            return None
        return (str(item["community_post_id"]), str(item.get("community_body_hash") or ""),
                str(item.get("source") or ""), str(item.get("url") or ""))

    updates = {identity(item): item for item in incoming.get("items") or [] if identity(item) is not None}
    items = []
    for old in stored.get("items") or []:
        current = updates.get(identity(old))
        summary_fields = ({key: current[key] for key in (
            "community_summary", "community_summary_status", "community_summary_partial",
        ) if key in current} if current else {})
        items.append({**old, **summary_fields})
    return {**incoming, "items": items, "updated_at": stored.get("updated_at")}


def claim_snapshot(
    *,
    asset_symbol: str,
    snapshot_key: str,
    news_payload: dict,
    prompt_version: str,
    model: str,
    retry_incomplete: bool,
    now_ms: int | None = None,
    db: Session | None = None,
) -> SnapshotClaim:
    """Atomically claim unique work before AI and return a fencing token."""
    if db is None:
        with get_session() as owned:
            return claim_snapshot(
                asset_symbol=asset_symbol,
                snapshot_key=snapshot_key,
                news_payload=news_payload,
                prompt_version=prompt_version,
                model=model,
                retry_incomplete=retry_incomplete,
                now_ms=now_ms,
                db=owned,
            )

    millis, now_iso = _clock(now_ms)
    asset = news_mod.canonical_asset_symbol(asset_symbol)
    if not asset:
        raise ValueError("invalid asset symbol")

    observation_seq = _next_observation_seq(
        db,
        asset_symbol=asset,
        observed_ms=millis,
        observed_at=now_iso,
    )

    existing = db.exec(
        select(TickerNewsSnapshot).where(
            TickerNewsSnapshot.snapshot_key == snapshot_key
        )
    ).first()
    if existing is not None:
        if existing.asset_symbol != asset:
            db.rollback()
            raise ValueError("snapshot key belongs to another asset")
        usable = (
            existing.processing_status in _USABLE_STATUSES
            and bool(existing.analysis_json)
            and _decoded_news(existing) is not None
        )
        should_retry = False
        if existing.processing_status == "degraded":
            should_retry = (
                retry_incomplete
                and millis >= int(existing.next_retry_ms or 0)
            )
        elif existing.processing_status == "rate_limited":
            should_retry = retry_incomplete

        recent_refresh = (
            usable
            and bool(existing.claim_token)
            and millis - int(existing.claimed_ms or 0) < _CLAIM_TIMEOUT_MS
        )
        if usable and (not should_retry or recent_refresh):
            observed = _observe_snapshot(
                db,
                int(existing.id),
                observation_seq=observation_seq,
                observed_ms=millis,
                observed_at=now_iso,
                # Preserve article/analysis order while new body summaries arrive.
                news_payload=_refresh_reused_payload(_decoded_news(existing) or {}, news_payload),
            )
            if not observed:
                db.rollback()
                return claim_snapshot(
                    asset_symbol=asset,
                    snapshot_key=snapshot_key,
                    news_payload=news_payload,
                    prompt_version=prompt_version,
                    model=model,
                    retry_incomplete=retry_incomplete,
                    now_ms=millis,
                    db=db,
                )
            _record_success(
                db,
                asset_symbol=asset,
                snapshot_id=int(existing.id),
                observation_seq=observation_seq,
                observed_ms=millis,
                now_iso=now_iso,
            )
            db.commit()
            return SnapshotClaim(
                "reused",
                int(existing.id),
                news_payload=news_payload,
                had_usable_analysis=True,
            )

        recent_pending = (
            existing.processing_status == "pending"
            and millis - int(existing.claimed_ms or 0) < _CLAIM_TIMEOUT_MS
        )
        if recent_pending:
            observed = _observe_snapshot(
                db,
                int(existing.id),
                observation_seq=observation_seq,
                observed_ms=millis,
                observed_at=now_iso,
                news_payload=None,
            )
            if not observed:
                db.rollback()
                return claim_snapshot(
                    asset_symbol=asset,
                    snapshot_key=snapshot_key,
                    news_payload=news_payload,
                    prompt_version=prompt_version,
                    model=model,
                    retry_incomplete=retry_incomplete,
                    now_ms=millis,
                    db=db,
                )
            db.commit()
            return SnapshotClaim("pending", int(existing.id))

        token = uuid.uuid4().hex
        stored_payload = _decoded_news(existing) if usable else None
        if stored_payload:
            stored_payload = _refresh_reused_payload(stored_payload, news_payload)
        values = {
            "claim_token": token,
            "claimed_at": now_iso,
            "claimed_ms": millis,
            "last_observed_at": now_iso,
            "last_observed_ms": millis,
            "last_observation_seq": observation_seq,
            "coin_name": str(news_payload.get("coin_name") or asset),
            "query": str(news_payload.get("query") or ""),
            "news_json": json.dumps(stored_payload or news_payload, ensure_ascii=False),
            "item_count": len(news_payload.get("items") or []),
        }
        if not usable:
            values.update({
                "processing_status": "pending",
                "analysis_status": "pending",
                "analysis_source": "",
                "analysis_json": "",
                "completed_at": "",
                "completed_ms": 0,
            })

        result = db.exec(
            update(TickerNewsSnapshot)
            .where(
                TickerNewsSnapshot.id == existing.id,
                TickerNewsSnapshot.processing_status == existing.processing_status,
                TickerNewsSnapshot.claim_token == (existing.claim_token or ""),
                TickerNewsSnapshot.claimed_ms == existing.claimed_ms,
                TickerNewsSnapshot.completed_ms == existing.completed_ms,
                TickerNewsSnapshot.last_observation_seq
                == existing.last_observation_seq,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            db.rollback()
            return claim_snapshot(
                asset_symbol=asset,
                snapshot_key=snapshot_key,
                news_payload=news_payload,
                prompt_version=prompt_version,
                model=model,
                retry_incomplete=retry_incomplete,
                now_ms=millis,
                db=db,
            )

        if usable:
            _record_success(
                db,
                asset_symbol=asset,
                snapshot_id=int(existing.id),
                observation_seq=observation_seq,
                observed_ms=millis,
                now_iso=now_iso,
            )
        db.commit()
        return SnapshotClaim(
            "claimed",
            int(existing.id),
            claim_token=token,
            news_payload=stored_payload or news_payload,
            had_usable_analysis=usable,
        )

    token = uuid.uuid4().hex
    snapshot = TickerNewsSnapshot(
        snapshot_key=snapshot_key,
        asset_symbol=asset,
        coin_name=str(news_payload.get("coin_name") or asset),
        query=str(news_payload.get("query") or ""),
        news_json=json.dumps(news_payload, ensure_ascii=False),
        item_count=len(news_payload.get("items") or []),
        processing_status="pending",
        analysis_status="pending",
        prompt_version=prompt_version,
        model=model,
        collected_at=now_iso,
        collected_ms=millis,
        claimed_at=now_iso,
        claimed_ms=millis,
        claim_token=token,
        last_observed_at=now_iso,
        last_observed_ms=millis,
        last_observation_seq=observation_seq,
    )
    db.add(snapshot)
    try:
        db.commit()
        db.refresh(snapshot)
        return SnapshotClaim(
            "claimed",
            int(snapshot.id),
            claim_token=token,
            news_payload=news_payload,
        )
    except IntegrityError:
        db.rollback()
        return claim_snapshot(
            asset_symbol=asset,
            snapshot_key=snapshot_key,
            news_payload=news_payload,
            prompt_version=prompt_version,
            model=model,
            retry_incomplete=retry_incomplete,
            now_ms=millis,
            db=db,
        )


def release_usable_claim(
    snapshot_id: int,
    claim_token: str,
    *,
    db: Session | None = None,
) -> bool:
    """Release a refresh lease without touching the last usable analysis."""
    if db is None:
        with get_session() as owned:
            return release_usable_claim(snapshot_id, claim_token, db=owned)
    if not claim_token:
        return False
    result = db.exec(
        update(TickerNewsSnapshot)
        .where(
            TickerNewsSnapshot.id == snapshot_id,
            TickerNewsSnapshot.claim_token == claim_token,
            TickerNewsSnapshot.processing_status.in_(_USABLE_STATUSES),
        )
        .values(claim_token="")
    )
    released = result.rowcount == 1
    db.commit()
    return released


def complete_snapshot(
    snapshot_id: int,
    analysis: dict,
    *,
    claim_token: str,
    news_payload: dict | None = None,
    keep_claim: bool = False,
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    if db is None:
        with get_session() as owned:
            return complete_snapshot(
                snapshot_id,
                analysis,
                claim_token=claim_token,
                news_payload=news_payload,
                keep_claim=keep_claim,
                now_ms=now_ms,
                db=owned,
            )
    if not claim_token:
        return False

    millis, now_iso = _clock(now_ms)
    row = db.get(TickerNewsSnapshot, snapshot_id)
    if row is None or row.claim_token != claim_token:
        return False
    _lock_state(
        db,
        asset_symbol=row.asset_symbol,
        now_iso=now_iso,
    )

    analysis_status = str(analysis.get("analysis_status") or "degraded")
    processing_status = (
        analysis_status
        if analysis_status in {"degraded", "rate_limited"}
        else "ready"
    )
    attempts = int(row.analysis_attempts or 0)
    if analysis_status == "degraded":
        attempts += 1
        next_retry_ms = millis + _retry_delay_ms(attempts)
    elif analysis_status == "ready":
        attempts = 0
        next_retry_ms = 0
    else:
        next_retry_ms = 0

    result = db.exec(
        update(TickerNewsSnapshot)
        .where(
            TickerNewsSnapshot.id == snapshot_id,
            TickerNewsSnapshot.claim_token == claim_token,
        )
        .values(
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            processing_status=processing_status,
            analysis_status=analysis_status,
            analysis_source=str(analysis.get("analysis_source") or "rule"),
            analysis_attempts=attempts,
            next_retry_ms=next_retry_ms,
            completed_at=now_iso,
            completed_ms=millis,
            claim_token=claim_token if keep_claim else "",
            **({"news_json": json.dumps(news_payload, ensure_ascii=False)} if news_payload is not None else {}),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False

    db.flush()
    db.expire_all()
    completed = db.get(TickerNewsSnapshot, snapshot_id)
    if completed is None:
        db.rollback()
        return False
    _record_success(
        db,
        asset_symbol=completed.asset_symbol,
        snapshot_id=snapshot_id,
        observation_seq=int(completed.last_observation_seq or 0),
        observed_ms=int(completed.last_observed_ms or completed.collected_ms),
        now_iso=now_iso,
    )
    db.commit()
    return True


def fail_snapshot(
    snapshot_id: int,
    error: str,
    *,
    claim_token: str,
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    if db is None:
        with get_session() as owned:
            return fail_snapshot(
                snapshot_id,
                error,
                claim_token=claim_token,
                now_ms=now_ms,
                db=owned,
            )
    if not claim_token:
        return False

    millis, now_iso = _clock(now_ms)
    row = db.get(TickerNewsSnapshot, snapshot_id)
    if row is None or row.claim_token != claim_token:
        return False
    _lock_state(
        db,
        asset_symbol=row.asset_symbol,
        now_iso=now_iso,
    )
    usable = row.processing_status in _USABLE_STATUSES and bool(row.analysis_json)
    attempts = int(row.analysis_attempts or 0) + 1
    values = {
        "claim_token": "",
        "analysis_attempts": attempts,
        "next_retry_ms": millis + _retry_delay_ms(attempts),
    }
    if not usable:
        values.update({
            "processing_status": "error",
            "analysis_status": "error",
            "completed_at": now_iso,
            "completed_ms": millis,
        })

    result = db.exec(
        update(TickerNewsSnapshot)
        .where(
            TickerNewsSnapshot.id == snapshot_id,
            TickerNewsSnapshot.claim_token == claim_token,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.rollback()
        return False

    db.exec(
        update(TickerNewsState)
        .where(
            TickerNewsState.asset_symbol == row.asset_symbol,
            TickerNewsState.observation_seq
            == int(row.last_observation_seq or 0),
        )
        .values(
            collection_status="error",
            last_error=str(error or "analysis failed")[:200],
            consecutive_failures=TickerNewsState.consecutive_failures + 1,
            last_attempt_at=now_iso,
            last_attempt_ms=millis,
            updated_at=now_iso,
        )
    )
    db.commit()
    return True


def mark_collection_outcome(
    asset_symbol: str,
    status: str,
    *,
    error: str = "",
    now_ms: int | None = None,
    db: Session | None = None,
) -> None:
    """Record an empty/error attempt without replacing the last good pointer."""
    if db is None:
        with get_session() as owned:
            mark_collection_outcome(
                asset_symbol,
                status,
                error=error,
                now_ms=now_ms,
                db=owned,
            )
            return

    millis, now_iso = _clock(now_ms)
    asset = news_mod.canonical_asset_symbol(asset_symbol)
    if not asset:
        return
    observation_seq = _next_observation_seq(
        db,
        asset_symbol=asset,
        observed_ms=millis,
        observed_at=now_iso,
    )
    values = {
        "collection_status": status,
        "last_error": str(error or "")[:200],
        "last_attempt_at": now_iso,
        "last_attempt_ms": millis,
        "updated_at": now_iso,
    }
    if status == "error":
        values["consecutive_failures"] = (
            TickerNewsState.consecutive_failures + 1
        )
    result = db.exec(
        update(TickerNewsState)
        .where(
            TickerNewsState.asset_symbol == asset,
            TickerNewsState.observation_seq == observation_seq,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("ticker news observation was superseded")
    db.commit()


def get_latest_snapshot(
    symbol: str,
    db: Session | None = None,
) -> dict | None:
    if db is None:
        with get_session() as owned:
            return get_latest_snapshot(symbol, owned)

    asset = news_mod.canonical_asset_symbol(symbol)
    state = db.get(TickerNewsState, asset) if asset else None
    if state is None or state.latest_snapshot_id is None:
        return None
    row = db.get(TickerNewsSnapshot, state.latest_snapshot_id)
    if row is None or row.processing_status not in _USABLE_STATUSES:
        return None
    try:
        news_payload = json.loads(row.news_json)
        analysis = json.loads(row.analysis_json)
    except (TypeError, json.JSONDecodeError):
        return None
    return {
        "snapshot_id": row.snapshot_key,
        "news_payload": news_payload,
        "analysis": analysis,
        "collection": {
            "status": state.collection_status,
            "last_attempt_at": state.last_attempt_at,
            "last_success_at": state.last_success_at,
            "last_success_ms": state.last_success_ms,
            "consecutive_failures": state.consecutive_failures,
            "last_error": (
                "최근 중앙 수집에 실패했어요."
                if state.last_error
                else ""
            ),
        },
    }


def prune_snapshots(
    *,
    retention_days: int,
    now_ms: int | None = None,
    db: Session | None = None,
) -> int:
    if db is None:
        with get_session() as owned:
            return prune_snapshots(
                retention_days=retention_days,
                now_ms=now_ms,
                db=owned,
            )

    millis, _ = _clock(now_ms)
    cutoff = millis - max(1, retention_days) * 86_400_000
    protected = select(TickerNewsState.latest_snapshot_id).where(
        TickerNewsState.latest_snapshot_id.is_not(None)
    )
    result = db.exec(
        delete(TickerNewsSnapshot).where(
            TickerNewsSnapshot.collected_ms < cutoff,
            TickerNewsSnapshot.last_observed_ms < cutoff,
            TickerNewsSnapshot.processing_status != "pending",
            TickerNewsSnapshot.claim_token == "",
            TickerNewsSnapshot.id.not_in(protected),
        )
    )
    removed = int(result.rowcount or 0)
    db.commit()
    return removed


def assert_worker_database() -> None:
    """Compatibility wrapper for existing callers and dialect test overrides."""
    assert_shared_worker_database(dialect=database_dialect())


def load_market_news_summary(
    summary_key: str,
    *,
    prompt_version: str = "",
    db: Session | None = None,
) -> str | None:
    """그날 시장 요약을 배포·인스턴스 사이에서 재사용한다. 프롬프트가 바뀌면 무시한다."""
    if db is None:
        with get_session() as owned:
            return load_market_news_summary(
                summary_key, prompt_version=prompt_version, db=owned
            )
    row = db.get(MarketNewsSummary, str(summary_key or "").strip())
    if row is None or not row.overview:
        return None
    if prompt_version and row.prompt_version != prompt_version:
        return None
    return row.overview


def store_market_news_summary(
    summary_key: str,
    overview: str,
    *,
    prompt_version: str = "",
    now_ms: int | None = None,
    db: Session | None = None,
) -> None:
    """Upsert the day's market overview so a restart does not pay for it again."""
    if db is None:
        with get_session() as owned:
            store_market_news_summary(
                summary_key,
                overview,
                prompt_version=prompt_version,
                now_ms=now_ms,
                db=owned,
            )
            return
    key = str(summary_key or "").strip()
    text = str(overview or "").strip()
    if not key or not text:
        return
    millis, now_iso = _clock(now_ms)
    row = db.get(MarketNewsSummary, key)
    if row is None:
        row = MarketNewsSummary(summary_key=key)
        db.add(row)
    row.overview = text
    row.prompt_version = prompt_version
    row.updated_at = now_iso
    row.updated_ms = millis
    db.commit()


def load_browser_pages(keys: list[str], *, now_ms=None, db=None) -> dict[str, dict]:
    """One batch read of unexpired public page results, independent of memory."""
    requested = sorted({key for key in keys if isinstance(key, str) and 0 < len(key) <= 512})[:_BROWSER_PAGE_CACHE_MAX_ROWS]
    if not requested:
        return {}
    if db is None:
        with get_session() as owned:
            return load_browser_pages(requested, now_ms=now_ms, db=owned)
    millis, _ = _clock(now_ms)
    rows = db.exec(select(BrowserNewsPageCache).where(
        BrowserNewsPageCache.cache_key.in_(requested),
        BrowserNewsPageCache.expires_ms > millis,
    )).all()
    results = {}
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            results[row.cache_key] = payload
    return results


def store_browser_pages(entries: dict[str, tuple[dict, int]], *, now_ms=None, db=None) -> None:
    """Atomically share public article metadata; bound rows and payload size."""
    if not entries:
        return
    if db is None:
        with get_session() as owned:
            return store_browser_pages(entries, now_ms=now_ms, db=owned)
    millis, _ = _clock(now_ms)
    values = []
    for key, (payload, expires_ms) in entries.items():
        if not isinstance(key, str) or not 0 < len(key) <= 512 or not isinstance(payload, dict):
            continue
        if expires_ms <= millis:
            continue
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > _BROWSER_PAGE_CACHE_MAX_PAYLOAD_BYTES:
            continue
        values.append({"cache_key": key, "payload_json": serialized,
                       "expires_ms": int(expires_ms), "updated_ms": millis})
        if len(values) >= _BROWSER_PAGE_CACHE_MAX_ROWS:
            break
    if not values:
        return

    # A single atomic batch upsert also handles two processes seeing the same
    # previously uncached public page. Late older results cannot overwrite new.
    insert = postgres_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(BrowserNewsPageCache).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[BrowserNewsPageCache.cache_key],
        set_={name: getattr(statement.excluded, name)
              for name in ("payload_json", "expires_ms", "updated_ms")},
        where=BrowserNewsPageCache.updated_ms <= statement.excluded.updated_ms,
    )
    db.exec(statement)
    db.exec(delete(BrowserNewsPageCache).where(BrowserNewsPageCache.expires_ms <= millis))
    oldest = select(BrowserNewsPageCache.cache_key).order_by(
        BrowserNewsPageCache.updated_ms.desc(), BrowserNewsPageCache.cache_key,
    ).offset(_BROWSER_PAGE_CACHE_MAX_ROWS)
    db.exec(delete(BrowserNewsPageCache).where(BrowserNewsPageCache.cache_key.in_(oldest)))
    db.commit()
