"""API smoke tests via FastAPI TestClient (uses cached/synthetic data offline)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import news
from app.main import app

client = TestClient(app)


def _macro_payload():
    return {
        "symbol": "BTCUSDT",
        "rule_type": "A",
        "position_side": "short",
        "params": {"take_profit_pct": 5.0, "initial_capital": 1000000},
        "risk": {"invest_ratio": 0.5, "stop_loss_pct": 3.0},
        "period": {"preset": "3m"},
    }


def test_create_get_backtest_gallery_card_flow():
    # create
    r = client.post("/api/macros", json=_macro_payload())
    assert r.status_code == 200, r.text
    data = r.json()
    slug = data["share_slug"]
    assert slug and data["human_summary"]
    assert "result" in data and "final_return_pct" in data["result"]

    # fetch (clone path)
    r = client.get(f"/api/macros/{slug}")
    assert r.status_code == 200
    loaded = r.json()["macro"]
    assert loaded["rule_type"] == "A" and loaded["position_side"] == "short"

    # re-backtest with a different period (visitor changes period)
    r = client.post("/api/backtest", json={"macro": loaded, "period_override": {"preset": "6m"}})
    assert r.status_code == 200
    assert "disclaimer" in r.json()

    # gallery
    r = client.get("/api/gallery")
    assert r.status_code == 200
    assert any(it["share_slug"] == slug for it in r.json()["items"])

    # share card
    r = client.get(f"/api/card/{slug}.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_short_without_stop_loss_rejected():
    bad = _macro_payload()
    bad["risk"] = {"invest_ratio": 0.5}  # no stop_loss on a short -> must fail
    r = client.post("/api/macros", json=bad)
    assert r.status_code == 422  # pydantic validation error


def test_news_translation_failure_returns_retryable_service_error(monkeypatch):
    def fail_translation(*_args, **_kwargs):
        raise news.NewsTranslationError("영문 뉴스 제목 번역에 실패했습니다.")

    monkeypatch.setattr(news, "get_market_news", fail_translation)
    monkeypatch.setattr(news, "get_coin_news", fail_translation)

    market = client.get("/api/news/market")
    coin = client.get("/api/news/coin/ARBUSDT")

    assert market.status_code == 503
    assert coin.status_code == 503
    assert "번역" in market.json()["detail"]
    assert "번역" in coin.json()["detail"]


def test_news_translation_preflight_busy_returns_safe_retry_status(monkeypatch):
    def busy(*_args, **_kwargs):
        raise news.NewsTranslationBusyError("뉴스 번역 요청이 몰려 있습니다.")

    monkeypatch.setattr(news, "get_market_news", busy)
    monkeypatch.setattr(news, "get_coin_news", busy)

    assert client.get("/api/news/market").status_code == 429
    assert client.get("/api/news/coin/ARBUSDT").status_code == 429
