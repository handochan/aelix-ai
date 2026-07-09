"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — ``run_tui`` interactive shell.

Sprint 6h₁₀b rework: ``run_tui`` drives a single long-running
:class:`~aelix_coding_agent.tui.chrome.AelixChrome` ``Application`` (persistent
status / footer / spinner / input) instead of per-turn ``PromptSession.
prompt_async``. It binds a concrete :class:`~aelix_coding_agent.tui.context.
AelixTUIContext` via ``harness.runtime.bind_ui`` so loaded extensions can drive
the UI, subscribes an :class:`~aelix_coding_agent.tui.render.EventRenderer`, and
runs an **output pump** that flushes committed Rich renderables above the chrome
via ``chrome.print_above`` (``in_terminal``) in order. The in-progress streamed
window rides in the chrome's stream widget.

Lifecycle parity with ``run_print_mode``: signal handlers → ``set_rebind_session``
→ ``bootstrap`` → drive turns → dispose in ``finally`` (also unbinds the UI back
to headless). A failed turn does not kill the REPL. Input parsing reuses
``cli/repl.py`` precedent (``handle_user_bash`` + ``parse_input_line``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_agent_core.session.context import build_display_messages
from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata
from prompt_toolkit.application.run_in_terminal import in_terminal
from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aelix_coding_agent.cli.repl import handle_user_bash
from aelix_coding_agent.extensions import HEADLESS_UI_CONTEXT
from aelix_coding_agent.extensions.api import MessageRenderOptions
from aelix_coding_agent.extensions.command_dispatch import (
    CommandDispatchService,
    CommandSurfaceBindings,
    DispatchOutcome,
)
from aelix_coding_agent.tui.activity_tracker import SessionActivityTracker
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    BuiltinCommand,
    CommandContext,
    match_command,
    slash_word,
)
from aelix_coding_agent.tui.completion import (
    DescriptorCommandCompleter,
    FileMentionCompleter,
)
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.descriptors import (
    DescriptorRegistry,
    DescriptorRenderer,
    ListModulesProbe,
)
from aelix_coding_agent.tui.ext_widgets import apply_manifest_widgets
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.input import parse_input_line
from aelix_coding_agent.tui.mcp_viewer import run_mcp_viewer
from aelix_coding_agent.tui.model_picker import run_model_picker
from aelix_coding_agent.tui.overlay import show_modal
from aelix_coding_agent.tui.render import (
    EventRenderer,
    _render_diff,
    component_to_text,
    render_user_message,
)
from aelix_coding_agent.tui.thinking_picker import run_thinking_picker

if TYPE_CHECKING:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime
    from aelix_ai.oauth import AuthStorage
    from aelix_ai.settings import SettingsManager
    from prompt_toolkit.completion import Completer

    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture
    from aelix_coding_agent.extensions.api import Extension
    from aelix_coding_agent.mcp import McpClientManager
    from aelix_coding_agent.model_registry import ModelRegistry

_log = logging.getLogger(__name__)

_RENDER_WIDTH = 80
# Sprint 6h₃₃ (ADR-0168, WP-8 D3) — cap on retained /stats history rows. Pruned
# once at startup so stats-history.jsonl can't grow without bound across sessions.
_HISTORY_MAX_RECORDS = 5000
# Sprint 6h₂₂ (ADR-0130) — chrome widget key for the auto-retry countdown.
# W-review LOW-4: keep this module-level so tests + future docs reference one
# canonical name; ``__dunder__`` convention signals private-to-shell.
_RETRY_WIDGET_KEY = "__auto_retry__"


def _reload_rebuild_enabled() -> bool:
    """Issue #24 / #53 — full factory-rebuild ``/reload`` (go-live default, ADR-0177).

    ``/reload`` routes through :meth:`AgentSessionRuntime.reload` — re-discovers
    on-disk extensions + rebuilds the runtime via the P-302 factory, so an
    agent-written extension goes live WITHOUT a process restart (the #53 moat).
    DEFAULT-ON after the multi-lens adversarial review. Set ``AELIX_RELOAD_REBUILD``
    to a falsy value (``0``/``false``/``no``/``off``) as a kill-switch to fall back
    to the cheap resources-discover refresh (``harness.reload_resources()``).
    """

    return os.environ.get("AELIX_RELOAD_REBUILD", "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _format_context_label(usage: object) -> str | None:
    """Format a harness ``ContextUsage`` into a footer segment.

    ``"◔ 42% · 84K/200K"`` when both percent and token counts are known;
    degrades to whichever part is available; ``None`` when usage is missing
    (e.g. model registry not wired — Pi returns undefined there too).
    """
    if usage is None:
        return None
    from aelix_coding_agent.cli.list_models import format_token_count

    percent = getattr(usage, "percent", None)
    tokens = getattr(usage, "tokens", None)
    window = getattr(usage, "context_window", None)
    parts: list[str] = []
    if isinstance(percent, int | float):
        parts.append(f"{percent:.0f}%")
    if isinstance(tokens, int) and isinstance(window, int) and window > 0:
        parts.append(f"{format_token_count(tokens)}/{format_token_count(window)}")
    return f"◔ {' · '.join(parts)}" if parts else None


# #66 item 6 — the permission mode drives BOTH the footer badge colour and the
# ❯ prompt colour, from ONE source (``MODE_META[...].badge_style``). Extracted to
# module level (not run_tui closures) so both are unit-testable with a plain
# ``PermissionPosture``.
_DEFAULT_PROMPT_STYLE = "class:aelix.prompt bold fg:cyan"


def _mode_badge_ansi(posture: PermissionPosture | None) -> str | None:
    """The footer permission badge, SGR-coloured by the mode's ``badge_style``.

    #66 item 6a. ``None`` on DEFAULT / no posture — the footer producer then
    substitutes the neutral (uncoloured) ``DEFAULT_BADGE``. Reads the SAME
    posture object shift+tab mutates.
    """

    if posture is None:
        return None
    from aelix_coding_agent.builtin.permission_mode import MODE_META
    from aelix_coding_agent.tui.footer_segments import sgr_wrap

    meta = MODE_META[posture.get()]
    if not meta.badge_text:
        return None
    return sgr_wrap(meta.badge_text, meta.badge_style)


def _mode_prompt_style(posture: PermissionPosture | None) -> str:
    """The ❯ input-prefix style following the mode (single source: ``badge_style``).

    #66 item 6b. DEFAULT / no posture / an empty style → the neutral bold cyan.
    """

    if posture is None:
        return _DEFAULT_PROMPT_STYLE
    from aelix_coding_agent.builtin.permission_mode import MODE_META, PermissionMode

    mode = posture.get()
    style = MODE_META[mode].badge_style
    if mode == PermissionMode.DEFAULT or not style:
        return _DEFAULT_PROMPT_STYLE
    # The ❯ prompt is always bold; fold in the badge_style colour token(s)
    # without duplicating a "bold" already present in it (e.g. yolo="bold red").
    colour = " ".join(t for t in style.split() if t.lower() != "bold")
    return f"class:aelix.prompt bold {colour}".rstrip()


def _format_session_choice(meta: object) -> str:
    """A one-line picker label for a session (``/resume``).

    ``JsonlSessionMetadata`` carries id + created_at + cwd (no title / message
    count), so the label is ``{created} · {short-id}``; degrades to the short id
    when no timestamp is present. Defensive getattr — never raises on an odd
    metadata shape.
    """

    short_id = (getattr(meta, "id", "") or "")[:8]
    created = (getattr(meta, "created_at", "") or "").replace("T", " ")[:16]
    if created:
        return f"{created} · {short_id}" if short_id else created
    return short_id or "session"


def _message_text(message: object) -> str:
    """Join the ``TextContent`` of a queued message (``/dequeue`` restore).

    Defensive — a non-list content / odd payload yields ``""`` (the dequeue path
    filters empties)."""

    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return ""
    return "\n".join(
        getattr(b, "text", "") or ""
        for b in content
        if getattr(b, "type", None) == "text"
    )


def _drive_compaction_indicator(
    chrome: AelixChrome, state: dict[str, Any], etype: object
) -> None:
    """Toggle the working-row "Compacting context…" indicator on compaction.

    A manual ``/compact`` dispatches OUTSIDE the ``set_running`` turn wrapper, so
    its multi-second summarizer LLM call would otherwise leave the prompt looking
    frozen. Driven from the harness ``compaction_start`` / ``compaction_end``
    subscriber events (manual + threshold + overflow), this shows a live spinner
    with a "Compacting context…" label in the same row as "Working…".

    ``state`` persists across calls (a closure dict in ``run_tui``). On the rising
    edge it captures the prior working row (message + visibility) so the matched
    end restores it — an auto-compaction fires mid-turn where the row is already
    visible via ``set_running`` and must be left intact. ``core`` emits a matched
    ``compaction_end`` on EVERY exit (success, "Nothing to compact", cancelled
    hook, summarizer failure), so the transient indicator always clears.

    ``turn_start`` is a SELF-HEAL trigger, handled identically to
    ``compaction_end``: a compaction cancelled via BaseException (Ctrl+C /
    CancelledError) never emits ``compaction_end`` (core re-raises before the
    emit), which would strand the indicator; the next turn means any compaction
    is long done, so the stale row is restored. It is a no-op when no indicator
    is active (the ``state["active"]`` guard), so it is safe to call on EVERY
    ``turn_start``.
    """

    if etype == "compaction_start":
        if not state.get("active"):
            state["prev_msg"] = chrome.get_working_message()
            state["prev_visible"] = chrome.get_working_visible()
            state["active"] = True
        chrome.set_working_message("Compacting context…")
        chrome.set_working_visible(True)
    elif etype in ("compaction_end", "turn_start"):
        if state.get("active"):
            chrome.set_working_message(state.get("prev_msg"))
            chrome.set_working_visible(bool(state.get("prev_visible")))
            state["active"] = False


async def run_tui(
    runtime_host: AgentSessionRuntime,
    *,
    cwd: str,
    model_registry: ModelRegistry | None = None,
    mcp_manager: McpClientManager | None = None,
    permission_ext: PermissionExtension | None = None,
    permission_posture: PermissionPosture | None = None,
    settings_manager: SettingsManager | None = None,
    auth_storage: AuthStorage | None = None,
    extensions: list[Extension] | None = None,
    chrome: AelixChrome | None = None,
    install_signal_handlers: bool = True,
) -> int:
    """Run the interactive TUI (persistent chrome) until ``/quit`` or EOF.

    :param model_registry: the live :class:`ModelRegistry` (entry.py builds it
        once and threads it here) so ``/model`` can list ``get_available()``.
        ``None`` (e.g. tests) makes ``/model`` fall back to its status print.
        NOTE: it is threaded explicitly because the harness does NOT expose the
        registry — ``AgentHarness`` never sets ``_model_registry`` (W-review 6h₂₆
        CRITICAL: reading it off the harness made the picker always unavailable).
    :param mcp_manager: the live :class:`McpClientManager` (entry.py builds it
        once and threads it here) so ``/mcp`` can report server status. ``None``
        (no servers / tests) makes ``/mcp`` degrade with a committed message.
        Threaded explicitly for the same reason as ``model_registry``: the
        harness does NOT expose the manager (Sprint 6h₂₇, ADR-0155).
    :param permission_ext: the held :class:`PermissionExtension` (entry.py builds
        it once and threads it here) so the TUI can wire the purpose-built
        approval dialog onto it (``approval_runner``). ``None`` (tests) leaves the
        extension on its generic ``ctx.ui.select`` prompt fallback.
    :param permission_posture: the held :class:`PermissionPosture` (entry.py
        builds it once) so shift+tab cycling + the footer badge read/write the
        SAME object the gate consults. ``None`` (tests) disables the badge +
        cycle (no posture to mutate).
    :param settings_manager: the held :class:`SettingsManager` (entry.py
        constructs it once via ``SettingsManager.create``) so ``/settings`` +
        ``/scoped-models`` read/persist the pi-parity settings. ``None`` (tests)
        leaves those commands on their degraded fallback. WP-2 (ADR-0160).
    :param auth_storage: the held :class:`~aelix_ai.oauth.AuthStorage` (entry.py
        builds it once, line ~680, and the ``ModelRegistry`` is created over the
        SAME object) so WP-8 ``/login`` storing a key is visible to model
        resolution immediately. ``None`` (tests) leaves ``/login`` / ``/logout``
        on their degraded fallback. WP-8 (Feature 1).
    :param extensions: the list of :class:`~aelix_coding_agent.extensions.api.
        Extension` objects discovered on the first harness build (entry.py
        threads it; empty when nothing loaded) so WP-8 ``/extension`` can list
        them. ``None`` → an empty list. WP-8 (Feature 3).
    :param chrome: injectable for tests (headless pipe input + DummyOutput).
    :param install_signal_handlers: pass ``False`` when embedding (tests / a host
        that owns process signals) — mirrors ``run_rpc_mode``.
    """

    # WP-8 — normalize the optional extensions list to a stable value so the
    # stage-B /extension flow (wired later) reads it without re-guarding None.
    # ``auth_storage`` is a parameter and threads straight into the stage-B
    # /login + /logout flows.
    extensions = list(extensions) if extensions else []

    def _live_extension_shortcuts() -> dict[str, Any]:
        # Issue #20 — LIVE read through the runtime host (never the stale
        # run_tui ``extensions`` snapshot) so a #24 reload's handler swaps
        # reach the chrome's fire-time lookup — the same live-read idiom as
        # CommandDispatchService (``lambda: runtime_host.harness``).
        runner = getattr(runtime_host.harness, "extension_runner", None)
        get = getattr(runner, "get_shortcuts", None) if runner else None
        try:
            return get() if callable(get) else {}
        except Exception:  # noqa: BLE001 — a faulty extension must not break keys
            return {}

    if chrome is not None:
        out_chrome = chrome
    else:
        # Persist input history across sessions (↑/↓ + Ctrl+R) — the chrome
        # already supports it; run_tui just never passed a path before.
        from pathlib import Path as _Path

        from aelix_coding_agent.cli.config import get_agent_dir

        out_chrome = AelixChrome(
            history_path=str(_Path(get_agent_dir()) / "tui_input_history"),
            # Issue #20 — extension keyboard shortcuts join the key bindings
            # (built at chrome construction; built-ins win collisions).
            extension_shortcuts=_live_extension_shortcuts,
        )
    footer = AelixFooterData(cwd=cwd)

    def _model_id() -> str | None:
        model = getattr(runtime_host.harness, "current_model", None)
        return getattr(model, "id", None) if model is not None else None

    def _steering_mode() -> str | None:
        # Live steering mode from the harness ("one-at-a-time"/"all") so the
        # footer ⏵⏵ segment reflects reality, not a hardcoded placeholder. Falls
        # back to the "one-at-a-time" sentinel (NOT None) when the harness lacks
        # the attribute, so the footer hides the ⏵⏵ segment instead of surfacing
        # a stray "⏵⏵ default" (ADR-0159, review MEDIUM).
        from aelix_coding_agent.tui.context import _DEFAULT_STEERING_MODE

        return getattr(
            runtime_host.harness, "steering_mode", _DEFAULT_STEERING_MODE
        )

    def _permission_badge() -> str | None:
        # Live permission posture badge (WP-0, ADR-0157): the held posture's
        # distinct glyph (✎/⏸/⚠/🤖), SGR-coloured by the mode's badge_style
        # (#66 item 6a), or None on DEFAULT so the footer substitutes the neutral
        # ● default. Reads the SAME object shift+tab mutates.
        return _mode_badge_ansi(permission_posture)

    # #66 item 6b — the ❯ prompt colour follows the same posture (single source:
    # MODE_META badge_style; DEFAULT → neutral bold cyan). Wired onto the chrome
    # so ``_ModePrompt`` re-reads it live on every render + shift+tab repaint.
    out_chrome.prompt_style_provider = lambda: _mode_prompt_style(permission_posture)

    # WP-2 (ADR-0160) — the coding-agent-owned statusline store gates which footer
    # segments render. Its defaults are the registry default-enabled ids, so a
    # fresh install (no statusline.json) renders the byte-identical pre-ADR-0160
    # footer. Built here so the context + the /statusline picker share one store.
    from aelix_coding_agent.tui.footer_segments import default_enabled_ids_from_spec
    from aelix_coding_agent.tui.statusline_store import StatuslineStore

    statusline_store = StatuslineStore(
        default_enabled=default_enabled_ids_from_spec(),
    )

    context = AelixTUIContext(
        out_chrome,
        footer,
        model_provider=_model_id,
        mode_provider=_steering_mode,
        pending_provider=lambda: getattr(
            runtime_host.harness, "pending_message_count", 0
        ),
        permission_badge_provider=_permission_badge,
        cwd=cwd,
        mode="default",
        statusline_store=statusline_store,
    )

    # Issue #21 tui_widgets (ADR-0182) — paint manifest-declared widgets through
    # the same ctx.ui path imperative extensions use. Re-applied on every
    # ``_rebind`` (startup, /resume·/fork swaps, #24 /reload — each rebuilds the
    # extension set); ``applied_widgets`` tracks painted keys → placement so a
    # removed plugin's widgets un-paint. Reads the PASSED harness (not
    # ``runtime_host.harness``) so a rebind mid-swap can't read the stale one;
    # never raises — a faulty widget contrib must not break the TUI.
    applied_widgets: dict[str, str] = {}

    def _apply_ext_widgets(harness: AgentHarness) -> None:
        runner = getattr(harness, "extension_runner", None)
        runtime = getattr(harness, "runtime", None)
        pending = tuple(getattr(runtime, "pending_activations", None) or ())
        with contextlib.suppress(Exception):
            apply_manifest_widgets(
                runner, context, applied_widgets, pending=pending
            )

    def _apply_ext_themes(harness: AgentHarness) -> None:
        # Issue #21 themes (ADR-0184) — reconcile manifest-contributed themes
        # into the registry so the /settings picker sees them. Registers only
        # (never auto-selects — the user's persisted theme is untouched);
        # wholesale-replaces the registered set so a removed plugin's themes
        # vanish. Never raises (a faulty theme must not break the TUI).
        from aelix_coding_agent.tui.ext_themes import apply_manifest_themes

        runner = getattr(harness, "extension_runner", None)
        runtime = getattr(harness, "runtime", None)
        pending = tuple(getattr(runtime, "pending_activations", None) or ())
        with contextlib.suppress(Exception):
            apply_manifest_themes(runner, pending=pending)

    # WP-2 (ADR-0160) — seed the live theme from the persisted setting so the
    # /settings → Theme choice actually applies on the NEXT launch (not only the
    # session that set it). Without this the context starts on DEFAULT_THEME and
    # the persisted ``theme`` field is write-only across launches. Guarded: an
    # unknown/stale theme name (theme since removed) leaves the default in place.
    if settings_manager is not None:
        with contextlib.suppress(Exception):
            persisted_theme = settings_manager.get_theme()
            if persisted_theme:
                context.set_theme(persisted_theme)  # no-op SetThemeResult on miss

    # Output pump seam: the synchronous renderer queues TAGGED commands; the
    # pump applies them above the chrome in order. Routing the live-tail update
    # through the same queue as committed lines keeps them ordered — otherwise a
    # synchronous tail-clear would race the async (in_terminal) commit flush and
    # the just-finalized text could vanish before it reaches scrollback.
    output_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def _commit(renderable: object) -> None:
        output_queue.put_nowait(("commit", renderable))

    def _set_tail(ansi: str) -> None:
        output_queue.put_nowait(("tail", ansi))

    renderer = EventRenderer(commit=_commit, set_tail=_set_tail, width=_RENDER_WIDTH)

    def _render_custom_message(msg: Any) -> object | None:
        # Issue #62 (ADR-0183) — pi CustomMessageComponent parity: look up the
        # first-wins extension renderer by custom_type LIVE through the
        # runtime host (reload-safe, the get_shortcuts idiom), call it
        # ``(message, MessageRenderOptions, theme)``, and snapshot the
        # returned Component to lines. None / a raise → None → the
        # EventRenderer's default rendering (pi swallows renderer errors the
        # same way, custom-message.ts:68-70).
        try:
            runner = getattr(runtime_host.harness, "extension_runner", None)
            get = getattr(runner, "get_message_renderer", None) if runner else None
            renderer_fn = (
                get(getattr(msg, "custom_type", "") or "") if callable(get) else None
            )
            if renderer_fn is None:
                return None
            options = MessageRenderOptions(expanded=context.get_tools_expanded())
            component = renderer_fn(msg, options, context.theme)
            if component is None:
                return None
            return component_to_text(component, _RENDER_WIDTH)
        except Exception:  # noqa: BLE001 — a faulty extension renderer must not
            # break replay; fall through to the default rendering. This is
            # where the REAL extension exception is caught (the EventRenderer
            # hook then just sees None), so the ADR-0181 log-on-skip diagnostic
            # must fire HERE at DEBUG, not in render.py's dead except (issue #62
            # review: the render-layer log never runs for the wired path).
            _log.debug(
                "custom-message renderer failed for %r; using default rendering",
                getattr(msg, "custom_type", None),
                exc_info=True,
            )
            return None

    renderer.render_custom_message = _render_custom_message

    async def _display_messages(session: Any) -> list[Any]:
        # Issue #62 (ADR-0183) — replay reads the DISPLAY tier (rich custom
        # messages, custom_type intact) when the session exposes get_branch.
        # The real ``Session`` ALWAYS defines get_branch (session.py), so every
        # production resume/fork/reload takes this branch; the degrade below
        # only fires for the test-fake sessions that stub build_context alone.
        # Degrade caveat (issue #62 review): build_context has already
        # flattened custom_message entries to UserMessages, so the degrade path
        # loses BOTH the custom-renderer dispatch AND the display gate — a
        # display=False custom would re-echo as a user line. This cannot happen
        # for a real session (always the display tier) and the fakes carry no
        # custom messages, so it is a latent-only gap, documented not guarded.
        get_branch = getattr(session, "get_branch", None)
        if callable(get_branch):
            return list(build_display_messages(await get_branch()))
        return list((await session.build_context()).messages)

    # Issue #50 — seed the live thinking settings from the persisted store at
    # startup (mirror of the WP-2/ADR-0160 theme + default-model seeds above).
    # Without this the /settings → "Show thinking" + "Default thinking level"
    # choices were write-only across launches: the renderer started on its
    # hardcoded default and the harness on ``off``, so the user had to re-toggle
    # them every session. Both seeds are guarded so a malformed settings file
    # never blocks launch.
    if settings_manager is not None:
        # (a) thinking-block VISIBILITY → the renderer flag. ``get_hide_thinking_
        # block`` defaults to False (visible, pi parity) which now also matches
        # render.py's hardcoded default, so headless / no-settings stays sane.
        with contextlib.suppress(Exception):
            renderer.hide_thinking = settings_manager.get_hide_thinking_block()
        # (a1) compaction-summary DISPLAY gate → the renderer flag (aelix-original,
        # mirrors hide_thinking). Gates the /compact summary panel + the replayed
        # compaction-summary message; the summary stays in the LLM context.
        with contextlib.suppress(Exception):
            renderer.hide_compaction_summary = (
                settings_manager.get_hide_compaction_summary()
            )
        # (a2) tool-card NORMAL-output line cap → the renderer (Issue #66). Without
        # this seed the persisted ``tool_card_max_lines`` never reaches the
        # renderer, leaving the setting inert. Guarded like the sibling above.
        with contextlib.suppress(Exception):
            renderer.tool_card_max_lines = (
                settings_manager.get_tool_card_max_lines()
            )
        # (b) default THINKING LEVEL → the live harness. SKIP when the current
        # model does not support the stored level (a reasoning-off model collapses
        # to ``["off"]``) so an unsupported level is never forced. ``None`` (unset)
        # preserves the default ``off`` — nothing to apply. Runs pre-bootstrap;
        # ``bootstrap`` only discovers resources, it never resets thinking_level.
        with contextlib.suppress(Exception):
            seed_level = settings_manager.get_default_thinking_level()
            if seed_level:
                from aelix_ai.models import get_supported_thinking_levels

                seed_model = getattr(runtime_host.harness, "current_model", None)
                if seed_model is not None and seed_level in get_supported_thinking_levels(
                    seed_model
                ):
                    await runtime_host.harness.set_thinking_level(seed_level)

    # WP-8 (Feature 2) — the TUI-side session activity tracker. The harness
    # SessionStats carries no per-tool success/failure split, no per-model
    # breakdown, and no wall-clock timing, so the tracker observes the agent event
    # stream to fill that gap for the /stats dashboard. ``model_provider=_model_id``
    # supplies the live model id for message_end events that omit it. Fed at the
    # TOP of _on_agent_event (before the renderer) and reset on _rebind.
    tracker = SessionActivityTracker(model_provider=_model_id)

    # WP-8 D3 (ADR-0168) — cross-session /stats history. The tracker above is
    # live-only (reset on swap, lost on exit); this store persists a cumulative
    # snapshot row per turn under get_agent_dir()/stats-history.jsonl so the
    # /stats History tab renders per-project / token-trend / hour-heatmap views
    # across sessions. Pruned ONCE here so the file can't grow without bound.
    from aelix_coding_agent.tui.stats_history import StatsHistoryStore

    history_store = StatsHistoryStore()
    history_store.prune(_HISTORY_MAX_RECORDS)

    # Sprint 6h₁₂a (ADR-0110) — first-party command core. The registry is static
    # for the session; the context carries the live chrome/harness/commit/cwd so
    # handlers (e.g. /help) can act on the running TUI.
    commands = list(BUILTIN_COMMANDS)

    def _set_mode(mode: str) -> None:
        # Reflect a /mode switch in the live footer ⏵⏵ segment (Sprint 6h₁₂d).
        context._mode = mode
        context._refresh_footer()

    async def _resume_session() -> None:
        # Sprint 6h₁₄b (ADR-0122) — /resume: list cwd sessions → picker →
        # in-process switch_session hot-swap → transcript replay. The runtime's
        # rebind callback (set_rebind_session → _rebind) re-subscribes the
        # EventRenderer to the new harness automatically, so we only drive the
        # picker + the repaint here.
        from aelix_agent_core.session.jsonl_repo import JsonlSessionListOptions

        # Guard: never swap a live harness mid-generation. /resume is dispatched
        # from the serialized _input_loop (blocked at ``await harness.prompt()``
        # during a turn, so it can't reach here mid-turn) and a "/resume" typed
        # while running is routed to steer() as a message, not a command — so this
        # is belt-and-braces, but explicit (W-review M3).
        if out_chrome.running:
            _commit(Text("Can't resume while a turn is running.", style="yellow"))
            return
        # ``_repo`` is a private attr of the runtime host (no public accessor on
        # AgentSessionRuntime, which is protected core); same-codebase coupling,
        # degrades to a message if it's ever renamed/absent (W-review L1).
        repo = getattr(runtime_host, "_repo", None)
        if repo is None:
            _commit(Text("Resume is unavailable (no session repo).", style="yellow"))
            return
        try:
            sessions = await repo.list(JsonlSessionListOptions(cwd=cwd))
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ could not list sessions: {exc}", style="bold red"))
            return
        current_file = getattr(runtime_host.session, "session_file", None)
        choices = [m for m in sessions if getattr(m, "path", None) != current_file]
        if not choices:
            _commit(Text("No other sessions to resume in this folder.", style="yellow"))
            return
        # select() shows the first 9 (newest-first); build a label→metadata map.
        labels: list[str] = []
        by_label: dict[str, object] = {}
        for meta in choices:
            label = _format_session_choice(meta)
            while label in by_label:  # guarantee uniqueness for the reverse map
                label += " ·"
            labels.append(label)
            by_label[label] = meta
        chosen = await context.select("Resume session", labels)
        if not chosen:
            return  # Esc / cancelled
        meta = by_label.get(chosen)
        if meta is None:
            return
        try:
            result = await runtime_host.switch_session(getattr(meta, "path", ""))
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ resume failed: {exc}", style="bold red"))
            return
        if getattr(result, "cancelled", False):
            _commit(Text("Resume cancelled by an extension.", style="yellow"))
            return
        # Repaint (pi renderCurrentSessionState parity): clear scrollback, then
        # replay the loaded session's transcript above the chrome. Build from the
        # PERSISTED branch via ``Session.build_context()`` (the in-memory
        # harness._state.messages is empty right after a switch — rebuilt lazily
        # on the next turn); this is the same path /compact reuses.
        out_chrome.clear()
        session = runtime_host.session
        messages = await _display_messages(session) if session is not None else []
        renderer.replay(messages)
        _commit(Text(f"↻ Resumed session ({len(messages)} messages)", style="green"))
        context._refresh_footer()

    async def _new_session() -> None:
        # Sprint 6h₁₅ (ADR-0123) — /new: start a fresh session in-process. Mirror
        # of _resume_session's swap (the rebind seam re-subscribes the renderer +
        # refreshes command_ctx.harness), but with no picker and no replay (the
        # new session is empty) — just clear + a fresh banner.
        if out_chrome.running:
            _commit(Text("Can't start a new session while a turn is running.", style="yellow"))
            return
        try:
            result = await runtime_host.new_session()
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ new session failed: {exc}", style="bold red"))
            return
        if getattr(result, "cancelled", False):
            _commit(Text("New session cancelled by an extension.", style="yellow"))
            return
        out_chrome.clear()
        _commit(_build_banner(runtime_host.harness, cwd))
        context._refresh_footer()

    async def _replay_after_swap(banner: str) -> None:
        """Common post-swap repaint (Sprint 6h₂₁): clear + replay persisted
        transcript + status + footer. The runtime's rebind seam already
        re-subscribed the renderer + refreshed ``command_ctx.harness``."""

        out_chrome.clear()
        session = runtime_host.session
        messages = await _display_messages(session) if session is not None else []
        renderer.replay(messages)
        _commit(Text(banner, style="green"))
        context._refresh_footer()

    async def _import_session(path: str) -> None:
        # Sprint 6h₂₁ (ADR-0129) — /import: copy a JSONL file into the local
        # sessions root and swap to it. Pi parity:
        # ``AgentSessionRuntime.import_from_jsonl`` (``agent-session-runtime.ts:329-364``)
        # which Aelix already exposes; this is the TUI consumer.
        if out_chrome.running:
            _commit(Text("Can't import while a turn is running.", style="yellow"))
            return
        # W-review MEDIUM-1: ``import_from_jsonl`` requires a cwd (either the
        # explicit override or the current session's). With no session bound,
        # the runtime raises ``RuntimeError("import_from_jsonl requires a cwd …")``
        # — gate here so the user sees the same friendly yellow degrade as the
        # other three closures instead of a raw runtime-internals string.
        if runtime_host.session is None:
            _commit(Text("Import is unavailable (no session).", style="yellow"))
            return
        try:
            result = await runtime_host.import_from_jsonl(path)
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ import failed: {exc}", style="bold red"))
            return
        if getattr(result, "cancelled", False):
            _commit(Text("Import cancelled by an extension.", style="yellow"))
            return
        await _replay_after_swap(f"↻ Imported session from {path}")

    async def _fork_session() -> None:
        # Sprint 6h₂₁ (ADR-0129) — /fork: cut the session before the most
        # recent user message. Pi parity:
        # ``AgentSessionRuntime.fork(entry_id, position="before")`` (``:262-280``)
        # — Aelix already exposes the user-message walk + ``position="before"``
        # branch + the ``Invalid entry ID for forking`` raise.
        if out_chrome.running:
            _commit(Text("Can't fork while a turn is running.", style="yellow"))
            return
        session = runtime_host.session
        if session is None:
            _commit(Text("Fork is unavailable (no session).", style="yellow"))
            return
        try:
            entries = await session.get_entries()
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ fork failed: {exc}", style="bold red"))
            return
        # Walk newest-first; find the most recent ``message`` entry with a
        # user-role message — the only entry shape ``runtime.fork`` accepts on
        # ``position="before"`` (Pi parity ``:268-273``).
        target_id: str | None = None
        for entry in reversed(entries):
            if getattr(entry, "type", None) != "message":
                continue
            message = getattr(entry, "message", None)
            if message is None or getattr(message, "role", None) != "user":
                continue
            target_id = getattr(entry, "id", None)
            if target_id:
                break
        if target_id is None:
            _commit(Text("No user message to fork before.", style="yellow"))
            return
        try:
            result = await runtime_host.fork(target_id, position="before")
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ fork failed: {exc}", style="bold red"))
            return
        if getattr(result, "cancelled", False):
            _commit(Text("Fork cancelled by an extension.", style="yellow"))
            return
        await _replay_after_swap("⎇ Forked session (cut before last user message)")

    async def _clone_session() -> None:
        # Sprint 6h₂₁ (ADR-0129) — /clone: copy the entire session into a new
        # file (no truncation). Pi parity: ``runtime.fork(leaf_id, position="at")``
        # — ``position="at"`` keeps the leaf itself and every ancestor entry in
        # the new session (``agent-session-runtime.ts:255-261, 282-296``).
        if out_chrome.running:
            _commit(Text("Can't clone while a turn is running.", style="yellow"))
            return
        session = runtime_host.session
        if session is None:
            _commit(Text("Clone is unavailable (no session).", style="yellow"))
            return
        # ``Session.get_leaf_id`` is the public accessor for the session tip
        # (Pi parity ``Session.get_leaf_id`` ``session.py:128``); degrades
        # silently when the session has no entries yet.
        try:
            leaf_id = await session.get_leaf_id()
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ clone failed: {exc}", style="bold red"))
            return
        if not leaf_id:
            _commit(Text("Nothing to clone (session is empty).", style="yellow"))
            return
        try:
            result = await runtime_host.fork(leaf_id, position="at")
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ clone failed: {exc}", style="bold red"))
            return
        if getattr(result, "cancelled", False):
            _commit(Text("Clone cancelled by an extension.", style="yellow"))
            return
        await _replay_after_swap("⎇ Cloned session (full transcript)")

    async def _tree_action() -> None:
        # Sprint 6h₂₁ (ADR-0129) — /tree: render the session lineage by walking
        # ``parent_session_path`` recursively. Each ancestor is loaded via the
        # repo seam so per-cwd lineage with cross-cwd ``parent_session_path``
        # round-trips. Defensive: a broken ancestor breaks the chain at that
        # row (no recurse-into-missing-file), never the REPL.
        session = runtime_host.session
        if session is None:
            _commit(Text("Tree is unavailable (no session).", style="yellow"))
            return
        fs = getattr(runtime_host, "_fs", None)
        if fs is None:
            _commit(Text("Tree is unavailable (no fs).", style="yellow"))
            return
        try:
            meta = await session.get_metadata()
        except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
            _commit(Text(f"✖ tree failed: {exc}", style="bold red"))
            return
        # Build the ancestor chain (current → root). Cap the walk so a circular
        # ``parent_session_path`` (corrupted file) can't loop forever.
        chain: list[object] = [meta]
        seen_paths: set[str] = {str(getattr(meta, "path", ""))}
        cursor: object = meta
        for _ in range(64):
            parent_path = getattr(cursor, "parent_session_path", None)
            if not parent_path or parent_path in seen_paths:
                break
            seen_paths.add(parent_path)
            try:
                parent_meta = await load_jsonl_session_metadata(fs, parent_path)
            except Exception:
                # Broken / missing ancestor: stop the walk, record nothing more.
                break
            chain.append(parent_meta)
            cursor = parent_meta
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="white")
        for depth, ancestor in enumerate(chain):
            marker = "●" if depth == 0 else "↳"
            short_id = (getattr(ancestor, "id", "") or "")[:8]
            created = (getattr(ancestor, "created_at", "") or "").replace("T", " ")[:16]
            path = str(getattr(ancestor, "path", ""))
            label = " · ".join(p for p in (created, short_id) if p) or short_id or "session"
            table.add_row(f"{marker} {label}", path)
        _commit(Panel(table, title="Session lineage", box=ROUNDED, border_style="cyan"))

    async def _settings_theme_action() -> None:
        # /settings → Theme: sub-select over the registered theme names → apply
        # live (context.set_theme repaints the footer + chrome) AND persist
        # (sm.set_theme). The select round-trips losslessly (names are unique).
        if settings_manager is None:
            return
        from aelix_coding_agent.tui import themes as theme_registry

        # Issue #21 themes (ADR-0184) — include manifest-contributed themes,
        # not only the built-in THEMES dict, so a plugin theme is selectable.
        names = theme_registry.all_theme_names()
        if not names:
            return
        current = settings_manager.get_theme() or "default"
        labels = [f"✱ {n}" if n == current else f"  {n}" for n in names]
        chosen = await context.select("Theme", labels)
        if not chosen:
            return
        try:
            idx = labels.index(chosen)
        except ValueError:
            return
        name = names[idx]
        result = context.set_theme(name)  # live repaint
        if not getattr(result, "success", True):
            _commit(Text(f"✖ {getattr(result, 'error', 'theme')}", style="bold red"))
            return
        settings_manager.set_theme(name)  # persist (global scope)
        await settings_manager.flush()
        _commit(Text(f"theme → {name}", style="green"))
        context._refresh_footer()

    async def _settings_default_model_action() -> None:
        # /settings → Default model: reuse the rich /model picker to switch the
        # live session, then persist the new (provider, model) as the default.
        if settings_manager is None:
            return
        # Capture the (provider, id) BEFORE the picker so we only persist when the
        # user actually CHOSE a different model. Opening the picker and Esc'ing
        # must NOT silently pin the current model as the persisted default
        # (W-review LOW): the picker no-ops on Esc, leaving current_model
        # unchanged, so an unconditional write would mislead the user.
        before = getattr(runtime_host.harness, "current_model", None)
        before_key = (getattr(before, "provider", None), getattr(before, "id", None))
        await _open_model_picker()  # live: switches harness.current_model
        model = getattr(runtime_host.harness, "current_model", None)
        model_id = getattr(model, "id", None)
        provider = getattr(model, "provider", None)
        if not (model_id and provider):
            return
        if (provider, model_id) == before_key:
            return  # picker cancelled / same model — nothing to persist
        # ``run_model_picker`` (reached via ``_open_model_picker`` above, which
        # threads ``settings_manager``) ALREADY persisted (provider, model_id) +
        # flushed as the default — so do NOT write/flush a second time here. Just
        # confirm it in the /settings context (the picker's own "model → X" line
        # doesn't say it was pinned as the default).
        _commit(Text(f"default model → {model_id} (persisted)", style="green"))

    async def _settings_thinking_action() -> None:
        # /settings → Thinking level: cycle the live model's supported levels
        # (model-aware, session) AND persist the new level as the default.
        if settings_manager is None:
            return
        new_level = await runtime_host.harness.cycle_thinking_level()  # live
        if new_level is None:
            _commit(Text("This model has no thinking levels to cycle.", style="yellow"))
            return
        settings_manager.set_default_thinking_level(new_level)  # persist default
        await settings_manager.flush()
        _commit(
            Text(
                f"thinking level → {new_level} (persisted as default)",
                style="green",
            )
        )

    async def _apply_live_setting(key: str, value: object) -> None:
        # Mirror a persisted dual-write row onto the LIVE session. The persist
        # half already ran in apply_setting; this is the in-session half so the
        # change takes effect this run (not only next launch). Steering/follow-up
        # write the harness (no persist of their own); hide-thinking writes the
        # renderer flag (live, not persisted by the renderer).
        harness = runtime_host.harness
        with contextlib.suppress(Exception):
            if key == "steering_mode":
                harness.set_steering_mode(str(value))
                context._refresh_footer()  # footer ⏵⏵ reads the live value
            elif key == "follow_up_mode":
                harness.set_follow_up_mode(str(value))
            elif key == "hide_thinking_block":
                renderer.hide_thinking = bool(value)
            elif key == "hide_compaction_summary":
                renderer.hide_compaction_summary = bool(value)
            elif key == "tool_card_max_lines":
                # render.py reads ``renderer.tool_card_max_lines`` fresh on every
                # card, so mirroring the persisted value here makes the new cap
                # take effect on the NEXT tool card this session (not only next
                # launch). ``value`` is the clamped string apply_setting re-read.
                renderer.tool_card_max_lines = int(str(value))

    async def _open_settings() -> None:
        # ImplConsumers (ADR-0161) — /settings: an expanded select over the
        # SettingsManager-backed rows (build_settings_rows). Picking a row
        # toggles (bool) / cycles (enum) / inputs (int) / delegates (action) the
        # setting via the held SettingsManager (persist) + the live session
        # (dual-write) where supported. Loops until Esc so several settings can be
        # changed in one open (pi parity). Sprint 6h₁₇ (ADR-0125) shipped a 4-row
        # subset; this grows it to ~16 via the SettingsManager seam.
        from aelix_coding_agent.tui.settings_rows import (
            apply_setting,
            build_settings_rows,
        )

        if settings_manager is None:
            # Degraded fallback: no SettingsManager threaded (older entrypoint /
            # tests). Surface honestly rather than presenting an inert menu.
            # run_tui always threads it, so this is belt-and-braces.
            _commit(
                Text(
                    "Settings are unavailable (no settings manager wired).",
                    style="yellow",
                )
            )
            return

        # Delegated live flows keyed by the action row key.
        actions: dict[str, Callable[[], object]] = {
            "theme": _settings_theme_action,
            "default_model": _settings_default_model_action,
            "thinking_level": _settings_thinking_action,
        }

        # Sprint 6h₃₀ (ADR-0163) — remember the highlighted row across re-opens so
        # returning from a sub-flow (e.g. the /model picker launched by the
        # "Default model" row) keeps the cursor on that row instead of snapping
        # back to the top. ``select(initial_index=...)`` restores it each pass.
        cursor_idx = 0
        while True:
            rows = build_settings_rows(settings_manager)
            # Pi screenshot parity: pad the label column so values line up.
            width = max(len(r.label) for r in rows) + 2
            labels = [
                f"{r.label.ljust(width)}{r.read(settings_manager)}" for r in rows
            ]
            cursor_idx = max(0, min(cursor_idx, len(labels) - 1))
            # Bind ``rows`` via a default arg so the per-highlight detail closure
            # references THIS iteration's rows (ruff B023 — the loop rebuilds rows
            # each pass; the lambda is consumed synchronously within the same pass
            # but the explicit bind documents + guarantees it).
            choice = await context.select(
                "Settings — select to change (Esc to close)",
                labels,
                detail=lambda i, _rows=rows: [_rows[i].help] if _rows[i].help else [],
                initial_index=cursor_idx,
            )
            if not choice:
                return  # Esc closes the menu
            # Lossless exact-label index recovery (the label list IS the option
            # set we passed in) — no startswith prefix scan (W-review 6h₂₄ M-1).
            try:
                row_idx = labels.index(choice)
            except ValueError:
                _commit(Text(f"✖ settings: unknown row {choice!r}", style="bold red"))
                continue
            cursor_idx = row_idx  # keep the cursor here on the next re-open
            row = rows[row_idx]

            # int rows: collect the new value via an input dialog first.
            int_value: int | None = None
            if row.kind == "int":
                lo, hi = row.int_range or (0, 0)
                raw = await context.input(f"{row.label} ({lo}-{hi})")
                if raw is None or not raw.strip():
                    continue  # cancelled / empty — no change
                try:
                    int_value = int(raw.strip())
                except ValueError:
                    _commit(
                        Text(
                            f"✖ {row.label}: not a number ({raw!r})",
                            style="bold red",
                        )
                    )
                    continue

            result = apply_setting(row, settings_manager, int_value=int_value)
            if result.kind == "delegate":
                action = actions.get(row.key)
                if action is not None:
                    await action()  # type: ignore[misc]
                continue
            if result.kind == "error":
                _commit(Text(f"✖ {result.message}", style="bold red"))
                continue
            # ok: persisted — flush + mirror live (dual-write) + commit.
            await settings_manager.flush()
            if result.live is not None:
                live_key, live_value = result.live
                await _apply_live_setting(live_key, live_value)
            _commit(Text(result.message, style="green"))

    async def _open_scoped_models() -> None:
        # ImplConsumers (ADR-0161) — /scoped-models: a multi-checkbox picker over
        # the auth-filtered catalog (ModelRegistry.get_available) that reads/writes
        # the enabled_models allow-list via the held SettingsManager (global scope,
        # pi parity). The flow lives in scoped_models.run_scoped_models (DI so it
        # is unit-testable without the prompt-toolkit app); this wires the live
        # registry + settings manager + multiselect + commit into it.
        from aelix_coding_agent.tui.scoped_models import run_scoped_models

        await run_scoped_models(
            registry=model_registry,
            settings_manager=settings_manager,
            multiselect=context.multiselect,
            commit=_commit,
        )

    async def _open_model_picker() -> None:
        # Sprint 6h₂₆ (ADR-0154, WP-7) — /model: a searchable picker over the
        # auth-filtered catalog (ModelRegistry.get_available) with a per-highlight
        # detail footer (modality / context-window / base-url / api-key), then
        # harness.set_model. The flow lives in model_picker.run_model_picker
        # (dependency-injected so it is unit-testable without the prompt-toolkit
        # app); this wires the live registry/select/commit/footer into it. The
        # registry is threaded from entry.py into run_tui — the harness does NOT
        # expose it (W-review 6h₂₆ CRITICAL).
        await run_model_picker(
            registry=model_registry,
            harness=runtime_host.harness,
            select=context.select,
            commit=_commit,
            refresh_footer=context._refresh_footer,
            settings_manager=settings_manager,
        )

    async def _open_thinking_picker() -> None:
        # Sprint 6h₂₇ (ADR-0155, WP-7) — /thinking: a picker over the current
        # model's supported reasoning levels (get_supported_thinking_levels) →
        # harness.set_thinking_level. The flow lives in
        # thinking_picker.run_thinking_picker (dependency-injected so it is
        # unit-testable without the prompt-toolkit app); this wires the live
        # harness/select/commit into it. ``runtime_host.harness`` is read live
        # (post-hot-swap) rather than a captured local — same reason as the model
        # picker. No footer refresh: there is no thinking footer segment today.
        await run_thinking_picker(
            harness=runtime_host.harness,
            select=context.select,
            commit=_commit,
        )

    async def _open_mcp_status() -> None:
        # Sprint 6h₂₇ (ADR-0155, WP-7) — /mcp: a read-only status panel over the
        # live McpClientManager (servers, transport, state, tool counts). The
        # flow lives in mcp_viewer.run_mcp_viewer (dependency-injected so it is
        # unit-testable without the prompt-toolkit app); the manager is threaded
        # from entry.py into run_tui — the harness does NOT expose it (same seam
        # as model_registry).
        await run_mcp_viewer(manager=mcp_manager, commit=_commit)

    async def _open_statusline() -> None:
        # WP-2 (ADR-0160) — /statusline: a multi-checkbox picker over the footer
        # segment registry → persist the enabled-id set to the coding-agent-owned
        # statusline store → repaint the footer. The flow lives in
        # statusline_picker.run_statusline_picker (dependency-injected so it is
        # unit-testable without the prompt-toolkit app); this wires the live store
        # + multiselect + commit + footer refresh into it. The segment registry is
        # the SAME one the context composes the footer from (context._segments).
        from aelix_coding_agent.tui.statusline_picker import run_statusline_picker

        await run_statusline_picker(
            segments=context._segments,
            load=statusline_store.load,
            save=statusline_store.save,
            multiselect=context.multiselect,
            commit=_commit,
            refresh_footer=context._refresh_footer,
        )

    async def _open_login() -> None:
        # WP-8 (Feature 1) — /login: the auth wizard (OAuth / built-in API key /
        # custom provider → AuthStorage). The flow lives in
        # login_wizard.run_login (dependency-injected so it is unit-testable
        # without the prompt-toolkit app); this wires the live auth storage +
        # context dialog callables + commit into it. ``auth_storage`` is the SAME
        # object the ModelRegistry was built over (entry.py) so a stored key is
        # visible to model resolution immediately (no reload).
        from aelix_coding_agent.tui.login_wizard import run_login

        await run_login(
            auth_storage=auth_storage,
            select=context.select,
            prompt_input=context.input,
            confirm=context.confirm,
            notify=context.notify,
            commit=_commit,
            # WP-8 follow-up — wire the checkbox picker + the live registry so a
            # custom OpenAI-compatible endpoint can fetch its /models, let the
            # user pick, and persist them to models.json (→ appear in /model).
            multiselect=context.multiselect,
            model_registry=model_registry,
            # So the "they now appear in /model" confirmation is scope-aware: a
            # concrete /scoped-models allow-list can hide the just-added models.
            settings_manager=settings_manager,
        )

    async def _open_logout() -> None:
        # WP-8 (Feature 1) — /logout: list stored credentials → picker → confirm
        # → AuthStorage.logout. Same DI module as /login; wires the live auth
        # storage + select + confirm + commit.
        from aelix_coding_agent.tui.login_wizard import run_logout

        await run_logout(
            auth_storage=auth_storage,
            select=context.select,
            confirm=context.confirm,
            commit=_commit,
            # S1 — thread the live registry + settings so /logout cascades the
            # de-authorization to models.json (stored apiKey) + settings.json
            # (scoped-models allow-list), not just auth.json.
            model_registry=model_registry,
            settings_manager=settings_manager,
        )

    async def _open_stats() -> None:
        # WP-8 (Feature 2) — /stats: the usage dashboard (a framed tabbed viewer
        # over the harness SessionStats + the TUI-side tracker snapshot — Session
        # / Activity / Efficiency tabs). The flow lives in
        # stats_dashboard.run_stats (dependency-injected so it is unit-testable
        # without the prompt-toolkit app); this wires the live async stats getter
        # + a point-in-time tracker snapshot + context.tabbed + commit into it.
        # The snapshot is captured ONCE at open time so the tabs are consistent
        # across switches. ``runtime_host.harness`` is read live (post-hot-swap).
        from aelix_coding_agent.tui.stats_dashboard import run_stats

        await run_stats(
            stats_getter=runtime_host.harness.get_session_stats,
            snapshot=tracker.snapshot(),
            tabbed=context.tabbed,
            commit=_commit,
            # D3 — the persisted cross-session History tab. Read live at open time
            # (capped) so it reflects rows this session has appended too.
            history_getter=lambda: history_store.load(limit=_HISTORY_MAX_RECORDS),
        )

    async def _open_extension() -> None:
        # WP-8 (Feature 3) — /extension: a read-only framed tabbed viewer over the
        # discovered extensions + the live MCP manager (Installed / Discover /
        # Sources tabs). The flow lives in extension_manager.run_extension_manager
        # (dependency-injected so it is unit-testable without the prompt-toolkit
        # app); this wires the discovered extensions list + the MCP manager +
        # context.tabbed + commit into it. The extensions list is threaded from
        # entry.py (the first harness build's discovery); the MCP manager is the
        # SAME object /mcp consults.
        from aelix_coding_agent.cli import extension_catalog
        from aelix_coding_agent.cli.config import get_agent_dir
        from aelix_coding_agent.tui.extension_manager import run_extension_manager

        await run_extension_manager(
            extensions=extensions,
            mcp_manager=mcp_manager,
            tabbed=context.tabbed,
            commit=_commit,
            # #32-A (ADR-0186) — the Sources tab renders the persisted
            # ``extension_sources`` list live. Bound method (or None when no
            # SettingsManager is wired) so each open re-reads the current list.
            sources_getter=(
                settings_manager.get_extension_sources
                if settings_manager is not None
                else None
            ),
            # Issue #65 (ADR-0188) — the filterable Discover tab renders the
            # CACHED catalogs (SYNC disk read via load_cached_catalog — no network
            # in the render closure; ``discover --refresh`` is the sole fetcher).
            # Gated like sources_getter so headless / no-settings runs pass None.
            catalog_getter=(
                (lambda: extension_catalog.load_cached_catalog(get_agent_dir()))
                if settings_manager is not None
                else None
            ),
        )

    command_ctx = CommandContext(
        chrome=out_chrome,
        harness=runtime_host.harness,
        commit=_commit,
        cwd=cwd,
        commands=commands,
        set_mode=_set_mode,
        refresh_footer=context._refresh_footer,
        model_picker=_open_model_picker,
        thinking_picker=_open_thinking_picker,
        mcp_status=_open_mcp_status,
        expand_lookup=renderer.get_expanded,
        resume_session=_resume_session,
        new_session=_new_session,
        settings_action=_open_settings,
        scoped_models_action=_open_scoped_models,
        statusline_action=_open_statusline,
        login_action=_open_login,
        logout_action=_open_logout,
        stats_action=_open_stats,
        extension_action=_open_extension,
        import_session=_import_session,
        fork_session=_fork_session,
        clone_session=_clone_session,
        tree_action=_tree_action,
        is_editor_open=lambda: editor_open_ref["open"],
        settings_manager=settings_manager,
    )

    loop = asyncio.get_running_loop()
    signals_installed: list[int] = []
    if install_signal_handlers and sys.platform != "win32":
        def _handle_signal() -> None:
            # Request a clean shutdown: EOF the input loop + stop the chrome so
            # run_tui's finally runs full teardown (unbind UI, unsubscribe,
            # dispose, restore terminal). Avoids sys.exit from a detached task,
            # which bypassed teardown and left the terminal in raw mode.
            out_chrome.request_eof()
            out_chrome.exit()

        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _handle_signal)
                signals_installed.append(sig)

    unsubscribe_holder: dict[str, Callable[[], None] | None] = {"u": None}

    context_usage_tasks: set[asyncio.Task[None]] = set()
    history_tasks: set[asyncio.Task[None]] = set()

    async def _refresh_context_usage() -> None:
        # Pull the context-window meter after each turn (async; walks messages,
        # so NOT per-frame). Degrades to no segment when usage is unavailable.
        get_stats = getattr(runtime_host.harness, "get_session_stats", None)
        if get_stats is None:
            return
        try:
            stats = await get_stats()
        except Exception:  # noqa: BLE001 — a stats hiccup must not kill the TUI
            return
        context.set_context_label(
            _format_context_label(getattr(stats, "context_usage", None))
        )
        # WP-2 (ADR-0160) — also cache the token/cost scalars for the OPTIONAL
        # input-tokens / output-tokens / cost footer segments (default-OFF). One
        # turn_end task feeds both the context% label + these scalars.
        tokens = getattr(stats, "tokens", None)
        with contextlib.suppress(Exception):
            context.set_usage_stats(
                int(getattr(tokens, "input", 0) or 0),
                int(getattr(tokens, "output", 0) or 0),
                float(getattr(stats, "cost", 0.0) or 0.0),
            )

    # Sprint 6h₂₂ (ADR-0130) — auto-retry UI countdown (closes the 6h₂₀ v2
    # deferral). Pi parity: ``interactive-mode.ts:2919-2948`` —
    # ``CountdownTimer + Loader`` show "Retrying (N/M) in Xs… (Esc to cancel)"
    # while the harness sleeps in ``_handle_retryable_error``. Aelix uses the
    # chrome's ``set_widget`` overlay above the input + a per-second ticker
    # task; Esc during countdown calls ``abort_retry()`` (Pi parity — pi's Esc
    # hook calls ``session.abortRetry()``, NOT ``abort()`` — the latter would
    # tear down the whole turn).
    retry_countdown_ref: dict[str, asyncio.Task[None] | None] = {"task": None}

    async def _tick_retry_countdown(
        attempt: int, max_attempts: int, delay_ms: int
    ) -> None:
        # W-review MEDIUM-1: self-supersession — back-to-back ``auto_retry_start``
        # events (attempt N → N+1) cancel the prior task but ``cancel()`` is
        # cooperative; if the prior body is past an ``await`` checkpoint it can
        # still write a stale widget label before CancelledError lands. Capture
        # the spawning task and exit the loop if a new ticker has replaced it.
        current = asyncio.current_task()
        # W-review LOW-2: defensive coerce — a malformed ``delay_ms`` (None /
        # non-numeric) would crash the ticker silently via ``None / 1000.0``.
        if not isinstance(delay_ms, (int, float)) or delay_ms < 0:
            delay_ms = 0
        remaining = max(0.0, delay_ms / 1000.0)
        # W-review MEDIUM-2: the prior ``try/except CancelledError: raise`` was
        # a no-op + misleading. Catch ``Exception`` to log + return so a stray
        # widget/sleep crash never gets swallowed by ``loop.create_task``'s
        # "Task exception was never retrieved" log-only fate. CancelledError is
        # BaseException → not in this except → propagates as the cooperative
        # cancellation it is.
        try:
            while remaining > 0:
                if retry_countdown_ref["task"] is not current:
                    return  # superseded by a new ticker — exit cleanly
                line = (
                    f"⟳ Retrying ({attempt}/{max_attempts}) in "
                    f"{int(remaining)}s… Esc to cancel"
                )
                out_chrome.set_widget(_RETRY_WIDGET_KEY, [line], above=True)
                step = min(1.0, remaining)
                await asyncio.sleep(step)
                remaining -= step
            if retry_countdown_ref["task"] is not current:
                return
            # Pi parity: after the sleep ends the harness immediately re-runs;
            # leave a "now…" placeholder until ``auto_retry_end`` arrives (or a
            # new ``auto_retry_start`` for the next attempt overwrites it).
            out_chrome.set_widget(
                _RETRY_WIDGET_KEY,
                [f"⟳ Retrying ({attempt}/{max_attempts}) now…"],
                above=True,
            )
        except Exception:  # noqa: BLE001 — log + return, never crash the TUI
            return

    def _start_retry_countdown(event: object) -> None:
        # Cancel any prior ticker (e.g. between attempt N and N+1) — the new
        # start event refreshes the countdown from scratch.
        prior = retry_countdown_ref["task"]
        if prior is not None and not prior.done():
            prior.cancel()
        # Swap the interrupt handler so Esc cancels the retry sleep (Pi
        # parity), not the whole turn. The original is restored on end.
        out_chrome.on_interrupt = _on_retry_interrupt
        retry_countdown_ref["task"] = loop.create_task(
            _tick_retry_countdown(
                getattr(event, "attempt", 1),
                getattr(event, "max_attempts", 1),
                getattr(event, "delay_ms", 0),
            )
        )

    def _end_retry_countdown(event: object) -> None:
        # W-review HIGH: an ``auto_retry_end`` arriving without a prior
        # ``auto_retry_start`` (out-of-order emit, double-end, defensive code)
        # would commit a misleading "✖ Retry failed" line. Skip the commit when
        # no active retry was in progress, but still restore the chrome
        # invariants (widget cleared, original interrupt handler) idempotently.
        task = retry_countdown_ref["task"]
        had_active = task is not None
        if task is not None and not task.done():
            task.cancel()
        retry_countdown_ref["task"] = None
        out_chrome.set_widget(_RETRY_WIDGET_KEY, None, above=True)
        out_chrome.on_interrupt = _on_interrupt
        if not had_active:
            return
        # Commit a final transcript line so the user has a record after the
        # transient widget clears. ``success=True`` is the terminal-success
        # path (counter reset in core.py); ``success=False`` is at-max or
        # user-cancel via ``abort_retry``.
        if getattr(event, "success", False):
            _commit(
                Text(
                    f"✓ Retry succeeded (attempt {getattr(event, 'attempt', '?')})",
                    style="green",
                )
            )
        else:
            reason = getattr(event, "final_error", None) or "cancelled"
            _commit(Text(f"✖ Retry failed: {reason}", style="bold red"))

    async def _record_history() -> None:
        # WP-8 D3 (ADR-0168) — persist a CUMULATIVE snapshot row for this session
        # at each turn end. Tokens + cost + session_id come from the authoritative
        # harness SessionStats; tool counts / turns / tool-seconds from the live
        # tracker. Fully guarded: a stats failure simply skips this row (store
        # ``append`` is itself best-effort), never disturbing the turn.
        try:
            stats = await runtime_host.harness.get_session_stats()
        except Exception:  # noqa: BLE001 — a stats failure must not break the turn
            return
        snap = tracker.snapshot()
        tokens = getattr(stats, "tokens", None)
        history_store.append(
            {
                "session_id": str(getattr(stats, "session_id", "") or ""),
                "cwd": cwd,
                "model": _model_id() or "",
                "turns": int(getattr(snap, "turns", 0) or 0),
                "tool_calls": int(getattr(snap, "tool_calls", 0) or 0),
                "tool_failures": int(getattr(snap, "tool_failures", 0) or 0),
                "input": int(getattr(tokens, "input", 0) or 0),
                "output": int(getattr(tokens, "output", 0) or 0),
                "cache_read": int(getattr(tokens, "cache_read", 0) or 0),
                "cost": float(getattr(stats, "cost", 0.0) or 0.0),
                "tool_seconds": float(
                    sum(
                        float(getattr(t, "total_duration", 0.0) or 0.0)
                        for t in getattr(snap, "per_tool", []) or []
                    )
                ),
            }
        )

    # Compaction has no turn-level spinner: a manual ``/compact`` dispatches
    # outside the ``set_running`` wrapper, so its multi-second summarizer LLM
    # call would leave the prompt looking frozen. Drive the "Working…" row from
    # the harness compaction events (manual + threshold + overflow) so the user
    # sees a live "Compacting context…" indicator. The prior working state is
    # saved/restored so an in-flight turn (auto-compaction) keeps its own row.
    _compaction_working_state: dict[str, Any] = {
        "active": False,
        "prev_msg": None,
        "prev_visible": False,
    }

    def _on_agent_event(event: object) -> None:
        # WP-8 (Feature 2) — feed the activity tracker FIRST (before the renderer)
        # so /stats reflects every event. on_event is internally guarded (a
        # malformed event never crashes the pump).
        tracker.on_event(event)
        renderer.on_agent_event(event)  # type: ignore[arg-type]
        etype = getattr(event, "type", None)
        if etype == "auto_retry_start":
            _start_retry_countdown(event)
        elif etype == "auto_retry_end":
            _end_retry_countdown(event)
        elif etype in ("compaction_start", "compaction_end", "turn_start"):
            # compaction_start/end drive the indicator; turn_start self-heals a
            # stranded indicator (a BaseException-cancelled compaction never emits
            # compaction_end). All three route through the one tested helper, so
            # there is no untested dispatch glue.
            _drive_compaction_indicator(
                out_chrome, _compaction_working_state, etype
            )
        elif etype == "turn_end":
            # Keep a strong reference so the task isn't GC'd before it runs.
            task = loop.create_task(_refresh_context_usage())
            context_usage_tasks.add(task)
            task.add_done_callback(context_usage_tasks.discard)
            # D3 — persist a cumulative /stats history row for this turn (async +
            # guarded; same strong-reference pattern as the context-usage task).
            htask = loop.create_task(_record_history())
            history_tasks.add(htask)
            htask.add_done_callback(history_tasks.discard)
            # The steer/follow-up queue drains during/after the turn — recompose
            # the footer so the "⋯ N queued" segment reflects the new count
            # (Sprint 6h₁₂e).
            context._refresh_footer()

    async def _rebind(new_harness: AgentHarness, reason: str = "resume") -> None:
        prior = unsubscribe_holder["u"]
        if prior is not None:
            with contextlib.suppress(Exception):
                prior()
        unsubscribe_holder["u"] = new_harness.subscribe(_on_agent_event)
        # A session swap (/resume, new, fork) builds a BRAND-NEW harness whose
        # fresh _ExtensionRuntime defaults to the HEADLESS ui — re-bind the live
        # TUI ui onto it (issue #9) so an extension command's ctx.ui.select /
        # confirm / notify (and hook/descriptor ui) keep driving the real surface
        # post-swap instead of hitting the headless stub. Mirrors the initial
        # bind at run-start. Issue #24: a /reload ALSO rebuilds the harness (same
        # P-302 factory), so bind_ui + command_ctx repoint are equally required.
        with contextlib.suppress(Exception):
            new_harness.runtime.bind_ui(context)
        # Issue #21 tui_widgets (ADR-0182) — reconcile manifest-declared widgets
        # against the NEW harness's extension set (swap AND reload rebuild the
        # extensions; a removed plugin's widgets must un-paint, a new one's must
        # paint). Runs before the reload early-return below on purpose.
        _apply_ext_widgets(new_harness)
        # Issue #21 themes (ADR-0184) — same reconcile for manifest themes.
        _apply_ext_themes(new_harness)
        # A session swap (/resume, new, fork) replaces the live harness — keep
        # the command context pointed at it so /model, /compact, /cost, … act on
        # the resumed session, not the stale one (Sprint 6h₁₄b, ADR-0122).
        command_ctx.harness = new_harness
        # Issue #24 — a "reload" reuses the SAME Session and keeps the SAME visible
        # transcript on screen, so the session-swap-only resets below must NOT run:
        # /expand ids still point at live transcript entries and the /stats lifetime
        # continues. Only a real session swap (new/resume/fork) clears them.
        if reason == "reload":
            return
        # /expand ids are scoped to the visible transcript, which a swap clears —
        # drop the store so post-swap /expand N can't surface the prior session's
        # body (Sprint 6h₁₅ W-review MEDIUM).
        renderer.reset_expand_store()
        # WP-8 (Feature 2) — a session swap starts a fresh /stats lifetime: the
        # tracker's per-tool / per-model / turn / wall accounting belongs to the
        # prior session, so reset it to track the resumed/new session from zero.
        tracker.reset()

    runtime_host.set_rebind_session(_rebind)

    def _on_interrupt() -> None:
        asyncio.ensure_future(_safe_abort(runtime_host.harness))

    def _on_retry_interrupt() -> None:
        # Sprint 6h₂₂ (ADR-0130) — Esc during the auto-retry countdown calls
        # ``abort_retry()`` (Pi parity ``interactive-mode.ts:2919-2948``).
        # ``abort_retry`` is synchronous (sets the flag + wakes the sleep
        # event) — no ``ensure_future`` needed. The countdown task observes
        # CancelledError when ``_end_retry_countdown`` cancels it from the
        # ``auto_retry_end`` event handler.
        harness = runtime_host.harness
        abort_retry = getattr(harness, "abort_retry", None)
        if callable(abort_retry):
            with contextlib.suppress(Exception):
                abort_retry()

    out_chrome.on_interrupt = _on_interrupt

    # Sprint 6h₁₂e — steer / follow-up (queue-while-running). The chrome fires
    # on_steer / on_follow_up ONLY while a turn is running (Enter / Alt+Enter),
    # bypassing the serialized _input_loop (blocked on harness.prompt). Enqueue
    # CONCURRENTLY (mirror of on_interrupt's ensure_future) and surface errors
    # rather than crash the chrome. A strong-ref set keeps the fire-and-forget
    # tasks alive until they finish (same pattern as context_usage_tasks).
    queue_tasks: set[asyncio.Task[None]] = set()

    def _enqueue(kind: str, text: str) -> None:
        label = "Steering" if kind == "steer" else "Follow-up"

        async def _run() -> None:
            # Late-steer race (W4 code-review): the chrome fired this only while
            # running, but the turn can end between that check and this coro
            # running. ``steer()``/``follow_up()`` enqueue regardless of phase
            # (they don't raise on idle), so an idle landing would sit ORPHANED
            # in the queue — echoed + counted but inert until the next prompt.
            # If the turn already ended, re-route through the normal submit path
            # so it drives a real turn (no misleading "Steering:" echo).
            if not out_chrome.running:
                out_chrome.submit_line(text)
                return
            output_queue.put_nowait(
                ("commit", render_user_message(text, kind=kind))
            )
            try:
                if kind == "steer":
                    await runtime_host.harness.steer(text)
                else:
                    await runtime_host.harness.follow_up(text)
            except Exception as exc:  # noqa: BLE001 — surface, never crash chrome
                output_queue.put_nowait(
                    ("commit", Text(f"✖ {label} failed: {exc}", style="bold red"))
                )
                return
            context._refresh_footer()  # reflect the new pending count

        task = loop.create_task(_run())
        queue_tasks.add(task)
        task.add_done_callback(queue_tasks.discard)

    out_chrome.on_steer = lambda t: _enqueue("steer", t)
    out_chrome.on_follow_up = lambda t: _enqueue("follow_up", t)

    # Sprint 6h₁₅ (ADR-0123) — Ctrl+T toggles thinking visibility on the live
    # renderer (collapsed → /expand-recoverable placeholder, vs full inline).
    def _toggle_thinking() -> None:
        renderer.hide_thinking = not renderer.hide_thinking
        state = "hidden" if renderer.hide_thinking else "visible"
        _commit(Text(f"💭 Thinking blocks: {state}", style="dim"))

    out_chrome.on_thinking_toggle = _toggle_thinking

    # WP-0 (ADR-0157) — shift+tab cycles the permission posture. Advances the
    # held PermissionPosture (the SAME object the gate reads), commits a transient
    # toast describing the new posture + its gate rule, and repaints the footer
    # badge. No-op when no posture is wired (tests).
    def _cycle_permission() -> None:
        if permission_posture is None:
            return
        from aelix_coding_agent.builtin.permission_mode import MODE_META

        new_mode = permission_posture.cycle()
        meta = MODE_META[new_mode]
        badge = meta.badge_text or "default"
        _commit(
            Text(
                f"⇧⇥ permission mode → {badge}  ·  {meta.description}",
                style=meta.badge_style or "dim",
            )
        )
        context._refresh_footer()

    out_chrome.on_permission_cycle = _cycle_permission
    # Surface the cycle + current posture through the command context so the
    # optional ``/permissions`` slash command works (shift+tab stays primary).
    if permission_posture is not None:
        command_ctx.cycle_permission_mode = _cycle_permission

        def _permission_mode_name() -> str | None:
            from aelix_coding_agent.builtin.permission_mode import MODE_META

            mode = permission_posture.get()
            return MODE_META[mode].badge_text or mode.value

        command_ctx.permission_mode = _permission_mode_name

    # WP-0 (ADR-0157) — wire the purpose-built approval dialog onto the held
    # PermissionExtension so the DEFAULT / AUTO_ACCEPT-bash / AUTO-ask prompt uses
    # the full-command + diff-preview modal instead of the generic select().
    if permission_ext is not None:
        from aelix_coding_agent.tui.approval_dialog import run_approval_dialog

        async def _run_approval(request: object) -> object:
            return await run_approval_dialog(
                request=request,  # type: ignore[arg-type]
                show_modal=show_modal,
                chrome=out_chrome,
                render_diff=_render_diff,
            )

        permission_ext.approval_runner = _run_approval

    # Sprint 6h₁₅ (ADR-0123) — Alt+Up restores queued steer + follow-up messages
    # back into the editor (pi app.message.dequeue parity). The harness has no
    # public queue-drain, so read/clear the private _MessageQueue instances —
    # same TUI-host private coupling as runtime._repo (documented). pi order:
    # steer messages first, then follow-up, joined by a blank line, with the
    # current editor text appended.
    def _dequeue() -> None:
        # Ungated (fires idle OR mid-turn — pi best-effort parity). Safe against
        # the harness's in-turn queue drain ONLY because this body is await-free:
        # single-threaded asyncio can't interleave it partway through a drain. If
        # this ever gains an `await`, gate it on ``not out_chrome.running``.
        harness = runtime_host.harness
        texts: list[str] = []
        for qname in ("_steering_queue", "_follow_up_queue"):
            queue = getattr(harness, qname, None)
            if queue is None:
                continue
            messages = getattr(queue, "_messages", None)
            if not messages:
                continue
            texts.extend(_message_text(m) for m in messages)
            with contextlib.suppress(Exception):
                queue.clear()
        texts = [t for t in texts if t.strip()]
        if not texts:
            _commit(Text("No queued messages to restore.", style="yellow"))
            return
        current = out_chrome.get_editor_text()
        parts = [*texts, current] if current.strip() else texts
        out_chrome.set_editor_text("\n\n".join(parts))
        context._refresh_footer()  # pending count is now 0

    out_chrome.on_dequeue = _dequeue

    # Sprint 6h₁₉ (ADR-0127) — Ctrl+V paste-image. pi parity
    # ``interactive-mode.ts:2430-2450 handleClipboardImagePaste``: read clipboard
    # via ``PIL.ImageGrab.grabclipboard()`` (PIL is already a dep — see
    # ``util/image_resize.py``); on a PIL Image, save to
    # ``tempfile.gettempdir()/aelix-clipboard-<uuid>.png`` and insert the bare
    # absolute path at the cursor. Silent no-op on no-image / file-list /
    # clipboard error (pi behavior — try/catch swallows all).
    def _paste_image() -> None:
        import os
        import tempfile
        import uuid

        try:
            from PIL import Image, ImageGrab
        except Exception:  # noqa: BLE001 — PIL absent (shouldn't happen): silent
            return
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception:  # noqa: BLE001 — pi parity: clipboard errors are silent
            return
        # ``grabclipboard`` returns ``Image.Image | list[str] | None``. pi handles
        # only the Image path; a file-list paste is out of v1 scope.
        if not isinstance(grabbed, Image.Image):
            return
        try:
            # W-review M1: ``os.path.join`` (not f-string with "/") so the
            # Windows path is single-separator — the model receives the bare
            # absolute path and expects platform-correct separators.
            path = os.path.join(
                tempfile.gettempdir(), f"aelix-clipboard-{uuid.uuid4()}.png"
            )
            # pi normalizes to png as the ``?? "png"`` fallback; Aelix writes
            # PNG unconditionally for lossless quality + universal support.
            grabbed.save(path, "PNG")
        except Exception:  # noqa: BLE001 — write failure: silent (pi parity)
            return
        out_chrome.paste_to_editor(path)

    out_chrome.on_image_paste = _paste_image

    # Sprint 6h₂₃ (ADR-0131) — Ctrl+G external editor. pi reference: pi
    # binds Ctrl+G to open ``$EDITOR`` on the current input (per the 6h₁₅
    # audit; line citation not in the audit memo). Aelix snapshots the
    # editor text → temp ``.md`` file (Aelix choice: many long prompts are
    # markdown; pi extension not in audit memo) → suspends prompt-toolkit
    # via ``in_terminal`` → spawns ``$VISUAL or $EDITOR or vi`` (POSIX
    # precedence: ``$VISUAL`` is the full-screen editor, the preferred
    # binding when one terminal escape is involved) via ``asyncio.to_thread``
    # so the event loop keeps draining (auto-retry ticker, signal handlers,
    # backend events) while the user edits → reads back → replaces editor
    # text. The ``editor_open_ref`` flag gates the input loop so an Enter
    # buffered/typed mid-edit can't drive a real turn (W-review HIGH-1).
    external_editor_tasks: set[asyncio.Task[None]] = set()
    editor_open_ref: dict[str, bool] = {"open": False}

    async def _run_external_editor(initial: str) -> None:
        editor = (
            os.environ.get("VISUAL")
            or os.environ.get("EDITOR")
            or "vi"
        )
        # ``delete=False`` so we control cleanup in the ``finally`` — the
        # editor's atomic save (vim ``:w`` → write-to-tmp + rename) won't
        # leave us with a stale fd.
        fd, path = tempfile.mkstemp(prefix="aelix-edit-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(initial)
            # Suspend prompt-toolkit's TTY ownership so ``$EDITOR`` (vim /
            # nano / emacs / VSCode CLI) can paint full-screen. ``in_terminal``
            # restores the chrome on exit. W-review MEDIUM-1: run the
            # synchronous blocking subprocess in a worker thread so the
            # asyncio loop keeps making progress (signal handlers, auto-retry
            # tickers, backend disconnects) for the minutes the user spends
            # editing.
            async with in_terminal():
                try:
                    await asyncio.to_thread(
                        subprocess.run, [editor, path], check=False  # noqa: S603,S607
                    )
                except (FileNotFoundError, OSError) as exc:
                    _commit(
                        Text(
                            f"✖ external editor failed: {exc}",
                            style="bold red",
                        )
                    )
                    return
            try:
                with open(path, encoding="utf-8") as f:
                    new_text = f.read()
            except OSError as exc:
                _commit(
                    Text(
                        f"✖ external editor read-back failed: {exc}",
                        style="bold red",
                    )
                )
                return
            # Most editors append a trailing newline on save — strip exactly
            # one so the round-trip preserves user intent (no spurious blank
            # line at the bottom of the editor).
            if new_text.endswith("\n"):
                new_text = new_text[:-1]
            # W-review LOW-1: intentional overwrite of any concurrent input.
            # ``editor_open_ref["open"]`` gates the input loop so it shouldn't
            # have driven a real turn; if the user typed into the buffer
            # the editor result replaces it. Pi behavior parity.
            out_chrome.set_editor_text(new_text)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
            editor_open_ref["open"] = False

    def _open_external_editor() -> None:
        # Fire-time guards: never spawn a sub-editor while a turn is in flight
        # OR while another editor is already open (back-to-back Ctrl+G).
        if out_chrome.running:
            _commit(
                Text(
                    "Can't open the external editor while a turn is running.",
                    style="yellow",
                )
            )
            return
        if editor_open_ref["open"]:
            return  # already open; ignore the second Ctrl+G silently
        editor_open_ref["open"] = True
        initial = out_chrome.get_editor_text()
        task = loop.create_task(_run_external_editor(initial))
        external_editor_tasks.add(task)
        task.add_done_callback(external_editor_tasks.discard)

    out_chrome.on_external_editor = _open_external_editor

    descriptor_unsub: Callable[[], None] | None = None
    descriptor_renderer: DescriptorRenderer | None = None
    chrome_task: asyncio.Task[None] | None = None
    pump_task: asyncio.Task[None] | None = None
    try:
        await runtime_host.harness.bootstrap()
        # Bind the real UI BEFORE the first session_start activation so
        # extensions never see the headless stub (ADR-0105 §1.3).
        runtime_host.harness.runtime.bind_ui(context)
        # Repaint the footer now the harness is bootstrapped so the live model
        # id (read via model_provider) shows from the first frame (Sprint 6h₁₂b).
        context._refresh_footer()
        await _rebind(runtime_host.harness)
        # Issue #21 themes (ADR-0184) — the WP-2 persisted-theme seed (~line 356)
        # runs BEFORE this initial _rebind registers manifest themes, so a
        # persisted PLUGIN theme (the /settings picker persists plugin names too)
        # was missed at seed time and the context fell back to DEFAULT_THEME on
        # every launch. Re-apply it now that _apply_ext_themes has populated the
        # registry — guarded on a name mismatch so a correctly-seeded built-in
        # theme isn't needlessly re-invalidated, and set_theme still no-ops on a
        # since-removed (unknown) name.
        if settings_manager is not None:
            with contextlib.suppress(Exception):
                persisted_theme = settings_manager.get_theme()
                if persisted_theme and context.theme.name != persisted_theme:
                    context.set_theme(persisted_theme)
        # Tier-2 descriptor probe (ADR-0095 / Sprint 6h₁₀c §C): build the keyed
        # registry + per-kind renderer, subscribe to the ui:list-modules channel,
        # then emit one synchronous probe so loaded extensions append descriptors.
        # Issue #9 — the extension-command execution authority. Reads the harness
        # LIVE (so it survives /resume·/new·/fork rebinds) and powers BOTH the
        # input-loop dispatch and the autocomplete source. ``repo`` +
        # ``session_runtime`` let a handler's ctx drive fork / new_session /
        # switch_session.
        dispatch = CommandDispatchService(
            lambda: runtime_host.harness,
            repo=getattr(runtime_host, "_repo", None),
            session_runtime=runtime_host,
        )
        descriptor_unsub, descriptor_renderer = _wire_descriptors(
            runtime_host, out_chrome, footer, context, loop, renderer, commands, cwd,
            dispatch.list_commands,
        )
        # No descriptor wiring (headless fakes without an event_bus) → the palette
        # still offers built-ins + extension commands. Install the union completer.
        if descriptor_renderer is None:
            out_chrome.set_command_completer(
                _build_input_completer(
                    lambda: {}, commands, cwd, dispatch.list_commands
                )
            )
        chrome_task = asyncio.create_task(out_chrome.run())
        pump_task = asyncio.create_task(_output_pump(output_queue, out_chrome))
        _commit(_build_banner(runtime_host.harness, cwd))
        await _input_loop(
            runtime_host,
            out_chrome,
            output_queue,
            renderer,
            descriptor_renderer,
            command_ctx,
            cwd=cwd,
            dispatch=dispatch,
        )
    finally:
        with contextlib.suppress(Exception):
            runtime_host.harness.runtime.bind_ui(HEADLESS_UI_CONTEXT)
        if descriptor_unsub is not None:
            with contextlib.suppress(Exception):
                descriptor_unsub()
        unsub = unsubscribe_holder["u"]
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()
        out_chrome.exit()
        # Sprint 6h₂₂ (ADR-0130) — cancel the auto-retry countdown ticker if a
        # shutdown lands mid-backoff. The end-event handler is the normal
        # cleanup path; this is the belt-and-braces for /quit + signals.
        countdown_task = retry_countdown_ref["task"]
        if countdown_task is not None and not countdown_task.done():
            countdown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await countdown_task
        # Sprint 6h₂₃ (ADR-0131, W-review MEDIUM-2) — cancel any in-flight
        # external-editor task. Cancellation propagates after the editor
        # subprocess exits (we don't kill the child), but tagging the task
        # cancelled stops a "Task exception was never retrieved" warning
        # under shutdown and keeps the file-cleanup ``finally`` reachable.
        for ext_task in list(external_editor_tasks):
            if not ext_task.done():
                ext_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await ext_task
        # D3 (ADR-0168) — cancel any in-flight /stats history-append task. A
        # dropped final row is acceptable (best-effort persistence); tagging the
        # task cancelled avoids a "Task exception was never retrieved" warning.
        for htask in list(history_tasks):
            if not htask.done():
                htask.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await htask
        for task in (pump_task, chrome_task):
            if task is not None:
                task.cancel()
                # CancelledError is a BaseException — suppress(Exception) misses
                # it; awaiting a just-cancelled task re-raises it.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if sys.platform != "win32":
            for sig in signals_installed:
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
        # WP-2 (ADR-0160) — flush any in-flight SettingsManager writes (the
        # setters are fire-and-forget asyncio tasks) so /settings + /scoped-models
        # changes are durable on disk before the loop tears down.
        if settings_manager is not None:
            with contextlib.suppress(Exception):
                await settings_manager.flush()
        with contextlib.suppress(Exception):
            await runtime_host.dispose()

    return 0


def _build_input_completer(
    get_routes: Callable[[], object],
    builtins: list[BuiltinCommand],
    cwd: str,
    get_ext_commands: Callable[[], list[tuple[str, str]]] | None = None,
) -> Completer:
    """The merged input completer: slash commands ∪ descriptor routes ∪
    extension commands (issue #9) ∪ ``@file`` path mentions (Sprint 6h₁₄a). Each
    sub-completer is inert outside its own trigger (``/`` vs ``@``), so merging
    them is safe."""

    from prompt_toolkit.completion import merge_completers

    return merge_completers(
        [
            DescriptorCommandCompleter(
                get_routes,  # type: ignore[arg-type]
                builtins=builtins,
                get_ext_commands=get_ext_commands,
            ),
            FileMentionCompleter(cwd),
        ]
    )


def _wire_descriptors(
    runtime_host: AgentSessionRuntime,
    chrome: AelixChrome,
    footer: AelixFooterData,
    context: AelixTUIContext,
    loop: asyncio.AbstractEventLoop,
    event_renderer: EventRenderer,
    builtins: list[BuiltinCommand],
    cwd: str,
    get_ext_commands: Callable[[], list[tuple[str, str]]] | None = None,
) -> tuple[Callable[[], None] | None, DescriptorRenderer | None]:
    """Build the descriptor registry + renderer, subscribe + emit one probe.

    Returns ``(unsubscribe, descriptor_renderer)``. The unsubscribe callable (or
    ``None`` when the runtime exposes no ``event_bus`` — e.g. headless test fakes)
    leaves all other run_tui behavior intact. ``refresh_footer`` is wired to the
    context's single footer composer so footer-segment descriptors don't clobber
    the ``⎇ branch`` line. The returned :class:`DescriptorRenderer` lets the input
    loop route ``/command`` lines that match a stored management-modal (§C).
    """

    event_bus = getattr(getattr(runtime_host.harness, "runtime", None), "event_bus", None)
    if event_bus is None:
        return None, None

    registry = DescriptorRegistry()
    renderer = DescriptorRenderer(
        chrome,
        footer,
        registry,
        loop=loop,
        refresh_footer=context._refresh_footer,
        event_bus=event_bus,
        confirm=lambda message: context.confirm("Confirm", message),
    )
    registry.on_apply = renderer.render
    registry.on_remove = renderer.clear

    # Surface built-ins ∪ the (live) descriptor command-routes through the input
    # completer. The completer reads ``renderer.command_routes`` by reference on
    # every keystroke (descriptors applied/removed after this point change
    # completions live); built-ins are static and win on a name clash (§B).
    chrome.set_command_completer(
        _build_input_completer(
            lambda: renderer.command_routes, builtins, cwd, get_ext_commands
        )
    )

    # §B — late-bind the live tool-renderer-desc lookup onto the EventRenderer so
    # tool_execution_end can intercept matching tools by reference (descriptors
    # applied/removed later change interception live, like command-routes).
    event_renderer.get_tool_renderer_desc = lambda tool_name: _lookup_tool_renderer_desc(
        registry, tool_name
    )
    event_renderer.descriptor_renderer = renderer

    unsubscribe = event_bus.on("ui:list-modules", registry.collect)
    probe = ListModulesProbe()
    event_bus.emit("ui:list-modules", probe)
    return unsubscribe, renderer


def _lookup_tool_renderer_desc(
    registry: DescriptorRegistry, tool_name: str
) -> DescriptorEnvelope | None:
    """Return the stored tool-renderer-desc envelope matching ``tool_name`` (§B)."""
    for env in registry.by_kind("tool-renderer-desc"):
        if getattr(env.payload, "tool_name", None) == tool_name:
            return env
    return None


def _match_management_modal(
    descriptor_renderer: DescriptorRenderer, text: str
) -> DescriptorEnvelope | None:
    """Match a submitted prompt line to a stored management-modal command (§C).

    A ``/<command>`` line whose command equals a stored management-modal's
    ``command`` discriminator returns that envelope (so the shell opens it
    instead of prompting the model). Non-slash lines never match.
    """
    if not text.startswith("/"):
        return None
    # Guard on the split result: "/ " → [] (whitespace-only body), not an IndexError.
    parts = text[1:].split(maxsplit=1)
    command = parts[0] if parts else ""
    if not command:
        return None
    for env in descriptor_renderer.registry.by_kind("management-modal"):
        if getattr(env.payload, "command", None) == command:
            return env
    return None


def _build_banner(harness: AgentHarness, cwd: str) -> object:
    """Build the startup banner: the Aelix terminal-logo header + a panel with
    the runtime summary (model / base url / cwd / version) followed by compact
    [Context] / [Tools] / [Skills] / [Hooks] / [Extensions] sections and a hint.

    Sprint 6h₂₅ (ADR-0153, TUI v2 quick-wins WP-5). Everything is re-derived
    INSIDE this function from ``(harness, cwd)`` so the two call sites (fresh
    start + ``/resume`` re-banner) need not change. EVERY field read is
    ``getattr``-guarded / exception-suppressed: the headless ``_BannerHarness``
    fakes (and minimal real harnesses) lack ``skills`` / ``hooks`` /
    ``extension_runner`` / ``state``, so the banner MUST degrade to a dim
    ``none`` rather than raise.

    The model id reads ``harness.current_model.id``; degrades gracefully to
    ``unknown``. The block-art logo (:mod:`aelix_coding_agent.tui._logo`) is
    styled with Rich, so it degrades to plain text on no-color terminals.
    """
    from rich.box import ROUNDED
    from rich.console import Group
    from rich.panel import Panel

    from aelix_coding_agent.tui._logo import LOGO_ANSI, LOGO_TAGLINE, LOGO_TITLE

    model = getattr(harness, "current_model", None)
    model_id = getattr(model, "id", None) or "unknown"
    base_url = getattr(model, "base_url", "") or ""

    # Version: PEP 621 read via the CLI config (Pi parity). Import-guarded so a
    # broken/absent config never crashes the first frame.
    version = "unknown"
    try:
        from aelix_coding_agent.cli.config import VERSION as _VERSION

        version = _VERSION or "unknown"
    except Exception:  # noqa: BLE001 — banner must never raise
        version = "unknown"

    # Gradient block-art (ADR-0164): Text.from_ansi renders the embedded 24-bit
    # truecolor SGR escapes (cyan → blue → purple) — no style= override so the
    # gradient shows; degrades cleanly on no-color terminals.
    logo = Text.from_ansi(LOGO_ANSI)
    logo.append(f"\n {LOGO_TITLE}\n", style="bold")
    logo.append(f" {LOGO_TAGLINE}", style="dim")

    # === runtime summary =================================================
    # The body is assembled AFTER the section labels are computed (below) so the
    # separator rule can be sized to the widest rendered row; see the assembly
    # block at the end of this function.

    # === compact sections ================================================
    # [Context] — discover_context_files at render time; non-empty → AGENTS.md
    # loaded, else 'none'. Any failure → 'none' (never crash startup).
    context_label = "none"
    try:
        from aelix_coding_agent.cli.agent_context import discover_context_files

        if discover_context_files(cwd).strip():
            context_label = "AGENTS.md"
    except Exception:  # noqa: BLE001
        context_label = "none"

    # [Tools] — SAME source the /tools command uses (harness._action_get_all_tools).
    tool_names: list[str] = []
    try:
        getter = getattr(harness, "_action_get_all_tools", None)
        if callable(getter):
            tool_names = [getattr(t, "name", str(t)) for t in (getter() or [])]
    except Exception:  # noqa: BLE001
        tool_names = []
    if tool_names:
        preview = ", ".join(tool_names[:4])
        if len(tool_names) > 4:
            preview += ", …"
        tools_label = f"{len(tool_names)} active ({preview})"
    else:
        tools_label = "none"

    # [Skills] — count of the harness skill registry (commonly 0; that's fine).
    try:
        skills_count = len(getattr(harness, "skills", []) or [])
    except Exception:  # noqa: BLE001
        skills_count = 0
    skills_label = str(skills_count) if skills_count else "none"

    # [Hooks] — count distinct event types with at least one handler on the bus.
    hooks_count = 0
    try:
        hooks = getattr(harness, "hooks", None)
        handlers = getattr(hooks, "_handlers", None)
        if isinstance(handlers, dict):
            hooks_count = sum(1 for v in handlers.values() if v)
    except Exception:  # noqa: BLE001
        hooks_count = 0
    hooks_label = str(hooks_count) if hooks_count else "none"

    # [Extensions] — names of the runtime's loaded extensions (the user's
    # explicit "show which extensions are active" ask). Built-ins guardrail +
    # permission are always present in the real CLI, so this is non-empty there.
    ext_names: list[str] = []
    try:
        runner = getattr(harness, "extension_runner", None)
        for ext in getattr(runner, "extensions", []) or []:
            name = getattr(ext, "name", None)
            if name:
                ext_names.append(str(name))
    except Exception:  # noqa: BLE001
        ext_names = []
    ext_label = ", ".join(ext_names) if ext_names else "none"

    # === assemble the panel body =========================================
    # Sprint 6h₃₂ — three UX fixes over the ADR-0153 banner:
    #   1. ALIGN: the runtime-summary values (model / baseurl / cwd / version)
    #      now start at the SAME column as the section values below them — both
    #      groups left-pad their label to ``label_w`` then one space.
    #   2. DIM the summary LABELS (``model:`` … ``version:``) so the live values
    #      read first; the values keep the default (normal) weight.
    #   3. A separator RULE between the two groups. It is a Rich ``Rule`` (NOT a
    #      hand-sized ``"─" * n`` run): Rich sizes the rule to the resolved panel
    #      interior, so it can never overflow into an orphaned wrapped dash row
    #      when a row (e.g. a deep cwd / long base url) exceeds the terminal width
    #      (6h₃₂ adversarial review, ux LOW-1).
    from rich.rule import Rule

    label_w = 12  # widest section tag ("[Extensions]") sets the shared value column

    meta_rows: list[tuple[str, str]] = [("model:", model_id)]
    if base_url:
        meta_rows.append(("baseurl:", base_url))
    meta_rows.extend([("cwd:", cwd), ("version:", version)])

    section_rows: list[tuple[str, str]] = [
        ("[Context]", context_label),
        ("[Tools]", tools_label),
        ("[Skills]", skills_label),
        ("[Hooks]", hooks_label),
        ("[Extensions]", ext_label),
    ]

    # The summary block: dim labels + aligned values, no trailing newline so the
    # rule sits flush below it inside the Group.
    meta_text = Text()
    for i, (label, value) in enumerate(meta_rows):
        if i:
            meta_text.append("\n")
        meta_text.append(f"{label:<{label_w}} ", style="dim")
        meta_text.append(value)

    # The sections block + a blank line + the hint. Verified against chrome.py key
    # bindings: the c-c handler interrupts while running and clears the buffer when
    # idle; an idle empty-buffer Ctrl+C twice (within 2s) exits (#66 item 2) —
    # advertise both.
    sections_text = Text()
    for tag, value in section_rows:
        sections_text.append(f"{tag:<{label_w}} ", style="bold cyan")
        sections_text.append(value, style="dim" if value == "none" else "")
        sections_text.append("\n")
    sections_text.append("\n")
    sections_text.append(
        "/help for commands • Ctrl+C to interrupt • Ctrl+C twice to exit",
        style="dim",
    )

    panel = Panel(
        Group(meta_text, Rule(style="dim", characters="─"), sections_text),
        box=ROUNDED,
        border_style="cyan",
        expand=False,
    )

    # Logo header, a blank spacer line, then the info panel.
    return Group(logo, Text(), panel)


async def _output_pump(queue: asyncio.Queue[tuple[str, object]], chrome: AelixChrome) -> None:
    """Apply tagged output commands in order: commit → scrollback, tail → widget.

    Sprint 6h₂₄ — flicker fix. The pump drains every item the queue can offer
    without blocking after the first ``await queue.get()`` and groups consecutive
    ``commit`` items into ONE ``print_above_many`` call (one ``in_terminal()``
    suspend per batch, regardless of how many committed lines arrived together).
    A ``tail`` item interleaved between commits flushes the pending commits
    first, then applies the tail, so visible ordering is unchanged.
    """

    while True:
        first = await queue.get()
        items: list[tuple[str, object]] = [first]
        # Drain any items already in the queue WITHOUT awaiting — they arrived
        # while we were processing the prior batch and would otherwise each
        # trigger their own in_terminal suspend.
        while True:
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # Coalesce consecutive commits while preserving order against tails.
        pending: list[object] = []
        for kind, payload in items:
            if kind == "commit":
                pending.append(payload)
            else:
                if pending:
                    with contextlib.suppress(Exception):
                        await chrome.print_above_many(pending)
                    pending = []
                if kind == "tail":
                    ansi = payload if isinstance(payload, str) else ""
                    chrome.set_widget(
                        "__stream__", ansi.split("\n") if ansi else None, above=True
                    )
        if pending:
            with contextlib.suppress(Exception):
                await chrome.print_above_many(pending)


async def _input_loop(
    runtime_host: AgentSessionRuntime,
    chrome: AelixChrome,
    output_queue: asyncio.Queue[tuple[str, object]],
    renderer: EventRenderer,
    descriptor_renderer: DescriptorRenderer | None,
    command_ctx: CommandContext,
    *,
    cwd: str,
    dispatch: CommandDispatchService | None = None,
) -> None:
    """Read → classify → drive the harness, one turn at a time."""

    # Issue #9 — surface bindings for extension-command output: a handler's
    # str-return and any failure commit to scrollback (a handler's own ctx.ui
    # toasts/dialogs flow through the separately-bound TUI ui).
    ext_command_bindings = CommandSurfaceBindings(
        emit_text=lambda s: output_queue.put_nowait(("commit", Text(s))),
        emit_error=lambda s: output_queue.put_nowait(
            ("commit", Text(s, style="bold red"))
        ),
    )

    while True:
        try:
            line = await chrome.get_input()
        except EOFError:
            return  # Ctrl+D exits

        # W-review HIGH-1 (Sprint 6h₂₃, ADR-0131): if the external editor is
        # still applying its result, any line that was buffered/pasted into
        # the parent TTY while the editor owned it (Enter, /quit, etc.) MUST
        # NOT drive a turn or escape the editor session. Silently drop the
        # line — the editor will overwrite the buffer in a moment via
        # ``set_editor_text``. ``quit``/``reload``/``empty`` are also dropped
        # since they'd race the editor's set_editor_text.
        if command_ctx.is_editor_open is not None and command_ctx.is_editor_open():
            continue

        parsed = parse_input_line(line)
        harness = runtime_host.harness

        if parsed.kind == "quit":
            return
        if parsed.kind == "empty":
            continue
        if parsed.kind == "reload":
            # Issue #24 — DORMANT FLIP POINT. Default-OFF keeps the cheap
            # resources-discover refresh; when AELIX_RELOAD_REBUILD is on, route
            # through the full factory-rebuild reload (re-discovers on-disk
            # extensions, no restart). runtime.reload() wait_for_idle()s and the
            # input loop is serialized, so no extra mid-turn guard is needed.
            if _reload_rebuild_enabled():
                await runtime_host.reload()
            else:
                await harness.reload_resources()
            continue
        # Sprint 6h₁₂a (ADR-0110) — a `prompt`-kind `/`-line resolves through the
        # command core BEFORE going to the model: (1) built-in registry handler,
        # (2) descriptor management-modal (§C), (3) else an "unknown command" hint
        # (a bare /x is NOT sent to the model). quit/exit/reload are already
        # handled above via parse_input_line (which stays PURE for cli/repl.py);
        # their metadata-only registry entries never reach this branch.
        if parsed.kind == "prompt" and parsed.text.startswith("/"):
            command = match_command(parsed.text, command_ctx.commands)
            if command is not None and command.handler is not None:
                # args = the text after the command word (Sprint 6h₁₂d). The
                # word is isolated by slash_word so this can never disagree with
                # the dispatch on what the typed command word was.
                args = parsed.text[len("/" + slash_word(parsed.text)):].strip()
                await command.handler(command_ctx, args)
                continue
            # Issue #9: extension-registered commands run HERE — after built-ins
            # (so a built-in always wins a name collision, pi parity) and BEFORE
            # the descriptor modal / "unknown command" fallback. A handled/errored
            # command suppresses the model turn; only a true miss (NOT_A_COMMAND)
            # falls through.
            if dispatch is not None:
                ext_result = await dispatch.try_execute(
                    parsed.text, ext_command_bindings
                )
                if ext_result.outcome is not DispatchOutcome.NOT_A_COMMAND:
                    continue
            if descriptor_renderer is not None:
                modal = _match_management_modal(descriptor_renderer, parsed.text)
                if modal is not None:
                    descriptor_renderer.open_modal(modal)
                    continue
            label = "/" + slash_word(parsed.text)
            output_queue.put_nowait(
                ("commit", Text(f"Unknown command: {label} — type /help", style="yellow"))
            )
            continue
        if parsed.kind in ("bash", "bash_transient"):
            if parsed.text:
                output = await handle_user_bash(
                    harness,
                    parsed.text,
                    exclude_from_context=(parsed.kind == "bash_transient"),
                    cwd=cwd,
                )
                if output.strip():
                    output_queue.put_nowait(("commit", Text(output.rstrip("\n"))))
            continue

        # prompt — drive a full turn while the chrome stays live (spinner on).
        # A failed turn must not kill the REPL (parity with run_print_mode).
        # Echo the user's own line into the transcript (Sprint 6h₁₂b) so the
        # assistant reply has its visible question above it — prompt path only
        # (bash / commands / empty already returned/continued before here).
        output_queue.put_nowait(("commit", render_user_message(parsed.text)))
        chrome.set_running(True)
        try:
            await harness.prompt(parsed.text, source="interactive")
        except Exception as exc:  # noqa: BLE001 — surface + survive a failed turn
            renderer.finalize()  # commit partial + clear the live stream window
            output_queue.put_nowait(("commit", Text(f"✖ {exc}", style="bold red")))
        finally:
            chrome.set_running(False)


async def _safe_abort(harness: AgentHarness) -> None:
    with contextlib.suppress(Exception):
        await harness.abort()


__all__ = ["run_tui"]
