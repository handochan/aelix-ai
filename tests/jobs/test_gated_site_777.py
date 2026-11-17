"""#777 PR c0: ``tests/jobs/gated_site/sitecustomize.py`` gates only an interpreter whose main module is aelix.

The launcher puts that directory first on its process's ``PYTHONPATH``, so every
``python`` the agent's tools run imports the hook. Each case starts a real
interpreter with that directory and, after it, a second one whose own
``sitecustomize`` prints ``activation.ACTIVE`` as the interpreter starts: the
line is there only if the hook imported the ``sitecustomize`` it hides. Since
#779 the gate is shipped on, so a gated interpreter and one the hook left alone
can read the same value; which one the hook gated is read from what only a gated
one does: it announces itself (``AELIX_TEST_ANNOUNCE``), and one it left alone
reads what the same command reads with no hook.

Fails on ``639dcdf6`` (#777 fix round 1): the hook read ``-m aelix_coding_agent``
anywhere in ``sys.orig_argv``, so the ``-c`` and script cases read ``True``
(Codex C2, verify mutant T4), and it imported no second ``sitecustomize``, so
no case prints anything. The two-copies cases fail on ``8f07404f`` (fix round 2):
its chain skipped only its own directory, so a second copy of the hook — another
checkout's, or this one by another spelling — imported the first again, forever
(``RecursionError``, verify-r2 F1), and the foreign one was never reached.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.env_sandbox import child_env
from tests.jobs.gated_cli import GATED_SITE

_PROBE = (
    "import sys\n"
    "from aelix_coding_agent.jobs import activation\n"
    "print('ACTIVE', activation.ACTIVE, file=sys.stderr)\n"
)


def _run(
    tmp_path: Path, args: list[str], *sites: Path, after: tuple[Path, ...] = (), **extra: str
) -> subprocess.CompletedProcess[str]:
    """``python <args>`` with ``sites``, then a foreign ``sitecustomize`` that prints ``ACTIVE``,
    then ``after``; ``extra`` goes into its environment."""

    other = tmp_path / "other_site"
    other.mkdir(exist_ok=True)
    (other / "sitecustomize.py").write_text(_PROBE, encoding="utf-8")
    entries = [*map(str, sites), str(other), *map(str, after), os.environ.get("PYTHONPATH")]
    env = child_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join(p for p in entries if p),
        AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
        **extra,
    )
    return subprocess.run(
        [sys.executable, *args],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _actives(done: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in done.stderr.splitlines() if line.startswith("ACTIVE ")]


@pytest.mark.parametrize(
    ("args", "gated"),
    [
        (["-P", "-m", "aelix_coding_agent", "--version"], True),
        (["-Pmaelix_coding_agent", "--version"], True),
        (["-c", "pass", "-m", "aelix_coding_agent"], False),
        (["{script}", "-m", "aelix_coding_agent"], False),
        (["-m", "json.tool", "-m", "aelix_coding_agent"], False),
        (["-c", "aelix_coding_agent"], False),
    ],
    ids=["-m", "-Pm-joined", "-c-then-m", "script-then-m", "other-module-then-m", "-c-named-like-it"],
)
def test_only_an_interpreter_running_aelix_as_its_module_is_gated(
    tmp_path: Path, args: list[str], gated: bool
) -> None:
    """A gated interpreter reads ``ACTIVE True`` and announces itself; any other reads what it
    reads with no hook at all, and announces nothing.

    #779: ``ACTIVE`` is shipped on, so before fix-r1 the four not-gated cases read
    ``ACTIVE True`` as a hard-coded value and would fail under a rollback
    (verify r1 F6). They now compare with the same command run without the hook,
    which reads the shipped value, whatever it is.
    """

    script = tmp_path / "script.py"
    script.write_text("pass\n", encoding="utf-8")
    argv = [str(script) if a == "{script}" else a for a in args]
    marks = tmp_path / "marks"
    marks.mkdir()
    done = _run(tmp_path, argv, GATED_SITE, AELIX_TEST_ANNOUNCE=str(marks))
    control = _run(tmp_path, argv)
    assert len(_actives(control)) == 1, control.stderr
    assert _actives(done) == (["ACTIVE True"] if gated else _actives(control)), done.stderr
    assert bool(list(marks.iterdir())) is gated, done.stderr


def test_a_second_copy_of_the_hook_is_skipped_and_the_foreign_one_imported(tmp_path: Path) -> None:
    """Two checkouts' ``gated_site`` on one ``PYTHONPATH``: gated once, the foreign one imported once."""

    copy = tmp_path / "second_checkout" / "gated_site"
    shutil.copytree(GATED_SITE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    later = tmp_path / "later_site"
    later.mkdir()
    (later / "sitecustomize.py").write_text("import sys\nprint('LATER', file=sys.stderr)\n", encoding="utf-8")
    done = _run(tmp_path, ["-P", "-m", "aelix_coding_agent", "--version"], GATED_SITE, copy, after=(later,))
    assert _actives(done) == ["ACTIVE True"], done.stderr
    assert "LATER" not in done.stderr, "only the first foreign sitecustomize is imported, as Python would"
    assert "Error in sitecustomize" not in done.stderr and done.returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="a directory symlink needs privileges on win32")
def test_the_same_hook_by_another_spelling_is_skipped(tmp_path: Path) -> None:
    """The ``/tmp`` and ``/private/tmp`` case: one directory reached by two paths."""

    link = tmp_path / "link"
    link.symlink_to(GATED_SITE, target_is_directory=True)
    done = _run(tmp_path, ["-P", "-m", "aelix_coding_agent", "--version"], GATED_SITE, link)
    assert _actives(done) == ["ACTIVE True"], done.stderr
    assert "Error in sitecustomize" not in done.stderr and done.returncode == 0


@pytest.mark.parametrize(
    ("args", "announced"),
    [
        (["-P", "-m", "aelix_coding_agent", "--version"], True),
        (["-c", "pass", "-m", "aelix_coding_agent"], False),
    ],
    ids=["gated", "not-gated"],
)
def test_with_the_announce_directory_set_a_gated_interpreter_writes_its_pid_and_start_time(
    tmp_path: Path, args: list[str], announced: bool
) -> None:
    """#777 PR c1: the parent-death cases watch a delegated child by this file (``AELIX_TEST_ANNOUNCE``)."""

    marks = tmp_path / "marks"
    marks.mkdir()
    done = _run(tmp_path, args, GATED_SITE, AELIX_TEST_ANNOUNCE=str(marks))
    assert done.returncode == 0, done.stderr
    files = sorted(marks.iterdir())
    if not announced:
        assert files == []
        return
    (only,) = files
    data = json.loads(only.read_text(encoding="utf-8"))
    assert only.name == f"child-{data['pid']}.json" and data["role"] == "child"
    assert data["pid"] != os.getpid() and isinstance(data["started"], float)
