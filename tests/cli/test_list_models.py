"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — list-models CLI tests.

Covers:
  - :func:`format_token_count` numeric formatting (Pi parity).
  - :func:`list_models` table output structure / sort / filter /
    empty-result message / no-models-available fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.list_models import format_token_count, list_models

# === format_token_count =====================================================


def test_format_token_count_thousands() -> None:
    assert format_token_count(200000) == "200K"


def test_format_token_count_millions_with_decimal() -> None:
    assert format_token_count(1_500_000) == "1.5M"


def test_format_token_count_millions_strips_trailing_zero() -> None:
    # 2_000_000 → "2M", NOT "2.0M".
    assert format_token_count(2_000_000) == "2M"


def test_format_token_count_thousands_with_decimal() -> None:
    assert format_token_count(1500) == "1.5K"


def test_format_token_count_below_thousand() -> None:
    assert format_token_count(500) == "500"


def test_format_token_count_zero() -> None:
    assert format_token_count(0) == "0"


# === list_models ============================================================


@dataclass
class _FakeRegistry:
    """Minimal :class:`ModelRegistry` test double.

    Implements only the surface :func:`list_models` calls:
    :meth:`get_available` + :meth:`get_error`.
    """

    available: list[Model]
    error: str | None = None

    def get_available(self) -> list[Model]:
        return list(self.available)

    def get_error(self) -> str | None:
        return self.error


def _make_model(provider: str, model_id: str, **extras: Any) -> Model:
    return Model(
        id=model_id,
        provider=provider,
        context_window=extras.get("context_window", 200000),
        max_tokens=extras.get("max_tokens", 8000),
        reasoning=extras.get("reasoning", False),
        input=extras.get("input", ["text"]),
    )


async def test_no_models_available_inline_fallback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(available=[])
    await list_models(registry, None)  # type: ignore[arg-type]
    captured = capsys.readouterr()
    assert "No models available" in captured.out


async def test_table_output_has_header_and_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[
            _make_model("anthropic", "claude-3-5-sonnet"),
            _make_model("openai", "gpt-4o"),
        ]
    )
    await list_models(registry, None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    # Header + 2 rows.
    assert len(lines) == 3
    # 6-column header.
    header = lines[0]
    for col in ("provider", "model", "context", "max-out", "thinking", "images"):
        assert col in header


async def test_sort_by_provider_then_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[
            _make_model("openai", "gpt-4o"),
            _make_model("anthropic", "claude-3-5-sonnet"),
            _make_model("anthropic", "claude-3-opus"),
        ]
    )
    await list_models(registry, None)  # type: ignore[arg-type]
    lines = capsys.readouterr().out.strip().splitlines()
    # Lines: header, anthropic/claude-3-5-sonnet, anthropic/claude-3-opus, openai/gpt-4o.
    assert "anthropic" in lines[1]
    assert "claude-3-5-sonnet" in lines[1]
    assert "anthropic" in lines[2]
    assert "claude-3-opus" in lines[2]
    assert "openai" in lines[3]


async def test_fuzzy_filter_reduces_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[
            _make_model("anthropic", "claude-3-5-sonnet"),
            _make_model("openai", "gpt-4o"),
            _make_model("openai", "gpt-3.5"),
        ]
    )
    await list_models(registry, "gpt")  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "gpt-4o" in out
    assert "gpt-3.5" in out
    assert "claude" not in out


async def test_empty_filter_result_prints_no_models_matching(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[_make_model("anthropic", "claude-3-5-sonnet")]
    )
    await list_models(registry, "xyznonexistent")  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert 'No models matching "xyznonexistent"' in out


async def test_load_error_warning_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[_make_model("anthropic", "claude-3-5-sonnet")],
        error="bad json near line 3",
    )
    await list_models(registry, None)  # type: ignore[arg-type]
    captured = capsys.readouterr()
    # Plain stderr text, no ANSI (Aelix-additive divergence vs Pi).
    assert "Warning" in captured.err
    assert "bad json" in captured.err
    # No ANSI escape sequence.
    assert "\x1b[" not in captured.err


async def test_true_search_pattern_skips_filter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--list-models`` with no pattern parses as ``True`` (per Args)."""

    registry = _FakeRegistry(
        available=[
            _make_model("anthropic", "claude-3-5-sonnet"),
            _make_model("openai", "gpt-4o"),
        ]
    )
    await list_models(registry, True)  # type: ignore[arg-type]
    lines = capsys.readouterr().out.strip().splitlines()
    # All models present (header + 2 rows).
    assert len(lines) == 3


async def test_images_column_yes_when_input_has_image(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _FakeRegistry(
        available=[
            _make_model(
                "openai",
                "gpt-4o",
                input=["text", "image"],
                reasoning=True,
            )
        ]
    )
    await list_models(registry, None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    # Header + 1 row.
    row = lines[1]
    # Final two columns are thinking + images.
    assert "yes" in row
