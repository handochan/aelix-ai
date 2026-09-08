"""WP-2 (ADR-0160) — footer segment registry tests.

Covers: the registry default-enabled set == the static spec; the exact ids and
order a fresh install renders, and where the 🧠 thinking-level segment sits in
the composed row (#248 turned it on by default and moved it after the model);
toggling a segment id removes/restores exactly that segment; an adversarial
enabled-set still respects the ADR-0159 in-producer invariants (permission badge
omit-when-no-provider + leading position; steering hidden at the default).
"""

from __future__ import annotations

import io
import json
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
    thinking_provider=None,
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
            thinking_provider=thinking_provider,
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
    # The exact ids AND order ``shell.py`` seeds a fresh statusline store with.
    # Without the order the seed can drift from the /statusline picker preview
    # silently. ``thinking-level`` sits after ``model`` since #248.
    assert default_enabled_ids_from_spec() == [
        "permission-mode",
        "steering",
        "pending-queued",
        "current-dir",
        "model",
        "thinking-level",
        "context-remaining",
        "git-branch",
    ]


# === golden default footer (no store) ==================================


async def test_default_footer_without_a_thinking_provider() -> None:
    # With a posture wired (DEFAULT badge), steering "all", a model, a cwd, and a
    # branch — but NO thinking provider — the default footer is unchanged by #248:
    # the thinking-level producer omits the segment when no provider is wired
    # (the ADR-0159 in-producer omit rule), so turning it on by default cannot
    # make a headless footer grow. This golden pins that rule, not the default set.
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


async def test_default_footer_with_a_thinking_provider_puts_the_brain_after_the_model() -> None:
    # #248 — the rendered position, both neighbours. The context label is set
    # deliberately: without it ``_context_remaining`` returns None and the line is
    # byte-identical whether the 🧠 sits before or after ``context-remaining``, so
    # the case would be blind to half of the move.
    footer = _FixedBranchFooter("main")
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        thinking_provider=lambda: "high",
        cwd="/tmp/proj",
        mode="all",
        permission_badge_provider=lambda: None,  # DEFAULT → "● default"
    ) as (ctx, chrome):
        ctx.set_context_label("◔ 42% · 84K/200K")
        ctx._refresh_footer()
        assert chrome._footer_line == (
            "● default  ·  ⏵⏵ all  ·  📂 /tmp/proj  ·  ✱ gpt-4o  ·  🧠 high"
            "  ·  ◔ 42% · 84K/200K  ·  ⎇ main"
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


# === thinking-level segment (beta) =====================================


async def test_thinking_level_producer_omits_without_provider() -> None:
    # No provider wired (headless/tests) → the producer omits the segment
    # (returns None), consistent with the other opt-in producers.
    footer = _FixedBranchFooter("main")
    async with _ctx(footer) as (ctx, _chrome):
        reg = {s.id: s for s in build_footer_registry(ctx)}
        assert reg["thinking-level"].produce() is None


async def test_thinking_level_producer_returns_live_value() -> None:
    footer = _FixedBranchFooter("main")
    async with _ctx(footer, thinking_provider=lambda: "high") as (ctx, _chrome):
        reg = {s.id: s for s in build_footer_registry(ctx)}
        assert reg["thinking-level"].produce() == "🧠 high"


async def test_thinking_level_on_by_default_in_footer() -> None:
    # #248 — default-ON: a provider wired and no store on disk → rendered.
    footer = _FixedBranchFooter("main")
    async with _ctx(
        footer, model_provider=lambda: "gpt-4o", thinking_provider=lambda: "high"
    ) as (_ctx_obj, chrome):
        assert "🧠 high" in chrome._footer_line


async def test_thinking_level_renders_when_enabled() -> None:
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["model", "thinking-level"])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        thinking_provider=lambda: "high",
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        assert "🧠 high" in chrome._footer_line


async def test_thinking_level_shows_off_not_none() -> None:
    # DELIBERATE deviation from cost/tokens: the segment SHOWS "🧠 off" (never
    # returns None) so a user who opted IN to monitor reasoning effort still sees
    # it when reasoning is off. The sharp edge the ``or 'off'`` guard exists for
    # is a provider that returns None or "" (a partial / headless harness with no
    # resolved level) — those must STILL render "🧠 off", never "🧠 None" and
    # never a bare "🧠 " with no level. Feeding a truthy "off" would pass with OR
    # WITHOUT the guard, so this exercises the falsy inputs the guard actually
    # handles.
    footer = _FixedBranchFooter("main")
    store = _FakeStore(["thinking-level"])
    for provider in (lambda: None, lambda: ""):
        async with _ctx(
            footer, thinking_provider=provider, statusline_store=store
        ) as (ctx, chrome):
            reg = {s.id: s for s in build_footer_registry(ctx)}
            assert reg["thinking-level"].produce() == "🧠 off"
            line = chrome._footer_line
            assert "🧠 off" in line
            assert "🧠 None" not in line
            # No bare glyph with an empty level ("🧠 " followed by nothing).
            assert not line.rstrip().endswith("🧠")


# === #248 — what the new default does and does NOT reach ================

_PRE_248_ENABLED = [
    "permission-mode",
    "steering",
    "pending-queued",
    "current-dir",
    "model",
    "context-remaining",
    "git-branch",
]
"""The default-enabled set as it shipped before #248 — i.e. exactly what an
existing ``statusline.json`` written by the /statusline picker holds."""


async def test_a_saved_store_without_thinking_level_stays_without_it(tmp_path) -> None:
    # The CHANGELOG's promise: a file already on disk is read VERBATIM and is not
    # migrated ON. It cannot tell "never saw this option" apart from "unchecked
    # it", so flipping the default must not reach it. A REAL StatuslineStore, not
    # the fake — the verbatim-vs-defaults branch is in ``load()``.
    from aelix_coding_agent.tui.statusline_store import StatuslineStore

    path = tmp_path / "statusline.json"
    path.write_text(json.dumps({"enabled": _PRE_248_ENABLED}), encoding="utf-8")
    store = StatuslineStore(path, default_enabled=default_enabled_ids_from_spec())
    assert "thinking-level" not in store.load().enabled

    footer = _FixedBranchFooter("main")
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        thinking_provider=lambda: "high",
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        assert "🧠" not in chrome._footer_line


async def test_a_mid_session_model_switch_keeps_the_level_the_user_last_set() -> None:
    # The limitation the CHANGELOG and the ``_thinking_level`` comment disclose,
    # end to end. ``AgentHarness.set_model`` mutates ``_state.model`` only —
    # nothing resets ``_state.thinking_level`` — so a switch to a model whose ONLY
    # supported level is "off" still renders the level last set, not ``🧠 off``.
    # The closure below is the body of ``shell.py::_thinking_level`` verbatim.
    # Default-ON (#248) is what makes this visible out of the box; if #251 (or
    # anything else) starts resetting the level on a model switch this dies, and
    # the CHANGELOG sentence it pins has to be rewritten with it.
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.models import get_supported_thinking_levels
    from aelix_ai.streaming import Model

    harness = AgentHarness(AgentHarnessOptions())
    await harness.set_thinking_level("high")
    await harness.set_model(Model(api="openai", id="gpt-4o"))
    assert get_supported_thinking_levels(harness.state.model) == ["off"]

    footer = _FixedBranchFooter("main")
    async with _ctx(
        footer,
        model_provider=lambda: harness.state.model.id,
        thinking_provider=lambda: getattr(harness.state, "thinking_level", None),
    ) as (_ctx_obj, chrome):
        assert "🧠 high" in chrome._footer_line


async def test_a_saved_store_that_already_enables_it_gets_the_new_position() -> None:
    # The one behaviour change #248 reaches EXISTING users with: the footer
    # renders in registry order, not in the persisted list order, so a saved file
    # that already enables the id sees the 🧠 leave the end of the row and land
    # after the model. Nothing else pins that.
    footer = _FixedBranchFooter("main")
    store = _FakeStore([*_PRE_248_ENABLED, "thinking-level"])
    async with _ctx(
        footer,
        model_provider=lambda: "gpt-4o",
        thinking_provider=lambda: "high",
        cwd="/tmp/proj",
        mode="all",
        permission_badge_provider=lambda: None,
        statusline_store=store,
    ) as (_ctx_obj, chrome):
        assert chrome._footer_line == (
            "● default  ·  ⏵⏵ all  ·  📂 /tmp/proj  ·  ✱ gpt-4o  ·  🧠 high  ·  ⎇ main"
        )


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
