"""Scans the local data directory and indexes parquet files in SQLite (C0.6, C1.2).

C0.6 freezes the public contract; C1.2 implements the parquet scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from local_equs_client.selection.types import TimeRange


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
    """Tracks the parquet files present under ``paths.data_dir()``."""

    def files_for(self, tool_id: str, time_range: TimeRange) -> list[LocalFile]:
        """Return the indexed files for ``tool_id`` overlapping ``time_range``."""
        raise NotImplementedError

    def pin(self, file_id: str) -> None:
        """Mark a file as pinned, exempting it from future auto-eviction."""
        raise NotImplementedError

    def unpin(self, file_id: str) -> None:
        """Remove the pinned mark from a file."""
        raise NotImplementedError

    def total_size_bytes(self) -> int:
        """Sum of on-disk sizes across all indexed files."""
        raise NotImplementedError

    def archived_files(self) -> list[LocalFile]:
        """Files present locally but no longer in the server manifest."""
        raise NotImplementedError
