"""Tests for v7: password hashing, edit-with-password, return sort, chat."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import chat as chat_mod
from app import leaderboard as lb
from app import paper as paper_mod
from app.main import app
from app.security import hash_password, verify_password

client = TestClient(app)


def _macro_dict(symbol="BTCUSDT"):
    return {
        "symbol": symbol,
        "rule_type": "A",
        "position_side": "long",
        "params": {"take_profit_pct": 5.0, "initial_capital": 1000000},
        "risk": {"invest_ratio": 0.5},
        "period": {"preset": "3m"},
    }


# --- password hashing ---------------------------------------------------
def test_password_hash_roundtrip_and_secrecy():
    h = hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert "s3cret!" not in h  # plaintext never present
    assert verify_password("s3cret!", h)
    assert not verify_password("wrong", h)
    # two hashes of the same password differ (random salt)
    assert hash_password("s3cret!") != h


# --- register requires id + password; never leaks -----------------------
def test_register_requires_id_and_password():
    r = client.post("/api/leaderboard/register", json={"macro": _macro_dict()})
    assert r.status_code == 422  # missing username/password
    r = client.post(
        "/api/leaderboard/register",
        json={"macro": _macro_dict(), "username": "  ", "password": ""},
    )
    assert r.status_code in (400, 422)


# --- fixtures for mocked paper ------------------------------------------
@pytest.fixture
def mock_paper(monkeypatch):
    monkeypatch.setattr(paper_mod, "ensure_spot_available", lambda s: None)
    counter = {"n": 100}
    returns = {}

    async def fake_start(macro, symbol, mode):
        counter["n"] += 1
        sid = counter["n"]
        returns.setdefault(sid, 0.0)
        return {"session_id": sid, "symbol": symbol, "mode": mode, "status": "running"}

    async def fake_stop(sid):
        return {"session_id": sid, "status": "stopped"}

    monkeypatch.setattr(paper_mod, "start_session", fake_start)
    monkeypatch.setattr(paper_mod, "stop_session", fake_stop)
    monkeypatch.setattr(
        lb.paper_mod, "get_statuses",
        lambda ids, *, db=None: {
            sid: {
                "current_return": returns.get(sid, 0.0),
                "current_equity": 1e6,
                "status": "running",
                "mode": "live",
            }
            for sid in ids
        },
    )
    return returns


def test_leaderboard_sorted_by_return(mock_paper):
    r1 = client.post("/api/leaderboard/register", json={"macro": _macro_dict(), "username": "low", "password": "p", "user_id": "a"}).json()["entry"]
    r2 = client.post("/api/leaderboard/register", json={"macro": _macro_dict(), "username": "high", "password": "p", "user_id": "b"}).json()["entry"]
    mock_paper[r1["paper_session_id"]] = 1.0
    mock_paper[r2["paper_session_id"]] = 9.0
    board = client.get("/api/leaderboard").json()["items"]
    # highest return must appear before lower return
    idx_high = next(i for i, e in enumerate(board) if e["id"] == r2["id"])
    idx_low = next(i for i, e in enumerate(board) if e["id"] == r1["id"])
    assert idx_high < idx_low
    # macro is included for copy-to-builder
    assert board[idx_high]["macro"]["rule_type"] == "A"


def test_edit_requires_correct_password(mock_paper):
    reg = client.post(
        "/api/leaderboard/register",
        json={"macro": _macro_dict(), "username": "owner", "password": "correct-horse", "user_id": "z"},
    ).json()["entry"]
    eid = reg["id"]

    # wrong password -> 403, no change
    bad = client.post(f"/api/leaderboard/{eid}/edit", json={"macro": _macro_dict(), "password": "nope"})
    assert bad.status_code == 403

    # right password -> edit succeeds, macro updated
    new = _macro_dict()
    new["params"]["take_profit_pct"] = 12.0
    ok = client.post(f"/api/leaderboard/{eid}/edit", json={"macro": new, "password": "correct-horse"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["entry"]["macro"]["params"]["take_profit_pct"] == 12.0


# --- chat ---------------------------------------------------------------
def test_chat_post_and_list():
    chat_mod._recent.clear()
    r = client.post("/api/chat", json={"username": "지피", "text": "안녕하세요"})
    assert r.status_code == 200
    assert r.json()["message"]["text"] == "안녕하세요"
    assert r.json()["message"]["created_kst"]
    lst = client.get("/api/chat").json()
    assert any(m["text"] == "안녕하세요" for m in lst["items"])
    assert "투자 조언이 아니" in lst["disclaimer"]


def test_chat_length_cap_and_empty():
    chat_mod._recent.clear()
    long_text = "가" * 500
    m = client.post("/api/chat", json={"username": "u", "text": long_text}).json()["message"]
    assert len(m["text"]) == chat_mod.MAX_LEN
    empty = client.post("/api/chat", json={"username": "u", "text": "   "})
    assert empty.status_code == 400


def test_chat_rate_limit():
    chat_mod._recent.clear()
    codes = [client.post("/api/chat", json={"username": "spam", "text": f"m{i}"}).status_code for i in range(8)]
    assert 429 in codes  # flood is throttled
