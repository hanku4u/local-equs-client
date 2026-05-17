"""CSV and PNG export actions (C5.8, C5.9).

`write_chart_csv` produces a wide, tool-prefixed CSV from the chart grid's
last query result. `write_table_csv` streams the full data-table result
through `RawQueryEngine`, paging to bound memory. Time values are written
as ISO-8601 UTC; nanosecond-precision timestamps are formatted by hand to
preserve precision past the `datetime.datetime` microsecond ceiling.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa

from local_equs_client.data_layer.query_engine import QueryError

if TYPE_CHECKING:
    from local_equs_client.data_layer.query_planner import QueryPlan
    from local_equs_client.data_layer.raw_query_engine import RawQueryEngine


def write_chart_csv(
    path: Path,
    results: dict[str, pa.Table | QueryError],
) -> None:
    """Write the chart's last results as a wide, ts-aligned CSV.

    Columns are ``ts`` followed by ``{tool_id}.{sensor}`` for every sensor
    column on every tool's :class:`pyarrow.Table`. ``QueryError`` entries
    are skipped silently — failed tools shouldn't poison the file.
    """
    good: dict[str, pa.Table] = {
        tid: t for tid, t in results.items() if isinstance(t, pa.Table)
    }
    sensor_cols: list[tuple[str, str]] = []  # (tool_id, sensor)
    for tool_id in sorted(good):
        ts_name = _time_column_name(good[tool_id])
        for col in good[tool_id].column_names:
            if col == ts_name:
                continue
            sensor_cols.append((tool_id, col))

    rows_by_ts: dict[str, dict[tuple[str, str], object]] = {}
    for tool_id, table in good.items():
        ts_name = _time_column_name(table)
        ts_col = table.column(ts_name)
        for i in range(table.num_rows):
            ts_str = _format_ts(ts_col[i])
            row = rows_by_ts.setdefault(ts_str, {})
            for col in table.column_names:
                if col == ts_name:
                    continue
                row[(tool_id, col)] = _format_value(table.column(col)[i])

    header = ["ts", *[f"{tid}.{col}" for tid, col in sensor_cols]]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for ts in sorted(rows_by_ts):
            row = rows_by_ts[ts]
            writer.writerow([ts, *[row.get(key, "") for key in sensor_cols]])


def write_table_csv(
    path: Path,
    plan: QueryPlan,
    engine: RawQueryEngine,
    *,
    page_size: int = 10_000,
) -> None:
    """Stream the full table result for ``plan`` to ``path``.

    Columns: ``tool_id, ts, <sorted union of raw sensor columns>``. Rows
    are fetched in pages of ``page_size`` and written incrementally so
    multi-million-row exports stay bounded in memory.
    """
    raw_columns: list[str] = sorted({
        col for q in plan.per_tool_queries for col in q.raw_columns
    })
    header = ["tool_id", "ts", *raw_columns]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        if not plan.per_tool_queries:
            return
        total = engine.count(plan)
        offset = 0
        while offset < total:
            page = engine.fetch_page(plan, offset=offset, limit=page_size)
            if page.num_rows == 0:
                break
            _write_page(writer, page, header)
            offset += page.num_rows


def _write_page(
    writer: csv._writer,  # type: ignore[name-defined]
    page: pa.Table,
    header: list[str],
) -> None:
    columns = {name: page.column(name) for name in page.column_names}
    for i in range(page.num_rows):
        row: list[str] = []
        for col_name in header:
            arr = columns.get(col_name)
            if arr is None:
                row.append("")
                continue
            scalar = arr[i]
            row.append(_format_ts(scalar) if col_name == "ts" else _format_value(scalar))
        writer.writerow(row)


def _time_column_name(table: pa.Table) -> str:
    """Chart query results use ``bucket``; raw queries use ``ts``."""
    for candidate in ("bucket", "ts"):
        if candidate in table.column_names:
            return candidate
    return str(table.column_names[0])


def _format_ts(scalar: pa.Scalar) -> str:
    if not scalar.is_valid:
        return ""
    if pa.types.is_timestamp(scalar.type) and scalar.type.unit == "ns":
        ns_value = int(scalar.value)
        seconds, frac_ns = divmod(ns_value, 1_000_000_000)
        dt = datetime.fromtimestamp(seconds, tz=UTC)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{frac_ns:09d}+00:00"
    value = scalar.as_py()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return "" if value is None else str(value)


def _format_value(scalar: pa.Scalar) -> str:
    if not scalar.is_valid:
        return ""
    value = scalar.as_py()
    return "" if value is None else str(value)


__all__ = ["write_chart_csv", "write_table_csv"]
