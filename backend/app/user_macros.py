"""Account-owned macro library used by the quick-run flow.

Marketplace rows describe registrations and purchases, but are not a safe
execution library because a source entry can later change or disappear. We
snapshot every macro when it enters an account and backfill still-available
legacy registrations/unlocks on first read.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlmodel import select

from . import paper as paper_mod
from .db import LeaderboardEntry, MacroRow, MacroUnlock, UserMacro, get_session
from .engine import Macro, human_summary

_SOURCES = {"created", "leaderboard", "upload", "builder"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _macro_name(macro: Macro, name: str = "") -> str:
    clean = " ".join((name or "").strip().split())[:60]
    if clean:
        return clean
    coin = macro.symbol.removesuffix("USDT") or macro.symbol
    return f"{coin} 매크로"


def _view(row: UserMacro) -> dict:
    try:
        macro = json.loads(row.macro_json)
    except (TypeError, ValueError):
        macro = None
    return {
        "id": row.id,
        "name": row.name,
        "symbol": row.symbol,
        "rule_type": row.rule_type,
        "position_side": row.position_side,
        "human_summary": row.human_summary,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "schema_version": row.schema_version,
        "created_at": row.created_at,
        "macro": macro,
    }


def _same_macro(left: str, right: str) -> bool:
    try:
        return json.loads(left) == json.loads(right)
    except (TypeError, ValueError):
        return False


def _performance(db, row: UserMacro) -> Optional[dict]:
    """Return an honestly labelled result for the account-library row.

    Leaderboard copies follow their paper session, builder saves use the
    representative backtest stored with the share row, and raw uploads have no
    measured result yet.
    """
    if row.source_type in {"created", "leaderboard"}:
        try:
            entry_id = int(row.source_ref)
        except (TypeError, ValueError):
            entry_id = 0
        entry = db.get(LeaderboardEntry, entry_id) if entry_id else None
        if (
            entry is not None
            and entry.paper_session_id is not None
            and _same_macro(row.macro_json, entry.macro_json)
        ):
            status = paper_mod.get_status(entry.paper_session_id)
            if status is not None and status.get("current_return") is not None:
                return {
                    "kind": "paper",
                    "return_pct": float(status["current_return"]),
                    "status": status.get("status", ""),
                    "period_label": "",
                }

    if row.source_type == "builder" and row.source_ref:
        shared = db.exec(
            select(MacroRow).where(MacroRow.share_slug == row.source_ref)
        ).first()
        if shared is not None:
            return {
                "kind": "backtest",
                "return_pct": float(shared.rep_return_pct),
                "status": "saved",
                "period_label": shared.rep_period_label,
            }

    return None


def _find(db, user_id: int, source_type: str, source_ref: str) -> Optional[UserMacro]:
    return db.exec(
        select(UserMacro).where(
            UserMacro.user_id == user_id,
            UserMacro.source_type == source_type,
            UserMacro.source_ref == source_ref,
        )
    ).first()


def _insert_snapshot(
    db,
    *,
    user_id: int,
    macro: Macro,
    source_type: str,
    source_ref: str,
    name: str = "",
    created_at: str = "",
) -> UserMacro:
    existing = _find(db, user_id, source_type, source_ref)
    if existing is not None:
        return existing
    now = _now_iso()
    row = UserMacro(
        user_id=user_id,
        name=_macro_name(macro, name),
        symbol=macro.symbol,
        rule_type=macro.rule_type.value,
        position_side=macro.position_side.value,
        macro_json=macro.model_dump_json(),
        human_summary=human_summary(macro),
        source_type=source_type,
        source_ref=source_ref,
        schema_version="1",
        created_at=created_at or now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def save_snapshot(
    user_id: int,
    macro: Macro,
    *,
    source_type: str,
    source_ref: str,
    name: str = "",
    created_at: str = "",
) -> dict:
    if source_type not in _SOURCES:
        raise ValueError("unsupported user macro source")
    with get_session() as db:
        row = _insert_snapshot(
            db,
            user_id=user_id,
            macro=macro,
            source_type=source_type,
            source_ref=str(source_ref),
            name=name,
            created_at=created_at,
        )
        db.commit()
        db.refresh(row)
        return _view(row)


def save_upload(user_id: int, macro: Macro, name: str = "") -> dict:
    normalized = macro.model_dump_json()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return save_snapshot(
        user_id,
        macro,
        source_type="upload",
        source_ref=digest,
        name=name,
    )


def save_from_leaderboard(user_id: int, entry_id: int) -> dict:
    """Copy one accessible leaderboard macro into the account library."""
    with get_session() as db:
        entry = db.get(LeaderboardEntry, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="리더보드 매크로를 찾을 수 없어요.")

        source_type = "created" if entry.owner_user_id == user_id else "leaderboard"
        if entry.owner_user_id not in {None, user_id}:
            unlocked = db.exec(
                select(MacroUnlock).where(
                    MacroUnlock.user_id == user_id,
                    MacroUnlock.entry_id == entry_id,
                )
            ).first()
            if unlocked is None:
                raise HTTPException(status_code=403, detail="먼저 이 매크로를 언락해 주세요.")

        try:
            macro = Macro.model_validate_json(entry.macro_json)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="리더보드 매크로 형식이 올바르지 않아요.")

        row = _insert_snapshot(
            db,
            user_id=user_id,
            macro=macro,
            source_type=source_type,
            source_ref=str(entry_id),
            name=f"{entry.symbol.removesuffix('USDT')} 리더보드 매크로",
            created_at=entry.created_at,
        )
        db.commit()
        db.refresh(row)
        view = _view(row)
        view["performance"] = _performance(db, row)
        return view


def _sync_legacy(db, user_id: int) -> None:
    """Snapshot still-available pre-library registrations and unlocks once."""
    owned = db.exec(
        select(LeaderboardEntry).where(LeaderboardEntry.owner_user_id == user_id)
    ).all()
    for entry in owned:
        if _find(db, user_id, "created", str(entry.id)) is not None:
            continue
        try:
            macro = Macro.model_validate_json(entry.macro_json)
        except (ValueError, TypeError):
            continue
        _insert_snapshot(
            db,
            user_id=user_id,
            macro=macro,
            source_type="created",
            source_ref=str(entry.id),
            created_at=entry.created_at,
        )

    unlocks = db.exec(select(MacroUnlock).where(MacroUnlock.user_id == user_id)).all()
    entry_ids = [row.entry_id for row in unlocks]
    entries: dict[int, LeaderboardEntry] = {}
    if entry_ids:
        entries = {
            row.id: row
            for row in db.exec(select(LeaderboardEntry).where(LeaderboardEntry.id.in_(entry_ids))).all()
        }
    for unlock in unlocks:
        entry = entries.get(unlock.entry_id)
        if entry is None or _find(db, user_id, "leaderboard", str(entry.id)) is not None:
            continue
        try:
            macro = Macro.model_validate_json(entry.macro_json)
        except (ValueError, TypeError):
            continue
        _insert_snapshot(
            db,
            user_id=user_id,
            macro=macro,
            source_type="leaderboard",
            source_ref=str(entry.id),
            created_at=unlock.created_at,
        )


def list_macros(user_id: int) -> dict:
    with get_session() as db:
        _sync_legacy(db, user_id)
        db.commit()
        rows = db.exec(
            select(UserMacro)
            .where(UserMacro.user_id == user_id)
            .order_by(UserMacro.id.desc())
        ).all()
        items = []
        for row in rows:
            view = _view(row)
            if view["macro"] is not None:
                view["performance"] = _performance(db, row)
                items.append(view)
        return {"items": items}


def get_macro(user_id: int, macro_id: int) -> dict:
    with get_session() as db:
        row = db.get(UserMacro, macro_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="내 매크로를 찾을 수 없어요.")
        view = _view(row)
        if view["macro"] is None:
            raise HTTPException(status_code=422, detail="저장된 매크로 형식이 올바르지 않아요.")
        return view
