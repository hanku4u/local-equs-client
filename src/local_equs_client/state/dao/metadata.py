"""DAO for cached sensor catalog and mappings (C2.9, C3.1).

C2.9 (this revision) reads + writes the cached sensor list per tool. C3.1 will
extend this with canonical sensors, categories, and mappings; both use the
same ETag-aware pattern.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def load_sensors(
    conn: sqlite3.Connection, tool_id: str
) -> tuple[Any | None, str | None]:
    """Return ``(payload, etag)`` cached for ``tool_id``, or ``(None, None)``."""
    row = conn.execute(
        "SELECT payload_json, etag FROM cached_sensors WHERE tool_id = ?",
        (tool_id,),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def store_sensors(
    conn: sqlite3.Connection, tool_id: str, payload: Any, etag: str | None
) -> None:
    """Replace the cached sensor payload for ``tool_id``."""
    conn.execute(
        """
        INSERT OR REPLACE INTO cached_sensors (tool_id, payload_json, etag, fetched_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            tool_id,
            json.dumps(payload, sort_keys=True),
            etag,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
