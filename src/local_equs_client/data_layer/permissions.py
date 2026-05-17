"""``is_admin()`` permissions gate (C5.7).

UI code that conditionally exposes admin affordances (Categories tab,
Mapping Editor full edit, etc.) MUST call :func:`is_admin` rather than
reading the underlying setting directly. That keeps the call sites
unchanged when the v1 simulate-flag implementation is replaced by a real
auth source.

Integration seam for the real implementation:

- Inject a callable via :func:`set_admin_check` from ``main.py`` once a
  real ``AuthClient`` (e.g. SSO group lookup, server-issued JWT scope)
  exists. The callable returns ``bool``.
- Until that callable is registered, :func:`is_admin` falls back to
  ``Settings.permissions_simulate_admin``.

Threading: the registered callable is invoked synchronously on the
calling thread. Real implementations should cache cheaply.
"""

from __future__ import annotations

from collections.abc import Callable

from local_equs_client.config.settings import get_settings

_admin_check: Callable[[], bool] | None = None


def is_admin() -> bool:
    """Return True if the current user has admin permissions.

    v1: reads ``Settings.permissions_simulate_admin``. Future revisions
    will replace the implementation via :func:`set_admin_check`.
    """
    if _admin_check is not None:
        return bool(_admin_check())
    return bool(get_settings().permissions_simulate_admin)


def set_admin_check(check: Callable[[], bool] | None) -> None:
    """Register the production admin-check callable.

    Pass ``None`` to revert to the simulate-flag fallback (used by tests).
    """
    global _admin_check
    _admin_check = check


__all__ = ["is_admin", "set_admin_check"]
