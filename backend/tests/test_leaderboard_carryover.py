"""Daily reset keeps the top 3: carry-over, streak counting, idempotency."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import leaderboard as lb
from app.db import LeaderboardCarryover, LeaderboardEntry, get_session
from app.main import app

client = TestClient(app)

DAY_MS = lb.DAY_MS
_MACRO_JSON = (
    '{"symbol":"BTCUSDT","rule_type":"A","candle_interval":"1d",'
    '"params":{"take_profit_pct":5,"initial_capital":1000000},'
    '"risk":{"stop_loss_pct":3},"period":{"preset":"3m"}}'
)


@pytest.fixture(autouse=True)
def _stub_paper(monkeypatch):
    """No market data, no real paper sessions; returns come from `_returns`."""
    counter = {"next": 900_000}
    returns: dict[int, float] = {}

    async def _fake_start(macro, symbol, mode):
        counter["next"] += 1
        return {"session_id": counter["next"]}

    def _fake_statuses(session_ids, db=None):
        return {
            sid: {"current_return": returns[sid], "current_equity": 1.0,
                  "status": "running", "mode": "live"}
            for sid in session_ids if sid in returns
        }

    monkeypatch.setattr("app.leaderboard.paper_mod.start_session", _fake_start)
    monkeypatch.setattr("app.leaderboard.paper_mod.get_statuses", _fake_statuses)
    monkeypatch.setattr("app.leaderboard.paper_mod.stop_session", lambda sid: None)
    monkeypatch.setattr("app.main.paper_mod.start_session", _fake_start)
    yield returns


@pytest.fixture(autouse=True)
def _fresh_day(monkeypatch):
    """Each test gets its own KST date key, so the day's claim is unclaimed."""
    monkeypatch.setattr(lb, "_carryover_done_date", None, raising=False)
    fake_date = "2099-" + secrets.token_hex(3)
    monkeypatch.setattr(lb, "_today_kst", lambda: fake_date)
    return fake_date


def _make_yesterday_entry(returns: dict, *, ret: float, session_id: int) -> int:
    """An entry sitting on yesterday's board with a known live return."""
    view = lb.create_entry(
        user_id="anon", username=f"u{secrets.token_hex(2)}", password_hash="",
        symbol="BTCUSDT", macro_json=_MACRO_JSON, human_summary="테스트 전략",
        paper_session_id=session_id,
    )
    returns[session_id] = ret
    with get_session() as db:
        row = db.get(LeaderboardEntry, view["id"])
        row.created_ms = lb.today_start_ms() - DAY_MS + 3_600_000  # 어제 01:00 KST
        row.created_at = "2000-01-01T00:00:00Z"
        db.add(row)
        db.commit()
    return view["id"]


def _entry(entry_id: int) -> LeaderboardEntry:
    with get_session() as db:
        return db.get(LeaderboardEntry, entry_id)


def _board_ids() -> set[int]:
    return {e["id"] for e in client.get("/api/leaderboard").json()["items"]}


def test_only_top_three_survive_the_daily_reset(_stub_paper):
    ids = [
        _make_yesterday_entry(_stub_paper, ret=r, session_id=sid)
        for sid, r in zip(range(101, 106), [1.0, 9.0, 5.0, -2.0, 7.0])
    ]
    assert lb.list_entries()["items"] == [] or set(ids).isdisjoint(
        {e["id"] for e in lb.list_entries()["items"]}
    ), "어제 엔트리는 이월 전 오늘 보드에 없어야 한다"

    board = _board_ids()  # 그날 첫 조회가 이월을 수행한다
    survivors = {ids[1], ids[4], ids[2]}  # 9.0 / 7.0 / 5.0
    assert survivors <= board
    assert not ({ids[0], ids[3]} & board), "4·5등은 초기화와 함께 사라진다"


def test_streak_counts_days_defended(_stub_paper):
    entry_id = _make_yesterday_entry(_stub_paper, ret=3.0, session_id=201)
    assert _entry(entry_id).streak_days == 1

    client.get("/api/leaderboard")
    row = _entry(entry_id)
    assert row.streak_days == 2
    assert row.created_ms == lb.today_start_ms()
    assert row.first_created_ms is not None, "원 등록 시각은 보존한다"

    view = next(e for e in client.get("/api/leaderboard").json()["items"] if e["id"] == entry_id)
    assert view["streak_days"] == 2 and view["defending"] is True


def test_carried_entry_keeps_its_session_and_accumulated_return(_stub_paper):
    """이월은 초기화가 아니다 — 등록 시점부터의 수익률이 그대로 순위에 남는다."""
    entry_id = _make_yesterday_entry(_stub_paper, ret=8.25, session_id=202)
    row_before = _entry(entry_id)

    client.get("/api/leaderboard")

    row = _entry(entry_id)
    assert row.paper_session_id == 202, "페이퍼 세션을 새로 시작하면 안 된다"
    assert row.created_at == row_before.created_at, "등록 시각은 손대지 않는다"

    view = next(e for e in client.get("/api/leaderboard").json()["items"] if e["id"] == entry_id)
    assert view["return_pct"] == 8.25, "누적 수익률이 0으로 초기화되면 안 된다"


def test_finished_macro_defends_with_its_final_return(_stub_paper, monkeypatch):
    """익절·손절·보유시간으로 종료된 매크로도 종료 시점 수익률로 순위를 지킨다."""
    done_id = _make_yesterday_entry(_stub_paper, ret=6.0, session_id=203)
    live_id = _make_yesterday_entry(_stub_paper, ret=2.0, session_id=204)

    def _stopped_statuses(session_ids, db=None):
        return {
            sid: {
                "current_return": _stub_paper[sid], "current_equity": 1.0,
                "status": "stopped" if sid == 203 else "running", "mode": "live",
            }
            for sid in session_ids if sid in _stub_paper
        }

    monkeypatch.setattr("app.leaderboard.paper_mod.get_statuses", _stopped_statuses)

    items = client.get("/api/leaderboard").json()["items"]
    ranked = [e["id"] for e in items if e["id"] in (done_id, live_id)]
    assert ranked == [done_id, live_id], "종료된 매크로가 더 높은 수익률이면 위에 온다"
    done = next(e for e in items if e["id"] == done_id)
    assert done["return_pct"] == 6.0 and done["paper_status"] == "stopped"
    assert _entry(done_id).streak_days == 2, "종료됐어도 상위권이면 이월된다"


def test_streak_keeps_growing_across_days(_stub_paper, monkeypatch):
    entry_id = _make_yesterday_entry(_stub_paper, ret=3.0, session_id=301)
    client.get("/api/leaderboard")

    # 하루 더 흐른 척: 방어 중인 엔트리를 다시 어제로 돌리고 새 날짜로 이월한다.
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        row.created_ms = lb.today_start_ms() - DAY_MS + 3_600_000
        _stub_paper[row.paper_session_id] = 4.0
        db.add(row)
        db.commit()
    monkeypatch.setattr(lb, "_carryover_done_date", None, raising=False)
    monkeypatch.setattr(lb, "_today_kst", lambda: "2099-" + secrets.token_hex(3))

    client.get("/api/leaderboard")
    assert _entry(entry_id).streak_days == 3


def test_carryover_runs_once_per_day(_stub_paper, _fresh_day):
    entry_id = _make_yesterday_entry(_stub_paper, ret=3.0, session_id=401)

    client.get("/api/leaderboard")
    client.get("/api/leaderboard")
    client.get("/api/leaderboard")
    assert _entry(entry_id).streak_days == 2, "재조회가 방어 일수를 더 올리면 안 된다"

    with get_session() as db:
        marks = db.exec(
            select(LeaderboardCarryover).where(LeaderboardCarryover.date_kst == _fresh_day)
        ).all()
    assert len(marks) == 1 and marks[0].carried == 1


def test_new_entry_is_not_marked_as_defending(_stub_paper):
    view = lb.create_entry(
        user_id="anon", username="newbie", password_hash="",
        symbol="BTCUSDT", macro_json=_MACRO_JSON, human_summary="오늘 등록",
        paper_session_id=None,
    )
    assert view["streak_days"] == 1 and view["defending"] is False
