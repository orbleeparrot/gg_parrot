"""Shared provider cooldown in the existing private maintenance-lease table.

No new schema, key, paid probe, item-budget reset or sleep. A new provider key
uses a different opaque namespace; cached successes remain available.
"""
from __future__ import annotations

import hashlib
import os
import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import get_session
from .agent_features.position_news.articles import NewsMaintenanceLease


def _scope():
    key = str(os.environ.get('OPENAI_API_KEY') or '').strip()
    return 'openai-pause:' + hashlib.sha256(key.encode()).hexdigest()[:24] if key else None


def ensure_available(*, now_ms=None):
    scope = _scope()
    if not scope:
        return
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    with get_session() as db:
        row = db.get(NewsMaintenanceLease, scope)
        until = row.next_run_ms if row else 0
    if until > millis:
        from .ai_runtime import AiBusyError
        raise AiBusyError('AI 공급자가 요청을 제한해 잠시 대기 중이에요. 준비된 결과는 계속 볼 수 있어요.')


def pause(error, *, now_ms=None):
    """Only provider-wide errors pause work; validation failures do not."""
    scope = _scope()
    code = int(getattr(error, 'status_code', 0) or 0)
    if not scope or code not in {401, 403, 429, 500, 502, 503, 504}:
        return
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    seconds = 900 if code in {401, 403} or type(error).__name__ == 'AiQuotaError' else 60
    until = millis + seconds * 1000
    with get_session() as db:
        insert = pg_insert if db.get_bind().dialect.name == 'postgresql' else sqlite_insert
        statement = insert(NewsMaintenanceLease).values(name=scope, next_run_ms=until)
        db.exec(statement.on_conflict_do_update(
            index_elements=[NewsMaintenanceLease.name], set_={'next_run_ms': until},
            where=NewsMaintenanceLease.next_run_ms < until))
        db.commit()
