"""물어볼까 v2 — 종목 후보 풀과 AI 선별 검증."""
from __future__ import annotations

from app import ask_candidates as ac
from app.db import AskMacroSession, get_session


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


def _krw(symbol, *, volume, change, high, low):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str((high + low) / 2),
            "quoteVolume": str(volume), "highPrice": str(high), "lowPrice": str(low)}


def test_pool_order_is_the_same_for_every_profile():
    # 2026-10-12: 성향별 정렬을 없앴다. 안정형엔 저변동 대형주(홀딩이 가장 잘 먹히는 자산)가,
    # 공격형엔 급등주(홀딩이 이기는 자산)가 올라와 둘 다 "홀딩을 넘어라" 와 모순이었다.
    orders = [[c["symbol"] for c in ac.build_pool(_tickers(), profile=p, size=10)]
              for p in ("stable", "balanced", "aggressive")]
    assert orders[0] == orders[1] == orders[2]


def test_pool_prefers_oscillation_headroom_over_raw_range():
    # 한 방향으로 크게 간 종목(변동폭 30% · 순변동 +27%)은 매크로가 +33% 를 벌어도
    # 홀딩 +91% 에 진다. 왕복 여지 = 변동폭 − |순변동| 로 재서 뒤로 보낸다.
    pumped = _krw("KRW-PUMP", volume=9e11, change=27.0, high=130.0, low=100.0)
    swinging = _krw("KRW-SWING", volume=8e11, change=1.0, high=112.0, low=100.0)
    pool = ac.build_pool([pumped, swinging], profile="balanced", exchange="upbit", size=10)
    assert [c["symbol"] for c in pool] == ["KRW-SWING", "KRW-PUMP"]
    assert ac.headroom_pct(pool[0]) > ac.headroom_pct(pool[1])


def test_pool_still_cuts_to_the_most_traded_forty():
    # 유동성 없는 코인이 후보에 오르면 안 된다 — 거래대금 상위 LIQUID_TOP 안에서만 고른다.
    assert ac.LIQUID_TOP == 40
    assert not hasattr(ac, "SCALPER_LIQUID_TOP"), "단타형 전용 상한은 사라졌다"


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


def test_session_has_flow_columns():
    with get_session() as db:
        row = AskMacroSession(
            user_id=1, day_kst="2026-09-23", request_json="{}", candidate_count=0,
            results_json="[]", disclaimer_version="ask-v2", ai_used=False, elapsed_ms=0,
            created_at="2026-09-23T00:00:00Z", created_ms=1,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.candidates_json == "[]"
        assert row.chosen_symbol == ""
        assert row.expires_ms == 0
        assert row.ask_count == 0
        db.delete(row)
        db.commit()


# --- 페그 자산은 후보가 되면 안 된다 (2026-09-23 점검에서 USDE 가 균형형 후보로 나왔다) -------
def _usde():
    """실제 USDE 숫자 — 값은 1.0 에 붙어 있는데 저가 한 번이 0.92 로 찍혀 변동폭이 커 보였다."""
    return {"symbol": "USDEUSDT", "priceChangePercent": "0.01", "lastPrice": "0.9999",
            "quoteVolume": "500000000", "highPrice": "1.0006", "lowPrice": "0.9202",
            "weightedAvgPrice": "0.9995"}


def test_pegged_asset_is_never_a_candidate_for_any_profile():
    tickers = _tickers() + [_usde()]
    for profile in ("stable", "balanced", "aggressive"):
        pool = ac.build_pool(tickers, profile=profile, size=10)
        assert "USDEUSDT" not in [c["symbol"] for c in pool], profile


def test_real_coin_keeps_its_range_pct_under_the_wick_resistant_measure():
    # 실제로 오르내린 코인은 값이 무너지지 않아야 한다 — 안 그러면 공격형 정렬이 뒤집힌다.
    real = {"symbol": "REALUSDT", "priceChangePercent": "8.0", "lastPrice": "0.22",
            "quoteVolume": "500000000", "highPrice": "0.23", "lowPrice": "0.19",
            "weightedAvgPrice": "0.21"}
    pool = ac.build_pool([real], profile="aggressive", size=10)
    assert pool[0]["range_pct"] > 15.0


# --- 이유 문구는 서버가 붙인 숫자 줄 옆에 놓인다 — AI 가 지어낸 통계는 막는다 ------------------
def test_invented_statistics_in_a_reason_fall_back_to_the_rule_sentence():
    coin = next(c for c in _pool() if c["symbol"] == "MIDUSDT")
    for text in ("거래대금 1위라 안전해요", "하루 변동 30% 라 신호가 자주 나와요",
                 "평소보다 3배 활발해요", "하루 거래대금이 5억 원이에요"):
        picks = ac.validate_picks([{"symbol": "MIDUSDT", "reason": text}], _pool(), "balanced")
        reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
        assert reason == ac.fallback_reason("balanced", coin), text


def test_a_harmless_number_in_a_reason_is_kept():
    picks = ac.validate_picks(
        [{"symbol": "MIDUSDT", "reason": "20일선 근처에서 천천히 움직이고 있어요"}],
        _pool(), "balanced")
    reason = next(p["reason"] for p in picks if p["symbol"] == "MIDUSDT")
    assert reason == "20일선 근처에서 천천히 움직이고 있어요"


def test_fallback_reasons_differ_between_clearly_different_coins():
    # AI 가 없을 때 카드 3~4장이 똑같은 문장을 달고 나오면 화면이 고장 난 것처럼 보인다.
    calm = {"symbol": "CALMUSDT", "base": "CALM", "volume_rank": 1, "range_pct": 1.0}
    wild = {"symbol": "WILDUSDT", "base": "WILD", "volume_rank": 25, "range_pct": 20.0}
    assert ac.fallback_reason("balanced", calm) != ac.fallback_reason("balanced", wild)
    assert "추천" not in ac.fallback_reason("balanced", calm)
    assert len(ac.fallback_reason("balanced", wild)) <= ac.REASON_MAX


def test_rule_only_candidates_do_not_all_share_one_sentence():
    tickers = _tickers() + [_t("EXTRAUSDT", 600_000_000, 140, 100)]
    pool = ac.build_pool(tickers, profile="balanced", size=10)
    picks, ai_used = ac.choose(pool, profile="balanced", horizon="weeks", watch="sometimes")
    assert ai_used is False
    assert len({p["reason"] for p in picks}) > 1


def test_pool_never_ranks_a_one_sided_pump_ahead_of_a_calm_coin():
    # 2026-10-12 전에는 안정형이 range_pct 오름차순이라 한쪽으로만 쭉 간 날의 코인이 '가장
    # 잔잔한' 자리에 올라올 수 있었다. 이제는 왕복 여지로 재므로 순변동이 큰 종목이 스스로
    # 뒤로 간다 — 급등주는 어느 성향에서도 첫 카드가 되지 않는다.
    def full(symbol, change, high, low, weighted, qvol):
        return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(weighted),
                "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low),
                "weightedAvgPrice": str(weighted)}

    pump = full("PUMPUSDT", 30, 1.30, 1.00, 1.295, 900_000_000)      # 고가 부근에서 마감
    calm = full("QUIETUSDT", 0.4, 101.5, 98.5, 100.0, 800_000_000)   # 진짜로 잔잔한 코인
    pool = ac.build_pool([pump, calm], profile="stable", size=10)
    assert [c["symbol"] for c in pool][0] == "QUIETUSDT"
    ranked = {c["symbol"]: c["range_pct"] for c in pool}
    assert ranked["PUMPUSDT"] > ranked["QUIETUSDT"]


# --- 30일 창 지표 (2026-10-29) -------------------------------------------------
# 왜 24시간이 아니라 30일인가: 24시간 창에서는 "며칠째 오르내리는 횡보 종목" 과
# "올라서 고점 찍고 막 되밀린 종목" 이 똑같이 보인다(둘 다 변동폭 크고 순변화 0).
# 뒤쪽이 사용자가 아쉬워한 '이미 고점에 올라간 코인' 이다.


def test_chop_is_zero_for_a_one_way_climb():
    assert ac.chop_pct([100.0, 101.0, 102.0, 103.0]) == 0.0


def test_chop_is_full_when_it_ends_where_it_started():
    assert ac.chop_pct([100.0, 110.0, 100.0, 110.0, 100.0]) == 100.0


def test_chop_is_unknown_without_movement():
    # 움직임이 없으면 분모가 0 이다 — 0 점이 아니라 '모른다'.
    assert ac.chop_pct([100.0, 100.0, 100.0]) is None


def test_chop_is_unknown_with_too_few_closes():
    assert ac.chop_pct([100.0]) is None


def test_range_position_is_full_at_the_top():
    assert ac.range_position_pct([100.0, 110.0, 120.0]) == 100.0


def test_range_position_is_half_in_the_middle():
    assert ac.range_position_pct([100.0, 120.0, 110.0]) == 50.0


def test_range_position_is_unknown_when_flat():
    assert ac.range_position_pct([100.0, 100.0]) is None


def test_chop_ignores_broken_closes():
    # 거래소 응답에는 빈 봉·None·NaN 이 섞인다. 조용히 순위를 망가뜨리지 않게 걸러낸다.
    assert ac.chop_pct([100.0, float("nan"), 110.0, 100.0]) == 100.0


def test_range_position_ignores_broken_closes():
    assert ac.range_position_pct([100.0, None, 120.0, 110.0]) == 50.0


# --- 30일 창을 풀 선정에 붙인다 (2026-10-29) -----------------------------------
# 호출자가 일봉을 주입한다(build_pool 은 순수 함수로 남는다 — 측정기가 과거 구간을
# 재현할 수 있어야 한다). 일봉이 없는 종목은 기존 headroom_pct 순서로 뒤에 붙는다.


def _climb(start=100.0, step=5.0, count=30):
    """한 방향으로만 오른 일봉 — chop 0, 지금 가격은 30일 최고점."""
    return [start + step * i for i in range(count)]


def _zigzag(low=100.0, high=110.0, count=30):
    """오르내리기를 되풀이한 일봉 — chop 높고 지금 가격은 범위 가운데쯤."""
    out = [low + (high - low) * (i % 2) for i in range(count - 1)]
    out.append((low + high) / 2)
    return out


def _wide_tickers():
    """고점 종목 하나를 빼도 MIN_PICKS 가 남는 세계. 실제로는 상위 40종목을 본다."""
    return [_t(f"W{i}USDT", 900_000_000 - i, 105, 100) for i in range(5)] + [
        _t("PEAKUSDT", 800_000_000, 120, 100)]


def test_pool_drops_a_coin_sitting_at_the_top_of_its_month():
    # 사용자 피드백의 핵심 — '이미 고점에 올라간 코인' 을 목록에서 뺀다.
    daily = {f"W{i}USDT": _zigzag() for i in range(5)}
    daily["PEAKUSDT"] = _climb()
    pool = ac.build_pool(_wide_tickers(), profile="balanced", size=10, daily=daily)
    assert "PEAKUSDT" not in [c["symbol"] for c in pool]


def test_pool_can_order_by_the_30d_chop_when_asked():
    # chop 정렬은 손잡이로만 남는다 — 기본값은 headroom 이다(ORDER_DEFAULT 주석의 측정).
    daily = {"CALMUSDT": _zigzag(), "MIDUSDT": _climb(step=1.0), "WILDUSDT": _climb()}
    pool = ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily, order="chop")
    assert pool[0]["symbol"] == "CALMUSDT"


def test_the_default_order_is_the_one_the_measurement_chose():
    # 기본값이 조용히 바뀌면 측정으로 고른 설정이 사라진다.
    assert ac.ORDER_DEFAULT == "headroom"


def test_pool_keeps_a_coin_whose_daily_candles_are_missing_but_puts_it_after():
    daily = {"CALMUSDT": _zigzag()}
    symbols = [c["symbol"] for c in ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily)]
    assert symbols[0] == "CALMUSDT"
    assert "MIDUSDT" in symbols and "WILDUSDT" in symbols


def test_pool_relaxes_the_cap_rather_than_coming_back_empty():
    # 상승장에서는 상위 종목 전부가 한 달 고점 근처일 수 있다. 503 을 내는 것보다
    # 고점 종목을 보여주는 쪽이 낫다.
    daily = {"CALMUSDT": _climb(), "MIDUSDT": _climb(), "WILDUSDT": _climb()}
    pool = ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily)
    assert len(pool) >= ac.MIN_PICKS


def test_pool_without_daily_candles_behaves_exactly_as_before():
    # 캐시가 비어 있는 첫 요청은 기다리지 않는다 — 예전 경로를 그대로 탄다.
    before = ac.build_pool(_tickers(), profile="balanced", size=10)
    after = ac.build_pool(_tickers(), profile="balanced", size=10, daily=None)
    assert [c["symbol"] for c in before] == [c["symbol"] for c in after]


def test_liquid_symbols_names_everything_the_caller_must_fetch():
    # 상위 10개만 주입하면, 폐기하려는 24시간 지표가 이미 고른 목록을 재배열만 한다.
    names = ac.liquid_symbols(_tickers())
    assert set(names) == {"CALMUSDT", "MIDUSDT", "WILDUSDT"}


# --- 종목 카드가 30일 창을 말한다 (2026-10-29) ---------------------------------
# 순서를 30일 기준으로 바꿔 놓고 화면에는 '하루 변동' 만 보이면, 사용자가 보는 숫자가
# 순서와 아무 관계가 없어진다("왜 이 종목인지 보이게 한다" 와 어긋난다).
# 문장은 **서버가** 만든다 — AI 가 쓴 퍼센트는 _FAKE_STAT 이 어차피 떨어뜨리고,
# 숫자는 서버가 붙인다는 원칙(설계 2장)도 그대로다.


def test_card_says_where_in_the_month_the_price_sits():
    daily = {f"W{i}USDT": _zigzag() for i in range(5)}
    daily["PEAKUSDT"] = _climb()
    pool = ac.build_pool(_wide_tickers(), profile="balanced", size=10, daily=daily)
    picks = ac.validate_picks([], pool, "balanced")
    assert all("한 달" in p["range_note"] for p in picks)


def test_card_tells_the_middle_of_the_month_from_the_top():
    middle = ac.range_note_for(45.0)
    top = ac.range_note_for(95.0)
    assert middle != top
    assert "가운데" in middle


def test_card_leaves_the_note_empty_without_daily_candles():
    # 일봉이 없으면 아무 말도 하지 않는다 — 화면은 그 줄을 렌더하지 않는다.
    pool = ac.build_pool(_tickers(), profile="balanced", size=10)
    picks = ac.validate_picks([], pool, "balanced")
    assert all(p["range_note"] == "" for p in picks)


def test_card_carries_the_30d_numbers_for_the_screen():
    daily = {"CALMUSDT": _zigzag(), "MIDUSDT": _zigzag(), "WILDUSDT": _zigzag()}
    pool = ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily)
    picks = ac.validate_picks([], pool, "balanced")
    assert all(isinstance(p["chop_30d"], float) for p in picks)


def _rise_then_dip():
    """꾸준히 오르다 끝에서 조금 밀린 일봉 — chop 은 낮고 위치는 상한 아래."""
    out = [100.0 + 5.0 * i for i in range(25)]
    out.append(190.0)
    return out


def test_pool_can_keep_the_old_order_while_still_dropping_peaks():
    # 상한(고점 제외)과 정렬(chop) 중 **어느 쪽이** 성적을 움직였는지 측정으로 가르려면
    # 둘을 따로 켤 수 있어야 한다. 상한은 그대로 두고 정렬만 예전 것으로.
    daily = {"CALMUSDT": _zigzag(), "MIDUSDT": _zigzag(), "WILDUSDT": _rise_then_dip()}
    by_chop = ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily, order="chop")
    by_headroom = ac.build_pool(_tickers(), profile="balanced", size=10, daily=daily)
    # 하루 변동폭이 가장 큰 WILD 가 headroom 으로는 1등, 30일 chop 으로는 아니다.
    assert by_headroom[0]["symbol"] == "WILDUSDT"
    assert by_chop[0]["symbol"] != "WILDUSDT"
    # 어느 정렬이든 상한은 그대로 듣는다.
    assert all(c["range_pos_30d"] <= ac.RANGE_POS_STEPS[0] for c in by_headroom)
