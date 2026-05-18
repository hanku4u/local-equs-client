"""Unit tests for ``local_equs_client.data_layer.app_telemetry`` (C5.12)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from local_equs_client.data_layer import app_telemetry
from local_equs_client.state import db


def _conn(tmp_path: Path):
    c = db.connect(tmp_path / "state.db")
    db.migrate(c)
    return c


def test_os_info_has_expected_keys() -> None:
    info = app_telemetry.os_info()
    assert set(info) == {"platform", "platform_release", "python_version"}
    assert all(isinstance(v, str) and v for v in info.values())
    assert info["python_version"].startswith(
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )


def test_seconds_since_last_exit_is_none_on_first_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    assert app_telemetry.seconds_since_last_exit(conn) is None


def test_record_exit_persists_iso_timestamp(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    app_telemetry.record_exit(conn)
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = 'last_app_exit_at'"
    ).fetchone()
    assert row is not None
    # ISO-8601 with timezone
    assert "T" in row[0] and ("+" in row[0] or "Z" in row[0])


def test_seconds_since_last_exit_is_nonnegative_after_record(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    app_telemetry.record_exit(conn)
    time.sleep(0.01)
    delta = app_telemetry.seconds_since_last_exit(conn)
    assert delta is not None
    assert 0.0 <= delta < 10.0


def test_record_exit_overwrites_previous(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    app_telemetry.record_exit(conn)
    first_row = conn.execute(
        "SELECT value FROM app_state WHERE key = 'last_app_exit_at'"
    ).fetchone()
    time.sleep(0.01)
    app_telemetry.record_exit(conn)
    second_row = conn.execute(
        "SELECT value FROM app_state WHERE key = 'last_app_exit_at'"
    ).fetchone()
    assert first_row[0] != second_row[0]


def test_seconds_since_last_exit_returns_none_on_garbage_value(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("last_app_exit_at", "not-an-iso-string", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    assert app_telemetry.seconds_since_last_exit(conn) is None


def test_app_start_payload_omits_duration_on_first_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    payload = app_telemetry.app_start_payload(conn)
    assert "app_version" in payload
    assert "platform" in payload
    assert "python_version" in payload
    assert "seconds_since_last_exit" not in payload


def test_app_start_payload_includes_duration_after_prior_exit(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    app_telemetry.record_exit(conn)
    payload = app_telemetry.app_start_payload(conn)
    assert "seconds_since_last_exit" in payload
    assert isinstance(payload["seconds_since_last_exit"], float)
    assert payload["seconds_since_last_exit"] >= 0.0


def test_app_exit_payload_has_expected_keys() -> None:
    payload = app_telemetry.app_exit_payload()
    assert "app_version" in payload
    assert "platform" in payload
    assert "python_version" in payload
