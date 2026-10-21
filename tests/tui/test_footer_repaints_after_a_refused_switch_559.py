"""#559 — a switch the TUI reports as failed still repaints the footer.

``/thinking <level>`` did not repaint the footer's ``🧠`` level at all on
``27fa21c5`` (the picker did), and #534's ``thinking_level_select`` hook made it
repaint through the bus. Through the bus is not the same way the picker does it:
the hook is one handler among the bus's, and ``run_tui`` registers it AFTER the
handlers the harness was built with — an extension's. One of those that raises
(the default ``error_mode="throw"``) stops the bus, and it does so after
``set_thinking_level`` has replaced the level (``harness/core.py`` assigns, then
emits). The command printed ``✖ thinking switch failed`` and the footer kept the
old level, while the next request went at the new one. Measured in a pty on this
lane's ``b2af463c`` (``.omc/probes/b4-b-live/559/``): ``/thinking low`` refused
that way left ``🧠 off`` over ``reasoning_effort=low``; the picker, whose host
repaints after every pick, showed ``🧠 low``. The same shape, measured, in
``/model <id>`` and the ``/model`` picker (``✱ m1`` over a request sent as
``m2``) and the ``/settings`` thinking row (``🧠 minimal`` over ``low``); by code
read in the pick after ``/login``.

Each door now repaints on its failure path too:

* ``/thinking <level>`` repaints in a ``finally`` (rows 1 and 2 — row 2 with the
  #534 hook taken off the bus, so the command's own repaint is the only one);
* ``/model <id>`` on its refusal branch (row 3);
* the ``/model`` picker before it reports the failure (row 4);
* the ``/settings`` thinking row before it re-raises (row 5);
* the pick after ``/login`` before it reports the failure (row 6).

The harness is the real :class:`AgentHarness` with its real hook bus, and the
refusing handler is registered on it BEFORE ``run_tui`` starts, which is where an
extension's handler sits. Nothing is sent: no turn runs and every ``baseUrl`` is a
closed local port. Each row reads the footer the moment the command's failure
line reaches scrollback, never after a wait for the footer itself.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.oauth import AuthStorage
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.tui import login_wizard
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.event_waits import wait_until
from tests.tui._polling import quit_within
from tests.tui.test_run_tui_smoke import FakeRuntime, _spy_commits

_WINDOW = 128_000
_SMALL_WINDOW = 32_000
_MODELS_JSON = {
    "providers": {
        "gwa": {
            "baseUrl": "http://127.0.0.1:9/gwa/v1",
            "apiKey": "fake-gwa-559",
            "api": "openai-completions",
            "models": [
                {"id": "m1", "contextWindow": _WINDOW, "reasoning": True},
                {"id": "m2", "contextWindow": _WINDOW, "reasoning": True},
            ],
        },
    }
}
# Rows 7-9: m3's window is not m1's, so a meter label left on m1 is visible.
_TWO_WINDOWS_JSON = {
    "providers": {
        "gwa": {
            **_MODELS_JSON["providers"]["gwa"],
            "models": [
                {"id": "m1", "contextWindow": _WINDOW, "reasoning": True},
                {"id": "m3", "contextWindow": _SMALL_WINDOW, "reasoning": True},
            ],
        },
    }
}


@pytest.fixture
def _completions_adapter() -> Iterator[None]:
    """Register the openai-completions adapter, restored IN PLACE afterwards.

    ``is_runnable`` reads a process-global registry; ``/model`` and the pick
    after ``/login`` only switch to a model whose api has an adapter.
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


def _refuse_levels(*levels: str) -> Callable[..., None]:
    def _refuse(event: Any, _ctx: object = None) -> None:
        level = getattr(event, "level", None)
        if not levels or level in levels:
            raise RuntimeError(f"refuses thinking {level}")

    return _refuse


def _refuse_models(*ids: str) -> Callable[..., None]:
    def _refuse(event: Any, _ctx: object = None) -> None:
        model_id = getattr(getattr(event, "model", None), "id", None)
        if not ids or model_id in ids:
            raise RuntimeError(f"refuses model {model_id}")

    return _refuse


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
        """Type ``line``; return once a committed line contains ``until``."""

        mark = len(self.commits)
        self.pipe.send_text(f"{line}\n")
        await wait_until(
            lambda: any(until in c for c in self.commits[mark:]),
            what=f"{line!r} committing {until!r}",
            detail=lambda: repr(self.commits[mark:]),
        )


@asynccontextmanager
async def _tui(
    tmp_path: Path,
    *,
    refuse: tuple[str, Callable[..., None]] | None = None,
    launch: Model | None = None,
    settings_manager: Any = None,
    models: dict[str, Any] = _MODELS_JSON,
) -> AsyncIterator[_Bench]:
    agent = tmp_path / "agent"  # tests/tui/conftest.py points AELIX_CODING_AGENT_DIR here
    agent.mkdir(parents=True, exist_ok=True)
    models_json = agent / "models.json"
    models_json.write_text(json.dumps(models), encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    registry = ModelRegistry(auth, str(models_json))
    model = launch if launch is not None else registry.find("gwa", "m1")
    assert model is not None

    harness = AgentHarness(AgentHarnessOptions(model=model, system_prompt="BASE"))
    if refuse is not None:
        # Before ``run_tui``: where a handler of an extension the harness was
        # built with sits, ahead of the TUI's own repaint hooks.
        harness.hooks.on(refuse[0], refuse[1], source="ext-559")  # type: ignore[call-overload]
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
                auth_storage=auth,
                settings_manager=settings_manager,
            )
        )
        await wait_until(lambda: chrome.app.is_running, what="the TUI starting")
        await wait_until(lambda: f"✱ {model.id}" in bench.footer(), what="the launch footer")
        try:
            yield bench
        finally:
            pipe.send_text("/quit\n")
            assert await quit_within(task) == 0


# === 1-2. /thinking <level> ===================================================


async def test_a_typed_level_a_handler_refuses_still_repaints_the_footer(tmp_path: Path) -> None:
    """Row 1 — the level took (the next request carries it) although the
    command says the switch failed; the footer names the level in force."""

    async with _tui(tmp_path, refuse=("thinking_level_select", _refuse_levels("low"))) as bench:
        assert "🧠 off" in bench.footer(), bench.footer()
        await bench.run("/thinking low", until="thinking switch failed")

        assert bench.harness.state.thinking_level == "low"
        assert "🧠 low" in bench.footer(), bench.footer()


async def test_a_typed_level_repaints_without_the_bus_hook(tmp_path: Path) -> None:
    """Row 2 — the command repaints by itself, as the picker's host does: with
    the TUI's ``thinking_level_select`` repaint (#534) taken off the bus, the
    success path still reaches the footer."""

    async with _tui(tmp_path) as bench:
        bus: Any = bench.harness.hooks
        ours = [
            h
            for h in bus._handlers.get("thinking_level_select", [])
            if bus._sources.get(("thinking_level_select", id(h))) == "tui-footer"
        ]
        assert ours, "run_tui registers its thinking repaint at startup"
        for handler in ours:
            bus._handlers["thinking_level_select"].remove(handler)

        await bench.run("/thinking high", until="thinking → high")

        assert "🧠 high" in bench.footer(), bench.footer()


# === 3-4. /model ===============================================================


async def test_a_typed_model_a_handler_refuses_still_repaints_the_footer(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 3 — ``set_model`` replaced the model before the handler raised."""

    async with _tui(tmp_path, refuse=("model_select", _refuse_models("m2"))) as bench:
        await bench.run("/model gwa/m2", until="model switch failed")

        assert bench.harness.state.model.id == "m2"
        assert "✱ m2" in bench.footer(), bench.footer()
        assert "✱ m1" not in bench.footer(), bench.footer()


async def test_a_picked_model_a_handler_refuses_still_repaints_the_footer(
    tmp_path: Path, _completions_adapter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 4 — the ``/model`` picker, reached through ``run_tui``."""

    async def _select(_self: object, _title: str, options: list[str], **_kw: object) -> str:
        return next(o for o in options if o.endswith("] m2"))

    monkeypatch.setattr(AelixTUIContext, "select", _select)
    async with _tui(tmp_path, refuse=("model_select", _refuse_models("m2"))) as bench:
        await bench.run("/model", until="model switch failed")

        assert bench.harness.state.model.id == "m2"
        assert "✱ m2" in bench.footer(), bench.footer()


# === 5. /settings → Thinking level ============================================


async def test_a_settings_cycle_a_handler_refuses_still_repaints_the_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 5 — the row cycles through ``cycle_thinking_level``, which sets the
    next level and lets the handler's raise out; ``/settings`` reports it."""

    picks = {"n": 0}

    async def _select(_self: object, _title: str, options: list[str], **_kw: object) -> Any:
        picks["n"] += 1
        if picks["n"] > 1:
            return None
        return next(o for o in options if o.startswith("Thinking level"))

    monkeypatch.setattr(AelixTUIContext, "select", _select)
    settings = SettingsManager.in_memory({})
    async with _tui(
        tmp_path,
        refuse=("thinking_level_select", _refuse_levels()),
        settings_manager=settings,
    ) as bench:
        assert "🧠 off" in bench.footer(), bench.footer()
        await bench.run("/settings", until="settings failed")

        level = bench.harness.state.thinking_level
        assert level not in (None, "off"), level
        assert f"🧠 {level}" in bench.footer(), bench.footer()


# === 6. the pick after /login =================================================


async def test_a_post_login_pick_a_handler_refuses_still_repaints_the_footer(
    tmp_path: Path, _completions_adapter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 6 — the launch model cannot run, so the login follow-through picks
    one (``shell.py``) and ``set_model`` replaces it before the refusal."""

    async def _fake_login(**_kwargs: Any) -> None:
        return None  # the gwa key is already in models.json

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)
    dead = Model(id="dead", name="dead", api="no-such-api-559", provider="nowhere")
    async with _tui(tmp_path, refuse=("model_select", _refuse_models()), launch=dead) as bench:
        await bench.run("/login", until="selecting its default model failed")

        picked = bench.harness.state.model.id
        assert picked in ("m1", "m2"), picked
        assert f"✱ {picked}" in bench.footer(), bench.footer()
        assert "✱ dead" not in bench.footer(), bench.footer()


# === 7-9. the context meter follows the model in force ========================
#
# Review round 2 (cross, category 1): the repaint rows 3, 4 and 6 pin recomposes
# the footer from its cached segments, and the meter's label (``◔ 0% · 0/128K``)
# is one of them. Only the TUI's ``model_select`` hook re-derived that label from
# the new model, and the refusing handler stops the bus before it, so the footer
# showed the refused-but-in-force model's id beside the previous model's window
# (measured in a pty: ``✱ m2 · … · ◔ 0% · 0/128K`` on a 32K model). Each refusal
# path now repaints through the helper the hook uses, which re-derives the label
# from the model in force. At idle that is a stats read scheduled off the frame
# (the hook's own way), so these rows wait for the label, bounded.


def _meter(footer: str) -> str:
    return footer.rsplit("◔", 1)[-1] if "◔" in footer else ""


async def _meter_shows(bench: _Bench, window: str) -> None:
    await wait_until(
        lambda: f"/{window}" in _meter(bench.footer()),
        what=f"the meter naming the {window} window",
        detail=bench.footer,
    )


async def test_a_typed_model_a_handler_refuses_gets_its_own_window_in_the_meter(
    tmp_path: Path, _completions_adapter: None
) -> None:
    """Row 7 — ``/model gwa/m3`` refused after ``set_model`` assigned it."""

    async with _tui(
        tmp_path, refuse=("model_select", _refuse_models("m3")), models=_TWO_WINDOWS_JSON
    ) as bench:
        await _meter_shows(bench, "128K")
        await bench.run("/model gwa/m3", until="model switch failed")

        assert bench.harness.state.model.id == "m3"
        await _meter_shows(bench, "32K")
        assert "✱ m3" in bench.footer(), bench.footer()


async def test_a_picked_model_a_handler_refuses_gets_its_own_window_in_the_meter(
    tmp_path: Path, _completions_adapter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 8 — the ``/model`` picker's failure path."""

    async def _select(_self: object, _title: str, options: list[str], **_kw: object) -> str:
        return next(o for o in options if o.endswith("] m3"))

    monkeypatch.setattr(AelixTUIContext, "select", _select)
    async with _tui(
        tmp_path, refuse=("model_select", _refuse_models("m3")), models=_TWO_WINDOWS_JSON
    ) as bench:
        await _meter_shows(bench, "128K")
        await bench.run("/model", until="model switch failed")

        assert bench.harness.state.model.id == "m3"
        await _meter_shows(bench, "32K")


async def test_a_post_login_pick_a_handler_refuses_gets_its_own_window_in_the_meter(
    tmp_path: Path, _completions_adapter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 9 — the pick after ``/login``; the launch model has no window, so
    the meter had no label until the picked model's is derived."""

    async def _fake_login(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)
    dead = Model(id="dead", name="dead", api="no-such-api-559", provider="nowhere")
    async with _tui(
        tmp_path,
        refuse=("model_select", _refuse_models()),
        launch=dead,
        models=_TWO_WINDOWS_JSON,
    ) as bench:
        await bench.run("/login", until="selecting its default model failed")

        picked = bench.harness.state.model
        assert picked.id in ("m1", "m3"), picked.id
        await _meter_shows(bench, "128K" if picked.id == "m1" else "32K")
