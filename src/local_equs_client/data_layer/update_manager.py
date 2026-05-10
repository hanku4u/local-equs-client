"""Fetches /v1/manifest.json with ETag caching and computes update diffs (C2.4, C2.5).

C2.4 (this revision) lights up :meth:`UpdateManager.fetch_manifest`. C2.5 will add
``compute_updates()`` for the to-download / archived diff once the local file
index lands in M1's local library and the manifest format settles.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from local_equs_client.data_layer.http import HttpClient, NotFound
from local_equs_client.state.dao import manifest_cache

_MANIFEST_PATH = "/v1/manifest.json"

logger = logging.getLogger(__name__)


class UpdateManager:
    """Server-driven update manifest cache + (later) diff."""

    def __init__(self, http: HttpClient, conn: sqlite3.Connection) -> None:
        self._http = http
        self._conn = conn

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
                # Server says "not modified" but we have nothing cached — refetch.
                logger.warning("Server returned 304 with no cached body; refetching")
                resp = self._http.get(_MANIFEST_PATH)
            else:
                return cached_body

        body = resp.json()
        new_etag = resp.headers.get("ETag")
        manifest_cache.store(self._conn, body, new_etag)
        return body


__all__ = ["UpdateManager"]
