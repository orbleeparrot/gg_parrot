"""관리자 대시보드 — 비콘(view/event/leave)이 계약대로 분류·중복 제거되고, 집계 다섯 개가 계약 모양을 지키며,
결정적 시나리오(방문자 2 · 세션 2 · 이탈 1 · 당일 가입 1 · 코호트)에서 정확한 값을 내고, 이웃 모듈이 없어도 무너지지 않는다.
"""
from __future__ import annotations

import json
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


@pytest.mark.parametrize("width, expected", [(0, "desktop"), (320, "mobile"), (767, "mobile"), (768, "tablet"), (1023, "tablet"), (1024, "desktop"), (2560, "desktop")])
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
    assert set(users) == {"days", "generated_at", "kpis", "series", "daily", "channels", "sources", "pages", "devices", "peak_hours"}
    assert users["days"] == 7 and users["generated_at"].endswith("Z")
    assert set(users["kpis"]) == {"dau", "wau", "mau", "stickiness_pct", "online_5m", "bounce_pct_today", "avg_session_sec_today"}
    assert set(users["series"]) == {"days", "dau", "wau", "mau"} and all(len(users["series"][k]) == 7 for k in users["series"])
    assert len(users["daily"]) == 7 and users["daily"][-1]["day"] == users["series"]["days"][-1] == admin_mod._today_kst()
    assert set(users["daily"][0]) == {"day", "active", "new", "returning", "sessions", "pageviews", "pv_per_session", "avg_session_sec", "bounce_pct"}
    assert [c["channel"] for c in users["channels"]] == ["direct", "search", "referral", "social", "campaign"]
    assert set(users["channels"][0]) == {"channel", "label", "sessions", "share_pct", "new_visitors", "bounce_pct", "signup_rate_pct"}
    assert [d["device"] for d in users["devices"]] == ["mobile", "desktop", "tablet"]
    assert [h["hour"] for h in users["peak_hours"]] == list(range(24))
    assert len(users["sources"]) <= 10 and len(users["pages"]) <= 12
    if users["pages"]:
        assert set(users["pages"][0]) == {"path", "label", "pageviews", "avg_dwell_sec", "landings", "exit_pct"}

    signups = client.get("/api/admin/signups?days=7", headers=headers).json()
    assert set(signups) == {"days", "generated_at", "kpis", "series", "daily", "funnel", "cohorts", "methods"}
    assert set(signups["kpis"]) == {"total_users", "signups", "signup_rate_pct", "revisit_pct", "d7_retention_pct", "deletions"}
    assert set(signups["series"]) == {"days", "signups", "signup_rate_pct"} and len(signups["series"]["signups"]) == 7
    assert len(signups["daily"]) == 7
    assert set(signups["daily"][0]) == {"day", "signups", "signup_rate_pct", "deletions", "cumulative", "first_backtest_same_day", "quest_active"}
    assert [(f["step"], f["key"]) for f in signups["funnel"]] == list(enumerate(["visit", "builder", "backtest", "signup", "macro_register", "macro_unlock", "agent_start"], start=1))
    assert [m["method"] for m in signups["methods"]] == ["google", "email"]
    assert set(signups["methods"][0]) == {"method", "label", "signups", "share_pct", "d7_retention_pct", "first_backtest_same_day"}
    assert signups["cohorts"] and set(signups["cohorts"][0]) == {"week", "signups", "d1", "d3", "d7", "d14", "d30"}
    assert signups["kpis"]["signups"] >= 1  # 이 테스트 파일이 만든 계정들

    macros = client.get("/api/admin/macros?days=7", headers=headers).json()
    assert set(macros) == {"days", "generated_at", "kpis", "series", "daily", "top", "sessions"}
    assert set(macros["kpis"]) == {"registered", "unlocks", "revenue_points", "creator_points", "ctr_pct", "cvr_pct", "agents_running"}
    assert set(macros["series"]) == {"days", "registered", "unlocks", "ctr_pct", "cvr_pct"} and len(macros["series"]["ctr_pct"]) == 7
    assert len(macros["daily"]) == 7
    assert set(macros["daily"][0]) == {"day", "registered", "impressions", "opens", "ctr_pct", "unlocks", "cvr_pct", "revenue_points", "creator_points"}
    assert [s["kind"] for s in macros["sessions"]] == ["agent", "paper"]
    assert set(macros["sessions"][0]) == {"kind", "label", "running", "stopped", "error", "started_period", "mainnet"}
    assert len(macros["top"]) <= 20

    news = client.get("/api/admin/news", headers=headers).json()
    assert set(news) == {"generated_at", "kpis", "engines", "hourly", "sources", "board", "failing", "enrichment"}
    assert set(news["kpis"]) == {"tickers_ok", "tickers_total", "tickers_failing", "articles_today", "failures_today", "pending", "ai_budget_used", "ai_budget_limit"}
    assert [e["key"] for e in news["enrichment"]] == ["pending", "due", "given_up", "stall", "public_next", "ai_budget", "coindesk"]
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
    """방문자 2(v1 데스크톱·검색, v2 모바일·소셜) · 세션 2 · 이탈 1 · 당일 가입 1(v1=u1) · 10일 전 구글 가입(u2, 이틀 전 재방문) · 탈퇴 1.

    공유 테스트 DB 에는 다른 테스트의 계정·방문이 섞이므로, 값을 정확히 맞추는 시나리오는 격리된 SQLite 파일에 만든다.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'admin-scenario.db'}")
    SQLModel.metadata.create_all(engine)
    _, now_ms = admin_mod._now()
    base = now_ms - 240_000  # 4분 전 → online_5m 에도 잡힌다
    day = admin_mod.day_kst(base)
    d2, d10, d20 = admin_mod._shift_day(day, -2), admin_mod._shift_day(day, -10), admin_mod._shift_day(day, -20)
    noon = lambda d: admin_mod._day_start_ms(d) + 12 * 3_600_000  # noqa: E731
    iso = admin_mod._iso_from_ms
    with Session(engine) as db:
        u1 = User(email="u1@ex.com", username="u1", password_hash="pbkdf2", created_at=iso(base))
        u2 = User(email="u2@ex.com", username="u2", password_hash="", created_at=iso(noon(d10)))
        u3 = User(email="u3@ex.com", username="u3", password_hash="pbkdf2", created_at=iso(noon(d20)), is_deleted=True)
        db.add_all([u1, u2, u3])
        db.commit()
        db.refresh(u1), db.refresh(u2), db.refresh(u3)
        v1 = dict(visitor_hash="v1", session_key="s1", channel="search", device="desktop", referrer_host="www.google.com", user_id=u1.id, is_new=True)
        db.add_all([
            Visit(day_kst=day, path="/", created_ms=base, dwell_ms=5000, is_landing=True, view_key="k1", **v1),
            Visit(day_kst=day, path="/builder", created_ms=base + 60_000, dwell_ms=10_000, view_key="k2", **v1),
            Visit(day_kst=day, path="backtest", kind="event", created_ms=base + 61_000, view_key="k3", **v1),
            Visit(day_kst=day, path="/leaderboard", created_ms=base + 5_000, is_landing=True, view_key="k4", visitor_hash="v2", session_key="s2",
                  channel="social", device="mobile", referrer_host="t.co", is_new=True, screen_w=390),
            Visit(day_kst=d10, path="/news", created_ms=noon(d10), is_landing=True, view_key="k5", visitor_hash="v3", session_key="s3",
                  channel="direct", device="desktop", is_new=True, user_id=u2.id),
            Visit(day_kst=d2, path="/news", created_ms=noon(d2), is_landing=True, view_key="k6", visitor_hash="v3", session_key="s4",
                  channel="direct", device="desktop", user_id=u2.id),
            DailyQuestClaim(user_id=u1.id, date_kst=day, quest_key="backtest_run", reward=10, created_at=iso(base), created_ms=base),
        ])
        db.commit()
        yield {"db": db, "day": day, "d2": d2, "d10": d10, "d20": d20, "base": base, "u1": u1.id, "u2": u2.id, "noon": noon, "iso": iso}


def _by_day(rows, day):
    return next(row for row in rows if row["day"] == day)


def test_users_scenario(scenario):
    db, day, d2 = scenario["db"], scenario["day"], scenario["d2"]
    report = admin_mod._users_report(db, 7)
    today = _by_day(report["daily"], day)
    assert today == {"day": day, "active": 2, "new": 2, "returning": 0, "sessions": 2, "pageviews": 3, "pv_per_session": 1.5,
                     "avg_session_sec": 35, "bounce_pct": 50.0}
    two_days_ago = _by_day(report["daily"], d2)
    assert (two_days_ago["active"], two_days_ago["new"], two_days_ago["returning"], two_days_ago["sessions"], two_days_ago["bounce_pct"]) == (1, 0, 1, 1, 100.0)
    assert report["kpis"]["online_5m"] == 2 and report["kpis"]["wau"] == 3 and report["kpis"]["mau"] == 3
    if report["series"]["days"][-1] == day:
        assert report["kpis"]["dau"] == 2 and report["kpis"]["stickiness_pct"] == 66.7
        assert report["kpis"]["bounce_pct_today"] == 50.0 and report["kpis"]["avg_session_sec_today"] == 35
    channels = {c["channel"]: c for c in report["channels"]}
    assert channels["search"] == {"channel": "search", "label": "검색", "sessions": 1, "share_pct": 33.3, "new_visitors": 1, "bounce_pct": 0.0, "signup_rate_pct": 100.0}
    assert (channels["social"]["sessions"], channels["social"]["bounce_pct"], channels["social"]["signup_rate_pct"]) == (1, 100.0, 0.0)
    assert (channels["direct"]["sessions"], channels["direct"]["new_visitors"]) == (1, 0)
    assert channels["referral"]["sessions"] == 0 and channels["campaign"]["sessions"] == 0
    sources = {s["source"]: s for s in report["sources"]}
    assert sources["www.google.com"] == {"source": "www.google.com", "channel": "search", "sessions": 1, "signups": 1}
    assert sources["t.co"]["channel"] == "social" and sources["t.co"]["signups"] == 0
    pages = {p["path"]: p for p in report["pages"]}
    assert pages["/"] == {"path": "/", "label": "홈", "pageviews": 1, "avg_dwell_sec": 5, "landings": 1, "exit_pct": 0.0}
    assert pages["/builder"] == {"path": "/builder", "label": "직접 만들기", "pageviews": 1, "avg_dwell_sec": 10, "landings": 0, "exit_pct": 100.0}
    assert (pages["/leaderboard"]["landings"], pages["/leaderboard"]["exit_pct"], pages["/leaderboard"]["avg_dwell_sec"]) == (1, 100.0, 0)
    assert [p["path"] for p in report["pages"]] == ["/", "/builder", "/leaderboard", "/news"]
    devices = {d["device"]: d for d in report["devices"]}
    assert (devices["mobile"]["sessions"], devices["desktop"]["sessions"], devices["tablet"]["sessions"]) == (1, 2, 0)
    assert devices["desktop"]["avg_session_sec"] == 35 and devices["mobile"]["share_pct"] == 33.3
    assert sum(h["sessions"] for h in report["peak_hours"]) == 3


def test_signups_scenario_with_cohorts(scenario):
    db, day, d10, d20 = scenario["db"], scenario["day"], scenario["d10"], scenario["d20"]
    week = admin_mod._monday(day)
    report = admin_mod._signups_report(db, 7)
    assert report["kpis"] == {"total_users": 2, "signups": 1, "signup_rate_pct": 50.0, "revisit_pct": 0.0, "d7_retention_pct": 0.0, "deletions": 1}
    today = _by_day(report["daily"], day)
    assert (today["signups"], today["signup_rate_pct"], today["cumulative"], today["first_backtest_same_day"], today["quest_active"]) == (1, 50.0, 3, 1, 1)
    assert today["deletions"] == (1 if report["series"]["days"][-1] == day else 0)
    assert {f["key"]: f["count"] for f in report["funnel"]} == {"visit": 3, "builder": 1, "backtest": 1, "signup": 1, "macro_register": 0, "macro_unlock": 0, "agent_start": 0}
    assert report["cohorts"] == [{"week": f"{week[5:]} 주", "signups": 1, "d1": None, "d3": None, "d7": None, "d14": None, "d30": None}]
    methods = {m["method"]: m for m in report["methods"]}
    assert methods["email"] == {"method": "email", "label": "이메일 가입", "signups": 1, "share_pct": 100.0, "d7_retention_pct": 0.0, "first_backtest_same_day": 1}
    assert methods["google"]["signups"] == 0

    wide = admin_mod._signups_report(db, 30)
    assert wide["kpis"]["signups"] == 3 and wide["kpis"]["d7_retention_pct"] == 50.0 and wide["kpis"]["revisit_pct"] == 33.3
    cohorts = {c["week"]: c for c in wide["cohorts"]}
    assert cohorts[f"{admin_mod._monday(d10)[5:]} 주"] == {"week": f"{admin_mod._monday(d10)[5:]} 주", "signups": 1, "d1": 100.0, "d3": 100.0, "d7": 100.0, "d14": None, "d30": None}
    assert cohorts[f"{admin_mod._monday(d20)[5:]} 주"]["d14"] == 0.0 and cohorts[f"{admin_mod._monday(d20)[5:]} 주"]["d30"] is None
    assert [c["week"] for c in wide["cohorts"]] == sorted(c["week"] for c in wide["cohorts"])
    methods = {m["method"]: m for m in wide["methods"]}
    assert (methods["google"]["signups"], methods["google"]["share_pct"], methods["google"]["d7_retention_pct"]) == (1, 33.3, 100.0)
    assert (methods["email"]["signups"], methods["email"]["share_pct"], methods["email"]["d7_retention_pct"]) == (2, 66.7, 0.0)


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
    db.add_all([
        MacroEventDaily(day_kst=day, entry_id=entry.id, impressions=10, opens=2, unlocks=1),
        MacroUnlock(user_id=u1, entry_id=entry.id, price=100, created_at=iso(base)),
        PointLedger(user_id=u2, delta=70, balance_after=70, reason="unlock_earn", ref=f"entry:{entry.id}", created_at=iso(base), created_ms=base),
        RunSession(user_id=u2, started_at=iso(base), status="running", testnet=False),
        RunSession(user_id=u1, started_at=iso(base), status="stopped", stopped_at=iso(base)),
    ])
    db.commit()
    report = admin_mod._macros_report(db, 7)
    assert report["kpis"] == {"registered": 1, "unlocks": 1, "revenue_points": 100, "creator_points": 70, "ctr_pct": 20.0, "cvr_pct": 50.0, "agents_running": 1}
    assert _by_day(report["daily"], day) == {"day": day, "registered": 0, "impressions": 10, "opens": 2, "ctr_pct": 20.0, "unlocks": 1, "cvr_pct": 50.0,
                                             "revenue_points": 100, "creator_points": 70}
    assert _by_day(report["daily"], d1)["registered"] == 1
    assert report["top"] == [{"entry_id": entry.id, "name": "x" * 40, "creator": "u2", "symbol": "BTCUSDT", "registered_day": d1, "impressions": 10, "opens": 2,
                              "ctr_pct": 20.0, "unlocks": 1, "cvr_pct": 50.0, "revenue_points": 100, "creator_points": 70, "paper_return_pct": 12.35}]
    agent, paper_row = report["sessions"]
    assert agent == {"kind": "agent", "label": "에이전트 (실행기)", "running": 1, "stopped": 1, "error": 0, "started_period": 2, "mainnet": 1}
    assert paper_row == {"kind": "paper", "label": "모의 (페이퍼) 세션", "running": 1, "stopped": 0, "error": 0, "started_period": 1, "mainnet": 0}
