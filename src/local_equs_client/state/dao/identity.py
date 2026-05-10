"""Persists the stable client UUID (C2.2).

The C2.3 HTTP wrapper stamps every outbound request with ``X-Client-Id``.
Generated lazily on first call, persisted to ``app_state`` so subsequent
launches see the same id. Not exposed to UI — there is no reset path.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

_CLIENT_ID_KEY = "client_id"


def client_id(conn: sqlite3.Connection) -> str:
    """Return the stable client UUID, creating + persisting on first call."""
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (_CLIENT_ID_KEY,)
    ).fetchone()
    if row is not None:
        return str(row[0])

    new_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
        (_CLIENT_ID_KEY, new_id, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return new_id
