"""분야 묶음으로 물어보기 — 고르게 하지 않고 여섯을 다 돌려 나온 것만 준다 (2026-11-02).

설계: docs/superpowers/specs/2026-11-02-sector-bundles-design.md

왜 고르게 하지 않는가: 분야를 고르는 자리를 두면 "눌렀는데 아무것도 안 나오는" 분야를
누르게 된다. 미리 돌려 두고 캐시에서 꺼내면 그 '나온다' 가 몇 시간 전 기준이라 같은 일이
생긴다. 그래서 그 자리에서 다 돌린다 — 실측(2026-11-02, 업비트 여섯 분야 · 일봉)으로
2.3~6.6초라 예산 40초 안에 들어간다.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from sqlmodel import select

from app import ask, portfolios
from app.db import AskMacroSession, User, get_session
from app.main import app
from engine import BacktestResult

client = TestClient(app)

# 분야 → 종목. 밈만 홀딩을 못 넘게 둔다 — '안 나온 분야는 안 보인다' 를 보는 자리다.
SECTORS = {
    "레이어1": ["KRW-BTC", "KRW-ETH"],
    "디파이": ["KRW-UNI", "KRW-AAVE"],
    "밈": ["KRW-DOGE", "KRW-SHIB"],
}
LOSERS = {"KRW-DOGE"}


def _portfolio(name, symbols, kind="sector"):
    weights = [round(100 / len(symbols), 4)] * len(symbols)
    weights[-1] = round(100 - sum(weights[:-1]), 4)
    return portfolios.Portfolio(
        key=f"sector_{name}", name=name, lead=f"{name} 설명", tags=("분야",), kind=kind,
        stats=None, legs=tuple(portfolios.Leg(symbol=s, weight=w) for s, w in zip(symbols, weights)))


def _result(ret, bh, mdd=4.0, trades=8):
    return BacktestResult(initial_capital=1.0, final_equity=1.0 + ret / 100, final_return_pct=ret,
                          mdd_pct=mdd, win_rate_pct=60.0, total_trades=trades, trades=[],
                          equity_curve=[], buy_hold_return_pct=bh)


@pytest.fixture
def world(monkeypatch):
    """코인게코도 거래소도 안 탄다 — 분야 목록과 백테스트 결과를 손으로 쥐여 준다."""
    seen = []

    def build(exchange="binance", market="spot"):
        return [_portfolio(name, symbols) for name, symbols in SECTORS.items()] + [
            _portfolio("시가총액 TOP5", ["KRW-BTC", "KRW-ETH"], kind="size")]

    def run(macro):
        seen.append(macro)
        first = macro.leg_specs()[0].symbol
        # 밈은 홀딩을 못 넘는다(그 자체로는 벌었지만 홀딩이 훨씬 낫다).
        return _result(2.0, 40.0) if first in LOSERS else _result(18.0, 1.0)

    monkeypatch.setattr(ask.portfolios, "build", build)
    monkeypatch.setattr(ask, "ai_available", lambda: False)
    monkeypatch.setattr("app.main._run_any", lambda macro: (run(macro), [], "test", macro.period.preset))
    return seen


def _signup_with_id():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"sb{tok}@ex.com", "username": f"sb_{tok}", "password": "password123"}).json()
    return body["token"], body["user"]["id"]


def _signup():
    return _signup_with_id()[0]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _body(**over):
    body = {"exchange": "upbit", "account_balance": 1_000_000, "risk_profile": "balanced",
            "market": "spot", "leverage": 1, "invest_horizon": "weeks"}
    body.update(over)
    return body


def _ask(tok, **over):
    client.post("/api/ask/consent", headers=_auth(tok))
    return client.post("/api/ask/bundles", json=_body(**over), headers=_auth(tok))


# --- 나온 분야만 보여 준다 ----------------------------------------------------

def test_each_sector_that_worked_gives_exactly_one_card(world):
    body = _ask(_signup()).json()
    assert [r["sector"] for r in body["results"]] == ["레이어1", "디파이"]


def test_a_sector_that_did_not_beat_holding_is_left_out(world):
    body = _ask(_signup()).json()
    assert "밈" not in [r["sector"] for r in body["results"]]


def test_the_screen_is_told_which_sectors_came_up_empty(world):
    """없는 것을 말없이 숨기지 않는다 — 화면이 한 줄로 말할 수 있어야 한다."""
    assert _ask(_signup()).json()["empty_sectors"] == ["밈"]


def test_size_bundles_never_enter_here(world):
    """"시가총액 TOP5" 는 분야가 아니라 큰 것들의 목록이다."""
    body = _ask(_signup()).json()
    assert "시가총액 TOP5" not in [r["sector"] for r in body["results"]]


# --- 봉과 기간 ----------------------------------------------------------------

def test_bundles_always_run_on_daily_candles(world):
    """짧은 봉을 고르면 캔들 받는 데만 예산이 날아간다(4시간봉 1년 = 29.4초, 실측)."""
    _ask(_signup())
    assert {m.candle_interval for m in world} == {"1d"}


def test_the_horizon_picks_the_backtest_window(world):
    _ask(_signup(), invest_horizon="long")
    assert {m.period.preset for m in world} == {"1y"}


def test_one_week_is_refused(world):
    res = _ask(_signup(), invest_horizon="days")
    assert res.status_code == 422
    assert "3개월" in res.text


# --- 횟수 ---------------------------------------------------------------------

def test_it_costs_one_use(world):
    tok = _signup()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    _ask(tok)
    after = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    assert after == before - 1


def test_nothing_came_out_gives_the_use_back(world, monkeypatch):
    """한 분야도 홀딩을 못 넘었으면 횟수를 돌려준다 — 기존 규칙 그대로."""
    monkeypatch.setattr("app.main._run_any",
                        lambda macro: (_result(2.0, 40.0), [], "test", macro.period.preset))
    tok = _signup()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    body = _ask(tok).json()
    assert body["results"] == [] and body["no_edge"] is True and body["refunded"] is True
    assert client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"] == before


def test_no_sectors_at_all_says_why_and_gives_the_use_back(world, monkeypatch):
    """코인게코가 막혀 분야를 못 받는 경우 — 빈 화면에 까닭이 있어야 한다."""
    monkeypatch.setattr(ask.portfolios, "build", lambda exchange="binance", market="spot": [])
    tok = _signup()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]
    res = _ask(tok)
    assert res.status_code == 503 and "분야" in res.text
    assert client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"] == before


def test_consent_comes_first(world):
    tok = _signup()
    assert client.post("/api/ask/bundles", json=_body(), headers=_auth(tok)).status_code == 403


# --- 돌리다 실패하면 횟수를 돌려준다 (2026-11-04) -------------------------------
#
# 횟수는 분야를 돌리기 **전에** 잡힌다(행을 먼저 커밋해 동시 요청 창을 닫는다). 그 뒤에
# 무엇이 터지든 사용자는 결과 없이 오류만 본다 — 마지막 저장이 연결을 못 받는 경우(웹 풀
# 2초 대기)가 가장 흔하다. 후보 단계(_run_candidates)와 같이 행을 지우고 추가권을 푼다.

def _remaining(tok):
    return client.get("/api/ask/status", headers=_auth(tok)).json()["remaining_today"]


def _sessions(uid):
    with get_session() as db:
        return list(db.exec(select(AskMacroSession).where(AskMacroSession.user_id == uid)).all())


def _boom(*args, **kwargs):
    raise RuntimeError("db down")


def test_a_failure_after_the_run_gives_the_use_back(world, monkeypatch):
    tok, uid = _signup_with_id()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = _remaining(tok)
    monkeypatch.setattr(ask, "_settle_use", _boom)  # 마지막 저장 직전에 터진다
    with pytest.raises(RuntimeError):  # TestClient 는 서버 예외를 그대로 올린다
        _ask(tok)
    assert _remaining(tok) == before
    assert _sessions(uid) == [], "결과 없는 빈 기록을 남기지 않는다"


def test_a_failure_while_making_cards_gives_the_use_back(world, monkeypatch):
    tok = _signup()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = _remaining(tok)
    monkeypatch.setattr(ask, "_result_view", _boom)
    with pytest.raises(RuntimeError):
        _ask(tok)
    assert _remaining(tok) == before


def test_a_failure_gives_the_extra_credit_back(world, monkeypatch):
    tok, uid = _signup_with_id()
    client.post("/api/ask/consent", headers=_auth(tok))
    monkeypatch.setenv("ASK_DAILY_LIMIT", "0")
    with get_session() as db:
        user = db.get(User, uid)
        user.points_balance = 100
        db.add(user)
        db.commit()
    assert client.post("/api/ask/extra", headers=_auth(tok)).status_code == 200
    assert _remaining(tok) == 1
    monkeypatch.setattr(ask, "_settle_use", _boom)
    with pytest.raises(RuntimeError):
        _ask(tok)
    assert _remaining(tok) == 1, "산 추가권이 묶인 채 남으면 안 된다"


def test_a_good_run_still_costs_one_use_and_keeps_the_record(world):
    tok, uid = _signup_with_id()
    client.post("/api/ask/consent", headers=_auth(tok))
    before = _remaining(tok)
    body = _ask(tok).json()
    assert _remaining(tok) == before - 1
    [row] = _sessions(uid)
    assert row.id == body["session_id"] and row.ask_count == 1 and row.chosen_symbol == "레이어1, 디파이"


# --- 안 나온 까닭을 나눠 말한다 (2026-11-04) ------------------------------------
#
# 카드는 관문 셋(최소 거래 수 · MDD 상한 · 홀딩 문턱)을 다 넘어야 나온다. 홀딩은 넘었는데
# MDD 상한에 걸린 분야에 "그냥 들고 있는 게 나았다" 고 말하면 거짓이다 — 한 종목 경로는
# 이미 그렇게 고쳤다(run_ask 의 no_edge 주석: 안정형 · BTC 1년 실측).

def test_empty_sectors_carry_why_they_came_up_empty(world):
    body = _ask(_signup()).json()
    assert body["empty_reasons"] == {"no_edge": ["밈"]}
    assert body["no_edge"] is False, "카드가 나왔으면 이득이 없었다고 말하지 않는다"


def test_beating_holding_but_failing_the_drawdown_cap_is_not_no_edge(world, monkeypatch):
    # 균형형 · 3개월의 MDD 상한은 32% — 60% 는 홀딩을 크게 넘어도 걸린다.
    monkeypatch.setattr("app.main._run_any",
                        lambda macro: (_result(18.0, 1.0, mdd=60.0), [], "test", macro.period.preset))
    body = _ask(_signup()).json()
    assert body["results"] == []
    assert body["no_edge"] is False, "홀딩은 넘었다 — 걸린 곳은 성향 조건이다"
    assert body["empty_reasons"] == {"filtered": ["레이어1", "디파이", "밈"]}
    assert body["refunded"] is True, "보여 줄 게 없으면 횟수는 그대로 돌려준다"


def test_a_sector_that_could_not_run_is_not_called_no_edge(world, monkeypatch):
    def run(macro):
        if macro.leg_specs()[0].symbol in LOSERS:
            raise RuntimeError("시세 없음")
        return _result(18.0, 1.0)

    monkeypatch.setattr("app.main._run_any", lambda macro: (run(macro), [], "test", macro.period.preset))
    body = _ask(_signup()).json()
    assert body["empty_reasons"] == {"failed": ["밈"]}
    assert body["empty_sectors"] == ["밈"], "옛 화면이 읽는 칸은 그대로 둔다"


def test_mixed_reasons_keep_the_sector_order(world, monkeypatch):
    def run(macro):
        first = macro.leg_specs()[0].symbol
        if first == "KRW-BTC":
            return _result(18.0, 1.0, mdd=60.0)  # 레이어1: 조건에 걸림
        if first in LOSERS:
            return _result(2.0, 40.0)            # 밈: 홀딩을 못 넘음
        return _result(18.0, 1.0)                # 디파이: 나옴

    monkeypatch.setattr("app.main._run_any", lambda macro: (run(macro), [], "test", macro.period.preset))
    body = _ask(_signup()).json()
    assert [r["sector"] for r in body["results"]] == ["디파이"]
    assert body["empty_sectors"] == ["레이어1", "밈"]
    assert body["empty_reasons"] == {"filtered": ["레이어1"], "no_edge": ["밈"]}
