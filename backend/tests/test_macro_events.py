"""MacroEventDaily 카운터 — 노출·열람·언락이 누적 upsert 되고, 없는 id 는 버리며, 실패는 삼키고 호출자 트랜잭션을 살린다."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import macro_events
from app.db import LeaderboardEntry, MacroEventDaily, get_session
from app.main import app

client = TestClient(app)


def _entry(db) -> int:
    now_iso, now_ms = "2026-09-17T00:00:00Z", 1_789_000_000_000
    row = LeaderboardEntry(user_id="anon", nickname="n", symbol="BTCUSDT", macro_json="{}", human_summary="t", created_at=now_iso, created_ms=now_ms)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _counts(entry_id: int) -> tuple[int, int, int]:
    with get_session() as db:
        row = db.get(MacroEventDaily, (macro_events._today_kst(), entry_id))
    return (row.impressions, row.opens, row.unlocks) if row else (0, 0, 0)


def test_clean_ids_dedupes_drops_junk_and_caps():
    assert macro_events._clean_ids([3, "3", 0, -1, "x", None, 2, 2.0], 100) == [2, 3]
    assert len(macro_events._clean_ids(range(1, 151), macro_events.MAX_IMPRESSION_IDS)) == 100
    assert macro_events._clean_ids(None, 10) == []


def test_counters_accumulate_across_calls_and_ignore_unknown_ids():
    with get_session() as db:
        first, second = _entry(db), _entry(db)
        bogus = second + 1_000_000
        assert macro_events.record_impressions(db, [first, second, first, bogus]) == 2
        assert macro_events.record_impressions(db, [first]) == 1
        assert macro_events.record_open(db, first) == 1
        assert macro_events.record_open(db, bogus) == 0
        assert macro_events.record_unlock(db, second, commit=True) == 1
        assert macro_events.record_unlock(db, second, commit=True) == 1
    assert _counts(first) == (2, 1, 0)
    assert _counts(second) == (1, 0, 2)
    assert _counts(bogus) == (0, 0, 0)


def test_record_unlock_rides_the_callers_transaction():
    with get_session() as db:
        entry_id = _entry(db)
    with get_session() as db:
        # 실제 언락처럼 결제 행이 먼저 flush 된 트랜잭션에 얹는다. (pysqlite 는 DML 전에 SAVEPOINT 가 오면 그것을 BEGIN 으로
        # 삼아 RELEASE 때 커밋해 버린다 — 운영 Postgres 에는 없는 버릇이라 테스트만 실제 순서를 따른다.)
        db.add(MacroEventDaily(day_kst="1998-01-01", entry_id=entry_id, impressions=1))
        db.flush()
        macro_events.record_unlock(db, entry_id)  # 커밋하지 않는다
        assert _counts(entry_id) == (0, 0, 0)  # 다른 세션에서는 아직 안 보인다
        db.commit()
    assert _counts(entry_id) == (0, 0, 1)


def test_failure_is_swallowed_and_callers_transaction_survives(monkeypatch):
    with get_session() as db:
        entry_id = _entry(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(macro_events, "_bump", _boom)
    with get_session() as db:
        pending = MacroEventDaily(day_kst="1999-01-01", entry_id=entry_id, impressions=7)  # 호출자가 같은 트랜잭션에 얹어 둔 변경
        db.add(pending)
        db.flush()
        assert macro_events.record_unlock(db, entry_id) == 0
        db.commit()
    with get_session() as db:
        assert db.get(MacroEventDaily, ("1999-01-01", entry_id)).impressions == 7
    with get_session() as db:  # 커밋까지 맡는 변형은 자기 세션을 되돌리고 0 을 돌려준다
        assert macro_events.record_open(db, entry_id) == 0
        assert macro_events.record_impressions(db, [entry_id]) == 0
    assert _counts(entry_id) == (0, 0, 0)

    class _Broken:
        def begin_nested(self):
            raise RuntimeError("no session")

        def rollback(self):
            raise RuntimeError("no rollback either")

    assert macro_events.record_impressions(_Broken(), [entry_id]) == 0


# --- 실제 언락 흐름: 첫 결제만 센다 ---------------------------------------------------------------------
@pytest.fixture
def _stub_paper(monkeypatch):
    async def _fake_start(macro, symbol, mode):
        return {"session_id": None}

    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={"email": f"e{tok}@ex.com", "username": f"e_{tok}", "password": "password123"}).json()
    return body["token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_unlock_counts_first_payment_only(_stub_paper):
    seller_tok, _ = _signup()
    macro = {"symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d", "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
             "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"}}
    res = client.post("/api/leaderboard/register", json={"macro": macro, "username": "", "password": "", "user_id": "anon"}, headers=_auth(seller_tok))
    assert res.status_code == 200, res.text
    entry_id = res.json()["entry"]["id"]
    buyer_tok, _ = _signup()
    assert client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(buyer_tok)).status_code == 200
    assert _counts(entry_id) == (0, 0, 1)
    assert client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(buyer_tok)).status_code == 200  # 재언락은 무료·멱등
    assert _counts(entry_id) == (0, 0, 1)
    assert client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(seller_tok)).status_code == 400  # 내 매크로
    assert _counts(entry_id) == (0, 0, 1)
