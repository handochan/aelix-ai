"""Sprint 5b §A — ls tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_ls_tool
from aelix_coding_agent.tools._truncate import DEFAULT_MAX_BYTES, format_size


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_ls_lists_entries(tmp_path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    assert result.is_error is False
    assert "a.txt" in result.content[0].text
    assert "b.txt" in result.content[0].text


async def test_ls_marks_dirs_with_trailing_slash(tmp_path):
    (tmp_path / "subdir").mkdir()
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    assert "subdir/" in result.content[0].text


async def test_ls_sorted_alphabetically(tmp_path):
    for name in ("z.txt", "a.txt", "m.txt"):
        (tmp_path / name).write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    text = result.content[0].text
    a_idx = text.index("a.txt")
    m_idx = text.index("m.txt")
    z_idx = text.index("z.txt")
    assert a_idx < m_idx < z_idx


async def test_ls_missing_dir(tmp_path):
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"path": str(tmp_path / "missing")})
    assert result.is_error is True


async def test_ls_limit(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 5})
    assert result.is_error is False
    # Only `limit` entries are emitted (plus the appended notice block).
    text = result.content[0].text
    entry_lines = text.split("\n\n[")[0].splitlines()
    assert len(entry_lines) == 5


async def test_ls_entry_limit_notice(tmp_path):
    # Pi parity: appended actionable notice when the entry limit is exceeded.
    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 5})
    assert "[5 entries limit reached. Use limit=10 for more]" in result.content[0].text
    assert result.details.entry_limit_reached is True


async def test_ls_entry_limit_off_by_one_exact(tmp_path):
    # Exactly `limit` entries must NOT trigger the entry-limit notice
    # (off-by-one: strictly greater than limit, not >=).
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 5})
    assert "limit reached" not in result.content[0].text
    assert result.details.entry_limit_reached is False
    assert len(result.content[0].text.splitlines()) == 5


async def test_ls_case_insensitive_sort(tmp_path):
    for name in ("Banana.txt", "apple.txt", "Cherry.txt"):
        (tmp_path / name).write_text("")
    tool = create_ls_tool(str(tmp_path))
    text = (await _exec(tool, {})).content[0].text
    a_idx = text.index("apple.txt")
    b_idx = text.index("Banana.txt")
    c_idx = text.index("Cherry.txt")
    assert a_idx < b_idx < c_idx


async def test_ls_byte_cap_and_notice(tmp_path):
    # Pi parity: output capped to DEFAULT_MAX_BYTES (50KB) with a byte notice.
    long_name = "x" * 200
    for i in range(400):
        (tmp_path / f"{long_name}_{i:04d}.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 100000})
    text = result.content[0].text
    body = text.split("\n\n[")[0]
    assert len(body.encode("utf-8")) <= DEFAULT_MAX_BYTES
    assert f"{format_size(DEFAULT_MAX_BYTES)} limit reached" in text
    assert format_size(DEFAULT_MAX_BYTES) == "50.0KB"
    assert result.details.truncated is True


async def test_ls_stat_failure_skips_entry(tmp_path):
    # Pi parity: entries that raise on stat are skipped (catch{continue}).
    (tmp_path / "good.txt").write_text("")
    (tmp_path / "bad_entry").write_text("")

    import pathlib

    real_is_dir = pathlib.Path.is_dir

    def _fake_is_dir(self):
        if self.name == "bad_entry":
            raise OSError("cannot stat")
        return real_is_dir(self)

    pathlib.Path.is_dir = _fake_is_dir
    try:
        tool = create_ls_tool(str(tmp_path))
        result = await _exec(tool, {})
    finally:
        pathlib.Path.is_dir = real_is_dir
    text = result.content[0].text
    assert "good.txt" in text
    assert "bad_entry" not in text


async def test_ls_custom_path(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inside.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"path": "sub"})
    assert "inside.txt" in result.content[0].text


async def test_ls_execution_mode_parallel():
    tool = create_ls_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_ls_empty_directory_sentinel(tmp_path):
    # Pi parity (``ls.ts``): a truly empty dir returns "(empty directory)"
    # with no details, not an empty string.
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    assert result.is_error is False
    assert result.content[0].text == "(empty directory)"
    assert result.details is None


async def test_ls_limit_zero_preserved_yields_empty_sentinel(tmp_path):
    # Pi parity: limit is ``?? DEFAULT`` (nullish), so an explicit limit=0 is
    # preserved -> zero entries -> "(empty directory)" (NOT coalesced to 500).
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 0})
    assert result.content[0].text == "(empty directory)"
    assert result.details is None
