"""묶음 검증 — 창을 레그마다 같은 시각으로 자르고, 한도가 있으면 창마다 동기 루프를 돈다."""
import pandas as pd
import pytest

from app.engine import walkforward as wf
from app.engine.schema import Macro

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def frame(closes, start="2026-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1h")
    return pd.DataFrame({"timestamp": ts, "open": closes,
                         "high": [c * 1.03 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)})


LONG = list(range(100, 116))


def test_bundle_windows_returns_one_row_per_window():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    rows = wf.run_bundle_windows(m, frames, 4)
    assert [r["index"] for r in rows] == [1, 2, 3, 4]
    assert all(r["start"] and r["end"] for r in rows)
    assert all(r["error"] == "" for r in rows)


def test_bundle_windows_return_is_the_whole_bundle_not_one_leg():
    """창 수익률은 묶음 합산이어야 한다 — 한 레그만 보면 한도의 효과가 안 보인다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    capped = wf.run_bundle_windows(m, frames, 2)
    free = wf.run_bundle_windows(
        m.model_copy(update={"bundle_risk": m.bundle_risk.model_copy(
            update={"max_positions": None, "max_exposure_pct": 100.0})}), frames, 2)
    assert [r["return_pct"] for r in capped] != [r["return_pct"] for r in free], \
        "한도가 창 수익률을 바꾸지 않았다 — 합산이 아니라 한 레그만 보고 있을 수 있다"


def test_bundle_window_failure_keeps_its_slot():
    """**정말 실패하는** 창이 자기 자리를 지키고 이웃 창은 그대로 숫자를 낸다.

    한 레그의 마지막 창에 읽을 수 없는 가격을 하나 심는다(상류 응답이 깨지면 실제로 그런
    값이 온다). 빈 프레임으로는 창이 실패하지 않는다 — 그 레그가 한 봉도 받지 않을 뿐이다.
    그래서 전에는 세 창 모두 `error: ""` 였고 이 시험이 아무것도 무지 않았다.

    과최적화 경고의 후반부 판정이 **마지막 원소**를 보므로, 실패한 창이 목록에서 빠지면
    판정이 엉뚱한 창을 본다.
    """
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    eth = frame(LONG)
    eth["close"] = eth["close"].astype(object)
    eth.loc[len(eth) - 4, "close"] = "읽을 수 없는 값"      # 세 창 중 마지막 창에 든다
    rows = wf.run_bundle_windows(m, {"BTCUSDT": frame(LONG), "ETHUSDT": eth}, 3)
    assert len(rows) == 3
    assert [r["index"] for r in rows] == [1, 2, 3]
    assert rows[-1]["error"] and rows[-1]["return_pct"] is None,         "실패한 창이 0.0 으로 꾸며졌다"
    assert [r["error"] for r in rows[:-1]] == ["", ""]
    assert all(r["return_pct"] is not None for r in rows[:-1]),         "한 창의 실패가 다른 창까지 비웠다"


def test_bundle_windows_too_few_rows_is_empty():
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    assert wf.run_bundle_windows(m, {"BTCUSDT": frame([100]), "ETHUSDT": frame([100])}, 4) == []


def test_bundle_windows_cut_by_time_when_legs_start_at_different_moments():
    """레그마다 시작 시각이 다르면 행 수 기준 경계는 어긋난다 — 창은 합친 시간선에서 잘려야 한다."""
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}],
                 "bundle_risk": {"max_positions": 1}})
    btc = frame(LONG, start="2026-01-01 00:00")
    eth = frame(LONG, start="2026-01-01 08:00")  # 8 시간 늦게 시작해 8 시간 늦게 끝난다
    rows = wf.run_bundle_windows(m, {"BTCUSDT": btc, "ETHUSDT": eth}, 2)
    assert len(rows) == 2
    assert rows[0]["start"] == wf._label(btc["timestamp"].min())
    assert rows[-1]["end"] == wf._label(eth["timestamp"].max()),         "마지막 창이 늦게 끝나는 레그의 끝까지 가지 않았다 — 한 레그의 행 수로 잘랐을 수 있다"


# --- 끝점이 더 이상 거절하지 않는다 ------------------------------------
def test_validate_endpoint_no_longer_refuses_portfolio(monkeypatch):
    """끝점을 **실제로** 친다 — 전에는 `inspect.getsource` 로 문구만 봐서, 거절 문구를
    한 글자만 바꿔 거절을 되살려도 초록이었다."""
    from fastapi.testclient import TestClient

    import app.main as main

    def fake_fetch(macro, start_ms, end_ms):
        return frame(LONG * 8), "synthetic"

    monkeypatch.setattr(main, "fetch_klines_for_macro", fake_fetch)
    main._validate_limiter._events.clear()
    body = {"macro": {**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                       {"symbol": "ETHUSDT", "weight": 40}]},
            "windows": 4}
    try:
        response = TestClient(main.app).post("/api/validate", json=body)
    finally:
        main._validate_limiter._events.clear()
    assert response.status_code == 200, response.text
    assert len(response.json()["windows"]) == 4


# --- B2. 한도 유무가 경로를 가르는 자리는 하나다 -------------------------
def test_dca_bundle_windows_do_not_crash():
    """적립식(C) 2종목 묶음 — 동기 루프는 봉 기반 심만 다루므로 전에는 창이 전부 KeyError 였다."""
    m = Macro(**{"symbol": "BTCUSDT", "rule_type": "C", "candle_interval": "1d",
                 "period": {"preset": "3m"},
                 "params": {"amount_per_buy": 100, "interval_days": 7, "initial_capital": 1000},
                 "risk": {"invest_ratio": 1.0},
                 "symbols": ["BTCUSDT", "ETHUSDT"]})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    rows = wf.run_bundle_windows(m, frames, 3)
    assert len(rows) == 3
    assert all(r["error"] == "" for r in rows), [r["error"] for r in rows]
    assert all(r["return_pct"] is not None for r in rows)


def test_no_limit_bundle_uses_the_same_path_for_period_and_windows():
    """한도 없는 묶음에서 전체기간과 창이 같은 코드를 지나야 한다 — 전에는 갈려 있었다.

    창 하나로 자르면 그 창의 수익률이 전체기간 수익률과 같아야 한다.
    """
    from app.engine import portfolio as portfolio_mod
    from app.engine.portfolio_backtest import run_legs

    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                  {"symbol": "ETHUSDT", "weight": 40}]})
    frames = {"BTCUSDT": frame(LONG), "ETHUSDT": frame(LONG)}
    whole, _ = portfolio_mod.aggregate(run_legs(m, frames), candle_interval=m.candle_interval)
    rows = wf.run_bundle_windows(m, frames, 1)
    assert len(rows) == 1
    assert rows[0]["return_pct"] == round(float(whole.final_return_pct), 2)


# --- S6. 시간대가 섞인 레그가 요청 전체를 죽이지 않는다 ------------------
def test_mixed_timezone_legs_do_not_kill_the_whole_request():
    """창 라벨 계산이 창별 `try` 밖이라, tz 가 섞이면 `TypeError` 가 요청 전체를 400 으로 만든다.

    바로 옆 `split_frames_by_time` 은 같은 경우를 UTC 나노초로 막는다 — 라벨도 같은 저울을 쓴다.
    """
    btc = frame(LONG)                                   # tz 없음
    eth = frame(LONG)
    eth["timestamp"] = eth["timestamp"].dt.tz_localize("UTC")   # tz 있음
    m = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                  {"symbol": "ETHUSDT", "weight": 50}]})
    rows = wf.run_bundle_windows(m, {"BTCUSDT": btc, "ETHUSDT": eth}, 2)
    assert len(rows) == 2
    assert all(r["start"] and r["end"] for r in rows)
