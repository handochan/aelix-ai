"""``aelix docs`` — routing, exit codes, streams, and the pipe case (#101).

THE EXIT CODES ARE A CONTRACT, not an implementation detail: ``docs`` is the
product's second subcommand, and a script that wraps both has to be able to
branch on the same numbers. So the assertions below pin ``docs``'s codes AND
assert they agree with ``extension``'s, rather than restating a constant this
module also defines (a test that reads ``_EXIT_USAGE`` and asserts it equals
``_EXIT_USAGE`` proves nothing).

STREAMS ARE ALSO A CONTRACT. ``aelix docs extension | head`` has to put markdown
into the pipe and nothing else. Every diagnostic below is asserted to be on
stderr specifically, not merely "somewhere in the output" — capturing both
together is how a stdout-polluting error message tests green.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from aelix_coding_agent.cli.docs import _EXIT_USAGE, run_docs_command
from aelix_coding_agent.help import bundled_docs_dir, topic_names

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_docs_exit_code_agrees_with_the_other_subcommand() -> None:
    """The two subcommands must not disagree about "you got it wrong"."""
    from aelix_coding_agent.cli.extension_install import _EXIT_DIDNT_RUN

    assert _EXIT_USAGE == _EXIT_DIDNT_RUN == 2


def test_bare_docs_lists_topics_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_docs_command([]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    for name in topic_names():
        assert name in out.out
    assert "extension-authoring" in out.out
    assert "project-trust" in out.out


def test_a_topic_prints_the_guide_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_docs_command(["project-trust"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.startswith("# Project Trust")
    # The whole document, not a summary: the last section has to be there too.
    assert "## Related" in out.out


def test_an_alias_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    """The five names #101 asks for, minus the one that has no document."""
    for alias, expected_heading in [
        ("extension", "# Writing an Extension"),
        ("extension-api", "# Writing an Extension"),
        ("tools", "# Writing an Extension"),
        ("security", "# Project Trust"),
        ("project-trust", "# Project Trust"),
    ]:
        assert run_docs_command([alias]) == 0, alias
        assert capsys.readouterr().out.startswith(expected_heading), alias


def test_topic_lookup_is_case_and_extension_insensitive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A user who just read the listing types what they saw, including `.md`."""
    for spelling in ("Project-Trust", "project-trust.md", "  project-trust  "):
        assert run_docs_command([spelling]) == 0, spelling
        assert capsys.readouterr().out.startswith("# Project Trust"), spelling


def test_an_unknown_topic_exits_2_and_lists_topics_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_docs_command(["nonesuch"]) == _EXIT_USAGE
    out = capsys.readouterr()
    assert out.out == "", "an error must not pollute the pipe"
    assert "unknown topic" in out.err
    assert "extension-authoring" in out.err, "the correction must name what exists"


def test_a_known_gap_says_what_is_missing_rather_than_printing_the_wrong_guide(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`skills` has no guide. The failure mode this pins is the OTHER one:
    aliasing it to `agent-profiles` would exit 0 and hand over a document that
    does not answer the question."""
    assert run_docs_command(["skills"]) == _EXIT_USAGE
    out = capsys.readouterr()
    assert out.out == ""
    assert "no guide covers writing a skill" in out.err
    assert "agent-profiles" in out.err


def test_search_hits_go_to_stdout_as_topic_line_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_docs_command(["--search", "AELIX_DOTENV_ALLOW"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert "project-trust:" in out.out
    assert "AELIX_DOTENV_ALLOW" in out.out
    # `topic:line: text` — the form the rest of this repo cites, so an editor
    # and a shell `cut -d:` both work on it.
    first = out.out.splitlines()[0]
    topic, line, _ = first.split(":", 2)
    assert topic in topic_names()
    assert line.isdigit()


def test_search_with_no_hits_is_exit_0_with_the_notice_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty result answers a well-formed question — not a usage error."""
    assert run_docs_command(["-s", "zzzznotinanyguide"]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert "No guide mentions" in out.err


def test_search_without_a_term_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_docs_command(["--search"]) == _EXIT_USAGE
    out = capsys.readouterr()
    assert out.out == ""
    assert "usage: aelix docs" in out.err


def test_an_unknown_option_is_a_usage_error_not_a_topic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--foo` must not be looked up as a topic name and reported as one."""
    assert run_docs_command(["--foo"]) == _EXIT_USAGE
    out = capsys.readouterr()
    assert "unknown option" in out.err
    assert "unknown topic" not in out.err


def test_help_goes_to_stdout_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_docs_command(["--help"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.startswith("usage: aelix docs")


def test_the_bundled_docs_dir_is_inside_the_package() -> None:
    """Guards the guard: every test above is vacuous if the accessor points at
    the repo's own ``docs/guides/`` instead of the packaged copy."""
    docs = bundled_docs_dir()
    assert docs.is_dir()
    assert docs.parent.name == "aelix_coding_agent"
    assert list(docs.glob("*.md"))


# --- subprocess-level: routing, and the pipe convention ---------------------


def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """The REAL CLI, as a child process.

    ``python -m aelix_coding_agent`` rather than an in-process call, because
    routing lives in ``entry._async_main`` and the SIGPIPE handling lives in
    ``entry.main_sync`` — neither is exercised by calling ``run_docs_command``.
    """
    import os

    env = dict(os.environ)
    src = REPO_ROOT / "packages"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(src / "aelix-agent-core" / "src"),
            str(src / "aelix-ai" / "src"),
            str(src / "aelix-coding-agent" / "src"),
            str(src / "aelix-server" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "aelix_coding_agent", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_cli_routes_docs_before_the_prompt_parser() -> None:
    """Without the branch in ``entry._async_main``, ``docs`` and the topic name
    are swallowed as chat-prompt positionals and the agent tries to run a turn."""
    result = _run(["docs"])
    assert result.returncode == 0, result.stderr
    assert "extension-authoring" in result.stdout


def test_the_cli_routes_a_topic() -> None:
    result = _run(["docs", "project-trust"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# Project Trust")


def test_the_cli_exits_2_on_an_unknown_topic() -> None:
    result = _run(["docs", "nonesuch"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert "unknown topic" in result.stderr


def test_docs_is_listed_in_the_help_text() -> None:
    """Nothing else catches a subcommand missing from ``--help``.

    Asserted against the real ``--help`` output rather than the source string,
    and inside the ``Subcommands:`` section specifically — the word "docs"
    appears elsewhere in the help, so a substring search over the whole text
    would pass with the entry deleted.
    """
    result = _run(["--help"])
    assert result.returncode == 0, result.stderr
    _, _, subcommands = result.stdout.partition("Subcommands:")
    assert subcommands, "the help text has no Subcommands: section"
    assert "docs [<topic>]" in subcommands


def test_piping_to_head_uses_the_repo_pipe_convention() -> None:
    """``aelix docs extension | head -1`` must not die noisily.

    The repo's convention is a quiet 141 (128+SIGPIPE) or a plain 0 when the
    output fit in the buffer — never 120, which is what an unguarded
    interpreter-shutdown flush of a dead pipe produces (issue #57).
    """
    import os

    env = dict(os.environ)
    src = REPO_ROOT / "packages"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(src / "aelix-agent-core" / "src"),
            str(src / "aelix-ai" / "src"),
            str(src / "aelix-coding-agent" / "src"),
            str(src / "aelix-server" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    proc = subprocess.run(  # noqa: S602
        f"{sys.executable} -m aelix_coding_agent docs extension | head -1",
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.stdout.strip() == "# Writing an Extension"
    assert "BrokenPipeError" not in proc.stderr
    # `head` is the last command in the pipeline, so the shell reports ITS code.
    # The aelix side is checked by the absence of the interpreter's noise above.
    assert proc.returncode == 0
