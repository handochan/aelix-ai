"""Issue #189 — the first thing a credential-less user reads, and how often.

Two defects lived on one path, and each needs its own lane here.

1. WHAT IT SAID. Pressing Esc at the first-run wizard and typing anything drove
   a bare ``Model()`` (``api == "unknown"``) straight into the agent loop, where
   ``aelix_ai.api_registry`` raised a message naming an internal sprint, a
   Python import path, a private wiring function, "a mock stream_fn" and "the
   agent loop" — written for someone editing this repo, shown to someone who
   had just installed it. Fixed by gating the turn the way ``/model``, the
   picker, ``/agents use`` and print/json mode were already gated.

2. HOW OFTEN. It arrived TWICE for one message, and that half is NOT specific to
   #189: ``harness/core.py`` catches anything escaping the agent loop,
   synthesises an ``AssistantMessage(stop_reason="error")`` whose ``message_end``
   the renderer prints, and then re-raises the same exception into
   ``shell.py``'s catch-and-print. Any escaping exception doubled. Fixed at the
   shell, so the tests below drive a DIFFERENT error than #189's — with the gate
   in place, #189's own string never reaches the loop again, and a test that
   used it would be measuring the gate a second time instead of the dedup.

WHY THESE DRIVE ``run_tui`` AND NOT A COPY OF ITS EXCEPT-BLOCK: an earlier
round of this repo's work shipped a test that replicated the shell's shape and
therefore stayed green when the shell's real code was deleted. Every assertion
here goes through the production ``_input_loop`` via ``run_tui``; delete either
fix and the corresponding test fails.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_ai.streaming import Model
from aelix_coding_agent.core import runnable_models
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _harness_chrome,
    _spy_commits,
    _wait,
)


class _Registry:
    """The two answers ``ModelRegistry.get_available()`` can give.

    Empty is the zero-credential first-run user the wizard has just spoken to;
    non-empty means they have credentials and picked something unrunnable
    (#98's typo case), which wants a different sentence.
    """

    def __init__(self, available: list[Any]) -> None:
        self._available = available

    def get_available(self) -> list[Any]:
        return list(self._available)


def _launch_with(
    runtime: FakeRuntime,
    chrome: AelixChrome,
    *,
    model_registry: object | None = None,
) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            model_registry=model_registry,  # type: ignore[arg-type]
        )
    )


@pytest.fixture
def _adapters_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``is_runnable`` able to say NO.

    ``is_runnable`` fails OPEN by design: an empty adapter set means "we cannot
    tell which adapters exist", so every model is runnable. Nothing in a bare
    test process calls any ``register_all()``, so without this the predicate
    can never return False and the gate under test could not fire at all — the
    test would pass by measuring nothing. Naming a set here is the positive
    control for the fixture itself: ``test_a_runnable_model_is_not_gated``
    below uses the SAME set and is NOT gated.
    """

    monkeypatch.setattr(
        runnable_models, "supported_apis", lambda: {"anthropic-messages"}
    )


# === Lane 1a — the gate =====================================================


async def test_the_first_run_turn_never_reaches_the_loop(
    _adapters_exist: None,
) -> None:
    """Zero credentials + Esc + a message: the wizard's sentence, not the kernel's."""

    harness = FakeHarness()
    harness.current_model = Model()  # type: ignore[attr-defined]  # api == "unknown"
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch_with(runtime, chrome, model_registry=_Registry([]))
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(
            lambda: any("No provider configured" in c for c in commits)
        )
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    # The loop was never entered — which is the whole point. A turn that runs
    # and then renders a nicer error would still have paid for the round trip
    # and still have persisted a failure message into the session.
    assert harness.prompts == []

    shown = "\n".join(commits)
    assert "No provider configured. Run /login to add one." in shown
    # And none of the vocabulary #189 was filed about.
    for leaked in ("Sprint ", "register_all", "stream_fn", "aelix_ai."):
        assert leaked not in shown, leaked


async def test_a_user_with_credentials_gets_the_model_sentence_instead(
    _adapters_exist: None,
) -> None:
    """A non-empty catalogue means they can log in — so name the MODEL, not /login.

    ``unsupported_message`` says "check the model id and provider spelling, or
    define the provider in models.json". That is right for #98's typo and
    actively wrong for someone who never typed a model id, which is why the two
    branches exist at all.
    """

    harness = FakeHarness()
    harness.current_model = Model(id="gpt-9.9", provider="openai")  # type: ignore[attr-defined]
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch_with(
            runtime, chrome, model_registry=_Registry([object()])
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        # NOT ``"gpt-9.9" in c``: the startup banner prints ``model: gpt-9.9``,
        # so that predicate goes true before the turn is even submitted and the
        # /quit below races the line under test out of the capture. Wait for
        # wording only this branch can produce.
        await _wait(
            lambda: any("Run /model to select" in c for c in commits)
        )
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert harness.prompts == []
    shown = "\n".join(commits)
    assert "Run /model to select a working model." in shown
    assert "No provider configured" not in shown


async def test_a_runnable_model_is_not_gated(_adapters_exist: None) -> None:
    """The control the gate would be worthless without.

    Same fixture, same registry — only the model's ``api`` differs. If this
    ever went red the gate would be refusing turns it has no business
    refusing, and the two tests above would still pass.
    """

    harness = FakeHarness()
    # ``base_url`` is load-bearing, not decoration: ``is_runnable`` refuses a
    # hostless model even when its api IS supported (a model with nowhere to
    # send a key is a credential-egress hazard, per ``_base_url_missing``). A
    # control built without one would be "not gated" for the wrong reason.
    harness.current_model = Model(  # type: ignore[attr-defined]
        id="claude",
        provider="anthropic",
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
    )
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch_with(runtime, chrome, model_registry=_Registry([]))
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(
            lambda: harness.prompts == [("hello there", "interactive")]
        )
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert "No provider configured" not in "\n".join(commits)


async def test_a_harness_without_a_current_model_is_never_gated() -> None:
    """An embedder that exposes no ``current_model`` must keep working.

    Note the absent ``_adapters_exist`` fixture: this is also the "we cannot
    tell" case, and both roads have to lead to a turn.
    """

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch_with(runtime, chrome, model_registry=None)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(
            lambda: runtime.harness.prompts
            == [("hello there", "interactive")]
        )
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


# === Lane 1b — the count ====================================================


class _RaisingHarness(FakeHarness):
    """A harness that fails a turn the way ``harness/core.py`` really does.

    ``emit_message_end`` chooses whether the renderer sees the failure before
    the exception reaches the shell. Both shapes occur in production: the real
    harness emits AND re-raises, but an exception thrown before that block is
    reached (or by the emit itself) only re-raises.
    """

    def __init__(
        self,
        *,
        raise_text: str,
        emit_message_end: bool = True,
        rendered_text: str | None = None,
    ) -> None:
        super().__init__()
        self.raise_text = raise_text
        self.emit_message_end = emit_message_end
        self.rendered_text = (
            raise_text if rendered_text is None else rendered_text
        )

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        from aelix_agent_core.types import MessageEndEvent, MessageStartEvent
        from aelix_ai.messages import AssistantMessage, TextContent

        self.prompts.append((text, source))
        if self.emit_message_end:
            failure = AssistantMessage(
                content=[TextContent(text=f"[error] {self.rendered_text}")],
                stop_reason="error",
                error_message=self.rendered_text,
            )
            for event in (
                MessageStartEvent(message=failure),
                MessageEndEvent(message=failure),
            ):
                for listener in self.subscribers:
                    listener(event)  # type: ignore[operator]
        raise RuntimeError(self.raise_text)


async def _drive(harness: FakeHarness) -> list[str]:
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch_with(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: harness.prompts != [])
        await _wait(lambda: any("✖" in c for c in commits))
        # Let any SECOND emission land before counting; a count taken the
        # instant the first one appears would read 1 no matter what.
        await asyncio.sleep(0.2)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    return commits


def _count(commits: list[str], needle: str) -> int:
    return sum(c.count(needle) for c in commits)


async def test_an_escaping_error_reaches_the_glass_exactly_once() -> None:
    """The measured defect: 2 for one message. Renderer prints it, shell repeats it."""

    harness = _RaisingHarness(raise_text="the wheels came off")
    commits = await _drive(harness)
    assert _count(commits, "the wheels came off") == 1, commits


async def test_an_error_the_renderer_never_saw_is_still_printed() -> None:
    """The other half of the pair — and the reason the fix compares TEXT.

    Suppressing on a bare flag (or deleting the shell's print outright) would
    leave this user staring at a turn that silently did nothing.
    """

    harness = _RaisingHarness(
        raise_text="nobody rendered me", emit_message_end=False
    )
    commits = await _drive(harness)
    assert _count(commits, "nobody rendered me") == 1, commits


async def test_a_different_error_is_not_suppressed() -> None:
    """Two genuinely different failures must both survive.

    This is what makes the suppression safe to ship: it can only ever hide a
    line that is already on screen character-for-character.
    """

    harness = _RaisingHarness(
        raise_text="the second one", rendered_text="the first one"
    )
    commits = await _drive(harness)
    assert _count(commits, "the first one") == 1, commits
    assert _count(commits, "the second one") == 1, commits
