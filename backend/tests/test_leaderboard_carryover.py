"""Daily reset keeps the top 3: carry-over, streak counting, idempotency."""
from __future__ import annotations

import asyncio
import secrets

from tests.leaderboard_helpers import publish_ready_board
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
    monkeypatch.setattr(lb, "_durable_statuses", lambda db, ids: _fake_statuses(ids, db=db))
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
    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
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

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")
    row = _entry(entry_id)
    assert row.streak_days == 2
    assert row.created_ms == lb.today_start_ms()
    assert row.first_created_ms is not None, "원 등록 시각은 보존한다"

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    view = next(e for e in client.get("/api/leaderboard").json()["items"] if e["id"] == entry_id)
    assert view["streak_days"] == 2 and view["defending"] is True


def test_carried_entry_keeps_its_session_and_accumulated_return(_stub_paper):
    """이월은 초기화가 아니다 — 등록 시점부터의 수익률이 그대로 순위에 남는다."""
    entry_id = _make_yesterday_entry(_stub_paper, ret=8.25, session_id=202)
    row_before = _entry(entry_id)

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")

    row = _entry(entry_id)
    assert row.paper_session_id == 202, "페이퍼 세션을 새로 시작하면 안 된다"
    assert row.created_at == row_before.created_at, "등록 시각은 손대지 않는다"

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
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
    monkeypatch.setattr(lb, "_durable_statuses", lambda db, ids: _stopped_statuses(ids, db=db))

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    items = client.get("/api/leaderboard").json()["items"]
    ranked = [e["id"] for e in items if e["id"] in (done_id, live_id)]
    assert ranked == [done_id, live_id], "종료된 매크로가 더 높은 수익률이면 위에 온다"
    done = next(e for e in items if e["id"] == done_id)
    assert done["return_pct"] == 6.0 and done["paper_status"] == "stopped"
    assert _entry(done_id).streak_days == 2, "종료됐어도 상위권이면 이월된다"


def test_streak_keeps_growing_across_days(_stub_paper, monkeypatch):
    entry_id = _make_yesterday_entry(_stub_paper, ret=3.0, session_id=301)
    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")

    # 하루 더 흐른 척: 방어 중인 엔트리를 다시 어제로 돌리고 새 날짜로 이월한다.
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        row.created_ms = lb.today_start_ms() - DAY_MS + 3_600_000
        _stub_paper[row.paper_session_id] = 4.0
        db.add(row)
        db.commit()
    monkeypatch.setattr(lb, "_carryover_done_date", None, raising=False)
    next_date = "2099-" + secrets.token_hex(3)
    monkeypatch.setattr(lb, "_today_kst", lambda: next_date)

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")
    assert _entry(entry_id).streak_days == 3


def test_carryover_runs_once_per_day(_stub_paper, _fresh_day):
    entry_id = _make_yesterday_entry(_stub_paper, ret=3.0, session_id=401)

    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")
    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
    client.get("/api/leaderboard")
    asyncio.run(lb.ensure_today_carryover())
    publish_ready_board()
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


# --- 순위 포인트 보상 (2026-09-22): 자정 이월 때 어제 최종 순위로 지급, 사람 1명당 최고 순위 1번, 봇 제외 ------
from app.db import NotificationMessage, PointLedger, User


def _user(points: int = 0) -> int:
    with get_session() as db:
        u = User(email=f"lb{secrets.token_hex(3)}@ex.com", username=f"lb_{secrets.token_hex(3)}",
                 password_hash="x", points_balance=points, created_at="2026-09-22T00:00:00Z")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id


def _owned_yesterday_entry(returns, *, ret, session_id, owner_id, is_ai=False, streak_days=1) -> int:
    entry_id = _make_yesterday_entry(returns, ret=ret, session_id=session_id)
    with get_session() as db:
        row = db.get(LeaderboardEntry, entry_id)
        row.owner_user_id = owner_id
        row.is_ai = is_ai
        row.streak_days = streak_days
        db.add(row)
        db.commit()
    return entry_id


def _balance(uid: int) -> int:
    with get_session() as db:
        return db.get(User, uid).points_balance


def test_rank_rewards_are_paid_once_at_carryover(_stub_paper, _fresh_day):
    first, second, third, fourth, eleventh = (_user() for _ in range(5))
    bot = _user()
    _owned_yesterday_entry(_stub_paper, ret=9.0, session_id=301, owner_id=first)
    _owned_yesterday_entry(_stub_paper, ret=8.0, session_id=302, owner_id=second)
    _owned_yesterday_entry(_stub_paper, ret=7.0, session_id=303, owner_id=third)
    _owned_yesterday_entry(_stub_paper, ret=6.0, session_id=304, owner_id=fourth)
    _owned_yesterday_entry(_stub_paper, ret=5.5, session_id=305, owner_id=bot, is_ai=True)  # 봇은 순위에 끼지만 보상 없음
    for i in range(6):
        _owned_yesterday_entry(_stub_paper, ret=5.0 - i * 0.1, session_id=310 + i, owner_id=fourth)  # 같은 사람 여러 개
    _owned_yesterday_entry(_stub_paper, ret=0.5, session_id=320, owner_id=eleventh)  # 12등

    asyncio.run(lb.ensure_today_carryover())
    asyncio.run(lb.ensure_today_carryover())  # 두 번 불러도 한 번만

    assert _balance(first) == 100 and _balance(second) == 60 and _balance(third) == 40
    assert _balance(fourth) == 15  # 4등 한 번만(그 사람의 다른 엔트리는 무시)
    assert _balance(bot) == 0 and _balance(eleventh) == 0
    with get_session() as db:
        rows = db.exec(select(PointLedger).where(PointLedger.reason == "leaderboard_rank", PointLedger.user_id == first)).all()
        assert [r.delta for r in rows] == [100] and rows[0].ref == f"lb:{_fresh_day}:1"
        note = db.exec(select(NotificationMessage).where(NotificationMessage.user_id == first, NotificationMessage.kind == "quest")).first()
        assert note is not None and "1등" in note.title and "100P" in note.title


def test_defending_top_three_gets_streak_bonus_capped(_stub_paper, _fresh_day):
    day2, day7 = _user(), _user()
    _owned_yesterday_entry(_stub_paper, ret=9.0, session_id=331, owner_id=day2, streak_days=2)  # 어제까지 2일째 → +10
    _owned_yesterday_entry(_stub_paper, ret=8.0, session_id=332, owner_id=day7, streak_days=7)  # 7일째 → +60 → 상한 50
    asyncio.run(lb.ensure_today_carryover())
    assert _balance(day2) == 110 and _balance(day7) == 110
