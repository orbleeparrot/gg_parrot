"""Schema safety contracts for the shared public large-fill collector."""
from sqlalchemy import BigInteger
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import db


def test_whale_state_has_only_shared_pair_identity_and_bigint_clocks():
    table = db.WhaleTradeState.__table__
    assert table.c.state_key.primary_key
    assert not {"user_id", "session_id", "testnet"}.intersection(table.c.keys())
    clocks = {column.name for column in table.columns if column.name.endswith("_ms")}
    assert clocks == {"last_success_ms", "last_attempt_ms", "next_collection_ms", "claimed_ms"}
    assert all(isinstance(table.c[name].type, BigInteger) for name in clocks)
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "BIGINT" in ddl


def test_whale_table_privileges_and_noop_schema_migration():
    table = db.WhaleTradeState.__table__
    state = {"tables": {table.name: False},
             "columns": {(table.name, col.name): "bigint" if col.name.endswith("_ms") else "text" for col in table.columns},
             "indexes": set(db._PG_INDEXES),
             "grants": {(table.name, role) for role in ("PUBLIC", "anon", "authenticated")}}
    assert table.name in db._PG_PRIVATE_CACHE_TABLES
    assert db._pg_migration_statements(state) == [
        "ALTER TABLE whaletradestate ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE whaletradestate FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE whaletradestate FROM anon",
        "REVOKE ALL PRIVILEGES ON TABLE whaletradestate FROM authenticated",
    ]
    state["tables"][table.name] = True
    state["grants"] = set()
    assert db._pg_migration_statements(state) == []


def test_catalog_grant_query_includes_whale_table():
    statements = []
    class Connection:
        def exec_driver_sql(self, query):
            statements.append(query)
            return []
    db._pg_schema_state(Connection())
    query = next(query for query in statements if "aclexplode" in query)
    assert "'whaletradestate'" in query
