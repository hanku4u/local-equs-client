"""Stores the cached manifest body and ETag (C2.4).

Single-row table — there's only ever one current manifest.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def load(conn: sqlite3.Connection) -> tuple[Any | None, str | None]:
    """Return ``(body, etag)`` or ``(None, None)`` if nothing's cached yet."""
    row = conn.execute(
        "SELECT body_json, etag FROM cached_manifest WHERE id = 1"
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def store(conn: sqlite3.Connection, body: Any, etag: str | None) -> None:
    """Replace the cached manifest with ``body`` and record the ETag + timestamp."""
    conn.execute(
        """
        INSERT OR REPLACE INTO cached_manifest (id, body_json, etag, fetched_at)
        VALUES (1, ?, ?, ?)
        """,
        (
            json.dumps(body, sort_keys=True),
            etag,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
