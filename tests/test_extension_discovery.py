"""Sprint 5a (Phase 3.1) — directory-scan extension discovery (P-21, §A).

Covers :func:`aelix_coding_agent.extensions.loader.discover_and_load_extensions`:

- Project-local (``cwd/.aelix/extensions/``) takes precedence over global
  (``~/.aelix/extensions/``).
- Explicit configured paths land after both directory tiers.
- Subdirectories with ``pyproject.toml [tool.aelix] extensions=[...]`` are
  expanded per the manifest; subdirectories with ``__init__.py`` are loaded
  as packages.
- Per-extension errors are contained (one bad file never aborts the wave).
- Dedup by resolved absolute path.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from aelix_coding_agent.extensions.loader import discover_and_load_extensions

_SETUP_PY = textwrap.dedent(
    """\
    def setup(aelix):
        aelix.register_flag({flag!r}, type="bool", default=True)
    """
)


def _write_ext(path: Path, flag: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SETUP_PY.format(flag=flag))


async def test_discover_project_local_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    _write_ext(cwd / ".aelix" / "extensions" / "ext_a.py", "project_flag")

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) == 0
    assert len(result.extensions) == 1
    assert "project_flag" in result.extensions[0].flags


async def test_discover_global_directory_only(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    home = tmp_path / "home"
    _write_ext(home / "extensions" / "ext_g.py", "global_flag")

    result = await discover_and_load_extensions([], cwd=cwd, agent_dir=home)
    assert len(result.errors) == 0
    assert len(result.extensions) == 1
    assert "global_flag" in result.extensions[0].flags


async def test_discover_project_and_global_both_load(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    _write_ext(cwd / ".aelix" / "extensions" / "ext_a.py", "project_flag")
    home = tmp_path / "home"
    _write_ext(home / "extensions" / "ext_b.py", "global_flag")

    result = await discover_and_load_extensions([], cwd=cwd, agent_dir=home)
    assert len(result.errors) == 0
    flags = {f for ext in result.extensions for f in ext.flags}
    assert {"project_flag", "global_flag"} <= flags


async def test_discover_dedup_by_resolved_path(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    _write_ext(cwd / ".aelix" / "extensions" / "ext.py", "only_once")
    # Reference the same file via explicit configured path — should dedup.
    explicit = cwd / ".aelix" / "extensions" / "ext.py"

    result = await discover_and_load_extensions(
        [explicit], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) == 0
    # Single extension loaded despite double mention.
    assert len(result.extensions) == 1


async def test_discover_explicit_path_loads(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    ext_file = tmp_path / "external.py"
    _write_ext(ext_file, "explicit_flag")

    result = await discover_and_load_extensions(
        [ext_file], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) == 0
    assert any("explicit_flag" in ext.flags for ext in result.extensions)


async def test_discover_per_extension_error_isolated(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    good = tmp_path / "good.py"
    _write_ext(good, "good")
    bad = tmp_path / "bad.py"
    bad.write_text("raise RuntimeError('boom')")

    result = await discover_and_load_extensions(
        [good, bad], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) >= 1
    assert any("boom" in e.error for e in result.errors)
    # The good one still loaded.
    assert any("good" in ext.flags for ext in result.extensions)


async def test_discover_pyproject_manifest_expansion(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "main.py").write_text(_SETUP_PY.format(flag="manifest_flag"))
    (pkg / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.aelix]
            extensions = ["main.py"]
            """
        )
    )

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) == 0
    assert any("manifest_flag" in ext.flags for ext in result.extensions)


async def test_discover_init_py_fallback(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "initpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(_SETUP_PY.format(flag="init_flag"))

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.errors) == 0
    assert any("init_flag" in ext.flags for ext in result.extensions)


async def test_discover_subdir_with_no_manifest_or_init_is_skipped(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "proj"
    skipped = cwd / ".aelix" / "extensions" / "ignored"
    skipped.mkdir(parents=True)
    (skipped / "notext.txt").write_text("hello")

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert len(result.extensions) == 0
    assert len(result.errors) == 0


async def test_discover_empty_dirs_returns_empty_result(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )
    assert result.extensions == []
    assert result.errors == []


# === Sprint: built-ins prepend + --no-extensions (no_discovery) ============


async def test_prepend_factories_load_before_discovered(tmp_path: Path) -> None:
    """``prepend`` factories register FIRST, before any discovered extension."""

    from aelix_coding_agent.builtin.guardrail import GuardrailExtension
    from aelix_coding_agent.builtin.permission import PermissionExtension

    cwd = tmp_path / "proj"
    _write_ext(cwd / ".aelix" / "extensions" / "ext_p.py", "project_flag")

    result = await discover_and_load_extensions(
        [],
        cwd=cwd,
        agent_dir=tmp_path / "no_global",
        prepend=[GuardrailExtension(), PermissionExtension()],
    )
    assert len(result.errors) == 0
    assert len(result.extensions) == 3
    # Built-ins first; the discovered project extension lands LAST.
    assert "project_flag" not in result.extensions[0].flags
    assert "project_flag" not in result.extensions[1].flags
    assert "project_flag" in result.extensions[-1].flags


async def test_no_discovery_skips_dirs_but_keeps_configured(tmp_path: Path) -> None:
    """``no_discovery`` skips dir auto-scan (Pi noExtensions) but still loads
    explicit configured paths + prepended built-ins."""

    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    cwd = tmp_path / "proj"
    _write_ext(cwd / ".aelix" / "extensions" / "ext_local.py", "local_flag")
    home = tmp_path / "home"
    _write_ext(home / "extensions" / "ext_global.py", "global_flag")
    explicit = tmp_path / "explicit" / "ext_e.py"
    _write_ext(explicit, "explicit_flag")

    result = await discover_and_load_extensions(
        [str(explicit)],
        cwd=cwd,
        agent_dir=home,
        prepend=[GuardrailExtension()],
        no_discovery=True,
    )
    assert len(result.errors) == 0
    flags = {f for ext in result.extensions for f in ext.flags}
    # Auto-discovered dirs are skipped...
    assert "local_flag" not in flags
    assert "global_flag" not in flags
    # ...but the explicit -e path still loads (Pi keeps cliEnabledExtensions).
    assert "explicit_flag" in flags
