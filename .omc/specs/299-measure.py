#!/usr/bin/env python
"""#299 — does ``!cmd`` output reach the model? The same file runs on `main`
and on the branch.

Three tiers have to agree, and on `main` none of them carries the output:

* **live**   — ``harness.messages`` (``_state.messages``), what the NEXT
  ``prompt()`` sends to the provider in this process;
* **replay** — ``build_session_context(branch).messages``, what a
  ``--continue`` of this session sends;
* **display** — ``build_display_messages(branch)``, what the TUI redraws.

``!cmd`` (include) must appear in all three. ``!!cmd`` (exclude) must appear
in none of them — a fix that includes both has broken the feature it fixes.

§2 measures the two things the FIRST cut of the fix got wrong. Reaching the
model is not enough on its own: the record is re-sent on every later turn and
every resume, so an uncapped one is a bill the user never agreed to
(``[size]``), and one carrying no exit code cannot tell a failed command from
a silent one (``[exit]``). Both sections run on `main` too — there the record
reaches no tier at all, so ``[size]``'s model-facing copy reads 0.

Run:  uv run --no-sync python .omc/specs/299-measure.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    Session,
)
from aelix_agent_core.session.context import (
    build_display_messages,
    build_session_context,
)
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.repl import handle_user_bash

INCLUDE = "MARKER-299-INCLUDE"
EXCLUDE = "MARKER-299-EXCLUDE"


def _text(message: object) -> str:
    """Flatten any tier's message into searchable text."""

    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(getattr(block, "text", "") or "" for block in content)
    return str(content)


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        cwd = str(Path(tmp))
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), str(Path(tmp) / "s.jsonl"), cwd=cwd, session_id="probe"
        )
        session = Session(storage)
        harness = AgentHarness(
            AgentHarnessOptions(model=Model(id="m", api="anthropic"), session=session)
        )

        await handle_user_bash(
            harness, f"echo {INCLUDE}", exclude_from_context=False, cwd=cwd
        )
        await handle_user_bash(
            harness, f"echo {EXCLUDE}", exclude_from_context=True, cwd=cwd
        )

        branch = await session.get_branch()
        path = (await storage.get_metadata()).path
        lines = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        entries = [
            (rec.get("type"), rec.get("customType"))
            for rec in lines
            if rec.get("type") != "session"
        ]

        tiers = {
            "live   (harness.messages)": [_text(m) for m in harness.messages],
            "replay (build_session_context)": [
                _text(m) for m in build_session_context(branch).messages
            ],
            "display(build_display_messages)": [
                _text(m) for m in build_display_messages(branch)
            ],
        }

        print(f"[entries on disk] {entries}")
        failures = 0
        for name, texts in tiers.items():
            joined = "\n".join(texts)
            has_include = INCLUDE in joined
            has_exclude = EXCLUDE in joined
            ok = has_include and not has_exclude
            failures += 0 if ok else 1
            print(
                f"[{name}] messages={len(texts)} "
                f"{INCLUDE}={has_include} {EXCLUDE}={has_exclude} "
                f"-> {'OK' if ok else 'BROKEN'}"
            )
        failures += await _size_and_exit(cwd)
        print(f"[verdict] {'PASS' if failures == 0 else f'FAIL ({failures} checks)'}")
        return 1 if failures else 0


async def _fresh(tmp: str, name: str) -> tuple[AgentHarness, Path]:
    """A harness on its own on-disk session, so the file size is one command's."""

    path = Path(tmp) / f"{name}.jsonl"
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(path), cwd=tmp, session_id=name
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"), session=Session(storage)
        )
    )
    return harness, Path((await storage.get_metadata()).path)


async def _size_and_exit(cwd: str) -> int:
    """§2 — is the record BOUNDED, and can it tell failure from success?"""

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        big = "python3 -c \"print('x'*1000000)\""
        harness, path = await _fresh(tmp, "size")
        on_screen = await handle_user_bash(
            harness, big, exclude_from_context=False, cwd=cwd
        )
        recorded = max((len(_text(m)) for m in harness.messages), default=0)
        on_disk = path.stat().st_size
        # 1MB on screen is what the user asked for; 1MB in the record is what
        # every later request pays for. The cap is 50KB (+ the notice).
        ok = 0 < recorded <= 64 * 1024 and on_disk <= 64 * 1024
        failures += 0 if ok else 1
        print(
            f"[size] on screen={len(on_screen)} chars | "
            f"model-facing record={recorded} chars | session file={on_disk} bytes "
            f"-> {'OK' if ok else 'UNBOUNDED'}"
        )

        seen: list[str] = []
        for command in ("exit 0", "exit 3"):
            harness, _ = await _fresh(tmp, "exit-" + command.replace(" ", "-"))
            await handle_user_bash(
                harness, command, exclude_from_context=False, cwd=cwd
            )
            seen.append("\n".join(_text(m) for m in harness.messages))
        distinguishable = seen[0] != seen[1]
        failures += 0 if distinguishable else 1
        print(
            f"[exit] `exit 0` -> {seen[0]!r} | `exit 3` -> {seen[1]!r} "
            f"-> {'OK' if distinguishable else 'IDENTICAL'}"
        )
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
