"""Fetches /v1/manifest.json with ETag caching and computes update diffs (C2.4, C2.5).

C2.4 added :meth:`UpdateManager.fetch_manifest`. C2.5 (this revision) adds
:meth:`UpdateManager.compute_updates`, which compares the manifest against the
local index and returns lists of files to download or files no longer in the
manifest (archived locally).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from local_equs_client.data_layer.http import HttpClient, NotFound
from local_equs_client.data_layer.local_library import LocalFile, LocalLibrary
from local_equs_client.state.dao import manifest_cache

_MANIFEST_PATH = "/v1/manifest.json"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """One file as described by ``/v1/manifest.json``."""

    file_id: str
    tool_id: str
    url: str
    sha256: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class UpdateDiff:
    """Output of :meth:`UpdateManager.compute_updates`."""

    to_download: list[ManifestFile]
    archived_locally: list[LocalFile]


class UpdateManager:
    """Server-driven update manifest cache + diff against the local library."""

    def __init__(
        self,
        http: HttpClient,
        conn: sqlite3.Connection,
        library: LocalLibrary | None = None,
    ) -> None:
        self._http = http
        self._conn = conn
        self._library = library

    def fetch_manifest(self) -> Any:
        """ETag-aware manifest fetch.

        First call: ``GET /v1/manifest.json`` and cache body + ETag.
        Subsequent calls: send ``If-None-Match`` with the cached ETag.
        On ``304`` returns the cached body; on ``200`` overwrites the cache.
        """
        cached_body, cached_etag = manifest_cache.load(self._conn)
        headers = {"If-None-Match": cached_etag} if cached_etag else None

        try:
            resp = self._http.get(_MANIFEST_PATH, headers=headers)
        except NotFound:
            raise

        if resp.status_code == 304:
            if cached_body is None:
                logger.warning("Server returned 304 with no cached body; refetching")
                resp = self._http.get(_MANIFEST_PATH)
            else:
                return cached_body

        body = resp.json()
        new_etag = resp.headers.get("ETag")
        manifest_cache.store(self._conn, body, new_etag)
        return body

    def compute_updates(self, manifest: Any | None = None) -> UpdateDiff:
        """Compare the manifest against ``LocalLibrary``.

        Pass ``manifest=None`` to fetch first; pass a parsed manifest to diff
        against an already-fetched copy.
        """
        if self._library is None:
            raise RuntimeError("UpdateManager.compute_updates needs a LocalLibrary")

        if manifest is None:
            manifest = self.fetch_manifest()

        manifest_files = parse_manifest(manifest)
        local_files = self._library.all_files()

        local_by_id = {f.file_id: f for f in local_files}
        manifest_ids = {mf.file_id for mf in manifest_files}

        to_download: list[ManifestFile] = []
        for mf in manifest_files:
            local = local_by_id.get(mf.file_id)
            if local is None:
                to_download.append(mf)
                continue
            if mf.sha256 and local.sha256 and mf.sha256 != local.sha256:
                to_download.append(mf)
                continue
            if mf.sha256 and not local.sha256:
                # Local file has no checksum on record (M1 scan didn't compute one);
                # treat the manifest as the source of truth and re-download.
                to_download.append(mf)

        archived = [lf for lf in local_files if lf.file_id not in manifest_ids]

        return UpdateDiff(to_download=to_download, archived_locally=archived)


def parse_manifest(manifest: Any) -> list[ManifestFile]:
    """Translate a parsed ``/v1/manifest.json`` body into typed file entries."""
    if not isinstance(manifest, dict):
        return []
    files = manifest.get("files")
    if not isinstance(files, list):
        return []

    out: list[ManifestFile] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        file_id = entry.get("file_id") or entry.get("path")
        tool_id = entry.get("tool_id")
        if not isinstance(file_id, str) or not isinstance(tool_id, str):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            url = f"/v1/data/{file_id.lstrip('/')}"
        sha = entry.get("sha256")
        sha_str = sha if isinstance(sha, str) and sha else None
        size = entry.get("size_bytes")
        size_int = int(size) if isinstance(size, int | float) else 0
        out.append(
            ManifestFile(
                file_id=file_id,
                tool_id=tool_id,
                url=url,
                sha256=sha_str,
                size_bytes=size_int,
            )
        )
    return out


__all__ = ["ManifestFile", "UpdateDiff", "UpdateManager", "parse_manifest"]
