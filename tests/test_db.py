"""Schema creation, idempotent migrations, active pragmas."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from gateway.shared.db import create_db_engine, run_migrations
from gateway.shared.models import Base

EXPECTED_TABLES = {
    "api_tokens",
    "messages",
    "webhook_deliveries",
    "gateway_status",
    "gateway_config",
}


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_db_engine(tmp_path / "test.db")
    yield eng
    eng.dispose()


def test_migrations_create_schema_on_empty_db(engine: Engine) -> None:
    applied = run_migrations(engine)

    assert applied, "expected at least one migration to be applied"
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_migrations_are_idempotent_on_second_run(engine: Engine) -> None:
    first = run_migrations(engine)
    second = run_migrations(engine)

    assert first
    assert second == []
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar_one()
    assert count == len(first)


def test_pragmas_active_on_every_connect(engine: Engine) -> None:
    run_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    run_migrations(engine)

    with engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO webhook_deliveries (id, message_id, attempt, status, created_at) "
                "VALUES ('whd_x', 'msg_does_not_exist', 0, 'pending', '2026-07-24T00:00:00Z')"
            )
        )


def test_models_match_migrated_schema(engine: Engine) -> None:
    run_migrations(engine)

    inspector = inspect(engine)
    for table in Base.metadata.tables.values():
        db_cols = {col["name"] for col in inspector.get_columns(table.name)}
        model_cols = {col.name for col in table.columns}
        assert model_cols == db_cols, f"{table.name}: model columns {model_cols} != db columns {db_cols}"
