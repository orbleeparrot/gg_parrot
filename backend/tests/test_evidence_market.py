"""1 층 근거 — 뉴스보다 먼저 '시장 베타인가 종목 알파인가' 를 가린다.

프레임은 실제 운영 모양(``timestamp`` 열, UTC datetime64)으로 만든다. 거래소가 어디든 같다.
"""
import json
import math

import pandas as pd
import pytest

from app import evidence


def frame(closes, volumes=None, *, with_volume=True):
    stamps = pd.date_range("2026-03-01", periods=len(closes), freq="1D", tz="UTC")
    data = {"timestamp": stamps, "close": closes}
    if with_volume:
        data["volume"] = volumes or [10.0] * len(closes)
    return pd.DataFrame(data)


def assert_json_safe(value):
    """FastAPI 는 allow_nan=False 로 직렬화한다 — nan/inf 가 하나라도 있으면 500 이 된다."""
    json.dumps(value, allow_nan=False)


# ── anomalies ────────────────────────────────────────────────────────────────

def test_anomalies_picks_the_days_that_moved_far_more_than_usual():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    rows = evidence.anomalies(frame(closes))
    assert rows and rows[0]["date"] == "2026-03-21"
    assert rows[0]["change_pct"] > 30


def test_the_reported_date_is_the_day_the_move_landed():
    """변동은 종가 i-1 → i 사이에 일어나므로 날짜는 i 번째 날이다(전날이 아니다)."""
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    df = frame(closes)
    rows = evidence.anomalies(df)
    assert [row["date"] for row in rows] == ["2026-03-21"]
    # 종가가 처음 131 이 된 날이 3/21 (3/1 + 20일). 전날 3/20 은 아직 100 이었다.
    assert df["timestamp"].iloc[20].strftime("%Y-%m-%d") == "2026-03-21"
    assert df["close"].iloc[19] == 100.0 and df["close"].iloc[20] == 131.0


def test_a_flat_coin_has_no_anomalies():
    """변동이 거의 없으면 근거 영역이 빈 채로 정상이어야 한다."""
    assert evidence.anomalies(frame([100.0] * 30)) == []


def test_anomalies_are_capped_and_sorted_by_size():
    closes = [100.0] * 10
    for bump in (40.0, 25.0, 60.0, 30.0, 50.0, 35.0):
        closes += [closes[-1] * (1 + bump / 100.0)]
        closes += [closes[-1]] * 3
    rows = evidence.anomalies(frame(closes), limit=3)
    assert len(rows) == 3
    assert rows[0]["change_pct"] >= rows[1]["change_pct"] >= rows[2]["change_pct"]


def test_drops_are_ranked_by_size_and_keep_their_sign():
    closes = [100.0] * 12
    closes += [70.0]            # -30%
    closes += [70.0] * 6
    closes += [70.0 * 1.5]      # +50%
    closes += [105.0] * 8
    rows = evidence.anomalies(frame(closes))
    assert [row["change_pct"] for row in rows] == [50.0, -30.0]


def test_volume_ratio_compares_against_the_trailing_average():
    closes = [100.0] * 20 + [131.0]
    volumes = [10.0] * 20 + [80.0]
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["volume_ratio"] == 8.0


def test_volume_lookback_is_the_twenty_days_before_the_move_day():
    """당일 거래량은 평균에 넣지 않고, 20 일보다 오래된 날은 빼야 한다."""
    closes = [100.0] * 25 + [131.0] + [131.0] * 4        # 급등일 = index 25
    volumes = [10_000.0] + [10.0] * 24 + [80.0] + [10.0] * 4  # index 0 은 창 밖
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["date"] == "2026-03-26"
    assert rows[0]["volume_ratio"] == 8.0


def test_a_missing_volume_column_still_reports_the_move_without_a_ratio():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    rows = evidence.anomalies(frame(closes, with_volume=False))
    assert rows[0]["date"] == "2026-03-21"
    assert rows[0]["volume_ratio"] is None
    assert_json_safe(rows)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0, 0.0])
def test_an_unusable_volume_gives_no_ratio_instead_of_a_non_finite_number(bad):
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    volumes = [10.0] * 20 + [bad] + [10.0] * 9
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["date"] == "2026-03-21"
    ratio = rows[0]["volume_ratio"]
    assert ratio is None or (math.isfinite(ratio) and ratio >= 0)
    assert_json_safe(rows)


def test_a_zero_trailing_volume_gives_no_ratio():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    volumes = [0.0] * 20 + [80.0] + [0.0] * 9
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["volume_ratio"] is None


@pytest.mark.parametrize("count", [0, 1, 2, 9, 10])
def test_a_short_series_returns_nothing_instead_of_raising(count):
    closes = [100.0] * max(0, count - 1) + ([200.0] if count else [])
    assert evidence.anomalies(frame(closes)) == []


def test_prices_that_cannot_make_a_return_are_skipped_not_counted_as_flat():
    """0 이하 · nan · inf 가격이 낀 구간은 변동률을 못 구하므로 건너뛴다. 나머지 급등은 그대로 잡는다."""
    closes = ([100.0] * 12 + [0.0] + [100.0] * 5 + [float("nan"), float("inf"), -3.0]
              + [100.0] * 5 + [131.0] + [131.0] * 6)
    rows = evidence.anomalies(frame(closes))
    assert [row["date"] for row in rows] == ["2026-03-27"]
    assert rows[0]["change_pct"] == 31.0
    assert_json_safe(rows)


def test_a_series_of_only_unusable_prices_is_empty():
    assert evidence.anomalies(frame([0.0] * 30)) == []
    assert evidence.anomalies(frame([-1.0] * 30)) == []
    assert evidence.anomalies(frame([float("nan")] * 30)) == []


def test_a_return_that_overflows_is_dropped_not_reported_as_inf():
    closes = [100.0] * 15 + [1e-300, 1e308] + [1e308] * 10
    rows = evidence.anomalies(frame(closes))
    assert_json_safe(rows)
    assert all(math.isfinite(row["change_pct"]) for row in rows)


def test_an_unreadable_timestamp_never_becomes_a_date():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    df = frame(closes)
    df.loc[20, "timestamp"] = pd.NaT
    rows = evidence.anomalies(df)
    assert all(row["date"].startswith("20") for row in rows)
    assert_json_safe(rows)


def test_a_frame_without_the_expected_columns_returns_nothing():
    assert evidence.anomalies(pd.DataFrame()) == []
    assert evidence.anomalies(pd.DataFrame({"close": [1.0] * 30})) == []   # timestamp 없음
    assert evidence.anomalies(None) == []


def test_limit_zero_means_none_and_an_empty_result_stays_empty():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    assert evidence.anomalies(frame(closes), limit=0) == []
    assert evidence.anomalies(frame(closes), limit=-3) == []
    assert evidence.anomalies(frame([100.0] * 30), limit=5) == []
    assert len(evidence.anomalies(frame(closes), limit=1)) == 1


def test_a_non_finite_sigma_falls_back_to_the_default():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    assert evidence.anomalies(frame(closes), sigma=float("nan")) == evidence.anomalies(frame(closes))
    assert evidence.anomalies(frame([100.0] * 30), sigma=0.0) == []


def test_every_returned_value_is_json_encodable():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    rows = evidence.anomalies(frame(closes))
    assert set(rows[0]) == {"date", "change_pct", "volume_ratio"}
    assert isinstance(rows[0]["date"], str)
    assert_json_safe(rows)


# ── market_context ───────────────────────────────────────────────────────────

def test_market_context_separates_a_market_day_from_a_coin_day():
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0},
            {"date": "2026-03-25", "change_pct": 9.0, "volume_ratio": 1.2}]
    btc = frame([100.0] * 20 + [131.0] + [139.0] * 9)
    out = evidence.market_context(rows, btc)
    assert out[0]["verdict"] == "시장"      # 기준 종목도 같이 31% 올랐다
    assert out[1]["verdict"] == "종목"      # 기준 종목은 조용한데 이 종목만
    assert out[0]["btc_change_pct"] == 31.0
    assert out[1]["btc_change_pct"] == 0.0


def test_market_context_compares_the_same_day_not_the_day_before():
    rows = [{"date": "2026-03-22", "change_pct": 6.0, "volume_ratio": 1.0}]
    btc = frame([100.0] * 20 + [131.0] + [139.0] * 9)    # 3/22 에 +6.1%
    out = evidence.market_context(rows, btc)
    assert out[0]["btc_change_pct"] == 6.11
    assert out[0]["verdict"] == "시장"


def test_no_benchmark_data_for_the_date_leaves_the_verdict_empty():
    """기준 종목 자료가 없다는 것은 '종목 탓' 의 근거가 아니다."""
    rows = [{"date": "2025-01-01", "change_pct": 31.0, "volume_ratio": 8.0}]
    btc = frame([100.0] * 30)
    out = evidence.market_context(rows, btc)
    assert out[0]["verdict"] == ""
    assert out[0]["btc_change_pct"] is None
    assert out[0]["date"] == "2025-01-01"


@pytest.mark.parametrize("benchmark", [None, pd.DataFrame(), pd.DataFrame({"close": [1.0, 2.0]})])
def test_an_unusable_benchmark_frame_leaves_every_verdict_empty(benchmark):
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0}]
    out = evidence.market_context(rows, benchmark)
    assert out[0]["verdict"] == "" and out[0]["btc_change_pct"] is None


def test_a_benchmark_day_with_an_unusable_price_has_no_verdict():
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0}]
    closes = [100.0] * 20 + [0.0] + [100.0] * 9           # 3/21 은 기준 가격이 0
    out = evidence.market_context(rows, frame(closes))
    assert out[0]["verdict"] == "" and out[0]["btc_change_pct"] is None
    assert_json_safe(out)


def test_market_context_does_not_decide_a_row_that_has_no_move():
    btc = frame([100.0] * 20 + [131.0] + [131.0] * 9)
    rows = [{"date": "2026-03-21", "change_pct": 0.0, "volume_ratio": None},
            {"date": "2026-03-21", "change_pct": float("nan"), "volume_ratio": None},
            {"date": "2026-03-21", "volume_ratio": None}]
    out = evidence.market_context(rows, btc)
    assert [row["verdict"] for row in out] == ["", "", ""]


def test_market_context_with_no_rows_is_empty_and_leaves_inputs_alone():
    btc = frame([100.0] * 30)
    assert evidence.market_context([], btc) == []
    assert evidence.market_context(None, btc) == []
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0}]
    evidence.market_context(rows, btc)
    assert rows == [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 8.0}]


def test_market_context_output_is_json_encodable_end_to_end():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    out = evidence.market_context(evidence.anomalies(frame(closes)), frame(closes))
    assert out and out[0]["verdict"] == "시장"
    assert_json_safe(out)
