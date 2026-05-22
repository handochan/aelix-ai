"""Sprint 6h₆/6h₈ — ``cli/file_processor.py`` tests.

Sprint 6h₆ (Phase 5a-i, ADR-0089) shipped the text-only port with
image-skip-with-warning behavior. Sprint 6h₈ (Phase 5a-iv, ADR-0092
§B) wires the real image branch via magic-byte detection + Pillow
resize; this test file is updated accordingly.

Covers text file reading, missing-file exit, empty-file skip, real
image processing (Sprint 6h₈), ~/ expansion, and the wrapping format.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from aelix_ai.messages import ImageContent
from aelix_coding_agent.cli.file_processor import (
    ProcessedFiles,
    process_file_arguments,
)
from PIL import Image

# === Text file branch ========================================================


async def test_text_file_wrapped(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("Hello, world!\n")
    result = await process_file_arguments([str(f)])
    assert result.text == '<file name="hello.txt">\nHello, world!\n\n</file>\n'
    assert result.images == []


async def test_text_file_relative_resolves_against_cwd(tmp_path: Path) -> None:
    f = tmp_path / "rel.txt"
    f.write_text("rel-content")
    result = await process_file_arguments(["rel.txt"], cwd=str(tmp_path))
    assert "rel-content" in result.text
    assert '<file name="rel.txt">' in result.text


async def test_text_file_absolute_path(tmp_path: Path) -> None:
    f = tmp_path / "abs.txt"
    f.write_text("abs-content")
    result = await process_file_arguments([str(f.resolve())])
    assert "abs-content" in result.text


async def test_multiple_text_files_concatenate_in_order(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("AAA")
    b = tmp_path / "b.txt"
    b.write_text("BBB")
    result = await process_file_arguments([str(a), str(b)])
    a_idx = result.text.index('<file name="a.txt">')
    b_idx = result.text.index('<file name="b.txt">')
    assert a_idx < b_idx
    assert "AAA" in result.text
    assert "BBB" in result.text


# === Missing file → sys.exit(1) ============================================


async def test_missing_file_exits_with_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nonexistent = tmp_path / "does_not_exist.txt"
    with pytest.raises(SystemExit) as exc_info:
        await process_file_arguments([str(nonexistent)])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err
    assert "file not found" in captured.err


# === Empty file skipped =====================================================


async def test_empty_file_skipped(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("")
    result = await process_file_arguments([str(f)])
    assert result.text == ""
    assert result.images == []


async def test_empty_file_does_not_block_subsequent(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    full = tmp_path / "full.txt"
    full.write_text("present")
    result = await process_file_arguments([str(empty), str(full)])
    assert "present" in result.text
    # Empty file's wrapper was NOT emitted.
    assert "empty.txt" not in result.text


# === Image branch (Sprint 6h₈ §B — real processing) =========================


async def test_real_png_image_processed(tmp_path: Path) -> None:
    """A real PNG file is base64-encoded and added to ``processed.images``."""

    img = Image.new("RGB", (10, 10), color=(200, 50, 100))
    path = tmp_path / "logo.png"
    img.save(path, format="PNG")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 1
    attachment = result.images[0]
    assert isinstance(attachment, ImageContent)
    # Small image fast-path → mime_type preserved as image/png.
    assert attachment.mime_type == "image/png"
    assert attachment.data  # non-empty base64 payload
    # The text stream includes a reference to the image file.
    assert '<file name="logo.png">' in result.text


async def test_real_jpeg_image_processed(tmp_path: Path) -> None:
    """A real JPEG file is processed into the images list."""

    img = Image.new("RGB", (10, 10), color=(50, 200, 100))
    path = tmp_path / "photo.jpg"
    img.save(path, format="JPEG")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 1
    attachment = result.images[0]
    assert isinstance(attachment, ImageContent)
    assert attachment.mime_type == "image/jpeg"
    assert '<file name="photo.jpg">' in result.text


async def test_real_gif_image_processed(tmp_path: Path) -> None:
    """A real GIF file is processed; resize falls through to PNG/JPEG."""

    img = Image.new("P", (10, 10), color=0)
    path = tmp_path / "anim.gif"
    img.save(path, format="GIF")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 1


async def test_oversized_image_resized_and_note_emitted(tmp_path: Path) -> None:
    """An over-large image is resized and a dimension note is emitted."""

    img = Image.new("RGB", (4000, 2000), color=(10, 20, 30))
    path = tmp_path / "big.png"
    img.save(path, format="PNG")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 1
    # Resized image emits the coordinate-mapping note.
    assert "original 4000x2000" in result.text
    assert "displayed at 2000x1000" in result.text


async def test_auto_resize_false_skips_resize(tmp_path: Path) -> None:
    """``auto_resize_images=False`` forwards images as-is."""

    img = Image.new("RGB", (50, 50), color=(0, 0, 255))
    path = tmp_path / "small.png"
    img.save(path, format="PNG")
    result = await process_file_arguments(
        [str(path)], auto_resize_images=False
    )
    assert len(result.images) == 1
    attachment = result.images[0]
    assert isinstance(attachment, ImageContent)
    # No dimension note in text because Pi parity emits empty body
    # when wasResized is False.
    assert '<file name="small.png"></file>' in result.text


async def test_text_file_with_image_extension(tmp_path: Path) -> None:
    """Magic bytes win: a ``.jpg`` containing text is text-processed."""

    path = tmp_path / "fake.jpg"
    path.write_text("not actually an image")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 0
    # Falls through to the text branch.
    assert '<file name="fake.jpg">' in result.text
    assert "not actually an image" in result.text


async def test_multiple_images_all_processed(tmp_path: Path) -> None:
    """All images in the arg list end up in ``processed.images``."""

    a = Image.new("RGB", (10, 10), color=(255, 0, 0))
    b = Image.new("RGB", (10, 10), color=(0, 255, 0))
    pa = tmp_path / "a.png"
    pb = tmp_path / "b.jpg"
    a.save(pa, format="PNG")
    b.save(pb, format="JPEG")
    result = await process_file_arguments([str(pa), str(pb)])
    assert len(result.images) == 2
    assert result.images[0].mime_type == "image/png"
    assert result.images[1].mime_type == "image/jpeg"


async def test_image_then_text_concatenate(tmp_path: Path) -> None:
    """Image references and text wrappers concatenate in order."""

    img = Image.new("RGB", (10, 10), color=(0, 0, 255))
    img_path = tmp_path / "x.png"
    img.save(img_path, format="PNG")
    txt_path = tmp_path / "y.txt"
    txt_path.write_text("text-body")
    result = await process_file_arguments([str(img_path), str(txt_path)])
    assert len(result.images) == 1
    # Image reference appears before text wrapper.
    img_idx = result.text.index('<file name="x.png">')
    txt_idx = result.text.index('<file name="y.txt">')
    assert img_idx < txt_idx


async def test_image_round_trip_base64(tmp_path: Path) -> None:
    """The base64 payload decodes back to a valid PIL image."""

    img = Image.new("RGB", (8, 8), color=(123, 45, 67))
    path = tmp_path / "img.png"
    img.save(path, format="PNG")
    result = await process_file_arguments([str(path)])
    assert len(result.images) == 1
    attachment = result.images[0]
    assert isinstance(attachment, ImageContent)
    raw = base64.b64decode(attachment.data)
    decoded = Image.open(io.BytesIO(raw))
    decoded.load()
    assert decoded.size == (8, 8)


# === ~/ expansion ===========================================================


async def test_tilde_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Point HOME at tmp_path so ``~/x.txt`` resolves into the sandbox.
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "x.txt"
    f.write_text("home-content")
    result = await process_file_arguments(["~/x.txt"])
    assert "home-content" in result.text


# === ProcessedFiles dataclass ===============================================


def test_processed_files_defaults() -> None:
    pf = ProcessedFiles()
    assert pf.text == ""
    assert pf.images == []


# === Empty arg list =========================================================


async def test_empty_args_returns_empty_result() -> None:
    result = await process_file_arguments([])
    assert result.text == ""
    assert result.images == []
