from __future__ import annotations

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app import account, auth, board, leaderboard, paper, runner
from app.agent_features.position_news import service as position_news_service
from app.db import BoardPost, LeaderboardEntry, PaperSession, RunSession, User


def _database():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


def _user() -> User:
    return User(
        email="scope@example.com",
        username="scope_user",
        password_hash="hash",
        points_balance=100,
        created_at="2026-08-26T00:00:00Z",
    )


def test_auth_dashboard_and_posts_reuse_one_injected_session(monkeypatch):
    engine = _database()

    def unexpected_session():
        raise AssertionError("service opened a second DB session")

    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(
            BoardPost(
                author_user_id=user.id,
                author_name=user.username,
                title="one",
                body="",
                created_at="2026-08-26T00:00:00Z",
                created_ms=1,
            )
        )
        db.commit()

        monkeypatch.setattr(auth, "_decode", lambda token: user.id)
        monkeypatch.setattr(auth, "get_session", unexpected_session)
        monkeypatch.setattr(account, "get_session", unexpected_session)
        monkeypatch.setattr(board, "get_session", unexpected_session)

        resolved = auth.current_user_in_session("Bearer token", db=db)
        dashboard = account.dashboard(resolved, db=db)
        posts = board.my_posts(resolved.id, db=db)

    assert dashboard["user"]["id"] == user.id
    assert [post["title"] for post in posts] == ["one"]


def test_paper_statuses_batch_missing_sessions_in_one_query(monkeypatch):
    engine = _database()
    with Session(engine) as db:
        rows = [
            PaperSession(
                macro_id="m1",
                symbol="BTCUSDT",
                mode="live",
                status="stopped",
                started_at="2026-08-26T00:00:00Z",
                virtual_balance=1_000,
                current_equity=1_010,
                current_return=1.0,
            ),
            PaperSession(
                macro_id="m2",
                symbol="ETHUSDT",
                mode="replay",
                status="stopped",
                started_at="2026-08-26T00:00:00Z",
                virtual_balance=1_000,
                current_equity=990,
                current_return=-1.0,
            ),
        ]
        db.add_all(rows)
        db.commit()
        for row in rows:
            db.refresh(row)

        queries = 0
        original_exec = db.exec

        def counted_exec(*args, **kwargs):
            nonlocal queries
            queries += 1
            return original_exec(*args, **kwargs)

        monkeypatch.setattr(db, "exec", counted_exec)
        monkeypatch.setattr(paper, "get_session", lambda: (_ for _ in ()).throw(
            AssertionError("batch lookup opened another session")
        ))

        statuses = paper.get_statuses([rows[0].id, rows[1].id, 999], db=db)

    assert queries == 1
    assert statuses[rows[0].id]["current_return"] == 1.0
    assert statuses[rows[1].id]["mode"] == "replay"
    assert 999 not in statuses


def test_leaderboard_uses_injected_session_and_batched_paper_statuses(monkeypatch):
    engine = _database()
    with Session(engine) as db:
        paper_rows = []
        for index, ret in enumerate((2.0, -1.0), start=1):
            row = PaperSession(
                macro_id=f"m{index}",
                symbol="BTCUSDT",
                mode="live",
                status="stopped",
                started_at="2026-08-26T00:00:00Z",
                virtual_balance=1_000,
                current_equity=1_000 + ret * 10,
                current_return=ret,
            )
            db.add(row)
            paper_rows.append(row)
        db.commit()
        for row in paper_rows:
            db.refresh(row)

        created_ms = leaderboard.today_start_ms() + 1
        for index, session in enumerate(paper_rows, start=1):
            db.add(
                LeaderboardEntry(
                    user_id=f"u{index}",
                    nickname=f"u{index}",
                    username=f"u{index}",
                    symbol="BTCUSDT",
                    macro_json="{}",
                    human_summary="summary",
                    paper_session_id=session.id,
                    created_at="2026-08-26T00:00:00Z",
                    created_ms=created_ms + index,
                )
            )
        db.commit()

        monkeypatch.setattr(leaderboard, "get_session", lambda: (_ for _ in ()).throw(
            AssertionError("leaderboard opened another session")
        ))
        calls = []
        original = paper.get_statuses

        def counted_statuses(ids, *, db=None):
            calls.append(tuple(ids))
            return original(ids, db=db)

        monkeypatch.setattr(paper, "get_statuses", counted_statuses)

        result = leaderboard.list_entries(db=db)

    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert [item["return_pct"] for item in result["items"]] == [2.0, -1.0]


def test_runner_session_poll_uses_one_query_and_keeps_all_active_rows(monkeypatch):
    engine = _database()
    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.commit()
        db.refresh(user)
        for index, status in enumerate(("running", "stopped", "running", "error"), start=1):
            db.add(
                RunSession(
                    user_id=user.id,
                    symbol=f"C{index}USDT",
                    status=status,
                    started_at=f"2026-08-26T00:00:0{index}Z",
                    last_heartbeat_at=f"2026-08-26T00:00:0{index}Z",
                )
            )
        db.commit()
        user_id = user.id

        statements = []

        def count_statement(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_statement)
        monkeypatch.setattr(runner, "get_session", lambda: (_ for _ in ()).throw(
            AssertionError("runner poll opened another session")
        ))

        result = runner.list_sessions(user_id, limit=1, db=db)

    assert len(statements) == 1
    assert {item["symbol"] for item in result["active"]} == {"C1USDT", "C3USDT"}
    assert [item["symbol"] for item in result["recent"]] == ["C4USDT"]


def test_position_news_snapshot_lookup_reuses_callers_session(monkeypatch):
    shared_db = object()
    observed = []

    def load(symbol, db=None):
        observed.append((symbol, db))
        return None

    monkeypatch.setattr(position_news_service, "_load_latest_snapshot", load)

    payload = position_news_service.get_position_news(
        {
            "session_id": 1,
            "symbol": "EDENUSDT",
            "position_side": "long",
            "in_position": True,
        },
        db=shared_db,
    )

    assert payload["analysis_status"] == "pending"
    assert observed == [("EDEN", shared_db)]
