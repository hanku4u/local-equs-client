"""Unit tests for ``local_equs_client.state.db`` (C0.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_equs_client.state import db

EXPECTED_TABLES = {
    "schema_version",
    "local_files",
    "saved_views",
    "saved_sets",
    "cached_sensors",
    "cached_mappings",
}


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "state.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_creates_all_tables(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "state.db")
    applied = db.migrate(conn)

    assert applied == [1]
    assert EXPECTED_TABLES.issubset(_table_names(conn))


def test_migrate_records_schema_version(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    assert versions == [1]


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "state.db")
    first = db.migrate(conn)
    second = db.migrate(conn)
    assert first == [1]
    assert second == []
    assert EXPECTED_TABLES.issubset(_table_names(conn))


def test_migrate_applies_in_order(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "002_second.sql").write_text("CREATE TABLE second (id INTEGER);")
    (migrations / "001_first.sql").write_text("CREATE TABLE first (id INTEGER);")

    conn = db.connect(tmp_path / "state.db")
    applied = db.migrate(conn, migrations_dir=migrations)
    assert applied == [1, 2]


def test_migrate_rejects_duplicate_versions(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_a.sql").write_text("SELECT 1;")
    (migrations / "001_b.sql").write_text("SELECT 1;")

    conn = db.connect(tmp_path / "state.db")
    with pytest.raises(RuntimeError, match="Duplicate migration"):
        db.migrate(conn, migrations_dir=migrations)
