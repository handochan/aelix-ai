"""#596 (ADR-0287) — the TUI's ``!`` commands, from Enter to the transcript and the model.

A ``!`` / ``!!`` line runs as a *block*. An idle one runs in the input loop and
prints its output under the line it was typed on, as before #596. One Entered
while something holds the session — a turn, an extension's turn, a compaction, a
command the input loop is still running — runs at once, as pi's does, and its
block is drawn in the *pending area* under the stream (pi's
``pendingMessagesContainer``): its command, ``(running…)``, then the tail of its
output. Alt+Enter on a ``!`` line then queues a block that runs once the turn is
over. Nothing of a block is committed among the answer's lines: a committed
block split a streamed sentence and a tool call from its output (#596 verify V4).

When nothing holds the session any more, :meth:`ShellLane.settle` moves every
finished block into the transcript, oldest first, and writes what the model gets
of a ``!`` one (:func:`record_user_bash`) — pi's ``_pendingBashMessages``, held
"to avoid breaking tool_use/tool_result ordering". The model sees it on its next
turn; ``!!`` records nothing. Every record the TUI makes goes through here, the
idle one included (Codex 596-01), and is written under the harness's
:meth:`~aelix_agent_core.harness.core.AgentHarness.idle_write`, so a turn that
starts meanwhile waits for it instead of forking the session away from it
(Codex 596-02). A record for a session the user left (``/new``, ``/resume``,
``/fork``, ``/clone``, ``/import``) is dropped; one for the session a ``/reload``
kept goes through the rebuilt harness (Codex 596-03, verify V2).

One ``!`` command runs at a time, as in pi ("A bash command is already
running"): the chrome refuses Enter on another while one runs (verify V10), and
a queued one waits for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from rich.console import Console, Group
from rich.text import Text

from aelix_coding_agent.cli.repl import UserBashRun, record_user_bash, run_user_bash
from aelix_coding_agent.tui.input_marks import paint_shell
from aelix_coding_agent.tui.render import _safe_tool_output

_log = logging.getLogger(__name__)

#: Rows of a finished block's output the pending area shows — its last ones. All
#: of it reaches the transcript when the block is settled.
_PENDING_OUTPUT_ROWS = 3
#: The pending area never takes more rows than this; the newest rows are kept.
_PENDING_MAX_ROWS = 8

BlockState = Literal["queued", "running", "done"]


@dataclass(eq=False)
class ShellBlock:
    """One ``!`` / ``!!`` command."""

    sigil: str
    command: str
    #: The session it was Entered in (identity): its record goes there or nowhere.
    session: Any
    state: BlockState = "running"
    #: Drawn in the pending area, so committed under its command line later.
    pending: bool = False
    #: Alt+Enter during a turn: runs once the turn is over.
    after_turn: bool = False
    run: UserBashRun | None = None
    #: Shown in place of output: the command could not run.
    failure: str | None = None


class ShellLane:
    """The blocks, the pending area and the held records of one TUI session."""

    def __init__(
        self,
        *,
        busy: Callable[[], bool],
        current_harness: Callable[[], Any],
        commit: Callable[[object], None],
        show: Callable[[list[str]], None],
        theme: Callable[[], Any],
        width: Callable[[], int],
        output_text: Callable[[str], Text],
    ) -> None:
        self._busy_fn = busy
        self._current_harness = current_harness
        self._commit = commit
        self._show = show
        self._theme = theme
        self._width = width
        self._output_text = output_text
        self._blocks: list[ShellBlock] = []
        self._lock = asyncio.Lock()
        self._quiet = asyncio.Event()
        self._quiet.set()
        self._waiter: asyncio.Task[None] | None = None
        #: Set by the input loop while it handles a line other than a ``!`` one.
        self.command_running = False

    # === what the chrome asks ==============================================

    def busy(self) -> bool:
        """Something holds the session: a ``!`` line runs at once, and records wait."""

        return self.command_running or self._held_by_turn()

    def running(self) -> bool:
        """A ``!`` command is running (Enter on another is refused)."""

        return any(b.state == "running" for b in self._blocks)

    # === blocks =============================================================

    def begin(
        self,
        sigil: str,
        command: str,
        *,
        state: BlockState = "running",
        pending: bool = False,
        after_turn: bool = False,
    ) -> ShellBlock:
        block = ShellBlock(
            sigil,
            command,
            self._session_now(),
            state=state,
            pending=pending,
            after_turn=after_turn,
        )
        self._blocks.append(block)
        self._redraw()
        return block

    def refuse(self, block: ShellBlock, reason: str) -> None:
        """*block* will not run; *reason* is shown in its place."""

        block.failure = reason
        block.state = "done"
        self._redraw()

    def next_after_turn(self) -> ShellBlock | None:
        return next((b for b in self._blocks if b.after_turn and b.state == "queued"), None)

    async def wait_until_quiet(self) -> None:
        """Return once no ``!`` command is running."""

        while self.running():
            self._quiet.clear()
            await self._quiet.wait()

    async def run(self, block: ShellBlock, *, cwd: str) -> None:
        """Run *block*, then settle: at once when nothing holds the session."""

        # Its place among the blocks is when it ran, not when it was queued.
        self._blocks.remove(block)
        self._blocks.append(block)
        block.state = "running"
        self._redraw()
        try:
            block.run = await run_user_bash(
                self._current_harness(),
                block.command,
                exclude_from_context=block.sigil == "!!",
                cwd=cwd,
            )
        except asyncio.CancelledError:
            # Stopped with the TUI: nothing to show, nothing to record.
            self._blocks.remove(block)
            self._ran_out()
            raise
        except Exception as exc:  # noqa: BLE001 — shown in its block, never raised into the chrome
            block.failure = f"✖ {exc}"
        block.state = "done"
        self._ran_out()
        if not block.pending and self.busy():
            # Something started while it ran: it waits with the rest.
            block.pending = True
        self._redraw()
        await self.settle()

    def _ran_out(self) -> None:
        if not self.running():
            self._quiet.set()

    # === settling ===========================================================

    async def settle(self, *, from_loop: bool = False, final: bool = False) -> None:
        """Move every finished block into the transcript, oldest first, and write
        its record — unless something holds the session, then they wait.

        *from_loop*: the input loop calls this between lines, so the line it is
        handling does not hold them. *final*: the TUI is closing; only the
        harness can still hold them.
        """

        async with self._lock:
            while True:
                block = next((b for b in self._blocks if b.state != "queued"), None)
                if block is None or block.state != "done":
                    return
                if self._held(from_loop=from_loop, final=final):
                    self._watch()
                    return
                self._blocks.remove(block)
                harness = self._current_harness()
                left = getattr(harness, "session", None) is not block.session
                renderable = self._transcript(block, left=left)
                if renderable is not None:
                    self._commit(renderable)
                self._redraw()
                if left or block.run is None or block.run.exclude_from_context:
                    continue
                try:
                    async with _idle_write(harness):
                        await record_user_bash(harness, block.run)
                except Exception:  # noqa: BLE001 — never take the input loop down
                    _log.warning("could not record a ! command", exc_info=True)

    async def close(self) -> None:
        """The TUI is closing: record what finished, if the harness lets it."""

        waiter, self._waiter = self._waiter, None
        if waiter is not None:
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await waiter
        await self.settle(final=True)

    def _held(self, *, from_loop: bool, final: bool) -> bool:
        if final:
            return not _is_idle(self._current_harness())
        if self.command_running and not from_loop:
            return True
        return self._held_by_turn()

    def _held_by_turn(self) -> bool:
        try:
            if self._busy_fn():
                return True
        except Exception:  # noqa: BLE001 — a faulty provider is not a turn
            pass
        return not _is_idle(self._current_harness())

    def _watch(self) -> None:
        """Settle again once the harness goes idle, when nothing else will.

        The input loop settles after its own turns and commands; a turn an
        extension started (``send_message(..., trigger_turn=True)``) or a
        compaction it ran ends with no one there to do it (Codex 596-04).
        """

        if self._waiter is not None or self.command_running:
            return
        with contextlib.suppress(Exception):
            if self._busy_fn():
                return
        wait = cast(
            "Callable[[], Awaitable[None]] | None",
            getattr(self._current_harness(), "wait_for_idle", None),
        )
        if wait is None:
            return

        async def _then() -> None:
            try:
                await wait()
            finally:
                self._waiter = None
            await self.settle()

        self._waiter = asyncio.get_running_loop().create_task(_then())

    def _session_now(self) -> Any:
        return getattr(self._current_harness(), "session", None)

    # === drawing ============================================================

    def _redraw(self) -> None:
        self._show(self._rows())

    def _rows(self) -> list[str]:
        shown = [b for b in self._blocks if b.pending and b.state != "queued"]
        shown += [b for b in self._blocks if b.pending and b.state == "queued"]
        if not shown:
            return []
        theme = self._theme_now()
        rows: list[Text] = []
        for block in shown:
            rows.append(_command_line(block, _pending_note(block), theme, first_line_only=True))
            rows.extend(self._preview(block))
        if len(rows) > _PENDING_MAX_ROWS:
            over = len(rows) - (_PENDING_MAX_ROWS - 1)
            rows = [Text(f"… {over} rows above, in full once the turn is over", style="dim")] + rows[
                -(_PENDING_MAX_ROWS - 1) :
            ]
        width = self._width_now()
        return [_ansi_row(row, width) for row in rows]

    def _preview(self, block: ShellBlock) -> list[Text]:
        if block.failure is not None:
            return [Text(block.failure, style="bold red")]
        if block.run is None or not block.run.output.strip():
            return []
        lines = _safe_tool_output(block.run.output.rstrip("\n")).split("\n")
        if len(lines) <= _PENDING_OUTPUT_ROWS:
            return [Text(line) for line in lines]
        keep = _PENDING_OUTPUT_ROWS - 1
        more = Text(f"… {len(lines) - keep} more lines, in full once the turn is over", style="dim")
        return [more, *(Text(line) for line in lines[-keep:])]

    def _transcript(self, block: ShellBlock, *, left: bool) -> object | None:
        body: Text | None = None
        if block.failure is not None:
            body = Text(block.failure, style="bold red")
        elif block.run is not None and block.run.output.strip():
            body = self._output_text(block.run.output)
        if not block.pending and not left:
            # An idle ``!``: its output alone, under the line it was typed on.
            return body
        head = _command_line(block, _settled_note(block, left=left), self._theme_now())
        return head if body is None else Group(head, body)

    def _theme_now(self) -> Any:
        try:
            return self._theme()
        except Exception:  # noqa: BLE001 — a faulty theme must not break the chrome
            return None

    def _width_now(self) -> int:
        try:
            return max(1, int(self._width()))
        except Exception:  # noqa: BLE001
            return 80


def _is_idle(harness: Any) -> bool:
    return bool(getattr(harness, "is_idle", True))


def _idle_write(harness: Any) -> contextlib.AbstractAsyncContextManager[Any]:
    hold = cast(
        "Callable[[], contextlib.AbstractAsyncContextManager[Any]] | None",
        getattr(harness, "idle_write", None),
    )
    if hold is not None:
        return hold()
    return contextlib.nullcontext()


def _pending_note(block: ShellBlock) -> str:
    if block.state == "queued":
        return "runs after the turn"
    if block.state == "running":
        return "running…"
    return _settled_note(block, left=False)


def _settled_note(block: ShellBlock, *, left: bool) -> str:
    if block.failure is not None:
        return ""
    if block.sigil == "!!":
        return "the model won't see it"
    if left:
        return "not recorded: you left its session"
    return "the model sees the output next turn"


def _command_line(block: ShellBlock, note: str, theme: Any, *, first_line_only: bool = False) -> Text:
    """The command a block runs, headed by its sigil in the shell colour."""

    command = _safe_tool_output(block.command)
    if first_line_only and "\n" in command:
        command = command.split("\n", 1)[0] + " …"
    line = Text.from_ansi(paint_shell(theme, f"{block.sigil} "))
    line.stylize("bold")
    line.append(command)
    if note:
        line.append(f"  ({note})", style="dim")
    return line


def _ansi_row(row: Text, width: int) -> str:
    """*row* as one row of SGR text, cut to *width* cells."""

    row = row.copy()
    row.expand_tabs()
    row.no_wrap = True
    row.truncate(width, overflow="ellipsis")
    buf = io.StringIO()
    # ``legacy_windows=False`` for the reason ``stream.plain_lines`` gives: this
    # console writes into a buffer whose SGR the chrome parses back.
    Console(file=buf, force_terminal=True, width=width, legacy_windows=False).print(
        row, end="", no_wrap=True, overflow="ellipsis"
    )
    return buf.getvalue()


__all__ = ["ShellBlock", "ShellLane"]
