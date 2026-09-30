"""Hard lifetime limit per source item, shared by every web/worker process.

Reserve immediately before each provider request, including title corrections.
Never refund: a timeout may already have been billed. These small counters are
not result caches and must not be pruned or reset by a model/prompt change.
"""
from __future__ import annotations

import hashlib

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from . import db as db_mod
from .db import NewsAiItemBudget, get_session

MAX_CALLS = 10


def _keys(kind: str, identities: list[str]) -> dict[str, str]:
    if kind not in {'title', 'summary'}:
        raise ValueError('Unknown news AI budget kind')
    if any(not isinstance(value, str) or not value.strip() for value in identities):
        raise ValueError('Empty news AI budget identity')
    return {value: f'{kind}:{hashlib.sha256(value.encode()).hexdigest()}' for value in identities}


def summary_identity(job: dict) -> str:
    # Sharing survives ticker, model and prompt changes; edited bodies are new input.
    return f"{job['post_id']}:{job['body_hash']}"


def reserve(kind: str, identities: list[str]) -> set[str]:
    """Atomically reserve one call per item; DB errors stop paid work.

    RETURNING works for both psycopg and SQLite; rowcount is not reliable for
    PostgreSQL upserts. Globally ordered keys prevent overlapping-batch deadlocks.
    """
    keys = _keys(kind, identities)
    if not keys:
        return set()
    accepted = set()
    with get_session() as db:
        dialect = db.get_bind().dialect.name
        if db_mod._DATABASE_URL and dialect != 'postgresql':
            raise RuntimeError('Shared news AI budget database unavailable')
        insert = pg_insert if dialect == 'postgresql' else sqlite_insert
        for identity, key in sorted(keys.items(), key=lambda pair: pair[1]):
            stmt = insert(NewsAiItemBudget).values(budget_key=key, calls=1)
            stmt = stmt.on_conflict_do_update(
                index_elements=[NewsAiItemBudget.budget_key],
                set_={'calls': NewsAiItemBudget.calls + 1},
                where=NewsAiItemBudget.calls < MAX_CALLS,
            ).returning(NewsAiItemBudget.budget_key)
            if db.exec(stmt).first() is not None:
                accepted.add(identity)
        db.commit()
    return accepted


def exhausted(kind: str, identities: list[str]) -> set[str]:
    keys = _keys(kind, identities)
    if not keys:
        return set()
    with get_session() as db:
        blocked = set(db.exec(select(NewsAiItemBudget.budget_key).where(
            NewsAiItemBudget.budget_key.in_(keys.values()), NewsAiItemBudget.calls >= MAX_CALLS,
        )).all())
    return {identity for identity, key in keys.items() if key in blocked}
