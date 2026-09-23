"""껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합을 찾는다."""
from __future__ import annotations

import json
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import select

from app import ask
from app.data.binance import MAX_BACKTEST_BARS, PERIOD_PRESET_DAYS, _expected_bar_count
from app.db import AskMacroSession, PointLedger, User, get_session
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


def test_templates_cover_every_type_before_portfolios():
    # 공격형 × 종목 3개: 유형 8개 × 종목 3개 = 24 로 단일 후보만으로 상한이 찬다 — 포트폴리오는 못 들어간다.
    req3 = ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands3 = ask.build_templates(req3)
    assert len(cands3) == ask.MAX_CANDIDATES
    types3 = {c.macro.rule_type.value for c in cands3}
    assert {"I", "H"} <= types3
    assert [c for c in cands3 if c.macro.symbols] == []

    # 공격형 × 종목 2개: 유형 8개 × 종목 2개(16) + 포트폴리오 8개 = 24 — 포트폴리오가 남고 모든 유형이 단일에 있다.
    req2 = ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT"])
    cands2 = ask.build_templates(req2)
    portfolios2 = [c for c in cands2 if c.macro.symbols]
    singles2 = [c for c in cands2 if not c.macro.symbols]
    assert portfolios2
    assert set(ask._allowed_types(req2)) <= {c.macro.rule_type.value for c in singles2}


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


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeMessages:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"content": [_FakeBlock(self.text)]})()


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _fake_runtime():
    class RT:
        def call(self, key, load, **kwargs):
            return load(), "miss"
    return RT()


def test_ai_proposals_are_validated_and_filtered(monkeypatch):
    req = ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"], interval="1d")
    monkeypatch.setattr(ask, "ai_available", lambda: False)
    assert ask.propose_with_ai(req) == []

    text = json.dumps({"macros": [
        {"rule_type": "J", "params": {"ma_type": "EMA", "fast_period": 12, "slow_period": 26, "initial_capital": 1000000}},
        {"rule_type": "H", "params": {"base_order_size": 1, "safety_order_size": 1, "price_deviation": 1, "take_profit": 1, "initial_capital": 1000000}},  # 안정형 밖 → 폐기
        {"rule_type": "J", "symbol": "SOLUSDT", "params": {"ma_type": "SMA", "fast_period": 5, "slow_period": 20, "initial_capital": 1000000}},  # 종목 바꿔치기 → 요청 종목으로 고정
        {"rule_type": "F", "params": {"rsi_period": "x"}},  # 스키마 불량 → 폐기
    ]})
    client_ok = _FakeClient(text)
    monkeypatch.setattr(ask, "ai_available", lambda: True)
    monkeypatch.setattr(ask, "get_ai_client", lambda: client_ok)
    monkeypatch.setattr(ask, "get_ai_runtime", _fake_runtime)
    out = ask.propose_with_ai(req)
    assert [c.macro.rule_type.value for c in out] == ["J", "J"]
    assert all(c.macro.symbol == "BTCUSDT" and c.macro.candle_interval == "1d" and c.source == "ai" for c in out)
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in out)
    system = client_ok.messages.calls[0]["system"]
    assert "추천" not in system and "C, J, G, A" in system

    client_bad = _FakeClient("not json")
    monkeypatch.setattr(ask, "get_ai_client", lambda: client_bad)
    assert ask.propose_with_ai(req) == []


_RETURNS = {"A": (4, 3, 8), "C": (2, 1, 12), "J": (9, 4, 6), "G": (6, 2, 5), "F": (7, 3, 2), "E": (5, 2, 4)}


@pytest.fixture
def _fake_backtest(monkeypatch):
    """유형별로 정해진 수치를 돌려주는 가짜 백테스트 — 네트워크 없이 선별 결과를 예측할 수 있다."""
    def fake_run_any(macro):
        ret, mdd, trades = _RETURNS.get(macro.rule_type.value, (1, 1, 5))
        return _result(ret, mdd, trades=trades), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    monkeypatch.setattr(ask, "ai_available", lambda: False)


_BODY = {"risk_profile": "balanced", "market": "spot", "leverage": 1,
         "symbols": ["BTCUSDT"], "period_preset": "3m", "interval": "1h"}


def test_status_and_consent_flow(_fake_backtest):
    token, _ = _signup()
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st == {"consented": False, "remaining_today": 5, "daily_limit": 5, "disclaimer_version": "ask-v1",
                  "extra_price": 30, "extra_left_today": 5, "points_balance": 1000}

    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 403

    ok = client.post("/api/ask/consent", headers=_auth(token)).json()
    assert ok == {"ok": True, "version": "ask-v1"}
    assert client.get("/api/ask/status", headers=_auth(token)).json()["consented"] is True
    assert client.get("/api/ask/status").status_code == 401


def test_macros_returns_top3_distinct_types_and_records(_fake_backtest):
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # 균형형 점수 = 수익률 - 0.5·MDD: J 7 > G 5 > E 4 > A 2.5 > C 1.5 ; F 는 거래 2회라 탈락
    assert [r["rule_type"] for r in data["results"]] == ["J", "G", "E"]
    first = data["results"][0]
    assert first["metrics"] == {"final_return_pct": 9, "mdd_pct": 4, "win_rate_pct": 50.0, "total_trades": 6}
    assert first["macro"]["symbol"] == "BTCUSDT" and first["macro"]["rule_type"] == "J"
    assert first["explanation"]["headline"] and first["ai_generated"] is False
    assert "추천" not in json.dumps(data, ensure_ascii=False)
    assert data["disclaimer"] == ask.DISCLAIMER and data["disclaimer_version"] == "ask-v1"
    assert data["remaining_today"] == 4 and data["ai_used"] is False and data["candidate_count"] > 3

    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert len(rows) == 1
        assert json.loads(rows[0].request_json)["symbols"] == ["BTCUSDT"]
        assert [r["rule_type"] for r in json.loads(rows[0].results_json)] == ["J", "G", "E"]
        assert rows[0].day_kst == ask.today_kst()


def test_macros_validation_and_daily_limit(_fake_backtest, monkeypatch):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    bad = dict(_BODY, risk_profile="stable", market="futures", leverage=2)
    assert client.post("/api/ask/macros", json=bad, headers=_auth(token)).status_code == 422
    assert client.post("/api/ask/macros", json=dict(_BODY, symbols=["A", "B", "C", "D"]), headers=_auth(token)).status_code == 422

    monkeypatch.setenv("ASK_DAILY_LIMIT", "2")
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()["remaining_today"] == 1
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()["remaining_today"] == 0
    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 429 and "내일" in res.json()["detail"]


def test_macros_with_no_candidates_returns_empty_results(_fake_backtest, monkeypatch):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))

    def boom(macro):
        raise RuntimeError("no data")

    monkeypatch.setattr("app.main._run_any", boom)
    data = client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()
    assert data["results"] == [] and data["remaining_today"] == 4


def test_in_flight_guard_rejects_concurrent_ask(_fake_backtest):
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    ask._IN_FLIGHT.add(user_id)
    try:
        res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
        assert res.status_code == 429
        assert "돌리는 중" in res.json()["detail"]
    finally:
        ask._IN_FLIGHT.discard(user_id)


def test_failed_ask_does_not_consume_quota(_fake_backtest, monkeypatch):
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))

    def boom(macro):
        raise ask.NoSpotDataError("no data")

    monkeypatch.setattr("app.main._run_any", boom)
    res = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert res.status_code == 422

    assert client.get("/api/ask/status", headers=_auth(token)).json()["remaining_today"] == 5
    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert rows == []


def test_scalper_profile_candidates_use_short_presets_and_skip_slow_types():
    req = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
    cands = ask.build_templates(req)
    types = {c.macro.rule_type.value for c in cands}
    assert types == {"A", "E", "F", "G", "J"}
    a_first = next(c for c in cands if c.macro.rule_type.value == "A")
    assert a_first.macro.params["take_profit_pct"] == 1.0 and a_first.macro.risk.stop_loss_pct == 0.7
    e_first = next(c for c in cands if c.macro.rule_type.value == "E")
    assert e_first.macro.risk.stop_loss_pct == 0.7
    j_first = next(c for c in cands if c.macro.rule_type.value == "J")
    assert (j_first.macro.params["ma_type"], j_first.macro.params["fast_period"], j_first.macro.params["slow_period"]) == ("EMA", 5, 13)
    assert all(c.macro.candle_interval == "5m" and c.macro.period.preset == "1w" for c in cands)
    # 기존 성향은 그대로 긴 프리셋
    slow = ask.build_templates(ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT"]))
    assert next(c for c in slow if c.macro.rule_type.value == "A").macro.params["take_profit_pct"] == 3


def test_scalper_request_pairs_interval_and_period():
    ok = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="1m")
    assert (ok.interval, ok.period_preset) == ("1m", "1w")
    ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT", "ETHUSDT"], period_preset="1m", interval="15m")
    with pytest.raises(ValidationError, match="1분 봉"):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1m", interval="1m")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="3m", interval="5m")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="1h")
    with pytest.raises(ValidationError, match="2개"):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], period_preset="1w", interval="5m")


def test_non_scalper_request_rejects_short_options():
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT"], period_preset="1w", interval="1h")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT"], period_preset="3m", interval="5m")
    # 기본값은 그대로 유효
    assert ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"]).interval == "1h"


def test_select_top_uses_profile_min_trades():
    def cand(rule_type):
        req = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
        return ask.Candidate(f"{rule_type}", ask._make_macro(req, rule_type, ask._SCALPER_PRESETS[rule_type][0], ["BTCUSDT"]), "template")
    ev = [ask.Evaluated(cand("A"), _result(5, 2, trades=9)), ask.Evaluated(cand("J"), _result(3, 2, trades=10))]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "scalper")] == ["J"]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "aggressive")] == ["A", "J"]


def test_scalper_ask_end_to_end(_fake_backtest):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    body = {"risk_profile": "scalper", "market": "spot", "leverage": 1, "symbols": ["BTCUSDT"], "period_preset": "1w", "interval": "1m"}
    res = client.post("/api/ask/macros", json=body, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # _RETURNS: 거래 수 A 8·J 6·G 5·F 2·E 4 → 전부 10 미만이라 단타형 최소 거래 수에 걸려 결과 없음
    assert data["results"] == [] and data["candidate_count"] > 0
    assert all(r["macro"]["candle_interval"] == "1m" for r in data["results"])
    bad = dict(body, period_preset="1m")
    assert client.post("/api/ask/macros", json=bad, headers=_auth(token)).status_code == 422


def test_scalper_ask_returns_short_macros_when_trades_suffice(_fake_backtest, monkeypatch):
    def fake_run_any(macro):
        return _result(4, 2, trades=12), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    body = {"risk_profile": "scalper", "market": "spot", "leverage": 1,
            "symbols": ["BTCUSDT"], "period_preset": "1w", "interval": "1m"}
    res = client.post("/api/ask/macros", json=body, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    results = data["results"]
    assert len(results) == 3
    rule_types = [r["rule_type"] for r in results]
    assert len(set(rule_types)) == len(rule_types)
    assert set(rule_types) <= {"A", "E", "F", "G", "J"}
    for r in results:
        macro = r["macro"]
        assert macro["candle_interval"] == "1m"
        assert macro["period"]["preset"] == "1w"
        assert "fees" in macro and macro["fees"].get("commission_pct") is not None
    assert data["remaining_today"] == 4


def test_every_reachable_pair_fits_backtest_bar_cap():
    # ask.py 의 봉×기간 짝 검증 규칙은 오직 이 상한(MAX_BACKTEST_BARS)을 넘기지 않기 위해 존재한다.
    now_ms = int(time.time() * 1000)
    for profile, cfg in ask.PROFILES.items():
        short = cfg["short"]
        intervals = ask.SHORT_INTERVALS if short else ask.LONG_INTERVALS
        periods = ask.SHORT_PERIODS if short else ask.LONG_PERIODS
        for interval in intervals:
            for period in periods:
                try:
                    ask.AskRequest(risk_profile=profile, symbols=["BTCUSDT"],
                                    period_preset=period, interval=interval)
                except ValidationError:
                    continue
                days = PERIOD_PRESET_DAYS[period]
                start_ms = now_ms - days * 86_400_000
                bars = _expected_bar_count(interval, start_ms, now_ms)
                assert bars <= MAX_BACKTEST_BARS


# --- 포인트로 횟수 추가 (2026-09-22): 무료 5회 소진 후 1회 30P, 하루 추가 상한 5회 ---------------
def _set_points(user_id: int, balance: int) -> None:
    with get_session() as db:
        u = db.get(User, user_id)
        u.points_balance = balance
        db.add(u)
        db.commit()


def test_status_exposes_extra_price_and_points(_fake_backtest):
    token, uid = _signup()
    _set_points(uid, 100)
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st["extra_price"] == 30 and st["extra_left_today"] == 5 and st["points_balance"] == 100


def test_extra_credit_is_only_purchasable_after_free_quota_is_gone(_fake_backtest, monkeypatch):
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    _set_points(uid, 100)
    res = client.post("/api/ask/extra", headers=_auth(token))
    assert res.status_code == 409 and "무료" in res.json()["detail"]


def test_extra_credit_costs_points_and_adds_one_ask(_fake_backtest, monkeypatch):
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "1")
    _set_points(uid, 100)
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).json()["remaining_today"] == 0
    res = client.post("/api/ask/extra", headers=_auth(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["remaining_today"] == 1 and body["points_balance"] == 70 and body["extra_left_today"] == 4
    with get_session() as db:
        ledger = db.exec(select(PointLedger).where(PointLedger.user_id == uid, PointLedger.reason == "ask_extra")).all()
        assert [l.delta for l in ledger] == [-30]
    run = client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert run.status_code == 200 and run.json()["remaining_today"] == 0
    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == uid).order_by(AskMacroSession.id)).all()
        assert [r.paid for r in rows] == [False, True]
    assert client.post("/api/ask/macros", json=_BODY, headers=_auth(token)).status_code == 429


def test_extra_credit_rejects_when_points_are_short(_fake_backtest, monkeypatch):
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "0")
    _set_points(uid, 29)
    res = client.post("/api/ask/extra", headers=_auth(token))
    assert res.status_code == 402 and "부족" in res.json()["detail"]
    assert client.get("/api/ask/status", headers=_auth(token)).json()["points_balance"] == 29


def test_extra_credit_daily_cap_is_five(_fake_backtest, monkeypatch):
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "0")
    _set_points(uid, 1000)
    for i in range(5):
        assert client.post("/api/ask/extra", headers=_auth(token)).status_code == 200, i
    res = client.post("/api/ask/extra", headers=_auth(token))
    assert res.status_code == 429 and "추가" in res.json()["detail"]
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st["remaining_today"] == 5 and st["extra_left_today"] == 0 and st["points_balance"] == 850


def test_failed_ask_returns_the_extra_credit(_fake_backtest, monkeypatch):
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "0")
    _set_points(uid, 100)
    assert client.post("/api/ask/extra", headers=_auth(token)).status_code == 200
    monkeypatch.setattr(ask, "build_templates", lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):  # TestClient 는 서버 예외를 그대로 올린다
        client.post("/api/ask/macros", json=_BODY, headers=_auth(token))
    assert client.get("/api/ask/status", headers=_auth(token)).json()["remaining_today"] == 1


def test_horizon_maps_to_backtest_period():
    assert ask.to_period("balanced", "days") == "1w"
    assert ask.to_period("balanced", "weeks") == "3m"
    assert ask.to_period("balanced", "months") == "6m"
    assert ask.to_period("balanced", "long") == "1y"


def test_scalper_period_is_clamped_to_short_presets():
    # 단타형은 짧은 구간만 — 1m 은 1개월이다(봉의 1분이 아니다)
    assert ask.to_period("scalper", "days") == "1w"
    assert ask.to_period("scalper", "weeks") == "1m"
    assert ask.to_period("scalper", "months") == "1m"
    assert ask.to_period("scalper", "long") == "1m"


def test_watch_maps_to_interval():
    assert ask.to_interval("balanced", "rarely", "6m") == "1d"
    assert ask.to_interval("balanced", "sometimes", "6m") == "4h"
    assert ask.to_interval("balanced", "often", "6m") == "1h"


def test_scalper_watch_maps_to_short_intervals():
    assert ask.to_interval("scalper", "rarely", "1m") == "15m"
    assert ask.to_interval("scalper", "sometimes", "1m") == "5m"
    # 1분 봉은 구간이 1주일 때만 — 아니면 5분으로 낮춘다
    assert ask.to_interval("scalper", "often", "1w") == "1m"
    assert ask.to_interval("scalper", "often", "1m") == "5m"
