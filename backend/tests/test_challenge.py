"""Daily AI challenge: lazy idempotent generation, template fallback, 🤖 flag."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import ai_challenge
from app import challenge
from app.engine.schema import Macro
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    # No real market data / paper sessions / hot-coin fetch.
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.challenge.paper_mod.start_session", _fake_start)
    monkeypatch.setattr("app.challenge._pick_symbol", lambda: "BTCUSDT")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # force template fallback


def test_generate_macros_falls_back_to_valid_templates():
    macros = ai_challenge.generate_macros("ETHUSDT", 3)
    assert len(macros) == 3
    for m in macros:
        Macro(**m)  # every one must be schema-valid
        assert m["symbol"] == "ETHUSDT"


def test_challenge_creates_three_ai_entries_and_is_idempotent(monkeypatch):
    # Isolate today's challenge to a unique KST date so reruns don't collide.
    import secrets
    fake_date = "2099-01-" + secrets.token_hex(2)[:2].rjust(2, "0")
    monkeypatch.setattr("app.challenge._today_kst", lambda: fake_date)

    r1 = client.get("/api/challenge/today")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["active"] is True
    assert body["symbol"] == "BTCUSDT"
    assert body["ai_name"] == "껄무새봇"

    # AI entries show on the board flagged is_ai, free/visible (macro present).
    items = client.get("/api/leaderboard").json()["items"]
    ai_entries = [e for e in items if e.get("is_ai")]
    assert len(ai_entries) >= 3
    assert all(e["locked"] is False and e["macro"] is not None for e in ai_entries)

    # Each bot is numbered 1..N. Asserted as a subset because an earlier run on
    # the same real KST day leaves its own bots on the board.
    names = {e["username"] for e in ai_entries}
    assert {"껄무새1호기봇", "껄무새2호기봇", "껄무새3호기봇"} <= names
    assert all(n.startswith("껄무새") and n.endswith("호기봇") for n in names)

    # Second call same day must NOT create more (idempotent).
    client.get("/api/challenge/today")
    items2 = client.get("/api/leaderboard").json()["items"]
    assert len([e for e in items2 if e.get("is_ai")]) == len(ai_entries)


def test_leaderboard_initializes_daily_challenge_before_listing(monkeypatch):
    events = []

    async def carryover():
        events.append("carryover")
        return 0

    async def ensure_challenge():
        events.append("challenge")
        return {"active": True}

    def list_entries(**_kwargs):
        events.append("list")
        return {"items": []}

    monkeypatch.setattr("app.main.leaderboard_mod.ensure_today_carryover", carryover)
    monkeypatch.setattr("app.main.challenge_mod.ensure_today", ensure_challenge)
    monkeypatch.setattr("app.main.leaderboard_mod.list_entries", list_entries)

    response = client.get("/api/leaderboard")

    assert response.status_code == 200
    assert events == ["carryover", "challenge", "list"]


def test_daily_challenge_is_claimed_before_expensive_generation():
    import secrets

    fake_date = f"claim-{secrets.token_hex(8)}"
    first = challenge._claim_daily_challenge(fake_date, now_ms=1_000)
    second = challenge._claim_daily_challenge(fake_date, now_ms=1_001)

    assert first["status"] == "claimed"
    assert first["claim_token"]
    assert second == {"status": "waiting", "claim_token": ""}

    challenge._complete_daily_challenge(
        fake_date,
        claim_token=first["claim_token"],
        symbol="BTCUSDT",
        now_ms=1_002,
    )

    assert challenge._claim_daily_challenge(fake_date, now_ms=1_003) == {
        "status": "ready",
        "claim_token": "",
    }
