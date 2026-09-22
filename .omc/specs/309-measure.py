#!/usr/bin/env python
"""#309 — what a trailing newline costs at each of ``_truncate``'s callers.

The same file runs on `main` and on the branch. ``truncate_tail`` splits on
``"\\n"`` raw, so output that ENDS in a newline leaves an empty final element
that is counted as a line. It costs 0 bytes, so it is the one line that fits
the byte budget, and the real line above it is dropped whole: body 0 bytes,
notice only. Every ``print()``, every ``echo``, ends in a newline.

Five sections, in the order the defect has to be believed in:

* **§1 helper** — the shapes, head and tail, with the counts the helper
  reports. The row named in the issue is ``huge + LF``.
* **§2 bash** — the model-facing tool, a real child process, the issue's own
  repro (``print('x'*1000000)``). This is the number in the issue body.
* **§3 the other callers** — read/grep/find/ls, each run for real. Only the
  ones whose input can END in a newline can be hurt; the section measures
  which those are rather than asserting it.
* **§4 rpc** — ``rpc_mode._handle_bash`` caps an ad-hoc RPC ``bash`` at 256
  lines / 32KB. The helper has SEVEN callers, not the five the issue lists,
  and this is one of the two missing from it; the other is ``cli/repl.py``
  ``_cap_for_the_record``, whose workaround WRAPS ``truncate_tail`` rather
  than replacing it, so it was a caller on both trees. Counted with
  ``grep -rn "_truncate import\\|truncate_tail(\\|truncate_head(" packages/``:
  bash, grep, find, ls, read, repl, rpc. The ``cli/agent_context.py`` hit is
  a docstring, not a call.
* **§5 the `!cmd` record** — #299's ``_cap_for_the_record`` works around the
  same helper bug locally. Its nine pinned rows are re-measured here so the
  workaround's removal can be checked against them row by row. Run on both
  trees they come out byte-identical: this surface shares the cap but never
  showed the defect to a user, which is why the CHANGELOG no longer says it
  did.

Run:  uv run --no-sync python .omc/specs/309-measure.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.cli.repl import _cap_for_the_record
from aelix_coding_agent.tools import (
    create_bash_tool,
    create_find_tool,
    create_grep_tool,
    create_ls_tool,
    create_read_tool,
)
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationInfo,
    truncate_head,
    truncate_tail,
)

KB = 1024


def digest(text: str) -> str:
    """A body short enough to print and specific enough to compare."""

    raw = text.encode()
    if not raw:
        return "b'' (0 bytes) <<< EMPTY"
    head = text[:14].encode("unicode_escape").decode()
    tail = text[-14:].encode("unicode_escape").decode()
    return f"{len(raw):>7} bytes  {head!r}…{tail!r}"


def row(label: str, body: str, info: TruncationInfo) -> None:
    print(f"  {label:<26} {digest(body)}")
    print(
        f"  {'':<26} truncated={info.truncated!s:<5} by={info.truncated_by!s:<5} "
        f"partial={info.last_line_partial!s:<5} "
        f"lines={info.original_lines}->{info.kept_lines} "
        f"bytes={info.original_bytes}->{info.kept_bytes}"
    )


def section_1_helper() -> None:
    print("\n§1 the helper itself (2000 lines / 50KB unless stated)\n")
    huge = "x" * 60000
    print(" truncate_tail:")
    for label, text, lines, byts in [
        ("huge, no newline", huge, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("huge + LF   <-- #309", huge + "\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("huge + LF LF", huge + "\n\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("huge + CRLF", huge + "\r\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("huge + CRLF CRLF", huge + "\r\n\r\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("50KB exactly", "x" * (50 * KB), DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("50KB + LF", "x" * (50 * KB) + "\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("100 lines + LF, cap 5", "".join(f"{n}\n" for n in range(1, 101)), 5, 50 * KB),
        ("100 lines, no LF, cap 5", "\n".join(str(n) for n in range(1, 101)), 5, 50 * KB),
        ("empty", "", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("one newline", "\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("three newlines", "\n\n\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
    ]:
        body, info = truncate_tail(text, max_lines=lines, max_bytes=byts)
        row(label, body, info)
    print("\n truncate_head:")
    for label, text, lines, byts in [
        ("blank line + huge", "\n" + huge, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("huge + LF", huge + "\n", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("2000 lines + LF", "a\n" * 2000, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("2001 lines + LF", "a\n" * 2001, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
        ("empty", "", DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES),
    ]:
        body, info = truncate_head(text, max_lines=lines, max_bytes=byts)
        row(label, body, info)


async def section_2_bash(tmp: Path) -> None:
    print("\n§2 the bash tool — a real child, the issue's own repro\n")
    tool = create_bash_tool(str(tmp))
    ctx = ToolExecutionContext(tool_call_id="probe")
    for label, command in [
        ("print('x'*1000000)", f"{sys.executable} -c \"print('x'*1000000)\""),
        (
            "print(big); print()",
            f"{sys.executable} -c \"print('x'*1000000); print()\"",
        ),
        ("seq 1 3000", f"{sys.executable} -c \"[print(n) for n in range(1,3001)]\""),
    ]:
        result = await tool.execute({"command": command}, ctx)
        info = result.details.truncation
        text = result.content[0].text
        row(label, text, info)
        first = text.split("\n\n[", 1)[0]
        notice = text[len(first) :].strip()
        print(f"  {'':<26} body={len(first.encode())} bytes  notice={notice[:110]!r}")


async def section_3_other_callers(tmp: Path) -> None:
    print("\n§3 read / grep / find / ls — run for real\n")
    ctx = ToolExecutionContext(tool_call_id="probe")

    # read: a file of exactly DEFAULT_MAX_LINES newline-terminated lines. The
    # trailing newline makes read's own split report one line more than the
    # file has, and hands truncate_head a text that ends in "\n".
    exact = tmp / "exactly-2000-lines.txt"
    exact.write_text("".join(f"line {n}\n" for n in range(1, DEFAULT_MAX_LINES + 1)))
    read_tool = create_read_tool(str(tmp))
    result = await read_tool.execute({"path": str(exact)}, ctx)
    text = result.content[0].text
    # ``details`` is None once read stops calling this file truncated.
    info = getattr(result.details, "truncation", None) or TruncationInfo()
    row("read 2000-line file", text, info)
    print(f"  {'':<26} tail={text[-80:]!r}")

    # read's notice counts with read's OWN total_lines (read.py:194-195, a raw
    # split), which this fix does not touch. For a file that still truncates,
    # that number and the ``original_lines`` in the SAME ToolResult's details
    # disagree by one on the branch, and agreed (both one too many) on base.
    # The CHANGELOG said "notices everywhere count the lines the file actually
    # has"; these two rows are what falsified it.
    for label, content in [
        ("read 2000 lines >50KB", "".join("x" * 44 + f"{n:05d}\n" for n in range(2000))),
        ("read 2001 lines <50KB", "".join(f"line {n}\n" for n in range(2001))),
    ]:
        path = tmp / (label.replace(" ", "-") + ".txt")
        path.write_text(content)
        result = await read_tool.execute({"path": str(path)}, ctx)
        text = result.content[0].text
        info = getattr(result.details, "truncation", None) or TruncationInfo()
        notice = "[" + text.rsplit("\n\n[", 1)[1] if "\n\n[" in text else ""
        print(f"  {label:<26} notice={notice.strip()!r}")
        print(f"  {'':<26} details.original_lines={info.original_lines}")

    # A file whose last line is blank, under a 60KB line: read's Branch A
    # (first line exceeds the cap) cannot fire, so truncate_head decides.
    blank_first = tmp / "blank-then-huge.txt"
    blank_first.write_text("\n" + "y" * 60000 + "\n")
    result = await read_tool.execute({"path": str(blank_first)}, ctx)
    text = result.content[0].text
    row(
        "read blank line + 60KB",
        text,
        getattr(result.details, "truncation", None) or TruncationInfo(),
    )

    # ls / find / grep: their input is "\n".join(...) — it cannot end in a
    # newline, so the phantom line cannot appear. Measured, not assumed:
    # 3000 files of ~24-byte names are well past the 50KB cap.
    big = tmp / "many"
    big.mkdir()
    for n in range(3000):
        (big / f"file-{n:05d}-padding.txt").write_text("needle\n")
    ls_tool = create_ls_tool(str(big))
    result = await ls_tool.execute({"path": str(big)}, ctx)
    text = result.content[0].text
    print(f"  {'ls 3000 entries':<26} {digest(text)}")
    print(f"  {'':<26} tail={text[-70:]!r}")
    find_tool = create_find_tool(str(big))
    result = await find_tool.execute({"pattern": "**/*.txt", "path": str(big)}, ctx)
    text = result.content[0].text
    print(f"  {'find 3000 files':<26} {digest(text)}")
    print(f"  {'':<26} tail={text[-70:]!r}")
    grep_tool = create_grep_tool(str(big))
    result = await grep_tool.execute(
        {"pattern": "needle", "path": str(big), "limit": 4000}, ctx
    )
    text = result.content[0].text
    print(f"  {'grep 3000 matches':<26} {digest(text)}")
    print(f"  {'':<26} tail={text[-70:]!r}")


def section_4_rpc() -> None:
    print("\n§4 rpc_mode._handle_bash — 1 of 7 callers, unlisted (256 lines / 32KB)\n")
    for label, text in [
        ("32KB+ one line + LF", "z" * 40000 + "\n"),
        ("300 lines + LF", "".join(f"{n}\n" for n in range(300))),
    ]:
        body, info = truncate_tail(text, max_lines=256, max_bytes=32 * KB)
        row(label, body, info)


def section_5_the_record() -> None:
    print("\n§5 the `!cmd` record (#299's nine pinned rows)\n")
    huge = "x" * 60000
    cap = DEFAULT_MAX_BYTES
    table = [
        ("empty", "", ""),
        ("at the line cap", "\n" * 2000, "\n" * 2000),
        ("over the line cap", "\n" * 2001, "\n" * 1999),
        ("huge + LF", huge + "\n", "x" * cap),
        ("huge + LF LF", huge + "\n\n", "x" * (cap - 1) + "\n"),
        ("huge + CRLF", huge + "\r\n", "x" * cap),
        ("huge + CRLF CRLF", huge + "\r\n\r\n", "x" * (cap - 1) + "\n"),
        ("exactly at the cap", "x" * cap, "x" * cap),
        ("at the cap + LF", "x" * cap + "\n", "x" * cap),
    ]
    for label, output, expected in table:
        record = _cap_for_the_record(output)
        verdict = "ok " if record.body == expected else "DIFF"
        print(f"  {verdict} {label:<22} {digest(record.body)}")
        if record.notice:
            print(f"  {'':<26} notice={record.notice.strip()[:104]!r}")


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        section_1_helper()
        await section_2_bash(root)
        await section_3_other_callers(root)
        section_4_rpc()
        section_5_the_record()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
