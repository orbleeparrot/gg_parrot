"""My-page dashboard aggregation.

Read-only rollup for the logged-in account: profile + tier, the macros they
created (with per-macro sales/earnings), the macros they bought, who bought
theirs, and the recent points ledger. Pure DB reads — no mutations.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from . import points as points_mod
from .db import LeaderboardEntry, MacroUnlock, PointLedger, User, get_session

_KST = timezone(timedelta(hours=9))

# 등급: 내 매크로가 팔린 누적 판매량 기준 (오름차순 threshold).
_TIERS = [(0, "새싹", "🌱"), (1, "브론즈", "🥉"), (5, "실버", "🥈"), (15, "골드", "🥇"), (40, "다이아", "💎")]


def _kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, _KST).strftime("%m/%d %H:%M")


def _macro_of(row: LeaderboardEntry):
    try:
        return json.loads(row.macro_json)
    except (ValueError, TypeError):
        return None


def _tier(total_sales: int) -> dict:
    name, icon, base = "새싹", "🌱", 0
    for threshold, tname, ticon in _TIERS:
        if total_sales >= threshold:
            name, icon, base = tname, ticon, threshold
    higher = [t for t in _TIERS if t[0] > base]
    nxt = min(higher, key=lambda t: t[0]) if higher else None
    return {
        "name": name,
        "icon": icon,
        "sales": total_sales,
        "next_name": nxt[1] if nxt else None,
        "next_at": nxt[0] if nxt else None,
        "to_next": (nxt[0] - total_sales) if nxt else 0,
    }


def dashboard(user: User, db: Optional[Session] = None) -> dict:
    session_scope = nullcontext(db) if db is not None else get_session()
    with session_scope as db:
        uid = user.id
        fresh = db.get(User, uid) or user

        my_entries = db.exec(
            select(LeaderboardEntry).where(LeaderboardEntry.owner_user_id == uid)
        ).all()
        my_ids = [e.id for e in my_entries]

        # Unlocks of MY entries (= my sales).
        my_sales_rows = []
        if my_ids:
            my_sales_rows = db.exec(select(MacroUnlock).where(MacroUnlock.entry_id.in_(my_ids))).all()
        sales_by_entry: dict[int, int] = {}
        earned_by_entry: dict[int, int] = {}
        for u in my_sales_rows:
            sales_by_entry[u.entry_id] = sales_by_entry.get(u.entry_id, 0) + 1
            earned_by_entry[u.entry_id] = earned_by_entry.get(u.entry_id, 0) + points_mod.creator_share(u.price)
        total_sales = len(my_sales_rows)
        total_earned = sum(earned_by_entry.values())

        created = [
            {
                "entry_id": e.id,
                "symbol": e.symbol,
                "human_summary": e.human_summary,
                "created_kst": _kst(e.created_ms),
                "sales": sales_by_entry.get(e.id, 0),
                "earned": earned_by_entry.get(e.id, 0),
                "macro": _macro_of(e),
            }
            for e in sorted(my_entries, key=lambda x: x.created_ms, reverse=True)
        ]

        # Macros I bought (my unlocks).
        my_purchases = db.exec(select(MacroUnlock).where(MacroUnlock.user_id == uid)).all()
        p_ids = [p.entry_id for p in my_purchases]
        p_entries: dict[int, LeaderboardEntry] = {}
        if p_ids:
            for e in db.exec(select(LeaderboardEntry).where(LeaderboardEntry.id.in_(p_ids))).all():
                p_entries[e.id] = e
        purchased = []
        for p in sorted(my_purchases, key=lambda x: x.id, reverse=True):
            e = p_entries.get(p.entry_id)
            if e is None:
                continue
            purchased.append(
                {
                    "entry_id": p.entry_id,
                    "seller": e.username or e.nickname,
                    "symbol": e.symbol,
                    "human_summary": e.human_summary,
                    "price": p.price,
                    "unlocked_at": p.created_at,
                    "macro": _macro_of(e),  # bought -> visible
                }
            )

        # Who bought mine (sales detail).
        buyer_name: dict[int, str] = {}
        if my_sales_rows:
            buyer_ids = list({u.user_id for u in my_sales_rows})
            for bu in db.exec(select(User).where(User.id.in_(buyer_ids))).all():
                buyer_name[bu.id] = bu.username
        entry_symbol = {e.id: e.symbol for e in my_entries}
        sales = [
            {
                "entry_id": u.entry_id,
                "symbol": entry_symbol.get(u.entry_id, ""),
                "buyer": buyer_name.get(u.user_id, "익명"),
                "earned": points_mod.creator_share(u.price),
                "at": u.created_at,
            }
            for u in sorted(my_sales_rows, key=lambda x: x.id, reverse=True)
        ]

        ledger = db.exec(
            select(PointLedger).where(PointLedger.user_id == uid)
            .order_by(PointLedger.id.desc()).limit(50)
        ).all()
        ledger_view = [
            {"delta": l.delta, "balance_after": l.balance_after, "reason": l.reason,
             "ref": l.ref, "created_at": l.created_at}
            for l in ledger
        ]

    return {
        "user": {
            "id": fresh.id, "username": fresh.username, "email": fresh.email,
            "points_balance": fresh.points_balance,
        },
        "tier": _tier(total_sales),
        "totals": {
            "created": len(my_entries), "sales": total_sales,
            "earned": total_earned, "purchased": len(purchased),
        },
        "created": created,
        "purchased": purchased,
        "sales": sales,
        "ledger": ledger_view,
    }
