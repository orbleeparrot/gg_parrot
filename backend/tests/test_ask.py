"""껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합을 찾는다."""
from __future__ import annotations

import json
import re
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
from engine import BacktestResult
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
    # 2026-10-12: 매매 방식 화이트리스트를 없앴다. 안정형이 8가지 중 4가지만 보던 탓에
    # 후보가 57개(공격형 114개)였고, 그 좁은 풀이 "전무" 의 원인 하나였다. 성향은 이제
    # 상한·점수로만 갈린다 — 남은 성향 제약은 선물 금지뿐이다.
    stable = ask.build_templates(_mkplan(risk_profile="stable", symbols=["BTCUSDT"], interval="4h"))
    types = {c.macro.rule_type.value for c in stable}
    assert types == set(ask.ALL_RULE_TYPES)
    assert all(c.macro.market == "spot" and c.macro.leverage == 1 for c in stable)
    assert all(c.macro.candle_interval == ("1d" if c.macro.rule_type.value == "C" else "4h")
               and c.macro.period.preset == "3m" for c in stable)
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
    # 2026-10-08: 프리셋이 유형당 4~5개가 되고 상한이 90 으로 올라가, 3종목 공격형에서도
    # 첫 프리셋 24개 뒤에 포트폴리오 8개가 들어간다(예전에는 24에서 잘려 못 들어갔다).
    # 순서는 그대로다 — 모든 유형의 단일 후보가 먼저, 포트폴리오가 그다음, 뒤 프리셋이 마지막.
    req3 = _mkplan(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cands3 = ask.build_templates(req3)
    assert len(cands3) == ask.MAX_CANDIDATES
    types3 = {c.macro.rule_type.value for c in cands3}
    assert {"I", "H"} <= types3
    singles3 = [c for c in cands3 if not c.macro.symbols]
    first_portfolio = next(i for i, c in enumerate(cands3) if c.macro.symbols)
    assert first_portfolio == 24, "유형 8개 × 종목 3개의 첫 프리셋이 먼저 와야 한다"
    assert all(not c.macro.symbols for c in cands3[:24])
    assert len(singles3) > 24, "뒤 프리셋의 단일 후보도 들어와야 한다 — 넓힌 보람"

    # 공격형 × 종목 2개: 유형 8개 × 종목 2개(16) + 포트폴리오 8개 — 포트폴리오가 남고 모든 유형이 단일에 있다.
    req2 = _mkplan(risk_profile="aggressive", symbols=["BTCUSDT", "ETHUSDT"])
    cands2 = ask.build_templates(req2)
    portfolios2 = [c for c in cands2 if c.macro.symbols]
    singles2 = [c for c in cands2 if not c.macro.symbols]
    assert portfolios2
    assert set(ask._allowed_types(req2)) <= {c.macro.rule_type.value for c in singles2}


def _result(ret, mdd, trades=10, win=50.0, bh=None):
    return BacktestResult(
        initial_capital=1.0, final_equity=1.0 + ret / 100, final_return_pct=ret, mdd_pct=mdd,
        win_rate_pct=win, total_trades=trades, trades=[], equity_curve=[],
        buy_hold_return_pct=bh,
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
    # 같은 유형은 MAX_PER_TYPE(1) 칸까지. 2 로 올리면 같은 유형의 다른 프리셋이 **같은 제목**으로
    # 나란히 오고 화면 카드 키까지 겹친다 — 레이블에 설정을 드러내는 일이 먼저다.
    # 모든 후보에 홀딩 기준(bh)이 있어야 한다 — 없으면 worth_the_macro 가 떨어뜨린다.
    # 2026-10-12: 상한과 거래 수가 기간·봉에서 나오므로 둘을 넘겨야 관문이 뜻을 갖는다.
    # 3개월 × 4시간봉 = 546봉 → 최소 거래 3회, 균형형 절대 상한 32%.
    ev = [
        ask.Evaluated(_cand("A"), _result(30, 40, bh=0)),   # 균형형 MDD 상한(32) 초과 → 탈락
        ask.Evaluated(_cand("J"), _result(12, 4, bh=0)),
        ask.Evaluated(_cand("J", "ETHUSDT"), _result(11, 3, bh=0)),  # 같은 유형 두 번째 → 다양성으로 탈락
        ask.Evaluated(_cand("G"), _result(8, 2, bh=0)),
        ask.Evaluated(_cand("E"), _result(9, 2, trades=2, bh=0)),    # 거래 2회 → 탈락
        ask.Evaluated(_cand("F"), _result(3, 1, bh=0)),
    ]
    top = ask.select_top(ev, "balanced", period="3m", interval="4h")
    assert [e.candidate.macro.rule_type.value for e in top] == ["J", "G", "F"]
    assert ask.MAX_PER_TYPE == 1, "올리려면 _label 이 설정을 드러내야 한다(제목·카드 키가 겹친다)"

    # 안정형은 수익률 ÷ MDD, 공격형은 수익률만 본다.
    assert ask.score("stable", _result(10, 5)) == pytest.approx(2.0)
    assert ask.score("stable", _result(10, 0.2)) == pytest.approx(10.0)  # MDD 는 1 아래로 나누지 않는다
    assert ask.score("balanced", _result(10, 4)) == pytest.approx(5.0)   # 10 / √4
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
        {"rule_type": "B", "params": {"entry_price": 1, "take_profit_price": 2, "initial_capital": 1000000}},  # 물어볼까가 안 쓰는 방식 → 폐기
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
    assert "추천" not in system and ", ".join(ask.ALL_RULE_TYPES) in system

    # 방식 제한은 시장에만 남는다 — 선물 요청에 C(적립식)를 제안하면 폐기한다.
    futures_req = _mkplan(risk_profile="aggressive", market="futures", leverage=2, symbols=["BTCUSDT"])
    dca = _FakeClient(json.dumps({"macros": [
        {"rule_type": "C", "params": {"amount_per_buy": 50_000, "interval_days": 3}},
    ]}))
    monkeypatch.setattr(ask, "get_ai_client", lambda: dca)
    assert ask.propose_with_ai(futures_req) == []

    client_bad = _FakeClient("not json")
    monkeypatch.setattr(ask, "get_ai_client", lambda: client_bad)
    assert ask.propose_with_ai(req) == []


# (수익률, MDD, 거래 수, 홀딩 수익률). 홀딩 기준이 없으면 worth_the_macro 가 전부 떨어뜨리므로
# 가짜도 기준을 실어야 한다 — 그래야 이 시험이 실제 선별 경로를 지나간다.
# C 는 일부러 홀딩과 0.5%p 차이로 둔다: 분할매수가 "홀딩이나 다름없는 추천" 으로 올라오던
# 바로 그 경우이고, 이제 문턱(3%p)에서 떨어져야 한다.
_RETURNS = {"A": (4, 3, 8, 0), "C": (2, 1, 12, 1.5), "J": (9, 4, 6, 0),
            "G": (6, 2, 5, 0), "F": (7, 3, 2, 0), "E": (5, 2, 4, 0)}


@pytest.fixture
def _fake_backtest(monkeypatch):
    """유형별로 정해진 수치를 돌려주는 가짜 백테스트 — 네트워크 없이 선별 결과를 예측할 수 있다."""
    def fake_run_any(macro):
        ret, mdd, trades, bh = _RETURNS.get(macro.rule_type.value, (1, 1, 5, 0))
        return _result(ret, mdd, trades=trades, bh=bh), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    monkeypatch.setattr(ask, "ai_available", lambda: False)


def test_status_and_consent_flow(_fake_backtest):
    token, _ = _signup()
    st = client.get("/api/ask/status", headers=_auth(token)).json()
    assert st == {"consented": False, "remaining_today": 5, "daily_limit": 5, "unlimited": False, "disclaimer_version": "ask-v2",
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
    # 균형형 점수 = 초과 수익 - 0.5·MDD: J 7 > G 5 > E 4 > A 2.5 ; F 는 거래 2회라 탈락.
    # C 는 홀딩과 0.5%p 차이라 문턱(3%p)에서 떨어진다 — 점수가 아니라 문턱이 막는다.
    assert [r["rule_type"] for r in data["results"]] == ["J", "G", "E"]
    assert "C" not in [r["rule_type"] for r in data["results"]]
    # 문턱은 기간마다 다르다 — 균형형 'weeks' 는 3개월 창이라 2%p 다.
    assert data["no_edge"] is False
    assert data["min_excess_pct"] == ask.MIN_EXCESS_PCT_BY_PERIOD["3m"]
    assert data["refunded"] is False, "추천이 나왔으면 횟수를 쓴 것이다"
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
    # 2026-10-08: 보여 줄 게 없으면 횟수를 돌려준다 — 차감 전으로 돌아가 5회가 남는다.
    assert data["results"] == [] and data["remaining_today"] == 5
    assert data["refunded"] is True


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


def test_select_top_counts_trades_against_the_window_not_the_profile():
    # 2026-10-12: 성향마다 고정 거래 수를 요구하던 것을 봉 수 비례로 바꿨다. 같은 후보가
    # 짧은 창에서는 통과하고 긴 창에서는 모자란다 — 성향이 아니라 창이 가른다.
    ev = [ask.Evaluated(_cand("A"), _result(30, 4, trades=2, bh=0)),
          ask.Evaluated(_cand("J"), _result(30, 4, trades=9, bh=0))]
    # 점수가 같으면 거래 수가 많은 쪽이 앞이라 순서는 뜻이 없다 — 둘 다 통과했는지만 본다.
    assert sorted(e.candidate.macro.rule_type.value
                  for e in ask.select_top(ev, "balanced", period="1w", interval="4h")) == ["A", "J"]
    assert [e.candidate.macro.rule_type.value
            for e in ask.select_top(ev, "balanced", period="6m", interval="4h")] == ["J"]


def test_every_reachable_pair_fits_backtest_bar_cap():
    # v2 — 봉×기간 짝을 사람이 직접 보내지 않고 to_period·to_interval 이 답변(투자 기간·볼 빈도)에서
    # 계산한다. 그 계산이 만들 수 있는 모든 조합이 이 상한(MAX_BACKTEST_BARS)을 넘지 않는지 본다.
    now_ms = int(time.time() * 1000)
    for horizon in ask.HORIZONS:
        period = ask.to_period(horizon)
        for watch in ask.WATCH_LEVELS:
            interval = ask.to_interval(watch, period)
            days = PERIOD_PRESET_DAYS[period]
            start_ms = now_ms - days * 86_400_000
            bars = _expected_bar_count(interval, start_ms, now_ms)
            assert bars <= MAX_BACKTEST_BARS, f"{horizon}·{watch} → {interval}×{period} = {bars}"


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
    assert ask.to_period("days") == "1w"
    assert ask.to_period("weeks") == "3m"
    assert ask.to_period("months") == "6m"
    assert ask.to_period("long") == "1y"


def test_watch_maps_to_interval():
    assert ask.to_interval("rarely", "6m") == "1d"
    assert ask.to_interval("sometimes", "6m") == "4h"
    assert ask.to_interval("often", "6m") == "1h"


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


def test_candidates_request_rejects_leverage_on_spot(monkeypatch):
    # 현물 + 레버리지는 CandidatesRequest 규칙 — 모델과 라우트 양쪽에서 막힌다.
    with pytest.raises(ValidationError):
        ask.CandidatesRequest(risk_profile="balanced", market="spot", leverage=2,
                              invest_horizon="weeks", watch_frequency="sometimes")
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, _ = _signup()
    _consent(tok)
    r = client.post("/api/ask/candidates", json=_candidates_body(market="spot", leverage=2),
                    headers=_auth(tok))
    assert r.status_code == 422
    assert "레버리지" in r.json()["detail"]


def test_validation_errors_are_korean_sentences(monkeypatch):
    # pydantic 영문 메시지가 그대로 한국어 말풍선에 찍히면 안 된다 — 두 경로 모두 한 문장이다.
    tok, _ = _signup()
    _consent(tok)
    bad_cards = client.post("/api/ask/candidates", json=_candidates_body(invest_horizon="forever"),
                            headers=_auth(tok))
    assert bad_cards.status_code == 422
    detail = bad_cards.json()["detail"]
    assert isinstance(detail, str) and re.search(r"[가-힣]", detail)
    assert "Input should be" not in detail and "field required" not in detail

    bad_symbol = client.post("/api/ask/macros", json={"session_id": 1, "symbol": "BTC-KRW"},
                             headers=_auth(tok))
    assert bad_symbol.status_code == 422
    assert bad_symbol.json()["detail"] == "종목 이름이 올바르지 않아요"


def test_session_without_expiry_is_treated_as_expired(monkeypatch):
    # expires_ms 가 0 인 행(옛 기록)이 영원히 살아 있으면 30분 만료가 뚫린다.
    tok, _ = _signup()
    flow = _start_flow(tok, monkeypatch)
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        row.expires_ms = 0
        db.add(row)
        db.commit()
    r = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 410


def test_in_flight_guard_rejects_concurrent_candidates(monkeypatch):
    # 차감이 일어나는 곳이라 동시 요청 두 개가 다 통과하면 하루 횟수가 두 번 깎인다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    tok, user_id = _signup()
    _consent(tok)
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    ask._IN_FLIGHT.add(user_id)
    try:
        r = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok))
        assert r.status_code == 429 and "돌리는 중" in r.json()["detail"]
    finally:
        ask._IN_FLIGHT.discard(user_id)
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before  # 막힌 요청은 차감하지 않는다


def test_candidate_stage_ai_flag_survives_a_later_macro_call(_fake_backtest, monkeypatch):
    # 설계 2장 — "무엇을 보여 줬는지"가 법적 방어 장치라, 그 목록을 AI 가 골랐는지도
    # 매크로 단계가 row.ai_used 를 덮어쓴 뒤까지 남아 있어야 한다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai",
                        lambda: (lambda prompt: json.dumps(
                            [{"symbol": "AAAUSDT", "reason": "거래가 활발해요"},
                             {"symbol": "BBBUSDT", "reason": "움직임이 적당해요"},
                             {"symbol": "CCCUSDT", "reason": "움직임이 큰 편이에요"}],
                            ensure_ascii=False)))
    tok, _ = _signup()
    _consent(tok)
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(tok)).json()
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        items, cand_ai_used = ask._candidates_record(row)
        assert cand_ai_used is True and len(items) >= ac.MIN_PICKS

    # 매크로 단계는 AI 를 쓰지 않는다(_fake_backtest 가 ai_available 을 끈다) → row.ai_used 는 False 가 된다.
    r = client.post("/api/ask/macros",
                    json={"session_id": flow["session_id"], "symbol": "AAAUSDT"},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    with get_session() as db:
        row = db.get(AskMacroSession, flow["session_id"])
        assert row.ai_used is False                       # 매크로 단계 기록은 덮어써도
        assert ask._candidates_record(row)[1] is True     # 후보 단계 기록은 남는다
        assert [c["symbol"] for c in ask._candidates_record(row)[0]]


# --- 홀딩 대비로 고른다 (2026-09-23) -------------------------------------
# 그냥 들고 있는 것보다 나은 조합을 위로 올린다. 절대 수익률로 뽑아 놓고 화면에서
# "홀딩이 나았어" 라고 말하던 엇갈림을 없앤다.

def test_score_uses_excess_over_holding():
    # 같은 +10% 라도 홀딩이 +30% 였으면 -20, +3% 였으면 +7.
    assert ask.score("aggressive", _result(10, 5, bh=30)) == pytest.approx(-20.0)
    assert ask.score("aggressive", _result(10, 5, bh=3)) == pytest.approx(7.0)
    assert ask.score("balanced", _result(10, 4, bh=2)) == pytest.approx(4.0)   # 8 / √4
    assert ask.score("stable", _result(10, 5, bh=5)) == pytest.approx(1.0)     # 5 / 5


def test_score_falls_back_to_absolute_return_without_holding_data():
    # 시세가 모자라 홀딩 기준을 못 구한 경우엔 예전처럼 절대 수익률로 본다.
    assert ask.score("aggressive", _result(10, 40)) == pytest.approx(10.0)
    assert ask.score("stable", _result(10, 5)) == pytest.approx(2.0)
    assert ask.score("balanced", _result(10, 4)) == pytest.approx(5.0)   # 10 / √4


def test_select_top_shows_fewer_rather_than_padding_with_weak_candidates():
    # 문턱을 넘긴 게 하나뿐이면 하나만 보여 준다. 예전에는 세 칸을 채우려고 홀딩만큼도 못 한
    # 후보를 함께 올렸고, 그래서 "가만히 홀딩만 해도 괜찮았다" 는 카드가 섞였다.
    ev = [
        ask.Evaluated(_cand("J"), _result(20, 4, bh=1)),    # +19%p — 통과
        ask.Evaluated(_cand("G"), _result(8, 2, bh=7)),     # +1%p — 문턱 아래
        ask.Evaluated(_cand("A"), _result(6, 3, bh=5)),     # +1%p — 문턱 아래
        ask.Evaluated(_cand("E"), _result(5, 2, bh=4)),     # +1%p — 문턱 아래
    ]
    top = ask.select_top(ev, "balanced")
    assert [e.candidate.macro.rule_type.value for e in top] == ["J"]
    assert len(top) == 1, "빈 칸을 약한 후보로 메우면 안 된다"


def test_select_top_drops_candidates_that_lost_to_holding():
    # 2026-10-08: 홀딩에 진 후보는 순위를 낮추는 게 아니라 **아예 내보내지 않는다.**
    # 예전에는 뒤로 밀어 두고도 세 칸을 채우려고 함께 보여 줬다.
    ev = [
        ask.Evaluated(_cand("A"), _result(30, 5, bh=90)),   # 커 보이지만 홀딩에 60%p 뒤짐
        ask.Evaluated(_cand("J"), _result(8, 5, bh=1)),     # 작아도 홀딩을 7%p 이김
    ]
    top = ask.select_top(ev, "aggressive")
    assert [e.candidate.macro.rule_type.value for e in top] == ["J"]


def test_worth_the_macro_needs_profit_and_a_real_margin_over_holding():
    # 1) 홀딩을 문턱(3%p)만큼 넘어야 한다 — 0 근처면 "그냥 들고 있기" 와 구분되지 않는다.
    assert ask.worth_the_macro(_result(8, 5, bh=1)) is True       # +7%p
    assert ask.worth_the_macro(_result(8, 5, bh=6)) is False      # +2%p — 문턱 아래
    assert ask.worth_the_macro(_result(8, 5, bh=5)) is True       # +3%p — 문턱에 닿으면 통과
    # 2) 그 자체로 벌어야 한다 — 홀딩이 더 크게 잃었다고 손실이 추천이 되지는 않는다.
    assert ask.worth_the_macro(_result(-10, 20, bh=-40)) is False
    assert ask.worth_the_macro(_result(0, 5, bh=-40)) is False
    # 3) 홀딩 기준을 모르면 "홀딩보다 낫다" 고 말할 수 없다.
    assert ask.worth_the_macro(_result(50, 5, bh=None)) is False


def test_min_excess_pct_is_tunable(monkeypatch):
    monkeypatch.setenv("ASK_MIN_EXCESS_PCT", "10")
    assert ask.min_excess_pct() == pytest.approx(10.0)
    assert ask.worth_the_macro(_result(8, 5, bh=1)) is False   # +7%p 는 10%p 문턱 아래
    monkeypatch.setenv("ASK_MIN_EXCESS_PCT", "0")
    assert ask.worth_the_macro(_result(8, 5, bh=8)) is True    # 문턱 0 이면 조금만 넘어도 된다
    monkeypatch.setenv("ASK_MIN_EXCESS_PCT", "뭐라고")
    assert ask.min_excess_pct() == pytest.approx(ask.MIN_EXCESS_PCT)


def test_dca_is_holding_in_disguise_and_never_recommended_near_zero_excess():
    """분할매수(C)는 백테스트가 스스로 "DCA is buy-and-hold" 라고 적어 둔 규칙이다.

    매수 횟수를 거래 수로 보고하므로 최소 거래 수 필터를 180회로 가볍게 통과한다.
    그래서 "가만히 홀딩만 해도 괜찮았다" 는 추천이 나왔다 — 막는 것은 홀딩 문턱뿐이다.
    """
    dca = ask.Evaluated(_cand("C"), _result(12, 8, trades=180, bh=11))  # 홀딩과 1%p 차이
    assert ask.select_top([dca], "balanced") == []
    # 반대로 하락장에서 평균매수가 홀딩을 크게 이기면 그것은 진짜 값어치다.
    good = ask.Evaluated(_cand("C"), _result(12, 8, trades=180, bh=2))
    assert [e.candidate.macro.rule_type.value for e in ask.select_top([good], "balanced")] == ["C"]


# --- 직접 고를래요: 거래 가능한 종목이면 받는다 (2026-09-23) ----------------
# v1 물어볼까엔 종목 검색창이 있었다. v2 가 고정 5개로 좁혀 버렸던 것을 되돌린다.

def _flow_row(db, user_id, market="spot"):
    from app.db import AskMacroSession as _S
    row = _S(user_id=user_id, day_kst="2026-09-23",
             request_json=json.dumps({"risk_profile": "balanced", "market": market, "leverage": 1,
                                      "invest_horizon": "weeks", "watch_frequency": "sometimes"}),
             candidate_count=0, results_json="[]", disclaimer_version="ask-v2", ai_used=False,
             elapsed_ms=0, created_at="2026-09-23T00:00:00Z", created_ms=1,
             candidates_json=json.dumps({"ai_used": False, "items": [{"symbol": "AAAUSDT"}]}),
             chosen_symbol="", expires_ms=9_999_999_999_999, ask_count=0)
    db.add(row); db.commit(); db.refresh(row)
    return row


def _fake_symbol_list(monkeypatch, items):
    monkeypatch.setattr(ask, "_tradable_symbols",
                        lambda market: {i["symbol"] for i in items if i.get(market)})


def test_allowed_symbols_accepts_any_tradable_symbol(monkeypatch):
    _, user_id = _signup()
    with get_session() as db:
        row = _flow_row(db, user_id)
        _fake_symbol_list(monkeypatch, [{"symbol": "MUBARAKUSDT", "spot": True}])
        allowed = ask._allowed_symbols(row, "spot")
        assert "MUBARAKUSDT" in allowed   # 검색으로 고른 것
        assert "AAAUSDT" in allowed       # 세션 후보
        assert "BTCUSDT" in allowed       # 빠른 선택 칩
        assert "NOPEUSDT" not in allowed  # 거래 목록에 없음


def test_allowed_symbols_respects_the_sessions_market(monkeypatch):
    _, user_id = _signup()
    with get_session() as db:
        row = _flow_row(db, user_id, market="futures")
        _fake_symbol_list(monkeypatch, [{"symbol": "SPOTONLYUSDT", "spot": True},
                                        {"symbol": "PERPUSDT", "spot": False, "futures": True}])
        allowed = ask._allowed_symbols(row, "futures")
        assert "PERPUSDT" in allowed
        assert "SPOTONLYUSDT" not in allowed


def test_allowed_symbols_falls_back_when_symbol_source_is_down(monkeypatch):
    _, user_id = _signup()
    with get_session() as db:
        row = _flow_row(db, user_id)

        def boom(market):
            raise RuntimeError("binance down")

        monkeypatch.setattr(ask, "_tradable_symbols", boom)
        allowed = ask._allowed_symbols(row, "spot")
        assert "BTCUSDT" in allowed and "AAAUSDT" in allowed
        assert "MUBARAKUSDT" not in allowed  # 목록을 못 받으면 넓히지 않는다


def test_result_view_carries_hold_return_and_a_small_curve():
    # 결과 카드(2026-09-30)가 '그냥 들고 있기' 대비와 자산곡선을 그리도록 옆 칸에 싣는다 — metrics 모양은 그대로.
    from engine.backtest import EquityPoint
    curve = [EquityPoint(t=f"2026-01-01T{h:02d}:00:00", equity=1000 + h * 3 + (h % 5)) for h in range(24)] * 5
    result = BacktestResult(
        initial_capital=1000.0, final_equity=1100.0, final_return_pct=10.0, mdd_pct=4.0,
        win_rate_pct=55.0, total_trades=8, trades=[], equity_curve=curve, buy_hold_return_pct=14.5,
    )
    view = ask._result_view(ask.Evaluated(_cand("A"), result))
    assert view["metrics"] == {"final_return_pct": 10.0, "mdd_pct": 4.0, "win_rate_pct": 55.0, "total_trades": 8}
    assert view["hold_return_pct"] == 14.5 and view["initial_capital"] == 1000.0
    assert len(view["curve"]) == ask.CURVE_POINTS
    assert view["curve"][0] == curve[0].equity and view["curve"][-1] == curve[-1].equity

    empty = ask._result_view(ask.Evaluated(_cand("A"), _result(3, 1, bh=None)))
    assert empty["curve"] == [] and empty["hold_return_pct"] is None


# --- 프리셋을 유형당 4~5개로 넓혔다 (2026-10-08) ---------------------------
# "홀딩이나 다름없는 결과" 의 다른 절반은 탐색이 좁았던 것이다. 프리셋이 종목·기간에 안 맞으면
# 그 유형에서 건질 게 없는데도 그중 제일 나은 것이 추천으로 올라왔다.

def test_every_preset_actually_builds_a_macro():
    """프리셋 하나라도 스키마에 걸리면 _make_macro 가 None 을 돌려주고 **조용히** 사라진다.

    그러면 넓힌 줄 알고 안 넓어진 상태로 돈다. H 의 자금 상한(기본+세이프티 전부 체결)과
    J 의 fast < slow 처럼 모델 검증이 걸리는 자리가 있어 실제로 쉽게 일어난다.
    """
    dropped = []
    for table, profile, period, interval in (
        (ask._PRESETS, "aggressive", "6m", "4h"),
        (ask._SHORT_BAR_PRESETS, "balanced", "1m", "15m"),
    ):
        for rule_type, presets in table.items():
            req = _mkplan(risk_profile=profile, symbols=["BTCUSDT"],
                          period_preset=period, interval=interval)
            for index, preset in enumerate(presets):
                if ask._make_macro(req, rule_type, preset, ["BTCUSDT"]) is None:
                    dropped.append(f"{rule_type}[{index}]")
    assert dropped == [], f"스키마에 걸려 사라지는 프리셋: {dropped}"


def test_presets_are_wide_enough_to_be_worth_the_budget():
    # 예산을 20 → 40초로 올린 근거가 이것이다. 좁아지면 되돌려야 한다.
    for rule_type, presets in ask._PRESETS.items():
        assert len(presets) >= 4, f"{rule_type} 프리셋이 {len(presets)}개"
    for rule_type, presets in ask._SHORT_BAR_PRESETS.items():
        assert len(presets) >= 4, f"짧은 봉 {rule_type} 프리셋이 {len(presets)}개"
    # 같은 유형 안에서 설정이 실제로 달라야 한다 — 복사된 프리셋은 백테스트만 낭비한다.
    for table in (ask._PRESETS, ask._SHORT_BAR_PRESETS):
        for rule_type, presets in table.items():
            seen = {json.dumps(p, sort_keys=True) for p in presets}
            assert len(seen) == len(presets), f"{rule_type} 에 똑같은 프리셋이 있다"
    assert ask.time_budget_sec() == pytest.approx(40.0)


def test_widened_presets_reach_the_backtest():
    # 넓힌 프리셋이 후보 목록에 실제로 들어오는지. 상한에서 잘리면 넓힌 보람이 없다.
    req = _mkplan(risk_profile="aggressive", symbols=["BTCUSDT"], period_preset="6m", interval="4h")
    cands = ask.build_templates(req)
    per_type: dict[str, int] = {}
    for c in cands:
        rt = c.macro.rule_type.value
        per_type[rt] = per_type.get(rt, 0) + 1
    for rule_type in ask._allowed_types(req):
        assert per_type.get(rule_type, 0) >= 4, f"{rule_type} 후보가 {per_type.get(rule_type, 0)}개뿐"


def test_no_edge_is_reported_when_nothing_beats_holding(_fake_backtest, monkeypatch):
    """돌려는 봤는데 문턱을 넘은 게 없는 경우 — 사용자 결정대로 추천하지 않고 그렇게 말한다.

    데이터가 없어 빈 것과 구분되어야 한다(화면이 하는 말이 다르다).
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)

    def fake_run_any(macro):
        # 전부 벌긴 벌었지만 홀딩을 1%p 밖에 못 넘었다 — "가만히 있어도 괜찮았다" 는 그 경우.
        return _result(11, 3, trades=20, bh=10), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()
    data = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                       headers=_auth(token)).json()
    assert data["results"] == []
    assert data["no_edge"] is True, "돌렸는데 넘은 게 없음을 화면에 알려야 한다"
    with get_session() as db:
        assert db.get(AskMacroSession, flow["session_id"]).candidate_count > 0, "후보는 돌았다"


def test_no_edge_is_not_claimed_when_the_profile_filters_blocked_them(_fake_backtest, monkeypatch):
    """성향 조건에 걸려 빈 것을 "홀딩이 나았다" 고 말하면 거짓이다.

    실측(캐시된 BTC 일봉 1년): 홀딩 -31.5% 인데 문턱을 넘은 후보가 7개였고 전부 안정형
    MDD 상한(10%)에서 떨어졌다. 그때 화면은 "홀딩이 나았다" 가 아니라 "이 조건으론 살아남은
    후보가 없었다" 고 말해야 한다.
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)

    def fake_run_any(macro):
        # 홀딩(-30%)을 40%p 넘겼지만 MDD 55% — 안정형 상한(10%)에 전부 걸린다.
        return _result(10, 55, trades=20, bh=-30), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(risk_profile="stable"),
                       headers=_auth(token)).json()
    data = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                       headers=_auth(token)).json()
    assert data["results"] == [], "MDD 상한에 전부 걸렸다"
    assert data["no_edge"] is False, "홀딩은 못 이긴 게 아니다 — 성향 조건이 막은 것이다"


# --- 수수료 · 기간별 문턱 · 횟수 환불 (2026-10-08) --------------------------
# 단타형이 늘 빈 화면을 주는 것을 조사하다 셋을 찾았다. 수수료가 거래소를 안 보고,
# 문턱이 기간을 안 보고, 결과가 0개인데 횟수가 깎였다.

def test_macro_fees_follow_the_exchange():
    """예전에는 전부 0.1%(바이낸스 요율)였다 — 국내가 실제보다 나쁘게 나왔다.

    실측: 1주 · 5m 단타 후보가 0.1% 에서 -6.08%, 0.05% 에서 -4.75%.
    """
    from engine.exchanges import spot_commission_pct
    seen = {}
    for exchange, symbol in (("binance", "BTCUSDT"), ("upbit", "KRW-BTC"), ("bithumb", "KRW-ETH")):
        req = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=[symbol],
                        period_preset="3m", interval="4h", exchange=exchange)
        macro = ask._make_macro(req, "A", ask._PRESETS["A"][0], [symbol])
        assert macro is not None
        seen[exchange] = macro.fees.commission_pct
        assert macro.fees.commission_pct == spot_commission_pct(exchange)
    assert seen["upbit"] < seen["binance"], "업비트 원화마켓은 바이낸스보다 싸다"
    assert seen["bithumb"] < seen["binance"]
    # 0 으로 두지 않는다 — 이벤트가 끝나면 조용히 거짓이 되고 "마찰 없음" 으로 읽힌다.
    assert all(value > 0 for value in seen.values())


def test_commission_can_be_overridden_by_env(monkeypatch):
    from engine.exchanges import spot_commission_pct
    monkeypatch.setenv("EXCHANGE_COMMISSION_JSON", '{"upbit": 0.139}')
    assert spot_commission_pct("upbit") == pytest.approx(0.139)
    assert spot_commission_pct("binance") == pytest.approx(0.1), "안 적은 거래소는 그대로"
    monkeypatch.setenv("EXCHANGE_COMMISSION_JSON", "깨진 값")
    assert spot_commission_pct("upbit") == pytest.approx(0.05), "못 읽으면 기본값"


def test_min_excess_scales_with_the_window():
    """1주에 3%p 를 요구하면 1년과 같은 난이도다 — 짧은 창의 진짜 승자까지 숨긴다."""
    assert ask.min_excess_pct("1y") == pytest.approx(3.0)
    assert ask.min_excess_pct("1w") == pytest.approx(0.5)
    periods = ["1w", "1m", "3m", "6m", "1y"]
    values = [ask.min_excess_pct(p) for p in periods]
    assert values == sorted(values), "기간이 길수록 문턱이 낮아지면 안 된다"
    assert len(set(values)) == len(values), "전부 다른 값이어야 눈금이 의미가 있다"
    # 모르는 기간은 가장 엄한 값으로 — 새 기간을 더하고 표를 안 고쳤을 때 느슨해지면 안 된다.
    assert ask.min_excess_pct("2y") == pytest.approx(ask.MIN_EXCESS_PCT)
    assert ask.min_excess_pct("") == pytest.approx(ask.MIN_EXCESS_PCT)
    # 같은 결과가 1주에선 통과, 1년에선 탈락할 수 있다.
    close = _result(8, 3, bh=7)  # +1%p
    assert ask.worth_the_macro(close, "1w") is True
    assert ask.worth_the_macro(close, "1y") is False


def test_env_threshold_still_wins_over_the_period_table(monkeypatch):
    monkeypatch.setenv("ASK_MIN_EXCESS_PCT", "9")
    assert ask.min_excess_pct("1w") == pytest.approx(9.0)
    assert ask.min_excess_pct("1y") == pytest.approx(9.0)


def test_select_top_uses_the_period_threshold():
    ev = [ask.Evaluated(_cand("J"), _result(8, 3, bh=7))]  # +1%p
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "balanced", period="1w")] == ["J"]
    assert ask.select_top(ev, "balanced", period="1y") == []


def test_refund_is_returned_when_a_later_symbol_does_produce_results(_fake_backtest, monkeypatch):
    """환불이 '한 번 주면 끝' 이면 세션이 공짜가 된다.

    안 나올 종목으로 환불받고 '다른 종목으로' 눌러 결과를 받는 길이 있다(세션당 6회).
    마지막 시도 기준으로 매긴다 — 결과가 나오면 다시 차감한다.
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()
    session_id = flow["session_id"]

    def nothing(macro):
        return _result(11, 3, trades=20, bh=10), [], "test", macro.period.preset  # +1%p, 3m 문턱 2%p

    monkeypatch.setattr("app.main._run_any", nothing)
    first = client.post("/api/ask/macros", json={"session_id": session_id, "symbol": "BTCUSDT"},
                        headers=_auth(token)).json()
    assert first["results"] == [] and first["refunded"] is True
    assert first["remaining_today"] == 5, "돌려받았다"

    # '다른 종목으로' — 같은 세션, 이번엔 결과가 나온다.
    monkeypatch.setattr("app.main._run_any", _fake_run_any_for_test())
    # 종목은 세션이 낸 후보에서 고른다 — 이름을 박아 두면 풀 정렬이 바뀔 때마다 깨진다
    # (2026-10-12 에 왕복 여지 순으로 바뀌면서 실제로 깨졌다).
    other = next(c["symbol"] for c in flow["candidates"] if c["symbol"] != "BTCUSDT")
    second = client.post("/api/ask/macros", json={"session_id": session_id, "symbol": other},
                         headers=_auth(token)).json()
    assert second["results"], "이번엔 추천이 나와야 한다"
    assert second["refunded"] is False
    assert second["remaining_today"] == 4, "값을 받았으면 다시 차감한다 — 세션이 공짜가 되면 안 된다"


def _fake_run_any_for_test():
    def run(macro):
        ret, mdd, trades, bh = _RETURNS.get(macro.rule_type.value, (1, 1, 5, 0))
        return _result(ret, mdd, trades=trades, bh=bh), [], "test", macro.period.preset
    return run


def test_endpoint_uses_the_window_threshold_not_the_strictest_one(_fake_backtest, monkeypatch):
    """끝점이 기간을 select_top 에 넘기는지 — 안 넘기면 기본값(가장 엄한 3%p)이 쓰인다.

    균형형 'weeks' 는 3개월 창이라 문턱이 2%p 다. 초과 수익 +2.5%p 는 그 창에서는 추천이고
    1년 창에서는 아니다. 기간을 안 넘기면 이 추천이 조용히 사라진다.
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)

    def fake_run_any(macro):
        return _result(12.5, 2, trades=20, bh=10), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    flow = client.post("/api/ask/candidates", json=_candidates_body(invest_horizon="weeks"),
                       headers=_auth(token)).json()
    data = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                       headers=_auth(token)).json()
    assert data["min_excess_pct"] == pytest.approx(2.0), "3개월 창의 문턱"
    assert data["results"], "+2.5%p 는 3개월 창에서 추천이다 — 기간을 안 넘기면 빈다"
    assert data["no_edge"] is False
    # 같은 숫자가 1년 창에서는 탈락한다는 것도 함께 못 박는다.
    assert ask.worth_the_macro(_result(12.5, 2, trades=20, bh=10), "1y") is False


def test_a_stored_scalper_session_refunds_instead_of_crashing(monkeypatch):
    """단타형으로 만들어 둔 세션이 성향 삭제 뒤에 열리면 — 500 이 아니라 안내 + 환불.

    세션 수명이 30분(SESSION_TTL_MS)이라 창은 짧지만 0 은 아니다. 배포 순간에 물어보던
    사람이 '종목 고르기' 를 누르면 저장된 scalper 가 CandidatesRequest 검증에서 터진다.
    예전에는 포괄 except 가 "오래된 형식" 으로 뭉갰고, 횟수는 돌아오지 않았다.
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    token, _ = _signup()
    _consent(token)
    flow = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token)).json()
    session_id = flow["session_id"]

    with get_session() as db:
        row = db.get(AskMacroSession, session_id)
        saved = json.loads(row.request_json)
        saved["risk_profile"] = "scalper"
        row.request_json = json.dumps(saved, ensure_ascii=False)
        db.add(row)
        db.commit()

    res = client.post("/api/ask/macros", json={"session_id": session_id, "symbol": "BTCUSDT"},
                      headers=_auth(token))
    assert res.status_code == 410
    assert "성향" in res.json()["detail"], "왜 못 쓰는지 말해야 한다"

    with get_session() as db:
        assert db.get(AskMacroSession, session_id).refunded is True, "횟수를 돌려줘야 한다"
    status = client.get("/api/ask/status", headers=_auth(token)).json()
    assert status["remaining_today"] == ask.daily_limit(), "안 쓴 것으로 세야 한다"


# --- 꾸준함 보정 (2026-10-29) ---------------------------------------------------
# 같은 과거 구간에서 고르고 채점하므로 그 구간에 우연히 맞은 설정이 1등이 된다.
# 꾸준함은 **순위에만** 쓴다 — 거르지 않으므로 카드 수는 줄어들 수 없다.

_FLUKE = {"top_trade_share_pct": 100.0}


def test_ranking_key_pushes_a_fluke_below_a_steady_card_of_the_same_score():
    steady = _result(20.0, 5.0, bh=0.0)
    fluke = steady.model_copy(update=_FLUKE)
    assert ask.score("aggressive", fluke) == ask.score("aggressive", steady)
    assert ask.ranking_key("aggressive", fluke) < ask.ranking_key("aggressive", steady)


def test_ranking_key_never_lifts_a_card_that_lost_to_holding():
    # 음수 점수에 1 미만을 곱하면 점수가 **커져** 순위가 거꾸로 올라간다.
    # 지금은 worth_the_macro 가 앞에서 걸러 주지만 그 필터에 의존하지 않는다.
    losing = _result(5.0, 5.0, bh=30.0).model_copy(update=_FLUKE)
    assert ask.score("aggressive", losing) < 0
    assert ask.ranking_key("aggressive", losing) == ask.score("aggressive", losing)


def test_select_top_prefers_the_steadier_of_two_equal_cards():
    steady = ask.Evaluated(_cand("C"), _result(20.0, 5.0, bh=0.0))
    fluke = ask.Evaluated(_cand("J"), _result(20.0, 5.0, bh=0.0).model_copy(update=_FLUKE))
    picked = ask.select_top([fluke, steady], "aggressive", n=2, period="3m")
    assert [e.candidate.macro.rule_type.value for e in picked] == ["C", "J"]


def test_candidates_feed_the_30d_window_into_the_pool(monkeypatch):
    # 일봉 공급을 풀 선정에 꽂아 두지 않으면 30일 창은 주입구만 있고 꺼진 상태로 남는다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    monkeypatch.setattr(ask.ask_daily, "daily_closes",
                        lambda tickers, **kw: {"BTCUSDT": [100.0, 110.0, 105.0]})
    seen: dict = {}
    real = ask.ask_candidates.build_pool

    def spy(tickers, **kw):
        seen.update(kw)
        return real(tickers, **kw)

    monkeypatch.setattr(ask.ask_candidates, "build_pool", spy)
    token, _user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    res = client.post("/api/ask/candidates", json=_candidates_body(), headers=_auth(token))
    assert res.status_code == 200, res.text
    assert seen.get("daily") == {"BTCUSDT": [100.0, 110.0, 105.0]}


def test_a_falling_market_shows_short_cards_end_to_end(monkeypatch):
    """하락 구간에서 화면이 "살아남은 후보가 없었다" 로 끝나지 않는다 (2026-10-30).

    예전에는 선물·레버리지를 골라도 롱 후보만 만들어, 내린 구간에서는 전부
    ``worth_the_macro`` 의 첫 관문(그 자체로 벌었나)에서 떨어졌다. 같은 구간에서 숏은
    벌었는데 아예 재 보지 않았다 — 이 시험은 그 경로 전체(세션 → 후보 → 카드)를 지난다.
    """
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    monkeypatch.setattr(ask, "ai_available", lambda: False)

    def fake_run_any(macro):
        # 내린 구간: 홀딩 -20%, 롱은 지고 숏은 번다.
        if macro.position_side.value == "short":
            return _result(15, 6, trades=12, bh=-20), [], "test", macro.period.preset
        return _result(-8, 12, trades=12, bh=-20), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    body = _candidates_body(risk_profile="aggressive", market="futures", leverage=2)
    flow = client.post("/api/ask/candidates", json=body, headers=_auth(token)).json()
    res = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                      headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["results"], "숏이 벌었으면 빈 화면이 아니어야 한다"
    assert data["no_edge"] is False
    assert all(r["macro"]["position_side"] == "short" for r in data["results"])
    assert all(r["macro"]["market"] == "futures" and r["macro"]["leverage"] == 2 for r in data["results"])
    assert all("숏" in r["label"] for r in data["results"])
    # 숏 카드만 나오는 방식은 엔진이 방향을 뒤집는 넷뿐이다.
    assert set(r["rule_type"] for r in data["results"]) <= set(ask.SHORT_RULE_TYPES)


def test_a_rising_market_keeps_short_cards_out(monkeypatch):
    # 예측을 하지 않는다는 뜻 — 오른 구간에서는 숏이 관문에서 저절로 떨어진다.
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: _fake_tickers())
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    monkeypatch.setattr(ask, "ai_available", lambda: False)

    def fake_run_any(macro):
        if macro.position_side.value == "short":
            return _result(-12, 10, trades=12, bh=20), [], "test", macro.period.preset
        return _result(30, 6, trades=12, bh=20), [], "test", macro.period.preset

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    token, _user_id = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    body = _candidates_body(risk_profile="aggressive", market="futures", leverage=2)
    flow = client.post("/api/ask/candidates", json=body, headers=_auth(token)).json()
    data = client.post("/api/ask/macros", json={"session_id": flow["session_id"], "symbol": "BTCUSDT"},
                       headers=_auth(token)).json()
    assert data["results"]
    assert all(r["macro"]["position_side"] == "long" for r in data["results"])
