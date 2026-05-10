"""Sensor picker: flat list (M1) -> tree, search, detail, saved sets (M3+).

Lists every ``(tool, raw_sensor)`` pair indexed by the Local Library and lets
the user multi-select. Selection updates ``SelectionModel.tools`` and
``SelectionModel.sensors_raw`` atomically; the Query Planner then takes the
Cartesian product (every selected sensor on every selected tool).

A debounced (150ms) filter box matches case-insensitively across tool id,
raw name, and units.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.selection.selection_model import SelectionModel

_FILTER_DEBOUNCE_MS = 150
_TOOL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_RAW_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_UNITS_ROLE = int(Qt.ItemDataRole.UserRole) + 3


class SensorPicker(QWidget):
    """Flat (tool, sensor) picker. Tree mode arrives in C3.3."""

    def __init__(
        self,
        selection_model: SelectionModel,
        library: LocalLibrary,
        metadata_cache: MetadataCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = selection_model
        self._library = library
        self._cache = metadata_cache
        self._suppress_push = False

        self._build_ui()
        self._wire()

        self._model.selectionChanged.connect(self._sync_from_model)

        self.refresh()

    # --- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter sensors…")
        layout.addWidget(self._filter_edit)

        header_row = QHBoxLayout()
        self._count_label = QLabel("Selected (0)")
        self._clear_btn = QPushButton("Clear all")
        header_row.addWidget(self._count_label)
        header_row.addStretch()
        header_row.addWidget(self._clear_btn)
        layout.addLayout(header_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._list.setUniformItemSizes(True)
        layout.addWidget(self._list)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_FILTER_DEBOUNCE_MS)

    def _wire(self) -> None:
        self._filter_edit.textChanged.connect(self._filter_timer.start)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._clear_btn.clicked.connect(self._on_clear)

    # --- Public ------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list from the Local Library + metadata cache."""
        self._list.blockSignals(True)
        try:
            self._list.clear()
            tools = sorted({f.tool_id for f in self._library.all_files() if not f.archived})
            for tool_id in tools:
                for sensor in self._cache.sensors_for(tool_id):
                    label = f"{tool_id}: {sensor.raw_name}"
                    if sensor.units:
                        label += f" ({sensor.units})"
                    item = QListWidgetItem(label)
                    item.setData(_TOOL_ROLE, tool_id)
                    item.setData(_RAW_ROLE, sensor.raw_name)
                    item.setData(_UNITS_ROLE, sensor.units or "")
                    self._list.addItem(item)
        finally:
            self._list.blockSignals(False)
        self._sync_from_model()
        self._apply_filter()

    # --- Slots ------------------------------------------------------------

    def _apply_filter(self) -> None:
        needle = self._filter_edit.text().casefold().strip()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not needle:
                item.setHidden(False)
                continue
            tool = str(item.data(_TOOL_ROLE) or "").casefold()
            raw = str(item.data(_RAW_ROLE) or "").casefold()
            units = str(item.data(_UNITS_ROLE) or "").casefold()
            item.setHidden(needle not in tool and needle not in raw and needle not in units)

    def _on_selection_changed(self) -> None:
        if self._suppress_push:
            return
        selected = self._list.selectedItems()
        tools = tuple(sorted({str(it.data(_TOOL_ROLE)) for it in selected}))
        sensors = tuple(sorted({str(it.data(_RAW_ROLE)) for it in selected}))
        self._suppress_push = True
        try:
            self._model.set_tools(tools)
            self._model.set_sensors_raw(sensors)
        finally:
            self._suppress_push = False
        self._count_label.setText(f"Selected ({len(selected)})")

    def _on_clear(self) -> None:
        self._suppress_push = True
        try:
            self._list.clearSelection()
        finally:
            self._suppress_push = False
        self._model.set_tools(())
        self._model.set_sensors_raw(())
        self._count_label.setText("Selected (0)")

    def _sync_from_model(self) -> None:
        """Reflect selection_model state in the list (idempotent)."""
        if self._suppress_push:
            return
        selected_tools = set(self._model.tools)
        selected_sensors = set(self._model.sensors_raw)

        self._list.blockSignals(True)
        try:
            count = 0
            for i in range(self._list.count()):
                item = self._list.item(i)
                tool = str(item.data(_TOOL_ROLE))
                raw = str(item.data(_RAW_ROLE))
                should_select = tool in selected_tools and raw in selected_sensors
                item.setSelected(should_select)
                if should_select:
                    count += 1
        finally:
            self._list.blockSignals(False)
        self._count_label.setText(f"Selected ({count})")


__all__ = ["SensorPicker"]
