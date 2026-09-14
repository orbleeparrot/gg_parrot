"""Startup migration contracts for the production deadlock failure.

The catalog/transaction doubles never connect to the production database.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app import db


def test_supabase_migrations_have_unique_versions():
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    versions = {}
    for path in sorted(migrations.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        assert version.isdigit(), path.name
        assert version not in versions, f"duplicate version: {versions.get(version)} / {path.name}"
        versions[version] = path.name


@pytest.mark.parametrize("table", ["dailyquestclaim", "runsessionevent"])
def test_new_private_member_tables_are_secured_on_app_boot(table):
    state = current_schema()
    state["tables"][table] = False
    state["grants"] = {(table, role) for role in ("PUBLIC", "anon", "authenticated")}
    assert db._pg_migration_statements(state) == [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM anon",
        f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM authenticated",
    ]


def test_existing_news_article_schema_adds_pending_column_before_index():
    state = current_schema()
    state["columns"].pop(("newsarticle", "enrichment_pending"))
    state["indexes"].discard("ix_newsarticle_enrichment")
    statements = db._pg_migration_statements(state)
    column = "ALTER TABLE newsarticle ADD COLUMN IF NOT EXISTS enrichment_pending BOOLEAN NOT NULL DEFAULT TRUE"
    index = "CREATE INDEX IF NOT EXISTS ix_newsarticle_enrichment ON newsarticle (enrichment_pending, last_seen_ms)"
    assert column in statements
    assert index in statements
    assert statements.index(column) < statements.index(index)


def test_existing_sqlite_news_rows_survive_startup_upgrade(tmp_path, monkeypatch):
    from sqlalchemy import inspect
    from app.agent_features.position_news import articles
    engine = create_engine(f"sqlite:///{tmp_path / 'older-news.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE newsarticle (
            asset_symbol VARCHAR NOT NULL, article_id VARCHAR NOT NULL,
            revision BIGINT NOT NULL, ready BOOLEAN NOT NULL,
            item_json VARCHAR NOT NULL, assessment_json VARCHAR NOT NULL,
            analysis_source VARCHAR NOT NULL, analysis_status VARCHAR NOT NULL,
            first_seen_ms BIGINT NOT NULL, last_seen_ms BIGINT NOT NULL,
            PRIMARY KEY (asset_symbol, article_id))""")
        conn.exec_driver_sql("""INSERT INTO newsarticle VALUES (
            'BTC', 'old', 1, TRUE, '{"title":"기존 비트코인 뉴스"}', '{}',
            'rule', 'ready', 1, 1)""")
    try:
        db.init_db()
        db.init_db()  # Repeated reloads must be a no-op and retain the article.
        assert "ix_newsarticle_enrichment" in {item["name"] for item in inspect(engine).get_indexes("newsarticle")}
        with db.get_session() as session:
            row = session.get(articles.NewsArticle, ("BTC", "old"))
            assert row is not None and "기존 비트코인 뉴스" in row.item_json
            assert row.enrichment_pending is True
    finally:
        engine.dispose()


def current_schema():
    columns = {(table.name, column.name): "text"
               for table in db.SQLModel.metadata.tables.values() for column in table.columns}
    for table, names in db._PG_BIGINT_COLUMNS.items():
        columns.update({(table, name): "bigint" for name in names})
    return {
        "tables": {table.name: table.name in db._PG_PRIVATE_CACHE_TABLES
                   for table in db.SQLModel.metadata.tables.values()},
        "columns": columns,
        "indexes": set(db._PG_INDEXES),
        "grants": set(),
    }


def missing_macro_column():
    state = current_schema()
    state["columns"].pop(("runsession", "macro_json"))
    return state


def sql_error(code):
    cause = Exception("fixture PostgreSQL error")
    cause.sqlstate = code
    return OperationalError("fixture DDL", None, cause)


class Connection:
    def __init__(self, state, *, failure=None, on_lock=None):
        self.state = deepcopy(state)
        self.failure = failure
        self.on_lock = on_lock
        self.statements = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        if self.failure and self.failure[0] in statement:
            raise self.failure[1]
        if "pg_advisory_xact_lock" in statement:
            assert self.statements[-2] == "SET LOCAL lock_timeout = '2000ms'"
            if self.on_lock:
                self.on_lock(self)
            return []
        if "SELECT c.relname" in statement:
            return list(self.state["tables"].items())
        if "FROM information_schema.columns" in statement:
            return [(table, column, kind) for (table, column), kind in self.state["columns"].items()]
        if "FROM pg_indexes" in statement:
            return [(name,) for name in self.state["indexes"]]
        if "aclexplode" in statement:
            return list(self.state["grants"])
        return []

    @property
    def ddl(self):
        return [statement for statement in self.statements
                if statement.startswith(("ALTER ", "CREATE ", "REVOKE "))]


class Engine:
    def __init__(self, *connections):
        self.connections = connections
        self.attempts = 0

    def begin(self):
        connection = self.connections[self.attempts]
        self.attempts += 1
        return connection


def wire(monkeypatch, *connections):
    engine = Engine(*connections)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_is_sqlite", lambda: False)
    monkeypatch.setattr(db.SQLModel.metadata, "create_all", lambda *a, **k: pytest.fail("unexpected create_all"))
    waits = []
    monkeypatch.setattr(db.time, "sleep", waits.append)
    return engine, waits


def test_current_postgres_schema_boots_with_catalog_reads_only(monkeypatch):
    connection = Connection(current_schema())
    wire(monkeypatch, connection)
    db.init_db()
    assert connection.committed
    assert len(connection.statements) == 4
    assert all(statement.startswith("SELECT ") for statement in connection.statements)
    assert not any("advisory" in statement for statement in connection.statements)
    assert connection.ddl == []


def test_catalog_reader_accepts_real_sqlalchemy_cursor_results():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            class CatalogConnection:
                def exec_driver_sql(self, statement):
                    if "SELECT c.relname" in statement:
                        return connection.exec_driver_sql("SELECT 'newstitletranslation', 1")
                    if "information_schema.columns" in statement:
                        return connection.exec_driver_sql("SELECT 'newstitletranslation', 'claimed_ms', 'bigint'")
                    if "pg_indexes" in statement:
                        return connection.exec_driver_sql("SELECT 'ix_newstitletranslation_claimed_ms'")
                    assert "aclexplode" in statement
                    return connection.exec_driver_sql("SELECT 'newstitletranslation', 'PUBLIC'")

            assert db._pg_schema_state(CatalogConnection()) == {
                "tables": {"newstitletranslation": 1},
                "columns": {("newstitletranslation", "claimed_ms"): "bigint"},
                "indexes": {"ix_newstitletranslation_claimed_ms"},
                "grants": {("newstitletranslation", "PUBLIC")},
            }
    finally:
        engine.dispose()


def test_only_missing_column_takes_lock_and_runs_ddl(monkeypatch):
    connection = Connection(missing_macro_column())
    wire(monkeypatch, connection)
    db.init_db()
    assert connection.ddl == ["ALTER TABLE runsession ADD COLUMN IF NOT EXISTS macro_json TEXT DEFAULT ''"]
    lock = next(i for i, query in enumerate(connection.statements) if "pg_advisory_xact_lock" in query)
    assert lock < connection.statements.index(connection.ddl[0])
    assert connection.committed


def test_concurrent_boot_rechecks_catalog_after_obtaining_lock(monkeypatch):
    connection = Connection(missing_macro_column(), on_lock=lambda conn: setattr(conn, "state", current_schema()))
    wire(monkeypatch, connection)
    db.init_db()
    assert any("pg_advisory_xact_lock" in query for query in connection.statements)
    assert connection.ddl == []


@pytest.mark.parametrize("code, failing_sql", [
    ("40P01", "ALTER TABLE"),
    ("55P03", "pg_advisory_xact_lock"),
])
def test_lock_failure_rolls_back_before_bounded_retry(monkeypatch, code, failing_sql):
    first = Connection(missing_macro_column(), failure=(failing_sql, sql_error(code)))
    second = Connection(missing_macro_column())
    engine, waits = wire(monkeypatch, first, second)
    db.init_db()
    assert first.rolled_back and not first.committed
    assert second.committed and not second.rolled_back
    assert engine.attempts == 2
    assert waits == [0.1]
    assert len(second.ddl) == 1


def test_repeated_deadlock_stops_after_three_rolled_back_transactions(monkeypatch):
    connections = [Connection(missing_macro_column(), failure=("ALTER TABLE", sql_error("40P01")))
                   for _ in range(3)]
    engine, waits = wire(monkeypatch, *connections)
    with pytest.raises(OperationalError):
        db.init_db()
    assert engine.attempts == 3
    assert waits == [0.1, 0.2]
    assert all(conn.rolled_back for conn in connections)


def test_non_lock_error_is_not_retried_or_suppressed(monkeypatch):
    connection = Connection(missing_macro_column(), failure=("ALTER TABLE", sql_error("42501")))
    engine, waits = wire(monkeypatch, connection)
    with pytest.raises(OperationalError):
        db.init_db()
    assert engine.attempts == 1
    assert connection.rolled_back
    assert waits == []


def test_schema_creation_uses_same_locked_transaction_before_security(monkeypatch):
    empty = {"tables": {}, "columns": {}, "indexes": set(), "grants": set()}
    connection = Connection(empty)
    wire(monkeypatch, connection)
    created = []

    def create_all(bind):
        assert bind is connection
        assert any("pg_advisory_xact_lock" in query for query in bind.statements)
        assert not bind.committed
        created.append(bind)
        bind.state = current_schema()
        bind.state["tables"]["newstitletranslation"] = False
        bind.state["grants"] = {("newstitletranslation", role) for role in ("PUBLIC", "anon", "authenticated")}

    monkeypatch.setattr(db.SQLModel.metadata, "create_all", create_all)
    db.init_db()
    assert created == [connection]
    assert connection.ddl == [
        "ALTER TABLE newstitletranslation ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM anon",
        "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM authenticated",
    ]
    assert connection.committed


def test_only_missing_index_and_remaining_grant_are_changed(monkeypatch):
    state = current_schema()
    state["indexes"].remove("ix_runsession_active_heartbeat")
    state["grants"] = {("newstitletranslation", "authenticated")}
    connection = Connection(state)
    wire(monkeypatch, connection)
    db.init_db()
    assert connection.ddl == [
        "CREATE INDEX IF NOT EXISTS ix_runsession_active_heartbeat ON runsession (status, last_heartbeat_at)",
        "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM authenticated",
    ]


def test_existing_integer_timestamps_upgrade_without_readding_columns(monkeypatch):
    state = current_schema()
    state["columns"][("tickernewsstate", "latest_observed_ms")] = "integer"
    connection = Connection(state)
    wire(monkeypatch, connection)
    db.init_db()
    assert connection.ddl == [
        "ALTER TABLE tickernewsstate ALTER COLUMN latest_observed_ms TYPE BIGINT USING latest_observed_ms::bigint"
    ]


def test_community_summary_security_changes_only_its_own_permissions(monkeypatch):
    state = current_schema()
    state["tables"]["communitypostsummary"] = False
    state["grants"] = {("communitypostsummary", role) for role in ("PUBLIC", "anon", "authenticated")}
    connection = Connection(state)
    wire(monkeypatch, connection)
    db.init_db()
    assert connection.ddl == [
        "ALTER TABLE communitypostsummary ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE communitypostsummary FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE communitypostsummary FROM anon",
        "REVOKE ALL PRIVILEGES ON TABLE communitypostsummary FROM authenticated",
    ]


def test_new_community_cache_creation_and_security_are_one_locked_transaction(monkeypatch):
    state = current_schema()
    state["tables"].pop("communitypostsummary")
    connection = Connection(state)
    wire(monkeypatch, connection)

    def create_all(bind):
        assert any("pg_advisory_xact_lock" in statement for statement in bind.statements)
        bind.state["tables"]["communitypostsummary"] = False
        bind.state["grants"] = {("communitypostsummary", "anon")}

    monkeypatch.setattr(db.SQLModel.metadata, "create_all", create_all)
    db.init_db()
    assert connection.committed
    assert connection.ddl == [
        "ALTER TABLE communitypostsummary ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE communitypostsummary FROM anon",
    ]
