"""Response reuse, privacy and update propagation use isolated fixtures only."""
from collections import OrderedDict
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.responses import Response

from app import db as database, main, public_news, observability
from app.agent_features.position_news import articles
from app.http_cache import CachePolicyMiddleware


def test_prepared_news_reuses_db_read_and_committed_article_invalidates():
    scope = "DELIVERYFIX"
    item = {"title": "비트코인 신규 소식입니다", "url": "https://example.invalid/news", "source": "뉴스"}
    articles.upsert_articles(scope, [item])
    statements = []
    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(database._engine, "before_cursor_execute", capture)
    try:
        first = public_news.get_coin_news(scope + "USDT")
        assert len(statements) == 2
        first["items"].clear()  # Caller mutation must not corrupt the shared value.
        warm = public_news.get_coin_news(scope + "USDT")
        assert len(statements) == 2
        assert warm["items"][0]["title"] == item["title"]
        articles.upsert_articles(scope, [{**item, "image": "https://example.invalid/new.webp"}])
        statements.clear()
        changed = public_news.get_coin_news(scope + "USDT")
        assert len(statements) == 2
        assert changed["items"][0]["image"].endswith("new.webp")
    finally:
        event.remove(database._engine, "before_cursor_execute", capture)


def test_public_news_conditional_response_and_changed_payload(monkeypatch):
    payload = {"items": [{"title": "준비된 기사"}], "cursor": 1}
    monkeypatch.setattr(public_news, "get_market_news", lambda: payload)
    client = TestClient(main.app)
    first = client.get("/api/news/market")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=1, s-maxage=1"
    same = client.get("/api/news/market", headers={"If-None-Match": "W/" + first.headers["etag"],
                                                       "Authorization": "Bearer fake-unused"})
    assert same.status_code == 304 and same.content == b""
    payload["cursor"] = 2
    changed = client.get("/api/news/market", headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and changed.json()["cursor"] == 2


def test_personal_routes_never_allow_http_storage(monkeypatch):
    user = SimpleNamespace(id=101)
    overrides = main.api_app.dependency_overrides
    monkeypatch.setitem(overrides, main.auth_mod.current_user, lambda: user)
    monkeypatch.setitem(overrides, main.auth_mod.current_user_in_session, lambda: user)
    monkeypatch.setitem(overrides, main.request_session, lambda: None)
    for target, name, value in (
        (main.auth_mod, "user_view", {"id": 101}),
        (main.account_mod, "dashboard", {"user": {"id": 101}}),
        (main.board_mod, "my_posts", []),
        (main.user_macros_mod, "list_macros", {"items": []}),
        (main.runner_mod, "get_or_create_key", {"key": "fixture-only"}),
        (main.runner_mod, "list_sessions", {"items": []}),
    ):
        monkeypatch.setattr(target, name, lambda *a, value=value, **k: value)
    client = TestClient(main.app)
    for path in ("/api/auth/me", "/api/me/dashboard", "/api/me/macros",
                 "/api/me/runner/key", "/api/me/runner/sessions"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"


def test_http_error_and_mixed_user_routes_cannot_be_public():
    application = FastAPI()
    @application.get("/api/leaderboard")
    async def mixed():
        return {"my_vote": 1}
    @application.get("/api/news/market")
    async def unavailable():
        return Response(status_code=503, headers={"Cache-Control": "public, max-age=100",
                                                 "CDN-Cache-Control": "public, max-age=100"})
    @application.get("/assets/app-12345678.js")
    async def asset():
        return Response("export {}", media_type="application/javascript")
    @application.get("/sw.js")
    async def worker():
        return Response("/* worker */", media_type="application/javascript")
    client = TestClient(CachePolicyMiddleware(application))
    for path in ("/api/leaderboard", "/api/news/market"):
        response = client.get(path)
        assert response.headers["cache-control"] == "private, no-store"
        assert "cdn-cache-control" not in response.headers
    assert "immutable" in client.get("/assets/app-12345678.js").headers["cache-control"]
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"


def test_metrics_are_bounded_and_do_not_contain_cache_keys(monkeypatch):
    monkeypatch.setattr(observability, "_performance_routes", OrderedDict())
    for index in range(200):
        observability.record_request_latency(f"/route-template-{index}", index)
    snapshot = observability.performance_snapshot()
    assert len(snapshot["routes"]) == 128
    assert snapshot["routes"]["/route-template-199"]["p95_ms"] == 199
    assert "public_news" in snapshot["caches"]
    assert set(snapshot["caches"]["public_news"].values())
    assert all(isinstance(value, (int, float)) for counters in snapshot["caches"].values() for value in counters.values())
