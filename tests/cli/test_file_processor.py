"""Sprint 6h₆ (Phase 5a-i, ADR-0089) — ``cli/file_processor.py`` tests.

Covers text file reading, missing-file exit, empty-file skip, image
warning, ~/ expansion, and the wrapping format.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_coding_agent.cli.file_processor import (
    ProcessedFiles,
    process_file_arguments,
)

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


# === Image branch — DEFERRED to 5a-iii ======================================


async def test_image_file_skipped_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    img = tmp_path / "logo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = await process_file_arguments([str(img)])
    assert result.text == ""
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "image" in captured.err.lower()


async def test_image_extensions_all_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    for ext in extensions:
        p = tmp_path / f"file{ext}"
        p.write_bytes(b"some-bytes")
    args = [str(tmp_path / f"file{e}") for e in extensions]
    result = await process_file_arguments(args)
    assert result.text == ""
    captured = capsys.readouterr()
    # One warning per file.
    for ext in extensions:
        assert f"file{ext}" in captured.err


async def test_image_case_insensitive_extension(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    img = tmp_path / "PHOTO.JPG"
    img.write_bytes(b"bytes")
    result = await process_file_arguments([str(img)])
    assert result.text == ""


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
