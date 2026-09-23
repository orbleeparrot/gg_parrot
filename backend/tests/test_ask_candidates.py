"""물어볼까 v2 — 종목 후보 풀과 AI 선별 검증."""
from __future__ import annotations

from app import ask_candidates as ac


def _t(symbol, qvol, high, low, change=1.0):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(low),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}


def _tickers():
    # 변동폭: CALM 1% · MID 5% · WILD 20%, 거래대금은 CALM > MID > WILD
    return [
        _t("CALMUSDT", 900_000_000, 101, 100),
        _t("MIDUSDT", 800_000_000, 105, 100),
        _t("WILDUSDT", 700_000_000, 120, 100),
        _t("USDCUSDT", 950_000_000, 101, 100),      # 스테이블 → 제외
        _t("BTCUPUSDT", 850_000_000, 130, 100),     # 레버리지 토큰 → 제외
        _t("TINYUSDT", 5_000, 200, 100),            # 거래대금 미달 → 제외
    ]


def test_pool_drops_stable_leverage_and_illiquid():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    symbols = [c["symbol"] for c in pool]
    assert "USDCUSDT" not in symbols
    assert "BTCUPUSDT" not in symbols
    assert "TINYUSDT" not in symbols


def test_pool_carries_volume_rank_by_quote_volume():
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    ranks = {c["symbol"]: c["volume_rank"] for c in pool}
    assert ranks["CALMUSDT"] == 1
    assert ranks["MIDUSDT"] == 2
    assert ranks["WILDUSDT"] == 3


def test_stable_profile_prefers_calm_coins():
    pool = ac.build_pool(_tickers(), profile="stable", size=3)
    assert pool[0]["symbol"] == "CALMUSDT"


def test_aggressive_profile_prefers_wild_coins():
    pool = ac.build_pool(_tickers(), profile="aggressive", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_balanced_profile_prefers_middle_volatility():
    pool = ac.build_pool(_tickers(), profile="balanced", size=3)
    assert pool[0]["symbol"] == "MIDUSDT"


def test_scalper_profile_prefers_wild_among_most_traded():
    pool = ac.build_pool(_tickers(), profile="scalper", size=3)
    assert pool[0]["symbol"] == "WILDUSDT"


def test_pool_size_is_capped():
    assert len(ac.build_pool(_tickers(), profile="balanced", size=2)) == 2


import json


def _pool():
    return ac.build_pool(_tickers(), profile="balanced", size=10)


def _ai(payload):
    return lambda prompt: json.dumps(payload, ensure_ascii=False)


def test_picks_outside_pool_are_dropped():
    picks = ac.validate_picks(
        [{"symbol": "SCAMUSDT", "reason": "좋아 보여요"},
         {"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
        _pool(), "balanced")
    symbols = [p["symbol"] for p in picks]
    assert "SCAMUSDT" not in symbols
    assert "MIDUSDT" in symbols


def test_banned_words_are_replaced_with_fallback_reason():
    picks = ac.validate_picks(
        [{"symbol": "MIDUSDT", "reason": "무조건 오르는 종목이라 수익을 보장해요"}],
        _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert "보장" not in reason and "무조건" not in reason
    assert reason == ac.fallback_reason("balanced", next(c for c in _pool() if c["symbol"] == "MIDUSDT"))


def test_short_ai_answer_is_topped_up_from_pool_order():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
                              _pool(), "balanced")
    assert len(picks) >= ac.MIN_PICKS


def test_too_many_picks_are_cut():
    # _pool() 은 유효 심볼이 3개뿐이라, 자르는 동작을 실제로 보려면 MAX_PICKS(4)
    # 보다 많은 유효 심볼이 있는 풀이 필요하다. 딱 4개만 넣으면 자르는 코드를
    # 지워도 테스트가 통과해버리므로 5개를 넣는다.
    tickers = _tickers() + [
        _t("EXTRAUSDT", 600_000_000, 110, 100),
        _t("EXTRA2USDT", 500_000_000, 108, 100),
    ]
    pool = ac.build_pool(tickers, profile="balanced", size=10)
    assert len(pool) == 5
    raw = [{"symbol": c["symbol"], "reason": "거래가 활발해요"} for c in pool]
    assert len(ac.validate_picks(raw, pool, "balanced")) == ac.MAX_PICKS


def test_long_reason_is_trimmed():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "가" * 200}], _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert len(reason) <= ac.REASON_MAX


def test_duplicate_symbols_are_collapsed():
    raw = [{"symbol": "MIDUSDT", "reason": "하나"}, {"symbol": "MIDUSDT", "reason": "둘"}]
    picks = ac.validate_picks(raw, _pool(), "balanced")
    assert [p["symbol"] for p in picks].count("MIDUSDT") == 1


def test_picks_carry_server_computed_numbers():
    picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": "거래가 활발해요"}],
                              _pool(), "balanced")
    mid = next(p for p in picks if p["symbol"] == "MIDUSDT")
    assert mid["range_pct"] == 5.0
    assert mid["volume_rank"] == 2
    assert mid["base"] == "MID"


def test_choose_uses_ai_when_it_answers():
    picks, ai_used = ac.choose(
        _pool(), profile="balanced", horizon="weeks", watch="sometimes",
        ask_ai=_ai([{"symbol": "WILDUSDT", "reason": "변동이 커서 신호가 자주 나와요"},
                    {"symbol": "MIDUSDT", "reason": "거래가 활발해요"},
                    {"symbol": "CALMUSDT", "reason": "하루 변동이 작아요"}]))
    assert ai_used is True
    assert [p["symbol"] for p in picks][:3] == ["WILDUSDT", "MIDUSDT", "CALMUSDT"]


def test_choose_falls_back_when_ai_raises():
    def boom(prompt):
        raise RuntimeError("gemini down")

    picks, ai_used = ac.choose(_pool(), profile="stable", horizon="months",
                               watch="rarely", ask_ai=boom)
    assert ai_used is False
    assert len(picks) >= ac.MIN_PICKS
    assert all(p["reason"] for p in picks)


def test_choose_falls_back_when_ai_returns_garbage():
    picks, ai_used = ac.choose(_pool(), profile="stable", horizon="months",
                               watch="rarely", ask_ai=lambda p: "not json at all")
    assert ai_used is False
    assert len(picks) >= ac.MIN_PICKS


def test_prompt_never_says_recommend():
    prompt = ac.build_prompt(_pool(), profile="balanced", horizon="weeks", watch="sometimes")
    assert "추천" not in prompt


def test_choose_reports_ai_used_false_when_all_ai_symbols_are_outside_pool():
    # AI 가 JSON 은 제대로 돌려줘도 풀 밖 심볼뿐이면, 결과는 전부 규칙 폴백이므로
    # ai_used 는 False 여야 한다 — 이게 이 태스크가 막으려는 바로 그 상황이다.
    picks, ai_used = ac.choose(
        _pool(), profile="balanced", horizon="weeks", watch="sometimes",
        ask_ai=_ai([{"symbol": "SCAMUSDT", "reason": "좋아 보여요"},
                    {"symbol": "FAKEUSDT", "reason": "많이 올라요"}]))
    assert ai_used is False
    assert len(picks) >= ac.MIN_PICKS


def test_profit_promise_pattern_is_replaced_with_fallback_reason():
    picks = ac.validate_picks(
        [{"symbol": "MIDUSDT", "reason": "하루 5% 씩 수익 나는 흐름이에요"}],
        _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert reason == ac.fallback_reason("balanced", next(c for c in _pool() if c["symbol"] == "MIDUSDT"))


def test_symbol_normalization_trims_whitespace_and_case():
    picks = ac.validate_picks(
        [{"symbol": "  midusdt  ", "reason": "거래가 활발해요"}],
        _pool(), "balanced")
    assert any(p["symbol"] == "MIDUSDT" for p in picks)
