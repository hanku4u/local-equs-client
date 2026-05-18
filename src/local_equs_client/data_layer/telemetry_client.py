"""Telemetry client: queue, batch, POST /v1/telemetry (C5.11).

Call sites use the module-level :func:`event` and :func:`flush`. ``main.py``
constructs a :class:`Telemetry` and registers it via :func:`set_client`;
without a registered client (tests, headless tooling), both functions are
safe no-ops.

Flush policy
------------
- Up to ``_BATCH_LIMIT`` events per POST.
- 2xx → delete the batch from the queue.
- 5xx or network error → leave the batch, retry on the next flush.
- 4xx → drop the batch and log a warning (the server has rejected the
  payload; retrying won't help, and an unbounded poison queue would
  block future events).
- Opt-out (``Settings.telemetry_opt_out``) drops new events immediately
  and short-circuits flush, but leaves any already-queued events alone.

Threading
---------
:meth:`Telemetry.event` and :meth:`Telemetry.flush` may be called from
any thread that owns the SQLite connection. ``main.py`` runs flush from
a ``QTimer`` on the Qt main thread.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from local_equs_client.config.settings import get_settings
from local_equs_client.data_layer.http import (
    HttpClient,
    ServerError,
    ServerUnreachable,
)
from local_equs_client.state.dao import telemetry_queue

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 50
_TELEMETRY_PATH = "/v1/telemetry"


class Telemetry:
    """Queue + flush worker for telemetry events.

    Construct once per app (in ``main.py``) and register via
    :func:`set_client`. Tests instantiate directly.
    """

    def __init__(self, conn: sqlite3.Connection, http: HttpClient) -> None:
        self._conn = conn
        self._http = http

    def event(self, type: str, **data: Any) -> None:
        """Queue one event. No-op if telemetry is opted out."""
        if get_settings().telemetry_opt_out:
            return
        telemetry_queue.enqueue(self._conn, type=type, data=dict(data))

    def flush(self) -> int:
        """POST one batch. Return the number of events sent successfully."""
        if get_settings().telemetry_opt_out:
            return 0
        batch = telemetry_queue.peek_batch(self._conn, limit=_BATCH_LIMIT)
        if not batch:
            return 0

        payload = {
            "events": [
                {"type": e.type, "data": e.data, "created_at": e.created_at}
                for e in batch
            ]
        }
        try:
            self._http.post(_TELEMETRY_PATH, json=payload)
        except ServerUnreachable as exc:
            logger.debug("telemetry flush: network error, retrying later: %s", exc)
            return 0
        except ServerError as exc:
            if 500 <= exc.status_code < 600:
                logger.debug(
                    "telemetry flush: server %s, retrying later", exc.status_code
                )
                return 0
            # 4xx: drop the batch — retrying won't help and we don't want
            # poison events to block future telemetry.
            logger.warning(
                "telemetry flush: server %s, dropping batch of %d events: %s",
                exc.status_code,
                len(batch),
                exc.body[:200],
            )
            telemetry_queue.delete_batch(self._conn, [e.id for e in batch])
            return 0

        telemetry_queue.delete_batch(self._conn, [e.id for e in batch])
        return len(batch)


_client: Telemetry | None = None


def set_client(client: Telemetry | None) -> None:
    """Register (or clear) the process-wide :class:`Telemetry` singleton."""
    global _client
    _client = client


def event(type: str, **data: Any) -> None:
    """Queue an event via the registered client; no-op if no client is set."""
    if _client is None:
        return
    _client.event(type, **data)


def flush() -> int:
    """Flush via the registered client; returns ``0`` if no client is set."""
    if _client is None:
        return 0
    return _client.flush()


__all__ = ["Telemetry", "event", "flush", "set_client"]
