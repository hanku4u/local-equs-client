"""Fixtures for UI unit tests.

Sets ``QT_QPA_PLATFORM=offscreen`` so widgets construct without a display.
Tests in this directory ``importorskip`` on ``PySide6.QtWidgets`` so they
collect cleanly on platforms (or sandboxes) where Qt's runtime libs aren't
installed; Windows CI has them and runs the suite via pytest-qt's bundled
``qapp`` fixture.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
