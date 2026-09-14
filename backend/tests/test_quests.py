"""일일 퀘스트: 백테스트·페이퍼·댓글을 하루 한 번씩 보상해 포인트를 벌 수 있다."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import quests
from app.main import app

client = TestClient(app)

_MACRO = {
    "symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
    "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
    "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"},
}


@pytest.fixture(autouse=True)
def _stub_engine(monkeypatch):
    """No market data in tests: backtest and paper start succeed without the network."""
    from app.engine import BacktestResult

    def fake_run_any(macro):
        result = BacktestResult(
            initial_capital=1.0, final_equity=1.0, final_return_pct=0.0, mdd_pct=0.0,
            win_rate_pct=0.0, total_trades=0, trades=[], equity_curve=[],
        )
        return result, [], "test", "3m"

    async def fake_start(macro, symbol, mode):
        return {"session_id": 1, "symbol": macro.symbol, "mode": mode, "status": "running"}

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    monkeypatch.setattr("app.main.paper_mod.start_session", fake_start)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"q{tok}@ex.com", "username": f"q_{tok}", "password": "password123",
    }).json()
    return body["token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _balance(token):
    return client.get("/api/auth/me", headers=_auth(token)).json()["user"]["points_balance"]


def test_quest_board_starts_empty_and_lists_every_quest():
    token = _signup()
    board = client.get("/api/me/quests", headers=_auth(token)).json()
    assert [q["key"] for q in board["quests"]] == ["backtest_run", "paper_start", "board_comment"]
    assert all(q["done"] is False for q in board["quests"])
    assert board["earned"] == 0
    assert board["total"] == sum(q["reward"] for q in quests.QUESTS)
    assert client.get("/api/me/quests").status_code == 401


def test_backtest_pays_once_per_day():
    token = _signup()
    before = _balance(token)

    first = client.post("/api/backtest", json={"macro": _MACRO}, headers=_auth(token)).json()
    assert first["quest"]["key"] == "backtest_run"
    assert first["quest"]["reward"] == 10
    assert _balance(token) == before + 10

    second = client.post("/api/backtest", json={"macro": _MACRO}, headers=_auth(token)).json()
    assert second["quest"] is None
    assert _balance(token) == before + 10

    board = client.get("/api/me/quests", headers=_auth(token)).json()
    done = {q["key"]: q["done"] for q in board["quests"]}
    assert done == {"backtest_run": True, "paper_start": False, "board_comment": False}
    assert board["earned"] == 10 and board["done_count"] == 1

    ledger = client.get("/api/me/dashboard", headers=_auth(token)).json()["ledger"]
    quest_rows = [row for row in ledger if row["reason"] == "quest"]
    assert len(quest_rows) == 1 and quest_rows[0]["delta"] == 10


def test_anonymous_backtest_has_no_quest_and_still_works():
    body = client.post("/api/backtest", json={"macro": _MACRO}).json()
    assert body["quest"] is None
    assert "result" in body


def test_paper_start_pays_the_paper_quest():
    token = _signup()
    before = _balance(token)
    body = client.post("/api/paper/start", json={"macro": _MACRO, "mode": "replay"}, headers=_auth(token)).json()
    assert body["quest"]["key"] == "paper_start"
    assert _balance(token) == before + 10
    again = client.post("/api/paper/start", json={"macro": _MACRO, "mode": "replay"}, headers=_auth(token)).json()
    assert again["quest"] is None


def test_comment_quest_needs_ten_characters():
    author = _signup()
    post = client.post("/api/board/posts", data={"title": "퀘스트 글", "body": "본문"}, headers=_auth(author)).json()
    post_id = post["post"]["id"] if "post" in post else post["id"]

    token = _signup()
    before = _balance(token)
    short = client.post(f"/api/board/posts/{post_id}/comments", json={"text": "짧은 댓글"}, headers=_auth(token)).json()
    assert short["quest"] is None
    assert _balance(token) == before

    long = client.post(f"/api/board/posts/{post_id}/comments", json={"text": "이 정도면 열 글자는 넘는 댓글이에요"}, headers=_auth(token)).json()
    assert long["quest"]["key"] == "board_comment"
    assert _balance(token) == before + 10


def test_dashboard_carries_the_quest_board():
    token = _signup()
    d = client.get("/api/me/dashboard", headers=_auth(token)).json()
    assert d["quests"]["total"] == 30
    assert len(d["quests"]["quests"]) == 3


def test_quest_reward_preserves_balance_changed_during_a_long_request():
    from app import points
    from app.db import PointLedger, User, get_session
    from sqlmodel import select

    token = _signup()
    uid = client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"]
    with get_session() as request_db:
        # Authentication loaded this account before the backtest began.
        account = request_db.get(User, uid)
        before = account.points_balance
        with get_session() as other_db:
            points.apply(other_db, other_db.get(User, uid), -100, "unlock_spend", "fixture")
            other_db.commit()
        assert account.points_balance == before
        assert quests.complete(request_db, account, "backtest_run") is not None
        assert account.points_balance == before - 100 + 10
    with get_session() as db:
        assert db.get(User, uid).points_balance == before - 100 + 10
        ledger = db.exec(select(PointLedger).where(PointLedger.user_id == uid, PointLedger.reason == "quest")).one()
        assert ledger.balance_after == before - 100 + 10


def test_quest_cannot_reward_an_account_deleted_during_a_long_request():
    from app.db import DailyQuestClaim, User, get_session
    from sqlmodel import select

    token = _signup()
    uid = client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"]
    with get_session() as request_db:
        account = request_db.get(User, uid)
        with get_session() as other_db:
            deleted = other_db.get(User, uid)
            deleted.is_deleted = True
            deleted.points_balance = 0
            other_db.add(deleted)
            other_db.commit()
        assert quests.complete(request_db, account, "backtest_run") is None
    with get_session() as db:
        assert db.get(User, uid).points_balance == 0
        assert db.exec(select(DailyQuestClaim).where(DailyQuestClaim.user_id == uid)).first() is None


@pytest.mark.parametrize("keys", [("backtest_run", "paper_start"), ("backtest_run", "backtest_run")])
def test_simultaneous_quest_requests_keep_wallet_and_ledger_consistent(keys):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.db import DailyQuestClaim, PointLedger, User, get_session
    from sqlmodel import select

    token = _signup()
    uid = client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"]
    before = _balance(token)
    loaded = Barrier(2)

    def claim(key):
        with get_session() as db:
            user = db.get(User, uid)
            loaded.wait(timeout=5)
            return quests.complete(db, user, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(claim, keys))
    expected = len(set(keys))
    assert sum(reply is not None for reply in replies) == expected
    with get_session() as db:
        assert db.get(User, uid).points_balance == before + 10 * expected
        claims = db.exec(select(DailyQuestClaim).where(DailyQuestClaim.user_id == uid)).all()
        ledger = db.exec(select(PointLedger).where(PointLedger.user_id == uid, PointLedger.reason == "quest")).all()
        assert len(claims) == len(ledger) == expected
        assert sorted(row.balance_after for row in ledger) == [before + 10 * (i + 1) for i in range(expected)]
