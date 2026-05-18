"""Unit tests for ``local_equs_client.data_layer.update_manager`` (C2.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import responses

from local_equs_client.data_layer.http import HttpClient
from local_equs_client.data_layer.update_manager import UpdateManager
from local_equs_client.state import db
from local_equs_client.state.dao import manifest_cache

_BASE = "https://equs.example.com"
_CLIENT_ID = "11111111-2222-3333-4444-555555555555"
_PATH = "/v1/manifest.json"


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "state.db")
    db.migrate(c)
    yield c
    c.close()


def _make_manager(conn) -> UpdateManager:
    return UpdateManager(HttpClient(_BASE, _CLIENT_ID, version="0.1.0"), conn)


@responses.activate
def test_first_call_fetches_and_caches(conn) -> None:
    payload = {"version": 1, "files": [{"id": "etch_a1.parquet"}]}
    responses.add(
        responses.GET,
        f"{_BASE}{_PATH}",
        json=payload,
        headers={"ETag": '"abc123"'},
    )

    body = _make_manager(conn).fetch_manifest()
    assert body == payload

    cached_body, cached_etag = manifest_cache.load(conn)
    assert cached_body == payload
    assert cached_etag == '"abc123"'


@responses.activate
def test_second_call_uses_etag_and_returns_cached_on_304(conn) -> None:
    initial = {"version": 1, "files": []}
    responses.add(
        responses.GET, f"{_BASE}{_PATH}", json=initial, headers={"ETag": '"v1"'}
    )

    manager = _make_manager(conn)
    manager.fetch_manifest()

    responses.add(responses.GET, f"{_BASE}{_PATH}", status=304)
    body = manager.fetch_manifest()

    assert body == initial
    sent_headers = responses.calls[1].request.headers
    assert sent_headers.get("If-None-Match") == '"v1"'


@responses.activate
def test_200_with_new_body_overwrites_cache(conn) -> None:
    first = {"version": 1, "files": []}
    second = {"version": 2, "files": [{"id": "x.parquet"}]}
    responses.add(responses.GET, f"{_BASE}{_PATH}", json=first, headers={"ETag": '"v1"'})
    responses.add(responses.GET, f"{_BASE}{_PATH}", json=second, headers={"ETag": '"v2"'})

    manager = _make_manager(conn)
    manager.fetch_manifest()
    body = manager.fetch_manifest()

    assert body == second
    cached_body, cached_etag = manifest_cache.load(conn)
    assert cached_body == second
    assert cached_etag == '"v2"'


@responses.activate
def test_304_without_cache_refetches_unconditionally(conn) -> None:
    """Server returns 304 but we never cached anything — refetch without ETag."""
    payload = {"version": 1, "files": []}
    responses.add(responses.GET, f"{_BASE}{_PATH}", status=304)
    responses.add(responses.GET, f"{_BASE}{_PATH}", json=payload, headers={"ETag": '"v1"'})

    body = _make_manager(conn).fetch_manifest()
    assert body == payload
    # Second call to the responses mock should have no If-None-Match header.
    assert "If-None-Match" not in responses.calls[1].request.headers


@responses.activate
def test_not_found_propagates(conn) -> None:
    from local_equs_client.data_layer.http import NotFound

    responses.add(responses.GET, f"{_BASE}{_PATH}", status=404)
    with pytest.raises(NotFound):
        _make_manager(conn).fetch_manifest()


# --- C5.14: telemetry events --------------------------------------------


from typing import Any  # noqa: E402

from local_equs_client.data_layer import telemetry_client  # noqa: E402


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


@responses.activate
def test_update_check_event_emitted_on_first_fetch(
    conn, _telemetry_recorder
) -> None:
    payload = {"version": 1, "files": []}
    responses.add(
        responses.GET, f"{_BASE}{_PATH}", json=payload, headers={"ETag": '"v1"'}
    )

    _make_manager(conn).fetch_manifest()

    assert _telemetry_recorder.events == [("update_check", {"cache_hit": False})]


@responses.activate
def test_update_check_event_reports_cache_hit_on_304(
    conn, _telemetry_recorder
) -> None:
    initial = {"version": 1, "files": []}
    responses.add(
        responses.GET, f"{_BASE}{_PATH}", json=initial, headers={"ETag": '"v1"'}
    )
    responses.add(responses.GET, f"{_BASE}{_PATH}", status=304)

    manager = _make_manager(conn)
    manager.fetch_manifest()  # cache_hit=False
    manager.fetch_manifest()  # cache_hit=True

    assert _telemetry_recorder.events == [
        ("update_check", {"cache_hit": False}),
        ("update_check", {"cache_hit": True}),
    ]


# --- C2.5 diff ---------------------------------------------------------------


def _make_manifest_files(*entries: dict) -> dict:
    return {"version": 1, "files": list(entries)}


def _seed_local_file(conn, *, file_id: str, tool_id: str, sha256: str | None) -> None:
    """Insert a synthetic row into local_files (no parquet on disk needed for diffs)."""
    columns = (
        "file_id, tool_id, hour_bucket, min_ts, max_ts, row_count, sha256, "
        "pinned, archived, size_bytes"
    )
    conn.execute(
        f"INSERT OR REPLACE INTO local_files ({columns}) "
        "VALUES (?, ?, NULL, 0, 0, 0, ?, 0, 0, 0)",
        (file_id, tool_id, sha256),
    )
    conn.commit()


def _manager_with_library(conn, tmp_path):
    from local_equs_client.data_layer.local_library import LocalLibrary

    library = LocalLibrary(tmp_path / "data", conn)
    http = HttpClient(_BASE, _CLIENT_ID, version="0.1.0")
    return UpdateManager(http, conn, library=library)


def test_compute_updates_lists_missing_files(conn, tmp_path: Path) -> None:
    manager = _manager_with_library(conn, tmp_path)
    manifest = _make_manifest_files(
        {"file_id": "a/1.parquet", "tool_id": "a", "sha256": "deadbeef", "size_bytes": 100},
        {"file_id": "b/1.parquet", "tool_id": "b", "sha256": "cafef00d", "size_bytes": 200},
    )

    diff = manager.compute_updates(manifest=manifest)

    assert {f.file_id for f in diff.to_download} == {"a/1.parquet", "b/1.parquet"}
    assert diff.archived_locally == []


def test_compute_updates_skips_files_with_matching_sha(conn, tmp_path: Path) -> None:
    manager = _manager_with_library(conn, tmp_path)
    _seed_local_file(conn, file_id="a/1.parquet", tool_id="a", sha256="deadbeef")
    manifest = _make_manifest_files(
        {"file_id": "a/1.parquet", "tool_id": "a", "sha256": "deadbeef", "size_bytes": 100},
    )

    diff = manager.compute_updates(manifest=manifest)
    assert diff.to_download == []
    assert diff.archived_locally == []


def test_compute_updates_re_downloads_on_sha_mismatch(conn, tmp_path: Path) -> None:
    manager = _manager_with_library(conn, tmp_path)
    _seed_local_file(conn, file_id="a/1.parquet", tool_id="a", sha256="oldhash")
    manifest = _make_manifest_files(
        {"file_id": "a/1.parquet", "tool_id": "a", "sha256": "newhash", "size_bytes": 100},
    )

    diff = manager.compute_updates(manifest=manifest)
    assert [f.file_id for f in diff.to_download] == ["a/1.parquet"]


def test_compute_updates_re_downloads_when_local_has_no_sha(conn, tmp_path: Path) -> None:
    """M1 scan didn't compute sha256, so local rows can have NULL sha. Trust the manifest."""
    manager = _manager_with_library(conn, tmp_path)
    _seed_local_file(conn, file_id="a/1.parquet", tool_id="a", sha256=None)
    manifest = _make_manifest_files(
        {"file_id": "a/1.parquet", "tool_id": "a", "sha256": "newhash", "size_bytes": 100},
    )

    diff = manager.compute_updates(manifest=manifest)
    assert [f.file_id for f in diff.to_download] == ["a/1.parquet"]


def test_compute_updates_marks_archived_locally(conn, tmp_path: Path) -> None:
    manager = _manager_with_library(conn, tmp_path)
    _seed_local_file(conn, file_id="z/old.parquet", tool_id="z", sha256="x")
    manifest = _make_manifest_files(
        {"file_id": "a/1.parquet", "tool_id": "a", "sha256": "y", "size_bytes": 100},
    )

    diff = manager.compute_updates(manifest=manifest)
    archived_ids = {f.file_id for f in diff.archived_locally}
    assert archived_ids == {"z/old.parquet"}


def test_parse_manifest_handles_minimal_entries() -> None:
    from local_equs_client.data_layer.update_manager import parse_manifest

    parsed = parse_manifest({"files": [{"file_id": "x.parquet", "tool_id": "x"}]})
    assert len(parsed) == 1
    assert parsed[0].url == "/v1/data/x.parquet"
    assert parsed[0].sha256 is None
    assert parsed[0].size_bytes == 0


def test_parse_manifest_rejects_garbage() -> None:
    from local_equs_client.data_layer.update_manager import parse_manifest

    assert parse_manifest(None) == []
    assert parse_manifest({"files": "nope"}) == []
    assert parse_manifest({"files": [{"file_id": 5}]}) == []  # wrong types


def test_compute_updates_requires_library(conn, tmp_path: Path) -> None:
    http = HttpClient(_BASE, _CLIENT_ID, version="0.1.0")
    manager = UpdateManager(http, conn)  # no library
    with pytest.raises(RuntimeError, match="LocalLibrary"):
        manager.compute_updates(manifest=_make_manifest_files())
