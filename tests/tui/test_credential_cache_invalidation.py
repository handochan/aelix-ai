"""#240 — the two in-app seams that drop a cached ``models.json`` credential.

A resolved ``!command`` value now lives on the :class:`ModelRegistry` that
resolved it until something reloads it, so the recoveries have to be real. Only
one in-app path reaches ``_load_models`` today (``/login`` → ``refresh()``), and
NEITHER ``/reload`` arm does: ``/reload`` calls ``AgentSessionRuntime.reload``
(not ``AgentHarness.reload``), the ``AgentHarness.reset()`` leg that would have
touched a registry is dead — ``harness/core.py`` guards on a ``_model_registry``
attribute nothing ever assigns on an ``AgentHarness`` — and the factory rebuild
re-binds the SAME registry object without re-running ``_load_models``. So both
seams are built explicitly, and both are pinned here.

Every case drives the production ``_input_loop`` through ``run_tui``. An earlier
round of this repo shipped a test that replicated the shell's shape and stayed
green when the shell's real code was deleted; delete either call below and the
matching case goes red.

The end-of-turn seam is keyed on the TERMINAL MESSAGE, not on a raised
exception, and the #240 review is why. The first cut keyed it off
``except Exception`` on the belief that a live session sees every failure there.
It does not: every shipping adapter converts a provider failure into an
``AssistantErrorEvent``, ``loop.py`` returns on the resulting ``stop_reason ==
"error"``, and ``harness.prompt`` therefore RETURNS NORMALLY for a 401 —
measured against a real ``AgentHarness`` fed a 401-shaped error event. So the
clear was inert for the one failure it existed to serve, while a test built on a
RAISING fake stayed green. Both shapes are pinned below, and so are the two
turns that must NOT pay a shell start: a successful one and an aborted one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_first_run_no_provider import _RaisingHarness
from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _harness_chrome,
    _spy_commits,
    _wait,
)


class _CountingRegistry:
    """Counts :meth:`clear_config_value_cache` and nothing else.

    ``get_available`` is here because the shell's unrunnable-model gate reads it
    off the registry; a ``FakeHarness`` exposes no ``current_model`` so that gate
    is skipped, and an empty list keeps the stub honest if that ever changes.
    """

    def __init__(self) -> None:
        self.cleared = 0

    def clear_config_value_cache(self) -> None:
        self.cleared += 1

    def get_available(self) -> list[Any]:
        return []


def _launch(
    runtime: FakeRuntime, chrome: AelixChrome, registry: _CountingRegistry
) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            model_registry=registry,  # type: ignore[arg-type]
        )
    )


class _ErroringHarness(FakeHarness):
    """The shape production really takes: an error message, and NO exception.

    ``providers/openai_completions.py`` catches every provider failure and
    yields an ``AssistantErrorEvent``; ``loop.py::_stream_assistant_response``
    turns that into a terminal ``AssistantMessage`` with the given
    ``stop_reason`` and the agent loop returns on it. Nothing raises, so
    ``harness.prompt`` returns normally — which is exactly what made the first
    cut of this feature inert. Parametrised on ``stop_reason`` because
    ``"aborted"`` travels the same wire and must NOT arm the clear.
    """

    def __init__(self, *, error_text: str, stop_reason: str = "error") -> None:
        super().__init__()
        self.error_text = error_text
        self.stop_reason = stop_reason

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        from aelix_agent_core.types import MessageEndEvent, MessageStartEvent
        from aelix_ai.messages import AssistantMessage, TextContent

        self.prompts.append((text, source))
        failure = AssistantMessage(
            content=[TextContent(text=f"[error] {self.error_text}")],
            stop_reason=self.stop_reason,  # type: ignore[arg-type]
            error_message=self.error_text,
        )
        for event in (
            MessageStartEvent(message=failure),
            MessageEndEvent(message=failure),
        ):
            for listener in self.subscribers:
                listener(event)  # type: ignore[operator]
        return []


async def test_a_provider_error_that_never_raises_drops_the_cached_credential() -> None:
    """T15 — the case the feature exists for, in the shape production has.

    A ``models.json`` provider rejecting the key with a 401 reaches the TUI as a
    terminal ``stop_reason == "error"`` message and a NORMAL return from
    ``harness.prompt``. This case is red against a clear that lives in
    ``except Exception``: nothing raises, so nothing runs, and the cached
    credential keeps going out for the rest of the session — the opposite of the
    "one bad turn, not the session" the CHANGELOG, the guide and ADR-0140
    promise. Verified against a real ``AgentHarness`` in the #240 review before
    this case was written.

    The trigger is deliberately coarse — a non-Anthropic 401 is not typed as an
    auth error anywhere the TUI can see — and it costs one extra shell start
    after a turn that already failed.
    """

    harness = _ErroringHarness(error_text="401 Unauthorized")
    registry = _CountingRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, registry)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: registry.cleared == 1)
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert registry.cleared == 1
    # The renderer printed it once; the shell added no second copy (#189).
    assert sum(c.count("401 Unauthorized") for c in commits) == 1, commits


async def test_a_turn_that_raises_also_drops_the_cached_credential() -> None:
    """T15b — the second arm: a failure that never reaches a ``message_end``.

    A hook that raised, a busy harness, a stream that ended with no result — the
    harness re-raises and the renderer has no terminal message to read, so the
    ``except`` arm is the only one that can arm the clear. Keeping BOTH arms is
    what makes the recovery total; keeping only this one is the bug T15 pins.

    The #189 dedup is asserted alongside it: a mistake there would print the
    error twice again.
    """

    harness = _RaisingHarness(raise_text="401 Unauthorized")
    registry = _CountingRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, registry)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: registry.cleared == 1)
        # Let a SECOND clear (or a second error line) land before counting; a
        # count taken the instant the first appears reads 1 whatever happens.
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert registry.cleared == 1
    assert sum(c.count("401 Unauthorized") for c in commits) == 1, commits


async def test_a_successful_turn_keeps_the_cached_credential() -> None:
    """T15c — the control, and the reason #240 exists at all.

    A clear that fired on every turn would re-fork the helper once per turn and
    give the whole feature back. ``FakeHarness.prompt`` returns cleanly and
    emits nothing, so the renderer's flag is never set.
    """

    harness = FakeHarness()
    registry = _CountingRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, registry)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: len(harness.prompts) == 1)
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert registry.cleared == 0


async def test_an_aborted_turn_keeps_the_cached_credential() -> None:
    """T15d — an abort is the user, not the credential.

    ``stop_reason == "aborted"`` rides the same ``message_end`` an error does,
    and the renderer's #189/#133 flags fire for both. The #240 flag is narrower
    on purpose: an Esc says nothing about the key that was in flight, so paying
    a shell start for every interrupt would be cost with no recovery behind it.
    Widen ``_render_message_error``'s guard back to ``in ("error", "aborted")``
    and this case goes red.
    """

    harness = _ErroringHarness(error_text="request aborted", stop_reason="aborted")
    registry = _CountingRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, registry)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: len(harness.prompts) == 1)
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert registry.cleared == 0


@pytest.mark.parametrize("rebuild", ["", "0"])
async def test_reload_drops_the_cached_credential_on_both_arms(
    monkeypatch: pytest.MonkeyPatch, rebuild: str
) -> None:
    """T16 — ``/reload`` is the recovery the docs promise it is.

    Parametrised over the ``AELIX_RELOAD_REBUILD`` kill-switch because the two
    arms call different objects (``runtime_host.reload()`` vs
    ``harness.reload_resources()``) and NEITHER of them reaches
    ``_load_models``. Without the explicit clear this case fails on both arms,
    which is the whole point: rev 2 of the design assumed ``/reload`` already
    invalidated, and it did not.
    """

    if rebuild:
        monkeypatch.setenv("AELIX_RELOAD_REBUILD", rebuild)
    else:
        monkeypatch.delenv("AELIX_RELOAD_REBUILD", raising=False)

    harness = FakeHarness()
    registry = _CountingRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, registry)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: registry.cleared == 1)
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert registry.cleared == 1
    # The arm under test really is the one that ran.
    if rebuild:
        assert (runtime.reloads, harness.reloads) == (0, 1)
    else:
        assert (runtime.reloads, harness.reloads) == (1, 0)
