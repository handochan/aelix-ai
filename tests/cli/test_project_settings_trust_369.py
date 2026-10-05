"""#369 / ADR-0252 — a project's ``.aelix/settings.json`` follows project trust.

Measured on ``5dee21d1`` (``.omc/probes/369-live/impl/base_sweep.out``, real CLI,
fake keys, a CONNECT recorder as ``HTTPS_PROXY``): a repo whose
``.aelix/settings.json`` held ``{"defaultProvider": "openai", "defaultModel":
"gpt-4o-mini"}`` and whose ``.env`` held ``OPENAI_API_KEY`` sent a plain
``aelix -p hi`` to ``api.openai.com`` while the user's own
``OPENROUTER_API_KEY`` was exported — with ``--no-approve`` too, because the
settings file was read whatever the trust and was not itself a trust-requiring
resource. ``aelix extension source add`` copied an index URL the repo's file
listed into the user's GLOBAL settings.

pi ``89a92207f`` (cited @ ``b223082bb``): ``settings.json`` is the first
trust-requiring resource (``trust-manager.ts:30-39``); the runtime's
``SettingsManager`` is created with the trust decision (``main.ts:736-748``) and
``resource-loader.ts:520`` sets it after resolving.

These drive the real predicate, prompt, ``_async_main`` (the print mode stubbed,
so no turn runs and nothing is sent), ``--list-models`` and the extension CLI.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustPromptResult,
    ProjectTrustStore,
    format_project_trust_prompt,
    has_trust_requiring_project_resources,
    project_trust_options,
    resolve_project_trusted,
)

from tests.cli.test_launch_route_344 import _FakePipedStdin
from tests.cli.test_launch_route_362 import _dotenv, _stub_print
from tests.env_sandbox import sandbox_home

_PAIR = {"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}
_NOTICE = (
    "Notice: project-local .aelix resources (extensions, skills, agent profiles, "
    "settings) skipped in an untrusted directory; pass --approve to trust."
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings_only(root: Path, payload: dict[str, Any] | None = None) -> Path:
    (root / ".aelix").mkdir(parents=True, exist_ok=True)
    (root / ".aelix" / "settings.json").write_text(json.dumps(payload or _PAIR), encoding="utf-8")
    return root


# === the predicate, the prompt, the /trust options, the child ======================


def test_a_settings_file_alone_requires_trust(tmp_path: Path) -> None:
    """RED on ``5dee21d1`` (``False``): step 2 trusted a settings-only repo unasked."""

    assert has_trust_requiring_project_resources(_settings_only(tmp_path)) is True


def test_an_empty_aelix_dir_still_requires_nothing(tmp_path: Path) -> None:
    (tmp_path / ".aelix").mkdir()
    assert has_trust_requiring_project_resources(tmp_path) is False


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_anything_named_settings_json_requires_trust(tmp_path: Path, kind: str) -> None:
    """pi tests EXISTENCE (``existsSync``, ``trust-manager.ts:192``), not a file.

    RED on ``67281070`` (``is_file()``): a FIFO or a directory at that path was
    "no resource", so step 2 trusted the repo whatever ``trust.json`` said.
    """

    (tmp_path / ".aelix").mkdir()
    target = tmp_path / ".aelix" / "settings.json"
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs on this platform")
        os.mkfifo(target)
    else:
        target.mkdir()
    assert has_trust_requiring_project_resources(tmp_path) is True


async def test_a_settings_only_repo_is_denied_headless_and_asked_interactively(
    tmp_path: Path,
) -> None:
    cwd = _settings_only(tmp_path / "repo")
    store = ProjectTrustStore(tmp_path / "agent")
    assert await resolve_project_trusted(cwd, override=None, has_ui=False, store=store) is False
    asked: list[Path] = []

    async def _prompt(path: Path) -> ProjectTrustPromptResult:
        asked.append(path)
        return ProjectTrustPromptResult(trusted=False, remember=False)

    await resolve_project_trusted(cwd, override=None, has_ui=True, prompt=_prompt, store=store)
    assert asked == [cwd]


def test_the_prompt_names_the_settings_file(tmp_path: Path) -> None:
    text = format_project_trust_prompt(tmp_path)
    assert "apply .aelix/settings.json" in text
    assert "can choose the model and provider your prompts are sent to" in text


def test_the_trust_command_offers_no_session_only_answer(tmp_path: Path) -> None:
    """pi ``trust-selector.ts:44``: ``/trust`` decides the NEXT launch only."""

    options = project_trust_options(tmp_path / "repo", include_session_only=False)
    assert options == [
        "Trust",
        f"Trust parent folder ({tmp_path})",
        "Do not trust",
    ]
    assert "Trust (this session only)" in project_trust_options(tmp_path / "repo")


def test_a_delegated_child_in_a_settings_only_repo_runs_untrusted(tmp_path: Path) -> None:
    """``child_trust_argv`` clause 1 calls the predicate, so it moves with it."""

    from aelix_agents.trust import child_trust_argv

    cwd = _settings_only(tmp_path)
    assert child_trust_argv(cwd, cwd) == ["--no-approve"]


# === the launch (real _async_main, print mode stubbed) ===============================


@pytest.fixture
def launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    (agent / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    return tmp_path


def _global(launch: Path, payload: dict[str, Any]) -> None:
    (launch / "agent" / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


async def _run(
    launch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[int, list[Any], str]:
    _dotenv(monkeypatch, launch, "OPENAI_API_KEY=oai-dotenv-fake\n")
    capsys.readouterr()
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", *flags, "-p", "hi"])
    return code, turns, capsys.readouterr().err


@pytest.fixture
def project_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every access ``FileSettingsStorage`` makes to a PROJECT settings path.

    A regular file is then read as usual (the trusted rows need it); anything
    else raises ``OSError`` instead of being opened, so a regression records an
    access here rather than blocking the suite on a FIFO.
    """

    from aelix_ai.settings.storage import FileSettingsStorage

    opens: list[str] = []
    real_flock = FileSettingsStorage._acquire_flock
    real_read = FileSettingsStorage._read_text

    def _guard(path: Path) -> None:
        if path.parent.name == ".aelix":
            opens.append(path.name)
            if not path.is_file():
                raise OSError("probe: a non-regular project settings path was opened")

    def _flock(self: Any, path: Path) -> int | None:
        _guard(path)
        return real_flock(self, path)

    def _read(self: Any, path: Path) -> str | None:
        _guard(path)
        return real_read(self, path)

    monkeypatch.setattr(FileSettingsStorage, "_acquire_flock", _flock)
    monkeypatch.setattr(FileSettingsStorage, "_read_text", _read)
    return opens


def _fifo_settings(cwd: Path) -> Path:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs on this platform")
    (cwd / ".aelix").mkdir()
    os.mkfifo(cwd / ".aelix" / "settings.json")
    return cwd


async def test_a_fifo_settings_file_with_a_saved_denial_is_never_opened(
    launch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_opens: list[str],
) -> None:
    """RED on ``67281070``: the predicate's ``is_file()`` skipped the FIFO, the
    saved denial never applied, and the loader opened it (``Warning: settings
    (project): [Errno 45] Operation not supported`` in the real CLI,
    ``.omc/probes/369-live/fix2/c1_fifo_on_67281070.out``)."""

    cwd = _fifo_settings(launch / "cwd")
    ProjectTrustStore(launch / "agent").set(cwd, False)
    _code, turns, err = await _run(launch, monkeypatch, capsys)
    assert project_opens == []
    assert turns == []
    assert _NOTICE in err.splitlines()
    assert "Warning: settings (project)" not in err


@pytest.mark.parametrize("flags", [[], ["--no-approve"]], ids=["S1-no-flag", "S3-no-approve"])
async def test_an_untrusted_repos_pair_does_not_choose_the_route(
    launch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    """RED on ``5dee21d1``: the turn ran on ``openai/gpt-4o-mini`` (api.openai.com, .env key)."""

    _settings_only(launch / "cwd")
    code, turns, err = await _run(launch, monkeypatch, capsys, *flags)
    assert turns == [], [(m.provider, m.id) for m in turns]
    assert code == 1
    assert "No model selected." in err.splitlines()
    assert _NOTICE in err.splitlines()


async def test_an_approved_repos_pair_is_the_users_choice(
    launch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """S1A — trusting the repo applies its settings (the reinforcement is declined, ADR-0252 §2.F)."""

    _settings_only(launch / "cwd")
    code, turns, err = await _run(launch, monkeypatch, capsys, "--approve")
    assert code == 0, err
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert _NOTICE not in err


async def test_a_saved_trust_decision_applies_the_repos_settings(
    launch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = _settings_only(launch / "cwd")
    ProjectTrustStore(launch / "agent").set(cwd, True)
    code, turns, err = await _run(launch, monkeypatch, capsys)
    assert code == 0, err
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]


async def test_the_users_own_global_pair_is_unchanged(
    launch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """S7 — no project file: nothing to gate, the user's pair runs."""

    _global(launch, _PAIR)
    code, turns, err = await _run(launch, monkeypatch, capsys)
    assert code == 0, err
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]


@pytest.mark.parametrize("where", ["global", "project"])
async def test_default_project_trust_counts_only_from_global_settings(
    launch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    where: str,
) -> None:
    if where == "global":
        _global(launch, {"defaultProjectTrust": "always"})
        _settings_only(launch / "cwd")
    else:
        _settings_only(launch / "cwd", {**_PAIR, "defaultProjectTrust": "always"})
    code, turns, err = await _run(launch, monkeypatch, capsys)
    if where == "global":
        assert code == 0, err
        assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    else:
        assert turns == []
        assert "No model selected." in err.splitlines()


async def test_a_trusted_repos_malformed_settings_are_reported_once_trusted(
    launch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (launch / "cwd" / ".aelix").mkdir()
    (launch / "cwd" / ".aelix" / "settings.json").write_text("{not json", encoding="utf-8")
    _code, _turns, err = await _run(launch, monkeypatch, capsys, "--no-approve")
    assert "Warning: settings (project)" not in err
    _code, _turns, err = await _run(launch, monkeypatch, capsys, "--approve")
    assert [line for line in err.splitlines() if line.startswith("Warning: settings (project)")]


# === --list-models (non-interactive trust: saved decision / global default) ==========


async def _list(
    launch: Path, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[list[str], str]:
    capsys.readouterr()
    code = await entry_mod._async_main(["--list-models", "gpt-4o", *flags])
    assert code == 0
    captured = capsys.readouterr()
    assert "Notice:" not in captured.out
    ids = [line.split()[1] for line in captured.out.splitlines()[1:] if line.strip()]
    return ids, captured.err


_ENABLED = {"enabledModels": ["openai/gpt-4o-mini"]}


@pytest.mark.parametrize(
    ("global_settings", "project", "saved", "flags", "trusted"),
    [
        ({}, _ENABLED, None, [], False),
        ({}, _ENABLED, True, [], True),
        ({}, _ENABLED, None, ["--approve"], True),
        # The documented order (ADR-0252 §2.C): the GLOBAL defaultProjectTrust
        # is the third step. "always" scopes the list ...
        ({"defaultProjectTrust": "always"}, _ENABLED, None, [], True),
        # ... "never" does not, and the project file is not even opened ...
        ({"defaultProjectTrust": "never"}, _ENABLED, None, [], False),
        # ... a PROJECT-scoped defaultProjectTrust counts for nothing ...
        ({}, {**_ENABLED, "defaultProjectTrust": "always"}, None, [], False),
        # ... and the two earlier steps beat a global "always".
        ({"defaultProjectTrust": "always"}, _ENABLED, False, [], False),
        ({"defaultProjectTrust": "always"}, _ENABLED, None, ["--no-approve"], False),
    ],
    ids=[
        "none",
        "saved",
        "approve",
        "global-always",
        "global-never",
        "project-scoped-always",
        "saved-denial-beats-global-always",
        "no-approve-beats-global-always",
    ],
)
async def test_list_models_reads_the_repos_scope_only_when_trusted(
    launch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_opens: list[str],
    global_settings: dict[str, Any],
    project: dict[str, Any],
    saved: bool | None,
    flags: list[str],
    trusted: bool,
) -> None:
    """Rows ``global-always`` / ``global-never`` / ``project-scoped-always`` were
    unpinned on ``67281070``: dropping the ``default_project_trust`` argument,
    or collapsing "never" into "always", passed every test (Codex round 1,
    ``.omc/probes/369-live/fix2/b1_repro_on_67281070.out``)."""

    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "oai-shell-fake")
    _global(launch, global_settings)
    cwd = _settings_only(launch / "cwd", project)
    if saved is not None:
        ProjectTrustStore(launch / "agent").set(cwd, saved)
    ids, err = await _list(launch, capsys, *flags)
    if trusted:
        assert ids == ["gpt-4o-mini"]
        assert project_opens, "a trusted repo's enabledModels must be read"
        assert _NOTICE not in err.splitlines()
    else:
        assert len(ids) > 1 and "gpt-4o-mini" in ids, ids
        assert project_opens == []
        # #369 round 2 — the run's headless notice, on stderr only (RED on
        # ``67281070``: an unscoped list said nothing about the skipped scope).
        assert _NOTICE in err.splitlines()


async def test_list_models_with_a_fifo_settings_file_and_a_saved_denial(
    launch: Path, capsys: pytest.CaptureFixture[str], project_opens: list[str]
) -> None:
    cwd = _fifo_settings(launch / "cwd")
    ProjectTrustStore(launch / "agent").set(cwd, False)
    _ids, err = await _list(launch, capsys)
    assert project_opens == []
    assert _NOTICE in err.splitlines()


# #369 round 2 verify — the notice's second conjunct
# (``has_trust_requiring_project_resources``), on both surfaces. A no-flag row
# cannot pin it: an ungated directory is trusted at step 2, so ``not trusted``
# alone already hides the notice and dropping the conjunct passed every test
# (``.omc/probes/369-live/fix3/mut_on_890c982a.out``). ``--no-approve`` is the
# one decision that is UNTRUSTED in an ungated directory (step 1 precedes step
# 2); a global "never" and a saved denial are steps 4-5 and so never reached
# there — kept as rows, each with its decision asserted, so a reorder that
# moved them before step 2 would still have to keep the notice quiet.
_NOTHING_GATED = [
    pytest.param({}, None, ["--no-approve"], False, id="no-approve"),
    pytest.param({"defaultProjectTrust": "never"}, None, [], True, id="global-never"),
    pytest.param({}, False, [], True, id="saved-denial"),
]


async def _nothing_gated(
    launch: Path, global_settings: dict[str, Any], saved: bool | None, flags: list[str]
) -> bool:
    """An empty ``.aelix/`` (nothing gated) under the row's decision; returns it."""

    _global(launch, global_settings)
    cwd = launch / "cwd"
    (cwd / ".aelix").mkdir()
    store = ProjectTrustStore(launch / "agent")
    if saved is not None:
        store.set(cwd, saved)
    override = {"--no-approve": False}.get(flags[0]) if flags else None
    return await resolve_project_trusted(
        cwd,
        override=override,
        has_ui=False,
        prompt=None,
        store=store,
        default_project_trust=global_settings.get("defaultProjectTrust", "ask"),
    )


async def test_list_models_in_a_repo_with_nothing_gated_prints_no_notice(
    launch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (launch / "cwd" / ".aelix").mkdir()
    _ids, err = await _list(launch, capsys)
    assert "Notice:" not in err


@pytest.mark.parametrize(("global_settings", "saved", "flags", "trusted"), _NOTHING_GATED)
async def test_list_models_untrusted_with_nothing_gated_prints_no_notice(
    launch: Path,
    capsys: pytest.CaptureFixture[str],
    global_settings: dict[str, Any],
    saved: bool | None,
    flags: list[str],
    trusted: bool,
) -> None:
    """Row ``no-approve`` is RED on ``890c982a`` with the conjunct removed
    (``if not list_trusted:``)."""

    assert await _nothing_gated(launch, global_settings, saved, flags) is trusted
    _ids, err = await _list(launch, capsys, *flags)
    assert "Notice:" not in err


@pytest.mark.parametrize(("global_settings", "saved", "flags", "trusted"), _NOTHING_GATED)
async def test_a_headless_run_with_nothing_gated_prints_no_notice(
    launch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    global_settings: dict[str, Any],
    saved: bool | None,
    flags: list[str],
    trusted: bool,
) -> None:
    """The run's own notice, same conjunct: row ``no-approve`` is RED on
    ``890c982a`` with ``has_trust_requiring_project_resources(Path(cwd))``
    dropped from the headless-notice condition."""

    # The user's own global pair, so the run reaches its turn (S7).
    settings = {**_PAIR, **global_settings}
    assert await _nothing_gated(launch, settings, saved, flags) is trusted
    code, turns, err = await _run(launch, monkeypatch, capsys, *flags)
    assert code == 0, err
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert "Notice:" not in err


# === the extension CLI (global-scope fields only) ====================================


async def test_extension_source_add_does_not_copy_a_repos_index_into_global_settings(
    launch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RED on ``5dee21d1``: the global file gained ``https://repo-index.invalid/simple``."""

    from aelix_coding_agent.cli.extension_install import run_extension_command_async

    _settings_only(
        launch / "cwd",
        {"extensionSources": [{"spec": "https://repo-index.invalid/simple", "kind": "index"}]},
    )
    local = launch / "localext"
    local.mkdir()
    assert await run_extension_command_async(["source", "list"]) == 0
    assert "repo-index.invalid" not in capsys.readouterr().out
    assert await run_extension_command_async(["source", "add", str(local)]) == 0
    saved = json.loads((launch / "agent" / "settings.json").read_text(encoding="utf-8"))
    assert [s["spec"] for s in saved["extensionSources"]] == [str(local.resolve())]


# === every production construction site says what it trusts ===========================


def _construction_sites() -> list[tuple[str, int, bool]]:
    sites: list[tuple[str, int, bool]] = []
    roots = [
        _REPO_ROOT / "packages" / "aelix-coding-agent" / "src",
        _REPO_ROOT / "packages" / "aelix-agent-core" / "src",
        _REPO_ROOT / "packages" / "aelix-server" / "src",
        _REPO_ROOT / "src",
    ]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"create", "from_storage", "in_memory"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "SettingsManager"
                ):
                    explicit = any(k.arg == "project_trusted" for k in node.keywords)
                    sites.append((path.relative_to(_REPO_ROOT).as_posix(), node.lineno, explicit))
    return sites


def test_every_production_settings_manager_passes_project_trust() -> None:
    """The default is pi's (trusted); a site that forgets the flag reopens #369."""

    sites = _construction_sites()
    files = {path for path, _line, _explicit in sites}
    # entry.py builds two (the run and --list-models); the extension CLI one.
    assert {
        "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py",
        "packages/aelix-coding-agent/src/aelix_coding_agent/cli/extension_install.py",
    } <= files, sites
    assert len(sites) >= 3, sites
    assert [s for s in sites if not s[2]] == []
