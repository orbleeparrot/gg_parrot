"""Tests for v6: spot-data safety guard, run.bat bundle, and the leaderboard."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import leaderboard as lb
from app import paper as paper_mod
from app.data import binance
from app.engine.schema import Macro
from app.main import app
from app.realtrade import build_bundle

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


# --- 1) safety guard: no spot data -> explicit rejection, no fabrication ---
def test_backtest_rejects_symbol_without_spot_data(monkeypatch):
    # Force "no real data available" for any fetch.
    monkeypatch.setattr(binance, "_fetch_binance", lambda *a, **k: [])
    monkeypatch.setattr(binance, "_read_cache", lambda *a, **k: __import__("pandas").DataFrame(columns=binance.COLUMNS))
    r = client.post("/api/backtest", json={"macro": _macro_dict("FAKEZZZUSDT")})
    assert r.status_code == 422
    assert "현물 시세" in r.json()["detail"]


def test_get_klines_no_synthetic_raises(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(binance, "_fetch_binance", lambda *a, **k: [])
    monkeypatch.setattr(binance, "_read_cache", lambda *a, **k: pd.DataFrame(columns=binance.COLUMNS))
    with pytest.raises(binance.NoSpotDataError):
        binance.get_klines("NOPEUSDT", 0, 86_400_000, allow_synthetic=False)
    # default path still returns synthetic (share/gallery flows unaffected)
    df, src = binance.get_klines("NOPEUSDT", 0, 3 * 86_400_000)
    assert src == "synthetic" and len(df) > 0


def test_ensure_spot_available_rejects_invalid(monkeypatch):
    class Resp:
        status_code = 400

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(binance.httpx, "Client", FakeClient)
    # no cached rows for this symbol -> reject
    with pytest.raises(binance.NoSpotDataError):
        binance.ensure_spot_available("DEFINITELYNOTREALUSDT")


# --- 2) run.bat bundle --------------------------------------------------
def test_bundle_contains_run_bat_and_requirements():
    macro = Macro(**_macro_dict())
    data = build_bundle(macro)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert {"run.bat", "run.command", "bot.py", "requirements.txt", "macro.json", "README-run.txt"} <= names
        run_bat = zf.read("run.bat").decode("utf-8")
        assert "bot.py" in run_bat and "pause" in run_bat and "%~dp0" in run_bat
        # macOS launcher: LF endings, bash shebang, executable bit for Finder.
        info = zf.getinfo("run.command")
        run_cmd = zf.read("run.command").decode("utf-8")
        assert run_cmd.startswith("#!/bin/bash") and "\r\n" not in run_cmd
        assert "bot.py" in run_cmd
        assert (info.external_attr >> 16) & 0o111  # any execute bit set


# --- 3) leaderboard -----------------------------------------------------
def test_leaderboard_register_list_vote(monkeypatch):
    # Avoid real network / paper thread: fake the spot check and paper session.
    monkeypatch.setattr(paper_mod, "ensure_spot_available", lambda s: None)

    async def fake_start(macro, symbol, mode):
        return {"session_id": 99999, "symbol": symbol, "mode": mode, "status": "running"}

    monkeypatch.setattr(paper_mod, "start_session", fake_start)
    monkeypatch.setattr(
        lb.paper_mod, "get_statuses",
        lambda ids, *, db=None: {
            sid: {
                "current_return": 3.2,
                "current_equity": 1032000.0,
                "status": "running",
                "mode": "live",
            }
            for sid in ids
        },
    )

    reg = client.post(
        "/api/leaderboard/register",
        json={"macro": _macro_dict(), "username": "테스터", "password": "pw1234", "user_id": "u1"},
    )
    assert reg.status_code == 200, reg.text
    entry_id = reg.json()["entry"]["id"]
    assert "password" not in reg.text and "password_hash" not in reg.text  # never leaked

    board = client.get("/api/leaderboard?user_id=u1").json()
    assert any(e["id"] == entry_id for e in board["items"])
    assert board["seconds_to_reset"] > 0
    mine = next(e for e in board["items"] if e["id"] == entry_id)
    assert mine["username"] == "테스터" and mine["return_pct"] == 3.2
    assert mine["created_kst"] and mine["is_mine"] is True

    # like, then toggle off
    v1 = client.post(f"/api/leaderboard/{entry_id}/vote", json={"user_id": "u2", "value": 1}).json()
    assert v1["likes"] == 1 and v1["my_vote"] == 1
    v2 = client.post(f"/api/leaderboard/{entry_id}/vote", json={"user_id": "u2", "value": 1}).json()
    assert v2["likes"] == 0 and v2["my_vote"] == 0
    # dislike from another user
    v3 = client.post(f"/api/leaderboard/{entry_id}/vote", json={"user_id": "u3", "value": -1}).json()
    assert v3["dislikes"] == 1


def test_seconds_to_reset_within_a_day():
    s = lb.seconds_to_reset()
    assert 0 < s <= 24 * 3600
