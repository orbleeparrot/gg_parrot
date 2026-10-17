"""Public routing stays read-only and independent of login and paid AI."""
import pytest
from fastapi.testclient import TestClient

from app import domestic_community, market_trends
from app.main import app


def test_actual_application_exposes_separate_domestic_community_without_collecting(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a public read must not collect or fetch exchange data")

    domestic_community._responses.clear()
    monkeypatch.setattr(domestic_community, "collect_once", forbidden)
    monkeypatch.setattr(domestic_community.krw, "list_symbols", forbidden)
    monkeypatch.setattr(domestic_community, "_read_snapshot", lambda: {"state": None, "items": []})
    response = TestClient(app).get("/api/community/domestic?exchange=upbit&symbol=KRW-BTC")
    assert response.status_code == 200
    assert response.json()["exchange"] == "upbit"
    assert response.json()["symbol"] == "KRW-BTC"
    assert response.json()["collection"]["status"] == "pending"
    assert response.json()["items"] == []


def test_actual_application_rejects_non_domestic_community_exchange_before_store(monkeypatch):
    monkeypatch.setattr(domestic_community, "_read_snapshot", lambda: pytest.fail("store read"))
    response = TestClient(app).get("/api/community/domestic?exchange=binance")
    assert response.status_code == 400


def test_market_source_failure_does_not_affect_community_routes(monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("source offline")

    monkeypatch.setattr(market_trends, "get_market_trends", unavailable)
    response = TestClient(app).get("/api/market/trends?exchange=bithumb")
    assert response.status_code == 503
    assert "source offline" not in response.text
