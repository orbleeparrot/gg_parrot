"""Authenticated HTTP boundary for the position-news feature."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ... import auth as auth_mod
from ... import runner as runner_mod
from ...db import User
from . import service

router = APIRouter(prefix="/api/me/agents/sessions", tags=["agent-features"])


@router.get("/{session_id}/position-news")
def position_news(
    session_id: int,
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    session = runner_mod.get_owned_session(user.id, session_id)
    try:
        return service.get_position_news(session, requester_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
