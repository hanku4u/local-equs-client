"""Unit tests for ``local_equs_client.state.dao.identity`` (C2.2)."""

from __future__ import annotations

import uuid
from pathlib import Path

from local_equs_client.state import db
from local_equs_client.state.dao import identity


def _open(tmp_path: Path):
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    return conn


def test_first_call_creates_persistent_uuid(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    cid = identity.client_id(conn)
    uuid.UUID(cid)  # raises if not a valid UUID


def test_subsequent_calls_return_same_id(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    first = identity.client_id(conn)
    second = identity.client_id(conn)
    assert first == second


def test_id_persists_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    conn1 = db.connect(db_path)
    db.migrate(conn1)
    first = identity.client_id(conn1)
    conn1.close()

    conn2 = db.connect(db_path)
    second = identity.client_id(conn2)
    conn2.close()

    assert first == second
