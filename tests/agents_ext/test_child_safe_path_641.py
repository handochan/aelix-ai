"""#641: a delegated child does not put its cwd — the project — on ``sys.path``.

Both channels started every child as ``python -m aelix_coding_agent`` in the
project folder, and ``python -m`` puts the cwd at ``sys.path[0]``. A project
file named like a global pack's ``[plugin.entry] python`` module or an entry
point's module was imported in its place, whatever the project's trust; a
project ``aelix_coding_agent/`` directory would have replaced the whole child.
The launch prefix is now ``python -P -m`` (Python 3.11's "safe path" flag; 3.11
is the floor), as pip's has been since #394.

The real-child rows run CPython with the PRODUCTION prefix and the production
child environment, in a hostile cwd. The control row drops only ``-P`` and
shows the same cwd taking over the child, so the production rows are not
vacuous. The loader half (an in-process ``python -m`` ordering) is
``tests/extensions/test_project_module_imports_641.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_agents.print_channel import build_child_argv, build_child_env
from aelix_agents.rpc_channel import build_rpc_child_argv
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.config import VERSION
from aelix_coding_agent.rpc.rpc_client import RpcClient

from tests.env_sandbox import child_env
from tests.extensions.test_ep_manifest import install_dist, manifest_toml


def _profile() -> AgentProfile:
    return AgentProfile(
        name="scout",
        description="Reads things.",
        body="You are a scout.",
        file_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
    )


def _print_argv(cwd: Path) -> list[str]:
    return build_child_argv(
        _profile(),
        prompt_path=str(cwd / "prompt.md"),
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(cwd),
        parent_cwd=str(cwd),
    )


def _rpc_argv(cwd: Path) -> list[str]:
    return build_rpc_child_argv(
        _profile(),
        prompt_path=str(cwd / "prompt.md"),
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(cwd),
        parent_cwd=str(cwd),
    )


@pytest.mark.parametrize("build", [_print_argv, _rpc_argv], ids=["print", "rpc"])
def test_both_channels_launch_the_child_with_safe_path(tmp_path: Path, build) -> None:
    assert build(tmp_path)[:4] == [sys.executable, "-P", "-m", "aelix_coding_agent"]


def test_the_rpc_client_default_launch_has_safe_path_too() -> None:
    """The library default (``-m aelix``, the umbrella) spawns in a caller's cwd."""

    assert RpcClient()._build_argv()[:4] == [sys.executable, "-P", "-m", "aelix"]


def _shadow_package(cwd: Path, marker: Path) -> None:
    """A project directory that holds its own ``aelix_coding_agent`` package."""

    pkg = cwd / "aelix_coding_agent"
    pkg.mkdir(parents=True)
    body = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    (pkg / "__main__.py").write_text(body, encoding="utf-8")


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    # Bytes, not ``text=True``: the Windows leg decodes cp1252 there.
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=180
    )


@pytest.mark.parametrize("build", [_print_argv, _rpc_argv], ids=["print", "rpc"])
def test_a_project_aelix_coding_agent_package_does_not_replace_the_child(
    tmp_path: Path, build
) -> None:
    cwd = tmp_path / "proj"
    marker = tmp_path / "shadow-ran"
    _shadow_package(cwd, marker)
    env = build_child_env(_profile(), base=child_env(tmp_path / "home", PI_OFFLINE="1"))

    result = _run([*build(cwd)[:4], "--version"], cwd, env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8", "replace").strip() == VERSION
    assert not marker.exists()


def test_control_without_safe_path_the_project_package_runs(tmp_path: Path) -> None:
    """Drop ONLY ``-P`` from the production prefix: the cwd's package is what runs."""

    cwd = tmp_path / "proj"
    marker = tmp_path / "shadow-ran"
    _shadow_package(cwd, marker)
    env = build_child_env(_profile(), base=child_env(tmp_path / "home", PI_OFFLINE="1"))
    prefix = [token for token in _print_argv(cwd)[:4] if token != "-P"]

    _run([*prefix, "--version"], cwd, env)

    assert marker.exists()


def _source_root() -> str:
    import aelix_coding_agent

    return str(Path(aelix_coding_agent.__file__).resolve().parents[1])


@pytest.mark.parametrize(
    "spelled",
    [
        pytest.param("{root}{sep}", id="root-then-empty"),
        pytest.param("{sep}{root}", id="empty-then-root"),
        pytest.param("{root}{sep}{sep}{other}", id="empty-in-the-middle"),
        pytest.param("{other}{sep}", id="root-not-listed"),
        pytest.param("{sep}", id="only-empty"),
    ],
)
def test_the_child_pythonpath_has_no_empty_segment(tmp_path: Path, spelled: str) -> None:
    """An empty segment is the cwd — the project — on ``sys.path``, ``-P`` or not.

    The root was prepended only when it was missing, so a ``PYTHONPATH`` that
    already listed it kept its empty segment (#641 verify V4).
    """

    root = _source_root()
    other = str(tmp_path / "other")
    base = {"PYTHONPATH": spelled.format(root=root, sep=os.pathsep, other=other)}

    parts = build_child_env(_profile(), base=base)["PYTHONPATH"].split(os.pathsep)

    assert "" not in parts
    assert parts[0] == root
    assert parts.count(root) == 1


def test_a_project_sitecustomize_does_not_run_in_the_child(tmp_path: Path) -> None:
    """The V4 reproduction as a real child: ``PYTHONPATH='<root>:'`` in a hostile cwd."""

    cwd = tmp_path / "proj"
    cwd.mkdir()
    marker = tmp_path / "sitecustomize-ran"
    (cwd / "sitecustomize.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    base = child_env(tmp_path / "home", PI_OFFLINE="1")
    base["PYTHONPATH"] = _source_root() + os.pathsep
    env = build_child_env(_profile(), base=base)

    result = _run([*_print_argv(cwd)[:4], "--version"], cwd, env)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


_RECORDER = """\
import json, os, pathlib, sys
with open({log!r}, "a", encoding="utf-8") as _f:
    _f.write(json.dumps({{"origin": {origin!r}, "module": __name__,
                          "safe_path": sys.flags.safe_path}}) + "\\n")


def setup(aelix):
    return None
"""


def _user_site(env: dict[str, str]) -> Path:
    """The child's ``pip install --user`` directory under a scratch PYTHONUSERBASE."""

    out = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
        env=env,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return Path(out.stdout.decode("utf-8").strip())


def test_a_delegated_child_imports_the_installed_extension_modules(tmp_path: Path) -> None:
    """The #641 reproduction, as a real child: a global manifest pack and an entry point.

    The pack's ``[plugin.entry] python`` and the endpoint's module are installed
    in the user site (the #91 provenance gate trusts it), and the project
    folder carries same-named files that record themselves. The child is the
    production prefix plus a ``-p`` run with no credential: extensions load
    before the missing key ends the run, which is all this needs.
    """

    home = tmp_path / "home"
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "proj"
    log = tmp_path / "imports.jsonl"
    cwd.mkdir()
    base = child_env(
        home,
        PI_OFFLINE="1",
        AELIX_CODING_AGENT_DIR=str(agent_dir),
        PYTHONUSERBASE=str(tmp_path / "userbase"),
    )
    site = _user_site(base)
    base["PYTHONPATH"] = str(site)
    for module in ("aelix_t641_voter", "aelix_t641_ep"):
        (cwd / f"{module}.py").write_text(
            _RECORDER.format(log=str(log), origin="project"), encoding="utf-8"
        )
    site.mkdir(parents=True)
    (site / "aelix_t641_voter.py").write_text(
        _RECORDER.format(log=str(log), origin="installed"), encoding="utf-8"
    )
    install_dist(
        site,
        "aelix_t641_ep",
        entry_points={"aelix.extensions": {"t641": "aelix_t641_ep:setup"}},
        files={"aelix_t641_ep.py": _RECORDER.format(log=str(log), origin="installed")},
    )
    pack = agent_dir / "extensions" / "voter"
    pack.mkdir(parents=True)
    (pack / "aelix-plugin.toml").write_text(
        manifest_toml("voter", entry="aelix_t641_voter:setup"), encoding="utf-8"
    )
    env = build_child_env(_profile(), base=base)
    argv = [
        *_print_argv(cwd)[:4],
        "--no-session",
        "--provider",
        "openrouter",
        "--model",
        "anthropic/claude-haiku-4.5",
        "--mode",
        "json",
        "-p",
        "hi",
    ]

    result = _run(argv, cwd, env)

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert sorted((r["module"], r["origin"]) for r in records) == [
        ("aelix_t641_ep", "installed"),
        ("aelix_t641_voter", "installed"),
    ], result.stderr
    assert all(r["safe_path"] for r in records)
