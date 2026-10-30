"""물어볼까 숏 후보 — 바이낸스 선물에서만, 엔진이 실제로 숏을 미러링하는 방식만 (2026-10-30).

왜 필요한가: 지금은 선물·레버리지를 골라도 ``_make_macro`` 가 ``position_side`` 를 "long"
으로 못 박아 롱 후보만 낸다. 하락 구간에서는 롱 후보가 ``worth_the_macro`` 의 첫 관문(그
자체로 벌었나)에서 전부 떨어져 화면이 "보여 드릴 만큼 이득이 난 조합이 없었다" 로 끝난다 —
같은 구간에서 숏은 벌었는데 아예 재 보지 않는다.

**방식을 가려 쓴다.** 엔진에서 숏을 미러링하는 것은 A·F·G·J 뿐이다. E(TrailingSim)·
H(MartingaleSim)·I(BreakoutSim) 는 ``_open_long`` 을 하드코딩하고 진입 조건에서
``self.side`` 를 읽지 않는다 — 숏으로 만들면 매크로는 숏이라 말하면서 엔진은 롱을 잡는다.

**예측은 하지 않는다.** 숏 후보가 올라오는 조건은 "떨어질 것 같다" 가 아니라 기존 관문
그대로다: 고른 과거 구간에서 그 자체로 벌었고(``worth_the_macro``) 홀딩을 문턱만큼 넘었다.
그래서 상승 구간에서는 숏이 저절로 사라지고 하락 구간에서는 롱이 저절로 사라진다.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import ask
from engine.backtest import BacktestResult
from engine.live_session import build_driver
from engine.schema import PositionSide

FUTURES = dict(risk_profile="aggressive", market="futures", leverage=2)


def _plan(**kw) -> ask._Plan:
    base = dict(risk_profile="balanced", market="spot", leverage=1, symbols=["BTCUSDT"],
                period_preset="3m", interval="1h")
    base.update(kw)
    return ask._Plan(**base)


def _side(macro) -> str:
    return macro.position_side.value


# --- 후보 생성 ---------------------------------------------------------------

def test_spot_plans_stay_long_only():
    # 현물은 공매도를 할 수 없다. 레버리지 없는 선물도 숏은 되지만, 시장이 현물이면 끝이다.
    cands = ask.build_templates(_plan())
    assert cands
    assert all(_side(c.macro) == "long" for c in cands)


def test_domestic_plans_stay_long_only():
    # 업비트·빗썸은 원화 현물뿐이다(CandidatesRequest 가 먼저 막지만 _Plan 으로도 들어올 수 있다).
    cands = ask.build_templates(_plan(exchange="upbit", symbols=["KRW-BTC"]))
    assert cands
    assert all(_side(c.macro) == "long" for c in cands)


def test_futures_plans_add_short_candidates():
    cands = ask.build_templates(_plan(**FUTURES))
    shorts = [c for c in cands if _side(c.macro) == "short"]
    longs = [c for c in cands if _side(c.macro) == "long"]
    assert shorts and longs, "선물에서는 두 방향을 모두 재 본다"
    assert all(c.macro.market == "futures" and c.macro.leverage == 2 for c in shorts)


def test_only_engine_mirrored_types_go_short():
    cands = ask.build_templates(_plan(**FUTURES))
    short_types = {c.macro.rule_type.value for c in cands if _side(c.macro) == "short"}
    assert short_types == set(ask.SHORT_RULE_TYPES)
    # 롱은 여덟 방식을 다 본다 — 숏을 더하면서 롱이 좁아지지 않았다.
    assert {c.macro.rule_type.value for c in cands if _side(c.macro) == "long"} == set(
        ask._allowed_types(_plan(**FUTURES)))


def test_short_first_presets_come_before_the_deeper_long_presets():
    """시간 예산(ASK_TIME_BUDGET_SEC)이 뒤쪽 후보를 자르므로 순서가 곧 산출 여부다.

    숏을 맨 뒤에 붙이면 예산이 끝난 뒤라 한 장도 평가되지 않는다 — 기능이 조용히 꺼진다.
    """
    cands = ask.build_templates(_plan(**FUTURES))
    sides = [_side(c.macro) for c in cands]
    first_short = sides.index("short")
    long_types_count = len(ask._allowed_types(_plan(**FUTURES)))
    assert first_short == long_types_count, "롱 첫 프리셋 한 바퀴 바로 뒤에 숏이 와야 한다"
    assert set(sides[first_short : first_short + len(ask.SHORT_RULE_TYPES)]) == {"short"}


def test_short_candidates_get_their_own_label():
    """화면이 ``label + rule_type`` 을 카드의 React 키로 쓴다.

    프리셋끼리는 예전부터 같은 라벨을 쓴다(``이동평균 크로스 · 1h · BTCUSDT``) — 한 방식에서
    한 장만 뽑히므로(MAX_PER_TYPE) 화면에서 부딪히지 않는다. 숏이 들어오면서 같은 방식의
    두 방향이 **함께** 뽑힐 수 있게 됐으니, 갈라지는 자리는 거기다.
    """
    cands = ask.build_templates(_plan(**FUTURES))
    by_side: dict[tuple[str, str], str] = {
        (c.macro.rule_type.value, _side(c.macro)): c.label for c in cands}
    for rule_type in ask.SHORT_RULE_TYPES:
        assert by_side[(rule_type, "long")] != by_side[(rule_type, "short")]
    assert all("숏" in label for (_rt, side), label in by_side.items() if side == "short")
    assert not any("숏" in label for (_rt, side), label in by_side.items() if side == "long")
    assert all("추천" not in c.label for c in cands)


def test_every_short_preset_survives_the_schema():
    """숏 프리셋이 스키마에 걸려 None 이 되면 그 방식이 조용히 빠진다.

    A 는 숏일 때 ``risk.stop_loss_pct > 0`` 이 필수다(숏 손실은 이론상 무한). 지금 프리셋은
    전부 손절을 들고 있는데, 손절 없는 프리셋이 하나 더해지면 A 숏이 말없이 사라진다.
    """
    plan = _plan(**FUTURES)
    presets = ask._presets_for(plan)
    for rule_type in ask.SHORT_RULE_TYPES:
        for index, preset in enumerate(presets[rule_type]):
            macro = ask._make_macro(plan, rule_type, preset, ["BTCUSDT"], side="short")
            assert macro is not None, f"{rule_type} 숏 프리셋 {index} 가 스키마에서 떨어졌다"
            assert macro.position_side is PositionSide.SHORT


def test_short_rule_types_agree_with_the_builder_whitelist():
    """빌더(frontend/src/lib/macro.js)의 ``allowShort`` 와 같은 집합이어야 한다.

    두 벌이 언어를 달리해 떨어져 있다. 한쪽만 고치면 물어볼까가 내준 카드를 빌더로
    불러올 때 "숏을 지원하지 않아요" 로 막힌다 — 사용자에게는 고장으로 보인다.
    """
    source = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "macro.js"
    text = source.read_text(encoding="utf-8")
    allowed = {m.group(1) for m in re.finditer(r"^\s*([A-Z]):\s*\{[^}]*allowShort:\s*true", text,
                                               re.MULTILINE)}
    assert allowed, "macro.js 에서 allowShort 화이트리스트를 읽지 못했다"
    assert set(ask.SHORT_RULE_TYPES) == allowed & set(ask.ALL_RULE_TYPES)


# --- 엔진이 정말 방향을 뒤집는가 ---------------------------------------------

def _wave_closes(bars: int = 400) -> list[float]:
    """주기가 다른 파동 셋을 겹친 종가. 지표가 실제로 발동하는 모양이다.

    규칙적인 톱니로는 안 된다 — 변동폭이 일정하면 볼린저 밴드를 한 번도 뚫지 않아 G 가
    한 번도 진입하지 않고, 그러면 방향을 볼 체결 자체가 없다.
    """
    closes: list[float] = []
    price = 1000.0
    for i in range(bars):
        price *= 1 + 0.012 * math.sin(i / 4.0) + 0.009 * math.sin(i / 11.0) + 0.006 * math.sin(i / 29.0)
        closes.append(round(price, 4))
    return closes


def _first_open_side(rule_type: str, side: str) -> str:
    """이 매크로가 **실제로 연 포지션의 방향**. 체결이 없으면 "none".

    왜 수익률 비교가 아니라 체결 방향인가: 엔진은 진입에서 side 를 안 읽는 방식도
    청산 · 자산 · 손절 · 청산가는 side 로 가른다. 그래서 E·H·I 를 숏으로 돌려도 숫자는
    롱과 **다르게** 나온다 — "결과가 다르다" 로는 아무것도 못 가린다. 가리는 것은
    ``Fill.side`` 하나다: 미러링하는 방식은 "short" 로 열고, 안 하는 방식은 "buy" 로 연다.
    """
    plan = _plan(**{**FUTURES, "leverage": 1})
    preset = ask._presets_for(plan)[rule_type][0]
    macro = ask._make_macro(plan, rule_type, preset, ["BTCUSDT"], side=side)
    assert macro is not None
    driver = build_driver(macro, "BTCUSDT")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, close in enumerate(_wave_closes()):
        ts = start + timedelta(hours=index)
        driver.push_candle("BTCUSDT", (int(ts.timestamp() * 1000), close, close * 1.003,
                                       close * 0.997, close, 1.0))
        fill = driver.tick(close, ts)
        if fill is not None and fill.side in ("buy", "short"):
            return fill.side
    return "none"


@pytest.mark.parametrize("rule_type", ask.SHORT_RULE_TYPES)
def test_short_macros_really_open_short(rule_type):
    assert _first_open_side(rule_type, "long") == "buy"
    assert _first_open_side(rule_type, "short") == "short"


@pytest.mark.parametrize("rule_type", ["E", "H", "I"])
def test_the_excluded_types_would_open_long_even_when_told_to_short(rule_type):
    """화이트리스트가 왜 넷뿐인지를 **보여 주는** 시험.

    E(TrailingSim) · H(MartingaleSim) · I(BreakoutSim) 는 진입에서 ``self.side`` 를 읽지
    않고 ``_open_long`` 을 부른다. 숏으로 만들면 매크로는 숏이라 말하면서 엔진은 롱을
    잡는다. 이 시험이 깨진다면 그 방식이 고쳐졌다는 뜻이니, 그때 SHORT_RULE_TYPES 에
    더하면 된다.
    """
    assert rule_type not in ask.SHORT_RULE_TYPES
    assert _first_open_side(rule_type, "short") == "buy"


# --- 줄 세우기 ---------------------------------------------------------------

def _result(ret, mdd, trades=20, win=50.0, bh=None) -> BacktestResult:
    return BacktestResult(
        initial_capital=1.0, final_equity=1.0 + ret / 100, final_return_pct=ret, mdd_pct=mdd,
        win_rate_pct=win, total_trades=trades, trades=[], equity_curve=[],
        buy_hold_return_pct=bh,
    )


def _cand(rule_type, side="long", symbol="BTCUSDT") -> ask.Candidate:
    plan = _plan(**FUTURES, symbols=[symbol])
    preset = ask._presets_for(plan)[rule_type][0]
    macro = ask._make_macro(plan, rule_type, preset, [symbol], side=side)
    assert macro is not None
    return ask.Candidate(ask._label(rule_type, plan, [symbol], side=side), macro, "template")


def test_select_top_gives_the_other_side_a_slot():
    """상위 셋이 모두 한 방향이면 꼴찌 자리를 다른 방향 1등에게 넘긴다.

    사장님 요청("롱·숏별로 보여 달라")의 본체다. 두 방향이 같은 관문을 넘었는데 점수순으로
    한쪽만 보여 주면, 사용자는 반대 방향이 가능했다는 사실 자체를 모른다.
    """
    ev = [
        ask.Evaluated(_cand("J"), _result(30, 6, bh=0)),
        ask.Evaluated(_cand("G"), _result(25, 6, bh=0)),
        ask.Evaluated(_cand("F"), _result(20, 6, bh=0)),
        ask.Evaluated(_cand("A", side="short"), _result(12, 6, bh=0)),
    ]
    top = ask.select_top(ev, "aggressive", period="3m", interval="1h")
    assert len(top) == 3
    sides = [_side(e.candidate.macro) for e in top]
    assert sides.count("short") == 1 and sides.count("long") == 2
    # 내준 자리는 꼴찌 하나다 — 롱 1·2등은 그대로 남는다.
    assert [e.candidate.macro.rule_type.value for e in top if _side(e.candidate.macro) == "long"] == ["J", "G"]


def test_select_top_keeps_rank_order_after_reserving_the_slot():
    # 자리를 내준 뒤에도 1위·2위·3위 배지가 점수순이어야 한다(화면이 순서를 순위로 읽는다).
    ev = [
        ask.Evaluated(_cand("J"), _result(30, 6, bh=0)),
        ask.Evaluated(_cand("G"), _result(9, 6, bh=0)),
        ask.Evaluated(_cand("F"), _result(8, 6, bh=0)),
        ask.Evaluated(_cand("A", side="short"), _result(20, 6, bh=0)),
    ]
    top = ask.select_top(ev, "aggressive", period="3m", interval="1h")
    returns = [float(e.result.final_return_pct) for e in top]
    assert returns == sorted(returns, reverse=True)
    assert returns[1] == 20.0, "숏이 2등 점수면 2위 자리에 있어야 한다"


def test_select_top_allows_one_card_per_type_and_side():
    # 같은 방식은 한 장까지지만, 롱과 숏은 서로 다른 설정이라 둘 다 올 수 있다.
    ev = [
        ask.Evaluated(_cand("J"), _result(30, 6, bh=0)),
        ask.Evaluated(_cand("J", side="short"), _result(28, 6, bh=0)),
        ask.Evaluated(_cand("J", symbol="ETHUSDT"), _result(26, 6, bh=0)),
    ]
    top = ask.select_top(ev, "aggressive", period="3m", interval="1h")
    assert len(top) == 2, "같은 방식 · 같은 방향은 한 장까지"
    assert {_side(e.candidate.macro) for e in top} == {"long", "short"}


def test_select_top_stays_long_only_when_no_short_qualifies():
    # 숏이 관문을 못 넘으면 자리를 비워 두지 않는다 — 억지로 끼워 넣지 않는다.
    ev = [
        ask.Evaluated(_cand("J"), _result(30, 6, bh=0)),
        ask.Evaluated(_cand("G"), _result(25, 6, bh=0)),
        ask.Evaluated(_cand("F"), _result(20, 6, bh=0)),
        ask.Evaluated(_cand("A", side="short"), _result(-5, 6, bh=0)),  # 손실 → worth_the_macro 탈락
    ]
    top = ask.select_top(ev, "aggressive", period="3m", interval="1h")
    assert len(top) == 3
    assert all(_side(e.candidate.macro) == "long" for e in top)


# --- 숏만 내는 비용(펀딩비) --------------------------------------------------

def test_shorts_carry_a_funding_cost_and_longs_do_not():
    """엔진에서 펀딩비는 숏에만 붙는 비용이다 — 0 으로 두면 숏만 면제받는다.

    그 면제가 그대로 "홀딩을 넘었나" 관문(worth_the_macro)을 숏 쪽으로 기울인다.
    그 관문이 숏 카드를 보여 주는 유일한 근거라 그냥 둘 수 없다.
    """
    plan = _plan(**FUTURES)
    preset = ask._presets_for(plan)["J"][0]
    long_macro = ask._make_macro(plan, "J", preset, ["BTCUSDT"], side="long")
    short_macro = ask._make_macro(plan, "J", preset, ["BTCUSDT"], side="short")
    assert long_macro.fees.funding_pct == 0.0
    assert short_macro.fees.funding_pct > 0.0


def test_the_funding_rate_is_prorated_to_the_bar():
    """엔진은 funding_pct 를 **봉마다** 깎는다 — 하루 요율을 그대로 넣으면 24~96배가 된다."""
    for interval, bars_per_day in (("1h", 24), ("15m", 96), ("1d", 1)):
        plan = _plan(**FUTURES, interval=interval)
        preset = ask._presets_for(plan)["J"][0]
        macro = ask._make_macro(plan, "J", preset, ["BTCUSDT"], side="short")
        daily = macro.fees.funding_pct * bars_per_day
        assert daily == pytest.approx(ask.DAILY_FUNDING_PCT, rel=1e-3), interval


def test_spot_and_domestic_never_carry_funding():
    for plan in (_plan(), _plan(exchange="upbit", symbols=["KRW-BTC"])):
        preset = ask._presets_for(plan)["J"][0]
        macro = ask._make_macro(plan, "J", preset, [plan.symbols[0]], side="long")
        assert macro.fees.funding_pct == 0.0
