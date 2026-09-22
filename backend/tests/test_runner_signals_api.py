"""서버 신호 실행기 API — 구버전 차단(426), v8 매크로 필수(422), heartbeat commands/acks."""
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunnerCommand, RunnerLaunchTicket, RunSession, get_session
from app.main import app
from tests.test_runner import _auth, _signup

client = TestClient(app)

RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "5m",
       "period": {"preset": "1w"}, "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05}}
A = {"symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "params": {"take_profit_pct": 3.0, "initial_capital": 1000},
     "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0}, "period": {"preset": "3m"}}
DCA = {"symbol": "BTCUSDT", "rule_type": "C", "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "1h",
       "period": {"preset": "3m"}, "params": {"amount_per_buy": 100, "interval_days": 1, "initial_capital": 1000},
       "risk": {"stop_loss_pct": 5}, "fees": {"commission_pct": 0, "slippage_pct": 0}}
SAR = {"symbol": "BTCUSDT", "rule_type": "K", "position_side": "long", "market": "futures", "leverage": 1, "candle_interval": "1h",
       "period": {"preset": "3m"}, "params": {"drop_trigger_pct": 5, "partial_exit_pct": 50, "flip_to_short": True,
                                              "short_take_profit_pct": 3, "short_stop_loss_pct": 2, "initial_capital": 1000},
       "risk": {"stop_loss_pct": 0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}


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


def test_unsupported_rule_types_are_rejected_for_every_runner_version(monkeypatch):
    """C(적립식)·K(SAR)는 실행기로 못 돌린다 — v7 도, v8 도 422. 세션도 드라이버도 만들지 않는다."""
    scheduled = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: scheduled.append(sid))
    token = _signup()
    key = _key(token)
    for macro in (DCA, SAR):
        for version in ("7", "8", ""):
            r = _start(key, macro, version)
            assert r.status_code == 422, (macro["rule_type"], version, r.json())
            assert r.json()["detail"] == runner_mod.UNSUPPORTED_RULE_DETAIL
    assert scheduled == []
    sessions = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    assert sessions["active"] == [] and sessions["recent"] == []
    assert _start(key, A, "7").status_code == 200  # A 는 여전히 v7 로 시작된다


def test_v8_requires_macro_and_schedules_driver(monkeypatch):
    scheduled = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: scheduled.append(sid))
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    key = _key(_signup())
    no_macro = client.post("/api/runner/start", json={"symbol": "ONEUSDT", "runner_version": "8"}, headers={"X-Runner-Key": key})
    assert no_macro.status_code == 422 and no_macro.json()["detail"] == runner_mod.MACRO_REQUIRED_DETAIL
    # 내 매크로 ID 만 보내고 매크로 본문이 없어도 같은 422 — 게이트가 ID 검사보다 먼저 선다.
    id_only = client.post("/api/runner/start", json={"symbol": "ONEUSDT", "runner_version": "8", "user_macro_id": 1},
                          headers={"X-Runner-Key": key})
    assert id_only.status_code == 422 and id_only.json()["detail"] == runner_mod.MACRO_REQUIRED_DETAIL
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


def _v8_session_with_command(monkeypatch, action="buy"):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    token = _signup()
    key = _key(token)
    sid = _start(key, RSI, "8").json()["session_id"]
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = eng.insert_command(db, row, {"action": action, "notional_frac": 1.0, "qty_frac": 1.0 if action == "sell" else 0.0,
                                           "signal_price": 0.01, "reason": "t"}, now_ms=eng._now_ms())
        db.commit()
        cmd_id = cmd.id
    return token, key, sid, cmd_id


def _hb(key, sid, **extra):
    body = {"session_id": sid, "last_price": 0.01, **extra}
    return client.post("/api/runner/heartbeat", json=body, headers={"X-Runner-Key": key}).json()


def _note(sid):
    with get_session() as db:
        return db.get(RunSession, sid).note


def test_heartbeat_keeps_server_owned_note_until_exit_succeeds(monkeypatch):
    # 실행기는 매 heartbeat 에 note(기본 "") 를 보낸다 — 서버가 쓴 '청산 실패' 메모를 덮어쓰면 안 된다.
    _, key, sid, cmd_id = _v8_session_with_command(monkeypatch, action="sell")
    _hb(key, sid, in_position=True, note="", acks=[{"command_id": cmd_id, "ok": False, "error": "insufficient balance"}])
    assert _note(sid) == eng.EXIT_FAIL_NOTE
    body = _hb(key, sid, in_position=True, note="")  # acks 없음, note "" — 메모는 살아 있어야 한다
    assert _note(sid) == eng.EXIT_FAIL_NOTE and [c["id"] for c in body["commands"]] == [cmd_id]
    _hb(key, sid, in_position=True, note="실행기 메모")  # 실행기 메모도 서버 마커를 덮지 못한다
    assert _note(sid) == eng.EXIT_FAIL_NOTE
    _hb(key, sid, in_position=False, note="", acks=[{"command_id": cmd_id, "ok": True, "executed_qty": 3000, "fill_price": 0.01}])
    assert _note(sid) == ""  # 청산 성공 → 마커 해제
    _hb(key, sid, in_position=False, note="정상 메모")  # 마커가 없으면 실행기 note 가 그대로 들어간다
    assert _note(sid) == "정상 메모"
    _stop(key, sid)


def test_heartbeat_returns_no_commands_while_stopping(monkeypatch):
    token, key, sid, cmd_id = _v8_session_with_command(monkeypatch)
    client.post(f"/api/me/runner/sessions/{sid}/request-stop", json={"mode": "close_and_stop"}, headers=_auth(token))
    body = _hb(key, sid, in_position=False)
    assert body["action"] == "close_and_stop" and body["commands"] == []  # 종료 중엔 매매 명령을 주지 않는다
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).status == "pending"  # 명령 자체는 건드리지 않는다
    _stop(key, sid)


def test_position_mismatch_skipped_when_runner_uncertain(monkeypatch):
    monkeypatch.setattr(eng, "schedule_start", lambda sid: None)
    monkeypatch.setattr(eng, "schedule_stop", lambda sid: None)
    calls = []
    monkeypatch.setattr(eng, "check_position_mismatch", lambda db, row, reported: calls.append(reported))
    key = _key(_signup())
    sid = _start(key, RSI, "8").json()["session_id"]
    _hb(key, sid, in_position=True, position_uncertain=True)
    assert calls == []  # 실행기 스스로 포지션을 모르면 대조하지 않는다
    _hb(key, sid, in_position=True, position_uncertain=False)
    assert calls == [True]
    _stop(key, sid)


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


def test_claim_rejects_unsupported_rule_types_without_consuming_ticket():
    token = _signup()
    for macro in (DCA, SAR):
        launch_id, ticket = _ticket(token, macro)
        for version in ("7", "8"):
            r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": version})
            assert r.status_code == 422 and r.json()["detail"] == runner_mod.UNSUPPORTED_RULE_DETAIL
        status = client.get(f"/api/me/runner/launch-tickets/{launch_id}", headers=_auth(token)).json()
        assert status["status"] != "rejected" and status["status"] != "claimed"  # 거절 표시도, 소비도 하지 않는다
        with get_session() as db:
            row = db.get(RunnerLaunchTicket, launch_id)
            assert row.claimed_at == "" and row.rejected_at == ""


def test_claim_allows_old_runner_for_rule_a():
    token = _signup()
    _, ticket = _ticket(token, A)
    r = client.post("/api/runner/launch-tickets/claim", json={"ticket": ticket, "runner_version": "7"})
    assert r.status_code == 200 and r.json()["macro"]["rule_type"] == "A"
