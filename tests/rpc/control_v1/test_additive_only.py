"""The floor only grows: R1, R2 and the frozen v1 cases (#623, ADR-0302 D8).

* R1: a fresh build (the fixtures plus a fresh in-process capture of every
  success case) holds the committed ``floor_v1.json``: nothing dropped or
  loosened, by ``scripts/check_control_v1.py``'s :func:`contains`.
* R2: the committed floor holds the fresh build, and is byte for byte what
  ``--update`` writes: an addition was run through ``--update``.
* The ``since: "v1"`` cases hash to :data:`V1_SHA256`, without git: the digest of
  each frozen case as written (``scripts/check_control_v1.py`` ``case_digest``),
  by entry, leaving out an entry with no since-v1 case. The freeze commit's own
  additions (#624's ``selections/*``) are ``since: "v1"`` and set this value
  once, in that commit; a later lane's cases and methods are
  ``since: "#<issue>"`` and leave it alone (``test_ratchet_rules.py`` shows it
  end to end); only a withdrawal of a v1 case moves it again.

R3 (the floor holds the merge base's floor) needs git and runs in CI:
``scripts/check_control_v1.py --check --require-base --github-event``. The
unit tests at the end show :func:`contains` refuses each kind of shrinkage.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import sandbox_home
from tests.rpc.control_v1 import rigs

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "check_control_v1.py"
V1_SHA256 = "62db178035bea51ce9630dff3ef081ca7bcb1289648864935f94ec90254f0436"


def _load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("_check_control_v1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(rigs.FLOOR.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fresh(
    tmp_path_factory: pytest.TempPathFactory, committed: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("control-v1-capture")
    with pytest.MonkeyPatch.context() as monkeypatch:
        sandbox_home(monkeypatch, root / "home")
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(root / "agent"))
        monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
        captured = asyncio.run(rigs.capture(root / "rigs"))
    yield gate.build_floor(gate.load_fixtures(REPO), captured, committed["withdrawals"])


def test_r1_a_fresh_build_holds_the_committed_floor(
    fresh: dict[str, Any], committed: dict[str, Any]
) -> None:
    problems = gate.contains(fresh, committed)
    assert problems == [], "version 1 lost or loosened (ADR-0302 D3):\n" + "\n".join(problems)


def test_r2_the_committed_floor_holds_a_fresh_build(
    fresh: dict[str, Any], committed: dict[str, Any]
) -> None:
    problems = gate.contains(committed, fresh)
    assert problems == [], (
        "the fixtures hold what the floor does not: run "
        "`uv run python scripts/check_control_v1.py --update`:\n" + "\n".join(problems)
    )
    assert rigs.FLOOR.read_text(encoding="utf-8") == gate.dump_floor(fresh), (
        "floor_v1.json is not what --update writes now; run it"
    )


def test_the_v1_cases_are_the_frozen_ones(fresh: dict[str, Any], committed: dict[str, Any]) -> None:
    assert gate.v1_digest(committed) == V1_SHA256
    assert gate.v1_digest(fresh) == V1_SHA256


# --- contains() refuses every kind of shrinkage ------------------------------------


def _floor() -> dict[str, Any]:
    case = {
        "since": "v1",
        "kind": "data",
        "rig": "full",
        "sends": {"mode": "plan"},
        "digest": "d-plan",
        "facts": ['const data.mode "plan"', "req data.mode string"],
    }
    later = {
        "since": "#612",
        "kind": "data",
        "rig": "full",
        "sends": {"mode": "plan"},
        "digest": "d-later",
        "facts": ['step check "session_file_exists"', "req data.mode string"],
    }
    return {
        "version": 1,
        "methods": {
            "permissions/set": {
                "since": "v1",
                "hosts": ["cli-rpc"],
                "requires": "posture",
                "params": {
                    "mode": {"type": "string", "required": True, "enum": ["plan", "yolo"]},
                    "opt": {"type": "string", "required": False, "maxLength": 10},
                },
                "data": {"mode": "string"},
                "forwarded": {"mode": "string"},
                "closed": {"data.mode": ["plan", "yolo"]},
                "open": {"data.kind": ["a", "b"]},
                "cases": {
                    "sets plan": case,
                    "sets yolo": {
                        **case,
                        "sends": {"mode": "yolo"},
                        "digest": "d-yolo",
                        "facts": [],
                    },
                    "a later case": later,
                },
            }
        },
        "events": {},
        "errors": {},
        "envelope": {},
        "withdrawals": [],
    }


def _edit(path: list[Any], value: Any) -> dict[str, Any]:
    floor = _floor()
    node: Any = floor
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return floor


_DELETE = object()
M = ["methods", "permissions/set"]
SHRINKAGES = {
    "a method removed": (["methods", "permissions/set"], _DELETE),
    "a host dropped": ([*M, "hosts"], []),
    "a param removed": ([*M, "params", "mode"], _DELETE),
    "a param's type changed": ([*M, "params", "mode", "type"], "integer"),
    "an optional param made required": ([*M, "params", "opt", "required"], True),
    "an accepted value refused": ([*M, "params", "mode", "enum"], ["yolo"]),
    "a bound lowered": ([*M, "params", "opt", "maxLength"], 9),
    "a result path removed": ([*M, "data"], {}),
    "a forwarded path's type changed": ([*M, "forwarded", "mode"], "integer"),
    "a closed enum grown": ([*M, "closed", "data.mode"], ["plan", "yolo", "new"]),
    "an open enum's value dropped": ([*M, "open", "data.kind"], ["a"]),
    "a case removed": ([*M, "cases", "sets plan"], _DELETE),
    "a case's request changed": ([*M, "cases", "sets plan", "sends"], {"mode": "yolo"}),
    "a case's pin dropped": ([*M, "cases", "sets plan", "facts"], ["req data.mode string"]),
    "a case's kind changed": ([*M, "cases", "sets plan", "kind"], "error"),
    "a case's rig changed": ([*M, "cases", "sets plan", "rig"], "child"),
    "a v1 case edited (its digest changed)": ([*M, "cases", "sets plan", "digest"], "d-other"),
    "a later case's step dropped": (
        [*M, "cases", "a later case", "facts"],
        ["req data.mode string"],
    ),
    "since rewritten": ([*M, "since"], "#612"),
}


@pytest.mark.parametrize("what", sorted(SHRINKAGES))
def test_contains_refuses(what: str) -> None:
    path, value = SHRINKAGES[what]
    assert gate.contains(_edit(path, value), _floor()) != [], what


def test_contains_accepts_additions() -> None:
    grown = _floor()
    entry = grown["methods"]["permissions/set"]
    entry["hosts"].append("server-ws")
    entry["params"]["mode"]["enum"].append("auto")
    entry["params"]["new"] = {"type": "string", "required": False}
    entry["params"]["opt"]["maxLength"] = 20
    entry["data"]["extra"] = "string"
    entry["forwarded"]["mode"] = "null|string"
    entry["open"]["data.kind"].append("c")
    # A later case is held by its facts: it may gain one, and its digest moves.
    entry["cases"]["a later case"]["facts"].append('const data.extra "x"')
    entry["cases"]["a later case"]["digest"] = "d-later-grown"
    entry["cases"]["new case"] = {
        "since": "#612",
        "kind": "data",
        "rig": "full",
        "sends": {},
        "digest": "d-new",
        "facts": [],
    }
    grown["methods"]["selections/list"] = copy.deepcopy(entry)
    assert gate.contains(grown, _floor()) == []


def test_a_v1_case_does_not_grow_either() -> None:
    """Adding a pin to a since-v1 case edits a frozen case: a later lane adds a case."""

    grown = _floor()
    case = grown["methods"]["permissions/set"]["cases"]["sets plan"]
    case["facts"].append('const data.extra "x"')
    case["digest"] = "d-plan-grown"
    assert gate.contains(grown, _floor()) != []


def _digest_floor(**entries: dict[str, Any]) -> dict[str, Any]:
    floor: dict[str, Any] = {s: {} for s in gate.SECTIONS}
    floor["methods"] = entries
    return floor


def test_the_v1_digest_leaves_out_an_entry_with_no_v1_case() -> None:
    """Codex CX-5: a method whose cases are all later moves the v1 digest by nothing."""

    v1 = {"cases": {"a": {"since": "v1", "digest": "d1"}}}
    later = {"cases": {"b": {"since": "#612", "digest": "d2"}}}
    frozen = gate.v1_digest(_digest_floor(**{"zz-synthetic/get": v1}))
    grown = _digest_floor(**{"zz-synthetic/get": v1, "zz-synthetic/list": later})
    assert gate.v1_digest(grown) == frozen
    v1_more = {"cases": {**v1["cases"], **later["cases"]}}
    assert gate.v1_digest(_digest_floor(**{"zz-synthetic/get": v1_more})) == frozen
    edited = {"cases": {"a": {"since": "v1", "digest": "d1-edited"}}}
    assert gate.v1_digest(_digest_floor(**{"zz-synthetic/get": edited})) != frozen


def test_a_withdrawal_exempts_only_the_case_it_names() -> None:
    record = {
        "method": "permissions/set",
        "case": "sets yolo",
        "note": "ADR-0302 Note (2026-12-01)",
        "addedIn": "#900",
    }
    shrunk = _floor()
    entry = shrunk["methods"]["permissions/set"]
    entry["params"]["mode"]["enum"] = ["plan"]
    entry["cases"]["sets yolo"]["kind"] = "error"
    assert gate.contains(shrunk, _floor()) != []
    shrunk["withdrawals"] = [record]
    assert gate.contains(shrunk, _floor()) == []
    del entry["cases"]["sets plan"]
    assert gate.contains(shrunk, _floor()) != []
    # A record, once in the floor, is never removed.
    kept = _floor()
    kept["withdrawals"] = [record]
    assert gate.contains(_floor(), kept) != []


# --- R3 (--check) in a scratch repository ------------------------------------------


def _git(repo: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def _commit_floor(repo: Path, floor: dict[str, Any], message: str) -> str:
    path = repo / "tests" / "rpc" / "control_v1" / "floor_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gate.dump_floor(floor), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _check(repo: Path, base: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = gate.main(["--check", "--root", str(repo), "--base", base, "--require-base"])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_r3_fails_a_floor_that_lost_what_the_base_held(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit_floor(repo, _floor(), "base")
    grown = _floor()
    grown["methods"]["permissions/set"]["cases"]["new"] = {
        "since": "#612",
        "kind": "data",
        "rig": "full",
        "sends": {},
        "facts": [],
    }
    _commit_floor(repo, grown, "an addition")
    assert _check(repo, base, capsys)[0] == 0
    shrunk = _floor()
    del shrunk["methods"]["permissions/set"]["cases"]["sets plan"]
    _commit_floor(repo, shrunk, "a removal")
    code, text = _check(repo, base, capsys)
    assert code == 1 and "case 'sets plan' removed" in text, text


def test_r3_takes_a_withdrawal_only_with_its_dated_adr_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit_floor(repo, _floor(), "base")
    withdrawn = _floor()
    del withdrawn["methods"]["permissions/set"]["cases"]["sets yolo"]
    withdrawn["withdrawals"] = [
        {
            "method": "permissions/set",
            "case": "sets yolo",
            "note": "ADR-0302 Note (2026-12-01)",
            "addedIn": "#900",
        }
    ]
    _commit_floor(repo, withdrawn, "a withdrawal")
    code, text = _check(repo, base, capsys)
    assert code == 1 and "Note (2026-12-01" in text, text
    adr = repo / "docs" / "decisions" / "0302-control-v1.md"
    adr.parent.mkdir(parents=True)
    adr.write_text("# 0302\n\n## Note (2026-12-01): yolo is refused\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the note")
    assert _check(repo, base, capsys)[0] == 0


def test_r3_without_a_merge_base_fails_only_under_require_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit_floor(repo, _floor(), "only")
    assert _check(repo, "no-such-ref", capsys)[0] == 1
    assert gate.main(["--check", "--root", str(repo), "--base", "no-such-ref"]) == 0


def test_r3_on_a_push_to_main_compares_with_the_commit_the_push_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--github-event`` takes the base from ``scripts/check_complexity.py``'s ``github_base``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    before = _commit_floor(repo, _floor(), "the main a push replaced")
    shrunk = _floor()
    del shrunk["methods"]["permissions/set"]["cases"]["sets plan"]
    _commit_floor(repo, shrunk, "the push")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": before}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    # HEAD is its own merge base with HEAD: only the event's ``before`` finds the shrinkage.
    code = gate.main(
        ["--check", "--root", str(repo), "--base", "HEAD", "--require-base", "--github-event"]
    )
    out = capsys.readouterr()
    assert code == 1, out.out + out.err
    assert f"compared with {before[:8]}" in out.out and "removed" in out.err
