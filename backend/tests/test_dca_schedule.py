"""적립식(C)의 매수 주기는 '날' 이다 — 틱 수가 아니다 (2026-10-12).

왜 생겼나: ``interval_days`` 를 바이낸스만 **봉 수**로 셌다(``_step % interval``).
백테스트에서는 한 봉이 하루라 맞았지만, 실시간에서 한 스텝은 **3초 틱**이다. 그래서
모의 실행에서 ``interval_days: 1`` 이 '3초마다 매수' 가 됐다 — 실측으로 1분(틱 20번)에
20회 매수, 모의 자금 20만원이 사라졌다. 국내는 달력 기준이라 멀쩡했다.

실거래는 영향이 없었다(C 는 실행기가 돌리지 않는다). 모의 실행만 해당된다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import Macro
from app.engine.driver import Leg, StrategyDriver
from app.engine.stepper import make_sim

T = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _macro(exchange="binance", interval_days=1, amount=10_000, capital=1_000_000):
    symbol = "BTCUSDT" if exchange == "binance" else "KRW-BTC"
    return Macro.model_validate({
        "symbol": symbol, "exchange": exchange,
        "quote_currency": "USDT" if exchange == "binance" else "KRW",
        "rule_type": "C", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1d", "period": {"preset": "3m"},
        "params": {"amount_per_buy": amount, "interval_days": interval_days,
                   "initial_capital": capital},
        "risk": {}, "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


def _buys(sim, steps, seconds, start=T):
    """``seconds`` 간격으로 ``steps`` 번 틱을 보내고 매수 횟수를 센다."""
    count = 0
    for i in range(steps):
        fill = sim.step(100.0, start + timedelta(seconds=seconds * i))
        if fill is not None and fill.side == "buy":
            count += 1
    return count


@pytest.mark.parametrize("exchange", ["binance", "upbit"])
def test_three_second_ticks_do_not_become_daily_buys(exchange):
    """거래소와 무관하게, 1분 동안 3초 틱 스무 번은 매수 한 번이다."""
    sim = make_sim(_macro(exchange))
    assert _buys(sim, steps=20, seconds=3) == 1, "틱마다 사면 모의 자금이 몇 분 만에 사라진다"
    assert sim.cash == pytest.approx(990_000)


@pytest.mark.parametrize("exchange", ["binance", "upbit"])
def test_buys_once_a_day_over_a_live_week(exchange):
    sim = make_sim(_macro(exchange, interval_days=1))
    # 1분 간격 틱으로 이레 — 하루에 한 번씩 일곱 번이어야 한다.
    assert _buys(sim, steps=7 * 24 * 60, seconds=60) == 7


@pytest.mark.parametrize("exchange", ["binance", "upbit"])
def test_interval_days_is_honoured_in_live(exchange):
    sim = make_sim(_macro(exchange, interval_days=7))
    assert _buys(sim, steps=28 * 24, seconds=3600) == 4, "4주 동안 7일마다 = 4회"


# --- 백테스트는 그대로여야 한다 --------------------------------------------
# C 는 스키마가 일봉을 강제하므로 한 봉 = 하루다. 그래서 '봉 수' 와 '날' 이 같은 뜻이고,
# 달력 기준으로 통일해도 백테스트 결과가 바뀌지 않는다.

@pytest.mark.parametrize("interval_days", [1, 3, 7])
def test_daily_bars_still_buy_every_interval_bars(interval_days):
    sim = make_sim(_macro("binance", interval_days=interval_days))
    bought_at = []
    for i in range(30):
        fill = sim.step(100.0, T + timedelta(days=i))
        if fill is not None and fill.side == "buy":
            bought_at.append(i)
    assert bought_at == list(range(0, 30, interval_days))


@pytest.mark.parametrize("exchange", ["binance", "upbit"])
def test_without_a_timestamp_it_does_not_buy(exchange):
    """시각이 없으면 사지 않는다 — 적립식은 안 사는 쪽이 잘못 사는 쪽보다 안전하다.

    실제 경로는 전부 시각을 준다(모의 _tick_and_checkpoint 는 필수 인자, 실행기는 now(),
    백테스트는 봉 시각). 국내가 이미 이렇게 두고 있었고, 바이낸스도 같은 규칙으로 맞춘다.
    한때 '스텝 수로 되돌아가기' 를 넣어 봤는데, 3초 폴링에서 그게 바로 매 틱 매수가 된다.
    """
    sim = make_sim(_macro(exchange, interval_days=3))
    for _ in range(9):
        assert sim.step(100.0) is None
    assert sim.buys_done == 0 and sim.cash == pytest.approx(1_000_000)


# --- 재기동: 바이낸스 일정도 이제 체크포인트에 실린다 ------------------------

@pytest.mark.parametrize("exchange", ["binance", "upbit"])
def test_schedule_survives_a_restart(exchange):
    """예전에는 바이낸스 일정이 state() 에 아예 없어서 재기동이 _step 0 에서 다시 셌다."""
    macro = _macro(exchange, interval_days=7)
    initial = 1_000_000.0
    live = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                          initial, macro=macro)
    live.tick(100.0, T)                      # 첫 매수
    saved = live.state()
    leg = saved["legs"][0]
    assert leg["dca_next_buy_ms"] is not None, f"{exchange} 일정이 체크포인트에 없다"

    reborn = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                            initial, macro=macro)
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    sim = reborn.legs[0].sim
    # 복구 직후(하루 뒤)에는 아직 안 산다 — 다음 매수는 7일 뒤다.
    assert sim.step(100.0, T + timedelta(days=1)) is None, "재기동이 매수를 앞당겼다"
    assert sim.step(100.0, T + timedelta(days=7)) is not None, "7일 뒤에는 사야 한다"


def test_a_stopped_dca_stays_stopped_across_a_restart():
    macro = _macro("binance", interval_days=1)
    initial = 1_000_000.0
    live = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                          initial, macro=macro)
    live.tick(100.0, T)
    live.legs[0].sim.stopped = True
    saved = live.state()
    assert saved["legs"][0]["dca_stopped"] is True

    reborn = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                            initial, macro=macro)
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    assert reborn.legs[0].sim.stopped is True
    assert reborn.legs[0].sim.step(100.0, T + timedelta(days=30)) is None, "멈춘 적립식이 되살아났다"


def test_a_legacy_checkpoint_without_a_schedule_does_not_buy_immediately():
    """옛 state_json 에는 일정 칸이 없다 — 그때 바로 또 사면 하루치를 두 번 넣는다.

    restore() 가 '지금 + 주기' 로 미뤄 두고, 정확한 일정은 드라이버 훅이 덮는다.
    """
    macro = _macro("binance", interval_days=7)
    initial = 1_000_000.0
    live = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                          initial, macro=macro)
    live.tick(100.0, T)
    saved = live.state()
    for leg in saved["legs"]:
        leg.pop("dca_next_buy_ms", None)      # 일정을 모르는 옛 체크포인트
        leg.pop("dca_stopped", None)
    assert saved["legs"][0]["in_position"] is True

    reborn = StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)],
                            initial, macro=macro)
    reborn.restore(saved, leg_equity={}, total_equity=live.equity)
    sim = reborn.legs[0].sim
    assert sim._next_dca_buy is not None, "일정을 비워 두면 다음 틱이 바로 산다"
    assert sim.step(100.0, datetime.now(timezone.utc)) is None, "복구 직후에 또 샀다"
