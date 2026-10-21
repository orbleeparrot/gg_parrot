"""체결 기준 장부 세션의 하트비트 흐름(2026-10-21 과제 9 · 11).

로컬 손절 체결이 보고되면 그 포지션을 전제로 만든 대기 명령(익절 · 물타기 · 재진입)을 같은 하트비트에서 취소한다.
정책 세션은 옛 '안 나간 진입 되돌리기'를 타지 않는다 — 장부는 체결 기록표를 접어 맞춘다.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunnerFill, RunSession, RunSessionEvent, get_session
from app.main import app
from tests.test_fill_ledger_entries import H_KRW
from tests.test_runner import _auth, _signup

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    eng._MISMATCH_NOTED.clear()
    yield
    eng._MISMATCH_NOTED.clear()


@pytest.fixture
def started():
    key = client.get("/api/me/runner/key", headers=_auth(_signup())).json()["key"]
    body = {"symbol": "KRW-GTC", "exchange": "upbit", "position_side": "long", "leverage": 1, "market": "spot",
            "testnet": False, "mode": "live", "human_summary": "t", "macro": H_KRW, "runner_version": "21"}
    r = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    with get_session() as db:
        assert db.get(RunSession, sid).exec_policy == eng.FILL_LEDGER_POLICY
    yield key, sid
    client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})


def _beat(key, sid, **snapshot):
    r = client.post("/api/runner/heartbeat", json={"session_id": sid, **snapshot}, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def _cmd(sid, action, ref, price=500.0):
    with get_session() as db:
        row = db.get(RunSession, sid)
        body = {"action": action, "notional_frac": 0.1 if action == "buy" else 0.0,
                "qty_frac": 1.0 if action == "sell" else 0.0, "signal_price": price, "reason": "t", "order_ref": ref}
        cmd = eng.insert_command(db, row, body, now_ms=eng._now_ms())
        db.commit()
        return cmd.id


def _status(cmd_id):
    with get_session() as db:
        return db.get(RunnerCommand, cmd_id).status


LOCAL = {"client_order_id": "ggp-local-1", "exchange_order_id": "ex-1", "status": "FILLED", "side": "sell",
         "executed_qty": 100.0, "acquired_qty": 100.0, "fill_price": 474.0, "fee_quote": 23.7, "fee_known": True,
         "reason": "stop_loss", "ts_ms": 1_761_000_000_000}


def test_local_stop_loss_cancels_the_commands_built_on_that_position(started):
    key, sid = started
    tp, so = _cmd(sid, "sell", "o2", 510.0), _cmd(sid, "buy", "o3", 495.0)
    reply = _beat(key, sid, in_position=False, position_qty=0.0, local_fills=[LOCAL])
    assert reply["commands"] == []
    assert _status(tp) == "cancelled" and _status(so) == "cancelled"
    with get_session() as db:
        [fill] = db.exec(select(RunnerFill).where(RunnerFill.session_id == sid)).all()
        assert (fill.source, fill.reason, fill.executed_qty) == ("local", "stop_loss", 100.0)
    again = _beat(key, sid, in_position=False, position_qty=0.0, local_fills=[LOCAL])  # 같은 보고가 다시 와도
    assert again["commands"] == []
    new = _cmd(sid, "buy", "o4", 480.0)  # 그 뒤 서버 전략이 낸 새 명령은 다시 온 옛 보고가 지우지 않는다
    _beat(key, sid, in_position=False, position_qty=0.0, local_fills=[LOCAL])
    assert _status(new) == "pending"


def test_partial_local_close_cancels_only_entries(started):
    key, sid = started
    tp, so = _cmd(sid, "sell", "o2", 510.0), _cmd(sid, "buy", "o3", 495.0)
    reply = _beat(key, sid, in_position=True, position_qty=40.0, entry_price=500.0,
                  local_fills=[{**LOCAL, "executed_qty": 60.0, "acquired_qty": 60.0}])
    assert _status(so) == "cancelled" and _status(tp) == "pending"
    assert [c["id"] for c in reply["commands"]] == [tp]


def test_policy_session_never_takes_the_old_entry_rollback(started, monkeypatch):
    key, sid = started

    def boom(*args, **kwargs):
        raise AssertionError("old rollback path")

    monkeypatch.setattr(eng, "settle_unexecuted_entries", boom)
    buy = _cmd(sid, "buy", "o1")
    _beat(key, sid, in_position=False, position_qty=0.0,
          acks=[{"command_id": buy, "ok": False, "executed_qty": 0.0, "fill_price": 0.0, "error": "잔고 부족", "orders": []}])
    assert _status(buy) == "failed"


def _mismatch_events(sid):
    with get_session() as db:
        return [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
                if e.event_name in ("engine.position_mismatch", "engine.ledger_mismatch")]


def _set_state(sid, *, in_position, qty=0.0, orders=None, dust=None):
    ledger = {"v": 1, "seq": 3, "epoch": 1, "lots": ([{"ref": "o1", "qty": qty, "fill": 500.0, "margin": qty * 500.0}]
                                                     if in_position else []),
              "orders": orders or {}, "exit": None, "dust": dust, "deferred": None, "keys": [], "recent": {}}
    state = {"in_position": in_position, "trade_count": 1, "legs": [{
        "symbol": "KRW-GTC", "qty": qty if in_position else 0.0, "dir": 1, "entry_price": 500.0 if in_position else 0.0,
        "last_price": 500.0, "in_position": in_position,
        "strategy": {"so_done": 0, "base_price": 500.0, "ledger": ledger, "reopen": {}}}]}
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.state_json = json.dumps(state)
        db.add(row)
        db.commit()


def test_mismatch_waits_while_reports_are_not_folded_yet(started):
    key, sid = started
    _set_state(sid, in_position=True, qty=100.0)  # 아직 접기 전 — 서버 장부는 보유 중
    _beat(key, sid, in_position=False, position_qty=0.0, local_fills=[LOCAL])
    assert _mismatch_events(sid) == []  # 접지 않은 체결 행이 있다 — 경고하지 않는다
    _set_state(sid, in_position=True, qty=100.0, orders={"o2": {"side": "sell", "kind": "exit", "epoch": 1}})
    with get_session() as db:
        for f in db.exec(select(RunnerFill).where(RunnerFill.session_id == sid)).all():
            f.applied_ms = 1
            db.add(f)
        db.commit()
    _beat(key, sid, in_position=False, position_qty=0.0)
    assert _mismatch_events(sid) == []  # 장부에 미결 주문이 있다 — 아직 정리 중


def test_ledger_quantity_mismatch_is_reported_once(started):
    key, sid = started
    _set_state(sid, in_position=True, qty=100.0)
    _beat(key, sid, in_position=True, position_qty=100.0, entry_price=500.0)
    assert _mismatch_events(sid) == []
    _beat(key, sid, in_position=True, position_qty=88.0, entry_price=500.0)
    _beat(key, sid, in_position=True, position_qty=88.0, entry_price=500.0)
    [warn] = _mismatch_events(sid)
    assert warn.event_name == "engine.ledger_mismatch" and "100" in warn.message and "88" in warn.message
    _beat(key, sid, in_position=True, position_qty=100.0, entry_price=500.0)  # 다시 맞으면 다음 불일치에 또 한 번
    _beat(key, sid, in_position=True, position_qty=90.0, entry_price=500.0)
    assert len(_mismatch_events(sid)) == 2


def test_dust_counts_on_both_sides(started):
    key, sid = started
    _set_state(sid, in_position=False, dust={"qty": 0.01, "fill": 500.0, "margin": 5.0})
    _beat(key, sid, in_position=False, position_qty=0.01)  # 실행기도 무포지션 · 먼지 0.01 을 보고한다
    assert _mismatch_events(sid) == []
