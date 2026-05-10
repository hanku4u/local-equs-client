"""Server-vs-local manifest diff and download UI (C2.7).

Opens against an :class:`UpdateManager` + :class:`DownloadManager`, fetches the
manifest, computes the diff, and shows a two-section tree:

- **Available updates** — files the manifest knows about that are missing
  locally (or whose SHA-256 doesn't match). Each row is checkable; a tool
  parent toggles all its children.
- **Archived locally** — files we have on disk that the manifest no longer
  references. Read-only; the user can drop them through the Local Library
  panel from C2.8.

Hitting **Download** wraps each checked manifest file in a C0.5
``BackgroundJob`` and submits through ``JobRunner``. Job ``finished`` / ``failed``
signals update the row status. **Cancel** flips every active job's
``request_cancel`` flag — the download manager observes it between chunks and
leaves ``.partial`` files in place for the next attempt.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.download_manager import (
    DownloadFailed,
    DownloadManager,
    DownloadResult,
)
from local_equs_client.data_layer.http import HttpError
from local_equs_client.data_layer.threading import BackgroundJob, JobRunner
from local_equs_client.data_layer.update_manager import (
    ManifestFile,
    UpdateDiff,
    UpdateManager,
)

_MF_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_FILE_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_ARCHIVED_COLOR = QColor(160, 160, 160)
_ERROR_COLOR = QColor(220, 100, 100)

logger = logging.getLogger(__name__)


class _DownloadJob(BackgroundJob):
    """BackgroundJob wrapper around ``DownloadManager.download_file``."""

    def __init__(self, manager: DownloadManager, manifest_file: ManifestFile) -> None:
        super().__init__()
        self._manager = manager
        self._mf = manifest_file

    @property
    def file_id(self) -> str:
        return self._mf.file_id

    def run(self) -> Any:
        return self._manager.download_file(self._mf, cancelled=self._is_cancelled)

    def _is_cancelled(self) -> bool:
        return self.cancelled


class UpdatesPanel(QDialog):
    """Dialog showing manifest-vs-local diff with selective download."""

    HEADERS: tuple[str, ...] = ("File", "Size", "Status")

    def __init__(
        self,
        update_manager: UpdateManager,
        download_manager: DownloadManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Updates")
        self.resize(900, 600)
        self._update_manager = update_manager
        self._download_manager = download_manager
        self._runner = JobRunner()
        self._jobs: dict[str, _DownloadJob] = {}

        layout = QVBoxLayout(self)

        self._summary = QLabel("Loading manifest…")
        layout.addWidget(self._summary)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(self.HEADERS))
        self._tree.setHeaderLabels(list(self.HEADERS))
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._tree.setAlternatingRowColors(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._tree)

        button_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        button_row.addWidget(self._refresh_btn)
        button_row.addStretch()
        self._download_btn = QPushButton("Download selected")
        self._download_btn.clicked.connect(self._on_download)
        button_row.addWidget(self._download_btn)
        self._cancel_btn = QPushButton("Cancel downloads")
        self._cancel_btn.clicked.connect(self._on_cancel_all)
        button_row.addWidget(self._cancel_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        self.refresh()

    # --- Refresh / populate ---------------------------------------------

    def refresh(self) -> None:
        try:
            diff = self._update_manager.compute_updates()
        except HttpError as exc:
            self._summary.setText(f"Server error: {exc}")
            self._tree.clear()
            return
        except Exception as exc:  # noqa: BLE001 — surface anything else as a banner
            logger.exception("compute_updates failed")
            self._summary.setText(f"Manifest error: {exc}")
            self._tree.clear()
            return

        self._summary.setText(
            f"{len(diff.to_download)} file(s) to download · "
            f"{len(diff.archived_locally)} archived locally"
        )
        self._populate(diff)

    def _populate(self, diff: UpdateDiff) -> None:
        self._tree.clear()

        download_root = QTreeWidgetItem(self._tree, ["Available updates", "", ""])
        download_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        by_tool: dict[str, list[ManifestFile]] = {}
        for mf in diff.to_download:
            by_tool.setdefault(mf.tool_id, []).append(mf)

        for tool_id in sorted(by_tool):
            files = by_tool[tool_id]
            tool_item = QTreeWidgetItem(
                download_root, [tool_id, _format_bytes(sum(f.size_bytes for f in files)), ""]
            )
            tool_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            tool_item.setCheckState(0, Qt.CheckState.Unchecked)
            for mf in files:
                child = QTreeWidgetItem(tool_item, [mf.file_id, _format_bytes(mf.size_bytes), ""])
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setData(0, _MF_ROLE, mf)
                child.setData(0, _FILE_ID_ROLE, mf.file_id)

        archived_root = QTreeWidgetItem(
            self._tree, ["Archived locally (not in manifest)", "", ""]
        )
        archived_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        for lf in diff.archived_locally:
            row = QTreeWidgetItem(
                archived_root, [lf.file_id, _format_bytes(lf.size_bytes), "archived"]
            )
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            row.setForeground(0, QBrush(_ARCHIVED_COLOR))
            row.setForeground(1, QBrush(_ARCHIVED_COLOR))
            row.setForeground(2, QBrush(_ARCHIVED_COLOR))

        self._tree.expandAll()

    # --- Download / cancel ----------------------------------------------

    def _on_download(self) -> None:
        selected = self._collect_checked()
        if not selected:
            return
        for mf in selected:
            self._submit_one(mf)

    def _on_cancel_all(self) -> None:
        for job in self._jobs.values():
            job.request_cancel()

    def _submit_one(self, mf: ManifestFile) -> None:
        if mf.file_id in self._jobs:
            return  # already running
        job = _DownloadJob(self._download_manager, mf)
        self._jobs[mf.file_id] = job

        def on_finished(result: object) -> None:
            self._jobs.pop(mf.file_id, None)
            if isinstance(result, DownloadResult):
                self._set_row_status(mf.file_id, "done")
            else:
                self._set_row_status(mf.file_id, "done")

        def on_failed(exc: object) -> None:
            self._jobs.pop(mf.file_id, None)
            if isinstance(exc, DownloadFailed):
                self._set_row_status(mf.file_id, str(exc), error=True)
            else:
                self._set_row_status(mf.file_id, f"error: {exc}", error=True)

        job.finished.connect(on_finished)
        job.failed.connect(on_failed)
        self._set_row_status(mf.file_id, "downloading…")
        self._runner.submit(job)

    # --- Tree helpers ----------------------------------------------------

    def _collect_checked(self) -> list[ManifestFile]:
        checked: list[ManifestFile] = []
        download_root = self._tree.topLevelItem(0)
        if download_root is None:
            return checked
        for tool_index in range(download_root.childCount()):
            tool_item = download_root.child(tool_index)
            for child_index in range(tool_item.childCount()):
                child = tool_item.child(child_index)
                if child.checkState(0) == Qt.CheckState.Checked:
                    mf = child.data(0, _MF_ROLE)
                    if mf is not None:
                        checked.append(mf)
        return checked

    def _set_row_status(self, file_id: str, text: str, *, error: bool = False) -> None:
        item = self._find_row(file_id)
        if item is None:
            return
        item.setText(2, text)
        if error:
            item.setForeground(2, QBrush(_ERROR_COLOR))
        else:
            item.setForeground(2, QBrush())

    def _find_row(self, file_id: str) -> QTreeWidgetItem | None:
        download_root = self._tree.topLevelItem(0)
        if download_root is None:
            return None
        for tool_index in range(download_root.childCount()):
            tool_item = download_root.child(tool_index)
            for child_index in range(tool_item.childCount()):
                child = tool_item.child(child_index)
                if child.data(0, _FILE_ID_ROLE) == file_id:
                    return child
        return None


def _format_bytes(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PiB"


__all__ = ["UpdatesPanel"]
