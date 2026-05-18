"""Background parquet downloads with checksum verification and Range-resume (C2.6, C5.14).

For each :class:`ManifestFile` the manager:

1. Streams ``GET <url>``, sending ``Range: bytes=N-`` if a ``.partial`` already
   exists from a prior interrupted attempt.
2. Appends bytes to ``<file>.partial``, hashing as it goes.
3. Verifies SHA-256 against the manifest entry on completion.
4. ``os.replace``s the partial onto the final path (atomic on the same fs).
5. Asks the LocalLibrary to re-index the file and stamps the verified sha.

Cancellation is checked between chunks; raising :class:`DownloadCancelled`
leaves the partial in place so the next attempt resumes. C2.7's updates panel
wraps each download in a C0.5 ``BackgroundJob`` so the UI thread stays alive.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_equs_client.data_layer import telemetry_client
from local_equs_client.data_layer.http import HttpClient
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.update_manager import ManifestFile

_CHUNK_SIZE = 64 * 1024

logger = logging.getLogger(__name__)


class DownloadFailed(Exception):
    """Base class for download failures."""


class DownloadCancelled(DownloadFailed):
    """Cancellation observed mid-download. ``.partial`` is preserved."""


class ChecksumMismatch(DownloadFailed):
    """Downloaded bytes' SHA-256 didn't match the manifest entry."""

    def __init__(self, file_id: str, expected: str, actual: str) -> None:
        super().__init__(f"{file_id}: expected {expected}, got {actual}")
        self.file_id = file_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class DownloadResult:
    file_id: str
    bytes_written: int
    sha256: str | None


class DownloadManager:
    """Per-file streaming downloader with Range resume + atomic move."""

    def __init__(self, http: HttpClient, library: LocalLibrary) -> None:
        self._http = http
        self._library = library

    def download_file(
        self,
        manifest_file: ManifestFile,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        target = self._library.data_dir / manifest_file.file_id
        partial = _partial_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        existing = partial.stat().st_size if partial.exists() else 0
        sha = hashlib.sha256()
        if existing > 0:
            with partial.open("rb") as f:
                for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
                    sha.update(chunk)

        telemetry_client.event(
            "download_started",
            file_id=manifest_file.file_id,
            resuming=existing > 0,
            existing_bytes=existing,
        )

        try:
            headers: dict[str, str] = {}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"

            resp = self._http.get(manifest_file.url, headers=headers, stream=True)

            if resp.status_code == 200 and existing > 0:
                # Server didn't honor Range; discard whatever we had and start over.
                logger.info(
                    "Range not honored for %s; restarting from byte 0",
                    manifest_file.file_id,
                )
                existing = 0
                sha = hashlib.sha256()
                partial.unlink(missing_ok=True)

            bytes_written = existing
            if cancelled is not None and cancelled():
                raise DownloadCancelled(manifest_file.file_id)
            with partial.open("ab") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled(manifest_file.file_id)
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha.update(chunk)
                    bytes_written += len(chunk)

            actual_sha = sha.hexdigest()
            if manifest_file.sha256:
                if actual_sha != manifest_file.sha256:
                    partial.unlink(missing_ok=True)
                    raise ChecksumMismatch(
                        manifest_file.file_id, manifest_file.sha256, actual_sha
                    )

            partial.replace(target)
            self._index_after_download(manifest_file, actual_sha)

        except DownloadCancelled:
            # User-initiated cancel — not a failure; no telemetry.
            raise
        except Exception as exc:
            telemetry_client.event(
                "download_failed",
                file_id=manifest_file.file_id,
                error_type=type(exc).__name__,
                partial_bytes=(partial.stat().st_size if partial.exists() else 0),
            )
            raise

        telemetry_client.event(
            "download_completed",
            file_id=manifest_file.file_id,
            bytes_written=bytes_written,
        )

        return DownloadResult(
            file_id=manifest_file.file_id,
            bytes_written=bytes_written,
            sha256=actual_sha,
        )

    # --- Index update ----------------------------------------------------

    def _index_after_download(self, manifest_file: ManifestFile, sha_hex: str) -> None:
        """Re-index the freshly-downloaded file and stamp the sha256 column."""
        indexed = self._library.index_file(manifest_file.file_id)
        if indexed is None:
            logger.warning("Indexed nothing after downloading %s", manifest_file.file_id)
            return
        self._library.set_sha256(manifest_file.file_id, sha_hex)


def _partial_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".partial")


__all__ = [
    "ChecksumMismatch",
    "DownloadCancelled",
    "DownloadFailed",
    "DownloadManager",
    "DownloadResult",
]
