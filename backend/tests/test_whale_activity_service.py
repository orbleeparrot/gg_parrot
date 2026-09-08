"""The authenticated feed reads shared observations without exchange I/O."""
from datetime import datetime, timezone

import pytest

from app import whales
from app.agent_features.whale_activity import service


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    service.clear_cache()
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: pytest.fail("read must not fetch"))


def test_first_read_is_quiet_pending_and_never_fetches(monkeypatch):
    monkeypatch.setattr(service.repository, "read_snapshot", lambda *_: None)
    result = service.get_activity("CHIPUSDT", "futures")
    assert result["status"] == "pending"
    assert result["items"] == []
    assert result["market"] == "futures"


def test_shared_read_cache_and_expiring_trades(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(service.time, "time", lambda: now)
    calls = []
    item = {"id": "spot:BTCUSDT:1", "notional": 150000,
            "occurred_at": datetime.fromtimestamp(now - 590, timezone.utc).isoformat()}
    def read(*args):
        calls.append(args)
        return {"status": "ready", "items": [item], "last_success_ms": int(now * 1000),
                "last_attempt_ms": int(now * 1000), "collection_status": "ready"}
    monkeypatch.setattr(service.repository, "read_snapshot", read)
    first = service.get_activity("BTCUSDT", "spot")
    second = service.get_activity("BTCUSDT", "spot")
    assert first["items"] == second["items"] == [item]
    assert len(calls) == 1
    first["items"].clear()
    assert service.get_activity("BTCUSDT", "spot")["items"] == [item]
    monkeypatch.setattr(service.time, "time", lambda: now + 15)
    assert service.get_activity("BTCUSDT", "spot")["items"] == []


@pytest.mark.parametrize("failure", ["error", "rate_limited"])
def test_failure_does_not_make_old_snapshot_fresh(monkeypatch, failure):
    now = 1_800_000_000.0
    monkeypatch.setattr(service.time, "time", lambda: now)
    monkeypatch.setattr(service.repository, "read_snapshot", lambda *_: {
        "status": "ready", "items": [], "last_success_ms": int((now - 130) * 1000),
        "last_attempt_ms": int(now * 1000), "collection_status": failure,
        "last_error": "rate_limited", "next_collection_ms": int((now + 300) * 1000),
    })
    payload = service.get_activity("BTCUSDT")
    assert payload["stale"] is True
    assert payload["status"] == "unavailable"
    assert payload["collection"]["last_success_ms"] == int((now - 130) * 1000)


def test_initial_provider_rate_limit_is_not_success_or_pending(monkeypatch):
    monkeypatch.setattr(service.repository, "read_snapshot", lambda *_: {
        "collection_status": "rate_limited", "last_success_ms": 0, "items": [],
    })
    assert service.get_activity("BTCUSDT")["status"] == "unavailable"


def test_unsupported_market_does_not_read_database(monkeypatch):
    monkeypatch.setattr(service.repository, "read_snapshot", lambda *_: pytest.fail("invalid pair"))
    assert service.get_activity("BTCUSDT", "unknown")["status"] == "unavailable"


def test_database_failure_is_unavailable_without_source_fallback(monkeypatch):
    monkeypatch.setattr(service.repository, "read_snapshot", lambda *_: (_ for _ in ()).throw(RuntimeError("secret-db-url")))
    payload = service.get_activity("BTCUSDT")
    assert payload["status"] == "unavailable"
    assert "secret" not in str(payload)
