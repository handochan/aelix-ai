"""Sprint P0 #10 — Project Trust (Option A+) tests.

Covers (spec §7):

1. ``cli/args.py`` parse: ``--approve``/``-a``→True, ``--no-approve``/``-na``
   →False, absent→None.
2. ``has_trust_requiring_project_resources`` matrix.
3. ``resolve_project_trusted`` order (override short-circuit / no-resources /
   store hit / headless-deny / prompt-honored / cancel-deny).
4. ``ProjectTrustStore`` get/set + nearest-ancestor + validation.
5. Integration (entry-level, headless): untrusted dir → on-disk extensions NOT
   loaded + project mcp.json NOT connected; ``--approve`` → loaded;
   trusted/no-resources dir → byte-identical regression. End-to-end over-gating
   is covered ONLY for ``$AELIX_MCP_CONFIG``
   (``test_integration_env_mcp_not_gated_in_untrusted_dir``); the ``-e`` and
   entry_points "still load when the project tier is gated" guarantees are
   asserted at the FACTORY level in section 6 (``no_project_local=True`` must
   suppress ONLY tier 1, leaving tier-3 ``-e`` configured paths and tier-4
   entry_points intact) — this catches over-gating regressions the entry-level
   tests do not.

6. Over-gating guards (factory level): ``discover_and_load_extensions(
   no_project_local=True)`` still loads an explicit ``-e`` configured path and
   an entry_points extension — only the auto-discovered project-local tier 1 is
   suppressed.

Patch module OBJECTS (not dotted strings), per the repo convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_coding_agent.cli import config as config_mod
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustPromptResult,
    ProjectTrustStore,
    has_trust_requiring_project_resources,
    project_trust_options,
    resolve_project_trusted,
)

# === 1. args parse ===========================================================


def test_approve_long_sets_true() -> None:
    assert parse_args(["--approve"]).project_trust_override is True


def test_approve_short_sets_true() -> None:
    assert parse_args(["-a"]).project_trust_override is True


def test_no_approve_long_sets_false() -> None:
    assert parse_args(["--no-approve"]).project_trust_override is False


def test_no_approve_short_sets_false() -> None:
    assert parse_args(["-na"]).project_trust_override is False


def test_trust_override_absent_is_none() -> None:
    assert parse_args([]).project_trust_override is None
    assert parse_args(["--print", "hi"]).project_trust_override is None


def test_approve_in_help_text() -> None:
    import io

    from aelix_coding_agent.cli.args import print_help

    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "--approve" in text
    assert "--no-approve" in text


# === 2. has_trust_requiring_project_resources matrix =========================


def test_resources_empty_dir_false(tmp_path: Path) -> None:
    assert has_trust_requiring_project_resources(tmp_path) is False


def test_resources_empty_extensions_dir_false(tmp_path: Path) -> None:
    # An empty .aelix/extensions dir loads nothing → no gate.
    (tmp_path / ".aelix" / "extensions").mkdir(parents=True)
    assert has_trust_requiring_project_resources(tmp_path) is False


def test_resources_extensions_with_file_true(tmp_path: Path) -> None:
    ext = tmp_path / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    (ext / "probe.py").write_text("def setup(aelix):\n    pass\n")
    assert has_trust_requiring_project_resources(tmp_path) is True


def test_resources_mcp_json_true(tmp_path: Path) -> None:
    aelix = tmp_path / ".aelix"
    aelix.mkdir(parents=True)
    (aelix / "mcp.json").write_text('{"mcpServers": {}}')
    assert has_trust_requiring_project_resources(tmp_path) is True


def test_resources_both_true(tmp_path: Path) -> None:
    ext = tmp_path / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    (ext / "probe.py").write_text("def setup(aelix):\n    pass\n")
    (tmp_path / ".aelix" / "mcp.json").write_text('{"mcpServers": {}}')
    assert has_trust_requiring_project_resources(tmp_path) is True


def test_resources_mcp_json_as_dir_is_not_file(tmp_path: Path) -> None:
    # mcp.json must be a FILE; a directory named mcp.json doesn't count.
    (tmp_path / ".aelix" / "mcp.json").mkdir(parents=True)
    assert has_trust_requiring_project_resources(tmp_path) is False


# === 3. resolve_project_trusted order ========================================


def _resources_dir(tmp_path: Path) -> Path:
    """A cwd that HAS trust-requiring resources (so the gate engages)."""

    ext = tmp_path / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    (ext / "probe.py").write_text("def setup(aelix):\n    pass\n")
    return tmp_path


async def test_resolve_override_true_short_circuits(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    called = {"prompt": False}

    async def _prompt(_c: Path) -> ProjectTrustPromptResult | None:
        called["prompt"] = True
        return ProjectTrustPromptResult(trusted=False, remember=False)

    out = await resolve_project_trusted(
        cwd,
        override=True,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
    )
    assert out is True
    assert called["prompt"] is False  # no prompt on override


async def test_resolve_override_false_short_circuits(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=False,
        has_ui=True,
        prompt=None,
        store=ProjectTrustStore(tmp_path / "agent"),
    )
    assert out is False


async def test_resolve_no_resources_trusts(tmp_path: Path) -> None:
    # Empty dir → nothing to gate → trust without prompting.
    out = await resolve_project_trusted(
        tmp_path,
        override=None,
        has_ui=False,
        prompt=None,
        store=ProjectTrustStore(tmp_path / "agent"),
    )
    assert out is True


async def test_resolve_store_hit_returned(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(cwd, True)
    out = await resolve_project_trusted(
        cwd, override=None, has_ui=False, prompt=None, store=store
    )
    assert out is True


async def test_resolve_store_hit_false_returned(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(cwd, False)
    out = await resolve_project_trusted(
        cwd, override=None, has_ui=True, prompt=None, store=store
    )
    assert out is False  # persisted false wins, no prompt


async def test_resolve_headless_denies(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,  # non-interactive → deny-by-default
        prompt=None,
        store=ProjectTrustStore(tmp_path / "agent"),
    )
    assert out is False


async def test_resolve_prompt_honored_and_persisted(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")

    async def _prompt(_c: Path) -> ProjectTrustPromptResult | None:
        return ProjectTrustPromptResult(trusted=True, remember=True)

    out = await resolve_project_trusted(
        cwd, override=None, has_ui=True, prompt=_prompt, store=store
    )
    assert out is True
    # Persisted: a subsequent resolve hits the store without prompting.
    assert store.get(cwd) is True


async def test_resolve_prompt_session_only_not_persisted(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")

    async def _prompt(_c: Path) -> ProjectTrustPromptResult | None:
        return ProjectTrustPromptResult(trusted=True, remember=False)

    out = await resolve_project_trusted(
        cwd, override=None, has_ui=True, prompt=_prompt, store=store
    )
    assert out is True
    # NOT persisted (session only).
    assert store.get(cwd) is None


async def test_resolve_cancel_denies(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)

    async def _prompt(_c: Path) -> ProjectTrustPromptResult | None:
        return None  # Esc / Ctrl+C

    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
    )
    assert out is False


# === 4. ProjectTrustStore get/set + nearest-ancestor + validation ===========


def test_store_get_missing_file_is_none(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    assert store.get(tmp_path / "proj") is None


def test_store_set_then_get_roundtrip(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    proj = tmp_path / "proj"
    proj.mkdir()
    store.set(proj, True)
    assert store.get(proj) is True
    store.set(proj, False)
    assert store.get(proj) is False


def test_store_keys_are_sorted_and_canonical(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    a = tmp_path / "b_proj"
    b = tmp_path / "a_proj"
    a.mkdir()
    b.mkdir()
    store.set(a, True)
    store.set(b, True)
    raw = json.loads((tmp_path / "agent" / "trust.json").read_text())
    keys = list(raw.keys())
    assert keys == sorted(keys)  # sorted on write


def test_store_nearest_ancestor_parent_trusts_child(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    parent = tmp_path / "parent"
    child = parent / "child" / "grandchild"
    child.mkdir(parents=True)
    store.set(parent, True)
    # The child has no direct entry → inherits parent True.
    assert store.get(child) is True


def test_store_child_false_overrides_ancestor_true(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    store.set(parent, True)
    store.set(child, False)
    assert store.get(child) is False  # nearest entry wins
    assert store.get(parent) is True


def test_store_validation_non_object_raises(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "trust.json").write_text("[1, 2, 3]")  # not an object
    store = ProjectTrustStore(agent)
    with pytest.raises(ValueError):
        store.get(tmp_path / "proj")


def test_store_validation_bad_value_raises(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "trust.json").write_text('{"/x": "yes"}')  # value not bool/null
    store = ProjectTrustStore(agent)
    with pytest.raises(ValueError):
        store.get(tmp_path / "proj")


def test_store_null_value_is_undecided(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    key = str(Path(proj).resolve())
    (agent / "trust.json").write_text(json.dumps({key: None}))
    store = ProjectTrustStore(agent)
    # null → undecided → walk continues → None (no ancestor decided).
    assert store.get(proj) is None


def test_store_empty_file_is_empty(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "trust.json").write_text("   ")
    store = ProjectTrustStore(agent)
    assert store.get(tmp_path / "proj") is None


def test_project_trust_options_includes_session_only_and_parent(
    tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    opts = project_trust_options(proj)
    assert "Trust" in opts
    assert "Do not trust" in opts
    assert any("session only" in o for o in opts)
    assert any(o.startswith("Trust parent folder") for o in opts)


# === 5. Integration (entry-level, headless) ==================================


class _FakePipedStdin:
    """Non-tty stdin → ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A clean cwd + isolated agent dir (no real ~/.aelix leakage)."""

    import sys

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    # Avoid the no-model guard interfering: provide an env-authed provider so
    # the run proceeds past the guard to the (mock) turn.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    return tmp_path


async def _run_main(argv: list[str], cwd: Path, monkeypatch) -> int:
    """Run ``_async_main`` with the process cwd set to ``cwd``."""

    monkeypatch.chdir(cwd)
    return await entry_mod._async_main(argv)


async def test_integration_untrusted_headless_skips_project_extension(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """Untrusted dir (resources present, no --approve, non-interactive) →
    project-local on-disk extension is NOT loaded; stderr carries the notice."""

    proj = _isolated_env / "proj"
    ext = proj / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    # A SENTINEL probe: it writes a marker file at IMPORT (``exec_module``) time,
    # THEN raises. The sentinel write is the load-bearing observation — it runs
    # whenever the loader actually ``exec_module``'s this project-local file,
    # i.e. whenever the gate is NOT applied. (The RuntimeError that follows is
    # swallowed by the loader's per-entry try/except into ``result.errors`` and
    # never reaches the extension LIST, so an extension-count assertion alone is
    # NOT a gate-removal detector — only the sentinel is.)
    sentinel = proj / "PROBE_EXECUTED"
    (ext / "probe.py").write_text(
        f"from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('loaded')\n"
        "raise RuntimeError('project-local extension SHOULD NOT load')\n"
    )

    captured: dict[str, object] = {}
    real_build = entry_mod._build_harness_options

    async def _spy_build(parsed, session, **kw):
        captured["project_trusted"] = kw.get("project_trusted")
        return await real_build(parsed, session, **kw)

    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)

    code = await _run_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"],
        proj,
        monkeypatch,
    )
    err = capsys.readouterr().err
    # The gate denied (headless, no --approve) → factory got project_trusted=False.
    assert captured["project_trusted"] is False
    # LOAD-BEARING end-to-end: the project-local probe was NEVER ``exec_module``'d,
    # so its import-time sentinel does NOT exist. If ``no_project_local=not
    # project_trusted`` were removed in entry.py, the loader would exec the probe,
    # the sentinel WOULD be written, and this assertion would fail — a real
    # gate-removal detector (verified by stubbing the wiring to
    # ``no_project_local=False``: the sentinel appears and this fails).
    assert not sentinel.exists()
    assert "untrusted directory" in err
    assert code in (0, 1)  # no RuntimeError from the poison extension


async def test_integration_approve_loads_project_extension(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """``--approve`` in the same untrusted dir → project_trusted=True (the
    project-local extension tier is enabled)."""

    proj = _isolated_env / "proj"
    ext = proj / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    # Same sentinel pattern as the untrusted test, but a CLEAN probe (registers
    # a flag in ``setup``, no raise). Under ``--approve`` the gate is bypassed,
    # so the loader DOES ``exec_module`` this file → the sentinel IS written.
    sentinel = proj / "PROBE_EXECUTED"
    (ext / "probe.py").write_text(
        f"from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('loaded')\n"
        "def setup(aelix):\n    aelix.register_flag('p', type='bool', default=True)\n"
    )

    captured: dict[str, object] = {}
    real_build = entry_mod._build_harness_options

    async def _spy_build(parsed, session, **kw):
        captured["project_trusted"] = kw.get("project_trusted")
        opts = await real_build(parsed, session, **kw)
        captured["extension_count"] = len(opts.extensions)
        return opts

    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)

    code = await _run_main(
        ["--no-session", "--print", "--approve", "--provider", "anthropic",
         "--model", "x"],
        proj,
        monkeypatch,
    )
    assert captured["project_trusted"] is True
    # LOAD-BEARING positive arm (paired with the untrusted test's sentinel):
    # ``--approve`` enables the project-local tier, so the probe IS executed (its
    # import-time sentinel exists) AND discovered into the extension set → 3
    # extensions (Guardrail + Permission + the project-local probe). Together the
    # pair forms the gate-removal detector: untrusted → no sentinel / 2 exts,
    # approved → sentinel / 3 exts.
    assert sentinel.exists()
    assert captured["extension_count"] == 3
    assert code in (0, 1)


async def test_integration_untrusted_headless_skips_project_mcp(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """Untrusted dir → project ``.aelix/mcp.json`` servers NOT connected."""

    proj = _isolated_env / "proj"
    aelix = proj / ".aelix"
    aelix.mkdir(parents=True)
    (aelix / "mcp.json").write_text(
        '{"mcpServers": {"fs": {"command": "echo", "args": ["hi"]}}}'
    )

    connected: dict[str, object] = {"contribs": None}

    class _SpyManager:
        def __init__(self, contribs):
            connected["contribs"] = contribs

        async def connect_all(self):
            return []

        async def collect_agent_tools(self):
            return []

        async def disconnect_all(self):
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _SpyManager)

    await _run_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"],
        proj,
        monkeypatch,
    )
    err = capsys.readouterr().err
    # The manager was never constructed (project contribs dropped pre-connect).
    assert connected["contribs"] is None
    assert "mcp.json" in err and "untrusted" in err


async def test_integration_approve_connects_project_mcp(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """``--approve`` → project ``.aelix/mcp.json`` servers ARE connected."""

    proj = _isolated_env / "proj"
    aelix = proj / ".aelix"
    aelix.mkdir(parents=True)
    (aelix / "mcp.json").write_text(
        '{"mcpServers": {"fs": {"command": "echo", "args": ["hi"]}}}'
    )

    connected: dict[str, object] = {"contribs": None}

    class _SpyManager:
        def __init__(self, contribs):
            connected["contribs"] = contribs

        async def connect_all(self):
            return []

        async def collect_agent_tools(self):
            return []

        async def disconnect_all(self):
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _SpyManager)

    await _run_main(
        ["--no-session", "--print", "--approve", "--provider", "anthropic",
         "--model", "x"],
        proj,
        monkeypatch,
    )
    assert connected["contribs"] is not None
    assert [c.name for c in connected["contribs"]] == ["fs"]  # type: ignore[attr-defined]


async def test_integration_env_mcp_not_gated_in_untrusted_dir(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """``$AELIX_MCP_CONFIG`` is a user choice → connected even in an
    untrusted dir (only project-local ``.aelix/mcp.json`` is gated)."""

    proj = _isolated_env / "proj"
    # Untrusted: a project-local extension dir makes the dir trust-requiring.
    ext = proj / ".aelix" / "extensions"
    ext.mkdir(parents=True)
    (ext / "probe.py").write_text("def setup(aelix):\n    pass\n")

    env_cfg = _isolated_env / "env-mcp.json"
    env_cfg.write_text('{"mcpServers": {"via_env": {"command": "echo"}}}')
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(env_cfg))

    connected: dict[str, object] = {"contribs": None}

    class _SpyManager:
        def __init__(self, contribs):
            connected["contribs"] = contribs

        async def connect_all(self):
            return []

        async def collect_agent_tools(self):
            return []

        async def disconnect_all(self):
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _SpyManager)

    await _run_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"],
        proj,
        monkeypatch,
    )
    # Env config is NOT gated → connected even though the dir is untrusted.
    assert connected["contribs"] is not None
    assert [c.name for c in connected["contribs"]] == ["via_env"]  # type: ignore[attr-defined]


async def test_integration_clean_dir_no_prompt_no_notice(
    _isolated_env: Path, monkeypatch, capsys
) -> None:
    """Regression: a dir with NO trust-requiring resources runs unchanged —
    no trust prompt, no notice, project_trusted=True."""

    proj = _isolated_env / "proj"
    proj.mkdir()

    captured: dict[str, object] = {}
    real_build = entry_mod._build_harness_options

    async def _spy_build(parsed, session, **kw):
        captured["project_trusted"] = kw.get("project_trusted")
        return await real_build(parsed, session, **kw)

    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)

    code = await _run_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"],
        proj,
        monkeypatch,
    )
    err = capsys.readouterr().err
    assert captured["project_trusted"] is True
    assert "untrusted" not in err
    assert code in (0, 1)


def test_config_source_global(tmp_path: Path, monkeypatch) -> None:
    """A global ~/.aelix-style mcp.json (via agent dir) tags source=global."""

    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    agent = tmp_path / "agent"
    agent.mkdir(parents=True)
    (agent / "mcp.json").write_text('{"mcpServers": {"g": {"command": "echo"}}}')
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    # cwd has no project-local mcp.json → falls back to global.
    contribs, _warnings, source = config_mod.load_mcp_server_contribs(
        str(tmp_path / "proj")
    )
    assert source == "global"
    assert [c.name for c in contribs] == ["g"]


# === 6. Over-gating guards (factory level) ===================================
#
# The gate is a FINER ``no_project_local`` flag that must suppress ONLY tier 1
# (auto-discovered ``cwd/.aelix/extensions/``). If a regression ever widened it
# to also drop tier-3 explicit ``-e`` configured paths or tier-4 entry_points
# (the way the coarser ``no_discovery`` does), the user's own extensions would
# silently vanish in an untrusted dir. These factory-level tests pin that
# boundary; the entry-level integration tests only cover the env-MCP arm.


class _OverGateEntryPoint:
    """Minimal entry-point stub (mirrors the discovery test harness)."""

    def __init__(self, name: str, factory: object) -> None:
        self.name = name
        self.value = f"fake:{name}"
        self._factory = factory

    def load(self) -> object:
        return self._factory


def _flag_setup(flag: str):
    def setup(aelix) -> None:  # noqa: ANN001 — ExtensionAPI
        aelix.register_flag(flag, type="bool", default=True)

    return setup


async def test_no_project_local_keeps_explicit_dash_e_extension(
    tmp_path: Path,
) -> None:
    """``-e <file>`` (tier 3) STILL loads when the project-local tier is gated.

    Paired check: the SAME run also has a project-local
    ``cwd/.aelix/extensions/`` file that MUST be suppressed — proving the gate
    is finer than ``no_discovery`` (drops tier 1 only, keeps the explicit path).
    """

    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    cwd = tmp_path / "proj"
    # Tier 1 (must be suppressed under the gate): a project-local extension.
    local = cwd / ".aelix" / "extensions" / "local.py"
    local.parent.mkdir(parents=True)
    local.write_text(
        "def setup(aelix):\n"
        "    aelix.register_flag('from_local', type='bool', default=True)\n"
    )
    # Tier 3 (explicit ``-e`` — a USER choice, must survive): a file OUTSIDE
    # the project-local discovery dir.
    explicit = tmp_path / "explicit_ext.py"
    explicit.write_text(
        "def setup(aelix):\n"
        "    aelix.register_flag('from_explicit', type='bool', default=True)\n"
    )

    result = await discover_and_load_extensions(
        [str(explicit)],
        cwd=cwd,
        agent_dir=tmp_path / "no_global",
        no_project_local=True,
    )

    assert result.errors == []
    flags = {f for ext in result.extensions for f in ext.flags}
    # The explicit ``-e`` extension loaded; the project-local one did NOT.
    assert "from_explicit" in flags
    assert "from_local" not in flags


async def test_no_project_local_keeps_entry_points_extension(
    tmp_path: Path, monkeypatch
) -> None:
    """An entry_points extension (tier 4) STILL loads under ``no_project_local``.

    ``no_project_local`` gates tier 1 only; entry_points is the Aelix-additive
    tier 4, loaded LAST, and is an installed/user choice — never project-local.
    """

    from aelix_coding_agent.extensions import loader as loader_mod
    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    cwd = tmp_path / "proj"
    cwd.mkdir()

    eps = [_OverGateEntryPoint("ext_remote", _flag_setup("from_ep"))]
    # Patch the module OBJECT's ``importlib.metadata.entry_points`` (repo
    # convention: monkeypatch objects, not dotted strings). Restore is
    # automatic at test teardown.
    monkeypatch.setattr(
        loader_mod.importlib.metadata,
        "entry_points",
        lambda *a, **k: eps,
    )

    result = await discover_and_load_extensions(
        [],
        cwd=cwd,
        agent_dir=tmp_path / "no_global",
        no_project_local=True,
    )

    assert result.errors == []
    flags = {f for ext in result.extensions for f in ext.flags}
    assert "from_ep" in flags
