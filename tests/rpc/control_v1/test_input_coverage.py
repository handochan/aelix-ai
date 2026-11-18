"""ADR-0302 D8's coverage rules, enforced over the fixtures and the floor (#623).

A fixture shows the inputs version 1 accepts by running them. These rules make
sure the cases do:

* the fixtures are well formed (one answer schema per case, a known rig,
  ``replay`` only on ``bare`` or ``child``, valid JSON Schema, no
  ``additionalProperties: false``, a type on every required property);
* every accepted enum value of every param has a success case;
* every optional param has a success case that omits it and one that sends it;
* every array param has a success case that sends ``[]``;
* every bound a param declares (``maxItems``, ``maxLength``) is sent at the
  bound in a success case and one past it in a refusal;
* every array a success case's schema reaches into was populated in a real
  answer (its item paths are in the floor's ``forwarded`` set);
* every param has a ``sensitive`` alternative, and re-sending the case with it
  gets an answer the case's own schema rejects (the host reads the param);
* every closed enum in the floor equals the value set the code defines, and an
  open enum's known values are among the code's (:data:`CODE_VALUES`); a row's
  ``decidedBy`` and the ``scopes`` key set are closed sets too (Codex CX-8): a
  new value of either is refused, while an object may still gain a field.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints

import pytest
from jsonschema import Draft202012Validator

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

REPO = Path(__file__).resolve().parents[3]
FIXTURES = rigs.load_fixtures()
FLOOR = json.loads(rigs.FLOOR.read_text(encoding="utf-8"))
METHODS = [f for f in FIXTURES if "method" in f]
ANSWERS = ("data", "error", "frames", "launch", "source")


class _Tokens(dict):  # type: ignore[type-arg]
    """``$rig:`` tokens stay as written; ``$gen`` ones are generated."""

    def __missing__(self, key: str) -> str:
        return f"$rig:{key}"


def _sent(case: dict[str, Any]) -> dict[str, Any]:
    params = rigs.resolve(case.get("params", {}), _Tokens())
    return params if isinstance(params, dict) else {}


def _ok(case: dict[str, Any]) -> bool:
    return "data" in case or "expect" in case


def _contracts(params: dict[str, Any], path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """``(path, contract)`` for every param, ``servers[].type`` for a field of an item."""

    out = []
    for name, contract in params.items():
        where = f"{path}{name}"
        out.append((where, contract))
        out += _contracts(contract.get("fields", {}), f"{where}[].")
    return out


def _values(sent: dict[str, Any], path: str) -> list[Any]:
    """Every value sent at ``path`` (``[]`` walks into the items); absent paths give none."""

    head, _, rest = path.partition("[].")
    if head not in sent:
        return []
    if not rest:
        return [sent[head]]
    items = sent[head] if isinstance(sent[head], list) else []
    return [v for item in items if isinstance(item, dict) for v in _values(item, rest)]


def _absent(sent: dict[str, Any], path: str) -> bool:
    head, _, rest = path.partition("[].")
    if not rest:
        return head not in sent
    items = sent.get(head)
    return isinstance(items, list) and any(isinstance(i, dict) and _absent(i, rest) for i in items)


# --- the fixtures are well formed --------------------------------------------------


def _schemas(node: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found += _schemas(value)
    elif isinstance(node, list):
        for value in node:
            found += _schemas(value)
    return found


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["_file"] for f in FIXTURES])
def test_the_fixture_is_well_formed(fixture: dict[str, Any]) -> None:
    assert fixture["contract"] == "control-v1"
    assert fixture["since"] == "v1" or re.fullmatch(r"#\d+", fixture["since"])
    assert fixture["hosts"] and set(fixture["hosts"]) <= {"cli-rpc", "server-ws"}
    names = [case["name"] for case in fixture["cases"]]
    assert len(names) == len(set(names)), "case names repeat"
    for case in fixture["cases"]:
        label = f"{fixture['_file']} {case['name']!r}"
        answers = [key for key in ANSWERS if key in case]
        assert (
            len(answers) == 1
            or (answers == ["frames"] and "expect" in case)
            or ("frames" in case and ("data" in case or "error" in case))
        ), f"{label}: one answer schema, not {answers}"
        if "source" not in case:
            assert case["rig"] in rigs.RIG_NAMES, label
        assert case.get("replay", False) is False or case["rig"] in ("bare", "child"), label
        assert set(case.get("during", {})) <= {"method", "params", "wait_for", "ended_by_case"}, (
            label
        )
        assert case.get("setup") is None or case["setup"] in rigs.SETUPS, label
        assert case.get("check") is None or case["check"] in rigs.CHECKS, label
        assert str(case.get("since", fixture["since"])) == fixture["since"] or re.fullmatch(
            r"#\d+", str(case.get("since"))
        ), label
        for key in ("data", "error", "expect", "stderr"):
            if key in case:
                Draft202012Validator.check_schema(case[key])
        for step in case.get("frames", []):
            Draft202012Validator.check_schema(step["frame"])
        for node in _schemas({k: case.get(k) for k in ("data", "error", "expect", "frames")}):
            assert node.get("additionalProperties") is not False, (
                f"{label}: a v1 client accepts extra fields"
            )
            for name in node.get("required", []) if node.get("type") == "object" else []:
                sub = node.get("properties", {}).get(name, {})
                assert {"type", "const", "enum"} & set(sub), (
                    f"{label}: required {name!r} has no type"
                )


# --- accepted inputs ---------------------------------------------------------------


def _param_rows() -> list[tuple[str, str, dict[str, Any]]]:
    return [(f["method"], path, c) for f in METHODS for path, c in _contracts(f.get("params", {}))]


def _cases(method: str) -> list[dict[str, Any]]:
    return next(f for f in METHODS if f["method"] == method)["cases"]


ENUMS = [(m, p, c) for m, p, c in _param_rows() if "enum" in c]
OPTIONAL = [(m, p, c) for m, p, c in _param_rows() if not c.get("required")]
ARRAYS = [
    (m, p, c) for m, p, c in _param_rows() if "array" in c["type"].split("|") and "[]." not in p
]
BOUNDS = [(m, p, c, b) for m, p, c in _param_rows() for b in ("maxItems", "maxLength") if b in c]


@pytest.mark.parametrize(
    ("method", "path", "contract"), ENUMS, ids=[f"{m} {p}" for m, p, _ in ENUMS]
)
def test_every_accepted_enum_value_has_a_success_case(
    method: str, path: str, contract: dict[str, Any]
) -> None:
    sent = {
        json.dumps(v) for case in _cases(method) if _ok(case) for v in _values(_sent(case), path)
    }
    missing = [v for v in contract["enum"] if json.dumps(v) not in sent]
    assert missing == [], f"{method} {path}: no success case sends {missing}"


@pytest.mark.parametrize(
    ("method", "path", "contract"), OPTIONAL, ids=[f"{m} {p}" for m, p, _ in OPTIONAL]
)
def test_every_optional_param_is_omitted_once_and_sent_once(
    method: str, path: str, contract: dict[str, Any]
) -> None:
    assert contract.get("absent"), f"{method} {path}: say what its absence means"
    successes = [_sent(case) for case in _cases(method) if _ok(case)]
    assert any(_absent(sent, path) for sent in successes), f"{method} {path}: never omitted"
    assert any(_values(sent, path) for sent in successes), f"{method} {path}: never sent"


@pytest.mark.parametrize(
    ("method", "path", "contract"), ARRAYS, ids=[f"{m} {p}" for m, p, _ in ARRAYS]
)
def test_every_array_param_is_sent_empty(method: str, path: str, contract: dict[str, Any]) -> None:
    sent = [v for case in _cases(method) if _ok(case) for v in _values(_sent(case), path)]
    assert [] in sent, f"{method} {path}: no success case sends []"


@pytest.mark.parametrize(
    ("method", "path", "contract", "bound"), BOUNDS, ids=[f"{m} {p} {b}" for m, p, _, b in BOUNDS]
)
def test_every_bound_is_sent_at_the_bound_and_refused_past_it(
    method: str, path: str, contract: dict[str, Any], bound: str
) -> None:
    kind = list if bound == "maxItems" else str
    limit = contract[bound]

    def sizes(success: bool) -> set[int]:
        return {
            len(v)
            for case in _cases(method)
            if (_ok(case) if success else "error" in case)
            for v in _values(_sent(case), path)
            if isinstance(v, kind)
        }

    assert limit in sizes(True), f"{method} {path}: no success case at {bound} {limit}"
    assert limit + 1 in sizes(False), f"{method} {path}: no refusal at {bound} {limit} + 1"


# --- populated arrays --------------------------------------------------------------


def test_every_array_a_schema_reaches_into_was_populated() -> None:
    empty = []
    for section in ("methods", "events"):
        for ident, entry in FLOOR[section].items():
            shown = entry.get("data", entry.get("frame", {}))
            for path in shown:
                if "[]." in path and path not in entry["forwarded"]:
                    empty.append(f"{section} {ident} {path}")
    assert empty == [], "no real answer populated these arrays:\n" + "\n".join(empty)


# --- the host reads every param ----------------------------------------------------


SENSITIVE = [
    (fixture, case, name)
    for fixture in METHODS
    for case in fixture["cases"]
    for name in case.get("sensitive", {})
]


def test_every_param_has_a_sensitive_alternative() -> None:
    missing = []
    for fixture in METHODS:
        named = {
            name for case in fixture["cases"] if _ok(case) for name in case.get("sensitive", {})
        }
        missing += [f"{fixture['method']} {p}" for p in fixture.get("params", {}) if p not in named]
    assert missing == [], "no case shows the host reads these params:\n" + "\n".join(missing)


@pytest.mark.parametrize(
    ("fixture", "case", "name"),
    SENSITIVE,
    ids=[f"{f['method']}::{c['name']}::{n}" for f, c, n in SENSITIVE],
)
async def test_the_sensitive_alternative_changes_the_answer(
    fixture: dict[str, Any], case: dict[str, Any], name: str, tmp_path: Path
) -> None:
    assert _ok(case), "a sensitive alternative needs a success case"
    result, _check = await rigs.run_in_process(
        fixture, case, tmp_path / "rig", override={name: case["sensitive"][name]}
    )
    answer = result.response
    schema = case.get("data", case.get("expect", {}))
    assert answer.get("success") is False or rigs.schema_rejects(schema, answer.get("data")), (
        f"{fixture['method']} {case['name']!r}: with {name}={case['sensitive'][name]!r} the answer "
        f"still matches the case's schema, so nothing shows the host reads {name!r}: {answer}"
    )


# --- closed enums are the code's value sets ----------------------------------------


def _modes() -> list[str]:
    from aelix_coding_agent.builtin.permission_mode import PermissionMode

    return sorted(mode.value for mode in PermissionMode)


def _outcomes() -> list[str]:
    from aelix_coding_agent.extensions.command_dispatch import DispatchOutcome

    return sorted([outcome.value for outcome in DispatchOutcome] + ["prompt"])


def _setting_types() -> list[str]:
    from aelix_coding_agent.rpc.control import SETTING_FIELDS

    names = {str: "string", bool: "boolean", list: "string_array"}
    return sorted({names[kind] for _field, kind, _choices in SETTING_FIELDS.values()})


def _setting_keys() -> list[str]:
    from aelix_coding_agent.rpc.control import SETTING_FIELDS

    return sorted(SETTING_FIELDS)


def _diagnostic_types() -> list[str]:
    from aelix_coding_agent.agents.profile import ProfileDiagnostic

    return sorted(get_args(get_type_hints(ProfileDiagnostic)["type"]))


def _subagent_channels() -> list[str]:
    from aelix_coding_agent.extensions.api import EventBus
    from aelix_coding_agent.rpc.control import RpcControlPlane

    bus = EventBus()
    RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(runtime=SimpleNamespace(event_bus=bus)))
    )
    return sorted(bus._handlers)


def _ui_methods() -> list[str]:
    """What ``RpcCommandUI`` and the control plane can put in an ``extension_ui_request``."""

    from aelix_coding_agent.rpc import control, control_ui

    text = Path(control.__file__).read_text("utf-8") + Path(control_ui.__file__).read_text("utf-8")
    return sorted(set(re.findall(r"(?:request|notice)\(\s*\"(\w+)\"", text)))


def _selection_kinds() -> list[str]:
    import dataclasses

    from aelix_coding_agent.cli.entry import SelectionControls

    return sorted(f.name for f in dataclasses.fields(SelectionControls))


def _selection_scopes() -> list[str]:
    from aelix_coding_agent.rpc.control_selection import SCOPES

    return sorted(SCOPES)


def _decided_by() -> list[Any]:
    """``ItemState.decided_by``: a :data:`Scope` or ``None`` (the default decided it)."""

    from aelix_coding_agent.core.resource_selection import ItemState

    values: list[Any] = []
    for arg in get_args(get_type_hints(ItemState)["decided_by"]):
        values += list(get_args(arg)) or [None]
    return values


def _literal(name: str) -> Any:
    def values() -> list[str]:
        from aelix_coding_agent.core import resource_selection

        return sorted(get_args(getattr(resource_selection, name)))

    return values


#: Every enum the floor holds, and the code's value set it must equal (closed) or
#: sit inside (open). ``None`` says the code keeps the set as literals with no
#: name to read; the fixture alone pins it, and the reason is the string.
CODE_VALUES: dict[tuple[str, str, str], Any] = {
    ("methods", "capabilities", "data.permissionModes[]"): _modes,
    ("methods", "permissions/get", "data.mode"): _modes,
    ("methods", "permissions/set", "data.mode"): _modes,
    ("methods", "permissions/set", "params.mode"): _modes,
    ("methods", "commands/run", "data.outcome"): _outcomes,
    ("methods", "commands/run", "data.messages[].type"): "two literal sinks in run_command",
    ("methods", "settings/get", "data.schema[].type"): _setting_types,
    ("methods", "settings/set", "params.key"): _setting_keys,
    ("methods", "agents/list", "data.diagnostics[].type"): _diagnostic_types,
    ("methods", "sessions/branch", "params.kind"): "a literal set in branch_session",
    ("methods", "mcp/configure", "params.servers[].type"): "literals in control_mcp.contributions",
    ("methods", "capabilities", "data.selectionKinds[]"): _selection_kinds,
    ("methods", "selections/list", "params.kind"): _selection_kinds,
    ("methods", "selections/set", "params.kind"): _selection_kinds,
    ("methods", "selections/set", "params.scope"): _selection_scopes,
    ("methods", "selections/list", "data.effect"): _literal("Effect"),
    ("methods", "selections/set", "data.effect"): _literal("Effect"),
    ("methods", "selections/list", "data.items[].role"): _literal("Role"),
    ("methods", "selections/list", "data.items[].lock.kind"): _literal("LockKind"),
    ("methods", "selections/list", "data.items[].decidedBy"): _decided_by,
    ("methods", "selections/list", "data.scopes{}"): _selection_scopes,
    ("events", "aelix_subagent", "frame.event"): _subagent_channels,
    ("events", "extension_ui", "frame.method"): _ui_methods,
}


def _floor_enums() -> list[tuple[tuple[str, str, str], list[Any], bool]]:
    rows = []
    for section in ("methods", "events"):
        for ident, entry in FLOOR[section].items():
            rows += [
                ((section, ident, path), values, True) for path, values in entry["closed"].items()
            ]
            rows += [
                ((section, ident, path), values, False) for path, values in entry["open"].items()
            ]
            for path, contract in _contracts(entry.get("params", {})):
                if "enum" in contract:
                    rows.append(((section, ident, f"params.{path}"), contract["enum"], True))
    return rows


ENUM_ROWS = _floor_enums()


@pytest.mark.parametrize(
    ("key", "values", "closed"), ENUM_ROWS, ids=[" ".join(k) for k, _, _ in ENUM_ROWS]
)
def test_every_enum_is_the_codes_value_set(
    key: tuple[str, str, str], values: list[Any], closed: bool
) -> None:
    assert key in CODE_VALUES, (
        f"{key}: name the code's value set in CODE_VALUES (or why there is none)"
    )
    source = CODE_VALUES[key]
    if isinstance(source, str):
        return
    floor = sorted(json.dumps(v) for v in values)
    code = sorted(json.dumps(v) for v in source())
    if closed:
        assert floor == code, f"{key}: the floor says {floor}, the code {code}"
    else:
        assert set(floor) <= set(code), (
            f"{key}: {sorted(set(floor) - set(code))} are not the code's"
        )


def test_every_code_values_row_is_an_enum_of_the_floor() -> None:
    assert set(CODE_VALUES) == {key for key, _, _ in ENUM_ROWS}


def test_the_row_and_scope_vocabularies_are_closed_sets() -> None:
    """A row's ``decidedBy`` and the ``scopes`` key set are closed (Codex CX-8, ADR-0302 D3)."""

    closed = {key for key, _values, is_closed in ENUM_ROWS if is_closed}
    assert ("methods", "selections/list", "data.items[].decidedBy") in closed
    assert ("methods", "selections/list", "data.scopes{}") in closed
