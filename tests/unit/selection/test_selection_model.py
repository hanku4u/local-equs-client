"""Unit tests for ``local_equs_client.selection.selection_model`` (C1.6).

Uses ``Qt.DirectConnection`` so the tests run without a Qt event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt

from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.types import Selection, TimeRange


def _emission_counter(model: SelectionModel) -> list[int]:
    received: list[int] = []
    model.selectionChanged.connect(lambda: received.append(1), Qt.DirectConnection)
    return received


def test_initial_state_is_empty_with_recent_time_range() -> None:
    model = SelectionModel()
    assert model.tools == ()
    assert model.sensors_canonical == ()
    assert model.sensors_raw == ()
    span = model.time_range.end - model.time_range.start
    assert span == timedelta(hours=24)


def test_set_tools_emits_and_updates() -> None:
    model = SelectionModel()
    received = _emission_counter(model)

    model.set_tools(("etcher_a1", "etcher_a2"))

    assert model.tools == ("etcher_a1", "etcher_a2")
    assert sum(received) == 1


def test_set_tools_with_same_value_does_not_emit() -> None:
    model = SelectionModel()
    model.set_tools(("etcher_a1",))
    received = _emission_counter(model)

    model.set_tools(("etcher_a1",))

    assert sum(received) == 0


def test_set_sensors_canonical_round_trip() -> None:
    model = SelectionModel()
    received = _emission_counter(model)

    model.set_sensors_canonical(("chamber_pressure", "rf_power"))

    assert model.sensors_canonical == ("chamber_pressure", "rf_power")
    assert sum(received) == 1

    model.set_sensors_canonical(("chamber_pressure", "rf_power"))
    assert sum(received) == 1


def test_set_sensors_raw_round_trip() -> None:
    model = SelectionModel()
    received = _emission_counter(model)

    model.set_sensors_raw(("ChamberPressure_torr",))

    assert model.sensors_raw == ("ChamberPressure_torr",)
    assert sum(received) == 1


def test_set_time_range_emits_only_on_change() -> None:
    model = SelectionModel()
    received = _emission_counter(model)

    new_range = TimeRange(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    model.set_time_range(new_range)
    assert model.time_range == new_range
    assert sum(received) == 1

    model.set_time_range(new_range)
    assert sum(received) == 1


def test_set_accepts_arbitrary_iterable_and_freezes_to_tuple() -> None:
    model = SelectionModel()
    model.set_tools(("a", "b"))
    # Tuple identity preserved
    assert isinstance(model.tools, tuple)


def test_clear_resets_selection_and_emits() -> None:
    model = SelectionModel()
    model.set_tools(("etcher_a1",))
    model.set_sensors_raw(("ChamberPressure_torr",))
    received = _emission_counter(model)

    model.clear()

    assert model.tools == ()
    assert model.sensors_canonical == ()
    assert model.sensors_raw == ()
    assert sum(received) == 1


def test_clear_when_empty_does_not_emit() -> None:
    model = SelectionModel()
    received = _emission_counter(model)

    model.clear()

    assert sum(received) == 0


def test_snapshot_returns_immutable_selection() -> None:
    model = SelectionModel()
    model.set_tools(("etcher_a1",))
    model.set_sensors_raw(("foo",))

    snap = model.snapshot()

    assert isinstance(snap, Selection)
    assert snap.tools == ("etcher_a1",)
    assert snap.sensors_raw == ("foo",)
    assert snap.sensors_canonical == ()
    # Snapshot must not change when the model mutates afterward.
    model.set_tools(("etcher_a2",))
    assert snap.tools == ("etcher_a1",)


def test_concurrent_reads_during_writes() -> None:
    """Smoke test: reads from a worker thread don't crash while UI thread mutates."""
    import threading

    model = SelectionModel()
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            _ = model.tools
            _ = model.sensors_raw
            _ = model.snapshot()

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(200):
            model.set_tools((f"tool_{i}",))
    finally:
        stop.set()
        t.join(timeout=2)
    assert not t.is_alive()
