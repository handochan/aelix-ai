"""#777 PR c0: ``tests/jobs/gated_site/sitecustomize.py`` gates only an interpreter whose main module is aelix.

The launcher puts that directory first on its process's ``PYTHONPATH``, so every
``python`` the agent's tools run imports the hook. Each case starts a real
interpreter with that directory and, after it, a second one whose own
``sitecustomize`` prints ``activation.ACTIVE`` as the interpreter starts: the
line is there only if the hook imported the ``sitecustomize`` it hides, and it
reads ``True`` only if the hook turned the gate on.

Fails on ``639dcdf6`` (#777 fix round 1): the hook read ``-m aelix_coding_agent``
anywhere in ``sys.orig_argv``, so the ``-c`` and script cases read ``True``
(Codex C2, verify mutant T4), and it imported no second ``sitecustomize``, so
no case prints anything. The two-copies cases fail on ``8f07404f`` (fix round 2):
its chain skipped only its own directory, so a second copy of the hook — another
checkout's, or this one by another spelling — imported the first again, forever
(``RecursionError``, verify-r2 F1), and the foreign one was never reached.
"""

from __future__ import annotations

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
    tmp_path: Path, args: list[str], *sites: Path, after: tuple[Path, ...] = ()
) -> subprocess.CompletedProcess[str]:
    """``python <args>`` with ``sites``, then a foreign ``sitecustomize`` that prints ``ACTIVE``,
    then ``after``."""

    other = tmp_path / "other_site"
    other.mkdir(exist_ok=True)
    (other / "sitecustomize.py").write_text(_PROBE, encoding="utf-8")
    entries = [*map(str, sites), str(other), *map(str, after), os.environ.get("PYTHONPATH")]
    env = child_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join(p for p in entries if p),
        AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
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
    script = tmp_path / "script.py"
    script.write_text("pass\n", encoding="utf-8")
    done = _run(tmp_path, [str(script) if a == "{script}" else a for a in args], GATED_SITE)
    assert _actives(done) == [f"ACTIVE {gated}"], done.stderr


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
