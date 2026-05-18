"""Unit tests for ``local_equs_client.data_layer.update_client`` (C6.4)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import responses

from local_equs_client.config import paths
from local_equs_client.data_layer import update_client
from local_equs_client.data_layer.http import HttpClient

_BASE = "https://equs.example.com"
_CLIENT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    yield tmp_path


def _http(version: str = "1.0.0") -> HttpClient:
    return HttpClient(_BASE, _CLIENT_ID, version=version)


# --- is_newer / _parse_version -----------------------------------------


@pytest.mark.parametrize(
    ("server", "current", "expected"),
    [
        ("1.0.1", "1.0.0", True),
        ("1.1.0", "1.0.9", True),
        ("2.0.0", "1.99.99", True),
        ("1.0.0", "1.0.0", False),
        ("0.9.9", "1.0.0", False),
        ("1.2.3", "1.2", True),  # longer wins via tuple compare
        ("1.2", "1.2.0", False),  # (1,2) < (1,2,0) by tuple compare
    ],
)
def test_is_newer_clean_semver(server: str, current: str, expected: bool) -> None:
    assert update_client.is_newer(server, current) is expected


def test_is_newer_returns_false_on_non_numeric() -> None:
    assert update_client.is_newer("1.2.3-rc1", "1.0.0") is False
    assert update_client.is_newer("1.0.0", "1.2.3-rc1") is False
    assert update_client.is_newer("abc", "1.0.0") is False


# --- updates_dir -------------------------------------------------------


def test_updates_dir_is_under_app_dir(_isolated_app_dir: Path) -> None:
    assert update_client.updates_dir() == _isolated_app_dir / "updates"


# --- UpdateClient.check_for_update ------------------------------------


@responses.activate
def test_check_returns_none_when_server_says_same_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    responses.add(
        responses.GET,
        f"{_BASE}/v1/app-version",
        json={"version": "1.0.0", "url": "x", "sha256": "y"},
    )
    assert update_client.UpdateClient(_http()).check_for_update() is None


@responses.activate
def test_check_returns_available_when_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    payload = {
        "version": "1.2.3",
        "url": "https://cdn.example.com/setup.exe",
        "sha256": "deadbeef",
        "release_notes": "Bug fixes",
    }
    responses.add(responses.GET, f"{_BASE}/v1/app-version", json=payload)

    av = update_client.UpdateClient(_http()).check_for_update()
    assert av is not None
    assert av.version == "1.2.3"
    assert av.url == "https://cdn.example.com/setup.exe"
    assert av.sha256 == "deadbeef"
    assert av.release_notes == "Bug fixes"


@responses.activate
def test_check_omitted_release_notes_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    responses.add(
        responses.GET,
        f"{_BASE}/v1/app-version",
        json={"version": "1.2.3", "url": "x", "sha256": "y"},
    )
    av = update_client.UpdateClient(_http()).check_for_update()
    assert av is not None
    assert av.release_notes is None


@responses.activate
def test_check_returns_none_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    responses.add(responses.GET, f"{_BASE}/v1/app-version", status=503)
    assert update_client.UpdateClient(_http()).check_for_update() is None


@responses.activate
def test_check_returns_none_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    responses.add(
        responses.GET,
        f"{_BASE}/v1/app-version",
        body=responses.ConnectionError(),
    )
    assert update_client.UpdateClient(_http()).check_for_update() is None


@responses.activate
def test_check_returns_none_on_missing_required_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_client, "app_version", lambda: "1.0.0")
    # No `sha256` field.
    responses.add(
        responses.GET,
        f"{_BASE}/v1/app-version",
        json={"version": "1.2.3", "url": "x"},
    )
    assert update_client.UpdateClient(_http()).check_for_update() is None


# --- UpdateClient.download --------------------------------------------


@responses.activate
def test_download_writes_to_updates_dir_and_verifies_sha(
    _isolated_app_dir: Path,
) -> None:
    body = b"installer-bytes" * 200
    sha = hashlib.sha256(body).hexdigest()
    url = "https://cdn.example.com/setup-1.2.3.exe"
    responses.add(responses.GET, url, body=body, status=200)

    out = update_client.UpdateClient(_http()).download(
        update_client.AvailableVersion(version="1.2.3", url=url, sha256=sha)
    )

    assert out == _isolated_app_dir / "updates" / "LocalEQUS-Setup-1.2.3.exe"
    assert out.is_file()
    assert out.read_bytes() == body
    # `.partial` should have been renamed onto the final path.
    assert not out.with_suffix(out.suffix + ".partial").exists()


@responses.activate
def test_download_raises_on_checksum_mismatch(_isolated_app_dir: Path) -> None:
    body = b"installer-bytes"
    url = "https://cdn.example.com/setup-1.2.3.exe"
    responses.add(responses.GET, url, body=body, status=200)

    with pytest.raises(update_client.ChecksumMismatch) as info:
        update_client.UpdateClient(_http()).download(
            update_client.AvailableVersion(
                version="1.2.3", url=url, sha256="0" * 64
            )
        )
    assert info.value.version == "1.2.3"
    assert info.value.expected == "0" * 64
    # Both the partial and the final must have been cleaned up.
    out = _isolated_app_dir / "updates" / "LocalEQUS-Setup-1.2.3.exe"
    assert not out.exists()
    assert not out.with_suffix(out.suffix + ".partial").exists()


@responses.activate
def test_download_accepts_uppercase_sha256_from_server(
    _isolated_app_dir: Path,
) -> None:
    body = b"installer-bytes"
    sha = hashlib.sha256(body).hexdigest().upper()
    url = "https://cdn.example.com/setup-1.2.3.exe"
    responses.add(responses.GET, url, body=body, status=200)

    out = update_client.UpdateClient(_http()).download(
        update_client.AvailableVersion(version="1.2.3", url=url, sha256=sha)
    )
    assert out.read_bytes() == body


@responses.activate
def test_download_cancelled_deletes_partial(_isolated_app_dir: Path) -> None:
    body = b"x" * (update_client._DOWNLOAD_CHUNK_SIZE * 4)
    sha = hashlib.sha256(body).hexdigest()
    url = "https://cdn.example.com/setup-1.2.3.exe"
    responses.add(responses.GET, url, body=body, status=200)

    with pytest.raises(update_client.UpdateCancelled):
        update_client.UpdateClient(_http()).download(
            update_client.AvailableVersion(version="1.2.3", url=url, sha256=sha),
            cancelled=lambda: True,
        )
    out = _isolated_app_dir / "updates" / "LocalEQUS-Setup-1.2.3.exe"
    assert not out.exists()
    assert not out.with_suffix(out.suffix + ".partial").exists()


# --- hand_off ----------------------------------------------------------


def test_hand_off_spawns_installer_with_restart_manager_flags(tmp_path: Path) -> None:
    installer = tmp_path / "LocalEQUS-Setup-1.2.3.exe"
    installer.write_bytes(b"fake")
    captured: list[tuple[list[str], dict]] = []

    def fake_spawn(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured.append((cmd, kwargs))
        return object()

    update_client.hand_off(installer, spawn=fake_spawn)

    [(cmd, _kwargs)] = captured
    assert cmd[0] == str(installer)
    assert "/SILENT" in cmd
    assert "/CLOSEAPPLICATIONS" in cmd
    assert "/RESTARTAPPLICATIONS" in cmd


def test_hand_off_invokes_quit_callback_after_spawn(tmp_path: Path) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"fake")
    spawned: list[list[str]] = []
    quit_calls: list[int] = []

    def fake_spawn(cmd, **kwargs):  # type: ignore[no-untyped-def]
        spawned.append(list(cmd))
        return object()

    update_client.hand_off(
        installer, spawn=fake_spawn, quit_callback=lambda: quit_calls.append(1)
    )

    assert len(spawned) == 1
    assert quit_calls == [1]


def test_hand_off_without_quit_callback_is_fine(tmp_path: Path) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"fake")

    update_client.hand_off(installer, spawn=lambda cmd, **_: object())
    # No assertion needed beyond "doesn't raise"; the call completing
    # without a quit_callback proves the optionality.


def test_hand_off_raises_when_installer_missing(tmp_path: Path) -> None:
    installer = tmp_path / "no-such-installer.exe"
    with pytest.raises(FileNotFoundError):
        update_client.hand_off(installer, spawn=lambda cmd, **_: object())
