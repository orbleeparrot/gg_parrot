"""Daily (KST) paper-return leaderboard.

Users register a macro; it starts a paper session (reusing the paper engine) and
appears on a board sorted by likes. The board is a *today-only* view: entries are
filtered to the current KST calendar day, so at KST 00:00 the board naturally
resets without any scheduler (spec §3.4, "조회 시 오늘 것만 필터").

The reset is not total: the previous board day's top ``KEEP_TOP_N`` entries are
carried into today so a winner has to defend its place. A carried entry keeps its
ORIGINAL paper session — the competition is the macro's own return measured from
the moment it was registered, so nothing about it is reset or restarted. If the
macro's own rules ended it (target hit, stop-loss, max holding time), that final
return is the number it defends with; ending on purpose is part of the strategy.
Carrying is lazy and idempotent like the daily AI challenge — the first
``/api/leaderboard`` request of a KST day runs it (see
:func:`ensure_today_carryover`).

KST is a fixed +09:00 offset (no DST), so we use ``timezone(timedelta(hours=9))``
instead of ``zoneinfo`` to avoid a tz-database dependency on Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from . import paper as paper_mod
from . import points as points_mod
from .db import (
    LeaderboardCarryover,
    LeaderboardEntry,
    LeaderboardVote,
    MacroUnlock,
    User,
    get_session,
)
from .security import verify_password

KST = timezone(timedelta(hours=9))
DAY_MS = 24 * 60 * 60 * 1000

# 매일 초기화해도 상위 몇 등까지는 다음 날 보드로 이월할지(=방어전에 들어갈 인원).
KEEP_TOP_N = int(os.environ.get("LEADERBOARD_KEEP_TOP", "3"))
# 아무도 보드를 열지 않은 조용한 날이 끼면 '어제'가 비어 있을 수 있다. 그럴 땐 글이
# 있던 마지막 날의 상위권을 이월하되, 몇 달 묵은 보드까지 되살리지는 않도록 막는다.
CARRYOVER_LOOKBACK_DAYS = int(os.environ.get("LEADERBOARD_CARRYOVER_LOOKBACK_DAYS", "7"))

# 👑 왕관 기준(누적, env 조정 가능): 판매량과 누적 좋아요가 모두 이 이상인 셀러.
CROWN_MIN_SALES = int(os.environ.get("CROWN_MIN_SALES", "3"))
CROWN_MIN_LIKES = int(os.environ.get("CROWN_MIN_LIKES", "3"))

# 👑 왕관 계산은 쿼리 3번을 쓴다 — 리더보드 한 번 그리는 데 드는 왕복 6회의 절반이다.
# 서버(Render)와 DB(Supabase)가 서로 다른 리전에 있으면 왕복 1회가 그대로 지연이라
# 이 3회를 줄이는 효과가 크다. 왕관은 '누적' 판매·좋아요 기준이라 초 단위로 바뀌지
# 않으므로 짧게 캐시해도 안전하다. 대가는 자격을 갓 얻은 셀러의 왕관이 최대
# CROWN_CACHE_SECONDS 만큼 늦게 보이는 것뿐이다.
CROWN_CACHE_SECONDS = float(os.environ.get("CROWN_CACHE_SECONDS", "180"))
# 키는 '오늘 출품자 집합' 이라 하루에 몇 가지 조합뿐이지만, 날이 바뀌며 쌓이므로 상한을 둔다.
_CROWN_CACHE_MAX_ENTRIES = 64
_crown_cache: dict[frozenset, tuple[frozenset, float]] = {}


class UnlockError(Exception):
    """Raised when an unlock can't proceed (own entry, no owner, already done…)."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --- KST time helpers ---------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _kst_midnight_bounds() -> tuple[datetime, datetime]:
    """(today 00:00 KST, tomorrow 00:00 KST) as aware datetimes."""
    now_kst = _now_utc().astimezone(KST)
    start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def today_start_ms() -> int:
    start, _ = _kst_midnight_bounds()
    return int(start.timestamp() * 1000)


def seconds_to_reset() -> int:
    _, nxt = _kst_midnight_bounds()
    return max(0, int((nxt - _now_utc().astimezone(KST)).total_seconds()))


def _kst_hhmm(created_ms: int) -> str:
    return datetime.fromtimestamp(created_ms / 1000, KST).strftime("%H:%M")


def _kst_mmdd(created_ms: int) -> str:
    return datetime.fromtimestamp(created_ms / 1000, KST).strftime("%m/%d")


def _today_kst() -> str:
    return _now_utc().astimezone(KST).strftime("%Y-%m-%d")


def _kst_day_start_ms(ms: int) -> int:
    """KST midnight of the day ``ms`` falls in (the board day an entry belongs to)."""
    day = datetime.fromtimestamp(ms / 1000, KST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(day.timestamp() * 1000)


# --- entries ------------------------------------------------------------
def create_entry(
    *,
    user_id: str,
    username: str,
    password_hash: str,
    symbol: str,
    macro_json: str,
    human_summary: str,
    paper_session_id: Optional[int],
    owner_user_id: Optional[int] = None,
    is_ai: bool = False,
) -> dict:
    now = _now_utc()
    created_ms = int(now.timestamp() * 1000)
    name = (username or "익명").strip()[:24] or "익명"
    row = LeaderboardEntry(
        user_id=user_id or "anon",
        nickname=name,
        username=name,
        password_hash=password_hash,
        owner_user_id=owner_user_id,
        is_ai=is_ai,
        symbol=symbol,
        macro_json=macro_json,
        human_summary=human_summary,
        paper_session_id=paper_session_id,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_ms=created_ms,
    )
    with get_session() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    # The creator sees their own new entry unlocked.
    return _entry_view(row, {}, viewer_id=user_id, viewer_user_id=owner_user_id)


def get_entry(entry_id: int) -> Optional[LeaderboardEntry]:
    with get_session() as db:
        return db.get(LeaderboardEntry, entry_id)


def verify_owner(entry_id: int, password: str) -> bool:
    """True if ``password`` matches the entry's stored hash (server-side check)."""
    row = get_entry(entry_id)
    if row is None or not row.password_hash:
        return False
    return verify_password(password, row.password_hash)


def update_entry(
    entry_id: int,
    *,
    symbol: str,
    macro_json: str,
    human_summary: str,
    paper_session_id: Optional[int],
) -> Optional[dict]:
    """Replace an entry's macro/session (caller must verify ownership first)."""
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        if row is None:
            return None
        row.symbol = symbol
        row.macro_json = macro_json
        row.human_summary = human_summary
        row.paper_session_id = paper_session_id
        db.add(row)
        db.commit()
        db.refresh(row)
        viewer = row.user_id
        owner = row.owner_user_id
    return _entry_view(row, {}, viewer_id=viewer, viewer_user_id=owner)


def delete_entry(entry_id: int) -> Optional[int]:
    """Delete an entry; returns its paper_session_id (to stop) or None if absent.
    Caller must verify ownership first."""
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        if row is None:
            return None
        sid = row.paper_session_id
        db.delete(row)
        db.commit()
        return sid


def _vote_tallies(db, entry_ids: list[int]) -> dict[int, dict]:
    """entry_id -> {likes, dislikes, votes_by_user: {user_id: value}}."""
    tallies: dict[int, dict] = {eid: {"likes": 0, "dislikes": 0, "by_user": {}} for eid in entry_ids}
    if not entry_ids:
        return tallies
    votes = db.exec(select(LeaderboardVote).where(LeaderboardVote.entry_id.in_(entry_ids))).all()
    for v in votes:
        t = tallies.get(v.entry_id)
        if not t:
            continue
        if v.value > 0:
            t["likes"] += 1
        elif v.value < 0:
            t["dislikes"] += 1
        t["by_user"][v.user_id] = v.value
    return tallies


_STATUS_NOT_PROVIDED = object()


def _live_return(
    session_id: Optional[int],
    status=_STATUS_NOT_PROVIDED,
) -> tuple[Optional[float], Optional[float], str, str]:
    """(return_pct, equity, status, mode) from the paper session, if any."""
    if session_id is None:
        return None, None, "none", "live"
    if status is _STATUS_NOT_PROVIDED:
        status = paper_mod.get_status(session_id)
    if not status:
        return None, None, "none", "live"
    return (
        status.get("current_return"),
        status.get("current_equity"),
        status.get("status", "unknown"),
        status.get("mode", "live"),
    )


def _entry_view(
    row: LeaderboardEntry,
    tally: dict,
    *,
    viewer_id: str,
    viewer_user_id: Optional[int] = None,
    unlocked_ids: frozenset = frozenset(),
    crown_owner_ids: frozenset = frozenset(),
    paper_status=_STATUS_NOT_PROVIDED,
) -> dict:
    ret, equity, pstatus, mode = _live_return(row.paper_session_id, paper_status)
    likes = tally.get("likes", 0)
    dislikes = tally.get("dislikes", 0)
    my_vote = tally.get("by_user", {}).get(viewer_id, 0)

    # Gating: owned entries are LOCKED (macro + summary hidden) until the viewer
    # is the owner or has paid to unlock. Legacy anonymous entries (no owner) stay
    # fully visible, so nothing that worked before breaks.
    has_owner = row.owner_user_id is not None
    is_owner = has_owner and viewer_user_id is not None and row.owner_user_id == viewer_user_id
    unlocked = (not has_owner) or is_owner or (row.id in unlocked_ids)

    streak = max(1, int(row.streak_days or 1))

    macro = None
    summary = ""
    if unlocked:
        try:
            macro = json.loads(row.macro_json)
        except (ValueError, TypeError):
            macro = None
        summary = row.human_summary

    # NOTE: password_hash is intentionally never included in the view.
    return {
        "id": row.id,
        "username": row.username or row.nickname,
        "nickname": row.nickname,
        "symbol": row.symbol,
        # Locked: summary/macro withheld so only id·종목·등락률이 노출된다.
        "human_summary": summary,
        "macro": macro,  # for "매크로 복사하기 → 빌더" prefill (unlocked only)
        "return_pct": ret,
        "equity": equity,
        "paper_status": pstatus,
        "mode": mode,
        "paper_session_id": row.paper_session_id,
        "likes": likes,
        "dislikes": dislikes,
        "score": likes - dislikes,
        "my_vote": my_vote,
        "created_at": row.created_at,
        "created_kst": _kst_hhmm(row.created_ms),
        # 방어전: 상위권으로 살아남아 오늘 보드까지 이어진 매크로는 며칠째인지 센다.
        # 1 = 오늘 등록(방어 아님), 2 이상 = "N일째 순위권 방어중". 이월돼도 수익률은
        # 등록 시점부터 그대로 이어지므로 first_created_kst 가 실제 등록일이다.
        "streak_days": streak,
        "defending": streak > 1,
        "first_created_kst": _kst_mmdd(row.first_created_ms or row.created_ms),
        "is_mine": row.user_id == viewer_id,
        "is_ai": bool(row.is_ai),  # 🤖 daily-challenge bot
        # marketplace fields
        "for_sale": has_owner,  # false for legacy anonymous entries
        "locked": not unlocked,
        "unlocked": unlocked,
        "is_owner": is_owner,
        "unlock_price": points_mod.UNLOCK_PRICE if (has_owner and not unlocked) else 0,
        "crown": has_owner and row.owner_user_id in crown_owner_ids,
    }


def _unlocked_ids_for(db, viewer_user_id: Optional[int], entry_ids: list[int]) -> frozenset:
    """Entry ids this account has already paid to unlock (so we don't re-charge)."""
    if viewer_user_id is None or not entry_ids:
        return frozenset()
    rows = db.exec(
        select(MacroUnlock.entry_id).where(
            MacroUnlock.user_id == viewer_user_id, MacroUnlock.entry_id.in_(entry_ids)
        )
    ).all()
    return frozenset(rows)


def _crown_owner_ids(db, owner_ids: list[int]) -> frozenset:
    """Owners who clear the crown bar, cached — see ``CROWN_CACHE_SECONDS``."""
    owners = [o for o in {o for o in owner_ids if o is not None}]
    if not owners:
        return frozenset()
    key = frozenset(owners)
    hit = _crown_cache.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    crowned = _compute_crown_owner_ids(db, owners)
    if len(_crown_cache) >= _CROWN_CACHE_MAX_ENTRIES:
        # 조합 수가 적어 통째로 비우는 편이 만료 항목만 골라내는 것보다 싸다.
        _crown_cache.clear()
    _crown_cache[key] = (crowned, time.time() + CROWN_CACHE_SECONDS)
    return crowned


def _compute_crown_owner_ids(db, owners: list[int]) -> frozenset:
    """Owners whose ALL-TIME sales and cumulative likes both clear the crown bar."""
    # Every entry each owner has ever posted (crown is a lifetime reputation).
    entries = db.exec(
        select(LeaderboardEntry.id, LeaderboardEntry.owner_user_id).where(
            LeaderboardEntry.owner_user_id.in_(owners)
        )
    ).all()
    entry_owner = {eid: oid for eid, oid in entries}
    all_entry_ids = list(entry_owner.keys())
    if not all_entry_ids:
        return frozenset()
    sales = {o: 0 for o in owners}
    for eid in db.exec(select(MacroUnlock.entry_id).where(MacroUnlock.entry_id.in_(all_entry_ids))):
        owner = entry_owner.get(eid)
        if owner is not None:
            sales[owner] += 1
    likes = {o: 0 for o in owners}
    for v in db.exec(select(LeaderboardVote).where(LeaderboardVote.entry_id.in_(all_entry_ids))):
        if v.value > 0:
            owner = entry_owner.get(v.entry_id)
            if owner is not None:
                likes[owner] += 1
    return frozenset(
        o for o in owners
        if sales[o] >= CROWN_MIN_SALES and likes[o] >= CROWN_MIN_LIKES
    )


def list_entries(viewer_id: str = "", viewer_user_id: Optional[int] = None, db=None) -> dict:
    """Today's (KST) entries, sorted by likes-score then live return.

    ``viewer_user_id`` (the logged-in account) drives per-viewer unlock state and
    ownership; ``viewer_id`` (anonymous localStorage id) still drives votes.
    """
    start_ms = today_start_ms()
    session_scope = nullcontext(db) if db is not None else get_session()
    with session_scope as db:
        rows = db.exec(
            select(LeaderboardEntry).where(LeaderboardEntry.created_ms >= start_ms)
        ).all()
        entry_ids = [r.id for r in rows]
        tallies = _vote_tallies(db, entry_ids)
        unlocked_ids = _unlocked_ids_for(db, viewer_user_id, entry_ids)
        crown_ids = _crown_owner_ids(db, [r.owner_user_id for r in rows])
        paper_statuses = paper_mod.get_statuses(
            [r.paper_session_id for r in rows if r.paper_session_id is not None],
            db=db,
        )

    items = [
        _entry_view(
            r, tallies.get(r.id, {}),
            viewer_id=viewer_id, viewer_user_id=viewer_user_id,
            unlocked_ids=unlocked_ids, crown_owner_ids=crown_ids,
            paper_status=paper_statuses.get(r.paper_session_id),
        )
        for r in rows
    ]
    _sort_board(items, key=lambda e: (e["return_pct"], e["created_at"]))
    return {
        "items": items,
        "seconds_to_reset": seconds_to_reset(),
        "keep_top": KEEP_TOP_N,
        "note": (
            "수익률/좋아요는 참고용이며 투자 조언이 아닙니다. 매일 KST 00:00 초기화되며, "
            f"상위 {KEEP_TOP_N}등은 다음 날 보드로 이어집니다."
        ),
    }


def _sort_board(rows: list, *, key) -> None:
    """Sort a board in place: live RETURN desc, tie-break by earliest registration.

    ``key`` maps one element to ``(return_pct, created_at)``. Stable two-pass:
    sort by created_at asc first, then by return desc so equal returns keep the
    earlier entry on top; entries with no return yet sink last.
    """
    rows.sort(key=lambda e: key(e)[1])
    rows.sort(
        key=lambda e: (key(e)[0] is not None, key(e)[0] if key(e)[0] is not None else 0.0),
        reverse=True,
    )


# --- daily carry-over: 상위 N등은 다음 날 보드로 --------------------------
# 이월은 새 행을 만들지 않고 기존 행의 '보드 날짜'(created_ms)를 오늘 자정으로 민다.
# 그래야 엔트리 id·좋아요·언락 기록은 물론 페이퍼 세션과 누적 수익률까지 그대로
# 이어져, 순위가 '등록 시점부터의 순수 매크로 수익률' 경쟁으로 남는다.
_carryover_lock = asyncio.Lock()
_carryover_done_date: Optional[str] = None


def _claim_carryover(date_kst: str) -> bool:
    """Insert the day's marker row first, so only one caller does the work.

    Claim-before-work (not after): if the work half-fails we must not run again
    on the same day — the already-moved entries would no longer be in the
    previous day's window, so a rerun would carry the *next* three as well.
    """
    with get_session() as db:
        exists = db.exec(
            select(LeaderboardCarryover).where(LeaderboardCarryover.date_kst == date_kst)
        ).first()
        if exists is not None:
            return False
        db.add(LeaderboardCarryover(
            date_kst=date_kst, carried=0,
            created_at=_now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # another worker claimed the same day
            return False
    return True


def _record_carried(date_kst: str, carried: int) -> None:
    with get_session() as db:
        row = db.exec(
            select(LeaderboardCarryover).where(LeaderboardCarryover.date_kst == date_kst)
        ).first()
        if row is None:
            return
        row.carried = carried
        db.add(row)
        db.commit()


def _carry_previous_day_top() -> int:
    """Move the previous board day's top-N onto today's board. Returns how many.

    Only the *board day* moves. ``paper_session_id`` is untouched on purpose: the
    session keeps running (or keeps its final number if the macro's own rules
    already closed it), so a defending entry's return is still measured from its
    registration. ``created_at``/``first_created_ms`` stay truthful for the same
    reason — and the older ``created_at`` is what breaks ties in its favour.
    """
    today_ms = today_start_ms()
    floor_ms = today_ms - CARRYOVER_LOOKBACK_DAYS * DAY_MS
    with get_session() as db:
        last = db.exec(
            select(LeaderboardEntry)
            .where(
                LeaderboardEntry.created_ms < today_ms,
                LeaderboardEntry.created_ms >= floor_ms,
            )
            .order_by(LeaderboardEntry.created_ms.desc())
        ).first()
        if last is None:
            return 0  # 되돌아볼 범위에 보드가 없다 — 이월할 것도 없다
        day_start = _kst_day_start_ms(last.created_ms)
        rows = db.exec(
            select(LeaderboardEntry).where(
                LeaderboardEntry.created_ms >= day_start,
                LeaderboardEntry.created_ms < day_start + DAY_MS,
            )
        ).all()
        statuses = paper_mod.get_statuses(
            [r.paper_session_id for r in rows if r.paper_session_id is not None], db=db
        )
        ranked = [
            (r, _live_return(r.paper_session_id, statuses.get(r.paper_session_id))[0])
            for r in rows
        ]
        _sort_board(ranked, key=lambda t: (t[1], t[0].created_at))

        carried = 0
        for row, _ in ranked[:KEEP_TOP_N]:
            if row.first_created_ms is None:
                row.first_created_ms = row.created_ms  # 원 등록 시각을 한 번만 보관
            row.streak_days = max(1, int(row.streak_days or 1)) + 1
            row.created_ms = today_ms  # 보드 날짜만 오늘로
            db.add(row)
            carried += 1
        db.commit()
    return carried


async def ensure_today_carryover() -> int:
    """Carry the previous board day's top-N into today. Lazy, once per KST day.

    Mirrors the daily AI challenge: the first request of the day does the work,
    guarded by an in-process lock and a unique ``LeaderboardCarryover`` row, so
    no scheduler is needed. Returns how many entries survived.
    """
    global _carryover_done_date
    today = _today_kst()
    if _carryover_done_date == today:
        return 0
    async with _carryover_lock:
        if _carryover_done_date == today:
            return 0
        if not _claim_carryover(today):
            _carryover_done_date = today
            return 0
        carried = await asyncio.to_thread(_carry_previous_day_top)
        await asyncio.to_thread(_record_carried, today, carried)
        _carryover_done_date = today
        return carried


def vote(entry_id: int, user_id: str, value: int) -> dict:
    """Set/toggle a user's vote (+1/-1). Re-voting the same value cancels it."""
    value = 1 if value > 0 else -1
    with get_session() as db:
        existing = db.exec(
            select(LeaderboardVote).where(
                LeaderboardVote.entry_id == entry_id, LeaderboardVote.user_id == user_id
            )
        ).first()
        if existing is None:
            db.add(LeaderboardVote(entry_id=entry_id, user_id=user_id, value=value))
        elif existing.value == value:
            db.delete(existing)  # toggle off
        else:
            existing.value = value
            db.add(existing)
        db.commit()
        tally = _vote_tallies(db, [entry_id])[entry_id]
    return {
        "entry_id": entry_id,
        "likes": tally["likes"],
        "dislikes": tally["dislikes"],
        "my_vote": tally["by_user"].get(user_id, 0),
    }


def unlock_entry(viewer: User, entry_id: int) -> dict:
    """Pay points to reveal+copy an entry's macro; 70% goes to the creator.

    Atomic, idempotent, and self-protecting: re-unlocking is free, and you can't
    unlock your own or an ownerless (free) entry. Raises :class:`UnlockError` or
    :class:`points.InsufficientPoints` on the respective failures.
    """
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        if row is None:
            raise UnlockError("엔트리를 찾을 수 없어요.", status=404)
        if row.owner_user_id is None:
            raise UnlockError("이 매크로는 무료 공개라 언락이 필요 없어요.")
        if row.owner_user_id == viewer.id:
            raise UnlockError("내 매크로예요. 이미 볼 수 있어요.")

        already = db.exec(
            select(MacroUnlock).where(
                MacroUnlock.user_id == viewer.id, MacroUnlock.entry_id == entry_id
            )
        ).first()

        viewer_row = db.get(User, viewer.id)
        if already is None:
            creator = db.get(User, row.owner_user_id)
            if creator is None:
                raise UnlockError("작성자 계정을 찾을 수 없어요.", status=404)
            # charge viewer, pay creator 70%, record the unlock — all in one commit.
            # Read the price at call time so it stays configurable/testable.
            points_mod.unlock_transfer(
                db, viewer=viewer_row, creator=creator, entry_id=entry_id,
                price=points_mod.UNLOCK_PRICE,
            )
            db.commit()
            db.refresh(viewer_row)

        tally = _vote_tallies(db, [entry_id])[entry_id]
        unlocked_ids = frozenset({entry_id})
        crown_ids = _crown_owner_ids(db, [row.owner_user_id])
        entry = _entry_view(
            row, tally, viewer_id="",
            viewer_user_id=viewer.id, unlocked_ids=unlocked_ids, crown_owner_ids=crown_ids,
        )
        return {"entry": entry, "points_balance": viewer_row.points_balance}
