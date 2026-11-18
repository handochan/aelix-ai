"""What the floor lets grow and what it refuses, end to end (#623, ADR-0302 D3, D4, D8, D9).

The scratch tests copy the fixtures into a git repository under ``tmp_path`` and
run ``scripts/check_control_v1.py`` there: ``--update`` with the in-process
capture replaced by an empty one (a case's facts and digest come from its
fixture alone; only ``forwarded`` comes from the capture), R1 and R2 as
:func:`contains` both ways between a fresh build and the written floor, and R3
as ``--check --base <the commit before> --require-base``.

* A method added later, whose cases are all ``"since": "#612"``, leaves the v1
  digest at :data:`V1_SHA256` and passes R1-R3 (Codex CX-5).
* A value added later to an open enum, in a case of its own, is an addition
  (CX-7); written into a v1 case instead, it is a change to a frozen case.
* Dropping the ``check`` of a v1 case, or the ``not`` of its schema, is refused by
  ``--update``, and a floor regenerated from scratch is refused by R3 (CX-6).
* A later case that loses an execution step (``before``, ``check``) or a schema
  assertion (``not``, ``anyOf``, a nested ``required``, a ``type``) is refused.
* A schema keyword or a case key the floor does not capture is refused, not
  dropped (CX-6).

The in-process tests show an open enum's unknown value passing the existing
cases on a real rig (CX-7), and the closed row vocabularies refusing one (CX-8).

Every method, field, enum value, row and case name these tests make up starts with
``zz-synthetic`` (or ``zzSynthetic`` for a field), a prefix no lane gives a real one,
and a synthetic fixture or case is never written over a real one of that name: at
``3d4e7364``, with a real ``children.list.json`` added (a name #612 may take), nine of
these tests wrote their own over it and failed (#623 fix round 2, verify F4).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)
from tests.rpc.control_v1.test_additive_only import V1_SHA256

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "check_control_v1.py"
FIXTURES_REL = Path("tests") / "rpc" / "control_v1" / "fixtures"
FLOOR_REL = Path("tests") / "rpc" / "control_v1" / "floor_v1.json"


def _load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("_check_control_v1_rules", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# --- the scratch repository ---------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixtures and ADR-0302 in a fresh repository; ``--update`` captures nothing."""

    root = tmp_path / "repo"
    shutil.copytree(REPO / FIXTURES_REL, root / FIXTURES_REL)
    adr = next((REPO / "docs" / "decisions").glob("0302-*.md"))
    (root / "docs" / "decisions").mkdir(parents=True)
    shutil.copyfile(adr, root / "docs" / "decisions" / adr.name)
    _git(root, "init", "-q")
    monkeypatch.setattr(gate, "capture", lambda _root: {})
    return root


def _update(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = gate.main(["--update", "--root", str(repo)])
    out = capsys.readouterr()
    return code, out.out + out.err


def _check(repo: Path, base: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = gate.main(["--check", "--root", str(repo), "--base", base, "--require-base"])
    out = capsys.readouterr()
    return code, out.out + out.err


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _floor(repo: Path) -> dict[str, Any]:
    return json.loads((repo / FLOOR_REL).read_text(encoding="utf-8"))


def _edit(repo: Path, name: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = repo / FIXTURES_REL / name
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _case(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(case for case in data["cases"] if case["name"] == name)


def _frozen(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, dict[str, Any]]:
    """The freeze commit: the floor of the fixtures as they are, at :data:`V1_SHA256`."""

    code, out = _update(repo, capsys)
    assert code == 0, out
    floor = _floor(repo)
    assert gate.v1_digest(floor) == V1_SHA256
    return _commit(repo, "the freeze"), floor


def _r1_r2(repo: Path) -> None:
    written = _floor(repo)
    fresh = gate.build_floor(gate.load_fixtures(repo), {}, written["withdrawals"])
    assert gate.contains(fresh, written) == [], "R1"
    assert gate.contains(written, fresh) == [], "R2"


#: The made-up method a later lane adds, and its fixture's file name.
SYNTHETIC_METHOD = "zz-synthetic/list"
SYNTHETIC_FIXTURE = "zz-synthetic.list.json"
SYNTHETIC_CASE = "zz-synthetic: lists its rows"

SYNTHETIC = {
    "contract": "control-v1",
    "method": SYNTHETIC_METHOD,
    "since": "#612",
    "hosts": ["cli-rpc"],
    "requires": "zz-synthetic-service",
    "params": {},
    "consumers": [],
    "cases": [
        {
            "name": SYNTHETIC_CASE,
            "rig": "full",
            "replay": False,
            "before": [{"method": "ui/enable", "params": {"enabled": True}}],
            "params": {},
            "data": {
                "type": "object",
                "required": ["zzSyntheticRows"],
                "properties": {
                    "zzSyntheticRows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["zzSyntheticId"],
                            "properties": {"zzSyntheticId": {"type": "string"}},
                        },
                        "not": {"contains": {"const": "zz-synthetic removed row"}},
                    },
                    "zzSyntheticParent": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "zzSyntheticOwner": {
                        "type": "object",
                        "required": ["zzSyntheticName"],
                        "properties": {"zzSyntheticName": {"type": "string"}},
                    },
                    "zzSyntheticNote": {"type": "string"},
                },
            },
            "check": "session_file_exists",
        }
    ],
}


def _add_synthetic_fixture(repo: Path, name: str, fixture: dict[str, Any]) -> None:
    """Write *fixture* as a NEW file of the scratch copy's fixtures, never over one.

    The copy holds every real fixture: a synthetic name a lane later takes for a real
    method would otherwise replace that lane's fixture and fail these tests on it.
    """

    path = repo / FIXTURES_REL / name
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(fixture, indent=2) + "\n")
    except FileExistsError:
        pytest.fail(
            f"{name} is a real fixture: a synthetic one never overwrites it; give the "
            "synthetic method a zz-synthetic name no lane takes"
        )


def _add_synthetic_method(repo: Path) -> None:
    _add_synthetic_fixture(repo, SYNTHETIC_FIXTURE, SYNTHETIC)


def _add_synthetic_case(data: dict[str, Any], case: dict[str, Any]) -> None:
    """Append *case* to a real fixture's cases, never over a real case of that name."""

    if any(existing["name"] == case["name"] for existing in data["cases"]):
        pytest.fail(f"{case['name']!r} is a real case: a synthetic case never replaces one")
    data["cases"].append(case)


def test_a_synthetic_fixture_or_case_never_replaces_a_real_one(repo: Path) -> None:
    """The guard itself: a real fixture under the synthetic file name, or a real case under
    a synthetic case's name, stops the test and is left as it was."""

    real = repo / FIXTURES_REL / SYNTHETIC_FIXTURE
    real.write_text('{"method": "a real one"}\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="is a real fixture"):
        _add_synthetic_method(repo)
    assert real.read_text(encoding="utf-8") == '{"method": "a real one"}\n'
    data = {"cases": [{"name": SYNTHETIC_CASE, "since": "#612"}]}
    with pytest.raises(pytest.fail.Exception, match="is a real case"):
        _add_synthetic_case(data, {"name": SYNTHETIC_CASE, "since": "#9"})
    assert data == {"cases": [{"name": SYNTHETIC_CASE, "since": "#612"}]}


# --- growth ---------------------------------------------------------------------------


def test_a_method_added_later_leaves_the_v1_digest_alone(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, frozen = _frozen(repo, capsys)
    _add_synthetic_method(repo)
    code, out = _update(repo, capsys)
    assert code == 0, out
    grown = _floor(repo)
    assert SYNTHETIC_METHOD in grown["methods"]
    assert gate.v1_digest(grown) == V1_SHA256, "a later method moved the v1 digest"
    _r1_r2(repo)
    assert gate.contains(grown, frozen) == []
    _commit(repo, f"{SYNTHETIC_METHOD} (#612)")
    code, out = _check(repo, base, capsys)
    assert code == 0, out


SYNTHETIC_KIND = "zz-synthetic-kind"
SIXTH = ["agent", "extension", "mcp", "skill", "tool", SYNTHETIC_KIND]


def test_a_value_added_later_to_an_open_enum_is_an_addition(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, frozen = _frozen(repo, capsys)

    def add_case(data: dict[str, Any]) -> None:
        _add_synthetic_case(
            data,
            {
                "name": "zz-synthetic: a later selection kind is listed",
                "since": "#612",
                "rig": "full",
                "replay": False,
                "params": {},
                "data": {
                    "type": "object",
                    "required": ["selectionKinds"],
                    "properties": {
                        "selectionKinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": SIXTH, "open": True},
                            "allOf": [{"contains": {"const": SYNTHETIC_KIND}}],
                        }
                    },
                },
            },
        )

    _edit(repo, "capabilities.json", add_case)
    code, out = _update(repo, capsys)
    assert code == 0, out
    grown = _floor(repo)
    assert SYNTHETIC_KIND in grown["methods"]["capabilities"]["open"]["data.selectionKinds[]"]
    assert gate.v1_digest(grown) == V1_SHA256
    _r1_r2(repo)
    assert gate.contains(grown, frozen) == []
    _commit(repo, f"{SYNTHETIC_KIND} (#612)")
    code, out = _check(repo, base, capsys)
    assert code == 0, out


def test_an_open_value_written_into_a_v1_case_is_a_change_to_a_frozen_case(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _frozen(repo, capsys)

    def widen(data: dict[str, Any]) -> None:
        items = _case(data, "a host with every service")["data"]["properties"]["selectionKinds"]
        items["items"]["enum"] = SIXTH

    _edit(repo, "capabilities.json", widen)
    code, out = _update(repo, capsys)
    assert code == 1 and "a host with every service" in out, out


# --- weakening --------------------------------------------------------------------------


def _drop_cancel_checks(data: dict[str, Any]) -> None:
    for case in data["cases"]:
        if case.get("check") == "cancelled_the_during_command":
            del case["check"]


def _drop_server_ws_not(data: dict[str, Any]) -> None:
    case = _case(data, "the server-ws shape serves only the unconditional methods")
    del case["data"]["properties"]["methods"]["not"]


WEAKENINGS = {
    "the cancel cases lose their check": (
        "commands.cancel.json",
        _drop_cancel_checks,
        "cancels the running command",
    ),
    "the server-ws case loses its not": (
        "capabilities.json",
        _drop_server_ws_not,
        "the server-ws shape serves only the unconditional methods",
    ),
}


@pytest.mark.parametrize("what", sorted(WEAKENINGS))
def test_weakening_a_v1_case_is_refused_by_update_and_by_r3(
    what: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name, weaken, case = WEAKENINGS[what]
    base, _frozen_floor = _frozen(repo, capsys)
    _edit(repo, name, weaken)
    code, out = _update(repo, capsys)
    assert code == 1 and case in out, f"--update wrote the weakened floor: {out}"
    # Regenerated from scratch (the floor deleted first), R3 still refuses it.
    (repo / FLOOR_REL).unlink()
    code, out = _update(repo, capsys)
    assert code == 0, out
    assert gate.v1_digest(_floor(repo)) != V1_SHA256
    _commit(repo, "weakened")
    code, out = _check(repo, base, capsys)
    assert code == 1 and case in out, out


def _synthetic_data(data: dict[str, Any]) -> dict[str, Any]:
    return data["cases"][0]["data"]["properties"]


LATER_LOSSES: dict[str, Callable[[dict[str, Any]], None]] = {
    "its before step": lambda data: data["cases"][0].pop("before"),
    "its check": lambda data: data["cases"][0].pop("check"),
    "a not": lambda data: _synthetic_data(data)["zzSyntheticRows"].pop("not"),
    "an anyOf": lambda data: _synthetic_data(data)["zzSyntheticParent"].pop("anyOf"),
    "a required member of an item": lambda data: _synthetic_data(data)["zzSyntheticRows"][
        "items"
    ].pop("required"),
    "a type": lambda data: _synthetic_data(data)["zzSyntheticRows"]["items"]["properties"][
        "zzSyntheticId"
    ].pop("type"),
    # Off the required chain: only the reqif and type facts hold these.
    "a required member of an optional object": lambda data: _synthetic_data(data)[
        "zzSyntheticOwner"
    ].pop("required"),
    "the type of an optional field": lambda data: _synthetic_data(data)["zzSyntheticNote"].pop(
        "type"
    ),
}


@pytest.mark.parametrize("what", sorted(LATER_LOSSES))
def test_a_later_case_that_loses_a_step_or_an_assertion_is_refused(
    what: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``since: "#612"`` case is held by its facts: losing one is refused (CX-6)."""

    _frozen(repo, capsys)
    _add_synthetic_method(repo)
    assert _update(repo, capsys)[0] == 0
    base = _commit(repo, f"{SYNTHETIC_METHOD} (#612)")
    _edit(repo, SYNTHETIC_FIXTURE, LATER_LOSSES[what])
    code, out = _update(repo, capsys)
    assert code == 1 and SYNTHETIC_CASE in out, out
    # Regenerated from scratch, R3 refuses it.
    (repo / FLOOR_REL).unlink()
    assert _update(repo, capsys)[0] == 0
    _commit(repo, "weakened")
    code, out = _check(repo, base, capsys)
    assert code == 1 and SYNTHETIC_CASE in out, out


UNCAPTURED = {
    "a schema keyword": lambda data: _case(data, "a host with every service")["data"]["properties"][
        "version"
    ].update(minimum=1),
    "a combinator the floor does not read": lambda data: _case(data, "a host with every service")[
        "data"
    ]["properties"]["version"].update(oneOf=[{"const": 1}]),
    "a case key": lambda data: _case(data, "a host with every service").update(
        after=[{"method": "ui/enable", "params": {"enabled": True}}]
    ),
}


@pytest.mark.parametrize("what", sorted(UNCAPTURED))
def test_what_the_floor_does_not_capture_is_refused(
    what: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _edit(repo, "capabilities.json", UNCAPTURED[what])
    code, out = _update(repo, capsys)
    assert code == 2 and "a host with every service" in out, out
    assert not (repo / FLOOR_REL).exists()


# --- open enums and closed vocabularies on a real rig ---------------------------------


def _fixture_case(fixture_id: str, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = next(f for f in rigs.load_fixtures() if rigs.fixture_id(f) == fixture_id)
    return fixture, _case(fixture, name)


@pytest.mark.usefixtures("rig_env")
async def test_a_sixth_selection_kind_passes_the_existing_capabilities_case(
    tmp_path: Path,
) -> None:
    fixture, case = _fixture_case("capabilities", "a host with every service")
    result, _check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
    rigs.assert_case(fixture, case, result)
    result.response["data"]["selectionKinds"].append(SYNTHETIC_KIND)
    rigs.assert_case(fixture, case, result)


@pytest.mark.parametrize("combinator", ["anyOf", "allOf"])
def test_an_open_enum_under_any_of_or_all_of_restricts_nothing(combinator: str) -> None:
    """``rigs.opened`` walks ``anyOf`` and ``allOf`` too: an open enum there lets a later
    value through, and a closed one still refuses it (verify r2 N7, mutant G04; no
    fixture has an open enum there yet)."""

    def schema(*, is_open: bool) -> dict[str, Any]:
        branch: dict[str, Any] = {"type": "string", "enum": ["zz-synthetic-known"]}
        if is_open:
            branch["open"] = True
        return {"type": "object", "properties": {"zzSyntheticField": {combinator: [branch]}}}

    later = {"zzSyntheticField": "zz-synthetic-later"}
    rigs._validate(schema(is_open=True), later, "an open enum")
    with pytest.raises(AssertionError, match="does not match its schema"):
        rigs._validate(schema(is_open=False), later, "a closed enum")


def _new_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row = copy.deepcopy(rows[0])
    row["key"] = "zz-synthetic-row"
    row["role"] = "zz-synthetic-role"
    row["lock"] = {
        "value": False,
        "reason": "zz-synthetic lock",
        "tag": "zz-synthetic",
        "kind": "zz-synthetic-lock-kind",
        "refusal": "zz-synthetic refusal",
    }
    return row


@pytest.mark.usefixtures("rig_env")
async def test_a_new_row_role_and_lock_kind_pass_the_existing_listing_case(
    tmp_path: Path,
) -> None:
    fixture, case = _fixture_case("selections/list", "lists the skills")
    result, _check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
    rigs.assert_case(fixture, case, result)
    rows = result.response["data"]["items"]
    rows.append(_new_row(rows))
    rigs.assert_case(fixture, case, result)


CLOSED_EDITS: dict[str, Callable[[dict[str, Any]], None]] = {
    "a decidedBy outside global and project": lambda data: data["items"][0].update(
        decidedBy="zz-synthetic-scope"
    ),
    "a third scope": lambda data: data["scopes"].update(
        {"zz-synthetic-scope": copy.deepcopy(data["scopes"]["global"])}
    ),
}


@pytest.mark.usefixtures("rig_env")
async def test_the_closed_row_vocabularies_refuse_a_new_value_and_allow_a_new_field(
    tmp_path: Path,
) -> None:
    fixture, case = _fixture_case("selections/list", "lists the skills")
    result, _check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
    answer = copy.deepcopy(result.response)
    for what, edit in CLOSED_EDITS.items():
        result.response = copy.deepcopy(answer)
        edit(result.response["data"])
        with pytest.raises(AssertionError, match="does not match its schema"):
            rigs.assert_case(fixture, case, result)
        assert what
    # An ordinary added field is not a closed set: a v1 client ignores it.
    result.response = copy.deepcopy(answer)
    result.response["data"]["items"][0]["zzSyntheticField"] = 1
    result.response["data"]["scopes"]["global"]["zzSyntheticField"] = 1
    rigs.assert_case(fixture, case, result)
