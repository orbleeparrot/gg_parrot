"""PostgreSQL schema contracts for the central position-news store."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import db as db_mod


_EXPECTED_BIGINT_COLUMNS = {
    db_mod.TickerNewsSnapshot.__table__: {
        "collected_ms",
        "claimed_ms",
        "last_observed_ms",
        "last_observation_seq",
        "next_retry_ms",
        "completed_ms",
    },
    db_mod.TickerNewsState.__table__: {
        "observation_seq",
        "latest_observation_seq",
        "latest_observed_ms",
        "last_attempt_ms",
        "last_success_ms",
        "collection_claimed_ms",
        "next_collection_ms",
    },
}


def test_fresh_postgres_ddl_uses_bigint_for_epoch_and_sequence_columns():
    dialect = postgresql.dialect()

    for table, expected_columns in _EXPECTED_BIGINT_COLUMNS.items():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        guarded_columns = {
            column.name
            for column in table.columns
            if column.name.endswith("_ms")
            or "observation_seq" in column.name
        }

        assert guarded_columns == expected_columns
        for column_name in expected_columns:
            assert re.search(
                rf"\b{re.escape(column_name)}\s+BIGINT\b",
                ddl,
            ), ddl


def test_public_browser_cache_schema_uses_bigint_without_user_columns():
    table = db_mod.BrowserNewsPageCache.__table__
    assert set(table.columns.keys()) == {"cache_key", "payload_json", "expires_ms", "updated_ms"}
    assert table.c.cache_key.primary_key
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert re.search(r"\bexpires_ms\s+BIGINT\b", ddl)
    assert re.search(r"\bupdated_ms\s+BIGINT\b", ddl)


def test_postgres_migration_upgrades_existing_integer_columns():
    state = {
        "tables": {table.name: False for table in _EXPECTED_BIGINT_COLUMNS},
        "columns": {(table.name, column): "integer"
                    for table, columns in _EXPECTED_BIGINT_COLUMNS.items() for column in columns},
        "indexes": set(), "grants": set(),
    }
    statements = set(db_mod._pg_migration_statements(state))
    for table, expected_columns in _EXPECTED_BIGINT_COLUMNS.items():
        for column_name in expected_columns:
            assert (
                f"ALTER TABLE {table.name} ALTER COLUMN {column_name} "
                f"TYPE BIGINT USING {column_name}::bigint"
            ) in statements
