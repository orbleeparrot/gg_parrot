"""Authenticated HTTP boundary for the position-news feature."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from ... import auth as auth_mod
from ... import runner as runner_mod
from ... import user_macros as user_macros_mod
from ...db import User, request_session
from ...engine import Macro
from ...news import (
    asset_from_market_symbol,
    canonical_asset_symbol,
    canonical_market_symbol,
)
from . import service

router = APIRouter(prefix="/api/me/agents", tags=["agent-features"])


@router.get("/sessions/{session_id}/position-news")
def position_news(
    session_id: int,
    response: Response,
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    session = runner_mod.get_owned_session(user.id, session_id, db=db)
    try:
        return service.get_position_news(session, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/macros/{macro_id}/position-news")
def macro_position_news(
    macro_id: int,
    response: Response,
    symbol: str | None = None,
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """Project one shared ticker snapshot onto an account-owned saved macro."""
    response.headers["Cache-Control"] = "private, no-store"
    saved = user_macros_mod.get_macro(user.id, macro_id, db=db)
    try:
        macro = Macro.model_validate(saved["macro"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="저장된 매크로 형식이 올바르지 않아요.",
        ) from exc

    members = macro.all_symbols()
    if symbol:
        requested_market = canonical_market_symbol(symbol)
        selected_symbol = next(
            (
                member
                for member in members
                if canonical_market_symbol(member) == requested_market
            ),
            "",
        )
        if not selected_symbol:
            requested_assets = dict.fromkeys(
                (
                    canonical_asset_symbol(requested_market),
                    asset_from_market_symbol(requested_market),
                )
            )
            for requested_asset in requested_assets:
                if not requested_asset:
                    continue
                selected_symbol = next(
                    (
                        member
                        for member in members
                        if asset_from_market_symbol(member) == requested_asset
                    ),
                    "",
                )
                if selected_symbol:
                    break
        if not requested_market or not selected_symbol:
            raise HTTPException(
                status_code=422,
                detail="이 매크로에 포함된 종목만 조회할 수 있어요.",
            )
    else:
        selected_symbol = members[0]

    return service.get_position_news(
        {
            "session_id": None,
            "user_macro_id": saved["id"],
            "symbol": selected_symbol,
            "position_side": macro.position_side.value,
            "in_position": False,
        },
        db=db,
    )
