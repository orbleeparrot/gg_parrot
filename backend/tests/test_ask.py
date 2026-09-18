"""껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합을 찾는다."""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import select

from app import ask
from app.db import AskMacroSession, User, get_session
from app.engine import BacktestResult
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


def test_request_normalizes_symbols_and_rejects_bad_combos():
    req = ask.AskRequest(risk_profile="balanced", symbols=[" btcusdt ", "BTCUSDT", "ethusdt"])
    assert req.symbols == ["BTCUSDT", "ETHUSDT"]
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="stable", market="futures", leverage=2, symbols=["BTCUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", market="spot", leverage=2, symbols=["BTCUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTC-KRW"])


def test_templates_follow_profile_rules():
    stable = ask.build_templates(ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"], interval="4h"))
    types = {c.macro.rule_type.value for c in stable}
    assert types == {"C", "J", "G", "A"}
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in stable)
    assert all(c.macro.candle_interval == "4h" and c.macro.period.preset == "3m" for c in stable)
    assert all(c.source == "template" for c in stable)

    aggressive = ask.build_templates(ask.AskRequest(
        risk_profile="aggressive", market="futures", leverage=2, symbols=["BTCUSDT"]))
    types = {c.macro.rule_type.value for c in aggressive}
    assert "I" in types and "H" in types and "C" not in types  # C 는 레버리지를 못 쓴다
    assert all(c.macro.leverage == 2 and c.macro.market == "futures" for c in aggressive)


def test_templates_cover_every_symbol_first_and_add_a_portfolio():
    req = ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands = ask.build_templates(req)
    assert len(cands) <= ask.MAX_CANDIDATES
    first_preset = cands[: 3 * 6]  # 3 종목 × 6 유형의 첫 프리셋이 먼저
    assert {c.macro.symbol for c in first_preset} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    portfolios = [c for c in cands if c.macro.symbols]
    assert portfolios and portfolios[0].macro.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all("추천" not in c.label for c in cands)


def test_templates_keep_portfolio_when_cap_is_hit():
    req = ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands = ask.build_templates(req)
    assert len(cands) == ask.MAX_CANDIDATES
    portfolios = [c for c in cands if c.macro.symbols]
    assert portfolios, "상한에 걸려도 포트폴리오 후보는 남아야 한다"
    # 유형마다 종목 3개 + 포트폴리오 1개가 붙어 있고, 뒤쪽(낮은 우선순위) 유형이 잘린다.
    first_type = cands[0].macro.rule_type.value
    assert [c.macro.rule_type.value for c in cands[:4]] == [first_type] * 4
    assert cands[3].macro.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
