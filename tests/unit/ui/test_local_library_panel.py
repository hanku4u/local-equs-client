"""Unit tests for ``local_equs_client.ui.local_library_panel`` (C2.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.ui.local_library_panel import LocalLibraryPanel  # noqa: E402


def _write_parquet(path: Path, *, start: datetime, n_rows: int = 60) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    naive = start.astimezone(UTC).replace(tzinfo=None)
    timestamps = [naive + timedelta(seconds=i) for i in range(n_rows)]
    rng = np.random.default_rng(seed=1)
    pq.write_table(
        pa.Table.from_pydict(
            {
                "ts": pa.array(timestamps, type=pa.timestamp("ns")),
                "chamber_pressure": pa.array(rng.random(n_rows), type=pa.float64()),
            }
        ),
        path,
    )


@pytest.fixture
def library_with_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_parquet(data_dir / "etch_a1.parquet", start=datetime(2026, 1, 1, tzinfo=UTC))
    _write_parquet(data_dir / "etch_b2.parquet", start=datetime(2026, 1, 2, tzinfo=UTC))
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    library.scan()
    yield library
    conn.close()


def test_table_lists_every_indexed_file(qapp, library_with_files: LocalLibrary) -> None:
    panel = LocalLibraryPanel(library_with_files)
    rows = [
        panel._table.item(r, 0).text()  # noqa: SLF001
        for r in range(panel._table.rowCount())  # noqa: SLF001
    ]
    assert set(rows) == {"etch_a1", "etch_b2"}


def test_footer_shows_total_size(qapp, library_with_files: LocalLibrary) -> None:
    panel = LocalLibraryPanel(library_with_files)
    text = panel._footer.text()  # noqa: SLF001
    assert text.startswith("Used:")
    assert library_with_files.total_size_bytes() > 0


def test_pin_toggle_updates_library(qapp, library_with_files: LocalLibrary) -> None:
    panel = LocalLibraryPanel(library_with_files)

    pinned_item = panel._table.item(0, 6)  # noqa: SLF001 — Pinned column
    file_id = panel._table.item(0, 0).data(  # noqa: SLF001
        int(Qt.ItemDataRole.UserRole) + 1
    )
    pinned_item.setCheckState(Qt.CheckState.Checked)

    by_id = {f.file_id: f for f in library_with_files.all_files()}
    assert by_id[file_id].pinned is True


def test_refresh_after_external_delete(qapp, library_with_files: LocalLibrary) -> None:
    panel = LocalLibraryPanel(library_with_files)
    initial = panel._table.rowCount()  # noqa: SLF001

    library_with_files.delete("etch_a1.parquet")
    panel.refresh()

    assert panel._table.rowCount() == initial - 1  # noqa: SLF001
