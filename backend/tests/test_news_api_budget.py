"""The HTTP provider allowance is shared and separate from AI spending."""
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, SQLModel, create_engine, select

from app.agent_features.position_news import repository
from app.db import TickerNewsAiBudget


def test_daily_and_lifetime_limits_commit_together_and_survive_next_day(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        reserve = lambda now: repository.reserve_news_api_budget(
            daily_limit=1, total_limit=2, now_ms=now, db=db)
        assert reserve(0)
        assert not reserve(0)
        assert db.get(TickerNewsAiBudget, "coindesk_news:lifetime").used == 1
        assert reserve(86400000)
        assert not reserve(2 * 86400000)
        assert db.get(TickerNewsAiBudget, "coindesk_news:lifetime").used == 2
        assert db.get(TickerNewsAiBudget, "coindesk_news:1970-01-03") is None
        assert repository.reserve_ai_budget(daily_limit=1, now_ms=0, db=db)


def test_parallel_workers_cannot_overspend_either_allowance(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'parallel.db'}",
                           connect_args={"check_same_thread": False, "timeout": 10})
    SQLModel.metadata.create_all(engine)
    def reserve(_):
        with Session(engine) as db:
            return repository.reserve_news_api_budget(daily_limit=3, total_limit=2, now_ms=0, db=db)
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(reserve, range(8))) == 2
    with Session(engine) as db:
        assert sorted(row.used for row in db.exec(select(TickerNewsAiBudget))) == [2, 2]


def test_zero_budget_does_not_open_database(monkeypatch):
    monkeypatch.setattr(repository, "get_session", lambda: (_ for _ in ()).throw(AssertionError()))
    assert not repository.reserve_news_api_budget(daily_limit=0, total_limit=10)
    assert not repository.reserve_news_api_budget(daily_limit=10, total_limit=0)
