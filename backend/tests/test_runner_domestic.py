"""국내 거래소 매크로 — 실행기 버전으로 여는 문(v10+). 바이낸스는 버전과 무관하다."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunnerLaunchTicket, RunSession, get_session
from app.engine import Macro
from app.main import app
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import A, DCA, _key, _start, _stop, _ticket

client = TestClient(app)

DOMESTIC = {"exchange": "upbit", "symbol": "KRW-BTC", "rule_type": "A",
            "params": {"initial_capital": 100000, "take_profit_pct": 3}}
DOMESTIC_RSI = {"exchange": "upbit", "symbol": "KRW-BTC", "rule_type": "F", "candle_interval": "5m",
                "period": {"preset": "1w"},
                "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 100000}}


@pytest.fixture
def domestic_macro():
    return Macro.model_validate(DOMESTIC)


@pytest.fixture
def binance_macro():
    return Macro.model_validate(A)


def test_version_gate_mirrors_the_signal_gate():
    assert runner_mod.DOMESTIC_MIN_VERSION == "10"
    assert runner_mod.supports_domestic("10") is True
    assert runner_mod.supports_domestic("11") is True
    assert runner_mod.supports_domestic("9") is False
    assert runner_mod.supports_domestic("") is False
    assert runner_mod.supports_domestic("abc") is False


def test_old_runner_cannot_take_a_domestic_macro(domestic_macro):
    with pytest.raises(Exception) as caught:
        runner_mod._require_supported_exchange(domestic_macro, "9")
    assert "실행기" in str(caught.value)


def test_new_runner_takes_a_domestic_macro(domestic_macro):
    runner_mod._require_supported_exchange(domestic_macro, "10")


def test_binance_macro_is_unaffected_by_version(binance_macro):
    runner_mod._require_supported_exchange(binance_macro, "9")
    runner_mod._require_supported_exchange(binance_macro, "10")


# --- 티켓: 발급은 열고 청구에서 버전을 본다 ----------------------------------
def test_ticket_issue_is_open_for_domestic_macro():
    # 발급 시점엔 실행기 버전을 모른다 — 여기서 막으면 v10 실행기도 영영 티켓을 못 받는다.
    _, ticket = _ticket(_signup(), DOMESTIC)
    assert ticket


def test_claim_rejects_old_runner_for_domestic_macro_and_keeps_the_ticket():
    token = _signup()
    launch_id, ticket = _ticket(token, DOMESTIC)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "9"})
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.DOMESTIC_REQUIRED_DETAIL
    status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
    assert status["status"] == "rejected" and status["runner_version"] == "9"
    with get_session() as db:
        assert db.get(RunnerLaunchTicket, launch_id).claimed_at == ""  # 소비하지 않는다
    ok = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "10"})
    assert ok.status_code == 200
    assert ok.json()["macro"]["exchange"] == "upbit" and ok.json()["symbol"] == "KRW-BTC"


def test_rejected_status_asks_domestic_macro_for_v10_and_binance_for_the_general_minimum():
    # v9 실행기 사용자가 "v9이에요. 웹 연결은 v6부터 돼요" 를 보면 안 된다 — 국내는 v10 이 필요하다.
    token = _signup()
    domestic_id, domestic_ticket = _ticket(token, DOMESTIC)
    binance_id, binance_ticket = _ticket(token, A)
    for ticket in (domestic_ticket, binance_ticket):
        r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "5"})
        assert r.status_code == 426
    domestic = client.get(f"/api/me/runner/launch-tickets/{domestic_id}", headers=_auth(token)).json()
    binance = client.get(f"/api/me/runner/launch-tickets/{binance_id}", headers=_auth(token)).json()
    assert domestic["status"] == binance["status"] == "rejected"
    assert domestic["min_runner_version"] == runner_mod.DOMESTIC_MIN_VERSION == "10"
    assert binance["min_runner_version"] == "6"


def test_download_info_tells_the_web_the_domestic_minimum():
    info = client.get("/api/runner/download/info").json()
    assert info["domestic_min_runner_version"] == runner_mod.DOMESTIC_MIN_VERSION == "10"


def test_claim_still_refuses_unsupported_rule_for_domestic_even_on_new_runner():
    macro = {**DCA, "exchange": "upbit", "symbol": "KRW-BTC", "market": "spot", "candle_interval": "1d",
             "params": {"amount_per_buy": 10000, "interval_days": 1, "initial_capital": 100000}}
    _, ticket = _ticket(_signup(), macro)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "10"})
    assert r.status_code == 422 and r.json()["detail"] == runner_mod.UNSUPPORTED_RULE_DETAIL


def test_claim_binance_macro_unaffected_by_domestic_gate():
    _, ticket = _ticket(_signup(), A)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "7"})
    assert r.status_code == 200


# --- 세션 시작: exchange 가 처음으로 실려 온다 ------------------------------
def _start_domestic(key, version, **over):
    body = {"symbol": "KRW-BTC", "exchange": "upbit", "position_side": "long", "leverage": 1, "market": "spot",
            "testnet": True, "human_summary": "t", "macro": DOMESTIC, "runner_version": version}
    body.update(over)
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


def test_start_rejects_old_runner_for_domestic_macro():
    key = _key(_signup())
    for version in ("9", "8", ""):
        r = _start_domestic(key, version)
        assert r.status_code == 426 and r.json()["detail"] == runner_mod.DOMESTIC_REQUIRED_DETAIL, version
    # 매크로 없이 거래소만 말해도 구버전에는 열지 않는다.
    r = _start_domestic(key, "9", macro=None)
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.DOMESTIC_REQUIRED_DETAIL


def test_start_accepts_domestic_macro_from_new_runner(monkeypatch):
    started = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: started.append(sid))
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    key = _key(_signup())
    r = _start_domestic(key, "10")
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert started == [sid]  # v8+ 이므로 서버 전략 드라이버가 올라간다
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert row.symbol == "KRW-BTC" and row.market == "spot" and row.runner_version == "10"
        assert '"exchange":"upbit"' in row.macro_json  # 세션 행에 거래소 칸은 없고 매크로 원문이 그 역할이다
    _stop(key, sid)


def test_start_without_macro_is_still_required_for_domestic_new_runner():
    key = _key(_signup())
    r = _start_domestic(key, "10", macro=None)
    assert r.status_code == 422 and r.json()["detail"] == runner_mod.MACRO_REQUIRED_DETAIL


def test_start_refuses_inconsistent_exchange_claims_whatever_the_version():
    key = _key(_signup())
    for version in ("10", "9"):
        assert _start_domestic(key, version, exchange="binance").status_code == 422  # 매크로는 업비트
        assert _start_domestic(key, version, symbol="BTCUSDT").status_code == 422  # 업비트에 달러 종목
        assert _start_domestic(key, version, exchange="kraken").status_code == 422
        # 바이낸스 매크로에 KRW 종목
        r = client.post("/api/runner/start", headers={"X-Runner-Key": key},
                        json={"symbol": "KRW-BTC", "macro": A, "runner_version": version})
        assert r.status_code == 422


def test_binance_start_without_exchange_is_unchanged(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    key = _key(_signup())
    r = _start(key, A, "7")  # exchange 를 안 보내는 옛 실행기
    assert r.status_code == 200
    _stop(key, r.json()["session_id"])


# --- 드라이버: 봉도 같은 거래소에서 -----------------------------------------
def test_start_driver_reads_candles_from_the_macro_exchange(monkeypatch):
    seen = {"history": [], "subscribe": []}

    class Feed:
        async def history(self, symbol, interval, market, n, **kwargs):
            seen["history"].append((symbol, kwargs))
            return [(i, 100, 100, 100, 100) for i in range(20)]

        def subscribe(self, symbol, interval, market, cb, **kwargs):
            seen["subscribe"].append((symbol, kwargs))
            return ("sub", symbol)

        def unsubscribe(self, sub):
            pass

    async def _noop(live):
        return None

    monkeypatch.setattr(eng, "feed", Feed())
    monkeypatch.setattr(eng, "_run", _noop)
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-BTC", position_side="long", leverage=1, market="spot", status="running",
                         started_at=now, last_heartbeat_at=now, runner_version="10",
                         macro_json=Macro.model_validate(DOMESTIC_RSI).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        sid = row.id

    async def go():
        assert await eng.start_driver(sid) is True
        assert eng._drivers[sid].exchange == "upbit"
        await eng.stop_driver(sid)

    try:
        asyncio.run(go())
    finally:
        with get_session() as db:
            db.delete(db.get(RunSession, sid))
            db.commit()
    assert seen["history"] == [("KRW-BTC", {"exchange": "upbit"})]
    assert seen["subscribe"] == [("KRW-BTC", {"since_t": 19, "exchange": "upbit"})]
