"""E 트레일링 스탑의 전략 상태가 재기동을 넘어간다 (2026-10-12).

왜 생겼나: 업비트 KRW-CKB · E · 1시간봉 실거래 중에 "안 사진다" 는 제보가 왔다. 화면의
'진입 목표 -3%' 선이 **현재가 기준**으로 그려져 가격을 따라 내려오고 있었고(엔진은 고정된
기준가를 본다), 조사하다 더 큰 것이 나왔다 — 체크포인트에 ``_ref``(눌림목 기준가)·
``_peak``(고점)·``_armed``(트레일 발동)이 **없었다.**

그래서 포지션을 들고 있는 중에 서버가 재배포되면, 복구는 수량·평단만 되살리고 트레일링은
500봉 웜업 재생이 남긴 값에서 다시 시작했다. 재생이 평평하게 끝났으면 ``_peak=0``·
``_armed=False`` 라서, 복원된 평단 기준으로 **다시 +활성화% 를 찍어야** 트레일이 켜진다.
그 사이 트레일링은 아무것도 지키지 않는다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import Macro
from app.engine.candles import TrailingSim, make_candle_sim
from app.engine.driver import Leg, StrategyDriver
from app.engine.stepper import make_sim

T = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _macro(**params):
    body = {
        "symbol": "KRW-CKB", "exchange": "upbit", "quote_currency": "KRW",
        "rule_type": "E", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"entry_mode": "dip", "entry_dip": 3, "activation_profit": 4,
                   "trail_percent": 2, "initial_capital": 1_000_000, **params},
        "risk": {},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    return Macro.model_validate(body)


def _bar(sim, o, h, l, c, minutes=0):
    sim.on_candle(o, h, l, c, T + timedelta(minutes=minutes))


# --- 1. 기준가는 고정이다(화면이 틀렸을 뿐) --------------------------------

def test_dip_reference_does_not_follow_the_price_down():
    """화면은 '현재가 -3%' 를 그렸지만 엔진의 문턱은 평평해진 순간 잡히고 안 움직인다."""
    sim = make_candle_sim(_macro(), initial_capital=1_000_000)
    _bar(sim, 100, 100, 99, 99)          # 기준가 = 첫 평평한 봉의 시가 100 → 목표 97
    assert sim.strategy_state()["dip_ref"] == pytest.approx(100.0)
    for i, price in enumerate((98, 97.5, 97.2), start=1):
        _bar(sim, price, price, price, price, minutes=i)
        assert sim.strategy_state()["dip_ref"] == pytest.approx(100.0), "기준가가 가격을 따라갔다"
    assert not sim.in_position(), "97 에 안 닿았으면 아직 안 산다"
    # 97 에 닿으면 산다 — 선이 따라 내려오면 영원히 못 닿는다.
    _bar(sim, 97.2, 97.2, 96.9, 97.0, minutes=4)
    assert sim.in_position(), "고정 문턱 97 을 깼으면 사야 한다"


# --- 2. 전략 상태가 체크포인트를 타고 넘어간다 ------------------------------

def test_strategy_state_round_trips_through_the_checkpoint():
    sim = make_candle_sim(_macro(), initial_capital=1_000_000)
    _bar(sim, 100, 100, 96, 97)           # 기준가 100 → 97 에 진입
    assert sim.in_position()
    _bar(sim, 97, 106, 97, 105, minutes=1)  # 고점 106, 평단 97 → +4% 넘겨 발동
    state = sim.strategy_state()
    assert state["armed"] is True and state["peak"] == pytest.approx(106.0)

    fresh = make_candle_sim(_macro(), initial_capital=1_000_000)
    fresh.restore_strategy(state)
    assert fresh.strategy_state() == state


def test_restore_strategy_keeps_warmup_values_when_the_checkpoint_is_old():
    """옛 state_json 에는 이 칸이 없다 — 그때는 예전처럼 웜업이 남긴 값을 쓴다."""
    sim = make_candle_sim(_macro(), initial_capital=1_000_000)
    _bar(sim, 100, 100, 96, 97)
    _bar(sim, 97, 106, 97, 105, minutes=1)
    before = sim.strategy_state()
    sim.restore_strategy({})                    # 키가 없는 옛 체크포인트
    assert sim.strategy_state() == before, "없는 칸을 0 으로 지우면 안 된다"
    sim.restore_strategy({"peak": None, "armed": None, "dip_ref": None})
    assert sim.strategy_state()["peak"] == pytest.approx(before["peak"]), "None 도 '모름' 이다"
    assert sim.strategy_state()["armed"] == before["armed"]


# --- 3. 드라이버가 실어 보내고 되살린다 ------------------------------------

def _driver(macro, initial=1_000_000.0):
    # make_sim 은 실시간 래퍼(LiveCandleSim)를 씌운다 — 실제 세션이 쓰는 모양이고,
    # 래퍼가 strategy_state 를 안 넘기면 여기서 바로 드러난다.
    return StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                          initial, macro=macro)


def test_driver_state_carries_the_strategy_block():
    macro = _macro()
    driver = _driver(macro)
    driver.push_candle(macro.symbol, (0, 100, 100, 96, 97))      # 기준가 100 → 97 진입
    # 고점 106 으로 발동(평단 97 의 +4% = 100.88 돌파). 저점 104 는 스탑선 103.88 을 안 깬다 —
    # 한 봉 안에서 고점과 저점을 다 보므로 저점을 낮게 두면 같은 봉에서 바로 청산된다.
    driver.push_candle(macro.symbol, (3_600_000, 97, 106, 104, 105))
    leg = driver.state()["legs"][0]
    assert leg["in_position"] is True, "같은 봉에서 청산됐으면 이 시험이 아무것도 안 본다"
    assert leg["strategy"]["armed"] is True
    assert leg["strategy"]["peak"] == pytest.approx(106.0)


def test_trailing_survives_a_restart_and_still_protects():
    """핵심 회귀 — 재기동 뒤에도 고점과 발동 상태를 기억해야 트레일이 지켜 준다."""
    macro = _macro()
    live = _driver(macro)
    live.push_candle(macro.symbol, (0, 100, 100, 96, 97))             # 97 진입
    live.push_candle(macro.symbol, (3_600_000, 97, 106, 104, 105))   # 고점 106, 발동(저점은 안 깸)
    saved = live.state()
    assert saved["in_position"] is True

    # 재배포: 새 프로세스 → 웜업(여기서는 재생이 평평하게 끝난 모습) → restore.
    reborn = _driver(macro)
    reborn.push_candle(macro.symbol, (0, 200, 200, 200, 200))  # 웜업 재생의 잔재(기준가 200)
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    sim = reborn.legs[0].sim
    assert sim.state()["in_position"] is True, "포지션은 예전부터 되살았다"
    assert sim.strategy_state()["armed"] is True, "발동 상태를 잊으면 트레일이 꺼진 채로 돈다"
    assert sim.strategy_state()["peak"] == pytest.approx(106.0), "고점을 잊으면 스탑선이 내려앉는다"

    # 고점 106 · 트레일 2% → 103.88 로 내려오면 청산해야 한다.
    fills = reborn.push_candle(macro.symbol, (7_200_000, 105, 105, 103.0, 103.5))
    assert fills == 1, "재기동 뒤에도 트레일링이 이익을 지켜야 한다"
    assert sim.state()["in_position"] is False


def test_restart_without_the_strategy_block_is_the_old_behaviour():
    """옛 체크포인트로 복구하면 예전과 같다 — 고치면서 옛 세션을 깨지 않는다."""
    macro = _macro()
    live = _driver(macro)
    live.push_candle(macro.symbol, (0, 100, 100, 96, 97))
    live.push_candle(macro.symbol, (3_600_000, 97, 106, 104, 105))
    saved = live.state()
    for leg in saved["legs"]:
        leg.pop("strategy", None)          # v10 이전 state_json 모양

    reborn = _driver(macro)
    reborn.push_candle(macro.symbol, (0, 200, 200, 200, 200))
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.state()["in_position"] is True, "포지션 복구는 그대로 되어야 한다"


# --- 4. 화면에 줄 가격선은 서버가 한 곳에서 계산한다 ------------------------

def test_strategy_levels_for_the_screen():
    from app.engine.live_levels import strategy_levels
    macro = _macro()

    # 무포지션 · 눌림목 대기 — 진입 목표는 **고정 기준가**의 -3% 다(현재가와 무관).
    flat = strategy_levels(macro, {"dip_ref": 100.0, "peak": 0.0, "armed": False}, entry_price=0.0)
    assert flat["entry_target"] == pytest.approx(97.0)
    assert flat["trail_stop"] is None and flat["trail_activation"] is None
    assert flat["armed"] is False, "평평한 세션에 '발동됨' 을 내보내면 화면이 거짓을 말한다"
    # 청산 뒤에도 심에는 armed·peak 가 남는다(다음 진입이 덮는다) — 그걸 그대로 흘리면 안 된다.
    stale = strategy_levels(macro, {"dip_ref": 100.0, "peak": 106.0, "armed": True}, entry_price=0.0)
    assert stale["armed"] is False and stale["trail_stop"] is None

    # 보유 중 · 아직 발동 전 — 평단 97 의 +4% 를 찍어야 트레일이 켜진다.
    held = strategy_levels(macro, {"dip_ref": None, "peak": 99.0, "armed": False}, entry_price=97.0)
    assert held["entry_target"] is None, "들고 있으면 진입 목표는 없다"
    assert held["trail_activation"] == pytest.approx(97.0 * 1.04)
    assert held["trail_stop"] is None, "발동 전에는 스탑선이 없다"

    # 보유 중 · 발동 뒤 — 고점 106 의 -2%.
    armed = strategy_levels(macro, {"dip_ref": None, "peak": 106.0, "armed": True}, entry_price=97.0)
    assert armed["trail_stop"] == pytest.approx(106.0 * 0.98)
    assert armed["armed"] is True

    # 즉시 진입(눌림목 아님)은 진입 목표가 없다.
    assert strategy_levels(_macro(entry_mode="immediate"), {"dip_ref": 100.0}, 0.0)["entry_target"] is None
    # 상태가 없으면(옛 세션) 아무 선도 그리지 않는다 — 추측해서 그리면 거짓이 된다.
    assert strategy_levels(macro, {}, 0.0) == {"entry_target": None, "trail_stop": None,
                                               "trail_activation": None, "armed": False}
    assert strategy_levels(macro, None, 0.0)["entry_target"] is None
    # E 가 아닌 규칙은 이 함수가 선을 내지 않는다.
    #
    # A·B·C 는 필요한 키만 검사하고 **남은 키를 그대로 보관한다** — 그래서 A 매크로가
    # entry_mode="dip" 을 들고 있을 수 있다(빌더에서 E 로 만들다 A 로 바꾼 판 등).
    # 규칙 검사를 빼면 그 매크로에 눌림목 진입선을 그려 준다. A 는 눌림목으로 안 산다.
    a = Macro.model_validate({**macro.model_dump(), "rule_type": "A",
                              "params": {"take_profit_pct": 3, "initial_capital": 1_000_000,
                                         "entry_mode": "dip", "entry_dip": 3}})
    assert a.params["entry_mode"] == "dip", "A 가 남은 키를 버리면 이 시험이 공허하다"
    assert strategy_levels(a, {"dip_ref": 100.0, "peak": 0.0, "armed": False},
                           entry_price=0.0)["entry_target"] is None


# --- 5. 세션 뷰가 실제 가격선을 실어 보낸다 --------------------------------

def test_session_view_carries_the_real_entry_target():
    """화면이 추측하지 않도록 서버가 계산해 보낸다 — 현재가와 무관한 고정 기준가의 -3%."""
    import json

    from app.db import RunSession
    from app.runner import _session_view

    macro = _macro()
    row = RunSession(
        user_id=1, symbol="KRW-CKB", position_side="long", leverage=1, market="spot",
        testnet=False, status="running", macro_json=json.dumps(macro.model_dump()),
        human_summary="", started_at="2026-10-12T07:00:00Z",
        in_position=False, entry_price=0.0, position_qty=0.0, last_price=2.73,
        state_json=json.dumps({"legs": [{"symbol": "KRW-CKB", "strategy":
                                         {"dip_ref": 2.81, "peak": 0.0, "armed": False}}]}),
    )
    levels = _session_view(row)["strategy_levels"]
    # 2.81 × 0.97 = 2.7257 — 가격이 2.73 까지 내려와도 목표는 그 자리에 있다.
    assert levels["entry_target"] == pytest.approx(2.81 * 0.97)
    assert levels["armed"] is False and levels["trail_stop"] is None

    # 전략 상태가 없는 옛 세션은 아무 선도 내지 않는다.
    row.state_json = json.dumps({"legs": [{"symbol": "KRW-CKB"}]})
    assert _session_view(row)["strategy_levels"]["entry_target"] is None
    # 깨진 state_json 으로 뷰가 터지면 실행 화면 전체가 안 열린다.
    row.state_json = "{깨짐"
    assert _session_view(row)["strategy_levels"]["entry_target"] is None
    row.macro_json = "{깨짐"
    assert _session_view(row)["strategy_levels"] == {"entry_target": None, "trail_stop": None,
                                                     "trail_activation": None, "armed": False}
