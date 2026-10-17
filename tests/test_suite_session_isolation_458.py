"""The suite keeps its sessions out of the developer's ``~/.aelix/sessions`` (#458).

Running the suite left a ``--<pytest tmp path>--`` directory in the developer's
real session root for every test that ran the CLI. The owner's machine had about
7,450 of them, 492 from a single day. Sessions go to
``AELIX_CODING_AGENT_SESSION_DIR`` and default to ``~/.aelix/sessions``
(``cli/config.py`` ``get_session_dir``). The suite set neither that variable nor
HOME for the tests that leaked. Isolating ``AELIX_CODING_AGENT_DIR`` moves
settings and auth, not sessions. Measured on 529b997e with a scratch HOME, the
full suite left six directories there, one per CLI test in
``tests/cli/test_tools_flag_validation.py``.

THESE RUN AGAINST THE REAL ``tests/conftest.py`` and import nothing from
``tests/session_root_guard.py``. On a tree without the fix they fail for the
reason the issue names, not on an import error. ``tests/test_session_root_guard_458.py``
tests the guard's parts. None of these tests can write the developer's home even
without the fix: each one sandboxes HOME first, so a leak lands in ``tmp_path``.

What is covered: the pin, for in-process code that reads the variable and for
every child that inherits the test's environment, a wider-scoped fixture's own
value reaching its tests (it stays in effect until that fixture is torn down;
for a session-scoped one, not autouse, that is every later test until the run
ends), and an empty value pinned like an unset one; the guard, a best-effort
net, for the in-process operations ``tests/session_root_guard.py`` lists, not
the ones it names as missed. A child given a hand-built environment without the
pin, a shell, ``env`` or ``uv run`` wrapper that drops it, and an
``aelix-server`` child (whose ``/rpc`` ignores the variable) are covered by
neither, and nothing here claims them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.cli.config import ENV_SESSION_DIR, get_session_dir
from aelix_coding_agent.cli.entry import _async_main

from tests.env_sandbox import child_env, sandbox_home

ROOT = Path(__file__).resolve().parents[1]

# ``tests/cli/test_tools_flag_validation.py``'s first case: the tool check fails
# after the session file exists and before any request, so no network is used.
_LEAKING_CLI_ARGS = (
    "-m",
    "aelix_coding_agent",
    "--print",
    "--model",
    "claude-haiku-4-5",
    "--provider",
    "anthropic",
    "--tools",
    "Bash,Frobnicate",
    "hi",
)


def _cli_env(cwd: Path) -> dict[str, str]:
    """``test_tools_flag_validation._run``'s environment, the shape that leaked.

    ``dict(os.environ)`` plus a fake key and an isolated agent dir: settings and
    auth are isolated, and the session root is whatever the parent has.
    """

    (cwd / "agentdir").mkdir(exist_ok=True)
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = "sk-fake-not-a-real-key"
    env["AELIX_CODING_AGENT_DIR"] = str(cwd / "agentdir")
    env["AELIX_SETTINGS_PATH"] = str(cwd / "agentdir" / "settings.json")
    return env


_SEEN: dict[str, str] = {}


@pytest.mark.parametrize("case", ["first", "second"])
def test_every_test_starts_with_a_session_root_of_its_own(case: str) -> None:
    """The pin is per TEST. A per-run root would let a ``--continue`` or a
    session list in one test find another test's sessions."""

    root = os.environ.get(ENV_SESSION_DIR)
    assert root, "tests/conftest.py did not pin AELIX_CODING_AGENT_SESSION_DIR"
    assert Path(root).is_absolute()
    # A string comparison only: the developer's real root is never touched.
    real = os.path.normcase(str(Path.home() / ".aelix" / "sessions"))
    assert not os.path.normcase(root).startswith(real)
    _SEEN[case] = root
    if case == "second":
        if "first" not in _SEEN:
            pytest.skip("compares against the 'first' case, which did not run")
        assert _SEEN["first"] != root


@pytest.fixture(scope="module")
def module_root() -> str | None:
    """What a module-scoped fixture sees. It is set up before any test's own pin."""

    return os.environ.get(ENV_SESSION_DIR)


def test_a_module_scoped_fixture_sees_a_root_that_is_not_the_developers(
    module_root: str | None,
) -> None:
    """A wider-scoped fixture is set up before the per-test pin, so the run has a
    root of its own as well (``_run_session_root``). Measured with a scratch
    HOME, the packaging gate's wheel builds start children at that point."""

    assert module_root, "a module-scoped fixture ran with no session root pinned"
    real = os.path.normcase(str(Path.home() / ".aelix" / "sessions"))
    assert not os.path.normcase(module_root).startswith(real)
    assert module_root != os.environ.get(ENV_SESSION_DIR)


def test_a_test_that_sets_its_own_session_dir_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin is a default. ``test_child_session_records.py`` and
    ``tests/cli/test_session_flags.py`` set the variable themselves, and the
    last ``monkeypatch`` write wins."""

    monkeypatch.setenv(ENV_SESSION_DIR, str(tmp_path / "mine"))
    assert get_session_dir() == str(tmp_path / "mine")


def _run_inner_pytest(
    inner: Path, home: Path, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    """Run ``inner``'s tests in a child pytest under this repository's real ``conftest.py``.

    HOME is ``home``. The child's basetemp is a fresh short directory in the
    system temp dir, not under this test's ``tmp_path``: the windows leg runs
    this, and an inner session path must stay under ``MAX_PATH``.
    """

    (inner / "conftest.py").write_text(
        (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    short = Path(tempfile.mkdtemp(prefix="i458-"))
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(ROOT / "pyproject.toml"),
                # #448: --confcutdir keeps the child out of the parent's basetemp.
                "--rootdir",
                str(inner),
                "--confcutdir",
                str(inner),
                "--basetemp",
                str(short / "b"),
                "-q",
                "-p",
                "no:cacheprovider",
                str(inner),
            ],
            cwd=str(inner),
            env=child_env(home, **extra_env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    finally:
        shutil.rmtree(short, ignore_errors=True)


_WIDER_MODULE = textwrap.dedent(
    """
    import os
    import subprocess
    import sys

    import pytest

    SHARED = {shared!r}

    @pytest.fixture(scope="module")
    def shared_root():
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("AELIX_CODING_AGENT_SESSION_DIR", SHARED)
            yield SHARED

    def test_in_process(shared_root):
        assert os.environ["AELIX_CODING_AGENT_SESSION_DIR"] == shared_root

    def test_in_a_child_built_from_os_environ(shared_root):
        code = "import os; print(os.environ['AELIX_CODING_AGENT_SESSION_DIR'])"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=120).stdout.strip()
        assert out == shared_root
    """
)

_WIDER_CLASS = textwrap.dedent(
    """
    import os
    from pathlib import Path

    import pytest

    SHARED = {shared!r}

    def _assert_pinned_per_test():
        value = os.environ["AELIX_CODING_AGENT_SESSION_DIR"]
        assert value != SHARED and Path(value).parent.name.startswith("aelix-s-"), value

    def test_pinned_again_once_the_module_fixture_is_gone():
        _assert_pinned_per_test()

    class TestAClassScopedFixture:
        @pytest.fixture(scope="class")
        def class_root(self):
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("AELIX_CODING_AGENT_SESSION_DIR", SHARED + "-class")
                yield SHARED + "-class"

        def test_in_process(self, class_root):
            assert os.environ["AELIX_CODING_AGENT_SESSION_DIR"] == class_root

    def test_pinned_again_once_the_class_fixture_is_gone():
        _assert_pinned_per_test()
    """
)


def test_a_wider_scoped_fixture_that_sets_the_session_dir_reaches_its_tests(
    tmp_path: Path,
) -> None:
    """A module- or class-scoped fixture that sets the variable is set up before
    the per-test pin. The pin applies only when the variable is unset, empty or
    still the run's root at the test's start, so that fixture's value reaches
    its tests and the children they start from ``os.environ``, and the tests
    after its module or class are pinned again. A session-scoped fixture (not
    autouse) is torn down only when the run ends, so its value stays in effect
    for every test that runs after it. With 9bffd71d's conftest (review round 2)
    the pin overrode it in process and in the child: ``3 failed, 2 passed``.
    """

    inner = tmp_path / "inner"
    inner.mkdir()
    shared = str(tmp_path / "shared")
    (inner / "test_a_module.py").write_text(_WIDER_MODULE.format(shared=shared), encoding="utf-8")
    (inner / "test_b_class.py").write_text(_WIDER_CLASS.format(shared=shared), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_inner_pytest(inner, home)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert re.search(r"\b5 passed\b", result.stdout), out
    assert not (home / ".aelix" / "sessions").exists()


_EMPTY_MODULE = textwrap.dedent(
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    import pytest
    from aelix_coding_agent.cli.config import get_session_dir

    @pytest.fixture(scope="module")
    def empty_root():
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("AELIX_CODING_AGENT_SESSION_DIR", "")
            yield

    def _assert_pinned_per_test(value):
        assert value and Path(value).parent.name.startswith("aelix-s-"), repr(value)

    def test_in_process(empty_root):
        _assert_pinned_per_test(os.environ["AELIX_CODING_AGENT_SESSION_DIR"])
        assert get_session_dir() is not None

    def test_in_a_child_built_from_os_environ(empty_root):
        code = "import os; print(os.environ['AELIX_CODING_AGENT_SESSION_DIR'])"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=120).stdout.strip()
        _assert_pinned_per_test(out)
    """
)


def test_a_wider_scoped_fixture_that_empties_the_session_dir_is_pinned_anyway(
    tmp_path: Path,
) -> None:
    """``get_session_dir()`` reads an empty value as unset, so code under it, and
    a child that inherits it, would fall back to ``~/.aelix/sessions``. The pin
    treats ``''`` as unset too: a module-scoped fixture that sets the variable to
    ``''`` gets the per-test root in its tests. With c8aabb64's conftest (review
    round 3) the empty value reached them: ``2 failed``.
    """

    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_empty.py").write_text(_EMPTY_MODULE, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_inner_pytest(inner, home)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert re.search(r"\b2 passed\b", result.stdout), out
    assert not (home / ".aelix" / "sessions").exists()


def test_a_cli_child_built_from_os_environ_writes_under_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE #458 SHAPE. Red before the fix: the child put its session under HOME.

    The child is ``test_tools_flag_validation.py``'s, given the environment
    that test gives it. HOME is sandboxed here, so without the pin the session
    lands in ``tmp_path/home`` and not in the developer's home.
    """

    home = sandbox_home(monkeypatch, tmp_path / "home")
    result = subprocess.run(
        [sys.executable, *_LEAKING_CLI_ARGS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
        env=_cli_env(tmp_path),
    )

    assert result.returncode == 1 and "Frobnicate" in result.stderr, result.stderr
    leaked = home / ".aelix" / "sessions"
    assert not leaked.exists(), f"the child's session went under HOME: {sorted(leaked.iterdir())}"
    pinned = os.environ.get(ENV_SESSION_DIR)
    assert pinned, "tests/conftest.py did not pin AELIX_CODING_AGENT_SESSION_DIR"
    assert list(Path(pinned).rglob("*.jsonl")), "the child's session is not under the pin"


async def test_a_test_may_still_use_the_default_root_under_a_sandboxed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard protects the REAL home's root, not every ``.aelix/sessions``.

    A test that removes the pin and sandboxes HOME exercises the product's own
    default, and a guard that blocked that path everywhere would be a plausible
    wrong form. The agent dir and settings follow the sandboxed home too: an
    exported ``AELIX_CODING_AGENT_DIR`` would otherwise get this run's
    ``auth.json``.
    """

    class _PipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    for name in (ENV_SESSION_DIR, "AELIX_CODING_AGENT_DIR", "AELIX_SETTINGS_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "stdin", _PipedStdin())
    code = await _async_main(["--print"])
    assert code in (0, 1)
    assert list((home / ".aelix" / "sessions").rglob("*.jsonl"))


# Paths are short on purpose. An inner session path is a root in the system temp
# dir, a bucket that encodes the inner tmp_path, and a 67-character file name,
# the windows leg runs this, and MAX_PATH is 260 there. So the child's basetemp
# is a fresh directory in the system temp dir (``_run_inner_pytest``).
_INNER_TESTS = textwrap.dedent(
    """
    import asyncio
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
    from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions

    ARGS = {args!r}
    EXPORTED = {exported!r}

    def _env(cwd):
        (cwd / "agentdir").mkdir(exist_ok=True)
        env = dict(os.environ)
        env["ANTHROPIC_API_KEY"] = "sk-fake-not-a-real-key"
        env["AELIX_CODING_AGENT_DIR"] = str(cwd / "agentdir")
        env["AELIX_SETTINGS_PATH"] = str(cwd / "agentdir" / "settings.json")
        return env

    def _create(repo, cwd):
        # The test swallows the failure, and the guard must still fail it.
        try:
            asyncio.run(repo.create(JsonlSessionCreateOptions(cwd=str(cwd))))
        except Exception:
            pass

    def test_a(tmp_path):
        # In process, with no sessions_root: the repo's own default under HOME.
        _create(JsonlSessionRepo(fs=LocalFileSystem()), tmp_path)

    def test_b(tmp_path):
        # In process, into the session dir the developer exported for the run.
        _create(JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=EXPORTED), tmp_path)

    def test_c(tmp_path):
        # The control: a CLI child that inherits the pin writes under it.
        pin = os.environ["AELIX_CODING_AGENT_SESSION_DIR"]
        subprocess.run([sys.executable, *ARGS], cwd=str(tmp_path), env=_env(tmp_path),
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        assert list(Path(pin).rglob("*.jsonl")), "the child's session is not under the pin"
        assert Path(pin).parent.parent == Path(tempfile.gettempdir())
        (Path(__file__).parent / "pin.txt").write_text(pin, encoding="utf-8")
    """
)


def test_a_test_that_still_reaches_the_real_root_fails_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """A test whose in-process repo creates a session in a real root fails, and writes nothing.

    A child pytest runs this repository's ``conftest.py`` with HOME pointed at a
    scratch directory and ``AELIX_CODING_AGENT_SESSION_DIR`` exported, so the
    scratch home's root and the exported dir are the roots the guard protects.
    ``test_a`` writes the first in process and ``test_b`` the second, and both
    catch the error the way code under test might. Each must still ERROR at
    teardown. ``test_c``, a CLI child that inherits the pin, must pass, and the
    run must remove its root (``aelix-s-*``) when it ends. Measured on
    529b997e: ``test_a`` and ``test_b`` passed and left a session directory in
    the scratch home's root and in the exported dir, and ``test_c`` failed,
    because with no pin its session went to the exported dir.
    """

    inner = tmp_path / "inner"
    inner.mkdir()
    exported = tmp_path / "exported"
    (inner / "test_leak.py").write_text(
        _INNER_TESTS.format(args=_LEAKING_CLI_ARGS, exported=str(exported)), encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()

    result = _run_inner_pytest(inner, home, AELIX_CODING_AGENT_SESSION_DIR=str(exported))
    out = result.stdout + result.stderr

    leaked = home / ".aelix" / "sessions"
    assert not leaked.exists(), f"a session reached the protected root: {sorted(leaked.iterdir())}"
    assert not exported.exists(), (
        f"a session reached the exported root: {sorted(exported.iterdir())}"
    )
    assert re.search(r"\b3 passed, 2 errors\b", result.stdout), out
    assert "ERROR at teardown of test_a" in result.stdout, out
    assert "ERROR at teardown of test_b" in result.stdout, out
    assert "ERROR at teardown of test_c" not in result.stdout, out
    reported = result.stdout.count("#458: this test wrote under the developer's REAL session root")
    assert reported >= 2, out
    pin = Path((inner / "pin.txt").read_text(encoding="utf-8"))
    assert not pin.parent.exists(), f"the run left its session roots behind: {pin.parent}"
