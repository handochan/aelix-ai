"""#638: ``-e acme`` runs the project's ``acme/`` only where the user trusted the folder.

``_is_module_ref`` clause 4 lets an existing path win over the module reading
(#114), so in a folder holding ``acme/`` the string ``-e acme`` meant that
folder — and an explicit ``-e`` loads outside the ``no_project_local`` trust
gate. The #114 warning was printed and then the folder's code ran, under
``--no-approve`` too, and in any folder with nothing to gate, which is trusted
without a question. Now such an entry loads only when the folder is EXPLICITLY
trusted — ``--approve`` or a saved trust decision of yes (owner decision
2026-11-01) — and is refused everywhere else before anything is opened, the
message leading with the two spellings that say what was meant.

The loader rows drive ``discover_and_load_extensions`` /
``scan_extension_manifests`` the way ``cli/entry.py`` does, in three trust
states: ``untrusted`` (``no_project_local``), ``implicit`` (trusted, but nobody
said yes: nothing to gate) and ``explicit``. The process rows run the issue's
reproduction with a real CPython ``aelix`` child, and the
``_project_explicitly_trusted`` rows pin which launches count as explicit.
"""

from __future__ import annotations

import importlib
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.cli import entry
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.project_trust import DefaultProjectTrust
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.api import ExtensionFactory
from aelix_coding_agent.extensions.loader import (
    LoadExtensionsResult,
    discover_and_load_extensions,
    scan_extension_manifests,
)
from aelix_coding_agent.tui.extension_manager import build_installed_lines

from tests.env_sandbox import child_env
from tests.extensions.test_ep_manifest import manifest_toml

_PAYLOAD = """\
import pathlib

with open({sentinel!r}, "a", encoding="utf-8") as _f:
    _f.write("exec\\n")


def setup(aelix):
    return None
"""


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    cwd = tmp_path / "proj"
    (cwd / "acme").mkdir(parents=True)
    (cwd / "acme" / "__init__.py").write_text(
        _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
    )
    yield cwd
    for name in [n for n in sys.modules if n.startswith("_aelix_ext_") or n == "acme"]:
        del sys.modules[name]


def _executions(tmp_path: Path) -> int:
    sentinel = tmp_path / "SENTINEL"
    return len(sentinel.read_text(encoding="utf-8").splitlines()) if sentinel.exists() else 0


#: ``(no_project_local, project_explicitly_trusted)`` per trust state, as
#: ``cli/entry.py`` passes them: ``not project_trusted`` and
#: ``_project_explicitly_trusted``.
_TRUST = {
    "untrusted": (True, False),
    "implicit": (False, False),
    "explicit": (False, True),
}


async def _load(
    entries: list[str], cwd: Path, tmp_path: Path, *, trust: str
) -> LoadExtensionsResult:
    """What ``cli/entry.py`` does with ``-e`` in the given trust state."""

    no_project_local, explicit = _TRUST[trust]
    configured: list[str | Path | ExtensionFactory] = [*entries]
    return await discover_and_load_extensions(
        configured,
        cwd=cwd,
        agent_dir=tmp_path / "agent",
        no_discovery=True,
        no_project_local=no_project_local,
        project_explicitly_trusted=explicit,
    )


_HEAD = "not loaded: pass -e ./acme or -e acme:setup — 'acme' would load "


@pytest.mark.parametrize("trust", ["untrusted", "implicit"])
async def test_a_folder_not_explicitly_trusted_refuses_a_module_shaped_entry_it_shadows(
    project: Path, tmp_path: Path, trust: str
) -> None:
    """``implicit`` is the owner's 2026-11-01 row: a folder with nothing to gate."""

    result = await _load(["acme"], project, tmp_path, trust=trust)

    assert result.extensions == []
    [error] = result.errors
    assert error.path == "acme"
    assert error.error.startswith(_HEAD)
    assert str(project / "acme") in error.error
    assert "not explicitly trusted (--approve, or a saved trust decision)" in error.error
    assert error.verify_applies is False
    assert _executions(tmp_path) == 0


async def test_the_explicit_flag_does_not_override_an_untrusted_folder(
    project: Path, tmp_path: Path
) -> None:
    """Both signals must agree: a caller saying "explicit" for an untrusted folder."""

    result = await discover_and_load_extensions(
        ["acme"],
        cwd=project,
        agent_dir=tmp_path / "agent",
        no_discovery=True,
        no_project_local=True,
        project_explicitly_trusted=True,
    )

    assert [e.path for e in result.errors] == ["acme"]
    assert _executions(tmp_path) == 0


async def test_a_library_caller_that_does_not_say_gets_the_refusal(
    project: Path, tmp_path: Path
) -> None:
    result = await discover_and_load_extensions(
        ["acme"], cwd=project, agent_dir=tmp_path / "agent", no_discovery=True
    )

    assert [e.path for e in result.errors] == ["acme"]
    assert _executions(tmp_path) == 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="control characters are not legal in Windows names"
)
async def test_the_refusal_flattens_the_folder_path(tmp_path: Path) -> None:
    """The folder's name is whatever a clone chose (#638 verify V5, Codex PC5)."""

    cwd = tmp_path / "proj\x1b[2J\x1b]0;pwned\x07\tFORGED\u202e"
    (cwd / "acme").mkdir(parents=True)
    (cwd / "acme" / "__init__.py").write_text(
        _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
    )

    result = await _load(["acme"], cwd, tmp_path, trust="implicit")

    [error] = result.errors
    assert error.error.startswith(_HEAD)
    assert not any(ch in error.error for ch in "\x1b\x07\t\n\u202e")
    assert "proj[2J]0;pwnedFORGED" in error.error
    rows = build_installed_lines([], [], result.errors)
    assert not any(ch in "".join(rows) for ch in "\x1b\x07\t\u202e")


def test_the_refusal_leads_with_what_to_type_at_80_columns(tmp_path: Path) -> None:
    """``/extension`` cuts a row at the width; the hints must come before the path."""

    message = L._shadowed_entry_refusal("acme", tmp_path / ("deep" * 30))
    row = f"  ✖ acme — refused: {message}"

    assert "-e ./acme" in row[:80]
    assert "-e acme:setup" in row[:80]
    assert row.index("-e acme:setup") < row.index(str(tmp_path))
    # The flattening, without a folder Windows could not create.
    hostile = L._shadowed_entry_refusal("acme", tmp_path / "p\x1b[2Jq")
    assert "\x1b" not in hostile and "p[2Jq" in hostile


def test_an_entry_with_a_colon_gets_the_other_hint(tmp_path: Path) -> None:
    message = L._shadowed_entry_refusal("acme:setup", tmp_path)

    assert message.startswith("not loaded: pass -e ./acme:setup, or run from another folder — ")
    assert ":setup:setup" not in message


def test_extension_verify_is_not_suggested_for_an_e_refusal_alone() -> None:
    shadow = L.ExtensionLoadError(path="acme", error="not loaded: …", verify_applies=False)
    other = L.ExtensionLoadError(path="pack", error="untrusted provenance")
    verify = "  Run `aelix extension verify` for the full diagnosis."

    assert verify not in build_installed_lines([], [], [shadow])
    assert verify in build_installed_lines([], [], [shadow, other])
    assert verify in build_installed_lines([], [], [other])


def test_the_verify_flag_stays_out_of_repr_and_equality() -> None:
    """The CLI prints ``Warning: extension load: {err!r}`` and callers compare errors."""

    shadow = L.ExtensionLoadError(path="acme", error="x", verify_applies=False)

    assert repr(shadow) == "ExtensionLoadError(path='acme', error='x')"
    assert shadow == L.ExtensionLoadError(path="acme", error="x")


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param("dotted", id="dotted-directory"),
        pytest.param("file", id="suffixless-file"),
        pytest.param("symlink", id="symlink-out-of-the-project"),
    ],
)
async def test_every_on_disk_shape_is_refused(tmp_path: Path, shape: str) -> None:
    """Whatever clause 4 found, the project folder is why it is a path."""

    cwd = tmp_path / "proj"
    cwd.mkdir()
    name = "mypkg.ext" if shape == "dotted" else "acme"
    target = cwd / name
    if shape == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "__init__.py").write_text(
            _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
        )
        try:
            target.symlink_to(elsewhere, target_is_directory=True)
        except OSError as exc:  # Windows without the symlink privilege
            pytest.skip(f"cannot create a directory symlink here: {exc}")
    elif shape == "file":
        target.write_text(_PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8")
    else:
        target.mkdir()
        (target / "on_disk.py").write_text(
            _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
        )

    result = await _load([name], cwd, tmp_path, trust="implicit")

    assert result.extensions == []
    assert [e.path for e in result.errors] == [name]
    assert _executions(tmp_path) == 0


async def test_an_explicitly_trusted_project_still_warns_and_loads(
    project: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=L.__name__):
        result = await _load(["acme"], project, tmp_path, trust="explicit")

    assert result.errors == []
    assert len(result.extensions) == 1
    assert "PROJECT-LOCAL code will be loaded" in caplog.text
    assert _executions(tmp_path) == 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="control characters are not legal in Windows names"
)
async def test_the_explicit_trust_warning_flattens_the_folder_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The #114 warning names the same path, on the same stderr (#638 V5)."""

    cwd = tmp_path / "proj\x1b[2J\x07FORGED"
    (cwd / "acme").mkdir(parents=True)
    (cwd / "acme" / "__init__.py").write_text(
        _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING, logger=L.__name__):
        await _load(["acme"], cwd, tmp_path, trust="explicit")

    assert "PROJECT-LOCAL code will be loaded" in caplog.text
    assert "proj[2JFORGED" in caplog.text
    assert "\x1b" not in caplog.text and "\x07" not in caplog.text


@pytest.mark.parametrize("trust", ["untrusted", "implicit"])
async def test_the_dot_slash_spelling_loads_the_folder_on_purpose(
    project: Path, tmp_path: Path, trust: str
) -> None:
    """``./acme`` is a path the user typed — explicit ``-e``, never gated."""

    result = await _load(["./acme"], project, tmp_path, trust=trust)

    assert result.errors == []
    assert len(result.extensions) == 1
    assert _executions(tmp_path) == 1


async def test_the_colon_spelling_loads_the_installed_module(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal's other hint: ``acme:setup`` can only be the module."""

    site = tmp_path / "site"
    (site / "acme").mkdir(parents=True)
    (site / "acme" / "__init__.py").write_text(
        'ORIGIN = "installed"\n\ndef setup(aelix):\n    return None\n', encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    result = await _load(["acme:setup"], project, tmp_path, trust="untrusted")

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["acme:setup"]
    assert sys.modules["acme"].ORIGIN == "installed"
    assert _executions(tmp_path) == 0


async def test_a_repeated_entry_is_refused_once(project: Path, tmp_path: Path) -> None:
    result = await _load(["acme", "acme"], project, tmp_path, trust="implicit")

    assert [e.path for e in result.errors] == ["acme"]


def _mcp_pack(cwd: Path) -> None:
    """A manifest pack inside ``acme/`` — ``-e <dir>`` expands one level down."""

    pack = cwd / "acme" / "mcppack"
    pack.mkdir()
    (pack / "aelix-plugin.toml").write_text(
        manifest_toml(
            "acme",
            capabilities="shell_exec = true\n",
            contributes=(
                "[[contributes.mcp_servers]]\n"
                'name = "acme-mcp"\n'
                'transport = "stdio"\n'
                'command = "acme-server"\n'
            ),
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("trust", ["untrusted", "implicit", "explicit"])
def test_the_manifest_scan_follows_the_same_rule(project: Path, tmp_path: Path, trust: str) -> None:
    """The scan feeds ``contributes.mcp_servers`` to the MCP connect — a spawn.

    It shares ``_discover_entries``, so ``-e acme`` in a folder not explicitly
    trusted surfaces no manifest; the explicit row is the control.
    """

    _mcp_pack(project)
    no_project_local, explicit = _TRUST[trust]

    configured: list[str | Path | ExtensionFactory] = ["acme"]
    manifests = scan_extension_manifests(
        configured,
        cwd=project,
        agent_dir=tmp_path / "agent",
        no_discovery=True,
        no_project_local=no_project_local,
        project_explicitly_trusted=explicit,
    )

    assert [m.plugin.id for m in manifests] == (["acme"] if explicit else [])


# --------------------------------------------------------------------------
# The issue's reproduction, as a real child
# --------------------------------------------------------------------------


_REFUSED = "not loaded: pass -e ./acme or -e acme:setup"


_LOADS = "PROJECT-LOCAL code"


@pytest.mark.parametrize(
    ("flags", "gated", "saved", "default", "executions", "says"),
    [
        pytest.param(["--no-approve"], False, None, None, 0, _REFUSED, id="no-approve"),
        pytest.param(["--approve"], False, None, None, 1, _LOADS, id="approve"),
        # The owner's 2026-11-01 row: nothing to gate, so the folder is trusted
        # without a question — and that is not explicit (base: 1 execution).
        pytest.param([], False, None, None, 0, _REFUSED, id="nothing-to-gate"),
        pytest.param([], False, True, None, 1, _LOADS, id="saved-yes"),
        # A gated resource and no flag: the pre-trust vote load runs, then -p
        # denies the undecided folder. Both loads refuse (base: 2 executions).
        pytest.param([], True, None, None, 0, _REFUSED, id="headless-undecided"),
        # A saved yes decides a gated folder under -p: the vote load refuses,
        # the real load loads, once.
        pytest.param([], True, True, None, 1, _LOADS, id="gated-saved-yes"),
        # The owner's second 2026-11-01 decision: the global setting is the
        # user's yes to every folder (fix round 1: 0 executions in both).
        pytest.param([], False, None, "always", 1, _LOADS, id="always-nothing-to-gate"),
        pytest.param([], True, None, "always", 1, _LOADS, id="always-gated"),
        pytest.param([], True, False, "always", 0, _REFUSED, id="always-but-a-saved-no"),
    ],
)
def test_the_reproduction(
    tmp_path: Path,
    flags: list[str],
    gated: bool,
    saved: bool | None,
    default: str | None,
    executions: int,
    says: str,
) -> None:
    repo = tmp_path / "repo"
    (repo / "acme").mkdir(parents=True)
    (repo / "acme" / "__init__.py").write_text(
        _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
    )
    if gated:
        (repo / ".aelix").mkdir()
        (repo / ".aelix" / "settings.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    home = tmp_path / "home"
    agent_dir = home / ".aelix" / "agent"
    agent_dir.mkdir(parents=True)
    if saved is not None:
        (agent_dir / "trust.json").write_text(
            json.dumps({str(repo.resolve()): saved}), encoding="utf-8"
        )
    if default is not None:
        (agent_dir / "settings.json").write_text(
            json.dumps({"defaultProjectTrust": default}), encoding="utf-8"
        )
    env = child_env(home, PI_OFFLINE="1", AELIX_CODING_AGENT_DIR=str(agent_dir))
    argv = [
        sys.executable,
        "-P",
        "-m",
        "aelix_coding_agent",
        "-e",
        "acme",
        *flags,
        "--no-session",
        "--provider",
        "openrouter",
        "--model",
        "anthropic/claude-haiku-4.5",
        "-p",
        "hi",
    ]

    # Bytes, not ``text=True``: the Windows leg decodes cp1252 there.
    result = subprocess.run(
        argv, cwd=repo, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=180
    )

    stderr = result.stderr.decode("utf-8", "replace")
    assert _executions(tmp_path) == executions, stderr
    assert says in stderr
    # #638 verify R3: the vote load runs before trust is decided, so it does not
    # print a refusal the decision may lift (``gated-saved-yes`` then loads).
    assert "vote-load: ExtensionLoadError(path='acme'" not in stderr


def test_the_vote_load_still_reports_its_other_errors(tmp_path: Path) -> None:
    """R3 silences the ``-e`` refusal only: a broken global extension still says so."""

    repo = tmp_path / "repo"
    (repo / "acme").mkdir(parents=True)
    (repo / "acme" / "__init__.py").write_text(
        _PAYLOAD.format(sentinel=str(tmp_path / "SENTINEL")), encoding="utf-8"
    )
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    agent_dir = tmp_path / "home" / ".aelix" / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "extensions" / "broken_zz.py").write_text(
        "raise RuntimeError('broken on purpose')\n", encoding="utf-8"
    )
    env = child_env(tmp_path / "home", PI_OFFLINE="1", AELIX_CODING_AGENT_DIR=str(agent_dir))
    argv = [
        sys.executable,
        "-P",
        "-m",
        "aelix_coding_agent",
        "-e",
        "acme",
        "--no-session",
        "--provider",
        "openrouter",
        "--model",
        "anthropic/claude-haiku-4.5",
        "-p",
        "hi",
    ]

    result = subprocess.run(
        argv, cwd=repo, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=180
    )

    stderr = result.stderr.decode("utf-8", "replace")
    vote_lines = [line for line in stderr.splitlines() if "project_trust vote-load:" in line]
    assert any("broken_zz" in line for line in vote_lines), stderr
    assert not any("path='acme'" in line for line in vote_lines), stderr


# --------------------------------------------------------------------------
# Which launches are explicit (``cli/entry.py``)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "saved", "project_trusted", "default", "explicit"),
    [
        pytest.param(True, None, True, "ask", True, id="approve"),
        pytest.param(False, True, False, "ask", False, id="no-approve-beats-a-saved-yes"),
        pytest.param(None, True, True, "ask", True, id="saved-yes"),
        pytest.param(None, None, True, "ask", False, id="nothing-to-gate-or-unsaved-yes"),
        pytest.param(None, False, False, "ask", False, id="saved-no"),
        # A saved yes the resolution did not follow (a vote said no first).
        pytest.param(None, True, False, "ask", False, id="saved-yes-but-not-trusted"),
        pytest.param(None, "malformed", True, "ask", False, id="malformed-store"),
        # The global ``defaultProjectTrust`` (owner, 2026-11-01): "always" is a yes.
        pytest.param(None, None, True, "always", True, id="always"),
        pytest.param(None, False, False, "always", False, id="always-but-a-saved-no"),
        pytest.param(False, None, False, "always", False, id="no-approve-beats-always"),
        pytest.param(None, None, True, "never", False, id="never-nothing-to-gate"),
    ],
)
def test_which_launches_count_as_explicit_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: bool | None,
    saved: bool | str | None,
    project_trusted: bool,
    default: DefaultProjectTrust,
    explicit: bool,
) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()
    if saved == "malformed":
        (agent_dir / "trust.json").write_text("[not an object", encoding="utf-8")
    elif saved is not None:
        # A PARENT's entry: the store walks up, as "Trust parent folder" relies on.
        (agent_dir / "trust.json").write_text(
            json.dumps({str(tmp_path.resolve()): saved}), encoding="utf-8"
        )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    parsed = Args()
    parsed.project_trust_override = override

    got = entry._project_explicitly_trusted(parsed, str(cwd), project_trusted, default)

    assert got is explicit


@pytest.mark.parametrize(
    ("flags", "spawned"),
    [pytest.param(["--approve"], 1, id="approve"), pytest.param([], 0, id="nothing-to-gate")],
)
def test_the_cli_manifest_scan_starts_the_folder_s_mcp_server_only_when_explicit(
    tmp_path: Path, flags: list[str], spawned: int
) -> None:
    """``cli/entry.py`` hands the scan the same explicit-trust answer as the load.

    The scan is what turns ``contributes.mcp_servers`` into a spawn at startup,
    so a missed argument there either starts an implicitly trusted folder's
    server or drops an explicitly trusted one's. The server's command records
    that it was started; the connection then fails, which is fine here.
    """

    repo = tmp_path / "repo"
    pack = repo / "acme" / "mcppack"
    pack.mkdir(parents=True)
    marker = tmp_path / "MCP-SPAWNED"
    command = f"open({str(marker)!r}, 'a', encoding='utf-8').write('x\\n')"
    pack.joinpath("aelix-plugin.toml").write_text(
        manifest_toml(
            "acme",
            capabilities="shell_exec = true\n",
            contributes=(
                "[[contributes.mcp_servers]]\n"
                'name = "acme-mcp"\n'
                'transport = "stdio"\n'
                f"command = {json.dumps(sys.executable)}\n"
                f"args = {json.dumps(['-c', command])}\n"
            ),
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    home = tmp_path / "home"
    env = child_env(home, PI_OFFLINE="1", AELIX_CODING_AGENT_DIR=str(home / ".aelix" / "agent"))
    argv = [
        sys.executable,
        "-P",
        "-m",
        "aelix_coding_agent",
        "-e",
        "acme",
        *flags,
        "--no-session",
        "--provider",
        "openrouter",
        "--model",
        "anthropic/claude-haiku-4.5",
        "-p",
        "hi",
    ]

    result = subprocess.run(
        argv, cwd=repo, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=180
    )

    lines = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    assert len(lines) == spawned, result.stderr
