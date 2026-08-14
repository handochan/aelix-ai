"""The Help API: topics derived from files, aliases that point somewhere, and
an import graph light enough for a tool to pay for (#101).

``aelix_coding_agent.help`` is the SINGLE source of the topic list — the CLI
reads it, and any in-agent docs tool will read it. Two things can quietly break
that: a topic list that stops matching the shipped files, and an alias naming a
document that no longer exists. Both are asserted below by DERIVING the expected
answer from the directory rather than restating a constant the module also
defines.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from aelix_coding_agent.help import (
    ALIASES,
    NEAR_MISSES,
    bundled_docs_dir,
    read_topic,
    resolve_topic,
    search_topics,
    topic_names,
    topics,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_docs_dir_resolves_to_the_package_not_its_parent() -> None:
    """The measurement the resolution depth turns on.

    ``parents[1]`` is the package directory; ``parents[2]`` is ``src/`` in a
    checkout and ``site-packages/`` in an install. Getting it wrong does not
    raise — it yields a path that is simply absent or empty, which reads as "no
    guides are bundled". So assert the SHAPE of the answer, not just that it
    resolved.
    """
    docs = bundled_docs_dir()
    assert docs.name == "docs"
    assert docs.parent.name == "aelix_coding_agent"
    assert docs.is_dir(), f"{docs} is not a directory"
    assert list(docs.glob("*.md")), f"{docs} has no guides in it"


def test_topics_are_derived_from_the_files_on_disk() -> None:
    """Not a hardcoded list. Derived from the directory, and compared against
    the directory read independently here — if the module ever grows a literal
    list, adding a guide breaks this."""
    on_disk = sorted(p.stem for p in bundled_docs_dir().glob("*.md"))
    assert topic_names() == on_disk
    assert len(on_disk) >= 7


def test_a_new_guide_becomes_a_topic_with_no_code_change(tmp_path: Path) -> None:
    """The property a hardcoded list cannot have.

    Plants a file in the real bundled directory, asserts it is offered, and
    removes it. Planted with a name no guide will ever use so a crash between
    the two cannot leave something plausible behind.
    """
    planted = bundled_docs_dir() / "zz-registry-probe.md"
    assert not planted.exists()
    planted.write_text("# Registry Probe\n\nbody\n", encoding="utf-8")
    try:
        assert "zz-registry-probe" in topic_names()
        found = resolve_topic("zz-registry-probe")
        assert found is not None
        assert found.title == "Registry Probe"
        assert read_topic(found) == "# Registry Probe\n\nbody\n"
    finally:
        planted.unlink()
    assert "zz-registry-probe" not in topic_names()


def test_every_alias_points_at_a_real_topic() -> None:
    """A typo in ALIASES is otherwise invisible: `resolve_topic` returns None
    and the user gets "unknown topic" for a name the table claims to handle."""
    names = set(topic_names())
    dangling = {k: v for k, v in ALIASES.items() if v not in names}
    assert dangling == {}, f"aliases naming a topic that does not exist: {dangling}"


def test_the_issue_five_names_are_all_accounted_for() -> None:
    """#101 names five: extension, extension-api, tools, skills, security.

    Four resolve to a document. `skills` deliberately does NOT — no guide covers
    writing one — so it is required to be a declared gap rather than silently
    absent. This assertion is what stops "we forgot skills" from looking the
    same as "we decided about skills".
    """
    for name in ("extension", "extension-api", "tools", "security"):
        assert resolve_topic(name) is not None, name
    assert resolve_topic("skills") is None
    assert "skills" in NEAR_MISSES


def test_aliases_and_near_misses_do_not_overlap() -> None:
    """A name in both would resolve, so its NEAR_MISSES text would be dead
    prose that nobody ever sees."""
    assert set(ALIASES) & set(NEAR_MISSES) == set()


def test_every_near_miss_names_a_guide_that_exists() -> None:
    """The pointer is the whole value of a near miss. If it names a guide that
    was renamed, the message sends the user somewhere that is not there."""
    names = set(topic_names())
    for key, message in NEAR_MISSES.items():
        assert any(f"`{n}`" in message for n in names), (
            f"NEAR_MISSES[{key!r}] points at no existing guide: {message!r}"
        )


def test_resolve_returns_none_for_junk() -> None:
    for junk in ("", "   ", "nonesuch", "../../etc/passwd", "extension/../README"):
        assert resolve_topic(junk) is None, junk


def test_search_is_substring_not_regex() -> None:
    """A user searching for `models.json` or `[capabilities]` should not have to
    escape anything, and a bad pattern must not raise."""
    assert search_topics("models.json"), "literal dot should match literally"
    assert search_topics("[capabilities]"), "brackets are literal, not a class"
    # `.` as a regex would match every non-empty line in every guide; as a
    # literal it matches only lines containing a full stop. The distinction is
    # visible in the count.
    assert len(search_topics("zzzz.zzzz")) == 0


def test_search_caps_hits_per_topic() -> None:
    """One guide that mentions a common word on every line must not bury the
    others."""
    hits = search_topics("e", limit_per_topic=2)
    per_topic: dict[str, int] = {}
    for hit in hits:
        per_topic[hit.topic.name] = per_topic.get(hit.topic.name, 0) + 1
    assert per_topic, "expected hits for a very common letter"
    assert max(per_topic.values()) <= 2


def test_search_reports_one_indexed_line_numbers() -> None:
    """`topic:line` has to agree with what an editor shows."""
    hits = search_topics("Status: Accepted", limit_per_topic=1)
    assert hits
    for hit in hits:
        lines = read_topic(hit.topic).splitlines()
        assert hit.text in lines[hit.line - 1]


def test_importing_help_does_not_drag_in_the_tui_extra() -> None:
    """Stdlib only, measured in a clean interpreter.

    ``aelix_coding_agent.help`` is meant to be importable from a tool running
    inside a turn and from an install with no ``[tui]`` extra. Asserting on
    ``sys.modules`` in THIS process would prove nothing — pytest has already
    imported half the repo. So the check runs in a child.
    """
    code = (
        "import sys;"
        "import aelix_coding_agent.help as h;"
        "assert h.bundled_docs_dir().is_dir();"
        "bad=[m for m in ('rich','prompt_toolkit') if m in sys.modules];"
        "print('LEAKED' if bad else 'CLEAN', bad)"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
        env=_pythonpath_env(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("CLEAN"), result.stdout


def _pythonpath_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    src = REPO_ROOT / "packages"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(src / "aelix-agent-core" / "src"),
            str(src / "aelix-ai" / "src"),
            str(src / "aelix-coding-agent" / "src"),
            str(src / "aelix-server" / "src"),
        ]
    )
    return env


def test_the_leak_check_can_actually_see_a_leak() -> None:
    """Guards the guard above: if `rich` is not installed at all in this
    environment, `CLEAN` is unearned and the assertion is vacuous."""
    assert shutil.which(sys.executable)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import rich, prompt_toolkit; print('PRESENT')"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
        env=_pythonpath_env(),
    )
    assert result.stdout.startswith("PRESENT"), (
        "rich/prompt_toolkit are not importable here, so the no-TUI assertion "
        "above passes for the wrong reason. Install the [tui] extra to make it "
        f"meaningful.\n{result.stderr}"
    )


def test_titles_come_from_the_document_not_the_filename() -> None:
    """The listing has to agree with what `aelix docs <topic>` prints."""
    for topic in topics():
        first_heading = next(
            (
                line[2:].strip()
                for line in read_topic(topic).splitlines()
                if line.startswith("# ")
            ),
            None,
        )
        assert topic.title == (first_heading or topic.name), topic.name
