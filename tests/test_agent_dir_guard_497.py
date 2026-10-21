"""The suite keeps out of the developer's ``~/.aelix/agent`` too (#497).

Four in-process CLI tests ran ``_async_main`` with neither ``sandbox_home`` nor
``AELIX_CODING_AGENT_DIR`` set. ``_async_main`` loads
``AuthStorage(<agent dir>/auth.json)`` before it dispatches, so each of them
made ``~/.aelix/agent``, ``chmod``-ed it to ``0700``, opened ``auth.json`` with
``O_RDWR | O_CREAT``, took the blocking ``flock`` a running aelix takes, and
read the developer's keys and tokens. Measured with a stand-in home, 59 more
tests reached it: 42 built harness options and listed
``~/.aelix/agent/extensions`` (with an extension installed there they import and
run it), 13 listed ``~/.aelix/extensions``, three read ``models.json`` or
``mcp.json``, and one ``run_tui`` test's mid-test ``monkeypatch.undo()`` let the
update check write ``update_check.json`` there (ADR-0026, #497 amendment).

``tests/session_root_guard.py`` now protects the whole ``~/.aelix`` and judges
reads and listings as well as writes. This file tests that part: the roots, the
reads, the auth load, and, end to end through the real ``conftest.py``, a test
that still reaches the agent dir. ``tests/test_session_root_guard_458.py`` keeps
the write cases. Every case that trips the guard on purpose points it at a
stand-in under ``tmp_path``, so nothing here comes near the developer's home.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import importlib
import json
import os
import re
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.oauth.auth_storage import AuthStorage
from aelix_coding_agent.cli.config import ENV_AGENT_DIR, ENV_SESSION_DIR, get_agent_dir

from tests import session_root_guard as srg
from tests.env_sandbox import sandbox_home
from tests.test_suite_session_isolation_458 import _run_inner_pytest

_FAKE_KEY = "sk-fake-497-not-a-real-key"


@pytest.fixture
def aelix_root(tmp_path: Path) -> Path:
    """A stand-in for ``~/.aelix``: an agent dir with an ``auth.json`` holding a
    fake key and one extension, and a session root."""

    root = tmp_path / "home" / ".aelix"
    agent = root / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "auth.json").write_text(
        json.dumps({"anthropic": {"type": "api_key", "key": _FAKE_KEY}}) + "\n", encoding="utf-8"
    )
    (root / "sessions").mkdir()
    return root


# === what is protected ===========================================================


def test_the_protected_root_holds_both_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_agent_dir()``'s default and ``JsonlSessionRepo``'s both lie under
    the protected ``~/.aelix``. If either default moves out, this fails, instead
    of the guard silently watching the old place."""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv(ENV_AGENT_DIR, raising=False)
    roots = srg.protected_roots({})

    assert get_agent_dir() == str(Path(srg.DEFAULT_AGENT_DIR).expanduser())
    assert srg._under(get_agent_dir(), roots)
    assert JsonlSessionRepo(fs=LocalFileSystem())._sessions_root_input == srg.DEFAULT_SESSIONS_ROOT
    assert srg._under(str(Path(srg.DEFAULT_SESSIONS_ROOT).expanduser()), roots)
    assert srg._norm(str(home / ".aelix")) in roots


def test_an_exported_agent_dir_is_protected_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A developer who exported ``AELIX_CODING_AGENT_DIR`` keeps their keys
    there, and a test that does not set its own would reach it. Spelled with
    ``~``, it is expanded as ``get_agent_dir`` expands it."""

    mine = tmp_path / "my-agent"
    assert srg._norm(str(mine)) in srg.protected_roots({ENV_AGENT_DIR: str(mine)})
    home = sandbox_home(monkeypatch, tmp_path / "home")
    assert srg._norm(str(home / "agent-x")) in srg.protected_roots({ENV_AGENT_DIR: "~/agent-x"})
    # Both exports at once: each is its own root.
    both = srg.protected_roots({ENV_AGENT_DIR: str(mine), ENV_SESSION_DIR: str(tmp_path / "s")})
    assert srg._norm(str(mine)) in both and srg._norm(str(tmp_path / "s")) in both


def test_an_exported_agent_dir_that_holds_the_temp_dir_is_not_protected(tmp_path: Path) -> None:
    """The temp-dir rule covers the new export as it covers the session dir."""

    temp = tmp_path / "temp"
    temp.mkdir()
    roots = srg.protected_roots({ENV_AGENT_DIR: str(tmp_path)}, temp_dir=str(temp))
    assert srg._norm(str(tmp_path)) not in roots


def test_a_sibling_of_dot_aelix_is_not_protected(aelix_root: Path) -> None:
    """``.aelix-old`` beside ``.aelix`` is another directory, and the home
    itself is not protected: only what lies under ``.aelix``."""

    sibling = aelix_root.parent / ".aelix-old"
    with srg.protecting([aelix_root]), srg.guard() as found:
        sibling.mkdir()
        (sibling / "auth.json").write_text("{}", encoding="utf-8")
        assert (sibling / "auth.json").read_text(encoding="utf-8") == "{}"
        assert ".aelix" in os.listdir(aelix_root.parent)
    assert found == []


@pytest.mark.parametrize("operation", ["rename", "rmtree"])
def test_dot_aelix_itself_is_judged_now(aelix_root: Path, tmp_path: Path, operation: str) -> None:
    """#458's root was ``~/.aelix/sessions``, so a rename or ``rmtree`` of
    ``~/.aelix`` itself was an ancestor's and went unseen. It is the root now."""

    with (
        srg.protecting([aelix_root]),
        srg.guard() as found,
        pytest.raises(PermissionError, match="#497"),
    ):
        if operation == "rename":
            os.rename(aelix_root, tmp_path / "moved")
        else:
            shutil.rmtree(aelix_root)

    assert len(found) == 1, found
    assert (aelix_root / "agent" / "auth.json").is_file()


# === in-process reads ============================================================


@pytest.mark.parametrize(
    ("operation", "kind"),
    [
        ("read_text", "open for reading"),
        ("open_rb", "open for reading"),
        ("os_open_read_only", "open for reading"),
        ("listdir", "list"),
        ("scandir", "list"),
        ("iterdir", "list"),
        ("path_glob", "list"),
        ("glob_module", "list"),
        ("os_walk", "list"),
    ],
)
def test_every_kind_of_read_under_the_root_is_blocked(
    aelix_root: Path, operation: str, kind: str
) -> None:
    """#458 judged writes only. A read is the #497 defect itself: the
    developer's ``auth.json`` in the test's memory. Some of these raise, and
    some swallow the error (``glob``, ``Path.glob``, ``os.walk``); the guard
    records each either way, and the test fails in teardown."""

    agent = aelix_root / "agent"
    auth = agent / "auth.json"
    actions = {
        "read_text": lambda: auth.read_text(encoding="utf-8"),
        "open_rb": lambda: auth.open("rb").read(),
        "os_open_read_only": lambda: os.close(os.open(auth, os.O_RDONLY)),
        "listdir": lambda: os.listdir(agent),
        "scandir": lambda: [entry.name for entry in os.scandir(agent)],
        "iterdir": lambda: list(agent.iterdir()),
        "path_glob": lambda: list(agent.glob("*.json")),
        "glob_module": lambda: glob.glob(os.path.join(str(agent), "*.json")),
        "os_walk": lambda: list(os.walk(aelix_root)),
    }
    got: object = None
    with (
        srg.protecting([aelix_root]),
        srg.guard() as found,
        contextlib.suppress(PermissionError),
    ):
        got = actions[operation]()

    assert found, f"{operation} under the root was not judged"
    assert all(item.startswith(kind + " ") for item in found), found
    assert _FAKE_KEY not in repr(got)
    assert "auth.json" not in repr(got), got


def test_an_extension_under_the_root_is_never_imported(
    aelix_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an extension installed in the developer's agent dir, a test that
    reaches it imports and runs it. The import system lists the directory
    first (``FileFinder``), the guard blocks that, and the module is not found."""

    marker = tmp_path / "planted-ran"
    planted = aelix_root / "agent" / "extensions" / "planted_ext_497.py"
    planted.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(planted.parent))
    monkeypatch.delitem(sys.modules, planted.stem, raising=False)

    with (
        srg.protecting([aelix_root]),
        srg.guard() as found,
        pytest.raises(ModuleNotFoundError),
    ):
        importlib.import_module(planted.stem)

    assert found and found[0].startswith("list "), found
    assert not marker.exists(), "the planted extension ran"


def test_the_auth_load_is_blocked_before_it_reads_a_key(aelix_root: Path) -> None:
    """The four tests' own path: ``AuthStorage(<agent dir>/auth.json).load()``.

    With ``fcntl`` it makes the dir, ``chmod``-s it to ``0700``, then opens the
    file ``O_RDWR | O_CREAT`` to lock it; the open is refused and ``load``
    raises. Without ``fcntl`` (Windows) it only reads, which is refused and
    swallowed into an empty store. Either way no key is loaded, and on POSIX
    the directory keeps its mode."""

    agent = aelix_root / "agent"
    if sys.platform != "win32":
        agent.chmod(0o755)
    mode_before = stat.S_IMODE(agent.stat().st_mode)
    storage = AuthStorage(agent / "auth.json")

    with (
        srg.protecting([aelix_root]),
        srg.guard() as found,
        contextlib.suppress(PermissionError),
    ):
        asyncio.run(storage.load())

    assert found, "the auth load was not judged"
    assert _FAKE_KEY not in repr(storage._data)  # noqa: SLF001 — what was loaded
    if sys.platform != "win32":
        assert any(item.startswith("open for writing ") for item in found), found
        assert stat.S_IMODE(agent.stat().st_mode) == mode_before
    assert _FAKE_KEY in (agent / "auth.json").read_text(encoding="utf-8")


# === end to end, through the real conftest ======================================

# The same shapes the sweep found, as a developer's test might still write them.
_INNER_TESTS = textwrap.dedent(
    """
    import asyncio
    import sys

    from aelix_agent_core.session.memory_storage import MemorySessionStorage
    from aelix_agent_core.session.session import Session
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _async_main, _build_harness_options

    from tests.env_sandbox import sandbox_home


    class _PipedStdin:
        def isatty(self):
            return False

        def read(self):
            return ""


    def _main(monkeypatch):
        monkeypatch.setattr(sys, "stdin", _PipedStdin())
        # The test swallows the failure, and the guard must still fail it.
        try:
            return asyncio.run(_async_main(["--no-session"]))
        except BaseException:
            return None


    def test_a_cli_run_with_no_isolation(monkeypatch):
        # The four tests' shape before #497: no sandbox_home, no AELIX_CODING_AGENT_DIR.
        _main(monkeypatch)


    def test_harness_options_with_no_isolation(tmp_path, monkeypatch):
        # The listing tests' shape: the global extension dir is the home's.
        monkeypatch.chdir(tmp_path)
        try:
            asyncio.run(_build_harness_options(Args(), Session(MemorySessionStorage())))
        except BaseException:
            pass


    def test_the_fixed_shape_passes(tmp_path, monkeypatch):
        # The four tests' shape after #497.
        sandbox_home(monkeypatch, tmp_path / "home")
        assert _main(monkeypatch) in (0, 1)
    """
)

_PLANTED_EXTENSION = textwrap.dedent(
    """
    from pathlib import Path

    Path({marker!r}).write_text("imported", encoding="utf-8")


    def setup(aelix):
        Path({marker!r}).write_text("ran", encoding="utf-8")
    """
)


def test_a_test_that_still_reaches_the_agent_dir_fails_and_touches_nothing(
    tmp_path: Path,
) -> None:
    """THE #497 SHAPE, red before the fix: a child pytest runs this repository's
    ``conftest.py`` with HOME at a stand-in home that holds an ``auth.json``
    and one extension. The CLI run and the harness build each reach that
    home's agent dir, catch what the guard raises, and must still ERROR at
    teardown. The control, sandboxed the way the four tests are now, passes.

    On this lane's base (``f6d41f77``) all three passed, and the stand-in home
    changed: its agent dir went to mode ``0700``, and the harness build
    imported the planted extension, ran its ``setup`` and wrote
    ``extensions/__pycache__`` beside it.
    """

    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_reach.py").write_text(_INNER_TESTS, encoding="utf-8")
    home = tmp_path / "home"
    agent = home / ".aelix" / "agent"
    (agent / "extensions").mkdir(parents=True)
    auth = agent / "auth.json"
    auth.write_text("{}\n", encoding="utf-8")
    marker = tmp_path / "planted-ran"
    (agent / "extensions" / "planted_497.py").write_text(
        _PLANTED_EXTENSION.format(marker=str(marker)), encoding="utf-8"
    )
    if sys.platform != "win32":
        agent.chmod(0o755)
    mode_before = stat.S_IMODE(agent.stat().st_mode)
    before = sorted(p.relative_to(home) for p in home.rglob("*"))

    result = _run_inner_pytest(inner, home)
    out = result.stdout + result.stderr

    assert not marker.exists(), f"the planted extension ran: {marker.read_text(encoding='utf-8')}"
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == before, out
    assert auth.read_text(encoding="utf-8") == "{}\n"
    assert stat.S_IMODE(agent.stat().st_mode) == mode_before  # no ``chmod 0700``
    # A test that errors at teardown counts as passed too, as in #458's own child run.
    assert re.search(r"\b3 passed, 2 errors\b", result.stdout), out
    assert "ERROR at teardown of test_a_cli_run_with_no_isolation" in result.stdout, out
    assert "ERROR at teardown of test_harness_options_with_no_isolation" in result.stdout, out
    assert result.stdout.count("#458/#497: this test reached the developer's REAL ~/.aelix") >= 2
    # With ``fcntl`` the auth load opens ``auth.json`` for writing first (the
    # lock); without it (Windows) it only reads. Either is judged.
    assert re.search(r"open for (writing|reading) \S*auth\.json", result.stdout), out
    assert "list " in result.stdout and "extensions" in result.stdout, out
