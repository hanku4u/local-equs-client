"""Unit tests for ``local_equs_client.ui.time_range_selector`` (C1.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.selection.selection_model import SelectionModel  # noqa: E402
from local_equs_client.selection.types import TimeRange  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.ui.time_range_selector import TimeRangeSelector  # noqa: E402


@pytest.fixture
def empty_library(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    yield LocalLibrary(data_dir, conn)
    conn.close()


def test_constructor_runs_without_data(qapp, empty_library: LocalLibrary) -> None:
    model = SelectionModel()
    widget = TimeRangeSelector(model, empty_library)

    assert widget._start_edit is not None  # noqa: SLF001 — widget internals under test
    assert widget._end_edit is not None  # noqa: SLF001


def test_inputs_reflect_initial_model_state(qapp, empty_library: LocalLibrary) -> None:
    model = SelectionModel()
    widget = TimeRangeSelector(model, empty_library)

    expected_start = model.time_range.start
    expected_end = model.time_range.end

    actual_start = datetime.fromtimestamp(
        widget._start_edit.dateTime().toSecsSinceEpoch(), tz=UTC  # noqa: SLF001
    )
    actual_end = datetime.fromtimestamp(
        widget._end_edit.dateTime().toSecsSinceEpoch(), tz=UTC  # noqa: SLF001
    )
    # QDateTimeEdit truncates to seconds; allow a 1s slack.
    assert abs((actual_start - expected_start).total_seconds()) < 1.5
    assert abs((actual_end - expected_end).total_seconds()) < 1.5


def test_external_model_change_syncs_inputs(qapp, empty_library: LocalLibrary) -> None:
    model = SelectionModel()
    widget = TimeRangeSelector(model, empty_library)

    new_range = TimeRange(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    model.set_time_range(new_range)

    actual_start = datetime.fromtimestamp(
        widget._start_edit.dateTime().toSecsSinceEpoch(), tz=UTC  # noqa: SLF001
    )
    assert actual_start == new_range.start


def test_invalid_range_does_not_push(qapp, empty_library: LocalLibrary) -> None:
    """End <= start should not overwrite the model's range."""
    from PySide6.QtCore import QDateTime, Qt

    model = SelectionModel()
    original = model.time_range
    widget = TimeRangeSelector(model, empty_library)

    bad_end = original.start - timedelta(hours=1)
    widget._end_edit.setDateTime(  # noqa: SLF001
        QDateTime.fromSecsSinceEpoch(int(bad_end.timestamp()), Qt.TimeSpec.UTC)
    )
    widget._push_to_model()  # noqa: SLF001 — bypass debounce

    assert model.time_range == original
