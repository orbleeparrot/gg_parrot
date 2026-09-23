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
from app import ask_candidates as ac
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


def test_ask_request_normalizes_symbol_and_rejects_bad_format():
    # v2 — AskRequest 는 session_id·symbol 만 받는다. 성향·기간 검증은 CandidatesRequest 로 옮겨갔다.
    req = ask.AskRequest(session_id=1, symbol=" btcusdt ")
    assert req.symbol == "BTCUSDT"
    with pytest.raises(ValidationError):
        ask.AskRequest(session_id=1, symbol="BTC-KRW")
    with pytest.raises(ValidationError):
        ask.AskRequest(symbol="BTCUSDT")  # session_id 없음


def _mkplan(risk_profile="balanced", symbols=("BTCUSDT",), market="spot", leverage=1,
            period_preset="3m", interval="1h"):
    """build_templates 등 기존 후보 생성 코드가 읽는 내부 전용 요청 모양(ask._Plan)을 만든다."""
    return ask._Plan(risk_profile=risk_profile, market=market, leverage=leverage,
                      symbols=list(symbols), period_preset=period_preset, interval=interval)


def test_templates_follow_profile_rules():
    stable = ask.build_templates(_mkplan(risk_profile="stable", symbols=["BTCUSDT"], interval="4h"))
    types = {c.macro.rule_type.value for c in stable}
    assert types == {"C", "J", "G", "A"}
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in stable)
    assert all(c.macro.candle_interval == "4h" and c.macro.period.preset == "3m" for c in stable)
    assert all(c.source == "template" for c in stable)

    aggressive = ask.build_templates(_mkplan(
        risk_profile="aggressive", market="futures", leverage=2, symbols=["BTCUSDT"]))
    types = {c.macro.rule_type.value for c in aggressive}
    assert "I" in types and "H" in types and "C" not in types  # C 는 레버리지를 못 쓴다
    assert all(c.macro.leverage == 2 and c.macro.market == "futures" for c in aggressive)


def test_templates_cover_every_symbol_first_and_add_a_portfolio():
    req = _mkplan(risk_profile="balanced", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands = ask.build_templates(req)
    assert len(cands) <= ask.MAX_CANDIDATES
    first_preset = cands[: 3 * 6]  # 3 종목 × 6 유형의 첫 프리셋이 먼저
    assert {c.macro.symbol for c in first_preset} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    portfolios = [c for c in cands if c.macro.symbols]
    assert portfolios and portfolios[0].macro.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all("추천" not in c.label for c in cands)


def test_templates_cover_every_type_before_portfolios():
    # 공격형 × 종목 3개: 유형 8개 × 종목 3개 = 24 로 단일 후보만으로 상한이 찬다 — 포트폴리오는 못 들어간다.
    req3 = _mkplan(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands3 = ask.build_templates(req3)
    assert len(cands3) == ask.MAX_CANDIDATES
    types3 = {c.macro.rule_type.value for c in cands3}
    assert {"I", "H"} <= types3
    assert [c for c in cands3 if c.macro.symbols] == []

    # 공격형 × 종목 2개: 유형 8개 × 종목 2개(16) + 포트폴리오 8개 = 24 — 포트폴리오가 남고 모든 유형이 단일에 있다.
    req2 = _mkplan(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT"])
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
    req = _mkplan(risk_profile="aggressive", symbols=[symbol])
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
    req = _mkplan(risk_profile="stable", symbols=["BTCUSDT"], interval="1d")
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


def test_status_and_consent_flow(_fake_backtest):
    token, _ = _signup()
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st == {"consented": False, "remaining_today": 5, "daily_limit": 5, "disclaimer_version": "ask-v2",
                  "extra_price": 30, "extra_left_today": 5, "points_balance": 1000}

    # 동의 전엔 세션이 있든 없든(여기선 없는 session_id) 종목 선택 자체가 막힌다 — consented() 가 먼저 걸린다.
    res = client.post("/api/ask/macros", json={"session_id": 1, "symbol": "BTCUSDT"}, headers=_auth(token))
    assert res.status_code == 403

    ok = client.post("/api/ask/consent", headers=_auth(token)).json()
    assert ok == {"ok": True, "version": "ask-v2"}
    assert client.get("/api/ask/status", headers=_auth(token)).json()["consented"] is True
    assert client.get("/api/ask/status").status_code == 401


def test_ask_returns_top3_distinct_types_and_records(_fake_backtest, monkeypatch):
    # v2 — 흐름은 2단계다: 후보를 낸 candidates 호출에서 1회 차감되고, 종목을 고르는
    # /api/ask 는 차감 없이 그 세션을 갱신한다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()

    res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                      headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # 균형형 점수 = 수익률 - 0.5·MDD: J 7 > G 5 > E 4 > A 2.5 > C 1.5 ; F 는 거래 2회라 탈락
    assert [r["rule_type"] for r in data["results"]] == ["J", "G", "E"]
    first = data["results"][0]
    assert first["metrics"] == {"final_return_pct": 9, "mdd_pct": 4, "win_rate_pct": 50.0, "total_trades": 6}
    assert first["macro"]["symbol"] == "BTCUSDT" and first["macro"]["rule_type"] == "J"
    assert first["explanation"]["headline"] and first["ai_generated"] is False
    assert "추천" not in json.dumps(data, ensure_ascii=False)
    assert data["disclaimer"] == ask.DISCLAIMER and data["disclaimer_version"] == "ask-v2"
    assert data["remaining_today"] == 4  # candidates 호출에서만 1회 차감됐고 /api/ask 는 0회

    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == user_id)).all()
        assert len(rows) == 1
        assert json.loads(rows[0].request_json)["risk_profile"] == "balanced"
        assert [r["rule_type"] for r in json.loads(rows[0].results_json)] == ["J", "G", "E"]
        assert rows[0].day_kst == ask.today_kst()
        assert rows[0].chosen_symbol == "BTCUSDT" and rows[0].ask_count == 1
        assert rows[0].candidate_count > 3 and rows[0].ai_used is False


def test_candidates_validation_and_daily_limit(_fake_backtest, monkeypatch):
    # v1 의 성향·시장 검증(레버리지·종목 개수)은 이제 후보를 내는 CandidatesRequest 쪽 규칙이고,
    # 하루 한도도 후보를 낼 때(=차감이 일어나는 곳)에서 소진된다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    bad = _candidates_body(risk_profile="stable", market="futures", leverage=2)
    assert client.post("/api/ask/candidates", json=bad, headers=_auth(token)).status_code == 422

    monkeypatch.setenv("ASK_DAILY_LIMIT", "2")
    assert client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()["remaining_today"] == 1
    assert client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()["remaining_today"] == 0
    res = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token))
    assert res.status_code == 429 and "내일" in res.json()["detail"]


def test_ask_with_no_candidates_returns_empty_results(_fake_backtest, monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()

    def boom(macro):
        raise RuntimeError("no data")

    monkeypatch.setattr("app.main._run_any", boom)
    data = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                       headers=_auth(token)).json()
    assert data["results"] == [] and data["remaining_today"] == 4


def test_in_flight_guard_rejects_concurrent_ask(_fake_backtest, monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()
    ask._IN_FLIGHT.add(user_id)
    try:
        res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                          headers=_auth(token))
        assert res.status_code == 429
        assert "돌리는 중" in res.json()["detail"]
    finally:
        ask._IN_FLIGHT.discard(user_id)


def test_failed_ask_does_not_consume_session_budget(_fake_backtest, monkeypatch):
    # v2 — 실패한 /api/ask 시도는 하루 남은 횟수도, 세션의 호출 수(ask_count)도 깎지 않는다.
    # 세션 행 자체도(v1 과 달리) 지우지 않는다 — 같은 세션으로 다시 시도할 수 있어야 한다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()

    def boom(macro):
        raise ask.NoSpotDataError("no data")

    monkeypatch.setattr("app.main._run_any", boom)
    res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                      headers=_auth(token))
    assert res.status_code == 422

    assert client.get("/api/ask/status", headers=_auth(token)).json()["remaining_today"] == 4
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        assert row is not None and row.ask_count == 0


def test_scalper_profile_candidates_use_short_presets_and_skip_slow_types():
    req = _mkplan(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
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
    slow = ask.build_templates(_mkplan(risk_profile="aggressive", symbols=["BTCUSDT"]))
    assert next(c for c in slow if c.macro.rule_type.value == "A").macro.params["take_profit_pct"] == 3


# 예전엔 AskRequest 자체가 성향별 interval·period_preset 짝을 검증했지만(예: 단타형은 1분·5분·15분
# 봉만), v2 에서 그 값은 클라이언트가 보내지 않고 to_period·to_interval 이 답변에서 계산해 늘 유효한
# 짝만 만든다. 그 불변식은 test_horizon_maps_to_backtest_period·test_watch_maps_to_interval·
# test_scalper_period_is_clamped_to_short_presets·test_scalper_watch_maps_to_short_intervals ·
# test_every_reachable_pair_fits_backtest_bar_cap 가 대신 지킨다.


def test_select_top_uses_profile_min_trades():
    def cand(rule_type):
        req = _mkplan(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
        return ask.Candidate(f"{rule_type}", ask._make_macro(req, rule_type, ask._SCALPER_PRESETS[rule_type][0], ["BTCUSDT"]), "template")
    ev = [ask.Evaluated(cand("A"), _result(5, 2, trades=9)), ask.Evaluated(cand("J"), _result(3, 2, trades=10))]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "scalper")] == ["J"]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "aggressive")] == ["A", "J"]


def test_scalper_ask_end_to_end(_fake_backtest, monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    # invest_horizon="days" + watch_frequency="often" → to_period="1w", to_interval="1m"(1주에서만 허용) — v1 과 같은 조합.
    flow = client.post("/api/ask/candidates",
                       json=_candidates_body(risk_profile="scalper", invest_horizon="days", watch_frequency="often"),
                       headers=_auth(token)).json()
    res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                      headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # _RETURNS: 거래 수 A 8·J 6·G 5·F 2·E 4 → 전부 10 미만이라 단타형 최소 거래 수에 걸려 결과 없음
    assert data["results"] == []
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        assert row.candidate_count > 0


def test_scalper_ask_returns_short_macros_when_trades_suffice(_fake_backtest, monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)

    def fake_run_any(macro):
        return _result(4, 2, trades=12), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates",
                       json=_candidates_body(risk_profile="scalper", invest_horizon="days", watch_frequency="often"),
                       headers=_auth(token)).json()
    res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                      headers=_auth(token))
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
    # v2 — 봉×기간 짝을 사람이 직접 보내지 않고 to_period·to_interval 이 답변(투자 기간·볼 빈도)에서
    # 계산한다. 그 계산이 만들 수 있는 모든 조합이 이 상한(MAX_BACKTEST_BARS)을 넘지 않는지 본다.
    now_ms = int(time.time() * 1000)
    for profile in ask.PROFILES:
        for horizon in ask.HORIZONS:
            period = ask.to_period(profile, horizon)
            for watch in ask.WATCH_LEVELS:
                interval = ask.to_interval(profile, watch, period)
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


def test_extra_credit_costs_points_and_adds_one_use(_fake_backtest, monkeypatch):
    # v2 — 차감은 candidates 호출에서만 일어나므로, 추가권 소비 여부도 거기서 확인한다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "1")
    _set_points(uid, 100)
    assert client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()["remaining_today"] == 0
    res = client.post("/api/ask/extra", headers=_auth(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["remaining_today"] == 1 and body["points_balance"] == 70 and body["extra_left_today"] == 4
    with get_session() as db:
        ledger = db.exec(select(PointLedger).where(PointLedger.user_id == uid, PointLedger.reason == "ask_extra")).all()
        assert [l.delta for l in ledger] == [-30]
    run = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token))
    assert run.status_code == 200 and run.json()["remaining_today"] == 0
    with get_session() as db:
        rows = db.exec(select(AskMacroSession).where(AskMacroSession.user_id == uid).order_by(AskMacroSession.id)).all()
        assert [r.paid for r in rows] == [False, True]
    assert client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).status_code == 429


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


def test_failed_candidates_returns_the_extra_credit(_fake_backtest, monkeypatch):
    # v2 — 차감이 candidates 호출에서 일어나므로, 실패 시 추가권을 돌려주는 것도 그 함수의 몫이다
    # (run_ask 는 애초에 차감하지 않으니 돌려줄 것도 없다).
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    token, uid = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "0")
    _set_points(uid, 100)
    assert client.post("/api/ask/extra", headers=_auth(token)).status_code == 200
    monkeypatch.setattr(ask.ask_candidates, "build_pool",
                        lambda tickers, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):  # TestClient 는 서버 예외를 그대로 올린다
        client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token))
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


def _consent(tok):
    client.post("/api/ask/consent", headers=_auth(tok))


def _candidates_body(**over):
    body = {"risk_profile": "balanced", "market": "spot", "leverage": 1,
            "invest_horizon": "weeks", "watch_frequency": "sometimes"}
    body.update(over)
    return body


def _fake_tickers():
    def t(symbol, qvol, high, low):
        return {"symbol": symbol, "priceChangePercent": "1.0", "lastPrice": str(low),
                "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}
    return [t("AAAUSDT", 900_000_000, 101, 100), t("BBBUSDT", 800_000_000, 105, 100),
            t("CCCUSDT", 700_000_000, 120, 100), t("DDDUSDT", 600_000_000, 110, 100)]


def test_candidates_consume_exactly_one_use(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)  # AI 없이 규칙 폴백
    tok, user_id = _signup()
    _consent(tok)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    body = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["session_id"]
    assert ac.MIN_PICKS <= len(data["candidates"]) <= ac.MAX_PICKS
    assert all(c["reason"] for c in data["candidates"])
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before - 1


def test_candidates_survive_ai_failure(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())

    def boom():
        def ask_ai(prompt):
            raise RuntimeError("gemini down")
        return ask_ai

    monkeypatch.setattr(ask, "_candidate_ai", boom)
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 200
    assert len(r.json()["candidates"]) >= 3


def test_candidates_record_pool_and_expiry(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, user_id = _signup()
    _consent(tok)
    sid = client.post("/api/ask/candidates", json=_candidates_body(),
                      headers=_auth(tok)).json()["session_id"]
    with get_session() as db:
        row = db.get(AskMacroSession, sid)
        assert row.disclaimer_version == "ask-v2"
        assert json.loads(row.candidates_json)
        assert row.expires_ms > row.created_ms
        assert row.ask_count == 0


def test_candidates_need_consent():
    tok, _ = _signup()
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 403


def test_candidates_reject_futures_for_stable_profile(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/candidates",
                    json=_candidates_body(risk_profile="stable", market="futures", leverage=2),
                    headers=_auth(tok))
    assert r.status_code == 422


def test_candidates_refund_when_source_is_down(monkeypatch):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: None)
    tok, _ = _signup()
    _consent(tok)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
    assert r.status_code == 503
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before


# --- 흐름 2단계: 세션에서 종목을 골라 매크로 후보를 낸다(차감 없음) -----------------------------
def _start_flow(tok, monkeypatch, **over):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    _consent(tok)
    return client.post("/api/ask/candidates", json=_candidates_body(**over),
                       headers=_auth(tok)).json()


def test_choosing_symbol_does_not_consume_a_use(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    symbol = flow["candidates"][0]["symbol"]
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": symbol},
                    headers=_auth(tok))
    assert r.status_code in (200, 422)  # 시세가 없으면 422 — 차감 여부만 본다
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before


def test_symbol_outside_candidates_and_manual_list_is_rejected(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "SCAMUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 422


def test_manual_symbol_is_allowed(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code != 422 or "찾지 못" in r.text  # 목록 거부(422)는 아니어야 한다


def test_expired_session_is_rejected(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        row.expires_ms = 1
        db.add(row)
        db.commit()
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 410


def test_session_call_budget_is_enforced(monkeypatch):
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        row.ask_count = ask.MAX_ASKS_PER_SESSION
        db.add(row)
        db.commit()
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 409


def test_other_users_session_is_not_readable(monkeypatch):
    tok_a, _ = _signup()
    flow = _start_flow(tok_a, monkeypatch)
    tok_b, _ = _signup()
    _consent(tok_b)
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok_b))
    assert r.status_code == 404


def test_old_request_shape_gets_refresh_hint(monkeypatch):
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/macros", json={"risk_profile": "balanced", "market": "spot",
                                      "leverage": 1, "symbols": ["BTCUSDT"],
                                      "period_preset": "6m", "interval": "4h"},
                    headers=_auth(tok))
    assert r.status_code == 422
    assert "새로고침" in r.text
