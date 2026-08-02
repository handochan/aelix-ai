"""``AELIX_SUBAGENT_CHANNEL`` — the dev-only delegation-transport selector.

THE LOAD-BEARING TEST IN THIS FILE IS
``test_an_unknown_value_refuses_instead_of_falling_back``. Every other row here
pins a mapping; that one pins the decision the module exists to make. Both
channels feed the identical ``_StreamState`` through the identical
``reduce_event`` into an identically shaped ``on_stream``, so a silently
downgraded run is indistinguishable from a successful rpc run at every surface
the parent observes — and a smoke that reports "rpc handled the timeout
correctly" while ``PrintChannel`` did the work is worse than no smoke.

The other two that are not mappings:

* ``test_the_extension_binds_a_registry_that_survives_a_rebuild`` is the "LIVE,
  not captured" proof. The registry is rebound on every ``/reload``, so a
  callable that closed over a VALUE keeps answering with the dead one.
* ``test_the_selected_channel_is_what_the_runtime_actually_runs`` asserts at
  ``runtime._run``'s ``self.channel.run(...)`` — DELIVERY. ``isinstance(
  ext._channel, RpcChannel)`` would be forwarding, and forwarding is how
  ``prompt(images=)`` stayed broken through a whole sprint with 22 green tests.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from aelix_agents.channel_select import (
    CHANNEL_ENV_VAR,
    ChannelSelectionError,
    select_channel,
)
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import PrintChannel
from aelix_agents.rpc_channel import RpcChannel
from aelix_agents.runtime import _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.subagent_contract import ResolvedProfile, SubagentResult

# === the mapping ============================================================


def test_unset_selects_print() -> None:
    """No variable means today's behaviour, silently."""

    assert isinstance(select_channel(env={}), PrintChannel)


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_empty_and_whitespace_read_as_unset(raw: str) -> None:
    """``VAR=`` and ``env -u VAR`` must behave identically.

    A shell script that clears a variable by assigning empty must not get a
    different agent than one that unsets it. ``AELIX_DEFAULT_CATALOG`` maps
    ``""`` → ``None`` for the same reason. Without the guard these fall through
    to the unknown branch and REFUSE, which would make every such script fail to
    start.
    """

    assert isinstance(select_channel(env={CHANNEL_ENV_VAR: raw}), PrintChannel)


def test_print_selects_the_print_channel() -> None:
    """The explicit spelling of the default — the discriminating control."""

    assert isinstance(select_channel(env={CHANNEL_ENV_VAR: "print"}), PrintChannel)


def test_rpc_selects_the_rpc_channel() -> None:
    assert isinstance(select_channel(env={CHANNEL_ENV_VAR: "rpc"}), RpcChannel)


@pytest.mark.parametrize("raw", [" RPC ", "Rpc", "rpc\n"])
def test_the_value_is_case_and_whitespace_insensitive(raw: str) -> None:
    """``.strip().lower()``, following ``_reload_rebuild_enabled``.

    A value pasted out of a shell history or a CI YAML block carries whitespace,
    and refusing it would spend the refusal on a typo the user did not make.
    """

    assert isinstance(select_channel(env={CHANNEL_ENV_VAR: raw}), RpcChannel)


def test_an_unknown_value_refuses_instead_of_falling_back() -> None:
    """THE ONE THAT MATTERS. See the module docstring.

    The message is asserted piece by piece because each piece answers a
    different question the developer has at the moment it is printed: WHICH
    variable (they may have several exported), WHAT they actually typed (the
    raw value, ``!r``, so a trailing quote or a stray character is visible), and
    WHAT would have worked.
    """

    with pytest.raises(ChannelSelectionError) as excinfo:
        select_channel(env={CHANNEL_ENV_VAR: "rpk"})

    message = str(excinfo.value)
    assert CHANNEL_ENV_VAR in message
    assert "'rpk'" in message
    assert "print" in message
    assert "rpc" in message


def test_the_refusal_is_a_value_error() -> None:
    """So a caller that only knows the shape of a bad value still catches it."""

    assert issubclass(ChannelSelectionError, ValueError)


def test_the_raw_value_is_reported_not_the_normalised_one() -> None:
    """``!r`` of what they TYPED.

    Reporting the ``.strip().lower()``'d form would hide the two mistakes this
    message is most often read about: a stray quote and a trailing space.
    """

    with pytest.raises(ChannelSelectionError) as excinfo:
        select_channel(env={CHANNEL_ENV_VAR: " RPC-ish "})

    assert "' RPC-ish '" in str(excinfo.value)


def test_the_channel_holds_the_callable_not_the_registry() -> None:
    """The registry is read LIVE, so what the channel holds must be the callable.

    Re-calling after the callable's answer changes must produce the NEW object.
    A snapshot — ``lambda r=reg: r``, or passing the registry itself — passes the
    identity assertion above and fails this one, which is the whole difference
    between a channel that survives ``/reload`` and one that does not.
    """

    box: list[object] = [object()]

    def registry() -> object:
        return box[0]

    channel = select_channel(model_registry=registry, env={})

    assert channel._model_registry is registry  # type: ignore[attr-defined]
    assert channel._model_registry() is box[0]  # type: ignore[attr-defined]
    fresh = object()
    box[0] = fresh
    assert channel._model_registry() is fresh  # type: ignore[attr-defined]


def test_the_rpc_channel_gets_the_registry_too() -> None:
    """Not withheld from either arm — see ``__post_init__``'s side-effect note.

    ``apply_cost_fallback`` had never run in production for EITHER channel. An
    asymmetry here would make the two arms of the smoke incomparable: one would
    report a real ``usage.cost`` and the other a hardcoded zero.
    """

    def registry() -> object:
        return object()

    channel = select_channel(model_registry=registry, env={CHANNEL_ENV_VAR: "rpc"})

    assert isinstance(channel, RpcChannel)
    assert channel._model_registry is registry


# === the extension: what it resolves, and what the runtime then runs =========


class _Ui:
    def set_status(self, key: str, text: str | None) -> None:
        """``SubagentProgressBridge``'s statusline half. Nothing reads it here."""


class _Runtime:
    """The three attributes ``AgentsExtension.__call__`` reaches for."""

    def __init__(self, registry: object) -> None:
        self.model_registry = registry
        self.ui = _Ui()
        self.subagents: Any = None

    def bind_subagents(self, runtime: Any, *, replace: bool = False) -> None:
        del replace
        self.subagents = runtime


class _Api:
    """A hand-written ``ExtensionAPI`` stand-in.

    Hand-written rather than a ``MagicMock`` for the reason ``_ProbeApi`` in
    ``test_rpc_channel`` gives: ``SubagentProgressBridge._ui()`` compares
    ``api.runtime.ui`` by IDENTITY against ``HEADLESS_UI_CONTEXT``, and a mock
    satisfies that silently.
    """

    def __init__(self, registry: object) -> None:
        self.runtime = _Runtime(registry)
        self.channels: list[str] = []
        api = self

        class _Events:
            def emit(self, channel: str, _payload: Any) -> None:
                api.channels.append(channel)

        self.events = _Events()

    def register_tool(self, tool: Any) -> None:
        del tool

    def on(self, event: str, handler: Any) -> None:
        del event, handler

    def add_cleanup(self, fn: Any) -> None:
        del fn


@pytest.fixture(autouse=True)
def _no_ambient_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The extension reads the REAL environment. A developer's export must not
    decide what these tests measure — and a bad one would raise out of every
    ``AgentsExtension()`` below with a message about the wrong file."""

    monkeypatch.delenv(CHANNEL_ENV_VAR, raising=False)


def test_the_extension_binds_a_registry_that_survives_a_rebuild() -> None:
    """LIVE, not captured. ``/reload`` rebinds the registry on a FRESH runtime.

    ``bind_model_registry`` re-runs against a new ``_ExtensionRuntime`` on every
    harness rebuild, so the only stale-proof read is one that resolves
    ``self._api`` at CALL time. Binding
    ``lambda: self._api.runtime.model_registry`` EVALUATED in ``__post_init__``
    — i.e. a captured value — passes the first assertion and fails the second.
    """

    first, second = object(), object()
    api1, api2 = _Api(first), _Api(second)
    ext = AgentsExtension()

    ext(api1)
    assert ext._channel._model_registry() is first  # type: ignore[attr-defined]

    ext(api2)
    assert ext._channel._model_registry() is second, (  # type: ignore[attr-defined]
        "the channel captured the first build's registry; a /reload would leave "
        "the cost fallback reading a dead one"
    )


def test_no_arguments_is_still_a_valid_construction() -> None:
    """``AgentsExtension()`` is a literal call site (``test_agents_extension_gate``)
    and the class docstring promises it. ``__post_init__`` must not need wiring."""

    assert isinstance(AgentsExtension()._channel, PrintChannel)


def test_an_explicit_channel_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test seam, and the hook Step 6's setting fills.

    ``channel=`` is an override, not a default: a caller that names one has
    already decided, and the env var must not second-guess it.
    """

    monkeypatch.setenv(CHANNEL_ENV_VAR, "rpc")
    explicit = PrintChannel()

    assert AgentsExtension(channel=explicit)._channel is explicit


def test_a_bad_value_raises_out_of_the_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure SURFACE. ``entry.py`` wraps only the import in its ``try``, so
    this propagates out of ``_async_main`` — a traceback ending in the message,
    and exit 1, long before the TUI starts."""

    monkeypatch.setenv(CHANNEL_ENV_VAR, "rpk")

    with pytest.raises(ChannelSelectionError):
        AgentsExtension()


def _resolved() -> ResolvedProfile:
    profile = AgentProfile(
        name="scout",
        description="Reads things.",
        body="You are a scout. Answer briefly.",
        file_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        tools=("read",),
    )
    return ResolvedProfile(
        name=profile.name,
        profile=profile,
        source_path=profile.file_path,
        scope=profile.scope,
    )


async def test_the_selected_channel_is_what_the_runtime_actually_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """DELIVERY, at ``runtime._run``'s ``await self.channel.run(...)``.

    ``isinstance(ext._channel, RpcChannel)`` is FORWARDING and is deliberately
    not what this asserts: it would stay green if ``__call__`` handed the runtime
    a different object than the one it resolved, which is exactly the wiring
    mistake between ``__post_init__`` and ``_SubagentRuntimeImpl(channel=…)``.

    ``ext._ctx`` is ``None``, so ``host.consent_context()`` answers ``None``,
    ``has_ui`` reads ``False`` and consent takes the headless silent grant: no
    dialog, no hang.

    BOTH CHANNELS ARE REPLACED AT THE CLASS, and the second one is not
    symmetry-for-its-own-sake. Patching only ``RpcChannel`` was written first
    and MEASURED under this test's own mutation: with ``__call__`` handing the
    runtime a ``PrintChannel()``, the un-patched channel spawned a REAL
    ``aelix_coding_agent`` child, which reached the developer's credentials and
    came back 31.9 s later with a real model answer. A unit test that spends
    tokens when it fails is a worse defect than the one it was written to catch,
    so the print arm records instead of spawning and the assertion below names
    which channel ran.
    """

    monkeypatch.setenv(CHANNEL_ENV_VAR, "rpc")
    seen: list[Any] = []

    def _recorder(label: str) -> Any:
        async def _run(
            channel_self: Any, plan: Any, *, child: Any = None, on_stream: Any = None
        ) -> SubagentResult:
            del on_stream
            seen.append(channel_self)
            if child is not None:
                child.state = "done"
            return SubagentResult(
                id=plan.id,
                profile=plan.resolved.name,
                ok=True,
                status="ok",
                summary=label,
            )

        return _run

    monkeypatch.setattr(RpcChannel, "run", _recorder("rpc-ran"))
    monkeypatch.setattr(PrintChannel, "run", _recorder("print-ran"))

    ext = AgentsExtension(cwd=str(tmp_path))
    ext(_Api(object()))
    result = await ext.runtime.spawn(_resolved(), "do the thing")

    assert result.summary == "rpc-ran", "the runtime ran the wrong channel"
    assert len(seen) == 1
    assert seen[0] is ext.runtime.channel
    assert isinstance(seen[0], RpcChannel)


# === the runtime keeps no second answer =====================================


def test_the_runtime_has_no_channel_default_of_its_own() -> None:
    """A default here is a SECOND, silently-wrong answer to "which transport".

    ``field(default_factory=PrintChannel)`` was dead — the one production
    construction has always passed ``channel=`` — but left in place it is what a
    future call site that forgets the argument would pick up: a print child
    under an ``AELIX_SUBAGENT_CHANNEL=rpc`` run, reported through an envelope
    that cannot tell the two apart.
    """

    field = next(
        f for f in dataclasses.fields(_SubagentRuntimeImpl) if f.name == "channel"
    )

    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING
