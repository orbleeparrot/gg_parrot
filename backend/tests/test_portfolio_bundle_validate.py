"""묶음 검증 — 창을 레그마다 같은 시각으로 자르고, 한도가 있으면 창마다 동기 루프를 돈다."""
import pandas as pd
import pytest

from app.engine import walkforward as wf
from app.engine.schema import Macro

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def frame(closes, start="2026-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1h")
    return pd.DataFrame({"timestamp": ts, "open": closes,
                         "high": [c * 1.03 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)})


LONG = list(range(100, 116))


def test_bundle_windows_returns_one_row_per_window():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    rows = wf.run_bundle_windows(m, frames, 4)
    assert [r["index"] for r in rows] == [1, 2, 3, 4]
    assert all(r["start"] and r["end"] for r in rows)
    assert all(r["error"] == "" for r in rows)


def test_bundle_windows_return_is_the_whole_bundle_not_one_leg():
    """창 수익률은 묶음 합산이어야 한다 — 한 레그만 보면 한도의 효과가 안 보인다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    capped = wf.run_bundle_windows(m, frames, 2)
    free = wf.run_bundle_windows(
        m.model_copy(update={"bundle_risk": m.bundle_risk.model_copy(
            update={"max_positions": None, "max_exposure_pct": 100.0})}), frames, 2)
    assert [r["return_pct"] for r in capped] != [r["return_pct"] for r in free], \
        "한도가 창 수익률을 바꾸지 않았다 — 합산이 아니라 한 레그만 보고 있을 수 있다"


def test_bundle_window_failure_keeps_its_slot():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame([])}
    rows = wf.run_bundle_windows(m, frames, 3)
    assert len(rows) == 3
    assert all("return_pct" in r for r in rows)


def test_bundle_windows_too_few_rows_is_empty():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    assert wf.run_bundle_windows(m, {"BTCUSDT": frame([100]), "ETHUSDT": frame([100])}, 4) == []


def test_bundle_windows_cut_by_time_when_legs_start_at_different_moments():
    """레그마다 시작 시각이 다르면 행 수 기준 경계는 어긋난다 — 창은 합친 시간선에서 잘려야 한다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    btc = frame(LONG, start="2026-01-01 00:00")
    eth = frame(LONG, start="2026-01-01 08:00")  # 8 시간 늦게 시작해 8 시간 늦게 끝난다
    rows = wf.run_bundle_windows(m, {"BTCUSDT": btc, "ETHUSDT": eth}, 2)
    assert len(rows) == 2
    assert rows[0]["start"] == wf._label(btc["timestamp"].min())
    assert rows[-1]["end"] == wf._label(eth["timestamp"].max()),         "마지막 창이 늦게 끝나는 레그의 끝까지 가지 않았다 — 한 레그의 행 수로 잘랐을 수 있다"


# --- 끝점이 더 이상 거절하지 않는다 ------------------------------------
def test_validate_endpoint_no_longer_refuses_portfolio():
    import app.main as main
    src = __import__("inspect").getsource(main)
    assert "여러 종목 포트폴리오 매크로는 아직 검증할 수 없습니다" not in src
