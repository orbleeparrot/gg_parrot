"""검증 API 는 백테스트 결과에 검증 레이어를 얹어 한 번에 돌려준다.

캔들은 monkeypatch 로 막아 둔다 — 시험은 네트워크에 붙지 않는다.
"""
import json
import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.data import NoSpotDataError
from app.engine import Macro, run_backtest
from app.engine import validation, walkforward

MACRO = {"symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
         "params": {"take_profit_pct": 5, "initial_capital": 1000000},
         "risk": {"stop_loss_pct": 3}, "period": {"preset": "1y"}}

KRW_MACRO = {"exchange": "upbit", "symbol": "KRW-BTC", "rule_type": "A", "candle_interval": "1d",
             "params": {"take_profit_pct": 5, "initial_capital": 1000000},
             "risk": {"stop_loss_pct": 3}, "period": {"preset": "1y"}}

BODY_KEYS = {"result", "monthly", "concentration", "drawdown",
             "sortino", "calmar", "windows", "warnings"}


def candles(rows: int = 365) -> pd.DataFrame:
    """운영 캔들과 같은 열(timestamp, open, high, low, close, volume) — 오르내리는 합성 곡선."""
    stamps = pd.date_range("2025-10-01", periods=rows, freq="1D", tz="UTC")
    close = [100.0 + 25.0 * math.sin(i / 9.0) + i * 0.05 for i in range(rows)]
    return pd.DataFrame({"timestamp": stamps, "open": close,
                         "high": [c * 1.02 for c in close], "low": [c * 0.98 for c in close],
                         "close": close, "volume": [10.0] * rows})


@pytest.fixture(autouse=True)
def fresh_limiter():
    """IP 별 호출 제한은 프로세스 안에 남는다 — 시험끼리 횟수를 나눠 쓰지 않게 비운다."""
    main._validate_limiter._events.clear()
    yield
    main._validate_limiter._events.clear()


@pytest.fixture
def client(monkeypatch):
    """캔들 조회를 합성 프레임으로 막은 클라이언트. 요청된 매크로를 calls 로 남긴다."""
    calls = []

    def fake_fetch(macro, start_ms, end_ms):
        calls.append(macro)
        return candles(), "synthetic"

    monkeypatch.setattr(main, "fetch_klines_for_macro", fake_fetch)
    test_client = TestClient(main.app)
    test_client.calls = calls
    return test_client


def _roundtrip(value):
    """응답과 같은 JSON 왕복 — 튜플 · 정수/실수 표기 차이를 맞춘다."""
    return json.loads(json.dumps(value))


def test_validate_returns_metrics_warnings_and_windows(client):
    """응답의 모든 칸이 같은 프레임에 순수 함수를 직접 돌린 값과 같다 — 고정값을 돌려주는 가짜는 통과 못 한다."""
    response = client.post("/api/validate", json={"macro": MACRO, "windows": 4})
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= BODY_KEYS

    macro = Macro.model_validate(MACRO)
    result = run_backtest(macro, candles())
    curve = result.equity_curve
    windows = walkforward.run_windows(macro, candles(), 4)

    assert len(body["windows"]) == 4
    assert body["windows"] == _roundtrip(windows)
    assert [w["return_pct"] for w in body["windows"]] == [w["return_pct"] for w in windows]
    assert body["monthly"] == _roundtrip(validation.monthly_returns(curve))
    assert body["concentration"] == _roundtrip(validation.concentration(curve))
    assert body["drawdown"] == _roundtrip(validation.drawdown_window(curve))
    assert body["sortino"] == validation.sortino(curve)
    assert body["calmar"] == validation.calmar(curve, result.mdd_pct)
    assert isinstance(body["calmar"], float)
    assert body["result"]["final_return_pct"] == result.final_return_pct
    assert body["result"]["total_trades"] == result.total_trades
    assert body["warnings"] == validation.warnings(
        curve=curve, total_trades=result.total_trades,
        window_returns=[w["return_pct"] for w in windows],
        top_trade_share_pct=result.top_trade_share_pct)


def test_the_trade_share_reaches_the_response_and_fires_the_warning(client, monkeypatch):
    """엔진이 낸 상위 거래 몫이 경고 규칙까지 이어진다 — 라우트가 이 값을 넘기지 않으면 거래_집중 은 영영 안 뜬다."""
    real = main.run_backtest

    def concentrated(macro, df):
        result = real(macro, df)
        return result.model_copy(update={"top_trade_share_pct": 90.0})

    monkeypatch.setattr(main, "run_backtest", concentrated)
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 4}).json()
    assert body["result"]["top_trade_share_pct"] == 90.0
    assert "거래_집중" in body["warnings"]


def test_a_low_trade_share_does_not_fire_the_concentration_warning(client):
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 4}).json()
    assert body["result"]["top_trade_share_pct"] < 50.0
    assert "거래_집중" not in body["warnings"]


def test_validation_reads_the_full_curve_not_the_sampled_one(client, monkeypatch):
    """result 곡선은 1000점으로 줄여 보내지만, 검증 지표는 줄이기 전 전체 곡선으로 센다."""
    monkeypatch.setattr(main, "fetch_klines_for_macro", lambda m, s, e: (candles(1500), "synthetic"))
    lengths = {}
    real = main.validation_mod.monthly_returns

    def spy(curve):
        lengths["monthly"] = len(curve)
        return real(curve)

    monkeypatch.setattr(main.validation_mod, "monthly_returns", spy)
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 4}).json()
    assert len(body["result"]["equity_curve"]) <= 1000
    assert lengths["monthly"] > 1000


def test_a_krw_macro_on_upbit_gets_the_same_response_shape(client):
    response = client.post("/api/validate", json={"macro": KRW_MACRO, "windows": 4})
    assert response.status_code == 200
    assert set(response.json()) >= BODY_KEYS
    assert client.calls[-1].exchange == "upbit" and client.calls[-1].symbol == "KRW-BTC"


def test_a_window_that_could_not_run_is_null_not_zero(client, monkeypatch):
    """못 잰 값은 None 그대로 — 0 으로 꾸미지 않고, 마지막 구간 실패도 순서를 지킨다."""
    failed = [{"index": i, "start": "a", "end": "b", "return_pct": r, "trades": 1, "error": e}
              for i, (r, e) in enumerate([(5.0, ""), (4.0, ""), (-3.0, ""), (None, "ValueError")], start=1)]
    monkeypatch.setattr(main.walkforward_mod, "run_windows", lambda macro, df, windows: failed)
    seen = {}
    real = main.validation_mod.warnings

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(main.validation_mod, "warnings", spy)
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 4}).json()
    assert body["windows"][-1]["return_pct"] is None
    assert seen["window_returns"] == [5.0, 4.0, -3.0, None]
    # 걸러 냈다면 세 번째(-3) 구간이 '마지막' 이 되어 후반부_음수 가 잘못 켜진다.
    assert "후반부_음수" not in body["warnings"]


def test_a_short_period_carries_calmar_as_null(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_klines_for_macro", lambda m, s, e: (candles(20), "synthetic"))
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 2}).json()
    assert body["calmar"] is None


def test_window_count_is_bounded(client):
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 999}).status_code == 422
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 1}).status_code == 422
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 2}).status_code == 200
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 12}).status_code == 200


def test_an_invalid_macro_is_rejected_in_korean(client):
    bad = {**MACRO, "params": {}}
    response = client.post("/api/validate", json={"macro": bad, "windows": 4})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "매크로" in detail and "Traceback" not in detail


def test_a_period_without_candles_is_a_clean_422(client, monkeypatch):
    def empty(macro, start_ms, end_ms):
        raise NoSpotDataError("해당 기간의 캔들이 없습니다")

    monkeypatch.setattr(main, "fetch_klines_for_macro", empty)
    response = client.post("/api/validate", json={"macro": MACRO, "windows": 4})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_a_portfolio_macro_is_validated_not_refused(client):
    """묶음도 검증한다 — 레그마다 캔들을 받고, 창은 묶음 합산으로 나온다."""
    portfolio = {**MACRO, "symbols": ["BTCUSDT", "ETHUSDT"]}
    response = client.post("/api/validate", json={"macro": portfolio, "windows": 4})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == BODY_KEYS
    assert len(body["windows"]) == 4
    assert {m.symbol for m in client.calls} == {"BTCUSDT", "ETHUSDT"}


def test_a_bad_period_preset_is_a_korean_400(client):
    bad = {**MACRO, "period": {"preset": "zzz"}}
    response = client.post("/api/validate", json={"macro": bad, "windows": 4})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "기간" in detail and "unknown" not in detail


def test_the_31st_call_in_a_minute_is_refused_like_the_beacons(client):
    """한 번에 백테스트 최대 13 번 — IP 별 분당 30 회까지만. 429 모양은 비콘과 같다."""
    for _ in range(30):
        assert client.post("/api/validate", json={"macro": MACRO, "windows": 2}).status_code == 200
    refused = client.post("/api/validate", json={"macro": MACRO, "windows": 2})
    assert refused.status_code == 429
    assert refused.json()["detail"] == "잠시 후 다시 시도해 주세요."
    assert int(refused.headers["Retry-After"]) >= 1


def test_the_limit_is_kept_per_forwarded_ip(client):
    for _ in range(30):
        client.post("/api/validate", json={"macro": MACRO, "windows": 2}, headers={"x-forwarded-for": "1.1.1.1"})
    other = client.post("/api/validate", json={"macro": MACRO, "windows": 2}, headers={"x-forwarded-for": "2.2.2.2"})
    assert other.status_code == 200
