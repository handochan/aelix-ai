"""#266 — three documents pinned to what they describe.

WHAT WAS WRONG (measured on ``27fa21c5``, ``.omc/probes/b4-b-live/266/``):

* ``README.md`` and ``README.ko.md`` opened their architecture section with
  "Three packages" / "세 패키지" and listed ``aelix-ai``, ``aelix-agent-core``
  and ``aelix-coding-agent``. The uv workspace has five members: the root
  project ``aelix`` (the package users install, ``uv tool install
  aelix@latest``) and the four under ``packages/``, of which ``aelix-server``
  was missing too. Nothing compared the READMEs with the workspace. (Review
  round 2: the first fix compared them with ``packages/`` only and said "Four
  packages", leaving out the root; the cross-review read the members from
  ``uv.lock``.)
* ``docs/00-conventions.md`` — the document every other document points to for
  its terms — named ``AgentRuntime`` "the runtime kernel object in the code".
  ``git grep AgentRuntime -- 'packages/*/src'`` finds nothing; the objects are
  ``Agent`` and ``AgentHarness``.
* ``docs/01-product-vision.md`` still set the goal of reimplementing pi "거의
  완벽에 가깝게", which ADR-0235 retired on 2026-09-03, and said nothing of the
  security/self-host-first positioning the owner confirmed in #86.

Each block below is red on that tree. The package list is derived from the
workspace's own declaration (the root ``pyproject.toml``'s ``[project]`` and
``[tool.uv.workspace]``) and the term paths are imported, so neither side is a
list typed into this file. pi carries the same README table with no check, and on
``fdce48eb4`` it lists 7 of its 11 packages (``codemode``, ``env``, ``evals``
and ``mcp`` are missing) — the drift this file exists to catch.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under ``heading`` (a whole line), up to the next ``## `` heading."""

    lines = text.splitlines()
    assert heading in lines, f"heading {heading!r} not found"
    body: list[str] = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


# === 1. the README's package list is the tree's ===============================

# Each README: its architecture heading, the pattern that captures the count word
# opening the section, and the word for each count. A count with no word here
# fails with a message saying to add one.
_READMES = {
    "README.md": (
        "## Architecture",
        re.compile(r"^(\w+) packages\b"),
        {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight"},
    ),
    "README.ko.md": (
        "## 아키텍처",
        re.compile(r"^(\S+) 패키지"),
        {2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟"},
    ),
}
_PACKAGE_BULLET = re.compile(r"^- \*\*`([^`]+)`\*\*", re.MULTILINE)


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _workspace_packages() -> list[str]:
    """``[project].name`` of every uv workspace member, as uv reads them.

    uv's workspace is the root project plus every directory a
    ``[tool.uv.workspace]`` ``members`` glob matches and no ``exclude`` glob
    does (each with a ``pyproject.toml``); ``uv.lock``'s ``[manifest].members``
    lists the same names (the last row of this block pins that).
    """

    root = _toml(REPO_ROOT / "pyproject.toml")
    workspace = root["tool"]["uv"]["workspace"]
    excluded = {
        path.resolve()
        for pattern in workspace.get("exclude", [])
        for path in REPO_ROOT.glob(pattern)
    }
    names = [root["project"]["name"]]
    for pattern in workspace["members"]:
        for member in sorted(REPO_ROOT.glob(pattern)):
            manifest = member / "pyproject.toml"
            if manifest.is_file() and member.resolve() not in excluded:
                names.append(_toml(manifest)["project"]["name"])
    return names


def test_the_workspace_has_packages_to_compare() -> None:
    """Guards the derivation: an empty tree would make every README agree, and
    the root project is a member uv counts (the package users install)."""

    names = _workspace_packages()
    assert len(names) > 1, names
    assert names[0] == "aelix", names


def test_the_derived_members_are_the_ones_uv_locked() -> None:
    """The derivation reads what uv reads: ``uv.lock`` records the workspace's
    members, so a glob this file expands differently from uv fails here."""

    lock = _toml(REPO_ROOT / "uv.lock")
    members = lock["manifest"]["members"]
    assert sorted(_workspace_packages()) == sorted(members), members


@pytest.mark.parametrize("readme", sorted(_READMES))
def test_the_readme_lists_every_package_and_no_other(readme: str) -> None:
    heading, _count, _words = _READMES[readme]
    listed = _PACKAGE_BULLET.findall(_section(_read(readme), heading))
    actual = _workspace_packages()
    missing = sorted(set(actual) - set(listed))
    extra = sorted(set(listed) - set(actual))
    assert not missing and not extra, (
        f"{readme} {heading!r} lists {listed}, the uv workspace holds {actual}: "
        f"add a '- **`<name>`** — …' line for {missing}, remove {extra}"
    )
    assert len(listed) == len(set(listed)), f"{readme} lists a package twice: {listed}"


@pytest.mark.parametrize("readme", sorted(_READMES))
def test_the_readme_counts_the_packages_it_lists(readme: str) -> None:
    heading, count, words = _READMES[readme]
    lead = next(line for line in _section(_read(readme), heading).splitlines() if line.strip())
    match = count.match(lead)
    assert match, f"{readme} {heading!r} should open with the package count: {lead!r}"
    n = len(_workspace_packages())
    assert n in words, f"add the word for {n} to _READMES[{readme!r}]"
    assert match.group(1) == words[n], (
        f"{readme} says {match.group(1)!r} packages, the uv workspace holds {n} ({words[n]!r})"
    )


# === 1b. what the README says the server and the root package are ===========


def _bullet(readme: str, name: str) -> str:
    heading = _READMES[readme][0]
    for line in _section(_read(readme), heading).splitlines():
        if line.startswith(f"- **`{name}`**"):
            return line
    raise AssertionError(f"{readme} {heading!r} has no line for {name}")


@pytest.mark.parametrize("readme", sorted(_READMES))
def test_the_server_line_names_only_the_route_measured_below(readme: str) -> None:
    """Review round 2 (cross): the first line said the daemon serves ``/rpc``
    "plus ``/healthz`` and ``/schemas``" with "no web UI". ``GET /schemas`` is
    404 (the route is ``/schemas/{name}``, and 404 outside a repo checkout), and
    FastAPI serves ``/docs``, ``/redoc`` and ``/openapi.json``. The line now
    names the package, what it is and where its own README is, and the only
    route it names is the one the next row drives."""

    line = _bullet(readme, "aelix-server")
    assert "(packages/aelix-server/README.md)" in line, line
    assert (REPO_ROOT / "packages" / "aelix-server" / "README.md").is_file()
    assert re.findall(r"`(/[^`]*)`", line) == ["/rpc"], line
    assert "PyPI" in line, line


def test_what_the_server_line_says_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the line says, each part measured: "a local daemon that serves the
    RPC protocol over HTTP (a WebSocket at ``/rpc``); not published to PyPI".
    The bind it gets with no ``AELIX_SERVER_*`` set (``main.py`` hands it to
    uvicorn), an RPC command answered over a WebSocket upgrade at ``/rpc``
    through FastAPI's test client, and the release job deleting its wheel and
    sdist before the upload."""

    pytest.importorskip("fastapi")
    from aelix_server import ServerConfig, create_app
    from fastapi.testclient import TestClient

    from tests.env_sandbox import sandbox_home

    for name in list(os.environ):
        if name.startswith("AELIX_SERVER_"):
            monkeypatch.delenv(name)
    assert ServerConfig.from_env().bind == "127.0.0.1"

    sandbox_home(monkeypatch, tmp_path)
    config = ServerConfig(
        bind="127.0.0.1", port=0, cwd=str(tmp_path), model="", provider="", schemas_dir=tmp_path
    )
    with TestClient(create_app(config)) as client, client.websocket_connect("/rpc") as ws:
        ws.send_text(json.dumps({"type": "get_state", "id": "266"}))
        frame = json.loads(ws.receive_text())
    assert (frame["type"], frame["command"], frame["id"], frame["success"]) == (
        "response",
        "get_state",
        "266",
        True,
    ), frame

    release = _read(".github/workflows/release.yml")
    assert "rm -f dist/aelix_server-*.whl dist/aelix_server-*.tar.gz" in release


@pytest.mark.parametrize("readme", sorted(_READMES))
def test_what_the_root_package_line_says_holds(readme: str) -> None:
    """The fifth member, which the first fix left out: the root project is the
    package users install, it gives the ``aelix`` command, depends on the three
    library packages and re-exports their main names (``src/aelix``)."""

    from aelix_agent_core.agent import Agent
    from aelix_agent_core.harness import AgentHarness

    import aelix

    line = _bullet(readme, "aelix")
    # it points at the Install section rather than naming one command: that
    # section recommends the installer and warns against upgrading through
    # ``uv tool install aelix@latest`` up to beta.2 (#262)
    assert "](#install)" in line or "](#설치)" in line, line
    assert "aelix@latest" not in line, line
    project = _toml(REPO_ROOT / "pyproject.toml")["project"]
    assert "aelix" in project["scripts"], project
    depends = {re.split(r"[\s=<>!~\[;]", d, maxsplit=1)[0] for d in project["dependencies"]}
    assert {"aelix-ai", "aelix-agent-core", "aelix-coding-agent"} <= depends, depends
    assert (aelix.Agent, aelix.AgentHarness) == (Agent, AgentHarness)


# === 2. a term the conventions call a code object is one ======================


def _term_entries() -> list[str]:
    """The ``용어 규칙`` bullets, each with its indented continuation lines."""

    entries: list[str] = []
    for line in _section(_read("docs/00-conventions.md"), "## 용어 규칙").splitlines():
        if line.startswith("- "):
            entries.append(line[2:])
        elif line.startswith("  ") and entries:
            entries[-1] += " " + line.strip()
    return entries


def _resolve(dotted: str) -> object:
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj: object = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        return obj
    raise ModuleNotFoundError(dotted)


def test_every_code_term_names_an_import_path_that_resolves() -> None:
    """An entry that calls its term a code object ("코드상의") carries the import
    path of that object, the path imports, and its last component is the term.

    ``AgentRuntime: 코드상의 런타임 kernel 객체`` named no path, so it fails the
    first check; a path that stops importing fails the second.
    """

    code_terms = [entry for entry in _term_entries() if "코드상의" in entry]
    assert code_terms, "no 용어 규칙 entry names a code object"
    for entry in code_terms:
        term = re.match(r"`([^`]+)`", entry)
        assert term, f"term entry without a `term`: {entry!r}"
        paths = re.findall(r"`(aelix_[A-Za-z0-9_.]+)`", entry)
        assert paths, f"{term.group(1)!r} is called a code object but names no import path"
        for path in paths:
            obj = _resolve(path)
            assert path.rsplit(".", 1)[-1] == term.group(1), f"{path} is not {term.group(1)}"
            assert getattr(obj, "__name__", None) == term.group(1), f"{path} -> {obj!r}"


# === 3. the vision states ADR-0235 and #86, not the retired goal ==============

_VISION = "docs/01-product-vision.md"


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", _read(rel))


def test_the_vision_no_longer_sets_the_retired_parity_goal() -> None:
    """``docs/02-initial-requirements.md`` still says it; the owner kept it out of
    #266 (its rewrite belongs to the architecture-document track)."""

    assert "거의 완벽" not in _flat(_VISION)
    assert "ADR-0235" in _read(_VISION)


def test_the_vision_names_each_of_the_four_promises() -> None:
    """The owner's #86 note (2026-09-19): say where it runs, where the model
    traffic goes, where artifacts come from and how permissions are enforced,
    each on its own — never "tokens and source never leave your network"."""

    text = _flat(_VISION)
    for label in ("실행 위치", "모델 전송 경로", "artifact 출처", "권한 enforcement"):
        assert f"**{label}**" in text, label
    assert "#86" in text


@pytest.mark.parametrize("rel", [_VISION, "README.md", "README.ko.md"])
def test_no_document_promises_that_nothing_leaves_the_network(rel: str) -> None:
    text = _flat(rel).lower()
    for claim in ("never leave", "never leaves", "절대 벗어나", "절대 나가지"):
        assert claim not in text, f"{rel}: {claim!r}"
