"""Sprint 6h₉b — manifest v1 loader integration tests.

Covers the 14 scenarios enumerated in the Sprint 6h₉b spec §3.6:

1.  Happy path: ``aelix-plugin.toml`` + python file → loaded with manifest.
2.  Legacy ``pyproject.toml [tool.aelix]`` path unchanged.
3.  Legacy ``__init__.py`` path unchanged.
4.  Priority: ``aelix-plugin.toml`` wins over ``pyproject.toml``.
5.  ``module:callable`` form resolves to the named callable (not ``setup``).
6.  Bare-module legacy form still resolves to ``setup``.
7.  Invalid ``module:callable`` form (empty side) raises ``ValueError``.
8.  Missing ``[plugin.entry] python`` when required raises ``ValueError``.
9.  ``min_level > AELIX_API_LEVEL`` rejected as ``ExtensionLoadError``.
10. ``level > AELIX_API_LEVEL`` warns and loads.
11. License outside whitelist warns and loads (Phase 5b warn-only).
12. Malformed TOML surfaces as ``ExtensionLoadError`` without aborting
    the wave.
13. Pydantic validation error (missing required field) surfaces as
    ``ExtensionLoadError``.
14. Activation / capabilities / contributes round-trip onto the loaded
    ``Extension.manifest``.
"""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.loader import (
    _factory_from_module,
    discover_and_load_extensions,
)

# === Fixtures / helpers ===


def _write_plugin_dir(
    parent: Path,
    *,
    name: str,
    manifest_toml: str | None,
    module_name: str | None = None,
    module_src: str | None = None,
    pyproject_extensions: list[str] | None = None,
    init_py: str | None = None,
) -> Path:
    """Create a plugin directory under ``parent/.aelix/extensions/<name>/``.

    Returns the directory path. Helper centralises the boilerplate so each
    test focuses on the asserted behaviour.
    """

    pkg_dir = parent / ".aelix" / "extensions" / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    if manifest_toml is not None:
        (pkg_dir / "aelix-plugin.toml").write_text(manifest_toml, encoding="utf-8")

    if module_name and module_src is not None:
        # Place the module on sys.path so ``importlib.import_module`` can
        # resolve it. Each test uses a unique module name to avoid the
        # import cache leaking state across tests.
        module_file = parent / f"{module_name}.py"
        module_file.write_text(module_src, encoding="utf-8")
        sys.path.insert(0, str(parent))

    if pyproject_extensions is not None:
        body_lines = ["[tool.aelix]"]
        body_lines.append("extensions = [")
        for entry in pyproject_extensions:
            body_lines.append(f"    {entry!r},")
        body_lines.append("]")
        (pkg_dir / "pyproject.toml").write_text(
            "\n".join(body_lines) + "\n", encoding="utf-8"
        )

    if init_py is not None:
        (pkg_dir / "__init__.py").write_text(init_py, encoding="utf-8")

    return pkg_dir


def _cleanup_sys_path(parent: Path) -> None:
    """Remove ``parent`` from ``sys.path`` after a test installs it."""

    while str(parent) in sys.path:
        sys.path.remove(str(parent))


_VALID_MANIFEST = textwrap.dedent("""
    [plugin]
    id = "my-plugin"
    name = "My Plugin"
    version = "0.1.0"
    description = "Test plugin"
    authors = ["Test <test@example.com>"]
    repository = "https://github.com/example/my-plugin"
    license = "MIT"

    [plugin.api]
    level = 1
    min_level = 1

    [plugin.entry]
    python = "{module}:setup"

    [capabilities]
    ui_descriptor = true

    [activation]
    on_startup_finished = true

    [contributes]
    commands = [{{ id = "greet", description = "Say hello" }}]
""").strip()


_SETUP_MODULE = textwrap.dedent("""
    def setup(aelix):
        # Minimal factory — registers no hooks; just confirms invocation.
        aelix.extension.name = aelix.extension.name  # no-op
""")


# === Tests ===


@pytest.mark.asyncio
async def test_happy_path_manifest_loaded(tmp_path: Path) -> None:
    """Test #1: ``aelix-plugin.toml`` + python file → loaded + manifest set."""

    module_name = "aelix_test_plugin_happy"
    _write_plugin_dir(
        tmp_path,
        name="happy",
        manifest_toml=_VALID_MANIFEST.format(module=module_name),
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    try:
        result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        ext = result.extensions[0]
        assert ext.name == "my-plugin"
        assert ext.manifest is not None
        assert ext.manifest.plugin.id == "my-plugin"
    finally:
        _cleanup_sys_path(tmp_path)


@pytest.mark.asyncio
async def test_legacy_pyproject_toml_path_unchanged(tmp_path: Path) -> None:
    """Test #2: ``pyproject.toml [tool.aelix]`` only → ``manifest is None``."""

    pkg_dir = _write_plugin_dir(
        tmp_path,
        name="legacy-pyproject",
        manifest_toml=None,
        pyproject_extensions=["ext.py"],
    )
    (pkg_dir / "ext.py").write_text(_SETUP_MODULE, encoding="utf-8")

    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert result.errors == []
    assert len(result.extensions) == 1
    assert result.extensions[0].manifest is None


@pytest.mark.asyncio
async def test_legacy_init_py_path_unchanged(tmp_path: Path) -> None:
    """Test #3: only ``__init__.py`` → loaded, ``manifest is None``."""

    _write_plugin_dir(
        tmp_path,
        name="legacy-init",
        manifest_toml=None,
        init_py=_SETUP_MODULE,
    )
    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert result.errors == []
    assert len(result.extensions) == 1
    assert result.extensions[0].manifest is None


@pytest.mark.asyncio
async def test_manifest_wins_over_pyproject_toml(tmp_path: Path) -> None:
    """Test #4: both present → manifest path wins, ``manifest is not None``."""

    module_name = "aelix_test_plugin_priority"
    pkg_dir = _write_plugin_dir(
        tmp_path,
        name="priority",
        manifest_toml=_VALID_MANIFEST.format(module=module_name),
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    # ``pyproject.toml`` would have been picked up by the legacy path, but
    # must lose to ``aelix-plugin.toml``.
    (pkg_dir / "pyproject.toml").write_text(
        "[tool.aelix]\nextensions = [\"legacy.py\"]\n", encoding="utf-8"
    )
    (pkg_dir / "legacy.py").write_text(_SETUP_MODULE, encoding="utf-8")

    try:
        result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        ext = result.extensions[0]
        assert ext.manifest is not None
        assert ext.name == "my-plugin"
    finally:
        _cleanup_sys_path(tmp_path)


@pytest.mark.asyncio
async def test_module_colon_callable_form_resolves(tmp_path: Path) -> None:
    """Test #5: ``python = "module:custom"`` → factory is ``custom``, not ``setup``.

    Sprint 6h₉b fold-in §A (W4 MINOR-1 / W5 m1): adds an explicit
    ``RAN_CUSTOM`` side-effect so the test PROVES the colon-form
    callable ran. Previously the test relied on a side-channel ("if
    ``setup`` were resolved instead, AttributeError"); that side channel
    would silently break if someone added a ``setup`` stub.
    """

    module_name = "aelix_test_plugin_colon"
    # Side-effect flag + a poison ``setup`` so wrong-callable resolution
    # would explicitly fail. Together, these two prove the colon-form
    # path resolved ``my_custom_setup``.
    custom_module = textwrap.dedent("""
        RAN_CUSTOM = False
        def my_custom_setup(aelix):
            global RAN_CUSTOM
            RAN_CUSTOM = True
        def setup(aelix):
            raise AssertionError(
                "Wrong callable resolved: ``setup`` should NOT have "
                "been picked up — manifest declared ``my_custom_setup``."
            )
    """)
    # Replace ``setup`` in the rendered manifest with ``my_custom_setup``
    # to exercise the colon-form: ``_VALID_MANIFEST`` hardcodes
    # ``{module}:setup`` and we need ``{module}:my_custom_setup``.
    manifest_toml = _VALID_MANIFEST.format(module=module_name).replace(
        f"{module_name}:setup", f"{module_name}:my_custom_setup"
    )
    _write_plugin_dir(
        tmp_path,
        name="colon",
        manifest_toml=manifest_toml,
        module_name=module_name,
        module_src=custom_module,
    )
    try:
        result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        # Proof that ``my_custom_setup`` ran — not the poison ``setup``.
        import importlib

        mod = importlib.import_module(module_name)
        assert mod.RAN_CUSTOM is True, (
            "Manifest [plugin.entry] python = 'module:my_custom_setup' "
            "did NOT resolve the custom callable; RAN_CUSTOM stayed False."
        )
    finally:
        _cleanup_sys_path(tmp_path)


def test_bare_module_legacy_form_resolves_setup(tmp_path: Path) -> None:
    """Test #6: bare ``_factory_from_module("my_pkg")`` → ``my_pkg.setup``."""

    module_name = "aelix_test_plugin_bare"
    module_file = tmp_path / f"{module_name}.py"
    module_file.write_text(
        "def setup(aelix):\n    pass\n", encoding="utf-8"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        factory = _factory_from_module(module_name)
        assert factory.__name__ == "setup"
    finally:
        _cleanup_sys_path(tmp_path)


def test_invalid_colon_form_raises_value_error() -> None:
    """Test #7: empty module or empty callable → ``ValueError``."""

    with pytest.raises(ValueError, match="module:callable"):
        _factory_from_module(":callable_only")
    with pytest.raises(ValueError, match="module:callable"):
        _factory_from_module("module_only:")


@pytest.mark.asyncio
async def test_missing_entry_python_when_required_raises(tmp_path: Path) -> None:
    """Test #8: capabilities require Python entry but ``[plugin.entry]`` missing.

    The Pydantic ``PluginManifest`` validator rejects this directly
    (Sprint 6h₉a contract), so the loader surfaces it as a manifest
    parse failure (``ExtensionLoadError`` via ``ExtensionManifestError``).
    """

    # ``ui_descriptor = true`` requires ``entry.python`` per
    # ``PluginManifest.validate_entry_python_required_for_python_capabilities``.
    bad_manifest = textwrap.dedent("""
        [plugin]
        id = "no-entry"
        name = "No Entry Plugin"
        version = "0.1.0"
        description = "Test plugin"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/no-entry"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [capabilities]
        ui_descriptor = true

        [activation]
        on_startup_finished = true
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="no-entry",
        manifest_toml=bad_manifest,
    )
    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert result.extensions == []
    assert len(result.errors) == 1
    # Sprint 6h₉b fold-in §A (W4 MINOR-5): tightened — the second clause
    # ``"entry" in result.errors[0].error`` previously matched any error
    # message mentioning the word "entry" (always true for entry-related
    # validation), making the assertion always pass. The Pydantic
    # validator at ``manifest.py`` raises with ``[plugin.entry] python``
    # explicitly in the message; assert against that precise string.
    err_lower = result.errors[0].error.lower()
    assert "entry.python" in err_lower or "[plugin.entry] python" in err_lower, (
        f"Expected ``[plugin.entry] python`` reference in error, got: "
        f"{result.errors[0].error!r}"
    )


@pytest.mark.asyncio
async def test_api_level_min_level_too_high_rejected(tmp_path: Path) -> None:
    """Test #9: ``min_level = 99`` → ``ExtensionLoadError``, plugin NOT loaded."""

    bad_manifest = textwrap.dedent("""
        [plugin]
        id = "future-plugin"
        name = "Future Plugin"
        version = "0.1.0"
        description = "Future API level"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/future"
        license = "MIT"

        [plugin.api]
        level = 99
        min_level = 99

        [plugin.entry]
        python = "irrelevant:setup"

        [activation]
        on_startup_finished = true
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="future",
        manifest_toml=bad_manifest,
    )
    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert result.extensions == []
    assert len(result.errors) == 1
    assert "API_LEVEL" in result.errors[0].error or "99" in result.errors[0].error


@pytest.mark.asyncio
async def test_api_level_forward_warn_and_load(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test #10: ``level = 99, min_level = 1`` → warning logged, loaded."""

    module_name = "aelix_test_plugin_forward"
    forward_manifest = textwrap.dedent(f"""
        [plugin]
        id = "forward-plugin"
        name = "Forward Plugin"
        version = "0.1.0"
        description = "Forward compat"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/forward"
        license = "MIT"

        [plugin.api]
        level = 99
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        [activation]
        on_startup_finished = true
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="forward",
        manifest_toml=forward_manifest,
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    try:
        with caplog.at_level(logging.WARNING, logger="aelix_coding_agent.extensions.loader"):
            result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        # Warning about forward API_LEVEL must be emitted.
        assert any(
            "API_LEVEL" in record.message and "99" in record.message
            for record in caplog.records
        )
    finally:
        _cleanup_sys_path(tmp_path)


@pytest.mark.asyncio
async def test_license_outside_whitelist_warns_and_loads(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test #11: ``license = "Custom-1.0"`` → warning logged, loaded."""

    module_name = "aelix_test_plugin_lic"
    license_manifest = _VALID_MANIFEST.format(module=module_name).replace(
        'license = "MIT"', 'license = "Custom-1.0"'
    )
    _write_plugin_dir(
        tmp_path,
        name="license",
        manifest_toml=license_manifest,
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    try:
        with caplog.at_level(logging.WARNING, logger="aelix_coding_agent.extensions.loader"):
            result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        assert any(
            "license" in record.message.lower() and "Custom-1.0" in record.message
            for record in caplog.records
        )
    finally:
        _cleanup_sys_path(tmp_path)


@pytest.mark.asyncio
async def test_malformed_toml_isolated_to_one_plugin(tmp_path: Path) -> None:
    """Test #12: malformed TOML → one plugin fails, wave continues."""

    # Plugin A: malformed TOML.
    _write_plugin_dir(
        tmp_path,
        name="malformed",
        manifest_toml="this is not valid TOML syntax @@@@\n[",
    )
    # Plugin B: legit __init__.py — must still load.
    _write_plugin_dir(
        tmp_path,
        name="legit",
        manifest_toml=None,
        init_py=_SETUP_MODULE,
    )
    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert len(result.extensions) == 1
    assert len(result.errors) == 1
    assert "Invalid manifest" in result.errors[0].error or "manifest" in result.errors[0].error.lower()


@pytest.mark.asyncio
async def test_pydantic_validation_error_isolated(tmp_path: Path) -> None:
    """Test #13: manifest missing required field → ``ExtensionLoadError``."""

    # Drop the required ``version`` field — Pydantic rejects.
    invalid_manifest = textwrap.dedent("""
        [plugin]
        id = "no-version"
        name = "No Version Plugin"
        description = "Missing version"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/no-version"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "irrelevant:setup"

        [activation]
        on_startup_finished = true
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="invalid",
        manifest_toml=invalid_manifest,
    )
    result = await discover_and_load_extensions([], cwd=tmp_path)
    assert result.extensions == []
    assert len(result.errors) == 1
    err = result.errors[0].error.lower()
    assert "invalid manifest" in err or "version" in err


@pytest.mark.asyncio
async def test_manifest_activation_capabilities_contributes_roundtrip(
    tmp_path: Path,
) -> None:
    """Test #14: round-trip — manifest fields visible on loaded ``Extension``."""

    module_name = "aelix_test_plugin_rt"
    rt_manifest = textwrap.dedent(f"""
        [plugin]
        id = "rt-plugin"
        name = "RT Plugin"
        version = "0.1.0"
        description = "Roundtrip"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/rt"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        [capabilities]
        ui_descriptor = true
        fs_read_user = true

        [activation]
        on_command = ["my-cmd"]

        [contributes]
        commands = [{{ id = "rt-cmd", description = "Roundtrip command" }}]
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="roundtrip",
        manifest_toml=rt_manifest,
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    try:
        result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        manifest = result.extensions[0].manifest
        assert manifest is not None
        assert manifest.activation.on_command == ["my-cmd"]
        assert manifest.capabilities.ui_descriptor is True
        assert manifest.capabilities.fs_read_user is True
        assert len(manifest.contributes.commands) == 1
        assert manifest.contributes.commands[0].id == "rt-cmd"
    finally:
        _cleanup_sys_path(tmp_path)


# === Sprint 6h₉b fold-in §A — W5 m2 boundary test ===


@pytest.mark.asyncio
async def test_api_level_equal_boundary_accepted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test #15 (Sprint 6h₉b fold-in §A): explicit ``==`` boundary on API_LEVEL.

    Sprint 6h₉b fold-in §A (W5 m2): pins ``min_level == AELIX_API_LEVEL``
    and ``level == AELIX_API_LEVEL`` to the accepted side. Without an
    explicit boundary test, a future refactor that flips ``>`` to ``>=``
    at ``loader.py`` would silently break the policy invariant — every
    other test would still pass because they all use ``level=1,
    min_level=1`` which is the boundary itself.

    Policy (ADR-0096 §"API_LEVEL policy"):
        - ``min_level > host`` → REJECT
        - ``level > host`` → WARN + LOAD
        - ``min_level == host`` → ACCEPT silently (this test)
        - ``level == host`` → ACCEPT silently (this test)
    """

    import logging

    from aelix_agent_core.contracts import AELIX_API_LEVEL

    module_name = "aelix_test_plugin_eq_boundary"
    boundary_manifest = textwrap.dedent(f"""
        [plugin]
        id = "boundary-plugin"
        name = "Boundary Plugin"
        version = "0.1.0"
        description = "Boundary test"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/boundary"
        license = "MIT"

        [plugin.api]
        level = {AELIX_API_LEVEL}
        min_level = {AELIX_API_LEVEL}

        [plugin.entry]
        python = "{module_name}:setup"

        [activation]
        on_startup_finished = true
    """).strip()
    _write_plugin_dir(
        tmp_path,
        name="boundary",
        manifest_toml=boundary_manifest,
        module_name=module_name,
        module_src=_SETUP_MODULE,
    )
    try:
        with caplog.at_level(logging.WARNING):
            result = await discover_and_load_extensions([], cwd=tmp_path)
        assert result.errors == []
        assert len(result.extensions) == 1
        # No API_LEVEL forward-compat warning on the equal boundary.
        api_warns = [
            rec for rec in caplog.records
            if "API_LEVEL" in rec.message or "api_level" in rec.message.lower()
        ]
        assert api_warns == [], (
            f"Equal boundary must not log API_LEVEL warning; got {api_warns!r}"
        )
        # Manifest carried through with the boundary values.
        manifest = result.extensions[0].manifest
        assert manifest is not None
        assert manifest.api.level == AELIX_API_LEVEL
        assert manifest.api.min_level == AELIX_API_LEVEL
    finally:
        _cleanup_sys_path(tmp_path)
