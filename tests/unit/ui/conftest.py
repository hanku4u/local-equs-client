"""Fixtures for UI unit tests.

Sets ``QT_QPA_PLATFORM=offscreen`` so widgets construct without a display.
Tests in this directory ``importorskip`` on ``PySide6.QtWidgets`` so they
collect cleanly on platforms (or sandboxes) where Qt's runtime libs aren't
installed; Windows CI has them and runs the suite.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
