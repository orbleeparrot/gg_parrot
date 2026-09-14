"""Daily AI challenge — 'AI를 이겨라'.

Once per KST day, pick a trending symbol, have the AI generate a few macros, and
register them on today's leaderboard as free/visible 🤖 bot entries (owner=None,
is_ai=True). Users then compete to rank above them on live paper return.

Generation runs in the leased background leaderboard worker. GET requests only
read the ready challenge; per-day bot slots prevent duplicate entries after retries.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from . import ai_challenge
from . import hotcoins as hotcoins_mod
from . import leaderboard as leaderboard_mod
from . import paper as paper_mod
from .db import DailyChallenge, LeaderboardEntry, get_session
from .leaderboard_snapshot import LeaderboardChallengeBot
from .engine import Macro, human_summary

# Each bot gets its own numbered name (껄무새1호기봇, 껄무새2호기봇, …) so the
# board reads as several competitors rather than one repeated entry.
AI_NAME_PREFIX = "껄무새"
AI_NAME_SUFFIX = "호기봇"
AI_NAME = "껄무새봇"  # family name, for the UI banner / API summary

_KST = timezone(timedelta(hours=9))
_lock = asyncio.Lock()
_CLAIM_LEASE_MS = max(
    30,
    int(os.environ.get("DAILY_CHALLENGE_CLAIM_LEASE_SECONDS", "120")),
) * 1000
_CLAIM_POLL_SECONDS = 0.25


def bot_name(index: int) -> str:
    """Display name of the ``index``-th (1-based) bot of the day."""
    return f"{AI_NAME_PREFIX}{index}{AI_NAME_SUFFIX}"


def _today_kst() -> str:
    return datetime.now(timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _existing(date_kst: str, db=None) -> DailyChallenge | None:
    with get_session() if db is None else nullcontext(db) as db:
        return db.exec(
            select(DailyChallenge).where(
                DailyChallenge.date_kst == date_kst,
                DailyChallenge.status == "ready",
            )
        ).first()


def _claim_daily_challenge(
    date_kst: str,
    *,
    now_ms: int | None = None,
) -> dict[str, str]:
    """Claim the day before market/AI/paper work across all web processes."""
    millis = int(now_ms if now_ms is not None else time.time() * 1000)
    now_iso = datetime.fromtimestamp(millis / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    claim_token = uuid.uuid4().hex
    with get_session() as db:
        row = db.exec(
            select(DailyChallenge).where(DailyChallenge.date_kst == date_kst)
        ).first()
        if row is None:
            try:
                db.add(DailyChallenge(
                    date_kst=date_kst,
                    symbol="",
                    created_at=now_iso,
                    status="pending",
                    claim_token=claim_token,
                    claimed_ms=millis,
                ))
                db.commit()
                return {"status": "claimed", "claim_token": claim_token}
            except IntegrityError:
                db.rollback()
                row = db.exec(
                    select(DailyChallenge).where(
                        DailyChallenge.date_kst == date_kst
                    )
                ).first()

        if row is None:
            return {"status": "waiting", "claim_token": ""}
        if row.status == "ready":
            return {"status": "ready", "claim_token": ""}
        if (
            row.status == "pending"
            and int(row.claimed_ms or 0) > millis - _CLAIM_LEASE_MS
        ):
            return {"status": "waiting", "claim_token": ""}

        previous_token = row.claim_token
        previous_claimed_ms = int(row.claimed_ms or 0)
        result = db.exec(
            update(DailyChallenge)
            .where(
                DailyChallenge.date_kst == date_kst,
                DailyChallenge.status != "ready",
                DailyChallenge.claim_token == previous_token,
                DailyChallenge.claimed_ms == previous_claimed_ms,
            )
            .values(
                status="pending",
                claim_token=claim_token,
                claimed_ms=millis,
                last_error="",
            )
        )
        claimed = result.rowcount == 1
        db.commit()
        return {
            "status": "claimed" if claimed else "waiting",
            "claim_token": claim_token if claimed else "",
        }


def _complete_daily_challenge(
    date_kst: str,
    *,
    claim_token: str,
    symbol: str,
    now_ms: int | None = None,
) -> None:
    millis = int(now_ms if now_ms is not None else time.time() * 1000)
    now_iso = datetime.fromtimestamp(millis / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_session() as db:
        result = db.exec(
            update(DailyChallenge)
            .where(
                DailyChallenge.date_kst == date_kst,
                DailyChallenge.status == "pending",
                DailyChallenge.claim_token == claim_token,
            )
            .values(
                symbol=symbol,
                created_at=now_iso,
                status="ready",
                claim_token="",
                claimed_ms=0,
                last_error="",
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise RuntimeError("daily challenge claim was superseded")
        db.commit()


def _fail_daily_challenge(
    date_kst: str,
    *,
    claim_token: str,
    error: BaseException,
) -> None:
    with get_session() as db:
        db.exec(
            update(DailyChallenge)
            .where(
                DailyChallenge.date_kst == date_kst,
                DailyChallenge.status == "pending",
                DailyChallenge.claim_token == claim_token,
            )
            .values(
                status="error",
                claim_token="",
                claimed_ms=0,
                last_error=str(error)[:200],
            )
        )
        db.commit()


def _pick_symbol() -> str:
    """Top trending USDT symbol from '오늘의 경주마', BTCUSDT on any failure."""
    try:
        coins = hotcoins_mod.get_hot_coins(limit=10).get("coins", [])
        for c in coins:
            sym = c.get("symbol")
            if sym:
                return sym
    except Exception:
        pass
    return "BTCUSDT"


def _bot_exists(date_kst: str, slot: int) -> bool:
    with get_session() as db:
        return db.get(LeaderboardChallengeBot, (date_kst, slot)) is not None


def _store_bot(date_kst: str, slot: int, token: str, macro: Macro, session_id) -> None:
    """The entry and its retry key commit together under the daily claim fence."""
    if _today_kst() != date_kst:
        raise RuntimeError("daily challenge date changed during preparation")
    with get_session() as db:
        owned = db.exec(update(DailyChallenge).where(
            DailyChallenge.date_kst == date_kst, DailyChallenge.status == "pending",
            DailyChallenge.claim_token == token,
        ).values(claimed_ms=int(time.time() * 1000)))
        if owned.rowcount != 1:
            raise RuntimeError("daily challenge claim was superseded")
        if db.get(LeaderboardChallengeBot, (date_kst, slot)) is not None:
            return
        now = datetime.now(timezone.utc)
        entry = LeaderboardEntry(user_id="ai", nickname=bot_name(slot), username=bot_name(slot),
            owner_user_id=None, is_ai=True, symbol=macro.symbol, macro_json=macro.model_dump_json(),
            human_summary=human_summary(macro), paper_session_id=session_id,
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), created_ms=int(now.timestamp() * 1000))
        db.add(entry)
        db.flush()
        db.add(LeaderboardChallengeBot(date_kst=date_kst, slot=slot, entry_id=entry.id))
        db.commit()


def _renew_claim(date_kst: str, token: str) -> bool:
    with get_session() as db:
        changed = db.exec(update(DailyChallenge).where(
            DailyChallenge.date_kst == date_kst, DailyChallenge.status == "pending",
            DailyChallenge.claim_token == token,
        ).values(claimed_ms=int(time.time() * 1000)))
        db.commit()
        return changed.rowcount == 1


async def _renew_claim_loop(date_kst: str, token: str) -> None:
    while True:
        await asyncio.sleep(max(1, _CLAIM_LEASE_MS / 3000))
        if not await asyncio.to_thread(_renew_claim, date_kst, token):
            return


async def ensure_today() -> None:
    """Create today's challenge if missing (idempotent, concurrency-safe)."""
    date_kst = _today_kst()
    if await asyncio.to_thread(_existing, date_kst):
        return
    async with _lock:
        while True:
            claim = await asyncio.to_thread(_claim_daily_challenge, date_kst)
            if claim["status"] == "ready":
                return
            if claim["status"] == "claimed":
                break
            await asyncio.sleep(_CLAIM_POLL_SECONDS)

        claim_token = claim["claim_token"]
        renewal = asyncio.create_task(_renew_claim_loop(date_kst, claim_token))
        try:
            symbol = await asyncio.to_thread(_pick_symbol)
            macro_dicts = await asyncio.to_thread(
                ai_challenge.generate_macros,
                symbol,
                3,
            )

            failed_slots = []
            for slot, md in enumerate(macro_dicts, 1):
                if await asyncio.to_thread(_bot_exists, date_kst, slot):
                    continue
                try:
                    macro = Macro(**md)
                    info = await paper_mod.start_session(macro, macro.symbol, "live")
                except Exception:
                    failed_slots.append(slot)
                    continue
                try:
                    await asyncio.to_thread(_store_bot, date_kst, slot, claim_token, macro,
                                            info["session_id"])
                except BaseException:
                    if info["session_id"] is not None:
                        await paper_mod.stop_session(info["session_id"])
                    raise

            if failed_slots or not macro_dicts:
                raise RuntimeError("daily challenge paper sessions are not ready")
            # Failed slots are retried by the scheduled worker, never by GET.
            await asyncio.to_thread(
                _complete_daily_challenge,
                date_kst,
                claim_token=claim_token,
                symbol=symbol,
            )
        except BaseException as exc:
            await asyncio.to_thread(
                _fail_daily_challenge,
                date_kst,
                claim_token=claim_token,
                error=exc,
            )
            raise
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)


def get_today(db=None) -> dict:
    """DB-only banner metadata; the background worker creates missing challenges."""
    row = _existing(_today_kst(), db=db)
    return {"date_kst": _today_kst(), "symbol": row.symbol if row else None,
            "ai_name": AI_NAME, "active": row is not None, "preparing": row is None}
