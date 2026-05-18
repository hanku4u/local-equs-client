"""Unit tests for ``local_equs_client.data_layer.telemetry_client`` (C5.11)."""

from __future__ import annotations

import json as _json
from dataclasses import replace
from pathlib import Path

import pytest
import responses

from local_equs_client.config import paths, settings
from local_equs_client.data_layer import telemetry_client
from local_equs_client.data_layer.http import HttpClient
from local_equs_client.state import db
from local_equs_client.state.dao import telemetry_queue

_BASE = "https://equs.example.com"
_CLIENT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    settings.reset_settings()
    telemetry_client.set_client(None)
    yield tmp_path
    telemetry_client.set_client(None)
    settings.reset_settings()


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    return conn


def _http() -> HttpClient:
    return HttpClient(_BASE, _CLIENT_ID, version="0.1.0")


# ----- Telemetry.event -----------------------------------------------------


def test_event_enqueues_to_sqlite(_isolated_app_dir: Path) -> None:
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())

    client.event("app.start", version="1.2.3")

    batch = telemetry_queue.peek_batch(conn, limit=10)
    assert len(batch) == 1
    assert batch[0].type == "app.start"
    assert batch[0].data == {"version": "1.2.3"}


def test_event_is_noop_when_opted_out(_isolated_app_dir: Path) -> None:
    settings.save(replace(settings.get_settings(), telemetry_opt_out=True))
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())

    client.event("app.start")

    assert telemetry_queue.count(conn) == 0


# ----- Telemetry.flush -----------------------------------------------------


@responses.activate
def test_flush_posts_batch_and_clears_queue(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", json={"ok": True})
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())

    client.event("a", i=1)
    client.event("b", i=2)
    sent = client.flush()

    assert sent == 2
    assert telemetry_queue.count(conn) == 0

    body = _json.loads(responses.calls[0].request.body)
    assert [e["type"] for e in body["events"]] == ["a", "b"]
    assert body["events"][0]["data"] == {"i": 1}


@responses.activate
def test_flush_caps_batch_at_50(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", json={"ok": True})
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    for i in range(75):
        client.event("t", i=i)

    sent = client.flush()
    assert sent == 50
    assert telemetry_queue.count(conn) == 25


def test_flush_empty_queue_returns_zero(_isolated_app_dir: Path) -> None:
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    assert client.flush() == 0


@responses.activate
def test_flush_5xx_keeps_events_for_retry(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503)
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a", i=1)
    client.event("b", i=2)

    sent = client.flush()
    assert sent == 0
    assert telemetry_queue.count(conn) == 2


@responses.activate
def test_flush_network_error_keeps_events_for_retry(_isolated_app_dir: Path) -> None:
    responses.add(
        responses.POST,
        f"{_BASE}/v1/telemetry",
        body=responses.ConnectionError(),
    )
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")
    client.event("b")

    sent = client.flush()
    assert sent == 0
    assert telemetry_queue.count(conn) == 2


@responses.activate
def test_flush_4xx_drops_batch(_isolated_app_dir: Path, caplog) -> None:
    responses.add(
        responses.POST,
        f"{_BASE}/v1/telemetry",
        status=400,
        body="malformed",
    )
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")
    client.event("b")

    sent = client.flush()
    assert sent == 0
    assert telemetry_queue.count(conn) == 0
    assert any("dropping batch" in rec.message for rec in caplog.records)


def test_flush_is_noop_when_opted_out(_isolated_app_dir: Path) -> None:
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")  # queue while still opted in
    settings.save(replace(settings.get_settings(), telemetry_opt_out=True))

    sent = client.flush()
    assert sent == 0
    assert telemetry_queue.count(conn) == 1  # nothing was dropped


# ----- Module-level proxy --------------------------------------------------


def test_event_module_level_is_noop_without_client() -> None:
    telemetry_client.event("a", i=1)  # registered client is None
    # Just asserting no exception is raised.


def test_flush_module_level_is_noop_without_client() -> None:
    assert telemetry_client.flush() == 0


@responses.activate
def test_module_level_event_routes_to_registered_client(
    _isolated_app_dir: Path,
) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", json={"ok": True})
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    telemetry_client.set_client(client)

    telemetry_client.event("a", k="v")
    assert telemetry_client.flush() == 1


# ----- Backoff -------------------------------------------------------------


def test_backoff_for_zero_failures_is_zero() -> None:
    assert telemetry_client._backoff_for(0) == 0.0


def test_backoff_for_one_failure_is_60s() -> None:
    assert telemetry_client._backoff_for(1) == 60.0


def test_backoff_steps_through_schedule() -> None:
    assert telemetry_client._backoff_for(1) == 60.0
    assert telemetry_client._backoff_for(2) == 120.0
    assert telemetry_client._backoff_for(3) == 300.0
    assert telemetry_client._backoff_for(4) == 900.0


def test_backoff_caps_at_steady_state() -> None:
    assert telemetry_client._backoff_for(5) == 900.0
    assert telemetry_client._backoff_for(99) == 900.0


@responses.activate
def test_flush_during_backoff_window_is_skipped(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503)
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")

    # First flush fails → starts a 60s backoff.
    assert client.flush() == 0
    assert client._consecutive_failures == 1
    assert len(responses.calls) == 1

    # Second flush is within the window → no extra POST.
    assert client.flush() == 0
    assert len(responses.calls) == 1


@responses.activate
def test_flush_after_backoff_expires_retries(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503)
    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")

    client.flush()
    # Move past the backoff window.
    client._next_flush_at_monotonic = 0.0
    client.flush()

    assert client._consecutive_failures == 2
    assert len(responses.calls) == 2


@responses.activate
def test_successful_flush_resets_backoff(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503)
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", json={"ok": True})

    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")

    client.flush()  # 503 → failures = 1
    assert client._consecutive_failures == 1

    client._next_flush_at_monotonic = 0.0
    client.flush()  # 2xx → reset
    assert client._consecutive_failures == 0
    assert client._next_flush_at_monotonic == 0.0


@responses.activate
def test_4xx_resets_backoff_after_dropping_batch(_isolated_app_dir: Path) -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503)
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=400)

    conn = _conn(_isolated_app_dir)
    client = telemetry_client.Telemetry(conn, _http())
    client.event("a")
    client.event("b")

    client.flush()  # 503
    assert client._consecutive_failures == 1

    client._next_flush_at_monotonic = 0.0
    client.flush()  # 400 drops batch, treated as "server is up"
    assert client._consecutive_failures == 0
