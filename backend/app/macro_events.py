"""리더보드 매크로별 하루 노출·열람·언락 카운터(MacroEventDaily) — 관리자 매크로 지표의 클릭률·전환율 재료.

기록은 모두 best-effort 다: 카운터 하나 못 올렸다고 목록·언락 같은 본 흐름을 막지 않는다(예외는 삼키고 경고만).
SQLite/Postgres 둘 다 ``INSERT … ON CONFLICT DO UPDATE`` 로 누적하고, rowcount 는 보지 않는다(운영 PG 에서 -1).

노출·열람은 프론트 비콘이 세션당 엔트리 1회만 보낸다(5초 폴링 응답마다 세면 부풀려진다). 언락은 처음 결제한
때만 센다 — 재언락은 무료·멱등이라 이벤트가 아니다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from .db import LeaderboardEntry, MacroEventDaily

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))
MAX_IMPRESSION_IDS = 100
_COUNTERS = ("impressions", "opens", "unlocks")


def _today_kst(now_ms: Optional[int] = None) -> str:
    moment = datetime.now(timezone.utc) if now_ms is None else datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    return moment.astimezone(_KST).strftime("%Y-%m-%d")


def _clean_ids(entry_ids: Iterable, limit: int) -> list[int]:
    ids: set[int] = set()
    for raw in entry_ids or ():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.add(value)
    return sorted(ids)[:limit]


def _bump(db, entry_ids: Iterable, column: str, *, day: Optional[str] = None) -> int:
    """entry 마다 오늘 행의 ``column`` 을 +1. 없는 엔트리 id 는 버린다 — 임의 id 로 쓰레기 행이 쌓이지 않게."""
    assert column in _COUNTERS
    ids = _clean_ids(entry_ids, MAX_IMPRESSION_IDS)
    if not ids:
        return 0
    existing = set(db.exec(select(LeaderboardEntry.id).where(LeaderboardEntry.id.in_(ids))).all())
    ids = [entry_id for entry_id in ids if entry_id in existing]
    if not ids:
        return 0
    day = day or _today_kst()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    values = [{"day_kst": day, "entry_id": entry_id, "impressions": 0, "opens": 0, "unlocks": 0, column: 1} for entry_id in ids]
    statement = insert(MacroEventDaily).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["day_kst", "entry_id"],
        set_={column: getattr(MacroEventDaily, column) + getattr(statement.excluded, column)},
    )
    db.exec(statement)
    return len(ids)


def _record(db, entry_ids: Iterable, column: str, *, commit: bool) -> int:
    # SAVEPOINT 안에서 올린다: 호출자의 트랜잭션(언락 결제)에 얹혀도 여기서 실패하면 그 지점만 되돌아가고
    # 호출자는 그대로 커밋할 수 있다(Postgres 는 실패한 문장 뒤 트랜잭션 전체가 막히므로 이게 필요하다).
    try:
        with db.begin_nested():
            count = _bump(db, entry_ids, column)
        if commit:
            db.commit()
        return count
    except Exception as exc:  # noqa: BLE001 — 카운터는 본 흐름을 절대 막지 않는다
        logger.warning("macro %s 기록 실패: %s", column, exc)
        if commit:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        return 0


def record_impressions(db, entry_ids: Iterable) -> int:
    """목록에 보인 엔트리들의 노출 +1(최대 100개, 중복 제거). 커밋까지 한다. 올린 개수를 돌려준다."""
    return _record(db, entry_ids, "impressions", commit=True)


def record_open(db, entry_id: int) -> int:
    """행동 버튼(빌더로 가져오기·빠른 실행·언락)을 누른 열람 +1. 커밋까지 한다."""
    return _record(db, [entry_id], "opens", commit=True)


def record_unlock(db, entry_id: int, *, commit: bool = False) -> int:
    """첫 언락 +1. 기본은 호출자 트랜잭션(unlock_entry)에 얹고 커밋하지 않는다."""
    return _record(db, [entry_id], "unlocks", commit=commit)
