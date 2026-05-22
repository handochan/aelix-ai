"""Sprint 6h₈ (Phase 5a-iv, ADR-0092, §C) — ``migrations.py`` smoke tests.

Aelix has no legacy data so :func:`run_migrations` is a no-op stub that
returns the Pi-shaped ``{"migrated_auth_providers": [],
"deprecation_warnings": []}`` dict. Tests verify shape + idempotency.

Pi citation: ``packages/coding-agent/src/migrations.ts:1-315`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

from pathlib import Path

from aelix_ai.migrations import run_migrations, show_deprecation_warnings


async def test_run_migrations_returns_empty_result_dict() -> None:
    """Result has Pi-shape with two empty lists."""

    result = await run_migrations(".")
    assert result == {"migrated_auth_providers": [], "deprecation_warnings": []}


async def test_run_migrations_accepts_path_or_str(tmp_path: Path) -> None:
    """Both ``str`` and :class:`Path` ``cwd`` arguments are accepted."""

    a = await run_migrations(str(tmp_path))
    b = await run_migrations(tmp_path)
    assert a == b == {"migrated_auth_providers": [], "deprecation_warnings": []}


async def test_run_migrations_idempotent() -> None:
    """Repeat calls produce the same result dict."""

    a = await run_migrations(".")
    b = await run_migrations(".")
    assert a == b


async def test_show_deprecation_warnings_no_op() -> None:
    """``show_deprecation_warnings`` is a no-op (no exception, no return)."""

    result = await show_deprecation_warnings([])
    assert result is None
    result2 = await show_deprecation_warnings(["x", "y", "z"])
    assert result2 is None
