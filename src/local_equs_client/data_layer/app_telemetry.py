"""App lifecycle telemetry payload helpers (C5.12).

``main.py`` emits ``app_start`` on launch and ``app_exit`` on Qt's
``aboutToQuit`` signal; this module assembles the (PII-free) payloads
and tracks the previous-exit timestamp in ``app_state`` so we can
report how long the app was closed.

The pure functions here are easy to unit-test against an in-memory
SQLite connection; the wiring (timer, signal, telemetry client
construction) lives in ``main.py``.
"""

from __future__ import annotations

import platform
import sqlite3
import sys
from datetime import UTC, datetime

from local_equs_client.data_layer.http import app_version

_LAST_EXIT_KEY = "last_app_exit_at"


def os_info() -> dict[str, str]:
    """Coarse OS + Python info — no usernames, hostnames, or paths."""
    return {
        "platform": platform.system(),  # "Windows" / "Linux" / "Darwin"
        "platform_release": platform.release(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
    }


def seconds_since_last_exit(conn: sqlite3.Connection) -> float | None:
    """Return seconds elapsed since the last recorded ``app_exit``.

    Returns ``None`` on first run (no prior exit) or if the stored value
    can't be parsed.
    """
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (_LAST_EXIT_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        prev = datetime.fromisoformat(row[0])
    except ValueError:
        return None
    delta = (datetime.now(UTC) - prev).total_seconds()
    return max(0.0, delta)


def record_exit(conn: sqlite3.Connection) -> None:
    """Persist the current UTC timestamp as the most-recent app_exit."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (_LAST_EXIT_KEY, now, now),
    )
    conn.commit()


def app_start_payload(conn: sqlite3.Connection) -> dict[str, object]:
    """Payload for the ``app_start`` event."""
    payload: dict[str, object] = {"app_version": app_version(), **os_info()}
    delta = seconds_since_last_exit(conn)
    if delta is not None:
        payload["seconds_since_last_exit"] = round(delta, 3)
    return payload


def app_exit_payload() -> dict[str, object]:
    """Payload for the ``app_exit`` event."""
    return {"app_version": app_version(), **os_info()}


__all__ = [
    "app_exit_payload",
    "app_start_payload",
    "os_info",
    "record_exit",
    "seconds_since_last_exit",
]
