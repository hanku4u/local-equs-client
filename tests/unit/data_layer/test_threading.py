"""Unit tests for ``local_equs_client.data_layer.threading`` (C0.5).

These tests exercise the framework directly (no QThreadPool dispatch) using
``Qt.DirectConnection`` so they run without a Qt event loop.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt

from local_equs_client.data_layer.threading import (
    BackgroundJob,
    JobRunner,
    _BackgroundJobRunnable,
)


class _CountingJob(BackgroundJob):
    def __init__(self, target: int) -> None:
        super().__init__()
        self.target = target

    def run(self) -> int:
        total = 0
        for i in range(self.target):
            if self.cancelled:
                return total
            total += i
        return total


class _FailingJob(BackgroundJob):
    def run(self) -> None:
        raise RuntimeError("boom")


class _LongJob(BackgroundJob):
    def __init__(self) -> None:
        super().__init__()
        self.iterations = 0

    def run(self) -> int:
        for _ in range(10_000):
            if self.cancelled:
                return self.iterations
            self.iterations += 1
            time.sleep(0.001)
        return self.iterations


def test_cancelled_flag_default_false() -> None:
    job = _CountingJob(5)
    assert job.cancelled is False


def test_request_cancel_flips_flag() -> None:
    job = _CountingJob(5)
    job.request_cancel()
    assert job.cancelled is True


def test_run_emits_finished_with_result() -> None:
    job = _CountingJob(5)
    received: list[int] = []
    job.finished.connect(received.append, Qt.DirectConnection)

    _BackgroundJobRunnable(job).run()

    assert received == [sum(range(5))]


def test_run_emits_failed_on_exception() -> None:
    job = _FailingJob()
    failures: list[BaseException] = []
    job.failed.connect(failures.append, Qt.DirectConnection)

    _BackgroundJobRunnable(job).run()

    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeError)


def test_run_skips_finished_when_cancelled() -> None:
    job = _CountingJob(5)
    received: list[int] = []
    job.finished.connect(received.append, Qt.DirectConnection)
    job.request_cancel()

    _BackgroundJobRunnable(job).run()

    assert received == []


def test_long_job_stops_promptly_on_cancel() -> None:
    job = _LongJob()

    def cancel_soon() -> None:
        time.sleep(0.05)
        job.request_cancel()

    threading.Thread(target=cancel_soon).start()
    _BackgroundJobRunnable(job).run()

    assert job.iterations < 10_000


def test_job_runner_submit_returns_job() -> None:
    job = _CountingJob(3)
    runner = JobRunner()
    handle = runner.submit(job)
    assert handle is job
