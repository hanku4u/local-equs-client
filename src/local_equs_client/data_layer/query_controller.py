"""Glue between SelectionModel and the query pipeline (C1.9, C5.13).

Subscribes to :attr:`SelectionModel.selectionChanged`, debounces 180ms, asks the
:class:`QueryPlanner` for a plan, dispatches the plan through a
:class:`QueryEngine` on a background thread, and emits the result as
``queryCompleted(plan, results)`` or ``queryFailed(exception)``.

A new selection while a query is in flight cancels the prior job — the
controller only ever emits ``queryCompleted`` for the latest dispatch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from local_equs_client.data_layer.query_engine import QueryCancelled, QueryEngine
from local_equs_client.data_layer.query_planner import QueryPlan, QueryPlanner
from local_equs_client.data_layer.threading import BackgroundJob, JobRunner
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.types import ViewMode
from local_equs_client.selection.view_controller import ViewController


class _QueryJob(BackgroundJob):
    """BackgroundJob that runs ``QueryEngine.execute`` for one plan."""

    def __init__(self, engine: QueryEngine, plan: QueryPlan) -> None:
        super().__init__()
        self._engine = engine
        self._plan = plan
        # Set by QueryController after construction; called from the worker
        # thread as each tool's result lands.
        self.on_tool_complete: Callable[[str, Any], None] | None = None
        self.tool_priority: list[str] | None = None

    def run(self) -> Any:
        return self._engine.execute(
            self._plan,
            cancelled=self._is_cancelled,
            on_tool_complete=self.on_tool_complete,
            tool_priority=self.tool_priority,
        )

    def _is_cancelled(self) -> bool:
        return self.cancelled


class QueryController(QObject):
    """Routes selection changes through planner + engine and emits results."""

    queryPlanned = Signal(object)  # QueryPlan — emitted before queries run
    toolCompleted = Signal(object, str, object)  # (QueryPlan, tool_id, ToolResult)
    queryCompleted = Signal(object, object)  # (QueryPlan, dict[str, ToolResult])
    queryFailed = Signal(object)  # exception

    DEBOUNCE_MS = 180
    DEFAULT_VIEWPORT_WIDTH = 1920

    def __init__(
        self,
        selection_model: SelectionModel,
        planner: QueryPlanner,
        engine: QueryEngine,
        runner: JobRunner | None = None,
        view_controller: ViewController | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = selection_model
        self._planner = planner
        self._engine = engine
        self._runner = runner or JobRunner()
        self._view_controller = view_controller

        self._mode: ViewMode = "standard"
        self._viewport_width = self.DEFAULT_VIEWPORT_WIDTH

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._dispatch)

        self._current_job: _QueryJob | None = None
        self._tool_priority: list[str] = []

        self._model.selectionChanged.connect(self._debounce.start)
        if self._view_controller is not None:
            # A mode flip is also a reason to re-query.
            self._view_controller.modeChanged.connect(self._debounce.start)
            self._view_controller.groupByChanged.connect(self._debounce.start)

    # --- Viewport priority hook (C4.5) -----------------------------------

    def set_tool_priority(self, tool_ids: list[str]) -> None:
        """C4.5: hint the engine to submit these tools first on the next dispatch."""
        self._tool_priority = list(tool_ids)

    # --- Public surface ---------------------------------------------------

    def trigger(self) -> None:
        """Run a query for the current selection without waiting for the debounce."""
        self._debounce.stop()
        self._dispatch()

    def set_mode(self, mode: ViewMode) -> None:
        """Set the mode used when no ViewController is attached. Otherwise no-op."""
        if self._view_controller is not None:
            self._view_controller.set_mode(mode)
        else:
            self._mode = mode

    def set_viewport_width(self, width_px: int) -> None:
        self._viewport_width = max(1, width_px)

    # --- Internals --------------------------------------------------------

    @property
    def _current_mode(self) -> ViewMode:
        return self._view_controller.mode if self._view_controller else self._mode

    def _dispatch(self) -> None:
        if self._current_job is not None:
            self._current_job.request_cancel()
            self._current_job = None

        snapshot = self._model.snapshot()
        plan = self._planner.plan(snapshot, self._current_mode, self._viewport_width)

        # C4.4: tell the chart grid the plan as soon as it's ready, before
        # any DuckDB work has started. The grid lays out empty placeholder
        # frames so the UI doesn't blank then refill.
        self.queryPlanned.emit(plan)

        job = _QueryJob(self._engine, plan)
        captured_plan = plan
        captured_job = job

        def _on_tool_done(tool_id: str, result: Any) -> None:
            # Called from the engine worker thread; Qt queues the signal to
            # whichever thread the slot is on.
            if captured_job is not self._current_job:
                return  # stale dispatch
            self.toolCompleted.emit(captured_plan, tool_id, result)

        def _on_finished(result: object) -> None:
            if captured_job is not self._current_job:
                return
            self._current_job = None
            self.queryCompleted.emit(captured_plan, result)

        def _on_failed(exc: object) -> None:
            if captured_job is not self._current_job:
                return
            self._current_job = None
            if isinstance(exc, QueryCancelled):
                return
            self.queryFailed.emit(exc)

        job.on_tool_complete = _on_tool_done
        job.tool_priority = list(self._tool_priority)
        job.finished.connect(_on_finished)
        job.failed.connect(_on_failed)

        self._current_job = job
        self._runner.submit(job)


__all__ = ["QueryController"]
