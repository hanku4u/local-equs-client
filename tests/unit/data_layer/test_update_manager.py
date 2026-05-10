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
