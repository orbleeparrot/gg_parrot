"""엔진의 판단을 글로 고정한다 — 다른 언어로 옮겨도 같은 자리에서 사고팔게 (2026-10-20).

실행기는 Tauri(Rust) 앱이다(``orbleeparrot/ggparrot-desktop``). 판단을 실행기로 내리면
엔진이 **두 언어로** 존재하게 되고, 그러면 "같은 코드를 쓴다" 는 말이 성립하지 않는다.
대신 할 수 있는 것은 하나다 — **같은 입력에 같은 답을 내는지 글로 박아 두고 양쪽을 재는 것.**

이 시험이 만드는 ``fixtures/engine_golden.json`` 이 그 계약이다:

* 여기(파이썬)에서는 지금 엔진이 그 답을 그대로 내는지 본다. 어긋나면 **판단이 바뀐 것**이다 —
  의도한 변경이면 golden 을 다시 만들고 ``ENGINE_VERSION`` 을 올린다(engine/version.py).
* Rust 쪽에서는 같은 파일을 읽어 자기 엔진에 같은 봉을 흘려보내고 같은 답이 나오는지 본다.

봉은 **일부러 섞어 만들었다** — 좁은 봉 뒤에 넓은 봉(돌파선이 가까워진다), 가끔 급락 · 급등
(밴드를 찌른다). 여섯 규칙이 전부 실제로 사고팔지 않으면 "둘이 같다" 가 아무 뜻이 없다.
``test_여섯_규칙이_모두_실제로_사고판다`` 가 그 사실 자체를 지킨다.

golden 을 다시 만들려면::

    cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_engine_golden.py --regolden
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine import Macro
from engine.live_session import build_driver, command_from_fill, session_capital

HOUR = 3_600_000
T0 = 1_760_000_000_000 // HOUR * HOUR
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "engine_golden.json"


def _macro(rule_type, params, **overrides):
    """계약에 쓰는 매크로 하나. 기본은 빗썸 KRW 현물 · 롱 · 1시간봉.

    ``overrides`` 는 그 규칙이 다른 거래소 · 시장을 요구할 때만 쓴다 —
    C(적립식)는 날짜를 세므로 일봉, K(SAR)는 숏 전환이라 바이낸스 선물.
    """
    body = {
        "symbol": "KRW-AZIT", "exchange": "bithumb", "quote_currency": "KRW",
        "rule_type": rule_type, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"initial_capital": 40000.0, **params},
        "risk": {"invest_ratio": 1.0},
        "fees": {"commission_pct": 0.04, "slippage_pct": 0.05},
    }
    body.update(overrides)
    return Macro.model_validate(body)


def bars(n=90, base=2.0):
    """고정된 봉 묶음. 값이 바뀌면 golden 도 전부 바뀐다 — 손대지 말 것."""
    out = []
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
        out.append([T0 + i * HOUR, o, h, low, c, 1000.0 + i])
    return out


#: 규칙마다 하나씩 — 실행기가 돌려야 할 **열한 가지 전부**(A~K).
#: 이름은 golden 파일의 키라 바꾸면 계약이 깨진다.
#:
#: 판단 시점이 둘로 갈린다. A · B · C 는 틱 기반(현재가를 볼 때마다 판단)이고 나머지는
#: 봉 기반(봉이 닫힐 때만 판단)이다. 계약은 둘 다 같은 자리에서 잰다 — 봉을 넣고 그 봉의
#: 종가로 틱을 돌린다. 서버 실시간도 정확히 그렇게 돈다.
CASES = {
    "A_tp_sl": _macro("A", {"take_profit_pct": 5.0}),
    "B_band": _macro("B", {"buy_price": 4.0, "sell_price": 4.8}),
    # 적립식은 '며칠마다' 를 세므로 일봉 매크로만 만들 수 있다(서버 스키마가 막는다).
    "C_dca": _macro("C", {"amount_per_buy": 5000.0, "interval_days": 1}, candle_interval="1d"),
    # 간격이 1 이면 곱셈이 항등이라 '며칠마다' 가 대조되지 않는다.
    "C_dca_every_2d": _macro(
        "C", {"amount_per_buy": 5000.0, "interval_days": 2}, candle_interval="1d"
    ),
    # 가격대를 벗어나 전부 파는 경우(band exit).
    "D_grid": _macro("D", {"lower_price": 1.0, "upper_price": 4.0, "grid_count": 6}),
    # 가격대 **안에서** 칸별로 사고파는 경우. 위 D_grid 는 밴드 이탈로만 팔아서 칸별
    # 매도가 한 번도 대조되지 않았다 — 실제로 변이가 살아남았다.
    "D_grid_inband": _macro(
        "D", {"lower_price": 0.8, "upper_price": 5.5, "grid_count": 15}
    ),
    "E_trailing": _macro("E", {"entry_mode": "dip", "entry_dip": 3,
                               "activation_profit": 4, "trail_percent": 2}),
    "F_rsi": _macro("F", {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 70}),
    "G_bollinger": _macro("G", {"bb_period": 10, "bb_std": 2.0}),
    # 수축 필터는 이 봉에서 **모든 진입을 막는다** — 그게 이 경우의 요점이다. 위
    # G_bollinger(같은 밴드, 필터 없음)가 한 번 사므로 "필터가 실제로 막았다" 가 증명된다.
    # 이름의 `_blocked` 가 "거래가 없어야 정상" 이라는 표시다.
    "G_bollinger_squeeze_blocked": _macro(
        "G", {"bb_period": 10, "bb_std": 2.0, "squeeze_filter": True, "squeeze_lookback": 3}
    ),
    "H_martingale": _macro("H", {"base_order_size": 8000, "safety_order_size": 6000,
                                 "price_deviation": 3, "safety_order_volume_scale": 1.5,
                                 "max_safety_orders": 3, "take_profit": 4}),
    "I_breakout": _macro("I", {"k": 0.6, "exit_mode": "trailing", "trail_percent": 2.0,
                               "ma_filter_period": 20, "session_start_hour": 9}),
    "J_ma_cross": _macro("J", {"fast_period": 5, "slow_period": 20}),
    "J_ma_cross_confirm": _macro(
        "J", {"fast_period": 5, "slow_period": 20, "ma_type": "EMA", "confirm_candles": 3}
    ),
    # 연속 확인(confirm_candles > 1)을 쓰는 경우. 나머지가 전부 1 이라 이것이 없으면
    # "몇 봉 연속이어야 믿는다" 는 셈이 대조되지 않는다 — 실제로 변이가 살아남았다.
    "F_rsi_confirm": _macro(
        "F", {"rsi_period": 7, "entry_threshold": 35, "exit_threshold": 65, "confirm_candles": 3}
    ),
    # --- 숏 매크로 --------------------------------------------------------
    # 숏은 조건이 거울처럼 뒤집힌다. 롱만 담으면 그 거울이 한 번도 대조되지 않는다
    # (실제로 변이가 살아남았다). 숏은 바이낸스 선물에서만 된다.
    "A_short": _macro(
        "A", {"take_profit_pct": 4.0}, symbol="BTCUSDT", exchange="binance",
        quote_currency="USDT", market="futures", position_side="short",
        risk={"invest_ratio": 1.0, "stop_loss_pct": 6.0},
    ),
    "F_rsi_short": _macro(
        "F", {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 60, "confirm_candles": 2},
        symbol="BTCUSDT", exchange="binance", quote_currency="USDT", market="futures",
        position_side="short",
    ),
    "J_ma_cross_short": _macro(
        "J", {"fast_period": 5, "slow_period": 20, "confirm_candles": 2},
        symbol="BTCUSDT", exchange="binance", quote_currency="USDT", market="futures",
        position_side="short",
    ),
    # SAR 은 롱 → 부분 청산 → 숏 전환이라 숏이 되는 곳에서만 성립한다(국내는 현물 롱 1배뿐).
    "K_sar": _macro(
        "K",
        {"drop_trigger_pct": 8, "partial_exit_pct": 50, "flip_to_short": True,
         "short_take_profit_pct": 5, "short_stop_loss_pct": 4},
        symbol="BTCUSDT", exchange="binance", quote_currency="USDT", market="futures",
    ),
}
WARMUP = 40


def _round(value, digits=10):
    return float(f"{float(value):.{digits}g}")


def run_case(macro, warmup, live):
    """웜업 → 봉마다 (push → 체결을 다 꺼냄). 주문 명령 목록과 끝 장부를 돌려준다.

    실행기도 **이 순서 그대로** 돌아야 한다: 봉은 '무엇을 할지' 만 정하고, 체결은 그 다음
    가격 틱에 난다(백테스트의 "마감에 판정하고 다음 시가에 체결" 과 같은 자리).
    """
    driver = build_driver(macro, macro.symbol)
    driver.warmup({macro.symbol: [tuple(bar) for bar in warmup]})
    initial = session_capital(macro)
    commands = []
    for bar in live:
        driver.push_candle(macro.symbol, tuple(bar))
        price = float(bar[4])
        ts = datetime.fromtimestamp(bar[0] / 1000, timezone.utc)
        while True:
            fill = driver.tick(price, ts)
            if fill is None:
                break
            cmd = command_from_fill(fill, initial)
            commands.append({
                "candle_t": int(bar[0]),
                "action": cmd["action"],
                "notional_frac": _round(cmd["notional_frac"]),
                "qty_frac": _round(cmd["qty_frac"]),
                "signal_price": _round(cmd["signal_price"]),
                "reason": cmd["reason"],
            })
    return {
        "commands": commands,
        "trade_count": int(driver.trade_count),
        "equity": _round(driver.equity),
    }


def build_golden():
    rows = bars()
    warmup, live = rows[:WARMUP], rows[WARMUP:]
    from engine.version import ENGINE_VERSION
    return {
        "note": "엔진의 판단 계약. Rust 실행기가 같은 봉으로 같은 답을 내야 한다.",
        "engine_version": ENGINE_VERSION,
        "t0_ms": T0,
        "interval": "1h",
        "warmup_count": WARMUP,
        "candles": rows,
        "cases": {
            name: {"macro": json.loads(macro.model_dump_json()), **run_case(macro, warmup, live)}
            for name, macro in CASES.items()
        },
    }


@pytest.fixture(scope="module")
def golden():
    if not GOLDEN.exists():
        pytest.fail(f"golden 파일이 없다: {GOLDEN} — --regolden 으로 만들어라")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_을_다시_만든다(request):
    """``--regolden`` 일 때만 쓴다. 평소에는 건너뛴다."""
    if not request.config.getoption("--regolden", default=False):
        pytest.skip("--regolden 없이는 golden 을 덮지 않는다")
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(build_golden(), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def test_봉이_그대로다(golden):
    """봉이 바뀌면 모든 답이 바뀐다 — 그건 계약을 새로 쓴 것이지 시험 통과가 아니다."""
    assert golden["candles"] == bars()
    assert golden["warmup_count"] == WARMUP


@pytest.mark.parametrize("name", sorted(CASES))
def test_엔진이_적어_둔_답을_그대로_낸다(golden, name):
    rows = bars()
    got = run_case(CASES[name], rows[:WARMUP], rows[WARMUP:])
    want = golden["cases"][name]
    assert got["commands"] == want["commands"], (
        f"{name}: 판단이 바뀌었다. 의도한 변경이면 --regolden 으로 다시 만들고 "
        "ENGINE_VERSION 을 올려라(engine/version.py)."
    )
    assert got["trade_count"] == want["trade_count"]
    assert got["equity"] == pytest.approx(want["equity"], rel=1e-9)


@pytest.mark.parametrize("name", sorted(CASES))
def test_매크로_설정도_그대로다(golden, name):
    """같은 봉이라도 설정이 다르면 다른 답이 나온다 — Rust 는 이 설정을 그대로 읽어야 한다."""
    assert json.loads(CASES[name].model_dump_json()) == golden["cases"][name]["macro"]


def test_모든_경우가_실제로_사고판다(golden):
    """아무것도 안 사는 계약은 아무것도 보장하지 않는다.

    예외는 이름이 ``_blocked`` 로 끝나는 경우뿐이다 — 거래가 **없는 것**이 그 경우의
    요점이고, 짝이 되는 경우가 거래하는 것을 아래 시험이 따로 못 박는다.
    """
    counts = {name: len(case["commands"]) for name, case in golden["cases"].items()}
    empty = [name for name, n in counts.items() if n == 0 and not name.endswith("_blocked")]
    assert not empty, f"이 규칙들은 한 번도 주문을 내지 않았다 — 계약이 공허하다: {empty}"
    assert sum(counts.values()) >= 20, f"거래가 너무 적다: {counts}"
    sells = sum(1 for case in golden["cases"].values()
                for cmd in case["commands"] if cmd["action"] in ("sell", "cover"))
    assert sells >= 3, "청산 명령이 거의 없다 — 청산 비율 계산이 대조되지 않는다"


def test_막는_설정은_실제로_막는다(golden):
    """필터를 켠 판과 끈 판을 짝으로 둔다 — 대조가 있어야 '막았다' 가 뜻을 갖는다."""
    blocked = golden["cases"]["G_bollinger_squeeze_blocked"]
    allowed = golden["cases"]["G_bollinger"]
    assert blocked["commands"] == [], "수축 필터가 막지 못했다"
    assert allowed["commands"], "필터 없는 짝이 거래하지 않으면 대조가 공허하다"
    only_filter = {
        k: v for k, v in blocked["macro"]["params"].items()
        if k not in ("squeeze_filter", "squeeze_lookback")
    }
    assert only_filter == {
        k: v for k, v in allowed["macro"]["params"].items()
        if k not in ("squeeze_filter", "squeeze_lookback")
    }, "필터 말고 다른 설정이 다르면 무엇이 막았는지 알 수 없다"


def test_숏_매크로가_계약에_있다(golden):
    """숏은 조건이 거울처럼 뒤집힌다 — 롱만 담으면 그 거울이 대조되지 않는다."""
    shorts = [
        name for name, case in golden["cases"].items()
        if case["macro"]["position_side"] == "short"
    ]
    assert len(shorts) >= 3, f"숏 경우가 너무 적다: {shorts}"
    sides = {
        cmd["action"] for name in shorts for cmd in golden["cases"][name]["commands"]
    }
    assert {"short", "cover"} <= sides, f"숏 진입 · 청산이 둘 다 나와야 한다: {sides}"


def test_계약이_엔진_세대와_함께_간다(golden):
    """golden 을 다시 만들었는데 세대를 안 올리면, 옛 실행기가 조용히 다른 답을 낸다."""
    from engine.version import ENGINE_VERSION
    assert golden.get("engine_version") == ENGINE_VERSION, (
        "golden 의 engine_version 과 지금 세대가 다르다 — 둘은 함께 움직여야 한다"
    )
