"""#101 — the ``extending-aelix`` skill's pointer at the bundled guides.

The skill body is loaded on demand, so the pointer costs nothing per turn (a
NEW skill would have cost a catalog entry in the system prompt forever). What it
does cost is the usual risk of a document that names commands and files: these
tests re-derive every command and every claim in the added section from the
product, rather than reading the markdown back to itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from aelix_coding_agent.cli.config import packaged_skills_dir
from aelix_coding_agent.cli.docs import run_docs_command
from aelix_coding_agent.help import bundled_docs_dir, resolve_topic

_SKILL = Path(packaged_skills_dir()) / "extending-aelix" / "SKILL.md"


def _body() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_the_no_manifest_sentence_is_scoped_to_the_single_file_case() -> None:
    """The shipped sentence was an unscoped absolute — "There is no manifest, no
    JSON, no build step and nothing to install", followed by "If you find
    yourself inventing a config format, stop".

    True of the one-file extension it describes, false of Aelix: a manifest
    format exists, ships next door as ``examples/echo/aelix-plugin.toml``, and
    is how installed packs declare themselves. A model that read the absolute
    could refuse to write a legitimate manifest.
    """

    # Line wrapping is not the subject, so it is normalised away rather than
    # matched with an ``or`` over two spellings — a disjunction here would pass
    # on a body that says neither thing well.
    flat = " ".join(_body().split())
    assert "For that file there is no manifest, no JSON, no build step" in flat
    assert "inventing a config format for one, stop" in flat
    assert "aelix-plugin.toml" in flat

    # The format the scoping clause says exists really does ship, next to the
    # example this same skill tells the reader to open.
    package_root = Path(packaged_skills_dir()).parent
    assert package_root.name == "aelix_coding_agent", package_root
    example_manifest = package_root / "examples" / "echo" / "aelix-plugin.toml"
    assert example_manifest.is_file(), example_manifest


def test_every_docs_command_the_skill_prints_actually_runs(capsys) -> None:
    """The commands are extracted from the markdown and executed through the
    real ``run_docs_command``. A skill that prints a command which exits 2 is
    the "confidently cites something that is not there" failure in a new place.

    Exit 0 alone is not enough for the ``--search`` example: an empty search is
    a true answer and exits 0 with its notice on stderr, so a term that matches
    nothing would pass an exit-code-only check while the skill demonstrates its
    search with a query that finds nothing. Hence the stdout assertion.
    """

    body = _body()
    invocations = re.findall(r"^aelix docs(.*)$", body, flags=re.MULTILINE)
    assert invocations, "the skill no longer prints any `aelix docs` command"

    for tail in invocations:
        # Strip the trailing `# comment` the code block uses for annotation.
        args = tail.split("#", 1)[0].split()
        shown = f"aelix docs {' '.join(args)}".strip()
        assert run_docs_command(args) == 0, f"`{shown}` did not exit 0"
        assert capsys.readouterr().out.strip(), f"`{shown}` printed nothing"


def test_the_named_topic_resolves() -> None:
    """``extension-authoring`` is named in prose as well as in the code block,
    so it is pinned against the registry directly."""

    assert "aelix docs extension-authoring" in _body()
    topic = resolve_topic("extension-authoring")
    assert topic is not None
    assert topic.path.is_file()


def test_the_no_bash_fallback_route_is_real() -> None:
    """The fallback tells the reader the guides are "two directories up from
    this skill file" under ``aelix_coding_agent/docs/``.

    That route exists because the other one it names — "the system prompt prints
    that directory" — is not always available: ``--system-prompt`` replaces the
    base prompt (and with it the docs block) while STILL appending the skills
    catalog, so a custom-prompt session can be reading this skill with no
    absolute path anywhere in its context.
    """

    assert "two directories up from this skill file" in " ".join(_body().split())
    # SKILL.md -> extending-aelix -> skills -> aelix_coding_agent
    assert _SKILL.resolve().parents[2] == bundled_docs_dir().parent
    assert bundled_docs_dir().is_dir()


def test_the_plan_mode_caveat_is_true_of_the_real_permission_ladder() -> None:
    """The skill tells the reader to fall back to ``read`` because ``bash`` is
    blocked in plan mode. Re-derived from the ladder rather than trusted:
    ``bash`` is in ``_MUTATING``, PLAN blocks every mutating tool
    (``builtin/permission.py:436``), and that check sits ABOVE the read-only
    short-circuit at ``:441`` — so ``read`` is unaffected."""

    from aelix_coding_agent.builtin import permission

    assert "blocked in plan mode" in _body()
    assert "bash" in permission._MUTATING
    assert "read" not in permission._MUTATING
