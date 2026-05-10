"""Scans the local data directory and indexes parquet files in SQLite (C0.6, C1.2).

Convention: any ``*.parquet`` under ``paths.data_dir()`` is indexed. The tool
id and hour bucket are derived from the file's path:

- ``data_dir/<tool_id>.parquet`` (single file per tool — spike layout)
  → ``tool_id`` from filename stem; ``hour_bucket = None``.
- ``data_dir/<tool_id>/<hour_bucket>.parquet`` (production layout)
  → ``tool_id`` from parent dir; ``hour_bucket`` from filename stem.

Timestamp extents come from the per-row-group statistics on the ``ts`` column
so the scan stays O(metadata) — no full reads.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from local_equs_client.selection.types import TimeRange

TIMESTAMP_COLUMN = "ts"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LocalFile:
    """One row from the ``local_files`` table joined with on-disk size."""

    file_id: str
    tool_id: str
    path: Path
    hour_bucket: str | None
    min_ts: datetime
    max_ts: datetime
    row_count: int
    sha256: str | None
    pinned: bool
    archived: bool
    size_bytes: int


class LocalLibrary:
    """Tracks the parquet files present under ``paths.data_dir()``.

    Not thread-safe by itself: each thread should construct its own
    ``LocalLibrary`` (with its own ``sqlite3.Connection``) or callers must
    serialize access externally.
    """

    def __init__(self, data_dir: Path, conn: sqlite3.Connection) -> None:
        self._data_dir = data_dir
        self._conn = conn

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def scan(self) -> int:
        """Walk ``data_dir`` and sync ``local_files``. Returns indexed file count."""
        from local_equs_client.state.dao import local_files as dao

        seen: set[str] = set()
        for parquet_path in self._iter_parquet():
            try:
                file = self._read_file_metadata(parquet_path)
            except (OSError, ValueError) as exc:
                logger.warning("Failed to index %s: %s", parquet_path, exc)
                continue
            dao.upsert(self._conn, file)
            seen.add(file.file_id)

        for vanished in dao.all_file_ids(self._conn) - seen:
            dao.delete(self._conn, vanished)

        self._conn.commit()
        return len(seen)

    def files_for(self, tool_id: str, time_range: TimeRange) -> list[LocalFile]:
        from local_equs_client.state.dao import local_files as dao

        return dao.files_for(self._conn, self._data_dir, tool_id, time_range)

    def pin(self, file_id: str) -> None:
        from local_equs_client.state.dao import local_files as dao

        dao.set_pinned(self._conn, file_id, True)
        self._conn.commit()

    def unpin(self, file_id: str) -> None:
        from local_equs_client.state.dao import local_files as dao

        dao.set_pinned(self._conn, file_id, False)
        self._conn.commit()

    def total_size_bytes(self) -> int:
        from local_equs_client.state.dao import local_files as dao

        return dao.total_size_bytes(self._conn)

    def archived_files(self) -> list[LocalFile]:
        from local_equs_client.state.dao import local_files as dao

        return dao.archived_files(self._conn, self._data_dir)

    def all_files(self) -> list[LocalFile]:
        from local_equs_client.state.dao import local_files as dao

        return dao.all_files(self._conn, self._data_dir)

    def delete(self, file_id: str) -> None:
        """Remove the on-disk file (if present) and drop the index row."""
        from local_equs_client.state.dao import local_files as dao

        path = self._data_dir / file_id
        if path.exists():
            path.unlink()
        dao.delete(self._conn, file_id)
        self._conn.commit()

    def index_file(self, file_id: str) -> LocalFile | None:
        """Index one file by its relative path. Returns the new row or None on failure."""
        from local_equs_client.state.dao import local_files as dao

        path = self._data_dir / file_id
        if not path.exists():
            return None
        try:
            local_file = self._read_file_metadata(path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to index %s: %s", path, exc)
            return None
        dao.upsert(self._conn, local_file)
        self._conn.commit()
        return local_file

    def set_sha256(self, file_id: str, sha256: str | None) -> None:
        """Update the stored SHA-256 for a file (downloaders stamp it post-verify)."""
        self._conn.execute(
            "UPDATE local_files SET sha256 = ? WHERE file_id = ?",
            (sha256, file_id),
        )
        self._conn.commit()

    def _iter_parquet(self) -> Iterator[Path]:
        if not self._data_dir.is_dir():
            return
        yield from sorted(self._data_dir.rglob("*.parquet"))

    def _read_file_metadata(self, path: Path) -> LocalFile:
        rel = path.relative_to(self._data_dir).as_posix()
        tool_id, hour_bucket = self._parse_layout(path)

        meta = pq.read_metadata(str(path))  # type: ignore[no-untyped-call]
        ts_min, ts_max = self._timestamp_extents(meta, path)

        return LocalFile(
            file_id=rel,
            tool_id=tool_id,
            path=path,
            hour_bucket=hour_bucket,
            min_ts=ts_min,
            max_ts=ts_max,
            row_count=meta.num_rows,
            sha256=None,
            pinned=False,
            archived=False,
            size_bytes=path.stat().st_size,
        )

    def _parse_layout(self, path: Path) -> tuple[str, str | None]:
        rel_parts = path.relative_to(self._data_dir).parts
        if len(rel_parts) >= 2:
            return rel_parts[0], path.stem
        return path.stem, None

    def _timestamp_extents(
        self, meta: pq.FileMetaData, path: Path
    ) -> tuple[datetime, datetime]:
        col_idx = self._find_ts_column(meta, path)
        mins: list[datetime] = []
        maxs: list[datetime] = []
        for rg_idx in range(meta.num_row_groups):
            stats = meta.row_group(rg_idx).column(col_idx).statistics
            if stats is None or not stats.has_min_max:
                raise ValueError(f"{path}: row group {rg_idx} missing ts statistics")
            mins.append(_to_utc(stats.min))
            maxs.append(_to_utc(stats.max))
        if not mins:
            raise ValueError(f"{path}: parquet has no row groups")
        return min(mins), max(maxs)

    def _find_ts_column(self, meta: pq.FileMetaData, path: Path) -> int:
        names = list(meta.schema.names)
        if TIMESTAMP_COLUMN not in names:
            raise ValueError(f"{path}: no '{TIMESTAMP_COLUMN}' column (found {names!r})")
        return names.index(TIMESTAMP_COLUMN)


def _to_utc(value: datetime | int) -> datetime:
    """Coerce a pyarrow timestamp statistic into a UTC ``datetime``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)
    raise TypeError(f"Unexpected timestamp statistic type: {type(value).__name__}")
