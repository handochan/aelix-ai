"""#376 — the TUI says when a session opened on another model than the one it last ran on.

pi shows ``modelFallbackMessage`` as a warning when interactive mode starts
(``interactive-mode.ts:1216-1217`` at ``pi@1cedd3272``). Here the runtime carries
it (``AgentSessionRuntime.model_fallback_message``, set by ``cli/entry.py``'s
harness factory on every build), and ``run_tui`` commits it under the banner at
startup and after a ``/resume`` swap — not on stderr, which the first repaint
erases. Nothing is committed when the session's model came back.
"""

from __future__ import annotations

import asyncio

import pytest
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _FakeEntry,
    _harness_chrome,
    _input_loop_is_idle,
    _launch,
    _quit_within,
    _ResumeMeta,
    _ResumeRepo,
    _ResumeRuntime,
    _ResumeSession,
    _spy_commits,
    _wait,
)

_LINE = "Could not restore model anthropic/claude-haiku-4-5. Using openrouter/anthropic/claude-sonnet-4.5"


async def test_startup_commits_the_fallback_line() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        runtime.model_fallback_message = _LINE  # type: ignore[attr-defined]
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: any(_LINE in c for c in commits), what="the fallback line")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert commits.count(f"Warning: {_LINE}") == 1


async def test_startup_without_a_fallback_commits_no_warning() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        runtime.model_fallback_message = None  # type: ignore[attr-defined]
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert not any("Could not restore" in c for c in commits)


class _FallbackResumeRuntime(_ResumeRuntime):
    """``switch_session`` lands on a session whose model could not be restored."""

    model_fallback_message: str | None = None

    async def switch_session(self, path: str, **kw: object) -> object:
        self.model_fallback_message = _LINE
        return await super().switch_session(path, **kw)


async def test_resume_commits_the_fallback_line_after_the_swap() -> None:
    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

    metas = [
        _ResumeMeta("aaaaaaaa", "/s/active.jsonl", "2026-10-08T15:00"),
        _ResumeMeta("bbbbbbbb", "/s/target.jsonl", "2026-10-08T14:00"),
    ]
    repo = _ResumeRepo(metas)
    active = _ResumeSession("/s/active.jsonl", [])
    target = _ResumeSession(
        "/s/target.jsonl",
        [
            UserMessage(content=[TextContent(text="q")]),
            AssistantMessage(content=[TextContent(text="a")]),
        ],
    )
    runtime = _FallbackResumeRuntime(FakeHarness(), repo, active, target=target)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        assert not any("Could not restore" in c for c in commits)
        pipe.send_text("/resume\n")
        await _wait(lambda: chrome.is_modal_open(), what="the /resume picker to mount")
        pipe.send_text("\r")
        await _wait(lambda: any(_LINE in c for c in commits), what="the fallback line")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    resumed = next(i for i, c in enumerate(commits) if c.startswith("↻ Resumed session"))
    assert commits[resumed + 1] == f"Warning: {_LINE}"


# === Review round 2 ==========================================================
#
# The line followed /resume only. /fork, /clone and /import repaint through
# ``_replay_after_swap`` and /reload through the input loop; each rebuilds
# through the same factory, which sets (or clears) the runtime's message.


class _SwapSession:
    session_file = "/s/swapped.jsonl"

    async def get_entries(self) -> list[object]:
        return [_FakeEntry("u1", "message", role="user")]

    async def get_leaf_id(self) -> str:
        return "u1"

    async def build_context(self) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(messages=[])


class _SwapRuntime(FakeRuntime):
    """Every swap lands on a session whose model could not be restored."""

    def __init__(self, harness: FakeHarness) -> None:
        super().__init__(harness)
        self.session = _SwapSession()
        self.model_fallback_message: str | None = None
        self.calls: list[str] = []

    def _land(self, what: str) -> object:
        from types import SimpleNamespace

        self.calls.append(what)
        self.model_fallback_message = _LINE
        return SimpleNamespace(cancelled=False)

    async def fork(self, entry_id: str, *, position: str = "before") -> object:
        return self._land(f"fork:{position}")

    async def import_from_jsonl(self, path: str, **_kw: object) -> object:
        return self._land("import")

    async def reload(self) -> None:
        await super().reload()
        self._land("reload")


@pytest.mark.parametrize(
    ("command", "call", "banner"),
    [
        ("/fork", "fork:before", "⎇ Forked session"),
        ("/clone", "fork:at", "⎇ Cloned session"),
        ("/import /s/other.jsonl", "import", "↻ Imported session"),
        ("/reload", "reload", None),
    ],
    ids=["fork", "clone", "import", "reload"],
)
async def test_every_rebuild_commits_the_fallback_line(
    command: str, call: str, banner: str | None
) -> None:
    harness = FakeHarness()
    async with _harness_chrome(harness=harness) as (_unused, chrome, pipe):
        runtime = _SwapRuntime(harness)
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: _input_loop_is_idle(chrome), what="the input loop to park")
        assert not any("Could not restore" in c for c in commits)
        pipe.send_text(f"{command}\n")
        await _wait(lambda: any(_LINE in c for c in commits), what=f"the line after {command}")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert runtime.calls == [call]
    assert commits.count(f"Warning: {_LINE}") == 1
    if banner is not None:
        at = next(i for i, c in enumerate(commits) if c.startswith(banner))
        assert commits[at + 1] == f"Warning: {_LINE}"


_REFUSAL = "model 'model-x' (provider 'newlab') has no adapter."


class _KeylessRegistry:
    """What ``ModelRegistry`` answers for a provider with no credential."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def has_configured_auth(self, model: object) -> bool:
        self.asked.append(getattr(model, "provider", ""))
        return False

    def get_provider_display_name(self, provider: str) -> str:
        return "Anthropic"

    def get_available(self) -> list[object]:
        return []


async def test_the_tui_leaves_the_credential_question_to_the_harness() -> None:
    """Round 2 asked about the credential in this input loop, before
    ``harness.prompt`` (and so before its ``input`` hook): an extension that
    handled the input itself on a keyless setup never saw it. The loop asks
    nothing about a credential (review round 4: neither does the harness — a
    keyless turn fails at request time, as on ``main``); a refusal the harness
    raises after the hook (``set_prompt_check``: a model an ``input`` handler
    switched to that has no adapter) is printed once."""

    from aelix_agent_core.harness.core import AgentHarnessError
    from aelix_ai.streaming import Model

    class _RefusingHarness(FakeHarness):
        async def prompt(self, text: str, *, source: str = "interactive", images=None):  # type: ignore[override]
            self.prompts.append((text, source))
            raise AgentHarnessError("invalid_state", _REFUSAL)

    harness = _RefusingHarness()
    harness.current_model = Model(  # type: ignore[attr-defined]
        id="claude-haiku-4-5",
        provider="anthropic",
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
    )
    registry = _KeylessRegistry()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=registry,  # type: ignore[arg-type]
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(lambda: any(_REFUSAL in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [("hello there", "interactive")]
    assert registry.asked == []  # the loop did not ask before the harness's hook
    assert sum(_REFUSAL in c for c in commits) == 1
