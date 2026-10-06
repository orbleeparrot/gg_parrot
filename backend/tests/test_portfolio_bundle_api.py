"""묶음 백테스트 경로 — 비중대로 자금이 갈라지고, 한도가 있으면 동기 루프를 쓴다."""
import pandas as pd
import pytest

from app.engine.schema import Macro

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}
RISING = [100, 101, 102, 103, 104, 105, 106, 107]


def frame(closes):
    ts = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return pd.DataFrame({"timestamp": ts, "open": closes,
                         "high": [c * 1.03 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)})


@pytest.fixture
def patched(monkeypatch):
    """캔들 조회를 막고 같은 프레임을 돌려준다 — 이 시험은 분배와 경로 선택만 본다."""
    import app.main as main

    calls = []

    def fake_fetch(macro, start_ms, end_ms):
        calls.append(macro)
        return frame(RISING), "test"

    monkeypatch.setattr(main, "fetch_klines_for_macro", fake_fetch)
    return main, calls


# --- Review Focus 1: 비중이 균등이 아닐 때 자금이 비중대로 갈라진다 ----
def test_uneven_weights_split_capital_on_the_plain_path(patched):
    """한도가 없는(기존) 경로에도 비중이 들어야 한다 — 균등 분배 코드가 남으면 여기서 걸린다.

    레그에 실제로 넘어간 자금을 직접 본다. 최종 자산의 비로 보면 수수료 · 반올림에 기대는
    약한 시험이 된다.
    """
    main, calls = patched
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                  {"symbol": "ETHUSDT", "weight": 30}]})
    main._run_portfolio_backtest(m, 0, 1)
    caps = {c.symbol: c.params["initial_capital"] for c in calls}
    assert caps == {"BTCUSDT": 700.0, "ETHUSDT": 300.0}


def test_even_symbols_form_unchanged(patched):
    """symbols 형태(옛 모양)는 균등 분배 그대로."""
    main, calls = patched
    m = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    main._run_portfolio_backtest(m, 0, 1)
    caps = {c.symbol: c.params["initial_capital"] for c in calls}
    assert caps == {"BTCUSDT": 500.0, "ETHUSDT": 500.0}


def test_bundle_risk_takes_the_lockstep_path(patched, monkeypatch):
    main, _ = patched
    seen = {}
    real = main.portfolio_backtest_mod.run_bundle

    def spy(macro, frames):
        seen["called"] = True
        return real(macro, frames)

    monkeypatch.setattr(main.portfolio_backtest_mod, "run_bundle", spy)
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    main._run_portfolio_backtest(m, 0, 1)
    assert seen.get("called") is True


def test_no_bundle_risk_does_not_take_the_lockstep_path(patched, monkeypatch):
    """한도가 없으면 기존 경로 그대로 — 기존 매크로의 결과를 건드리지 않는다."""
    main, _ = patched
    seen = {}
    monkeypatch.setattr(main.portfolio_backtest_mod, "run_bundle",
                        lambda *a, **k: seen.setdefault("called", True) or [])
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}]})
    main._run_portfolio_backtest(m, 0, 1)
    assert "called" not in seen


def test_each_leg_is_fetched_with_its_own_rule(patched):
    main, calls = patched
    m = Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]})
    main._run_portfolio_backtest(m, 0, 1)
    got = {c.symbol: c.rule_type.value for c in calls}
    assert got == {"BTCUSDT": "E", "ETHUSDT": "I"}
