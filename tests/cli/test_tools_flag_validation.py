"""#155 — an unknown ``--tools`` name is a message, not a traceback.

The defect was NOT a missing message. ``AgentHarness.__init__`` raised a
perfectly good ``AgentHarnessError``; nothing caught it at the startup build,
so it printed as a traceback and exited 1.

The issue described this as "the CLI path" and noted that the TUI already
degrades correctly. That is inaccurate in a way worth pinning: the clean
message it refers to is the IN-SESSION ``/agents use`` handler, and the crash
is at the shared pre-mode-dispatch build — measured, ``--print``, ``--json``,
RPC and the TUI all tracebacked identically.

These run the REAL binary because the whole bug was "an exception escapes the
process", which no in-process call can observe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.tui.commands import _profile_tools_cell

_ECHO_EXTENSION = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "aelix-coding-agent"
    / "src"
    / "aelix_coding_agent"
    / "examples"
    / "echo"
    / "echo.py"
)


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The real CLI, hermetic and offline.

    A fake key so provider auth is never the thing that fails first, and an
    isolated agent dir so a developer's own settings cannot change the result.
    """

    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = "sk-fake-not-a-real-key"
    env["AELIX_CODING_AGENT_DIR"] = str(cwd / "agentdir")
    env["AELIX_SETTINGS_PATH"] = str(cwd / "agentdir" / "settings.json")
    return subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--print", *args, "hi"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        env=env,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "agentdir").mkdir()
    return tmp_path


def test_unknown_tool_name_is_a_message_not_a_traceback(workspace: Path) -> None:
    """THE #155 PIN. Red before the fix, with the traceback in stderr."""

    result = _run(
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "--tools", "Bash,Frobnicate",
        cwd=workspace,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Frobnicate" in result.stderr


def test_the_message_names_the_valid_tools(workspace: Path) -> None:
    """A refusal that does not say what WOULD work is barely better than the
    traceback. ``--help`` named no valid value either (that is fixed too), so
    this message was the only place a user could learn them."""

    result = _run(
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "--tools", "Bash",
        cwd=workspace,
    )

    stderr = result.stderr
    for builtin in ("bash", "edit", "find", "grep", "ls", "read", "write"):
        assert builtin in stderr, f"{builtin} missing from {stderr!r}"
    # "Bash" is itself invalid — the single most surprising fact in the issue —
    # so the message has to say that matching is case-sensitive.
    assert "case-sensitive" in stderr


def test_an_extension_tool_is_a_valid_tools_value(workspace: Path) -> None:
    """THE ANTI-REGRESSION CONTROL, and the reason the fix is shaped as it is.

    ``--tools`` is validated against the LIVE registry, which includes
    extension and MCP tools — ``AgentHarness.__init__`` merges them before
    checking. A "simpler" fix that pre-validates against ``ALL_TOOL_NAMES``
    looks correct, passes a unit test over that frozenset, and rejects every
    extension and MCP tool in the real binary. This is what catches it.

    Asserted on the ABSENCE of the tool error rather than a clean exit,
    because the run still has no usable model and is expected to fail later —
    what matters is that it got past tool validation.
    """

    result = _run(
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "-e", str(_ECHO_EXTENSION), "--tools", "echo,read",
        cwd=workspace,
    )

    assert "unknown tool name" not in result.stderr


def test_the_same_name_is_rejected_without_the_extension(workspace: Path) -> None:
    """The other half of the control: ``echo`` is not universally accepted, it
    is accepted BECAUSE the extension registered it. Without this, the test
    above would also pass against a build that validates nothing at all."""

    result = _run(
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "--tools", "echo",
        cwd=workspace,
    )

    assert result.returncode == 1
    assert "unknown tool name" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_profile_with_a_bad_tool_fails_cleanly(workspace: Path) -> None:
    """The persistent form: a profile's ``tools:`` reaches the same
    ``parsed.tools``, so EVERY launch of that profile failed until the file was
    edited. One catch covers both, which is why the fix is at the shared build
    rather than in the ``--tools`` parse arm."""

    agents = workspace / ".aelix" / "agents"
    agents.mkdir(parents=True)
    (agents / "badtools.md").write_text(
        "---\nname: badtools\ndescription: probe\ntools: [Bash, read]\n---\nprobe\n",
        encoding="utf-8",
    )

    result = _run(
        "--approve",
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "--agent", "badtools",
        cwd=workspace,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Bash" in result.stderr


def test_a_valid_tools_list_still_starts(workspace: Path) -> None:
    """Deferring the allowlist past construction must not break the happy path.

    Without this, a fix that simply dropped ``--tools`` on the floor would pass
    every assertion above.
    """

    result = _run(
        "--model", "claude-haiku-4-5", "--provider", "anthropic",
        "--tools", "read,bash",
        cwd=workspace,
    )

    assert "unknown tool name" not in result.stderr


# === /agents list annotation ===============================================


def _profile(tools: tuple[str, ...] | None) -> AgentProfile:
    return AgentProfile(
        name="p",
        description="d",
        body="b",
        file_path="/tmp/p.md",
        scope="project",
        tools=tools,
    )


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_agents_list_flags_a_tool_this_build_lacks() -> None:
    """The row should say so before the user picks it — the same
    annotate-in-place shape #152 gave the MODEL column."""

    cell = _profile_tools_cell(_profile(("Bash", "read")), [_Tool("bash"), _Tool("read")])
    assert "unknown here: Bash" in cell.plain


def test_agents_list_does_not_flag_an_extension_tool() -> None:
    """The annotation reads the LIVE registry, so a profile naming a tool an
    extension registered is correct, not broken."""

    cell = _profile_tools_cell(_profile(("echo",)), [_Tool("echo"), _Tool("read")])
    assert "unknown here" not in cell.plain


def test_agents_list_annotates_nothing_without_a_registry() -> None:
    """``None`` = no registry available. An unannotated row beats a ``/agents
    list`` that raises, and beats one that flags every tool as unknown."""

    cell = _profile_tools_cell(_profile(("Bash",)), None)
    assert "unknown here" not in cell.plain


def test_the_three_valued_tools_cell_is_unchanged() -> None:
    """``None`` (inherit) and ``()`` (no tools) mean opposite things and must
    not collapse — the pre-existing contract this change had to preserve."""

    assert _profile_tools_cell(_profile(None)).plain == "—"
    assert _profile_tools_cell(_profile(())).plain == "(none)"
