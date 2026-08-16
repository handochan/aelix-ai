"""Issue #121 — the TUI's two project-context REPORTS must reflect the prompt.

``/context``'s estimated composition and the startup banner's ``[Context]`` row
both used to re-run ``discover_context_files`` against the cwd, which answers
from the FILESYSTEM a question only the assembled PROMPT can answer. Measured on
the pre-change build with one 7175-char ``AGENTS.md`` (1794 estimated tokens):

* without ``-nc`` the chunk was counted TWICE — once inside ``System prompt``
  (which already contains it) and again as ``Memory files``;
* with ``-nc`` a ``Memory files`` row appeared for text that was never injected;
* the banner printed ``[Context] AGENTS.md`` under ``-nc``, and the second
  discovery call re-emitted 115 bytes of stderr warnings carrying the absolute
  path RAW — ESC and BEL from a hostile directory name reached the stream.

The assembly these tests mirror is ``cli/entry.py:1218-1223`` (the chunk is
appended VERBATIM, gated on ``not parsed.no_context_files``) joined by
``harness/core.py:596-602`` with ``"\\n\\n"``. Nothing here asserts the chunk's
INTERNAL shape: that belongs to ``cli/agent_context.py`` and changed inside this
same issue (markdown header → pi's ``<project_context>`` fence), so every
expectation below is derived by calling ``discover_context_files`` rather than
by spelling a marker out.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli.agent_context import discover_context_files
from aelix_coding_agent.cli.list_models import format_token_count
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    match_command,
)
from aelix_coding_agent.tui.context_usage import estimate_tokens
from rich.console import Console

# A directory component carrying a live OSC title-set sequence (ESC ] 0 ; … BEL)
# and an SGR colour change. POSIX permits every byte but ``/`` and NUL in a path
# component, so this is a directory an attacker can really ship in a tarball or a
# repo. Rich was measured to strip the BEL but NOT the ESC, so ``Text`` is not a
# defence here and the bytes have to be removed before they are rendered.
HOSTILE_DIR = "proj\x1b]0;pwned\x07\x1b[31mZ"

# Every code point that can steer a terminal on its own: C0, DEL and C1 (``\x9b``
# IS a one-byte CSI). Same set as ``commands._CONTROL_KILL``, restated here so
# the test fails if that table is narrowed.
CONTROL_CHARS = frozenset(chr(c) for c in [*range(0x20), 0x7F, *range(0x80, 0xA0)])

BASE_PROMPT = "You are Aelix, an interactive CLI coding agent."


def _render(renderable: object) -> str:
    buffer = io.StringIO()
    # width=200 so a composition line never wraps mid-number, no_color so the
    # ONLY escape bytes that can appear are ones the code under test emitted.
    Console(file=buffer, width=200, no_color=True).print(renderable)
    return buffer.getvalue()


def _discover_quietly(cwd: str) -> str:
    """The chunk ``entry.py`` would inject, without the warnings on the test log."""

    with contextlib.redirect_stderr(io.StringIO()):
        return discover_context_files(cwd)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A cwd with an ``AGENTS.md`` big enough that its row is unmistakable."""

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "AGENTS.md").write_text("# House rules\n" + ("use tabs. " * 700), "utf-8")
    return root


class _ReportHarness:
    """Only the seams ``/context`` reads, with production's EXACT signatures.

    ``_action_get_system_prompt`` takes no arguments and returns ``str``
    (``harness/core.py:3757-3758``); ``messages`` is a plain list property. No
    ``_action_get_all_tools``, so the tools category is genuinely absent rather
    than faked — the rows under test are then the only two produced, and no
    ``**kwargs`` anywhere lets a call through that production would reject.
    """

    current_model = None

    def __init__(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt
        self.messages: list[Any] = []

    def _action_get_system_prompt(self) -> str:
        return self._system_prompt

    async def get_session_stats(self) -> Any:
        class _Usage:
            context_window = 200_000
            tokens = None
            percent = None

        class _Stats:
            context_usage = _Usage()

        return _Stats()


class _BannerHarness:
    """The banner's view of a harness: a model and a readable system prompt."""

    current_model = None

    def __init__(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    def _action_get_system_prompt(self) -> str:
        return self._system_prompt


async def _run_context(system_prompt: str, cwd: Path) -> str:
    committed: list[object] = []
    ctx = CommandContext(
        chrome=None,  # type: ignore[arg-type]
        harness=_ReportHarness(system_prompt),  # type: ignore[arg-type]
        commit=committed.append,
        cwd=str(cwd),
        commands=BUILTIN_COMMANDS,
    )
    cmd = match_command("/context", ctx.commands)
    assert cmd is not None and cmd.handler is not None
    await cmd.handler(ctx, "")
    return "\n".join(_render(c) for c in committed)


def _row(rendered: str, name: str) -> str:
    rows = [ln for ln in rendered.splitlines() if name in ln]
    assert len(rows) == 1, f"expected exactly one {name!r} row, got {rows}"
    return rows[0]


# === /context ===============================================================


async def test_context_does_not_count_the_project_context_twice(project: Path) -> None:
    """Without ``-nc`` the chunk is inside the system prompt already.

    So ``System prompt`` must be charged the prompt MINUS the chunk. Pre-change
    it was charged the whole prompt and ``Memory files`` charged the chunk again,
    overstating the composition by the chunk's full estimate.
    """

    chunk = _discover_quietly(project)
    assert chunk, "fixture must produce a context chunk"
    assembled = f"{BASE_PROMPT}\n\n{chunk}"  # harness/core.py:596-602

    rendered = await _run_context(assembled, project)

    remainder_tokens = estimate_tokens(assembled.replace(chunk, "", 1))
    assert f"{format_token_count(remainder_tokens)} tokens" in _row(
        rendered, "System prompt"
    )
    assert f"{format_token_count(estimate_tokens(chunk))} tokens" in _row(
        rendered, "Memory files"
    )
    # The defect stated as arithmetic: the two rows must not sum past the whole.
    assert remainder_tokens + estimate_tokens(chunk) <= estimate_tokens(assembled) + 1


async def test_context_omits_memory_row_under_no_context_files(project: Path) -> None:
    """``-nc`` means the chunk was never injected — no row may claim it was.

    The ``AGENTS.md`` is on disk and discoverable; the only thing that changed is
    that the assembled prompt does not contain it, which is exactly what
    ``entry.py:1220`` produces under ``--no-context-files``.
    """

    assert _discover_quietly(project), "the file must be discoverable, just not used"

    rendered = await _run_context(BASE_PROMPT, project)

    assert "Memory files" not in rendered
    assert f"{format_token_count(estimate_tokens(BASE_PROMPT))} tokens" in _row(
        rendered, "System prompt"
    )


async def test_context_reports_no_memory_when_harness_cannot_show_its_prompt(
    project: Path,
) -> None:
    """No readable prompt → no claim about it, rather than a filesystem guess."""

    class _Opaque:
        current_model = None
        messages: list[Any] = []

        async def get_session_stats(self) -> Any:
            class _Usage:
                context_window = 200_000
                tokens = None
                percent = None

            class _Stats:
                context_usage = _Usage()

            return _Stats()

    committed: list[object] = []
    ctx = CommandContext(
        chrome=None,  # type: ignore[arg-type]
        harness=_Opaque(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd=str(project),
        commands=BUILTIN_COMMANDS,
    )
    cmd = match_command("/context", ctx.commands)
    assert cmd is not None and cmd.handler is not None
    await cmd.handler(ctx, "")
    assert "Memory files" not in "\n".join(_render(c) for c in committed)


# === startup banner =========================================================


def test_banner_context_row_respects_no_context_files(project: Path) -> None:
    """The banner must not advertise context the prompt does not carry."""

    from aelix_coding_agent.tui.shell import _build_banner

    out = _render(_build_banner(_BannerHarness(BASE_PROMPT), str(project)))  # type: ignore[arg-type]
    row = _row(out, "[Context]")
    assert "AGENTS.md" not in row
    assert "none" in row


def test_banner_context_row_reports_agents_md_when_it_is_in_the_prompt(
    project: Path,
) -> None:
    """The positive arm, so the fix above cannot be "always say none"."""

    from aelix_coding_agent.tui.shell import _build_banner

    prompt = f"{BASE_PROMPT}\n\n{_discover_quietly(project)}"
    out = _render(_build_banner(_BannerHarness(prompt), str(project)))  # type: ignore[arg-type]
    assert "AGENTS.md" in _row(out, "[Context]")


def test_banner_does_not_re_emit_the_context_budget_warnings(tmp_path: Path) -> None:
    """Rendering a banner must not print discovery's warnings a second time.

    ``cli/entry.py:1221`` already called discovery once at startup and its
    ``Warning:`` lines have already been shown; the banner render is a REPORT and
    must be silent. An oversized ``AGENTS.md`` is what makes discovery warn
    (``agent_context.py:1250-1255``, the 32768-byte budget).
    """

    root = tmp_path / "big"
    root.mkdir()
    (root / "AGENTS.md").write_text("x" * 40_000, encoding="utf-8")
    assert _discover_quietly(root), "the oversized file must still be discovered"

    from aelix_coding_agent.tui.shell import _build_banner

    prompt = f"{BASE_PROMPT}\n\n{_discover_quietly(root)}"
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        _render(_build_banner(_BannerHarness(prompt), str(root)))  # type: ignore[arg-type]
    assert captured.getvalue() == ""


def test_banner_keeps_control_bytes_from_a_hostile_cwd_off_the_terminal(
    tmp_path: Path,
) -> None:
    """Neither the rendered banner nor its stderr may carry raw control bytes.

    Two independent leaks were measured on the pre-change build over a directory
    named ``proj\\x1b]0;pwned\\x07\\x1b[31mZ``: the re-emitted budget warning
    interpolated the path raw into stderr, and the panel's ``cwd:`` row rendered
    the path raw into the banner itself (Rich strips BEL but passes ESC through).
    """

    root = tmp_path / HOSTILE_DIR
    root.mkdir()
    (root / "AGENTS.md").write_text("x" * 40_000, encoding="utf-8")

    from aelix_coding_agent.tui.shell import _build_banner

    prompt = f"{BASE_PROMPT}\n\n{_discover_quietly(root)}"
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        out = _render(_build_banner(_BannerHarness(prompt), str(root)))  # type: ignore[arg-type]

    leaked = sorted({c for c in out if c in CONTROL_CHARS} - {"\n"})
    assert leaked == [], f"banner put control bytes on the terminal: {leaked!r}"
    assert captured.getvalue() == ""
    # The path is still IDENTIFIABLE — de-fanging must not blank the row out.
    assert "pwned" in _row(out, "cwd:")


# === the split itself =======================================================


def test_split_is_exact_and_disjoint(project: Path) -> None:
    """The two halves never overlap, so a caller can charge each once."""

    from aelix_coding_agent.tui.project_context import split_project_context

    chunk = _discover_quietly(project)
    assembled = f"{BASE_PROMPT}\n\n{chunk}"
    rest, found = split_project_context(assembled, str(project))
    assert found == chunk
    assert chunk not in rest
    assert len(rest) + len(found) == len(assembled)


def test_split_returns_the_prompt_whole_when_context_is_absent(project: Path) -> None:
    """The ``-nc`` shape: discoverable on disk, absent from the prompt."""

    from aelix_coding_agent.tui.project_context import split_project_context

    assert split_project_context(BASE_PROMPT, str(project)) == (BASE_PROMPT, "")
    assert split_project_context(None, str(project)) == ("", "")


def test_split_finds_the_chunk_when_it_is_not_last(project: Path) -> None:
    """The production shape whenever skills are loaded.

    ``cli/entry.py:1218-1240`` orders the appended sections base → user append →
    project context → skills catalog, so the chunk is only the tail of the prompt
    in the no-skills case. A split that quietly assumed "at the end" would drop
    the row for every session that has a skill.
    """

    from aelix_coding_agent.tui.project_context import split_project_context

    chunk = _discover_quietly(project)
    trailer = "\n\n<available_skills>cat</available_skills>"
    assembled = f"{BASE_PROMPT}\n\n{chunk}{trailer}"
    rest, found = split_project_context(assembled, str(project))
    assert found == chunk
    assert rest.endswith(trailer)
    assert len(rest) + len(found) == len(assembled)


def test_split_degrades_to_no_row_when_the_file_changed_mid_session(
    project: Path,
) -> None:
    """A stale read must understate, never invent.

    Editing ``AGENTS.md`` after the prompt was assembled makes the re-read stop
    matching what was injected. The documented degrade is that the row vanishes
    and its tokens stay charged to ``System prompt`` — one row understated, which
    is neither the phantom nor the double count this module exists to remove.
    """

    from aelix_coding_agent.tui.project_context import split_project_context

    assembled = f"{BASE_PROMPT}\n\n{_discover_quietly(project)}"
    (project / "AGENTS.md").write_text("# House rules\nrewritten", encoding="utf-8")
    assert split_project_context(assembled, str(project)) == (assembled, "")
