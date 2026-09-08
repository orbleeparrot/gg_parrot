"""Durable, fenced work claims for Korean public community-post summaries.

All default calls own short sessions and finish before the caller contacts an AI
provider. Database errors deliberately propagate: a failed claim cannot authorize
paid work. No source body text, account information, or API credentials are stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid

from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import db as db_mod
from .db import CommunityPostSummary, get_session

LEASE_MS = 180_000
RETRY_MS = 300_000
CACHE_TTL_MS = 30 * 86_400_000
_MAX_BATCH = 100
_MAX_LEASE_MS = 600_000


def _millis(now_ms):
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def make_summary_key(*, post_id: str, body_hash: str, prompt_version: str) -> str:
    """Include model in prompt_version when selecting different summary models."""
    post_id, body_hash, prompt_version = str(post_id), str(body_hash), str(prompt_version)
    if (not re.fullmatch(r"[0-9]{1,30}", post_id)
            or not re.fullmatch(r"[a-f0-9]{64}", body_hash)
            or not prompt_version.strip() or len(prompt_version) > 160):
        raise ValueError("Invalid community summary identity")
    material = json.dumps([prompt_version, post_id, body_hash], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _requests(requests: list[dict]) -> dict[str, dict]:
    unique = {}
    for item in requests:
        identity = {name: str(item.get(name) or "") for name in ("post_id", "body_hash", "prompt_version")}
        key = make_summary_key(**identity)
        if item.get("summary_key") != key:
            raise ValueError("Community summary key does not match its identity")
        unique[key] = identity
    if len(unique) > _MAX_BATCH:
        raise ValueError("Community summary batch exceeds 100 items")
    # Every instance acquires contested rows in the same order.
    return dict(sorted(unique.items()))


def _keys(keys) -> list[str]:
    unique = sorted(set(keys))
    if len(unique) > _MAX_BATCH or any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) for key in unique):
        raise ValueError("Invalid community summary keys")
    return unique


def _matches(row, identity):
    return all(getattr(row, name) == value for name, value in identity.items())


def get_summaries(requests: list[dict], *, now_ms: int | None = None, db: Session | None = None) -> dict[str, str]:
    requested = _requests(requests)
    if not requested:
        return {}
    if db is None:
        with get_session() as owned:
            return get_summaries(requests, now_ms=now_ms, db=owned)
    cutoff = _millis(now_ms) - CACHE_TTL_MS
    rows = db.exec(select(CommunityPostSummary).where(
        CommunityPostSummary.summary_key.in_(requested),
        CommunityPostSummary.processing_status == "ready",
        CommunityPostSummary.updated_ms > cutoff,
    )).all()
    return {row.summary_key: row.summary_ko for row in rows
            if row.summary_ko and _matches(row, requested[row.summary_key])}


def claim_summaries(requests: list[dict], *, rejected_keys: list[str] | None = None,
                    lease_ms: int = LEASE_MS, retry_ms: int = RETRY_MS,
                    now_ms: int | None = None, db: Session | None = None) -> dict:
    requested = _requests(requests)
    rejected = set(_keys(rejected_keys or []))
    result = {"claim_token": "", "claimed": [], "waiting": [], "deferred": [], "cached": {}}
    if not requested:
        return result
    if db is None:
        with get_session() as owned:
            return claim_summaries(requests, rejected_keys=rejected_keys, lease_ms=lease_ms,
                                   retry_ms=retry_ms, now_ms=now_ms, db=owned)
    if ((os.environ.get("DATABASE_URL", "").strip() or db_mod._DATABASE_URL)
            and db.get_bind().dialect.name != "postgresql"):
        raise RuntimeError("Configured shared database is unavailable for community summary claims")
    millis = _millis(now_ms)
    stale_before = millis - max(30_000, min(_MAX_LEASE_MS, int(lease_ms)))
    retry_ms = max(0, min(3_600_000, int(retry_ms)))
    token = uuid.uuid4().hex
    rows = {row.summary_key: row for row in db.exec(select(CommunityPostSummary).where(
        CommunityPostSummary.summary_key.in_(requested))).all()}
    for key, identity in requested.items():
        row = rows.get(key)
        if row is None:
            candidate = CommunityPostSummary(summary_key=key, **identity, processing_status="pending",
                                             claim_token=token, claimed_ms=millis, updated_ms=millis)
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                result["claimed"].append(key)
                continue
            except IntegrityError:
                db.expire_all()
                row = db.get(CommunityPostSummary, key)
        if row is None or not _matches(row, identity):
            result["waiting"].append(key)
            continue
        if (row.processing_status == "ready" and row.summary_ko and key not in rejected
                and row.updated_ms > millis - CACHE_TTL_MS):
            result["cached"][key] = row.summary_ko
            continue
        if row.processing_status == "error" and row.updated_ms + retry_ms > millis:
            result["deferred"].append(key)
            continue
        # A caller rejecting an old cached answer must not steal another
        # instance's already active replacement claim.
        if row.processing_status == "pending" and row.claimed_ms > stale_before:
            result["waiting"].append(key)
            continue
        changed = db.exec(update(CommunityPostSummary).where(
            CommunityPostSummary.summary_key == key,
            CommunityPostSummary.claim_token == row.claim_token,
            CommunityPostSummary.claimed_ms == row.claimed_ms,
            CommunityPostSummary.processing_status == row.processing_status,
            CommunityPostSummary.updated_ms == row.updated_ms,
            CommunityPostSummary.summary_ko == row.summary_ko,
        ).values(summary_ko="", processing_status="pending", claim_token=token,
                 claimed_ms=millis, updated_ms=millis))
        result["claimed" if changed.rowcount == 1 else "waiting"].append(key)
    db.commit()
    result["claim_token"] = token if result["claimed"] else ""
    return result


def store_summaries(summaries: dict[str, str], *, claim_token: str,
                    now_ms: int | None = None, db: Session | None = None) -> list[str]:
    """Store only caller-validated Korean output still owned by this claim."""
    keys = _keys(summaries)
    if not keys or not claim_token:
        return []
    if db is None:
        with get_session() as owned:
            return store_summaries(summaries, claim_token=claim_token, now_ms=now_ms, db=owned)
    stored, millis = [], _millis(now_ms)
    for key in keys:
        summary = str(summaries[key] or "").strip()
        if not summary or len(summary) > 4000 or not re.search(r"[가-힣]", summary):
            continue
        changed = db.exec(update(CommunityPostSummary).where(
            CommunityPostSummary.summary_key == key,
            CommunityPostSummary.processing_status == "pending",
            CommunityPostSummary.claim_token == claim_token,
        ).values(summary_ko=summary, processing_status="ready", claim_token="", claimed_ms=0, updated_ms=millis))
        if changed.rowcount == 1:
            stored.append(key)
    db.commit()
    return stored


def renew_claims(keys: list[str], *, claim_token: str, now_ms: int | None = None,
                 db: Session | None = None) -> bool:
    """Renew every not-yet-stored key immediately before a paid provider batch."""
    keys = _keys(keys)
    if not keys or not claim_token:
        return False
    if db is None:
        with get_session() as owned:
            return renew_claims(keys, claim_token=claim_token, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    changed = db.exec(update(CommunityPostSummary).where(
        CommunityPostSummary.summary_key.in_(keys),
        CommunityPostSummary.processing_status == "pending",
        CommunityPostSummary.claim_token == claim_token,
    ).values(claimed_ms=millis, updated_ms=millis))
    if changed.rowcount != len(keys):
        db.rollback()
        return False
    db.commit()
    return True


def release_claims(keys: list[str], *, claim_token: str, retry_immediately: bool = False,
                   now_ms: int | None = None, db: Session | None = None) -> int:
    keys = _keys(keys)
    if not keys or not claim_token:
        return 0
    if db is None:
        with get_session() as owned:
            return release_claims(keys, claim_token=claim_token, retry_immediately=retry_immediately,
                                  now_ms=now_ms, db=owned)
    changed = db.exec(update(CommunityPostSummary).where(
        CommunityPostSummary.summary_key.in_(keys),
        CommunityPostSummary.processing_status == "pending",
        CommunityPostSummary.claim_token == claim_token,
    ).values(processing_status="retryable" if retry_immediately else "error",
             claim_token="", claimed_ms=0, updated_ms=_millis(now_ms)))
    db.commit()
    return int(changed.rowcount)


def prune_summaries(*, retention_days: int = 30, limit: int = 500,
                    now_ms: int | None = None, db: Session | None = None) -> int:
    """Bounded periodic maintenance; never called from load or claim hot paths."""
    if db is None:
        with get_session() as owned:
            return prune_summaries(retention_days=retention_days, limit=limit, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    cutoff = millis - max(1, min(365, int(retention_days))) * 86_400_000
    conditions = (CommunityPostSummary.updated_ms < cutoff, or_(
        CommunityPostSummary.processing_status != "pending",
        CommunityPostSummary.claimed_ms <= millis - _MAX_LEASE_MS,
    ))
    keys = db.exec(select(CommunityPostSummary.summary_key).where(*conditions)
                  .order_by(CommunityPostSummary.updated_ms, CommunityPostSummary.summary_key)
                  .limit(max(1, min(1000, int(limit)))).with_for_update(skip_locked=True)).all()
    if not keys:
        db.rollback()
        return 0
    changed = db.exec(delete(CommunityPostSummary).where(CommunityPostSummary.summary_key.in_(keys), *conditions))
    db.commit()
    return int(changed.rowcount)
