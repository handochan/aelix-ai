"""WP-2 (ADR-0160) — footer segment registry tests.

Covers: the registry default-enabled set == the static spec; the default footer
is byte-identical to the pre-ADR-0160 hard-coded order; toggling a segment id
removes/restores exactly that segment; an adversarial enabled-set still respects
the ADR-0159 in-producer invariants (permission badge omit-when-no-provider +
leading position; steering hidden at the default).
"""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.footer_segments import (
    _SEGMENT_SPEC,
    FooterSegment,
    build_footer_registry,
    default_enabled_ids,
    default_enabled_ids_from_spec,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


class _FixedBranchFooter(AelixFooterData):
    def __init__(self, branch: str | None) -> None:
        super().__init__(cwd=".")
        self._branch = branch

    def get_git_branch(self) -> str | None:
        return self._branch


class _FakeStore:
    """A minimal statusline store stand-in (load() returns a config-like obj)."""

    def __init__(self, enabled: list[str]) -> None:
        self._enabled = enabled

    def load(self) -> object:
        store = self

        class _Cfg:
            enabled = store._enabled
            use_theme_colors = True

        return _Cfg()


@asynccontextmanager
async def _ctx(
    footer: AelixFooterData,
    *,
    model_provider=None,
    cwd=None,
    mode: str = "all",
    permission_badge_provider=None,
    statusline_store=None,
) -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome]]:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=200)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(
            chrome,
            footer,
            model_provider=model_provider,
            mode_provider=lambda: mode,
            permission_badge_provider=permission_badge_provider,
            cwd=cwd,
            statusline_store=statusline_store,
        )
        yield ctx, chrome


# === spec / default-enabled equivalence =================================


async def test_spec_matches_built_registry() -> None:
    async with _ctx(_FixedBranchFooter("main")) as (ctx, _chrome):
        reg = build_footer_registry(ctx)
        spec_full = [(s.id, s.label, s.description, s.default_enabled) for s in reg]
        assert spec_full == _SEGMENT_SPEC
        assert default_enabled_ids(reg) == default_enabled_ids_from_spec()


async def test_default_enabled_ids_are_the_canonical_order() -> None:
    # The default-ON set is the byte-identical pre-ADR-0160 footer order.
    assert default_enabled_ids_from_spec() == [
        "permission-mode",
        "steering",
        "pending-queued",
        "current-dir",
        "model",
        "context-remaining",
        "git-branch",
    ]


# === golden default footer (no store) ==================================


async def test_default_footer_is_byte_identical_to_pre_adr0160() -> None:
    # With a posture wired (DEFAULT badge), steering "all", a model, a cwd, and a
    # branch, the default footer composes EXACTLY as the old inline list did.
    footer = _FixedBranchFooter("main")
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        cwd="/tmp/proj",
        mode="all",
        permission_badge_provider=lambda: None,  # DEFAULT → "● default"
    ) as (_ctx_obj, chrome):
        assert chrome._footer_line == (
            "● default  ·  ⏵⏵ all  ·  📂 /tmp/proj  ·  ✱ gpt-4o  ·  ⎇ main"
        )


# === enabled-set gating ================================================


async def test_disabling_a_segment_removes_exactly_that_segment() -> None:
    footer = _FixedBranchFooter("main")
    # Enable everything EXCEPT the model segment.
    store = _FakeStore(
        ["permission-mode", "steering", "pending-queued", "current-dir",
         "context-remaining", "git-branch"]
    )
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        cwd="/tmp/proj",
        mode="all",
        permission_badge_provider=lambda: None,
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "✱ gpt-4o" not in line  # model removed
        assert "📂 /tmp/proj" in line  # neighbours intact
        assert "⎇ main" in line
        assert "● default" in line


async def test_reenabling_a_segment_restores_it() -> None:
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["model", "git-branch"])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        mode="all",
        statusline_store=store,
    ) as (ctx, chrome):
        assert "✱ gpt-4o" in chrome._footer_line
        assert "📂" not in chrome._footer_line  # current-dir not enabled
        # restore current-dir
        store._enabled = ["model", "current-dir", "git-branch"]
        ctx._cwd = "/tmp/proj"
        ctx._refresh_footer()
        assert "📂 /tmp/proj" in chrome._footer_line


async def test_optional_token_cost_segments_off_by_default() -> None:
    footer = _FixedBranchFooter("main")
    async with _ctx(footer, model_provider=lambda: "gpt-4o", mode="all") as (
        ctx,
        chrome,
    ):
        ctx.set_usage_stats(123, 45, 0.0099)
        assert "↑" not in chrome._footer_line  # input-tokens off by default
        assert "↓" not in chrome._footer_line
        assert "$" not in chrome._footer_line


async def test_optional_token_cost_segments_render_when_enabled() -> None:
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["model", "input-tokens", "output-tokens", "cost"])
    async with _ctx(
        footer, model_provider=lambda: "gpt-4o", mode="all", statusline_store=store
    ) as (ctx, chrome):
        ctx.set_usage_stats(1234, 56, 0.0099)
        line = chrome._footer_line
        assert "↑ 1,234" in line
        assert "↓ 56" in line
        assert "$ 0.0099" in line


# === ADR-0159 invariants survive an adversarial enabled-set =============


async def test_adversarial_store_cannot_surface_badge_without_provider() -> None:
    # An enabled-set listing permission-mode must NOT surface a badge when NO
    # posture provider is wired (the omit-when-no-provider rule lives in the
    # producer, independent of the enabled-set).
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["permission-mode", "model", "git-branch"])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        mode="all",
        permission_badge_provider=None,  # no posture
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "● default" not in line
        assert "✎" not in line and "⏸" not in line and "⚠" not in line


async def test_adversarial_store_keeps_badge_leading() -> None:
    footer = _FixedBranchFooter("main")
    # Even with an arbitrary enabled order in the set, rendering order is the
    # REGISTRY order — so the permission badge stays leftmost (before steering).
    store = _FakeStore(["git-branch", "steering", "permission-mode", "model"])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        mode="all",
        permission_badge_provider=lambda: None,
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "● default" in line
        assert "⏵⏵ all" in line
        assert line.index("● default") < line.index("⏵⏵ all")


async def test_adversarial_store_keeps_steering_hidden_at_default() -> None:
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["steering", "git-branch"])
    async with _ctx(
        footer,
        mode="one-at-a-time",  # default → steering hidden by the producer
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        assert "⏵⏵" not in chrome._footer_line


async def test_empty_enabled_set_only_drops_user_unchecked() -> None:
    # An EMPTY enabled-set hides every user-toggleable segment, but the producer
    # rules still hold (nothing surfaces a stray badge / steering). Extension
    # statuses (not registry segments) would still append — none here.
    footer = _FixedBranchFooter("main")
    store = _FakeStore([])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        mode="all",
        permission_badge_provider=lambda: None,
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        assert chrome._footer_line == ""


def test_footer_segment_is_frozen() -> None:
    import dataclasses

    import pytest

    seg = FooterSegment("x", "X", "desc", lambda: None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        seg.id = "y"  # type: ignore[misc]
