"""``aelix status`` — the session-free report, and the three ways it could lie (#101).

WHAT IS ACTUALLY DRIVEN. Every behavioural test below calls the shipped
``run_status_command`` against a real temporary project with real extension
files on disk, and lets it run the real ``resolve_project_trusted`` and the real
``discover_and_load_extensions``. Nothing here stubs the trust store or the
loader: the command's entire value is that it reports what a launch WOULD do, so
a double that answered for either would test the double.

THE THREE FAILURE MODES THIS FILE EXISTS FOR, each with its own test:

1. **Reporting a permission the next launch will not grant.** The command runs
   with ``has_ui=False``, so an undecided directory must DENY, silently, and the
   project-tier extension must not load.
2. **Reporting "none" where the honest answer is "we did not look".** ``mode``,
   ``active_tools`` and ``all_tools`` have no session-free answer. Emitting them
   as ``[]`` is #120 in miniature, so they are absent from the JSON and named in
   ``session_only``.
3. **Dying on a headless install.** The verb must import no ``rich`` and no
   ``prompt_toolkit`` — the ``[tui]`` extra is optional. This one is measured in
   a SUBPROCESS, because by the time the suite reaches this file the test
   session has already imported both and an in-process check would pass over a
   command that cannot run.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.cli.status import SESSION_ONLY, _collect, run_status_command
from aelix_coding_agent.extensions.always_on import BUILTIN_ALWAYS_ON_NAMES

_TRIVIAL_EXTENSION = textwrap.dedent(
    """
    from aelix_coding_agent.extensions.api import ExtensionAPI


    def setup(aelix: ExtensionAPI) -> None:
        pass
    """
).lstrip()


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A cwd with a project-tier extension and an agent dir with a global one."""

    project = tmp_path / "proj"
    (project / ".aelix" / "extensions").mkdir(parents=True)
    (project / ".aelix" / "extensions" / "pext.py").write_text(_TRIVIAL_EXTENSION)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "extensions" / "gext.py").write_text(_TRIVIAL_EXTENSION)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.chdir(project)
    return project, agent_dir


def _trust(project: Path, agent_dir: Path) -> None:
    from aelix_coding_agent.cli.project_trust import ProjectTrustStore

    ProjectTrustStore(str(agent_dir)).set(project, True)


def _names(report: dict) -> list[str]:
    return [e["name"] for e in (report["discovered_extensions"] or [])]


# --- (1) the trust decision ---------------------------------------------------


async def test_an_undecided_project_is_denied_and_nothing_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No trust record, a real ``.aelix/extensions/`` present, no UI.

    Both halves matter. ``project_trusted`` must be ``False`` — a status command
    that reported ``True`` here would be describing a launch that will not
    happen. And the project extension must be ABSENT from the list, because the
    decision has to reach discovery, not merely the display.

    "Nothing is asked" is observable rather than asserted directly: this test
    runs with no stdin and no prompt callback, so a command that tried to ask
    would hang or raise instead of returning a report.
    """

    _project(tmp_path, monkeypatch)
    report = await _collect(discover=True)

    assert report["project_trusted"] is False
    assert "pext.py" not in _names(report)
    assert "gext.py" in _names(report), "the global tier is not gated by project trust"


def test_the_trust_call_is_non_interactive_in_both_ways() -> None:
    """Pinned structurally, because behaviour cannot see it.

    ``resolve_project_trusted`` denies on ``if not has_ui or prompt is None``.
    With no ``prompt=`` passed, ``has_ui`` is therefore INERT — measured: with
    ``has_ui=False`` flipped to ``True`` in ``status.py``, all 13 behavioural
    tests in this file passed. A future edit that adds a ``prompt=`` would then
    turn a scripted ``aelix status`` into something that blocks on a dialog, and
    nothing would have failed.

    So both halves are asserted at the call site: ``has_ui`` must be the literal
    ``False``, and ``prompt`` must not be passed at all.
    """

    import aelix_coding_agent.cli.status as status_mod

    tree = ast.parse(Path(status_mod.__file__).read_text(encoding="utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "resolve_project_trusted"
    ]
    assert len(calls) == 1, f"expected exactly one trust resolution, found {len(calls)}"
    kwargs = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kwargs.get("has_ui") == "False", kwargs
    assert "prompt" not in kwargs, "aelix status must never be able to ask"
    assert kwargs.get("override") == "None", kwargs


async def test_a_trusted_project_loads_its_own_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same gate, so the previous test cannot pass by
    reporting an empty list for every directory."""

    project, agent_dir = _project(tmp_path, monkeypatch)
    _trust(project, agent_dir)

    report = await _collect(discover=True)

    assert report["project_trusted"] is True
    assert "pext.py" in _names(report)
    scopes = {e["name"]: e["scope"] for e in report["discovered_extensions"]}
    assert scopes["pext.py"] == "project"
    assert scopes["gext.py"] == "global"


# --- (2) absent, not empty ----------------------------------------------------


async def test_the_session_only_fields_are_absent_rather_than_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honesty property, and the reason this is not a ``RuntimeSnapshot``.

    An ``active_tools: []`` in the JSON does not read as "unknown", it reads as
    "this session has no tools" — the #120 defect, exported to a script. The
    keys must be MISSING, and ``session_only`` must name them so a consumer can
    tell "not looked at" from "looked at and found nothing".
    """

    _project(tmp_path, monkeypatch)
    report = await _collect(discover=True)

    for field in SESSION_ONLY:
        assert field not in report, f"{field} must be absent, not empty"
    assert report["session_only"] == list(SESSION_ONLY)
    assert set(SESSION_ONLY) == {"mode", "active_tools", "all_tools"}


async def test_no_extensions_distinguishes_not_looked_from_found_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``None`` for "did not look", ``[]`` for "looked, found nothing".

    Same distinction as above one level down. A ``--no-extensions`` run that
    reported ``[]`` would tell a reader their extensions are gone.
    """

    project, agent_dir = _project(tmp_path, monkeypatch)
    _trust(project, agent_dir)

    skipped = await _collect(discover=False)
    assert skipped["discovered_extensions"] is None

    empty_dir = tmp_path / "bare"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "no-agent-dir"))
    looked = await _collect(discover=True)
    assert looked["discovered_extensions"] == []


async def test_no_extensions_imports_no_extension_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-extensions`` is a promise about execution, not about display.

    Measured with a side effect the loader cannot avoid triggering: the module
    writes a file at IMPORT time. If discovery ran, the sentinel exists.
    """

    project = tmp_path / "proj"
    (project / ".aelix" / "extensions").mkdir(parents=True)
    sentinel = tmp_path / "imported.marker"
    (project / ".aelix" / "extensions" / "loud.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            Path({str(sentinel)!r}).write_text("imported")


            def setup(aelix) -> None:
                pass
            """
        ).lstrip()
    )
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.chdir(project)
    _trust(project, agent_dir)

    await _collect(discover=False)
    assert not sentinel.exists(), "--no-extensions still imported the extension"

    await _collect(discover=True)
    assert sentinel.exists(), "without the flag the loader must really load it"


async def test_a_load_error_is_reported_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken extension is the main thing someone runs this command to find.

    And its message is author-controlled text, so it goes through the same cap
    as every other emitted string — #101's M2 measured a 4138-character
    ``plugin.version`` reaching a report unbounded.
    """

    from aelix_status.snapshot import MAX_EMITTED_CHARS

    project = tmp_path / "proj"
    (project / ".aelix" / "extensions").mkdir(parents=True)
    (project / ".aelix" / "extensions" / "broken.py").write_text(
        f'raise RuntimeError("{"X" * 4138}")\n'
    )
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.chdir(project)
    _trust(project, agent_dir)

    report = await _collect(discover=True)

    assert report["extension_errors"], "a raising extension must be reported"
    for err in report["extension_errors"]:
        assert len(err) <= MAX_EMITTED_CHARS, len(err)


# --- the command surface ------------------------------------------------------


async def test_a_usage_error_exits_2_and_says_nothing_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``aelix status --json | jq`` must never receive an error message.

    Exit 2 is ``docs._EXIT_USAGE`` / ``extension_install._EXIT_DIDNT_RUN``: we
    did not do the thing you asked.
    """

    _project(tmp_path, monkeypatch)
    assert await run_status_command(["--bogus"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--bogus" in captured.err


async def test_help_exits_0_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, monkeypatch)
    assert await run_status_command(["--help"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("usage: aelix status")
    assert captured.err == ""


async def test_json_is_the_only_thing_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, monkeypatch)
    assert await run_status_command(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cwd"] == str(tmp_path / "proj")


# --- wiring, pinned structurally ----------------------------------------------


def _entry_tree() -> ast.Module:
    import aelix_coding_agent.cli.entry as entry

    return ast.parse(Path(entry.__file__).read_text(encoding="utf-8"))


def test_the_verb_is_dispatched_before_the_flag_parser() -> None:
    """Placement is the whole wiring, and a substring search cannot see it.

    ``parse_args`` is a FLAT parser: reached with ``["status"]`` it treats the
    word as the first chat prompt and launches a session. So the dispatch has to
    sit ahead of it, exactly like ``docs`` and ``extension``. This asserts on
    statement ORDER inside ``_async_main``, which is the property that breaks.
    """

    tree = _entry_tree()
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_main"
    )

    dispatch_at: int | None = None
    parse_at: int | None = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and ast.unparse(node) == "argv[0] == 'status'":
            dispatch_at = node.lineno
        if isinstance(node, ast.Call) and ast.unparse(node) == "parse_args(argv)":
            parse_at = node.lineno

    assert dispatch_at is not None, "no `argv[0] == 'status'` dispatch in _async_main"
    assert parse_at is not None, "parse_args(argv) moved or was renamed"
    assert dispatch_at < parse_at, (
        f"the status dispatch (line {dispatch_at}) is BELOW parse_args "
        f"(line {parse_at}); the flat parser will swallow the verb"
    )


def test_the_dispatch_awaits_the_real_command() -> None:
    """A dispatch that forgot the ``await`` returns a coroutine as an exit code,
    which ``sys.exit`` renders as 1 with a RuntimeWarning and no report."""

    tree = _entry_tree()
    calls = {
        ast.unparse(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.Await)
    }
    assert "await run_status_command(argv[1:])" in calls


def test_the_always_on_names_are_the_ones_entry_py_actually_prepends() -> None:
    """The report names three built-ins as "loaded either way". Check that
    against ``entry.py``, not against the constant the report read.

    Asserting ``report["always_on_builtins"] == sorted(BUILTIN_ALWAYS_ON_NAMES)``
    would be a tautology — both sides are the same object. The claim that can
    actually go stale is the one about ``entry.py``: a built-in added to or
    dropped from ``prepend_extensions`` leaves the list wrong. So the check is
    that every name in the constant is a class ``entry.py`` constructs, and that
    no UNCONDITIONALLY appended construction is missing from it.
    """

    tree = _entry_tree()
    constructed = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    missing = sorted(BUILTIN_ALWAYS_ON_NAMES - constructed)
    assert not missing, f"named as always-on but never constructed in entry.py: {missing}"


# --- (3) it has to run where there is no TUI ----------------------------------


def test_the_report_runs_with_no_rich_and_no_prompt_toolkit(tmp_path: Path) -> None:
    """The ``[tui]`` extra is OPTIONAL, so an import of it here is not a cost —
    it is an ImportError on a headless install and the command dies.

    A SUBPROCESS, deliberately. By the time pytest reaches this file both
    packages are already in ``sys.modules``, so the in-process version of this
    check passes over a command that cannot run. This one measures a fresh
    interpreter that has done nothing but execute the verb.

    Regression guard with a real history: the first revision imported
    ``BUILTIN_ALWAYS_ON_NAMES`` from ``tui/extension_manager``, which pulled
    166 modules including both.
    """

    probe = textwrap.dedent(
        """
        import asyncio, sys
        from aelix_coding_agent.cli.status import run_status_command
        asyncio.run(run_status_command(["--json"]))
        heavy = sorted({m.split(".")[0] for m in sys.modules
                        if m.split(".")[0] in ("rich", "prompt_toolkit")})
        print("HEAVY=" + ",".join(heavy), file=sys.stderr)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={**_child_env(), "AELIX_CODING_AGENT_DIR": str(tmp_path / "agent")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "HEAVY=\n" in proc.stderr or proc.stderr.rstrip().endswith("HEAVY="), proc.stderr


def _child_env() -> dict[str, str]:
    """The parent env plus a PYTHONPATH that reaches the worktree's sources.

    An installed aelix would be importable without this; a worktree run under
    ``PYTHONPATH`` would not, and the subprocess must load THIS tree rather than
    whatever is in site-packages.
    """

    import os

    import aelix_agent_core
    import aelix_coding_agent
    import aelix_status

    roots = {
        str(Path(m.__file__).resolve().parent.parent)
        for m in (aelix_coding_agent, aelix_agent_core, aelix_status)
        if m.__file__
    }
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(sorted(roots) + ([existing] if existing else []))
    return env
