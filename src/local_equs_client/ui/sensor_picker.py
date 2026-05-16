"""Sensor picker (C1.4 flat list, C3.3 tree mode, C3.4-3.7 search/detail/sets/header).

The picker has six stacked sections plus a detail pane:

::

    +----------------------------------+
    | Selected (N)   [Clear] [Save…]   |  C3.7
    +----------------------------------+
    | Saved Sets                       |  C3.6 (stub)
    |   No saved sets yet — M5         |
    +----------------------------------+
    | Filter: [_______________]        |  C3.4
    +----------------------------------+
    | Results (when filter active)     |  C3.4
    |   Etcher A1 / Process / chamber  |
    |   ...                            |
    +----------------------------------+
    | Tree                             |  C3.3
    |   Tool A                         |
    |     Category 1                   |
    |       chamber_pressure           |
    |     ...                          |
    +----------------------------------+
    | Detail pane                      |  C3.5
    |   Name: chamber_pressure         |
    |   Units: torr                    |
    |   ...                            |
    +----------------------------------+

Selection normally flows through ``SelectionModel.sensors_canonical``. When a
tool has no canonical metadata (offline run with no server, or a tool the
backend doesn't know about), the branch falls back to the raw sensor names
read from the parquet schema via ``MetadataCache.sensors_for``. Those leaves
push to ``SelectionModel.sensors_raw`` so the planner uses them verbatim
without going through canonical → raw mapping.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import (
    CanonicalSensor,
    Category,
    MetadataCache,
    SensorInfo,
)
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.types import TimeRange
from local_equs_client.state.dao import saved_sets as saved_sets_dao

_FILTER_DEBOUNCE_MS = 150
_TOOL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_SENSOR_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_NODE_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 3
_DESCRIPTION_ROLE = int(Qt.ItemDataRole.UserRole) + 4
_UNITS_ROLE = int(Qt.ItemDataRole.UserRole) + 5
_SENSOR_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 6
_NODE_TOOL = "tool"
_NODE_CATEGORY = "category"
_NODE_SENSOR = "sensor"
_SENSOR_CANONICAL = "canonical"
_SENSOR_RAW = "raw"
_RAW_CATEGORY_LABEL = "All sensors (raw)"

_SAVE_AS_SET_TOOLTIP = "Save current sensor selection as a named set"
_DETAIL_EMPTY = "Hover a sensor to see details."
_SET_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 10


class SensorPicker(QWidget):
    """Tree picker with search, detail pane, and (M5-stub) saved sets section."""

    def __init__(
        self,
        selection_model: SelectionModel,
        library: LocalLibrary,
        metadata_cache: MetadataCache,
        conn: sqlite3.Connection | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = selection_model
        self._library = library
        self._cache = metadata_cache
        self._conn = conn
        self._suppress_push = False

        self._build_ui()
        self._wire()

        self._model.selectionChanged.connect(self._sync_from_model)
        self.refresh()

    # --- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # C3.7 — Selected (N) header with Clear all + (disabled) Save as set
        header_row = QHBoxLayout()
        self._count_label = QLabel("Selected (0)")
        header_font = QFont(self._count_label.font())
        header_font.setBold(True)
        self._count_label.setFont(header_font)
        header_row.addWidget(self._count_label)
        header_row.addStretch()
        self._clear_btn = QPushButton("Clear all")
        header_row.addWidget(self._clear_btn)
        self._save_as_set_btn = QPushButton("Save as set…")
        self._save_as_set_btn.setEnabled(True)
        self._save_as_set_btn.setToolTip(_SAVE_AS_SET_TOOLTIP)
        header_row.addWidget(self._save_as_set_btn)
        outer.addLayout(header_row)

        # C5.1 — Saved sets section (full CRUD)
        self._saved_sets_box = QGroupBox("Saved Sets")
        sets_layout = QVBoxLayout(self._saved_sets_box)
        self._sets_list = QListWidget()
        self._sets_list.setMaximumHeight(120)
        self._sets_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hint = QLabel("Click to load · Shift+click to add · Right-click for options")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        sets_layout.addWidget(self._sets_list)
        sets_layout.addWidget(hint)
        outer.addWidget(self._saved_sets_box)

        # C3.4 — Filter / search box
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter sensors…")
        outer.addWidget(self._filter_edit)

        # Tree (C3.3) and detail pane (C3.5) live inside a vertical splitter
        # so the user can give the detail pane more / less room.
        body_splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(body_splitter, stretch=1)

        # Search results + tree share the top section.
        top_section = QWidget()
        top_layout = QVBoxLayout(top_section)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self._results_label = QLabel("Results")
        self._results_label.setVisible(False)
        top_layout.addWidget(self._results_label)
        self._results_list = QListWidget()
        self._results_list.setVisible(False)
        self._results_list.setMaximumHeight(180)
        top_layout.addWidget(self._results_list)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setMouseTracking(True)
        top_layout.addWidget(self._tree, stretch=1)

        body_splitter.addWidget(top_section)

        # C3.5 — detail pane
        self._detail_box = QGroupBox("Details")
        detail_layout = QVBoxLayout(self._detail_box)
        self._detail_name = QLabel(_DETAIL_EMPTY)
        self._detail_name.setStyleSheet("color: gray;")
        self._detail_description = QLabel("")
        self._detail_description.setWordWrap(True)
        self._detail_units = QLabel("")
        self._detail_files = QLabel("")
        self._detail_range = QLabel("")
        for w in (
            self._detail_name,
            self._detail_description,
            self._detail_units,
            self._detail_files,
            self._detail_range,
        ):
            detail_layout.addWidget(w)
        detail_layout.addStretch()
        body_splitter.addWidget(self._detail_box)
        body_splitter.setStretchFactor(0, 3)
        body_splitter.setStretchFactor(1, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_FILTER_DEBOUNCE_MS)

    def _wire(self) -> None:
        self._filter_edit.textChanged.connect(self._filter_timer.start)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemEntered.connect(self._on_item_hovered)
        self._results_list.itemEntered.connect(self._on_result_hovered)
        self._results_list.itemClicked.connect(self._on_result_clicked)
        self._results_list.setMouseTracking(True)
        self._clear_btn.clicked.connect(self._on_clear)
        self._save_as_set_btn.clicked.connect(self._on_save_as_set)
        self._sets_list.itemClicked.connect(self._on_set_clicked)
        self._sets_list.customContextMenuRequested.connect(self._on_sets_context_menu)

    # --- Public ----------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the tree from the Local Library + metadata cache."""
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            categories = {c.id: c for c in self._cache.category_tree()}
            tools = sorted({f.tool_id for f in self._library.all_files() if not f.archived})
            for tool_id in tools:
                self._build_tool_branch(tool_id, categories)
        finally:
            self._tree.blockSignals(False)
        self._sync_from_model()
        self._apply_filter()
        self._show_detail_empty()
        self._reload_saved_sets()

    # --- Tree construction -----------------------------------------------

    def _build_tool_branch(
        self, tool_id: str, categories_by_id: dict[str, Category]
    ) -> None:
        prc_group = self._cache.prc_group_for(tool_id)
        sensors: list[CanonicalSensor] = (
            self._cache.canonical_sensors(prc_group) if prc_group else []
        )

        tool_item = QTreeWidgetItem(self._tree, [tool_id])
        tool_item.setData(0, _TOOL_ROLE, tool_id)
        tool_item.setData(0, _NODE_KIND_ROLE, _NODE_TOOL)
        tool_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsAutoTristate
        )
        tool_item.setCheckState(0, Qt.CheckState.Unchecked)

        if sensors:
            self._populate_canonical(tool_item, tool_id, sensors, categories_by_id)
            return

        # Offline fallback: no canonical metadata for this tool, so list the
        # raw parquet columns directly and route checks to ``sensors_raw``.
        raw_sensors = self._cache.sensors_for(tool_id)
        if raw_sensors:
            self._populate_raw(tool_item, tool_id, raw_sensors)

    def _populate_canonical(
        self,
        tool_item: QTreeWidgetItem,
        tool_id: str,
        sensors: list[CanonicalSensor],
        categories_by_id: dict[str, Category],
    ) -> None:
        by_category: dict[str | None, list[CanonicalSensor]] = {}
        for c in sensors:
            by_category.setdefault(c.category_id, []).append(c)

        ordered_keys = sorted(
            by_category.keys(),
            key=lambda k: (k is None, _category_label(k, categories_by_id)),
        )

        total = sum(len(v) for v in by_category.values())
        tool_item.setText(0, f"{tool_id} (0/{total})")

        for cat_id in ordered_keys:
            cat_label = _category_label(cat_id, categories_by_id)
            cat_item = _make_category_item(tool_item, cat_label)

            for sensor in sorted(by_category[cat_id], key=lambda s: s.name):
                label = sensor.name
                if sensor.units:
                    label += f" ({sensor.units})"
                _make_sensor_leaf(
                    cat_item,
                    tool_id=tool_id,
                    sensor_name=sensor.name,
                    label=label,
                    description=sensor.description or "",
                    units=sensor.units or "",
                    kind=_SENSOR_CANONICAL,
                )

    def _populate_raw(
        self,
        tool_item: QTreeWidgetItem,
        tool_id: str,
        sensors: list[SensorInfo],
    ) -> None:
        tool_item.setText(0, f"{tool_id} (0/{len(sensors)})")
        cat_item = _make_category_item(tool_item, _RAW_CATEGORY_LABEL)
        for sensor in sorted(sensors, key=lambda s: s.raw_name):
            label = sensor.raw_name
            if sensor.units:
                label += f" ({sensor.units})"
            _make_sensor_leaf(
                cat_item,
                tool_id=tool_id,
                sensor_name=sensor.raw_name,
                label=label,
                description="",
                units=sensor.units or "",
                kind=_SENSOR_RAW,
            )

    # --- Filter + search results ----------------------------------------

    def _apply_filter(self) -> None:
        needle = self._filter_edit.text().casefold().strip()
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            tool_visible = self._filter_branch(tool_item, needle)
            tool_item.setHidden(not tool_visible)
        self._rebuild_results(needle)

    def _filter_branch(self, node: QTreeWidgetItem, needle: str) -> bool:
        kind = node.data(0, _NODE_KIND_ROLE)
        if kind == _NODE_SENSOR:
            visible = not needle or self._matches_sensor(node, needle)
            node.setHidden(not visible)
            return visible

        any_visible = False
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            if self._filter_branch(child, needle):
                any_visible = True
        if kind == _NODE_TOOL and not needle:
            return True
        node.setHidden(not any_visible)
        return any_visible

    def _matches_sensor(self, leaf: QTreeWidgetItem, needle: str) -> bool:
        haystacks = [
            leaf.text(0),
            str(leaf.data(0, _TOOL_ROLE) or ""),
            str(leaf.data(0, _SENSOR_ROLE) or ""),
            str(leaf.data(0, _UNITS_ROLE) or ""),
            str(leaf.data(0, _DESCRIPTION_ROLE) or ""),
        ]
        return any(needle in h.casefold() for h in haystacks)

    def _rebuild_results(self, needle: str) -> None:
        """C3.4: flat results list grouped by tool, with breadcrumb labels."""
        self._results_list.clear()
        if not needle:
            self._results_label.setVisible(False)
            self._results_list.setVisible(False)
            return

        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None or tool_item.isHidden():
                continue
            for cat_idx in range(tool_item.childCount()):
                cat = tool_item.child(cat_idx)
                if cat is None or cat.isHidden():
                    continue
                for leaf_idx in range(cat.childCount()):
                    leaf = cat.child(leaf_idx)
                    if leaf is None or leaf.isHidden():
                        continue
                    tool_id = str(leaf.data(0, _TOOL_ROLE) or "")
                    sensor = str(leaf.data(0, _SENSOR_ROLE) or "")
                    breadcrumb = f"{tool_id}  /  {cat.text(0)}  /  {leaf.text(0)}"
                    item = QListWidgetItem(breadcrumb)
                    item.setData(_TOOL_ROLE, tool_id)
                    item.setData(_SENSOR_ROLE, sensor)
                    self._results_list.addItem(item)

        count = self._results_list.count()
        self._results_label.setText(f"Results ({count})")
        self._results_label.setVisible(True)
        self._results_list.setVisible(True)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        tool_id = str(item.data(_TOOL_ROLE) or "")
        sensor = str(item.data(_SENSOR_ROLE) or "")
        leaf = self._find_leaf(tool_id, sensor)
        if leaf is None:
            return
        new_state = (
            Qt.CheckState.Unchecked
            if leaf.checkState(0) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        leaf.setCheckState(0, new_state)

    def _find_leaf(self, tool_id: str, sensor: str) -> QTreeWidgetItem | None:
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            if str(tool_item.data(0, _TOOL_ROLE) or "") != tool_id:
                continue
            for cat_idx in range(tool_item.childCount()):
                cat = tool_item.child(cat_idx)
                if cat is None:
                    continue
                for leaf_idx in range(cat.childCount()):
                    leaf = cat.child(leaf_idx)
                    if leaf is None:
                        continue
                    if str(leaf.data(0, _SENSOR_ROLE) or "") == sensor:
                        return leaf
        return None

    # --- Detail pane (C3.5) ---------------------------------------------

    def _on_item_hovered(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, _NODE_KIND_ROLE) != _NODE_SENSOR:
            return
        tool_id = str(item.data(0, _TOOL_ROLE) or "")
        sensor = str(item.data(0, _SENSOR_ROLE) or "")
        description = str(item.data(0, _DESCRIPTION_ROLE) or "")
        units = str(item.data(0, _UNITS_ROLE) or "")
        self._show_detail(tool_id, sensor, description, units)

    def _on_result_hovered(self, item: QListWidgetItem) -> None:
        tool_id = str(item.data(_TOOL_ROLE) or "")
        sensor = str(item.data(_SENSOR_ROLE) or "")
        leaf = self._find_leaf(tool_id, sensor)
        if leaf is None:
            return
        description = str(leaf.data(0, _DESCRIPTION_ROLE) or "")
        units = str(leaf.data(0, _UNITS_ROLE) or "")
        self._show_detail(tool_id, sensor, description, units)

    def _show_detail(
        self,
        tool_id: str,
        sensor: str,
        description: str,
        units: str,
    ) -> None:
        self._detail_name.setStyleSheet("")
        self._detail_name.setText(f"{sensor}  —  {tool_id}")
        self._detail_description.setText(description or "(no description)")
        self._detail_units.setText(f"Units: {units or '—'}")
        files = [f for f in self._library.all_files() if f.tool_id == tool_id and not f.archived]
        self._detail_files.setText(f"Local files: {len(files)}")
        if files:
            extent = TimeRange(
                start=min(f.min_ts for f in files),
                end=max(f.max_ts for f in files),
            )
            self._detail_range.setText(
                f"Local range: {extent.start.isoformat()}  →  {extent.end.isoformat()}"
            )
        else:
            self._detail_range.setText("Local range: (no local data)")

    def _show_detail_empty(self) -> None:
        self._detail_name.setStyleSheet("color: gray;")
        self._detail_name.setText(_DETAIL_EMPTY)
        self._detail_description.setText("")
        self._detail_units.setText("")
        self._detail_files.setText("")
        self._detail_range.setText("")

    # --- Selection sync --------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress_push:
            return
        kind = item.data(0, _NODE_KIND_ROLE)
        if kind != _NODE_SENSOR and kind not in (_NODE_TOOL, _NODE_CATEGORY):
            return
        self._push_to_model()

    def _on_clear(self) -> None:
        self._tree.blockSignals(True)
        try:
            self._set_all_checked(Qt.CheckState.Unchecked)
        finally:
            self._tree.blockSignals(False)
        self._push_to_model()

    # --- Saved sets (C5.1) ------------------------------------------------

    def _reload_saved_sets(self) -> None:
        self._sets_list.clear()
        if self._conn is None:
            return
        for s in saved_sets_dao.list_all(self._conn):
            item = QListWidgetItem(s.name)
            item.setData(_SET_ID_ROLE, s.set_id)
            self._sets_list.addItem(item)

    def _on_save_as_set(self) -> None:
        if self._conn is None:
            return
        snap = self._model.snapshot()
        if not snap.tools and not snap.sensors_canonical and not snap.sensors_raw:
            QMessageBox.information(self, "Save as set", "Nothing selected to save.")
            return
        name, ok = QInputDialog.getText(self, "Save as set", "Set name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            saved_sets_dao.create(
                self._conn,
                name,
                snap.tools,
                snap.sensors_canonical,
                snap.sensors_raw,
            )
        except Exception:  # unique constraint or DB error
            QMessageBox.warning(self, "Save as set", f"A set named '{name}' already exists.")
            return
        self._reload_saved_sets()

    def _on_set_clicked(self, item: QListWidgetItem) -> None:
        if self._conn is None:
            return
        set_id = int(item.data(_SET_ID_ROLE))
        sets = {s.set_id: s for s in saved_sets_dao.list_all(self._conn)}
        s = sets.get(set_id)
        if s is None:
            return
        shift_held = bool(
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        if shift_held:
            merged_tools = tuple(sorted(set(self._model.tools) | set(s.tools)))
            merged_canonicals = tuple(
                sorted(set(self._model.sensors_canonical) | set(s.sensors_canonical))
            )
            merged_raws = tuple(sorted(set(self._model.sensors_raw) | set(s.sensors_raw)))
            self._model.set_tools(merged_tools)
            self._model.set_sensors_canonical(merged_canonicals)
            self._model.set_sensors_raw(merged_raws)
        else:
            self._model.set_tools(s.tools)
            self._model.set_sensors_canonical(s.sensors_canonical)
            self._model.set_sensors_raw(s.sensors_raw)

    def _on_sets_context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        if self._conn is None:
            return
        item = self._sets_list.itemAt(pos)
        if item is None:
            return
        set_id = int(item.data(_SET_ID_ROLE))
        menu = QMenu(self)
        rename_action = menu.addAction("Rename…")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(self._sets_list.viewport().mapToGlobal(pos))
        if chosen is rename_action:
            self._rename_set(set_id, item.text())
        elif chosen is delete_action:
            self._delete_set(set_id, item.text())

    def _rename_set(self, set_id: int, current_name: str) -> None:
        if self._conn is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename set", "New name:", text=current_name
        )
        if not ok or not new_name.strip() or new_name.strip() == current_name:
            return
        try:
            saved_sets_dao.rename(self._conn, set_id, new_name.strip())
        except Exception:
            QMessageBox.warning(
                self, "Rename set", f"A set named '{new_name.strip()}' already exists."
            )
            return
        self._reload_saved_sets()

    def _delete_set(self, set_id: int, name: str) -> None:
        if self._conn is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete set",
            f"Delete saved set '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        saved_sets_dao.delete(self._conn, set_id)
        self._reload_saved_sets()

    def _set_all_checked(self, state: Qt.CheckState) -> None:
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            self._set_branch_state(tool_item, state)

    def _set_branch_state(self, node: QTreeWidgetItem, state: Qt.CheckState) -> None:
        node.setCheckState(0, state)
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            self._set_branch_state(child, state)

    def _push_to_model(self) -> None:
        tools: set[str] = set()
        canonicals: set[str] = set()
        raws: set[str] = set()
        total_selected = 0
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            tool_id = str(tool_item.data(0, _TOOL_ROLE) or "")
            selected_in_tool = self._collect_selected_leaves(
                tool_item, tools, canonicals, raws
            )
            self._update_tool_label(tool_item, tool_id, selected_in_tool)
            total_selected += selected_in_tool

        self._suppress_push = True
        try:
            self._model.set_tools(tuple(sorted(tools)))
            self._model.set_sensors_canonical(tuple(sorted(canonicals)))
            self._model.set_sensors_raw(tuple(sorted(raws)))
        finally:
            self._suppress_push = False
        self._count_label.setText(f"Selected ({total_selected})")

    def _collect_selected_leaves(
        self,
        node: QTreeWidgetItem,
        tools: set[str],
        canonicals: set[str],
        raws: set[str],
    ) -> int:
        kind = node.data(0, _NODE_KIND_ROLE)
        if kind == _NODE_SENSOR:
            if node.checkState(0) == Qt.CheckState.Checked:
                tool_id = str(node.data(0, _TOOL_ROLE) or "")
                sensor = str(node.data(0, _SENSOR_ROLE) or "")
                if tool_id and sensor:
                    tools.add(tool_id)
                    sensor_kind = node.data(0, _SENSOR_KIND_ROLE) or _SENSOR_CANONICAL
                    if sensor_kind == _SENSOR_RAW:
                        raws.add(sensor)
                    else:
                        canonicals.add(sensor)
                    return 1
            return 0
        count = 0
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            count += self._collect_selected_leaves(child, tools, canonicals, raws)
        return count

    def _update_tool_label(
        self, tool_item: QTreeWidgetItem, tool_id: str, selected: int
    ) -> None:
        total = _count_leaves(tool_item)
        if total > 0:
            tool_item.setText(0, f"{tool_id} ({selected}/{total})")
        else:
            tool_item.setText(0, tool_id)

    def _sync_from_model(self) -> None:
        if self._suppress_push:
            return
        tools = set(self._model.tools)
        canonicals = set(self._model.sensors_canonical)
        raws = set(self._model.sensors_raw)

        self._tree.blockSignals(True)
        try:
            for tool_idx in range(self._tree.topLevelItemCount()):
                tool_item = self._tree.topLevelItem(tool_idx)
                if tool_item is None:
                    continue
                self._sync_branch(tool_item, tools, canonicals, raws)
        finally:
            self._tree.blockSignals(False)

        self._refresh_counts()

    def _sync_branch(
        self,
        node: QTreeWidgetItem,
        tools: set[str],
        canonicals: set[str],
        raws: set[str],
    ) -> None:
        kind = node.data(0, _NODE_KIND_ROLE)
        if kind == _NODE_SENSOR:
            tool_id = str(node.data(0, _TOOL_ROLE) or "")
            sensor = str(node.data(0, _SENSOR_ROLE) or "")
            sensor_kind = node.data(0, _SENSOR_KIND_ROLE) or _SENSOR_CANONICAL
            lookup = raws if sensor_kind == _SENSOR_RAW else canonicals
            should = tool_id in tools and sensor in lookup
            node.setCheckState(0, Qt.CheckState.Checked if should else Qt.CheckState.Unchecked)
            return
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            self._sync_branch(child, tools, canonicals, raws)

    def _refresh_counts(self) -> None:
        total = 0
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            tool_id = str(tool_item.data(0, _TOOL_ROLE) or "")
            selected = _count_checked_leaves(tool_item)
            self._update_tool_label(tool_item, tool_id, selected)
            total += selected
        self._count_label.setText(f"Selected ({total})")


def _category_label(
    cat_id: str | None, categories_by_id: dict[str, Category]
) -> str:
    if cat_id is None:
        return "Other"
    cat = categories_by_id.get(cat_id)
    return cat.name if cat is not None else cat_id


def _count_leaves(node: QTreeWidgetItem) -> int:
    if node.data(0, _NODE_KIND_ROLE) == _NODE_SENSOR:
        return 1
    count = 0
    for i in range(node.childCount()):
        child = node.child(i)
        if child is None:
            continue
        count += _count_leaves(child)
    return count


def _count_checked_leaves(node: QTreeWidgetItem) -> int:
    if node.data(0, _NODE_KIND_ROLE) == _NODE_SENSOR:
        return 1 if node.checkState(0) == Qt.CheckState.Checked else 0
    count = 0
    for i in range(node.childCount()):
        child = node.child(i)
        if child is None:
            continue
        count += _count_checked_leaves(child)
    return count


def _make_category_item(parent: QTreeWidgetItem, label: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, [label])
    item.setData(0, _NODE_KIND_ROLE, _NODE_CATEGORY)
    item.setFlags(
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsUserCheckable
        | Qt.ItemFlag.ItemIsAutoTristate
    )
    item.setCheckState(0, Qt.CheckState.Unchecked)
    return item


def _make_sensor_leaf(
    parent: QTreeWidgetItem,
    *,
    tool_id: str,
    sensor_name: str,
    label: str,
    description: str,
    units: str,
    kind: str,
) -> QTreeWidgetItem:
    leaf = QTreeWidgetItem(parent, [label])
    leaf.setData(0, _TOOL_ROLE, tool_id)
    leaf.setData(0, _SENSOR_ROLE, sensor_name)
    leaf.setData(0, _NODE_KIND_ROLE, _NODE_SENSOR)
    leaf.setData(0, _SENSOR_KIND_ROLE, kind)
    leaf.setData(0, _DESCRIPTION_ROLE, description)
    leaf.setData(0, _UNITS_ROLE, units)
    leaf.setFlags(
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsSelectable
        | Qt.ItemFlag.ItemIsUserCheckable
    )
    leaf.setCheckState(0, Qt.CheckState.Unchecked)
    return leaf


__all__ = ["SensorPicker"]
