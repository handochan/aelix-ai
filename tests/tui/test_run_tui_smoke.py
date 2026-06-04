"""Sprint 6h₁₀b (ADR-0105) — run_tui (chrome-driven) headless smoke tests.

Drives the reworked ``run_tui`` with a headless ``AelixChrome`` (pipe input +
DummyOutput under create_app_session) and a fake runtime/harness that records
the lifecycle calls (bootstrap, bind_ui, subscribe, prompt, dispose).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import pytest
from aelix_coding_agent.extensions import HEADLESS_UI_CONTEXT
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


class _FakeHooks:
    async def emit(self, event: object) -> None:
        return None


class _FakeExtRuntime:
    def __init__(self) -> None:
        self.bound: list[object] = []

    def bind_ui(self, ui: object) -> None:
        self.bound.append(ui)


class FakeHarness:
    def __init__(self) -> None:
        self.bootstrapped = 0
        self.prompts: list[tuple[str, str]] = []
        self.reloads = 0
        self.aborts = 0
        self.subscribers: list[object] = []
        self.unsubscribed = 0
        self.runtime = _FakeExtRuntime()
        self.hooks = _FakeHooks()
        self.session = None

    async def bootstrap(self) -> None:
        self.bootstrapped += 1

    def subscribe(self, listener: object):
        self.subscribers.append(listener)

        def _unsub() -> None:
            self.unsubscribed += 1

        return _unsub

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        self.prompts.append((text, source))
        return []

    async def reload_resources(self) -> None:
        self.reloads += 1

    async def abort(self) -> None:
        self.aborts += 1


class FakeRuntime:
    def __init__(self, harness: FakeHarness) -> None:
        self._harness = harness
        self.rebind_cb = None
        self.disposed = 0

    @property
    def harness(self) -> FakeHarness:
        return self._harness

    def set_rebind_session(self, cb) -> None:
        self.rebind_cb = cb

    async def dispose(self) -> None:
        self.disposed += 1


async def _wait(predicate, *, timeout: float = 3.0) -> None:
    """Poll until ``predicate()`` is true (deterministic; no fixed sleeps)."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _harness_chrome(
    *, harness: FakeHarness | None = None
) -> AsyncGenerator[tuple[FakeRuntime, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime = FakeRuntime(harness or FakeHarness())
        chrome = AelixChrome()
        yield runtime, chrome, pipe


def _launch(runtime: FakeRuntime, chrome: AelixChrome) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
    )


async def test_run_tui_drives_prompt_and_quits() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello world\n")
        await _wait(lambda: runtime.harness.prompts == [("hello world", "interactive")])
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert runtime.harness.bootstrapped == 1
    assert runtime.harness.subscribers, "renderer must subscribe"
    assert runtime.rebind_cb is not None
    assert runtime.disposed == 1


async def test_run_tui_binds_then_unbinds_ui() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    bound = runtime.harness.runtime.bound
    assert isinstance(bound[0], AelixTUIContext)  # real UI bound first
    assert bound[-1] is HEADLESS_UI_CONTEXT  # reverted to headless on teardown


async def test_run_tui_reload_command() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: runtime.harness.reloads == 1)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.reloads == 1
    assert runtime.harness.prompts == []


async def test_run_tui_eof_exits() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("\x04")  # Ctrl+D on an empty buffer → EOF
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0
    assert runtime.disposed == 1
    assert runtime.harness.prompts == []


async def test_run_tui_survives_turn_exception() -> None:
    class ExplodingHarness(FakeHarness):
        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            self.prompts.append((text, source))
            raise RuntimeError("turn blew up")

    async with _harness_chrome(harness=ExplodingHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("boom\n")
        await _wait(lambda: runtime.harness.prompts == [("boom", "interactive")])
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0  # a failed turn did not kill the REPL
    assert runtime.disposed == 1


async def test_run_tui_ctrl_c_during_turn_aborts_and_survives() -> None:
    class BlockingHarness(FakeHarness):
        def __init__(self) -> None:
            super().__init__()
            self._unblock = asyncio.Event()

        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            self.prompts.append((text, source))
            await self._unblock.wait()  # block until aborted
            return []

        async def abort(self) -> None:
            self.aborts += 1
            self._unblock.set()  # the in-flight turn returns (harness contract)

    async with _harness_chrome(harness=BlockingHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("long turn\n")
        await _wait(lambda: runtime.harness.prompts == [("long turn", "interactive")])
        pipe.send_text("\x03")  # Ctrl+C mid-turn → on_interrupt → abort
        await _wait(lambda: runtime.harness.aborts == 1)
        pipe.send_text("/quit\n")  # REPL must still accept input
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0
    assert runtime.harness.aborts == 1


# === §C — management-modal command-trigger ==================================


def _modal_module() -> dict[str, object]:
    return {
        "kind": "management-modal",
        "namespace": "ext",
        "id": "m",
        "payload": {
            "kind": "management-modal",
            # A non-built-in command name: built-ins (e.g. /settings) win on a
            # name clash (ADR-0110), so the descriptor-modal example must use a
            # name with no built-in (here /deploy).
            "command": "deploy",
            "title": "Deploy",
            "view": "form",
        },
    }


class _BusExtRuntime(_FakeExtRuntime):
    """A fake ext-runtime exposing a real EventBus that yields one modal."""

    def __init__(self) -> None:
        super().__init__()
        from aelix_coding_agent.extensions.api import EventBus

        self.event_bus = EventBus()
        self.event_bus.on(
            "ui:list-modules", lambda probe: probe.modules.append(_modal_module())
        )


class _ModalHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.runtime = _BusExtRuntime()


async def test_run_tui_management_modal_command_opens_not_prompts() -> None:
    from aelix_coding_agent.tui.descriptors import DescriptorRenderer

    opened: list[object] = []
    orig = DescriptorRenderer.open_modal

    def _spy(self: DescriptorRenderer, env: object) -> None:
        opened.append(env)

    DescriptorRenderer.open_modal = _spy  # type: ignore[method-assign]
    try:
        async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
            task = _launch(runtime, chrome)
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("/deploy\n")  # matches the stored management-modal
            await _wait(lambda: len(opened) == 1)
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
    finally:
        DescriptorRenderer.open_modal = orig  # type: ignore[method-assign]

    assert len(opened) == 1
    assert runtime.harness.prompts == []  # routed to modal, never sent to model


async def test_run_tui_unknown_slash_is_not_sent_to_model() -> None:
    # Sprint 6h₁₂a: a `/x` matching no built-in + no modal commits an unknown-
    # command hint and is NOT sent to the model (previously it prompted).
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/unknown thing\n")  # no built-in, no matching modal
        pipe.send_text("real prompt\n")  # a real prompt DOES reach the model
        await _wait(lambda: runtime.harness.prompts == [("real prompt", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    # The unknown /x was never forwarded to the harness.
    assert runtime.harness.prompts == [("real prompt", "interactive")]


async def test_run_tui_help_command_runs_handler_not_prompt() -> None:
    # /help dispatches the built-in handler (commits the command table) and is
    # NOT sent to the model.
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/help\n")
        # No deterministic commit hook here; instead assert it never reached the
        # model and the REPL keeps running, then drive a real prompt as a barrier.
        pipe.send_text("hi\n")
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == [("hi", "interactive")]


# === Sprint 6h₁₂d — model / context command dispatch (handler args) =========


class _ModeFakeHarness(FakeHarness):
    """A FakeHarness that records /mode switches through set_steering_mode."""

    def __init__(self) -> None:
        super().__init__()
        self.steering_mode = "one-at-a-time"
        self.mode_calls: list[str] = []
        self.runtime = _BusExtRuntime()  # so descriptor wiring (and footer) is live

    def set_steering_mode(self, mode: str) -> None:
        self.mode_calls.append(mode)
        self.steering_mode = mode


async def test_run_tui_mode_command_sets_and_reflects_footer() -> None:
    # /mode all → set_steering_mode("all") + the footer ⏵⏵ segment reflects it.
    # The footer is the chrome's footer line (set via context._refresh_footer).
    harness = _ModeFakeHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/mode all\n")
        await _wait(lambda: runtime.harness.mode_calls == ["all"])  # type: ignore[attr-defined]
        await _wait(lambda: "⏵⏵ all" in chrome._footer_line)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.mode_calls == ["all"]
    assert harness.prompts == []  # never sent to the model


async def test_run_tui_clear_command_runs_handler_not_prompt() -> None:
    # /clear dispatches the built-in handler (chrome.clear) and is NOT prompted.
    async with _harness_chrome() as (runtime, chrome, pipe):
        cleared: list[int] = []
        orig = chrome.clear

        def _spy() -> None:
            cleared.append(1)
            orig()

        chrome.clear = _spy  # type: ignore[method-assign]
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/clear\n")
        await _wait(lambda: cleared == [1])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == []


async def test_run_tui_bash_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool, str]] = []

    async def _fake_bash(harness, command, *, exclude_from_context, cwd):
        calls.append((command, exclude_from_context, cwd))
        return "bash output\n"

    # Patch on the module object (not a dotted string) — robust against other
    # tests in the full suite that perturb the aelix_coding_agent.tui attr.
    monkeypatch.setattr(tui_shell, "handle_user_bash", _fake_bash)
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(runtime, cwd="/work", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("!ls\n")
        await _wait(lambda: calls == [("ls", False, "/work")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert calls == [("ls", False, "/work")]


# === Sprint 6h₁₂b — user-message echo =======================================


def _spy_commits(chrome: AelixChrome) -> list[str]:
    """Record the plain text of every renderable committed to scrollback.

    Sprint 6h₂₄ — the pump now drives chrome.print_above_many for the batch
    case (the flicker fix); print_above stays as a public API and is still
    spied as a belt-and-braces in case any caller routes around the pump.
    """
    commits: list[str] = []
    orig_single = chrome.print_above
    orig_many = chrome.print_above_many

    def _record(renderable: object) -> None:
        text = getattr(renderable, "plain", None)
        commits.append(text if isinstance(text, str) else str(renderable))

    async def _capture_single(renderable: object) -> None:
        _record(renderable)
        await orig_single(renderable)

    async def _capture_many(renderables: list[object]) -> None:
        for r in renderables:
            _record(r)
        await orig_many(renderables)

    chrome.print_above = _capture_single  # type: ignore[method-assign]
    chrome.print_above_many = _capture_many  # type: ignore[method-assign]
    return commits


async def test_run_tui_echoes_user_prompt_into_transcript() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("what is 2+2\n")
        await _wait(lambda: runtime.harness.prompts == [("what is 2+2", "interactive")])
        await _wait(lambda: any("» what is 2+2" in c for c in commits))
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    # The user's own line is echoed (role-marked) before the assistant reply.
    assert any(c == "» what is 2+2" for c in commits)


async def test_run_tui_does_not_echo_bash_command_or_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_bash(harness, command, *, exclude_from_context, cwd):
        return ""  # no output → nothing committed for the bash path either

    monkeypatch.setattr(tui_shell, "handle_user_bash", _fake_bash)
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("!ls\n")  # bash → no echo
        pipe.send_text("/help\n")  # command → no echo
        pipe.send_text("\n")  # empty → no echo
        pipe.send_text("real\n")  # prompt → DOES echo (barrier)
        await _wait(lambda: runtime.harness.prompts == [("real", "interactive")])
        await _wait(lambda: any(c == "» real" for c in commits))
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    # Only the prompt path echoed; no `» !ls`, `» /help`, or `» ` blank echo.
    # The barrier prompt "real" is the only model-bound line, hence the only echo.
    echoed = [c for c in commits if c.startswith("» ")]
    assert echoed == ["» real"]
    assert runtime.harness.prompts == [("real", "interactive")]


# === Sprint 6h₁₄b (ADR-0122) — /resume wiring ==============================


async def test_run_tui_resume_command_degrades_without_repo() -> None:
    # /resume is wired into the command context; with the fake runtime (no
    # session repo) it degrades gracefully and the REPL keeps running (barrier).
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")  # no _repo → degrade, must not crash
        pipe.send_text("hi\n")  # barrier: REPL still alive and reaching the model
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == [("hi", "interactive")]


# --- orchestration fakes: a runtime with a session repo + switch_session -----


class _ResumeMeta:
    def __init__(self, sid: str, path: str, created_at: str) -> None:
        self.id = sid
        self.path = path
        self.created_at = created_at
        self.cwd = "."


class _ResumeRepo:
    def __init__(self, metas: list[_ResumeMeta]) -> None:
        self._metas = metas
        self.list_cwds: list[str | None] = []

    async def list(self, options: object = None) -> list[_ResumeMeta]:
        self.list_cwds.append(getattr(options, "cwd", None))
        return list(self._metas)


class _ResumeSession:
    def __init__(self, session_file: str, messages: list[object]) -> None:
        self.session_file = session_file
        self._messages = messages

    async def build_context(self) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(messages=list(self._messages))


class _ResumeRuntime(FakeRuntime):
    def __init__(
        self,
        harness: FakeHarness,
        repo: _ResumeRepo,
        session: _ResumeSession,
        *,
        target: _ResumeSession | None = None,
        cancelled: bool = False,
    ) -> None:
        super().__init__(harness)
        self._repo = repo
        self.session = session  # the ACTIVE session (excluded from the picker)
        self._target = target
        self._cancelled = cancelled
        self.switch_calls: list[str] = []

    async def switch_session(self, path: str, **_kw: object) -> object:
        from types import SimpleNamespace

        self.switch_calls.append(path)
        if not self._cancelled and self._target is not None:
            self.session = self._target  # hot-swap the live session
            if self.rebind_cb is not None:
                await self.rebind_cb(self._harness)  # re-subscribe + refresh ctx
        return SimpleNamespace(cancelled=self._cancelled)


async def test_run_tui_resume_picker_excludes_active_switches_and_replays() -> None:
    # W-review M1: cover the _resume_session orchestration — list (cwd-scoped) →
    # exclude the active session → picker select #1 → switch_session(path).
    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

    metas = [
        _ResumeMeta("aaaaaaaa", "/s/active.jsonl", "2026-05-27T15:00"),  # active → excluded
        _ResumeMeta("bbbbbbbb", "/s/new.jsonl", "2026-05-27T14:00"),  # picker #1
        _ResumeMeta("cccccccc", "/s/old.jsonl", "2026-05-27T13:00"),  # picker #2
    ]
    repo = _ResumeRepo(metas)
    active = _ResumeSession("/s/active.jsonl", [])
    target = _ResumeSession(
        "/s/new.jsonl",
        [
            UserMessage(content=[TextContent(text="hi there")]),
            AssistantMessage(content=[TextContent(text="hello back")]),
        ],
    )
    runtime = _ResumeRuntime(FakeHarness(), repo, active, target=target)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")
        await _wait(lambda: bool(repo.list_cwds))  # list happened → picker shown
        await asyncio.sleep(0.1)  # let the modal render + focus
        # Sprint 6h₂₄: arrow-key select — Enter picks the cursor row (idx 0 =
        # the first non-active session, "new.jsonl" — matches the prior intent).
        pipe.send_text("\r")
        await _wait(lambda: bool(runtime.switch_calls))
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert repo.list_cwds == ["."]  # listed cwd-scoped
    # active.jsonl excluded; #1 = the newest remaining (new.jsonl), not active.
    assert runtime.switch_calls == ["/s/new.jsonl"]


async def test_run_tui_resume_empty_choices_does_not_switch() -> None:
    # Only the active session exists → no other sessions → no switch; REPL lives.
    metas = [_ResumeMeta("aaaaaaaa", "/s/active.jsonl", "2026-05-27T15:00")]
    repo = _ResumeRepo(metas)
    active = _ResumeSession("/s/active.jsonl", [])
    runtime = _ResumeRuntime(FakeHarness(), repo, active)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")
        await _wait(lambda: bool(repo.list_cwds))
        pipe.send_text("hi\n")  # barrier: REPL still reaches the model
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.switch_calls == []  # nothing to switch to


# === Sprint 6h₁₅ (ADR-0123) — /new + Alt+Up dequeue =======================


class _NewRuntime(FakeRuntime):
    def __init__(self, harness: FakeHarness) -> None:
        super().__init__(harness)
        self.new_calls = 0
        self.session = _ResumeSession("/s/active.jsonl", [])

    async def new_session(self, **_kw: object) -> object:
        from types import SimpleNamespace

        self.new_calls += 1
        if self.rebind_cb is not None:
            await self.rebind_cb(self._harness)
        return SimpleNamespace(cancelled=False)


async def test_run_tui_new_command_starts_fresh_session() -> None:
    runtime = _NewRuntime(FakeHarness())
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/new\n")
        await _wait(lambda: runtime.new_calls == 1)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.new_calls == 1


class _FakeQ:
    def __init__(self, texts: list[str]) -> None:
        from aelix_ai.messages import TextContent, UserMessage

        self._messages: list[object] = [
            UserMessage(content=[TextContent(text=t)]) for t in texts
        ]

    def clear(self) -> None:
        self._messages = []


class _QueueHarness(FakeHarness):
    def __init__(self, steer: list[str], follow: list[str]) -> None:
        super().__init__()
        self._steering_queue = _FakeQ(steer)
        self._follow_up_queue = _FakeQ(follow)


class _SettingsHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        from types import SimpleNamespace

        self.steering_mode = "one-at-a-time"
        self.follow_up_mode = "one-at-a-time"
        self.steer_set: list[str] = []
        self.follow_set: list[str] = []
        self.level_set: list[str] = []
        self._state = SimpleNamespace(thinking_level=None)

    def set_steering_mode(self, mode: str) -> None:
        self.steer_set.append(mode)
        self.steering_mode = mode

    def set_follow_up_mode(self, mode: str) -> None:
        self.follow_set.append(mode)
        self.follow_up_mode = mode

    async def set_thinking_level(self, level: str) -> None:
        self.level_set.append(level)
        self._state.thinking_level = level

    async def cycle_thinking_level(self) -> str | None:
        # Fake the canonical model-aware cycle: off → low (records via set).
        await self.set_thinking_level("low")
        return "low"


async def test_run_tui_settings_toggles_steering_mode() -> None:
    # Sprint 6h₂₄ — digit shortcuts replaced by arrow nav + Enter. Cursor
    # starts at idx 0 ("Steering mode"), so a bare Enter picks it.
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await asyncio.sleep(0.15)  # menu render + focus
        pipe.send_text("\r")  # Enter on default cursor (idx 0 = Steering mode)
        await _wait(lambda: harness.steer_set == ["all"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.steer_set == ["all"]


async def test_run_tui_settings_cycles_thinking_level() -> None:
    # Sprint 6h₂₄ — "Thinking level" is the 4th option (idx 3); 3× Down +
    # Enter selects it. Pi-faithful arrow navigation pattern.
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await asyncio.sleep(0.15)
        pipe.send_text("\x1b[B\x1b[B\x1b[B\r")  # Down × 3 → Enter
        await _wait(lambda: harness.level_set == ["low"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.level_set == ["low"]


async def test_run_tui_ctrl_v_pastes_clipboard_image_path_to_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sprint 6h₁₉ (ADR-0127): Ctrl+V → ImageGrab.grabclipboard() → temp PNG →
    # insert bare absolute path at the cursor (pi parity
    # ``interactive-mode.ts:2430-2450``). Stub the clipboard with a real tiny
    # PIL Image (PIL is already a dep) — the real save() writes a valid PNG.
    # W-review M2: ``monkeypatch.setattr`` auto-restores even if the test body
    # raises before reaching teardown (vs a hand-rolled try/finally that can
    # leak a global mutation).
    import os

    from PIL import Image, ImageGrab

    img = Image.new("RGB", (4, 4), color=(255, 0, 0))
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: img)
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("\x16")  # Ctrl+V
        await _wait(lambda: chrome.get_editor_text() != "")
        text = chrome.get_editor_text()
        assert text.endswith(".png")
        assert "aelix-clipboard-" in text
        assert os.path.exists(text)  # real PNG written to disk by PIL.save
        assert os.path.getsize(text) > 0
        os.unlink(text)
        pipe.send_text("\x03")  # Ctrl+C clears the editor
        await _wait(lambda: chrome.get_editor_text() == "")
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_ctrl_v_silent_noop_when_clipboard_has_no_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pi parity ``interactive-mode.ts:2433-2435``: if grabclipboard returns None,
    # silent no-op (no error, no editor change). W-review M2: monkeypatch.
    from PIL import ImageGrab

    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: None)
    async with _harness_chrome(harness=_ModalHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("\x16")  # Ctrl+V on an empty-image clipboard
        await asyncio.sleep(0.1)
        assert chrome.get_editor_text() == ""  # no insertion, no error
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_alt_up_restores_queued_messages_to_editor() -> None:
    # Alt+Up drains steer + follow-up queues back into the editor (steer first,
    # blank-line joined), and clears the queues.
    harness = _QueueHarness(["steer one"], ["follow two"])
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("\x1b\x1b[A")  # Alt+Up
        await _wait(lambda: chrome.get_editor_text().strip() != "")
        assert chrome.get_editor_text() == "steer one\n\nfollow two"
        assert harness._steering_queue._messages == []
        assert harness._follow_up_queue._messages == []
        pipe.send_text("\x03")  # Ctrl+C clears the editor (idle)
        await _wait(lambda: chrome.get_editor_text() == "")
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


# === Sprint 6h₂₁ (ADR-0129) — /fork shell smoke test =======================
# The /fork closure is the logic-dense one (newest-first walk for the most
# recent user-role ``message`` entry). Other 6h₂₁ closures (/import, /clone,
# /tree) are thin glue and covered by the handler-level dispatch tests.


class _FakeMessage:
    def __init__(self, role: str) -> None:
        self.role = role


class _FakeEntry:
    def __init__(self, entry_id: str, kind: str, role: str | None = None) -> None:
        self.id = entry_id
        self.type = kind
        self.message = _FakeMessage(role) if role is not None else None


class _FakeForkSession:
    """Minimal Session shape for ``_fork_session`` — only needs
    ``get_entries`` + ``build_context`` (post-swap replay)."""

    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = entries

    async def get_entries(self) -> list[_FakeEntry]:
        return self._entries

    async def build_context(self) -> object:
        class _Ctx:
            messages: list[object] = []

        return _Ctx()


class _ForkRuntime(FakeRuntime):
    """Records ``runtime.fork`` args; returns a non-cancelled result so the
    closure proceeds to the replay branch."""

    def __init__(self, harness: FakeHarness, entries: list[_FakeEntry]) -> None:
        super().__init__(harness)
        self._session = _FakeForkSession(entries)
        self.fork_calls: list[tuple[str, str]] = []

    @property
    def session(self) -> _FakeForkSession:
        return self._session

    async def fork(self, entry_id: str, *, position: str = "before") -> object:
        self.fork_calls.append((entry_id, position))

        class _Result:
            cancelled = False

        return _Result()


async def test_run_tui_fork_picks_most_recent_user_message() -> None:
    # Entries in append order (oldest → newest): u1, a1, u2, a2. Reversed walk
    # in ``_fork_session`` must select ``u2`` (the most recent user-role
    # ``message`` entry) and call ``runtime.fork("u2", position="before")``.
    entries = [
        _FakeEntry("u1", "message", role="user"),
        _FakeEntry("a1", "message", role="assistant"),
        _FakeEntry("u2", "message", role="user"),
        _FakeEntry("a2", "message", role="assistant"),
    ]
    harness = FakeHarness()
    async with _harness_chrome(harness=harness) as (_runtime, chrome, pipe):
        runtime = _ForkRuntime(harness, entries)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/fork\n")
        await _wait(lambda: runtime.fork_calls == [("u2", "before")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_fork_with_no_user_message_degrades_gracefully() -> None:
    # Entries with no user-role ``message``: the closure must emit
    # "No user message to fork before." and NOT call ``runtime.fork``.
    entries = [
        _FakeEntry("a1", "message", role="assistant"),
        _FakeEntry("t1", "thinking_level_change", role=None),
    ]
    harness = FakeHarness()
    async with _harness_chrome(harness=harness) as (_runtime, chrome, pipe):
        runtime = _ForkRuntime(harness, entries)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/fork\n")
        # The closure commits a yellow Text; we don't easily read scrollback in
        # this smoke test, so the assertion is the negative: ``fork`` MUST NOT
        # be called. A small post-submit settle keeps the assertion robust.
        await asyncio.sleep(0.05)
        assert runtime.fork_calls == []
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


# === Sprint 6h₂₂ (ADR-0130) — auto-retry UI countdown subscriber ============
# Closes the 6h₂₀ v2 deferral. Drives synthetic AutoRetryStart/End events into
# the harness subscriber and asserts the chrome ``__auto_retry__`` widget +
# the interrupt-handler swap (Esc during countdown calls abort_retry, not
# abort).


class _RetryHarness(FakeHarness):
    """FakeHarness + ``abort_retry`` recording for the Esc-during-countdown
    handler swap test."""

    def __init__(self) -> None:
        super().__init__()
        self.retry_aborts = 0

    def abort_retry(self) -> None:
        self.retry_aborts += 1


async def test_run_tui_auto_retry_countdown_shows_and_clears_widget() -> None:
    from aelix_agent_core.types import (
        AutoRetryEndEvent,
        AutoRetryStartEvent,
    )

    async with _harness_chrome(harness=_RetryHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        # Renderer subscribed → there's exactly one subscriber wired by run_tui.
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber: Callable[[object], None] = (
            runtime.harness.subscribers[0]  # type: ignore[assignment]
        )

        # Emit start (delay 1s so the tick task gets a real cycle but the test
        # doesn't sit too long).
        subscriber(
            AutoRetryStartEvent(
                attempt=2,
                max_attempts=3,
                delay_ms=1000,
                error_message="overloaded",
            )
        )
        # The widget shows up on the very first tick (set_widget is called
        # before the first sleep). Poll the chrome widget slot.
        await _wait(lambda: "__auto_retry__" in chrome._widgets_above)
        widget_lines = chrome._widgets_above["__auto_retry__"]
        text = " ".join(widget_lines)
        assert "Retrying (2/3)" in text
        assert "Esc to cancel" in text

        # End event clears the widget + commits a transcript line.
        subscriber(AutoRetryEndEvent(success=True, attempt=2, final_error=None))
        await _wait(lambda: "__auto_retry__" not in chrome._widgets_above)

        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_auto_retry_esc_calls_abort_retry_not_abort() -> None:
    # Sprint 6h₂₂ — during the countdown, ``out_chrome.on_interrupt`` is
    # swapped to call ``harness.abort_retry()`` (Pi parity). ``abort()`` (which
    # tears down the whole turn) must NOT be invoked.
    from aelix_agent_core.types import (
        AutoRetryEndEvent,
        AutoRetryStartEvent,
    )

    harness = _RetryHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber: Callable[[object], None] = (
            runtime.harness.subscribers[0]  # type: ignore[assignment]
        )

        subscriber(
            AutoRetryStartEvent(
                attempt=1, max_attempts=3, delay_ms=2000, error_message="429"
            )
        )
        await _wait(lambda: "__auto_retry__" in chrome._widgets_above)

        # Fire the swapped handler (chrome.on_interrupt is what Esc binds to).
        assert chrome.on_interrupt is not None
        chrome.on_interrupt()
        # The swap routed Esc to ``abort_retry`` — NOT ``abort``.
        assert harness.retry_aborts == 1
        assert harness.aborts == 0

        # Cleanly end so the test doesn't hang on the ticker.
        subscriber(AutoRetryEndEvent(success=False, attempt=1, final_error="cancelled"))
        await _wait(lambda: "__auto_retry__" not in chrome._widgets_above)

        # After end, the interrupt handler is restored → Esc calls ``abort``.
        chrome.on_interrupt()
        await _wait(lambda: harness.aborts == 1)
        assert harness.retry_aborts == 1  # unchanged

        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_auto_retry_end_without_prior_start_is_idempotent() -> None:
    # W-review HIGH (Sprint 6h₂₂): a stray ``auto_retry_end`` arriving without
    # an active retry must NOT commit a misleading "✖ Retry failed" transcript
    # line — chrome invariants (widget cleared, handler restored) are still
    # applied idempotently, but the commit is skipped.
    from aelix_agent_core.types import AutoRetryEndEvent

    async with _harness_chrome(harness=_RetryHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber: Callable[[object], None] = (
            runtime.harness.subscribers[0]  # type: ignore[assignment]
        )

        # No prior start. Send a stray end.
        subscriber(AutoRetryEndEvent(success=False, attempt=0, final_error="stray"))
        # A short settle so the test doesn't pass on async-not-yet-scheduled.
        await asyncio.sleep(0.05)
        # Widget never appeared.
        assert "__auto_retry__" not in chrome._widgets_above
        # No retry-related transcript line was committed. The smoke test
        # doesn't read scrollback directly, but the subscriber returning
        # without raising is the actual contract.

        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_auto_retry_shutdown_cancels_ticker_mid_backoff() -> None:
    # W-review LOW-3 (Sprint 6h₂₂): /quit during the auto-retry backoff sleep
    # must cancel the ticker via the ``finally`` block (no orphan task, fast
    # shutdown). A 10s delay would hang the test for 10s without the cleanup.
    from aelix_agent_core.types import AutoRetryStartEvent

    async with _harness_chrome(harness=_RetryHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber: Callable[[object], None] = (
            runtime.harness.subscribers[0]  # type: ignore[assignment]
        )

        subscriber(
            AutoRetryStartEvent(
                attempt=1, max_attempts=3, delay_ms=10_000, error_message="overloaded"
            )
        )
        await _wait(lambda: "__auto_retry__" in chrome._widgets_above)

        # /quit mid-backoff. If the finally block doesn't cancel the ticker,
        # this hangs ~10s; the timeout=2 makes a regression loud.
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=2)


async def test_run_tui_ctrl_g_external_editor_round_trips_through_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sprint 6h₂₃ (ADR-0131): Ctrl+G snapshots the current editor text into a
    # temp file, suspends prompt-toolkit via ``in_terminal``, spawns $EDITOR,
    # then replaces the editor text with the saved content. The smoke test:
    #   - patches ``subprocess.run`` to rewrite the temp file in place
    #     (simulating the user editing + :wq);
    #   - patches ``in_terminal`` to a no-op async context manager so the
    #     headless ``DummyOutput`` chrome doesn't deadlock on the TTY suspend.
    import subprocess as _subprocess
    from contextlib import asynccontextmanager as _ctxmgr

    from aelix_coding_agent.tui import shell as _shell

    captured_paths: list[str] = []

    def _fake_run(argv: list[str], **_kw: object) -> object:
        # ``argv = [editor, path]``. Rewrite the temp file as if the user
        # edited it, then return a CompletedProcess so caller code is happy.
        path = argv[-1]
        captured_paths.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("edited prompt body\n")

        class _CP:
            returncode = 0

        return _CP()

    @_ctxmgr
    async def _fake_in_terminal() -> AsyncGenerator[None, None]:
        yield None

    monkeypatch.setattr(_subprocess, "run", _fake_run)
    # ``shell.py`` hoists ``in_terminal`` to module-level; patch the binding
    # the closure actually resolves through. W-review LOW-2 simplification
    # over the prior ``importlib`` dance.
    monkeypatch.setattr(_shell, "in_terminal", _fake_in_terminal)

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            chrome.set_editor_text("draft prompt")
            pipe.send_text("\x07")  # Ctrl+G
            await _wait(lambda: chrome.get_editor_text() == "edited prompt body")
            assert len(captured_paths) == 1
            import os as _os

            assert not _os.path.exists(captured_paths[0])
        finally:
            # The test's success condition is the editor-text round-trip, NOT
            # graceful run_tui shutdown — cancel the task to release the
            # context manager regardless of test outcome.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def test_run_tui_ctrl_g_input_loop_gates_during_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # W-review HIGH-1 (Sprint 6h₂₃, ADR-0131): while $EDITOR is open, any
    # input that lands on the parent TTY (buffered Enter, pasted /quit, …)
    # MUST be dropped by the input loop — the editor's set_editor_text will
    # overwrite the buffer in a moment and a turn must not start.
    import subprocess as _subprocess
    from contextlib import asynccontextmanager as _ctxmgr

    from aelix_coding_agent.tui import shell as _shell

    release = asyncio.Event()

    def _slow_fake_run(argv: list[str], **_kw: object) -> object:
        # The mock blocks in a worker thread (asyncio.to_thread) until the
        # test releases it — simulating the user editing for a while.
        # ``asyncio.run`` from a non-loop thread won't work, so poll instead.
        import time

        while not release.is_set():
            time.sleep(0.01)
        path = argv[-1]
        with open(path, "w", encoding="utf-8") as f:
            f.write("done\n")

        class _CP:
            returncode = 0

        return _CP()

    @_ctxmgr
    async def _fake_in_terminal() -> AsyncGenerator[None, None]:
        yield None

    monkeypatch.setattr(_subprocess, "run", _slow_fake_run)
    monkeypatch.setattr(_shell, "in_terminal", _fake_in_terminal)

    harness = FakeHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            chrome.set_editor_text("draft")
            pipe.send_text("\x07")  # Ctrl+G — opens the (slow) editor
            # Let the input loop pick up the Ctrl+G + start the editor task.
            await asyncio.sleep(0.05)
            # Now the editor is mid-flight. A pasted-Enter scenario:
            pipe.send_text("this would have been a turn\n")
            pipe.send_text("/quit\n")
            # Settle so the input loop processes both lines.
            await asyncio.sleep(0.1)
            # The gate dropped both lines — no turn fired, no quit-return.
            assert harness.prompts == []
            assert chrome.app.is_running, "input loop must not have exited"
        finally:
            # Release the editor so the task can complete; then cancel.
            release.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def test_run_tui_ctrl_g_blocked_while_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Ctrl+G handler refuses to spawn the editor while a turn is running
    # (the editor would compete for the TTY with the live model output).
    import subprocess as _subprocess

    called: list[int] = []

    def _fake_run(_argv: list[str], **_kw: object) -> object:
        called.append(1)

        class _CP:
            returncode = 0

        return _CP()

    monkeypatch.setattr(_subprocess, "run", _fake_run)

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            chrome.set_running(True)  # simulate an in-flight turn
            pipe.send_text("\x07")  # Ctrl+G
            await asyncio.sleep(0.1)
            assert called == []
            chrome.set_running(False)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def test_run_tui_auto_retry_back_to_back_starts_cancel_prior_ticker() -> None:
    # A second ``auto_retry_start`` (attempt 2) arriving while attempt 1 is
    # still ticking must cancel the prior task + refresh the widget label to
    # the new attempt count.
    from aelix_agent_core.types import (
        AutoRetryEndEvent,
        AutoRetryStartEvent,
    )

    async with _harness_chrome(harness=_RetryHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber: Callable[[object], None] = (
            runtime.harness.subscribers[0]  # type: ignore[assignment]
        )

        subscriber(
            AutoRetryStartEvent(
                attempt=1, max_attempts=3, delay_ms=5000, error_message="429"
            )
        )
        await _wait(lambda: "__auto_retry__" in chrome._widgets_above)
        assert "(1/3)" in " ".join(chrome._widgets_above["__auto_retry__"])

        subscriber(
            AutoRetryStartEvent(
                attempt=2, max_attempts=3, delay_ms=5000, error_message="429"
            )
        )
        # The new ticker overwrites the widget label.
        await _wait(lambda: "(2/3)" in " ".join(chrome._widgets_above["__auto_retry__"]))

        subscriber(AutoRetryEndEvent(success=True, attempt=2, final_error=None))
        await _wait(lambda: "__auto_retry__" not in chrome._widgets_above)

        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
