"""서버 신호 실행기 API — 구버전 차단(426), v8 매크로 필수(422), heartbeat commands/acks."""
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, get_session
from app.main import app
from tests.test_runner import _auth, _signup

client = TestClient(app)

RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "5m",
       "period": {"preset": "1w"}, "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05}}
A = {"symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "params": {"take_profit_pct": 3.0, "initial_capital": 1000},
     "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0}, "period": {"preset": "3m"}}


def _key(token):
    return client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]


def _start(key, macro, version):
    body = {"symbol": macro["symbol"], "position_side": "long", "leverage": 1, "market": "spot", "testnet": True,
            "human_summary": "t", "macro": macro, "runner_version": version}
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


def _stop(key, sid):
    # running 인 v8 세션을 남기지 않는다 — test_runner_engine 의 재기동 복구 테스트가 DB 의 running v8 세션을 센다.
    return client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})


def test_old_runner_cannot_start_indicator_macro():
    key = _key(_signup())
    r = _start(key, RSI, "7")
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.SIGNAL_REQUIRED_DETAIL
    assert _start(key, RSI, "").status_code == 426  # 버전 없는 v6 도 막힌다


def test_old_runner_without_macro_is_blocked():
    # 2026-09-22 결정: 매크로를 안 보내는 v6 는 지표형인지 판별할 수 없으므로 차단한다.
    key = _key(_signup())
    r = client.post("/api/runner/start", json={"symbol": "BTCUSDT"}, headers={"X-Runner-Key": key})
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.SIGNAL_REQUIRED_DETAIL


def test_old_runner_can_still_start_rule_a():
    key = _key(_signup())
    assert _start(key, A, "7").status_code == 200
    assert _start(key, A, "").status_code == 200  # 버전 없는 실행기도 A/B 는 로컬 판단으로 충분하다


def test_v8_requires_macro_and_schedules_driver(monkeypatch):
    scheduled = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: scheduled.append(sid))
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    key = _key(_signup())
    no_macro = client.post("/api/runner/start", json={"symbol": "ONEUSDT", "runner_version": "8"}, headers={"X-Runner-Key": key})
    assert no_macro.status_code == 422 and no_macro.json()["detail"] == runner_mod.MACRO_REQUIRED_DETAIL
    r = _start(key, RSI, "8")
    assert r.status_code == 200 and scheduled == [r.json()["session_id"]]
    _stop(key, r.json()["session_id"])


def test_v7_rule_a_does_not_schedule_driver(monkeypatch):
    scheduled = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: scheduled.append(sid))
    key = _key(_signup())
    assert _start(key, A, "7").status_code == 200
    assert scheduled == []


def test_heartbeat_returns_pending_commands_and_applies_acks(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    stopped = []
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: stopped.append(sid))
    key = _key(_signup())
    sid = _start(key, RSI, "8").json()["session_id"]
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 0.01, "reason": "RSI 20 ≤ 25 · 진입"}, now_ms=eng._now_ms())
        db.commit()
        cmd_id = cmd.id
    hb = client.post("/api/runner/heartbeat", json={"session_id": sid, "in_position": False, "last_price": 0.01}, headers={"X-Runner-Key": key})
    body = hb.json()
    assert body["action"] == "continue" and [c["id"] for c in body["commands"]] == [cmd_id]
    assert body["commands"][0]["action"] == "buy" and body["commands"][0]["seq"] == 1
    hb2 = client.post("/api/runner/heartbeat", json={"session_id": sid, "in_position": True, "last_price": 0.01,
                                                     "acks": [{"command_id": cmd_id, "ok": True, "executed_qty": 3000, "fill_price": 0.0101}]},
                      headers={"X-Runner-Key": key})
    assert hb2.json()["commands"] == []
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).status == "acked"
    _stop(key, sid)
    assert stopped == [sid]
    # 종료된 세션의 heartbeat 도 commands 키를 갖는다(응답 형태 통일).
    gone = client.post("/api/runner/heartbeat", json={"session_id": sid}, headers={"X-Runner-Key": key}).json()
    assert gone["action"] == "stop_only" and gone["commands"] == []


def test_old_runner_heartbeat_has_empty_commands(monkeypatch):
    key = _key(_signup())
    sid = _start(key, A, "7").json()["session_id"]
    hb = client.post("/api/runner/heartbeat", json={"session_id": sid, "last_price": 100.0}, headers={"X-Runner-Key": key}).json()
    assert hb == {"action": "continue", "commands": []}


def _ticket(token, macro):
    saved = client.post("/api/me/macros", json={"macro": macro, "name": "t"}, headers=_auth(token)).json()["item"]
    created = client.post("/api/me/runner/launch-tickets", json={"user_macro_id": saved["id"], "testnet": True}, headers=_auth(token)).json()
    ticket = parse_qs(urlsplit(created["launch_url"]).query)["ticket"][0]
    return created["launch_id"], ticket


def test_claim_rejects_old_runner_for_indicator_macro():
    token = _signup()
    launch_id, ticket = _ticket(token, RSI)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "7"})
    assert r.status_code == 426 and r.json()["detail"] == runner_mod.SIGNAL_REQUIRED_DETAIL
    status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
    assert status["status"] == "rejected" and status["runner_version"] == "7"
    # 티켓은 살아 있다 — v8 실행기가 이어서 받아갈 수 있다.
    ok = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "8"})
    assert ok.status_code == 200 and ok.json()["macro"]["rule_type"] == "F"


def test_claim_allows_old_runner_for_rule_a():
    token = _signup()
    _, ticket = _ticket(token, A)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "7"})
    assert r.status_code == 200 and r.json()["macro"]["rule_type"] == "A"
