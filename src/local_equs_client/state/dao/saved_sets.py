"""DAO for the ``saved_sets`` table (C5.1).

A saved set stores a named snapshot of tools + canonical/raw sensor names.
Time range is intentionally *not* stored — sets are about *what* to look at,
not *when*.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class SavedSet:
    set_id: int
    name: str
    tools: tuple[str, ...]
    sensors_canonical: tuple[str, ...]
    sensors_raw: tuple[str, ...]


def list_all(conn: sqlite3.Connection) -> list[SavedSet]:
    rows = conn.execute(
        "SELECT set_id, name, payload_json FROM saved_sets ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_saved_set(row) for row in rows]


def create(
    conn: sqlite3.Connection,
    name: str,
    tools: tuple[str, ...],
    sensors_canonical: tuple[str, ...],
    sensors_raw: tuple[str, ...],
) -> int:
    payload = json.dumps(
        {
            "tools": list(tools),
            "sensors_canonical": list(sensors_canonical),
            "sensors_raw": list(sensors_raw),
        }
    )
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO saved_sets (name, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, payload, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)  # type: ignore[arg-type]


def rename(conn: sqlite3.Connection, set_id: int, new_name: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE saved_sets SET name = ?, updated_at = ? WHERE set_id = ?",
        (new_name, now, set_id),
    )
    conn.commit()


def delete(conn: sqlite3.Connection, set_id: int) -> None:
    conn.execute("DELETE FROM saved_sets WHERE set_id = ?", (set_id,))
    conn.commit()


def _row_to_saved_set(row: sqlite3.Row) -> SavedSet:
    data = json.loads(row["payload_json"])
    return SavedSet(
        set_id=int(row["set_id"]),
        name=str(row["name"]),
        tools=tuple(data.get("tools", [])),
        sensors_canonical=tuple(data.get("sensors_canonical", [])),
        sensors_raw=tuple(data.get("sensors_raw", [])),
    )
