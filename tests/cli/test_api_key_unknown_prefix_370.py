"""#370 / ADR-0250 §2.4, §2.7 — ``--api-key`` never rides guard 2 to OpenRouter.

On ``62e2238b`` ``--model newlab/model-x --api-key K`` with the user's own
``OPENROUTER_API_KEY`` exported went to ``openrouter.ai`` as written (guard 2),
and K was attached to ``openrouter``: the prompt carried the TYPED key as the
bearer, in print, json, RPC and the TUI. pi has no guard 2 —
``resolveCliModel`` ends "not found" for a prefix no provider has
(``model-resolver.ts`` :599-605 @ b223082bb) and ``main.ts`` attaches
``--api-key`` only to a model that resolved. Now:

* print/json exit 1 with pi's text and the two explicit OpenRouter routes,
  nothing sent, no key attached (with or without an OpenRouter key of the
  user's own; an OpenRouter vendor namespace with an id the snapshot does not
  list, too);
* a provider an extension registered and unregistered in ``session_start``
  (gone before the late decision) is the same not-found;
* interactive and RPC hold the unresolved placeholder (aelix's stated
  divergence, ADR-0250 §2.4) — interactive with the Warning — and the first
  prompt, ``/new`` and ``/reload`` send nothing;
* unchanged: ``openrouter/<id>``, ``--provider openrouter`` and an id
  OpenRouter's catalogue lists (``x-ai/grok-4.3``, pi's step 2) take the typed
  key to OpenRouter; without ``--api-key`` guard 2 still sends the string with
  the user's own key.

Drives the real ``_async_main`` (the mode entry stubbed where a mode would
block) with every HTTP request recorded by an ``httpx.MockTransport`` — fake
keys, an isolated agent dir, nothing leaves the process.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli import test_late_provider_landing_367 as _l367
from tests.cli.test_late_provider_refused_367 import _REFUSAL, _openrouter
from tests.cli.test_launch_route_344 import _FakeTTYStdin

# The #367 rows' isolated environment, wire recorder and ``--api-key`` spy.
env = _l367.env
wire = _l367.wire
attached = _l367.attached

_TYPED = "k-typed-370"
_PLACEHOLDER = ("newlab", "model-x", "unknown")


def _typed_not_found(model: str) -> str:
    return (
        f'Model "{model}" not found. Use --list-models to see available models. (--api-key '
        "does not send an id this build does not know to OpenRouter; to send it there with "
        f"that key, use --model openrouter/{model} or --provider openrouter --model {model}.)"
    )


#: ``sessext`` registered in ``session_start`` and unregistered before it returns
#: (the issue's "late gone" shape): no provider has the prefix at the decision.
_GONE = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        async def _on_start(event, ctx):
            aelix.register_provider("sessext", ProviderConfigInput(name="probe",
                api_key="ext-fake-literal", models={{"m1": Model(id="m1", provider="sessext",
                api="openai-completions", base_url={_l367._EXT!r})}}))
            aelix.unregister_provider("sessext")
        aelix.on("session_start", _on_start)
    """
)


def _args(
    model: list[str], *, ext: str | None = None, typed: bool = True, session: bool = False
) -> list[str]:
    return [
        *(["--approve"] if session else ["--no-session"]),
        *(["-e", ext] if ext else []),
        *model,
        *(["--api-key", _TYPED] if typed else []),
    ]


@pytest.mark.parametrize("mode", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
@pytest.mark.parametrize(
    ("openrouter", "model"),
    [
        ("exported", "newlab/model-x"),
        ("none", "newlab/model-x"),
        ("exported", "x-ai/grok-4"),
        # Round 2 (Codex cat 4): a nested id. Guard 2's shape takes every
        # slashed string whose segments are non-empty, so a "two segments
        # only" fix (``model_flag.count("/") > 1`` left on guard 2) or a hint
        # limited to one slash passed every row above.
        ("exported", "newlab/org/model-x"),
    ],
    ids=[
        "own-openrouter-key",
        "no-openrouter-key",
        "openrouter-namespace-unlisted-id",
        "nested-id",
    ],
)
async def test_print_and_json_refuse_a_string_no_provider_places_under_api_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
    mode: list[str],
    openrouter: str,
    model: str,
) -> None:
    """The issue's A-print / A-json / H / I rows. On 62e2238b the first and the
    third went to ``/api/v1/chat/completions`` with "Bearer k-typed-370" and
    exit 0, after guard 2's Note; the second exited 1 with pi's bare text."""

    _openrouter(monkeypatch, env, openrouter)
    code = await entry_mod._async_main([*_args(["--model", model]), *mode, "hi"])
    err = capsys.readouterr().err
    assert (code, attached, wire) == (1, [], [])
    assert f"Error: {_typed_not_found(model)}" in err
    assert "as written" not in err


async def test_a_string_with_an_empty_segment_gets_the_bare_not_found(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
) -> None:
    """Round 2 (Codex cat 2), decided: the hint names the OpenRouter routes only
    for a string guard 2's shape takes — every slashed string whose segments are
    non-empty (ADR-0250 §2.12, §7 item 13). ``newlab//model-x`` cannot be an
    OpenRouter id, so naming OpenRouter's routes for it would mislead: pi's bare
    not-found, nothing sent, no key attached."""

    _openrouter(monkeypatch, env, "exported")
    code = await entry_mod._async_main([*_args(["--model", "newlab//model-x"]), "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, attached, wire) == (1, [], [])
    assert (
        'Error: Model "newlab//model-x" not found. Use --list-models to see available models.\n'
    ) in err
    assert "--api-key does not send" not in err


@pytest.mark.parametrize(
    "model",
    [
        ["--model", "openrouter/newlab/model-x"],
        ["--provider", "openrouter", "--model", "newlab/model-x"],
        ["--model", "x-ai/grok-4.3"],
    ],
    ids=["openrouter-prefix", "provider-openrouter", "catalogued-openrouter-id"],
)
async def test_explicit_and_catalogued_openrouter_routes_keep_the_typed_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[tuple[str, str]],
    attached: list[str],
    model: list[str],
) -> None:
    """Unchanged by #370: the user named OpenRouter, or OpenRouter's catalogue
    lists the whole string (pi's step 2 exact hit, ``model-resolver.ts``
    :465-504) — the typed key goes there, over the user's own OpenRouter key."""

    _openrouter(monkeypatch, env, "exported")
    await entry_mod._async_main([*_args(model), "-p", "hi"])
    assert attached == ["openrouter"]
    assert [auth for _url, auth in wire] == [f"Bearer {_TYPED}"]
    assert wire[0][0].startswith("https://openrouter.ai/api/v1/")


async def test_guard_two_without_api_key_still_sends_with_the_users_own_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
) -> None:
    """Guard 2 is unchanged without ``--api-key`` (ADR-0250 §2.4): the user's
    own OpenRouter key, the Note."""

    _openrouter(monkeypatch, env, "exported")
    await entry_mod._async_main([*_args(["--model", "newlab/model-x"], typed=False), "-p", "hi"])
    assert attached == []
    assert [auth for _url, auth in wire] == ["Bearer or-fake-literal"]
    assert "sending it to OpenRouter as written" in capsys.readouterr().err


async def test_a_provider_gone_by_the_late_decision_is_not_found_under_api_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
) -> None:
    """The issue's C-gone row. ``sessext`` is registered and unregistered in
    ``session_start``: at the late decision no provider has the prefix, so the
    launch route stands — guard 2 on 62e2238b, the typed key attached to
    ``openrouter`` after the decision and sent there. Now pi's not-found."""

    _openrouter(monkeypatch, env, "exported")
    ext = _l367._write(env, "gone.py", _GONE)
    code = await entry_mod._async_main([*_args(["--model", "sessext/m1"], ext=ext), "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, attached, wire) == (1, [], [])
    assert f"Error: {_typed_not_found('sessext/m1')}" in err


@pytest.mark.parametrize("mode", ["interactive", "rpc"])
@pytest.mark.parametrize("shape", ["unknown-prefix", "nested", "gone", "late"])
async def test_interactive_and_rpc_hold_the_placeholder_and_send_nothing(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wire: list[tuple[str, str]],
    attached: list[str],
    mode: str,
    shape: str,
) -> None:
    """Interactive keeps aelix's stated divergence (ADR-0250 §2.4: a Warning and
    a held placeholder where pi exits 1); RPC keeps today's unresolved-route
    shape (its silent start is #371). The first prompt, ``/new`` and
    ``/reload`` send nothing and no key is attached. ``late``: ``sessext``
    registered only in ``session_start`` — #367's hold, unchanged."""

    _openrouter(monkeypatch, env, "exported")
    model = "newlab/model-x"
    ext = None
    if shape == "nested":
        model = "newlab/org/model-x"
    elif shape == "gone":
        model, ext = "sessext/m1", _l367._write(env, "gone.py", _GONE)
    elif shape == "late":
        model, ext = "sessext/m1", _l367._write(env, "late.py", _l367._extension())
    seen: list[tuple[str, Any]] = []

    async def _drive(runtime: Any) -> None:
        seen.append(("launch", _l367._route(runtime.harness.current_model)))
        seen.append(("prompt", bool(await _l367._prompt_refused(runtime.harness))))
        for step in ("new", "reload"):
            await (runtime.new_session() if step == "new" else runtime.reload())
            seen.append((step, _l367._route(runtime.harness.current_model)))

    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            await _drive(runtime)
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        flags: list[str] = []
    else:
        from aelix_coding_agent import modes

        async def _stub_run_rpc(harness: Any, **kwargs: Any) -> None:
            await _drive(kwargs["runtime_host"])

        monkeypatch.setattr(modes, "run_rpc_mode", _stub_run_rpc)
        flags = ["--mode", "rpc"]
    # A session (not ``--no-session``): ``/new`` needs its cwd.
    code = await entry_mod._async_main([*_args(["--model", model], ext=ext, session=True), *flags])
    err = capsys.readouterr().err
    held = (model.partition("/")[0], model.partition("/")[2], "unknown")
    assert code == 0
    assert seen == [
        ("launch", held),
        ("prompt", True),
        ("new", held),
        ("reload", held),
    ]
    assert (attached, wire) == ([], [])
    if shape == "late":
        assert f"Warning: {_REFUSAL.format(subject=model)}" in err
    elif mode == "interactive":
        assert (
            f"Warning: {_typed_not_found(model)}\n         Run /model to select a working model."
        ) in err
    else:
        assert "as written" not in err


@pytest.mark.parametrize(
    "profile",
    ["nlprof", "plain", None],
    ids=["profile-model-beaten-by-flag", "model-less-profile", "none"],
)
async def test_agents_use_refuses_the_launch_string_as_the_launch_did(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[tuple[str, str]],
    attached: list[str],
    profile: str | None,
) -> None:
    """The #370 sweep: ``/agents use`` re-resolves the launch inputs (a profile
    with a ``model:`` of its own does not beat an explicit ``--model``), and it
    was the one re-resolution not told about ``--api-key``. After a launch
    held on the not-found placeholder it took guard 2 to OpenRouter on the
    user's own key (measured on the fix before this line: "after /agents use
    --none: route=(openrouter, newlab/model-x, ...) requests=[... OR_OWN]"),
    and the next ``/new`` put the placeholder back. It now refuses with the
    launch's text and rolls back, as the factory re-resolves — for ``--none``,
    a profile with no ``model:``/``provider:`` (round 2), and one whose
    ``model:`` the explicit ``--model`` beat: each re-resolves the launch
    inputs. On 62e2238b all three went to OpenRouter on the user's own key."""

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "nlprof.md").write_text(
        "---\nname: nlprof\ndescription: p\nmodel: newlab/model-x\n---\nx\n", encoding="utf-8"
    )
    (agents / "plain.md").write_text("---\nname: plain\ndescription: p\n---\nx\n", encoding="utf-8")
    seen: list[Any] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        try:
            await kwargs["agent_service"].use(profile, harness=runtime.harness)
        except Exception as exc:  # noqa: BLE001 — the refusal is the subject
            seen.append(str(exc))
        seen.append(_l367._route(runtime.harness.current_model))
        seen.append(bool(await _l367._prompt_refused(runtime.harness)))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main([*_args(["--model", "newlab/model-x"], session=True)])
    assert code == 0
    assert seen == [_typed_not_found("newlab/model-x"), _PLACEHOLDER, True]
    assert (attached, wire) == ([], [])


async def test_agents_use_of_a_profiles_own_model_resolves_as_before_api_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[tuple[str, str]],
    attached: list[str],
) -> None:
    """Round 2, the scope of ``/agents use``'s ``typed_key``: only a re-resolve
    of the LAUNCH inputs is told about ``--api-key``. A profile's own
    ``model:`` is a new pick, not the string the key was typed with, so it
    resolves as on 62e2238b: the settings pair launches on anthropic (the
    typed key attached there), ``/agents use nlown`` (``model:
    newlab/model-x``) takes guard 2 to OpenRouter on the user's OWN key, and
    the typed key is never sent to OpenRouter — through the next ``/new`` too.
    Round 1 told every resolve here about the key and refused this profile."""

    import json

    import aelix_coding_agent.tui as tui_pkg

    _openrouter(monkeypatch, env, "exported")
    (env / "agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "anthropic", "defaultModel": "claude-haiku-4-5"}),
        encoding="utf-8",
    )
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "nlown.md").write_text(
        "---\nname: nlown\ndescription: p\nmodel: newlab/model-x\n---\nx\n", encoding="utf-8"
    )
    seen: list[Any] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen.append(_l367._route(runtime.harness.current_model))
        status = await kwargs["agent_service"].use("nlown", harness=runtime.harness)
        seen.append(status.splitlines()[0])
        seen.append(_l367._route(runtime.harness.current_model))
        await _l367._prompt_refused(runtime.harness)
        await runtime.new_session()
        seen.append(_l367._route(runtime.harness.current_model))
        await _l367._prompt_refused(runtime.harness)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--approve", "--api-key", _TYPED])
    on_openrouter = ("openrouter", "newlab/model-x", "openai-completions")
    assert code == 0
    assert seen == [
        ("anthropic", "claude-haiku-4-5", "anthropic-messages"),
        "Agent profile: nlown (user)",
        on_openrouter,
        on_openrouter,
    ]
    assert attached == ["anthropic"]
    assert wire == [("https://openrouter.ai/api/v1/chat/completions", "Bearer or-fake-literal")] * 2
