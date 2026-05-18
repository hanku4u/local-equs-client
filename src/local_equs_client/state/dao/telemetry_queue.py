"""Local queue for telemetry events pending upload (C5.11).

Events are enqueued synchronously by any thread that holds the SQLite
connection. :func:`peek_batch` reads up to ``limit`` of the oldest
events without removing them, so a failed POST can be retried later;
:func:`delete_batch` clears them only on successful flush.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class QueuedEvent:
    id: int
    type: str
    data: dict[str, Any]
    created_at: str


def enqueue(conn: sqlite3.Connection, *, type: str, data: dict[str, Any]) -> int:
    """Persist one event and return its row id."""
    payload = json.dumps(data, default=str)
    cursor = conn.execute(
        "INSERT INTO telemetry_queue (type, payload_json, created_at) VALUES (?, ?, ?)",
        (type, payload, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    if cursor.lastrowid is None:
        raise RuntimeError("enqueue: lastrowid was None")
    return cursor.lastrowid


def peek_batch(conn: sqlite3.Connection, *, limit: int) -> list[QueuedEvent]:
    """Return the ``limit`` oldest events without removing them."""
    rows = conn.execute(
        "SELECT id, type, payload_json, created_at FROM telemetry_queue "
        "ORDER BY id ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [
        QueuedEvent(
            id=row[0],
            type=row[1],
            data=json.loads(row[2]),
            created_at=row[3],
        )
        for row in rows
    ]


def delete_batch(conn: sqlite3.Connection, ids: Sequence[int]) -> None:
    """Remove events whose ids are in ``ids``."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM telemetry_queue WHERE id IN ({placeholders})",
        tuple(int(i) for i in ids),
    )
    conn.commit()


def count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM telemetry_queue").fetchone()
    return int(row[0]) if row else 0


__all__ = ["QueuedEvent", "count", "delete_batch", "enqueue", "peek_batch"]
