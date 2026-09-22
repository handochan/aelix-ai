"""#194 — seed real session files holding failed turns, for the live TUI check.

    uv run python .omc/specs/194-live.py     # writes the files, prints the commands
    uv run aelix --session /tmp/aelix-194/failed-turn.jsonl
    uv run aelix --session /tmp/aelix-194/failed-turn-no-message.jsonl

TWO files, because the fix has two cases and the second is the one review
caught.

Both turns are produced by a REAL ``AgentHarness``, so the messages on disk are
the ones ``harness/core.py`` synthesises — not hand-written imitations of them.
The files are written before the fix can touch them and are identical on both
trees; only the rendering differs.

**File 1 — ``failed-turn.jsonl``.** The provider stream ends with no terminal
event. WHAT TO LOOK FOR, once the TUI has painted the transcript above the
prompt:

    ✖ The model stream ended without a result: it stopped before sending a
      completion, an error, or an end event, so this turn produced nothing.

ONE line, bold red, starting with ``✖``. On the branch base the SAME sentence
appears TWICE — first as ordinary body text prefixed ``[error] `` (rendered
through Markdown, so any backticks in a provider's message come back as code
spans), then as the ``✖`` line.

**File 2 — ``failed-turn-no-message.jsonl``.** The provider raises a bare
``TimeoutError()``, so ``str(exc)`` is ``""`` and the harness writes
``error_message=""`` beside the body ``"[error] "``. WHAT TO LOOK FOR:

    ✖ request error

ONE line, bold red, and NOTHING above it. The first draft of this fix painted
TWO lines here and they did not even match — a bare ``[error]`` on one line and
``✖ request error`` on the next.

``--session <path>`` takes the startup-paint road into ``EventRenderer.replay``
(issue #165); ``/resume`` and picking this session takes the other road
(ADR-0122). Both end in the same method — checking either is enough, and
checking both costs one extra keystroke.

Re-run this script to get clean files back; it overwrites.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.streaming import (
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

OUT_DIR = Path("/tmp/aelix-194")
OUT = OUT_DIR / "failed-turn.jsonl"
OUT_NO_MESSAGE = OUT_DIR / "failed-turn-no-message.jsonl"


async def truncated_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    yield AssistantStartEvent()


async def bare_timeout_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """``str(TimeoutError())`` is ``""`` — the review case."""

    yield AssistantStartEvent()
    raise TimeoutError


async def seed(path: Path, session_id: str, stream_fn: object) -> None:
    if path.exists():
        path.unlink()
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(path), cwd=str(Path.cwd()), session_id=session_id
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=Session(storage),
            stream_fn=stream_fn,  # type: ignore[arg-type]
        )
    )
    try:
        await harness.prompt("what is the capital of France?")
    except Exception as exc:  # noqa: BLE001 — the loop is expected to raise
        print(f"seeded a turn that failed with: {str(exc)!r}")

    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"wrote {path} — 1 header + {len(lines) - 1} entries")


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await seed(OUT, "live194", truncated_stream)
    await seed(OUT_NO_MESSAGE, "live194b", bare_timeout_stream)
    print()
    print("now run, in a real terminal:")
    print(f"    uv run aelix --session {OUT}")
    print("      expect ONE bold-red line beginning with ✖ and NO '[error] ' above it.")
    print(f"    uv run aelix --session {OUT_NO_MESSAGE}")
    print("      expect ONE bold-red '✖ request error' and NO '[error]' above it.")


if __name__ == "__main__":
    asyncio.run(main())
