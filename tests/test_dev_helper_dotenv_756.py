"""#756: the dev helpers that start aelix hand the child only what aelix's own loader admits.

``scripts/diag_tui_live.py`` seeded the child's environment from ``$PWD/.env``
with a parser of its own, and ``tui_capture.py`` (the aelix-live-check skill)
read ``--key`` values out of ``--dotenv`` the same way. Both copied a value
aelix itself refuses, so the child's ``bash -c`` got it anyway: Codex measured
``EVIL_API_KEY=a[$(touch PWNED)]`` surviving into the child, unrecorded in
``AELIX_DOTENV_ADMITTED``, and ``BASH_ENV=./marker`` running before a
``bash -c`` body. Both helpers now run the file through ``load_dotenv``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="both helpers import POSIX-only modules (fcntl, termios)"
)

# One line per way the helpers' own parsers let a value through: a credential name
# with shell code for a value, a name ADR-0203 locks, and an ``export`` line, which
# aelix refuses as a bad name. The last line is the control that must still load.
HOSTILE_DOTENV = (
    "EVIL_API_KEY=a[$(touch PWNED)]\n"
    "BASH_ENV=./marker\n"
    "export EXPORTED_API_KEY=sk-exported-9\n"
    "GOOD_API_KEY=sk-good-9\n"
)
NAMES = ("EVIL_API_KEY", "BASH_ENV", "EXPORTED_API_KEY", "GOOD_API_KEY", "AELIX_DOTENV_ADMITTED")


def _module(path: Path, name: str, monkeypatch):
    # No ``__pycache__`` beside a skill script: nothing else imports one.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
    # load_dotenv writes os.environ directly; monkeypatch has nothing to restore
    # for a name that was absent.
    for name in NAMES:
        os.environ.pop(name, None)


@pytest.mark.usefixtures("clean_env")
def test_tui_capture_takes_from_dotenv_only_what_aelix_would_load(tmp_path, monkeypatch) -> None:
    """On ``ced24970`` ``read_dotenv`` returned all four values."""

    module = _module(
        REPO / ".agents" / "skills" / "aelix-live-check" / "scripts" / "tui_capture.py",
        "tui_capture_756",
        monkeypatch,
    )
    env_file = tmp_path / ".env"
    env_file.write_text(HOSTILE_DOTENV, encoding="utf-8")
    before = dict(os.environ)
    got = {name: module.read_dotenv(env_file, name) for name in NAMES[:4]}
    assert dict(os.environ) == before, "read_dotenv left the dotenv's names in this process"
    assert got == {
        "EVIL_API_KEY": None,
        "BASH_ENV": None,
        "EXPORTED_API_KEY": None,
        "GOOD_API_KEY": "sk-good-9",
    }


@pytest.mark.usefixtures("clean_env")
def test_diag_tui_live_seeds_only_what_aelix_would_load(tmp_path, monkeypatch) -> None:
    """``$PWD/.env`` is the path a cloned repo supplies. On ``ced24970`` all four lines
    reached the child's environment and none was recorded as coming from a ``.env``."""

    module = _module(REPO / "scripts" / "diag_tui_live.py", "diag_tui_live_756", monkeypatch)
    (tmp_path / ".env").write_text(HOSTILE_DOTENV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    env: dict[str, str] = {}
    used = module._load_dotenv(env, None, str(tmp_path / "no-checkout-here"))
    assert used is not None and os.path.samefile(used, tmp_path / ".env")
    assert sorted(env) == ["AELIX_DOTENV_ADMITTED", "GOOD_API_KEY"]
    assert env["GOOD_API_KEY"] == "sk-good-9"
    assert env["AELIX_DOTENV_ADMITTED"] == "GOOD_API_KEY"


@pytest.mark.usefixtures("clean_env")
def test_diag_tui_live_keeps_an_inherited_record(tmp_path, monkeypatch) -> None:
    """A nested run inherits ``AELIX_DOTENV_ADMITTED``; the child gets it with this
    ``.env``'s names added. ``load_dotenv`` rewrites the inherited record in place,
    so a helper that copied only keys new to the environment would hand the child
    no record at all (the round-2 verify's surviving mutant)."""

    module = _module(REPO / "scripts" / "diag_tui_live.py", "diag_tui_live_756b", monkeypatch)
    (tmp_path / ".env").write_text(HOSTILE_DOTENV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "INHERITED_API_KEY")
    env: dict[str, str] = {}
    module._load_dotenv(env, None, str(tmp_path / "no-checkout-here"))
    assert sorted(env) == ["AELIX_DOTENV_ADMITTED", "GOOD_API_KEY"]
    assert env["AELIX_DOTENV_ADMITTED"] == "GOOD_API_KEY,INHERITED_API_KEY"
