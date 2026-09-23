import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import news_sentiment_test as mod
from app.news_sentiment_test import require_test_admin as require_admin
from app.db import User, get_session
from app.main import app
from app.agent_features.position_news.articles import NewsArticle

client = TestClient(app)
api_app = app
while not hasattr(api_app, "dependency_overrides"):
    api_app = api_app.app


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("SEMIF_API_BASE", "http://model.invalid:11434")
    monkeypatch.delenv("SEMIF_API_KEY", raising=False)
    with get_session() as db:
        db.exec(delete(NewsArticle))
        db.commit()
    yield
    api_app.dependency_overrides.pop(require_admin, None)


def admin():
    api_app.dependency_overrides[require_admin] = lambda: User(id=1, is_admin=True)


def seed(n=1, scope="BTC", stamp=None, **item):
    stamp = int(time.time() * 1000) if stamp is None else stamp
    key = f"{n:020x}"
    with get_session() as db:
        db.add(NewsArticle(asset_symbol=scope, article_id=key, revision=n, first_seen_ms=stamp,
                           last_seen_ms=stamp, ready=False, item_json=json.dumps({
                               "title": "Bitcoin ETF inflows rise", "source": "Fixture", "url": "https://example.com/news",
                               "excerpt": "<p>Net inflows increased.</p>", **item})))
        db.commit()
    return {"id": key, "scope": scope}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_routes_require_admin_before_reading_or_inference(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("model must not be contacted by an anonymous request")
    monkeypatch.setattr(mod.httpx, "AsyncClient", forbidden)
    assert client.get("/api/admin/news-test/status").status_code == 401
    assert client.get("/api/admin/news-test/articles").status_code == 401
    assert client.post("/api/admin/news-test/analyze", json={"id": "a" * 20, "scope": "BTC"}).status_code == 401


def test_normal_account_cannot_use_model():
    from app.auth import current_user
    api_app.dependency_overrides[current_user] = lambda: User(id=1, is_admin=False)
    try:
        assert client.get("/api/admin/news-test/status").status_code == 403
    finally:
        api_app.dependency_overrides.pop(current_user, None)


def test_feed_reads_untranslated_news_and_cursor_handles_equal_timestamps():
    admin()
    stamp = int(time.time() * 1000)
    for n in range(1, 24):
        seed(n, stamp=stamp, url="javascript:alert(1)")
    first = client.get("/api/admin/news-test/articles").json()
    assert len(first["items"]) == 20
    assert first["items"][0]["id"] == f"{4:020x}"
    assert first["items"][0]["excerpt"] == "Net inflows increased."
    assert first["items"][0]["url"] == ""
    seed(24, stamp=stamp)
    seed(25, scope="ETH", stamp=stamp)
    response = client.get("/api/admin/news-test/articles", params={"cursor": first["cursor"]})
    assert [item["id"] for item in response.json()["items"]] == [f"{24:020x}", f"{25:020x}"]
    assert "no-store" in response.headers["cache-control"]
    assert client.get("/api/admin/news-test/articles?cursor=broken").status_code == 422


def test_old_articles_not_replayed():
    admin()
    seed(stamp=int(time.time() * 1000) - 90_000_000)
    assert client.get("/api/admin/news-test/articles").json()["items"] == []


def test_status_checks_exact_model_and_keeps_key_server_side(monkeypatch):
    admin()
    monkeypatch.setenv("SEMIF_API_KEY", "fixture-only-key")
    def handler(request):
        assert request.url == "http://model.invalid:11434/api/tags"
        assert request.headers["Authorization"] == "Bearer fixture-only-key"
        return httpx.Response(200, json={"models": [{"name": mod.MODEL}]})
    transport(monkeypatch, handler)
    response = client.get("/api/admin/news-test/status")
    assert response.json() == {"model": mod.MODEL, "ready": True, "detail": ""}
    assert "fixture-only-key" not in response.text


def test_one_real_call_per_article_reports_separate_timing_without_caching(monkeypatch):
    admin()
    article = seed()
    calls = []
    def handler(request):
        prompt = json.loads(request.content)
        calls.append(prompt)
        assert prompt["model"] == "semif-test:0.1.1"
        assert prompt["stream"] is False
        assert prompt["format"] == "json"
        assert "Net inflows increased" in prompt["messages"][1]["content"]
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"verdict": "bullish", "reason": "ETF 자금 유입이 늘었습니다."})},
                                        "total_duration": 350_000_000, "load_duration": 25_000_000})
    transport(monkeypatch, handler)
    for _ in range(2):
        response = client.post("/api/admin/news-test/analyze", json=article)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["verdict"] == "bullish"
        assert result["model_ms"] == 350 and result["load_ms"] == 25
        assert result["elapsed_ms"] >= 0
        assert result["article"]["id"] == article["id"]
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [
    {"done": True, "message": {"content": "This seems positive"}},
    {"done": False, "message": {"content": '{"verdict":"bullish","reason":"test"}'}},
    {"done": True, "message": {"content": '{"verdict":"unknown","reason":"test"}'}},
    {"done": True, "message": {"content": '{"verdict":"bearish","reason":" "}'}},
])
def test_invalid_model_response_is_not_fabricated_as_a_verdict(monkeypatch, payload):
    admin()
    article = seed()
    transport(monkeypatch, lambda req: httpx.Response(200, json=payload))
    response = client.post("/api/admin/news-test/analyze", json=article)
    assert response.status_code == 502
    assert "verdict" not in response.json()


def test_timeout_and_missing_article_release_model_slot(monkeypatch):
    admin()
    assert client.post("/api/admin/news-test/analyze", json={"id": "a" * 20, "scope": "BTC"}).status_code == 404
    article = seed()
    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout")
    transport(monkeypatch, timeout)
    assert client.post("/api/admin/news-test/analyze", json=article).status_code == 504
    assert mod._slot.acquire(blocking=False)
    mod._slot.release()


def test_busy_model_does_not_start_second_inference(monkeypatch):
    admin()
    article = seed()
    assert mod._slot.acquire(blocking=False)
    try:
        assert client.post("/api/admin/news-test/analyze", json=article).status_code == 429
    finally:
        mod._slot.release()


def test_disconnection_cancels_model_request(monkeypatch):
    cancelled = []
    async def slow(article):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    class Disconnected:
        async def is_disconnected(self):
            return True
    monkeypatch.setattr(mod, "_infer", slow)
    seed()
    with pytest.raises(mod.HTTPException) as error:
        asyncio.run(mod.analyze(mod.ArticleRequest(id=f"{1:020x}", scope="BTC"), Disconnected()))
    assert error.value.status_code == 499
    assert cancelled == [True]
    assert mod._slot.acquire(blocking=False)
    mod._slot.release()
