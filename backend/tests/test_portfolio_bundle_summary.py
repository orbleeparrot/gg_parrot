"""묶음 요약 문구 — 비중과 한도가 요약과 해설에 들어간다."""
from app.engine.backtest import BacktestResult
from app.engine.explain import explain_result
from app.engine.schema import Macro
from app.engine.summary import human_summary

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def test_even_weights_say_even():
    s = human_summary(Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]}))
    assert "균등" in s


def test_uneven_weights_are_listed():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                                {"symbol": "ETHUSDT", "weight": 30}]}))
    assert "BTC 70%" in s and "ETH 30%" in s
    assert "균등" not in s


def test_bundle_limit_is_named():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                                {"symbol": "ETHUSDT", "weight": 50}],
                               "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}}))
    assert "묶음 한도" in s and "한 번에 1종목까지" in s and "총 노출 60% 까지" in s


def test_single_symbol_summary_unchanged():
    s = human_summary(Macro(**BASE))
    assert "묶음 한도" not in s and "균등" not in s


def test_leg_rules_are_named_when_they_differ():
    s = human_summary(Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "activation_profit": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]}))
    assert "종목별 규칙" in s


def test_no_leg_rule_means_no_leg_rule_line():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                                {"symbol": "ETHUSDT", "weight": 50}]}))
    assert "종목별 규칙" not in s


def test_limit_line_follows_entry_condition_line():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                                {"symbol": "ETHUSDT", "weight": 40}],
                               "bundle_risk": {"max_positions": 1},
                               "entry_filter": {"kind": "ma", "params": {"period": 20, "side": "above"}}}))
    assert s.index("진입 조건") < s.index("종목 비중") < s.index("묶음 한도")


def _result(**kw):
    base = dict(
        final_return_pct=5.0, buy_hold_return_pct=None, mdd_pct=10.0, total_trades=3,
        win_rate_pct=50.0, max_consecutive_losses=0, liquidation_count=0, sharpe=None,
    )
    return BacktestResult.model_construct(**{**base, **kw})


def test_explain_names_bundle_limit():
    macro = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                      {"symbol": "ETHUSDT", "weight": 50}],
                     "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}})
    pts = explain_result(macro, _result()).points
    assert any("묶음 한도" in p and "한 번에 1종목까지 · 총 노출 60% 까지" in p for p in pts)


def test_explain_silent_without_bundle_limit():
    macro = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    pts = explain_result(macro, _result()).points
    assert not any("묶음 한도" in p for p in pts)


# --- S2. 요약을 조각으로 쪼개는 화면을 깨뜨리지 않는다 -------------------
_SPLIT_HEADINGS = ("종목 비중:", "묶음 한도:")


def test_bundle_pieces_keep_their_heading_when_the_summary_is_split():
    """요약을 `" · "` 로 쪼개 조각으로 조판하는 화면이 있다(전략 문구 · 리더보드 문구).

    묶음 줄이 `" · "` 를 **안에 품으면** `ETH 30%` 와 `총 노출 60% 까지` 가 머리말 없는
    조각으로 떠돈다 — 무엇에 대한 숫자인지 알 수 없다. 묶음 사실은 자기 머리말을 달고
    한 조각 안에 있어야 한다.
    """
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                                {"symbol": "ETHUSDT", "weight": 30}],
                               "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}}))
    pieces = s.split(" · ")
    for fact in ("BTC 70%", "ETH 30%", "한 번에 1종목까지", "총 노출 60% 까지"):
        holders = [p for p in pieces if fact in p]
        assert holders, f"{fact} 가 요약에서 사라졌다"
        assert all(p.startswith(_SPLIT_HEADINGS) for p in holders), \
            f"{fact} 가 머리말 없는 조각으로 떠돈다: {holders}"


def test_the_limit_card_phrase_still_uses_the_middle_dot():
    """카드(`bundleLimitPhrase`)와 해설은 `" · "` 를 그대로 쓴다 — 바꾼 것은 요약에 넣을 때만이다."""
    from app.engine.bundle import limit_note
    from app.engine.schema import BundleRisk

    assert limit_note(BundleRisk(max_positions=1, max_exposure_pct=60)) \
        == "한 번에 1종목까지 · 총 노출 60% 까지"


# --- S7. 묶음의 홀딩 비교가 대표 종목 이야기가 아니다 --------------------
def test_explain_compares_a_bundle_against_the_bundle_not_one_coin():
    """묶음의 홀딩 수익률은 비중대로 섞은 바스켓이다 — 그것을 "그냥 BTC" 라고 하면 거짓이다."""
    macro = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                      {"symbol": "ETHUSDT", "weight": 30}]})
    pts = explain_result(macro, _result(buy_hold_return_pct=3.0)).points
    hold = [p for p in pts if "들고 있었으면" in p]
    assert len(hold) == 1, pts
    assert "이 묶음을 그냥 들고 있었으면" in hold[0]
    assert "그냥 BTC" not in hold[0]


def test_explain_still_names_the_coin_for_a_single_symbol():
    """단일 종목은 그대로 — 바꾼 것은 묶음일 때만이다."""
    pts = explain_result(Macro(**BASE), _result(buy_hold_return_pct=3.0)).points
    hold = [p for p in pts if "들고 있었으면" in p]
    assert len(hold) == 1 and "그냥 BTC 들고 있었으면" in hold[0]
