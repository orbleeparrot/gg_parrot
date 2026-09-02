"""Durable repository for centrally collected, position-independent news."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ... import news as news_mod
from ...db import (
    RunSession,
    TickerNewsAiBudget,
    TickerNewsSnapshot,
    TickerNewsState,
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
) -> list[str]:
    """Return canonical assets backed by a live runner heartbeat."""
    if db is None:
        with get_session() as owned:
            return discover_tracked_symbols(owned)

    assets: set[str] = set()
    now_ms = int(time.time() * 1000)
    active_window_ms = max(
        30,
        int(os.environ.get("POSITION_NEWS_ACTIVE_SESSION_SECONDS", "60")),
    ) * 1000
    running_sessions = db.exec(
        select(RunSession).where(RunSession.status == "running")
    ).all()
    assets.update(
        asset
        for row in running_sessions
        if now_ms - active_window_ms
        <= _iso_millis(row.last_heartbeat_at)
        <= now_ms + 30_000
        if (asset := news_mod.asset_from_market_symbol(row.symbol))
    )

    states = db.exec(
        select(TickerNewsState).where(
            TickerNewsState.asset_symbol.in_(sorted(assets))
        )
    ).all()
    last_attempt_by_asset = {
        row.asset_symbol: int(row.last_attempt_ms or 0)
        for row in states
    }
    return sorted(
        assets,
        key=lambda symbol: (last_attempt_by_asset.get(symbol, 0), symbol),
    )


def reserve_ai_budget(
    *,
    daily_limit: int,
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    """Atomically reserve one KST-day model call across all worker instances."""
    if daily_limit <= 0:
        return False
    if db is None:
        with get_session() as owned:
            return reserve_ai_budget(
                daily_limit=daily_limit,
                now_ms=now_ms,
                db=owned,
            )

    millis, now_iso = _clock(now_ms)
    day = datetime.fromtimestamp(
        millis / 1000,
        timezone.utc,
    ).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    result = db.exec(
        update(TickerNewsAiBudget)
        .where(
            TickerNewsAiBudget.budget_date_kst == day,
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
                budget_date_kst=day,
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
            TickerNewsAiBudget.budget_date_kst == day,
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
                news_payload=news_payload,
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
                news_payload=news_payload,
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
        stored_payload = news_payload if usable else None
        values = {
            "claim_token": token,
            "claimed_at": now_iso,
            "claimed_ms": millis,
            "last_observed_at": now_iso,
            "last_observed_ms": millis,
            "last_observation_seq": observation_seq,
            "coin_name": str(news_payload.get("coin_name") or asset),
            "query": str(news_payload.get("query") or ""),
            "news_json": json.dumps(news_payload, ensure_ascii=False),
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
    now_ms: int | None = None,
    db: Session | None = None,
) -> bool:
    if db is None:
        with get_session() as owned:
            return complete_snapshot(
                snapshot_id,
                analysis,
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
            claim_token="",
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
    """Fail closed on Render if the worker is not sharing durable Postgres."""
    default_required = "true" if os.environ.get("RENDER") else "false"
    required = os.environ.get(
        "POSITION_NEWS_REQUIRE_POSTGRES",
        default_required,
    ).strip().lower() in {"1", "true", "yes"}
    if required and database_dialect() != "postgresql":
        raise RuntimeError(
            "중앙 뉴스 워커는 웹 서버와 같은 Postgres DATABASE_URL이 필요합니다."
        )
