"""#663 (ADR-0289) — the TUI after a ``/reload`` that failed past the teardown.

Before: the line said "nothing applied; /reload again or restart" while the
session ran on the torn-down harness — no approval dialog, no guardrail
(``.omc/specs/25-followups-evidence/i2a/``). Now the runtime puts the session
back on its previous settings ("kept the previous settings") or stops ("aelix is
stopped until /reload succeeds"), and the line says which.

The first group drives ``run_tui`` over a real :class:`AgentSessionRuntime` whose
harness carries the real Guardrail and Permission extensions and the real
built-in ``bash`` tool, with ``permission_ext`` handed to ``run_tui`` so the
approval dialog is the TUI's own; the model is scripted. The second group uses
the scripted runtimes of ``test_reload_extensions_25`` for the lines alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aelix_coding_agent.core.runnable_models as runnable_models
import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import (
    STOPPED_MESSAGE,
    RebuildFailedError,
    ReloadSeed,
    create_agent_session_runtime,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.core.extension_selection import ExtensionInventory
from aelix_coding_agent.extensions.loader import load_extensions
from aelix_coding_agent.tools import create_all_tools
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_reload_extensions_25 import (
    _control,
    _drive,
    _Registry,
    _row,
    _ScriptedRuntime,
)
from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _quit_within,
    _spy_commits,
    _wait,
)

MODEL = Model(id="fixture", provider="fixture")


class _GateRig:
    def __init__(self, tmp: Path) -> None:
        self.work = tmp / "work"
        self.work.mkdir()
        self.tmp = tmp
        self.requests = 0
        self.permission = PermissionExtension()
        self.fail: Callable[[ReloadSeed | None], bool] = lambda _seed: False
        self.runtime: Any = None

    async def _stream(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.requests += 1
        last = context.messages[-1]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        text = "".join(getattr(c, "text", "") for c in last.content)
        if getattr(last, "role", None) == "user" and text in ("run it", "guard"):
            command = (
                "echo AFTER > after.txt"
                if text == "run it"
                else "rm -rf ./no-such-dir-663 && echo GUARD > guard.txt"
            )
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[
                        ToolCallContent(
                            tool_call_id=f"call_{self.requests}",
                            tool_name="bash",
                            input={"command": command},
                        )
                    ],
                    stop_reason="tool_use",
                )
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    async def build(self, session: Session) -> AgentHarness:
        # The order ``cli/entry.py`` prepends them in: Guardrail first.
        loaded = await load_extensions([GuardrailExtension(), self.permission])
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL,
                stream_fn=self._stream,
                session=session,
                cwd=str(self.work),
                tools=[create_all_tools(str(self.work))["bash"]],
                extensions=loaded.extensions,
                runtime=loaded.runtime,
            )
        )

    async def factory(self, session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        if self.fail(reload_seed):
            raise RuntimeError("build boom")
        return await self.build(session)

    @classmethod
    async def create(cls, tmp: Path) -> _GateRig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(rig.work)))
        await session.append_message(UserMessage(content=[TextContent(text="hello")]))
        rig.runtime = await create_agent_session_runtime(
            await rig.build(session), rig.factory, repo=repo, fs=fs
        )
        return rig


def _once(seed: ReloadSeed | None) -> bool:
    return not (seed is not None and seed.restore)


def _always(_seed: ReloadSeed | None) -> bool:
    return True


def _never(_seed: ReloadSeed | None) -> bool:
    return False


def _joined(commits: list[str]) -> list[str]:
    return [" ".join(c.split()) for c in commits]


async def test_the_tui_keeps_approval_and_guardrail_then_stops_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The scripted model has no adapter; the #189 gate is not what is measured.
    monkeypatch.setattr(runnable_models, "turn_refusal", lambda _m: None)
    rig = await _GateRig.create(tmp_path)
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                rig.runtime,
                cwd=str(rig.work),
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                permission_ext=rig.permission,
            )
        )
        await _wait(lambda: chrome.app.is_running)

        # kept: the rebuild fails, the build on the previous settings works.
        rig.fail = _once
        pipe.send_text("/reload\n")
        await _wait(lambda: any("kept the previous settings" in c for c in _joined(commits)))
        assert rig.runtime.stopped is None

        # Permission asks (default mode): the TUI's approval dialog opens; Esc denies.
        pipe.send_text("run it\n")
        await _wait(chrome.is_modal_open, what="the approval dialog")
        pipe.send_text("\x1b")
        await _wait(lambda: rig.requests >= 2 and rig.runtime.harness.is_idle)
        assert not (rig.work / "after.txt").exists()

        # Guardrail refuses ``rm -rf``.
        pipe.send_text("guard\n")
        await _wait(lambda: any("[guardrail] refusing 'rm -rf'" in c for c in commits))
        await _wait(lambda: rig.runtime.harness.is_idle)
        assert not (rig.work / "guard.txt").exists()

        # stopped: both builds fail. A prompt and a ! line are refused, no request.
        rig.fail = _always
        pipe.send_text("/reload\n")
        await _wait(
            lambda: any("aelix is stopped until /reload succeeds" in c for c in _joined(commits))
        )
        requests = rig.requests
        pipe.send_text("run it\n")
        await _wait(lambda: sum(STOPPED_MESSAGE in c for c in _joined(commits)) >= 2)
        pipe.send_text("!echo BANG > bang.txt\n")
        await _wait(lambda: sum(STOPPED_MESSAGE in c for c in _joined(commits)) >= 3)
        assert rig.requests == requests
        assert not chrome.is_modal_open()
        assert not (rig.work / "after.txt").exists()
        assert not (rig.work / "bang.txt").exists()

        # recovery: the cause is fixed and /reload succeeds; the gate is back.
        rig.fail = _never
        pipe.send_text("/reload\n")
        await _wait(lambda: any("✓ /reload succeeded — aelix is running again" in c for c in commits))
        assert rig.runtime.stopped is None
        pipe.send_text("guard\n")
        await _wait(lambda: sum("[guardrail] refusing 'rm -rf'" in c for c in commits) >= 2)
        assert not (rig.work / "guard.txt").exists()

        pipe.send_text("/quit\n")
        await _quit_within(task)


# --- the lines alone, over a scripted runtime ------------------------------------------


async def test_a_reload_kept_on_its_previous_settings_says_so() -> None:
    before = ExtensionInventory((_row("weather.py", loaded=True),))
    holder: dict[str, Any] = {"inventory": before}

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime.reload_phase = "build"
        runtime._harness = FakeHarness()  # the build on the previous settings
        raise RebuildFailedError(
            operation="reload", phase="build", cause=RuntimeError("factory boom"), state="kept"
        )

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        control = _control(holder)
        commits = await _drive(runtime, chrome, pipe, control, "/reload failed")
    [entry] = [c for c in _joined(commits) if "/reload failed" in c]
    assert entry == (
        "✖ /reload failed during the rebuild: RuntimeError: factory boom — kept the "
        "previous settings; fix the cause, then /reload again"
    )
    assert "nothing applied" not in entry
    assert control.kind.notices()[0] == entry


async def test_a_stopped_reload_says_aelix_is_stopped() -> None:
    holder: dict[str, Any] = {"inventory": ExtensionInventory()}

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime.reload_phase = "settings"
        raise RebuildFailedError(
            operation="reload",
            phase="settings",
            cause=RecursionError("maximum recursion depth exceeded"),
            state="stopped",
            restore_error=RuntimeError("again"),
        )

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        commits = await _drive(runtime, chrome, pipe, _control(holder), "/reload failed")
    [entry] = [c for c in _joined(commits) if "/reload failed" in c]
    assert entry.startswith(
        "✖ /reload failed during re-reading the settings: RecursionError: maximum recursion "
        "depth exceeded; rebuilding with the previous settings failed too (RuntimeError: again)"
    )
    assert entry.endswith(STOPPED_MESSAGE)


async def test_a_stopped_runtime_refuses_prompts_and_bang_lines_but_not_commands() -> None:
    async with _harness_chrome() as (base, chrome, pipe):
        runtime = base
        runtime.stopped = STOPPED_MESSAGE  # type: ignore[attr-defined]
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello\n")
        pipe.send_text("!echo hi\n")
        pipe.send_text("!!echo hi\n")
        await _wait(lambda: sum(f"✖ {STOPPED_MESSAGE}" == " ".join(c.split()) for c in commits) >= 3)
        pipe.send_text("/reload\n")
        await _wait(lambda: runtime.reloads >= 1)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert base.harness.prompts == []


async def test_the_kill_switch_reload_says_it_failed_instead_of_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", "0")

    class _Raising(FakeHarness):
        async def reload_resources(self) -> None:
            self.reloads += 1
            raise RuntimeError("resources_discover hook handler raised: late")

    async with _harness_chrome(harness=_Raising()) as (base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                base,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: any("/reload failed" in c for c in commits))
        pipe.send_text("hello\n")
        await _wait(lambda: base.harness.prompts == [("hello", "interactive")])
        pipe.send_text("/quit\n")
        await _quit_within(task)
    [entry] = [c for c in _joined(commits) if "/reload failed" in c]
    assert entry.startswith("✖ /reload failed during re-discovering resources: RuntimeError:")
    assert "kept the previous settings" in entry


async def test_a_stop_ended_by_a_kept_reload_says_aelix_runs_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V6: the stop ended (the bash call asked again), but no line said so.

    And V7: ``/agents run`` while stopped said "Delegation is unavailable.
    Relaunch with --agents" — the stopped harness unbinds the seam.
    """

    monkeypatch.setattr(runnable_models, "turn_refusal", lambda _m: None)
    rig = await _GateRig.create(tmp_path)
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                rig.runtime,
                cwd=str(rig.work),
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                permission_ext=rig.permission,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        rig.fail = _always
        pipe.send_text("/reload\n")
        await _wait(lambda: rig.runtime.stopped is not None)

        pipe.send_text("/agents run helper do the thing\n")
        await _wait(lambda: f"✖ {STOPPED_MESSAGE}" in _joined(commits))
        assert not any("Delegation is unavailable" in c for c in _joined(commits))

        # The next rebuild fails again, the build on the previous settings works.
        rig.fail = _once
        pipe.send_text("/reload\n")
        await _wait(lambda: "✓ aelix is running again" in _joined(commits))
        assert rig.runtime.stopped is None
        lines = _joined(commits)
        kept = next(i for i, c in enumerate(lines) if "kept the previous settings" in c)
        assert lines.index("✓ aelix is running again") == kept + 1

        pipe.send_text("/quit\n")
        await _quit_within(task)
