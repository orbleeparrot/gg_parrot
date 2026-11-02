"""코인게코 열쇠 — 있으면 머리말에 붙이고, 없으면 지금처럼 그냥 부른다 (2026-11-01).

왜 필요한가: 공개 API 는 데이터센터 IP 를 자주 막는다. Render 에서 추천 묶음이 계속
비어 돌아온 까닭이다. 무료 Demo 열쇠(분당 30회)면 풀린다.
"""
from __future__ import annotations

import pytest

from app.data import coingecko as cg


class _Reply:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _Client:
    """마지막으로 받은 머리말을 들고 있는 가짜 클라이언트."""

    def __init__(self, body=None):
        self.headers = None
        self.url = None
        self._body = body if body is not None else []

    def get(self, url, params=None, timeout=None, headers=None):
        self.url = url
        self.headers = dict(headers or {})
        return _Reply(self._body)


@pytest.fixture
def client(monkeypatch):
    fake = _Client()
    monkeypatch.setattr(cg, "get_http_client", lambda: fake)
    return fake


def test_the_key_rides_in_the_demo_header(monkeypatch, client):
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-demo-abc")
    cg._fetch({"vs_currency": "usd"})
    assert client.headers.get("x-cg-demo-api-key") == "cg-demo-abc"


def test_no_key_means_no_header(monkeypatch, client):
    """열쇠가 없어도 도는 길은 그대로다 — 개발 · 시험에서 열쇠를 요구하지 않는다."""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    cg._fetch({"vs_currency": "usd"})
    assert "x-cg-demo-api-key" not in client.headers
    assert client.headers.get("accept") == "application/json"


def test_an_empty_key_is_the_same_as_none(monkeypatch, client):
    """빈 환경변수를 열쇠로 보내면 코인게코가 400 으로 막는다 — 없는 것으로 친다."""
    monkeypatch.setenv("COINGECKO_API_KEY", "   ")
    cg._fetch({"vs_currency": "usd"})
    assert "x-cg-demo-api-key" not in client.headers


def test_the_key_is_read_each_call(monkeypatch, client):
    """불러올 때 읽어야 한다 — 들여올 때 읽으면 운영에서 열쇠를 넣고도 재시작 전까지 안 먹는다."""
    monkeypatch.setenv("COINGECKO_API_KEY", "first")
    cg._fetch({})
    assert client.headers.get("x-cg-demo-api-key") == "first"
    monkeypatch.setenv("COINGECKO_API_KEY", "second")
    cg._fetch({})
    assert client.headers.get("x-cg-demo-api-key") == "second"


def test_a_pro_key_rides_in_the_pro_header_and_hits_the_pro_host(monkeypatch, client):
    """Pro 열쇠는 머리말 이름도 주소도 다르다 — Demo 주소로 보내면 거절당한다."""
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-pro-xyz")
    monkeypatch.setenv("COINGECKO_PLAN", "pro")
    cg._fetch({})
    assert client.headers.get("x-cg-pro-api-key") == "cg-pro-xyz"
    assert "x-cg-demo-api-key" not in client.headers
    assert client.url.startswith("https://pro-api.coingecko.com/")
