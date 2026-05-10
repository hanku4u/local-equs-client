"""Unit tests for ``local_equs_client.data_layer.query_controller`` (C1.9).

Tests bypass the Qt event loop by:

- Driving the debounce ``QTimer`` manually via ``trigger()``.
- Substituting a synchronous runner that records jobs without dispatching.
- Connecting signals with ``Qt.DirectConnection``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from PySide6.QtCore import Qt

from local_equs_client.data_layer.query_controller import QueryController, _QueryJob
from local_equs_client.data_layer.query_engine import QueryCancelled
from local_equs_client.data_layer.query_planner import QueryPlan
from local_equs_client.data_layer.threading import _BackgroundJobRunnable
from local_equs_client.selection.selection_model import SelectionModel


class _StubPlanner:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def plan(self, selection: Any, mode: Any, viewport_width: int) -> QueryPlan:
        self.calls.append((selection, mode, viewport_width))
        return QueryPlan(
            per_tool_queries=[],
            target_resolution=timedelta(seconds=1),
            partial_data_warnings=[],
        )


class _StubEngine:
    def __init__(self, *, raise_: Exception | None = None) -> None:
        self.calls: list[QueryPlan] = []
        self.priorities: list[list[str] | None] = []
        self._raise = raise_

    def execute(
        self,
        plan: QueryPlan,
        cancelled: Any = None,
        on_tool_complete: Any = None,
        tool_priority: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(plan)
        self.priorities.append(tool_priority)
        if cancelled is not None and cancelled():
            raise QueryCancelled()
        if self._raise is not None:
            raise self._raise
        results = {q.tool_id: object() for q in plan.per_tool_queries}
        if on_tool_complete is not None:
            for tool_id, result in results.items():
                on_tool_complete(tool_id, result)
        return results


class _SyncRunner:
    """Captures jobs without dispatching to QThreadPool."""

    def __init__(self) -> None:
        self.submitted: list[_QueryJob] = []

    def submit(self, job: _QueryJob) -> _QueryJob:
        self.submitted.append(job)
        return job


def _make_controller(*, engine_raises: Exception | None = None) -> tuple:
    model = SelectionModel()
    planner = _StubPlanner()
    engine = _StubEngine(raise_=engine_raises)
    runner = _SyncRunner()
    controller = QueryController(model, planner, engine, runner=runner)
    return controller, model, planner, engine, runner


def _emission_collector(controller: QueryController) -> tuple[list, list]:
    completed: list[tuple] = []
    failed: list[Exception] = []
    controller.queryCompleted.connect(
        lambda plan, results: completed.append((plan, results)), Qt.DirectConnection
    )
    controller.queryFailed.connect(lambda exc: failed.append(exc), Qt.DirectConnection)
    return completed, failed


def _run_submitted(runner: _SyncRunner, index: int = -1) -> None:
    """Synchronously run a submitted job through the BackgroundJob plumbing."""
    job = runner.submitted[index]
    _BackgroundJobRunnable(job).run()


def test_trigger_runs_planner_and_engine() -> None:
    controller, _model, planner, engine, runner = _make_controller()
    completed, failed = _emission_collector(controller)

    controller.trigger()

    assert len(planner.calls) == 1
    # Engine isn't called until the job runs in the worker thread.
    assert len(runner.submitted) == 1

    _run_submitted(runner)

    assert len(engine.calls) == 1
    assert len(completed) == 1
    assert failed == []


def test_completed_carries_the_plan_and_results() -> None:
    controller, _model, _planner, _engine, runner = _make_controller()
    completed, _ = _emission_collector(controller)

    controller.trigger()
    _run_submitted(runner)

    plan, results = completed[0]
    assert isinstance(plan, QueryPlan)
    assert results == {}


def test_engine_failure_emits_query_failed() -> None:
    boom = RuntimeError("boom")
    controller, _model, _planner, _engine, runner = _make_controller(engine_raises=boom)
    completed, failed = _emission_collector(controller)

    controller.trigger()
    _run_submitted(runner)

    assert completed == []
    assert failed == [boom]


def test_query_cancelled_does_not_emit_failed() -> None:
    controller, _model, _planner, engine, runner = _make_controller()
    completed, failed = _emission_collector(controller)

    controller.trigger()
    job = runner.submitted[-1]
    job.request_cancel()  # engine.execute raises QueryCancelled when polled
    _run_submitted(runner)

    assert completed == []
    assert failed == []


def test_rapid_dispatch_cancels_prior_job() -> None:
    controller, _model, _planner, _engine, runner = _make_controller()
    completed, _ = _emission_collector(controller)

    controller.trigger()
    first_job = runner.submitted[-1]
    controller.trigger()  # supersedes the first
    second_job = runner.submitted[-1]

    assert first_job is not second_job
    assert first_job.cancelled is True

    # Run the (stale) first job — it should not emit completed.
    _run_submitted(runner, index=0)
    assert completed == []

    # Run the live second job — it emits.
    _run_submitted(runner, index=1)
    assert len(completed) == 1


def test_set_mode_passes_through_to_planner() -> None:
    controller, _model, planner, _engine, runner = _make_controller()
    controller.set_mode("focus")
    controller.trigger()
    _run_submitted(runner)

    _selection, mode, _vw = planner.calls[-1]
    assert mode == "focus"


def test_set_viewport_width_passes_through_to_planner() -> None:
    controller, _model, planner, _engine, runner = _make_controller()
    controller.set_viewport_width(1024)
    controller.trigger()
    _run_submitted(runner)

    _selection, _mode, vw = planner.calls[-1]
    assert vw == 1024


# --- C4.4: progressive emission ------------------------------------------


def test_query_planned_fires_before_engine_runs() -> None:
    controller, _model, _planner, _engine, runner = _make_controller()
    planned: list[QueryPlan] = []
    controller.queryPlanned.connect(lambda p: planned.append(p), Qt.DirectConnection)

    controller.trigger()

    # queryPlanned must fire on the dispatching thread, before the worker.
    assert len(planned) == 1
    # Engine hasn't been called yet — job is submitted but not run.
    assert _engine.calls == []

    _run_submitted(runner)
    # Engine has been called now.
    assert _engine.calls == [planned[0]]


def test_tool_completed_emitted_per_tool() -> None:
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from local_equs_client.data_layer.query_planner import ToolQuery
    from local_equs_client.selection.types import TimeRange

    class _PlannerWithTools:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def plan(self, selection: Any, mode: Any, viewport_width: int) -> QueryPlan:
            self.calls.append((selection, mode, viewport_width))
            start = datetime(2026, 1, 1, tzinfo=UTC)
            return QueryPlan(
                per_tool_queries=[
                    ToolQuery(
                        tool_id="a",
                        file_paths=(Path("a.parquet"),),
                        raw_columns=("c",),
                        time_range=TimeRange(start=start, end=start + timedelta(seconds=10)),
                    ),
                    ToolQuery(
                        tool_id="b",
                        file_paths=(Path("b.parquet"),),
                        raw_columns=("c",),
                        time_range=TimeRange(start=start, end=start + timedelta(seconds=10)),
                    ),
                ],
                target_resolution=timedelta(seconds=1),
                partial_data_warnings=[],
            )

    model = SelectionModel()
    planner = _PlannerWithTools()
    engine = _StubEngine()
    runner = _SyncRunner()
    controller = QueryController(model, planner, engine, runner=runner)

    tool_events: list[tuple[str, object]] = []
    controller.toolCompleted.connect(
        lambda plan, tool_id, result: tool_events.append((tool_id, result)),
        Qt.DirectConnection,
    )

    controller.trigger()
    _run_submitted(runner)

    tool_ids = {ev[0] for ev in tool_events}
    assert tool_ids == {"a", "b"}


def test_tool_completed_suppressed_for_stale_job() -> None:
    controller, _model, _planner, _engine, runner = _make_controller()
    tool_events: list[tuple[str, object]] = []
    controller.toolCompleted.connect(
        lambda plan, tool_id, result: tool_events.append((tool_id, result)),
        Qt.DirectConnection,
    )

    controller.trigger()
    controller.trigger()  # supersedes the first
    _run_submitted(runner, index=0)  # stale

    assert tool_events == []


# --- C4.5: viewport priority hint ---------------------------------------


def test_set_tool_priority_threaded_to_engine() -> None:
    controller, _model, _planner, engine, runner = _make_controller()
    controller.set_tool_priority(["b", "a"])
    controller.trigger()
    _run_submitted(runner)

    assert engine.priorities[-1] == ["b", "a"]
