"""Sprint 5b §A — write tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_write_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_write_new_file(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "new.txt", "content": "hello"})
    assert result.is_error is False
    assert (tmp_path / "new.txt").read_text() == "hello"


async def test_write_creates_parent_dirs(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "a/b/c.txt", "content": "deep"})
    assert result.is_error is False
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "deep"


async def test_write_overwrites_existing(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("old")
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "w.txt", "content": "new"})
    assert result.is_error is False
    assert f.read_text() == "new"


async def test_write_missing_path():
    tool = create_write_tool("/tmp")
    result = await _exec(tool, {"content": "x"})
    assert result.is_error is True


async def test_write_missing_content(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "x.txt"})
    assert result.is_error is True


async def test_write_byte_count_reported(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "b.txt", "content": "abc"})
    assert "3" in result.content[0].text


async def test_write_success_message_wording_and_raw_path(tmp_path):
    # Pi parity: ``Successfully wrote {len} bytes to {rawPath}`` — the RAW
    # user-supplied path, not the resolved absolute path.
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "b.txt", "content": "abc"})
    assert result.is_error is False
    assert result.content[0].text == "Successfully wrote 3 bytes to b.txt"
    # File still lands at the resolved absolute location.
    assert (tmp_path / "b.txt").read_text() == "abc"


async def test_write_length_is_utf16_code_units(tmp_path):
    # Pi reports JS string ``.length`` (UTF-16 code units): BMP chars count 1,
    # astral chars (emoji) count 2 — NOT the UTF-8 byte length.
    tool = create_write_tool(str(tmp_path))
    # "héllo αβ" = 8 UTF-16 code units (all BMP), 11 UTF-8 bytes.
    result = await _exec(tool, {"path": "u.txt", "content": "héllo αβ"})
    assert result.content[0].text == "Successfully wrote 8 bytes to u.txt"


async def test_write_length_counts_astral_as_two(tmp_path):
    # "a😀b": 😀 (U+1F600) is one Python char but two UTF-16 code units, so
    # JS ``.length`` == 4 (vs 3 Python chars / 6 UTF-8 bytes).
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "e.txt", "content": "a😀b"})
    assert result.content[0].text == "Successfully wrote 4 bytes to e.txt"


async def test_write_utf8_round_trip(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "u.txt", "content": "héllo αβ"})
    assert result.is_error is False
    assert (tmp_path / "u.txt").read_text(encoding="utf-8") == "héllo αβ"


async def test_write_expands_tilde_home(tmp_path, monkeypatch):
    # resolve_to_cwd runs expand_path: a leading ``~/`` resolves to $HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    tool = create_write_tool(str(tmp_path / "elsewhere"))
    result = await _exec(tool, {"path": "~/tilde.txt", "content": "hi"})
    assert result.is_error is False
    assert (tmp_path / "tilde.txt").read_text() == "hi"
    # Success message echoes the RAW path verbatim (no expansion).
    assert result.content[0].text == "Successfully wrote 2 bytes to ~/tilde.txt"


async def test_write_strips_leading_at_mention(tmp_path):
    # expand_path strips a single leading ``@`` (model file-mention artifact).
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "@at.txt", "content": "x"})
    assert result.is_error is False
    assert (tmp_path / "at.txt").read_text() == "x"


async def test_write_collapses_unicode_space_in_path(tmp_path):
    # expand_path collapses special unicode spaces (here NBSP U+00A0) to ASCII.
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "a b.txt", "content": "x"})
    assert result.is_error is False
    assert (tmp_path / "a b.txt").read_text() == "x"


async def test_write_execution_mode_sequential():
    tool = create_write_tool("/tmp")
    assert tool.execution_mode == "sequential"
