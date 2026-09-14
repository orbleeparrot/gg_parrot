"""Durable public leaderboard snapshots and the fenced background writer lease.

Readers never generate strategies, inspect live runners, or aggregate rankings.
Private macros and account identifiers are kept out of the shared JSON payload.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import nullcontext
from typing import Optional

from sqlalchemy import BigInteger, Index, delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, select

from .db import LeaderboardEntry, LeaderboardVote, MacroUnlock, get_session

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
LEASE_MS = 90_000
RETENTION_MS = 15 * 60_000


class LeaderboardSnapshotControl(SQLModel, table=True):
    key: str = Field(default="board", primary_key=True)
    current_version: str = ""
    claim_token: str = ""
    lease_until_ms: int = Field(default=0, sa_type=BigInteger)
    next_refresh_ms: int = Field(default=0, sa_type=BigInteger)


class LeaderboardSnapshotVersion(SQLModel, table=True):
    id: str = Field(primary_key=True)
    date_kst: str = Field(index=True)
    created_ms: int = Field(sa_type=BigInteger, index=True)
    total: int = 0


class LeaderboardSnapshotItem(SQLModel, table=True):
    __table_args__ = (Index("ix_leaderboard_snapshot_rank", "version_id", "rank"),)
    version_id: str = Field(primary_key=True)
    entry_id: int = Field(primary_key=True)
    rank: int
    public_json: str


class LeaderboardEntryStats(SQLModel, table=True):
    entry_id: int = Field(primary_key=True)
    likes: int = 0
    dislikes: int = 0


class LeaderboardChallengeBot(SQLModel, table=True):
    date_kst: str = Field(primary_key=True)
    slot: int = Field(primary_key=True)
    entry_id: int


def now_ms() -> int:
    return int(time.time() * 1000)


def claim_refresh(*, now: Optional[int] = None) -> Optional[str]:
    millis = now_ms() if now is None else now
    token = uuid.uuid4().hex
    with get_session() as db:
        row = db.get(LeaderboardSnapshotControl, "board")
        if row is None:
            try:
                db.add(LeaderboardSnapshotControl(claim_token=token, lease_until_ms=millis + LEASE_MS))
                db.commit()
                return token
            except IntegrityError:
                db.rollback()
        elif row.lease_until_ms > millis or row.next_refresh_ms > millis:
            return None
        result = db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board",
            LeaderboardSnapshotControl.lease_until_ms <= millis,
            LeaderboardSnapshotControl.next_refresh_ms <= millis,
        ).values(claim_token=token, lease_until_ms=millis + LEASE_MS))
        db.commit()
        return token if result.rowcount == 1 else None


def renew_refresh(token: str, *, now: Optional[int] = None) -> bool:
    millis = now_ms() if now is None else now
    with get_session() as db:
        changed = db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board",
            LeaderboardSnapshotControl.claim_token == token,
            LeaderboardSnapshotControl.lease_until_ms > millis,
        ).values(lease_until_ms=millis + LEASE_MS))
        db.commit()
        return changed.rowcount == 1


def release_refresh(token: str, *, delay_seconds: float = 5) -> None:
    with get_session() as db:
        db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board",
            LeaderboardSnapshotControl.claim_token == token,
        ).values(claim_token="", lease_until_ms=0,
                 next_refresh_ms=now_ms() + int(delay_seconds * 1000)))
        db.commit()


def request_refresh() -> None:
    """Mutations make the next scheduled refresh eligible; readers never call this."""
    with get_session() as db:
        db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board",
        ).values(next_refresh_ms=0))
        db.commit()


def ensure_stats(db, entry_ids: list[int]) -> None:
    """Backfill old entries once. New votes update these counters transactionally."""
    if not entry_ids:
        return
    existing = set(db.exec(select(LeaderboardEntryStats.entry_id).where(
        LeaderboardEntryStats.entry_id.in_(entry_ids))).all())
    missing = [eid for eid in entry_ids if eid not in existing]
    if not missing:
        return
    totals = {(eid, value): count for eid, value, count in db.exec(
        select(LeaderboardVote.entry_id, LeaderboardVote.value, func.count())
        .where(LeaderboardVote.entry_id.in_(missing))
        .group_by(LeaderboardVote.entry_id, LeaderboardVote.value)).all()}
    values = [{"entry_id": eid, "likes": totals.get((eid, 1), 0),
               "dislikes": totals.get((eid, -1), 0)} for eid in missing]
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    # One insert for each bounded batch; a concurrent vote's newer counters win.
    for offset in range(0, len(values), 500):
        db.exec(insert(LeaderboardEntryStats).values(values[offset:offset + 500])
                .on_conflict_do_nothing(index_elements=["entry_id"]))


def publish_snapshot(token: str, date_kst: str, items: list[dict], *, now: Optional[int] = None) -> str:
    """Publish one complete generation atomically; a superseded worker cannot publish."""
    millis = now_ms() if now is None else now
    version_id = uuid.uuid4().hex
    with get_session() as db:
        # This conditional write also locks the control row until publication commits.
        owned = db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board",
            LeaderboardSnapshotControl.claim_token == token,
            LeaderboardSnapshotControl.lease_until_ms > millis,
        ).values(lease_until_ms=millis + LEASE_MS))
        if owned.rowcount != 1:
            raise RuntimeError("leaderboard refresh lease expired or superseded")
        ensure_stats(db, [item["id"] for item in items])
        db.add(LeaderboardSnapshotVersion(id=version_id, date_kst=date_kst,
                                         created_ms=millis, total=len(items)))
        for rank, item in enumerate(items, 1):
            public = {key: value for key, value in item.items() if key not in {
                "macro", "human_summary", "owner_user_id", "user_id", "password_hash",
                "my_vote", "is_mine", "is_owner", "unlocked", "locked", "unlock_price",
            }}
            db.add(LeaderboardSnapshotItem(version_id=version_id, entry_id=item["id"],
                                          rank=rank, public_json=json.dumps(public, ensure_ascii=False)))
        db.exec(update(LeaderboardSnapshotControl).where(
            LeaderboardSnapshotControl.key == "board").values(current_version=version_id))
        expired = select(LeaderboardSnapshotVersion.id).where(
            LeaderboardSnapshotVersion.created_ms < millis - RETENTION_MS,
            LeaderboardSnapshotVersion.id != version_id)
        db.exec(delete(LeaderboardSnapshotItem).where(LeaderboardSnapshotItem.version_id.in_(expired)))
        db.exec(delete(LeaderboardSnapshotVersion).where(
            LeaderboardSnapshotVersion.created_ms < millis - RETENTION_MS,
            LeaderboardSnapshotVersion.id != version_id))
        db.commit()
    return version_id


def read_board(viewer_id: str = "", viewer_user_id: Optional[int] = None, *, db=None,
               page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, snapshot_id: str = "",
               entry_id: Optional[int] = None) -> dict:
    from . import leaderboard as lb
    from . import points

    page = max(1, int(page))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
    with nullcontext(db) if db is not None else get_session() as session:
        if snapshot_id:
            version = session.get(LeaderboardSnapshotVersion, snapshot_id)
        else:
            version = session.exec(select(LeaderboardSnapshotVersion).join(
                LeaderboardSnapshotControl,
                LeaderboardSnapshotControl.current_version == LeaderboardSnapshotVersion.id,
            ).where(LeaderboardSnapshotControl.key == "board")).first()
        if version is None:
            return {"items": [], "page": 1, "page_size": page_size, "total": 0,
                    "has_more": False, "snapshot_id": "", "snapshot_expired": bool(snapshot_id),
                    "entry_location": None,
                    "date_kst": None, "updated_ms": None, "preparing": True, "stale": False,
                    "seconds_to_reset": lb.seconds_to_reset(), "keep_top": lb.KEEP_TOP_N}
        # A new day's smaller board must not strand a viewer on an empty page.
        page = min(page, max(1, (version.total + page_size - 1) // page_size))
        entry_location = None
        if entry_id is not None:
            rank = session.exec(select(LeaderboardSnapshotItem.rank)
                .join(LeaderboardEntry, LeaderboardEntry.id == LeaderboardSnapshotItem.entry_id)
                .where(LeaderboardSnapshotItem.version_id == version.id,
                       LeaderboardSnapshotItem.entry_id == entry_id).limit(1)).first()
            if rank is not None:
                page = (rank - 1) // page_size + 1
                entry_location = {"entry_id": entry_id, "rank": rank, "page": page}
        records = session.exec(select(
            LeaderboardSnapshotItem, LeaderboardEntry.id, LeaderboardEntry.user_id,
            LeaderboardEntry.owner_user_id, LeaderboardEntry.macro_json,
            LeaderboardEntry.human_summary, LeaderboardEntry.username, LeaderboardEntry.nickname,
            LeaderboardEntryStats.likes, LeaderboardEntryStats.dislikes,
        ).join(LeaderboardEntry, LeaderboardEntry.id == LeaderboardSnapshotItem.entry_id)
         .outerjoin(LeaderboardEntryStats, LeaderboardEntryStats.entry_id == LeaderboardSnapshotItem.entry_id)
         .where(LeaderboardSnapshotItem.version_id == version.id,
                LeaderboardSnapshotItem.rank > (page - 1) * page_size,
                LeaderboardSnapshotItem.rank <= page * page_size)
         .order_by(LeaderboardSnapshotItem.rank).limit(page_size)).all()
        ids = [r[1] for r in records]
        unlocked = lb._unlocked_ids_for(session, viewer_user_id, ids)
        votes = dict(session.exec(select(LeaderboardVote.entry_id, LeaderboardVote.value).where(
            LeaderboardVote.entry_id.in_(ids), LeaderboardVote.user_id == viewer_id)).all()) if ids and viewer_id else {}
        items = []
        for item, eid, anonymous_id, owner_id, macro_json, summary, username, nickname, likes, dislikes in records:
            value = json.loads(item.public_json)
            is_owner = viewer_user_id is not None and owner_id == viewer_user_id
            visible = owner_id is None or is_owner or eid in unlocked
            value.update(rank=item.rank, username=username or nickname, nickname=nickname,
                         macro=None, human_summary="", my_vote=votes.get(eid, 0),
                         is_mine=bool(viewer_id) and anonymous_id == viewer_id,
                         for_sale=owner_id is not None, is_owner=is_owner,
                         locked=not visible, unlocked=visible,
                         unlock_price=points.UNLOCK_PRICE if not visible else 0)
            if likes is not None:
                value.update(likes=likes, dislikes=dislikes, score=likes - dislikes)
            if visible:
                try:
                    value["macro"] = json.loads(macro_json)
                except (ValueError, TypeError):
                    pass
                value["human_summary"] = summary
            items.append(value)
        today = lb._today_kst()
        return {"items": items, "page": page, "page_size": page_size, "total": version.total,
                "entry_location": entry_location,
                "has_more": page * page_size < version.total, "snapshot_id": version.id,
                "snapshot_expired": False, "date_kst": version.date_kst,
                "updated_ms": version.created_ms, "preparing": version.date_kst != today,
                "stale": version.date_kst != today, "seconds_to_reset": lb.seconds_to_reset(),
                "keep_top": lb.KEEP_TOP_N,
                "note": "수익률/좋아요는 참고용이며 투자 조언이 아닙니다."}
