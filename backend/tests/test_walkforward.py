"""워크포워드는 캔들을 한 번만 받아 DataFrame 을 잘라 돌린다 — 네트워크 재호출 없음."""
import pandas as pd
import pytest

pytest.importorskip("pandas")

from app.engine import walkforward
from app.engine.schema import Macro


def frame(rows: int) -> pd.DataFrame:
    """운영 캔들과 같은 모양 — 엔진은 timestamp 열을, 구간 표기는 그 열을 읽는다."""
    stamps = pd.date_range("2026-01-01", periods=rows, freq="1D", tz="UTC")
    price = [100.0 + i for i in range(rows)]
    return pd.DataFrame({"timestamp": stamps, "open": price, "high": [p * 1.01 for p in price],
                         "low": [p * 0.99 for p in price], "close": price,
                         "volume": [10.0] * rows})


def macro() -> Macro:
    return Macro(symbol="BTCUSDT", rule_type="A", candle_interval="1d",
                 params={"take_profit_pct": 5, "initial_capital": 1_000_000})


def test_split_frame_divides_rows_evenly_and_keeps_every_row():
    parts = walkforward.split_frame(frame(100), 4)
    assert len(parts) == 4
    assert sum(len(part) for part in parts) == 100


def test_split_frame_puts_the_remainder_in_the_last_slice():
    """나머지 행은 버리지 않고 마지막 구간이 받는다 — 마지막 구간이 기간 끝까지 닿는다."""
    source = frame(103)
    parts = walkforward.split_frame(source, 4)
    assert [len(part) for part in parts] == [25, 25, 25, 28]
    rejoined = pd.concat(parts)
    assert rejoined["timestamp"].tolist() == source["timestamp"].tolist()
    assert parts[-1]["timestamp"].iloc[-1] == source["timestamp"].iloc[-1]


def test_split_frame_refuses_more_windows_than_usable_rows():
    parts = walkforward.split_frame(frame(6), 4)
    assert len(parts) <= 3, "구간마다 최소 두 행은 있어야 수익률을 낸다"
    assert all(len(part) >= 2 for part in parts)


def test_split_frame_never_emits_an_empty_slice():
    assert walkforward.split_frame(frame(1), 4) == []
    assert walkforward.split_frame(frame(0), 4) == []
    assert len(walkforward.split_frame(frame(3), 0)) == 1


def test_split_frame_cuts_in_time_order_even_if_rows_arrive_shuffled():
    shuffled = frame(40).sample(frac=1.0, random_state=7)
    parts = walkforward.split_frame(shuffled, 4)
    firsts = [part["timestamp"].iloc[0] for part in parts]
    assert firsts == sorted(firsts)
    assert all(part["timestamp"].is_monotonic_increasing for part in parts)


def test_run_windows_reports_one_row_per_window_in_order():
    rows = walkforward.run_windows(macro(), frame(120), windows=4)
    assert [row["index"] for row in rows] == [1, 2, 3, 4]
    assert all(row["start"] < row["end"] for row in rows)
    assert all(isinstance(row["return_pct"], float) and row["error"] == "" for row in rows)


def test_run_windows_labels_windows_with_iso_times_ending_at_the_period_end():
    source = frame(103)
    rows = walkforward.run_windows(macro(), source, windows=4)
    assert rows[0]["start"] == "2026-01-01T00:00:00+00:00"
    assert rows[-1]["end"] == source["timestamp"].iloc[-1].isoformat()


def test_run_windows_calls_the_engine_once_per_window_on_slices_of_the_given_frame(monkeypatch):
    """캔들은 한 번 받은 것을 자를 뿐 — 구간 수만큼 엔진만 불리고 입력 프레임은 그대로다."""
    seen = []

    def spy(macro_arg, df):
        seen.append(len(df))
        return real(macro_arg, df)

    real = walkforward.run_backtest
    monkeypatch.setattr(walkforward, "run_backtest", spy)
    source = frame(120)
    before = source.copy()
    walkforward.run_windows(macro(), source, windows=4)
    assert seen == [30, 30, 30, 30]
    pd.testing.assert_frame_equal(source, before)


def test_a_window_without_candles_fails_alone(monkeypatch):
    """상장 전 구간이 섞이면 그 구간만 비고 나머지는 나와야 한다."""
    calls = {"n": 0}
    real = walkforward.run_backtest

    def flaky(macro_arg, df):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("no candles")
        return real(macro_arg, df)

    monkeypatch.setattr(walkforward, "run_backtest", flaky)
    rows = walkforward.run_windows(macro(), frame(120), windows=4)
    assert len(rows) == 4
    assert [row["index"] for row in rows] == [1, 2, 3, 4]
    assert rows[1]["return_pct"] is None and rows[1]["error"]
    assert rows[1]["trades"] == 0
    assert rows[0]["return_pct"] is not None and rows[3]["return_pct"] is not None


def test_the_last_window_failing_still_occupies_the_last_position(monkeypatch):
    """후반부 판정은 마지막 원소를 보므로, 실패한 마지막 구간도 자리를 지켜야 한다."""
    real = walkforward.run_backtest

    def last_fails(macro_arg, df):
        if df["timestamp"].iloc[0] >= pd.Timestamp("2026-04-01", tz="UTC"):
            raise RuntimeError("delisted")
        return real(macro_arg, df)

    monkeypatch.setattr(walkforward, "run_backtest", last_fails)
    rows = walkforward.run_windows(macro(), frame(120), windows=4)
    assert [row["index"] for row in rows] == [1, 2, 3, 4]
    assert rows[3]["return_pct"] is None and rows[3]["error"]


def test_run_windows_on_a_frame_too_short_for_any_window_returns_nothing():
    assert walkforward.run_windows(macro(), frame(1), windows=4) == []


def test_edge_labels_use_open_time_millis_when_there_is_no_timestamp_column():
    ms = pd.DataFrame({"open_time": [1_767_225_600_000, 1_767_312_000_000]})
    assert walkforward._edge(ms) == ("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00")


def test_edge_labels_on_an_empty_or_unlabelled_slice_are_renderable_strings():
    assert walkforward._edge(frame(5).iloc[0:0]) == ("", "")
    assert walkforward._edge(pd.DataFrame({"close": [1.0, 2.0]})) == ("", "")
