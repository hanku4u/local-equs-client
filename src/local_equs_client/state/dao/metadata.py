"""DAO for cached sensor catalog, canonical sensors, categories, and mappings.

(C2.9, C3.1)

Every cache uses the same shape: ``payload_json`` for the body, ``etag`` for
conditional GETs, and ``fetched_at`` for staleness audits. The DAO makes the
shape uniform across endpoints; :class:`MetadataCache` orchestrates which
endpoint feeds each cache.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- /v1/sensors/{tool_id}.json -- C2.9 -------------------------------------


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
        (tool_id, json.dumps(payload, sort_keys=True), etag, _now()),
    )
    conn.commit()


# --- /v1/process-groups/{id}/canonical-sensors.json -- C3.1 -----------------


def load_canonical_sensors(
    conn: sqlite3.Connection, prc_group_id: str
) -> tuple[Any | None, str | None]:
    row = conn.execute(
        "SELECT payload_json, etag FROM cached_canonical_sensors WHERE prc_group_id = ?",
        (prc_group_id,),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def store_canonical_sensors(
    conn: sqlite3.Connection, prc_group_id: str, payload: Any, etag: str | None
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cached_canonical_sensors
            (prc_group_id, payload_json, etag, fetched_at)
        VALUES (?, ?, ?, ?)
        """,
        (prc_group_id, json.dumps(payload, sort_keys=True), etag, _now()),
    )
    conn.commit()


# --- /v1/process-groups/{id}/mappings.json -- C3.1 --------------------------


def load_mappings(
    conn: sqlite3.Connection, prc_group_id: str
) -> tuple[Any | None, str | None]:
    row = conn.execute(
        "SELECT payload_json, etag FROM cached_mappings WHERE prc_group_id = ?",
        (prc_group_id,),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def store_mappings(
    conn: sqlite3.Connection, prc_group_id: str, payload: Any, etag: str | None
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cached_mappings
            (prc_group_id, payload_json, etag, fetched_at)
        VALUES (?, ?, ?, ?)
        """,
        (prc_group_id, json.dumps(payload, sort_keys=True), etag, _now()),
    )
    conn.commit()


def all_mapping_payloads(conn: sqlite3.Connection) -> list[Any]:
    """Return every cached mapping payload (used to build the cross-prc-group index)."""
    rows = conn.execute("SELECT payload_json FROM cached_mappings").fetchall()
    return [json.loads(row[0]) for row in rows]


# --- /v1/categories.json -- C3.1 -------------------------------------------


def load_categories(
    conn: sqlite3.Connection,
) -> tuple[Any | None, str | None]:
    row = conn.execute(
        "SELECT payload_json, etag FROM cached_categories WHERE id = 1"
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def store_categories(
    conn: sqlite3.Connection, payload: Any, etag: str | None
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cached_categories (id, payload_json, etag, fetched_at)
        VALUES (1, ?, ?, ?)
        """,
        (json.dumps(payload, sort_keys=True), etag, _now()),
    )
    conn.commit()
