"""Unit tests for the CLI argument parser in main.py (C6.6)."""

from __future__ import annotations

import pytest

from local_equs_client.main import _parse_args


def test_parser_defaults_induce_crash_false() -> None:
    args = _parse_args([])
    assert args.induce_crash is False


def test_parser_accepts_induce_crash_flag() -> None:
    args = _parse_args(["--induce-crash"])
    assert args.induce_crash is True


def test_parser_rejects_unknown_flag() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--no-such-flag"])
