"""Public KRW routing and runner fail-closed checks (isolated SQLite only)."""
import secrets

import pytest
from fastapi.testclient import TestClient

from app import chart, marketdata, runner
from app.data import symbols
from app.main import app

DOMESTIC = {"exchange": "upbit", "symbol": "KRW-BTC", "rule_type": "A",
            "params": {"initial_capital": 100000, "take_profit_pct": 3}}


def test_public_market_routes_preserve_exchange(monkeypatch):
    seen = []
    monkeypatch.setattr(chart, "get_candles", lambda symbol, **kw: seen.append((symbol, kw)) or {"exchange": kw["exchange"]})
    monkeypatch.setattr(chart, "get_live_candles", lambda symbol, **kw: seen.append((symbol, kw)) or {"exchange": kw["exchange"]})
    monkeypatch.setattr(symbols, "list_symbols", lambda **kw: {"items": [], "exchange": kw["exchange"]})
    monkeypatch.setattr(marketdata, "batch_prices", lambda wanted, **kw: seen.append((wanted, kw)) or {wanted[0]: 10})
    with TestClient(app) as client:
        for path in ("/api/candles", "/api/candles/live"):
            assert client.get(path, params={"symbol": "KRW-BTC", "exchange": "upbit"}).json()["exchange"] == "upbit"
        assert client.get("/api/symbols?exchange=bithumb").json()["exchange"] == "bithumb"
        prices = client.get("/api/prices?symbols=KRW-BTC&exchange=bithumb").json()
        assert prices["quote_currency"] == "KRW"
        assert prices["exchange"] == "bithumb"
        assert all(kwargs["exchange"] in ("upbit", "bithumb") for _, kwargs in seen)
        assert client.get("/api/candles?symbol=KRW-BTC&exchange=unknown").status_code == 422
        assert client.get("/api/prices?symbols=KRW-BTC").status_code == 422
        assert client.get("/api/prices?symbols=BTCUSDT&exchange=upbit").status_code == 422
        assert client.get("/api/funding-rate?symbol=KRW-BTC&exchange=upbit").json()["available"] is False


@pytest.mark.parametrize("path", ["/api/realtrade/bundle", "/api/realtrade/macro-file"])
def test_domestic_cannot_download_binance_runner_file(path):
    with TestClient(app) as client:
        res = client.post(path, json={"macro": DOMESTIC})
        assert res.status_code == 422
        # 파일은 바이낸스 전용이라고 범위로 말한다 — 실행기 직접 연결이 안 된다고 거짓말하지 않는다.
        assert "바이낸스 전용" in res.json()["detail"]
        assert "지원하지 않" not in res.json()["detail"]


def test_domestic_save_round_trip_and_inconsistent_start_blocked(monkeypatch):
    # 거래소·종목·매크로가 서로 맞지 않는 시작 요청은 실행기 버전과 무관하게 거절한다.
    with TestClient(app) as client:
        name = "exchange_" + secrets.token_hex(5)
        response = client.post("/api/auth/signup", json={"email": f"{name}@example.invalid", "username": name, "password": "password123"})
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['token']}"}
        saved = client.post("/api/me/macros", headers=headers, json={"macro": DOMESTIC}).json()["item"]
        assert saved["macro"]["exchange"] == "upbit"
        assert saved["macro"]["quote_currency"] == "KRW"
        assert client.get(f"/api/me/macros/{saved['id']}", headers=headers).json()["macro"]["exchange"] == "upbit"
        # 티켓 발급은 열려 있다 — 실행기 버전은 청구할 때 가려진다(test_runner_domestic 참고).
        ticket = client.post("/api/me/runner/launch-tickets", headers=headers, json={"user_macro_id": saved["id"], "testnet": True})
        assert ticket.status_code == 200, ticket.text
        key = client.get("/api/me/runner/key", headers=headers).json()["key"]
        for payload in ({"symbol": "KRW-BTC"}, {"symbol": "BTCUSDT", "exchange": "upbit"}, {"symbol": "BTCUSDT", "macro": DOMESTIC}):
            result = client.post("/api/runner/start", headers={"X-Runner-Key": key}, json=payload)
            assert result.status_code == 422, result.text


def test_runner_guard_rejects_domestic_for_old_runner_before_database_work():
    from app.engine import Macro
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        runner._require_supported_exchange(Macro.model_validate(DOMESTIC), "9")
    assert exc.value.status_code == 426
