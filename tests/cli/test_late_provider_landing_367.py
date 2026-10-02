"""#367 round 3 — the late-provider rule is where the launch inputs LAND, judged after every ``session_start``.

Round 2 of #367 keyed the hold by the exact refused ``(parsed.model,
parsed.provider)`` pair and judged the launch by the model the harness held
after ``session_start``. Two reviews found what that let through:

* verify round 2 — ``/agents use --none`` (or of a profile naming no route)
  resets ``parsed`` to the CLI + settings baseline, a DIFFERENT pair from the
  launch's when the launch came through ``--agent``, and that pair re-resolved
  onto the late provider: the next prompt went there (shapes a, b), also from a
  launch that was never held (shape c);
* Codex — a ``session_start`` hook's ``trigger_turn`` sent a turn on the launch
  route (guard 2: OpenRouter, the user's own key, and with ``--api-key`` the
  TYPED key) before the refusal said "No prompt was sent" (1); a hook's
  ``set_model`` in the launch's ``session_start`` made the refusal pass (2) and
  in a rebuild's released the hold (3); and a late decision that drops the
  settings ``defaultProvider`` tie-break passed every #367 test (5).

The rule now (ADR-0250 §2.11): a provider is LATE when an extension registered
it in ``session_start`` and it was not registered when the launch route was
chosen; every implicit re-resolution of the launch inputs that lands on one is
held, an explicit pick switches; the launch's own decision re-resolves the
inputs, not the hook's model; it is re-applied after every rebuild's
``session_start``; and while ``session_start`` runs a launch route no
registered provider claimed is a pending placeholder, with ``--api-key``
attached only after the decision.

Verify round 3 of 5a1330b5 (B1, the owner-directed pi reading): the launch
refusal is for a PENDING launch only. A launch that resolved to a registered
provider stays on it — its ``session_start`` may run turns there, as in pi —
even when its inputs would now land on a late provider; the rebuilds after it
re-resolve and are held where they land late. B2 and B3 pin the factory's
rebuild hold and the post-decision ``--api-key`` attach, which no test held.

Verify round 4 of 76055424 (B1): the pending placeholder covered turns on the
launch model only — a handler that called ``set_model`` and then triggered a
turn sent it on the model it set, in a pending launch's ``session_start`` and in
a held rebuild's, and print/json then said "No prompt was sent.". While those
``session_start`` handlers run, no turn of any kind starts now (the turn gate,
``AgentHarness.hold_turns``), whatever model is current. B2 pins four seams
whose removal passed every test: the rebuild's after-``session_start`` record,
``/agents use``'s second ask, the end-of-build snapshot, and the restore that
keeps a handler's own model.

Verify round 5 of a543754c: a ``model_select`` handler that answered the
launch hold's placeholder with its own ``set_model`` released the hold (B1),
and the rebuild's check after its re-hold was unpinned (B2) — every path that
applies a hold now applies it through ``LateRouteHold.apply`` and checks the
state after it. And a handler that awaited its own gated ``trigger_turn`` hung
the launch: that trigger is now a refused turn, which ends.

Verify round 6 of 343e75cd (B1): ``/agents use`` applied its hold with turns
open, so a ``model_select`` handler that answered the placeholder with
``set_model`` onto the late provider and a ``trigger_turn`` sent that prompt
before ``apply`` put the placeholder back; fix7 found the same on the settle of
a rebuild whose ``session_start`` first registered the provider. Every
application of a hold now runs under the turn gate (``LateRouteHold.apply``).

Verify round 8 of 29499345: the late text said "registered while
session_start handlers ran", false when the registration came after the
handler returned - the turn a fire-and-forget trigger starts, a task the
handler or ``setup()`` spawned - though each is inside the window and late (B1;
it now says "while a session was starting"); and the route line's
``(provider, id)`` condition was pinned by neither half alone (B2).

These drive the real ``_async_main`` (the mode entry stubbed) with every HTTP
request recorded by an ``httpx.MockTransport`` — fake keys, an isolated agent
dir, nothing leaves the process.
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import HOLD_GATE

from tests.cli.test_late_provider_refused_367 import _REFUSAL, _openrouter, _stub_print
from tests.cli.test_launch_route_344 import (
    _EXT,
    _SESSION_START_EXTENSION,
    _FakePipedStdin,
    _FakeTTYStdin,
)
from tests.env_sandbox import sandbox_home

_OTHER = "http://127.0.0.1:9/other/v1"
#: #367 verify round 7 — the seconds a row that waits on a turn's end gives it.
#: A refused turn ends at once; three sabotages (the harness lets a gated
#: ``trigger_turn`` through, drops it, or queues it) leave it never ending, and
#: the rows that catch them fail by this bound instead of hanging the suite.
_BOUND = 10


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated agent dir and cwd, no provider key in the environment (as in
    ``test_late_provider_refused_367.py``), and ``sessext.py`` beside them."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    (tmp_path / "sessext.py").write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    return tmp_path


def _extension(
    *,
    trigger: bool | str = False,
    set_model: str | None = None,
    keyless: bool = False,
    setup_other: bool = False,
    second: bool = False,
    ids: tuple[str, ...] = ("m1",),
    set_to: str = "sessext",
    register: str = "always",
    other_key: bool = True,
    record: bool = False,
    model_select: str | None = None,
) -> str:
    """``sessext`` registered in ``session_start`` (and what the row adds).

    ``set_model``: ``"startup"`` calls ``set_model(sessext/<first id>)`` in the
    launch's ``session_start``, ``"rebuild"`` in every later one. ``trigger``
    sends a message with ``trigger_turn=True`` there and waits for the turn
    (``"rebuild"``: in every ``session_start`` but the launch's).
    ``setup_other`` registers a keyed ``other`` serving the same ids in
    ``setup()`` (keyless with ``other_key=False``); ``second`` a keyed
    ``other`` in ``session_start`` too. ``set_to``: the provider ``set_model`` names (``"other"`` needs
    ``setup_other``). ``register="rebuild"``: ``sessext`` is registered only in
    a rebuild's ``session_start``, never the launch's. ``record`` prints the
    model each ``session_start`` handler enters with (``HOOK_MODEL ...``).
    ``trigger="await-end"`` triggers the turn and waits for that turn's
    ``agent_end`` (an ``asyncio.Event`` an ``agent_end`` handler sets), with no
    timeout, printing ``WAIT done`` when it returns; ``"nowait"`` triggers it
    and returns at once. ``model_select`` adds a
    ``model_select`` handler that answers a placeholder (``api='unknown'``)
    with ``set_model(sessext/<first id>)``: ``"always"``, or ``"rebuild"`` only
    once a rebuild's ``session_start`` has run; ``"trigger"`` also triggers a
    turn there and yields for 50 ms before returning.
    """

    key = "None" if keyless else '"ext-fake-literal"'
    first = ids[0]
    hook = [
        f'aelix.register_provider("sessext", ProviderConfigInput(name="probe", api_key={key}, '
        f'models={{m: Model(id=m, provider="sessext", api="openai-completions", '
        f"base_url={_EXT!r}) for m in {ids!r}}}))",
    ]
    if second:
        hook.append(
            'aelix.register_provider("other", ProviderConfigInput(name="other", '
            'api_key="other-fake-literal", models={m: Model(id=m, provider="other", '
            f'api="openai-completions", base_url={_OTHER!r}) for m in {ids!r}}}))'
        )
    if register == "rebuild":
        hook = ['if event.reason != "startup":', "    " + hook[0], *hook[1:]]
    if record:
        hook.insert(
            0,
            'print("HOOK_MODEL", event.reason, ctx.model.provider, ctx.model.id, '
            "ctx.model.api, flush=True)",
        )
    if set_model is not None:
        base = _OTHER if set_to == "other" else _EXT
        call = (
            f"await aelix.set_model(Model(id={first!r}, provider={set_to!r}, "
            f'api="openai-completions", base_url={base!r}))'
        )
        if set_model == "rebuild":
            hook.append('if event.reason != "startup":')
            hook.append("    " + call)
        else:
            hook.append(call)
    if trigger == "nowait":
        hook.append(
            "aelix.send_message(UserMessage(content=[TextContent(text='hook-prompt')]), "
            "trigger_turn=True)"
        )
    elif trigger == "await-end":
        hook.extend(
            [
                "aelix.send_message(UserMessage(content=[TextContent(text='hook-prompt')]), "
                "trigger_turn=True)",
                "await DONE.wait()",
                'print("WAIT done", flush=True)',
            ]
        )
    elif trigger:
        turn = [
            "aelix.send_message(UserMessage(content=[TextContent(text='hook-prompt')]), "
            "trigger_turn=True)",
            # Wait for that turn to finish (or to be refused), as Codex's probe
            # did with a fixed sleep: the request, if any, is made in the handler.
            "await asyncio.sleep(0.05)",
            "for _ in range(300):",
            "    if ctx.is_idle():",
            "        break",
            "    await asyncio.sleep(0.01)",
        ]
        if trigger == "rebuild":
            hook.append('if event.reason != "startup":')
            turn = ["    " + line for line in turn]
        hook.extend(turn)
    setup = "pass"
    if setup_other:
        other_key = '"other-fake-literal"' if other_key else "None"
        setup = (
            'aelix.register_provider("other", ProviderConfigInput(name="other", '
            f'api_key={other_key}, models={{m: Model(id=m, provider="other", '
            f'api="openai-completions", base_url={_OTHER!r}) for m in {ids!r}}}))'
        )
    if model_select == "rebuild":
        hook.insert(0, 'if event.reason != "startup":')
        hook.insert(1, "    REBUILT.append(event.reason)")
    extra = [
        "async def _on_end(event, ctx):",
        "    DONE.set()",
        'aelix.on("agent_end", _on_end)',
    ]
    if model_select is not None:
        gate = "REBUILT and " if model_select == "rebuild" else ""
        extra += [
            "async def _on_select(event, ctx):",
            f'    if {gate}event.model.api == "unknown":',
            f"        await aelix.set_model(Model(id={first!r}, provider='sessext', "
            f"api='openai-completions', base_url={_EXT!r}))",
        ]
        if model_select == "trigger":
            extra += [
                "        aelix.send_message(UserMessage(content=[TextContent(text='ms-prompt')]), "
                "trigger_turn=True)",
                "        await asyncio.sleep(0.05)",
            ]
        extra.append('aelix.on("model_select", _on_select)')
    body = "\n".join("        " + line for line in hook)
    tail = "\n".join("    " + line for line in extra)
    return textwrap.dedent(
        """
        import asyncio
        from aelix_ai.messages import TextContent, UserMessage
        from aelix_ai.streaming import Model
        from aelix_coding_agent.model_registry import ProviderConfigInput

        DONE = asyncio.Event()
        REBUILT = []

        def setup(aelix):
            {setup}
            async def _on_start(event, ctx):
        {body}
            aelix.on("session_start", _on_start)
        {tail}
        """
    ).format(setup=setup, body=body, tail=tail)


class _Wire(list[tuple[str, str]]):
    """``(url, authorization)`` per request; ``users`` holds each request's user
    texts, ``models`` its body's ``model``."""

    users: list[list[str]]
    models: list[Any]


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    """Every HTTP request any client makes: ``(url, authorization)``. Nothing is sent."""

    import httpx
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    # ``is_runnable`` and the turn path need the api adapters (``main_sync``
    # registers them; ``_async_main`` does not).
    register_providers()
    sent = _Wire()
    sent.users = []
    sent.models = []

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append((str(request.url), request.headers.get("authorization") or ""))
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        messages = body.get("messages", []) if isinstance(body, dict) else []
        sent.models.append(body.get("model") if isinstance(body, dict) else None)
        sent.users.append(
            [
                m["content"]
                if isinstance(m.get("content"), str)
                else " ".join(c.get("text", "") for c in m.get("content") or [])
                for m in messages
                if m.get("role") == "user"
            ]
        )
        return httpx.Response(400, json={"error": {"message": "recorded", "code": 400}})

    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(_record)
        kwargs["mounts"] = {}
        kwargs["trust_env"] = False
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    monkeypatch.setenv("AELIX_MAX_RETRIES", "0")
    return sent


@pytest.fixture
def attached(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The providers ``--api-key`` was attached to (the key itself is never kept)."""

    from aelix_ai.oauth import AuthStorage

    providers: list[str] = []
    real = AuthStorage.set_runtime_api_key

    def _spy(self: Any, provider: str, api_key: str) -> None:
        providers.append(provider)
        real(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    return providers


def _write(env: Path, name: str, source: str) -> str:
    path = env / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def _route(model: Any) -> tuple[str, str, str]:
    return (model.provider, model.id, model.api)


_HELD = ("sessext", "m1", "unknown")
_SWITCHED = ("sessext", "m1", "openai-completions")


# === Codex 1 — nothing is sent while ``session_start`` runs ======================


@pytest.mark.parametrize("mode", ["print", "json"])
@pytest.mark.parametrize("typed", [False, True], ids=["own-key", "api-key"])
async def test_a_turn_a_session_start_hook_triggers_sends_nothing(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
    mode: str,
    typed: bool,
) -> None:
    """Codex finding 1. The hook registers ``sessext`` and triggers a turn. On
    9e233be9 the harness was on the guard-2 route during ``session_start``, so
    that turn went to OpenRouter with the user's key — with ``--api-key`` the
    TYPED key, attached before ``session_start`` — and the refusal after it
    said "No prompt was sent". The launch route is pending now: the hook's turn
    is refused like any unresolvable model, and the key is attached nowhere."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "trigger.py", _extension(trigger=True))
    turns = _stub_print(monkeypatch)
    if mode == "json":
        from aelix_coding_agent import modes

        async def _no_json(runtime: Any, **kwargs: Any) -> int:
            turns.append(runtime.harness.current_model)
            return 0

        monkeypatch.setattr(modes, "run_json_mode", _no_json, raising=False)
    flags = ["--api-key", "typed-fake-literal"] if typed else []
    mode_flags = ["--mode", "json"] if mode == "json" else []
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "sessext/m1", *flags, *mode_flags, "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert wire == []
    assert attached == []
    assert (code, turns) == (1, [])
    assert "Error: " + _REFUSAL.format(subject="sessext/m1") + " No prompt was sent." in err


async def test_a_launch_that_is_not_late_gets_its_guard_two_route_back(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[tuple[str, str]],
) -> None:
    """D4's other half. ``--model newlab/x`` goes to OpenRouter as written
    (guard 2) and ``sessext`` does not claim it: after ``session_start`` the
    pending placeholder is replaced by the launch route itself, so the first
    turn has the route it had on 9e233be9 — while the hook's own triggered turn,
    run against the placeholder, was not sent (9e233be9: sent to OpenRouter)."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "trigger.py", _extension(trigger=True))
    seen: list[Any] = []
    during: list[tuple[str, str, str]] = []
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        during.append(_route(harness.current_model))
        return await real_create(harness, factory, **kwargs)

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    from aelix_coding_agent import modes

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        seen.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "newlab/x", "-p", "hi"]
    )
    assert code == 0
    assert during == [("openrouter", "newlab/x", "unknown")]
    assert [(m.provider, m.id, m.api) for m in seen] == [
        ("openrouter", "newlab/x", "openai-completions")
    ]
    assert wire == []


# === Codex 2 — a hook's set_model is not evidence ================================


@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_a_hook_set_model_in_the_launch_session_start_does_not_pass_the_refusal(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    openrouter: str,
) -> None:
    """Codex finding 2. The hook registers ``sessext`` and sets the harness to
    it. 9e233be9 judged the post-hook model — runnable, not OpenRouter — and
    let print run on ``sessext``. The decision re-resolves the launch inputs,
    which land on ``sessext``: refused."""

    _openrouter(monkeypatch, env, openrouter)
    ext = _write(env, "set.py", _extension(set_model="startup"))
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns, wire) == (1, [], [])
    assert "Error: " + _REFUSAL.format(subject="sessext/m1") + " No prompt was sent." in err


async def test_a_hook_set_model_in_the_launch_session_start_is_held_interactively(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive half of Codex 2: the hold overwrites the hook's model."""

    import aelix_coding_agent.tui as tui_pkg

    ext = _write(env, "set.py", _extension(set_model="startup"))
    handed: list[Any] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        handed.append(_route(runtime.harness.current_model))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "sessext/m1"])
    assert (code, handed) == (0, [_HELD])


# === Codex 3 — a rebuild's session_start cannot release the hold ===============


async def test_a_hook_set_model_in_a_rebuild_session_start_does_not_release_the_hold(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex finding 3. The hook sets the model to ``sessext`` in every
    ``session_start`` but the launch's. On 9e233be9 the factory put the
    placeholder on the rebuilt harness and the hook then replaced it: ``/new``
    and ``/reload`` each left the session on ``sessext``. The runtime's
    after-``session_start`` callback re-applies the hold before any turn."""

    import aelix_coding_agent.tui as tui_pkg

    ext = _write(env, "setrebuild.py", _extension(set_model="rebuild"))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen["launch"] = _route(runtime.harness.current_model)
        await runtime.new_session()
        seen["new"] = _route(runtime.harness.current_model)
        await runtime.reload()
        seen["reload"] = _route(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["-e", ext, "--approve", "--model", "sessext/m1"])
    assert code == 0
    assert seen == {"launch": _HELD, "new": _HELD, "reload": _HELD}


# === Verify round 3 (B1–B3) and Codex 5 — a registered launch stays; rebuilds hold ==


async def _interactive(
    env: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], steps: list[str]
) -> list[tuple[str, tuple[str, str, str]]]:
    """Launch interactive (the TUI stubbed), then ``/new`` / ``/reload`` per step."""

    import aelix_coding_agent.tui as tui_pkg

    seen: list[tuple[str, tuple[str, str, str]]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen.append(("launch", _route(runtime.harness.current_model)))
        for step in steps:
            await (runtime.new_session() if step == "new" else runtime.reload())
            seen.append((step, _route(runtime.harness.current_model)))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(argv)
    assert code == 0
    return seen


_ON_OTHER = ("other", "m1", "openai-completions")


@pytest.mark.parametrize("mode", ["print", "json"])
@pytest.mark.parametrize("typed", [False, True], ids=["own-key", "api-key"])
@pytest.mark.parametrize("shape", ["default-names-sessext", "both-keyed"])
async def test_a_launch_on_a_registered_provider_stays_on_it(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
    mode: str,
    typed: bool,
    shape: str,
) -> None:
    """#367 verify round 3, B1 (the decision: pi's direction). ``other``
    (``setup()``, keyed) serves ``m1``, so ``--model m1`` resolves to it at
    launch; ``sessext`` (``session_start``) serves ``m1`` too and, with settings
    ``defaultProvider`` naming it (or both keyed, which makes the re-resolve
    ambiguous), the inputs would land on it now. pi resolves the launch model
    before ``session_start`` and keeps it. 5a1330b5 refused this launch after
    its ``session_start`` had already run the hook's triggered turn on
    ``other`` — "No prompt was sent." was false. Now the hook's turn and the
    prompt both go to ``other``, with the user's ``other`` key or the typed
    ``--api-key``, and nothing is refused (the print gate judges the launch
    route the turn takes, not the re-resolve that lands on ``sessext``)."""

    keyless = shape == "default-names-sessext"
    ext = _write(env, "otherlate.py", _extension(trigger=True, keyless=keyless, setup_other=True))
    if keyless:
        (env / "agent" / "settings.json").write_text(
            json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
        )
    flags = ["--api-key", "typed-fake-literal"] if typed else []
    mode_flags = ["--mode", "json"] if mode == "json" else []
    await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "m1", *flags, *mode_flags, "-p", "hi"]
    )
    err = capsys.readouterr().err
    auth = "Bearer typed-fake-literal" if typed else "Bearer other-fake-literal"
    assert wire == [(_OTHER + "/chat/completions", auth)] * 2
    assert attached == (["other"] if typed else [])
    assert "session_start handler" not in err
    assert "No prompt was sent" not in err


@pytest.mark.parametrize(
    ("default", "after"), [("sessext", _HELD), (None, _ON_OTHER)], ids=["default-sessext", "none"]
)
async def test_a_rebuild_after_a_registered_launch_is_held_where_it_lands_late(
    env: Path, monkeypatch: pytest.MonkeyPatch, default: str | None, after: tuple[str, str, str]
) -> None:
    """B1's other half, and Codex finding 5 (the settings ``defaultProvider``
    tie-break is part of the inputs). The launch stays on ``other`` (above); a
    rebuild re-resolves ``--model m1`` with the tie-break — where pi would keep
    the session model — and that lands on ``sessext``, which nobody picked:
    ``/new`` and ``/reload`` are held. Without a ``defaultProvider`` the
    re-resolve lands on ``other`` again and nothing is held. A late decision
    that drops the tie-break holds neither row."""

    ext = _write(env, "otherlate.py", _extension(keyless=True, setup_other=True))
    settings = {"defaultProvider": default} if default else {}
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    seen = await _interactive(
        env, monkeypatch, ["-e", ext, "--approve", "--model", "m1"], ["new", "reload"]
    )
    assert seen == [("launch", _ON_OTHER), ("new", after), ("reload", after)]


@pytest.mark.parametrize("launch", ["pending", "registered"])
async def test_a_rebuild_whose_session_start_triggers_a_turn_sends_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: list[tuple[str, str]], launch: str
) -> None:
    """#367 verify round 3, B2. The hook triggers a turn in every
    ``session_start`` but the launch's. A rebuild re-resolves the launch inputs
    onto ``sessext``; the harness factory starts it on the hold's placeholder,
    so the turn the rebuild's ``session_start`` triggers is refused before a
    request. Without that (only the after-``session_start`` re-hold), the
    triggered turn reached ``sessext`` ("/new: requests=1; /reload: requests=2"
    to /late/v1 in the verifier's pty probe) and every test stayed green.
    ``pending``: ``--model sessext/m1`` (held from launch); ``registered``: the
    launch on ``other`` above, ``defaultProvider`` naming ``sessext``."""

    if launch == "pending":
        _openrouter(monkeypatch, env, "exported")
        ext = _write(env, "trigrebuild.py", _extension(trigger="rebuild"))
        argv, first = ["--model", "sessext/m1"], _HELD
    else:
        ext = _write(env, "trigrebuild.py", _extension(trigger="rebuild", setup_other=True))
        (env / "agent" / "settings.json").write_text(
            json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
        )
        argv, first = ["--model", "m1"], _ON_OTHER
    seen = await _interactive(env, monkeypatch, ["-e", ext, "--approve", *argv], ["new", "reload"])
    assert seen == [("launch", first), ("new", _HELD), ("reload", _HELD)]
    assert wire == []


async def test_a_guard_two_launch_that_is_not_late_sends_with_the_typed_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[tuple[str, str]],
    attached: list[str],
) -> None:
    """#367 verify round 3, B3. ``--model newlab/x --api-key K`` is guard 2
    (OpenRouter as written), pending while ``session_start`` runs, so the key
    waits for the late decision (D4). ``sessext`` does not claim
    ``newlab/x``: the launch route is restored and the TYPED key attached to
    it, and the prompt goes to OpenRouter with it. Without that attach the key
    was silently unused — the user's own OpenRouter key went instead
    ("Bearer typed-g2-fake" -> "Bearer or-own-fake" in the verifier's probe)
    — and every test stayed green. (Guard 2 needs that exported key: with
    none, ``newlab/x`` is "not found" at launch.)"""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "plain.py", _extension())
    await entry_mod._async_main(
        [
            "--no-session",
            "-e",
            ext,
            "--model",
            "newlab/x",
            "--api-key",
            "typed-fake-literal",
            "-p",
            "hi",
        ]
    )
    assert attached == ["openrouter"]
    assert [auth for _url, auth in wire] == ["Bearer typed-fake-literal"]
    assert "/chat/completions" in wire[0][0]


@pytest.mark.parametrize("default", [True, False], ids=["default-sessext", "no-default"])
async def test_ids_two_late_providers_serve_are_refused(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    default: bool,
) -> None:
    """Codex's counterexample: ``sessext`` and ``other`` both registered in
    ``session_start``, both serving the raw id ``lab/m1``; ``--model lab/m1``
    went to OpenRouter at launch (guard 2). With settings ``defaultProvider``
    the inputs land on ``sessext``; without it the re-resolve is ambiguous
    between two late providers — refused too, naming both (on 9e233be9 the
    print gate failed it as ambiguous after the guard-2 note)."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "twolate.py", _extension(second=True, ids=("lab/m1",)))
    if default:
        (env / "agent" / "settings.json").write_text(
            json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
        )
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "lab/m1", "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, turns, wire) == (1, [], [])
    if default:
        expected = _REFUSAL.format(subject="lab/m1")
    else:
        expected = (
            "The launch model \"lab/m1\" matches the providers 'other' and 'sessext', which "
            "an extension registered while a session was starting (for example in a "
            "session_start handler), after the launch model was chosen."
        )
    assert "Error: " + expected in err


# === verify round 2 — the landing rule, not the pair ===========================

_PROFILES = {
    "plainprof": "---\nname: plainprof\ndescription: p\n---\nplain\n",
    "lateprof": "---\nname: lateprof\ndescription: p\nmodel: sessext/m1\n---\nlate\n",
    "provonly": "---\nname: provonly\ndescription: p\nprovider: sessext\n---\nprov\n",
    "orprof": "---\nname: orprof\ndescription: p\nprovider: openrouter\n---\nor\n",
}


async def _session(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    steps: list[str],
    *,
    ext: str | None = None,
) -> list[tuple[str, tuple[str, str, str]]]:
    """Launch interactive (the TUI stubbed), then ``/agents use <step>`` or ``/new``."""

    import aelix_coding_agent.tui as tui_pkg

    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    for name, body in _PROFILES.items():
        (agents / f"{name}.md").write_text(body, encoding="utf-8")
    seen: list[tuple[str, tuple[str, str, str]]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen.append(("launch", _route(runtime.harness.current_model)))
        for step in steps:
            if step == "new":
                await runtime.new_session()
            else:
                await kwargs["agent_service"].use(
                    None if step == "--none" else step, harness=runtime.harness
                )
            seen.append((step, _route(runtime.harness.current_model)))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["-e", ext or str(env / "sessext.py"), "--approve", *argv])
    assert code == 0
    return seen


@pytest.mark.parametrize("step", ["--none", "plainprof"])
@pytest.mark.parametrize("openrouter", ["exported", "none"])
@pytest.mark.parametrize(
    ("settings", "argv"),
    [
        ({"defaultProvider": "sessext", "defaultModel": "m1"}, ["--agent", "lateprof"]),
        ({}, ["--agent", "provonly", "--model", "m1"]),
    ],
    ids=["a-settings-pair+agent-lateprof", "b-agent-provonly+model-m1"],
)
async def test_agents_use_of_a_baseline_that_lands_on_the_provider_keeps_the_hold(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
    argv: list[str],
    openrouter: str,
    step: str,
) -> None:
    """Verify round 2, shapes (a) and (b). The launch pair comes through
    ``--agent``; ``/agents use --none`` (or of a profile naming no route) resets
    ``parsed`` to the CLI + settings baseline — (a) ``("m1", "sessext")``, (b)
    ``("m1", None)`` — not the held pair, so 9e233be9 re-resolved it onto
    ``sessext`` and the next prompt went there (pty: "EXT POST
    /sess/v1/chat/completions"). Where the baseline LANDS is what holds it now;
    ``/new`` after it stays held."""

    _openrouter(monkeypatch, env, openrouter)
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    seen = await _session(env, monkeypatch, argv, [step, "new"])
    assert seen == [("launch", _HELD), (step, _HELD), ("new", _HELD)]


@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_a_launch_that_was_never_held_is_held_when_agents_use_lands_on_the_provider(
    env: Path, monkeypatch: pytest.MonkeyPatch, openrouter: str
) -> None:
    """Verify round 2, shape (c). ``--agent orprof`` (``provider: openrouter``)
    with ``--model sessext/m1`` is an explicit OpenRouter route — not late, not
    held. ``/agents use --none`` drops the profile; the baseline ``--model
    sessext/m1`` re-resolved lands on ``sessext``. 9e233be9 had no hold for that
    pair and switched there; the landing rule holds it."""

    _openrouter(monkeypatch, env, openrouter)
    seen = await _session(
        env, monkeypatch, ["--agent", "orprof", "--model", "sessext/m1"], ["--none", "new"]
    )
    assert seen[0] == ("launch", ("openrouter", "sessext/m1", "openai-completions"))
    assert seen[1:] == [("--none", ("sessext", "m1", "unknown")), ("new", _HELD)]


async def test_a_profile_naming_only_the_provider_is_an_explicit_pick(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1's explicit side: ``provider:`` alone names the route. From a held
    ``--model sessext/m1``, ``/agents use provonly`` switches to ``sessext``,
    and the rebuild after it keeps the profile's choice (the rule is marked
    explicit) — until an implicit ``/agents use --none`` holds again."""

    _openrouter(monkeypatch, env, "exported")
    seen = await _session(
        env, monkeypatch, ["--model", "sessext/m1"], ["provonly", "new", "--none", "new"]
    )
    assert seen == [
        ("launch", _HELD),
        ("provonly", _SWITCHED),
        ("new", _SWITCHED),
        ("--none", _HELD),
        ("new", _HELD),
    ]


# === Verify round 4 — the turn gate, and four mutations no row held ===========


def _stub_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> list[Any]:
    """print (``_stub_print``) or json stubbed: the models a turn would have used."""

    turns = _stub_print(monkeypatch)
    if mode == "json":
        from aelix_coding_agent import modes

        async def _no_json(runtime: Any, **kwargs: Any) -> int:
            turns.append(runtime.harness.current_model)
            return 0

        monkeypatch.setattr(modes, "run_json_mode", _no_json, raising=False)
    return turns


@pytest.mark.parametrize("set_to", ["sessext", "other"])
@pytest.mark.parametrize("openrouter", ["exported", "none"])
@pytest.mark.parametrize("mode", ["print", "json"])
async def test_a_hook_that_sets_a_model_and_triggers_a_turn_in_a_pending_launch_sends_nothing(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    mode: str,
    openrouter: str,
    set_to: str,
) -> None:
    """#367 verify round 4, B1. The launch ``--model sessext/m1`` is pending
    (guard 2 with an OpenRouter key, the unresolved placeholder without one).
    The hook registers ``sessext``, calls ``set_model`` — onto ``sessext``
    itself or onto ``other`` (``setup()``, keyed) — and then triggers a turn.
    D4's placeholder covered turns on the launch model only: on 76055424 that
    turn went to the model the hook set ("/late/v1 ... Bearer late-fake",
    "/other/v1 ... Bearer other-fake" in the verifier's x-set-trigger-* and
    x-setother-trigger-print rows) and print/json then said "No prompt was
    sent.". The turn gate holds every turn while that ``session_start`` runs,
    whatever model is current: nothing is sent and the sentence is true."""

    _openrouter(monkeypatch, env, openrouter)
    ext = _write(
        env,
        "settrigger.py",
        _extension(set_model="startup", trigger=True, set_to=set_to, setup_other=set_to == "other"),
    )
    turns = _stub_mode(monkeypatch, mode)
    mode_flags = ["--mode", "json"] if mode == "json" else []
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "sessext/m1", *mode_flags, "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert wire == []
    assert (code, turns) == (1, [])
    assert "Error: " + _REFUSAL.format(subject="sessext/m1") + " No prompt was sent." in err


async def test_a_hook_that_sets_a_model_and_triggers_a_turn_sends_nothing_with_a_typed_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    attached: list[str],
) -> None:
    """B1 with ``--api-key`` (x-set-trigger-typed): the hook's turn is held, and
    the key, which waits for the late decision, is attached nowhere."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "settrigger.py", _extension(set_model="startup", trigger=True))
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        [
            "--no-session",
            "-e",
            ext,
            "--model",
            "sessext/m1",
            "--api-key",
            "typed-fake-literal",
            "-p",
            "hi",
        ]
    )
    err = capsys.readouterr().err
    assert (wire, attached, code, turns) == ([], [], 1, [])
    assert "No prompt was sent." in err


@pytest.mark.parametrize("set_to", ["sessext", "other"])
async def test_a_hook_that_sets_a_model_and_triggers_a_turn_in_a_pending_launch_is_held(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire, set_to: str
) -> None:
    """B1, interactive (the TUI stubbed; RPC takes the same launch block): the
    hook's turn is not sent, and the session starts on the hold's placeholder,
    which overwrites the model the hook set."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    ext = _write(
        env,
        "settrigger.py",
        _extension(set_model="startup", trigger=True, set_to=set_to, setup_other=set_to == "other"),
    )
    handed: list[Any] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        handed.append(_route(runtime.harness.current_model))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "sessext/m1"])
    assert (code, handed, wire) == (0, [_HELD], [])


@pytest.mark.parametrize("set_to", ["sessext", "other"])
@pytest.mark.parametrize("launch", ["pending", "registered"])
async def test_a_hook_that_sets_a_model_and_triggers_a_turn_in_a_held_rebuild_sends_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire, launch: str, set_to: str
) -> None:
    """B1's rebuild half. In every ``session_start`` but the launch's the hook
    calls ``set_model`` and triggers a turn. The factory starts the rebuild on
    the hold's placeholder and ``_settle_late_route`` re-holds after that
    ``session_start`` — but the turn ran in between, on the model the hook set
    (76055424, the pty probe h1-held-new-set-late: "/new:
    /late/v1/chat/completions m1 Bearer late-fake"; -set-other: /other/v1;
    /reload likewise). The factory now holds the rebuild's turns until the
    callback has re-applied the hold."""

    if launch == "pending":
        _openrouter(monkeypatch, env, "exported")
        argv, first = ["--model", "sessext/m1"], _HELD
    else:
        (env / "agent" / "settings.json").write_text(
            json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
        )
        argv, first = ["--model", "m1"], _ON_OTHER
    ext = _write(
        env,
        "setrebuild.py",
        _extension(
            set_model="rebuild",
            trigger="rebuild",
            set_to=set_to,
            setup_other=launch == "registered" or set_to == "other",
        ),
    )
    seen = await _interactive(env, monkeypatch, ["-e", ext, "--approve", *argv], ["new", "reload"])
    assert seen == [("launch", first), ("new", _HELD), ("reload", _HELD)]
    assert wire == []


async def test_the_turn_gate_is_lifted_once_the_hold_is_re_applied(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire
) -> None:
    """The gate is not a second hold: after the held ``/new`` above, an explicit
    pick (what ``/model`` does) runs the next prompt, and the message of the
    hook's ``trigger_turn`` while the gate was up rides with it — since verify
    round 5 as the refused turn's prompt in the conversation, not as a queued
    message. A gate left up would refuse that prompt as well."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_ai.streaming import Model

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "setrebuild.py", _extension(set_model="rebuild", trigger="rebuild"))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        await runtime.new_session()
        seen["new"] = _route(runtime.harness.current_model)
        seen["gate"] = runtime.harness.turns_held
        await runtime.harness.set_model(
            Model(id="m1", provider="sessext", api="openai-completions", base_url=_EXT)
        )
        await runtime.harness.prompt("hi")
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["-e", ext, "--approve", "--model", "sessext/m1"])
    assert (code, seen["new"], seen["gate"]) == (0, _HELD, None)
    assert wire == [(_EXT + "/chat/completions", "Bearer ext-fake-literal")]
    assert wire.users == [["hook-prompt", "hi"]]


@pytest.mark.parametrize("trigger", [False, True], ids=["set-model", "set-model+trigger"])
async def test_a_hook_set_model_in_a_launch_that_is_not_late_stands(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire, trigger: bool
) -> None:
    """D4's restore keeps a handler's own model (verify round 4, B2(d): the
    restore without its ``harness.state.model is pending_model`` test passed
    every #367 test, and in the probe v3-set-notlate the prompt went to
    OpenRouter as written instead of "/late/v1"). ``--model newlab/x`` is guard
    2, pending; the hook registers ``sessext`` and sets the model to it;
    ``newlab/x`` does not land there, so nothing is late and the hook's choice
    stands. With ``trigger`` the hook also triggers a turn: on 76055424 it went
    to ``sessext`` during ``session_start`` (two requests); under the gate it
    is a refused turn (verify round 5), so the one request carries the hook's
    message — that turn's prompt, in the conversation — and the prompt."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "setnotlate.py", _extension(set_model="startup", trigger=trigger))
    await entry_mod._async_main(["--no-session", "-e", ext, "--model", "newlab/x", "-p", "hi"])
    assert wire == [(_EXT + "/chat/completions", "Bearer ext-fake-literal")]
    assert wire.users == [["hook-prompt", "hi"] if trigger else ["hi"]]


async def test_a_provider_first_registered_in_a_rebuild_session_start_is_late(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify round 4, B2(a). ``sessext`` is registered only in a REBUILD's
    ``session_start``; the launch ``--model sessext/m1`` went to OpenRouter as
    written (guard 2, not late). The first ``/new`` re-resolves before the
    registration (OpenRouter again), its ``session_start`` registers
    ``sessext``, and the after-``session_start`` callback records it — the
    rebuild is held. Without that record (``_settle_late_route`` minus
    ``late_rule.after_session_start()``), every test passed and the second
    ``/new`` moved onto ``sessext``, which nobody picked (the verifier's pty
    probe g1-late-on-rebuild: "hi: /late/v1/chat/completions")."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "rebuildonly.py", _extension(register="rebuild"))
    seen = await _interactive(
        env, monkeypatch, ["-e", ext, "--approve", "--model", "sessext/m1"], ["new", "new"]
    )
    assert seen == [
        ("launch", ("openrouter", "sessext/m1", "openai-completions")),
        ("new", _HELD),
        ("new", _HELD),
    ]


@pytest.mark.parametrize("step", ["plainprof", "--none"])
async def test_agents_use_asks_again_without_the_settings_default(
    env: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Verify round 4, B2(b). ``other`` (``setup()``, keyless) and ``sessext``
    (``session_start``, keyed) both serve ``m1``; settings ``defaultProvider``
    is ``other``, so ``--model m1`` launches on ``other`` (registered). ``/agents
    use`` of a profile naming no route (or ``--none``) resolves the baseline
    WITHOUT the settings default, as ``use()``'s own resolve does — and that
    lands on ``sessext``. The hold's second ask (``use_default=False``) holds
    it; without that ask every test passed and the next prompt went to
    ``sessext`` (g3-use-second-ask: "hi: /late/v1/chat/completions")."""

    ext = _write(env, "g3.py", _extension(setup_other=True, other_key=False))
    (env / "agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "other"}), encoding="utf-8"
    )
    seen = await _session(env, monkeypatch, ["--model", "m1"], [step], ext=ext)
    assert seen == [("launch", _ON_OTHER), (step, _HELD)]


async def test_a_provider_a_reloaded_extension_registers_in_setup_is_not_late(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify round 4, B2(c) — #344's /reload case through the whole launch
    (``test_launch_route_344``'s row stops before the after-``session_start``
    seam). The extension registers nothing at launch (``--model extprov/m1`` is
    guard 2); it is rewritten to register ``extprov`` in ``setup()`` and
    ``/reload`` rebuilds onto it. ``extprov`` arrived in a build, before that
    build's ``session_start``, so it is not late. Without the end-of-build
    snapshot (``late_rule.before_session_start()``) it counted as
    ``session_start``'s and the reload was HELD (g2-reload-setup: "hi: NO
    REQUEST", "✖ The launch model "extprov/m1" names provider 'extprov'")."""

    import aelix_coding_agent.tui as tui_pkg

    from tests.cli.test_launch_route_344 import _EXTENSION

    _openrouter(monkeypatch, env, "exported")
    ext = env / "extprov.py"
    ext.write_text("def setup(aelix):\n    return None\n", encoding="utf-8")
    seen: list[tuple[str, str, str]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen.append(_route(runtime.harness.current_model))
        ext.write_text(_EXTENSION, encoding="utf-8")
        await runtime.reload()
        seen.append(_route(runtime.harness.current_model))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--approve", "--model", "extprov/m1"]
    )
    assert code == 0
    assert seen == [
        ("openrouter", "extprov/m1", "openai-completions"),
        ("extprov", "m1", "openai-completions"),
    ]


@pytest.mark.parametrize("launch", ["pending", "registered"])
async def test_a_held_rebuild_session_start_enters_on_the_placeholder(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], launch: str
) -> None:
    """The factory's placeholder, under the gate. Since round 5 the gate alone
    keeps a held rebuild's ``session_start`` from running a turn, so removing
    the factory's ``opts.model = held.placeholder`` left every other test green;
    what it still decides is the model the handlers see — the placeholder
    (``api='unknown'``), as a pending launch's handlers do (D4), not the late
    provider the re-resolve lands on, which nobody picked. ``registered``: the
    launch on ``other`` with settings ``defaultProvider`` naming ``sessext``
    (both keyed) — the factory asks WITH that default, as every build
    resolves, so the placeholder names ``sessext`` (verify4's
    V-factory-no-default asked without it: ``HOOK new  m1 unknown``)."""

    if launch == "pending":
        _openrouter(monkeypatch, env, "exported")
        ext = _write(env, "record.py", _extension(record=True))
        argv, first = ["--model", "sessext/m1"], _HELD
        start = "HOOK_MODEL startup openrouter sessext/m1 unknown"
    else:
        ext = _write(env, "record.py", _extension(record=True, setup_other=True))
        (env / "agent" / "settings.json").write_text(
            json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
        )
        argv, first = ["--model", "m1"], _ON_OTHER
        start = "HOOK_MODEL startup other m1 openai-completions"
    seen = await _interactive(env, monkeypatch, ["-e", ext, "--approve", *argv], ["new", "reload"])
    assert seen == [("launch", first), ("new", _HELD), ("reload", _HELD)]
    hooks = [line for line in capsys.readouterr().out.splitlines() if line.startswith("HOOK_MODEL")]
    assert hooks == [
        start,
        "HOOK_MODEL new sessext m1 unknown",
        "HOOK_MODEL reload sessext m1 unknown",
    ]


async def test_a_held_launch_runs_the_turn_of_an_explicit_pick(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire
) -> None:
    """The launch's gate is lifted once the hold is applied: the placeholder
    refuses turns from there with the hold's reason, and an explicit pick (what
    ``/model`` does) runs the next prompt — the message of the hook's refused
    ``trigger_turn`` during ``session_start`` rides with it, from the
    conversation. A gate left up after the hold would refuse that prompt too,
    and ``/model`` could never leave the hold."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_ai.streaming import Model

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "trigger.py", _extension(trigger=True))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen["launch"] = _route(runtime.harness.current_model)
        seen["gate"] = runtime.harness.turns_held
        await runtime.harness.set_model(
            Model(id="m1", provider="sessext", api="openai-completions", base_url=_EXT)
        )
        await runtime.harness.prompt("hi")
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "sessext/m1"])
    assert (code, seen["launch"], seen["gate"]) == (0, _HELD, None)
    assert wire == [(_EXT + "/chat/completions", "Bearer ext-fake-literal")]
    assert wire.users == [["hook-prompt", "hi"]]


# === Verify round 5 — the held state is checked, and a refused turn ends ========


def _gate_answers(harness: Any) -> list[str]:
    """The answers of the turns the turn gate refused (``AgentHarness.hold_turns``)."""

    from aelix_ai.messages import AssistantMessage

    return [
        m.error_message or ""
        for m in harness.messages
        if isinstance(m, AssistantMessage)
        and m.stop_reason == "error"
        and (m.error_message or "").startswith("No turn runs")
    ]


async def _prompt_refused(harness: Any) -> str:
    """Prompt once on a held harness; return the refusal (nothing may be sent)."""

    try:
        await harness.prompt("hi")
    except Exception as exc:  # noqa: BLE001 — the placeholder's own refusal
        return str(exc)
    return ""


@pytest.mark.parametrize("openrouter", ["exported", "none"])
@pytest.mark.parametrize("mode", ["interactive", "rpc"])
async def test_a_model_select_handler_cannot_move_a_held_launch_off_the_placeholder(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    mode: str,
    openrouter: str,
) -> None:
    """#367 verify round 5, B1. A ``model_select`` handler answers the hold's
    placeholder with its own ``set_model`` onto ``sessext``. The launch applied
    the hold with ``set_model`` and never looked again: on a543754c the
    session started on ``sessext`` with nobody's pick, the Warning "No prompt
    will be sent for it" printed, and the first prompt went to ``sessext``
    (the verifier's o-ms-handler-rpc: "/late/v1/chat/completions ... Bearer
    late-fake"; pty v5-ms-launch, -noor). The launch now applies the hold as a
    rebuild re-holds (``LateRouteHold.apply``): the placeholder is put back
    before the gate is lifted and before the Warning, which is then true."""

    _openrouter(monkeypatch, env, openrouter)
    ext = _write(env, "msel.py", _extension(model_select="always"))
    seen: dict[str, Any] = {}

    async def _record(harness: Any) -> None:
        seen["route"] = _route(harness.current_model)
        seen["gate"] = harness.turns_held
        seen["refused"] = await _prompt_refused(harness)

    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            await _record(runtime.harness)
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        flags: list[str] = []
    else:
        from aelix_coding_agent import modes

        async def _stub_run_rpc(harness: Any, **kwargs: Any) -> None:
            await _record(harness)

        monkeypatch.setattr(modes, "run_rpc_mode", _stub_run_rpc)
        flags = ["--mode", "rpc"]
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "sessext/m1", *flags])
    err = capsys.readouterr().err
    assert (code, seen["route"], seen["gate"]) == (0, _HELD, None)
    assert "api='unknown'" in seen["refused"]
    assert wire == []
    assert "Warning: " + _REFUSAL.format(subject="sessext/m1") in err
    assert "No prompt will be sent for it" in err


async def test_a_model_select_handler_turn_during_the_launch_hold_sends_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire
) -> None:
    """The order round 6 fixes: the launch lifts its turn gate only after the
    hold is applied and checked. A ``model_select`` handler that answers the
    placeholder with ``set_model(sessext/m1)`` and triggers a turn there gets
    a refused turn (the gate is still up), and the session starts held. With
    the gate lifted before the hold is applied, that turn ran on ``sessext``
    before the placeholder was put back."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "mseltrigger.py", _extension(model_select="trigger"))
    handed: list[Any] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        handed.append(_route(runtime.harness.current_model))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session", "-e", ext, "--model", "sessext/m1"])
    assert (code, handed, wire) == (0, [_HELD], [])


async def test_a_model_select_handler_cannot_move_a_held_rebuild_off_the_placeholder(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire
) -> None:
    """#367 verify round 5, B2. In every rebuild's ``session_start`` the hook
    calls ``set_model(sessext/m1)``; once a rebuild ran, a ``model_select``
    handler answers the placeholder with ``set_model(sessext/m1)`` too. The
    settle's re-hold then has to check the state after its ``set_model`` —
    the fallback assignment no row pinned (V-settle-no-fallback left 192
    tests green while the verifier's pty probe v5-msr-rebuild sent "hi" to
    "/late/v1" after ``/new``)."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "mselrebuild.py", _extension(set_model="rebuild", model_select="rebuild"))
    seen: list[tuple[str, tuple[str, str, str], bool]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        for step in ("launch", "new", "reload"):
            if step == "new":
                await runtime.new_session()
            elif step == "reload":
                await runtime.reload()
            refused = await _prompt_refused(runtime.harness)
            seen.append((step, _route(runtime.harness.current_model), bool(refused)))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["-e", ext, "--approve", "--model", "sessext/m1"])
    assert code == 0
    assert seen == [("launch", _HELD, True), ("new", _HELD, True), ("reload", _HELD, True)]
    assert wire == []


async def test_a_model_select_handler_cannot_move_agents_use_off_the_placeholder(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire
) -> None:
    """The third path that applies a hold, ``/agents use``: shape (c) of
    verify round 2 (``--agent orprof --model sessext/m1``, never held), then
    ``/agents use --none``, whose baseline lands on ``sessext``. A
    ``model_select`` handler answering the placeholder with
    ``set_model(sessext/m1)`` must not leave the session there; the same
    helper puts the placeholder back, and the next prompt sends nothing."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "orprof.md").write_text(_PROFILES["orprof"], encoding="utf-8")
    ext = _write(env, "msel.py", _extension(model_select="always"))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen["launch"] = _route(runtime.harness.current_model)
        await kwargs["agent_service"].use(None, harness=runtime.harness)
        seen["use"] = _route(runtime.harness.current_model)
        seen["refused"] = await _prompt_refused(runtime.harness)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--approve", "--agent", "orprof", "--model", "sessext/m1"]
    )
    assert code == 0
    assert seen["launch"] == ("openrouter", "sessext/m1", "openai-completions")
    assert (seen["use"], bool(seen["refused"])) == (_HELD, True)
    assert wire == []


@pytest.mark.parametrize(
    ("argv", "mode"),
    [
        (["--model", "newlab/x"], "print"),
        (["--model", "sessext/m1"], "print"),
        (["--model", "sessext/m1"], "interactive"),
    ],
    ids=["not-late-print", "late-print", "late-interactive"],
)
async def test_a_handler_awaiting_its_own_triggered_turn_returns(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    argv: list[str],
    mode: str,
) -> None:
    """#367 verify round 5, the hang. The ``session_start`` handler triggers a
    turn and waits, with no timeout, for that turn's ``agent_end``. Round 5's
    gate queued the message instead of starting a turn, so no ``agent_end``
    came, ``session_start`` never returned and ``aelix -p`` hung with no output
    — also for a guard-2 launch that is not late (the verifier's
    o-await-forever-notlate: "timeout True" on a543754c; 5dee21d1 and 76055424
    completed). A gated ``trigger_turn`` now runs as a REFUSED turn: the
    events of a turn that cannot reach its model, ``agent_end`` included,
    nothing sent. Then the launch proceeds as decided: not late, the prompt
    goes out on the launch route, carrying the handler's message as the
    refused turn's prompt in the conversation (as any failed turn's); late,
    print refuses ("No prompt was sent." stays true) and interactive starts
    held."""

    import asyncio

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "awaitend.py", _extension(trigger="await-end"))
    handed: list[Any] = []
    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            handed.append(_route(runtime.harness.current_model))
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        flags: list[str] = []
    else:
        flags = ["-p", "hi"]
    code = await asyncio.wait_for(
        entry_mod._async_main(["--no-session", "-e", ext, *argv, *flags]), timeout=_BOUND
    )
    out, err = capsys.readouterr()
    assert "WAIT done" in out
    if argv[1] == "newlab/x":
        # rc 1 is the recorder's 400 answer to the prompt that went out.
        assert (code, "recorded" in err) == (1, True)
        assert [url for url, _ in wire] == ["https://openrouter.ai/api/v1/chat/completions"]
        assert wire.users == [["hook-prompt", "hi"]]
    elif mode == "print":
        assert (code, wire) == (1, [])
        assert "Error: " + _REFUSAL.format(subject="sessext/m1") + " No prompt was sent." in err
    else:
        assert (code, handed, wire) == (0, [_HELD], [])


@pytest.mark.parametrize("launch", ["not-late-print", "held-rebuild"])
async def test_a_refused_turn_ends_before_the_route_is_decided(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire, launch: str
) -> None:
    """The refused turn runs in a task of its own; a handler that triggers and
    returns at once ends ``session_start`` before that task has claimed the
    harness. The launch decision (and a rebuild's settle) waits it out, so the
    first prompt after it finds the harness idle — not busy, which would refuse
    the prompt (or lose the handler's turn) depending on who claims first —
    and carries the handler's message from the conversation."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_ai.streaming import Model

    _openrouter(monkeypatch, env, "exported")
    if launch == "not-late-print":
        ext = _write(env, "nowait.py", _extension(trigger="nowait"))
        await entry_mod._async_main(["--no-session", "-e", ext, "--model", "newlab/x", "-p", "hi"])
        assert wire.users == [["hook-prompt", "hi"]]
        return
    ext = _write(env, "nowait.py", _extension(trigger="nowait"))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        await runtime.new_session()
        seen["idle"] = runtime.harness.is_idle
        await runtime.harness.set_model(
            Model(id="m1", provider="sessext", api="openai-completions", base_url=_EXT)
        )
        await runtime.harness.prompt("hi")
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["-e", ext, "--approve", "--model", "sessext/m1"])
    assert (code, seen["idle"]) == (0, True)
    assert wire.users == [["hook-prompt", "hi"]]


# === Verify round 6 — every application of a hold runs under the turn gate =====


_HOLD_PATHS = ["launch", "rebuild-held", "rebuild-first", "agents-use", "agents-use-after-model"]


@pytest.mark.parametrize("path", _HOLD_PATHS)
async def test_a_model_select_handler_turn_while_a_hold_is_applied_sends_nothing(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    path: str,
) -> None:
    """#367 verify round 6, B1. Inside ``LateRouteHold.apply``'s
    ``set_model(placeholder)`` a ``model_select`` handler answers the
    placeholder with ``set_model(sessext/m1)``, triggers a turn and yields
    (``model_select="trigger"``). On 343e75cd only the launch and a rebuild the
    factory held ran that under a turn gate: ``/agents use`` (shape (c) of
    verify round 2, ``--agent orprof --model sessext/m1``, and ``--model
    sessext/m1`` after an explicit pick of ``other``) and the settle of a
    rebuild whose ``session_start`` first registered ``sessext`` (the factory
    found nothing to hold, so no gate) applied it with turns open, and the
    triggered prompt went to ``sessext`` before ``apply`` put the placeholder
    back (round 6's pty: "/late/v1/chat/completions m1 Bearer late-fake";
    fix7's v7-mst-rebuild-first the same after ``/new``). ``launch`` and
    ``rebuild-held`` (whose ``session_start`` sets ``sessext``, so the settle
    re-applies) were gated by their caller and stay green there; each row is
    red with the gate removed on its own path (fix7's sabotage). Every path now
    applies a hold through the one helper, which holds turns for its duration
    and waits out the refused turn it produced: nothing is sent, the session
    ends on the placeholder, and what aelix then says ("No prompt will be
    sent for it") is true. (``apply`` also waits out the refused turn before
    it lifts its gate - ``test_late_route_hold_apply_367.py`` pins that.)"""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_ai.streaming import Model

    _openrouter(monkeypatch, env, "exported")
    kwargs: dict[str, Any] = {"model_select": "trigger"}
    argv = ["--model", "sessext/m1"]
    if path == "rebuild-held":
        # The rebuild's ``session_start`` moves off the factory's placeholder,
        # so the settle re-applies the hold (under the factory's gate).
        kwargs["set_model"] = "rebuild"
    elif path == "rebuild-first":
        kwargs["register"] = "rebuild"
    elif path == "agents-use":
        argv = ["--agent", "orprof", "--model", "sessext/m1"]
    elif path == "agents-use-after-model":
        kwargs["setup_other"] = True
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "orprof.md").write_text(_PROFILES["orprof"], encoding="utf-8")
    ext = _write(env, "mstrigger.py", _extension(**kwargs))
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kw: Any) -> int:
        seen["launch"] = _route(runtime.harness.current_model)
        if path.startswith("rebuild"):
            await runtime.new_session()
        elif path.startswith("agents-use"):
            if path == "agents-use-after-model":
                await runtime.harness.set_model(
                    Model(id="m1", provider="other", api="openai-completions", base_url=_OTHER)
                )
            seen["status"] = await kw["agent_service"].use(None, harness=runtime.harness)
        seen["sent"] = list(wire)
        seen["after"] = _route(runtime.harness.current_model)
        seen["gate"] = runtime.harness.turns_held
        seen["idle"] = runtime.harness.is_idle
        # Idle already when the hold was applied under the gate; on 343e75cd
        # the handler's turn was still running here - let it reach the wire.
        await runtime.harness.wait_for_idle()
        seen["answers"] = _gate_answers(runtime.harness)
        seen["refused"] = await _prompt_refused(runtime.harness)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    # A rebuild needs a session with a cwd (``/new``); the others run without one.
    session = [] if path.startswith("rebuild") else ["--no-session"]
    code = await entry_mod._async_main([*session, "-e", ext, "--approve", *argv])
    err = capsys.readouterr().err
    assert (code, wire) == (0, [])
    assert seen["sent"] == []
    assert (seen["after"], seen["gate"], seen["idle"]) == (_HELD, None, True)
    # Verify round 7, V-B1: the handler's turn is refused because the session
    # is being put on hold, and its answer says so on every path. On f3fd162c
    # the launch and a held rebuild answered with their caller's gate reason
    # ("... while this session's session_start handlers run. The launch route
    # is not decided yet ..."), stale once the hold was being applied.
    # ``agents-use-after-model`` keeps the launch's harness, whose own hold
    # refused the handler's first turn: two answers, both the hold's.
    held_turns = 2 if path == "agents-use-after-model" else 1
    expected = f"{HOLD_GATE} " + _REFUSAL.format(subject="sessext/m1")
    assert seen["answers"] == [expected] * held_turns
    assert "api='unknown'" in seen["refused"]
    if path == "launch":
        assert "No prompt will be sent for it; run /model to select a model." in err
    if path.startswith("agents-use"):
        assert "No prompt will be sent for it; run /model to select a model." in seen["status"]


# === Verify round 7 / Codex pass 7 — what a refused turn and the refusal say ====


@pytest.mark.parametrize("path", ["launch", "held-rebuild"])
async def test_a_turn_a_session_start_handler_triggers_keeps_the_session_start_reason(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: _Wire, path: str
) -> None:
    """#367 verify round 7, V-B1, the other half. A turn a ``session_start``
    handler triggers while that ``session_start`` runs is refused because the
    route is not decided yet, and its answer keeps the ``session_start`` reason
    (verify7's p23/p23b showed that text is true there); only a turn refused
    while the hold is applied answers with the hold's reason. ``launch``: the
    pending launch's ``session_start`` triggers a turn, then a ``model_select``
    handler triggers one inside the hold's ``apply`` - two answers, one of
    each. ``held-rebuild``: ``/new`` after a held launch, whose
    ``session_start`` triggers a turn under the factory's gate."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    refusal = _REFUSAL.format(subject="sessext/m1")
    if path == "launch":
        ext = _write(env, "sstrigger.py", _extension(trigger=True, model_select="trigger"))
        argv = ["--no-session", "-e", ext, "--model", "sessext/m1"]
        expected = [
            f"{entry_mod._SESSION_START_GATE} The launch route is not decided yet: a provider "
            "registered there may be where the launch model lands.",
            f"{HOLD_GATE} {refusal}",
        ]
    else:
        ext = _write(env, "sstrigger.py", _extension(trigger="rebuild"))
        argv = ["-e", ext, "--approve", "--model", "sessext/m1"]
        expected = [f"{entry_mod._SESSION_START_GATE} {refusal}"]
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        if path == "held-rebuild":
            seen["launch"] = _gate_answers(runtime.harness)
            await runtime.new_session()
        seen["route"] = _route(runtime.harness.current_model)
        seen["answers"] = _gate_answers(runtime.harness)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(argv)
    assert (code, seen["route"], wire) == (0, _HELD, [])
    assert seen["answers"] == expected
    if path == "held-rebuild":
        assert seen["launch"] == []


_REGISTER_IN_THE_WINDOW = textwrap.dedent(
    """
    import asyncio
    from aelix_ai.messages import TextContent, UserMessage
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    CASE = %r
    DONE = asyncio.Event()
    STARTED = asyncio.Event()

    def setup(aelix):
        def _register(where):
            aelix.register_provider("sessext", ProviderConfigInput(name="probe",
                api_key="ext-fake-literal", models={"m1": Model(id="m1", provider="sessext",
                api="openai-completions", base_url=%r)}))
            print("REGISTERED_IN", where, flush=True)
        async def _on_turn(event, ctx):
            _register(event.type)
        async def _end(event, ctx):
            DONE.set()
        async def _task():
            _register("task")
        async def _setup_task():
            await STARTED.wait()
            _register("setup-task")
        if CASE == "setup-task":
            asyncio.get_running_loop().create_task(_setup_task())
        async def _start(event, ctx):
            if CASE == "task":
                asyncio.get_running_loop().create_task(_task())
            elif CASE == "setup-task":
                STARTED.set()
            else:
                aelix.send_message(UserMessage(content=[TextContent(text="hook-prompt")]),
                    trigger_turn=True)
                if CASE.startswith("awaited"):
                    await asyncio.wait_for(DONE.wait(), 10)
            print("SS_RETURN", flush=True)
        if CASE.endswith("-input"):
            aelix.on("input", _on_turn)
        elif CASE.endswith("-before_agent_start"):
            aelix.on("before_agent_start", _on_turn)
        aelix.on("agent_end", _end)
        aelix.on("session_start", _start)
    """
)


@pytest.mark.parametrize(
    "case",
    [
        "awaited-input",
        "awaited-before_agent_start",
        "fire-and-forget-input",
        "fire-and-forget-before_agent_start",
        "task",
        "setup-task",
    ],
)
async def test_a_provider_registered_while_the_session_starts_is_refused_with_a_true_reason(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    case: str,
) -> None:
    """#367 Codex pass 7, C-P2, and verify round 8, B1. Something other than
    the ``session_start`` handler registers ``sessext`` while the session is
    starting: the ``input`` (or ``before_agent_start``) handler of a turn the
    handler triggered - ``awaited-*``: it waits for that turn's ``agent_end``;
    ``fire-and-forget-*``: it returns at once, and the turn (refused by the
    gate) runs after the emit returned, while aelix waits it out - a task the
    handler spawned with no delay (``task``), or a task ``setup()`` spawned
    that registers once ``session_start`` fired (``setup-task``). Every one is
    inside the window (``LateRoute``), after the launch route was chosen, so
    the launch ``--model sessext/m1`` is refused - pi resolves the launch model
    before any handler runs (behaviour kept, owner decision); nothing is sent.
    The text said "registered in a session_start handler" (f3fd162c), false for
    all six, then "while session_start handlers ran" (29499345), false for the
    four that register after the handler returned (verify8's
    ``cp2_timing.head.out``: "EMIT_DONE session_start | REGISTERED_IN input");
    it now says "while a session was starting (for example in a session_start
    handler)", true for all six, and keeps the ``setup()`` remedy."""

    _openrouter(monkeypatch, env, "exported")
    ext = _write(env, "windowreg.py", _REGISTER_IN_THE_WINDOW % (case, _EXT))
    code = await entry_mod._async_main(
        ["--no-session", "-e", ext, "--model", "sessext/m1", "-p", "hi"]
    )
    out, err = capsys.readouterr()
    registered = out.index("REGISTERED_IN")
    # The timing the text must be true for: an awaited trigger registers before
    # the handler returns, every other case after it.
    if case.startswith("awaited"):
        assert registered < out.index("SS_RETURN"), out
    else:
        assert out.index("SS_RETURN") < registered, out
    assert (code, wire) == (1, [])
    assert "Error: " + _REFUSAL.format(subject="sessext/m1") + " No prompt was sent." in err
    assert "while a session was starting (for example in a session_start handler)" in err
    assert "session_start handlers ran" not in err
    assert "in a session_start handler," not in err
    assert "Register 'sessext' in the extension's setup() (its factory)" in err


_MOVES_THE_LAUNCH = textwrap.dedent(
    """
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        aelix.register_provider("other", ProviderConfigInput(name="other",
            api_key="other-fake-literal", models={"m1": Model(id="m1", provider="other",
            api="openai-completions", base_url=%(other)r)}))
        async def _start(event, ctx):
            await aelix.set_model(Model(id=%(id)r, provider=%(provider)r, api=%(api)r,
                base_url=%(base)r))
        aelix.on("session_start", _start)
    """
)
_OR_MOVED = "http://127.0.0.1:9/or/v1"
_ANT_MOVED = "http://127.0.0.1:9/ant"
_GUARD2_NOTE = (
    'Note: Model "newlab/x" is not in this build\'s catalog; sending it to OpenRouter as written.'
)
_CUSTOM_ID_WARNING = (
    'Warning: Model "claude-new-9" not found for provider "anthropic". Using custom model id.'
)


@pytest.mark.parametrize(
    ("key", "model", "moved_to", "sent", "line"),
    [
        (
            "OPENROUTER_API_KEY",
            "newlab/x",
            ("m1", "other", "openai-completions", _OTHER),
            (_OTHER + "/chat/completions", "m1"),
            _GUARD2_NOTE,
        ),
        (
            "OPENROUTER_API_KEY",
            "newlab/x",
            ("vendor/other-model", "openrouter", "openai-completions", _OR_MOVED),
            (_OR_MOVED + "/chat/completions", "vendor/other-model"),
            _GUARD2_NOTE,
        ),
        (
            "OPENROUTER_API_KEY",
            "newlab/x",
            ("newlab/x", "other", "openai-completions", _OTHER),
            (_OTHER + "/chat/completions", "newlab/x"),
            _GUARD2_NOTE,
        ),
        (
            "ANTHROPIC_API_KEY",
            "anthropic/claude-new-9",
            ("m1", "other", "openai-completions", _OTHER),
            (_OTHER + "/chat/completions", "m1"),
            _CUSTOM_ID_WARNING,
        ),
        (
            "ANTHROPIC_API_KEY",
            "anthropic/claude-new-9",
            ("claude-other-1", "anthropic", "anthropic-messages", _ANT_MOVED),
            (_ANT_MOVED + "/v1/messages", "claude-other-1"),
            _CUSTOM_ID_WARNING,
        ),
        (
            "ANTHROPIC_API_KEY",
            "anthropic/claude-new-9",
            ("claude-new-9", "other", "openai-completions", _OTHER),
            (_OTHER + "/chat/completions", "claude-new-9"),
            _CUSTOM_ID_WARNING,
        ),
    ],
    ids=[
        "guard2-note",
        "guard2-same-provider-other-id",
        "guard2-same-id-other-provider",
        "custom-id-warning",
        "custom-id-same-provider-other-id",
        "custom-id-same-id-other-provider",
    ],
)
async def test_the_route_line_is_not_printed_when_an_extension_moved_the_launch_off_the_route(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: _Wire,
    key: str,
    model: str,
    moved_to: tuple[str, str, str, str],
    sent: tuple[str, str],
    line: str,
) -> None:
    """#367 Codex pass 7, C-P3. An extension registers ``other`` in
    ``setup()`` and its ``session_start`` handler calls ``set_model`` - the
    extension's own action (ADR-0250 §6) - so the prompt goes where it moved
    the launch. On f3fd162c the launch still printed the resolver's line for
    the route it no longer took: "Note: Model "newlab/x" is not in this build's
    catalog; sending it to OpenRouter as written." while the request went to
    /other/v1. The line describes the launch route and is printed only while
    the harness is still on it - the same provider AND the same id (verify
    round 8, B2: a check of the provider alone, or of the id alone, passed
    every row until the ``same-provider-other-id`` rows - ``openrouter`` /
    ``vendor/other-model`` for guard 2, ``anthropic`` / ``claude-other-1`` for
    the custom id - and the ``same-id-other-provider`` rows - ``other`` /
    ``newlab/x``, ``other`` / ``claude-new-9``). The plain guard-2 Note and
    the custom-id Warning (``test_launch_route_362.py``'s
    ``test_the_route_warning_is_printed_once_on_stderr``) stay."""

    monkeypatch.setenv(key, "fake-key-literal")
    model_id, provider, api, base = moved_to
    ext = _write(
        env,
        "moves.py",
        _MOVES_THE_LAUNCH
        % {"other": _OTHER, "id": model_id, "provider": provider, "api": api, "base": base},
    )
    await entry_mod._async_main(["--no-session", "-e", ext, "--model", model, "-p", "hi"])
    err = capsys.readouterr().err
    assert list(zip([url for url, _ in wire], wire.models, strict=True)) == [sent]
    assert line not in err
    assert "Note:" not in err and "Using custom model id" not in err
