"""ADR-0197 (P2) — the product-core subagent CONTRACT and its binding seam.

Everything downstream (the bundled ``aelix_agents`` extension, ``/agents run``,
a P4 dashboard) is typed against ``subagent_contract``, so this file pins the
shape rather than the behaviour: the module stays import-free of product-core,
the Protocol stays consent-free, the dataclasses stay frozen and additive, and
``bind_subagents`` refuses in exactly the three ways ADR-0197 says it does.

The findings each test closes are named inline; they come from the two P2
adversarial review rounds (B5/B7/B8/B9, I4, OC-1/OC-3).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import typing
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.extensions.api import ExtensionError, _ExtensionRuntime
from aelix_coding_agent.extensions.api import (
    __file__ as API_FILE,
)
from aelix_coding_agent.subagent_contract import (
    CONTRACT_VERSION,
    DEPTH_ENV_VAR,
    MIN_SUPPORTED_CONTRACT_VERSION,
    ResolvedProfile,
    SubagentProgress,
    SubagentResult,
    SubagentRuntime,
    SubagentUsage,
    subagent_depth,
)
from aelix_coding_agent.subagent_contract import (
    __file__ as CONTRACT_FILE,
)

# The five kwargs a ``CONTRACT_VERSION == 1`` producer knows about. Anything
# added later must be DEFAULTED, which is the promise the version range rests
# on (finding B9) — see ``test_additive_field_does_not_break_a_v1_runtime``.
_V1_RESULT_KWARGS: dict[str, Any] = {
    "id": "sub-1",
    "profile": "scout",
    "ok": True,
    "status": "ok",
    "summary": "done",
}


class _StubRuntime:
    """The minimal shape ``SubagentRuntime`` demands — no spawn behaviour."""

    contract_version: int = 1

    def resolve_profile(
        self, name_or_path: str, *, allow_project: bool = False
    ) -> ResolvedProfile:
        raise NotImplementedError

    async def spawn(self, resolved: ResolvedProfile, task: str, **kwargs: Any) -> SubagentResult:
        raise NotImplementedError

    def list(self) -> list[Any]:
        raise NotImplementedError

    def status(self, id: str) -> Any:
        raise NotImplementedError

    async def stop(self, id: str) -> None:
        raise NotImplementedError

    async def stop_all(self) -> None:
        raise NotImplementedError


def _stub(version: int = 1) -> _StubRuntime:
    runtime = _StubRuntime()
    runtime.contract_version = version
    return runtime


def _profile() -> AgentProfile:
    return AgentProfile(
        name="scout",
        description="Recon agent",
        body="You are scout.",
        file_path="/w/agents/scout.md",
        scope="user",
    )


# === The import rule =========================================================


def test_contract_imports_nothing_from_product_core() -> None:
    """The contract is a LEAF module.

    Its own docstring states the rule; this is the gate. A runtime import of
    ``aelix_coding_agent`` would drag the profile/discovery/resolver chain into
    every consumer and make the seam circular the moment ``extensions/api.py``
    (which now names it) is imported first.
    """

    tree = ast.parse(Path(CONTRACT_FILE).read_text(encoding="utf-8"))

    # Import nodes that live under an ``if TYPE_CHECKING:`` guard are exempt —
    # they never execute.
    type_checking_only: set[ast.stmt] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = (
            test.id
            if isinstance(test, ast.Name)
            else test.attr
            if isinstance(test, ast.Attribute)
            else None
        )
        if name != "TYPE_CHECKING":
            continue
        for guarded in node.body:
            for inner in ast.walk(guarded):
                if isinstance(inner, ast.Import | ast.ImportFrom):
                    type_checking_only.add(inner)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if node in type_checking_only:
            continue
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "aelix_coding_agent"
        ):
            offenders.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            offenders.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.startswith("aelix_coding_agent")
            )

    assert offenders == [], (
        "subagent_contract.py must not import aelix_coding_agent at runtime; "
        f"found {offenders}"
    )


# === The Protocol ============================================================


def test_runtime_checkable_protocol_accepts_minimal_impl() -> None:
    assert isinstance(_stub(), SubagentRuntime)

    class _Partial:
        contract_version = 1

        def list(self) -> list[Any]:
            return []

    # Discriminating: a runtime missing ``spawn`` must NOT satisfy the seam.
    assert not isinstance(_Partial(), SubagentRuntime)


def test_protocol_has_no_consent_parameter() -> None:
    """OC-1 — consent policy never leaks into product-core.

    ``spawn`` takes its own consent internally (ADR-0197 §(i)); the grant type
    is impl-private and rides ``spawn_granted``, which is deliberately absent
    from this Protocol. If a ``grant=`` parameter ever appears here, product-core
    has started authoring consent instead of merely surfacing a refusal.
    """

    for method_name in ("spawn", "resolve_profile"):
        params = inspect.signature(getattr(SubagentRuntime, method_name)).parameters
        leaked = [p for p in params if "grant" in p.lower() or "consent" in p.lower()]
        assert leaked == [], f"{method_name} leaks consent policy: {leaked}"

    # And the positive half of the same rule: the project-identity gate (B5)
    # IS on the Protocol, because refusing an identity is the caller's to state.
    resolve_params = inspect.signature(SubagentRuntime.resolve_profile).parameters
    assert resolve_params["allow_project"].default is False
    assert resolve_params["allow_project"].kind is inspect.Parameter.KEYWORD_ONLY


# === The binding seam ========================================================


def test_bind_subagents_default_none() -> None:
    assert _ExtensionRuntime().subagents is None


def test_bind_subagents_roundtrip() -> None:
    runtime = _ExtensionRuntime()
    impl = _stub()
    runtime.bind_subagents(impl)
    assert runtime.subagents is impl


def test_bind_subagents_refuses_version_below_floor() -> None:
    runtime = _ExtensionRuntime()
    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_stub(version=0))
    assert excinfo.value.code == "contract_mismatch"
    assert runtime.subagents is None


def test_bind_subagents_refuses_version_above_ceiling() -> None:
    runtime = _ExtensionRuntime()
    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_stub(version=999))
    assert excinfo.value.code == "contract_mismatch"
    assert runtime.subagents is None


def test_bind_subagents_refuses_a_partial_implementation() -> None:
    """MEDIUM #7 — ``contract_version`` alone proved nothing about SHAPE.

    Before this gate the seam's only conformance check was a SELF-DECLARED
    ``int``, and ``SubagentRuntime`` — ``runtime_checkable`` since it was
    written — was never actually used. Measured against the real
    ``_ExtensionRuntime``::

        bound a NON-conforming runtime: True
        isinstance(p, SubagentRuntime) = False
        has list/status/stop/stop_all: [False, False, False, False]
        a P4 dashboard calling .list() -> AttributeError

    The bind succeeded; the failure surfaced later, as a generic red line from
    ``/agents run``'s ``except Exception`` with nothing to say the runtime was
    malformed. It is refused at BIND time now, and the message names what is
    missing so a third-party implementer can act on it.
    """

    class _TwoOfSeven:
        """The shape the Protocol docstring dwells on, and nothing else."""

        contract_version = 1

        def resolve_profile(
            self, name_or_path: str, *, allow_project: bool = False
        ) -> Any:  # pragma: no cover — never reached; the bind refuses first
            raise NotImplementedError

        async def spawn(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

    runtime = _ExtensionRuntime()
    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_TwoOfSeven())  # type: ignore[arg-type]
    assert excinfo.value.code == "contract_mismatch"
    for member in ("list", "status", "stop", "stop_all"):
        assert member in str(excinfo.value)
    assert runtime.subagents is None

    # The single-attribute stub the review found in the tree is refused too.
    class _OneAttribute:
        contract_version = 1

    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_OneAttribute())  # type: ignore[arg-type]
    assert excinfo.value.code == "contract_mismatch"
    assert runtime.subagents is None


def test_a_version_mismatch_is_reported_before_a_shape_mismatch() -> None:
    """Order matters for the DIAGNOSTIC, not for safety.

    A runtime built against a future contract is told about the version rather
    than about whichever member that version renamed — otherwise the first thing
    a P3 implementer sees is a misleading "missing: …" list.
    """

    runtime = _ExtensionRuntime()

    class _FutureAndPartial:
        contract_version = 999

    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_FutureAndPartial())  # type: ignore[arg-type]
    assert "contract_version=999" in str(excinfo.value)


def test_bind_accepts_older_supported_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The B9 compat-window pin.

    An exact-equality gate is either dead code or a hard break for every
    third-party runtime the day P3 bumps the version. ``bind_subagents`` reads
    both constants through a function-local import, so monkeypatching the
    module attributes exercises the real range check.
    """

    import aelix_coding_agent.subagent_contract as contract

    monkeypatch.setattr(contract, "MIN_SUPPORTED_CONTRACT_VERSION", 1)
    monkeypatch.setattr(contract, "CONTRACT_VERSION", 2)

    runtime = _ExtensionRuntime()
    impl = _stub(version=1)
    runtime.bind_subagents(impl)
    assert runtime.subagents is impl


def test_second_bind_is_refused_without_replace() -> None:
    """B7 — extension LOAD ORDER is not a contract.

    A silent swap would leave the ``agent`` tool and ``/agents run`` driving
    two different child registries, so a second engine must opt in.
    """

    runtime = _ExtensionRuntime()
    first, second = _stub(), _stub()
    runtime.bind_subagents(first)

    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(second)
    assert excinfo.value.code == "invalid_state"
    assert runtime.subagents is first

    runtime.bind_subagents(second, replace=True)
    assert runtime.subagents is second


def test_teardown_does_not_unbind_a_foreign_runtime() -> None:
    """B7 — ``api.add_cleanup`` must be identity-scoped.

    ``bind_subagents(None)`` in a teardown nulls the slot for whichever runtime
    happens to hold it; ``unbind_subagents`` only releases your own.
    """

    runtime = _ExtensionRuntime()
    first, second = _stub(), _stub()
    runtime.bind_subagents(first)
    runtime.bind_subagents(second, replace=True)

    runtime.unbind_subagents(first)
    assert runtime.subagents is second

    runtime.unbind_subagents(second)
    assert runtime.subagents is None


def test_bind_subagents_none_cannot_evict_a_foreign_runtime() -> None:
    """B7's OTHER spelling — the one the docstring advertises (P2 review, HIGH #6).

    Every refusal used to sit inside ``if runtime is not None:`` with an
    unconditional assignment after it, so ``bind_subagents(None)`` nulled the
    slot for whichever runtime happened to hold it while the docstring promised
    that only the caller's own runtime was implicitly replaceable. Executed
    against a real ``_ExtensionRuntime``: ``bound a`` → ``second bind refused``
    → ``bind_subagents(None)`` → slot ``None``, i.e. ``a`` evicted by a caller
    that does not own it.

    The failure that buys is concrete: a P4 ``aelix-team`` extension calling the
    documented spelling on its own teardown or ``/reload`` path leaves
    ``/agents run`` reporting ``_DELEGATION_UNAVAILABLE`` for the rest of the
    session while ``AgentsExtension`` is still loaded, still holding live
    children in ``_children``, and its ``agent`` tool still spawning.
    """

    runtime = _ExtensionRuntime()
    mine = _stub()
    runtime.bind_subagents(mine)

    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(None)
    assert excinfo.value.code == "invalid_state"
    assert runtime.subagents is mine

    # An EXPLICIT takeover still clears it — the flag is the whole difference
    # between "I did not know somebody was there" and "I mean to displace them".
    runtime.bind_subagents(None, replace=True)
    assert runtime.subagents is None


def test_bind_subagents_none_on_an_empty_slot_is_a_noop() -> None:
    """Releasing nothing is always legal, flag or no flag.

    A cleanup that runs twice, or that runs after ``unbind_subagents`` already
    did the work, must not raise out of a teardown path.
    """

    runtime = _ExtensionRuntime()
    runtime.bind_subagents(None)
    assert runtime.subagents is None


def test_bind_subagents_refused_at_max_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    """I4 — the fork-bomb guard lives in the SEAM, not in one constructor.

    ``extensions/loader.py:470`` drops tier-4 entry points under
    ``--no-extensions`` and ``agents/profile.py:346-351`` bans ``extensions:``
    at project scope, but a user-scope tier-1 extension still loads inside a
    child with ``inherit_extensions: true``. Product-core refuses to HOLD it.
    """

    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    runtime = _ExtensionRuntime()
    with pytest.raises(ExtensionError) as excinfo:
        runtime.bind_subagents(_stub())
    assert excinfo.value.code == "invalid_state"
    assert runtime.subagents is None

    # Unbinding is always legal — teardown must work inside a child too.
    runtime.bind_subagents(None)
    assert runtime.subagents is None


# === The depth reader ========================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("2", 2),
        ("", 0),
        ("   ", 0),
        ("abc", 0),
        ("-5", 0),
        ("1.5", 0),
    ],
)
def test_subagent_depth_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    """Malformed / negative values read as 0 — "I am the root".

    That is the value that makes every downstream depth GATE most restrictive:
    a root binds, prepends and spawns, and each of those re-checks.
    """

    # Both channels: the explicit mapping and the process environment.
    assert subagent_depth({DEPTH_ENV_VAR: raw}) == expected

    monkeypatch.setenv(DEPTH_ENV_VAR, raw)
    assert subagent_depth() == expected


def test_subagent_depth_unset_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEPTH_ENV_VAR, raising=False)
    assert subagent_depth() == 0
    assert subagent_depth({}) == 0


# === The dataclasses =========================================================


@pytest.mark.parametrize(
    ("factory", "attr"),
    [
        (lambda: SubagentResult(**_V1_RESULT_KWARGS), "summary"),
        (SubagentUsage, "input"),
        (lambda: SubagentProgress(id="s", profile="scout", state="running"), "state"),
        (
            lambda: ResolvedProfile(
                name="scout", profile=_profile(), source_path="/w/a.md", scope="user"
            ),
            "scope",
        ),
    ],
)
def test_dataclasses_are_frozen(factory: Any, attr: str) -> None:
    instance = factory()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, attr, "mutated")


def test_additive_field_does_not_break_a_v1_runtime() -> None:
    """B9's other half — the additive-fields-do-not-bump rule.

    A ``CONTRACT_VERSION == 1`` producer constructs ``SubagentResult`` with
    exactly the required kwargs. That only keeps working if every field added
    since carries a default, which is what makes the version RANGE honest.
    """

    result = SubagentResult(**_V1_RESULT_KWARGS)
    assert result.summary == "done"

    required = set(_V1_RESULT_KWARGS)
    undefaulted = [
        f.name
        for f in dataclasses.fields(SubagentResult)
        if f.name not in required
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    ]
    assert undefaulted == [], (
        "fields added after CONTRACT_VERSION 1 must be DEFAULTED (they are not "
        f"allowed to bump the version): {undefaulted}"
    )


def test_result_has_details_and_dropped_lines() -> None:
    """B8 — ``summary`` is capped and its truncation marker promises the full
    text lives "in tool details". ``/agents run`` never builds a ``ToolResult``,
    so without ``details`` on the envelope that promise is false on that door.
    """

    result = SubagentResult(**_V1_RESULT_KWARGS)
    assert result.details is None
    assert result.dropped_lines == 0


def test_result_has_permission_mode() -> None:
    """OC-1/OC-3 — every envelope records the authority the child actually ran
    under, including a human-widened grant, and ``declined`` is a first-class
    outcome (a refused consent dialog is never an exception)."""

    result = SubagentResult(**_V1_RESULT_KWARGS)
    assert result.permission_mode is None

    declined = SubagentResult(
        id="sub-1",
        profile="scout",
        ok=False,
        status="declined",
        summary="Declined.",
        permission_mode="plan",
    )
    assert declined.status == "declined"
    assert declined.permission_mode == "plan"

    from aelix_coding_agent.subagent_contract import SubagentOutcome

    assert "declined" in SubagentOutcome.__args__


def test_version_range_is_coherent() -> None:
    assert MIN_SUPPORTED_CONTRACT_VERSION <= CONTRACT_VERSION


# === The member set (ADR-0199 decision 1) ====================================

# ``SubagentRuntime``'s complete surface at ``CONTRACT_VERSION == 1``. P3 adds
# fan-out and adds NOTHING here: parallel and chain are composed by the
# extension's batch executor calling ``spawn_granted`` once per member, and
# ``spawn_granted`` is impl-private precisely because it carries the grant.
_PROTOCOL_MEMBERS = frozenset(
    {
        "contract_version",
        "resolve_profile",
        "spawn",
        "list",
        "status",
        "stop",
        "stop_all",
    }
)


def _protocol_members(protocol: type) -> frozenset[str]:
    """The Protocol's own non-dunder members.

    ``typing.get_protocol_members`` only exists from 3.13; ``__protocol_attrs__``
    is what it reads and is present on every ``Protocol`` subclass back to 3.8.
    Preferring the public function where it exists keeps this from breaking on
    the interpreter that finally makes the private name go away.
    """

    public = getattr(typing, "get_protocol_members", None)
    if public is not None:  # pragma: no cover - 3.13+
        return frozenset(public(protocol))
    return frozenset(protocol.__protocol_attrs__)  # type: ignore[attr-defined]


def _bind_subagents_member_tuple() -> frozenset[str]:
    """The hardcoded member tuple inside ``_ExtensionRuntime.bind_subagents``.

    Read from the SOURCE rather than exercised, because the tuple is only
    reachable on the failure path (``api.py:669-685`` builds it to name what a
    malformed runtime is missing) and a stale entry there is invisible to every
    green run.
    """

    tree = ast.parse(Path(API_FILE).read_text(encoding="utf-8"))
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "bind_subagents"
    )
    literals = [
        frozenset(str(element.value) for element in generator.iter.elts)
        for node in ast.walk(func)
        if isinstance(node, ast.GeneratorExp)
        for generator in node.generators
        if isinstance(generator.iter, ast.Tuple)
        and generator.iter.elts
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in generator.iter.elts
        )
    ]
    assert len(literals) == 1, (
        "expected exactly one hardcoded member tuple in bind_subagents; the "
        f"diagnostic has been restructured and this test must follow: {literals}"
    )
    return literals[0]


def test_the_protocol_member_set_is_frozen() -> None:
    """ADR-0199 decision 1 — P3 adds NO member to ``SubagentRuntime``.

    This is the phase's most load-bearing decision and, until this test, its
    only enforcement was two ``git diff --stat`` verification gates: branch
    local, and gone the moment P3 merges — the same reasoning
    ``test_p2_band_boundaries.py`` records for retiring its own ``main...HEAD``
    range gate. Nothing that survives the merge pins the member set:
    ``test_runtime_checkable_protocol_accepts_minimal_impl`` is a negative
    ``isinstance`` on a partial stub, and ``test_protocol_has_no_consent_``
    ``parameter`` scans PARAMETER names.

    So a P4 author who adds ``spawn_granted`` to the Protocol — precisely
    because ADR-0199 says fan-out runs through it, and hoisting it looks like
    tidying — gets a fully green suite while ``bind_subagents`` starts
    demanding an eighth attribute and every third-party v1 runtime that was
    valid yesterday fails ``isinstance`` at bind time. The failure lands in
    someone else's extension, at startup, with a ``contract_mismatch``.

    Both halves are asserted. The Protocol is the contract; the tuple in
    ``bind_subagents`` is the error message. If only the first were pinned, the
    tuple could silently stop naming the real missing member and the refusal
    would read ``missing: (signature mismatch)``.
    """

    assert _protocol_members(SubagentRuntime) == _PROTOCOL_MEMBERS, (
        "SubagentRuntime's member set is frozen at CONTRACT_VERSION == 1. A new "
        "member is a BREAKING change for every runtime already bound against "
        "it (api.py's isinstance check is a hasattr sweep over exactly these "
        "names). Delegation topology belongs in the extension: the batch "
        "executor calls spawn_granted once per member with mode='single'."
    )
    assert _bind_subagents_member_tuple() == _PROTOCOL_MEMBERS, (
        "the member tuple in _ExtensionRuntime.bind_subagents has drifted from "
        "the Protocol, so a malformed runtime will be told the wrong thing is "
        "missing (or, if the tuple is short, will bind and fail later at the "
        "call site with a bare AttributeError)."
    )
