"""Sprint 6h₁₀b (ADR-0105) — run_tui (chrome-driven) headless smoke tests.

Drives the reworked ``run_tui`` with a headless ``AelixChrome`` (pipe input +
DummyOutput under create_app_session) and a fake runtime/harness that records
the lifecycle calls (bootstrap, bind_ui, subscribe, prompt, dispose).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
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
    """Record the plain text of every renderable committed to scrollback."""
    commits: list[str] = []
    orig = chrome.print_above

    async def _capture(renderable: object) -> None:
        text = getattr(renderable, "plain", None)
        commits.append(text if isinstance(text, str) else str(renderable))
        await orig(renderable)

    chrome.print_above = _capture  # type: ignore[method-assign]
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
        pipe.send_text("1")  # pick the first NON-active session
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
    # W-review-lesson: cover the _open_settings orchestration — /settings → menu
    # → select "Steering mode" (#1) → set_steering_mode toggled to "all".
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await asyncio.sleep(0.15)  # menu render + focus
        pipe.send_text("1")  # pick "Steering mode" → toggles one-at-a-time → all
        await _wait(lambda: harness.steer_set == ["all"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.steer_set == ["all"]


async def test_run_tui_settings_cycles_thinking_level() -> None:
    # Select "Thinking level" (#4) cycles off → low.
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await asyncio.sleep(0.15)
        pipe.send_text("4")  # "Thinking level: off" → cycles to "low"
        await _wait(lambda: harness.level_set == ["low"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.level_set == ["low"]


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
