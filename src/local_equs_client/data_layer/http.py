"""Shared HTTP client wrapper that injects X-Client-Id and X-App-Version (C2.3).

Wraps a ``requests.Session`` with:

- ``X-Client-Id`` (the persistent UUID from C2.2),
- ``X-App-Version`` (read from package metadata),
- a default timeout, and
- typed errors so call sites can branch on offline vs server-side failure
  vs not-found without sniffing exception messages.

Pass relative paths (``"/v1/manifest.json"``) — the wrapper joins them onto
``Settings.server_url``.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

import requests

_DEFAULT_TIMEOUT_S = 30.0


class HttpError(Exception):
    """Base class for HTTP wrapper errors."""


class ServerUnreachable(HttpError):
    """Network or DNS failure — server didn't answer at all."""


class ServerError(HttpError):
    """Server answered with a non-success status (other than 404)."""

    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        super().__init__(f"{status_code} from {url}: {body[:200]}")
        self.status_code = status_code
        self.url = url
        self.body = body


class NotFound(HttpError):
    """Server answered 404 for the requested URL."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Not found: {url}")
        self.url = url


def app_version() -> str:
    """Resolve the installed package version, falling back to ``unknown``."""
    try:
        return metadata.version("local-equs-client")
    except metadata.PackageNotFoundError:
        return "unknown"


class HttpClient:
    """Server-aware HTTP client with default headers and typed errors."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        version: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        v = version or app_version()
        self._session.headers.update(
            {
                "X-Client-Id": client_id,
                "X-App-Version": v,
                "User-Agent": f"LocalEQUS/{v}",
            }
        )

    def get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        url = self._url(path)
        try:
            resp = self._session.get(
                url,
                timeout=self._timeout,
                headers=headers,
                params=params,
                stream=stream,
            )
        except requests.RequestException as exc:
            raise ServerUnreachable(str(exc)) from exc

        self._raise_for_status(resp, url)
        return resp

    def close(self) -> None:
        self._session.close()

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self._base_url}/{path.lstrip('/')}"

    @staticmethod
    def _raise_for_status(resp: requests.Response, url: str) -> None:
        # 304 is a valid response for conditional GETs, not an error.
        if resp.status_code == 404:
            raise NotFound(url)
        if 400 <= resp.status_code < 600:
            raise ServerError(resp.status_code, url, body=resp.text)


__all__ = [
    "HttpClient",
    "HttpError",
    "NotFound",
    "ServerError",
    "ServerUnreachable",
    "app_version",
]
