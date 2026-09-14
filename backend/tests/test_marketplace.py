"""Leaderboard marketplace: unlock gating, 70% creator share, crown, backward-compat.

Uses the API for auth + register + unlock. Paper sessions are stubbed so no
network/live data is needed and registration is deterministic.
"""
from __future__ import annotations

import secrets

from tests.leaderboard_helpers import publish_ready_board
import pytest
from fastapi.testclient import TestClient

from app import leaderboard as lb
from app import points as points_mod
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    """Avoid real paper sessions / market data during register."""
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)
    # live-return lookup returns nothing for a None session id anyway.


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"m{tok}@ex.com", "username": f"m_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


_MACRO = {
    "symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
    "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
    "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"},
}


def _register(token, symbol="BTCUSDT"):
    macro = {**_MACRO, "symbol": symbol}
    res = client.post("/api/leaderboard/register",
                      json={"macro": macro, "username": "", "password": "", "user_id": "anon"},
                      headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()["entry"]


def _find(entries, entry_id):
    return next(e for e in entries if e["id"] == entry_id)


# --- gating -------------------------------------------------------------
def test_owned_entry_is_locked_for_others_and_open_for_owner():
    seller_tok, _ = _signup()
    entry = _register(seller_tok)
    eid = entry["id"]

    # A different account sees it LOCKED: no macro/summary, price shown.
    buyer_tok, _ = _signup()
    publish_ready_board()
    listed = client.get("/api/leaderboard", headers=_auth(buyer_tok)).json()["items"]
    view = _find(listed, eid)
    assert view["for_sale"] is True
    assert view["locked"] is True
    assert view["macro"] is None
    assert view["human_summary"] == ""
    assert view["unlock_price"] == points_mod.UNLOCK_PRICE
    assert view["symbol"] == "BTCUSDT"  # id·종목·등락률은 보임

    # The owner sees it unlocked.
    publish_ready_board()
    owner_view = _find(client.get("/api/leaderboard", headers=_auth(seller_tok)).json()["items"], eid)
    assert owner_view["locked"] is False
    assert owner_view["macro"] is not None


def test_unlock_charges_buyer_and_pays_creator_70pct():
    seller_tok, seller = _signup()
    entry = _register(seller_tok)
    eid = entry["id"]
    buyer_tok, buyer = _signup()

    res = client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(buyer_tok))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["entry"]["macro"] is not None  # revealed
    assert body["entry"]["locked"] is False
    price = points_mod.UNLOCK_PRICE
    assert body["points_balance"] == points_mod.SIGNUP_GRANT - price

    # creator got 70%.
    seller_me = client.get("/api/auth/me", headers=_auth(seller_tok)).json()["user"]
    assert seller_me["points_balance"] == points_mod.SIGNUP_GRANT + price * 70 // 100


def test_unlock_is_idempotent_no_double_charge():
    seller_tok, _ = _signup()
    eid = _register(seller_tok)["id"]
    buyer_tok, _ = _signup()
    client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(buyer_tok))
    again = client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(buyer_tok))
    assert again.status_code == 200
    assert again.json()["points_balance"] == points_mod.SIGNUP_GRANT - points_mod.UNLOCK_PRICE


def test_cannot_unlock_own_entry():
    tok, _ = _signup()
    eid = _register(tok)["id"]
    res = client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(tok))
    assert res.status_code == 400


def test_unlock_requires_login():
    seller_tok, _ = _signup()
    eid = _register(seller_tok)["id"]
    assert client.post(f"/api/leaderboard/{eid}/unlock").status_code == 401


def test_insufficient_points_returns_402(monkeypatch):
    # Make unlock cost more than the starter grant.
    monkeypatch.setattr(points_mod, "UNLOCK_PRICE", points_mod.SIGNUP_GRANT + 1)
    seller_tok, _ = _signup()
    eid = _register(seller_tok)["id"]
    buyer_tok, _ = _signup()
    res = client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(buyer_tok))
    assert res.status_code == 402


# --- backward compatibility --------------------------------------------
def test_anonymous_entry_stays_free_and_visible():
    res = client.post("/api/leaderboard/register",
                      json={"macro": _MACRO, "username": "게스트", "password": "pw123456", "user_id": "anon1"})
    assert res.status_code == 200, res.text
    entry = res.json()["entry"]
    assert entry["for_sale"] is False
    assert entry["locked"] is False
    assert entry["macro"] is not None  # legacy entries remain copyable


# --- crown --------------------------------------------------------------
def test_owner_can_delete_own_entry_others_cannot():
    seller_tok, _ = _signup()
    eid = _register(seller_tok)["id"]

    # a different account cannot delete it
    other_tok, _ = _signup()
    assert client.delete(f"/api/leaderboard/{eid}", headers=_auth(other_tok)).status_code == 403
    # anonymous cannot delete
    assert client.delete(f"/api/leaderboard/{eid}").status_code == 401
    # owner can
    assert client.delete(f"/api/leaderboard/{eid}", headers=_auth(seller_tok)).status_code == 200
    # and it's gone from the board
    publish_ready_board()
    items = client.get("/api/leaderboard").json()["items"]
    assert all(e["id"] != eid for e in items)


def test_account_owner_edits_without_password(monkeypatch):
    seller_tok, _ = _signup()
    eid = _register(seller_tok)["id"]
    new_macro = {**_MACRO, "symbol": "ETHUSDT"}
    res = client.post(f"/api/leaderboard/{eid}/edit",
                      json={"macro": new_macro, "password": "", "mode": "live"},
                      headers=_auth(seller_tok))
    assert res.status_code == 200, res.text
    assert res.json()["entry"]["symbol"] == "ETHUSDT"

    # a non-owner account is rejected (no password fallback for owned entries)
    other_tok, _ = _signup()
    res2 = client.post(f"/api/leaderboard/{eid}/edit",
                       json={"macro": new_macro, "password": "", "mode": "live"},
                       headers=_auth(other_tok))
    assert res2.status_code == 403


def test_crown_after_enough_sales_and_likes(monkeypatch):
    monkeypatch.setattr(lb, "CROWN_MIN_SALES", 2)
    monkeypatch.setattr(lb, "CROWN_MIN_LIKES", 2)
    seller_tok, seller = _signup()
    eid = _register(seller_tok)["id"]

    # 2 buyers unlock (sales=2) and like (likes=2).
    for _ in range(2):
        btok, buyer = _signup()
        client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(btok))
        client.post(f"/api/leaderboard/{eid}/vote",
                    json={"user_id": f"v{buyer['id']}", "value": 1})

    publish_ready_board()
    listed = client.get("/api/leaderboard").json()["items"]
    assert _find(listed, eid)["crown"] is True
