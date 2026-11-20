#!/usr/bin/env python3
"""The control-v1 floor: version 1 only grows (#623, ADR-0302 D8).

    uv run python scripts/check_control_v1.py --update     # regenerate the floor; only ever adds
    uv run python scripts/check_control_v1.py --check      # R3: the floor holds the merge base's
    uv run python scripts/check_control_v1.py --check --require-base --github-event   # CI

THE FLOOR. ``tests/rpc/control_v1/floor_v1.json`` is generated, never edited by
hand. ``--update`` builds it from the fixtures in ``tests/rpc/control_v1/fixtures``
and from one in-process run of every success case on its rig
(``tests/rpc/control_v1/rigs.py`` ``capture``). Per method, event family, pinned
error and the envelope it holds: ``since``, ``hosts``, ``requires``, the
``params`` input contract, the ``data`` (or ``frame``) paths a success case
requires with their types, the ``forwarded`` paths seen in the real answers
with their types, each ``closed`` enum's values and each ``open`` enum's known
values, and per case its request (``sends``), its ``digest`` (:func:`case_digest`:
the whole case as written) and ``facts``: every execution step
(:func:`execution_facts`: ``before``, ``during``, ``setup``, ``check``,
``trigger``, ``replay``, ``sensitive``), every required path with its type, and
every type, ``const``, enum, key set, ``contains``, ``allOf``/``anyOf``/``not``/``prefixItems``
and bound the case's schemas state. A schema keyword or a case key the floor
does not read is refused, never dropped (:data:`SCHEMA_KEYWORDS`,
:data:`CASE_KEYS`). ``withdrawals`` records the only accepted shrinkage
(ADR-0302 D4).

WHAT "CONTAINS" MEANS (:func:`contains`; R1, R2 and R3 all use it). A floor A
holds a floor B when every entry of B is in A with the same ``since`` and
``requires``, B's hosts among A's, every param of B in A with the same type,
still optional where B had it optional, accepting every value B accepted and a
bound no lower; every ``data``/``frame`` and ``forwarded`` path of B in A with
B's types among A's; every closed enum with exactly B's values; every open
enum's values among A's; every case of B in A with the same kind, rig and
request and B's facts among A's; every since-v1 case of B in A with B's
digest (a frozen case is not edited, even to add to it); and every withdrawal
of B in A. A case a withdrawal names (and the param values only that case
sent) is exempt.

* R1 and R2 (``tests/rpc/control_v1/test_additive_only.py``, no git): a fresh
  build holds the committed floor (nothing was dropped or loosened), and the
  committed floor holds a fresh build (``--update`` was run).
* R3 (``--check``): the floor on disk holds the floor at
  ``git merge-base HEAD <--base>`` (default ``origin/main``). With
  ``--github-event`` a push to ``main`` compares with the commit the push
  replaced: :func:`github_base` is imported from ``scripts/check_complexity.py``
  (ADR-0300's own, not copied). Without git or the ref the check says so and
  passes, unless ``--require-base``. A withdrawal the base did not have also
  needs the heading ``## Note (<its date>`` in ``docs/decisions/0302-*.md``.

``--update`` refuses to write a floor that does not hold the committed one, and
names what would be dropped or loosened. The one way past it is a recorded
withdrawal, a safety refusal ADR-0302 D4 allows only with a per-case owner
decision and a dated note on ADR-0302:
``--update --withdraw <method>:<case> --note <YYYY-MM-DD> --issue '#<n>'``.

HOW A LATER LANE ADDS TO VERSION 1 (ADR-0302 D9). Add the fixture file or the
case with ``"since": "#<issue>"`` (``tests/rpc/control_v1/rigs.py`` says how, and
where a new service goes), then run ``--update`` and commit the floor with the
fixture. ``test_additive_only.py`` fails until the floor has the addition, and
from then on R1-R3 keep it. Leave every ``"since": "v1"`` case as it is: a new
value of an open enum, a new field or a new method goes in a case of its own,
and the v1 digest (:func:`v1_digest`, pinned as ``V1_SHA256`` in
``test_additive_only.py``) does not move. Only the freeze commit, or a
withdrawal, changes that pin.

Exit: 0 clean; 1 a problem, each named; 2 the floor, a fixture or git could not be read
(a fixture with a schema keyword or a case key the floor does not capture among them).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOOR_REL = "tests/rpc/control_v1/floor_v1.json"
FIXTURES_REL = "tests/rpc/control_v1/fixtures"
ADR_GLOB = "docs/decisions/0302-*.md"
SECTIONS = ("methods", "events", "errors", "envelope")
SCHEMA_KEYS = ("data", "error", "expect")
BOUND_KEYS = ("minItems", "maxItems", "minLength", "maxLength", "pattern")
PARAM_BOUNDS = ("maxItems", "maxLength", "minimum", "maximum")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: The JSON Schema keywords :func:`schema_facts` turns into facts. Any other
#: keyword in a case's schema is refused (``FloorError``), never dropped: a
#: keyword the floor does not read could be removed without the floor changing.
SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "const",
        "enum",
        "open",
        "properties",
        "required",
        "items",
        "contains",
        "allOf",
        "anyOf",
        "not",
        "propertyNames",
        "prefixItems",
        *BOUND_KEYS,
    }
)
#: Annotations: they change no answer a schema accepts, so they state no fact.
ANNOTATIONS = frozenset({"description", "title", "$comment"})
#: The keys a case may have. Each one either selects or sends the request, or
#: is a schema, or is an execution step (:func:`execution_facts`); a key outside
#: this set is refused, since the floor would not see it removed.
CASE_KEYS = frozenset(
    {
        "name",
        "since",
        "rig",
        "replay",
        "params",
        "launch",
        "sensitive",
        "before",
        "during",
        "setup",
        "check",
        "trigger",
        "method",
        "frameType",
        "data",
        "error",
        "expect",
        "frames",
        "source",
        "stderr",
    }
)
#: The fixture-level fields a case runs with (the event family's frame and reply
#: schemas, the envelope's own schemas, the pinned error's text): part of each
#: case's digest, so editing them changes every since-v1 case of the fixture.
FIXTURE_CONTEXT = (
    "method",
    "event",
    "error",
    "frameType",
    "frame",
    "reply",
    "request",
    "response",
    "text",
)


class FloorError(Exception):
    """The floor, a fixture or git could not be read, or a fixture says what the floor cannot hold; exit 2."""


# --- reading fixtures -------------------------------------------------------------


def load_fixtures(root: Path) -> list[dict[str, Any]]:
    fixtures = []
    for path in sorted((root / FIXTURES_REL).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FloorError(f"{path.name}: {exc}") from exc
        data["_file"] = path.name
        fixtures.append(data)
    return fixtures


def section_and_id(fixture: dict[str, Any]) -> tuple[str, str]:
    """``("methods", "permissions/set")``, ``("events", "aelix_subagent")``, ..."""

    if "method" in fixture:
        return "methods", str(fixture["method"])
    if "event" in fixture:
        return "events", str(fixture["event"])
    if "error" in fixture:
        return "errors", str(fixture["error"])
    return "envelope", "aelix_control"


def capture_key(fixture: dict[str, Any]) -> str:
    """The key ``rigs.capture`` files a fixture's answers under (``rigs.fixture_id``)."""

    section, ident = section_and_id(fixture)
    return {"methods": ident, "events": f"event:{ident}", "errors": f"error:{ident}"}.get(
        section, "envelope"
    )


# --- schemas to facts -------------------------------------------------------------


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def node_type(node: dict[str, Any]) -> str:
    declared = node.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        return "|".join(sorted(declared))
    if "const" in node:
        return json_type(node["const"])
    if "enum" in node:
        return "|".join(sorted({json_type(v) for v in node["enum"]}))
    return "any"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _uncaptured(node: dict[str, Any], where: str) -> None:
    unknown = sorted(set(node) - SCHEMA_KEYWORDS - ANNOTATIONS)
    if unknown:
        raise FloorError(
            f"schema keyword {unknown[0]!r} at {where} is not one the floor captures; "
            "teach scripts/check_control_v1.py's schema_facts to read it (ADR-0302 D8)"
        )
    if "open" in node and (node["open"] is not True or "enum" not in node):
        raise FloorError(f'"open" at {where} is true and goes with an "enum"')
    for key, kind in (("properties", dict), ("items", dict), ("contains", dict), ("not", dict)):
        if key in node and not isinstance(node[key], kind):
            raise FloorError(f"{key!r} at {where} is not a schema object the floor reads")
    for key in ("allOf", "anyOf", "required", "prefixItems"):
        if key in node and not isinstance(node[key], list):
            raise FloorError(f"{key!r} at {where} is not a list")


def _key_set_facts(node: dict[str, Any], path: str, facts: set[str]) -> None:
    """``propertyNames`` with an ``enum``: the object's key set, at ``<path>{}``."""

    names = node["propertyNames"]
    facts.add(f"propertyNames {path or '.'} {_dump(names)}")
    if isinstance(names, dict) and "enum" in names:
        where = f"{path}{{}}"
        values = sorted(_dump(v) for v in names["enum"])
        if names.get("open") is True:
            facts.update(f"open {where} {v}" for v in values)
        else:
            facts.add(f"closed {where} [{', '.join(values)}]")


def schema_facts(node: Any, path: str, facts: set[str], required: bool = True) -> None:
    """Every fact a JSON Schema states, as ``"<kind> <path> <value>"`` strings.

    ``req`` facts follow ``required`` from the root, so they are the paths a
    client may rely on; ``reqif`` names every required member wherever it sits
    (required whenever its object is there), ``type`` every declared type, and
    ``const``, enums, ``contains``, the key set and bounds are facts wherever
    they sit (a value present there must meet them). ``allOf``, ``anyOf``,
    ``not`` and ``prefixItems`` (an array's ordered head, #612 fix round 1) are
    kept whole, so no part of one can go without a fact going. A keyword
    outside :data:`SCHEMA_KEYWORDS` is refused (CX-6).
    """

    if not isinstance(node, dict):
        return
    where = path or "."
    _uncaptured(node, where)
    if "type" in node:
        facts.add(f"type {where} {node_type(node)}")
    if "const" in node:
        facts.add(f"const {where} {_dump(node['const'])}")
    if "enum" in node:
        values = sorted(_dump(v) for v in node["enum"])
        if node.get("open") is True:
            facts.update(f"open {where} {v}" for v in values)
        else:
            facts.add(f"closed {where} [{', '.join(values)}]")
    for key in BOUND_KEYS:
        if key in node:
            facts.add(f"bound {where}:{key} {_dump(node[key])}")
    for sub in node.get("allOf", []):
        facts.add(f"allOf {where} {_dump(sub)}")
        schema_facts(sub, path, facts, required=False)
    for key in ("anyOf", "not", "prefixItems"):
        if key in node:
            facts.add(f"{key} {where} {_dump(node[key])}")
    contained = node.get("contains")
    if isinstance(contained, dict):
        facts.add(f"contains {where} {_dump(contained)}")
    if "propertyNames" in node:
        _key_set_facts(node, path, facts)
    properties = node.get("properties", {})
    wanted = set(node.get("required", []))
    for name in sorted(wanted):
        facts.add(f"reqif {_join(path, name)} {node_type(properties.get(name, {}))}")
    for name, sub in properties.items():
        sub_path = _join(path, name)
        if required and name in wanted:
            facts.add(f"req {sub_path} {node_type(sub)}")
        schema_facts(sub, sub_path, facts, required=required and name in wanted)
    items = node.get("items")
    if isinstance(items, dict):
        item_path = f"{path}[]"
        if required and ("type" in items or "const" in items or "enum" in items):
            facts.add(f"req {item_path} {node_type(items)}")
        schema_facts(items, item_path, facts, required=required)


def case_kind(case: dict[str, Any]) -> str:
    for kind in ("launch", "source", "frames", "data", "error"):
        if case.get(kind):
            return kind
    return "data" if "expect" in case else "error"


def execution_facts(case: dict[str, Any]) -> set[str]:
    """What runs a case besides its request: each step is a fact, so none goes silently (CX-6)."""

    facts: set[str] = set()
    for index, step in enumerate(case.get("before", [])):
        facts.add(f"step before[{index}] {_dump(step)}")
    for key in ("during", "trigger", "setup", "check", "method", "frameType"):
        if key in case:
            facts.add(f"step {key} {_dump(case[key])}")
    if case.get("replay") is True:
        facts.add("step replay true")
    for name, value in sorted(case.get("sensitive", {}).items()):
        facts.add(f"step sensitive.{name} {_dump(value)}")
    return facts


def case_facts(fixture: dict[str, Any], case: dict[str, Any]) -> list[str]:
    unknown = sorted(set(case) - CASE_KEYS)
    if unknown:
        raise FloorError(
            f"case key {unknown[0]!r} is not one the floor captures; teach "
            "scripts/check_control_v1.py's execution_facts to read it (ADR-0302 D8)"
        )
    facts = execution_facts(case)
    for key in SCHEMA_KEYS:
        if key in case:
            schema_facts(case[key], key, facts)
    family = fixture.get("frame")
    for index, step in enumerate(case.get("frames", [])):
        schema_facts(family, f"frame[{index}]", facts)
        schema_facts(step.get("frame"), f"frame[{index}]", facts)
        if "reply" in step:
            facts.add(f"reply frame[{index}] {_dump(step['reply'])}")
            schema_facts(fixture.get("reply"), f"reply[{index}]", facts)
    if "stderr" in case:
        schema_facts(case["stderr"], "stderr", facts)
    for source in case.get("source", []):
        facts.add(f"source {source['path']} {_dump(source['contains'])}")
    return sorted(facts)


def case_digest(fixture: dict[str, Any], case: dict[str, Any]) -> str:
    """sha256 of the whole case as written, with its ``since`` and :data:`FIXTURE_CONTEXT`.

    A since-v1 case is frozen as written (ADR-0302 D3): any edit to it, a step
    or an assertion dropped as much as one added, changes this digest, and
    :func:`contains` refuses it unless a withdrawal names the case (D4).
    """

    body = {key: value for key, value in case.items() if key != "name"}
    body["since"] = str(case.get("since", fixture["since"]))
    body["fixture"] = {key: fixture[key] for key in FIXTURE_CONTEXT if key in fixture}
    return hashlib.sha256(_dump(body).encode("utf-8")).hexdigest()


def _types(text: str) -> set[str]:
    return set(text.split("|"))


def _merge_type(paths: dict[str, str], path: str, kind: str) -> None:
    paths[path] = "|".join(sorted(_types(paths[path]) | _types(kind))) if path in paths else kind


def observed_paths(value: Any, path: str, out: dict[str, str]) -> None:
    """Every path of a real answer with the JSON types seen there (``[]`` marks items)."""

    if isinstance(value, dict):
        for key, item in value.items():
            sub = _join(path, str(key))
            _merge_type(out, sub, json_type(item))
            observed_paths(item, sub, out)
    elif isinstance(value, list):
        for item in value:
            _merge_type(out, f"{path}[]", json_type(item))
            observed_paths(item, f"{path}[]", out)


# --- the floor --------------------------------------------------------------------


def _enum_maps(facts: list[str], prefix: str) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    closed: dict[str, list[Any]] = {}
    opened: dict[str, list[Any]] = {}
    for fact in facts:
        kind, where, value = fact.split(" ", 2)
        if not where.startswith(prefix):
            continue
        key = re.sub(r"^frame\[\d+\]", "frame", where)
        if kind == "closed":
            closed[key] = json.loads(value)
        elif kind == "open":
            opened.setdefault(key, [])
            if json.loads(value) not in opened[key]:
                opened[key].append(json.loads(value))
    return closed, {k: sorted(v, key=_dump) for k, v in opened.items()}


def build_entry(fixture: dict[str, Any], captured: dict[tuple[str, str], Any]) -> dict[str, Any]:
    section, _ident = section_and_id(fixture)
    key = capture_key(fixture)
    shown = "frame" if section == "events" else "data"
    paths: dict[str, str] = {}
    forwarded: dict[str, str] = {}
    closed: dict[str, list[Any]] = {}
    opened: dict[str, list[Any]] = {}
    cases: dict[str, Any] = {}
    for case in fixture["cases"]:
        try:
            facts = case_facts(fixture, case)
        except FloorError as exc:
            raise FloorError(f"{fixture['_file']} case {case['name']!r}: {exc}") from None
        for fact in facts:
            kind, where, value = fact.split(" ", 2)
            if kind != "req":
                continue
            if where.startswith("data."):
                _merge_type(paths, where[len("data.") :], value)
            elif where.startswith("frame["):
                _merge_type(paths, where.split(".", 1)[1] if "." in where else where, value)
        for prefix in ("data", "frame", "expect"):
            more_closed, more_open = _enum_maps(facts, prefix)
            closed.update(more_closed)
            for k, values in more_open.items():
                opened[k] = sorted(
                    {_dump(v): v for v in opened.get(k, []) + values}.values(), key=_dump
                )
        cases[case["name"]] = {
            "since": str(case.get("since", fixture["since"])),
            "kind": case_kind(case),
            "rig": case.get("rig"),
            "sends": case.get("params", case.get("trigger", case.get("launch"))),
            "digest": case_digest(fixture, case),
            "facts": facts,
        }
        answer = captured.get((key, case["name"]))
        if answer is not None:
            if section == "events":
                for frame in answer.get("frames", []):
                    observed_paths(frame, "", forwarded)
            elif answer.get("data") is not None:
                observed_paths(answer["data"], "", forwarded)
    entry: dict[str, Any] = {
        "since": str(fixture["since"]),
        "hosts": sorted(fixture.get("hosts", [])),
        "requires": fixture.get("requires"),
        "params": copy.deepcopy(fixture.get("params", {})),
        shown: dict(sorted(paths.items())),
        "forwarded": dict(sorted(forwarded.items())),
        "closed": dict(sorted(closed.items())),
        "open": dict(sorted(opened.items())),
        "cases": cases,
    }
    if section == "errors":
        entry["text"] = fixture.get("text")
    if section == "envelope":
        envelope: set[str] = set()
        for shape in ("success", "failure"):
            schema_facts(fixture["response"][shape], "", envelope)
        entry["response"] = {
            f.split(" ")[1]: f.split(" ")[2] for f in sorted(envelope) if f.startswith("req ")
        }
    if section == "events":
        entry["frameType"] = fixture.get("frameType")
    return entry


def build_floor(
    fixtures: list[dict[str, Any]],
    captured: dict[tuple[str, str], Any],
    withdrawals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    floor: dict[str, Any] = {
        "version": 1,
        "frozenBy": "#623 (ADR-0302)",
        "methods": {},
        "events": {},
        "errors": {},
        "envelope": {},
        "withdrawals": list(withdrawals or []),
    }
    for fixture in fixtures:
        section, ident = section_and_id(fixture)
        if ident in floor[section]:
            raise FloorError(f"{fixture['_file']}: {section} {ident!r} has two fixtures")
        floor[section][ident] = build_entry(fixture, captured)
    for section in SECTIONS:
        floor[section] = dict(sorted(floor[section].items()))
    return floor


def v1_digest(floor: dict[str, Any]) -> str:
    """sha256 of every ``since: "v1"`` case's digest, by section, entry and case name.

    An entry with no since-v1 case is left out (CX-5): a method, event family or
    case a later lane adds with ``"since": "#<issue>"`` leaves this value alone
    (ADR-0302 D9), and only a change to a frozen case moves it.
    """

    frozen: dict[str, dict[str, dict[str, str]]] = {}
    for section in SECTIONS:
        frozen[section] = {}
        for ident, entry in sorted(floor[section].items()):
            cases = {
                name: str(case.get("digest"))
                for name, case in sorted(entry["cases"].items())
                if case["since"] == "v1"
            }
            if cases:
                frozen[section][ident] = cases
    return hashlib.sha256(_dump(frozen).encode("utf-8")).hexdigest()


# --- containment ------------------------------------------------------------------


def _withdrawn(withdrawals: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(w.get("method")), str(w.get("case"))) for w in withdrawals}


def _param_values(sends: Any, name: str) -> list[Any]:
    if isinstance(sends, dict) and name in sends:
        return [sends[name]]
    return []


def _compare_params(
    where: str, new: dict[str, Any], old: dict[str, Any], exempt: list[Any]
) -> list[str]:
    problems = []
    for name, was in old.items():
        now = new.get(name)
        if now is None:
            problems.append(f"{where}: param {name!r} removed")
            continue
        if now.get("type") != was.get("type"):
            problems.append(f"{where}: param {name!r} type {was.get('type')} -> {now.get('type')}")
        if not was.get("required") and now.get("required"):
            problems.append(f"{where}: param {name!r} was optional and is now required")
        if "enum" in was:
            lost = [v for v in was["enum"] if v not in now.get("enum", []) and v not in exempt]
            if lost:
                problems.append(f"{where}: param {name!r} no longer accepts {lost}")
        for bound in PARAM_BOUNDS:
            if bound in was and (bound not in now or now[bound] < was[bound]):
                problems.append(f"{where}: param {name!r} {bound} {was[bound]} -> {now.get(bound)}")
        if "items" in was and (now.get("items") or {}).get("type") != was["items"].get("type"):
            problems.append(f"{where}: param {name!r} items {was['items']} -> {now.get('items')}")
        if "fields" in was:
            problems += _compare_params(
                f"{where} {name}[]", now.get("fields") or {}, was["fields"], exempt
            )
    return problems


def _compare_paths(where: str, label: str, new: dict[str, str], old: dict[str, str]) -> list[str]:
    problems = []
    for path, kind in old.items():
        if path not in new:
            problems.append(f"{where}: {label} path {path!r} ({kind}) is gone")
        elif not _types(kind) <= _types(new[path]):
            problems.append(f"{where}: {label} path {path!r} was {kind}, now {new[path]}")
    return problems


def _compare_entry(
    where: str,
    ident: str,
    new: dict[str, Any],
    old: dict[str, Any],
    withdrawn: set[tuple[str, str]],
) -> list[str]:
    problems = []
    for key in ("since", "requires", "text", "frameType"):
        if old.get(key) != new.get(key):
            problems.append(f"{where}: {key} {old.get(key)!r} -> {new.get(key)!r}")
    lost_hosts = sorted(set(old.get("hosts", [])) - set(new.get("hosts", [])))
    if lost_hosts:
        problems.append(f"{where}: no longer served on {lost_hosts}")
    exempt_cases = {name for name in old["cases"] if (ident, name) in withdrawn}
    exempt_values = [
        value
        for name in exempt_cases
        for param in old.get("params", {})
        for value in _param_values(old["cases"][name].get("sends"), param)
    ]
    problems += _compare_params(where, new.get("params", {}), old.get("params", {}), exempt_values)
    for label in ("data", "frame", "response", "forwarded"):
        if label in old:
            problems += _compare_paths(where, label, new.get(label, {}), old[label])
    for path, values in old.get("closed", {}).items():
        if new.get("closed", {}).get(path) != values:
            problems.append(
                f"{where}: closed enum {path} {values} -> {new.get('closed', {}).get(path)}"
            )
    for path, values in old.get("open", {}).items():
        lost = [v for v in values if v not in new.get("open", {}).get(path, [])]
        if lost:
            problems.append(f"{where}: open enum {path} lost {lost}")
    for name, case in old["cases"].items():
        if name in exempt_cases:
            continue
        now = new["cases"].get(name)
        if now is None:
            problems.append(f"{where}: case {name!r} removed")
            continue
        for key in ("since", "kind", "rig", "sends"):
            if now.get(key) != case.get(key):
                problems.append(f"{where}: case {name!r} {key} changed")
        if case.get("since") == "v1" and "digest" in case and now.get("digest") != case["digest"]:
            problems.append(
                f"{where}: case {name!r} changed, and a since-v1 case is frozen as written "
                "(restore it; a correction is a withdrawal, ADR-0302 D4)"
            )
        lost = sorted(set(case["facts"]) - set(now["facts"]))
        if lost:
            problems.append(f"{where}: case {name!r} no longer pins {lost}")
    return problems


def contains(
    new: dict[str, Any], old: dict[str, Any], withdrawals: list[dict[str, Any]] | None = None
) -> list[str]:
    """What ``new`` does not hold of ``old``; empty when it holds all of it (module docstring)."""

    problems: list[str] = []
    withdrawn = _withdrawn(list(withdrawals or []) + list(new.get("withdrawals", [])))
    for section in SECTIONS:
        for ident, entry in old.get(section, {}).items():
            where = f"{section} {ident}"
            if ident not in new.get(section, {}):
                problems.append(f"{where}: removed")
                continue
            problems += _compare_entry(where, ident, new[section][ident], entry, withdrawn)
    for record in old.get("withdrawals", []):
        if record not in new.get("withdrawals", []):
            problems.append(f"withdrawal {record} removed")
    return problems


# --- I/O --------------------------------------------------------------------------


def load_floor(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        floor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FloorError(f"{path}: {exc}") from exc
    if not isinstance(floor, dict) or floor.get("version") != 1:
        raise FloorError(f"{path}: not a version 1 floor")
    return floor


def dump_floor(floor: dict[str, Any]) -> str:
    return json.dumps(floor, indent=2, ensure_ascii=False) + "\n"


def capture(root: Path) -> dict[tuple[str, str], Any]:
    """Run every success case in process (``rigs.capture``) with this process kept off ``~``."""

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.env_sandbox import home_env
    from tests.rpc.control_v1 import rigs

    with tempfile.TemporaryDirectory(prefix="control-v1-") as scratch:
        base = Path(scratch)
        saved = dict(os.environ)
        os.environ.update(home_env(base / "home"))
        os.environ["AELIX_CODING_AGENT_DIR"] = str(base / "agent")
        os.environ.pop("AELIX_SETTINGS_PATH", None)
        try:
            return asyncio.run(rigs.capture(base / "rigs"))
        finally:
            os.environ.clear()
            os.environ.update(saved)


def withdrawal_record(spec: str, note: str, issue: str) -> dict[str, Any]:
    method, sep, case = spec.partition(":")
    if not sep or not method or not case:
        raise FloorError(f"--withdraw takes <method>:<case>, not {spec!r}")
    if not DATE.fullmatch(note):
        raise FloorError(f"--note takes the ADR-0302 note's date, YYYY-MM-DD, not {note!r}")
    if not re.fullmatch(r"#\d+", issue):
        raise FloorError(f"--issue takes '#<number>', not {issue!r}")
    return {"method": method, "case": case, "note": f"ADR-0302 Note ({note})", "addedIn": issue}


def cmd_update(root: Path, withdraw: str | None, note: str | None, issue: str | None) -> int:
    path = root / FLOOR_REL
    old = load_floor(path)
    previous = list(old["withdrawals"]) if old else []
    records = previous[:]
    if withdraw is not None:
        if note is None or issue is None:
            raise FloorError("--withdraw needs --note <YYYY-MM-DD> and --issue '#<n>'")
        record = withdrawal_record(withdraw, note, issue)
        if record not in records:
            records.append(record)
    fresh = build_floor(load_fixtures(root), capture(root), records)
    problems = contains(fresh, old, records) if old else []
    if problems:
        print(
            "control-v1 floor NOT written: it would drop or loosen what it holds", file=sys.stderr
        )
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(
            "Version 1 only grows (ADR-0302 D3). Restore the fixture, or, for a safety refusal "
            "with a per-case owner decision and a dated ADR-0302 note, --withdraw <method>:<case> "
            "--note <YYYY-MM-DD> --issue '#<n>'.",
            file=sys.stderr,
        )
        return 1
    text = dump_floor(fresh)
    changed = not path.is_file() or path.read_text(encoding="utf-8") != text
    path.write_text(text, encoding="utf-8")
    counts = ", ".join(f"{len(fresh[s])} {s}" for s in SECTIONS)
    print(
        f"control-v1 floor {'written' if changed else 'unchanged'}: {counts}; v1 digest {v1_digest(fresh)}"
    )
    return 0


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    except OSError as exc:
        raise FloorError(f"git did not run: {exc}") from exc


def github_base(default: str) -> tuple[str, list[str]]:
    """``scripts/check_complexity.py``'s own :func:`github_base` (ADR-0300 section 6)."""

    spec = importlib.util.spec_from_file_location(
        "_check_complexity", Path(__file__).resolve().parent / "check_complexity.py"
    )
    if spec is None or spec.loader is None:
        raise FloorError("scripts/check_complexity.py did not load")
    module = sys.modules.get(spec.name) or importlib.util.module_from_spec(spec)
    if spec.name not in sys.modules:
        # Registered first: its dataclasses look their module up while they are built.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    try:
        return module.github_base(default)
    except module.MeasureError as exc:
        raise FloorError(str(exc)) from exc


def note_problems(root: Path, records: list[dict[str, Any]]) -> list[str]:
    adrs = sorted(root.glob(ADR_GLOB))
    text = "\n".join(p.read_text(encoding="utf-8") for p in adrs)
    problems = []
    for record in records:
        date = DATE.search(str(record.get("note", "")))
        if date is None or f"## Note ({date.group(0)}" not in text:
            problems.append(
                f"withdrawal {record}: no heading '## Note ({date.group(0) if date else '?'}' in "
                f"{ADR_GLOB} (ADR-0302 D4)"
            )
    return problems


def cmd_check(root: Path, base_ref: str, require_base: bool, github_event: bool) -> int:
    head = load_floor(root / FLOOR_REL)
    if head is None:
        print(f"control-v1 FAILED: {FLOOR_REL} is missing", file=sys.stderr)
        return 1
    notes: list[str] = []
    if github_event:
        base_ref, why = github_base(base_ref)
        notes += [note.replace("HISTORY ", "R3 ", 1) for note in why]
    proc = _git(root, "merge-base", "HEAD", base_ref)
    if proc.returncode != 0:
        line = f"R3 not compared: no merge base with {base_ref} ({proc.stderr.decode('utf-8', 'replace').strip()})"
        if require_base:
            print(
                f"control-v1 FAILED: {line}; --require-base makes this a failure", file=sys.stderr
            )
            return 1
        print(f"{line}\ncontrol-v1 OK - floor read; R3 skipped")
        return 0
    sha = proc.stdout.decode().strip()
    shown = _git(root, "show", f"{sha}:./{FLOOR_REL}")
    for note in notes:
        print(note)
    if shown.returncode != 0:
        print(f"R3 merge base {sha[:8]} has no {FLOOR_REL}: nothing to compare")
        print("control-v1 OK - the floor starts here")
        return 0
    try:
        old = json.loads(shown.stdout.decode("utf-8"))
    except ValueError as exc:
        raise FloorError(f"{FLOOR_REL} at {sha[:8]}: {exc}") from exc
    added = [w for w in head.get("withdrawals", []) if w not in old.get("withdrawals", [])]
    problems = contains(head, old, head.get("withdrawals", [])) + note_problems(root, added)
    if problems:
        print(
            f"control-v1 FAILED - the floor does not hold the floor at merge base {sha[:8]}:",
            file=sys.stderr,
        )
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{len(head[s])} {s}" for s in SECTIONS)
    print(f"control-v1 OK - the floor ({counts}) holds the floor at merge base {sha[:8]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="R3: the floor holds the merge base's")
    mode.add_argument("--update", action="store_true", help="regenerate the floor (only adds)")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="the repository")
    parser.add_argument(
        "--base", default="origin/main", help="R3: compare with merge-base HEAD BASE"
    )
    parser.add_argument("--require-base", action="store_true", help="R3: fail without a merge base")
    parser.add_argument(
        "--github-event",
        action="store_true",
        help="R3 in GitHub Actions: a push to main compares with `before`",
    )
    parser.add_argument("--withdraw", metavar="METHOD:CASE", help="--update: record a withdrawal")
    parser.add_argument("--note", metavar="YYYY-MM-DD", help="--withdraw: the ADR-0302 note's date")
    parser.add_argument("--issue", metavar="#N", help="--withdraw: the issue that withdraws it")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.update:
            return cmd_update(root, args.withdraw, args.note, args.issue)
        if args.withdraw or args.note or args.issue:
            parser.error("--withdraw, --note and --issue go with --update")
        return cmd_check(root, args.base, args.require_base, args.github_event)
    except FloorError as exc:
        print(f"control-v1: cannot check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
