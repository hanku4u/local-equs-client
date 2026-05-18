"""Self-updater: poll, download, verify the next signed installer (C6.4).

Periodically checks ``/v1/app-version`` against the running app version.
If the server reports a newer version, downloads the signed installer to
``%LOCALAPPDATA%\\LocalEQUS\\updates\\`` and verifies the SHA-256 from the
manifest entry. The actual hand-off (running the installer and exiting
the app) lands in #74; this module exposes the data-layer primitives.

Expected server response shape::

    GET /v1/app-version  ->  200
    {
        "version": "1.2.3",
        "url": "https://.../LocalEQUS-Setup-1.2.3.exe",
        "sha256": "0123...",
        "release_notes": "optional human-readable string"
    }

Polling cadence is driven by ``Settings.update_check_frequency_hours``
(0 = disabled). Wiring lives in ``main.py``; this module is reachable
from tests in isolation.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_equs_client.config import paths
from local_equs_client.data_layer.http import (
    HttpClient,
    HttpError,
    app_version,
)

logger = logging.getLogger(__name__)

_APP_VERSION_PATH = "/v1/app-version"
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class AvailableVersion:
    """A newer-than-current version reported by ``/v1/app-version``."""

    version: str
    url: str
    sha256: str
    release_notes: str | None = None


def updates_dir() -> Path:
    """Directory where downloaded installers land."""
    return paths.app_dir() / "updates"


def _parse_version(value: str) -> tuple[int, ...] | None:
    """Return a tuple-of-ints for a clean semver, or ``None`` for non-numeric.

    Pre-release suffixes (``-rc1``, ``.dev0``) deliberately don't parse —
    they compare as "not newer" downstream, which is the conservative
    default for an updater that shouldn't push pre-release builds.
    """
    parts = value.split(".")
    out: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out)


def is_newer(server_version: str, current_version: str) -> bool:
    """True when ``server_version`` is strictly greater than ``current_version``.

    Returns ``False`` on any parse failure — better to under-prompt than
    to push a malformed version.
    """
    a = _parse_version(server_version)
    b = _parse_version(current_version)
    if a is None or b is None:
        return False
    return a > b


class UpdateCancelled(Exception):
    """Caller-driven cancellation observed mid-download."""

    def __init__(self, version: str) -> None:
        super().__init__(f"update {version} download cancelled")
        self.version = version


class ChecksumMismatch(Exception):
    """Downloaded installer SHA-256 didn't match the server's manifest entry."""

    def __init__(self, *, version: str, expected: str, actual: str) -> None:
        super().__init__(
            f"update {version}: expected sha256 {expected}, got {actual}"
        )
        self.version = version
        self.expected = expected
        self.actual = actual


class UpdateClient:
    """Polls ``/v1/app-version`` and downloads a signed installer."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def check_for_update(self) -> AvailableVersion | None:
        """Return the available version if it's newer than the running app.

        ``None`` covers: server unreachable, malformed response, same or
        older version. Network errors are logged at INFO, not raised, so
        the periodic poll doesn't surface them to the user.
        """
        try:
            resp = self._http.get(_APP_VERSION_PATH)
        except HttpError as exc:
            logger.info("update check failed: %s", exc)
            return None

        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("update check: bad JSON: %s", exc)
            return None

        try:
            version = str(payload["version"])
            url = str(payload["url"])
            sha256 = str(payload["sha256"])
        except (KeyError, TypeError) as exc:
            logger.warning("update check: missing required field: %s", exc)
            return None
        release_notes = payload.get("release_notes")
        notes = str(release_notes) if release_notes is not None else None

        if not is_newer(version, app_version()):
            logger.info(
                "update check: server reports %s, running %s — no update",
                version,
                app_version(),
            )
            return None

        logger.info(
            "update check: %s available (running %s)", version, app_version()
        )
        return AvailableVersion(
            version=version, url=url, sha256=sha256, release_notes=notes
        )

    def download(
        self,
        available: AvailableVersion,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Download the signed installer; verify SHA-256; return the path.

        Raises:
            ``HttpError`` subclass on network / server failure.
            ``ChecksumMismatch`` if the downloaded bytes don't match
            ``available.sha256``.
            ``UpdateCancelled`` if ``cancelled()`` returns True between
            chunks.
        """
        is_cancelled = cancelled or (lambda: False)
        out_dir = updates_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"LocalEQUS-Setup-{available.version}.exe"
        partial = out_path.with_suffix(out_path.suffix + ".partial")

        logger.info("Downloading update %s -> %s", available.url, out_path)
        resp = self._http.get(available.url, stream=True)
        sha = hashlib.sha256()
        bytes_written = 0
        try:
            with partial.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                    if is_cancelled():
                        raise UpdateCancelled(available.version)
                    if not chunk:
                        continue
                    fh.write(chunk)
                    sha.update(chunk)
                    bytes_written += len(chunk)
        except UpdateCancelled:
            partial.unlink(missing_ok=True)
            raise

        actual = sha.hexdigest()
        if actual != available.sha256.lower():
            partial.unlink(missing_ok=True)
            raise ChecksumMismatch(
                version=available.version, expected=available.sha256, actual=actual
            )

        partial.replace(out_path)
        logger.info(
            "Update download complete: %s (%d bytes)", out_path, bytes_written
        )
        return out_path


__all__ = [
    "AvailableVersion",
    "ChecksumMismatch",
    "UpdateCancelled",
    "UpdateClient",
    "is_newer",
    "updates_dir",
]
