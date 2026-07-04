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
from pathlib import Path

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
        self.reloads = 0

    @property
    def harness(self) -> FakeHarness:
        return self._harness

    def set_rebind_session(self, cb) -> None:
        self.rebind_cb = cb

    async def reload(self) -> None:
        # Issue #24 — /reload routes here by default (go-live); the cheap
        # harness.reload_resources() path is the AELIX_RELOAD_REBUILD=0 kill-switch.
        self.reloads += 1

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


async def _esc_until_settings_closed(
    chrome: AelixChrome, pipe: PipeInput, *, tries: int = 6
) -> None:
    """Close the looping /settings menu deterministically.

    The driver re-opens ``context.select`` after each applied change (and, for the
    async delegate rows, only after the action's awaits settle). A single Esc can
    land in the unmount gap. Send one Esc, then give the driver up to ~0.5s to
    settle into "closed"; if it re-opened, send another Esc. Crucially we wait for
    EACH Esc to be processed before sending the next, so we never out-pace the key
    queue (which previously corrupted the input buffer).
    """

    for _ in range(tries):
        if not chrome.is_modal_open():
            # Confirm it STAYS closed (no pending re-mount) before returning.
            await asyncio.sleep(0.1)
            if not chrome.is_modal_open():
                return
        pipe.send_text("\x1b")
        try:
            await _wait(lambda: not chrome.is_modal_open(), timeout=0.5)
        except AssertionError:
            continue  # re-opened mid-Esc; loop sends another
    raise AssertionError("settings menu did not close")


@asynccontextmanager
async def _harness_chrome(
    *, harness: FakeHarness | None = None
) -> AsyncGenerator[tuple[FakeRuntime, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime = FakeRuntime(harness or FakeHarness())
        chrome = AelixChrome()
        yield runtime, chrome, pipe


def _launch(
    runtime: FakeRuntime,
    chrome: AelixChrome,
    *,
    settings_manager: object | None = None,
) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            settings_manager=settings_manager,  # type: ignore[arg-type]
        )
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
        # Issue #24 go-live: /reload routes through runtime.reload() (the full
        # factory rebuild) by default, not harness.reload_resources().
        await _wait(lambda: runtime.reloads == 1)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.reloads == 1
    assert runtime.harness.reloads == 0  # the kill-switch path was NOT taken
    assert runtime.harness.prompts == []


async def test_run_tui_paints_manifest_widgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #21 (ADR-0182) — the startup ``_rebind`` paints a loaded
    extension's manifest-declared ``contributes.tui_widgets`` through the
    real run_tui wiring, and a rebind onto a harness WITHOUT the plugin
    un-paints them (reconcile)."""
    import textwrap
    from types import SimpleNamespace

    from aelix_agent_core.contracts import parse_manifest_toml
    from aelix_coding_agent.extensions.api import Extension

    (tmp_path / "smoke_widget_mod.py").write_text(
        textwrap.dedent("""
            class _W:
                def render(self, width):
                    return ["manifest-widget-line"]

                def handle_input(self, data):
                    pass

                def invalidate(self):
                    pass


            def make(tui, theme):
                return _W()
        """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    manifest = parse_manifest_toml(
        textwrap.dedent("""
            [plugin]
            id = "smoke-plug"
            name = "Smoke Plugin"
            version = "0.1.0"
            description = "Declares a TUI widget"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/smoke-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "smoke_widget_mod:make"

            [capabilities]
            ui_tui_trusted = true

            [activation]
            on_startup_finished = true

            [contributes]
            tui_widgets = [{ slot = "above_editor", factory = "smoke_widget_mod:make" }]
        """).strip()
    )
    harness = FakeHarness()
    harness.extension_runner = SimpleNamespace(  # type: ignore[attr-defined]
        extensions=[Extension(name="smoke-plug", manifest=manifest)]
    )
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(
            lambda: "manifest-widget-line" in str(chrome._render_widgets_above())
        )
        # A reason=="reload" rebind onto a harness without the plugin still
        # reconciles (un-paints): _apply_ext_widgets runs BEFORE _rebind's
        # reload early-return — this pins that ordering (review MEDIUM).
        assert runtime.rebind_cb is not None
        await runtime.rebind_cb(FakeHarness(), "reload")
        assert "manifest-widget-line" not in str(chrome._render_widgets_above())
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


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

    def _plain(renderable: object) -> str:
        # Sprint 6h₂₅ (ADR-0153) — the user echo is now a Rich Group (leading
        # blank line + bold-cyan echo via render_user_message). Flatten a Group
        # into its rows' plain text and drop the leading/trailing blank rows so
        # the echo reads as the bare ``» text`` line (matches the pre-helper
        # capture shape these tests assert on).
        rows = getattr(renderable, "renderables", None)
        if rows is not None:
            return "\n".join(_plain(r) for r in rows).strip("\n")
        text = getattr(renderable, "plain", None)
        return text if isinstance(text, str) else str(renderable)

    def _record(renderable: object) -> None:
        commits.append(_plain(renderable))

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


# === Sprint 6h₂₇ (ADR-0155) — /thinking + /mcp + /hooks + /context wiring ===


async def test_run_tui_wp7_commands_degrade_and_survive() -> None:
    # The WP-7 commands are wired into the command context; with the bare fake
    # harness (no current_model / set_thinking_level / get_session_stats; a
    # _FakeHooks without _handlers) + no mcp_manager threaded, each degrades
    # gracefully and the REPL keeps running (barrier proves it never crashed).
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/thinking\n")  # picker wired → degrade (no current_model)
        pipe.send_text("/mcp\n")  # mcp_status wired → "No MCP servers configured."
        pipe.send_text("/hooks\n")  # hooks bus has no _handlers → degrade
        pipe.send_text("/context\n")  # no get_session_stats → degrade
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
    # ImplConsumers (ADR-0161) — /settings is SettingsManager-backed; the
    # "Steering mode" row dual-writes the live harness AND the persisted
    # SettingsManager. Type-to-filter "Steering" narrows to the single row, Enter
    # cycles one-at-a-time → all (chars + Enter sent atomically so the select's
    # <any> filter handlers drain before the accept).
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({})
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await _wait(lambda: chrome.is_modal_open())
        pipe.send_text("Steering\n")  # filter → Enter cycles the row
        await _wait(lambda: harness.steer_set == ["all"])  # live half
        await _wait(lambda: sm.get_steering_mode() == "all")  # persisted half
        # The menu loops (re-opens after each change). Esc until closed (the loop
        # then fully exits), then terminate via the EOF path the signal handler
        # uses (typed /quit would land in a re-opened filter and race the unmount).
        await _esc_until_settings_closed(chrome, pipe)
        chrome.request_eof()
        chrome.exit()
        await asyncio.wait_for(task, timeout=5)
    assert harness.steer_set == ["all"]  # live dual-write
    assert sm.get_steering_mode() == "all"  # persisted dual-write


async def test_run_tui_settings_cycles_thinking_level() -> None:
    # ImplConsumers (ADR-0161) — the "Thinking level" row delegates to the
    # model-aware cycle (live) AND persists the new level as the default. Filter
    # to "Thinking level" then Enter triggers the delegated action.
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({})
    harness = _SettingsHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await _wait(lambda: chrome.is_modal_open())
        pipe.send_text("Thinking level\n")  # filter → Enter delegates the cycle
        await _wait(lambda: harness.level_set == ["low"])  # live cycle ran
        await _wait(lambda: sm.get_default_thinking_level() == "low")  # persisted
        # The thinking row delegates to an ASYNC action (cycle + flush); the menu
        # re-opens only after those awaits settle. Esc once the menu is showing
        # again, then wait for it to close (loop fully exited), then EOF-exit.
        await _esc_until_settings_closed(chrome, pipe)
        chrome.request_eof()
        chrome.exit()
        await asyncio.wait_for(task, timeout=5)
    assert harness.level_set == ["low"]  # live half
    assert sm.get_default_thinking_level() == "low"  # persisted default


async def test_run_tui_seeds_theme_from_persisted_setting() -> None:
    # WP-2 (ADR-0160): the live theme is seeded from the persisted ``theme``
    # setting at startup so the /settings → Theme choice applies on the NEXT
    # launch (not only the session that set it). Without the seed the context
    # starts on DEFAULT_THEME and the persisted theme is write-only.
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({"theme": "dark"})
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        # The real UI context is bound onto the ext-runtime first (see
        # test_run_tui_binds_then_unbinds_ui); read its live theme.
        await _wait(lambda: bool(runtime.harness.runtime.bound))
        context = runtime.harness.runtime.bound[0]
        await _wait(lambda: getattr(context._theme, "name", None) == "dark")
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert getattr(context._theme, "name", None) == "dark"


async def test_run_tui_unknown_persisted_theme_falls_back_to_default() -> None:
    # A stale/removed theme name in settings must NOT break startup: the seed
    # is guarded (set_theme no-ops on an unknown name) and the context keeps the
    # default theme.
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.tui import themes as _themes

    sm = SettingsManager.in_memory({"theme": "no-such-theme"})
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.runtime.bound))
        context = runtime.harness.runtime.bound[0]
        # Settle so any (no-op) seed attempt has run.
        await asyncio.sleep(0.05)
        assert context._theme is _themes.DEFAULT_THEME
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_restores_persisted_manifest_theme(tmp_path: Path) -> None:
    # Issue #21 (ADR-0184) review HIGH regression: a persisted PLUGIN theme must
    # survive relaunch. The WP-2 seed runs BEFORE the startup _rebind registers
    # manifest themes, so WITHOUT the post-_rebind re-seed the context would fall
    # back to DEFAULT_THEME every launch even though the plugin theme is present.
    import textwrap
    from types import SimpleNamespace

    from aelix_agent_core.contracts import parse_manifest_toml
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.extensions.api import Extension
    from aelix_coding_agent.tui import themes as _themes

    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "solar.toml").write_text(
        'name = "solarized"\n[roles]\naccent = "green"\n', encoding="utf-8"
    )
    manifest = parse_manifest_toml(
        textwrap.dedent("""
            [plugin]
            id = "theme-plug"
            name = "Theme Plugin"
            version = "0.1.0"
            description = "Ships a theme"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/theme-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "theme_plug_mod:setup"

            [activation]
            on_startup_finished = true

            [contributes]
            themes = [{ path = "themes/solar.toml" }]
        """).strip()
    )
    ext = Extension(name="theme-plug", manifest=manifest)
    ext.resolved_path = str(tmp_path)
    harness = FakeHarness()
    harness.extension_runner = SimpleNamespace(  # type: ignore[attr-defined]
        extensions=[ext]
    )
    sm = SettingsManager.in_memory({"theme": "solarized"})
    try:
        async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
            task = _launch(runtime, chrome, settings_manager=sm)
            await _wait(lambda: chrome.app.is_running)
            await _wait(lambda: bool(runtime.harness.runtime.bound))
            context = runtime.harness.runtime.bound[0]
            # The plugin theme is registered by _apply_ext_themes AND re-applied
            # (not left on default) — this is what the re-seed fixes.
            await _wait(
                lambda: getattr(context._theme, "name", None) == "solarized"
            )
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
        assert getattr(context._theme, "name", None) == "solarized"
    finally:
        _themes.register_themes([])  # keep the process-global registry clean


# === Issue #50 — startup seed of persisted thinking settings ================


def _capture_renderer(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Patch the module-level ``EventRenderer`` so the test can read the live
    instance run_tui builds (it is otherwise wrapped in a closure subscriber)."""

    from aelix_coding_agent.tui.render import EventRenderer as _RealRenderer

    captured: list[object] = []

    def _factory(*args: object, **kwargs: object) -> object:
        r = _RealRenderer(*args, **kwargs)  # type: ignore[arg-type]
        captured.append(r)
        return r

    monkeypatch.setattr(tui_shell, "EventRenderer", _factory)
    return captured


async def test_run_tui_seeds_visible_thinking_from_persisted_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issue #50 (a): hideThinkingBlock=False (visible) saved → after startup the
    # renderer's hide_thinking is False, so reasoning renders in full this run.
    from aelix_ai.settings import SettingsManager

    captured = _capture_renderer(monkeypatch)
    sm = SettingsManager.in_memory({"hideThinkingBlock": False})
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(captured))
        await _wait(lambda: captured[0].hide_thinking is False)  # type: ignore[attr-defined]
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert captured[0].hide_thinking is False  # type: ignore[attr-defined]


async def test_run_tui_seeds_hidden_thinking_from_persisted_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issue #50 (a) — the discriminating direction: hideThinkingBlock=True saved
    # → the renderer hides even though the hardcoded default is now visible.
    from aelix_ai.settings import SettingsManager

    captured = _capture_renderer(monkeypatch)
    sm = SettingsManager.in_memory({"hideThinkingBlock": True})
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(captured))
        await _wait(lambda: captured[0].hide_thinking is True)  # type: ignore[attr-defined]
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert captured[0].hide_thinking is True  # type: ignore[attr-defined]


class _ThinkingSeedHarness(FakeHarness):
    """FakeHarness exposing a fixed ``current_model`` + recording
    ``set_thinking_level`` so the issue #50 (b) startup seed is observable."""

    def __init__(self, model: object) -> None:
        super().__init__()
        from types import SimpleNamespace

        self._model = model
        self.level_set: list[str] = []
        self._state = SimpleNamespace(thinking_level=None)

    @property
    def current_model(self) -> object:
        return self._model

    async def set_thinking_level(self, level: str) -> None:
        self.level_set.append(level)
        self._state.thinking_level = level


async def test_run_tui_seeds_default_thinking_level_when_supported() -> None:
    # Issue #50 (b): a persisted defaultThinkingLevel the current model supports
    # is applied to the live harness at startup (mirror of the default-model seed).
    from aelix_ai.models import Model
    from aelix_ai.settings import SettingsManager

    model = Model(
        id="m",
        api="anthropic",
        reasoning=True,
        thinking_level_map={"low": 2048, "medium": 8192, "high": 16384},
    )
    harness = _ThinkingSeedHarness(model)
    sm = SettingsManager.in_memory({"defaultThinkingLevel": "medium"})
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: harness.level_set == ["medium"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.level_set == ["medium"]


async def test_run_tui_skips_default_thinking_level_when_unsupported() -> None:
    # Issue #50 (b): a non-reasoning model supports only "off"; a persisted
    # "high" is NOT applied (the seed validates via get_supported_thinking_levels).
    from aelix_ai.models import Model
    from aelix_ai.settings import SettingsManager

    model = Model(id="m", api="anthropic", reasoning=False)
    harness = _ThinkingSeedHarness(model)
    sm = SettingsManager.in_memory({"defaultThinkingLevel": "high"})
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=sm)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hi\n")  # barrier: the startup seed already ran before this
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert harness.level_set == []  # unsupported level was skipped


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


# === WP-8 — /login + /logout + /stats + /extension wiring ===================


async def test_run_tui_wp8_commands_resolve_and_survive() -> None:
    # The four WP-8 commands are wired into the command context. With the bare
    # fake harness (no auth_storage / no get_session_stats threaded) /login,
    # /logout, and /stats each degrade gracefully and the REPL keeps running
    # (the barrier prompt proves nothing crashed and none were sent to the model).
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/login\n")  # no auth_storage → red "no auth storage"
        pipe.send_text("/logout\n")  # no auth_storage → red "no auth storage"
        pipe.send_text("/stats\n")  # no get_session_stats → degrade
        pipe.send_text("hi\n")  # barrier: REPL still alive and reaching the model
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == [("hi", "interactive")]


async def test_run_tui_extension_command_opens_tabbed_viewer_and_closes() -> None:
    # /extension is wired to context.tabbed (Stage A primitive). With no
    # extensions threaded + no mcp_manager the Installed tab is the empty-state
    # text; the modal opens and Esc closes it, then the REPL keeps running.
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/extension\n")
        await _wait(lambda: chrome.is_modal_open())  # the tabbed viewer opened
        pipe.send_text("\x1b")  # Esc closes the viewer
        await _wait(lambda: not chrome.is_modal_open())
        pipe.send_text("hi\n")  # barrier: REPL still alive
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == [("hi", "interactive")]


class _FakePluginIdentity:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version


class _FakePluginManifest:
    def __init__(self, name: str, version: str) -> None:
        self.plugin = _FakePluginIdentity(name, version)


class _FakeExtension:
    def __init__(self, name: str, manifest: object | None) -> None:
        self.name = name
        self.manifest = manifest


class _FakeMcpConn:
    def __init__(self, name: str, transport: str, *, connected: bool) -> None:
        self.name = name
        self.transport = transport
        self.connected = connected


class _FakeMcpManager:
    def __init__(self, conns: list[_FakeMcpConn]) -> None:
        self.connections = {c.name: c for c in conns}


async def test_run_tui_extension_command_renders_populated_installed_tab() -> None:
    # End-to-end: a discovered extension list + a live mcp_manager threaded into
    # run_tui must reach the /extension Installed tab. Spy on context.tabbed to
    # capture the tabs the wired _open_extension passes, then drive the Installed
    # render closure and assert it shows the plugin + MCP rows (not just the
    # empty-state path the other smoke test covers).
    captured: dict[str, object] = {}

    async def _spy_tabbed(self, title, tabs, *, initial=0):  # type: ignore[no-untyped-def]
        captured["title"] = title
        captured["tabs"] = list(tabs)
        # Do not actually mount a modal — just record the wiring + return.
        return None

    ext = _FakeExtension("my-plugin", _FakePluginManifest("My Plugin", "3.1.4"))
    mcp = _FakeMcpManager([_FakeMcpConn("srv", "stdio", connected=True)])

    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        runtime = FakeRuntime(FakeHarness())
        chrome = AelixChrome()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(AelixTUIContext, "tabbed", _spy_tabbed, raising=True)
            task = asyncio.ensure_future(
                run_tui(
                    runtime,  # type: ignore[arg-type]
                    cwd=".",
                    chrome=chrome,
                    install_signal_handlers=False,
                    extensions=[ext],  # type: ignore[list-item]
                    mcp_manager=mcp,  # type: ignore[arg-type]
                )
            )
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("/extension\n")
            await _wait(lambda: "tabs" in captured)
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)

    assert captured["title"] == "Extensions"
    tabs = dict(captured["tabs"])  # type: ignore[arg-type]
    assert list(tabs.keys()) == ["Installed", "Discover", "Sources"]
    installed_body = "\n".join(tabs["Installed"]())  # type: ignore[operator]
    # The threaded extension (manifest name + version) and the live MCP server
    # row both render — proving the discovered list + mcp_manager reached here.
    assert "✓ My Plugin 3.1.4" in installed_body
    assert "srv — stdio — connected" in installed_body


class _StatsTrackerHarness(FakeHarness):
    """FakeHarness exposing ``get_session_stats`` so /stats opens its dashboard."""

    async def get_session_stats(self) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            tokens=SimpleNamespace(
                input=100, output=50, cache_read=10, cache_write=5, total=160
            ),
            cost=0.0123,
            total_messages=4,
            user_messages=2,
            assistant_messages=2,
        )


async def test_run_tui_stats_command_opens_dashboard_and_closes() -> None:
    # With a harness exposing get_session_stats, /stats opens the framed tabbed
    # dashboard (Session/Activity/Efficiency); Esc closes it; REPL survives.
    async with _harness_chrome(harness=_StatsTrackerHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/stats\n")
        await _wait(lambda: chrome.is_modal_open())  # dashboard opened
        pipe.send_text("\t")  # Tab switches Session → Activity (no crash)
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")  # Esc closes
        await _wait(lambda: not chrome.is_modal_open())
        pipe.send_text("hi\n")  # barrier
        await _wait(lambda: runtime.harness.prompts == [("hi", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.prompts == [("hi", "interactive")]


# === Issue #9 — extension /commands execute in the TUI ======================


def _ext_harness(handler, name: str = "hello") -> FakeHarness:
    from aelix_agent_core.harness._extension_runner import ExtensionRunner
    from aelix_coding_agent.extensions.api import Extension, RegisteredCommand

    class _ExtHarness(FakeHarness):
        def __init__(self) -> None:
            super().__init__()
            ext = Extension(name="demo")
            ext.commands[name] = RegisteredCommand(
                name=name, handler=handler, description="greet", source="demo"
            )
            self.extension_runner = ExtensionRunner(extensions=[ext])

        def make_command_context(self, *, repo=None, session_runtime=None):
            return object()  # the handlers in these tests ignore ctx

    return _ExtHarness()


async def test_run_tui_extension_command_runs_not_prompts() -> None:
    ran: list[str] = []

    def _hello(args, ctx):
        ran.append(args)
        return "hi from ext"

    async with _harness_chrome(harness=_ext_harness(_hello)) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/hello world\n")
        await _wait(lambda: ran == ["world"])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    # The extension command ran with raw args and was NOT sent to the model.
    assert ran == ["world"]
    assert runtime.harness.prompts == []


async def test_run_tui_builtin_wins_over_extension_command() -> None:
    ran: list[str] = []

    # An extension also registers "help" — the built-in /help must win, so this
    # handler must NEVER run.
    harness = _ext_harness(lambda args, ctx: ran.append(args), name="help")
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/help\n")
        # barrier: a real prompt proves the input loop processed past /help.
        pipe.send_text("ping\n")
        await _wait(lambda: runtime.harness.prompts == [("ping", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert ran == []  # the built-in won; the extension's /help never ran


async def test_run_tui_rebind_rebinds_ui_to_new_harness() -> None:
    """Review MEDIUM-1: a session swap builds a fresh harness whose runtime
    defaults to the headless UI — the rebind must re-bind the live TUI ui so an
    extension command's ctx.ui (and hook/descriptor ui) keep working post-swap."""
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        assert runtime.rebind_cb is not None
        new_harness = FakeHarness()
        await runtime.rebind_cb(new_harness)
        # bind_ui was called on the NEW harness's runtime with the real TUI ui.
        assert new_harness.runtime.bound, "new harness runtime was not re-bound"
        assert isinstance(new_harness.runtime.bound[-1], AelixTUIContext)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_extension_command_throw_survives() -> None:
    def _boom(args, ctx):
        raise RuntimeError("kaboom")

    async with _harness_chrome(harness=_ext_harness(_boom)) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/hello\n")  # handler raises — REPL must survive
        # barrier proves the loop survived the throw and is NOT stuck.
        pipe.send_text("ping\n")
        await _wait(lambda: runtime.harness.prompts == [("ping", "interactive")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    # The thrown command did not fall through to the model.
    assert runtime.harness.prompts == [("ping", "interactive")]


async def test_run_tui_resume_replays_custom_message_via_extension_renderer() -> None:
    """Issue #62 (ADR-0183) — /resume takes the DISPLAY tier when the session
    exposes ``get_branch`` and dispatches the extension MessageRenderer
    (first-wins by custom_type; display=False gated before lookup)."""
    from aelix_agent_core.harness._extension_runner import ExtensionRunner
    from aelix_agent_core.session.entries import CustomMessageEntry, MessageEntry
    from aelix_ai.messages import TextContent, UserMessage
    from aelix_coding_agent.extensions.api import Extension

    calls: list[tuple[str, bool]] = []

    class _Comp:
        def render(self, width: int) -> list[str]:
            return ["CUSTOM-RENDERED"]

        def handle_input(self, data: str) -> None:
            pass

        def invalidate(self) -> None:
            pass

    def _ext_renderer(msg: object, options: object, theme: object) -> object:
        assert theme is not None  # the live TUI theme is threaded through
        calls.append(
            (getattr(msg, "custom_type", ""), bool(getattr(options, "expanded", False)))
        )
        return _Comp()

    ext = Extension(name="rplug")
    ext.message_renderers["status"] = _ext_renderer

    class _BranchSession(_ResumeSession):
        """A resume target that ALSO exposes get_branch (display tier)."""

        def __init__(self, session_file: str, entries: list[object]) -> None:
            super().__init__(session_file, [])
            self._entries = entries
            self.get_branch_calls = 0

        async def get_branch(self) -> list[object]:
            self.get_branch_calls += 1
            return list(self._entries)

    ts = "2026-07-04T00:00:00Z"
    entries: list[object] = [
        MessageEntry(
            id="1",
            parent_id=None,
            timestamp=ts,
            message=UserMessage(content=[TextContent(text="hi there")]),
        ),
        CustomMessageEntry(
            id="2",
            parent_id="1",
            timestamp=ts,
            custom_type="status",
            content="deploy green",
            display=True,
        ),
        CustomMessageEntry(
            id="3",
            parent_id="2",
            timestamp=ts,
            custom_type="status",
            content="hidden",
            display=False,
        ),
    ]
    metas = [
        _ResumeMeta("aaaaaaaa", "/s/active.jsonl", "2026-05-27T15:00"),
        _ResumeMeta("bbbbbbbb", "/s/new.jsonl", "2026-05-27T14:00"),
    ]
    repo = _ResumeRepo(metas)
    active = _ResumeSession("/s/active.jsonl", [])
    target = _BranchSession("/s/new.jsonl", entries)
    harness = FakeHarness()
    harness.extension_runner = ExtensionRunner(  # type: ignore[attr-defined]
        extensions=[ext]
    )
    runtime = _ResumeRuntime(harness, repo, active, target=target)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")
        await _wait(lambda: bool(repo.list_cwds))
        await asyncio.sleep(0.1)  # let the picker modal render + focus
        pipe.send_text("\r")
        await _wait(lambda: bool(runtime.switch_calls))
        await _wait(lambda: bool(calls))  # renderer dispatched during replay
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert target.get_branch_calls == 1  # DISPLAY tier taken, not build_context
    assert calls == [("status", False)]  # display=False custom never dispatched


async def test_run_tui_fork_replays_custom_via_renderer_second_callsite() -> None:
    """Issue #62 review (NIT): the display-tier renderer dispatch is wired on
    the SECOND replay callsite too (_replay_after_swap: /fork·/clone·/import),
    not only /resume. /fork picks the recent user message, forks, then
    _replay_after_swap → _display_messages → get_branch → renderer."""
    from aelix_agent_core.harness._extension_runner import ExtensionRunner
    from aelix_agent_core.session.entries import CustomMessageEntry, MessageEntry
    from aelix_ai.messages import TextContent, UserMessage
    from aelix_coding_agent.extensions.api import Extension

    calls: list[str] = []

    class _Comp:
        def render(self, width: int) -> list[str]:
            return ["FORK-CUSTOM-RENDERED"]

        def handle_input(self, data: str) -> None:
            pass

        def invalidate(self) -> None:
            pass

    def _ext_renderer(msg: object, options: object, theme: object) -> object:
        calls.append(getattr(msg, "custom_type", ""))
        return _Comp()

    ext = Extension(name="fplug")
    ext.message_renderers["status"] = _ext_renderer

    ts = "2026-07-04T00:00:00Z"
    branch: list[object] = [
        MessageEntry(id="u1", parent_id=None, timestamp=ts,
                     message=UserMessage(content=[TextContent(text="hi")])),
        CustomMessageEntry(id="c1", parent_id="u1", timestamp=ts,
                           custom_type="status", content="deploy green", display=True),
    ]

    class _ForkBranchSession(_FakeForkSession):
        async def get_branch(self, from_id: str | None = None) -> list[object]:
            return list(branch)

    class _ForkBranchRuntime(_ForkRuntime):
        def __init__(self, harness: FakeHarness, entries: list[_FakeEntry]) -> None:
            super().__init__(harness, entries)
            self._session = _ForkBranchSession(entries)  # type: ignore[assignment]

    entries = [_FakeEntry("u1", "message", role="user")]
    harness = FakeHarness()
    harness.extension_runner = ExtensionRunner(extensions=[ext])  # type: ignore[attr-defined]
    async with _harness_chrome(harness=harness) as (_r, chrome, pipe):
        runtime = _ForkBranchRuntime(harness, entries)
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/fork\n")
        await _wait(lambda: runtime.fork_calls == [("u1", "before")])
        await _wait(lambda: calls == ["status"])  # renderer dispatched on replay
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)


async def test_run_tui_registers_manifest_theme_on_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #21 themes (ADR-0184) — the startup _rebind runs _apply_ext_themes,
    registering a loaded extension's manifest theme so /settings can pick it;
    a rebind onto a harness WITHOUT the plugin un-registers it (reconcile)."""
    import textwrap

    from aelix_agent_core.contracts import parse_manifest_toml
    from aelix_agent_core.harness._extension_runner import ExtensionRunner
    from aelix_coding_agent.extensions.api import Extension
    from aelix_coding_agent.tui import themes as theme_registry

    pkg = tmp_path / "pkg"
    (pkg / "themes").mkdir(parents=True)
    (pkg / "themes" / "solar.toml").write_text(
        'name = "smoke-solar"\n[roles]\naccent = "green"\n', encoding="utf-8"
    )
    manifest = parse_manifest_toml(
        textwrap.dedent("""
            [plugin]
            id = "smoke-theme-plug"
            name = "Smoke Theme Plugin"
            version = "0.1.0"
            description = "Ships a theme"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/smoke-theme-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "x:setup"

            [activation]
            on_startup_finished = true

            [contributes]
            themes = [{ path = "themes/solar.toml" }]
        """).strip()
    )
    ext = Extension(name="smoke-theme-plug", manifest=manifest)
    ext.resolved_path = str(pkg)
    harness = FakeHarness()
    harness.extension_runner = ExtensionRunner(extensions=[ext])  # type: ignore[attr-defined]
    try:
        async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
            task = _launch(runtime, chrome)
            await _wait(lambda: theme_registry.get_theme("smoke-solar") is not None)
            assert "smoke-solar" in theme_registry.all_theme_names()
            # Reconcile: rebind onto a plugin-less harness un-registers it.
            assert runtime.rebind_cb is not None
            await runtime.rebind_cb(FakeHarness(), "reload")
            assert theme_registry.get_theme("smoke-solar") is None
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
    finally:
        theme_registry.register_themes([])  # module-global cleanup


async def test_run_tui_settings_theme_picker_selects_manifest_theme(
    tmp_path: Path,
) -> None:
    """Issue #21 (ADR-0184) review MEDIUM: the /settings → Theme picker READS
    theme_registry.all_theme_names() (shell.py) — a revert to THEMES.keys()
    would silently drop manifest themes yet pass every unit test. Drive the
    real picker and select a plugin theme: it can only be chosen if the picker
    actually enumerated it."""
    import textwrap
    from types import SimpleNamespace

    from aelix_agent_core.contracts import parse_manifest_toml
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.extensions.api import Extension
    from aelix_coding_agent.tui import themes as _themes

    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "solar.toml").write_text(
        'name = "solarized"\n[roles]\naccent = "green"\n', encoding="utf-8"
    )
    manifest = parse_manifest_toml(
        textwrap.dedent("""
            [plugin]
            id = "theme-plug"
            name = "Theme Plugin"
            version = "0.1.0"
            description = "Ships a theme"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/theme-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "theme_plug_mod:setup"

            [activation]
            on_startup_finished = true

            [contributes]
            themes = [{ path = "themes/solar.toml" }]
        """).strip()
    )
    ext = Extension(name="theme-plug", manifest=manifest)
    ext.resolved_path = str(tmp_path)
    harness = _SettingsHarness()
    harness.extension_runner = SimpleNamespace(  # type: ignore[attr-defined]
        extensions=[ext]
    )
    sm = SettingsManager.in_memory({"theme": "default"})
    try:
        async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
            task = _launch(runtime, chrome, settings_manager=sm)
            await _wait(lambda: chrome.app.is_running)
            # Wait until the plugin theme has been registered (startup _rebind).
            await _wait(lambda: _themes.get_theme("solarized") is not None)
            pipe.send_text("/settings\n")
            await _wait(lambda: chrome.is_modal_open())
            pipe.send_text("Theme\n")  # filter+select the Theme row → sub-picker
            await asyncio.sleep(0.1)  # let the theme sub-picker mount
            pipe.send_text("solarized\n")  # filter to the plugin theme + Enter
            # It can only be persisted if the picker's list included it.
            await _wait(lambda: sm.get_theme() == "solarized")
            await _esc_until_settings_closed(chrome, pipe)
            chrome.request_eof()
            chrome.exit()
            await asyncio.wait_for(task, timeout=5)
        assert sm.get_theme() == "solarized"  # picker enumerated the manifest theme
    finally:
        _themes.register_themes([])  # module-global cleanup
