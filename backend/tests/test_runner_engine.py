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
    now = eng._now_iso()  # start_session 처럼 시작 시각·마지막 heartbeat 를 지금으로 — 오래된 값이면 체크포인트가 세션을 닫는다
    fields = dict(user_id=1, symbol="ONEUSDT", position_side="long", leverage=1, market="spot",
                  status="running", started_at=now, last_heartbeat_at=now, runner_version="8", macro_json=_rsi_json())
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


def test_exit_commands_never_expire_but_entries_do():
    """청산은 TTL 이 지나도 pending 으로 남아 실행기에 다시 간다 — 버리면 포지션이 남는다. 진입은 만료된다."""
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.0, "reason": "청산"}, now_ms=1_000)
            eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.1, "reason": "진입"}, now_ms=2_000)
            eng.insert_command(db, row, {"action": "cover", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.2, "reason": "청산"}, now_ms=3_000)
            db.commit()
            out = eng.pending_commands(db, row, now_ms=10_000_000)  # TTL(90s) 훨씬 뒤
            db.commit()
            assert [(c["seq"], c["action"]) for c in out] == [(1, "sell"), (3, "cover")]
            assert out[0]["expires_ms"] == 1_000 + 90_000  # 필드는 그대로 실린다(형식 유지) — 서버는 청산에 만료를 적용하지 않는다
            statuses = {c.seq: c.status for c in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()}
            assert statuses == {1: "pending", 2: "expired", 3: "pending"}
    finally:
        _cleanup(sid)


def test_ok_ack_on_expired_command_is_accepted_but_failure_is_ignored():
    """실행기가 만료 직전에 실행한 명령의 ok ack 는 받아들인다(실주문이 나갔다). 만료 명령의 실패 ack 는 무시."""
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            a = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "진입"}, now_ms=1_000)
            b = eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.5, "reason": "진입"}, now_ms=1_000)
            db.commit()
            assert eng.pending_commands(db, row, now_ms=500_000) == []
            db.commit()
            db.refresh(a); db.refresh(b)
            assert a.status == "expired" and b.status == "expired"
            eng.apply_acks(db, row, [{"command_id": a.id, "ok": True, "executed_qty": 30.0, "fill_price": 1.01},
                                     {"command_id": b.id, "ok": False, "error": "timeout"}], now_ms=500_001)
            db.commit()
            db.refresh(a); db.refresh(b)
            assert a.status == "acked" and a.executed_qty == 30.0 and a.fill_price == 1.01 and a.attempts == 1
            assert b.status == "expired" and b.attempts == 0 and b.error == ""
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert kinds == ["order"]
    finally:
        _cleanup(sid)


def test_insert_command_dedupes_same_signal_within_window():
    """직전 명령과 action·signal_price 가 같고 60초 안이면 기존 행을 돌려준다(롤링 배포 겹침). 죽은 명령·다른 신호는 새로."""
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            same = {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 1.0, "reason": "진입"}
            first, created = eng.upsert_command(db, row, same, now_ms=10_000)
            assert created is True and first.seq == 1
            dup, created = eng.upsert_command(db, row, dict(same), now_ms=10_000 + 59_000)
            assert created is False and dup.id == first.id
            assert eng.insert_command(db, row, dict(same), now_ms=10_000 + 30_000).id == first.id
            first.status = "acked"  # 실행기가 이미 실행한 명령도 창 안이면 중복으로 본다
            db.add(first)
            db.flush()
            assert eng.upsert_command(db, row, dict(same), now_ms=10_000 + 40_000)[1] is False
            other_price, created = eng.upsert_command(db, row, {**same, "signal_price": 1.01}, now_ms=10_000 + 40_000)
            assert created is True and other_price.seq == 2
            late, created = eng.upsert_command(db, row, {**same, "signal_price": 1.01}, now_ms=10_000 + 40_000 + 61_000)
            assert created is True and late.seq == 3  # 창 밖이면 새 명령
            late.status = "failed"
            db.add(late)
            db.flush()
            again, created = eng.upsert_command(db, row, {**same, "signal_price": 1.01}, now_ms=10_000 + 40_000 + 62_000)
            assert created is True and again.seq == 4  # failed/expired 는 다시 넣는다
            db.commit()
    finally:
        _cleanup(sid)


def test_persist_fill_skips_signal_event_for_duplicate_command(monkeypatch):
    sid = _session()
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "_run", _no_loop)
    try:
        loop = asyncio.new_event_loop()
        assert loop.run_until_complete(eng.start_driver(sid)) is True
        live = eng._drivers[sid]
        fill = Fill("buy", 50.0, 0.64, 32.0, 0.0, reason="RSI 1.0 ≤ 25 · 진입", qty_before=0.0)
        eng._persist_fill(live, fill, "{}")
        eng._persist_fill(live, fill, "{}")  # 다른 프로세스가 같은 신호를 이미 남긴 상황과 같다
        with get_session() as db:
            cmds = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
            assert len(cmds) == 1
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert kinds == ["signal"]
        loop.run_until_complete(eng.stop_driver(sid))
    finally:
        _cleanup(sid)


def test_shutdown_drivers_stops_every_live_driver(monkeypatch):
    a, b = _session(), _session()
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 50.0)
    monkeypatch.setattr(eng, "POLL_SECONDS", 0.01)

    async def scenario():
        assert await eng.start_driver(a) and await eng.start_driver(b)
        tasks = [eng._drivers[a].task, eng._drivers[b].task]
        assert await eng.shutdown_drivers() == 2
        assert eng._drivers == {} and fake.subs == []
        assert all(t.done() for t in tasks)
        assert await eng.shutdown_drivers() == 0  # 비어 있으면 아무것도 안 한다

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(a)
        _cleanup(b)


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


def test_ack_ok_after_retry_clears_exit_fail_note():
    sid = _session()
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            cmd = eng.insert_command(db, row, {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 1.0, "reason": "청산"}, now_ms=1_000)
            db.commit()
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": False, "error": "insufficient"}], now_ms=2_000)
            db.commit()
            assert row.note == eng.EXIT_FAIL_NOTE
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 1.0, "fill_price": 1.0}], now_ms=3_000)
            db.commit()
            db.refresh(cmd)
            db.refresh(row)
            assert cmd.status == "acked" and row.note == ""
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
            return [(i, 100, 100, 100, 100) for i in range(20)]  # 평탄한 웜업 — 의도 없이 시작한다

        def subscribe(self, symbol, interval, market, cb, *, since_t=None):
            self.subs.append(cb)
            self.since.append(since_t)
            return ("sub", symbol)

        def unsubscribe(self, sub):
            self.subs.clear()

    fake = FakeFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 91.0)
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        assert fake.subs and live.driver.state()["in_position"] is False
        assert fake.since == [19]  # 웜업 마지막 봉의 t 로 커서를 시딩 — 같은 봉을 두 번 받지 않는다
        await eng._tick_once(live)  # 의도가 없으면 틱은 시세 갱신뿐
        await fake.subs[0]("ONEUSDT", (0, 90, 90, 90, 90))  # 급락 봉 마감 → RSI(7) 0 → enter 의도(체결은 아직)
        assert live.driver.state()["in_position"] is False
        await eng._tick_once(live)  # 봉 뒤 첫 틱(≈ 다음 시가)에 체결 → 명령 기록
        with get_session() as db:
            cmds = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
            assert [c.action for c in cmds] == ["buy"] and cmds[0].reason.startswith("RSI ")
            assert cmds[0].status == "pending" and cmds[0].seq == 1
            assert abs(cmds[0].signal_price - 91.0 * 1.0005) < 1e-9  # 틱 가격 91 + 슬리피지 0.05% — 엔진 체결가 그대로
            assert abs(cmds[0].notional_frac - 1.0) < 1e-6 and cmds[0].qty_frac == 0.0  # invest_ratio 1.0 → 초기자본 전액
            row = db.get(RunSession, sid)
            assert json.loads(row.state_json)["in_position"] is True
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert kinds == ["signal"]
        await fake.subs[0]("ONEUSDT", (0, 91, 91, 91, 91))  # 다음 봉이 같은 의도를 다시 체결하지 않는다
        await eng._tick_once(live)
        with get_session() as db:
            assert len(db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()) == 1
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


class _RsiFeed:
    """급락 웜업 + 구독 기록 — start_driver 시나리오 공용 더미."""

    def __init__(self):
        self.subs = []

    async def history(self, symbol, interval, market, n):
        return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]

    def subscribe(self, symbol, interval, market, cb, **kwargs):
        self.subs.append(cb)
        return ("sub", symbol)

    def unsubscribe(self, sub):
        self.subs.clear()


def test_concurrent_start_driver_creates_one_driver(monkeypatch):
    """같은 세션을 동시에 두 번 시작해도 드라이버·구독은 하나 — 명령이 두 번 나가면 실주문이 두 번 나간다."""
    sid = _session()
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        results = await asyncio.gather(eng.start_driver(sid), eng.start_driver(sid), eng.start_driver(sid))
        assert results == [True, True, True]
        assert list(eng._drivers) == [sid] and len(fake.subs) == 1 and sid not in eng._starting
        await eng.stop_driver(sid)
        assert eng._drivers == {} and fake.subs == []

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_persist_failure_keeps_fill_and_retries_next_tick(monkeypatch):
    """DB 저장이 한 번 실패해도 체결(신호)은 사라지지 않고 다음 틱에 명령으로 기록된다."""
    sid = _session()
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s: 50.0)
    monkeypatch.setattr(eng, "_run", _no_loop)
    real_persist = eng._persist_fill
    calls = []

    def flaky(live, fill, state_json):
        calls.append(fill)
        if len(calls) == 1:
            raise RuntimeError("db down")
        real_persist(live, fill, state_json)

    monkeypatch.setattr(eng, "_persist_fill", flaky)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        for c in (50, 50):
            await fake.subs[0]("ONEUSDT", (0, c, c, c, c))
        await eng._tick_once(live)  # 첫 저장 실패 → unsaved 에 보관
        assert len(live.unsaved) == 1 and live.stop_flag is False
        with get_session() as db:
            assert db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all() == []
        await eng._tick_once(live)  # 다음 틱: 먼저 다시 저장
        assert live.unsaved == [] and len(calls) == 2
        with get_session() as db:
            cmds = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
            assert [c.action for c in cmds] == ["buy"]
            assert json.loads(db.get(RunSession, sid).state_json)["in_position"] is True
        await eng.stop_driver(sid)

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_loop_death_notes_session_and_cleans_up(monkeypatch):
    """루프가 예상 못 한 오류로 죽으면 세션 note·error 이벤트를 남기고 등록·구독을 정리한다."""
    sid = _session()
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)

    async def boom(live):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(eng, "_tick_once", boom)

    async def scenario():
        assert await eng.start_driver(sid) is True
        live = eng._drivers[sid]
        await asyncio.wait_for(live.task, timeout=5)
        assert sid not in eng._drivers and fake.subs == []
        with get_session() as db:
            assert db.get(RunSession, sid).note == eng.LOOP_ERROR_NOTE
            kinds = [e.kind for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
            assert kinds == ["error"]

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)


def test_schedule_runs_on_installed_loop(monkeypatch):
    started = []

    async def fake_start(session_id):
        started.append(session_id)
        return True

    monkeypatch.setattr(eng, "start_driver", fake_start)
    loop = asyncio.new_event_loop()
    try:
        eng.install(loop)
        eng.schedule_start(7)
        loop.run_until_complete(asyncio.sleep(0.01))
        assert started == [7] and eng._scheduled == set()
    finally:
        monkeypatch.setattr(eng, "_loop", None)
        loop.close()


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


def test_checkpoint_stops_session_when_runner_heartbeat_is_stale(monkeypatch):
    """실행기가 1시간 넘게 heartbeat 를 안 보내면 체크포인트가 세션을 닫고(stopped·stop 이벤트) 루프를 멈춘다."""
    sid = _session(last_heartbeat_at="2026-09-22T00:00:00Z", in_position=True)  # 오래전
    fresh = _session(last_heartbeat_at=eng._now_iso())
    fake = _RsiFeed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "_run", _no_loop)

    async def scenario():
        assert await eng.start_driver(sid) and await eng.start_driver(fresh)
        stale_live, fresh_live = eng._drivers[sid], eng._drivers[fresh]
        eng._persist_state(fresh_live, "{}")
        assert fresh_live.stop_flag is False  # 최근 heartbeat 면 그대로
        eng._persist_state(stale_live, '{"in_position": true}')
        assert stale_live.stop_flag is True
        with get_session() as db:
            row = db.get(RunSession, sid)
            assert row.status == "stopped" and row.stopped_at and row.note == eng.STALE_RUNNER_NOTE
            assert row.state_json == '{"in_position": true}'
            events = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
            assert [e.kind for e in events] == ["stop"] and "포지션 보유 중" in events[0].message
            assert db.get(RunSession, fresh).status == "running"
        await eng.stop_driver(sid)
        await eng.stop_driver(fresh)

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        _cleanup(sid)
        _cleanup(fresh)


def test_stale_runner_threshold_falls_back_to_started_at_and_tolerates_bad_iso():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
    row = RunSession(user_id=1, started_at="2026-09-22T11:59:00Z", last_heartbeat_at="")
    assert eng._runner_is_stale(row, now) is False
    row.started_at = "2026-09-22T09:00:00Z"
    assert eng._runner_is_stale(row, now) is True
    row.last_heartbeat_at = "2026-09-22T11:30:00Z"  # heartbeat 가 있으면 그게 기준
    assert eng._runner_is_stale(row, now) is False
    row.last_heartbeat_at = "garbage"
    row.started_at = "also garbage"
    assert eng._runner_is_stale(row, now) is False  # 못 읽으면 닫지 않는다


def test_start_driver_refuses_missing_or_stopped_session():
    sid = _session(status="stopped")
    try:
        loop = asyncio.new_event_loop()
        assert loop.run_until_complete(eng.start_driver(sid)) is False
        assert loop.run_until_complete(eng.start_driver(999_999_999)) is False
        assert sid not in eng._drivers
        with get_session() as db:  # 종료된 세션은 '시작 실패' 로 표시하지 않는다
            assert db.get(RunSession, sid).note == ""
            assert db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all() == []
    finally:
        _cleanup(sid)


@pytest.mark.parametrize("macro_json", ["", "{not json", '{"symbol": "ONEUSDT"}'])
def test_start_driver_failure_on_running_session_is_noted(macro_json):
    """running 세션인데 매크로가 비었거나 깨져 드라이버를 못 만들면 note·error 이벤트로 알린다 — 조용히 죽지 않는다."""
    sid = _session(macro_json=macro_json)
    try:
        assert asyncio.new_event_loop().run_until_complete(eng.start_driver(sid)) is False
        assert sid not in eng._drivers
        with get_session() as db:
            assert db.get(RunSession, sid).note == eng.START_FAIL_NOTE
            events = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
            assert [e.kind for e in events] == ["error"] and "시작하지 못했어요" in events[0].message
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
