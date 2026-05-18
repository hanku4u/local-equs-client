"""Unit tests for ``local_equs_client.data_layer.http`` (C2.3)."""

from __future__ import annotations

import pytest
import responses

from local_equs_client.data_layer.http import (
    HttpClient,
    NotFound,
    ServerError,
    ServerUnreachable,
)

_BASE = "https://equs.example.com"
_CLIENT_ID = "11111111-2222-3333-4444-555555555555"


def _client(version: str = "0.1.0") -> HttpClient:
    return HttpClient(_BASE, _CLIENT_ID, version=version)


@responses.activate
def test_get_returns_response_on_2xx() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", json={"ok": True})

    resp = _client().get("/v1/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@responses.activate
def test_default_headers_are_set() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", body="ok")
    _client(version="1.2.3").get("/v1/test")

    headers = responses.calls[0].request.headers
    assert headers["X-Client-Id"] == _CLIENT_ID
    assert headers["X-App-Version"] == "1.2.3"
    assert headers["User-Agent"] == "LocalEQUS/1.2.3"


@responses.activate
def test_404_raises_not_found() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/missing", status=404)
    with pytest.raises(NotFound):
        _client().get("/v1/missing")


@responses.activate
def test_5xx_raises_server_error() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/oops", status=500, body="boom")
    with pytest.raises(ServerError) as info:
        _client().get("/v1/oops")
    assert info.value.status_code == 500
    assert "boom" in info.value.body


@responses.activate
def test_4xx_other_raises_server_error() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", status=403)
    with pytest.raises(ServerError):
        _client().get("/v1/test")


@responses.activate
def test_304_is_returned_as_response_not_error() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", status=304)
    resp = _client().get("/v1/test")
    assert resp.status_code == 304


@responses.activate
def test_connection_error_raises_server_unreachable() -> None:
    responses.add(
        responses.GET,
        f"{_BASE}/v1/test",
        body=responses.ConnectionError(),
    )
    with pytest.raises(ServerUnreachable):
        _client().get("/v1/test")


@responses.activate
def test_relative_path_joined_to_base_url() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", body="ok")
    _client().get("v1/test")  # no leading slash
    assert responses.calls[0].request.url == f"{_BASE}/v1/test"


@responses.activate
def test_post_sends_json_body_and_returns_response() -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", json={"ok": True})

    resp = _client().post("/v1/telemetry", json={"events": [{"type": "t"}]})
    assert resp.status_code == 200
    import json as _json

    body = _json.loads(responses.calls[0].request.body)
    assert body == {"events": [{"type": "t"}]}


@responses.activate
def test_post_5xx_raises_server_error() -> None:
    responses.add(responses.POST, f"{_BASE}/v1/telemetry", status=503, body="busy")
    with pytest.raises(ServerError) as info:
        _client().post("/v1/telemetry", json={})
    assert info.value.status_code == 503


@responses.activate
def test_post_connection_error_raises_server_unreachable() -> None:
    responses.add(
        responses.POST,
        f"{_BASE}/v1/telemetry",
        body=responses.ConnectionError(),
    )
    with pytest.raises(ServerUnreachable):
        _client().post("/v1/telemetry", json={})


@responses.activate
def test_absolute_url_not_re_joined() -> None:
    other = "https://other.example.com/x"
    responses.add(responses.GET, other, body="ok")
    _client().get(other)
    assert responses.calls[0].request.url == other


@responses.activate
def test_custom_headers_merge() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", body="ok")
    _client().get("/v1/test", headers={"If-None-Match": "abc"})
    sent = responses.calls[0].request.headers
    assert sent["If-None-Match"] == "abc"
    assert sent["X-Client-Id"] == _CLIENT_ID


@responses.activate
def test_trailing_slash_in_base_url_trimmed() -> None:
    responses.add(responses.GET, f"{_BASE}/v1/test", body="ok")
    HttpClient(f"{_BASE}/", _CLIENT_ID, version="0.1.0").get("/v1/test")
    assert responses.calls[0].request.url == f"{_BASE}/v1/test"
