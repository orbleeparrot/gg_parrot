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


def _result(ret, mdd, trades=10, win=50.0):
    return BacktestResult(
        initial_capital=1.0, final_equity=1.0 + ret / 100, final_return_pct=ret, mdd_pct=mdd,
        win_rate_pct=win, total_trades=trades, trades=[], equity_curve=[],
    )


def _cand(rule_type, symbol="BTCUSDT"):
    req = ask.AskRequest(risk_profile="aggressive", symbols=[symbol])
    preset = ask._PRESETS[rule_type][0]
    return ask.Candidate(f"{rule_type} · 1h · {symbol}", ask._make_macro(req, rule_type, preset, [symbol]), "template")


def test_evaluate_skips_failures_and_respects_time_budget(monkeypatch):
    def run(macro):
        if macro.rule_type.value == "F":
            raise RuntimeError("no data")
        return _result(5, 3)

    out = ask.evaluate([_cand("A"), _cand("F"), _cand("J")], run, time_budget_sec=60)
    assert [e.candidate.macro.rule_type.value for e in out] == ["A", "J"]

    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(ask.time, "monotonic", lambda: next(clock))
    out = ask.evaluate([_cand("A"), _cand("J"), _cand("E")], run, time_budget_sec=10)
    assert len(out) == 1  # 예산이 끝나면 남은 후보는 건너뛴다


def test_select_top_filters_scores_and_keeps_rule_types_distinct():
    ev = [
        ask.Evaluated(_cand("A"), _result(30, 25)),   # 균형형 MDD 상한(20) 초과 → 탈락
        ask.Evaluated(_cand("J"), _result(12, 4)),
        ask.Evaluated(_cand("J", "ETHUSDT"), _result(11, 3)),  # 같은 유형 두 번째 → 다양성으로 탈락
        ask.Evaluated(_cand("G"), _result(8, 2)),
        ask.Evaluated(_cand("E"), _result(9, 2, trades=2)),    # 거래 2회 → 탈락
        ask.Evaluated(_cand("F"), _result(3, 1)),
    ]
    top = ask.select_top(ev, "balanced")
    assert [e.candidate.macro.rule_type.value for e in top] == ["J", "G", "F"]

    # 안정형은 수익률 ÷ MDD, 공격형은 수익률만 본다.
    assert ask.score("stable", _result(10, 5)) == pytest.approx(2.0)
    assert ask.score("stable", _result(10, 0.2)) == pytest.approx(10.0)  # MDD 는 1 아래로 나누지 않는다
    assert ask.score("balanced", _result(10, 4)) == pytest.approx(8.0)
    assert ask.score("aggressive", _result(10, 40)) == pytest.approx(10.0)
    assert ask.select_top([], "stable") == []
