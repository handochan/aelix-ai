"""Issue #20 (W1) — extension keyboard shortcuts wired into the TUI chrome.

Rules under test: human key specs translate to prompt-toolkit names; bindings
register at chrome build (built-ins WIN collisions); the HANDLER is resolved
LIVE through the provider at fire time (so a #24 reload's handler swaps take
effect without rebinding); a faulty handler never crashes the chrome; the
ExtensionRunner aggregates ``Extension.shortcuts`` first-registration-wins.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_coding_agent.extensions.api import Extension, ExtensionShortcut
from aelix_coding_agent.tui.chrome import AelixChrome, _translate_key_spec
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _make_chrome(provider=None) -> AelixChrome:
    console = Console(file=io.StringIO(), force_terminal=True, width=80)
    return AelixChrome(console=console, extension_shortcuts=provider)


def _bindings_for(chrome: AelixChrome, *keys: str) -> list[object]:
    kb = chrome.app.key_bindings
    assert kb is not None
    out: list[object] = []
    for binding in kb.bindings:
        bound = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if bound == keys:
            out.append(binding)
    return out


# === key-spec translation =====================================================


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ctrl+y", ("c-y",)),
        ("Ctrl+Y", ("c-y",)),
        ("shift+tab", ("s-tab",)),
        ("alt+x", ("escape", "x")),
        ("meta+x", ("escape", "x")),
        ("c-y", ("c-y",)),  # native prompt-toolkit name passes through
        ("f5", ("f5",)),
        ("", None),
        ("ctrl+alt+x", None),  # unsupported chords skip (caller warns)
        ("hyper+x", None),
        # A bare printable char would bind globally and hijack typing that
        # letter (review HIGH) — rejected.
        ("y", None),
    ],
)
def test_translate_key_spec(spec: str, expected: tuple[str, ...] | None) -> None:
    assert _translate_key_spec(spec) == expected


# === registration + conflict rules ============================================


async def test_extension_shortcut_registers_and_fires_live() -> None:
    calls: list[str] = []
    shortcuts = {
        "ctrl+y": ExtensionShortcut(
            key="ctrl+y", handler=lambda: calls.append("v1")
        )
    }
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        chrome = _make_chrome(lambda: shortcuts)
        bindings = _bindings_for(chrome, "c-y")
        assert bindings, "extension shortcut ctrl+y did not bind to c-y"

        for binding in bindings:
            binding.handler(None)  # type: ignore[attr-defined]
        assert calls == ["v1"]

        # LIVE handler swap (what a #24 reload does): the binding must fire
        # the NEW handler without rebinding — fire-time provider lookup.
        shortcuts["ctrl+y"] = ExtensionShortcut(
            key="ctrl+y", handler=lambda: calls.append("v2")
        )
        for binding in bindings:
            binding.handler(None)  # type: ignore[attr-defined]
        assert calls == ["v1", "v2"]


async def test_builtin_binding_wins_key_collision() -> None:
    """ctrl+t is the built-in thinking toggle — an extension claiming it must
    be skipped (built-ins win), leaving exactly the baseline binding count."""
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        baseline = len(_bindings_for(_make_chrome(), "c-t"))
        assert baseline >= 1  # sanity: the built-in exists

        shortcuts = {
            "ctrl+t": ExtensionShortcut(key="ctrl+t", handler=lambda: None)
        }
        chrome = _make_chrome(lambda: shortcuts)
        assert len(_bindings_for(chrome, "c-t")) == baseline  # not shadowed


async def test_invalid_and_failing_shortcuts_never_crash() -> None:
    """Untranslatable specs are skipped at build; a throwing handler is
    contained at fire time (the chrome logs, the app lives)."""

    def _boom() -> None:
        raise RuntimeError("extension bug")

    shortcuts = {
        "hyper+x": ExtensionShortcut(key="hyper+x", handler=lambda: None),
        "ctrl+y": ExtensionShortcut(key="ctrl+y", handler=_boom),
    }
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        baseline_kb = _make_chrome().app.key_bindings
        assert baseline_kb is not None
        baseline_total = len(baseline_kb.bindings)

        chrome = _make_chrome(lambda: shortcuts)
        kb = chrome.app.key_bindings
        assert kb is not None
        # hyper+x bound NOTHING: exactly one binding (ctrl+y) was added on
        # top of the baseline chrome. (The old check — _bindings_for(chrome)
        # with zero keys — was vacuously true; review LOW.)
        assert len(kb.bindings) == baseline_total + 1
        (binding,) = _bindings_for(chrome, "c-y")
        binding.handler(None)  # type: ignore[attr-defined]  # must not raise


async def test_key_alias_cannot_shadow_builtin_enter() -> None:
    """'enter' is prompt-toolkit's ALIAS for 'c-m' — an extension claiming it
    must be caught by the alias-canonicalized collision check, or it would
    SHADOW the core Enter submit binding (review HIGH, live-reproduced)."""
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        baseline = len(_bindings_for(_make_chrome(), "c-m"))
        assert baseline >= 1  # sanity: Enter submit exists (canonical c-m)

        shortcuts = {
            "enter": ExtensionShortcut(key="enter", handler=lambda: None)
        }
        chrome = _make_chrome(lambda: shortcuts)
        assert len(_bindings_for(chrome, "c-m")) == baseline  # not shadowed


async def test_async_shortcut_handler_scheduled() -> None:
    fired = asyncio.Event()

    async def _handler() -> None:
        fired.set()

    shortcuts = {"ctrl+y": ExtensionShortcut(key="ctrl+y", handler=_handler)}
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        chrome = _make_chrome(lambda: shortcuts)
        (binding,) = _bindings_for(chrome, "c-y")
        binding.handler(None)  # type: ignore[attr-defined]
        await asyncio.wait_for(fired.wait(), timeout=5)


# === runner aggregation =======================================================


def test_runner_get_shortcuts_first_registration_wins() -> None:
    first = Extension(name="first")
    first.shortcuts["ctrl+y"] = ExtensionShortcut(
        key="ctrl+y", handler=lambda: "first"
    )
    second = Extension(name="second")
    second.shortcuts["ctrl+y"] = ExtensionShortcut(
        key="ctrl+y", handler=lambda: "second"
    )
    second.shortcuts["ctrl+u"] = ExtensionShortcut(
        key="ctrl+u", handler=lambda: "unique"
    )

    runner = ExtensionRunner(extensions=[first, second])
    shortcuts = runner.get_shortcuts()

    assert set(shortcuts) == {"ctrl+y", "ctrl+u"}
    assert shortcuts["ctrl+y"].handler() == "first"  # load order = priority
    assert shortcuts["ctrl+u"].handler() == "unique"


def test_get_message_renderer_first_wins_across_extensions() -> None:
    # Issue #62 (ADR-0183) — pi runner.ts:502-510: first extension in load
    # order wins a custom_type collision; no warning (pi has none); miss → None.
    def _r1(*args: object) -> str:
        return "first"

    def _r2(*args: object) -> str:
        return "second"

    first = Extension(name="a")
    first.message_renderers["status"] = _r1
    second = Extension(name="b")
    second.message_renderers["status"] = _r2
    second.message_renderers["other"] = _r2

    runner = ExtensionRunner(extensions=[first, second])

    assert runner.get_message_renderer("status") is _r1
    assert runner.get_message_renderer("other") is _r2
    assert runner.get_message_renderer("missing") is None


def test_get_message_renderer_skips_attrless_extension() -> None:
    # Issue #62 review (LOW): the duck-typed lookup must tolerate an extension
    # with no message_renderers attribute (getattr default), returning None
    # rather than crashing.
    runner = ExtensionRunner(extensions=[object()])
    assert runner.get_message_renderer("status") is None
