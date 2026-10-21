"""#534 — a model or thinking-level change repaints the footer when it happens.

The footer is a cached string, recomposed only when something calls
``AelixTUIContext._refresh_footer``. ``/model`` called it itself; ``/agents use``
did not, and relied on the ``model_select`` hook ``run_tui`` registers for the
context meter (#249). That hook repaints through ``set_context_label``, which
returns on an UNCHANGED label — so a switch between two models with the same
context window (``gwa/m1`` → ``gwa/m2``, or ``gwa/m1`` → ``gwb/n1``) left
``✱ m1`` on screen while the next prompt went to the new model, until that turn
ended. Measured in a pty on the parent commit (``.omc/probes/b4-b-live/534/``):
the same for an extension's ``ctx.set_model``, and the ``🧠`` segment the same
way after ``/thinking <level>``, a profile's ``thinking:`` and an extension's
``ctx.set_thinking_level`` — none of those repainted at all.

Three pieces, each with its row here:

* the ``model_select`` hook repaints the footer whatever the meter did
  (rows 1, 1b and 2; row 4, the issue's own path, is repainted by the third
  piece too, so it goes red only with both gone);
* a ``thinking_level_select`` hook does the same for the level (rows 3, 5, 6,
  and 8 / 8b for its registration);
* ``/agents use`` repaints once more after a use that FAILED: the rollback in
  ``AgentProfileService.use`` writes the model back without an event, so with
  either hook alone the abandoned profile's id would stay on screen beside the
  red line (row 7 — green on the parent commit because nothing repainted at
  all, red with either hook and without this piece).

The harness is the REAL :class:`AgentHarness` with its real hook bus — its
``set_model`` / ``set_thinking_level`` assign the state and then emit, which is
the ordering the hooks depend on — and ``/agents use`` runs the real
:class:`AgentProfileService` over a ``models.json`` registry. Nothing is sent: no
turn runs, and the ``baseUrl`` is a closed local port. The assertions read the
footer right after the change (an awaited setter, or the command's own answer
reaching scrollback), never after a wait for the footer itself: a repaint that
only comes later would also satisfy a wait, and "later" is the defect.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.skills import load_skills
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.agents.service import AgentProfileService
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.event_waits import wait_until
from tests.tui._polling import quit_within
from tests.tui.test_run_tui_smoke import FakeRuntime, _message_end, _spy_commits

# Two providers, three models, ONE context window: the label the meter paints is
# the same string before and after every switch below, which is the condition
# the defect needed. (``gws/s1``, at another window, is only row 9's.) ``reasoning`` so ``thinking: high`` is a level these models
# list (``AgentProfileService.use`` skips one they do not).
_WINDOW = 128_000
_MODELS_JSON = {
    "providers": {
        "gwa": {
            "baseUrl": "http://127.0.0.1:9/gwa/v1",
            "apiKey": "fake-gwa-534",
            "api": "openai-completions",
            "models": [
                {"id": "m1", "contextWindow": _WINDOW, "reasoning": True},
                {"id": "m2", "contextWindow": _WINDOW, "reasoning": True},
            ],
        },
        "gwb": {
            "baseUrl": "http://127.0.0.1:9/gwb/v1",
            "apiKey": "fake-gwb-534",
            "api": "openai-completions",
            "models": [{"id": "n1", "contextWindow": _WINDOW, "reasoning": True}],
        },
        "gws": {
            "baseUrl": "http://127.0.0.1:9/gws/v1",
            "apiKey": "fake-gws-534",
            "api": "openai-completions",
            "models": [{"id": "s1", "contextWindow": 32_000, "reasoning": True}],
        },
    }
}

_PROFILES = {
    "a2": "model: gwa/m2\n",
    "b1": "model: gwb/n1\n",
    "hi": "thinking: high\n",
    "a2hi": "model: gwa/m2\nthinking: high\n",
    # Its ``thinking: low`` is refused by a handler (row 7), AFTER its model was set.
    "a2low": "model: gwa/m2\nthinking: low\n",
    # Row 9: the same refusal onto a model with another window.
    "s1low": "model: gws/s1\nthinking: low\n",
}


@pytest.fixture
def _completions_adapter() -> Iterator[None]:
    """Register the openai-completions adapter, restored IN PLACE afterwards.

    ``is_runnable`` reads a process-global registry, so whether ``/agents use``
    accepts a ``models.json`` model would otherwise depend on which test ran
    first (the ``_adapters_registered`` fixture in ``test_agents_command.py``
    registers only anthropic's).
    """

    from aelix_ai import api_registry
    from aelix_ai.providers import openai_completions

    saved = dict(api_registry._PROVIDERS)
    try:
        openai_completions.register_all()
        yield
    finally:
        api_registry._PROVIDERS.clear()
        api_registry._PROVIDERS.update(saved)


class _Bench:
    def __init__(self, runtime: FakeRuntime, chrome: AelixChrome, pipe: PipeInput) -> None:
        self.runtime = runtime
        self.chrome = chrome
        self.pipe = pipe
        self.commits = _spy_commits(chrome)

    @property
    def harness(self) -> AgentHarness:
        return self.runtime.harness  # type: ignore[return-value]

    def footer(self) -> str:
        return self.chrome._footer_line

    async def run(self, line: str, *, until: str) -> None:
        """Type ``line``; return once a committed line contains ``until``.

        The command commits that line AFTER it has changed the model and run
        whatever repaint it runs, so the footer read next is the one the user
        sees the moment the command's answer appears.
        """

        mark = len(self.commits)
        self.pipe.send_text(f"{line}\n")
        await wait_until(
            lambda: any(until in c for c in self.commits[mark:]),
            what=f"{line!r} committing {until!r}",
            detail=lambda: repr(self.commits[mark:]),
        )


@asynccontextmanager
async def _tui(tmp_path: Path, *, with_service: bool = True) -> AsyncIterator[_Bench]:
    agent = tmp_path / "agent"  # tests/tui/conftest.py points AELIX_CODING_AGENT_DIR here
    (agent / "agents").mkdir(parents=True, exist_ok=True)
    models_json = agent / "models.json"
    models_json.write_text(json.dumps(_MODELS_JSON), encoding="utf-8")
    for name, extra in _PROFILES.items():
        (agent / "agents" / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: d\n{extra}inherit_extensions: true\n---\n{name}\n",
            encoding="utf-8",
        )
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    registry = ModelRegistry(auth, str(models_json))
    launch = registry.find("gwa", "m1")
    assert launch is not None and launch.context_window == _WINDOW, launch

    harness = AgentHarness(AgentHarnessOptions(model=launch, system_prompt="BASE"))
    parsed = Args()
    service = AgentProfileService(
        cwd=str(cwd),
        project_trusted=True,
        parsed=parsed,
        baseline=Args(),
        skills_holder={"result": load_skills([])},
        agent_dir=str(agent),
        model_registry=registry,
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        bench = _Bench(FakeRuntime(harness), chrome, pipe)  # type: ignore[arg-type]
        task = asyncio.ensure_future(
            run_tui(
                bench.runtime,  # type: ignore[arg-type]
                cwd=str(cwd),
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=registry,
                agent_service=service if with_service else None,
            )
        )
        await wait_until(lambda: chrome.app.is_running, what="the TUI starting")
        await wait_until(lambda: "✱ m1" in bench.footer(), what="the launch footer")
        # The startup stats read paints the meter asynchronously; let it land so a
        # late label repaint cannot be what makes a row below pass.
        await wait_until(lambda: "◔" in bench.footer(), what="the startup meter")
        try:
            yield bench
        finally:
            pipe.send_text("/quit\n")
            assert await quit_within(task) == 0


# === 1-2. the model_select funnel ============================================


async def test_a_same_window_set_model_repaints_the_footer_id(tmp_path: Path) -> None:
    """Row 1 — the funnel itself: an extension's ``ctx.set_model`` is a direct
    ``harness.set_model`` (``_action_set_model``), so this IS that path, and the
    one ``/agents use`` takes. Awaited, so when it returns its hooks have run."""

    async with _tui(tmp_path) as bench:
        label_before = bench.footer().split("◔", 1)[1]
        await bench.harness.set_model(await _registry_model(tmp_path, "gwa", "m2"))

        assert "✱ m2" in bench.footer(), bench.footer()
        assert "✱ m1" not in bench.footer(), bench.footer()
        # The condition the defect needed: the meter's label did not move.
        assert bench.footer().split("◔", 1)[1] == label_before


async def test_a_same_window_set_model_mid_turn_repaints_the_footer_id(tmp_path: Path) -> None:
    """Row 1b — the meter hook's OTHER branch: while a live ``message_end``
    figure is held it repaints synchronously through ``set_context_label``
    instead of scheduling a stats read, and that call short-circuits on the
    same label just the same. A repaint written into the idle branch only
    would pass row 1 and fail here."""

    async with _tui(tmp_path) as bench:
        await bench.harness._emit_to_subscribers(_message_end(96_900))
        await wait_until(lambda: "96.9K/128K" in bench.footer(), what="the live figure")
        label_before = bench.footer().split("◔", 1)[1]

        await bench.harness.set_model(await _registry_model(tmp_path, "gwa", "m2"))

        assert "✱ m2" in bench.footer(), bench.footer()
        assert bench.footer().split("◔", 1)[1] == label_before


async def test_a_same_window_set_model_across_providers_repaints_too(tmp_path: Path) -> None:
    """Row 2 — the issue's guess, measured: a provider change is not what made
    the footer move before, a window change was. ``gwa/m1`` → ``gwb/n1`` at
    one window kept ``✱ m1`` on the parent commit."""

    async with _tui(tmp_path) as bench:
        await bench.harness.set_model(await _registry_model(tmp_path, "gwb", "n1"))

        assert "✱ n1" in bench.footer(), bench.footer()


# === 3. the thinking_level_select funnel =====================================


async def test_set_thinking_level_repaints_the_footer_level(tmp_path: Path) -> None:
    """Row 3 — an extension's ``ctx.set_thinking_level`` schedules exactly this
    setter (``_action_set_thinking_level``)."""

    async with _tui(tmp_path) as bench:
        assert "🧠 off" in bench.footer(), bench.footer()
        await bench.harness.set_thinking_level("high")

        assert "🧠 high" in bench.footer(), bench.footer()


# === 4-7. the commands =======================================================


async def test_agents_use_onto_a_same_window_model_repaints_at_once(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 4 — the issue: the green status line and the footer agree."""

    async with _tui(tmp_path) as bench:
        await bench.run("/agents use a2", until="Agent profile: a2")

        assert bench.harness.state.model.id == "m2"
        assert "✱ m2" in bench.footer(), bench.footer()


async def test_agents_use_with_a_thinking_level_repaints_the_level(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 5 — a profile's ``thinking:`` alone, and with a model: the level is
    set AFTER the model (``AgentProfileService.use``), so a repaint on the model
    alone would still show the old level."""

    async with _tui(tmp_path) as bench:
        await bench.run("/agents use hi", until="Agent profile: hi")
        assert "✱ m1" in bench.footer() and "🧠 high" in bench.footer(), bench.footer()

    async with _tui(tmp_path / "both") as bench:
        await bench.run("/agents use a2hi", until="Agent profile: a2hi")
        assert "✱ m2" in bench.footer() and "🧠 high" in bench.footer(), bench.footer()


async def test_thinking_with_a_level_repaints_the_level(tmp_path: Path) -> None:
    """Row 6 — ``/thinking high`` printed ``thinking → high`` above ``🧠 off``.
    No agent service: this row is about the ``/thinking`` command alone."""

    async with _tui(tmp_path, with_service=False) as bench:
        await bench.run("/thinking high", until="thinking → high")

        assert "🧠 high" in bench.footer(), bench.footer()


async def test_a_rolled_back_agents_use_leaves_the_model_it_stayed_on(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 7 — the repaint must not outlive a rollback.

    The profile's model is set (and ``model_select`` repaints the footer onto
    it), then a ``thinking_level_select`` handler refuses its level — after the
    TUI's own handler on that bus, registered earlier, has repainted it again
    with the profile's model — and ``AgentProfileService.use`` restores the
    model by assignment, with no event. The command's repaint after the use is
    what puts ``✱ m1`` back; without it the footer names the model the red line
    says was not adopted.
    """

    async with _tui(tmp_path) as bench:

        def _refuse_low(event: Any, _ctx: object = None) -> None:
            if getattr(event, "level", None) == "low":
                raise RuntimeError("refuses low")

        bench.harness.hooks.on("thinking_level_select", _refuse_low, source="test-534")
        await bench.run("/agents use a2low", until="agents use failed")

        assert bench.harness.state.model.id == "m1", "the use rolled back"
        assert "✱ m1" in bench.footer(), bench.footer()
        assert "✱ m2" not in bench.footer(), bench.footer()


async def test_a_use_rolled_back_mid_turn_puts_the_meter_back_on_the_kept_model(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 9 (#559 review round 2) — the #249 rollback gap, mid-turn.

    While a live ``message_end`` figure is held the ``model_select`` hook paints
    the meter synchronously against the new model's window, so the profile's
    ``gws/s1`` (32K) moved it to ``96.9K/32K``. The rollback restores ``m1``
    with no event, and ``/agents use``'s repaint used to recompose that cached
    label. It now goes through the helper that re-derives the meter from the
    model in force, so the meter is back on m1's 128K when the red line lands.
    """

    async with _tui(tmp_path) as bench:
        await bench.harness._emit_to_subscribers(_message_end(96_900))
        await wait_until(lambda: "96.9K/128K" in bench.footer(), what="the live figure")

        def _refuse_low(event: Any, _ctx: object = None) -> None:
            if getattr(event, "level", None) == "low":
                raise RuntimeError("refuses low")

        bench.harness.hooks.on("thinking_level_select", _refuse_low, source="test-559")
        await bench.run("/agents use s1low", until="agents use failed")

        assert bench.harness.state.model.id == "m1", "the use rolled back"
        assert "96.9K/128K" in bench.footer(), bench.footer()
        assert "/32K" not in bench.footer(), bench.footer()


# === 8. the new registration =================================================


async def test_a_raising_footer_repaint_cannot_fail_a_thinking_switch(tmp_path: Path) -> None:
    """Row 8 — ``set_thinking_level`` raises ``AgentHarnessError`` for a handler
    error AFTER it assigned the level, so a footer bug must not surface as a
    failed switch (the ``error_mode="continue"`` the meter's hook carries, #249).
    The raise is counted, so the row cannot pass because nothing listened."""

    async with _tui(tmp_path) as bench:
        exploded: list[int] = []

        def _boom(_self: object) -> None:
            exploded.append(1)
            raise RuntimeError("footer exploded")

        # #497: a scoped patch, not ``monkeypatch.undo()``. The undo also took
        # back tests/tui/conftest.py's AELIX_CODING_AGENT_DIR while this TUI
        # still ran, and its update check wrote ``update_check.json`` into the
        # home's ``.aelix/agent`` (measured with a stand-in home).
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(AelixTUIContext, "_refresh_footer", _boom)
            await bench.harness.set_thinking_level("high")  # must not raise

        assert exploded, "the thinking_level_select repaint never ran"
        assert bench.harness.state.thinking_level == "high"


async def test_the_thinking_hook_follows_a_swap_and_is_released_at_teardown(
    tmp_path: Path,
) -> None:
    """Row 8b — the same lifecycle as the meter's ``model_select`` hook: a
    ``/resume``, ``/new``, ``/fork`` or ``/reload`` builds a new bus, and the
    repaint has to move with it; teardown lets go of it."""

    async with _tui(tmp_path) as bench:
        first = bench.harness
        second = AgentHarness(
            AgentHarnessOptions(
                model=await _registry_model(tmp_path, "gwa", "m1"), system_prompt="B2"
            )
        )
        assert _tui_thinking_handlers(first), "registered at startup"
        bench.runtime._harness = second  # type: ignore[assignment]
        assert bench.runtime.rebind_cb is not None
        await bench.runtime.rebind_cb(second, "resume")

        await second.set_thinking_level("high")
        assert "🧠 high" in bench.footer(), bench.footer()
        assert not _tui_thinking_handlers(first), "the old bus still holds the repaint"

    assert not _tui_thinking_handlers(second), "teardown did not release the repaint"


# --- helpers ------------------------------------------------------------------


async def _registry_model(tmp_path: Path, provider: str, model_id: str) -> Any:
    auth = AuthStorage(path=tmp_path / "auth-lookup.json")
    await auth.load()
    found = ModelRegistry(auth, str(tmp_path / "agent" / "models.json")).find(provider, model_id)
    assert found is not None and found.context_window == _WINDOW, found
    return found


def _tui_thinking_handlers(harness: AgentHarness) -> list[Any]:
    """The ``thinking_level_select`` handlers ``run_tui`` registered on this bus.

    Read off the bus's own bookkeeping (``HookBus.on`` keys a handler's source
    by ``(event, id(handler))``): there is no public listing, and a count of all
    handlers would also count an extension's.
    """

    bus: Any = harness.hooks
    return [
        h
        for h in bus._handlers.get("thinking_level_select", [])
        if bus._sources.get(("thinking_level_select", id(h))) == "tui-footer"
    ]
