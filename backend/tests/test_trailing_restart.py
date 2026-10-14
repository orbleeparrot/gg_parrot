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

    # 값이 다 있는 선에는 pending 이 서지 않는다 — 서면 화면이 멀쩡한 선을 숨긴다.
    assert flat["pending"] is False and held["pending"] is False and armed["pending"] is False

    # 즉시 진입(눌림목 아님)은 진입 목표가 없다.
    immediate = strategy_levels(_macro(entry_mode="immediate"), {"dip_ref": 100.0}, 0.0)
    assert immediate["entry_target"] is None
    assert immediate["pending"] is False, "즉시 진입은 평평할 때 그릴 선이 애초에 없다 — 기다리라고 하면 거짓이다"
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


def test_levels_say_pending_before_the_first_candle_closes():
    """첫 봉이 마감되기 전에는 "아직 없다" 고 말해야 한다 — 화면이 메우지 않게.

    실측(2026-10-12): 업비트 KRW-ICX · E · 1시간봉 세션을 11:08 에 켰다. 봉 피드는 진행 중
    봉을 배달하지 않으므로(candle_feed) 12:00 마감까지 엔진은 봉을 하나도 못 받고, 웜업 끝의
    ``reset_book()`` 이 ``dip_ref`` 를 None 으로 되돌려 둔 상태다. 그 52분 동안 서버는
    ``entry_target: None`` 을 보냈고 화면은 미리보기 '현재가 -3%' 선을 그렸다 — 가격이
    내려갈 때마다 선도 같이 내려와 "매수지점이 계속 따라내려간다" 로 읽혔다.
    """
    from app.engine.live_levels import strategy_levels
    macro = _macro()

    # 무포지션 · 눌림목 대기인데 기준가가 아직 없다.
    for state in ({}, None, {"dip_ref": None, "peak": 0.0, "armed": False}):
        out = strategy_levels(macro, state, entry_price=0.0)
        assert out["entry_target"] is None, "없는 기준가를 추측해서 내면 안 된다"
        assert out["pending"] is True, f"{state!r}: 맡고 있는 선이 없다고 말해야 화면이 안 메운다"

    # 보유 중 · 발동 뒤인데 고점을 잃었다(옛 체크포인트) — 스탑선도 기다려야 한다.
    lost_peak = strategy_levels(macro, {"armed": True, "peak": None}, entry_price=97.0)
    assert lost_peak["trail_stop"] is None and lost_peak["pending"] is True

    # E 가 아닌 규칙은 이 모듈이 맡지 않는다 — 기다리라고 하면 영원히 안 오는 선을 기다린다.
    other = Macro.model_validate({**macro.model_dump(), "rule_type": "A",
                                  "params": {"take_profit_pct": 3, "initial_capital": 1_000_000}})
    assert strategy_levels(other, {}, 0.0)["pending"] is False


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

    assert levels["pending"] is False

    # 전략 상태가 없는 옛 세션은 선을 내지 않지만 **기다리라고 말한다**. pending 이 없으면
    # 화면이 미리보기(현재가 -3%)로 메워 가격을 따라 내려오는 거짓 선을 그린다.
    row.state_json = json.dumps({"legs": [{"symbol": "KRW-CKB"}]})
    waiting = _session_view(row)["strategy_levels"]
    assert waiting["entry_target"] is None and waiting["pending"] is True
    # 깨진 state_json 으로 뷰가 터지면 실행 화면 전체가 안 열린다.
    row.state_json = "{깨짐"
    assert _session_view(row)["strategy_levels"]["pending"] is True
    # 매크로를 못 읽으면 규칙을 모른다 — 그때는 기다리라고도 못 한다(E 인지조차 모른다).
    row.macro_json = "{깨짐"
    assert _session_view(row)["strategy_levels"] == {"entry_target": None, "trail_stop": None,
                                                     "trail_activation": None, "armed": False,
                                                     "pending": False}


# ===========================================================================
#  D · H · I · K 도 같은 보호를 받는다 (2026-10-12)
# ===========================================================================
# E 와 같은 결함이 네 규칙에 더 있었다. 각 규칙이 "봉에서 다시 못 구하는" 상태를 들고 있고,
# 체크포인트에 없으면 재기동이 500봉 웜업 재생의 잔재에서 다시 시작한다.

def _m(rule, params, risk=None):
    body = {
        "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot",
        "leverage": 1, "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {**params, "initial_capital": 1_000_000}, "risk": risk or {},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    return Macro.model_validate(body)


def _drive(macro, bars, initial=1_000_000.0, tick=None):
    """봉을 밀어 넣고 마지막에 틱 한 번 — 실제 세션이 3초마다 하는 일이다.

    틱을 빼면 leg.last_price 와 driver.equity 가 초기값으로 남고, 그 0 으로 현금을
    되짚게 되어 복구 자산이 틀어진다(체크포인트는 틱이 채운 값을 싣는다).
    """
    driver = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                            initial, macro=macro)
    for bar in bars:
        driver.push_candle(macro.symbol, bar)
    driver.tick(float(tick if tick is not None else bars[-1][4]), T)
    return driver


# --- D 그리드: 어느 칸을 사 뒀는지 ----------------------------------------

def test_grid_remembers_which_levels_it_holds():
    macro = _m("D", {"lower_price": 90, "upper_price": 110, "grid_count": 4,
                     "per_grid_invest": 100_000})
    live = _drive(macro, [(0, 100, 100, 95, 96), (3_600_000, 96, 97, 92, 93)])
    sim = live.legs[0].sim
    held = dict(sim.inner.holdings)
    assert held, "칸을 하나도 안 샀으면 이 시험이 아무것도 안 본다"
    saved = live.state()
    assert saved["legs"][0]["strategy"]["grid"], "칸별 롯을 실어야 한다"
    equity_before = live.legs[0].sim.equity(93.0)

    reborn = _drive(macro, [(0, 105, 106, 104, 105)])     # 웜업 재생의 잔재
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    after = reborn.legs[0].sim
    assert set(after.inner.holdings.keys()) == set(held.keys()), "어느 칸을 사 뒀는지 잊었다"
    for level, lot in held.items():
        assert after.inner.holdings[level].qty == pytest.approx(lot.qty)
        assert after.inner.holdings[level].fill == pytest.approx(lot.fill)
    # 칸별 롯으로 다시 깔아도 자산은 그대로여야 한다(체크포인트 평단이 수량가중 평균이므로).
    assert after.equity(93.0) == pytest.approx(equity_before, rel=1e-9)


def test_grid_that_stopped_outside_the_band_stays_stopped():
    macro = _m("D", {"lower_price": 90, "upper_price": 110, "grid_count": 4,
                     "per_grid_invest": 100_000})
    live = _drive(macro, [(0, 100, 100, 95, 96), (3_600_000, 96, 120, 96, 120)])  # 밴드 위로 이탈
    assert live.legs[0].sim.inner.stopped is True
    saved = live.state()
    reborn = _drive(macro, [(0, 100, 100, 100, 100)])
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner.stopped is True, "멈춘 그리드가 재기동으로 되살아나면 안 된다"


# --- H 마틴게일: 추가매수 차수 --------------------------------------------

def test_martingale_remembers_how_many_safety_orders_are_done():
    macro = _m("H", {"base_order_size": 100_000, "safety_order_size": 100_000,
                     "price_deviation": 2, "max_safety_orders": 5, "take_profit": 2})
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 100, 94, 95)])
    sim = live.legs[0].sim
    done = sim.inner._so_done
    assert done >= 1, "추가매수가 하나도 안 들어갔으면 이 시험이 공허하다"
    saved = live.state()
    assert saved["legs"][0]["strategy"]["so_done"] == done

    reborn = _drive(macro, [(0, 200, 200, 200, 200)])
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner._so_done == done, "차수를 잊으면 채운 단계를 다시 산다"
    assert reborn.legs[0].sim.inner._base_price == pytest.approx(sim.inner._base_price)


# --- I 변동성 돌파: 다음 봉 시가 청산 예약 --------------------------------

def test_breakout_remembers_the_pending_next_open_exit():
    macro = _m("I", {"k": 0.5, "exit_mode": "next_open"})
    live = _drive(macro, [(0, 100, 104, 96, 100), (3_600_000, 100, 110, 99, 109)])
    sim = live.legs[0].sim
    assert sim.inner._exit_next_open is True, "돌파 진입 뒤 다음 봉 시가 청산이 예약돼야 한다"
    saved = live.state()
    assert saved["legs"][0]["strategy"]["exit_next_open"] is True

    reborn = _drive(macro, [(0, 100, 104, 96, 100)])
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner._exit_next_open is True, "예약을 잊으면 청산이 사라진다"
    assert reborn.legs[0].sim.inner._peak == pytest.approx(sim.inner._peak)


# --- K 하락 방어: 방향이 현금 계산을 가른다 -------------------------------

def test_sar_restores_the_short_leg_and_its_cash():
    """restore() 는 self.side 로 현금을 되짚는다(숏은 식이 다르다).

    방어 전환으로 숏이 된 세션을 LONG 으로 복구하면 **현금이 틀린 식으로** 되살아난다.
    그래서 전략 상태를 장부보다 **먼저** 돌려놓는다.
    """
    from app.engine.schema import PositionSide
    macro = _m("K", {"drop_trigger_pct": 5, "partial_exit_pct": 50, "flip_to_short": True,
                     "short_take_profit_pct": 5, "short_stop_loss_pct": 5})
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 100, 94, 94)])
    sim = live.legs[0].sim
    assert sim.inner.side is PositionSide.SHORT, "방어 전환이 안 일어났으면 이 시험이 공허하다"
    assert sim.state()["in_position"] is True
    saved = live.state()
    assert saved["legs"][0]["strategy"]["side"] == "short"
    equity_before = sim.equity(94.0)

    reborn = _drive(macro, [(0, 100, 100, 100, 100)])   # 웜업 재생은 LONG 으로 끝났다
    assert reborn.legs[0].sim.inner.side is PositionSide.LONG
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    after = reborn.legs[0].sim
    assert after.inner.side is PositionSide.SHORT, "방향을 잊으면 숏을 롱으로 들고 간다"
    assert after.inner._phase == sim.inner._phase
    assert after.inner._defended == sim.inner._defended
    # 방향이 맞아야 현금이 같은 식으로 되짚어진다.
    assert after.equity(94.0) == pytest.approx(equity_before, rel=1e-9)


# --- 네 규칙 모두: 옛 체크포인트는 예전과 같다 -----------------------------

@pytest.mark.parametrize("rule,params", [
    ("D", {"lower_price": 90, "upper_price": 110, "grid_count": 4, "per_grid_invest": 100_000}),
    ("H", {"base_order_size": 100_000, "safety_order_size": 100_000, "price_deviation": 2,
           "max_safety_orders": 5, "take_profit": 2}),
    ("I", {"k": 0.5, "exit_mode": "next_open"}),
    ("K", {"drop_trigger_pct": 5, "partial_exit_pct": 50, "flip_to_short": True,
           "short_take_profit_pct": 5, "short_stop_loss_pct": 5}),
])
def test_old_checkpoints_still_restore(rule, params):
    macro = _m(rule, params)
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 100, 94, 95)])
    saved = live.state()
    assert saved["legs"][0].get("strategy"), f"{rule} 이 전략 상태를 안 싣는다"
    for leg in saved["legs"]:
        leg.pop("strategy", None)        # v12 이전 state_json 모양
    reborn = _drive(macro, [(0, 100, 100, 100, 100)])
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)   # 터지지 않아야 한다
    assert reborn.legs[0].sim.state()["in_position"] == saved["legs"][0]["in_position"]


@pytest.mark.parametrize("rule,params", [
    ("D", {"lower_price": 90, "upper_price": 110, "grid_count": 4, "per_grid_invest": 100_000}),
    ("E", {"entry_mode": "dip", "entry_dip": 3, "activation_profit": 4, "trail_percent": 2}),
    ("H", {"base_order_size": 100_000, "safety_order_size": 100_000, "price_deviation": 2,
           "max_safety_orders": 5, "take_profit": 2}),
    ("I", {"k": 0.5, "exit_mode": "next_open"}),
    ("K", {"drop_trigger_pct": 5, "partial_exit_pct": 50, "flip_to_short": True,
           "short_take_profit_pct": 5, "short_stop_loss_pct": 5}),
])
def test_partial_strategy_block_keeps_the_current_values(rule, params):
    """칸이 일부만 있거나 ``None`` 인 체크포인트는 '모름' 이다 — 0 으로 지우면 안 된다.

    훅의 약속이 그것이고(옛 state_json 호환), 지우면 잊는 것과 같아진다.
    앞 버전이 쓴 전략 블록에 새로 더한 칸이 없을 때가 이 경우다.
    """
    macro = _m(rule, params)
    sim = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 100, 94, 95)]).legs[0].sim
    before = sim.strategy_state()
    assert before, f"{rule} 이 전략 상태를 안 낸다"

    sim.restore_strategy({})                                   # 빈 블록
    assert sim.strategy_state() == before, "빈 블록이 값을 지웠다"
    sim.restore_strategy({key: None for key in before})        # 전부 모름
    assert sim.strategy_state() == before, "None 이 값을 지웠다"
    for key in before:                                         # 한 칸씩 빠진 블록
        partial = {k: v for k, v in before.items() if k != key}
        sim.restore_strategy(partial)
        assert sim.strategy_state() == before, f"'{key}' 가 없을 때 다른 칸이 지워졌다"


# ===========================================================================
#  F · G · J 의 '다음 봉 시가 실행' 의도 (2026-10-12)
# ===========================================================================
# 지표형 셋은 마감봉에서 의도(_pending)를 정하고 다음 봉 시가에 실행한다. 지표 자체는
# 웜업이 과거 봉으로 다시 만들지만, **의도는 포지션 여부로 갈린다**(보유 중이면 "exit",
# 비었으면 "enter"). 그래서 재생의 포지션이 실제와 다르면 의도가 통째로 뒤바뀐다.
#
# execute_pending 이 양쪽을 막아 주므로(exit 는 보유 중일 때만, enter 는 무포지션일 때만)
# 중복 매수나 없는 물량 매도는 안 난다. 대신 **정해 둔 청산이 조용히 사라진다.**

_INDICATOR_RULES = [
    ("F", {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70}),
    ("G", {"bb_period": 2, "bb_std": 0.5, "strategy": "reversion", "exit_target": "mid"}),
    ("J", {"ma_type": "SMA", "fast_period": 2, "slow_period": 3}),
]


@pytest.mark.parametrize("rule,params", _INDICATOR_RULES)
def test_indicator_sims_carry_the_pending_intent(rule, params):
    macro = _m(rule, params)
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 101, 99, 101)])
    sim = live.legs[0].sim
    # 의도를 직접 세운다 — 어떤 지표 조합이든 "마감봉에서 정하고 아직 실행 안 한" 상태다.
    sim.inner._pending = "exit"
    sim.inner._signal_note = "시험용 근거"
    saved = live.state()
    assert saved["legs"][0]["strategy"]["pending"] == "exit", f"{rule} 이 의도를 안 싣는다"

    reborn = _drive(macro, [(0, 100, 100, 100, 100)])
    reborn.legs[0].sim.inner._pending = "enter"        # 재생이 남긴 반대 의도
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner._pending == "exit", "재생의 의도가 실제 의도를 덮었다"
    assert reborn.legs[0].sim.inner._signal_note == "시험용 근거"


@pytest.mark.parametrize("rule,params", _INDICATOR_RULES)
def test_pending_none_is_carried_too(rule, params):
    """'의도 없음' 도 상태다 — 재생이 남긴 의도를 그대로 두면 없던 주문이 생긴다."""
    macro = _m(rule, params)
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 101, 99, 101)])
    live.legs[0].sim.inner._pending = None
    saved = live.state()
    assert saved["legs"][0]["strategy"]["pending"] is None

    reborn = _drive(macro, [(0, 100, 100, 100, 100)])
    reborn.legs[0].sim.inner._pending = "enter"
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner._pending is None, "없던 의도가 재생에서 살아났다"


def test_a_decided_exit_survives_the_restart_and_actually_fires():
    """핵심 회귀 — 보유 중에 정해 둔 청산이 재기동에서 사라지면 안 된다."""
    macro = _m("J", {"ma_type": "SMA", "fast_period": 2, "slow_period": 3})
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 101, 102, 101, 103),
                          (7_200_000, 104, 106, 104, 106), (10_800_000, 107, 108, 106, 107)])
    sim = live.legs[0].sim
    assert sim.state()["in_position"] is True, "진입이 안 됐으면 이 시험이 공허하다"
    sim.inner._pending = "exit"          # 마감봉에서 정했고 아직 시가 실행 전
    saved = live.state()

    reborn = _drive(macro, [(0, 100, 100, 100, 100)])   # 재생은 평평하게 끝났다
    assert reborn.legs[0].sim.state()["in_position"] is False
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    after = reborn.legs[0].sim
    assert after.state()["in_position"] is True
    assert after.inner._pending == "exit"
    # 다음 봉이 오면 시가에 청산된다 — 의도를 잊었으면 그대로 들고 간다.
    assert reborn.push_candle(macro.symbol, (14_400_000, 107, 107, 106, 106)) >= 1
    assert after.state()["in_position"] is False, "정해 둔 청산이 사라졌다"


@pytest.mark.parametrize("rule,params", _INDICATOR_RULES)
def test_indicator_old_checkpoints_still_restore(rule, params):
    macro = _m(rule, params)
    live = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 101, 99, 101)])
    saved = live.state()
    assert saved["legs"][0].get("strategy"), f"{rule} 이 전략 상태를 안 싣는다"
    for leg in saved["legs"]:
        leg.pop("strategy", None)
    reborn = _drive(macro, [(0, 100, 100, 100, 100)])
    reborn.legs[0].sim.inner._pending = "enter"
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.inner._pending == "enter", "옛 체크포인트는 예전처럼 웜업 값을 쓴다"


@pytest.mark.parametrize("rule,params", _INDICATOR_RULES)
def test_unknown_pending_value_becomes_no_intent(rule, params):
    """state_json 은 버전을 넘어 살아남는 데이터다 — 모르는 의도는 '없음' 으로 읽는다.

    그대로 담아 두면 다음에 의도가 하나 더 생겼을 때 옛 서버가 모르는 값을 들고 돈다.
    """
    macro = _m(rule, params)
    sim = _drive(macro, [(0, 100, 100, 100, 100), (3_600_000, 100, 101, 99, 101)]).legs[0].sim
    for junk in ("sell_everything", "", 0, [], {"a": 1}):
        sim.inner._pending = "exit"
        sim.restore_strategy({"pending": junk})
        assert sim.inner._pending is None, f"{junk!r} 를 의도로 들고 있다"
    # 아는 값 둘은 그대로 들어간다.
    for good in ("enter", "exit"):
        sim.restore_strategy({"pending": good})
        assert sim.inner._pending == good
