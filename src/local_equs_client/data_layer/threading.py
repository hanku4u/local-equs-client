"""``BackgroundJob`` / ``JobRunner`` threading scaffold for cancellable work (C0.5).

Subclass :class:`BackgroundJob` and override ``run()`` to return a result. Submit
the job through a :class:`JobRunner`; the framework wraps it in a ``QRunnable``,
captures exceptions, and emits ``finished(result)`` or ``failed(exception)`` on
the job. Long-running jobs cooperatively check ``self.cancelled`` and bail.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class BackgroundJob(QObject):
    """Base class for a unit of background work."""

    finished = Signal(object)
    failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = False

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> Any:
        """Subclasses override this to do the actual work and return a result."""
        raise NotImplementedError


class _BackgroundJobRunnable(QRunnable):
    """Adapts a :class:`BackgroundJob` for ``QThreadPool``."""

    def __init__(self, job: BackgroundJob) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            result = self._job.run()
        except Exception as exc:  # noqa: BLE001 — boundary capture by design
            self._job.failed.emit(exc)
            return
        if not self._job.cancelled:
            self._job.finished.emit(result)


class JobRunner:
    """Thin wrapper around ``QThreadPool`` that submits :class:`BackgroundJob` instances."""

    def __init__(self, pool: QThreadPool | None = None) -> None:
        self._pool = pool or QThreadPool.globalInstance()

    def submit(self, job: BackgroundJob) -> BackgroundJob:
        """Schedule the job and return it as a handle for connecting signals / cancelling."""
        self._pool.start(_BackgroundJobRunnable(job))
        return job
