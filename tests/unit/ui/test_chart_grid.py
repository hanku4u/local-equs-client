"""Unit tests for ``local_equs_client.ui.chart_grid`` (C1.10, C1.11, C1.12, C1.13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402

from local_equs_client.data_layer.query_engine import QueryError  # noqa: E402
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery  # noqa: E402
from local_equs_client.selection.types import TimeRange  # noqa: E402
from local_equs_client.ui.chart_grid import ChartGrid  # noqa: E402


def _make_plan(per_tool: list[tuple[str, tuple[str, ...]]]) -> QueryPlan:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return QueryPlan(
        per_tool_queries=[
            ToolQuery(
                tool_id=tool_id,
                file_paths=(Path(f"{tool_id}.parquet"),),
                raw_columns=raw,
                time_range=TimeRange(start=start, end=start + timedelta(seconds=60)),
            )
            for tool_id, raw in per_tool
        ],
        target_resolution=timedelta(seconds=10),
        partial_data_warnings=[],
    )


def _make_results(*, columns: tuple[str, ...]) -> pa.Table:
    naive_start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i * 10) for i in range(6)]
    rng = np.random.default_rng(seed=1)
    arrays: dict[str, pa.Array] = {
        "bucket": pa.array(timestamps, type=pa.timestamp("ns")),
    }
    for col in columns:
        arrays[f"{col}_avg"] = pa.array(rng.random(6), type=pa.float64())
        arrays[f"{col}_min"] = pa.array(rng.random(6), type=pa.float64())
        arrays[f"{col}_max"] = pa.array(rng.random(6), type=pa.float64())
    return pa.Table.from_pydict(arrays)


def test_empty_grid(qapp) -> None:
    grid = ChartGrid()
    assert grid._plots == {}  # noqa: SLF001


def test_creates_one_plot_per_tool_sensor(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("chamber_pressure", "rf_power"))])
    results = {"etch_a1": _make_results(columns=("chamber_pressure", "rf_power"))}
    grid.update_from_results(plan, results)
    assert set(grid._plots) == {  # noqa: SLF001
        ("etch_a1", "chamber_pressure"),
        ("etch_a1", "rf_power"),
    }


def test_removes_plots_no_longer_in_plan(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure", "rf_power"))]),
        {"etch_a1": _make_results(columns=("chamber_pressure", "rf_power"))},
    )

    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure",))]),
        {"etch_a1": _make_results(columns=("chamber_pressure",))},
    )
    assert set(grid._plots) == {("etch_a1", "chamber_pressure")}  # noqa: SLF001


def test_handles_missing_tool_results_gracefully(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("chamber_pressure",))])
    grid.update_from_results(plan, {})  # no tool data
    plot = grid._plots[("etch_a1", "chamber_pressure")]  # noqa: SLF001
    x, _y = plot.avg_curve.getData()
    assert x is None or len(x) == 0


def test_clear_drops_every_plot(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure",))]),
        {"etch_a1": _make_results(columns=("chamber_pressure",))},
    )
    grid.clear()
    assert grid._plots == {}  # noqa: SLF001


def test_x_axes_link_to_first_plot(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("a", ("c",)), ("b", ("c",))]),
        {
            "a": _make_results(columns=("c",)),
            "b": _make_results(columns=("c",)),
        },
    )
    anchor = grid._anchor_plot  # noqa: SLF001
    assert anchor is not None
    other_keys = [k for k in grid._plots if grid._plots[k].plot_item is not anchor]  # noqa: SLF001
    assert other_keys, "expected at least one non-anchor plot"
    other = grid._plots[other_keys[0]].plot_item  # noqa: SLF001
    assert other.getViewBox().linkedView(0) is anchor.getViewBox()


# --- C1.11: zoom-driven re-query --------------------------------------------


def test_user_range_change_emits_signal(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("c",))]),
        {"etch_a1": _make_results(columns=("c",))},
    )
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid._on_x_range_changed(None, (1778262200.0, 1778262300.0))  # noqa: SLF001

    assert len(received) == 1
    assert received[0].start.timestamp() == 1778262200.0
    assert received[0].end.timestamp() == 1778262300.0


def test_programmatic_range_change_does_not_emit(qapp) -> None:
    grid = ChartGrid()
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid.update_from_results(
        _make_plan([("etch_a1", ("c",))]),
        {"etch_a1": _make_results(columns=("c",))},
    )

    assert received == []


def test_invalid_range_pair_does_not_emit(qapp) -> None:
    grid = ChartGrid()
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid._on_x_range_changed(None, (100.0, 100.0))  # zero-width  # noqa: SLF001
    grid._on_x_range_changed(None, (200.0, 100.0))  # inverted  # noqa: SLF001

    assert received == []


# --- C1.12: per-tool error -------------------------------------------------


def test_query_error_shows_overlay_no_curves(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    grid.update_from_results(
        plan, {"etch_a1": QueryError(tool_id="etch_a1", message="bad parquet")}
    )
    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    x, _y = plot.avg_curve.getData()
    assert x is None or len(x) == 0


def test_one_tool_error_does_not_affect_other_tool(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("good", ("c",)), ("bad", ("c",))])
    grid.update_from_results(
        plan,
        {
            "good": _make_results(columns=("c",)),
            "bad": QueryError(tool_id="bad", message="oh no"),
        },
    )
    good_plot = grid._plots[("good", "c")]  # noqa: SLF001
    bad_plot = grid._plots[("bad", "c")]  # noqa: SLF001
    good_x, _ = good_plot.avg_curve.getData()
    assert good_x is not None and len(good_x) > 0
    assert not good_plot.overlay.isVisible()
    assert bad_plot.overlay.isVisible()


# --- C1.13: no data overlay ------------------------------------------------


def test_empty_table_shows_no_data_overlay(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    empty_table = pa.Table.from_pydict(
        {
            "bucket": pa.array([], type=pa.timestamp("ns")),
            "c_avg": pa.array([], type=pa.float64()),
            "c_min": pa.array([], type=pa.float64()),
            "c_max": pa.array([], type=pa.float64()),
        }
    )
    grid.update_from_results(plan, {"etch_a1": empty_table})

    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    assert "No data" in plot.overlay.toPlainText()


def test_all_null_avg_shows_no_data_overlay(qapp) -> None:
    """Result has rows but every avg is NaN — common when union_by_name fills missing column."""
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    nan = float("nan")
    naive_start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i * 10) for i in range(6)]
    table = pa.Table.from_pydict(
        {
            "bucket": pa.array(timestamps, type=pa.timestamp("ns")),
            "c_avg": pa.array([nan] * 6, type=pa.float64()),
            "c_min": pa.array([nan] * 6, type=pa.float64()),
            "c_max": pa.array([nan] * 6, type=pa.float64()),
        }
    )
    grid.update_from_results(plan, {"etch_a1": table})

    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    assert "No data" in plot.overlay.toPlainText()


def test_overlay_hidden_when_data_returns(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    grid.update_from_results(plan, {"etch_a1": QueryError(tool_id="etch_a1", message="x")})
    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()

    grid.update_from_results(plan, {"etch_a1": _make_results(columns=("c",))})
    assert not plot.overlay.isVisible()


# --- C4.4: progressive rendering ------------------------------------------


def test_on_plan_ready_lays_out_loading_placeholders(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)

    assert set(grid._plots) == {("a", "c"), ("b", "c")}  # noqa: SLF001
    for plot in grid._plots.values():  # noqa: SLF001
        assert plot.overlay.isVisible()
        assert "Loading" in plot.overlay.toPlainText()


def test_progress_label_visible_until_all_tools_complete(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)
    # isHidden() reflects the explicit setVisible(False), independent of whether
    # the widget tree is shown — this widget is never .show()n in the test.
    assert not grid._progress_label.isHidden()  # noqa: SLF001

    grid.on_tool_complete(plan, "a", _make_results(columns=("c",)))
    assert not grid._progress_label.isHidden()  # noqa: SLF001
    assert "1 / 2" in grid._progress_label.text()  # noqa: SLF001

    grid.on_tool_complete(plan, "b", _make_results(columns=("c",)))
    assert grid._progress_label.isHidden()  # noqa: SLF001


def test_on_tool_complete_fills_only_that_tool(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)

    grid.on_tool_complete(plan, "a", _make_results(columns=("c",)))

    assert not grid._plots[("a", "c")].overlay.isVisible()  # noqa: SLF001
    assert grid._plots[("b", "c")].overlay.isVisible()  # noqa: SLF001 — still loading


# --- C4.6: chart cap + banner --------------------------------------------


def test_cap_banner_hidden_below_threshold(qapp) -> None:
    grid = ChartGrid()
    grid.on_plan_ready(_make_plan([("a", ("c",))]))
    assert grid._cap_banner.isHidden()  # noqa: SLF001


def test_cap_banner_shows_when_plan_exceeds_max(qapp) -> None:
    from local_equs_client.ui.chart_grid import MAX_VISIBLE_PLOTS

    big = [(f"t{i}", ("c",)) for i in range(MAX_VISIBLE_PLOTS + 5)]
    plan = _make_plan(big)
    grid = ChartGrid()
    grid.on_plan_ready(plan)

    assert not grid._cap_banner.isHidden()  # noqa: SLF001
    # Only the first MAX_VISIBLE_PLOTS plots were created.
    assert len(grid._plots) == MAX_VISIBLE_PLOTS  # noqa: SLF001
    text = grid._cap_banner_label.text()  # noqa: SLF001
    assert f"of {MAX_VISIBLE_PLOTS + 5}" in text


# --- C4.10: soft guardrail (escalation + button + mode-aware hide) --------


def test_banner_escalates_above_overview_escalation_threshold(qapp) -> None:
    from local_equs_client.ui.chart_grid import OVERVIEW_ESCALATION_THRESHOLD

    over_threshold = [
        (f"t{i}", ("c",)) for i in range(OVERVIEW_ESCALATION_THRESHOLD + 5)
    ]
    grid = ChartGrid()
    grid.on_plan_ready(_make_plan(over_threshold))

    assert not grid._cap_banner.isHidden()  # noqa: SLF001
    assert "Are you sure" in grid._cap_banner_label.text()  # noqa: SLF001
    assert (
        str(OVERVIEW_ESCALATION_THRESHOLD + 5)
        in grid._cap_banner_label.text()  # noqa: SLF001
    )


def test_banner_button_emits_switch_to_overview(qapp) -> None:
    from local_equs_client.ui.chart_grid import MAX_VISIBLE_PLOTS

    big = [(f"t{i}", ("c",)) for i in range(MAX_VISIBLE_PLOTS + 5)]
    grid = ChartGrid()
    grid.on_plan_ready(_make_plan(big))

    fired: list[bool] = []
    grid.switchToOverviewRequested.connect(lambda: fired.append(True), Qt.DirectConnection)
    grid._cap_banner_button.click()  # noqa: SLF001
    assert fired == [True]


def test_banner_hidden_in_overview_mode_even_above_threshold(qapp) -> None:
    from local_equs_client.ui.chart_grid import MAX_VISIBLE_PLOTS

    grid = ChartGrid()
    grid.set_mode("overview")
    big = [(f"t{i}", ("c",)) for i in range(MAX_VISIBLE_PLOTS + 5)]
    grid.on_plan_ready(_make_plan(big))

    assert grid._cap_banner.isHidden()  # noqa: SLF001


def test_banner_reappears_when_switching_back_to_standard(qapp) -> None:
    from local_equs_client.ui.chart_grid import MAX_VISIBLE_PLOTS

    grid = ChartGrid()
    big = [(f"t{i}", ("c",)) for i in range(MAX_VISIBLE_PLOTS + 5)]
    grid.on_plan_ready(_make_plan(big))
    assert not grid._cap_banner.isHidden()  # noqa: SLF001

    grid.set_mode("overview")
    assert grid._cap_banner.isHidden()  # noqa: SLF001

    grid.set_mode("standard")
    assert not grid._cap_banner.isHidden()  # noqa: SLF001


# --- C4.7 overview mode ---------------------------------------------------


def test_overview_mode_uses_sparklines_not_plots(qapp) -> None:
    grid = ChartGrid()
    grid.set_mode("overview")
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)

    # Sparklines created, no PlotItems.
    assert set(grid._sparklines) == {("a", "c"), ("b", "c")}  # noqa: SLF001
    assert grid._plots == {}  # noqa: SLF001


def test_overview_to_standard_clears_sparklines(qapp) -> None:
    grid = ChartGrid()
    grid.set_mode("overview")
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)
    assert grid._sparklines  # noqa: SLF001

    grid.set_mode("standard")
    assert grid._sparklines == {}  # noqa: SLF001
    assert set(grid._plots) == {("a", "c"), ("b", "c")}  # noqa: SLF001


def test_sparkline_click_emits_promote(qapp) -> None:
    from PySide6.QtCore import Qt as _Qt

    grid = ChartGrid()
    grid.set_mode("overview")
    plan = _make_plan([("a", ("c",))])
    grid.on_plan_ready(plan)

    captured: list[tuple[str, str]] = []
    grid.promoteRequested.connect(
        lambda t, s: captured.append((t, s)), _Qt.DirectConnection
    )
    grid._sparklines[("a", "c")].clicked.emit("a", "c")  # noqa: SLF001
    assert captured == [("a", "c")]


# --- C4.8 focus mode ------------------------------------------------------


def test_focus_mode_caps_at_four(qapp) -> None:
    from local_equs_client.ui.chart_grid import FOCUS_MAX_PLOTS

    grid = ChartGrid()
    grid.set_mode("focus")
    too_many = [(f"t{i}", ("c",)) for i in range(FOCUS_MAX_PLOTS + 3)]
    grid.on_plan_ready(_make_plan(too_many))

    assert len(grid._plots) == FOCUS_MAX_PLOTS  # noqa: SLF001


def test_focus_mode_title_includes_stats(qapp) -> None:
    grid = ChartGrid()
    grid.set_mode("focus")
    plan = _make_plan([("a", ("c",))])
    grid.on_plan_ready(plan)
    grid.update_from_results(plan, {"a": _make_results(columns=("c",))})

    title = grid._plots[("a", "c")].plot_item.titleLabel.text  # noqa: SLF001
    assert "min=" in title and "mean=" in title and "max=" in title


def test_standard_mode_title_is_plain(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",))])
    grid.on_plan_ready(plan)
    grid.update_from_results(plan, {"a": _make_results(columns=("c",))})

    title = grid._plots[("a", "c")].plot_item.titleLabel.text  # noqa: SLF001
    assert "min=" not in title


# --- Wheel-to-scroll filter ----------------------------------------------


def _make_wheel_event(*, delta_y: int, ctrl: bool = False):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    modifiers = (
        Qt.KeyboardModifier.ControlModifier
        if ctrl
        else Qt.KeyboardModifier.NoModifier
    )
    return QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_plain_wheel_scrolls_graphics_view(qapp) -> None:
    grid = ChartGrid()
    # Seed plots so the viewport is taller than the visible area would be.
    plan = _make_plan([(f"t{i}", ("c",)) for i in range(20)])
    grid.on_plan_ready(plan)
    scrollbar = grid._graphics.verticalScrollBar()  # noqa: SLF001
    scrollbar.setRange(0, 1000)
    scrollbar.setValue(100)

    plot_viewport = next(iter(grid._plots.values())).plot_widget.viewport()  # noqa: SLF001
    accepted = grid._graphics_wheel_filter.eventFilter(  # noqa: SLF001
        plot_viewport, _make_wheel_event(delta_y=120)
    )

    assert accepted is True
    assert scrollbar.value() == 100 - 120 or scrollbar.value() == max(0, 100 - 120)


def test_ctrl_wheel_falls_through_for_zoom(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",))])
    grid.on_plan_ready(plan)
    scrollbar = grid._graphics.verticalScrollBar()  # noqa: SLF001
    before = scrollbar.value()

    plot_viewport = next(iter(grid._plots.values())).plot_widget.viewport()  # noqa: SLF001
    accepted = grid._graphics_wheel_filter.eventFilter(  # noqa: SLF001
        plot_viewport, _make_wheel_event(delta_y=120, ctrl=True)
    )

    assert accepted is False
    assert scrollbar.value() == before


def test_real_plain_wheel_on_plot_viewport_moves_outer_scrollbar(qapp) -> None:
    """A real wheel event delivered to PlotWidget.viewport() must move the
    outer scrollbar — proving the filter is actually installed there and not
    only on the (dead) outer-scrollarea viewport.
    """
    from PySide6.QtCore import QCoreApplication

    grid = ChartGrid()
    plan = _make_plan([(f"t{i}", ("c",)) for i in range(20)])
    grid.on_plan_ready(plan)
    scrollbar = grid._graphics.verticalScrollBar()  # noqa: SLF001
    scrollbar.setRange(0, 1000)
    scrollbar.setValue(200)

    plot = next(iter(grid._plots.values()))  # noqa: SLF001
    QCoreApplication.sendEvent(
        plot.plot_widget.viewport(), _make_wheel_event(delta_y=120)
    )

    assert scrollbar.value() < 200, (
        "outer scrollbar did not move — filter is not installed on the "
        "PlotWidget viewport, so plain wheel is being consumed by pyqtgraph."
    )


def test_real_ctrl_wheel_on_plot_viewport_does_not_move_outer_scrollbar(qapp) -> None:
    """Ctrl+wheel must fall through the filter so pyqtgraph's ViewBox can zoom.
    The outer scrollbar must therefore stay put.
    """
    from PySide6.QtCore import QCoreApplication

    grid = ChartGrid()
    plan = _make_plan([(f"t{i}", ("c",)) for i in range(20)])
    grid.on_plan_ready(plan)
    scrollbar = grid._graphics.verticalScrollBar()  # noqa: SLF001
    scrollbar.setRange(0, 1000)
    scrollbar.setValue(200)

    plot = next(iter(grid._plots.values()))  # noqa: SLF001
    QCoreApplication.sendEvent(
        plot.plot_widget.viewport(), _make_wheel_event(delta_y=120, ctrl=True)
    )

    assert scrollbar.value() == 200


def test_overview_wheel_filter_scrolls_overview_container(qapp) -> None:
    grid = ChartGrid()
    grid.set_mode("overview")
    plan = _make_plan([(f"t{i}", ("c",)) for i in range(20)])
    grid.on_plan_ready(plan)
    scrollbar = grid._overview_container.verticalScrollBar()  # noqa: SLF001
    scrollbar.setRange(0, 1000)
    scrollbar.setValue(50)

    accepted = grid._overview_wheel_filter.eventFilter(  # noqa: SLF001
        grid._overview_container.viewport(), _make_wheel_event(delta_y=60)
    )

    assert accepted is True
    assert scrollbar.value() < 50


# --- Scrollable layout architecture --------------------------------------


def test_standard_mode_uses_qscrollarea_with_plotwidgets(qapp) -> None:
    """Each plot is its own pg.PlotWidget in a QVBoxLayout under a QScrollArea."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QScrollArea

    grid = ChartGrid()
    plan = _make_plan([("a", ("c",)), ("b", ("c",))])
    grid.on_plan_ready(plan)

    assert isinstance(grid._graphics, QScrollArea)  # noqa: SLF001
    assert all(  # noqa: SLF001
        isinstance(p.plot_widget, pg.PlotWidget) for p in grid._plots.values()
    )


def test_each_plot_widget_added_to_inner_layout(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("a", ("c",)), ("b", ("c",)), ("c", ("c",))])
    grid.on_plan_ready(plan)

    inner_widgets = {
        grid._graphics_layout.itemAt(i).widget()  # noqa: SLF001
        for i in range(grid._graphics_layout.count())  # noqa: SLF001
    }
    inner_widgets.discard(None)
    plot_widgets = {p.plot_widget for p in grid._plots.values()}  # noqa: SLF001
    assert plot_widgets.issubset(inner_widgets)


def test_removed_plot_widgets_are_dropped_from_layout(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("a", ("c",)), ("b", ("c",))]),
        {"a": _make_results(columns=("c",)), "b": _make_results(columns=("c",))},
    )
    layout = grid._graphics_layout  # noqa: SLF001
    before = layout.count()

    grid.update_from_results(
        _make_plan([("a", ("c",))]),
        {"a": _make_results(columns=("c",))},
    )
    # One plot widget removed (the layout always has a trailing stretch).
    assert layout.count() == before - 1
