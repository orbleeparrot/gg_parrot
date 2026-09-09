"""My-page dashboard: aggregates created/purchased/sales/ledger + tier."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)


_MACRO = {
    "symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
    "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
    "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"},
}


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"d{tok}@ex.com", "username": f"d_{tok}", "password": "password123",
    }).json()
    return body["token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_dashboard_requires_login():
    assert client.get("/api/me/dashboard").status_code == 401


def test_dashboard_reflects_created_purchased_and_sales():
    seller = _signup()
    reg = client.post("/api/leaderboard/register",
                      json={"macro": _MACRO, "username": "", "password": "", "user_id": "anon"},
                      headers=_auth(seller))
    eid = reg.json()["entry"]["id"]

    buyer = _signup()
    client.post(f"/api/leaderboard/{eid}/unlock", headers=_auth(buyer))

    # Seller side: 1 created, 1 sale, earned 70, tier bumped off 새싹.
    sd = client.get("/api/me/dashboard", headers=_auth(seller)).json()
    assert sd["totals"]["created"] == 1
    assert sd["totals"]["sales"] == 1
    assert sd["totals"]["earned"] == 70
    assert sd["created"][0]["sales"] == 1
    assert sd["tier"]["name"] == "브론즈"
    assert any(l["reason"] == "unlock_earn" for l in sd["ledger"])
    # 등급 사다리·가입일·등록 시각(ms)은 화면이 그대로 그린다.
    assert sd["tier"]["ladder"][:2] == [{"name": "새싹", "at": 0}, {"name": "브론즈", "at": 1}]
    assert sd["tier"]["ladder"][-1] == {"name": "다이아", "at": 40}
    assert sd["user"]["created_at"].endswith("Z")
    assert isinstance(sd["created"][0]["created_ms"], int)

    # Buyer side: 1 purchase with the macro visible, balance debited.
    bd = client.get("/api/me/dashboard", headers=_auth(buyer)).json()
    assert bd["totals"]["purchased"] == 1
    assert bd["purchased"][0]["macro"] is not None
    assert bd["user"]["points_balance"] == 1000 - 100
