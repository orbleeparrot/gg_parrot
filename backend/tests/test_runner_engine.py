"""실행기 세션 드라이버 — Fill→명령, 만료, ack, 불일치, 복구."""
import asyncio
import json

import pytest
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, RunSessionEvent, get_session
from app.engine import Macro
from app.engine.stepper import Fill


@pytest.fixture(autouse=True)
def _reset_engine_memory():
    """드라이버 레지스트리·불일치 메모는 프로세스 전역 — 한 테스트가 남긴 것이 다음 테스트로 새지 않게."""
    eng._drivers.clear()
    eng._MISMATCH_NOTED.clear()
    yield
    eng._drivers.clear()
    eng._MISMATCH_NOTED.clear()


async def _no_loop(live):
    """틱 루프를 끈다 — 테스트가 _tick_once 를 직접 불러 결정적으로 검사한다(paper 테스트와 같은 방식)."""


def _rsi_json():
    return Macro.model_validate({
        "symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "1w"},
        "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
        "risk": {"invest_ratio": 1.0, "cooldown_minutes": 0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05},
    }).model_dump_json()


def _session(**over):
    fields = dict(user_id=1, symbol="ONEUSDT", position_side="long", leverage=1, market="spot",
                  status="running", started_at="2026-09-22T00:00:00Z", runner_version="8", macro_json=_rsi_json())
    fields.update(over)
    with get_session() as db:
        row = RunSession(**fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _cleanup(session_id):
    with get_session() as db:
        for t in (RunnerCommand, RunSessionEvent):
            for r in db.exec(select(t).where(t.session_id == session_id)).all():
                db.delete(r)
        row = db.get(RunSession, session_id)
        if row:
            db.delete(row)
        db.commit()


def test_command_from_fill_entry_is_notional_fraction_of_initial():
    cmd = eng.command_from_fill(Fill("buy", 0.0125, 1280.0, 32.0, 0.0, reason="RSI 23.1 ≤ 25 · 진입", qty_before=0.0), 32.0)
    assert cmd["action"] == "buy" and abs(cmd["notional_frac"] - 0.5) < 1e-9 and cmd["qty_frac"] == 0.0
    assert cmd["signal_price"] == 0.0125 and cmd["reason"] == "RSI 23.1 ≤ 25 · 진입"


def test_command_from_fill_exit_is_qty_fraction_of_position():
    full = eng.command_from_fill(Fill("sell", 0.013, 1280.0, 33.0, 3.1, reason="청산", qty_before=1280.0), 32.0)
    assert full["qty_frac"] == 1.0 and full["notional_frac"] == 0.0
    part = eng.command_from_fill(Fill("sell", 0.013, 640.0, 33.0, 3.1, reason="부분 청산", qty_before=1280.0), 32.0)
    assert abs(part["qty_frac"] - 0.5) < 1e-9
    orphan = eng.command_from_fill(Fill("cover", 1.0, 5.0, 1.0, 0.0, qty_before=0.0), 32.0)
    assert orphan["qty_frac"] == 1.0  # 직전 수량을 모르면 전량


def test_pending_commands_expire_and_order_by_seq():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "a"}, now_ms=1_000)
            eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.1, "reason": "b"}, now_ms=200_000)
            db.commit()
            out = eng.pending_commands(db, row, now_ms=200_001)
            db.commit()
            assert [c["seq"] for c in out] == [2] and out[0]["action"] == "sell" and out[0]["expires_ms"] == 200_000 + 90_000
            first = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid, RunnerCommand.seq == 1)).one()
            assert first.status == "expired"
    finally:
        _cleanup(sid)


def test_ack_ok_marks_acked_and_logs_order_event():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "RSI 20 ≤ 25 · 진입"}, now_ms=1_000)
            db.commit()
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 30.0, "fill_price": 1.01}], now_ms=5_000)
            db.commit()
            db.refresh(cmd)
            assert cmd.status == "acked" and cmd.executed_qty == 30.0 and cmd.fill_price == 1.01 and cmd.acked_at
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert "order" in kinds
    finally:
        _cleanup(sid)


def test_failed_exit_is_retried_three_times_then_failed_and_noted():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.0, "reason": "청산"}, now_ms=1_000)
            db.commit()
            for i in range(1, 4):
                eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": False, "error": "insufficient"}], now_ms=1_000 + i)
                db.commit()
                db.refresh(cmd)
                if i < 3:
                    assert cmd.status == "pending" and cmd.attempts == i and cmd.expires_ms == 1_000 + i + 90_000
            assert cmd.status == "failed" and cmd.attempts == 3
            assert row.note == "청산 실패 — 확인 필요"
    finally:
        _cleanup(sid)


def test_failed_entry_is_final():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "진입"}, now_ms=1_000)
            db.commit()
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": False, "error": "min notional"}], now_ms=2_000)
            db.commit()
            db.refresh(cmd)
            assert cmd.status == "failed" and cmd.attempts == 1
            msgs = [e.message for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert any("진입 실패" in m for m in msgs)
    finally:
        _cleanup(sid)


def test_position_mismatch_warns_once():
    sid = _session(state_json=json.dumps({"in_position": True, "legs": []}))
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            eng.check_position_mismatch(db, row, reported_in_position=False)
            eng.check_position_mismatch(db, row, reported_in_position=False)
            db.commit()
            warns = [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() if e.kind == "warn"]
            assert len(warns) == 1
            eng.check_position_mismatch(db, row, reported_in_position=True)  # 일치하면 다음 불일치에 다시 1회
            eng.check_position_mismatch(db, row, reported_in_position=False)
            db.commit()
            warns = [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() if e.kind == "warn"]
            assert len(warns) == 2
    finally:
        _cleanup(sid)


def test_start_driver_warms_up_subscribes_and_writes_commands_on_fill(monkeypatch):
    sid = _session()

    class FakeFeed:
        subs = []
        since = []

        async def history(self, symbol, interval, market, n):
            return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]

        def subscribe(self, symbol, interval, market, cb, *, since_t=None):
            self.subs.append(cb)
            self.since.append(since_t)
            return ("sub", symbol)

        def unsubscribe(self, sub):
            self.subs.clear()

    fake = FakeFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 50.0)
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        assert fake.subs and live.driver.state()["in_position"] is False
        assert fake.since == [19]  # 웜업 마지막 봉의 t 로 커서를 시딩 — 같은 봉을 두 번 받지 않는다
        for c in (50, 50):  # 웜업 끝 RSI(7) 낮음 → enter → 다음 봉 시가 체결
            await fake.subs[0]("ONEUSDT", (0, c, c, c, c))
        await eng._tick_once(live)  # 큐 드레인 → 명령 기록
        with get_session() as db:
            cmds = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
            assert [c.action for c in cmds] == ["buy"] and cmds[0].reason.startswith("RSI ")
            assert cmds[0].status == "pending" and cmds[0].seq == 1
            assert abs(cmds[0].signal_price - 50.025) < 1e-9  # 시가 50 + 슬리피지 0.05% — 엔진 체결가 그대로
            assert abs(cmds[0].notional_frac - 1.0) < 1e-6 and cmds[0].qty_frac == 0.0  # invest_ratio 1.0 → 초기자본 전액
            row = db.get(RunSession, sid)
            assert json.loads(row.state_json)["in_position"] is True
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert kinds == ["signal"]
        await eng.stop_driver(sid)
        assert sid not in eng._drivers and fake.subs == []

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_start_driver_restores_checkpoint_after_warmup(monkeypatch):
    """복구는 웜업 → 복구 순서 — 웜업이 장부를 비우므로 체크포인트 포지션이 남아야 한다."""
    state = {"in_position": True, "trade_count": 3, "cooldown_until_ms": None,
             "legs": [{"symbol": "ONEUSDT", "qty": 0.5, "dir": 1, "entry_price": 60.0, "last_price": 55.0, "in_position": True}]}
    sid = _session(state_json=json.dumps(state))

    class FakeFeed:
        subs = []

        async def history(self, symbol, interval, market, n):
            return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]

        def subscribe(self, symbol, interval, market, cb, **kwargs):
            self.subs.append(cb)
            return ("sub", symbol)

        def unsubscribe(self, sub):
            self.subs.clear()

    monkeypatch.setattr(eng, "feed", FakeFeed())
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        st = live.driver.state()
        assert st["in_position"] is True and st["trade_count"] == 3 and st["legs"][0]["qty"] == 0.5
        await eng.stop_driver(sid)

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_loop_cleans_up_when_session_stops_in_db(monkeypatch):
    """마이페이지가 세션을 끝내면 체크포인트가 그걸 보고 루프가 스스로 멈춘다 — 등록·구독도 함께 정리."""
    sid = _session()

    class FakeFeed:
        subs = []

        async def history(self, symbol, interval, market, n):
            return []

        def subscribe(self, symbol, interval, market, cb, **kwargs):
            self.subs.append(cb)
            return ("sub", symbol)

        def unsubscribe(self, sub):
            self.subs.clear()

    fake = FakeFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 50.0)
    monkeypatch.setattr(eng, "POLL_SECONDS", 0)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        with get_session() as db:  # 첫 틱 전에 세션을 끝낸다
            row = db.get(RunSession, sid)
            row.status = "stopped"
            db.add(row)
            db.commit()
        await asyncio.wait_for(live.task, timeout=5)
        assert live.stop_flag is True and sid not in eng._drivers and fake.subs == []

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_start_driver_refuses_missing_or_stopped_session():
    sid = _session(status="stopped")
    try:
        loop = asyncio.new_event_loop()
        assert loop.run_until_complete(eng.start_driver(sid)) is False
        assert loop.run_until_complete(eng.start_driver(999_999_999)) is False
        assert sid not in eng._drivers
    finally:
        _cleanup(sid)


def test_schedule_without_loop_is_noop(monkeypatch):
    monkeypatch.setattr(eng, "_loop", None)
    eng.schedule_start(1)  # 로그만 남기고 조용히 넘어간다
    eng.schedule_stop(1)


def test_resume_only_v8_running_sessions(monkeypatch):
    old = _session(runner_version="7")
    new = _session(runner_version="8", status="stopped")
    started = []

    async def fake_start(session_id):
        started.append(session_id)
        return True

    monkeypatch.setattr(eng, "start_driver", fake_start)
    try:
        n = asyncio.new_event_loop().run_until_complete(eng.resume_running_runner_sessions())
        assert n == 0 and started == []
    finally:
        _cleanup(old)
        _cleanup(new)


def test_resume_starts_running_v8_session(monkeypatch):
    sid = _session(runner_version="8")
    started = []

    async def fake_start(session_id):
        started.append(session_id)
        return True

    monkeypatch.setattr(eng, "start_driver", fake_start)
    try:
        n = asyncio.new_event_loop().run_until_complete(eng.resume_running_runner_sessions())
        assert n == 1 and started == [sid]
    finally:
        _cleanup(sid)
