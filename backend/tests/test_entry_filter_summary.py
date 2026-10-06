"""한 줄 요약에 필터가 보인다 — 리더보드·카드·실행 독이 이 문장을 쓴다."""
import pytest

from app.engine.schema import Macro
from app.engine.summary import human_summary

BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}

# 종류마다 요약 맨 끝에 정확히 이 문구가 와야 한다 (FilterEval.note() 의 문구)
KINDS = [
    ({"kind": "ma", "params": {"period": 20, "side": "above"}}, "진입 조건: 20봉 SMA 이동평균 위"),
    ({"kind": "ma", "params": {"period": 20, "side": "below"}}, "진입 조건: 20봉 SMA 이동평균 아래"),
    ({"kind": "rsi", "params": {"period": 14, "max": 70}}, "진입 조건: RSI(14) 70 이하"),
    ({"kind": "bb", "params": {"period": 20, "zone": "inside"}}, "진입 조건: 볼린저(20, 2σ) 밴드 안"),
    ({"kind": "volume", "params": {"period": 20, "multiple": 2.0}}, "진입 조건: 거래량이 20봉 평균의 2배 이상"),
]


def _summary(entry_filter=None, **extra):
    body = {**BODY, **extra}
    if entry_filter is not None:
        body["entry_filter"] = entry_filter
    return human_summary(Macro(**body))


def test_filter_is_appended_to_the_summary():
    text = _summary({"kind": "ma", "params": {"period": 20, "side": "above"}})
    assert "20봉" in text and "이동평균" in text and "위" in text


def test_summary_without_filter_is_unchanged():
    assert _summary() == human_summary(Macro(**{**BODY, "entry_filter": None}))
    assert "진입 조건" not in _summary()


def test_filter_comes_after_the_capital_part():
    parts = _summary({"kind": "volume", "params": {"period": 20, "multiple": 2.0}}).split(" · ")
    assert "투입" in parts[-2] and "거래량" in parts[-1]


@pytest.mark.parametrize("spec,tail", KINDS)
def test_every_filter_kind_reads_as_its_own_condition(spec, tail):
    assert _summary(spec).split(" · ")[-1] == tail


def test_filter_only_adds_one_trailing_part():
    bare = _summary().split(" · ")
    filtered = _summary({"kind": "rsi", "params": {"period": 14, "max": 70}}).split(" · ")
    assert filtered[:-1] == bare and len(filtered) == len(bare) + 1


def test_filter_comes_after_leverage():
    parts = _summary({"kind": "rsi", "params": {"period": 14, "max": 70}},
                     leverage=3, margin_mode="isolated").split(" · ")
    assert "레버리지" in parts[-2] and parts[-1].startswith("진입 조건:")
