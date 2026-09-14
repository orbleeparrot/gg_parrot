"""Multi-symbol (portfolio) paper sessions run every symbol, not just the first.

Feedback: "종목 여러 개 추가해도 한 종목만 도는 건가요?" — the backtest already
splits a portfolio macro into per-symbol legs; paper must do the same and show
fills from every coin in one log with the total on top.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import paper
from app.db import PaperSession, PaperTrade
from app.engine import Macro


class _FakeSim:
    """Fills once on the first tick; equity grows with the tick price."""

    def __init__(self, fill=None, initial=500.0):
        self.fill = fill
        self.initial = initial
        self.ticks = 0
        self.liquidations = 0
        self.liquidated_loss = 0.0

    def step(self, price, ts):
        self.ticks += 1
        return self.fill if self.ticks == 1 else None

    def equity(self, price):
        return self.initial + price


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        executor = getattr(loop, "_default_executor", None)
        loop._default_executor = None
        if executor is not None:
            executor.shutdown(wait=True)
        loop.close()


def _portfolio_runner(session_id=7):
    btc = paper._Leg("BTCUSDT", _FakeSim(SimpleNamespace(side="buy", price=100.0, qty=1.0, return_pct=0.0)), 500.0)
    eth = paper._Leg("ETHUSDT", _FakeSim(SimpleNamespace(side="buy", price=10.0, qty=5.0, return_pct=0.0)), 500.0)
    return paper._Runner(session_id, btc.sim, btc.symbol, "live", 1_000.0, legs=[btc, eth])


def test_portfolio_macro_starts_a_leg_per_symbol(monkeypatch):
    macro = Macro(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], rule_type="A", params={"take_profit_pct": 1.0, "initial_capital": 3_000.0})
    seen = []
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: seen.append(s))
    monkeypatch.setattr(paper, "make_sim", lambda m, initial_capital=None: _FakeSim(initial=initial_capital))
    monkeypatch.setattr(paper, "_create_session", lambda *a: 42)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    try:
        info = _run(paper.start_session(macro, None, "live"))
        runner = paper._running[42]
    finally:
        paper._running.pop(42, None)

    assert seen == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert info["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert info["symbol"] == "BTCUSDT"
    assert runner.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # Capital is split evenly like the backtest, total unchanged.
    assert [leg.initial for leg in runner.legs] == [1_000.0, 1_000.0, 1_000.0]
    assert runner.initial == 3_000.0


def test_single_symbol_macro_keeps_one_leg(monkeypatch):
    macro = Macro(symbol="BTCUSDT", rule_type="A", params={"take_profit_pct": 1.0, "initial_capital": 1_000.0})
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: None)
    monkeypatch.setattr(paper, "make_sim", lambda m, initial_capital=None: _FakeSim(initial=initial_capital))
    monkeypatch.setattr(paper, "_create_session", lambda *a: 43)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    try:
        info = _run(paper.start_session(macro, "ethusdt", "live"))
        runner = paper._running[43]
    finally:
        paper._running.pop(43, None)

    assert info["symbols"] == ["ETHUSDT"]
    assert not runner.is_portfolio()
    assert runner.legs[0].initial == 1_000.0


def test_every_leg_ticks_and_totals_are_summed():
    runner = _portfolio_runner()

    btc_fill = paper._tick(runner, 100.0, symbol="BTCUSDT")
    eth_fill = paper._tick(runner, 10.0, symbol="ETHUSDT")

    assert btc_fill.side == "buy" and eth_fill.side == "buy"
    assert runner.legs[0].equity == 600.0
    assert runner.legs[1].equity == 510.0
    assert runner.equity == 1_110.0
    assert runner.ret == 11.0
    # The headline price is the primary symbol's; each leg keeps its own.
    assert runner.last_price == 100.0
    assert runner.legs[1].last_price == 10.0


def test_round_persists_one_fill_per_leg_tagged_with_its_symbol(monkeypatch):
    persisted = []

    def persist(snapshot, fill):
        persisted.append((snapshot, fill))
        return {"id": len(persisted), **fill} if fill else None

    monkeypatch.setattr(paper, "_persist_checkpoint", persist)
    runner = _portfolio_runner()

    _run(paper._tick_and_checkpoint(runner, [100.0, 10.0], None))

    fills = [f for _, f in persisted if f]
    assert [f["symbol"] for f in fills] == ["BTCUSDT", "ETHUSDT"]
    assert [f["qty"] for f in fills] == [1.0, 5.0]
    # Both fills are in the live log, newest first.
    assert [t["symbol"] for t in runner.recent] == ["ETHUSDT", "BTCUSDT"]
    # The snapshot carries the per-symbol breakdown for the UI.
    legs = persisted[-1][0]["legs"]
    assert [leg["symbol"] for leg in legs] == ["BTCUSDT", "ETHUSDT"]
    assert legs[0]["current_equity"] == 600.0


def test_missing_price_skips_that_leg_only():
    runner = _portfolio_runner()

    async def exercise():
        await paper._tick_and_checkpoint(runner, [None, 10.0], None)

    _run(exercise())
    assert runner.legs[0].sim.ticks == 0
    assert runner.legs[1].sim.ticks == 1


def test_stopped_portfolio_session_keeps_legs_and_trade_symbols(monkeypatch):
    # Checkpoints run on worker threads: one shared in-memory DB across threads.
    engine = create_engine(
        "sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def session_factory():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(paper, "get_session", session_factory)
    with Session(engine) as db:
        db.add(PaperSession(id=7, macro_id="adhoc", symbol="BTCUSDT", status="running", started_at="t", virtual_balance=1_000.0))
        db.commit()

    runner = _portfolio_runner()
    _run(paper._tick_and_checkpoint(runner, [100.0, 10.0], None))
    _run(paper._finalize_async(runner))

    status = paper.get_status(7)
    assert status["status"] == "stopped"
    assert status["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert [leg["current_equity"] for leg in status["legs"]] == [600.0, 510.0]
    assert status["current_equity"] == 1_110.0
    assert sorted(t["symbol"] for t in status["trades"]) == ["BTCUSDT", "ETHUSDT"]
    with Session(engine) as db:
        rows = db.exec(select(PaperTrade).where(PaperTrade.session_id == 7)).all()
    assert {r.symbol for r in rows} == {"BTCUSDT", "ETHUSDT"}
