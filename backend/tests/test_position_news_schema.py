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


class _Result:
    def first(self):
        return ("integer",)


class _Connection:
    def __init__(self):
        self.statements = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        return _Result()

    def commit(self):
        self.committed = True


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_postgres_migration_upgrades_existing_integer_columns(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(db_mod, "_engine", _Engine(connection))

    db_mod._migrate_pg()

    statements = set(connection.statements)
    for table, expected_columns in _EXPECTED_BIGINT_COLUMNS.items():
        for column_name in expected_columns:
            assert (
                f"ALTER TABLE {table.name} ALTER COLUMN {column_name} "
                f"TYPE BIGINT USING {column_name}::bigint"
            ) in statements
    assert connection.committed is True
