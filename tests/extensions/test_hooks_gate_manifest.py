"""Issue #91 — ``contributes.hooks`` manifest loader gate ORDERING.

Trust gate (v1 declarative): ``[[contributes.hooks]]`` requires
``capabilities.shell_exec = true``. The refusal itself was already covered
(``tests/subprocess_hooks/test_subprocess_hooks.py::
test_loader_hooks_without_shell_exec_errors``) — but only for a hooks-ONLY
plugin, which has no ``[plugin.entry] python`` and therefore no code to run.
A hooks plugin WITH a python entry had its module imported and its
``setup()`` executed before being refused, because the gate sat in
``_invoke_factory`` while the sibling ``ui_tui_trusted`` gate had been
hoisted into ``_resolve_factory``.

This module mirrors ``test_tui_widgets_manifest.py`` one-for-one: the
load-bearing assertions are NEGATIVE and observed through marker files, so
they distinguish "refused" from "refused BEFORE executing anything".
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions import loader as loader_mod
from aelix_coding_agent.extensions.loader import (
    ExtensionManifestError,
    activate_pending_extension,
    discover_and_load_extensions,
)


def _manifest(
    module_name: str,
    *,
    capabilities: str,
    activation: str = "on_startup_finished = true",
) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "hook-gate-plug"
        name = "Hook Gate Plugin"
        version = "0.1.0"
        description = "Declares subprocess hooks"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/hook-gate-plug"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        {capabilities}

        [activation]
        {activation}

        [[contributes.hooks]]
        event = "tool_call"
        command = "cat"
        timeout_ms = 2000
    """).strip()


def _module_src(import_marker: Path, setup_marker: Path) -> str:
    return textwrap.dedent(f"""
        from pathlib import Path

        Path({str(import_marker)!r}).write_text("IMPORTED")

        def setup(aelix):
            Path({str(setup_marker)!r}).write_text("SETUP")
    """)


async def _load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_name: str,
    capabilities: str,
    activation: str = "on_startup_finished = true",
):
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "hook-gate-plug"
    pkg.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(
        _manifest(module_name, capabilities=capabilities, activation=activation),
        encoding="utf-8",
    )
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    (tmp_path / f"{module_name}.py").write_text(
        _module_src(import_marker, setup_marker), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    return loaded, import_marker, setup_marker


async def test_hooks_without_shell_exec_fails_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="hook_gate_mod_denied",
        capabilities="",  # no [capabilities] → shell_exec defaults false
    )
    assert loaded.extensions == []
    assert len(loaded.errors) == 1
    assert "shell_exec" in loaded.errors[0].error
    # The refusal is now raised in _resolve_factory, so it lands in the
    # ``path=<entry>`` handler rather than the ``path=<name>`` one. Label the
    # entry by plugin id, exactly like the sibling handler: cli/entry.py
    # prints the whole ExtensionLoadError to stderr verbatim.
    assert loaded.errors[0].path == "hook-gate-plug"
    # Data before code (issue #91): the gate fires BEFORE the entry-module
    # import, so a denied plugin executes NOTHING — not even module top-level
    # code, and certainly not setup(). The exception alone would only prove
    # refusal, not that refusal came FIRST.
    assert not import_marker.exists()
    assert not setup_marker.exists()


async def test_hooks_with_shell_exec_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="hook_gate_mod_allowed",
        capabilities="[capabilities]\nshell_exec = true",
    )
    assert loaded.errors == []
    assert import_marker.exists() and setup_marker.exists()
    (ext,) = loaded.extensions
    assert ext.manifest is not None
    (contrib,) = ext.manifest.contributes.hooks
    assert contrib.event == "tool_call"
    # The declared hook actually wired (the gate did not merely stop failing).
    assert len(ext.handlers["tool_call"]) == 1


async def test_hooks_gate_also_guards_lazy_activation_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression fence: contributes.hooks forces eager today
    (``_is_lazy_eligible``), but if eligibility ever regresses (simulated
    here), the _resolve_factory gate must still deny the plugin AT
    ACTIVATION — before its module imports."""
    monkeypatch.setattr(loader_mod, "_is_lazy_eligible", lambda _m: True)
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="hook_gate_mod_lazy",
        capabilities="",  # gate-denied
        activation='on_command = ["gate-cmd"]',
    )
    # Eligibility was forced True → the plugin DEFERRED (no load error).
    assert loaded.errors == []
    assert "hook-gate-plug" in loaded.runtime.pending_activations
    assert not import_marker.exists()
    with pytest.raises(ExtensionManifestError, match="shell_exec"):
        await activate_pending_extension(loaded.runtime, "hook-gate-plug")
    # The gate fired before the import on the lazy path too.
    assert not import_marker.exists()
    assert not setup_marker.exists()


async def test_hooks_pack_is_eager_not_lazy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hoist must not change WHICH packs defer: ``contributes.hooks``
    keeps a pure-``on_command`` plugin eager, so its gate error stays a
    visible load-time failure rather than a mid-session dispatch error."""
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="hook_gate_mod_eager",
        capabilities="",
        activation='on_command = ["gate-cmd"]',
    )
    assert loaded.runtime.pending_activations == {}
    assert len(loaded.errors) == 1
    assert "shell_exec" in loaded.errors[0].error
    assert not import_marker.exists()
    assert not setup_marker.exists()


async def test_refusal_does_not_leak_manifest_payload_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #91 review: the refusal message must not dump the manifest.

    Hoisting the hooks gate into ``_resolve_factory`` moved its failure into
    the ``ExtensionLoadError(path=str(entry), ...)`` handler, and ``entry`` is
    a ``_ManifestEntry`` carrying the whole parsed ``PluginManifest`` —
    including ``contributes.mcp_servers[].env``, which holds plugin-supplied
    API tokens. ``cli/entry.py`` prints that dataclass to stderr verbatim, so
    an unredacted repr puts a token into terminal scrollback, CI logs and any
    pasted bug report. Both the entry label AND ``_ManifestEntry.__repr__``
    are pinned here.
    """
    secret = "ghp_TESTONLY_MANIFEST_SECRET_0123456789"
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "leaky"
    pkg.mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(
        textwrap.dedent(f"""
            [plugin]
            id = "leaky"
            name = "Leaky"
            version = "0.1.0"
            description = "declares a secret-bearing mcp server"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/leaky"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "leaky_gate_mod:setup"

            [capabilities]
            shell_exec = false

            [activation]
            on_startup_finished = true

            [[contributes.mcp_servers]]
            name = "secretsrv"
            transport = "stdio"
            command = "node"
            [contributes.mcp_servers.env]
            GITHUB_TOKEN = "{secret}"

            [[contributes.hooks]]
            event = "tool_call"
            command = "cat"
            timeout_ms = 2000
        """).strip(),
        encoding="utf-8",
    )
    import_marker = tmp_path / "leaky.imported"
    (tmp_path / "leaky_gate_mod.py").write_text(
        _module_src(import_marker, tmp_path / "leaky.setup"), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "no_global"
    )

    (err,) = loaded.errors
    assert err.path == "leaky"
    assert "shell_exec" in err.error
    assert not import_marker.exists()
    # This is the exact string cli/entry.py writes to stderr.
    rendered = f"Warning: extension load: {err}"
    assert secret not in rendered
    assert "PluginManifest(" not in rendered
    # …and the carrier itself stays redacted wherever it is stringified.
    manifest = loader_mod._load_manifest_from_dir(pkg)
    assert manifest is not None
    assert manifest.contributes.mcp_servers[0].env["GITHUB_TOKEN"] == secret
    entry = loader_mod._ManifestEntry(manifest=manifest, pkg_dir=pkg)
    assert secret not in repr(entry)
    assert "leaky" in repr(entry)


async def test_both_declarative_gates_deny_before_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering property pinned directly, for BOTH gate families at once:
    a manifest denied by either gate imports nothing.

    Parametrised inline rather than via ``pytest.mark.parametrize`` so the
    two families are compared in one assertion block — if a future
    ``contributes.*`` family is added with its gate in the wrong phase, the
    asymmetry this test encodes is what catches it.
    """
    for module_name, capability, contribution in (
        (
            "both_gate_widget",
            "ui_tui_trusted",
            'tui_widgets = [{ slot = "above_editor", '
            'factory = "both_gate_widget:make" }]',
        ),
        (
            "both_gate_hook",
            "shell_exec",
            "",  # hooks are declared as an array-of-tables below
        ),
    ):
        cwd = tmp_path / module_name
        pkg = cwd / ".aelix" / "extensions" / "p"
        pkg.mkdir(parents=True)
        hooks_block = (
            ""
            if contribution
            else '\n[[contributes.hooks]]\nevent = "tool_call"\n'
            'command = "cat"\ntimeout_ms = 2000\n'
        )
        pkg.joinpath("aelix-plugin.toml").write_text(
            textwrap.dedent(f"""
                [plugin]
                id = "{module_name.replace("_", "-")}"
                name = "Gate {module_name}"
                version = "0.1.0"
                description = "gate ordering"
                authors = ["Test <test@example.com>"]
                repository = "https://github.com/example/{module_name}"
                license = "MIT"

                [plugin.api]
                level = 1
                min_level = 1

                [plugin.entry]
                python = "{module_name}:setup"

                [activation]
                on_startup_finished = true

                [contributes]
                {contribution}
            """).strip()
            + hooks_block,
            encoding="utf-8",
        )
        import_marker = tmp_path / f"{module_name}.imported"
        setup_marker = tmp_path / f"{module_name}.setup"
        (tmp_path / f"{module_name}.py").write_text(
            _module_src(import_marker, setup_marker)
            + "\ndef make(tui, theme):\n    raise AssertionError('no')\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        loaded = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=tmp_path / "no_global"
        )
        assert len(loaded.errors) == 1, (module_name, loaded.errors)
        assert capability in loaded.errors[0].error
        assert not import_marker.exists(), f"{module_name} imported before refusal"
        assert not setup_marker.exists(), f"{module_name} ran setup before refusal"
