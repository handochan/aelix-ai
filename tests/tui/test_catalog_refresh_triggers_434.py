"""#434 (ADR-0264 §4.6) — the triggers that refresh the model catalogs, and what each user sees.

* ``/model``: the cached list at once; when the run lands, the list re-renders
  with the new rows and one status line; closing the picker leaves the run.
* ``/scoped-models``: the cached list, and one status line naming how many
  models the refreshed list adds (its rows stay: #153's save rules read them).
* ``/model <term>`` with no match: "Refreshing model catalogs…", then a second match.
* ``aelix update --models``: forced, each source's outcome on stderr, exit 1 on
  a failure; ``aelix update`` alone exits 2 and names ``--models``.
* ``--list-models``: the cache, and a stderr line naming the cached revision.

The pickers run with injected ``select``/``multiselect`` (as their other
tests do); the CLI paths run the real ``_async_main`` against the loopback
catalog of :mod:`tests.hosted_catalog_fixtures_434`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai import models as catalog
from aelix_ai.models_store import ModelsRefreshResult, SourceOutcome
from aelix_ai.streaming import Model
from aelix_coding_agent.tui.model_picker import refresh_status, run_model_picker
from aelix_coding_agent.tui.scoped_models import run_scoped_models

from tests.hosted_catalog_fixtures_434 import (  # noqa: F401 - the fixture
    INDEX_PATH,
    NEW_OR_ID,
    CatalogServer,
    Response,
    catalog_server,
    new_row,
    revision,
)


class _Registry:
    def __init__(self, models: list[Model]) -> None:
        self.models = models

    def get_available(self) -> list[Model]:
        return list(self.models)


class _Harness:
    current_model: Model | None = None

    async def set_model(self, model: Model) -> None:
        self.current_model = model


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


async def test_the_model_picker_re_renders_when_the_refresh_lands() -> None:
    registry = _Registry([Model(id="a", provider="p")])
    landed = asyncio.Event()
    updates: list[tuple[list[str], str | None]] = []

    async def refresh() -> ModelsRefreshResult:
        registry.models.append(Model(id="new", provider="p"))
        return ModelsRefreshResult(outcomes=(SourceOutcome("aelix-hosted", "published"),))

    async def select(title: str, options: list[str], *, detail: Any, on_open: Any) -> str | None:
        assert len(options) == 1  # the cached list at once

        def update(new_options: list[str], *, status: str | None = None, identity: Any = None) -> None:
            updates.append((list(new_options), status))
            landed.set()

        on_open(update)
        await landed.wait()
        assert detail(1)  # ``detail`` reads the new rows: index 1 exists now
        assert "new" in updates[-1][0][1]
        return updates[-1][0][1]

    harness = _Harness()
    await run_model_picker(
        registry=registry, harness=harness, select=select, commit=lambda _r: None, refresh=refresh
    )

    assert [len(options) for options, _ in updates] == [2]
    assert updates[0][1] == "Model catalogs refreshed"
    assert harness.current_model is not None and harness.current_model.id == "new"


async def test_closing_the_model_picker_leaves_the_refresh() -> None:
    cancelled = asyncio.Event()

    async def refresh() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def select(title: str, options: list[str], *, detail: Any, on_open: Any) -> str | None:
        on_open(lambda *_a, **_k: None)
        await asyncio.sleep(0)
        return None  # Esc

    await run_model_picker(
        registry=_Registry([Model(id="a", provider="p")]),
        harness=_Harness(),
        select=select,
        commit=lambda _r: None,
        refresh=refresh,
    )
    await asyncio.wait_for(cancelled.wait(), 2)


@pytest.mark.parametrize(
    ("result", "line"),
    [
        (None, "Model catalogs: refresh timed out; showing cached models"),
        (
            ModelsRefreshResult(errors=("x",), outcomes=(SourceOutcome("aelix-hosted", "failed"),)),
            "Model catalogs: could not refresh; showing cached models",
        ),
        (ModelsRefreshResult(outcomes=(SourceOutcome("aelix-hosted", "skipped"),)), "Model catalogs refreshed"),
    ],
)
def test_the_pickers_one_status_line(result: Any, line: str) -> None:
    assert refresh_status(result) == line


class _Settings:
    def get_enabled_models(self) -> list[str] | None:
        return None

    def set_enabled_models(self, _value: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


async def test_scoped_models_says_what_the_refresh_added_and_keeps_its_rows() -> None:
    registry = _Registry([Model(id="a", provider="p")])
    rendered: list[int] = []
    seen: dict[str, Any] = {}

    async def refresh() -> ModelsRefreshResult:
        registry.models.append(Model(id="new", provider="p"))
        return ModelsRefreshResult(outcomes=(SourceOutcome("aelix-hosted", "published"),))

    async def multiselect(title: str, options: list[Any], *, selected: set[str], preview: Any) -> Any:
        seen["rows"] = len(options)
        for _ in range(50):
            if rendered:
                break
            await asyncio.sleep(0.01)
        seen["preview"] = preview(set(selected), {})
        return None

    await run_scoped_models(
        registry=registry,
        settings_manager=_Settings(),
        multiselect=multiselect,
        commit=lambda _r: None,
        refresh=refresh,
        request_render=lambda: rendered.append(1),
    )

    assert seen["rows"] == 1
    assert seen["preview"][-1] == (
        "Model catalogs refreshed (1 new — reopen /scoped-models to see them)"
    )


async def test_model_with_no_match_refreshes_and_matches_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent import model_refresh
    from aelix_coding_agent.cli import model_switch
    from aelix_coding_agent.core import model_argument, runnable_models
    from aelix_coding_agent.core.model_argument import ArgumentResolution

    target = Model(id="new", provider="p", api="openai-completions")
    calls: list[str] = []
    state = {"refreshed": False}

    async def resolve(argument: str, **_k: Any) -> ArgumentResolution:
        calls.append(argument)
        if state["refreshed"]:
            return ArgumentResolution(model=target)
        return ArgumentResolution(error="no model 'new' is available", no_match=True)

    async def refresh(_registry: Any, *, trigger: str, **_k: Any) -> None:
        assert trigger == "model-command"
        state["refreshed"] = True

    class Registry:
        def refresh_models(self) -> None:  # the refreshable shape
            return None

    monkeypatch.setattr(model_argument, "resolve_model_argument", resolve)
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", "http://127.0.0.1:9")  # the source is on
    monkeypatch.setattr(model_refresh, "refresh_catalogs", refresh)
    monkeypatch.setattr(runnable_models, "is_runnable", lambda _m: True)
    warned: list[str] = []
    harness = _Harness()

    switched = await model_switch.switch_model_argument(
        "new", harness=harness, model_registry=Registry(), settings_manager=None, warn=warned.append
    )

    assert switched.refusal is None and harness.current_model is target
    assert warned == ["Refreshing model catalogs…"]
    assert calls == ["new", "new"]


# ── the CLI ──────────────────────────────────────────────────────────


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.env_sandbox import sandbox_home

    sandbox_home(monkeypatch, tmp_path / "home")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.chdir(tmp_path)
    return agent


async def test_update_models_forces_a_check_and_reports_each_source(
    agent: Path, catalog_server: CatalogServer, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import entry

    raw = revision(add={"openrouter": {NEW_OR_ID: new_row()}})
    catalog_server.publish(raw)

    assert await entry._async_main(["update", "--models"]) == 0
    err = capsys.readouterr().err
    assert f"model catalog aelix-hosted: published ({hc.revision_name(raw)[:19]})" in err
    assert "1 model(s) this release does not ship" in err

    assert await entry._async_main(["update", "--models"]) == 0  # forced past the 4 hours
    assert "model catalog aelix-hosted: unchanged" in capsys.readouterr().err
    assert len(catalog_server.requests(INDEX_PATH)) == 2


async def test_update_models_exits_1_on_a_failure_and_offline(
    agent: Path, catalog_server: CatalogServer, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import entry

    catalog_server.routes[INDEX_PATH] = Response(500)
    assert await entry._async_main(["update", "--models"]) == 1
    assert "model catalog aelix-hosted: failed: index: HTTP 500" in capsys.readouterr().err

    monkeypatch.setenv("AELIX_OFFLINE", "1")
    assert await entry._async_main(["update", "--models"]) == 1
    assert "offline" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["update"], ["update", "--model"], ["update", "x"]])
async def test_update_without_models_exits_2_and_names_it(
    agent: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_coding_agent.cli import entry

    assert await entry._async_main(argv) == 2
    assert "aelix update --models" in capsys.readouterr().err


async def test_list_models_reads_the_cache_and_names_its_revision(
    agent: Path, catalog_server: CatalogServer, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import entry

    raw = revision(add={"openrouter": {NEW_OR_ID: new_row()}})
    catalog_server.publish(raw)
    assert await entry._async_main(["update", "--models"]) == 0
    capsys.readouterr()
    catalog.install_overlay(None)
    requests = len(catalog_server.log)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")

    assert await entry._async_main(["--list-models", "acme"]) == 0

    out, err = capsys.readouterr()
    assert NEW_OR_ID in out
    assert f"published revision {hc.revision_name(raw)[:19]} (cached" in err
    assert len(catalog_server.log) == requests  # no network


async def test_a_backfilled_id_refreshes_first_and_takes_the_published_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/model openrouter/<new id>``: #136 would backfill a custom id; a refresh may publish it."""

    from aelix_coding_agent import model_refresh
    from aelix_coding_agent.cli import model_switch
    from aelix_coding_agent.core import model_argument, runnable_models
    from aelix_coding_agent.core.model_argument import ArgumentResolution

    published = Model(id="acme/new", provider="openrouter", api="openai-completions")
    state = {"refreshed": False}

    async def resolve(argument: str, **_k: Any) -> ArgumentResolution:
        if state["refreshed"]:
            return ArgumentResolution(model=published)
        return ArgumentResolution(model=Model(id="acme/new", provider="openrouter"), caution="not in catalog")

    async def refresh(_registry: Any, **_k: Any) -> None:
        state["refreshed"] = True

    class Registry:
        def refresh_models(self) -> None:
            return None

    monkeypatch.setattr(model_argument, "resolve_model_argument", resolve)
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", "http://127.0.0.1:9")  # the source is on
    monkeypatch.setattr(model_refresh, "refresh_catalogs", refresh)
    monkeypatch.setattr(runnable_models, "is_runnable", lambda _m: True)
    harness = _Harness()

    switched = await model_switch.switch_model_argument(
        "openrouter/acme/new", harness=harness, model_registry=Registry(), settings_manager=None, warn=lambda _m: None
    )

    assert harness.current_model is published and switched.caution is None


@pytest.mark.parametrize(
    ("downs", "shrink", "picked"), [(1, False, "b"), (2, False, "c"), (2, True, "a")]
)
async def test_a_refresh_landing_under_the_cursor_keeps_the_row_and_never_breaks_enter(
    downs: int, shrink: bool, picked: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real ``AelixTUIContext.select`` and its key bindings (Codex review 1).

    The cursor is on ``b`` (or ``c``) when the refresh lands; the new list drops
    ``a`` (renumbering every label) or keeps only ``a``. Enter takes the row the
    cursor stood on when it is still listed, else the first one, and never
    raises: the picker no longer shares its rows with the caller.
    """

    from types import SimpleNamespace

    from aelix_coding_agent.tui import context as context_module

    rows = [Model(id=x, provider="openrouter", api="openai-completions") for x in "abc"]
    registry = SimpleNamespace(get_available=lambda: list(rows))
    harness = _Harness()
    go = asyncio.Event()
    failures: list[str] = []

    async def refresh() -> ModelsRefreshResult:
        await go.wait()
        rows[:] = rows[:1] if shrink else rows[1:]
        return ModelsRefreshResult()

    async def modal(_chrome: Any, build: Any) -> Any:
        future = asyncio.get_running_loop().create_future()
        window = build(future)
        bindings = window.content.key_bindings

        def key(name: str) -> None:
            next(b.handler for b in bindings.bindings if str(b.keys[0]) == name)(None)

        for _ in range(downs):
            key("Keys.Down")
        go.set()
        for _ in range(20):
            await asyncio.sleep(0)
        try:
            key("Keys.ControlM")
        except Exception as exc:  # noqa: BLE001 - recorded, the assertion below reads it
            failures.append(f"{type(exc).__name__}: {exc}")
            future.set_result(None)
        return await future

    ctx = context_module.AelixTUIContext.__new__(context_module.AelixTUIContext)
    ctx.chrome = SimpleNamespace(invalidate=lambda: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(context_module, "show_modal", modal)

    async def select(*args: Any, **kwargs: Any) -> str | None:
        return await ctx.select(*args, own=True, **kwargs)

    await run_model_picker(
        registry=registry, harness=harness, select=select, commit=lambda _r: None, refresh=refresh
    )

    assert failures == []
    assert harness.current_model is not None and harness.current_model.id == picked


async def test_with_the_catalog_off_a_model_miss_says_nothing_about_refreshing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AELIX_MODEL_CATALOG_URL=`` turns the source off: no "Refreshing model catalogs…" (review 1)."""

    from aelix_coding_agent.cli import model_switch
    from aelix_coding_agent.core import model_argument
    from aelix_coding_agent.core.model_argument import ArgumentResolution

    async def resolve(argument: str, **_k: Any) -> ArgumentResolution:
        return ArgumentResolution(error="no model 'x' is available", no_match=True)

    class Registry:
        def refresh_models(self) -> None:
            return None

    monkeypatch.setattr(model_argument, "resolve_model_argument", resolve)
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", "")
    warned: list[str] = []

    switched = await model_switch.switch_model_argument(
        "x", harness=_Harness(), model_registry=Registry(), settings_manager=None, warn=warned.append
    )

    assert switched.refusal is not None and warned == []


async def test_update_takes_offline_after_the_command_too(
    agent: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_coding_agent.cli import entry

    assert await entry._async_main(["update", "--models", "--offline"]) == 1
    assert "offline" in capsys.readouterr().err


async def test_update_offline_alone_prints_the_usage(agent: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from aelix_coding_agent.cli import entry

    assert await entry._async_main(["update", "--offline"]) == 2
    assert "usage: aelix update --models" in capsys.readouterr().err
