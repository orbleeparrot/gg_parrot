"""A one-connection pool must be available while callers wait on HTTP or AI.

All rows and market/model callbacks are isolated fixtures; no external API runs.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel, create_engine, select

from app import ask, coach, db as db_mod, main
from app.db import AskMacroSession, User
from app.engine import BacktestResult, Macro


@pytest.fixture
def one_connection(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'external-work.db'}", poolclass=QueuePool,
        pool_size=1, max_overflow=0, pool_timeout=0.05,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "_engine", engine)
    with Session(engine) as db:
        user = User(email="external@example.com", username="external_work",
                    password_hash="fixture", created_at="2026-10-08T00:00:00Z",
                    ask_consent_version=ask.DISCLAIMER_VERSION)
        db.add(user)
        db.commit()
        db.refresh(user)
        yield engine, db, user
    engine.dispose()


def available(engine):
    assert engine.pool.checkedout() == 0, "external work retained a DB checkout"
    # Provider health/usage callbacks need their own short DB transaction.
    with db_mod.get_session() as nested:
        assert nested.exec(select(User.id)).first() is not None


def request():
    return ask.CandidatesRequest(risk_profile="balanced", market="spot",
                                 invest_horizon="weeks", watch_frequency="sometimes")


def macro():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "A", "position_side": "long",
        "params": {"take_profit_pct": 5, "initial_capital": 1000},
        "risk": {"invest_ratio": 0.5, "stop_loss_pct": 3},
        "period": {"preset": "3m"},
    })


def result():
    return BacktestResult(initial_capital=1000, final_equity=1000, final_return_pct=0,
                          mdd_pct=0, win_rate_pct=0, total_trades=0, equity_curve=[])


def question():
    return {"key": coach.FIRST_KEY, "page": 0, "ai": False}


def flow(db, user, *, is_coach=False):
    now = int(time.time() * 1000)
    row = AskMacroSession(
        user_id=user.id, day_kst=ask.today_kst(),
        request_json=json.dumps({"kind": "coach", "exchange": "binance"}) if is_coach
            else request().model_dump_json(),
        candidates_json=json.dumps(coach._empty_state("binance")) if is_coach else "[]",
        expires_ms=now + ask.SESSION_TTL_MS, created_ms=now,
        created_at="2026-10-08T00:00:00Z",
        disclaimer_version=ask.DISCLAIMER_VERSION,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_backtest_releases_auth_read_before_market_work(one_connection, monkeypatch):
    engine, db, user = one_connection
    def run(_macro):
        available(engine)
        return result(), [], "fixture", "fixture"
    monkeypatch.setattr(main, "_run_any", run)
    response = main.backtest(main.BacktestRequest(macro=macro()), account=user, db=db)
    assert response["quest"]["key"] == "backtest_run"


def test_paper_start_releases_auth_before_awaited_history(one_connection, monkeypatch):
    engine, db, user = one_connection
    async def start(*_args):
        available(engine)
        return {"session_id": 123}
    monkeypatch.setattr(main.paper_mod, "start_session", start)
    response = asyncio.run(main.paper_start(main.PaperStartRequest(macro=macro(), symbol="BTCUSDT"),
                                           account=user, db=db))
    assert response["quest"]["key"] == "paper_start"


def test_ask_releases_db_before_listing_model_and_evaluation(one_connection, monkeypatch):
    engine, db, user = one_connection
    row = flow(db, user)
    def listings(*_args):
        available(engine)
        return {"BTCUSDT"}
    def propose(*_args):
        available(engine)
        return []
    def evaluate(*_args):
        available(engine)
        return []
    monkeypatch.setattr(ask, "_allowed_symbols", listings)
    monkeypatch.setattr(ask, "propose_with_ai", propose)
    monkeypatch.setattr(ask, "evaluate", evaluate)
    response = ask.run_ask(db, user, ask.AskRequest(session_id=row.id, symbol="BTCUSDT"), lambda _: result())
    assert response["results"] == [] and response["refunded"] is True


def test_candidates_keep_reserved_quota_but_release_db_before_generation(one_connection, monkeypatch):
    engine, db, user = one_connection
    def tickers():
        available(engine)
        return [{"symbol": "BTCUSDT"}]
    def choose(*_args, **_kwargs):
        available(engine)
        with db_mod.get_session() as other:
            assert other.exec(select(AskMacroSession)).first() is not None
        return [{"symbol": "BTCUSDT"}], False
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", tickers)
    monkeypatch.setattr(ask.ask_candidates, "build_pool", lambda *_args, **_kwargs: [{"symbol": "BTCUSDT"}])
    monkeypatch.setattr(ask.ask_candidates, "choose", choose)
    response = ask.run_candidates(db, user, request())
    assert response["candidates"] == [{"symbol": "BTCUSDT"}]


def test_coach_start_releases_reserved_session_before_voice(one_connection, monkeypatch):
    engine, db, user = one_connection
    def voice(*_args):
        available(engine)
        return question()
    monkeypatch.setattr(coach, "_first_question", voice)
    assert coach.start(db, user)["session_id"] > 0


@pytest.mark.parametrize("operation", ["more", "back", "answer"])
def test_coach_turns_release_db_before_voice(one_connection, monkeypatch, operation):
    engine, db, user = one_connection
    row = flow(db, user, is_coach=True)
    def voice(*_args):
        available(engine)
        return question()
    monkeypatch.setattr(coach, "_question_view", voice)
    monkeypatch.setattr(coach, "next_key", lambda _answers: coach.FIRST_KEY)
    monkeypatch.setattr(coach, "patch_for", lambda *_args: {})
    if operation == "more":
        response = coach.more(db, user, row.id, coach.FIRST_KEY)
    elif operation == "answer":
        response = coach.answer(db, user, row.id, coach.FIRST_KEY, "fixture")
    else:
        response = coach.back(db, user, row.id)
    assert response["question"]["key"] == coach.FIRST_KEY


@pytest.mark.parametrize("change", ["deleted", "auth_version"])
def test_ask_revalidates_account_after_external_work(one_connection, monkeypatch, change):
    engine, db, user = one_connection
    row = flow(db, user)
    session_id, user_id = row.id, user.id
    monkeypatch.setattr(ask, "_allowed_symbols", lambda *_args: {"BTCUSDT"})
    def propose(*_args):
        available(engine)
        with db_mod.get_session() as other:
            account = other.get(User, user_id)
            if change == "deleted":
                account.is_deleted = True
            else:
                account.auth_version += 1
            other.add(account)
            other.commit()
        return []
    monkeypatch.setattr(ask, "propose_with_ai", propose)
    monkeypatch.setattr(ask, "evaluate", lambda *_args: [])
    with pytest.raises(HTTPException) as error:
        ask.run_ask(db, user, ask.AskRequest(session_id=session_id, symbol="BTCUSDT"), lambda _: result())
    assert error.value.status_code == 401
    assert db.get(AskMacroSession, session_id).ask_count == 0


def test_release_never_discards_uncommitted_changes(one_connection):
    _engine, db, user = one_connection
    user.bio = "uncommitted fixture change"
    with pytest.raises(RuntimeError, match="committed database changes"):
        ask.release_read_session(db, user)
    assert user.bio == "uncommitted fixture change" and user in db.dirty


def test_coach_does_not_overwrite_a_turn_changed_during_voice(one_connection, monkeypatch):
    engine, db, user = one_connection
    row = flow(db, user, is_coach=True)
    session_id = row.id
    concurrent_state = {**coach._empty_state("binance"), "pages": {coach.FIRST_KEY: 9}}
    def voice(*_args):
        available(engine)
        with db_mod.get_session() as other:
            current = other.get(AskMacroSession, session_id)
            current.candidates_json = json.dumps(concurrent_state)
            other.add(current)
            other.commit()
        return question()
    monkeypatch.setattr(coach, "_question_view", voice)
    monkeypatch.setattr(coach, "next_key", lambda _: coach.FIRST_KEY)
    with pytest.raises(ask.AskError) as error:
        coach.more(db, user, session_id, coach.FIRST_KEY)
    assert error.value.status == 409
    assert json.loads(db.get(AskMacroSession, session_id).candidates_json) == concurrent_state


def test_worker_repository_operations_release_the_one_connection(one_connection):
    from app import api_usage, collector_runs, news_ai_budget
    from app.agent_features.position_news import articles, repository
    engine, db, user = one_connection
    ask.release_read_session(db, user)
    title = "Bitcoin ETF approved"
    source = {"title": title, "source": "CoinDesk", "url": "https://example.test/bitcoin-etf"}
    articles.upsert_articles("BTC", [source])
    available(engine)
    claim = repository.claim_title_translations([title], prompt_version="fixture")
    assert claim["claimed"] == [title]
    available(engine)
    assert news_ai_budget.reserve("title", [title]) == {title}
    available(engine)
    repository.store_title_translations({title: "비트코인 ETF 승인"}, claim_token=claim["claim_token"])
    available(engine)
    localized = {**source, "original_title": title, "title": "비트코인 ETF 승인"}
    articles.upsert_articles("BTC", [localized])
    available(engine)
    assert articles.read_article_feed("BTC")["items"][0]["title"] == localized["title"]
    available(engine)
    snapshot = repository.claim_snapshot(asset_symbol="BTC", snapshot_key="fixture-checkout",
        news_payload={"items": [localized]}, prompt_version="fixture", model="fixture",
        retry_incomplete=False)
    available(engine)
    assert repository.complete_snapshot(snapshot.snapshot_id,
        {"analysis_status": "ready", "analysis_source": "rule", "items": []},
        claim_token=snapshot.claim_token)
    available(engine)
    assert repository.get_latest_snapshot("BTC") is not None
    available(engine)
    assert api_usage.record_openai_usage(model="fixture", purpose="fixture", usage=None, ok=False)
    available(engine)
    assert collector_runs.record_run("position_news", started_ms=1, finished_ms=2) is not None
    available(engine)


def stub_candidates(monkeypatch, *, choose=None):
    monkeypatch.setattr(ask.hotcoins, "get_cached_tickers", lambda: [{"symbol": "BTCUSDT"}])
    monkeypatch.setattr(ask.ask_candidates, "build_pool", lambda *_args, **_kwargs: [{"symbol": "BTCUSDT"}])
    monkeypatch.setattr(ask.ask_candidates, "choose",
                        choose or (lambda *_args, **_kwargs: ([{"symbol": "BTCUSDT"}], False)))


@pytest.mark.parametrize("kind", ["candidates", "coach"])
@pytest.mark.parametrize("paid", [False, True])
def test_reservation_refund_recovers_an_aborted_db_transaction(one_connection, monkeypatch, kind, paid):
    from app.db import AskExtraCredit
    _engine, db, user = one_connection
    original = []
    if paid:
        created_at, now_ms = ask._now()
        db.add(AskExtraCredit(user_id=user.id, day_kst=ask.today_kst(), price=10,
                             created_at=created_at, created_ms=now_ms))
        db.commit()
        monkeypatch.setattr(ask, "free_remaining_today", lambda *_args: 0)
        monkeypatch.setattr(coach, "free_remaining_today", lambda *_args: 0)
    def abort_resume(db, _user, **_kwargs):
        # A genuine failed flush leaves Session unusable until rollback.
        db.add(User(email="external@example.com", username="duplicate_fixture",
                    password_hash="fixture", created_at="2026-10-08T00:00:00Z"))
        try:
            db.flush()
        except IntegrityError as error:
            original.append(error)
            raise
    stub_candidates(monkeypatch)
    monkeypatch.setattr(coach, "_first_question", lambda *_args: question())
    monkeypatch.setattr(ask, "resume_external_user", abort_resume)
    monkeypatch.setattr(coach, "resume_external_user", abort_resume)
    with pytest.raises(IntegrityError) as caught:
        if kind == "candidates":
            ask.run_candidates(db, user, request())
        else:
            coach.start(db, user)
    assert caught.value is original[0]
    assert db.exec(select(AskMacroSession)).first() is None
    if paid:
        assert db.exec(select(AskExtraCredit)).one().used_session_id is None


@pytest.mark.parametrize("kind", ["candidates", "coach"])
def test_refund_failure_preserves_original_error_and_sanitizes_log(one_connection, monkeypatch, caplog, kind):
    _engine, db, user = one_connection
    original = ValueError("fixture external failure")
    def external_failure(*_args, **_kwargs):
        raise original
    def refund_failure(*_args, **_kwargs):
        raise RuntimeError("private-refund-error-secret")
    stub_candidates(monkeypatch, choose=external_failure)
    monkeypatch.setattr(coach, "_first_question", external_failure)
    monkeypatch.setattr(db, "get", refund_failure)
    with pytest.raises(ValueError) as caught:
        if kind == "candidates":
            ask.run_candidates(db, user, request())
        else:
            coach.start(db, user)
    assert caught.value is original
    assert "refund" in caplog.text.lower() and "RuntimeError" in caplog.text
    assert "private-refund-error-secret" not in caplog.text
