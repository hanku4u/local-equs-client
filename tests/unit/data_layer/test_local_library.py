"""Unit tests for ``local_equs_client.data_layer.local_library`` (C1.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.selection.types import TimeRange
from local_equs_client.state import db


def _make_parquet(
    path: Path,
    start: datetime,
    *,
    n_rows: int = 100,
    hz: int = 10,
    sensor_names: tuple[str, ...] = ("chamber_pressure", "rf_forward_power"),
) -> tuple[datetime, datetime]:
    """Write a small parquet with a ``ts`` column + sensor columns. Returns (min_ts, max_ts)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    naive_start = start.astimezone(UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i / hz) for i in range(n_rows)]
    columns: dict[str, pa.Array] = {
        "ts": pa.array(timestamps, type=pa.timestamp("ns")),
    }
    rng = np.random.default_rng(seed=42)
    for name in sensor_names:
        columns[name] = pa.array(rng.random(n_rows), type=pa.float64())
    pq.write_table(pa.Table.from_pydict(columns), path)
    return start, start + timedelta(seconds=(n_rows - 1) / hz)


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "state.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_scan_indexes_flat_layout(data_dir: Path, conn) -> None:
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    _make_parquet(data_dir / "etch_a1.parquet", start)

    library = LocalLibrary(data_dir, conn)
    indexed = library.scan()

    assert indexed == 1
    files = library.all_files()
    assert len(files) == 1
    f = files[0]
    assert f.tool_id == "etch_a1"
    assert f.hour_bucket is None
    assert f.row_count == 100
    assert f.size_bytes > 0
    assert f.min_ts == start
    assert f.archived is False
    assert f.pinned is False


def test_scan_indexes_nested_layout(data_dir: Path, conn) -> None:
    start = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    _make_parquet(data_dir / "etch_b2" / "2026-01-01T13.parquet", start)

    library = LocalLibrary(data_dir, conn)
    library.scan()

    files = library.all_files()
    assert len(files) == 1
    f = files[0]
    assert f.tool_id == "etch_b2"
    assert f.hour_bucket == "2026-01-01T13"


def test_files_for_returns_overlapping_only(data_dir: Path, conn) -> None:
    for day in (1, 2, 3):
        _make_parquet(
            data_dir / "tool_x" / f"2026-01-0{day}T08.parquet",
            datetime(2026, 1, day, 8, tzinfo=UTC),
        )

    library = LocalLibrary(data_dir, conn)
    library.scan()

    range_jan2 = TimeRange(
        start=datetime(2026, 1, 2, tzinfo=UTC),
        end=datetime(2026, 1, 2, 23, 59, tzinfo=UTC),
    )
    matched = library.files_for("tool_x", range_jan2)
    buckets = {f.hour_bucket for f in matched}
    assert buckets == {"2026-01-02T08"}


def test_files_for_excludes_other_tools(data_dir: Path, conn) -> None:
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    _make_parquet(data_dir / "etch_a1" / "block.parquet", start)
    _make_parquet(data_dir / "etch_b2" / "block.parquet", start)

    library = LocalLibrary(data_dir, conn)
    library.scan()

    matched = library.files_for(
        "etch_a1",
        TimeRange(
            start=start - timedelta(hours=1),
            end=start + timedelta(hours=1),
        ),
    )
    assert len(matched) == 1
    assert matched[0].tool_id == "etch_a1"


def test_scan_skips_corrupt_parquet(data_dir: Path, conn, caplog) -> None:
    good = data_dir / "good.parquet"
    bad = data_dir / "bad.parquet"
    _make_parquet(good, datetime(2026, 1, 1, tzinfo=UTC))
    bad.write_bytes(b"not a parquet file at all")

    library = LocalLibrary(data_dir, conn)
    indexed = library.scan()

    assert indexed == 1
    assert library.all_files()[0].file_id == "good.parquet"


def test_scan_removes_vanished_files(data_dir: Path, conn) -> None:
    p = data_dir / "etch_a1.parquet"
    _make_parquet(p, datetime(2026, 1, 1, tzinfo=UTC))

    library = LocalLibrary(data_dir, conn)
    library.scan()
    assert len(library.all_files()) == 1

    p.unlink()
    library.scan()

    assert library.all_files() == []


def test_scan_handles_missing_data_dir(tmp_path: Path, conn) -> None:
    library = LocalLibrary(tmp_path / "does-not-exist", conn)
    assert library.scan() == 0


def test_pin_unpin(data_dir: Path, conn) -> None:
    _make_parquet(data_dir / "etch_a1.parquet", datetime(2026, 1, 1, tzinfo=UTC))
    library = LocalLibrary(data_dir, conn)
    library.scan()

    library.pin("etch_a1.parquet")
    assert library.all_files()[0].pinned is True

    library.unpin("etch_a1.parquet")
    assert library.all_files()[0].pinned is False


def test_total_size_bytes(data_dir: Path, conn) -> None:
    _make_parquet(data_dir / "a.parquet", datetime(2026, 1, 1, tzinfo=UTC))
    _make_parquet(data_dir / "b.parquet", datetime(2026, 1, 2, tzinfo=UTC))
    library = LocalLibrary(data_dir, conn)
    library.scan()

    expected = sum(f.size_bytes for f in library.all_files())
    assert library.total_size_bytes() == expected


def test_archived_files_excluded_from_files_for(data_dir: Path, conn) -> None:
    _make_parquet(data_dir / "etch_a1.parquet", datetime(2026, 1, 1, tzinfo=UTC))
    library = LocalLibrary(data_dir, conn)
    library.scan()

    conn.execute("UPDATE local_files SET archived = 1 WHERE file_id = ?", ("etch_a1.parquet",))
    conn.commit()

    matched = library.files_for(
        "etch_a1",
        TimeRange(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )
    assert matched == []
    assert len(library.archived_files()) == 1


def test_scan_is_idempotent(data_dir: Path, conn) -> None:
    _make_parquet(data_dir / "etch_a1.parquet", datetime(2026, 1, 1, tzinfo=UTC))
    library = LocalLibrary(data_dir, conn)
    first = library.scan()
    second = library.scan()

    assert first == second == 1
    assert len(library.all_files()) == 1
