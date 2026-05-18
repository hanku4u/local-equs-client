"""Unit tests for ``local_equs_client.data_layer.crash_handler`` (C5.15)."""

from __future__ import annotations

import sys
import threading
from typing import Any

import pytest

from local_equs_client.data_layer import crash_handler, telemetry_client


class _RecordingClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, type: str, **data: Any) -> None:
        self.events.append((type, dict(data)))


@pytest.fixture
def _telemetry_recorder():
    rec = _RecordingClient()
    telemetry_client.set_client(rec)  # type: ignore[arg-type]
    yield rec
    telemetry_client.set_client(None)


@pytest.fixture
def _installed_crash_handler():
    # Stub pytest's hooks with no-ops so the crash handler's chain-through
    # doesn't re-raise the exception we just synthesized and fail the test.
    original_sys = sys.excepthook
    original_thread = threading.excepthook
    sys.excepthook = lambda *a, **k: None  # type: ignore[assignment]
    threading.excepthook = lambda args: None  # type: ignore[assignment]
    crash_handler.install()
    yield
    crash_handler.uninstall()
    sys.excepthook = original_sys
    threading.excepthook = original_thread


def _raise_then_capture() -> tuple[type[BaseException], BaseException, Any]:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        return sys.exc_info()  # type: ignore[return-value]


def test_install_replaces_sys_excepthook() -> None:
    original = sys.excepthook
    crash_handler.install()
    try:
        assert sys.excepthook is not original
    finally:
        crash_handler.uninstall()
    assert sys.excepthook is original


def test_install_is_idempotent() -> None:
    crash_handler.install()
    hook_after_first = sys.excepthook
    crash_handler.install()
    assert sys.excepthook is hook_after_first
    crash_handler.uninstall()


def test_uninstall_restores_previous_hook() -> None:
    sentinel: Any = object()
    original = sys.excepthook
    sys.excepthook = sentinel
    try:
        crash_handler.install()
        crash_handler.uninstall()
        assert sys.excepthook is sentinel
    finally:
        sys.excepthook = original


def test_main_thread_crash_emits_error_event(
    _installed_crash_handler, _telemetry_recorder
) -> None:
    exc_type, exc_value, tb = _raise_then_capture()
    sys.excepthook(exc_type, exc_value, tb)

    [(name, payload)] = _telemetry_recorder.events
    assert name == "error"
    assert payload["error_type"] == "RuntimeError"
    assert "boom" in payload["traceback"]
    assert payload["thread"] == "main"


def test_keyboard_interrupt_does_not_emit_telemetry(
    _installed_crash_handler, _telemetry_recorder
) -> None:
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        exc_type, exc_value, tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, tb)

    assert _telemetry_recorder.events == []


def test_thread_crash_emits_error_event(
    _installed_crash_handler, _telemetry_recorder
) -> None:
    exc_type, exc_value, tb = _raise_then_capture()
    args = threading.ExceptHookArgs(
        (exc_type, exc_value, tb, threading.Thread(name="worker-1"))
    )
    threading.excepthook(args)

    [(name, payload)] = _telemetry_recorder.events
    assert name == "error"
    assert payload["error_type"] == "RuntimeError"
    assert payload["thread"] == "worker-1"


def test_thread_systemexit_does_not_emit_telemetry(
    _installed_crash_handler, _telemetry_recorder
) -> None:
    try:
        raise SystemExit(0)
    except SystemExit:
        exc_type, exc_value, tb = sys.exc_info()
    args = threading.ExceptHookArgs(
        (exc_type, exc_value, tb, threading.Thread(name="worker-1"))
    )
    threading.excepthook(args)

    assert _telemetry_recorder.events == []


def test_traceback_is_truncated_for_huge_payloads(
    _installed_crash_handler, _telemetry_recorder
) -> None:
    # Manufacture a deep stack to produce a long traceback.
    def deep(n: int) -> None:
        if n == 0:
            raise RuntimeError("x" * 1000)
        deep(n - 1)

    try:
        deep(80)
    except RuntimeError:
        exc_type, exc_value, tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, tb)

    [(_, payload)] = _telemetry_recorder.events
    assert len(payload["traceback"]) <= 4000
    assert "truncated" in payload["traceback"]


def test_crash_logged_at_error_level(
    _installed_crash_handler,
    _telemetry_recorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc_type, exc_value, tb = _raise_then_capture()
    with caplog.at_level("ERROR"):
        sys.excepthook(exc_type, exc_value, tb)

    assert any("Uncaught exception" in rec.message for rec in caplog.records)


def test_chains_to_previous_sys_hook(_telemetry_recorder) -> None:
    received: list[tuple[Any, Any, Any]] = []

    def fake_previous(et: Any, ev: Any, tb: Any) -> None:
        received.append((et, ev, tb))

    original = sys.excepthook
    sys.excepthook = fake_previous
    try:
        crash_handler.install()
        exc_type, exc_value, tb = _raise_then_capture()
        sys.excepthook(exc_type, exc_value, tb)
        assert len(received) == 1
        assert received[0][0] is RuntimeError
    finally:
        crash_handler.uninstall()
        sys.excepthook = original
