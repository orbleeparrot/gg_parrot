"""Account macro library used by quick-run."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app.engine import BacktestResult
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)


_MACRO = {
    "symbol": "BTCUSDT",
    "rule_type": "A",
    "candle_interval": "1d",
    "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
    "risk": {"stop_loss_pct": 3},
    "period": {"preset": "3m"},
}


def _signup():
    suffix = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"lib{suffix}@ex.com",
        "username": f"lib_{suffix}",
        "password": "password123",
    }).json()
    return body["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_my_macros_requires_login():
    assert client.get("/api/me/macros").status_code == 401


def test_upload_is_validated_saved_and_deduplicated():
    token = _signup()
    first = client.post(
        "/api/me/macros",
        json={"macro": _MACRO, "name": "내 BTC 전략"},
        headers=_auth(token),
    )
    assert first.status_code == 200
    item = first.json()["item"]
    assert item["name"] == "내 BTC 전략"
    assert item["source_type"] == "upload"
    assert item["macro"]["symbol"] == "BTCUSDT"

    second = client.post(
        "/api/me/macros",
        json={"macro": _MACRO, "name": "중복 이름"},
        headers=_auth(token),
    )
    assert second.status_code == 200
    assert second.json()["item"]["id"] == item["id"]

    items = client.get("/api/me/macros", headers=_auth(token)).json()["items"]
    assert [row["id"] for row in items].count(item["id"]) == 1


def test_logged_in_builder_save_enters_account_library(monkeypatch):
    result = BacktestResult(
        final_return_pct=1.2,
        win_rate_pct=50,
        mdd_pct=-0.4,
        total_trades=2,
        initial_capital=1_000_000,
        final_equity=1_012_000,
        equity_curve=[],
    )
    monkeypatch.setattr(
        "app.main._run_for_macro",
        lambda macro: (result, "test", "최근 3개월"),
    )

    token = _signup()
    saved = client.post("/api/macros", json=_MACRO, headers=_auth(token))
    assert saved.status_code == 200, saved.text
    account_macro = saved.json()["user_macro"]
    assert account_macro["source_type"] == "builder"

    items = client.get("/api/me/macros", headers=_auth(token)).json()["items"]
    library_row = next(row for row in items if row["id"] == account_macro["id"])
    assert library_row["performance"]["kind"] == "backtest"
    assert library_row["performance"]["return_pct"] == pytest.approx(1.2)


def test_register_and_unlock_create_account_snapshots():
    seller = _signup()
    registered = client.post(
        "/api/leaderboard/register",
        json={"macro": _MACRO, "username": "", "password": "", "user_id": "anon"},
        headers=_auth(seller),
    )
    assert registered.status_code == 200
    entry_id = registered.json()["entry"]["id"]
    owned_copy = client.post(
        f"/api/me/macros/from-leaderboard/{entry_id}", headers=_auth(seller)
    )
    assert owned_copy.status_code == 200
    assert owned_copy.json()["item"]["source_type"] == "created"
    seller_items = client.get("/api/me/macros", headers=_auth(seller)).json()["items"]
    assert any(row["source_type"] == "created" and row["source_ref"] == str(entry_id) for row in seller_items)

    buyer = _signup()
    locked_copy = client.post(
        f"/api/me/macros/from-leaderboard/{entry_id}", headers=_auth(buyer)
    )
    assert locked_copy.status_code == 403
    unlocked = client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(buyer))
    assert unlocked.status_code == 200
    assert unlocked.json()["user_macro"]["source_ref"] == str(entry_id)
    purchased_copy = client.post(
        f"/api/me/macros/from-leaderboard/{entry_id}", headers=_auth(buyer)
    )
    assert purchased_copy.status_code == 200
    assert purchased_copy.json()["item"]["source_type"] == "leaderboard"
    buyer_items = client.get("/api/me/macros", headers=_auth(buyer)).json()["items"]
    assert any(row["source_type"] == "leaderboard" and row["source_ref"] == str(entry_id) for row in buyer_items)


def test_free_leaderboard_macro_can_be_saved_inline():
    registered = client.post(
        "/api/leaderboard/register",
        json={
            "macro": _MACRO,
            "username": "무료 매크로",
            # Anonymous entries keep the legacy edit-password requirement, but
            # still have no account owner and therefore remain free to copy.
            "password": "password123",
            "user_id": f"anon-{secrets.token_hex(4)}",
        },
    )
    assert registered.status_code == 200

    viewer = _signup()
    entry_id = registered.json()["entry"]["id"]
    copied = client.post(
        f"/api/me/macros/from-leaderboard/{entry_id}", headers=_auth(viewer)
    )
    assert copied.status_code == 200
    item = copied.json()["item"]
    assert item["source_type"] == "leaderboard"
    assert item["source_ref"] == str(entry_id)
