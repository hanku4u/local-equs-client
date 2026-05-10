"""SQLite connection management and migration runner (C0.4).

Use :func:`connect` to open a configured connection (foreign keys on, ``Row``
factory). :func:`migrate` applies any pending ``NNN_*.sql`` files from the
``migrations/`` directory in numeric order and records each in
``schema_version``. Both functions are safe to call repeatedly.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from local_equs_client.config import paths

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_PATTERN = re.compile(r"^(\d+)_.+\.sql$")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with the project-wide pragmas applied."""
    target = db_path or paths.state_db()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in ascending numeric order. Returns the list applied."""
    _ensure_schema_version_table(conn)

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    pending = sorted(_discover_migrations(migrations_dir).items())

    newly_applied: list[int] = []
    for version, path in pending:
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
        newly_applied.append(version)
    return newly_applied


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT    NOT NULL
        )
        """
    )
    conn.commit()


def _discover_migrations(migrations_dir: Path) -> dict[int, Path]:
    found: dict[int, Path] = {}
    for path in migrations_dir.iterdir():
        match = _MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in found:
            raise RuntimeError(
                f"Duplicate migration version {version}: {found[version]} and {path}"
            )
        found[version] = path
    return found
