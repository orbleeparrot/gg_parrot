"""Shared worker guard stays lightweight and preserves legacy news config."""
import pytest

from app import db


@pytest.mark.parametrize("render, required, dialect, rejected", [
    ("", None, "sqlite", False), ("true", None, "sqlite", True),
    ("true", None, "postgresql", False), ("", "true", "sqlite", True),
    ("", "YES", "sqlite", True), ("true", "false", "sqlite", False),
])
def test_shared_guard_preserves_worker_database_configuration(monkeypatch, render, required, dialect, rejected):
    monkeypatch.setenv("RENDER", render)
    if required is None:
        monkeypatch.delenv("POSITION_NEWS_REQUIRE_POSTGRES", raising=False)
    else:
        monkeypatch.setenv("POSITION_NEWS_REQUIRE_POSTGRES", required)
    monkeypatch.setattr(db, "database_dialect", lambda: dialect)
    if rejected:
        with pytest.raises(RuntimeError, match="Postgres DATABASE_URL"):
            db.assert_shared_worker_database()
    else:
        db.assert_shared_worker_database()


def test_news_wrapper_preserves_existing_dialect_monkeypatch(monkeypatch):
    from app.agent_features.position_news import repository
    monkeypatch.setenv("POSITION_NEWS_REQUIRE_POSTGRES", "true")
    monkeypatch.setattr(db, "database_dialect", lambda: "postgresql")
    monkeypatch.setattr(repository, "database_dialect", lambda: "sqlite")
    with pytest.raises(RuntimeError, match="Postgres DATABASE_URL"):
        repository.assert_worker_database()
    monkeypatch.setattr(repository, "database_dialect", lambda: "postgresql")
    repository.assert_worker_database()
