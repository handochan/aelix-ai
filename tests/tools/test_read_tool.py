"""Sprint 5b §A — read tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_read_tool
from aelix_coding_agent.tools.read import ReadToolDetails


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_read_simple_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\nline3\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "hello.txt"})
    assert result.is_error is False
    assert "line1" in result.content[0].text


async def test_read_with_offset(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("a\nb\nc\nd\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "n.txt", "offset": 2})
    assert "c" in result.content[0].text


async def test_read_with_limit(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("\n".join(str(i) for i in range(100)))
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "n.txt", "limit": 5})
    assert isinstance(result.details, ReadToolDetails)
    assert result.details.truncation.kept_lines == 5


async def test_read_absolute_path(tmp_path):
    f = tmp_path / "abs.txt"
    f.write_text("absolute\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": str(f)})
    assert result.is_error is False


async def test_read_missing_file(tmp_path):
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "does-not-exist.txt"})
    assert result.is_error is True


async def test_read_missing_path():
    tool = create_read_tool("/tmp")
    result = await _exec(tool, {})
    assert result.is_error is True


async def test_read_directory_is_error(tmp_path):
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "."})
    assert result.is_error is True


async def test_read_line_numbering(tmp_path):
    f = tmp_path / "ln.txt"
    f.write_text("alpha\nbeta\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "ln.txt"})
    # Pi parity: cat -n style line numbers.
    assert "1" in result.content[0].text


async def test_read_unicode(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("héllo αβ\n", encoding="utf-8")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "u.txt"})
    assert "héllo" in result.content[0].text


async def test_read_execution_mode_parallel():
    tool = create_read_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_read_image_emits_data_url_base64(tmp_path):
    """W4 MAJOR-2 regression: image bytes must be base64-encoded inside a
    ``data:<mime>;base64,...`` URL (Pi parity ``buffer.toString("base64")``).

    Previously the source field was hex (``data.hex()``), which downstream
    multimodal consumers cannot decode. This guards against re-regression.
    """

    from aelix_ai.messages import ImageContent

    # 1x1 transparent PNG header bytes (enough to assert encoding semantics).
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    f = tmp_path / "tiny.png"
    f.write_bytes(png_bytes)
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "tiny.png"})
    assert result.is_error is False
    image_parts = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(image_parts) == 1
    src = image_parts[0].source
    assert src.startswith("data:image/png;base64,"), src[:40]
    # Round-trip: base64 payload after the prefix must decode to the bytes.
    import base64 as _b64

    payload = src.split(",", 1)[1]
    assert _b64.b64decode(payload) == png_bytes
