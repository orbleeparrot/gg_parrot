"""Tests for the Hangang water-temperature proxy (v10)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import hangang as hangang_mod
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache():
    hangang_mod._cache.clear()
    yield
    hangang_mod._cache.clear()


def test_label_formatting():
    assert hangang_mod._fmt_updated("20260709", "11:00") == "07/09 11:00"
    assert hangang_mod._fmt_updated("", "11:00") == "11:00"
    assert hangang_mod._fmt_updated("bad", "") is None


def test_normalizes_success(monkeypatch):
    # Patch the HTTP layer so we exercise the real normalization path in _fetch.
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"success": True, "date": "20260709", "time": "11:00",
                    "location": "중랑천", "temperature": 25.2}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(hangang_mod.httpx, "Client", _Client)
    d = client.get("/api/hangang-temp").json()
    assert d["ok"] is True
    assert d["temperature"] == 25.2
    assert d["location"] == "중랑천"
    assert d["observed_label"] == "07/09 11:00"
    assert d["cached"] is False


def test_upstream_failure_reports_not_ok(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(hangang_mod.httpx, "Client", _Client)
    d = client.get("/api/hangang-temp").json()
    assert d["ok"] is False
    assert d["temperature"] is None


def test_success_false_is_treated_as_failure(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"success": False}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(hangang_mod.httpx, "Client", _Client)
    d = client.get("/api/hangang-temp").json()
    assert d["ok"] is False


def test_serves_stale_cache_after_failure(monkeypatch):
    # 1) prime the cache with a good value
    hangang_mod._cache.get_or_load("temperature", lambda: {
        "ok": True, "temperature": 22.0, "location": "중랑천",
        "date": "20260709", "time": "11:00", "observed_label": "07/09 11:00"
    }, ttl=1, stale_ttl=3600)
    hangang_mod._cache._entries["temperature"].expires = hangang_mod._cache.clock() - 1


    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(hangang_mod.httpx, "Client", _Client)
    d = client.get("/api/hangang-temp").json()
    assert d["ok"] is True  # last good value
    assert d["temperature"] == 22.0
    assert d["stale"] is True
