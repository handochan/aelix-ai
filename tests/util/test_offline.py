"""#288 — one offline predicate, read the same way by every self-initiated network path.

Before #288 the decision lived in three places: ``update_check.is_offline`` and
``cli/extension_install._is_offline`` read ``PI_OFFLINE`` and ``AELIX_OFFLINE``,
``util/tools_manager._is_offline`` read ``PI_OFFLINE`` only, and
``cli/entry.py`` exported ``PI_OFFLINE=1`` only from ``--offline`` or a set
``PI_OFFLINE``. Measured on ``aab1f210``: with ``AELIX_OFFLINE=1``,
``tools_manager._is_offline()`` was ``False`` and ``ensure_tool("fd")`` printed
``fd not found. Downloading...`` and resolved ``api.github.com``.

These tests pin three things: the value table (pi's documented values read as
pi reads them, unknown values fail closed), that every consumer gives the same
answer as the predicate for every value of both names, and that no module
outside the predicate reads either name again.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_coding_agent import update_check
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli import extension_install
from aelix_coding_agent.util import offline, tools_manager

NAMES = ("PI_OFFLINE", "AELIX_OFFLINE")

# (value, offline?) — pi's ``isTruthyEnvFlag`` set is 1/true/yes; ADR-0185 made
# 0 read as OFF. Everything else non-blank fails closed.
TABLE = [
    ("1", True),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("Yes", True),
    ("on", True),
    (" 1 ", True),
    ("nope", True),
    ("2", True),
    ("0", False),
    ("false", False),
    ("FALSE", False),
    ("no", False),
    ("off", False),
    (" 0 ", False),
    ("", False),
    ("   ", False),
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    # setenv-then-delenv, not ``delenv(raising=False)``: on an ABSENT name the
    # latter records nothing, so the ``PI_OFFLINE=1`` that ``cli/entry.py``
    # exports below would outlive the test and turn every later test offline.
    for name in NAMES:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return monkeypatch


def test_unset_is_online(clean_env: pytest.MonkeyPatch) -> None:
    assert offline.is_offline() is False


def test_explicit_flag_is_offline_regardless_of_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PI_OFFLINE", "0")
    assert offline.is_offline(True) is True


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value,expected", TABLE)
def test_value_table(clean_env: pytest.MonkeyPatch, name: str, value: str, expected: bool) -> None:
    clean_env.setenv(name, value)
    assert offline.is_offline() is expected


@pytest.mark.parametrize("off_name,on_name", [NAMES, tuple(reversed(NAMES))])
def test_an_off_value_in_one_name_does_not_cancel_the_other(
    clean_env: pytest.MonkeyPatch, off_name: str, on_name: str
) -> None:
    clean_env.setenv(off_name, "0")
    clean_env.setenv(on_name, "1")
    assert offline.is_offline() is True


def test_update_check_reexports_the_one_predicate() -> None:
    assert update_check.is_offline is offline.is_offline


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value,expected", TABLE)
def test_every_consumer_agrees_with_the_predicate(
    clean_env: pytest.MonkeyPatch, name: str, value: str, expected: bool
) -> None:
    """The defect was a consumer that disagreed. Ask each one directly."""

    clean_env.setenv(name, value)
    got = {
        "update_check.is_offline": update_check.is_offline(),
        "extension_install._is_offline": extension_install._is_offline(False),
        "tools_manager._is_offline": tools_manager._is_offline(),
    }
    assert got == dict.fromkeys(got, expected)


# --- the CLI export -----------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
async def test_entry_exports_pi_offline_from_either_name(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """``aelix`` exports ``PI_OFFLINE=1`` so children and pi-shaped readers inherit it.

    ``--version`` returns right after the offline block, so this is the export
    and nothing else.
    """

    clean_env.setenv(name, "1")
    assert await entry_mod._async_main(["--version"]) == 0
    assert entry_mod.os.environ.get("PI_OFFLINE") == "1"


async def test_entry_flag_exports_pi_offline(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await entry_mod._async_main(["--offline", "--version"]) == 0
    assert entry_mod.os.environ.get("PI_OFFLINE") == "1"


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
async def test_entry_does_not_turn_an_explicit_off_into_on(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    """``PI_OFFLINE=0`` used to be rewritten to ``1`` because any set value counted."""

    clean_env.setenv("PI_OFFLINE", value)
    assert await entry_mod._async_main(["--version"]) == 0
    assert entry_mod.os.environ.get("PI_OFFLINE") == value
    assert offline.is_offline() is False


async def test_entry_leaves_online_unset(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await entry_mod._async_main(["--version"]) == 0
    assert "PI_OFFLINE" not in entry_mod.os.environ


# --- the export reaches a verb's children (review round 1, Codex) -------------
#
# The verbs return before ``parse_args``; round 1 exported after it, so
# ``AELIX_OFFLINE=1 aelix extension install <path>`` launched its installer with
# no ``PI_OFFLINE`` (measured on 3012bd3e: a fake ``uv`` on PATH saw
# ``PI_OFFLINE=<unset> AELIX_OFFLINE=1``), and the subcommand's own
# ``--offline`` reached no child at all. Pi exports before
# ``handlePackageCommand``. These drive the real installer, whose runner is
# swapped for one that starts a REAL child interpreter and reports what it
# inherited.

_CHILD = (
    "import json, os; "
    "print(json.dumps({n: os.environ.get(n) for n in ('PI_OFFLINE', 'AELIX_OFFLINE')}))"
)


def _install_child_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[dict[str, str | None]]:
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    monkeypatch.setenv("AELIX_AUTH_PATH", str(agent / "auth.json"))
    seen: list[dict[str, str | None]] = []

    def _runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        child = subprocess.run(  # noqa: S603 — argv list, no shell
            [sys.executable, "-c", _CHILD], capture_output=True, check=True
        )
        seen.append(json.loads(child.stdout))
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(extension_install, "_default_runner", _runner)
    monkeypatch.setattr(
        extension_install, "detect_install_backend", lambda: extension_install.PIP_BACKEND
    )
    return seen


def _local_extension(tmp_path: Path) -> str:
    pkg = tmp_path / "local-ext"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "offline-probe-ext"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    return str(pkg)


@pytest.mark.parametrize(
    "env,flag,expected",
    [
        ({"AELIX_OFFLINE": "1"}, False, "1"),
        ({"AELIX_OFFLINE": "enabled"}, False, "1"),
        ({"PI_OFFLINE": "yes"}, False, "1"),
        ({}, True, "1"),
        ({}, False, None),
        ({"PI_OFFLINE": "0"}, False, "0"),
    ],
    ids=[
        "AELIX_OFFLINE=1",
        "AELIX_OFFLINE=enabled",
        "PI_OFFLINE=yes",
        "--offline",
        "online",
        "PI_OFFLINE=0",
    ],
)
async def test_extension_verb_child_inherits_the_offline_export(
    clean_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    env: dict[str, str],
    flag: bool,
    expected: str | None,
) -> None:
    seen = _install_child_env(clean_env, tmp_path)
    for name, value in env.items():
        clean_env.setenv(name, value)
    argv = ["extension", "install", _local_extension(tmp_path), "--yes", "--no-verify"]
    if flag:
        argv.append("--offline")
    await entry_mod._async_main(argv)
    assert seen, capsys.readouterr()
    assert [child["PI_OFFLINE"] for child in seen] == [expected] * len(seen)


@pytest.mark.parametrize("verb", [["docs"], ["status", "--help"]])
async def test_every_verb_runs_after_the_export(
    clean_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    verb: list[str],
) -> None:
    """``docs`` and ``status`` return before ``parse_args`` too; record at dispatch."""

    at_dispatch: list[str | None] = []

    def _record(*_a: object, **_k: object) -> int:
        at_dispatch.append(entry_mod.os.environ.get("PI_OFFLINE"))
        return 0

    async def _record_async(*a: object, **k: object) -> int:
        return _record(*a, **k)

    from aelix_coding_agent.cli import docs as docs_mod
    from aelix_coding_agent.cli import status as status_mod

    monkeypatch.setattr(docs_mod, "run_docs_command", _record)
    monkeypatch.setattr(status_mod, "run_status_command", _record_async)
    clean_env.setenv("AELIX_OFFLINE", "1")
    assert await entry_mod._async_main(verb) == 0
    assert at_dispatch == ["1"]


# --- no fourth copy -----------------------------------------------------------

_SRC = Path(__file__).resolve().parents[2] / "packages"
_PREDICATE = Path(offline.__file__).resolve()


def _name_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in NAMES
    ]


def test_no_module_outside_the_predicate_reads_an_offline_name() -> None:
    """A second reader is how ``AELIX_OFFLINE`` went missing from one of three.

    Docstrings and comments may name the variables; code may not. The predicate
    module holds every read and the one write (:func:`offline.export_if_offline`,
    which ``cli/entry.py`` calls).
    """

    hits: list[str] = []
    for path in sorted(_SRC.glob("*/src/**/*.py")):
        if path.resolve() == _PREDICATE:
            continue
        for lineno, value in _name_literals(path):
            hits.append(f"{path.relative_to(_SRC)}:{lineno} {value}")
    assert hits == []
