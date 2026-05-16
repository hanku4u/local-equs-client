"""Shared SQL string-building helpers for the DuckDB query engines."""

from __future__ import annotations


def quote_string(value: str) -> str:
    """Quote a SQL string literal, escaping embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


def quote_ident(value: str) -> str:
    """Quote a SQL identifier, escaping embedded double quotes."""
    return '"' + value.replace('"', '""') + '"'


__all__ = ["quote_string", "quote_ident"]
