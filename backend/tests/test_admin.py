"""관리자 대시보드 — 비콘(view/event/leave)이 계약대로 분류·중복 제거되고, 집계 다섯 개가 계약 모양을 지키며,
결정적 시나리오(방문자 2 · 세션 2 · 이탈 1 · 당일 가입 1 · 코호트)에서 정확한 값을 내고, 이웃 모듈이 없어도 무너지지 않는다.
"""
from __future__ import annotations

import json
import logging
import secrets
import sys
import types

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app import admin as admin_mod
from app import main as main_mod
from app import observability
from app.db import (DailyQuestClaim, LeaderboardEntry, MacroEventDaily, MacroUnlock, PaperSession, PointLedger, RunSession, User, Visit,
                    get_session)
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache_and_generous_limits(monkeypatch):
    # 집계 캐시가 앞 테스트의 값을 돌려주면 안 되고, 비콘 한도(분당 240)가 테스트 파일 전체를 막아서도 안 된다.
    admin_mod.clear_cache()
    for name in ("_visit_limiter", "_leave_limiter", "_impressions_limiter", "_open_limiter"):
        monkeypatch.setattr(main_mod, name, observability.SlidingWindowRateLimiter(limit=100_000, window_seconds=60, max_keys=100))
    yield
    admin_mod.clear_cache()


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={"email": f"a{tok}@ex.com", "username": f"a_{tok}", "password": "password123"}).json()
    return body["token"], body["user"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _make_admin(user_id: int):
    with get_session() as db:
        user = db.get(User, user_id)
        user.is_admin = True
        db.add(user)
        db.commit()


def _admin_client():
    token, user_id = _signup()
    _make_admin(user_id)
    return _auth(token)


def _row(view_key: str) -> Visit | None:
    with get_session() as db:
        return db.exec(select(Visit).where(Visit.view_key == view_key)).first()


def _view(**overrides) -> dict:
    payload = {"kind": "view", "path": "/", "view_key": secrets.token_hex(6), "session_key": secrets.token_hex(6), "referrer": "",
               "utm_source": "", "visitor": "vis-" + secrets.token_hex(3), "is_new": True, "is_landing": True, "screen_w": 1440}
    payload.update(overrides)
    return payload


# --- 권한 ---------------------------------------------------------------------------------------
def test_admin_comes_only_from_the_column_not_the_environment(monkeypatch):
    """권한 경로는 DB 컬럼 하나뿐 — 환경 변수로 여는 옛 문(ADMIN_USERNAMES)이 되살아나면 여기서 잡힌다."""
    token, user_id = _signup()
    username = client.get("/api/auth/me", headers=_auth(token)).json()["user"]["username"]
    monkeypatch.setenv("ADMIN_USERNAMES", f"someone_else, {username.upper()} ")
    assert client.get("/api/admin/users", headers=_auth(token)).status_code == 403
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["is_admin"] is False
    _make_admin(user_id)
    monkeypatch.delenv("ADMIN_USERNAMES", raising=False)
    assert client.get("/api/admin/users", headers=_auth(token)).status_code == 200


# --- 비콘 ---------------------------------------------------------------------------------------
def test_view_beacon_stores_classified_row_without_raw_identity():
    token, user_id = _signup()
    payload = _view(path="/board/123?x=1#top", referrer="https://www.google.com/search?q=gg", visitor="abc-123", screen_w=390)
    assert client.post("/api/visit", json=payload, headers=_auth(token)).status_code == 204
    row = _row(payload["view_key"])
    assert (row.kind, row.path, row.referrer_host, row.channel, row.device, row.user_id) == ("view", "/board/:id", "www.google.com", "search", "mobile", user_id)
    assert (row.session_key, row.is_new, row.is_landing, row.screen_w, row.dwell_ms) == (payload["session_key"], True, True, 390, 0)
    assert row.visitor_hash and row.visitor_hash != "abc-123" and len(row.visitor_hash) == 24
    assert row.day_kst == admin_mod.day_kst(row.created_ms)

    anonymous = _view(path="/", referrer="https://gg-parrot.vercel.app/board", visitor="", utm_source="naver blog!")
    assert client.post("/api/visit", json=anonymous).status_code == 204
    row = _row(anonymous["view_key"])
    assert (row.referrer_host, row.visitor_hash, row.user_id, row.utm_source, row.channel) == ("", "", None, "naverblog", "campaign")


def test_view_beacon_ignores_duplicate_view_key():
    payload = _view(path="/leaderboard")
    assert client.post("/api/visit", json=payload).status_code == 204
    assert client.post("/api/visit", json={**payload, "path": "/news"}).status_code == 204
    with get_session() as db:
        rows = db.exec(select(Visit).where(Visit.view_key == payload["view_key"])).all()
    assert len(rows) == 1 and rows[0].path == "/leaderboard"


@pytest.mark.parametrize("host, utm, expected", [
    ("", "", "direct"),
    ("", "newsletter", "campaign"),
    ("www.google.com", "kakao", "campaign"),  # utm 이 유입 호스트보다 우선
    ("www.google.com", "", "search"),
    ("m.search.naver.com", "", "search"),
    ("duckduckgo.com", "", "search"),
    ("t.co", "", "social"),
    ("x.com", "", "social"),
    ("mobile.twitter.com", "", "social"),
    ("www.netflix.com", "", "referral"),  # 'x.com' 부분 일치로 소셜이 되면 안 된다
    ("l.instagram.com", "", "social"),
    ("discord.com", "", "social"),
    ("news.ycombinator.com", "", "referral"),
])
def test_channel_classification(host, utm, expected):
    assert admin_mod.classify_channel(host, utm) == expected


def test_referrer_host_treats_own_site_as_direct():
    assert admin_mod.referrer_host("https://gg-parrot.vercel.app/leaderboard") == ""
    assert admin_mod.referrer_host("http://localhost:5173/") == ""
    assert admin_mod.referrer_host("https://blog.naver.com/x/1") == "blog.naver.com"
    assert admin_mod.referrer_host("not a url") == ""


@pytest.mark.parametrize("width, expected", [(0, "unknown"), (320, "mobile"), (767, "mobile"), (768, "tablet"), (1023, "tablet"), (1024, "desktop"), (2560, "desktop")])
def test_device_classification(width, expected):
    assert admin_mod.classify_device(width) == expected


def test_sanitize_path_matches_rum_rules():
    assert admin_mod.sanitize_path("/board/123?x=1") == "/board/:id"
    assert admin_mod.sanitize_path("/board/123/edit") == "/board/:id/edit"
    assert admin_mod.sanitize_path("/s/btc-5pct") == "/s/:slug"
    assert admin_mod.sanitize_path("/x/0123456789abcdef0123/") == "/x/:id"
    assert admin_mod.sanitize_path("/x/3fa85f64-5717-4562-b3fc-2c963f66afa6") == "/x/:id"
    assert admin_mod.sanitize_path("") == "/"
    assert admin_mod.sanitize_path("leaderboard#row") == "/leaderboard"
    assert admin_mod.sanitize_path("/" + "a" * 500) == "/:id"  # 16자 이상 hex 는 식별자로 본다(rum 과 동일)
    assert len(admin_mod.sanitize_path("/" + "z" * 500)) == 120


def test_event_beacon_keeps_whitelisted_names_only():
    ok = _view(kind="event", path="backtest")
    bad = _view(kind="event", path="drop_table")
    assert client.post("/api/visit", json=ok).status_code == 204
    assert client.post("/api/visit", json=bad).status_code == 204
    row = _row(ok["view_key"])
    assert (row.kind, row.path) == ("event", "backtest")
    assert _row(bad["view_key"]) is None


def test_leave_beacon_keeps_max_dwell_and_caps_six_hours():
    payload = _view(path="/news")
    client.post("/api/visit", json=payload)
    assert client.post("/api/visit/leave", json={"view_key": payload["view_key"], "dwell_ms": 5000}).status_code == 204
    assert _row(payload["view_key"]).dwell_ms == 5000
    client.post("/api/visit/leave", json={"view_key": payload["view_key"], "dwell_ms": 3000})
    assert _row(payload["view_key"]).dwell_ms == 5000
    client.post("/api/visit/leave", json={"view_key": payload["view_key"], "dwell_ms": 10 * 3_600_000})
    assert _row(payload["view_key"]).dwell_ms == admin_mod.DWELL_CAP_MS
    assert client.post("/api/visit/leave", json={"view_key": "never-seen", "dwell_ms": 100}).status_code == 204


def test_visit_beacon_is_rate_limited_per_ip(monkeypatch):
    monkeypatch.setattr(main_mod, "_visit_limiter", observability.SlidingWindowRateLimiter(limit=2, window_seconds=60, max_keys=10))
    assert client.post("/api/visit", json=_view()).status_code == 204
    assert client.post("/api/visit", json=_view()).status_code == 204
    assert client.post("/api/visit", json=_view()).status_code == 429


def test_visit_beacon_rate_limit_keys_on_forwarded_client_ip(monkeypatch):
    # 운영은 Vercel rewrite → Render 라 client.host 가 모든 요청에서 같은 프록시 주소다. 첫 X-Forwarded-For 홉으로 나눠야 한 사람의 한도가 사이트 전체 한도가 되지 않는다.
    monkeypatch.setattr(main_mod, "_visit_limiter", observability.SlidingWindowRateLimiter(limit=2, window_seconds=60, max_keys=10))
    a = {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}
    b = {"X-Forwarded-For": "198.51.100.9"}
    assert client.post("/api/visit", json=_view(), headers=a).status_code == 204
    assert client.post("/api/visit", json=_view(), headers=a).status_code == 204
    assert client.post("/api/visit", json=_view(), headers=a).status_code == 429
    assert client.post("/api/visit", json=_view(), headers=b).status_code == 204  # 다른 클라이언트는 자기 한도
    assert client.post("/api/visit", json=_view()).status_code == 204  # 헤더 없으면 client.host 로 되돌아감


def test_beacon_rate_limit_keys_on_forwarded_client_address(monkeypatch):
    # 운영은 Vercel rewrite → Render 프록시 뒤라 request.client.host 가 늘 프록시 주소(모두 한 버킷) — X-Forwarded-For 의
    # 클라이언트 항목(첫 홉; Vercel 이 실제 접속 주소로 덮어쓴다)으로 방문자를 나눈다.
    monkeypatch.setattr(main_mod, "_visit_limiter", observability.SlidingWindowRateLimiter(limit=1, window_seconds=60, max_keys=10))
    a, b = {"x-forwarded-for": "203.0.113.5"}, {"x-forwarded-for": "198.51.100.9"}
    assert client.post("/api/visit", json=_view(), headers=a).status_code == 204
    res = client.post("/api/visit", json=_view(), headers=a)
    assert res.status_code == 429 and res.headers.get("retry-after")
    assert client.post("/api/visit", json=_view(), headers=b).status_code == 204  # 다른 방문자는 다른 버킷
    # Render 가 뒤에 자기 홉을 덧붙여도(client, proxy) 같은 방문자
    assert client.post("/api/visit", json=_view(), headers={"x-forwarded-for": "203.0.113.5, 10.9.9.9"}).status_code == 429


def test_beacon_rejects_oversized_input_instead_of_truncating():
    # 스키마 상한(경로 120 · 키 64 · entry_ids 100 · kind 는 view|event): 넘으면 422 이고 행은 남지 않는다.
    long_path = _view(path="/" + "z" * 200)
    assert client.post("/api/visit", json=long_path).status_code == 422
    assert _row(long_path["view_key"]) is None
    assert client.post("/api/visit", json=_view(kind="purchase")).status_code == 422
    assert client.post("/api/visit/leave", json={"view_key": "k" * 65, "dwell_ms": 1}).status_code == 422
    assert client.post("/api/leaderboard/impressions", json={"entry_ids": list(range(1, 102))}).status_code == 422
    assert client.post("/api/leaderboard/impressions", json={"entry_ids": list(range(1, 101))}).status_code == 204


def test_beacon_body_is_capped_before_parsing():
    # 4 KB 넘는 body 는 Content-Length 로 먼저 413, 선언이 없으면(chunked) 받은 바이트를 세서 413. 비콘 경로에만 건다.
    huge = _view(referrer="https://x.example/" + "r" * (main_mod.BEACON_MAX_BODY_BYTES + 1000))
    res = client.post("/api/visit", json=huge)
    assert res.status_code == 413
    assert _row(huge["view_key"]) is None
    raw = json.dumps(huge).encode()
    chunked = client.post("/api/visit", content=iter([raw[:2000], raw[2000:]]), headers={"content-type": "application/json"})
    assert "content-length" not in chunked.request.headers and chunked.status_code == 413
    assert client.post("/api/visit", headers={"content-length": "abc"}, content=b"{}").status_code == 400
    pat = main_mod._BEACON_PATH_RE
    assert pat.match("/api/visit/") and pat.match("/api/visit/leave") and pat.match("/api/leaderboard/impressions") and pat.match("/api/leaderboard/42/open")
    assert not pat.match("/api/leaderboard/register") and not pat.match("/api/visit/leave/x") and not pat.match("/api/leaderboard/impressions/1")


def test_prune_visits_drops_old_rows():
    with get_session() as db:
        db.add(Visit(day_kst="2020-01-01", path="/", created_ms=1_577_836_800_000))
        db.commit()
        removed = admin_mod.prune_visits(db)
        db.commit()
        assert removed >= 1
        assert db.exec(select(Visit).where(Visit.day_kst == "2020-01-01")).first() is None


# --- 노출 · 열람 비콘 -------------------------------------------------------------------------------
def _entry(db, **overrides) -> LeaderboardEntry:
    now_iso, now_ms = admin_mod._now()
    fields = dict(user_id="anon", nickname="n", username="", symbol="BTCUSDT", macro_json='{"rule_type": "A"}', human_summary="테스트 매크로",
                  created_at=now_iso, created_ms=now_ms)
    fields.update(overrides)
    row = LeaderboardEntry(**fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _events(entry_id: int) -> tuple[int, int, int]:
    with get_session() as db:
        row = db.get(MacroEventDaily, (admin_mod._today_kst(), entry_id))
    return (row.impressions, row.opens, row.unlocks) if row else (0, 0, 0)


def test_impressions_and_open_beacons_count_once_per_call_and_skip_unknown_ids():
    with get_session() as db:
        entry = _entry(db)
    bogus = entry.id + 1_000_000
    assert client.post("/api/leaderboard/impressions", json={"entry_ids": [entry.id, entry.id, bogus]}).status_code == 204
    assert _events(entry.id) == (1, 0, 0) and _events(bogus) == (0, 0, 0)
    client.post("/api/leaderboard/impressions", json={"entry_ids": [entry.id]})
    assert client.post(f"/api/leaderboard/{entry.id}/open").status_code == 204
    assert _events(entry.id) == (2, 1, 0)
    assert client.post(f"/api/leaderboard/{bogus}/open").status_code == 204
    assert client.post("/api/leaderboard/impressions", json={"entry_ids": []}).status_code == 204


# --- 관리자 API 문 · 모양 ---------------------------------------------------------------------------
ADMIN_PATHS = ("/api/admin/users?days=7", "/api/admin/signups?days=7", "/api/admin/macros?days=7", "/api/admin/news", "/api/admin/costs?months=3")


def test_admin_endpoints_closed_unless_flag_set():
    for path in ADMIN_PATHS:
        assert client.get(path).status_code == 401, path
    token, user_id = _signup()
    for path in ADMIN_PATHS:
        assert client.get(path, headers=_auth(token)).status_code == 403, path
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["is_admin"] is False
    _make_admin(user_id)
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["is_admin"] is True
    for path in ADMIN_PATHS:
        assert client.get(path, headers=_auth(token)).status_code == 200, path
    assert client.get("/api/admin/overview", headers=_auth(token)).status_code == 404
    assert client.get("/api/admin/users?days=3", headers=_auth(token)).status_code == 422


def test_admin_report_shapes_follow_contract():
    headers = _admin_client()
    users = client.get("/api/admin/users?days=7", headers=headers).json()
    assert set(users) == {"days", "generated_at", "coverage", "kpis", "series", "daily", "channels", "sources", "pages", "devices", "peak_hours"}
    assert users["days"] == 7 and users["generated_at"].endswith("Z")
    assert set(users["coverage"]) == {"visits_since", "events_since", "macro_events_since", "quests_since"}
    assert set(users["kpis"]) == {"dau", "wau", "mau", "stickiness_pct", "online_5m", "bounce_pct_today", "avg_session_sec_today", "new_visitors"}
    assert set(users["series"]) == {"days", "dau", "wau", "mau"} and all(len(users["series"][k]) == 7 for k in users["series"])
    assert len(users["daily"]) == 7 and users["daily"][-1]["day"] == users["series"]["days"][-1] == admin_mod._today_kst()
    assert set(users["daily"][0]) == {"day", "active", "new", "returning", "sessions", "pageviews", "pv_per_session", "avg_session_sec", "bounce_pct"}
    assert [c["channel"] for c in users["channels"]] == ["direct", "search", "referral", "social", "campaign"]
    assert set(users["channels"][0]) == {"channel", "label", "sessions", "share_pct", "new_visitors", "bounce_pct", "signup_rate_pct"}
    assert [d["device"] for d in users["devices"]] == ["mobile", "desktop", "tablet", "unknown"]
    assert set(users["devices"][0]) == {"device", "label", "sessions", "share_pct", "avg_session_sec"} and users["devices"][3]["share_pct"] is None
    assert [h["hour"] for h in users["peak_hours"]] == list(range(24))
    assert len(users["sources"]) <= 10 and len(users["pages"]) <= 12
    if users["pages"]:
        assert set(users["pages"][0]) == {"path", "label", "pageviews", "avg_dwell_sec", "landings", "exit_pct"}

    signups = client.get("/api/admin/signups?days=7", headers=headers).json()
    assert set(signups) == {"days", "generated_at", "coverage", "revisit_days", "kpis", "series", "daily", "funnel_acquisition", "funnel_members", "cohorts", "methods"}
    assert set(signups["kpis"]) == {"total_users", "signups", "signup_rate_pct", "revisit_pct", "d7_retention_pct", "deletions", "new_visitors"}
    assert set(signups["series"]) == {"days", "signups", "signup_rate_pct"} and len(signups["series"]["signups"]) == 7
    assert len(signups["daily"]) == 7
    assert set(signups["daily"][0]) == {"day", "signups", "signup_rate_pct", "deletions", "cumulative", "first_backtest_same_day", "quest_active"}
    assert [(f["step"], f["key"]) for f in signups["funnel_acquisition"]] == list(enumerate(["visit", "builder", "backtest", "signup"], start=1))
    assert [(f["step"], f["key"]) for f in signups["funnel_members"]] == list(enumerate(["signup", "macro_register", "macro_unlock", "agent_start"], start=1))
    assert all(set(f) == {"step", "key", "label", "count", "pct_of_first", "pct_of_prev"} for f in signups["funnel_acquisition"] + signups["funnel_members"])
    assert [m["method"] for m in signups["methods"]] == ["google", "email"]
    assert set(signups["methods"][0]) == {"method", "label", "signups", "share_pct", "d7_retention_pct", "first_backtest_same_day"}
    assert signups["cohorts"] and set(signups["cohorts"][0]) == {"week", "monday", "signups", "measurable", "partial", "d1", "d3", "d7", "d14", "d30"}
    assert signups["kpis"]["signups"] >= 1  # 이 테스트 파일이 만든 계정들

    macros = client.get("/api/admin/macros?days=7", headers=headers).json()
    assert set(macros) == {"days", "generated_at", "coverage", "paper_liveness", "kpis", "series", "daily", "top", "sessions"}
    assert macros["paper_liveness"] == "reported"
    assert set(macros["kpis"]) == {"registered", "unlocks", "revenue_points", "creator_points", "ctr_pct", "cvr_pct", "agents_running"}
    assert set(macros["series"]) == {"days", "registered", "unlocks", "ctr_pct", "cvr_pct"} and len(macros["series"]["ctr_pct"]) == 7
    assert len(macros["daily"]) == 7
    assert set(macros["daily"][0]) == {"day", "registered", "impressions", "opens", "ctr_pct", "unlocks", "cvr_pct", "revenue_points", "creator_points"}
    assert [s["kind"] for s in macros["sessions"]] == ["agent", "paper"]
    assert set(macros["sessions"][0]) == {"kind", "label", "running", "stale", "stopped", "error", "started_period", "mainnet"}
    assert len(macros["top"]) <= 20

    news = client.get("/api/admin/news", headers=headers).json()
    assert set(news) == {"generated_at", "kpis", "engines", "hourly", "sources", "board", "failing", "enrichment"}
    assert set(news["kpis"]) == {"tickers_ok", "tickers_total", "tickers_failing", "articles_today", "failures_today", "pending", "ai_budget_used", "ai_budget_limit"}
    assert [e["key"] for e in news["enrichment"]] == ["pending", "due", "given_up_today", "given_up", "stall", "public_next", "ai_budget", "coindesk"]
    assert all(set(e) == {"key", "label", "value"} for e in news["enrichment"])

    costs = client.get("/api/admin/costs?months=3", headers=headers).json()
    assert set(costs) >= {"generated_at", "month", "month_days_elapsed", "kpis", "monthly", "providers", "purposes", "daily"}
    assert set(costs["kpis"]) >= {"month_total_usd", "last_month_total_usd", "gemini_month_usd", "gemini_calls_month", "gemini_today_usd"}


def test_admin_reports_are_cached_for_a_minute():
    headers = _admin_client()
    first = client.get("/api/admin/users?days=7", headers=headers).json()
    client.post("/api/visit", json=_view(path="/guide"))
    assert client.get("/api/admin/users?days=7", headers=headers).json()["generated_at"] == first["generated_at"]
    admin_mod.clear_cache()
    refreshed = client.get("/api/admin/users?days=7", headers=headers).json()
    assert refreshed["daily"][-1]["pageviews"] >= first["daily"][-1]["pageviews"] + 1


# --- 이웃 모듈(collector_runs · api_usage)이 없거나 터져도 화면은 산다 -------------------------------------
def test_news_and_costs_degrade_gracefully_without_sibling_modules(monkeypatch):
    headers = _admin_client()
    monkeypatch.setitem(sys.modules, "app.collector_runs", None)  # import 가 ImportError 를 내게
    monkeypatch.setitem(sys.modules, "app.api_usage", None)
    news = client.get("/api/admin/news", headers=headers).json()
    assert (news["engines"], news["hourly"], news["sources"]) == ([], [], [])
    assert news["board"] is not None and news["enrichment"][0]["key"] == "pending"
    costs = client.get("/api/admin/costs?months=6", headers=headers).json()
    assert costs["month"] == admin_mod._today_kst()[:7] and costs["month_days_elapsed"] == int(admin_mod._today_kst()[8:10])
    assert (costs["monthly"], costs["providers"], costs["purposes"], costs["daily"]) == ([], [], [], [])
    assert costs["kpis"]["month_total_usd"] == 0


def test_news_and_costs_pass_sibling_reports_through(monkeypatch):
    headers = _admin_client()
    fake_engines = {"engines": [{"engine": "position_news", "status": "ok"}], "hourly": [{"hour": 0, "items": 1, "failures": 0}], "sources": []}
    monkeypatch.setitem(sys.modules, "app.collector_runs", types.SimpleNamespace(engines_report=lambda db, **kw: fake_engines))
    fake_costs = {"generated_at": "x", "month": "2026-09", "month_days_elapsed": 17, "kpis": {}, "monthly": [{"month": "2026-09"}], "providers": [], "purposes": [], "daily": []}
    monkeypatch.setitem(sys.modules, "app.api_usage", types.SimpleNamespace(costs_report=lambda db, months: {**fake_costs, "months": months}))
    news = client.get("/api/admin/news", headers=headers).json()
    assert news["engines"] == fake_engines["engines"] and news["hourly"] == fake_engines["hourly"]
    costs = client.get("/api/admin/costs?months=4", headers=headers).json()
    assert costs["monthly"] == [{"month": "2026-09"}] and costs["months"] == 4


# --- 결정적 시나리오(격리된 SQLite) --------------------------------------------------------------------
@pytest.fixture
def scenario(tmp_path):
    """방문자 2(v1 데스크톱·검색, v2 모바일·소셜) · 세션 2 · 이탈 1 · 당일 가입 1(v1=u1) · 10일 전 구글 가입(u2, 이틀 전 재방문) ·
    20일 전 가입해 이틀 전 탈퇴한 u3(가입 방법 기록 없음).

    공유 테스트 DB 에는 다른 테스트의 계정·방문이 섞이므로, 값을 정확히 맞추는 시나리오는 격리된 SQLite 파일에 만든다.
    방문 기록은 10일 전(d10)부터, 이벤트·퀘스트 기록은 오늘부터 — coverage 가 그 날짜를 읽는다.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'admin-scenario.db'}")
    SQLModel.metadata.create_all(engine)
    _, now_ms = admin_mod._now()
    base = now_ms - 240_000  # 4분 전 → online_5m 에도 잡히고, 30분 안이라 '아직 보고 있는' 세션(종료 아님)
    day = admin_mod.day_kst(base)
    d2, d10, d20 = admin_mod._shift_day(day, -2), admin_mod._shift_day(day, -10), admin_mod._shift_day(day, -20)
    noon = lambda d: admin_mod._day_start_ms(d) + 12 * 3_600_000  # noqa: E731
    iso = admin_mod._iso_from_ms
    with Session(engine) as db:
        u1 = User(email="u1@ex.com", username="u1", password_hash="pbkdf2", signup_method="email", created_at=iso(base))
        u2 = User(email="u2@ex.com", username="u2", password_hash="", signup_method="google", created_at=iso(noon(d10)))
        u3 = User(email="u3@ex.com", username="u3", password_hash="", created_at=iso(noon(d20)), is_deleted=True, deleted_at=iso(noon(d2)))
        db.add_all([u1, u2, u3])
        db.commit()
        db.refresh(u1), db.refresh(u2), db.refresh(u3)
        v1 = dict(visitor_hash="v1", session_key="s1", channel="search", device="desktop", referrer_host="www.google.com", user_id=u1.id,
                  is_new=True, screen_w=1440)
        db.add_all([
            Visit(day_kst=day, path="/", created_ms=base, dwell_ms=5000, is_landing=True, view_key="k1", **v1),
            Visit(day_kst=day, path="/builder", created_ms=base + 60_000, dwell_ms=10_000, view_key="k2", **v1),
            Visit(day_kst=day, path="backtest", kind="event", created_ms=base + 61_000, view_key="k3", **v1),
            Visit(day_kst=day, path="/leaderboard", created_ms=base + 5_000, is_landing=True, view_key="k4", visitor_hash="v2", session_key="s2",
                  channel="social", device="mobile", referrer_host="t.co", is_new=True, screen_w=390),
            Visit(day_kst=d10, path="/news", created_ms=noon(d10), is_landing=True, view_key="k5", visitor_hash="v3", session_key="s3",
                  channel="direct", device="desktop", is_new=True, user_id=u2.id, screen_w=1280),
            Visit(day_kst=d2, path="/news", created_ms=noon(d2), is_landing=True, view_key="k6", visitor_hash="v3", session_key="s4",
                  channel="direct", device="desktop", user_id=u2.id, screen_w=1280),
            DailyQuestClaim(user_id=u1.id, date_kst=day, quest_key="backtest_run", reward=10, created_at=iso(base), created_ms=base),
        ])
        db.commit()
        yield {"db": db, "day": day, "d2": d2, "d10": d10, "d20": d20, "base": base, "u1": u1.id, "u2": u2.id, "noon": noon, "iso": iso}


def _by_day(rows, day):
    return next(row for row in rows if row["day"] == day)


def test_users_scenario(scenario):
    db, day, d2, d10 = scenario["db"], scenario["day"], scenario["d2"], scenario["d10"]
    report = admin_mod._users_report(db, 7)
    assert report["coverage"] == {"visits_since": d10, "events_since": day, "macro_events_since": None, "quests_since": day}
    today = _by_day(report["daily"], day)
    # 세션 시간 = 체류 합: s1 은 5초 + 10초, s2 는 마지막 뷰의 체류를 몰라 제외 → 평균 15초(첫·끝 시각 차 35초가 아니다)
    assert today == {"day": day, "active": 2, "new": 2, "returning": 0, "sessions": 2, "pageviews": 3, "pv_per_session": 1.5,
                     "avg_session_sec": 15, "bounce_pct": 50.0}
    two_days_ago = _by_day(report["daily"], d2)
    assert (two_days_ago["active"], two_days_ago["new"], two_days_ago["returning"], two_days_ago["sessions"], two_days_ago["bounce_pct"]) == (1, 0, 1, 1, 100.0)
    assert two_days_ago["avg_session_sec"] is None  # 유일한 뷰의 체류를 모른다 — 0초가 아니라 측정 불가
    assert report["kpis"]["online_5m"] == 2 and report["kpis"]["wau"] == 3 and report["kpis"]["mau"] == 3 and report["kpis"]["new_visitors"] == 2
    if report["series"]["days"][-1] == day:
        assert report["kpis"]["dau"] == 2 and report["kpis"]["stickiness_pct"] == 66.7
        assert report["kpis"]["bounce_pct_today"] == 50.0 and report["kpis"]["avg_session_sec_today"] == 15
    channels = {c["channel"]: c for c in report["channels"]}
    assert channels["search"] == {"channel": "search", "label": "검색", "sessions": 1, "share_pct": 33.3, "new_visitors": 1, "bounce_pct": 0.0, "signup_rate_pct": 100.0}
    assert (channels["social"]["sessions"], channels["social"]["bounce_pct"], channels["social"]["signup_rate_pct"]) == (1, 100.0, 0.0)
    assert (channels["direct"]["sessions"], channels["direct"]["new_visitors"], channels["direct"]["signup_rate_pct"]) == (1, 0, None)
    assert channels["referral"] == {"channel": "referral", "label": "추천 링크", "sessions": 0, "share_pct": 0.0, "new_visitors": 0, "bounce_pct": None, "signup_rate_pct": None}
    sources = {s["source"]: s for s in report["sources"]}
    assert sources["www.google.com"] == {"source": "www.google.com", "channel": "search", "sessions": 1, "signups": 1}
    assert sources["t.co"]["channel"] == "social" and sources["t.co"]["signups"] == 0
    pages = {p["path"]: p for p in report["pages"]}
    # 4분 전 세션들은 아직 보고 있을 수 있어 종료로 세지 않는다. 이틀 전 /news 세션만 종료.
    assert pages["/"] == {"path": "/", "label": "홈", "pageviews": 1, "avg_dwell_sec": 5, "landings": 1, "exit_pct": 0.0}
    assert pages["/builder"] == {"path": "/builder", "label": "직접 만들기", "pageviews": 1, "avg_dwell_sec": 10, "landings": 0, "exit_pct": 0.0}
    assert (pages["/leaderboard"]["landings"], pages["/leaderboard"]["exit_pct"], pages["/leaderboard"]["avg_dwell_sec"]) == (1, 0.0, None)
    assert (pages["/news"]["pageviews"], pages["/news"]["exit_pct"]) == (1, 100.0)
    assert [p["path"] for p in report["pages"]] == ["/", "/builder", "/leaderboard", "/news"]
    devices = {d["device"]: d for d in report["devices"]}
    assert [d["device"] for d in report["devices"]] == ["mobile", "desktop", "tablet", "unknown"]
    assert (devices["mobile"]["sessions"], devices["desktop"]["sessions"], devices["tablet"]["sessions"], devices["unknown"]["sessions"]) == (1, 2, 0, 0)
    assert devices["desktop"]["avg_session_sec"] == 15 and devices["mobile"]["share_pct"] == 33.3 and devices["unknown"]["share_pct"] is None
    assert sum(h["sessions"] for h in report["peak_hours"]) == 3


def test_channel_signup_rate_counts_only_signups_on_the_first_touch_day(tmp_path):
    """채널 가입 전환의 분자는 signups_report 와 같다 — 신규로 온 **그날** 가입한 사람만(F4).
    va: 1일 검색으로 처음 옴 → 3일 직접 접속 세션에서 가입 → 어느 채널의 전환도 아니다(예전엔 검색 100% 로 잡혔다).
    vb: 1일 검색으로 처음 와 그날 가입 → 검색 채널 전환."""
    engine = create_engine(f"sqlite:///{tmp_path / 'first-touch.db'}")
    SQLModel.metadata.create_all(engine)
    today = admin_mod._today_kst()
    d1, d3 = admin_mod._shift_day(today, -6), admin_mod._shift_day(today, -4)
    noon = lambda d: admin_mod._day_start_ms(d) + 12 * 3_600_000  # noqa: E731
    iso = admin_mod._iso_from_ms
    with Session(engine) as db:
        ua = User(email="ua@ex.com", username="ua", password_hash="x", signup_method="email", created_at=iso(noon(d3)))
        ub = User(email="ub@ex.com", username="ub", password_hash="x", signup_method="email", created_at=iso(noon(d1)))
        db.add_all([ua, ub])
        db.commit()
        db.refresh(ua), db.refresh(ub)
        search = dict(channel="search", referrer_host="www.google.com", device="desktop", screen_w=1440, is_landing=True, path="/")
        db.add_all([
            Visit(day_kst=d1, created_ms=noon(d1), visitor_hash="va", session_key="sa1", is_new=True, view_key="a1", **search),
            Visit(day_kst=d3, created_ms=noon(d3), visitor_hash="va", session_key="sa2", view_key="a2", user_id=ua.id, path="/",
                  channel="direct", device="desktop", screen_w=1440, is_landing=True),
            Visit(day_kst=d1, created_ms=noon(d1) + 1000, visitor_hash="vb", session_key="sb1", is_new=True, view_key="b1", user_id=ub.id, **search),
        ])
        db.commit()
        users = admin_mod._users_report(db, 7)
        signups = admin_mod._signups_report(db, 7)
    channels = {c["channel"]: c for c in users["channels"]}
    assert (channels["search"]["new_visitors"], channels["search"]["signup_rate_pct"]) == (2, 50.0)
    assert (channels["direct"]["new_visitors"], channels["direct"]["signup_rate_pct"]) == (0, None)
    # 두 표가 같은 분자·분모를 쓴다: 신규 방문자 2 · 전환 1.
    assert (signups["kpis"]["new_visitors"], signups["kpis"]["signup_rate_pct"]) == (2, 50.0)


def test_signups_scenario_with_cohorts(scenario):
    db, day, d2, d10, d20 = scenario["db"], scenario["day"], scenario["d2"], scenario["d10"], scenario["d20"]
    week = admin_mod._monday(day)
    report = admin_mod._signups_report(db, 7)
    since_day = report["series"]["days"][0]
    assert report["coverage"]["visits_since"] == d10 and report["revisit_days"] == 7
    # 탈퇴(u3)는 회원 수·가입 수에 없고, 탈퇴 수는 탈퇴한 날(d2)에 찍힌다. 전환율 = 신규 방문자 2(v1·v2) 중 당일 가입 1.
    assert report["kpis"] == {"total_users": 2, "signups": 1, "signup_rate_pct": 50.0, "revisit_pct": 0.0, "d7_retention_pct": None,
                              "deletions": 1, "new_visitors": 2}
    today = _by_day(report["daily"], day)
    assert (today["signups"], today["signup_rate_pct"], today["cumulative"], today["first_backtest_same_day"], today["quest_active"]) == (1, 50.0, 2, 1, 1)
    assert today["deletions"] == 0
    two_days_ago = _by_day(report["daily"], d2)
    # 이틀 전: 신규 방문자가 없어 전환율 분모 0 → None. 이벤트·퀘스트 기록은 오늘부터라 그 전 날짜는 측정 불가(None).
    assert (two_days_ago["deletions"], two_days_ago["signup_rate_pct"], two_days_ago["first_backtest_same_day"], two_days_ago["quest_active"]) == (1, None, None, None)
    assert [(f["step"], f["key"], f["count"], f["pct_of_first"], f["pct_of_prev"]) for f in report["funnel_acquisition"]] == [
        (1, "visit", 3, 100.0, None), (2, "builder", 1, 33.3, 33.3), (3, "backtest", 1, 33.3, 100.0), (4, "signup", 1, 33.3, 100.0)]
    assert [(f["key"], f["count"], f["pct_of_first"], f["pct_of_prev"]) for f in report["funnel_members"]] == [
        ("signup", 1, 100.0, None), ("macro_register", 0, 0.0, 0.0), ("macro_unlock", 0, 0.0, None), ("agent_start", 0, 0.0, None)]
    cohorts = {c["monday"]: c for c in report["cohorts"]}
    assert cohorts[week] == {"week": f"{week[5:]} 주", "monday": week, "signups": 1, "measurable": 1,
                             "partial": week < since_day or admin_mod._shift_day(week, 6) > day,
                             "d1": None, "d3": None, "d7": None, "d14": None, "d30": None}
    assert admin_mod._monday(d20) not in cohorts  # 탈퇴 회원은 코호트에 없다
    methods = {m["method"]: m for m in report["methods"]}
    assert methods["email"] == {"method": "email", "label": "이메일 가입", "signups": 1, "share_pct": 100.0, "d7_retention_pct": None, "first_backtest_same_day": 1}
    assert methods["google"] == {"method": "google", "label": "구글 간편 가입", "signups": 0, "share_pct": 0.0, "d7_retention_pct": None, "first_backtest_same_day": 0}

    wide = admin_mod._signups_report(db, 30)
    assert wide["kpis"]["signups"] == 2 and wide["kpis"]["d7_retention_pct"] == 100.0 and wide["kpis"]["revisit_pct"] == 33.3
    assert wide["kpis"]["signup_rate_pct"] == 66.7 and wide["kpis"]["new_visitors"] == 3 and wide["kpis"]["deletions"] == 1
    assert wide["revisit_days"] == 11  # 방문 기록은 11일치뿐 — 화면 라벨이 '최근 11일' 이 된다
    assert _by_day(wide["daily"], d10)["signup_rate_pct"] == 100.0
    assert _by_day(wide["daily"], d20) == {"day": d20, "signups": 0, "signup_rate_pct": None, "deletions": 0, "cumulative": 0,
                                           "first_backtest_same_day": None, "quest_active": None}
    cohorts = {c["monday"]: c for c in wide["cohorts"]}
    old_week = admin_mod._monday(d10)
    assert cohorts[old_week] == {"week": f"{old_week[5:]} 주", "monday": old_week, "signups": 1, "measurable": 1, "partial": False,
                                 "d1": 100.0, "d3": 100.0, "d7": 100.0, "d14": None, "d30": None}
    assert sorted(cohorts) == [old_week, week]
    methods = {m["method"]: m for m in wide["methods"]}
    assert (methods["google"]["signups"], methods["google"]["share_pct"], methods["google"]["d7_retention_pct"]) == (1, 50.0, 100.0)
    assert (methods["email"]["signups"], methods["email"]["share_pct"], methods["email"]["d7_retention_pct"]) == (1, 50.0, None)


def test_macros_scenario(scenario):
    db, day, base, u1, u2 = scenario["db"], scenario["day"], scenario["base"], scenario["u1"], scenario["u2"]
    d1 = admin_mod._shift_day(day, -1)
    noon, iso = scenario["noon"], scenario["iso"]
    paper = PaperSession(macro_id="m", symbol="BTCUSDT", started_at=iso(noon(d1)), current_return=12.3456)
    db.add(paper)
    db.commit()
    db.refresh(paper)
    entry = LeaderboardEntry(user_id="anon", nickname="n", symbol="BTCUSDT", macro_json='{"rule_type": "A"}', human_summary="x" * 50,
                             owner_user_id=u2, created_at=iso(noon(d1)), created_ms=noon(d1), paper_session_id=paper.id)
    bot = LeaderboardEntry(user_id="ai", nickname="bot", symbol="ETHUSDT", macro_json="{}", human_summary="ai", is_ai=True, created_at=iso(base), created_ms=base)
    db.add_all([entry, bot])
    db.commit()
    db.refresh(entry)
    _, now_ms = admin_mod._now()
    db.add_all([
        MacroEventDaily(day_kst=day, entry_id=entry.id, impressions=10, opens=2, unlocks=1),
        MacroUnlock(user_id=u1, entry_id=entry.id, price=100, created_at=iso(base)),
        PointLedger(user_id=u2, delta=70, balance_after=70, reason="unlock_earn", ref=f"entry:{entry.id}", created_at=iso(base), created_ms=base),
        RunSession(user_id=u2, started_at=iso(base), status="running", testnet=False, last_heartbeat_at=iso(now_ms - 60_000)),
        # status 는 running 인데 하트비트가 6분 넘게 끊긴 세션 — 실행기가 죽은 것. 실행 중이 아니라 stale.
        RunSession(user_id=u1, started_at=iso(base), status="running", testnet=False, last_heartbeat_at=iso(now_ms - 6 * 60_000)),
        RunSession(user_id=u1, started_at=iso(base), status="stopped", stopped_at=iso(base)),
    ])
    db.commit()
    report = admin_mod._macros_report(db, 7)
    assert report["coverage"]["macro_events_since"] == day and report["paper_liveness"] == "reported"
    assert report["kpis"] == {"registered": 1, "unlocks": 1, "revenue_points": 100, "creator_points": 70, "ctr_pct": 20.0, "cvr_pct": 50.0, "agents_running": 1}
    assert _by_day(report["daily"], day) == {"day": day, "registered": 0, "impressions": 10, "opens": 2, "ctr_pct": 20.0, "unlocks": 1, "cvr_pct": 50.0,
                                             "revenue_points": 100, "creator_points": 70}
    # 카운터가 생기기 전날: 노출·열람·구매·비율은 측정 불가(None), 결제 기록에서 읽는 매출은 숫자.
    assert _by_day(report["daily"], d1) == {"day": d1, "registered": 1, "impressions": None, "opens": None, "ctr_pct": None, "unlocks": None,
                                            "cvr_pct": None, "revenue_points": 0, "creator_points": 0}
    assert report["top"] == [{"entry_id": entry.id, "name": "x" * 40, "creator": "u2", "symbol": "BTCUSDT", "registered_day": d1, "impressions": 10, "opens": 2,
                              "ctr_pct": 20.0, "unlocks": 1, "cvr_pct": 50.0, "revenue_points": 100, "creator_points": 70, "paper_return_pct": 12.35}]
    agent, paper_row = report["sessions"]
    assert agent == {"kind": "agent", "label": "에이전트 (실행기)", "running": 1, "stale": 1, "stopped": 1, "error": 0, "started_period": 3, "mainnet": 1}
    assert paper_row == {"kind": "paper", "label": "모의 (페이퍼) 세션", "running": 1, "stale": 0, "stopped": 0, "error": 0, "started_period": 1, "mainnet": 0}


# --- 정의 하나씩 --------------------------------------------------------------------------------------
def test_pct_and_ratio_return_none_when_denominator_is_zero():
    """가짜 0% 금지 — 분모가 없으면 값이 없다. JSON 으로는 null."""
    assert admin_mod._pct(1, 0) is None and admin_mod._pct(0, 0) is None and admin_mod._ratio(3, 0) is None
    assert admin_mod._pct(1, 4) == 25.0 and admin_mod._ratio(3, 2) == 1.5
    assert json.dumps({"x": admin_mod._pct(1, 0)}) == '{"x": null}'


def test_signup_method_rule_prefers_column_and_never_guesses_for_deleted_rows():
    assert admin_mod.signup_method_of("google", True, False) == "google"  # 구글 가입 뒤 비밀번호를 만들어도 그대로
    assert admin_mod.signup_method_of("email", False, False) == "email"
    assert admin_mod.signup_method_of("", True, False) == "email" and admin_mod.signup_method_of("", False, False) == "google"
    assert admin_mod.signup_method_of("", False, True) == "unknown"  # 탈퇴 행은 해시를 비우므로 추정하면 전부 구글이 된다


def _rows(*views):
    """(session_key, path, created_ms, dwell_ms, day, screen_w) → _view_rows 와 같은 열 순서."""
    return [(key, path, ms, dwell, False, False, "direct", "desktop", f"vis-{key}", None, day, "", "", width)
            for key, path, ms, dwell, day, width in sorted(views, key=lambda v: v[2])]


def test_session_seconds_are_dwell_sums_and_unknown_tails_are_excluded():
    _, now_ms = admin_mod._now()
    t = now_ms - 3 * 3_600_000
    day = admin_mod.day_kst(t)
    rows = _rows(
        ("a", "/", t, 0, day, 1440), ("a", "/news", t + 31 * 60_000, 0, day, 1440),  # 31분 간격 → 30분 상한, 마지막 뷰는 모름
        ("b", "/guide", t, 0, day, 1440),  # 뷰 하나, 비콘 없음 → 세션 시간 모름
        ("c", "/", t, 0, day, 1440), ("c", "/board", t + 10_000, 4000, day, 1440),  # 10초 간격 + 비콘 4초
        ("d", "/", t, 0, day, 0),  # 화면 너비 0 → 기기 알 수 없음
    )
    sessions, pages = admin_mod._fold_sessions(rows, window_set={day}, now_ms=now_ms)
    assert sessions["a"].seconds == 1800 and sessions["b"].seconds is None and sessions["c"].seconds == 14
    assert admin_mod._avg_seconds(sessions.values()) == 907  # (1800 + 14) / 2 — b·d 는 분모에 없다
    assert admin_mod._avg_seconds([sessions["b"]]) is None
    assert sessions["d"].device == "unknown" and sessions["c"].device == "desktop"
    # 페이지 체류도 같은 채움 규칙: '/' 는 a(1800초)·c(10초) 둘, /guide·/news·/board(비콘 4초) 는 아는 것만
    assert (pages["/"]["dwell_sum"], pages["/"]["dwell_n"]) == (1_810_000, 2)
    assert (pages["/guide"]["dwell_n"], pages["/news"]["dwell_n"], pages["/board"]["dwell_sum"]) == (0, 0, 4000)
    # 세 시간 전 세션은 모두 끝났다 — 종료 페이지가 센다
    assert (pages["/news"]["exits"], pages["/guide"]["exits"], pages["/board"]["exits"], pages["/"]["exits"]) == (1, 1, 1, 1)


def test_live_sessions_are_not_exits_and_out_of_window_sessions_are_dropped():
    _, now_ms = admin_mod._now()
    today = admin_mod.day_kst(now_ms)
    yesterday = admin_mod._shift_day(today, -1)
    rows = _rows(
        ("live", "/", now_ms - 20 * 60_000, 0, today, 1440), ("live", "/news", now_ms - 10 * 60_000, 0, today, 1440),  # 10분 전 마지막 뷰 → 아직 보는 중
        ("old", "/", now_ms - 40 * 60_000, 0, today, 1440),  # 40분 전 → 종료
        ("cross", "/", admin_mod._day_start_ms(today) - 60_000, 0, yesterday, 1440),  # 어제 시작한 세션의
        ("cross", "/board", admin_mod._day_start_ms(today) + 60_000, 0, today, 1440),  # 오늘 뷰 — 세션 날짜(어제)가 창 밖이라 버린다
    )
    sessions, pages = admin_mod._fold_sessions(rows, window_set={today}, now_ms=now_ms)
    assert set(sessions) == {"live", "old"}
    assert (pages["/news"]["exits"], pages["/"]["exits"], pages["/board"]["views"]) == (0, 1, 0)
    assert sum(s.views for s in sessions.values()) == sum(p["views"] for p in pages.values()) == 3


def test_cohort_cells_before_visit_coverage_are_unmeasurable_not_churned(tmp_path):
    """8/31 주 가입자 419명이 '전원 이탈 0%' 로 보이던 문제 — 가입+N 이 방문 기록 시작 전이면 분모에서 뺀다."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cohort.db'}")
    SQLModel.metadata.create_all(engine)
    today = admin_mod._today_kst()
    d5, d6, d20 = (admin_mod._shift_day(today, -n) for n in (5, 6, 20))
    noon = lambda d: admin_mod._day_start_ms(d) + 12 * 3_600_000  # noqa: E731
    with Session(engine) as db:
        old = User(email="o@ex.com", username="o", password_hash="x", signup_method="email", created_at=admin_mod._iso_from_ms(noon(d20)))
        fresh = User(email="f@ex.com", username="f", password_hash="x", signup_method="email", created_at=admin_mod._iso_from_ms(noon(d6)))
        db.add_all([old, fresh])
        db.commit()
        db.refresh(old), db.refresh(fresh)
        # 방문 기록은 5일 전부터 — fresh 만 그날 다시 왔다
        db.add(Visit(day_kst=d5, path="/", created_ms=noon(d5), visitor_hash="vf", session_key="sf", user_id=fresh.id, view_key="kf"))
        db.commit()
        report = admin_mod._signups_report(db, 30)
    cohorts = {c["monday"]: c for c in report["cohorts"]}
    old_row = cohorts[admin_mod._monday(d20)]
    # old: 가입+1 ~ 가입+14 가 모두 방문 기록 이전 → 측정 불가(None). d30 은 아직 안 지남(None). 0% 가 어디에도 없다.
    assert (old_row["measurable"], old_row["d1"], old_row["d3"], old_row["d7"], old_row["d14"], old_row["d30"]) == (0, None, None, None, None, None)
    fresh_row = cohorts[admin_mod._monday(d6)]
    # fresh: 가입+1 = d5 = 기록 시작일 → 측정 가능, 그날 왔으니 100%. 가입+3 = d3 도 측정 가능인데 안 왔으니 0%. 가입+7 은 미래.
    assert (fresh_row["measurable"], fresh_row["d1"], fresh_row["d3"], fresh_row["d7"]) == (1, 100.0, 0.0, None)
    assert report["kpis"]["d7_retention_pct"] is None  # 측정 가능한 회원이 없다 → 0% 가 아니라 None
    assert report["coverage"]["visits_since"] == d5 and report["revisit_days"] == 6


def test_signups_without_any_visit_rows_report_unmeasurable_rates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'novisit.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(email="a@ex.com", username="a", password_hash="x", signup_method="email", created_at=admin_mod._now()[0]))
        db.commit()
        report = admin_mod._signups_report(db, 7)
    assert report["coverage"] == {"visits_since": None, "events_since": None, "macro_events_since": None, "quests_since": None}
    assert report["revisit_days"] is None and report["kpis"]["signup_rate_pct"] is None and report["kpis"]["revisit_pct"] is None
    assert all(row["signup_rate_pct"] is None and row["first_backtest_same_day"] is None and row["quest_active"] is None for row in report["daily"])
    assert report["cohorts"][-1]["measurable"] == 0 and report["kpis"]["signups"] == 1
    assert report["methods"][1]["first_backtest_same_day"] is None


def test_signup_records_method_and_close_keeps_it_while_stamping_deleted_at():
    from app.profile import close_account_rows
    _token, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        assert user.signup_method == "email" and user.deleted_at == ""
        close_account_rows(db, user)
        db.commit()
        db.refresh(user)
        assert user.is_deleted and user.password_hash == "" and user.signup_method == "email"
        assert user.deleted_at.endswith("Z") and admin_mod._kst_day_from_iso(user.deleted_at) == admin_mod._today_kst()


def test_duplicate_view_key_race_is_swallowed_by_the_unique_index(monkeypatch):
    """SELECT-then-INSERT 사이로 같은 비콘이 끼어들면 유니크 인덱스가 막고, 기록 함수는 경고 없이 None 을 돌려준다."""
    payload = _view(path="/news")
    assert client.post("/api/visit", json=payload).status_code == 204
    monkeypatch.setattr(admin_mod, "_view_key_exists", lambda db, key: False)  # 사전 확인이 놓친 상황을 흉내 낸다
    with get_session() as db:
        assert admin_mod.record_visit(db, path="/board", view_key=payload["view_key"], user_id=None, secret="s") is None
        rows = db.exec(select(Visit).where(Visit.view_key == payload["view_key"])).all()
    assert len(rows) == 1 and rows[0].path == "/news"


def test_init_db_boots_when_a_raced_duplicate_view_key_predates_the_unique_index(tmp_path, monkeypatch, caplog):
    """인덱스가 생기기 전에 경쟁으로 들어온 중복 view_key 가 있는 개발 DB 도 기동해야 한다(F10) — 인덱스는 못 만들어도
    경고만 남기고 넘어가며, 앞서 확정한 마이그레이션은 되돌리지 않는다."""
    from app import db as db_mod
    engine = create_engine(f"sqlite:///{tmp_path / 'dupes.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Visit(day_kst="2026-09-18", path="/", view_key="dup"), Visit(day_kst="2026-09-18", path="/", view_key="dup")])
        db.commit()
    monkeypatch.setattr(db_mod, "_engine", engine)
    with caplog.at_level(logging.WARNING, logger="app.db"):
        db_mod.init_db()  # raise 하면 안 된다
    assert any("ux_visit_view_key" in record.getMessage() for record in caplog.records)
    with engine.connect() as conn:
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(visit)")}
        rows = conn.exec_driver_sql("SELECT count(*) FROM visit WHERE view_key = 'dup'").scalar()
    assert "ux_visit_view_key" not in indexes and rows == 2, "중복 행은 손대지 않고 인덱스만 건너뛴다"
    assert "ix_newsarticle_enrichment" in {row[1] for row in engine.connect().exec_driver_sql("PRAGMA index_list(newsarticle)")}, \
        "인덱스 실패 뒤의 마이그레이션도 계속 돈다"


def test_online_5m_counts_a_view_that_is_still_being_read(monkeypatch):
    """하트비트가 dwell 을 갱신하므로 8분 전에 열어 지금까지 읽고 있는 뷰도 '지금 접속' 이다."""
    _, now_ms = admin_mod._now()
    key = secrets.token_hex(6)
    with get_session() as db:
        db.add(Visit(day_kst=admin_mod.day_kst(now_ms), path="/news", created_ms=now_ms - 8 * 60_000, dwell_ms=0,
                     visitor_hash="online-" + key, session_key=key, view_key=key))
        db.commit()
        before = admin_mod._online_5m(db, now_ms)
        admin_mod.record_leave(db, view_key=key, dwell_ms=7 * 60_000)
        assert admin_mod._online_5m(db, now_ms) == before + 1


def test_report_cache_keys_include_the_kst_date(monkeypatch):
    headers = _admin_client()
    client.get("/api/admin/users?days=7", headers=headers)
    client.get("/api/admin/signups?days=7", headers=headers)
    client.get("/api/admin/macros?days=7", headers=headers)
    today = admin_mod._today_kst()
    assert {f"users:7:{today}", f"signups:7:{today}", f"macros:7:{today}"} <= set(admin_mod._cache)
    monkeypatch.setattr(admin_mod, "_today_kst", lambda: "2099-01-01")  # 자정을 넘기면 캐시가 빗나가 새로 센다
    client.get("/api/admin/users?days=7", headers=headers)
    assert "users:7:2099-01-01" in admin_mod._cache
