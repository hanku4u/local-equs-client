"""Un-aggregated row-level DuckDB queries for the C5.2 Data Table view.

Sister to ``query_engine.QueryEngine``: same plan input, but the SQL is a
straight ``SELECT … FROM read_parquet`` with no ``time_bucket``. Per-tool
SELECTs are UNION-ed together with explicit NULL padding for sensors a tool
doesn't have, so the result has a stable column set across multiple tools.

``count(plan)`` powers the table's scrollbar range; ``fetch_page(plan, …)``
backs the row paging in the ``DataTableView``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

import duckdb
import pyarrow as pa

from local_equs_client.data_layer._sql import quote_ident, quote_string
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery

logger = logging.getLogger(__name__)


class QueryCancelled(Exception):
    """Raised when ``cancelled()`` returns True during a count / fetch."""


class RawQueryEngine:
    """Executes raw-row DuckDB queries over a :class:`QueryPlan`."""

    def count(
        self,
        plan: QueryPlan,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        is_cancelled = cancelled or (lambda: False)
        if not plan.per_tool_queries:
            return 0
        union_sql = _build_union_sql(plan.per_tool_queries, displayed_columns=())
        sql = f"SELECT COUNT(*) FROM (\n{union_sql}\n)"
        conn = duckdb.connect(":memory:")
        try:
            if is_cancelled():
                raise QueryCancelled()
            (row,) = conn.execute(sql).fetchone() or (0,)
            if is_cancelled():
                raise QueryCancelled()
            return int(row)
        finally:
            conn.close()


def _displayed_columns(queries: list[ToolQuery]) -> tuple[str, ...]:
    """Union of raw columns across every per-tool query, sorted alphabetically."""
    seen: set[str] = set()
    for q in queries:
        seen.update(q.raw_columns)
    return tuple(sorted(seen))


def _build_per_tool_select(
    query: ToolQuery, displayed_columns: tuple[str, ...]
) -> str:
    """One SELECT, padding missing sensors with NULL so all branches align."""
    own = set(query.raw_columns)
    cols = [f"{quote_string(query.tool_id)} AS tool_id", "ts"]
    for col in displayed_columns:
        if col in own:
            cols.append(quote_ident(col))
        else:
            cols.append(f"NULL AS {quote_ident(col)}")
    paths_sql = ", ".join(quote_string(str(p)) for p in query.file_paths)
    start_sql = quote_string(
        query.time_range.start.strftime("%Y-%m-%d %H:%M:%S.%f")
    )
    end_sql = quote_string(
        query.time_range.end.strftime("%Y-%m-%d %H:%M:%S.%f")
    )
    return (
        f"SELECT {', '.join(cols)}\n"
        f"FROM read_parquet([{paths_sql}], union_by_name = TRUE)\n"
        f"WHERE ts >= TIMESTAMP {start_sql} AND ts < TIMESTAMP {end_sql}"
    )


def _build_union_sql(
    queries: list[ToolQuery],
    displayed_columns: tuple[str, ...],
) -> str:
    parts = [
        _build_per_tool_select(q, displayed_columns) for q in queries if q.file_paths
    ]
    return "\nUNION ALL\n".join(parts)


__all__ = ["QueryCancelled", "RawQueryEngine"]
