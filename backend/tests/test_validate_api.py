"""검증 API 는 백테스트 결과에 검증 레이어를 얹어 한 번에 돌려준다.

캔들은 monkeypatch 로 막아 둔다 — 시험은 네트워크에 붙지 않는다.
"""
import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.data import NoSpotDataError

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


def test_validate_returns_metrics_warnings_and_windows(client):
    response = client.post("/api/validate", json={"macro": MACRO, "windows": 4})
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= BODY_KEYS
    assert isinstance(body["warnings"], list)
    assert len(body["windows"]) <= 4
    assert body["monthly"], "1년 곡선이면 월별 수익이 나온다"
    assert body["calmar"] is not None and isinstance(body["calmar"], float)


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
              for i, (r, e) in enumerate([(5.0, ""), (4.0, ""), (3.0, ""), (None, "ValueError")], start=1)]
    monkeypatch.setattr(main.walkforward_mod, "run_windows", lambda macro, df, windows: failed)
    seen = {}
    real = main.validation_mod.warnings

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(main.validation_mod, "warnings", spy)
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 4}).json()
    assert body["windows"][-1]["return_pct"] is None
    assert seen["window_returns"] == [5.0, 4.0, 3.0, None]


def test_a_short_period_carries_calmar_as_null(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_klines_for_macro", lambda m, s, e: (candles(20), "synthetic"))
    body = client.post("/api/validate", json={"macro": MACRO, "windows": 2}).json()
    assert body["calmar"] is None


def test_window_count_is_bounded(client):
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 999}).status_code == 422
    assert client.post("/api/validate", json={"macro": MACRO, "windows": 1}).status_code == 422


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


def test_a_portfolio_macro_is_refused_in_korean(client):
    portfolio = {**MACRO, "symbols": ["BTCUSDT", "ETHUSDT"]}
    response = client.post("/api/validate", json={"macro": portfolio, "windows": 4})
    assert response.status_code == 422
    assert "포트폴리오" in response.json()["detail"]
