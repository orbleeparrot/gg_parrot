"""1 층 근거 — 뉴스보다 먼저 '시장 베타인가 종목 알파인가' 를 가린다.

프레임은 실제 운영 모양(``timestamp`` 열, UTC datetime64)으로 만든다. 거래소가 어디든 같다.
"""
import json
import math
import statistics
from datetime import timezone

import pandas as pd
import pytest

from app import evidence
from app.data.krw import _KST


def frame(closes, volumes=None, *, with_volume=True, start="2026-03-01", tz="UTC"):
    stamps = pd.date_range(start, periods=len(closes), freq="1D", tz=tz)
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
    # 100 → 1e-300 (-100%) 은 정상 변동, 1e-300 → 1e308 은 넘쳐서 버린다.
    assert rows == [{"date": "2026-03-16", "change_pct": -100.0, "volume_ratio": 1.0}]
    assert_json_safe(rows)


def test_an_unreadable_timestamp_never_becomes_a_date():
    """시각을 못 읽은 행은 정렬에서 맨 뒤로 가고, 날짜 자리에 'NaT' 가 새지 않는다.

    그 행에 닿았던 급등은 시각을 아는 다음 행(3/22, 처음 새 가격으로 관찰된 날)의 것이 된다.
    """
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    df = frame(closes)
    df.loc[20, "timestamp"] = pd.NaT
    rows = evidence.anomalies(df)
    assert rows == [{"date": "2026-03-22", "change_pct": 31.0, "volume_ratio": 1.0}]
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


# ── 방향: 같은 쪽으로 움직여야 '시장' ─────────────────────────────────────────

def benchmark_moving(pct, *, day=20):
    """day 번째 날(0 기준)에만 pct% 움직이는 기준 종목 프레임. 그날은 3/21."""
    closes = [100.0] * day + [100.0 * (1 + pct / 100.0)] * (30 - day)
    return frame(closes)


@pytest.mark.parametrize("own, bench, verdict", [
    (31.0, -20.0, "종목"),   # 시장은 내렸는데 이 종목만 올랐다 — 시장으로는 설명되지 않는다
    (-31.0, 20.0, "종목"),
    (-31.0, -20.0, "시장"),
    (31.0, 20.0, "시장"),
    (31.0, 10.0, "종목"),    # 같은 방향이어도 절반(15.5)에 못 미치면 종목
])
def test_the_verdict_needs_the_same_direction_and_enough_magnitude(own, bench, verdict):
    rows = [{"date": "2026-03-21", "change_pct": own, "volume_ratio": 1.0}]
    out = evidence.market_context(rows, benchmark_moving(bench))
    assert out[0]["verdict"] == verdict
    assert out[0]["btc_change_pct"] == bench


def test_a_flat_benchmark_day_is_data_so_the_coin_alone_moved():
    """기준 종목이 정확히 0 이면 '자료 있음 · 안 움직임' → 종목. 자료 없음(None → 빈 판정)과 다르다."""
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 1.0}]
    out = evidence.market_context(rows, frame([100.0] * 30))
    assert out[0]["btc_change_pct"] == 0.0
    assert out[0]["verdict"] == "종목"


def test_a_coin_change_of_exactly_zero_is_left_undecided_even_if_the_benchmark_moved():
    rows = [{"date": "2026-03-21", "change_pct": 0, "volume_ratio": None}]
    out = evidence.market_context(rows, benchmark_moving(31.0))
    assert out[0]["verdict"] == ""
    assert out[0]["btc_change_pct"] == 31.0


# ── sigma: 평소 흔들림은 걸러내고 튄 날만 남긴다 ──────────────────────────────

def noisy_closes(spike_at=15, spike_pct=10.0, days=30):
    """하루 +1% / -1% 를 번갈아 흔들리다 spike_at 번째 날에 spike_pct% 튄다."""
    closes = [100.0]
    for index in range(1, days):
        if index == spike_at:
            closes.append(closes[-1] * (1 + spike_pct / 100.0))
        else:
            closes.append(closes[-1] * (1.01 if index % 2 else 1 / 1.01))
    return closes


def test_ordinary_noise_is_excluded_and_only_the_spike_comes_back():
    rows = evidence.anomalies(frame(noisy_closes()))
    assert [row["date"] for row in rows] == ["2026-03-16"]
    assert rows[0]["change_pct"] == 10.0


def test_sigma_zero_returns_every_move_and_a_huge_sigma_returns_none():
    closes = noisy_closes()
    everything = evidence.anomalies(frame(closes), sigma=0, limit=100)
    assert len(everything) == len(closes) - 1
    assert everything[0]["change_pct"] == 10.0                # 그래도 큰 순
    assert evidence.anomalies(frame(closes), sigma=100.0, limit=100) == []


def test_a_move_just_inside_or_just_outside_the_threshold():
    closes = noisy_closes()
    changes = [(after / before - 1.0) * 100.0 for before, after in zip(closes, closes[1:])]
    z = max(abs(change) for change in changes) / statistics.pstdev(changes)
    assert [row["date"] for row in evidence.anomalies(frame(closes), sigma=z * 0.99)] == ["2026-03-16"]
    assert evidence.anomalies(frame(closes), sigma=z * 1.01) == []


def test_the_bare_minimum_of_returns_is_ten():
    """변동률 10 개(종가 11 개)면 평소를 잴 수 있고, 9 개(종가 10 개)면 아직 못 잰다."""
    ten = [100.0] * 10 + [200.0]
    nine = [100.0] * 9 + [200.0]
    assert [row["date"] for row in evidence.anomalies(frame(ten))] == ["2026-03-11"]
    assert evidence.anomalies(frame(nine)) == []


# ── 거래량 창 길이 ────────────────────────────────────────────────────────────

def test_a_volume_outlier_at_the_oldest_edge_of_the_window_counts():
    closes = [100.0] * 25 + [131.0] + [131.0] * 4              # 급등일 = index 25, 창 = 5..24
    volumes = [10.0] * 5 + [210.0] + [10.0] * 19 + [80.0] + [10.0] * 4   # index 5 = 창의 맨 앞
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["volume_ratio"] == 4.0                       # 평균 (19*10+210)/20 = 20


def test_a_volume_outlier_just_before_the_window_does_not_count():
    closes = [100.0] * 25 + [131.0] + [131.0] * 4
    volumes = [10.0] * 4 + [210.0] + [10.0] * 20 + [80.0] + [10.0] * 4   # index 4 = 창 밖
    rows = evidence.anomalies(frame(closes, volumes))
    assert rows[0]["volume_ratio"] == 8.0


# ── limit · 입력 순서 ─────────────────────────────────────────────────────────

def test_an_infinite_limit_falls_back_to_the_default_instead_of_raising():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    assert len(evidence.anomalies(frame(closes), limit=float("inf"))) == 1
    assert len(evidence.anomalies(frame(closes), limit=float("nan"))) == 1
    assert len(evidence.anomalies(frame(closes), limit=None)) == 1


def test_a_shuffled_frame_gives_the_same_answer_as_a_sorted_one():
    """순서가 섞인 프레임이 '변동 없음' 으로 조용히 빈 결과가 되면 아무도 눈치채지 못한다."""
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    shuffled = frame(closes).sample(frac=1, random_state=7).reset_index(drop=True)
    assert evidence.anomalies(shuffled) == evidence.anomalies(frame(closes))
    rows = [{"date": "2026-03-21", "change_pct": 31.0, "volume_ratio": 1.0}]
    assert evidence.market_context(rows, shuffled) == evidence.market_context(rows, frame(closes))


# ── 날짜 기준(tz): 국내 거래소 일봉은 KST 0 시에 열린다 ────────────────────────

def kst_midnight_frame(closes):
    """3/1 00:00 KST 에 열려 하루 간격으로 이어지는 일봉 — UTC 로는 전날 15:00."""
    return frame(closes, start="2026-02-28 15:00")


def test_the_date_label_follows_the_requested_timezone():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    df = kst_midnight_frame(closes)
    assert df["timestamp"].iloc[20].strftime("%Y-%m-%d %H:%M") == "2026-03-20 15:00"
    assert evidence.anomalies(df)[0]["date"] == "2026-03-20"                        # 기본은 UTC
    assert evidence.anomalies(df, tz=timezone.utc)[0]["date"] == "2026-03-20"
    assert evidence.anomalies(df, tz=_KST)[0]["date"] == "2026-03-21"               # 한국 사용자가 보는 날


def test_a_timezone_naive_frame_is_read_as_utc_and_does_not_raise():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    naive = kst_midnight_frame(closes)
    naive["timestamp"] = naive["timestamp"].dt.tz_localize(None)
    assert evidence.anomalies(naive)[0]["date"] == "2026-03-20"
    assert evidence.anomalies(naive, tz=_KST)[0]["date"] == "2026-03-21"


def test_market_context_matches_dates_in_the_same_timezone():
    closes = [100.0] * 20 + [131.0] + [131.0] * 9
    df = kst_midnight_frame(closes)
    rows = evidence.anomalies(df, tz=_KST)
    out = evidence.market_context(rows, df, tz=_KST)
    assert out[0]["verdict"] == "시장" and out[0]["btc_change_pct"] == 31.0
    # 날짜 기준을 어긋나게 주면 기준 종목의 급등이 3/20 에 붙어 3/21 은 조용한 날로 읽힌다 —
    # 그래서 anomalies 와 market_context 에 같은 tz 를 넘겨야 한다.
    mismatched = evidence.market_context(rows, df)[0]
    assert mismatched["btc_change_pct"] == 0.0 and mismatched["verdict"] == "종목"
