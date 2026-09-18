"""껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합을 찾는다."""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
# from pydantic import ValidationError  # Task 2 에서 푼다
from sqlmodel import select

# from app import ask  # Task 2 에서 푼다
from app.db import AskMacroSession, User, get_session
# from app.engine import BacktestResult  # Task 2 에서 푼다
from app.main import app

client = TestClient(app)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"ask{tok}@ex.com", "username": f"ask_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_user_has_consent_columns_and_session_table_exists():
    _, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        assert user.ask_consent_version == ""
        assert user.ask_consent_at == ""
        db.add(AskMacroSession(
            user_id=user_id, day_kst="2026-09-18", request_json="{}", candidate_count=0,
            results_json="[]", disclaimer_version="ask-v1", ai_used=False, elapsed_ms=1,
            created_at="2026-09-18T00:00:00Z", created_ms=1,
        ))
        db.commit()
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert len(rows) == 1 and rows[0].day_kst == "2026-09-18"
