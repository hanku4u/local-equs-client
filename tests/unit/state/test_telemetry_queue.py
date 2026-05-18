"""Unit tests for ``local_equs_client.state.dao.telemetry_queue`` (C5.11)."""

from __future__ import annotations

from pathlib import Path

from local_equs_client.state import db
from local_equs_client.state.dao import telemetry_queue


def _open(tmp_path: Path):
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    return conn


def test_enqueue_returns_increasing_ids(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    a = telemetry_queue.enqueue(conn, type="app.start", data={})
    b = telemetry_queue.enqueue(conn, type="app.start", data={})
    assert b > a


def test_peek_batch_returns_oldest_first(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    telemetry_queue.enqueue(conn, type="a", data={"i": 1})
    telemetry_queue.enqueue(conn, type="b", data={"i": 2})
    telemetry_queue.enqueue(conn, type="c", data={"i": 3})

    batch = telemetry_queue.peek_batch(conn, limit=10)
    assert [e.type for e in batch] == ["a", "b", "c"]
    assert batch[0].data == {"i": 1}


def test_peek_batch_honors_limit(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    for i in range(5):
        telemetry_queue.enqueue(conn, type="t", data={"i": i})
    batch = telemetry_queue.peek_batch(conn, limit=2)
    assert len(batch) == 2


def test_peek_batch_does_not_remove(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    telemetry_queue.enqueue(conn, type="t", data={})
    telemetry_queue.peek_batch(conn, limit=10)
    assert telemetry_queue.count(conn) == 1


def test_delete_batch_removes_only_listed_ids(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    a = telemetry_queue.enqueue(conn, type="a", data={})
    b = telemetry_queue.enqueue(conn, type="b", data={})
    c = telemetry_queue.enqueue(conn, type="c", data={})

    telemetry_queue.delete_batch(conn, [a, b])
    remaining = telemetry_queue.peek_batch(conn, limit=10)
    assert [e.id for e in remaining] == [c]


def test_delete_batch_empty_is_noop(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    telemetry_queue.enqueue(conn, type="t", data={})
    telemetry_queue.delete_batch(conn, [])
    assert telemetry_queue.count(conn) == 1


def test_count_reflects_enqueue_and_delete(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    assert telemetry_queue.count(conn) == 0
    ids = [
        telemetry_queue.enqueue(conn, type="t", data={"i": i}) for i in range(3)
    ]
    assert telemetry_queue.count(conn) == 3
    telemetry_queue.delete_batch(conn, ids)
    assert telemetry_queue.count(conn) == 0


def test_event_payload_round_trips_nested_dict(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    payload = {"nested": {"k": 1}, "list": [1, 2, 3], "s": "hi"}
    telemetry_queue.enqueue(conn, type="t", data=payload)
    batch = telemetry_queue.peek_batch(conn, limit=1)
    assert batch[0].data == payload
