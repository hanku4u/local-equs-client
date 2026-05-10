"""DAO for the ``local_files`` table (C1.2).

All SQL touching ``local_files`` lives here. Callers pass an open
``sqlite3.Connection`` so the DAO doesn't own connection lifetime.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_equs_client.data_layer.local_library import LocalFile
from local_equs_client.selection.types import TimeRange

_COLUMNS = (
    "file_id",
    "tool_id",
    "hour_bucket",
    "min_ts",
    "max_ts",
    "row_count",
    "sha256",
    "pinned",
    "archived",
    "size_bytes",
)

_SELECT_ALL_COLUMNS = ", ".join(_COLUMNS)


def upsert(conn: sqlite3.Connection, file: LocalFile) -> None:
    conn.execute(
        f"""
        INSERT INTO local_files ({", ".join(_COLUMNS)})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            tool_id     = excluded.tool_id,
            hour_bucket = excluded.hour_bucket,
            min_ts      = excluded.min_ts,
            max_ts      = excluded.max_ts,
            row_count   = excluded.row_count,
            sha256      = excluded.sha256,
            size_bytes  = excluded.size_bytes
        """,
        (
            file.file_id,
            file.tool_id,
            file.hour_bucket,
            file.min_ts.timestamp(),
            file.max_ts.timestamp(),
            file.row_count,
            file.sha256,
            int(file.pinned),
            int(file.archived),
            file.size_bytes,
        ),
    )


def files_for(
    conn: sqlite3.Connection,
    data_dir: Path,
    tool_id: str,
    time_range: TimeRange,
) -> list[LocalFile]:
    rows = conn.execute(
        f"""
        SELECT {_SELECT_ALL_COLUMNS} FROM local_files
        WHERE tool_id = ?
          AND archived = 0
          AND min_ts <= ?
          AND max_ts >= ?
        ORDER BY min_ts
        """,
        (tool_id, time_range.end.timestamp(), time_range.start.timestamp()),
    ).fetchall()
    return [_row_to_file(row, data_dir) for row in rows]


def all_file_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT file_id FROM local_files")}


def all_files(conn: sqlite3.Connection, data_dir: Path) -> list[LocalFile]:
    rows = conn.execute(
        f"SELECT {_SELECT_ALL_COLUMNS} FROM local_files ORDER BY tool_id, min_ts"
    ).fetchall()
    return [_row_to_file(row, data_dir) for row in rows]


def archived_files(conn: sqlite3.Connection, data_dir: Path) -> list[LocalFile]:
    rows = conn.execute(
        f"""
        SELECT {_SELECT_ALL_COLUMNS} FROM local_files
        WHERE archived = 1
        ORDER BY tool_id, min_ts
        """
    ).fetchall()
    return [_row_to_file(row, data_dir) for row in rows]


def delete(conn: sqlite3.Connection, file_id: str) -> None:
    conn.execute("DELETE FROM local_files WHERE file_id = ?", (file_id,))


def set_pinned(conn: sqlite3.Connection, file_id: str, pinned: bool) -> None:
    conn.execute(
        "UPDATE local_files SET pinned = ? WHERE file_id = ?",
        (1 if pinned else 0, file_id),
    )


def total_size_bytes(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM local_files").fetchone()
    return int(row[0])


def _row_to_file(row: sqlite3.Row | tuple[Any, ...], data_dir: Path) -> LocalFile:
    (
        file_id,
        tool_id,
        hour_bucket,
        min_ts,
        max_ts,
        row_count,
        sha256,
        pinned,
        archived,
        size_bytes,
    ) = row
    return LocalFile(
        file_id=file_id,
        tool_id=tool_id,
        path=data_dir / file_id,
        hour_bucket=hour_bucket,
        min_ts=datetime.fromtimestamp(min_ts, tz=UTC),
        max_ts=datetime.fromtimestamp(max_ts, tz=UTC),
        row_count=row_count,
        sha256=sha256,
        pinned=bool(pinned),
        archived=bool(archived),
        size_bytes=size_bytes,
    )
