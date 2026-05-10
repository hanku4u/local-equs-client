"""Unit tests for ``local_equs_client.data_layer.download_manager`` (C2.6)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import responses

from local_equs_client.data_layer.download_manager import (
    ChecksumMismatch,
    DownloadCancelled,
    DownloadManager,
)
from local_equs_client.data_layer.http import HttpClient
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.update_manager import ManifestFile
from local_equs_client.state import db

_BASE = "https://equs.example.com"


def _make_parquet_bytes() -> bytes:
    """Build a real parquet so the post-download index step has something to read."""
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive + timedelta(seconds=i) for i in range(20)]
    rng = np.random.default_rng(seed=1)
    table = pa.Table.from_pydict(
        {
            "ts": pa.array(timestamps, type=pa.timestamp("ns")),
            "chamber_pressure": pa.array(rng.random(20), type=pa.float64()),
        }
    )
    import io

    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    http = HttpClient(_BASE, "client-id", version="0.1.0")
    yield DownloadManager(http, library), library, data_dir
    conn.close()


def _mf(file_id: str, body: bytes) -> ManifestFile:
    return ManifestFile(
        file_id=file_id,
        tool_id=file_id.split("/")[0],
        url=f"/v1/data/{file_id}",
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )


@responses.activate
def test_full_download_writes_target_and_updates_index(env) -> None:
    manager, library, data_dir = env
    body = _make_parquet_bytes()
    mf = _mf("etch_a1.parquet", body)

    responses.add(responses.GET, f"{_BASE}{mf.url}", body=body, status=200)

    result = manager.download_file(mf)

    assert result.bytes_written == len(body)
    target = data_dir / "etch_a1.parquet"
    assert target.read_bytes() == body
    assert not target.with_suffix(".parquet.partial").exists()

    files = library.all_files()
    assert len(files) == 1
    assert files[0].sha256 == mf.sha256


@responses.activate
def test_resume_from_partial_uses_range_header(env) -> None:
    manager, _library, data_dir = env
    body = _make_parquet_bytes()
    mf = _mf("etch_a1.parquet", body)

    # Pre-write a partial with the first half of the body.
    partial = data_dir / "etch_a1.parquet.partial"
    half = len(body) // 2
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(body[:half])

    # Server returns 206 with the remaining bytes.
    responses.add(
        responses.GET,
        f"{_BASE}{mf.url}",
        body=body[half:],
        status=206,
        headers={"Content-Range": f"bytes {half}-{len(body) - 1}/{len(body)}"},
    )

    manager.download_file(mf)

    target = data_dir / "etch_a1.parquet"
    assert target.read_bytes() == body
    assert "Range" in responses.calls[0].request.headers
    assert responses.calls[0].request.headers["Range"] == f"bytes={half}-"


@responses.activate
def test_server_returns_200_despite_range_restarts_download(env) -> None:
    manager, _library, data_dir = env
    body = _make_parquet_bytes()
    mf = _mf("etch_a1.parquet", body)

    partial = data_dir / "etch_a1.parquet.partial"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"junk")

    responses.add(responses.GET, f"{_BASE}{mf.url}", body=body, status=200)

    manager.download_file(mf)
    target = data_dir / "etch_a1.parquet"
    assert target.read_bytes() == body


@responses.activate
def test_cancellation_preserves_partial(env) -> None:
    manager, _library, data_dir = env
    body = _make_parquet_bytes()
    mf = _mf("etch_a1.parquet", body)

    responses.add(responses.GET, f"{_BASE}{mf.url}", body=body, status=200)

    with pytest.raises(DownloadCancelled):
        manager.download_file(mf, cancelled=lambda: True)

    target = data_dir / "etch_a1.parquet"
    assert not target.exists()  # not finalized
    # Partial may exist (empty) or not — either is fine for retry.


@responses.activate
def test_checksum_mismatch_raises_and_drops_partial(env) -> None:
    manager, _library, data_dir = env
    body = _make_parquet_bytes()

    mf = ManifestFile(
        file_id="etch_a1.parquet",
        tool_id="etch_a1",
        url="/v1/data/etch_a1.parquet",
        sha256="0" * 64,  # wrong
        size_bytes=len(body),
    )

    responses.add(responses.GET, f"{_BASE}{mf.url}", body=body, status=200)

    with pytest.raises(ChecksumMismatch):
        manager.download_file(mf)

    target = data_dir / "etch_a1.parquet"
    assert not target.exists()
    assert not target.with_suffix(".parquet.partial").exists()


@responses.activate
def test_no_sha_in_manifest_skips_verification(env) -> None:
    manager, library, data_dir = env
    body = _make_parquet_bytes()
    mf = ManifestFile(
        file_id="etch_a1.parquet",
        tool_id="etch_a1",
        url="/v1/data/etch_a1.parquet",
        sha256=None,
        size_bytes=len(body),
    )
    responses.add(responses.GET, f"{_BASE}{mf.url}", body=body, status=200)

    result = manager.download_file(mf)
    assert (data_dir / "etch_a1.parquet").read_bytes() == body
    assert result.sha256  # still computed for the index
    assert library.all_files()[0].sha256 == result.sha256
