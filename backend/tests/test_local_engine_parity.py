"""실행기의 로컬 판단이 서버 판단과 **같은가** (2026-10-20).

판단을 실행기로 내리는 이유는 서버 장애가 사용자의 매매 시점을 좌우하지 않게 하기
위해서다. 그런데 옮긴 판단이 서버와 조금이라도 다르면, 웹에서 본 백테스트와 실제 매매가
갈리고 사용자는 그 차이를 볼 방법이 없다. 늦게 사는 건 보이지만 다르게 사는 건 안 보인다.

그래서 여기서는 같은 매크로 · 같은 봉 · 같은 가격을 **서버 드라이버와 실행기 LocalStrategy
양쪽에** 흘려보내고, 나온 주문 명령이 글자 하나까지 같은지 본다.

실행기 쪽은 ``runner/`` 에 있으므로 이 시험은 백엔드에서 돈다 — 양쪽 코드를 한 프로세스에
올려 견주려면 여기가 유일한 자리다.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RUNNER_DIR = Path(__file__).resolve().parents[2] / "runner"
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

from engine import Macro  # noqa: E402
from engine.live_session import build_driver, command_from_fill, session_capital  # noqa: E402
from local_engine import LOCAL_ID_PREFIX, LocalStrategy, is_local_command  # noqa: E402

HOUR = 3_600_000
T0 = 1_760_000_000_000 // HOUR * HOUR


def _macro(rule_type, params, **extra):
    body = {
        "symbol": "KRW-AZIT", "exchange": "bithumb", "quote_currency": "KRW",
        "rule_type": rule_type, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"initial_capital": 40000.0, **params},
        "risk": {"invest_ratio": 1.0},
        "fees": {"commission_pct": 0.04, "slippage_pct": 0.05},
        **extra,
    }
    return Macro.model_validate(body)


# 실제 KRW-AZIT 1시간봉을 닮은 흐름 — 눌렀다가 오르고, 다시 밀린다. 규칙마다 걸리는
# 조건이 달라서(밴드 이탈 · 변동폭 돌파 · 눌림목), 여섯 규칙이 **전부 실제로 사고팔도록**
# 일부러 모양을 섞었다: 좁은 봉 뒤에 넓은 봉(돌파선이 가까워진다), 가끔 급락 · 급등(밴드를
# 찌른다). 아무것도 안 사는 데이터로는 "둘이 같다" 가 아무 뜻이 없다 — 아래
# test_여섯_규칙이_모두_실제로_사고판다 가 그걸 지킨다.
def _bars(n=90, base=2.0):
    bars = []
    price = base
    for i in range(n):
        drift = 0.985 if i < 20 else (1.04 if i < 50 else 0.965)
        price *= drift
        shock = 0.90 if i % 13 == 7 else (1.06 if i % 13 == 9 else 1.0)
        o = price * shock
        rng = 0.055 if i % 4 == 0 else 0.012
        h = o * (1 + rng)
        low = o * (1 - rng * 0.9)
        c = o * (1 + rng * 0.7) if drift > 1 and shock >= 1 else o * (1 - rng * 0.8)
        bars.append((T0 + i * HOUR, o, h, low, c, 1000.0 + i))
    return bars


MACROS = {
    "E 트레일링": _macro("E", {"entry_mode": "dip", "entry_dip": 3,
                             "activation_profit": 4, "trail_percent": 2}),
    "I 변동성돌파": _macro("I", {"k": 0.6, "exit_mode": "trailing", "trail_percent": 2.0,
                              "ma_filter_period": 20, "session_start_hour": 9}),
    "D 그리드": _macro("D", {"lower_price": 1.0, "upper_price": 4.0, "grid_count": 6}),
    "H 마틴게일": _macro("H", {"base_order_size": 8000, "safety_order_size": 6000,
                             "price_deviation": 3, "safety_order_volume_scale": 1.5,
                             "max_safety_orders": 3, "take_profit": 4}),
    "F RSI": _macro("F", {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 70}),
    "G 볼린저": _macro("G", {"bb_period": 10, "bb_std": 2.0}),
}


def _server_commands(macro, warmup, live_bars, prices):
    """서버가 하는 그대로 — 웜업 → 봉 push → 틱마다 체결 하나."""
    driver = build_driver(macro, macro.symbol)
    driver.warmup({macro.symbol: warmup})
    initial = session_capital(macro)
    out = []
    for bar, price in zip(live_bars, prices):
        driver.push_candle(macro.symbol, bar)
        while True:
            fill = driver.tick(float(price), datetime.fromtimestamp(bar[0] / 1000, timezone.utc))
            if fill is None:
                break
            out.append(command_from_fill(fill, initial))
    return out, driver


def _runner_commands(macro, warmup, live_bars, prices):
    """실행기가 하는 그대로 — 같은 순서, 같은 입력."""
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup(warmup)
    out = []
    for bar, price in zip(live_bars, prices):
        strategy.push_candles([bar])
        out += strategy.decide(price)
    return out, strategy


def _comparable(cmd):
    """id · seq · source 는 실행기가 붙이는 꼬리표 — 판단 내용만 견준다."""
    return {k: v for k, v in cmd.items() if k not in ("id", "seq", "source")}


@pytest.mark.parametrize("name", sorted(MACROS))
def test_실행기와_서버가_같은_주문_명령을_낸다(name):
    macro = MACROS[name]
    bars = _bars(90)
    warmup, live = bars[:40], bars[40:]
    prices = [bar[4] for bar in live]

    server, server_driver = _server_commands(macro, warmup, live, prices)
    runner, runner_strategy = _runner_commands(macro, warmup, live, prices)

    assert [_comparable(c) for c in runner] == [_comparable(c) for c in server], (
        f"{name}: 실행기와 서버의 판단이 갈렸다"
    )
    # 장부도 같아야 한다 — 명령만 같고 포지션이 다르면 다음 봉부터 갈린다.
    assert runner_strategy.driver.trade_count == server_driver.trade_count
    assert pytest.approx(runner_strategy.driver.equity, rel=1e-9) == server_driver.equity


@pytest.mark.parametrize("name", sorted(MACROS))
def test_재기동을_넘어도_같은_판단을_이어간다(name):
    """중간에 실행기가 꺼졌다 켜져도, 끊김 없이 돈 쪽과 같은 자리에서 사고팔아야 한다."""
    macro = MACROS[name]
    bars = _bars(90)
    warmup, live = bars[:40], bars[40:]
    prices = [bar[4] for bar in live]
    cut = len(live) // 2

    whole, _ = _runner_commands(macro, warmup, live, prices)

    first = LocalStrategy(macro, now_ms=lambda: T0)
    first.warmup(warmup)
    restarted = []
    for bar, price in zip(live[:cut], prices[:cut]):
        first.push_candles([bar])
        restarted += first.decide(price)
    saved = first.state()

    second = LocalStrategy(macro, now_ms=lambda: T0)
    second.warmup(warmup + live[:cut])      # 재기동은 과거 봉을 다시 받아 지표를 세운다
    second.restore(saved)
    for bar, price in zip(live[cut:], prices[cut:]):
        second.push_candles([bar])
        restarted += second.decide(price)

    assert [_comparable(c) for c in restarted] == [_comparable(c) for c in whole], (
        f"{name}: 재기동 뒤 판단이 갈렸다 — 전략 상태가 체크포인트를 못 넘었다"
    )


def test_로컬_명령_id_는_서버_명령_id_와_섞이지_않는다():
    """실행기는 서버 명령 id 로 '이미 했다' 를 기억한다 — 겹치면 주문이 통째로 사라진다."""
    macro = MACROS["E 트레일링"]
    bars = _bars(90)
    _, _ = _runner_commands(macro, bars[:40], bars[40:], [b[4] for b in bars[40:]])
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup(bars[:40])
    for bar in bars[40:]:
        strategy.push_candles([bar])
        for cmd in strategy.decide(bar[4]):
            assert isinstance(cmd["id"], str) and cmd["id"].startswith(LOCAL_ID_PREFIX)
            assert is_local_command(cmd)
    assert not is_local_command({"id": 12})
    assert not is_local_command({})


def test_화면에_그릴_선도_서버와_같다():
    """웹 화면과 실행기 창에 다른 선이 뜨면 어느 쪽이 실제 판단인지 아무도 모른다."""
    from engine.live_levels import strategy_levels

    macro = MACROS["E 트레일링"]
    bars = _bars(90)
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup(bars[:40])
    for bar in bars[40:]:
        strategy.push_candles([bar])
        strategy.decide(bar[4])
    entry = 2.0
    assert strategy.levels(entry) == strategy_levels(macro, strategy.state(), entry)


def test_여섯_규칙이_모두_실제로_사고판다():
    """아무것도 안 사는 데이터로는 '둘이 같다' 가 아무 뜻이 없다 — 이 보초가 그걸 막는다."""
    bars = _bars(90)
    warmup, live = bars[:40], bars[40:]
    prices = [bar[4] for bar in live]
    counts = {}
    for name, macro in MACROS.items():
        commands, _ = _runner_commands(macro, warmup, live, prices)
        counts[name] = len(commands)
    empty = [name for name, n in counts.items() if n == 0]
    assert not empty, f"이 규칙들은 한 번도 주문을 내지 않았다 — 대조가 공허하다: {empty}"
    assert sum(counts.values()) >= 20, f"거래가 너무 적다: {counts}"
    # 매수만 나오면 청산 경로가 대조되지 않는다.
    sells = sum(1 for macro in MACROS.values()
                for cmd in _runner_commands(macro, warmup, live, prices)[0]
                if cmd["action"] in ("sell", "cover"))
    assert sells >= 3, "청산 명령이 거의 없다 — 청산 비율 계산이 대조되지 않는다"


def test_봉만_넣으면_주문이_나오지_않는다():
    """봉은 '무엇을 할지' 만 정한다 — 체결은 다음 틱이다(백테스트의 다음 시가와 같은 자리)."""
    macro = MACROS["I 변동성돌파"]
    bars = _bars(90)
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup(bars[:40])
    queued = sum(strategy.push_candles([bar]) for bar in bars[40:])
    assert queued >= 1, "이 흐름이면 적어도 한 번은 신호가 서야 한다 — 시험 데이터를 확인하라"


def test_한_봉에_여러_칸이_걸리면_한_틱에_다_꺼낸다():
    """그리드가 한 봉에서 여러 칸을 지나는 일은 실제로 있다(급락).

    드라이버의 ``tick`` 은 한 번에 체결 하나만 준다. 서버는 3초마다 한 개씩 가져가지만
    실행기는 그 자리에서 다 비운다 — 한 개씩 5초를 기다리면 그 사이 가격이 달아난다.
    """
    macro = _macro("D", {"lower_price": 1.0, "upper_price": 4.0, "grid_count": 7})
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup([(T0 + i * HOUR, 3.9, 3.95, 3.85, 3.9, 100.0) for i in range(30)])
    strategy.push_candles([(T0 + 31 * HOUR, 3.9, 3.9, 1.2, 1.3, 500.0)])  # 한 봉에 급락
    commands = strategy.decide(1.3)
    assert len(commands) >= 3, f"한 틱에 {len(commands)}개만 나왔다 — 큐를 다 비우지 않는다"
    assert [c["seq"] for c in commands] == sorted(c["seq"] for c in commands), "순서가 섞였다"
    assert len({c["id"] for c in commands}) == len(commands), "같은 id 가 두 번 나왔다"


def test_상한에_걸리면_남은_것은_다음_틱에_가져간다():
    """큐가 끝없이 나오는 상황에서도 루프가 멈춰야 한다."""
    macro = _macro("D", {"lower_price": 1.0, "upper_price": 4.0, "grid_count": 7})
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.MAX_FILLS_PER_TICK = 2
    strategy.warmup([(T0 + i * HOUR, 3.9, 3.95, 3.85, 3.9, 100.0) for i in range(30)])
    strategy.push_candles([(T0 + 31 * HOUR, 3.9, 3.9, 1.2, 1.3, 500.0)])
    first = strategy.decide(1.3)
    assert len(first) == 2
    assert strategy.decide(1.3), "남은 것이 다음 틱에 나와야 한다"


def test_가격이_없으면_아무것도_하지_않는다():
    """시세 조회가 실패한 틱에 0 원으로 주문을 내면 안 된다."""
    macro = MACROS["E 트레일링"]
    strategy = LocalStrategy(macro, now_ms=lambda: T0)
    strategy.warmup(_bars(40))
    assert strategy.decide(0) == []
    assert strategy.decide(-1) == []
    assert strategy.decide(None) == []


def test_웜업_봉_수가_서버와_같다():
    """지표 초기값이 달라지면 같은 봉에서 다른 판정이 난다."""
    from engine.live_session import WARMUP_CANDLES
    assert LocalStrategy(MACROS["F RSI"]).warmup_count == WARMUP_CANDLES
