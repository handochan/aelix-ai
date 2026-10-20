"""#371 — ``aelix --mode rpc`` has the model registry, and an unresolved launch exits 1.

On ``7faf4b9a`` ``cli/entry.py`` called ``run_rpc_mode`` without a model
registry, so in the shipped RPC mode ``set_model``, ``cycle_model`` and
``get_available_models`` all answered "requires a ModelRegistry — none
configured": a session #367 held on its late-provider placeholder could not
leave it ("restart with another --model"), where interactive's ``/model``
could. pi's RPC mode has a model runtime (``rpc-mode.ts`` ``set_model`` /
``cycle_model`` / ``get_available_models`` at ``fdce48eb4``).

And a launch model that does not resolve started RPC silently: ``-p`` and
``--mode json`` exited 1 with "Model ... not found" while RPC answered the
first ``prompt`` with ``success: false`` and exited 0 at stdin EOF. pi's
``main.ts`` makes ``resolveCliModel``'s error an error diagnostic and exits 1
in every mode — for ``--model`` only; the owner decided (issue comment,
2026-10-20) that RPC follows it, with -p's text. A saved default pi cannot
resolve falls back (``findInitialModel``), so a settings default that does not
resolve still starts RPC, as it did on ``7faf4b9a`` (review round 2: the first
cut exited on it too). #367's hold stays distinct: a launch whose inputs land
on a provider registered while the session was starting still starts held,
and so does #367 round 3's B1 launch, which resolved before a provider
registered in ``session_start`` made its inputs ambiguous.

Drives the real ``_async_main``. The RPC rows that exchange commands run the
real ``run_rpc_mode`` on an in-memory stdin/stdout; every HTTP request is
recorded by an ``httpx.MockTransport`` — fake keys, an isolated agent dir,
nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_launch_route_344 import (
    _LATE_REFUSAL,
    _SESSION_START_EXTENSION,
    _FakePipedStdin,
)
from tests.env_sandbox import sandbox_home

_BASE = "http://127.0.0.1:9"


def _models_json(*providers: str, ids: tuple[str, ...] = ("m1", "m2")) -> str:
    return json.dumps(
        {
            "providers": {
                name: {
                    "api": "openai-completions",
                    "baseUrl": f"{_BASE}/{name}/v1",
                    "apiKey": f"{name}-fake-literal",
                    "models": [{"id": i} for i in ids],
                }
                for name in providers
            }
        }
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    monkeypatch.setenv("AELIX_MAX_RETRIES", "0")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    (tmp_path / "sessext.py").write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    # ``main_sync`` registers the api adapters; ``_async_main`` does not, and
    # without them the runnability checks fail open.
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    return tmp_path


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """``(url path, body model)`` per request; each answered with a short SSE reply."""

    import httpx

    sent: list[tuple[str, str | None]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        sent.append((request.url.path, body.get("model") if isinstance(body, dict) else None))
        chunks = [
            {
                "id": "p",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}
                ],
            },
            {
                "id": "p",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=data.encode("utf-8")
        )

    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(_record)
        kwargs["mounts"] = {}
        kwargs["trust_env"] = False
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return sent


def _write_models(env: Path, *providers: str) -> None:
    (env / "agent" / "models.json").write_text(_models_json(*providers), encoding="utf-8")


def _stub_rpc(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub ``run_rpc_mode``; return the keyword arguments it was handed."""

    from aelix_coding_agent import modes

    handed: list[dict[str, Any]] = []

    async def _stub(harness: Any, **kwargs: Any) -> None:
        handed.append({"model": harness.current_model, **kwargs})

    monkeypatch.setattr(modes, "run_rpc_mode", _stub)
    return handed


def _stub_print(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    return turns


def _drive_real_rpc(
    monkeypatch: pytest.MonkeyPatch, commands: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run the REAL ``run_rpc_mode`` on in-memory pipes: send each command after
    the previous one's response (and, for a prompt that was accepted, its
    ``agent_end``); return every response in order."""

    from aelix_coding_agent import modes

    real = modes.run_rpc_mode
    responses: list[dict[str, Any]] = []

    async def _wrapped(harness: Any, **kwargs: Any) -> None:
        reader = asyncio.StreamReader()
        lines: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def _write(data: bytes) -> None:
            for raw in data.decode("utf-8").splitlines():
                if raw.strip():
                    lines.put_nowait(json.loads(raw))

        async def _next(pred: Any) -> dict[str, Any]:
            while True:
                obj = await asyncio.wait_for(lines.get(), timeout=60)
                if pred(obj):
                    return obj

        async def _client() -> None:
            try:
                for cmd in commands:
                    reader.feed_data((json.dumps(cmd) + "\n").encode("utf-8"))
                    resp = await _next(
                        lambda o, i=cmd["id"]: o.get("type") == "response" and o.get("id") == i
                    )
                    responses.append(resp)
                    if cmd["type"] == "prompt" and resp.get("success"):
                        await _next(lambda o: o.get("type") == "agent_end")
            finally:
                reader.feed_eof()

        client = asyncio.create_task(_client())
        await real(
            harness, stdin=reader, stdout_write=_write, install_signal_handlers=False, **kwargs
        )
        await client

    monkeypatch.setattr(modes, "run_rpc_mode", _wrapped)
    return responses


def _ref(model: dict[str, Any] | None) -> str | None:
    return None if model is None else f"{model['provider']}/{model['id']}"


# === (a) the shipped RPC mode has the model registry ===========================


async def test_rpc_is_handed_the_registry_its_harness_resolves_through(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry reaches ``run_rpc_mode``: the one the launch resolved
    ``rec/m1`` through (``models.json``), not a fresh empty one."""

    _write_models(env, "rec")
    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    assert len(handed) == 1
    registry = handed[0]["model_registry"]
    assert registry is not None
    assert {f"{m.provider}/{m.id}" for m in registry.get_available()} >= {"rec/m1", "rec/m2"}
    assert handed[0]["runtime_host"] is not None


async def test_set_model_cycle_model_and_get_available_models_work_in_rpc(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: list[tuple[str, str | None]]
) -> None:
    """The issue's three commands through the real ``run_rpc_mode``: the list
    names the user's models, ``set_model`` moves the next prompt to the model
    it names, ``cycle_model`` to the next one, an unknown id is pi's
    "Model not found". On ``7faf4b9a`` each answered "requires a ModelRegistry"
    and both prompts went to ``rec/m1``."""

    _write_models(env, "rec", "rec2")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"type": "get_available_models", "id": "list"},
            {"type": "set_model", "id": "set", "provider": "rec", "modelId": "m2"},
            {"type": "prompt", "id": "p1", "message": "first"},
            {"type": "cycle_model", "id": "cycle"},
            {"type": "prompt", "id": "p2", "message": "second"},
            {"type": "set_model", "id": "missing", "provider": "rec", "modelId": "zz"},
            {"type": "get_state", "id": "state"},
        ],
    )
    code = await entry_mod._async_main(["--no-session", "--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    by_id = {r["id"]: r for r in responses}
    assert by_id["list"]["success"] is True
    listed = [_ref(m) for m in by_id["list"]["data"]["models"]]
    assert {"rec/m1", "rec/m2", "rec2/m1", "rec2/m2"} <= set(listed)
    assert (by_id["set"]["success"], _ref(by_id["set"]["data"])) == (True, "rec/m2")
    assert by_id["cycle"]["success"] is True
    cycled = _ref(by_id["cycle"]["data"]["model"])
    assert cycled == listed[(listed.index("rec/m2") + 1) % len(listed)]
    assert by_id["missing"] == {
        "type": "response",
        "command": "set_model",
        "success": False,
        "id": "missing",
        "error": "Model not found: rec/zz",
    }
    assert _ref(by_id["state"]["data"]["model"]) == cycled
    provider, model_id = cycled.split("/")
    assert wire == [
        ("/rec/v1/chat/completions", "m2"),
        (f"/{provider}/v1/chat/completions", model_id),
    ]


async def test_a_held_rpc_session_leaves_the_hold_with_set_model(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str | None]],
) -> None:
    """#367's hold in RPC: the launch inputs land on ``sessext``, registered in
    ``session_start``, so the session starts on the placeholder and its prompt
    is refused, nothing sent. The Warning now names ``set_model`` (it said
    "restart with another --model"), and ``set_model`` to the user's own model
    leaves the hold: the next prompt goes there."""

    _write_models(env, "rec")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"type": "get_state", "id": "s0"},
            {"type": "prompt", "id": "p0", "message": "held"},
            {"type": "set_model", "id": "set", "provider": "rec", "modelId": "m1"},
            {"type": "prompt", "id": "p1", "message": "left"},
            {"type": "get_state", "id": "s1"},
        ],
    )
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--model", "sessext/m1", "--mode", "rpc"]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert (
        f"Warning: {_LATE_REFUSAL}\n         No prompt will be sent for it; "
        "send set_model to select a model.\n"
    ) in err
    by_id = {r["id"]: r for r in responses}
    held = by_id["s0"]["data"]["model"]
    assert (held["provider"], held["id"], held["api"]) == ("sessext", "m1", "unknown")
    assert by_id["p0"]["success"] is False
    assert (by_id["set"]["success"], _ref(by_id["set"]["data"])) == (True, "rec/m1")
    assert by_id["p1"]["success"] is True
    assert _ref(by_id["s1"]["data"]["model"]) == "rec/m1"
    assert wire == [("/rec/v1/chat/completions", "m1")]


# === (b) an unresolved launch exits 1, as -p does ==============================


_ROWS = [
    pytest.param(["--model", "newlab/model-x", "--api-key", "k-typed-371"], "", {}, id="api-key"),
    pytest.param(["--model", "newlab/model-x"], "", {}, id="unknown-prefix"),
    pytest.param(["--provider", "nope", "--model", "x"], "", {}, id="unknown-provider"),
    pytest.param(["--model", "m1"], "reca recb", {}, id="ambiguous"),
    # The id is the command line's; the settings default only failed to place
    # it (#98's split pair) — pi's ``--model`` error, so RPC exits too.
    pytest.param(
        ["--model", "zz"],
        "rec",
        {"defaultProvider": "gone", "defaultModel": "m9"},
        id="cli-model-beside-a-stale-default",
    ),
]


@pytest.mark.parametrize(("argv", "providers", "settings"), _ROWS)
async def test_an_unresolved_launch_exits_1_in_rpc_with_the_print_text(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str | None]],
    argv: list[str],
    providers: str,
    settings: dict[str, str],
) -> None:
    """The owner's decision (2026-10-20), for a launch model typed on the
    command line: the same error -p writes, on stderr, exit 1 before stdin is
    served — nothing on stdout, nothing sent. On ``7faf4b9a`` RPC was handed
    the placeholder (``code == 0``) and answered the first prompt with
    ``success: false``."""

    if providers:
        _write_models(env, *providers.split())
    if settings:
        (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    turns = _stub_print(monkeypatch)
    print_code = await entry_mod._async_main(["--no-session", *argv, "-p", "hi"])
    print_err = capsys.readouterr().err
    print_errors = [line for line in print_err.splitlines() if line.startswith("Error: ")]
    assert (print_code, turns, len(print_errors)) == (1, [], 1), print_err

    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main(["--no-session", *argv, "--mode", "rpc"])
    out, err = capsys.readouterr()
    assert (code, handed, out) == (1, [], "")
    assert [line for line in err.splitlines() if line.startswith("Error: ")] == print_errors
    assert "Warning:" not in err
    assert wire == []


_STALE_DEFAULTS = [
    pytest.param({"defaultProvider": "gone", "defaultModel": "m9"}, ("gone", "m9"), id="pair"),
    pytest.param({"defaultProvider": "gone"}, ("gone", ""), id="provider-only"),
    pytest.param({"defaultModel": "zzz"}, ("", "zzz"), id="model-only"),
]


@pytest.mark.parametrize(("settings", "placeholder"), _STALE_DEFAULTS)
async def test_a_settings_default_that_does_not_resolve_still_starts_rpc(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str | None]],
    settings: dict[str, str],
    placeholder: tuple[str, str],
) -> None:
    """Review round 2: the owner's decision is pi's ``--model`` rule, and a
    saved default is not ``--model`` — pi falls back from one it cannot
    resolve (``findInitialModel``) and never exits on it. So RPC starts on it
    as on ``7faf4b9a``: on the placeholder, each prompt refused and nothing
    sent, until ``switch_session`` reopens a session that can run (and then
    its prompt goes out) or ``set_model`` picks a model. -p and json are
    unchanged: they still exit 1 with their error. On ``4a7751c6`` RPC exited
    1 with -p's text before reading a command."""

    from tests.cli.test_session_model_restore_376 import _session

    _write_models(env, "rec")
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    turns = _stub_print(monkeypatch)
    for argv in (["-p", "hi"], ["--mode", "json", "hi"]):
        code = await entry_mod._async_main(["--no-session", *argv])
        errors = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Error: ")]
        assert (code, turns, len(errors)) == (1, [], 1), argv

    recorded = await _session(env, ("rec", "m1"))
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"type": "get_state", "id": "s0"},
            {"type": "prompt", "id": "p0", "message": "on the default"},
            {"type": "switch_session", "id": "switch", "sessionPath": recorded},
            {"type": "get_state", "id": "s1"},
            {"type": "prompt", "id": "p1", "message": "switched"},
            {"type": "set_model", "id": "set", "provider": "rec", "modelId": "m2"},
            {"type": "prompt", "id": "p2", "message": "picked"},
        ],
    )
    code = await entry_mod._async_main(
        ["--session-dir", str(env / "rpc-sessions"), "--mode", "rpc"]
    )
    out_err = capsys.readouterr()
    assert code == 0, out_err.err
    assert "Error:" not in out_err.err
    by_id = {r["id"]: r for r in responses}
    held = by_id["s0"]["data"]["model"]
    assert (held["provider"], held["id"], held["api"]) == (*placeholder, "unknown")
    assert by_id["p0"]["success"] is False
    assert by_id["switch"]["success"] is True
    assert _ref(by_id["s1"]["data"]["model"]) == "rec/m1"
    assert by_id["p1"]["success"] is True
    assert (by_id["set"]["success"], _ref(by_id["set"]["data"])) == (True, "rec/m2")
    assert by_id["p2"]["success"] is True
    assert wire == [("/rec/v1/chat/completions", "m1"), ("/rec/v1/chat/completions", "m2")]


@pytest.mark.parametrize(
    ("flags", "rpc_code"),
    [([], 0), (["--provider", "nope"], 1)],
    ids=["profile-alone", "beside-a-provider-flag"],
)
async def test_an_agent_profile_model_is_judged_by_the_flags_beside_it(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    rpc_code: int,
) -> None:
    """Only the command line's ``--model`` / ``--provider`` count (review round
    2). An agent profile's ``model:`` is not one (pi has no ``--agent``): RPC
    starts on one that does not resolve, as on ``7faf4b9a``. A ``--provider``
    typed beside it is, as pi's ``--provider`` is a command-line input: RPC
    exits 1 with -p's text. -p refuses both."""

    profile = env / "scout.md"
    profile.write_text(
        "---\nname: scout\ndescription: probe\nmodel: x\n---\nYou are SCOUT.\n", encoding="utf-8"
    )
    argv = ["--no-session", "--agent-file", str(profile), *flags]
    turns = _stub_print(monkeypatch)
    assert await entry_mod._async_main([*argv, "-p", "hi"]) == 1
    print_errors = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Error: ")]
    assert (turns, len(print_errors)) == ([], 1)

    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main([*argv, "--mode", "rpc"])
    rpc_errors = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Error: ")]
    assert code == rpc_code
    if rpc_code:
        assert (handed, rpc_errors) == ([], print_errors)
    else:
        assert rpc_errors == []
        assert [(h["model"].id, h["model"].api) for h in handed] == [("x", "unknown")]


# === kept distinct: what still starts ===========================================


_REC3_IN_SESSION_START = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        async def _on_start(event, ctx):
            aelix.register_provider(
                "rec3",
                ProviderConfigInput(
                    name="late twin",
                    api_key="rec3-fake-literal",
                    models={{"m1": Model(id="m1", provider="rec3", api="openai-completions",
                                         base_url="{_BASE}/rec3/v1")}},
                ),
            )

        aelix.on("session_start", _on_start)
    """
)


async def test_a_launch_that_resolved_before_session_start_made_it_ambiguous_starts_rpc(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str | None]],
) -> None:
    """#367 verify round 3, B1, in RPC: ``--model m1`` resolved to ``rec/m1``
    at launch (``models.json`` rec serves it), and the session stays there
    although ``rec3/m1``, registered in ``session_start``, makes the bare id
    ambiguous for any re-resolve after it. RPC's gate reads ``turn_route``
    with that fallback, as -p's does: it starts, and the prompt reaches
    ``rec/m1``. A gate on a fresh post-``session_start`` resolve exits 1 with
    "ambiguous across providers: rec/m1, rec3/m1" (round 1's S10)."""

    _write_models(env, "rec")
    ext = env / "rec3late.py"
    ext.write_text(_REC3_IN_SESSION_START, encoding="utf-8")
    argv = ["--no-session", "-e", str(ext), "--model", "m1"]

    turns = _stub_print(monkeypatch)
    assert await entry_mod._async_main([*argv, "-p", "hi"]) == 0
    assert [(m.provider, m.id) for m in turns] == [("rec", "m1")]
    capsys.readouterr()

    responses = _drive_real_rpc(
        monkeypatch,
        [{"type": "get_state", "id": "s0"}, {"type": "prompt", "id": "p0", "message": "hi"}],
    )
    code = await entry_mod._async_main([*argv, "--mode", "rpc"])
    err = capsys.readouterr().err
    assert code == 0, err
    assert "Error:" not in err
    by_id = {r["id"]: r for r in responses}
    model = by_id["s0"]["data"]["model"]
    assert (model["provider"], model["id"], model["api"]) == ("rec", "m1", "openai-completions")
    assert by_id["p0"]["success"] is True
    assert wire == [("/rec/v1/chat/completions", "m1")]


_EARLY_AND_LATE = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def _provider(name):
        return ProviderConfigInput(
            name=name,
            api_key=name + "-fake-literal",
            models={{"m1": Model(id="m1", provider=name, api="openai-completions",
                                 base_url="{_BASE}/" + name + "/v1")}},
        )

    def setup(aelix):
        aelix.register_provider("early", _provider("early"))

        async def _on_start(event, ctx):
            aelix.register_provider("late", _provider("late"))

        aelix.on("session_start", _on_start)
    """
)


@pytest.mark.parametrize("launch", ["late/m1", "rec/m1"], ids=["held", "registered"])
async def test_rpc_lists_and_reaches_the_providers_extensions_registered(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str | None]],
    launch: str,
) -> None:
    """The registry RPC gets is the launch's own, with the extension providers
    replayed onto it — ``early`` from ``setup()``, ``late`` from
    ``session_start`` — not a fresh one that reads only ``models.json``
    (round 1's S14: both were missing from the list and ``set_model`` said
    "Model not found"). On the ``held`` launch (its inputs land on ``late``,
    #367) ``set_model`` leaves the hold for either."""

    _write_models(env, "rec")
    ext = env / "earlylate.py"
    ext.write_text(_EARLY_AND_LATE, encoding="utf-8")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"type": "get_available_models", "id": "list"},
            {"type": "set_model", "id": "set-early", "provider": "early", "modelId": "m1"},
            {"type": "prompt", "id": "p-early", "message": "early"},
            {"type": "set_model", "id": "set-late", "provider": "late", "modelId": "m1"},
            {"type": "prompt", "id": "p-late", "message": "late"},
        ],
    )
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", launch, "--mode", "rpc"]
    )
    err = capsys.readouterr().err
    assert code == 0, err
    assert ("send set_model to select a model." in err) is (launch == "late/m1")
    by_id = {r["id"]: r for r in responses}
    listed = {_ref(m) for m in by_id["list"]["data"]["models"]}
    assert {"rec/m1", "early/m1", "late/m1"} <= listed
    for name in ("early", "late"):
        assert (by_id[f"set-{name}"]["success"], _ref(by_id[f"set-{name}"]["data"])) == (
            True,
            f"{name}/m1",
        )
        assert by_id[f"p-{name}"]["success"] is True
    assert wire == [("/early/v1/chat/completions", "m1"), ("/late/v1/chat/completions", "m1")]


@pytest.mark.parametrize("typed", [False, True], ids=["no-key", "api-key"])
async def test_the_367_hold_still_starts_rpc(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: bool,
) -> None:
    """Not unresolved: the inputs land on a provider ``session_start``
    registered — with ``--api-key`` the launch route was even "not found" at
    first — and the session starts held, as since #367, handed the registry."""

    handed = _stub_rpc(monkeypatch)
    key = ["--api-key", "k-typed-371"] if typed else []
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--model", "sessext/m1", *key]
        + ["--mode", "rpc"]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert [(h["model"].provider, h["model"].id, h["model"].api) for h in handed] == [
        ("sessext", "m1", "unknown")
    ]
    assert handed[0]["model_registry"] is not None
    assert f"Warning: {_LATE_REFUSAL}" in err
    assert "Error:" not in err


_NO_ADAPTER = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        aelix.register_provider(
            "weird",
            ProviderConfigInput(
                name="no adapter",
                api_key="weird-fake-literal",
                models={{"m1": Model(id="m1", provider="weird", api="weird-api",
                                     base_url="{_BASE}/weird/v1")}},
            ),
        )
    """
)


async def test_a_launch_with_no_model_still_starts_rpc(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing to resolve: no ``--model``, no default, no credential. The
    client can pick one with ``set_model``; its first prompt says "No model
    selected", as before. (pi exits 1 here; outside the owner's decision.)"""

    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--mode", "rpc"])
    assert code == 0
    assert len(handed) == 1
    assert "Error:" not in capsys.readouterr().err


async def test_a_resolved_model_without_an_adapter_still_starts_rpc(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resolved — a provider ``setup()`` registered serves it — but its api has
    no adapter. -p refuses it at its #98 gate; pi starts on it, and so does RPC
    (its prompt is refused, ``set_model`` leaves it)."""

    ext = env / "weird.py"
    ext.write_text(_NO_ADAPTER, encoding="utf-8")
    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "weird/m1", "--mode", "rpc"]
    )
    assert code == 0
    assert [(h["model"].provider, h["model"].id, h["model"].api) for h in handed] == [
        ("weird", "m1", "weird-api")
    ]
    assert "Error:" not in capsys.readouterr().err


_MOVER = textwrap.dedent(
    """
    from aelix_ai.streaming import Model

    def setup(aelix):
        async def _on_start(event, ctx):
            await aelix.set_model(Model(id="ghost", provider="ghostprov"))

        aelix.on("session_start", _on_start)
    """
)


async def test_a_model_a_session_start_handler_moved_to_is_not_judged_as_the_launch(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--model rec/m1`` resolves; a ``session_start`` handler then moves the
    session to ``ghostprov/ghost``, which nothing serves. The gate judges the
    launch inputs' route (pi's error is ``resolveCliModel``'s, about
    ``--model``), not the model the extension set: RPC starts on it and its
    prompt is refused, as before. (Round 1 held this through a flagless row
    in ``test_session_model_round3_376.py``; since round 2 a flagless launch
    never reaches the gate, so this row keeps the gate off the moved model.)"""

    _write_models(env, "rec")
    ext = env / "mover.py"
    ext.write_text(_MOVER, encoding="utf-8")
    handed = _stub_rpc(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "rec/m1", "--mode", "rpc"]
    )
    assert code == 0
    assert [(h["model"].provider, h["model"].id, h["model"].api) for h in handed] == [
        ("ghostprov", "ghost", "unknown")
    ]
    assert "Error:" not in capsys.readouterr().err


async def test_rpc_asks_the_same_runnability_question_as_print(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``api='unknown'`` arm asks ``turn_refusal``, as -p's gate does, so
    the two agree where it fails open (an embedder that registered no api
    adapter): a launch -p lets through is not refused by RPC either."""

    monkeypatch.setattr(entry_mod, "turn_refusal", lambda model: None)
    turns = _stub_print(monkeypatch)
    argv = ["--no-session", "--provider", "nope", "--model", "x"]
    assert await entry_mod._async_main([*argv, "-p", "hi"]) == 0
    assert [(m.provider, m.id) for m in turns] == [("nope", "x")]
    handed = _stub_rpc(monkeypatch)
    assert await entry_mod._async_main([*argv, "--mode", "rpc"]) == 0
    assert [(h["model"].provider, h["model"].id) for h in handed] == [("nope", "x")]
    assert "Error:" not in capsys.readouterr().err
