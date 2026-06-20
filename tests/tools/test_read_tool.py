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
    # Pi parity: offset is 1-INDEXED — offset=2 starts at the SECOND line (b),
    # it does NOT skip to the third line.
    result = await _exec(tool, {"path": "n.txt", "offset": 2})
    assert result.content[0].text.startswith("b\nc\nd")


async def test_read_with_limit(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("\n".join(str(i) for i in range(100)))  # 100 lines, no trailing nl
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "n.txt", "limit": 5})
    assert result.is_error is False
    text = result.content[0].text
    assert text.startswith("0\n1\n2\n3\n4")
    # Pi parity branch C: a "N more lines" continuation notice; details undefined.
    assert "95 more lines in file. Use offset=6 to continue." in text
    assert result.details is None


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


async def test_read_no_line_numbering(tmp_path):
    # Pi parity: read returns the RAW slice — NO cat -n style line numbering.
    f = tmp_path / "ln.txt"
    f.write_text("alpha\nbeta\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "ln.txt"})
    assert result.content[0].text == "alpha\nbeta\n"
    assert "\t" not in result.content[0].text


async def test_read_offset_beyond_eof_is_error(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("only\n")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "n.txt", "offset": 50})
    assert result.is_error is True
    assert "beyond end of file" in result.content[0].text


async def test_read_byte_cap_truncation_notice(tmp_path):
    # Pi parity branch B: total bytes exceed 50KB -> truncateHead byte cap +
    # a continuation notice; details carry truncated_by="bytes".
    f = tmp_path / "big.txt"
    line = "x" * 200
    f.write_text("\n".join(line for _ in range(400)))  # ~80KB
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "big.txt"})
    text = result.content[0].text
    assert "50.0KB limit" in text
    assert "Use offset=" in text
    assert isinstance(result.details, ReadToolDetails)
    assert result.details.truncation.truncated_by == "bytes"


async def test_read_unicode(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("héllo αβ\n", encoding="utf-8")
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "u.txt"})
    assert "héllo" in result.content[0].text


async def test_read_execution_mode_parallel():
    tool = create_read_tool("/tmp")
    assert tool.execution_mode == "parallel"


def _valid_png_bytes(width: int = 2, height: int = 2, color=(255, 0, 0)) -> bytes:
    """Encode a real, PIL-decodable PNG (the byte-header-only fixtures used
    before cannot be opened by Pillow, so resize would drop the image)."""

    import io

    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_read_image_returns_image_content_and_note(tmp_path):
    """P0 #3 HEAVY (ADR-0139): an image read returns a ``Read image file
    [mime]`` text note + an ``ImageContent(mime_type, data)`` attachment — the
    Pi ``{type:"image", data, mimeType}`` shape (NOT the legacy data URL)."""

    import base64 as _b64

    from aelix_ai.messages import ImageContent, TextContent

    png = _valid_png_bytes()
    f = tmp_path / "tiny.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "tiny.png"})
    assert result.is_error is False
    text_parts = [c for c in result.content if isinstance(c, TextContent)]
    image_parts = [c for c in result.content if isinstance(c, ImageContent)]
    assert text_parts[0].text == "Read image file [image/png]"
    assert len(image_parts) == 1
    assert image_parts[0].mime_type == "image/png"
    # Small image is within 2000x2000 / 4.5MB → not resized → original bytes,
    # so no dimension note is appended (Pi formatDimensionNote → None).
    assert _b64.b64decode(image_parts[0].data) == png
    assert "Image: original" not in text_parts[0].text


async def test_read_image_auto_resize_disabled_forwards_raw(tmp_path):
    """Pi parity: ``auto_resize_images=False`` forwards the raw image."""

    import base64 as _b64

    from aelix_ai.messages import ImageContent

    png = _valid_png_bytes()
    f = tmp_path / "raw.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path), {"auto_resize_images": False})
    result = await _exec(tool, {"path": "raw.png"})
    image_parts = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(image_parts) == 1
    assert _b64.b64decode(image_parts[0].data) == png


async def test_read_image_non_vision_model_note(tmp_path):
    """Pi parity ``getNonVisionImageNote``: a non-vision ``ctx.model`` (no
    ``"image"`` in ``input``) appends the omission note."""

    from types import SimpleNamespace

    from aelix_ai.messages import TextContent

    png = _valid_png_bytes()
    f = tmp_path / "nv.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path))
    ctx = ToolExecutionContext(
        tool_call_id="t", model=SimpleNamespace(input=["text"])
    )
    result = await tool.execute({"path": "nv.png"}, ctx)
    text = [c for c in result.content if isinstance(c, TextContent)][0].text
    assert "[Current model does not support images." in text


async def test_read_image_vision_model_no_note(tmp_path):
    """Pi parity: a vision-capable ``ctx.model`` emits NO omission note."""

    from types import SimpleNamespace

    from aelix_ai.messages import TextContent

    png = _valid_png_bytes()
    f = tmp_path / "v.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path))
    ctx = ToolExecutionContext(
        tool_call_id="t", model=SimpleNamespace(input=["text", "image"])
    )
    result = await tool.execute({"path": "v.png"}, ctx)
    text = [c for c in result.content if isinstance(c, TextContent)][0].text
    assert text == "Read image file [image/png]"


async def test_read_image_resize_failure_text_only(tmp_path, monkeypatch):
    """Pi parity: when resize gives up (returns ``None``) the tool emits a
    text-only note and NO image attachment."""

    from aelix_ai.messages import ImageContent, TextContent

    async def _fail_resize(_img, _options=None):
        return None

    # Patch the exact namespace the tool's ``execute`` closure resolves
    # ``resize_image`` against (``create_read_tool.__globals__`` IS the read
    # module dict). Reload-proof vs a ``"a.b.c"`` string path, which can target
    # a different module object after a prior test perturbs ``sys.modules``.
    monkeypatch.setitem(
        create_read_tool.__globals__, "resize_image", _fail_resize
    )
    png = _valid_png_bytes()
    f = tmp_path / "fail.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "fail.png"})
    assert not [c for c in result.content if isinstance(c, ImageContent)]
    text = [c for c in result.content if isinstance(c, TextContent)][0].text
    assert "Read image file [image/png]" in text
    assert "could not be resized below the inline image size limit" in text


async def test_read_image_large_resized_dimension_note(tmp_path):
    """P0 #3 HEAVY: an over-2000px image is resized and carries the Pi
    coordinate-mapping dimension note."""

    from aelix_ai.messages import ImageContent, TextContent

    png = _valid_png_bytes(width=3000, height=1500, color=(10, 120, 200))
    f = tmp_path / "big.png"
    f.write_bytes(png)
    tool = create_read_tool(str(tmp_path))
    result = await _exec(tool, {"path": "big.png"})
    text = [c for c in result.content if isinstance(c, TextContent)][0].text
    image_parts = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(image_parts) == 1
    assert "[Image: original 3000x1500, displayed at 2000x" in text
    assert "Multiply coordinates by 1.50 to map to original image.]" in text
