"""SQLite engine, pragmas and migrations.

Pragmas (WAL, busy_timeout, foreign_keys) are set on every pool connect so both
containers get identical behavior. Migrations are numbered SQL scripts in
gateway/shared/migrations/, applied exactly once and recorded in schema_version.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def create_db_engine(database_path: str | Path) -> Engine:
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def run_migrations(engine: Engine, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in filename order; return newly applied versions."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, filename TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
        )
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_version"))}

    newly_applied: list[int] = []
    for script in sorted(migrations_dir.glob("[0-9]*.sql")):
        version = int(script.name.split("_", 1)[0])
        if version in applied:
            continue
        sql = script.read_text(encoding="utf-8")
        raw = engine.raw_connection()
        try:
            # executescript runs multi-statement DDL; sqlite has no transactional DDL anyway
            raw.driver_connection.executescript(sql)  # type: ignore[union-attr]
            raw.driver_connection.execute(  # type: ignore[union-attr]
                "INSERT INTO schema_version (version, filename, applied_at) VALUES (?, ?, ?)",
                (version, script.name, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
            raw.driver_connection.commit()  # type: ignore[union-attr]
        finally:
            raw.close()
        newly_applied.append(version)
    return newly_applied
