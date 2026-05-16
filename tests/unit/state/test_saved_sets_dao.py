"""Unit tests for ``local_equs_client.state.dao.saved_sets`` (C5.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_equs_client.state import db
from local_equs_client.state.dao import saved_sets as dao


@pytest.fixture()
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "state.db")
    db.migrate(c)
    return c


def test_list_empty(conn) -> None:
    assert dao.list_all(conn) == []


def test_create_and_list(conn) -> None:
    sid = dao.create(conn, "My Set", ("tool_a",), ("pressure",), ())
    sets = dao.list_all(conn)
    assert len(sets) == 1
    s = sets[0]
    assert s.set_id == sid
    assert s.name == "My Set"
    assert s.tools == ("tool_a",)
    assert s.sensors_canonical == ("pressure",)
    assert s.sensors_raw == ()


def test_create_multiple_sorted_by_name(conn) -> None:
    dao.create(conn, "Zebra", ("t1",), (), ())
    dao.create(conn, "Alpha", ("t2",), (), ())
    names = [s.name for s in dao.list_all(conn)]
    assert names == ["Alpha", "Zebra"]


def test_create_duplicate_name_raises(conn) -> None:
    dao.create(conn, "Dup", ("t1",), (), ())
    with pytest.raises(Exception):  # unique constraint
        dao.create(conn, "Dup", ("t2",), (), ())


def test_rename(conn) -> None:
    sid = dao.create(conn, "Old Name", ("t1",), ("s1",), ())
    dao.rename(conn, sid, "New Name")
    sets = dao.list_all(conn)
    assert sets[0].name == "New Name"


def test_rename_nonexistent_is_noop(conn) -> None:
    dao.rename(conn, 9999, "Whatever")  # no error
    assert dao.list_all(conn) == []


def test_delete(conn) -> None:
    sid = dao.create(conn, "To Delete", ("t1",), (), ())
    dao.delete(conn, sid)
    assert dao.list_all(conn) == []


def test_delete_nonexistent_is_noop(conn) -> None:
    dao.delete(conn, 9999)  # no error
    assert dao.list_all(conn) == []


def test_roundtrip_raw_sensors(conn) -> None:
    dao.create(conn, "Raw Set", ("tool_x",), (), ("raw_a", "raw_b"))
    s = dao.list_all(conn)[0]
    assert s.sensors_raw == ("raw_a", "raw_b")
    assert s.sensors_canonical == ()
