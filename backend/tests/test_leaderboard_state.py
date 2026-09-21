"""리더보드 실시간 상태 — 시뮬레이터 state(), 체크포인트 state_json, 행 상태 파생, /api/prices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine import Macro
from app.engine.stepper import DcaSim, PositionSim, make_sim


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
