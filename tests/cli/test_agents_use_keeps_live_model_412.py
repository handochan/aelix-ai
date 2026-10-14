"""#412 — ``/agents use`` of a profile with no ``model:``/``provider:`` keeps the model you are on.

Owner decision 2026-10-14, option A. ``AgentProfileService.use`` resets ``parsed``
to the launch baseline, and the baseline holds settings ``defaultModel`` /
``defaultProvider`` when neither ``--model`` nor ``--provider`` was typed
(``cli/entry.py``'s back-fill).
A use where no ``model:``/``provider:`` of the profile applied (none named, or a
typed flag beat each one named; ``--none``) then re-resolved the baseline and
``set_model``-ed it. In a restored session (#376) with neither flag typed that
left the restored model for the settings pair, ``set_model`` recorded the move,
and the next reopen came back on the settings model; in any session it reverted
a ``/model`` pick, against ADR-0196 D8 (with ``--model`` typed, to that flag).
Measured on ``529b997e`` in the real TUI (``.omc/probes/412-live/impl/``):
restored ``claude-haiku-4-5``, ``/agents use plain`` → footer
``claude-sonnet-4-5``, the next prompt sent as sonnet, ``model_change`` sonnet
appended, ``--continue`` → sonnet.

Now such a use keeps the live model — no resolve, no ``set_model``, no
``model_change`` — and ``parsed`` still returns to the baseline, so ``/new``
follows the launch inputs. A profile whose ``model:`` a typed ``--model`` beat
but whose ``provider:`` applied names a route and switches, as before. A use
that DROPS a profile's route (the launch's ``--agent`` or an earlier use applied
its own ``model:``/``provider:``) re-resolves the baseline as before; with an
empty baseline there is nothing to re-resolve and the profile's model stays
(review round 3: with the kept model's note). ``--none`` says "the model is
unchanged" wherever it left the model in place. The thinking level is not
covered: a level ``--thinking`` or the profile's ``thinking:`` names is
re-applied on every path (section 7). When the kept model will have its prompts
refused, the status says why with the prompt check's own text (section 6) —
the reason the harness's check gives; the TUI prompt line's own #189 gate,
which words a structurally unrunnable model its own way, is not driven here.

These drive the real ``_async_main`` (the TUI entry stubbed, so no turn runs and
nothing is sent) over sessions written by a real ``JsonlSessionRepo``, in the
sandbox of ``test_session_model_restore_376.py``. Hermetic: fake keys, an
isolated agent dir and home, no network.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.cli.test_session_model_restore_376 import (
    _HAIKU,
    _SONNET,
    _ident,
    _session,
    _sessions,
    _settings,
    _stub_modes,
)
from tests.cli.test_session_start_model_413 import _entries, _fail_one_turn, _files, _records
from tests.env_sandbox import sandbox_home

_PROFILES = {
    "plain": "---\nname: plain\ndescription: names no model\ninherit_extensions: true\n---\nplain\n",
    "plain2": "---\nname: plain2\ndescription: names no model\ninherit_extensions: true\n---\np2\n",
    "haikuprof": (
        "---\nname: haikuprof\ndescription: names a model\nprovider: anthropic\n"
        "model: claude-haiku-4-5\ninherit_extensions: true\n---\nhaiku\n"
    ),
    "modelonly": (
        "---\nname: modelonly\ndescription: names a model only\n"
        "model: claude-opus-4-1\ninherit_extensions: true\n---\nm\n"
    ),
    "opusprov": (
        "---\nname: opusprov\ndescription: names both\nprovider: anthropic\n"
        "model: claude-opus-4-1\ninherit_extensions: true\n---\no\n"
    ),
    "thinker": (
        "---\nname: thinker\ndescription: names a level only\nthinking: high\n"
        "inherit_extensions: true\n---\nt\n"
    ),
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_session_model_restore_376.py``'s sandbox, plus three user profiles."""

    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    (agent / "agents").mkdir(parents=True)
    (agent / "models.json").write_text("{}", encoding="utf-8")
    for name, body in _PROFILES.items():
        (agent / "agents" / f"{name}.md").write_text(body, encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    register_providers()
    return tmp_path


def _registry_model(registry: Any, provider: str, model_id: str) -> Any:
    """The registry's own model, as the ``/model`` picker hands it to ``set_model``."""

    found = next(
        (m for m in registry.get_all() if (m.provider, m.id) == (provider, model_id)), None
    )
    assert found is not None, (provider, model_id)
    return found


def _embedder_service(live: Any) -> Any:
    """An ``AgentProfileService`` as an embedder builds one: the live service's
    ``Args`` (copies) and skills, but no ``model_registry`` and no ``late_route``."""

    from aelix_coding_agent.agents.service import AgentProfileService

    return AgentProfileService(
        cwd=live.cwd,
        project_trusted=live.project_trusted,
        parsed=copy.deepcopy(live.parsed),
        baseline=copy.deepcopy(live.baseline),
        skills_holder=dict(live.skills_holder),
        agent_dir=live.agent_dir,
    )


async def _tui(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], steps: list[str]
) -> dict[str, Any]:
    """Launch interactive (the TUI stubbed); per step: ``/agents use <name>``,
    ``--none``, ``pick:<provider>/<id>`` (a ``/model`` pick), ``think:<level>``
    (a ``/thinking`` pick), ``embedder:<name>`` (``use`` on a service built as
    an embedder builds it: no registry, no late route), ``new``, or
    ``fail-turn`` (#413's turn that failed before the request: a conversation
    whose answer names no model, so a reopen restores from the records). Also
    records the level after each step and what the prompt check the factory
    installed says of the final model (``refusal``) — asked directly, so no
    prompt runs."""

    import aelix_coding_agent.tui as tui_pkg

    seen: dict[str, Any] = {"after": [], "status": [], "thinking": []}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen["launch"] = _ident(runtime.harness.current_model)
        seen["file"] = getattr(runtime.harness.session, "session_file", None)
        for step in steps:
            if step == "new":
                await runtime.new_session()
            elif step == "fail-turn":
                await _fail_one_turn(runtime)
            elif step.startswith("pick:"):
                provider, _, model_id = step[len("pick:") :].partition("/")
                model = _registry_model(kwargs["model_registry"], provider, model_id)
                await runtime.harness.set_model(model)
            elif step.startswith("think:"):
                await runtime.harness.set_thinking_level(step[len("think:") :])
            elif step.startswith("embedder:"):
                service = _embedder_service(kwargs["agent_service"])
                seen["status"].append(
                    await service.use(step[len("embedder:") :], harness=runtime.harness)
                )
            else:
                name = None if step == "--none" else step
                seen["status"].append(
                    await kwargs["agent_service"].use(name, harness=runtime.harness)
                )
            seen["after"].append(_ident(runtime.harness.current_model))
            seen["thinking"].append(runtime.harness.state.thinking_level)
        check = runtime.harness._prompt_check
        assert check is not None
        seen["refusal"] = check(runtime.harness.state.model)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(argv)
    assert code == 0
    return seen


async def _reopened(monkeypatch: pytest.MonkeyPatch, session_file: str) -> tuple[str, str]:
    """What a reopen of that file comes back on (rpc, no model flags)."""

    seen = _stub_modes(monkeypatch)
    code = await entry_mod._async_main(["--session", session_file, "--mode", "rpc"])
    assert code == 0
    return _ident(seen["model"])


# === 1. the issue: a restored session keeps its model =========================


@pytest.mark.parametrize(
    "steps",
    [["plain"], ["--none"], ["plain", "--none"], ["plain", "plain2"]],
    ids=["model-less-profile", "none", "profile-then-none", "profile-then-profile"],
)
async def test_a_restored_session_keeps_its_model_through_an_agents_use_naming_no_route(
    env: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    """THE DEFECT. The session ran on haiku; settings name the pair sonnet, so
    the baseline ``/agents use`` resets to is sonnet. On 529b997e every row
    left haiku for sonnet, appended ``model_change`` sonnet, and the reopen
    came back on sonnet. Now the session stays on haiku, the file gains no
    record, and the reopen comes back on haiku again. The two-step rows: a
    route-less profile replaced by another (or ``--none``) has no route to
    drop — a "previous profile exists" test in place of "its route applied"
    re-resolves there."""

    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    path = await _session(env, _HAIKU)
    seen = await _tui(
        monkeypatch, ["--continue", "--session-dir", _sessions(env), "--approve"], steps
    )
    # ``samefile``, not a string compare: on Windows a temp root can reach the two
    # sides spelled differently (an 8.3 short name on one, the long one on the other).
    assert seen["file"] is not None and os.path.samefile(seen["file"], path)
    assert seen["launch"] == _HAIKU
    assert seen["after"] == [_HAIKU] * len(steps)
    assert not any("Note:" in status for status in seen["status"])
    assert _records(path) == []
    assert await _reopened(monkeypatch, path) == _HAIKU


async def test_after_a_route_less_agents_use_new_still_follows_the_launch_inputs(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``parsed`` still returns to the baseline: the use keeps the restored
    model live, and ``/new`` starts where the launch inputs say (the settings
    pair, #376: ``/new`` keeps the launch inputs) — recording that start model
    in the new file (#413)."""

    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    path = await _session(env, _HAIKU)
    seen = await _tui(
        monkeypatch,
        ["--continue", "--session-dir", _sessions(env), "--approve"],
        ["plain", "new"],
    )
    assert seen["after"] == [_HAIKU, _SONNET]
    assert _records(path) == []
    newest = [f for f in _files(env) if not os.path.samefile(f, path)]
    assert len(newest) == 1 and _records(newest[0]) == [_SONNET]


# === 2. a fresh session: no second record, and a /model pick survives ===========


@pytest.mark.parametrize(
    "argv",
    [["--provider", "anthropic", "--model", "claude-haiku-4-5"], []],
    ids=["typed-flags", "settings-pair"],
)
async def test_a_fresh_session_writes_no_second_record_for_an_agents_use_naming_no_route(
    env: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """#413 records the start model; on 529b997e each route-less use then
    re-resolved the same inputs and ``set_model`` appended the same model
    again (``[haiku, haiku, haiku]``). Now the start record stays alone."""

    _settings(env, defaultProvider=_HAIKU[0], defaultModel=_HAIKU[1])
    seen = await _tui(
        monkeypatch, ["--session-dir", _sessions(env), "--approve", *argv], ["plain", "--none"]
    )
    assert seen["after"] == [_HAIKU, _HAIKU]
    assert _records(str(seen["file"])) == [_HAIKU]


async def test_a_model_pick_survives_an_agents_use_naming_no_route(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0196 D8's promise, which the baseline back-fill broke: a ``/model``
    pick (haiku, over the settings pair sonnet) survives ``/agents use plain``
    and ``--none``; the file records the start model and the pick, and a
    reopen comes back on the pick (the conversation is #413's failed turn, so
    the records decide). On 529b997e both uses reverted to sonnet, each
    recorded, and the reopen came back on sonnet."""

    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    seen = await _tui(
        monkeypatch,
        ["--session-dir", _sessions(env), "--approve"],
        ["pick:anthropic/claude-haiku-4-5", "plain", "--none", "fail-turn"],
    )
    assert seen["launch"] == _SONNET
    assert seen["after"] == [_HAIKU] * 4
    path = str(seen["file"])
    assert _records(path) == [_SONNET, _HAIKU]
    assert await _reopened(monkeypatch, path) == _HAIKU


# === 3. dropping a profile's route still re-resolves the baseline ===============


@pytest.mark.parametrize(
    ("argv", "steps", "after"),
    [
        ([], ["haikuprof", "plain"], [_HAIKU, _SONNET]),
        ([], ["haikuprof", "--none"], [_HAIKU, _SONNET]),
        (["--agent", "haikuprof"], ["plain"], [_SONNET]),
        (["--agent", "haikuprof"], ["--none"], [_SONNET]),
        (
            ["--agent", "haikuprof"],
            ["plain", "pick:anthropic/claude-haiku-4-5", "--none"],
            [_SONNET, _HAIKU, _HAIKU],
        ),
    ],
    ids=[
        "earlier-use-then-profile",
        "earlier-use-then-none",
        "launch-agent-then-profile",
        "launch-agent-then-none",
        "launch-agent-dropped-once",
    ],
)
async def test_an_agents_use_that_drops_a_profiles_route_re_resolves_the_baseline(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    steps: list[str],
    after: list[tuple[str, str]],
) -> None:
    """Kept from before #412: when the profile being replaced applied its own
    ``model:``/``provider:`` — by an earlier use or the launch's ``--agent`` —
    the live model is the profile's, so a use naming no route re-resolves the
    baseline (the settings pair, sonnet) and records it. Once dropped, there
    is nothing left to drop: the last row's ``/model`` pick survives the
    following ``--none``."""

    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    seen = await _tui(monkeypatch, ["--session-dir", _sessions(env), "--approve", *argv], steps)
    assert seen["after"] == after


@pytest.mark.parametrize("step", ["plain", "--none"])
async def test_a_profile_whose_route_a_cli_flag_beat_leaves_no_route_to_drop(
    env: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """ "Dropping a route" means the replaced profile's ``model:``/``provider:``
    APPLIED — the overlay's own answer, over the baseline's ``provided``. Here
    ``--agent sonnetprof`` names a model and a provider and both typed flags beat
    them ("CLI flags override model, provider"), so the launch runs on haiku and
    the profile put nothing on the model: a ``/model`` pick (sonnet) then
    survives a use naming no route. A test that ignored the typed flags would
    re-resolve the baseline here and revert the pick to haiku."""

    agents = env / "agent" / "agents"
    (agents / "sonnetprof.md").write_text(
        "---\nname: sonnetprof\ndescription: d\nprovider: anthropic\n"
        "model: claude-sonnet-4-5\ninherit_extensions: true\n---\nsonnet\n",
        encoding="utf-8",
    )
    seen = await _tui(
        monkeypatch,
        [
            "--session-dir",
            _sessions(env),
            "--approve",
            "--provider",
            "anthropic",
            "--model",
            "claude-haiku-4-5",
            "--agent",
            "sonnetprof",
        ],
        ["pick:anthropic/claude-sonnet-4-5", step],
    )
    assert seen["launch"] == _HAIKU
    assert seen["after"] == [_SONNET, _SONNET]


#: Review round 3 — two route-less profiles WITHOUT ``inherit_extensions: true``.
#: The field defaults to ``false``, so their overlay applies ``no_extensions``
#: (the shape most real profiles have); every profile in ``_PROFILES`` sets it.
_DEFAULT_FIELD_PROFILES = {
    "reviewer": "---\nname: reviewer\ndescription: no route, default fields\n---\nreview\n",
    "writer": "---\nname: writer\ndescription: no route, default fields\n---\nwrite\n",
}


@pytest.mark.parametrize(
    "steps",
    [["reviewer", "writer"], ["thinker", "--none"]],
    ids=["profile-applying-other-fields-replaced", "thinking-only-profile-then-none"],
)
async def test_only_a_model_or_provider_that_applied_is_a_route_to_drop(
    env: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    """Review round 3 (cross 3). "Dropping a route" counts the replaced
    profile's ``model:`` and ``provider:`` and nothing else it applied.
    ``reviewer`` applies ``no_extensions`` (no ``inherit_extensions: true``),
    ``thinker`` applies ``thinking: high``; neither names a model or a
    provider, so replacing either drops nothing and the restored haiku stays,
    unrecorded, through the reopen. A drop rule that counted any applied field
    (or ``thinking``) took the drop branch here: it re-resolved the settings
    pair, moved the session to sonnet and recorded it — #412 again, for the
    profiles users actually write."""

    agents = env / "agent" / "agents"
    for name, body in _DEFAULT_FIELD_PROFILES.items():
        (agents / f"{name}.md").write_text(body, encoding="utf-8")
    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    path = await _session(env, _HAIKU)
    seen = await _tui(
        monkeypatch, ["--continue", "--session-dir", _sessions(env), "--approve"], steps
    )
    assert seen["launch"] == _HAIKU
    assert seen["after"] == [_HAIKU, _HAIKU]
    assert _records(path) == []
    assert await _reopened(monkeypatch, path) == _HAIKU


#: The ``--none`` status: the baseline's prompt, tools and skills come back,
#: and the model stays where it is unless the use dropped a route it could
#: re-resolve (review round 3, cross 5).
_CLEARED = "Agent profile cleared (baseline CLI identity restored)."
_CLEARED_MODEL_KEPT = (
    "Agent profile cleared (baseline CLI identity restored; the model is unchanged)."
)


@pytest.mark.parametrize("step", ["plain", "--none"])
async def test_a_drop_with_nothing_to_re_resolve_keeps_the_profiles_model_and_says_why_it_cannot_run(
    env: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Review round 3 (cross 2). No ``--model``/``--provider`` and no settings default: the
    baseline is empty, so a use that drops ``--agent haikuprof``'s route has
    nothing to re-resolve and haikuprof's haiku stays live (as on 529b997e).
    With no Anthropic key every prompt on it is refused "No API key found for
    Anthropic." — and on 53ecb70b that branch never asked for the note the keep
    branch gives the same model, so the status said nothing. It asks now."""

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    seen = await _tui(monkeypatch, ["--no-session", "--approve", "--agent", "haikuprof"], [step])
    assert seen["launch"] == _HAIKU
    assert seen["after"] == [_HAIKU]
    assert seen["refusal"] is not None
    assert "No API key found for Anthropic." in seen["refusal"]
    head = _CLEARED_MODEL_KEPT if step == "--none" else "Agent profile: plain (user)"
    assert seen["status"] == [f"{head}\nNote: {seen['refusal']} {_REMEDY}"]


@pytest.mark.parametrize("step", ["plain", "--none"])
async def test_a_drop_with_nothing_to_re_resolve_is_not_what_new_does(
    env: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """The same empty baseline with the key: the dropped profile's haiku stays
    and runs, so there is no note; ``/new`` then resolves the empty launch
    inputs and lands on the empty placeholder. The drop re-derives the model
    only where the launch inputs name one (the guides no longer say "as
    ``/new`` does")."""

    seen = await _tui(
        monkeypatch,
        ["--session-dir", _sessions(env), "--approve", "--agent", "haikuprof"],
        [step, "new"],
    )
    assert seen["after"] == [_HAIKU, ("", "")]
    head = _CLEARED_MODEL_KEPT if step == "--none" else "Agent profile: plain (user)"
    assert seen["status"] == [head]


@pytest.mark.parametrize(
    ("argv", "steps", "after", "status"),
    [
        (
            ["--provider", "anthropic", "--model", "claude-haiku-4-5"],
            ["pick:anthropic/claude-sonnet-4-5", "--none"],
            [_SONNET, _SONNET],
            _CLEARED_MODEL_KEPT,
        ),
        (["--agent", "haikuprof"], ["--none"], [_SONNET], _CLEARED),
    ],
    ids=["keeps-a-pick-over-the-typed-model", "drops-a-route-and-re-resolves"],
)
async def test_the_none_status_says_whether_the_model_stayed(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    steps: list[str],
    after: list[tuple[str, str]],
    status: str,
) -> None:
    """Review round 3 (cross 5). ``--none`` after a ``/model`` pick keeps the
    pick (sonnet) though the typed ``--model`` is haiku; on 53ecb70b the status
    still read "baseline CLI identity restored", which the typed model's
    absence contradicted. It now says the model is unchanged. Where ``--none``
    drops a route (``--agent haikuprof``) the baseline model (the settings
    pair, sonnet) IS restored, and the line says only that."""

    _settings(env, defaultProvider=_SONNET[0], defaultModel=_SONNET[1])
    seen = await _tui(monkeypatch, ["--session-dir", _sessions(env), "--approve", *argv], steps)
    assert seen["after"] == after
    assert seen["status"] == [status]


async def test_a_session_with_no_model_at_all_gets_no_note(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note a route-less use attaches is about a model that was chosen and
    cannot run (#370's not-found placeholder). With nothing chosen at all — no
    flag, no settings default, no key: the empty ``''/''`` placeholder — the use
    says nothing about the model, as on 529b997e (whose re-resolve had no
    baseline to resolve), rather than the headless "No model selected" block."""

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    seen = await _tui(monkeypatch, ["--no-session", "--approve"], ["plain", "--none"])
    assert seen["launch"] == ("", "")
    assert seen["status"] == ["Agent profile: plain (user)", _CLEARED_MODEL_KEPT]


# === 4. what /new resolves after a route-less use (#398's reported shape) ======


async def test_new_after_a_route_less_agents_use_resolves_the_launch_string_as_the_launch_did(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#398 was filed on this shape: ``--model openrouter/auto``, ``/agents use
    <profile>``, ``/new`` — the launch resolves the string without a provider
    (``auto``), and the use's re-resolve completed ``parsed.provider``, so the
    rebuild resolved ``('openrouter/auto', 'openrouter')`` and landed on the
    catalogue's literal ``openrouter/auto`` id. A route-less use resolves
    nothing now and leaves ``parsed`` at the baseline, so ``/new`` resolves the
    string exactly as the launch did. (#398 stays open for a use that drops a
    profile's route, which still completes the pair.)"""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    seen = await _tui(
        monkeypatch,
        ["--session-dir", _sessions(env), "--approve", "--model", "openrouter/auto"],
        ["plain", "new"],
    )
    assert seen["launch"] == ("openrouter", "auto")
    assert seen["after"] == [("openrouter", "auto"), ("openrouter", "auto")]


# === 5. what decides a profile's route: what APPLIES, not what it names ========


@pytest.mark.parametrize(
    ("argv", "profile", "after", "records", "override"),
    [
        (
            ["--model", "claude-haiku-4-5"],
            "opusprov",
            _HAIKU,
            [_HAIKU, _SONNET, _HAIKU],
            "Note: CLI flags override model",
        ),
        (
            ["--provider", "anthropic", "--model", "claude-haiku-4-5"],
            "opusprov",
            _SONNET,
            [_HAIKU, _SONNET],
            "Note: CLI flags override model, provider",
        ),
        (
            ["--model", "claude-haiku-4-5"],
            "modelonly",
            _SONNET,
            [_HAIKU, _SONNET],
            "Note: CLI flags override model",
        ),
    ],
    ids=["model-beaten-provider-applies", "both-beaten", "model-only-beaten"],
)
async def test_a_profile_names_a_route_only_where_its_model_or_provider_applies(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    profile: str,
    after: tuple[str, str],
    records: list[tuple[str, str]],
    override: str,
) -> None:
    """The rule is the overlay's ``applied``, not what the profile names.
    ``opusprov`` names ``provider: anthropic`` and ``model: claude-opus-4-1``.
    With only ``--model`` typed its ``model:`` is beaten but its ``provider:``
    applies: that is a route, so the use re-resolves (the typed model on the
    profile's provider, haiku) and switches off the ``/model`` pick, recording
    it — as on 529b997e. With ``--provider`` typed too, nothing of it applies:
    the pick (sonnet) stays and nothing is recorded. ``modelonly`` names only a
    ``model:``, which the typed ``--model`` beats: nothing applies, the pick
    stays. (On 529b997e the last two reverted the pick to the typed haiku and
    recorded it again — never the settings model: a typed ``--model`` is the
    baseline.)"""

    seen = await _tui(
        monkeypatch,
        ["--session-dir", _sessions(env), "--approve", *argv],
        ["pick:anthropic/claude-sonnet-4-5", profile],
    )
    assert seen["launch"] == _HAIKU
    assert seen["after"] == [_SONNET, after]
    assert seen["status"] == [f"Agent profile: {profile} (user)\n{override}"]
    assert _records(str(seen["file"])) == records


# === 6. the note asks what the prompt check asks ================================

_REMEDY = "No prompt will be sent for it; run /model to select a working model."


def _vertex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("argv", "env_keys", "reason"),
    [
        (["--provider", "openai", "--model", "gpt-4o-mini"], {}, "No API key found for OpenAI."),
        (
            ["--provider", "cloudflare-ai-gateway", "--model", "claude-3-5-haiku"],
            {"CLOUDFLARE_API_KEY": "cf-fake"},
            "model 'claude-3-5-haiku' needs configuration before it can run",
        ),
        (
            [
                "--provider",
                "google-vertex",
                "--model",
                "gemini-1.5-flash",
                "--api-key",
                "vertex-fake-key",
            ],
            {},
            None,
        ),
    ],
    ids=["no-credential", "cloudflare-unconfigured", "vertex-on-typed-key"],
)
async def test_the_note_on_a_kept_model_is_the_refusal_its_next_prompt_gets(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env_keys: dict[str, str],
    reason: str | None,
) -> None:
    """The kept model's note is the prompt check the factory installs
    (``_make_prompt_check``: ``turn_refusal``, then the registry's configured
    credential source), word for word, plus the remedy — and no note where that
    check lets the prompt through. ``gpt-4o-mini`` with no OpenAI key: every
    prompt is refused "No API key found for OpenAI."; ``turn_refusal`` alone
    passes it (round 1 printed no note). The cloudflare model's base URL keeps
    its ``{CLOUDFLARE_ACCOUNT_ID}`` placeholders: refused whatever the key (a
    note built only for ``api 'unknown'`` placeholders missed it). A Vertex
    model on a typed ``--api-key`` with no ``GOOGLE_CLOUD_*`` environment: the
    prompt check passes it and the prompt is sent, so there is no note
    (``is_runnable``'s environment-only Vertex arm would claim "No prompt will
    be sent")."""

    _vertex_env(monkeypatch)
    for name, value in env_keys.items():
        monkeypatch.setenv(name, value)
    seen = await _tui(monkeypatch, ["--no-session", "--approve", *argv], ["plain"])
    assert seen["after"] == [seen["launch"]]
    if reason is None:
        assert seen["refusal"] is None
        assert seen["status"] == ["Agent profile: plain (user)"]
    else:
        assert seen["refusal"] is not None and reason in seen["refusal"]
        assert seen["status"] == [f"Agent profile: plain (user)\nNote: {seen['refusal']} {_REMEDY}"]


@pytest.mark.parametrize(
    ("argv", "env_keys", "note"),
    [
        (["--provider", "openai", "--model", "gpt-4o-mini"], {}, None),
        (
            ["--provider", "cloudflare-ai-gateway", "--model", "claude-3-5-haiku"],
            {"CLOUDFLARE_API_KEY": "cf-fake"},
            "model 'claude-3-5-haiku' needs configuration before it can run",
        ),
    ],
    ids=["no-credential", "cloudflare-unconfigured"],
)
async def test_an_embedder_service_with_no_registry_asks_turn_refusal_alone(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env_keys: dict[str, str],
    note: str | None,
) -> None:
    """A service built without a ``model_registry`` (an embedder's) cannot ask
    for a configured credential, so it notes only what ``turn_refusal`` refuses
    — the cloudflare placeholder — and keeps the model either way; the use
    applies and raises nothing."""

    for name, value in env_keys.items():
        monkeypatch.setenv(name, value)
    seen = await _tui(monkeypatch, ["--no-session", "--approve", *argv], ["embedder:plain"])
    assert seen["after"] == [seen["launch"]]
    (status,) = seen["status"]
    if note is None:
        assert status == "Agent profile: plain (user)"
    else:
        assert status.startswith(f"Agent profile: plain (user)\nNote: {note}")
        assert status.endswith(_REMEDY)


# === 7. the thinking level: still applied on the keep path ======================


def _levels(path: str) -> list[str]:
    return [e["thinkingLevel"] for e in _entries(path) if e["type"] == "thinking_level_change"]


@pytest.mark.parametrize(
    ("argv", "steps", "thinking", "levels"),
    [
        ([], ["thinker"], ["high"], ["high"]),
        (["--thinking", "high"], ["think:low", "plain"], ["low", "high"], ["high", "low", "high"]),
        (["--thinking", "high"], ["think:low", "--none"], ["low", "high"], ["high", "low", "high"]),
    ],
    ids=[
        "profile-thinking-applies",
        "typed-thinking-reverts-a-pick",
        "typed-thinking-reverts-a-pick-on-none",
    ],
)
async def test_a_route_less_use_keeps_the_model_but_still_applies_a_named_thinking_level(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    steps: list[str],
    thinking: list[str],
    levels: list[str],
) -> None:
    """#412 keeps the MODEL only. A level the profile names (``thinker``:
    ``thinking: high``, no route) is applied as before — keeping the live model
    must not drop it. A level ``--thinking`` names is re-applied too, so a
    ``/thinking`` pick is reverted and the move recorded as a
    ``thinking_level_change`` — the known corner ADR-0196 and ADR-0239 list
    (follow-up) — by a profile and by ``--none`` alike (review round 3: the
    ``--none`` row; a re-apply skipped for ``--none`` left the pick). No
    ``model_change`` is written by any of these uses."""

    seen = await _tui(
        monkeypatch,
        [
            "--session-dir",
            _sessions(env),
            "--approve",
            "--provider",
            "anthropic",
            "--model",
            "claude-sonnet-4-5",
            *argv,
        ],
        steps,
    )
    assert seen["after"] == [_SONNET] * len(steps)
    assert seen["thinking"] == thinking
    path = str(seen["file"])
    assert _records(path) == [_SONNET]
    assert _levels(path) == levels
