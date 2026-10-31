"""#434 (ADR-0264 §4.4, §8) — the sweep: no resolution lands on a removed default.

A settings default (or ``OPENROUTER_DEFAULT_MODEL``) naming a removed id counts
as unset wherever it is resolved. The skip sits in :func:`resolve_route` itself:
the launch records the settings pair it seeded into ``parsed``
(``record_settings_seed``), and ``resolve_route`` skips a removed id those
strings (or the variable, in step 0) name. So every caller that resolves
``parsed``'s pair is covered without passing anything.

This file enumerates every call of the resolution functions FROM THE CODE (as
r11's ``callers.py`` did), never from a list, and fails on a call it has not
classified: a new caller is a failure until someone decides which kind it is.
The kinds:

* ``seed`` - resolves ``parsed``'s pair: the launch, the factory, the late-route
  hold, ``/agents use`` dropping a route. ``resolve_route``'s skip covers it;
  driven below and through ``_async_main`` in ``test_withdrawn_model_434.py``.
* ``typed`` - resolves a string the user typed (``/model <arg>``, a profile's
  ``model:``): resolved as typed, a removed id included (§4.4).
* ``inner`` - ``resolve_route``'s own recursion and its thin wrappers, which
  pass their strings through unchanged.
* ``pick`` - the post-``/login`` pick (``find_initial_model``), whose ``find``
  through ``_RouteAuthView`` offers only the route rows: a removed id is not
  among them (driven below).
"""

from __future__ import annotations

import ast
import os
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli import runtime_bootstrap as rb
from aelix_coding_agent.model_registry import ModelRegistry

_SRC = Path(__file__).resolve().parents[2] / "packages"
_RESOLVERS = {"resolve_route", "resolve_model", "late_registered_route", "hold_for", "find_initial_model"}

#: (file under ``src/``, enclosing function, callee) -> (kind, how many such calls).
_EXPECTED: dict[tuple[str, str, str], tuple[str, int]] = {
    ("aelix_coding_agent/agents/resolver.py", "_pin_route", "resolve_route"): ("typed", 1),
    # #610: the parent asks what a resumed child's recorded pin resolves to. The
    # child gets those strings as ``--model``/``--provider`` and resolves them as
    # typed, and only a ROUTE pin is asked — a registered model, which is never
    # a removed id, so no seed can skip it (driven below).
    ("aelix_coding_agent/agents/resolver.py", "model_pin_route_moved", "resolve_route"): ("typed", 1),
    ("aelix_coding_agent/agents/service.py", "AgentProfileService.use", "hold_for"): ("seed", 2),
    ("aelix_coding_agent/agents/service.py", "AgentProfileService.use", "resolve_route"): ("seed", 1),
    ("aelix_coding_agent/cli/entry.py", "_build_harness_options", "resolve_route"): ("seed", 1),
    ("aelix_coding_agent/cli/entry.py", "_async_main", "resolve_route"): ("seed", 4),
    # #511: the factory's body is ``_build_harness``; ``_harness_factory`` wraps it in
    # the skills apply gate.
    ("aelix_coding_agent/cli/entry.py", "_async_main._build_harness", "hold_for"): ("seed", 1),
    ("aelix_coding_agent/cli/entry.py", "_async_main", "hold_for"): ("seed", 1),
    ("aelix_coding_agent/cli/entry.py", "_async_main._settle_late_route", "hold_for"): ("seed", 1),
    ("aelix_coding_agent/cli/model_switch.py", "switch_model_argument", "resolve_route"): ("typed", 1),
    ("aelix_coding_agent/cli/runtime_bootstrap.py", "resolve_route", "resolve_route"): ("inner", 1),
    ("aelix_coding_agent/cli/runtime_bootstrap.py", "resolve_model", "resolve_route"): ("inner", 1),
    ("aelix_coding_agent/cli/runtime_bootstrap.py", "late_registered_route", "resolve_route"): ("inner", 1),
    ("aelix_coding_agent/cli/runtime_bootstrap.py", "LateRoute.hold_for", "late_registered_route"): ("inner", 1),
    ("aelix_coding_agent/cli/runtime_bootstrap.py", "LateRoute.hold_for", "resolve_route"): ("inner", 1),
    ("aelix_coding_agent/tui/commands.py", "_profile_model_cell", "resolve_model"): ("typed", 1),
    ("aelix_coding_agent/tui/shell.py", "run_tui._open_login", "find_initial_model"): ("pick", 2),
}


def _calls() -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()
    for path in sorted(_SRC.glob("*/src/**/*.py")):
        rel = path.relative_to(_SRC).as_posix().split("/src/", 1)[1]
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, stack: list[str], rel: str = rel) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack = [*stack, node.name]
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                )
                if name in _RESOLVERS:
                    found[(rel, ".".join(stack) or "<module>", name)] += 1
            for child in ast.iter_child_nodes(node):
                visit(child, stack)

        visit(tree, [])
    return found


def test_every_resolution_call_is_classified() -> None:
    found = _calls()
    expected = Counter({key: count for key, (_kind, count) in _EXPECTED.items()})
    assert found == expected, (
        f"new or moved: {dict(found - expected)}; gone: {dict(expected - found)}. "
        "Classify each call (seed / typed / inner / pick) and drive it."
    )


def test_seed_callers_pass_parseds_pair_through() -> None:
    """A ``seed`` call resolves ``parsed``'s pair, so the strings the seed recorded reach it."""

    for path in sorted(_SRC.glob("*/src/**/*.py")):
        rel = path.relative_to(_SRC).as_posix().split("/src/", 1)[1]
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in ("resolve_route", "hold_for") or rel.endswith("runtime_bootstrap.py"):
                continue
            first = ast.get_source_segment(source, node.args[0]) or ""
            if first.endswith("parsed.model"):
                second = ast.get_source_segment(source, node.args[1]) if len(node.args) > 1 else ""
                assert second and second.endswith("parsed.provider"), (rel, node.lineno)


@pytest.fixture
async def registry(monkeypatch: pytest.MonkeyPatch) -> ModelRegistry:
    for name in list(os.environ):
        if name.endswith("_API_KEY") or name == "OPENROUTER_DEFAULT_MODEL":
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    storage = AuthStorage(Path(tempfile.mkdtemp(prefix="434-sweep-")) / "auth.json")
    await storage.load()
    registry = ModelRegistry.in_memory(storage)
    registry._removed = {"openrouter": frozenset({"acme/withdrawn-1"})}
    return registry


@pytest.mark.parametrize("typed_key", [False, True])
async def test_a_seeded_removed_pair_resolves_to_the_providers_default(
    registry: ModelRegistry, typed_key: bool
) -> None:
    """The launch, the factory, the hold and ``/agents use``'s drop (``typed_key=False``,
    r10's ``agents_use_drop.py``) all call this with ``parsed``'s pair."""

    rb.record_settings_seed("acme/withdrawn-1", "openrouter")

    route = rb.resolve_route("acme/withdrawn-1", "openrouter", registry, None, typed_key=typed_key)

    assert route.withdrawn == ("openrouter", "acme/withdrawn-1")
    assert (route.model.provider, route.model.id) == ("openrouter", "openai/gpt-5.4")
    assert route.kind == "withdrawn_default"


async def test_the_variable_naming_a_removed_id_is_skipped_in_step_0(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "acme/withdrawn-1")

    route = rb.resolve_route(None, None, registry)

    assert route.withdrawn == ("openrouter", "acme/withdrawn-1")
    assert (route.model.provider, route.model.id) == ("openrouter", "openai/gpt-5.4")


async def test_typed_strings_and_an_unseeded_pair_resolve_as_before(registry: ModelRegistry) -> None:
    typed = rb.resolve_route("openrouter/acme/withdrawn-1", None, registry)
    assert (typed.model.provider, typed.model.id) == ("openrouter", "acme/withdrawn-1")
    assert typed.withdrawn is None

    rb.record_settings_seed("openai/gpt-5.4", "openrouter")  # the seed is another pair
    pair = rb.resolve_route("acme/withdrawn-1", "openrouter", registry)
    assert (pair.model.provider, pair.model.id) == ("openrouter", "acme/withdrawn-1")


async def test_with_no_runnable_candidate_the_route_names_no_model(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    rb.record_settings_seed("acme/withdrawn-1", "openrouter")
    monkeypatch.setattr(rb, "withdrawn_fallback", lambda _registry, **_k: None)

    route = rb.resolve_route("acme/withdrawn-1", "openrouter", registry)

    assert route.kind == "none" and route.model.id == ""
    assert rb.withdrawn_default_message(("openrouter", "acme/withdrawn-1"), None, registry) == (
        "Default model openrouter/acme/withdrawn-1 was withdrawn from the published catalog"
    )


async def test_the_post_login_pick_never_lands_on_a_removed_default(registry: ModelRegistry) -> None:
    """r10's case 5: ``find`` through ``_RouteAuthView`` offers the route rows only."""

    from aelix_coding_agent.core.model_resolver import find_initial_model
    from aelix_coding_agent.tui.shell import _RouteAuthView

    result = await find_initial_model(
        default_provider="openrouter",
        default_model_id="acme/withdrawn-1",
        model_registry=_RouteAuthView(registry),
    )

    assert result.model is not None
    assert (result.model.provider, result.model.id) == ("openrouter", "openai/gpt-5.4")


async def test_a_typed_key_never_moves_to_another_provider_on_a_withdrawn_default(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent review 1: the OpenRouter-typed key went to api.anthropic.com."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    rb.record_settings_seed("acme/withdrawn-1", "openrouter")

    untyped = rb.resolve_route("acme/withdrawn-1", "openrouter", registry)
    typed = rb.resolve_route("acme/withdrawn-1", "openrouter", registry, typed_key=True)

    assert untyped.model.provider == "anthropic"  # the provider-default order
    assert (typed.model.provider, typed.model.id) == ("openrouter", "openai/gpt-5.4")
    assert typed.withdrawn == ("openrouter", "acme/withdrawn-1")


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("OpenRouter", "acme/withdrawn-1"),  # aelix's case rule
        ("openrouter", "openrouter/acme/withdrawn-1"),  # pi's <provider>/ strip
        (None, "openrouter/acme/withdrawn-1"),  # the prefix names the provider
        (None, "OpenRouter/acme/withdrawn-1"),
    ],
)
async def test_the_removed_check_reads_the_pair_as_resolution_does(
    registry: ModelRegistry, provider: str | None, model_id: str
) -> None:
    """Review round 2: canonicalization and the prefix strip had no test."""

    assert rb.removed_selection(registry, provider, model_id) == ("openrouter", "acme/withdrawn-1")
    assert rb.removed_selection(registry, provider, model_id.replace("withdrawn", "other")) is None


async def test_a_typed_key_with_no_other_credential_falls_back_on_its_provider(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2 (N2): the key exists only on the command line, so no credential filter."""

    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    rb.record_settings_seed("acme/withdrawn-1", "openrouter")

    typed = rb.resolve_route("acme/withdrawn-1", "openrouter", registry, typed_key=True)

    assert (typed.model.provider, typed.model.id) == ("openrouter", "openai/gpt-5.4")


async def test_a_resumed_childs_pin_resolves_as_typed_even_under_the_seed(
    registry: ModelRegistry,
) -> None:
    """#610 — ``model_pin_route_moved``'s ``typed`` above, driven.

    A resume's recorded pin reaches the child as ``--model``/``--provider``,
    which the child resolves as typed: a launch that names a model records no
    active settings seed. The parent's pre-check resolves the same strings only
    for a pin that carries a ROUTE, and a route is pinned only for an explicitly
    registered provider's model — which ``is_removed`` never calls removed (the
    route rows have it). So even a parent whose own active settings seed is
    exactly that pair, with a hold that took the id out, lands where the child
    will: on the pinned model, not on a withdrawn default's fallback.
    """

    from aelix_ai.streaming import Model
    from aelix_coding_agent.agents.resolver import ModelPin, model_pin_route_moved
    from aelix_coding_agent.model_registry import ProviderConfigInput, registration_route_digest

    row = Model(id="m1", provider="own", api="openai-completions", base_url="http://127.0.0.1:9/v1")
    registry.register_provider("own", ProviderConfigInput(api_key="x", models={"m1": row}))
    registry._removed = {**registry._removed, "own": frozenset({"m1"})}  # a hold took it out
    assert rb.removed_selection(registry, "own", "m1") is None, "registered, so not removed"

    child = rb.resolve_route("m1", "own", registry, runtime_overrides=False)  # no seed, as the child
    pin = ModelPin(model="m1", provider="own", route=registration_route_digest(child.model))

    rb.record_settings_seed("m1", "own")  # the worst case: the parent's own default is this pair
    parent = rb.resolve_route("m1", "own", registry, runtime_overrides=False)
    assert (parent.model.provider, parent.model.id, parent.withdrawn) == ("own", "m1", None)
    assert model_pin_route_moved(pin, registry) is False
