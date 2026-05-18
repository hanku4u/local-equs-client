"""Top-level crash handler: log + emit telemetry for uncaught exceptions (C5.15).

Installs hooks on ``sys.excepthook`` (covers exceptions that escape from
Qt signal slots, since PySide6 routes them through Python's default
exception handling) and ``threading.excepthook`` (covers stdlib threads
that don't bubble back to the main thread). Both hooks:

- log the traceback at ERROR level so it lands in the rotating log file
- emit an ``error`` telemetry event with type, truncated traceback, and
  thread name
- fall through to the previously-registered hook so default behavior
  (printing to stderr, terminating the process) still happens

``KeyboardInterrupt`` and ``SystemExit`` are special-cased: they're not
crashes and we don't want telemetry noise for Ctrl-C or normal exits.

Worker exceptions raised inside :class:`BackgroundJob.run` are already
captured by :class:`_BackgroundJobRunnable` and re-emitted as ``failed``
signals, so they don't reach these hooks.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from types import TracebackType
from typing import Any

from local_equs_client.data_layer import telemetry_client

logger = logging.getLogger(__name__)

_MAX_TRACEBACK_CHARS = 4000

_previous_sys_excepthook: Any = None
_previous_threading_excepthook: Any = None


def install() -> None:
    """Wire ``sys.excepthook`` and ``threading.excepthook``.

    Idempotent: re-installing is a no-op when the hooks are already ours,
    so calling at startup more than once is safe.
    """
    global _previous_sys_excepthook, _previous_threading_excepthook
    if sys.excepthook is _crash_hook:
        return
    _previous_sys_excepthook = sys.excepthook
    _previous_threading_excepthook = threading.excepthook
    sys.excepthook = _crash_hook
    threading.excepthook = _thread_crash_hook


def uninstall() -> None:
    """Restore the previous hooks. Intended for tests."""
    global _previous_sys_excepthook, _previous_threading_excepthook
    if _previous_sys_excepthook is not None:
        sys.excepthook = _previous_sys_excepthook
        _previous_sys_excepthook = None
    if _previous_threading_excepthook is not None:
        threading.excepthook = _previous_threading_excepthook
        _previous_threading_excepthook = None


def _crash_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    tb: TracebackType | None,
) -> None:
    """Handle an uncaught exception on the main thread."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Let Ctrl-C terminate the app cleanly without telemetry noise.
        if _previous_sys_excepthook is not None:
            _previous_sys_excepthook(exc_type, exc_value, tb)
        else:
            sys.__excepthook__(exc_type, exc_value, tb)
        return

    tb_text = "".join(traceback.format_exception(exc_type, exc_value, tb))
    logger.error("Uncaught exception:\n%s", tb_text)
    telemetry_client.event(
        "error",
        error_type=exc_type.__name__,
        traceback=_truncate(tb_text),
        thread="main",
    )
    # Best-effort sync flush so the 'error' event leaves the queue before
    # the process dies. Network failures / timeouts are swallowed inside
    # flush() — the event stays queued for the next launch.
    _safe_flush()

    # Chain to the previous hook so default reporting / process exit still runs.
    if _previous_sys_excepthook is not None:
        _previous_sys_excepthook(exc_type, exc_value, tb)
    else:
        sys.__excepthook__(exc_type, exc_value, tb)


def _thread_crash_hook(args: threading.ExceptHookArgs) -> None:
    """Handle an uncaught exception in a stdlib ``threading.Thread``."""
    if args.exc_type is SystemExit:
        return

    tb_text = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    thread_name = args.thread.name if args.thread is not None else "?"
    logger.error("Uncaught exception in thread %s:\n%s", thread_name, tb_text)
    telemetry_client.event(
        "error",
        error_type=args.exc_type.__name__,
        traceback=_truncate(tb_text),
        thread=thread_name,
    )
    _safe_flush()

    if _previous_threading_excepthook is not None:
        _previous_threading_excepthook(args)


def _safe_flush() -> None:
    """Flush telemetry, swallowing anything that goes wrong.

    The crash hook is the last code that runs before the process dies;
    raising here would mask the original exception. ``flush()`` already
    handles ServerUnreachable / 5xx by leaving events in the queue, but
    we still defend against bugs in the flush path itself.
    """
    try:
        telemetry_client.flush()
    except Exception as exc:  # noqa: BLE001 — last-mile robustness
        logger.warning("crash handler: flush failed: %s", exc)


def _truncate(text: str, limit: int = _MAX_TRACEBACK_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 18] + "\n…(truncated)…"


__all__ = ["install", "uninstall"]
