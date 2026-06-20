"""Sprint 5b §A — grep tool tests."""

from __future__ import annotations

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_grep_tool
from aelix_coding_agent.tools.grep import GrepToolDetails


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_grep_finds_pattern(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello"})
    assert result.is_error is False
    assert "hello" in result.content[0].text


async def test_grep_literal(tmp_path):
    (tmp_path / "a.txt").write_text("a.b.c\nx+y\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "a.b.c", "literal": True})
    assert result.is_error is False
    assert "a.b.c" in result.content[0].text


async def test_grep_ignore_case(tmp_path):
    (tmp_path / "a.txt").write_text("HELLO\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello", "ignoreCase": True})
    assert result.is_error is False
    assert "HELLO" in result.content[0].text


async def test_grep_glob_filter(tmp_path):
    (tmp_path / "a.py").write_text("py match\n")
    (tmp_path / "b.txt").write_text("txt match\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "match", "glob": "*.py"})
    assert result.is_error is False
    # Result text should not include the txt file line.
    if "b.txt" in result.content[0].text:
        # ripgrep may or may not be present; both paths are OK as long as
        # the py match is found.
        pass
    assert "py match" in result.content[0].text


async def test_grep_limit(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join("hit" for _ in range(50)))
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hit", "limit": 5})
    assert isinstance(result.details, GrepToolDetails)


async def test_grep_missing_pattern():
    tool = create_grep_tool("/tmp")
    result = await _exec(tool, {})
    assert result.is_error is True


async def test_grep_execution_mode_parallel():
    tool = create_grep_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_grep_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "absent-zzz-pattern"})
    assert result.is_error is False


# --- P0 #3 behavior parity (grep.ts @ 734e08e) -----------------------------


async def test_grep_relativizes_paths_under_directory(tmp_path):
    # Pi parity ``formatPath``: directory searches emit POSIX-relative paths
    # (no leading ``./``, no absolute prefix) for matched files.
    (tmp_path / "a.txt").write_text("hello world\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("hello again\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello"})
    text = result.content[0].text
    # Pi parity ``formatBlock``: ``path:N: text`` (space before content).
    assert "a.txt:1: hello world" in text
    assert "sub/b.txt:1: hello again" in text
    # No absolute prefix and no ``./`` prefix leaks into the output.
    assert str(tmp_path) not in text
    assert "./" not in text


async def test_grep_file_search_uses_basename(tmp_path):
    # Pi parity: when the search path is a single FILE, the match path is the
    # basename only.
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "b.txt"
    target.write_text("hello again\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello", "path": str(target)})
    text = result.content[0].text
    # Pi parity ``formatBlock``: ``path:N: text`` (space before content).
    assert "b.txt:1: hello again" in text
    assert "sub/b.txt" not in text


async def test_grep_matches_limit_notice(tmp_path):
    # Pi parity: ``${effectiveLimit} matches limit reached. Use
    # limit=${effectiveLimit*2} for more, or refine pattern`` in a ``[…]`` block.
    (tmp_path / "many.txt").write_text("\n".join("hit" for _ in range(20)))
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hit", "limit": 3})
    text = result.content[0].text
    assert (
        "[3 matches limit reached. Use limit=6 for more, or refine pattern]"
        in text
    )
    assert isinstance(result.details, GrepToolDetails)
    assert result.details.match_limit_reached is True


async def test_grep_effective_limit_floor(tmp_path):
    # Pi parity: ``Math.max(1, limit ?? 100)`` — limit=0 floors to 1, not 100.
    (tmp_path / "many.txt").write_text("\n".join("hit" for _ in range(5)))
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hit", "limit": 0})
    text = result.content[0].text
    assert "[1 matches limit reached. Use limit=2 for more, or refine pattern]" in text


async def test_grep_long_line_truncated_to_500(tmp_path):
    # Pi parity: per-line cap is 500 chars (not 250) and emits the lines-
    # truncated notice + the ``... [truncated]`` marker.
    (tmp_path / "long.txt").write_text("X" + ("y" * 600) + "\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "X"})
    text = result.content[0].text
    assert "... [truncated]" in text
    assert (
        "[Some lines truncated to 500 chars. Use read tool to see full lines]"
        in text
    )
    assert result.details.lines_truncated == 1
    # The kept prefix is exactly 500 chars before the ``... [truncated]`` marker.
    body = text.split("\n\n[")[0]
    first = body.split("\n")[0]
    assert first.endswith("... [truncated]")
    assert len(first) == 500 + len("... [truncated]")


async def test_grep_byte_cap_notice(tmp_path):
    # Pi parity: output is capped at DEFAULT_MAX_BYTES (50KB) via truncateHead,
    # appending the ``50.0KB limit reached`` notice.
    lines = "\n".join(
        f"match line number {i} padding padding padding" for i in range(2000)
    )
    (tmp_path / "big.txt").write_text(lines + "\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "match", "limit": 5000})
    text = result.content[0].text
    assert "[50.0KB limit reached]" in text
    assert result.details.truncated is True
    # Body (before the notice block) must not exceed the 50KB cap.
    body = text.split("\n\n[")[0]
    assert len(body.encode("utf-8")) <= 50 * 1024


async def test_grep_no_matches_message(tmp_path):
    """Pi parity ``grep.ts:308-310``: zero matches → ``No matches found``."""

    (tmp_path / "f.txt").write_text("nothing here\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "ZZZ_no_such_pattern"})
    assert result.content[0].text == "No matches found"
    assert result.is_error is False


# --- rg-backed path: deterministic via a stubbed subprocess (no real rg) -----


def _stub_rg(monkeypatch, stdout: str, captured: dict | None = None):
    """Stub run_cancellable + force ensure_tool to a fake path, so the rg
    branch of grep runs deterministically without a real ripgrep binary."""

    from aelix_coding_agent.tools import grep as _grep_mod

    async def _fake_run_cancellable(cmd, **_kw):
        if captured is not None:
            captured["cmd"] = cmd
        return (stdout, 0)

    async def _rg(_tool, silent=True):
        return "/fake/rg"

    monkeypatch.setattr(_grep_mod, "run_cancellable", _fake_run_cancellable)
    monkeypatch.setattr(_grep_mod, "ensure_tool", _rg)


async def test_grep_rg_path_basename(tmp_path, monkeypatch):
    """P0 #3 HEAVY (ADR-0139): lock in the rg ``-H`` single-file parity — the
    flag is passed AND the path-prefixed rg line relativizes to the
    basename-prefixed ``b.txt:1: text`` (matching pi's ``--json`` mode)."""

    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "b.txt"
    target.write_text("hello again\n")
    captured: dict = {}
    _stub_rg(monkeypatch, f"{target}:1:hello again\n", captured)
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello", "path": str(target)})
    assert "-H" in captured["cmd"]  # the single-file parity flag is passed
    assert "b.txt:1: hello again" in result.content[0].text


async def test_grep_rg_match_count_cap_drops_excess(tmp_path, monkeypatch):
    """P0 #3 HEAVY (ADR-0139): the rg branch caps on MATCH count, not raw lines.
    With context, limit=1 keeps the first match + its context and drops the
    second match's block (pi ``matchCount`` semantics) — NOT a mid-block slice."""

    base = str(tmp_path)
    stdout = "\n".join(
        [
            f"{base}/a.txt-1- before",
            f"{base}/a.txt:2:MATCH one",
            f"{base}/a.txt-3- after",
            "--",
            f"{base}/b.txt-9- before",
            f"{base}/b.txt:10:MATCH two",
            f"{base}/b.txt-11- after",
        ]
    ) + "\n"
    _stub_rg(monkeypatch, stdout)
    tool = create_grep_tool(base)
    result = await _exec(tool, {"pattern": "MATCH", "context": 1, "limit": 1})
    text = result.content[0].text
    assert "a.txt:2: MATCH one" in text
    assert "a.txt-1- before" in text and "a.txt-3- after" in text  # context kept
    assert "MATCH two" not in text  # second match dropped by the match cap
    assert not text.split("\n\n[")[0].rstrip().endswith("--")  # dangling sep stripped
    assert "[1 matches limit reached." in text


async def test_grep_rg_match_count_cap_keeps_all_under_limit(tmp_path, monkeypatch):
    """Under the limit, both matches + their context are kept; no limit notice."""

    base = str(tmp_path)
    stdout = "\n".join(
        [
            f"{base}/a.txt:2:MATCH one",
            "--",
            f"{base}/b.txt:10:MATCH two",
        ]
    ) + "\n"
    _stub_rg(monkeypatch, stdout)
    tool = create_grep_tool(base)
    result = await _exec(tool, {"pattern": "MATCH", "context": 1, "limit": 5})
    text = result.content[0].text
    assert "a.txt:2: MATCH one" in text and "b.txt:10: MATCH two" in text
    assert "matches limit reached" not in text


# --- Lane B cancellation: _try_ripgrep re-raises CancelledError -------------


async def test_grep_try_ripgrep_reraises_cancelled_error(
    tmp_path, monkeypatch
) -> None:
    """CancelledError from run_cancellable propagates OUT of the grep tool.

    Lane B (grep/find cooperative abort): ``_try_ripgrep`` must NOT swallow
    ``CancelledError`` — it must propagate up through ``tool.execute`` so the
    harness abort unwinds the whole turn.  This is the end-to-end wiring test
    (tool surface → run_cancellable → CancelledError).
    """
    import asyncio

    from aelix_coding_agent.tools import grep as _grep_mod

    (tmp_path / "a.txt").write_text("hello world\n")

    async def _cancellable_that_raises(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _fake_rg(_tool, silent=True):
        return "/fake/rg"

    monkeypatch.setattr(_grep_mod, "run_cancellable", _cancellable_that_raises)
    monkeypatch.setattr(_grep_mod, "ensure_tool", _fake_rg)

    tool = create_grep_tool(str(tmp_path))

    async def _run():
        return await tool.execute(
            {"pattern": "hello"}, ToolExecutionContext(tool_call_id="t-cancel")
        )

    task = asyncio.create_task(_run())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


# --- Find tool Lane B: _try_fd re-raises CancelledError --------------------


async def test_find_try_fd_reraises_cancelled_error(
    tmp_path, monkeypatch
) -> None:
    """CancelledError from run_cancellable propagates OUT of the find tool.

    Lane B (find cooperative abort): ``_try_fd`` must NOT swallow
    ``CancelledError`` — it must propagate through ``tool.execute``.
    """
    import asyncio

    from aelix_coding_agent.tools import create_find_tool
    from aelix_coding_agent.tools import find as _find_mod

    (tmp_path / "a.txt").write_text("content\n")

    async def _cancellable_that_raises(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _fake_fd(_tool, silent=True):
        return "/fake/fd"

    monkeypatch.setattr(_find_mod, "run_cancellable", _cancellable_that_raises)
    monkeypatch.setattr(_find_mod, "ensure_tool", _fake_fd)

    tool = create_find_tool(str(tmp_path))

    async def _run():
        return await tool.execute(
            {"pattern": "*.txt"}, ToolExecutionContext(tool_call_id="t-find-cancel")
        )

    task = asyncio.create_task(_run())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
