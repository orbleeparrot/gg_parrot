"""레그 동기 백테스트 — 한도가 레그를 가로질러 듣는가, 그리고 한도가 없을 때 기존 경로와 같은가."""
import pandas as pd
import pytest

from app.engine.backtest import run_backtest
from app.engine.portfolio_backtest import run_bundle, split_frames_by_time
from app.engine.schema import Macro

BREAKOUT_PARAMS = {"k": 0.5, "initial_capital": 1000}
BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": BREAKOUT_PARAMS, "risk": {"invest_ratio": 1.0},
}


def frame(closes, start="2026-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1h")
    return pd.DataFrame({
        "timestamp": ts,
        "open": closes, "high": [c * 1.03 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * len(closes),
    })


RISING = [100, 101, 102, 103, 104, 105, 106, 107]


def bundle(legs, **over):
    """BASE 위에 legs 와 덮어쓸 칸(risk · bundle_risk 등)을 얹는다."""
    return Macro(**{**BASE, "legs": legs, **over})


# --- 한도가 없을 때 두 경로가 같은 답을 낸다 (§6) ----------------------
def test_lockstep_matches_per_leg_path_when_limit_never_binds():
    """한도가 **닿을 수 없을 때** 동기 루프가 기존 경로와 같은 답을 내야 한다.

    주의: 노출 한도 100% 는 "한도 없음" 이 아니다. 투자비율 1.0 인 두 레그가 다 들어가면
    노출이 정확히 100% 가 되어 그 뒤의 진입이 막힌다. 그래서 투자비율을 0.4 로 낮춰
    최대 노출을 40% 로 묶는다 — 이러면 한도가 구조적으로 닿지 않는다.
    """
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    m = bundle(legs, risk={"invest_ratio": 0.4}, bundle_risk={"max_exposure_pct": 100.0})
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}

    got = dict(run_bundle(m, frames))
    for sym in ("BTCUSDT", "ETHUSDT"):
        want = run_backtest(m.for_leg(next(l for l in m.legs if l.symbol == sym), 500.0),
                            frames[sym])
        # 거래가 0건이면 두 경로가 같은 것은 당연하다 — 비교가 뜻을 가지려면 실제 거래가 있어야 한다.
        assert want.total_trades > 0, f"{sym}: 대조 경로에 거래가 없어 비교가 무의미하다"
        assert got[sym].final_return_pct == want.final_return_pct
        assert got[sym].total_trades == want.total_trades
        assert [p.equity for p in got[sym].equity_curve] == [p.equity for p in want.equity_curve]


# --- 한도가 레그를 가로질러 듣는다 ------------------------------------
def test_max_positions_one_lets_only_one_leg_in():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}

    free = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}), frames))
    assert free["BTCUSDT"].total_trades > 0 and free["ETHUSDT"].total_trades > 0, \
        "대조군에서 두 레그가 다 거래해야 한다 — 아니면 한도 시험이 무의미하다"

    capped = dict(run_bundle(bundle(legs, bundle_risk={"max_positions": 1}), frames))
    assert capped["BTCUSDT"].total_trades > 0
    assert capped["ETHUSDT"].total_trades == 0, "앞 레그가 자리를 잡았는데 뒤 레그가 들어갔다"


def test_leg_order_decides_who_gets_the_slot():
    """순서를 뒤집으면 자리를 잡는 레그가 바뀐다 — 결정적이고 사용자가 뜻을 표현할 수 있다."""
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    first = dict(run_bundle(bundle(
        [{"symbol": "ETHUSDT", "weight": 50}, {"symbol": "BTCUSDT", "weight": 50}],
        bundle_risk={"max_positions": 1}), frames))
    assert first["ETHUSDT"].total_trades > 0 and first["BTCUSDT"].total_trades == 0


def test_exposure_cap_stops_the_second_leg():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    # 한 레그가 자기 자금(500) 전액을 넣으면 노출 50%. 한도를 40% 로 두면 둘째는 못 들어간다.
    capped = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 40.0}), frames))
    assert capped["BTCUSDT"].total_trades > 0
    assert capped["ETHUSDT"].total_trades == 0


# --- 비중 ------------------------------------------------------------
def test_weights_decide_leg_capital():
    legs = [{"symbol": "BTCUSDT", "weight": 70}, {"symbol": "ETHUSDT", "weight": 30}]
    frames = {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}), frames))
    assert got["BTCUSDT"].initial_capital == 700.0
    assert got["ETHUSDT"].initial_capital == 300.0


# --- 레그 길이가 다를 때 (Review Focus 2) -----------------------------
def test_shorter_leg_only_steps_on_its_own_bars():
    """상장이 늦은 종목이 없는 가격으로 거래하면 안 된다.

    짧은 레그의 자산곡선 점 수가 자기 봉 수와 같아야 한다 — 긴 레그의 시각에 끌려가면
    없는 봉에서 판정을 내렸다는 뜻이다.
    """
    long_f = frame(RISING)                                   # 8봉, 2026-01-01 00:00~
    short_f = frame(RISING[:4], start="2026-01-01 04:00")    # 4봉, 04:00~
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": long_f, "ETHUSDT": short_f}))
    assert len(got["BTCUSDT"].equity_curve) == 8
    assert len(got["ETHUSDT"].equity_curve) == 4
    # 짧은 레그의 첫 점은 자기 첫 봉 시각이다(긴 레그의 00:00 이 아니다).
    assert got["ETHUSDT"].equity_curve[0].t.startswith("2026-01-01T04:00")


def test_leg_with_no_rows_fails_that_leg_only():
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": frame(RISING), "ETHUSDT": frame([])}))
    assert got["BTCUSDT"].total_trades >= 0
    assert got["ETHUSDT"].total_trades == 0
    assert got["ETHUSDT"].initial_capital == 500.0


# --- 레그별 규칙 ------------------------------------------------------
def test_legs_can_run_different_rules():
    legs = [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": frame(RISING), "ETHUSDT": frame(RISING)}))
    assert set(got) == {"BTCUSDT", "ETHUSDT"}


# --- 시간 기준 창 자르기 ----------------------------------------------
def test_split_frames_by_time_uses_the_same_boundaries_for_every_leg():
    long_f = frame(list(range(100, 108)))
    short_f = frame(list(range(100, 104)), start="2026-01-01 04:00")
    parts = split_frames_by_time({"BTCUSDT": long_f, "ETHUSDT": short_f}, 2)
    assert len(parts) == 2
    # 앞 창은 00:00~03:00 — 짧은 레그는 그 구간에 봉이 없다.
    assert len(parts[0]["BTCUSDT"]) == 4 and len(parts[0]["ETHUSDT"]) == 0
    assert len(parts[1]["BTCUSDT"]) == 4 and len(parts[1]["ETHUSDT"]) == 4
    # 행이 빠지거나 겹치지 않는다.
    for sym, f in (("BTCUSDT", long_f), ("ETHUSDT", short_f)):
        assert sum(len(p[sym]) for p in parts) == len(f)


def test_split_frames_by_time_handles_too_few_rows():
    assert split_frames_by_time({"BTCUSDT": frame([100])}, 4) == []


def test_shorter_leg_does_not_compete_for_slots_before_it_exists():
    """없는 봉에서는 자리도 잡지 않는다 — 시기가 겹치지 않는 두 레그는 한도 1 이어도 서로 막지 않는다.

    ETH 를 앞에 둔다. ETH 가 자기 봉이 없는 00:00~03:00 에 판정을 내리면 BTC 보다 먼저
    자리를 잡아, 혼자 돌린 BTC 와 결과가 달라진다.
    """
    btc = frame(RISING[:4])                                  # 00:00~03:00
    eth = frame(RISING[:4], start="2026-01-01 04:00")        # 04:00~07:00
    legs = [{"symbol": "ETHUSDT", "weight": 50}, {"symbol": "BTCUSDT", "weight": 50}]
    m = bundle(legs, bundle_risk={"max_positions": 1})
    got = dict(run_bundle(m, {"BTCUSDT": btc, "ETHUSDT": eth}))
    alone = run_backtest(m.for_leg(next(l for l in m.legs if l.symbol == "BTCUSDT"), 500.0), btc)
    assert alone.total_trades > 0, "혼자 돌린 BTC 에 거래가 없으면 이 시험은 무의미하다"
    assert got["BTCUSDT"].total_trades == alone.total_trades
    assert [p.equity for p in got["BTCUSDT"].equity_curve] == [p.equity for p in alone.equity_curve]


# --- 조용히 봉을 버리던 자리 (A2) --------------------------------------
def test_duplicate_timestamps_do_not_swallow_the_rest_of_the_leg():
    """같은 시각 행이 둘이면 커서가 멈춰 남은 봉을 다 버리던 결함."""
    f = frame(RISING)
    dup = pd.concat([f.iloc[:3], f.iloc[2:3], f.iloc[3:]], ignore_index=True)   # 3번째 시각이 두 번
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, risk={"invest_ratio": 0.4},
                                 bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": dup, "ETHUSDT": frame(RISING)}))
    # 중복 행까지 포함해 모든 봉이 처리된다 — 점 수가 행 수와 같다.
    assert len(got["BTCUSDT"].equity_curve) == len(dup)
    assert len(got["ETHUSDT"].equity_curve) == len(RISING)
    # 중복이 없는 레그는 영향을 받지 않는다.
    assert got["ETHUSDT"].total_trades > 0


# --- 빈 프레임 · 시간대 혼용 (A5) --------------------------------------
def test_leg_with_a_column_less_empty_frame_does_not_crash():
    """데이터 계층이 열 없는 빈 프레임을 주더라도 타임라인 조립이 터지지 않는다."""
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": frame(RISING), "ETHUSDT": pd.DataFrame()}))
    assert got["BTCUSDT"].total_trades > 0            # 성한 레그는 자기 봉을 다 본다
    assert len(got["BTCUSDT"].equity_curve) == len(RISING)
    assert got["ETHUSDT"].total_trades == 0


def test_mixed_timezones_do_not_crash_and_each_leg_keeps_its_own_bars():
    """한 레그는 tz 가 있고 하나는 없을 때 — 정렬이 터지지 않고 레그는 자기 봉만 본다.

    운영 데이터는 전부 ``utc=True`` 지만 시험 픽스처는 naive 다. 둘이 섞이는 순간
    ``sorted()`` 가 TypeError 로 죽었다.
    """
    naive = frame(RISING)
    aware = frame(RISING)
    aware["timestamp"] = aware["timestamp"].dt.tz_localize("UTC")
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    m = bundle(legs, risk={"invest_ratio": 0.4}, bundle_risk={"max_exposure_pct": 100.0})
    got = dict(run_bundle(m, {"BTCUSDT": naive, "ETHUSDT": aware}))
    # 두 레그가 같은 시각을 가리키므로 봉 수 · 결과가 모두 같아야 한다 — 합집합이 두 배로
    # 늘거나 한쪽이 상대 봉에서 판정했다면 여기서 어긋난다.
    assert len(got["BTCUSDT"].equity_curve) == len(RISING)
    assert len(got["ETHUSDT"].equity_curve) == len(RISING)
    assert got["BTCUSDT"].total_trades > 0
    assert got["ETHUSDT"].total_trades == got["BTCUSDT"].total_trades
    assert [p.equity for p in got["ETHUSDT"].equity_curve] == \
        [p.equity for p in got["BTCUSDT"].equity_curve]


def test_mixed_timezones_in_window_split_do_not_crash():
    """창 자르기도 같은 자리에서 죽었다 — 경계는 UTC 로 비교한다."""
    naive = frame(list(range(100, 108)))
    aware = frame(list(range(100, 108)))
    aware["timestamp"] = aware["timestamp"].dt.tz_localize("UTC")
    parts = split_frames_by_time({"BTCUSDT": naive, "ETHUSDT": aware}, 2)
    assert len(parts) == 2
    for p in parts:
        assert len(p["BTCUSDT"]) == 4 and len(p["ETHUSDT"]) == 4


def test_empty_frames_are_not_shared_between_windows():
    """빈 레그가 모든 창에 같은 객체로 들어가면 호출부가 한 창을 손대면 다 번진다."""
    parts = split_frames_by_time(
        {"BTCUSDT": frame(list(range(100, 108))), "ETHUSDT": pd.DataFrame()}, 2)
    assert len(parts) == 2
    assert parts[0]["ETHUSDT"] is not parts[1]["ETHUSDT"]


def test_curve_timestamps_match_the_shared_iso_format():
    """곡선의 t 는 backtest._iso 와 같은 글자여야 한다 — 서식을 두 벌 들고 있어서 못 박는다."""
    from app.engine.backtest import _iso

    f = frame(RISING)
    legs = [{"symbol": "BTCUSDT", "weight": 50}, {"symbol": "ETHUSDT", "weight": 50}]
    got = dict(run_bundle(bundle(legs, bundle_risk={"max_exposure_pct": 100.0}),
                          {"BTCUSDT": f, "ETHUSDT": f}))
    assert [p.t for p in got["BTCUSDT"].equity_curve] == [_iso(t) for t in f["timestamp"]]
