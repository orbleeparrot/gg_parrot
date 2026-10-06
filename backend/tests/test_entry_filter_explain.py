"""필터가 걸린 매크로의 설명에 필터가 보인다 — 없으면 '왜 안 샀는지' 를 설명에서 못 찾는다."""
import pytest

from app.engine.backtest import BacktestResult
from app.engine.explain import explain_result
from app.engine.schema import Macro

BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}
RESULT = BacktestResult(final_return_pct=1.0, win_rate_pct=50.0, mdd_pct=5.0, total_trades=4,
                        initial_capital=1000.0, final_equity=1010.0, equity_curve=[])

# 종류마다 필터 설정과, 설명에 그대로 나와야 하는 한국어 조건 (FilterEval.note() 의 문구)
KINDS = [
    ({"kind": "ma", "params": {"period": 20, "side": "above"}}, "20봉 SMA 이동평균 위"),
    ({"kind": "rsi", "params": {"period": 14, "max": 70}}, "RSI(14) 70 이하"),
    ({"kind": "bb", "params": {"period": 20, "zone": "inside"}}, "볼린저(20, 2σ) 밴드 안"),
    ({"kind": "volume", "params": {"period": 20, "multiple": 2.0}}, "거래량이 20봉 평균의 2배 이상"),
]


def _points(entry_filter=None):
    body = BODY if entry_filter is None else {**BODY, "entry_filter": entry_filter}
    return explain_result(Macro(**body), RESULT).points


def test_filter_appears_in_the_points():
    text = " ".join(_points({"kind": "ma", "params": {"period": 20, "side": "above"}}))
    assert "20" in text and "이동평균" in text


@pytest.mark.parametrize("spec,note", KINDS)
def test_each_kind_reads_its_own_condition(spec, note):
    last = _points(spec)[-1]
    assert note in last
    assert last.startswith("진입 조건을 하나 더 걸었어")


def test_kinds_are_told_apart():
    lasts = [_points(spec)[-1] for spec, _ in KINDS]
    assert len(set(lasts)) == len(KINDS)


def test_no_filter_adds_no_line():
    bare = _points()
    filtered = _points({"kind": "volume", "params": {"period": 20, "multiple": 2.0}})
    assert len(filtered) == len(bare) + 1
    assert filtered[:-1] == bare            # 앞 문구의 순서와 내용은 그대로
    assert not any("진입 조건" in p for p in bare)
