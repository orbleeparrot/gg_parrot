"""Google 간편 로그인(GIS credential) 인증.

Google 의 tokeninfo 호출(auth.httpx.get)을 가짜로 바꿔, 토큰 검증 → 계정 자동
생성/재로그인 → 대상(aud) 불일치·미검증 이메일 거부를 SQLite 로 확인한다.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app import points as points_mod
from app.main import app

client = TestClient(app)

_CLIENT_ID = "test-client.apps.googleusercontent.com"


class _FakeResp:
    def __init__(self, claims, status_code=200):
        self._claims = claims
        self.status_code = status_code

    def json(self):
        return self._claims


@pytest.fixture
def google_on(monkeypatch):
    """구글 로그인을 켜고, tokeninfo 응답을 주입할 수 있는 세터를 준다."""
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", _CLIENT_ID)

    state = {"claims": None, "status": 200}

    def fake_get(url, params=None, timeout=None):
        return _FakeResp(state["claims"], state["status"])

    monkeypatch.setattr(auth_mod.httpx, "get", fake_get)

    def set_claims(**overrides):
        base = {
            "aud": _CLIENT_ID,
            "iss": "accounts.google.com",
            "email": f"g_{secrets.token_hex(4)}@example.com",
            "email_verified": "true",
            "name": "구글 사용자",
            "sub": secrets.token_hex(8),
        }
        base.update(overrides)
        state["claims"] = base
        return base

    return set_claims


def test_config_reports_enabled_state(monkeypatch):
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "")
    assert client.get("/api/auth/google/config").json() == {"enabled": False, "client_id": ""}
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", _CLIENT_ID)
    cfg = client.get("/api/auth/google/config").json()
    assert cfg == {"enabled": True, "client_id": _CLIENT_ID}


def test_google_creates_account_with_starter_grant(google_on):
    claims = google_on()
    res = client.post("/api/auth/google", json={"credential": "fake"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token"]
    assert body["user"]["email"] == claims["email"]
    assert body["user"]["points_balance"] == points_mod.SIGNUP_GRANT
    assert "password_hash" not in body["user"]


def test_google_same_email_reuses_account(google_on):
    google_on(email="same@example.com", sub="a")
    first = client.post("/api/auth/google", json={"credential": "fake"}).json()
    # 다른 sub 이어도 같은 이메일이면 같은 계정으로 로그인(중복 생성 없음).
    google_on(email="same@example.com", sub="b")
    second = client.post("/api/auth/google", json={"credential": "fake"}).json()
    assert first["user"]["id"] == second["user"]["id"]


def test_google_rejects_wrong_audience(google_on):
    google_on(aud="some-other-app")
    assert client.post("/api/auth/google", json={"credential": "fake"}).status_code == 401


def test_google_rejects_unverified_email(google_on):
    google_on(email_verified="false")
    assert client.post("/api/auth/google", json={"credential": "fake"}).status_code == 401


def test_google_disabled_returns_503(monkeypatch):
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "")
    assert client.post("/api/auth/google", json={"credential": "fake"}).status_code == 503
