"""Sensor picker (C1.4 flat list, C3.3 tree mode, C3.4+ search/detail/saved sets).

C3.3 (this revision) replaces the M1 flat list with a tree:

::

    Tool A
        Category 1
            chamber_pressure
            rf_power
        Category 2
            ...
    Tool B
        ...

- Tools come from ``LocalLibrary.all_files()`` (only tools with files locally).
- Each tool's prc_group is resolved through ``MetadataCache.prc_group_for``.
- Categories + canonical sensors come from ``MetadataCache``.
- Tools with no cached metadata appear as childless rows so the user knows the
  tool exists but the picker can't expand it yet.

Tri-state checkboxes propagate up to category and tool nodes; the tool header
shows ``Tool X (selected/total)``. Multi-select on canonical leaves writes to
``SelectionModel.tools`` and ``SelectionModel.sensors_canonical`` atomically;
the planner takes the Cartesian product per C3.2.

A debounced (150ms) filter matches case-insensitively across canonical name,
units, description, and tool id; non-matching leaves hide and any branch with
no visible descendants collapses to ``setHidden(True)``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
)
from local_equs_client.selection.selection_model import SelectionModel

_FILTER_DEBOUNCE_MS = 150
_TOOL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_SENSOR_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_NODE_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 3
_NODE_TOOL = "tool"
_NODE_CATEGORY = "category"
_NODE_SENSOR = "sensor"
_NODE_OTHER = "category"  # uncategorized sensors live under a synthetic "Other"


class SensorPicker(QWidget):
    """Tree picker grouping canonical sensors by tool → category → leaf."""

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

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        layout.addWidget(self._tree)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_FILTER_DEBOUNCE_MS)

    def _wire(self) -> None:
        self._filter_edit.textChanged.connect(self._filter_timer.start)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._clear_btn.clicked.connect(self._on_clear)

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

        if not sensors:
            return

        # Group canonicals by category id; sensors with unknown / missing
        # category land in a synthetic "Other" bucket.
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
            cat_item = QTreeWidgetItem(tool_item, [cat_label])
            cat_item.setData(0, _NODE_KIND_ROLE, _NODE_CATEGORY)
            cat_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            cat_item.setCheckState(0, Qt.CheckState.Unchecked)

            for sensor in sorted(by_category[cat_id], key=lambda s: s.name):
                label = sensor.name
                if sensor.units:
                    label += f" ({sensor.units})"
                leaf = QTreeWidgetItem(cat_item, [label])
                leaf.setData(0, _TOOL_ROLE, tool_id)
                leaf.setData(0, _SENSOR_ROLE, sensor.name)
                leaf.setData(0, _NODE_KIND_ROLE, _NODE_SENSOR)
                leaf.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                leaf.setCheckState(0, Qt.CheckState.Unchecked)

    # --- Filter ----------------------------------------------------------

    def _apply_filter(self) -> None:
        needle = self._filter_edit.text().casefold().strip()
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            tool_visible = self._filter_branch(tool_item, needle)
            tool_item.setHidden(not tool_visible)

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
            return True  # show empty tool nodes when no filter is active
        node.setHidden(not any_visible)
        return any_visible

    def _matches_sensor(self, leaf: QTreeWidgetItem, needle: str) -> bool:
        text = leaf.text(0).casefold()
        if needle in text:
            return True
        tool_id = str(leaf.data(0, _TOOL_ROLE) or "")
        if needle in tool_id.casefold():
            return True
        return False

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
        total_selected = 0
        for tool_idx in range(self._tree.topLevelItemCount()):
            tool_item = self._tree.topLevelItem(tool_idx)
            if tool_item is None:
                continue
            tool_id = str(tool_item.data(0, _TOOL_ROLE) or "")
            selected_in_tool = self._collect_selected_leaves(tool_item, tools, canonicals)
            self._update_tool_label(tool_item, tool_id, selected_in_tool)
            total_selected += selected_in_tool

        self._suppress_push = True
        try:
            self._model.set_tools(tuple(sorted(tools)))
            self._model.set_sensors_canonical(tuple(sorted(canonicals)))
        finally:
            self._suppress_push = False
        self._count_label.setText(f"Selected ({total_selected})")

    def _collect_selected_leaves(
        self,
        node: QTreeWidgetItem,
        tools: set[str],
        canonicals: set[str],
    ) -> int:
        kind = node.data(0, _NODE_KIND_ROLE)
        if kind == _NODE_SENSOR:
            if node.checkState(0) == Qt.CheckState.Checked:
                tool_id = str(node.data(0, _TOOL_ROLE) or "")
                sensor = str(node.data(0, _SENSOR_ROLE) or "")
                if tool_id and sensor:
                    tools.add(tool_id)
                    canonicals.add(sensor)
                    return 1
            return 0
        count = 0
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            count += self._collect_selected_leaves(child, tools, canonicals)
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
        sensors = set(self._model.sensors_canonical)

        self._tree.blockSignals(True)
        try:
            for tool_idx in range(self._tree.topLevelItemCount()):
                tool_item = self._tree.topLevelItem(tool_idx)
                if tool_item is None:
                    continue
                self._sync_branch(tool_item, tools, sensors)
        finally:
            self._tree.blockSignals(False)

        # Recompute counts based on the synced state.
        self._refresh_counts()

    def _sync_branch(
        self, node: QTreeWidgetItem, tools: set[str], sensors: set[str]
    ) -> None:
        kind = node.data(0, _NODE_KIND_ROLE)
        if kind == _NODE_SENSOR:
            tool_id = str(node.data(0, _TOOL_ROLE) or "")
            sensor = str(node.data(0, _SENSOR_ROLE) or "")
            should = tool_id in tools and sensor in sensors
            node.setCheckState(0, Qt.CheckState.Checked if should else Qt.CheckState.Unchecked)
            return
        for child_idx in range(node.childCount()):
            child = node.child(child_idx)
            if child is None:
                continue
            self._sync_branch(child, tools, sensors)

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


__all__ = ["SensorPicker"]
