"""모의 · 실시간의 묶음 — 비중 분배, 레그가 공유하는 관문, 복구 뒤에도 듣는 한도."""
import asyncio

import pytest

from app import paper
from app.engine.bundle import BundleGate
from app.engine.schema import BundleRisk, Macro
from app.engine.stepper import make_sim

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def bundle(**over):
    return Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                     {"symbol": "ETHUSDT", "weight": 30}], **over})


def test_build_legs_splits_by_weight():
    from app.paper import build_bundle_legs

    m = bundle()
    legs, gate = build_bundle_legs(m, 1000.0)
    caps = {leg.symbol: leg.initial for leg in legs}
    assert caps == {"BTCUSDT": 700.0, "ETHUSDT": 300.0}
    assert gate is None                     # 한도가 없으면 관문도 없다


def test_build_legs_even_for_symbols_form():
    from app.paper import build_bundle_legs

    m = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    legs, _gate = build_bundle_legs(m, 1000.0)
    assert {leg.initial for leg in legs} == {500.0}


def test_build_legs_attaches_one_shared_gate():
    from app.paper import build_bundle_legs

    m = bundle(bundle_risk={"max_positions": 1})
    legs, gate = build_bundle_legs(m, 1000.0)
    assert gate is not None
    inners = [getattr(leg.sim, "inner", leg.sim) for leg in legs]
    assert all(s.bundle_gate is gate for s in inners), "레그마다 다른 관문을 꽂았다"


def test_build_legs_uses_each_leg_rule():
    from app.paper import build_bundle_legs

    m = Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]})
    legs, _gate = build_bundle_legs(m, 1000.0)
    kinds = {leg.symbol: type(getattr(leg.sim, "inner", leg.sim)).__name__ for leg in legs}
    assert kinds["BTCUSDT"] != kinds["ETHUSDT"], "레그별 규칙이 반영되지 않았다"


# --- Review Focus 4: 재기동 복구 뒤에도 한도가 듣는다 ------------------
def test_gate_reads_restored_books():
    """1차에서 복구 경로를 빠뜨려 차단 결함을 냈다. 관문은 상태를 따로 들지 않으므로
    복구된 장부를 그 즉시 올바르게 읽어야 한다 — 그것을 여기서 못 박는다."""
    from app.paper import build_bundle_legs

    m = bundle(bundle_risk={"max_positions": 1})
    legs, gate = build_bundle_legs(m, 1000.0)
    btc, eth = legs[0], legs[1]
    assert gate.blocks(eth.sim) is False, "아무도 안 들고 있는데 막았다"

    # 재기동: BTC 레그가 포지션을 들고 있던 상태로 되살아난다.
    btc.sim.restore(700.0, in_position=True, qty=5.0, entry_price=100.0, last_price=100.0)
    assert gate.blocks(eth.sim) is True, "복구된 장부를 관문이 못 읽었다"
    assert gate.blocks(btc.sim) is False, "들고 있는 레그의 추가 매수를 막았다"


# --- 세션 조립: 시작 길과 재기동 복구 길이 같은 레그를 만든다 -------------
def test_start_session_checks_spot_before_creating_session(monkeypatch):
    """데이터 없는 종목으로 durable 세션을 만들지 않는다 — 순서 보존."""
    order = []
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s, **kw: order.append(("spot", s)))
    monkeypatch.setattr(paper, "_create_session",
                        lambda macro, sym, mode, initial: order.append(("create", sym)) or 9)
    monkeypatch.setattr(paper, "_spawn_loop", lambda runner: None)

    async def _no_feed(runner):
        return None
    monkeypatch.setattr(paper, "_attach_feed", _no_feed)
    paper._running.clear()

    m = bundle(bundle_risk={"max_positions": 1})
    out = asyncio.run(paper.start_session(m, None, "live"))
    try:
        assert out["symbols"] == ["BTCUSDT", "ETHUSDT"]
        assert order.index(("create", "BTCUSDT")) > max(i for i, e in enumerate(order) if e[0] == "spot")
        runner = paper._running[9]
        caps = {leg.symbol: leg.initial for leg in runner.legs}
        assert sum(caps.values()) == pytest.approx(out["virtual_balance"])
        assert caps["BTCUSDT"] == pytest.approx(out["virtual_balance"] * 0.7)
        inners = [getattr(leg.sim, "inner", leg.sim) for leg in runner.legs]
        assert inners[0].bundle_gate is inners[1].bundle_gate is not None
    finally:
        paper._running.pop(9, None)


def test_rebuild_runner_keeps_weights_and_gate():
    """재기동 복구(_rebuild_runner)도 같은 길을 타야 한다 — 아니면 되살린 세션이 균등 분배에 한도 없이 돈다."""
    m = bundle(bundle_risk={"max_positions": 1})
    info = {
        "id": 21, "symbol": "BTCUSDT", "macro_json": m.model_dump_json(),
        "virtual_balance": 1000.0, "current_equity": 1000.0, "legs": [], "state": {}, "trades": [],
    }
    runner = paper._rebuild_runner(info)
    caps = {leg.symbol: leg.initial for leg in runner.legs}
    assert caps == {"BTCUSDT": 700.0, "ETHUSDT": 300.0}
    inners = [getattr(leg.sim, "inner", leg.sim) for leg in runner.legs]
    assert inners[0].bundle_gate is not None and inners[0].bundle_gate is inners[1].bundle_gate


# --- 단일 종목 경로는 전과 같다 ------------------------------------------
def test_single_symbol_session_gets_full_capital(monkeypatch):
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s, **kw: None)
    monkeypatch.setattr(paper, "_create_session", lambda macro, sym, mode, initial: 31)
    monkeypatch.setattr(paper, "_spawn_loop", lambda runner: None)

    async def _no_feed(runner):
        return None
    monkeypatch.setattr(paper, "_attach_feed", _no_feed)
    paper._running.clear()

    m = Macro(**{**BASE, "symbol": "BTCUSDT"})
    out = asyncio.run(paper.start_session(m, "ethusdt", "live"))
    try:
        runner = paper._running[31]
        assert out["symbols"] == ["ETHUSDT"]          # 호출자가 고른 종목을 따른다
        assert len(runner.legs) == 1
        assert runner.legs[0].symbol == "ETHUSDT"
        assert runner.legs[0].initial == out["virtual_balance"], "단일 종목이 자금 전액을 못 받았다"
        assert getattr(getattr(runner.legs[0].sim, "inner", runner.legs[0].sim), "bundle_gate", None) is None
    finally:
        paper._running.pop(31, None)
