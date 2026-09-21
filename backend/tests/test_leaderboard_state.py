"""리더보드 실시간 상태 — 시뮬레이터 state(), 체크포인트 state_json, 행 상태 파생, /api/prices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app import marketdata
from app.engine import Macro
from app.engine.stepper import DcaSim, PositionSim, make_sim

client = TestClient(app)


def _macro(rule="A", **over):
    base = {
        "name": "t", "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long",
        "market": "spot", "leverage": 1, "candle_interval": "1h",
        "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000} if rule == "A" else {"amount_per_buy": 100, "interval_days": 1},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 30, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


def test_position_sim_state_before_after_entry_and_exit():
    sim = make_sim(_macro("A"), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    flat = sim.state()
    assert flat == {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    sim.step(100.0, t0)  # rule A enters immediately
    held = sim.state()
    assert held["in_position"] is True and held["qty"] > 0 and held["entry_price"] == 100.0
    sim.step(98.9, t0 + timedelta(minutes=1))  # stop-loss → cooldown 30m
    after = sim.state()
    assert after["in_position"] is False and after["qty"] == 0.0
    assert after["cooldown_until_ms"] == int((t0 + timedelta(minutes=31)).timestamp() * 1000)
    assert after["halted_today"] is False


def test_position_sim_short_dir_and_daily_halt():
    sim = make_sim(_macro("A", position_side="short", market="futures", leverage=2,
                          risk={"stop_loss_pct": 50, "daily_max_loss_pct": 1, "cooldown_minutes": 0, "max_holding_hours": 0}), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    sim.step(100.0, t0)
    assert sim.state()["dir"] == -1
    sim.step(103.0, t0 + timedelta(minutes=1))  # short loses > 1% of day-start equity → halt
    st = sim.state()
    assert st["in_position"] is False and st["halted_today"] is True
    sim.step(103.0, t0 + timedelta(days=1))  # next day clears the halt
    assert sim.state()["halted_today"] is False


def test_dca_sim_state_tracks_average_cost():
    sim = make_sim(_macro("C"), 1_000.0)
    assert sim.state()["in_position"] is False
    sim.step(100.0, None)
    sim.step(200.0, None)
    st = sim.state()
    assert st["in_position"] is True and st["dir"] == 1
    assert abs(st["entry_price"] - (sim.cost_basis / sim.qty)) < 1e-9
    assert st["cooldown_until_ms"] is None


# ---------------------------------------------------------------------------
# Task 2: 러너 체결 추적 + state_json 체크포인트 + 10s
# ---------------------------------------------------------------------------
import json
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app import paper
from app.db import PaperSession
from app.engine.stepper import Fill


class _StatefulSim:
    def __init__(self):
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.in_pos = False
        self.fills = []

    def step(self, price, ts):
        return self.fills.pop(0) if self.fills else None

    def equity(self, price):
        return 1_010.0

    def state(self):
        return {"in_position": self.in_pos, "dir": 1, "qty": 2.0 if self.in_pos else 0.0,
                "entry_price": 100.0 if self.in_pos else 0.0, "cooldown_until_ms": None, "halted_today": False}


def test_snapshot_state_tracks_fills_and_kind():
    sim = _StatefulSim()
    runner = paper._Runner(7, sim, "BTCUSDT", "live", 1_000.0)
    st = paper._snapshot(runner)["state"]
    assert st["in_position"] is False and st["trade_count"] == 0 and st["last_fill_kind"] == "" and st["legs"][0]["symbol"] == "BTCUSDT"
    sim.in_pos = True
    entry = Fill(side="buy", price=100.0, qty=2.0, equity_after=1_000.0, return_pct=0.0)
    paper._note_fill(runner, entry, "BTCUSDT")
    st = paper._snapshot(runner)["state"]
    assert st["trade_count"] == 1 and st["last_fill_side"] == "buy" and st["last_fill_kind"] == "" and st["in_position"] is True
    assert st["legs"][0]["qty"] == 2.0 and st["legs"][0]["entry_price"] == 100.0 and st["legs"][0]["dir"] == 1
    sim.in_pos = False
    exit_ = Fill(side="sell", price=102.0, qty=2.0, equity_after=1_004.0, return_pct=0.4)
    paper._note_fill(runner, exit_, "BTCUSDT")
    st = paper._snapshot(runner)["state"]
    assert st["trade_count"] == 2 and st["last_fill_kind"] == "tp" and st["last_fill_return"] == 0.4
    loss = Fill(side="sell", price=99.0, qty=2.0, equity_after=998.0, return_pct=-0.2)
    paper._note_fill(runner, Fill(side="buy", price=100.0, qty=2.0, equity_after=998.0, return_pct=-0.2), "BTCUSDT")
    paper._note_fill(runner, loss, "BTCUSDT")
    assert paper._snapshot(runner)["state"]["last_fill_kind"] == "sl"
    assert runner.entry_returns == {}


def test_note_fill_judges_tp_sl_per_symbol_in_portfolio():
    # Fill.return_pct 는 세션 누적 수익률: A 진입 0.0 → B 진입 1.0 → A 청산 0.5.
    # 러너 전체 기준(마지막 진입 1.0)이면 A 는 잘못 "sl"; 종목별 기준(0.0)이면 "tp".
    runner = paper._Runner(9, _StatefulSim(), "BTCUSDT", "live", 1_000.0)
    paper._note_fill(runner, Fill(side="buy", price=100.0, qty=1.0, equity_after=1_000.0, return_pct=0.0), "BTCUSDT")
    paper._note_fill(runner, Fill(side="buy", price=10.0, qty=1.0, equity_after=1_010.0, return_pct=1.0), "ETHUSDT")
    assert runner.entry_returns == {"BTCUSDT": 0.0, "ETHUSDT": 1.0}
    paper._note_fill(runner, Fill(side="sell", price=100.5, qty=1.0, equity_after=1_005.0, return_pct=0.5), "BTCUSDT")
    st = paper._snapshot(runner)["state"]
    assert st["last_fill_kind"] == "tp" and st["last_fill_side"] == "sell" and st["trade_count"] == 3
    assert runner.entry_returns == {"ETHUSDT": 1.0}  # B 의 진입 기준은 그대로
    paper._note_fill(runner, Fill(side="sell", price=9.0, qty=1.0, equity_after=1_004.0, return_pct=0.4), "ETHUSDT")
    assert paper._snapshot(runner)["state"]["last_fill_kind"] == "sl"
    # 진입 기록이 없는 종목의 청산(재시작 후 등)은 "exit"
    paper._note_fill(runner, Fill(side="cover", price=1.0, qty=1.0, equity_after=1_004.0, return_pct=0.4), "SOLUSDT")
    assert paper._snapshot(runner)["state"]["last_fill_kind"] == "exit"


def test_snapshot_state_tolerates_sim_without_state():
    class _Bare:
        liquidations = 0
        liquidated_loss = 0.0
        def step(self, p, ts): return None
        def equity(self, p): return 1_000.0
    runner = paper._Runner(8, _Bare(), "ETHUSDT", "live", 1_000.0)
    st = paper._snapshot(runner)["state"]
    assert st["in_position"] is False and st["legs"][0]["qty"] == 0.0


def test_checkpoint_persists_state_json(monkeypatch):
    # test_paper_persistence.py 와 같은 방식: 임시 sqlite 엔진을 paper.get_session 에 붙인다.
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        row = PaperSession(
            macro_id="m1", symbol="BTCUSDT", mode="live", status="running",
            started_at="2026-09-21T00:00:00Z", virtual_balance=1_000, current_equity=1_000, current_return=0.0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        session_id = row.id

    @contextmanager
    def session_factory():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(paper, "get_session", session_factory)

    sim = _StatefulSim()
    runner = paper._Runner(session_id, sim, "BTCUSDT", "live", 1_000.0)
    sim.in_pos = True
    paper._note_fill(runner, Fill(side="buy", price=100.0, qty=2.0, equity_after=1_000.0, return_pct=0.0), "BTCUSDT")
    snapshot = paper._snapshot(runner)
    assert snapshot["state"]["in_position"] is True and snapshot["state"]["trade_count"] == 1

    assert paper._persist_checkpoint(snapshot, None) is None
    with Session(engine) as db:
        stored = db.get(PaperSession, session_id)
        assert stored.status == "running"
        assert stored.state_json == json.dumps(snapshot["state"])
        assert json.loads(stored.state_json)["legs"][0]["entry_price"] == 100.0

    sim.in_pos = False
    paper._note_fill(runner, Fill(side="sell", price=102.0, qty=2.0, equity_after=1_004.0, return_pct=0.4), "BTCUSDT")
    final = paper._snapshot(runner)
    assert final["state"] != snapshot["state"]
    paper._persist_finalize(final)
    with Session(engine) as db:
        stored = db.get(PaperSession, session_id)
        assert stored.status == "stopped"
        assert stored.state_json == json.dumps(final["state"])
        assert json.loads(stored.state_json)["trade_count"] == 2
        assert json.loads(stored.state_json)["last_fill_kind"] == "tp"


def test_checkpoint_default_is_10s():
    assert paper.CHECKPOINT_SECONDS == 10.0


def test_parse_state_handles_empty_and_invalid():
    assert paper.parse_state("") == {}
    assert paper.parse_state(None) == {}
    assert paper.parse_state("not json") == {}
    assert paper.parse_state("[1, 2]") == {}
    assert paper.parse_state('{"in_position": true}') == {"in_position": True}


def test_get_status_exposes_state_in_memory_and_from_db(monkeypatch):
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        row = PaperSession(
            macro_id="m2", symbol="ETHUSDT", mode="live", status="stopped",
            started_at="2026-09-21T00:00:00Z", virtual_balance=1_000, current_equity=1_000, current_return=0.0,
            state_json=json.dumps({"in_position": True, "trade_count": 3}),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        session_id = row.id

    @contextmanager
    def session_factory():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(paper, "get_session", session_factory)
    monkeypatch.setattr(paper, "_running", {})

    # DB branch
    status = paper.get_status(session_id)
    assert status["state"] == {"in_position": True, "trade_count": 3}

    # in-memory branch
    sim = _StatefulSim()
    sim.in_pos = True
    runner = paper._Runner(session_id, sim, "ETHUSDT", "live", 1_000.0)
    paper._running[session_id] = runner
    live = paper.get_status(session_id)
    assert live["state"]["in_position"] is True and live["state"]["legs"][0]["symbol"] == "ETHUSDT"


# ---------------------------------------------------------------------------
# Task 3: 리더보드 행 상태 파생 + 엔트리 뷰 새 키
# ---------------------------------------------------------------------------
from types import SimpleNamespace

from app import leaderboard


def test_derive_row_state_priority():
    d = leaderboard.derive_row_state
    assert d(None, {}) == "none"
    assert d("stopped", {"in_position": True}) == "stopped"
    assert d("running", {"halted_today": True, "in_position": True}) == "halted"
    assert d("running", {"in_position": True, "trade_count": 3}) == "holding"
    assert d("running", {"in_position": False, "trade_count": 2}) == "exited"
    assert d("running", {"in_position": False, "trade_count": 0}) == "waiting"
    assert d("running", {}) == "waiting"


def _row(**over):
    base = dict(id=1, user_id="u", nickname="n", username="user", owner_user_id=42, is_ai=False,
                symbol="BTCUSDT", macro_json="{}", human_summary="비밀 전략", paper_session_id=9,
                created_at="2026-09-21T00:00:00Z", created_ms=1, streak_days=1, first_created_ms=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_entry_view_exposes_state_even_when_locked():
    row = _row()
    status = {"current_return": 1.5, "current_equity": 1015.0, "status": "running", "mode": "live", "virtual_balance": 1000.0,
              "state": {"in_position": True, "halted_today": False, "cooldown_until_ms": 1_700_000_100_000, "trade_count": 1,
                        "last_fill_ms": 1_700_000_000_000, "last_fill_side": "buy", "last_fill_return": 1.8, "last_fill_kind": "",
                        "last_price": 101.0, "checkpoint_ms": 1_700_000_005_000,
                        "legs": [{"symbol": "BTCUSDT", "qty": 2.0, "dir": 1, "entry_price": 100.0, "last_price": 101.0, "in_position": True}]}}
    # viewer_user_id=7 은 row 의 owner(42)가 아니고 unlocked_ids 에도 없으므로 잠긴 뷰다.
    view = leaderboard._entry_view(row, {}, viewer_id="x", viewer_user_id=7, paper_status=status)
    assert view["locked"] is True and view["human_summary"] == "" and view["macro"] is None
    assert view["state"] == "holding" and view["trade_count"] == 1 and view["last_fill_kst"] is not None
    assert view["virtual_balance"] == 1000.0 and view["legs"][0]["qty"] == 2.0 and view["last_price"] == 101.0
    # 유료 파라미터로 역산 가능한 값은 잠긴 행에서 가려진다: 진입가·쿨다운·직전 체결 수익률.
    assert view["legs"][0]["entry_price"] == 0.0
    assert view["last_fill_return"] is None and view["cooldown_until_ms"] is None
    assert view["last_fill_kind"] == ""
    assert view["checkpoint_ms"] == 1_700_000_005_000
    assert view["return_pct"] == 1.5 and view["paper_status"] == "running"
    none_view = leaderboard._entry_view(row, {}, viewer_id="x", viewer_user_id=7, paper_status=None)
    assert none_view["state"] == "none" and none_view["legs"] == [] and none_view["trade_count"] == 0
    assert none_view["virtual_balance"] is None and none_view["last_fill_kst"] is None and none_view["last_fill_kind"] == ""

    # viewer_user_id=42 는 row 의 owner 이므로 잠기지 않은 뷰: 값이 그대로 노출된다.
    owner_view = leaderboard._entry_view(row, {}, viewer_id="x", viewer_user_id=42, paper_status=status)
    assert owner_view["locked"] is False
    assert owner_view["legs"][0]["entry_price"] == 100.0
    assert owner_view["last_fill_return"] == 1.8
    assert owner_view["cooldown_until_ms"] == 1_700_000_100_000


def test_entry_view_state_without_session_or_state_json():
    # paper_session_id 없음 → get_status 호출 없이 none
    view = leaderboard._entry_view(_row(paper_session_id=None), {}, viewer_id="u", viewer_user_id=42)
    assert view["state"] == "none" and view["legs"] == [] and view["trade_count"] == 0 and view["paper_status"] == "none"
    # 구 행: status 는 있으나 state 가 비어 있음 → running 이면 waiting, stopped 면 stopped
    running = {"current_return": 0.0, "current_equity": 1000.0, "status": "running", "mode": "live", "virtual_balance": 1000.0, "state": {}}
    assert leaderboard._entry_view(_row(), {}, viewer_id="u", paper_status=running)["state"] == "waiting"
    stopped = dict(running, status="stopped")
    assert leaderboard._entry_view(_row(), {}, viewer_id="u", paper_status=stopped)["state"] == "stopped"


def test_entry_view_falls_back_to_get_status(monkeypatch):
    calls = []

    def fake_get_status(session_id):
        calls.append(session_id)
        return {"current_return": -0.5, "current_equity": 995.0, "status": "running", "mode": "live", "virtual_balance": 1000.0,
                "state": {"in_position": False, "halted_today": True, "trade_count": 2, "legs": []}}

    monkeypatch.setattr(leaderboard.paper_mod, "get_status", fake_get_status)
    view = leaderboard._entry_view(_row(), {}, viewer_id="u")
    assert calls == [9]  # 상태 dict 는 한 번만 조회
    assert view["state"] == "halted" and view["trade_count"] == 2 and view["return_pct"] == -0.5


def test_durable_statuses_carry_state_and_virtual_balance():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        fresh = PaperSession(macro_id="m3", symbol="BTCUSDT", mode="live", status="running",
                             started_at="2026-09-21T00:00:00Z", virtual_balance=1_000, current_equity=1_010, current_return=1.0,
                             state_json=json.dumps({"in_position": True, "trade_count": 1, "legs": []}))
        legacy = PaperSession(macro_id="m4", symbol="ETHUSDT", mode="live", status="stopped",
                              started_at="2026-09-21T00:00:00Z", virtual_balance=500, current_equity=500, current_return=0.0)
        db.add(fresh)
        db.add(legacy)
        db.commit()
        db.refresh(fresh)
        db.refresh(legacy)
        statuses = leaderboard._durable_statuses(db, [fresh.id, legacy.id, 999_999])
    assert set(statuses) == {fresh.id, legacy.id}
    assert statuses[fresh.id]["virtual_balance"] == 1_000 and statuses[fresh.id]["state"]["trade_count"] == 1
    assert statuses[fresh.id]["current_return"] == 1.0 and statuses[fresh.id]["status"] == "running"
    assert statuses[legacy.id]["state"] == {} and statuses[legacy.id]["virtual_balance"] == 500


def test_prices_endpoint_validates_and_skips_failures(monkeypatch):
    calls = {"n": 0}

    def fake_fetch_all_prices():
        calls["n"] += 1
        return {"BTCUSDT": 100.5}

    monkeypatch.setattr(marketdata, "fetch_all_prices", fake_fetch_all_prices)
    marketdata._all_prices_cache.clear()
    r = client.get("/api/prices?symbols=btcusdt,ETHUSDT")
    assert r.status_code == 200 and r.json()["prices"] == {"BTCUSDT": 100.5} and isinstance(r.json()["ms"], int)
    client.get("/api/prices?symbols=BTCUSDT")
    assert calls["n"] == 1  # 2초 캐시 — 두 번째 요청은 상류를 다시 부르지 않는다
    assert client.get("/api/prices?symbols=BTC-KRW").status_code == 422
    assert client.get("/api/prices?symbols=").status_code == 422
    r_missing = client.get("/api/prices")
    assert r_missing.status_code == 422 and r_missing.json()["detail"] == "종목 형식이 잘못됐어요."
    assert client.get("/api/prices?symbols=" + ",".join(f"S{i}USDT" for i in range(31))).status_code == 422


# --- 재배포 후 세션 복구 ---------------------------------------------------
# Render 재배포는 프로세스를 강제로 끊어 종료 훅이 안 돌 수 있고, 기동 시 running 세션을 되살리는
# 코드가 없어 리더보드 수익률이 마지막 체크포인트에 박제됐다(2026-09-21 프로덕션 8행 전부).
from app.db import PaperTrade


def test_position_sim_restore_rebuilds_a_held_position_and_flat_cash():
    sim = make_sim(_macro("A", market="futures", leverage=2, risk={"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 30, "max_holding_hours": 0}), 1_000.0)
    sim.restore(1_050.0, in_position=True, qty=20.0, entry_price=100.0, last_price=102.5, cooldown_until_ms=None)
    st = sim.state()
    assert st["in_position"] is True and st["qty"] == 20.0 and st["entry_price"] == 100.0
    assert abs(sim.equity(102.5) - 1_050.0) < 1e-6  # 복구 직후 자산이 체크포인트 값과 같다
    assert sim.liq_price is not None  # 레버리지면 청산가도 다시 세운다
    sim.step(101.0)
    assert abs(sim.equity(101.0) - 1_020.0) < 1e-6  # 이후 가격 변화가 그대로 반영

    flat = make_sim(_macro("A"), 1_000.0)
    flat.restore(1_339.87, in_position=False, qty=0.0, entry_price=0.0, last_price=0.0, cooldown_until_ms=1_700_000_000_000)
    assert flat.state()["in_position"] is False and flat.cash == 1_339.87
    assert flat.state()["cooldown_until_ms"] == 1_700_000_000_000


def test_dca_sim_restore_rebuilds_accumulated_position():
    sim = make_sim(_macro("C"), 1_000.0)
    sim.restore(1_100.0, in_position=True, qty=5.0, entry_price=100.0, last_price=120.0)
    assert sim.state()["in_position"] is True and abs(sim.state()["entry_price"] - 100.0) < 1e-9
    assert abs(sim.equity(120.0) - 1_100.0) < 1e-6


def _resume_db(rows):
    # resume 는 워커 스레드에서 읽으므로 스레드 간에 같은 메모리 DB 를 공유해야 한다.
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", echo=False, poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    ids = []
    with Session(engine) as db:
        for row, trades in rows:
            db.add(row)
            db.commit()
            db.refresh(row)
            ids.append(row.id)
            for t in trades:
                db.add(PaperTrade(session_id=row.id, **t))
            db.commit()

    @contextmanager
    def session_factory():
        with Session(engine) as db:
            yield db

    return engine, session_factory, ids


def test_resume_running_sessions_revives_zombies_and_restores_state(monkeypatch):
    macro_json = _macro("A").model_dump_json()
    zombie = PaperSession(macro_id="m", symbol="ONEUSDT", mode="live", status="running", started_at="2026-09-17T00:00:00Z",
                          virtual_balance=1_000.0, current_equity=1_339.87, current_return=33.987, macro_json=macro_json, state_json="")
    held_state = {"in_position": True, "halted_today": False, "cooldown_until_ms": None, "trade_count": 3,
                  "last_fill_ms": 1, "last_fill_side": "buy", "last_fill_return": 0.5, "last_fill_kind": "", "last_price": 102.0,
                  "checkpoint_ms": 1, "legs": [{"symbol": "BTCUSDT", "qty": 10.0, "dir": 1, "entry_price": 100.0, "last_price": 102.0, "in_position": True}]}
    held = PaperSession(macro_id="m", symbol="BTCUSDT", mode="live", status="running", started_at="2026-09-21T00:00:00Z",
                        virtual_balance=1_000.0, current_equity=1_020.0, current_return=2.0, macro_json=macro_json, state_json=json.dumps(held_state))
    replay = PaperSession(macro_id="m", symbol="ETHUSDT", mode="replay", status="running", started_at="2026-09-21T00:00:00Z",
                          virtual_balance=1_000.0, current_equity=990.0, current_return=-1.0, macro_json=macro_json)
    stopped = PaperSession(macro_id="m", symbol="SOLUSDT", mode="live", status="stopped", started_at="2026-09-21T00:00:00Z",
                           virtual_balance=1_000.0, current_equity=1_000.0, current_return=0.0, macro_json=macro_json)
    trades = [
        {"ts": "2026-09-19T00:00:00Z", "symbol": "ONEUSDT", "side": "buy", "price": 1.0, "qty": 1000.0, "return_at_trade": 0.0},
        {"ts": "2026-09-19T01:00:00Z", "symbol": "ONEUSDT", "side": "sell", "price": 1.1, "qty": 1000.0, "return_at_trade": 10.0},
    ]
    held_trades = [
        {"ts": "2026-09-21T00:00:00Z", "symbol": "BTCUSDT", "side": "buy", "price": 90.0, "qty": 10.0, "return_at_trade": 0.0},
        {"ts": "2026-09-21T01:00:00Z", "symbol": "BTCUSDT", "side": "sell", "price": 95.0, "qty": 10.0, "return_at_trade": 0.5},
        {"ts": "2026-09-21T02:00:00Z", "symbol": "BTCUSDT", "side": "buy", "price": 100.0, "qty": 10.0, "return_at_trade": 0.5},
    ]
    engine, factory, ids = _resume_db([(zombie, trades), (held, held_trades), (replay, []), (stopped, [])])
    monkeypatch.setattr(paper, "get_session", factory)
    started = []
    monkeypatch.setattr(paper, "_spawn_loop", lambda runner: started.append(runner.session_id))  # 루프는 띄우지 않는다
    paper._running.clear()
    try:
        revived = _run(paper.resume_running_sessions())
        assert revived == 2 and len(started) == 2
        z = paper._running[ids[0]]
        assert z.equity == 1_339.87 and abs(z.ret - 33.987) < 1e-3 and z.sim.state()["in_position"] is False
        assert z.trade_count == 2 and z.last_fill["side"] == "sell" and z.last_fill["kind"] == "tp"
        h = paper._running[ids[1]]
        assert h.sim.state()["in_position"] is True and h.sim.state()["qty"] == 10.0 and h.sim.state()["entry_price"] == 100.0
        assert h.trade_count == 3 and h.last_fill["side"] == "buy" and h.entry_returns == {"BTCUSDT": 0.5}
        assert paper._snapshot(h)["state"]["in_position"] is True
        with Session(engine) as db:
            assert db.get(PaperSession, ids[2]).status == "stopped"  # 리플레이는 되살리지 않고 닫는다
            assert db.get(PaperSession, ids[3]).status == "stopped"
        assert ids[2] not in paper._running and ids[3] not in paper._running
    finally:
        paper._running.clear()


def _run(coro):
    """test_paper_persistence 와 같은 방식 — 실행기 종료 대기 문제를 피해 새 루프에서 돌린다."""
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        executor = getattr(loop, "_default_executor", None)
        loop._default_executor = None
        if executor is not None:
            executor.shutdown(wait=True)
        loop.close()
