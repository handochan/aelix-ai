"""Sprint 6h₁₁ — coding-agent system prompt + AGENTS.md context + tool wiring.

Covers the fix for the "bare chat model" gap: the interactive/print/rpc harness
now ships the 7 coding tools + a real coding-agent system prompt, and
auto-discovers AGENTS.md project context.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli import agent_context as _agent_context
from aelix_coding_agent.cli.agent_context import (
    build_system_prompt,
    discover_context_files,
)
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options

_TOOL_NAMES = {"read", "bash", "edit", "write", "grep", "find", "ls"}


# --- system prompt -----------------------------------------------------------


def test_build_system_prompt_has_identity_and_tools() -> None:
    prompt = build_system_prompt(".")
    assert "Aelix" in prompt  # identity (was empty → generic chatbot)
    assert "coding agent" in prompt.lower()
    for tool in ("read", "write", "edit", "bash", "grep", "find", "ls"):
        assert tool in prompt  # the toolset is described


def test_build_system_prompt_includes_environment(tmp_path) -> None:
    prompt = build_system_prompt(str(tmp_path))
    assert str(tmp_path) in prompt  # absolute cwd surfaced
    assert "Working directory" in prompt


def test_build_system_prompt_has_convergence_guidance() -> None:
    """Weak models loop on vague requests without explicit stop-when-done /
    no-repeat guidance (pi's default prompt lacks it; authored here)."""
    prompt = build_system_prompt(".")
    assert "STOP calling tools" in prompt
    assert "same tool with the same arguments twice" in prompt
    assert "ambiguous" in prompt


# --- self-extension signpost (issue #117) ------------------------------------
#
# Measured against a freshly built 0.1.0b1 wheel driven by a real model in an
# empty directory: asked to add a tool, the agent failed 2/2 and failed
# confidently — it invented a `tools_definition.json` manifest that exists
# nowhere in Aelix and announced "the tool is now ready to use". The complete
# system prompt it received was 1847 chars in which "extension" appeared ZERO
# times. With this block injected, the same model wrote a loadable
# `.aelix/extensions/describe_dataset.py` (errors=[], tools=[...]). These tests
# pin the five facts that made the difference.


def test_prompt_teaches_the_setup_aelix_contract() -> None:
    """(a) the contract is `def setup(aelix)`, in-process, no plugin/build step."""

    prompt = build_system_prompt(".")
    assert "Extending yourself" in prompt
    assert "def setup(aelix)" in prompt
    assert "register_tool" in prompt
    assert "IN THIS PROCESS" in prompt
    # The measured failure mode was inventing a manifest file format.
    assert "no manifest" in prompt
    assert "Never invent a config format" in prompt


def test_prompt_gives_absolute_project_and_global_write_targets(tmp_path) -> None:
    """(b) both write targets are absolute and are the dirs the loader scans."""
    from aelix_coding_agent.cli.config import get_agent_dir

    prompt = build_system_prompt(str(tmp_path))
    project_target = os.path.join(str(tmp_path), ".aelix", "extensions", "<name>.py")
    global_target = os.path.join(get_agent_dir(), "extensions", "<name>.py")
    assert project_target in prompt
    assert global_target in prompt
    assert os.path.isabs(project_target) and os.path.isabs(global_target)


async def test_prompt_lists_the_global_target_first_and_labels_the_trust_gate(
    tmp_path, monkeypatch
) -> None:
    """(b) ordering + labels must match what the REAL loader does.

    The project-local tier is trust-gated and fails SILENTLY, which is exactly
    the confident-failure mode #117 exists to kill. This test writes one
    extension to each of the two paths the prompt actually emits and runs the
    shipped loader over them in both trust states.
    """

    from aelix_coding_agent.cli.config import get_agent_dir
    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    agent_dir = tmp_path / "agentdir"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    project = tmp_path / "proj"
    project.mkdir()

    prompt = build_system_prompt(str(project))
    global_target = os.path.join(get_agent_dir(), "extensions", "<name>.py")
    project_target = os.path.join(str(project), ".aelix", "extensions", "<name>.py")

    # (1) GLOBAL FIRST: the target that is not trust-gated is the one the model
    # reads first. Ordering is the recommendation.
    assert prompt.index(global_target) < prompt.index(project_target)

    # (2) The project-local line names its CONDITION and its failure mode; the
    # global line does not claim a guarantee ("always") that `--no-extensions`
    # would falsify.
    project_line = next(
        line for line in prompt.splitlines() if project_target in line
    )
    global_line = next(line for line in prompt.splitlines() if global_target in line)
    assert "only if the project is trusted" in project_line
    assert "skipped silently" in project_line
    assert "no trust gate" in global_line
    assert "always" not in global_line

    # (3) The behaviour those labels describe, measured on the emitted paths.
    ext_body = 'def setup(aelix):\n    aelix.register_flag("f", type="bool", default=True)\n'
    for target in (global_target, project_target):
        path = Path(target.replace("<name>.py", "probe.py"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ext_body, encoding="utf-8")

    trusted = await discover_and_load_extensions(
        [], cwd=project, agent_dir=agent_dir, no_project_local=False
    )
    untrusted = await discover_and_load_extensions(
        [], cwd=project, agent_dir=agent_dir, no_project_local=True
    )

    assert len(trusted.extensions) == 2  # global + project-local
    assert trusted.errors == []
    # The project-local one is DROPPED, and — the whole point — with NO error
    # and NO warning. The global one survives, which is why it is listed first.
    assert len(untrusted.extensions) == 1
    assert untrusted.errors == []


def test_prompt_never_names_the_wrong_global_dir(tmp_path, monkeypatch) -> None:
    """The plausible-but-WRONG ``~/.aelix/extensions`` must never be emitted.

    ``extensions/loader.py:455-457`` scans ``get_agent_dir()/extensions`` and
    ``cli/entry.py:925`` passes ``agent_dir=Path(get_agent_dir())``, i.e. the
    real global dir is ``~/.aelix/agent/extensions``. A hardcoded
    ``~/.aelix/extensions`` would send every user's extension to a directory
    nothing ever reads.
    """

    monkeypatch.delenv("AELIX_CODING_AGENT_DIR", raising=False)
    prompt = build_system_prompt(str(tmp_path))
    wrong = os.path.join(str(Path.home()), ".aelix", "extensions")
    right = os.path.join(str(Path.home()), ".aelix", "agent", "extensions")
    assert wrong + os.sep + "<name>.py" not in prompt
    assert right in prompt


def test_prompt_honours_agent_dir_env_at_call_time(tmp_path, monkeypatch) -> None:
    """(b) ``$AELIX_CODING_AGENT_DIR`` is read per call, not frozen at import.

    The module is imported once per process but the env var is set by the user's
    shell (and by tests / subagents) at any time; a value captured at import
    would emit a stale path for the rest of the session.
    """

    before = build_system_prompt(str(tmp_path))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "elsewhere"))
    after = build_system_prompt(str(tmp_path))

    moved = os.path.join(str(tmp_path / "elsewhere"), "extensions", "<name>.py")
    assert moved in after
    assert moved not in before
    assert after != before


def test_prompt_pointer_paths_exist_on_disk() -> None:
    """(c) every path the block cites is really readable — no dead pointers.

    Both files ship inside the wheel (verified in the built
    ``aelix_coding_agent-0.1.0b1-py3-none-any.whl``:
    ``aelix_coding_agent/examples/echo/echo.py`` and
    ``aelix_coding_agent/extensions/api.py``), so an installed user can open
    them with the ``read`` tool.
    """

    prompt = build_system_prompt(".")
    cited = [
        line.split(": ", 1)[1].strip()
        for line in prompt.splitlines()
        if line.startswith("  - ") and ".py" in line and "<name>.py" not in line
    ]
    assert len(cited) == 2, cited
    for path in cited:
        assert os.path.isabs(path), path
        assert Path(path).is_file(), path
    assert any(p.endswith(os.path.join("examples", "echo", "echo.py")) for p in cited)
    assert any(p.endswith(os.path.join("extensions", "api.py")) for p in cited)


def test_prompt_pointer_paths_ship_in_the_built_wheel(tmp_path) -> None:
    """(c) the pointers must survive PACKAGING, not just exist in the checkout.

    ``_package_pointer`` resolves relative to the installed package, so both
    citations are true for a source checkout by construction — and every test
    above runs in a source checkout. That is precisely the blind spot: a future
    ``[tool.hatch.build]`` exclude (or a move of ``examples/`` out of the
    package) would keep every other test green while every INSTALLED user gets
    a prompt citing two files their ``read`` tool cannot open. That is the
    hallucinated-path failure #117 exists to prevent, re-created by packaging.

    The wheel is built for real (~2.5s with ``uv``) rather than asserted
    against ``[tool.hatch.build.targets.wheel] packages`` — the config lists
    two package ROOTS and says nothing about excludes, so reading it would
    re-state the assumption instead of testing it. The test SKIPS if no builder
    is on PATH so a minimal CI image cannot fail spuriously; if it skips
    everywhere, the packaging guarantee is unverified.
    """

    import shutil
    import subprocess
    import zipfile

    from aelix_coding_agent.cli.agent_context import _API_PARTS, _EXAMPLE_PARTS

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("no `uv` on PATH; cannot build a wheel to inspect")

    pkg_root = Path(_agent_context.__file__).resolve().parents[3]
    assert (pkg_root / "pyproject.toml").is_file(), pkg_root

    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=pkg_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stderr

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    for parts in (_EXAMPLE_PARTS, _API_PARTS):
        # The same parts tuple the prompt emits, as a wheel-internal path.
        arcname = "/".join(("aelix_coding_agent", *parts))
        assert arcname in names, f"{arcname} missing from {wheels[0].name}"


def test_prompt_omits_a_missing_pointer_instead_of_emitting_it_dead(
    monkeypatch,
) -> None:
    """(c) a renamed/absent shipped file drops its line; the block survives.

    A pointer to a path the reader cannot open is worse than no pointer — it
    reproduces exactly the hallucinated-path failure this block exists to stop.
    """

    monkeypatch.setattr(
        _agent_context, "_EXAMPLE_PARTS", ("examples", "echo", "gone.py")
    )
    prompt = build_system_prompt(".")

    assert "gone.py" not in prompt
    assert "worked example" not in prompt
    # The surviving pointer and the rest of the block are untouched.
    assert "Do not recall or import the API" in prompt
    assert "full API" in prompt
    assert "Extending yourself" in prompt


def test_prompt_omits_the_read_these_header_when_no_pointer_resolves(
    monkeypatch,
) -> None:
    """With both shipped files gone the header itself goes too (no empty list)."""

    monkeypatch.setattr(_agent_context, "_EXAMPLE_PARTS", ("nope_a.py",))
    monkeypatch.setattr(_agent_context, "_API_PARTS", ("nope_b.py",))
    prompt = build_system_prompt(".")

    assert "Do not recall or import the API" not in prompt
    assert "Extending yourself" in prompt  # the rest still ships
    assert "/reload" in prompt


def test_prompt_never_tells_the_model_to_import_aelix_in_bash() -> None:
    """(c) no ``python -c "import aelix_coding_agent"`` incantation.

    The block must hand the model absolute file paths for its ``read`` tool,
    never an import. This test also pins the REASON, because an earlier
    docstring here (and on ``_package_pointer``) gave a false one: it claimed
    the bash tool's ``PATH`` "is only ``<agent dir>/bin``". Re-derived below —
    ``get_shell_env`` PREPENDS to the inherited ``PATH`` (``shell_env.py:41-43``),
    so that reason is measurably wrong.

    The real reason is the INTERPRETER. ``install.sh:165`` installs with
    ``uv tool install``, which isolates Aelix in a per-tool virtualenv; only the
    ``aelix`` console script is on ``PATH`` and a bare ``python`` resolves to a
    different interpreter whose ``sys.path`` excludes that venv. Reproducing a
    real ``uv tool install`` in a test would be slow and network-shaped, so the
    half that is cheap and was actually WRONG is the half asserted here.
    """

    import os as _os

    from aelix_coding_agent.util.shell_env import get_shell_env

    # The falsified claim: PATH is NOT replaced, it is prepended to.
    inherited = _os.environ.get("PATH", "").split(_os.pathsep)
    entries = get_shell_env()["PATH"].split(_os.pathsep)
    assert len(entries) > 1
    assert all(e in entries for e in inherited if e), "inherited PATH was dropped"
    assert entries[1:] == [e for e in inherited if e] or len(entries) >= len(
        [e for e in inherited if e]
    )

    prompt = build_system_prompt(".")
    assert "python -c" not in prompt
    assert "import aelix_coding_agent" not in prompt
    assert "pip install" not in prompt


def test_prompt_says_reload_is_the_users_keystroke() -> None:
    """(d) the model must not claim it activated anything; /reload is human."""

    prompt = build_system_prompt(".")
    assert "/reload" in prompt
    assert "cannot load it yourself" in prompt
    assert "ask the user to run /reload" in prompt


def test_reload_instruction_is_mode_agnostic() -> None:
    """(d) ``/reload`` does not exist outside the interactive surfaces.

    It is parsed ONLY in the TUI (``tui/input.py:47-48``) and the basic REPL
    (``cli/repl.py:99``) — this test greps the tree for its dispatch sites so a
    third surface (or a removal) forces the wording to be revisited. Yet this
    block is emitted for ``--print`` / ``--mode json`` / ``--mode rpc`` and for
    delegated subagents, where "ask the user to run /reload" names a command
    the user has no way to type. The instruction must therefore carry its own
    fallback.
    """

    src = Path(_agent_context.__file__).resolve().parents[1]
    dispatch_sites = {
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if '== "/reload"' in p.read_text(encoding="utf-8")
        or '"/reload"' in p.read_text(encoding="utf-8")
        and "ParsedInput" in p.read_text(encoding="utf-8")
    }
    assert dispatch_sites == {
        os.path.join("tui", "input.py"),
        os.path.join("cli", "repl.py"),
    }, dispatch_sites

    prompt = build_system_prompt(".")
    assert "in an interactive session ask the user to run /reload" in prompt
    # The fallback is the one action that is correct on EVERY surface.
    assert "otherwise report the absolute path you wrote and stop" in prompt


def test_reload_instruction_admits_reload_may_not_re_discover(monkeypatch) -> None:
    """(d) MINOR 4 — ``/reload`` does not ALWAYS pick up a new extension file.

    ``tui/shell.py:2448`` gates the factory rebuild on
    ``_reload_rebuild_enabled()``. That is a documented, supported kill-switch:
    with ``AELIX_RELOAD_REBUILD`` set to a falsy value ``/reload`` routes to
    ``harness.reload_resources()``, which only re-emits a resources discover
    (``harness/core.py:2955-2962``) and never re-scans the extension
    directories — so the file the agent just wrote stays dormant while the
    agent reports success.

    Re-derived from the shipped gate rather than asserted as prose, so a change
    to the kill-switch's accepted values forces this wording to be revisited.
    """

    from aelix_coding_agent.tui.shell import _reload_rebuild_enabled

    monkeypatch.delenv("AELIX_RELOAD_REBUILD", raising=False)
    assert _reload_rebuild_enabled() is True  # default: /reload DOES re-discover

    # ...but every documented falsy value disables the rebuild.
    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("AELIX_RELOAD_REBUILD", falsy)
        assert _reload_rebuild_enabled() is False, falsy

    # Because that path exists, the instruction may not promise /reload works;
    # it must name the fallback that always does.
    prompt = build_system_prompt(".")
    reload_line = next(ln for ln in prompt.splitlines() if "/reload" in ln)
    assert "restart aelix if /reload does not pick it up" in reload_line


async def test_prompt_states_write_creates_dirs_instead_of_ordering_a_mkdir(
    tmp_path,
) -> None:
    """(b) "mkdir if missing" bought only a redundant bash call.

    ``tools/write.py:78-83`` mkdirs the parent (``parents=True,
    exist_ok=True``) before EVERY write, so an instruction to mkdir first
    induced a bash call whose work the next tool call redoes — and on the
    extension path that bash call is itself a second approval surface.
    Asserted against the tool's real behaviour, not its source text: a write
    through the SHIPPED ``write`` tool into a directory chain that does not
    exist must succeed on its own.
    """

    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.tools import create_write_tool

    prompt = build_system_prompt(".")
    assert "mkdir" not in prompt
    assert "write creates missing dirs" in prompt

    # Exactly the shape the block tells the model to write: a <name>.py three
    # levels below a directory that does not exist yet.
    tool = create_write_tool(str(tmp_path))
    nested = tmp_path / ".aelix" / "extensions" / "deep" / "probe.py"
    assert not nested.parent.exists()
    result = await tool.execute(
        {"path": str(nested), "content": "def setup(aelix):\n    pass\n"},
        ToolExecutionContext(tool_call_id="t1"),
    )
    assert result.is_error is False
    assert nested.read_text(encoding="utf-8") == "def setup(aelix):\n    pass\n"


def _emitted_api_hint() -> tuple[str, str]:
    """Recover the (shell command, bare regex) the block actually emits.

    Parsed out of the live prompt so the tests below can never assert about a
    hint the block no longer ships.
    """

    import re

    prompt = build_system_prompt(".")
    line = next(ln for ln in prompt.splitlines() if "full API" in ln)
    command = re.search(r"(grep[^']*'[^']+')", line).group(1)
    pattern = re.search(r"grep[^']*'([^']+)'", line).group(1)
    return command, pattern


def test_prompt_names_the_real_hook_call_and_a_grep_that_finds_it() -> None:
    """(c) "hooks" is not a method; the hook surface is ``aelix.on(...)``.

    The block previously said "(also register_command, register_flag, hooks)"
    and pointed the model at ``grep 'def register_'``. That grep cannot reveal
    the hook surface — every hit is a ``register_*`` method — so a model told
    hooks exist and handed a search that never finds them invents a name.
    """

    import re

    from aelix_coding_agent.cli.agent_context import _API_PARTS, _package_pointer

    api_src = Path(_package_pointer(*_API_PARTS)).read_text(encoding="utf-8")

    # The real call, verbatim, including the event name the block cites.
    assert re.search(r'def on\(\s*\n\s*self,\s*\n\s*event: Literal\["tool_call"\]', api_src)

    prompt = build_system_prompt(".")
    assert '`aelix.on("tool_call", handler)` for hooks' in prompt

    _command, pattern = _emitted_api_hint()
    hit_lines = [line for line in api_src.splitlines() if re.search(pattern, line)]

    # The hint must find BOTH families. Asserting only "more hits than the old
    # hint" is not enough: the natural widening ``def (register_|on)\(`` binds
    # the escaped paren to the whole alternation, so it matches 38 ``def on(``
    # lines and ZERO ``register_*`` — strictly more hits than the old hint's 10
    # while hiding ``register_tool``, the call this block is mostly about. That
    # near-miss shipped once and this assertion is what catches it.
    registers = [line for line in hit_lines if "def register_" in line]
    hooks = [line for line in hit_lines if line.strip().startswith("def on(")]
    assert len(registers) == len(re.findall(r"def register_", api_src)) > 0
    assert len(hooks) == len(re.findall(r"def on\(", api_src)) > 0
    # ...and the specific names the model needs are among them.
    assert any("def register_tool(" in line for line in registers)

    # The OLD hint provably could not reach the hook surface at all.
    assert not any(
        line.strip().startswith("def on(")
        for line in api_src.splitlines()
        if re.search(r"def register_", line)
    )


async def test_emitted_api_grep_command_actually_RUNS_in_every_surface() -> None:
    """The emitted command must WORK when executed, not merely look plausible.

    A truth audit that ran each emitted instruction through Aelix's own tools
    found this one dead. The block shipped ``grep 'def (register_|on\\()'``;
    the pattern needs alternation, so it is an ERE, but plain ``grep`` is BRE
    where ``(`` is literal and ``\\(`` OPENS a group. Executed through the real
    bash tool that command printed ``grep: Unmatched ( or \\(`` and exited 2.

    A system prompt that tells the agent to run a command that errors is worse
    than saying nothing: the model burns a turn and may conclude the feature is
    broken. So this test does not string-match the hint — it RUNS it, in all
    three engines a model can actually reach:

      (a) real ``/bin/grep`` via the shipped bash tool,
      (b) ripgrep via the shipped grep tool (``tools/grep.py:219``),
      (c) the grep tool's Python ``re`` fallback (``tools/grep.py:272-274``).

    All three must agree, exit clean, and surface ``register_tool`` (the call
    the block is mostly about) AND the ``def on(`` hook surface.
    """

    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.cli.agent_context import _API_PARTS, _package_pointer
    from aelix_coding_agent.tools import create_bash_tool, create_grep_tool
    from aelix_coding_agent.tools.grep import _python_grep

    api = _package_pointer(*_API_PARTS)
    command, pattern = _emitted_api_hint()

    def _text(result) -> str:
        return "".join(c.text for c in result.content if hasattr(c, "text"))

    # (a) THE BASH TOOL — the surface the emitted command is literally written
    # for. Exit status is asserted, because the BRE failure was a non-zero exit
    # with an error message, not a silent empty result.
    bash_tool = create_bash_tool(str(Path(api).parent))
    bash_out = _text(
        await bash_tool.execute(
            {"command": f"{command} {api}; echo RC=$?"}, ToolExecutionContext()
        )
    )
    assert "RC=0" in bash_out, bash_out
    assert "Unmatched" not in bash_out, bash_out
    bash_hits = [ln for ln in bash_out.splitlines() if "def " in ln]

    # THE ``-n`` IS PART OF THE CONTRACT (round-3 audit MAJOR 1). The clause that
    # follows the command in the block is "then read at the line it reports", so
    # every hit must actually carry a line number. Without ``-n`` bash grep emits
    # bare source lines — 38 of them the identical string ``    def on(`` — and
    # the instruction dead-ends. Asserted on real output, not on the flag string.
    assert bash_hits, bash_out
    assert all(re.match(r"^\d+:", ln) for ln in bash_hits), bash_hits[:5]

    # (b) THE GREP TOOL (ripgrep). It takes a bare pattern and no flags, which
    # is why the ``-E`` sits OUTSIDE the quotes in the emitted command — the
    # quoted pattern has to stay copy-pastable into this argument verbatim.
    grep_tool = create_grep_tool(str(Path(api).parent))
    rg_out = _text(
        await grep_tool.execute(
            {"pattern": pattern, "path": api, "limit": 200}, ToolExecutionContext()
        )
    )
    rg_hits = [ln for ln in rg_out.splitlines() if "def " in ln]

    # (c) THE PYTHON FALLBACK, used when ripgrep cannot be resolved.
    py_out, _limited, _trimmed = _python_grep(
        pattern,
        api,
        glob="**/*",
        ignore_case=False,
        literal=False,
        context=0,
        limit=200,
        is_directory=False,
    )
    py_hits = [ln for ln in py_out.splitlines() if "def " in ln]

    # The three engines must not disagree — a hint that works in the tool but
    # not in bash (or vice versa) is exactly the defect this test exists for.
    assert len(bash_hits) == len(rg_hits) == len(py_hits) > 0, (
        len(bash_hits),
        len(rg_hits),
        len(py_hits),
    )

    for engine, hits in (("bash", bash_hits), ("rg", rg_hits), ("python", py_hits)):
        registers = [h for h in hits if "def register_" in h]
        hooks = [h for h in hits if "def on(" in h]
        # BOTH families, in every engine. The trap pattern ``def (register_|on)\(``
        # passes a "more hits than before" check while scoring ZERO here.
        assert registers, f"{engine}: no register_* hits"
        assert hooks, f"{engine}: no hook (def on() hits"
        assert any("def register_tool(" in h for h in registers), engine


async def test_the_shipped_BRE_form_of_the_hint_would_have_failed() -> None:
    """Pin the actual defect so the ``-E`` cannot be quietly dropped again.

    Re-derives, through the real bash tool, that the same pattern WITHOUT
    ``-E`` is a hard error — so if someone "simplifies" the emitted command by
    removing the flag, this test fails loudly instead of shipping a dead
    instruction to every user on every turn.
    """

    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.cli.agent_context import _API_PARTS, _package_pointer
    from aelix_coding_agent.tools import create_bash_tool

    api = _package_pointer(*_API_PARTS)
    command, pattern = _emitted_api_hint()

    # The emitted command must carry an explicit ERE flag. Match the FLAG, not
    # the substring "-E": the command also carries ``-n`` (round-3 audit), and
    # short flags bundle, so the shipped form is ``-nE``. A naive
    # ``"-E" in command`` check fails on ``-nE`` even though it is correct ERE.
    assert re.search(r"(?:^|\s)-[a-zA-Z]*E\b", command) or "egrep" in command, command

    bash_tool = create_bash_tool(str(Path(api).parent))
    result = await bash_tool.execute(
        {"command": f"grep '{pattern}' {api}; echo RC=$?"}, ToolExecutionContext()
    )
    bre_out = "".join(c.text for c in result.content if hasattr(c, "text"))

    # BRE cannot parse it: unbalanced ``\(``, non-zero exit, zero hits.
    assert "RC=0" not in bre_out, bre_out
    assert "def register_tool(" not in bre_out


async def test_api_pointer_does_not_tell_the_model_to_read_an_untruncatable_file() -> None:
    """The block must not order a read that returns a confidently wrong window.

    Truth audit MAJOR 2. The block used to list api.py under "READ these
    first". Executed through the real ``read`` tool that is a dead
    instruction: api.py is ~2130 lines / 84KB and ``read`` truncates at
    ``DEFAULT_MAX_BYTES`` = 50KB (``tools/_truncate.py:12``), so the model gets
    lines 1-1183 — a window containing NONE of ``register_tool`` /
    ``register_command`` / ``register_flag`` (all past :1655). It does not
    error; it returns a plausible prefix, which is the exact confident-failure
    mode this whole block exists to kill.

    This test re-derives that truncation instead of hard-coding line numbers,
    then proves the instruction the block ACTUALLY ships delivers the goods.
    """

    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.cli.agent_context import _API_PARTS, _package_pointer
    from aelix_coding_agent.tools import create_read_tool
    from aelix_coding_agent.tools._truncate import DEFAULT_MAX_BYTES

    api = _package_pointer(*_API_PARTS)
    read_tool = create_read_tool(str(Path(api).parent))

    def _text(result) -> str:
        return "".join(c.text for c in result.content if hasattr(c, "text"))

    # (1) The file really is past the cap — the premise, measured.
    assert Path(api).stat().st_size > DEFAULT_MAX_BYTES

    # (2) A plain read is genuinely insufficient: it truncates and drops the
    # methods the block names. If api.py ever shrinks below the cap this
    # assertion fails and the instruction can be simplified back to a read.
    plain = _text(await read_tool.execute({"path": api}, ToolExecutionContext()))
    assert "limit)" in plain and "Use offset=" in plain, "expected a truncation notice"
    assert "def register_tool(" not in plain

    # (3) So the block must NOT put api.py under a read-it instruction.
    prompt = build_system_prompt(".")
    api_line = next(ln for ln in prompt.splitlines() if "full API" in ln)
    assert "too big to read whole" in api_line

    # (4) And the instruction it DOES give must work. The block says: grep it,
    # then read at the line it reports. Drive exactly that, taking the line
    # number from grep at runtime — never a baked-in offset, which would rot
    # silently on the next edit to api.py.
    _command, pattern = _emitted_api_hint()
    import re as _re

    src_lines = Path(api).read_text(encoding="utf-8").splitlines()
    reported = next(
        i + 1
        for i, ln in enumerate(src_lines)
        if _re.search(pattern, ln) and "def register_tool(" in ln
    )
    window = _text(
        await read_tool.execute(
            {"path": api, "offset": reported, "limit": 60}, ToolExecutionContext()
        )
    )
    # The signature the model came for actually arrives this time.
    assert "def register_tool(self, tool: AgentTool) -> None:" in window
    assert "def register_flag(" in window


def test_worked_example_is_small_enough_to_read_whole_and_is_self_consistent() -> None:
    """The one file the block says to READ must survive being read — and be coherent.

    Truth audit MINOR 8: echo.py's module docstring said a ``setup(aelix)``
    factory "can be added here" while ``def setup(aelix)`` was already defined
    45 lines below. The first file every agent reads contradicted itself about
    the single contract the block is teaching.
    """

    from aelix_coding_agent.cli.agent_context import _EXAMPLE_PARTS, _package_pointer
    from aelix_coding_agent.tools._truncate import DEFAULT_MAX_BYTES

    example = Path(_package_pointer(*_EXAMPLE_PARTS))
    source = example.read_text(encoding="utf-8")

    # Small enough that "read this one" is a promise the read tool can keep.
    assert example.stat().st_size < DEFAULT_MAX_BYTES

    # The contract the block teaches is really demonstrated here.
    assert "def setup(aelix" in source
    assert "aelix.register_tool(" in source

    # ...and the docstring does not describe it as hypothetical.
    docstring = source.split('"""')[1]
    assert "can be added here" not in docstring
    for hedge in ("could be added", "may be added", "would be added"):
        assert hedge not in docstring


def test_signpost_header_is_scoped_to_self_extension_requests() -> None:
    """(f) the block is last in the base prompt — the most recency-weighted
    slot — and is emitted on EVERY turn. An unconditional "Extending yourself"
    there is a standing invitation to write an extension when the user asked
    for ordinary work. One trigger clause removes the pull."""

    prompt = build_system_prompt(".")
    header = next(
        line for line in prompt.splitlines() if line.startswith("Extending yourself")
    )
    assert "when the user asks" in header
    assert "Aelix itself" in header


async def test_prompt_does_not_claim_dot_aelix_writes_ALWAYS_prompt(tmp_path) -> None:
    """(e) the approval claim must be CONDITIONAL, because the ladder is.

    The first shipped draft asserted a ``.aelix/`` write "always prompts the
    user for approval, even in auto-accept-edits". Driving the real
    ``PermissionExtension._on_tool_call`` with a real ``write`` event targeting
    ``<cwd>/.aelix/extensions/x.py`` falsifies "always" in most cells — this
    test re-derives the table rather than trusting the prose:

    - YOLO returns at branch (e) (``permission.py:438-439``) BEFORE the write
      check, so no prompt in ANY surface.
    - Headless (``-p`` / ``--mode json`` / ``--mode rpc``) has no approver at
      all: branch (d) (``:486-489``) allows outright.

    An overclaim here is worse than in a doc: the model ACTS on it. A model
    told "you will always be prompted" that then is not prompted has been
    handed a false model of its own permission system.
    """

    from unittest.mock import MagicMock

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode

    event = ToolCallHookEvent(
        tool_name="write",
        args={"path": str(tmp_path / ".aelix" / "extensions" / "x.py"), "content": "x"},
    )

    async def prompted(mode: PermissionMode, *, has_ui: bool) -> bool:
        ext = PermissionExtension()
        ext.posture.set(mode)
        seen = []

        async def fake_prompt(_event, _ctx):
            seen.append(1)
            return None

        ext._prompt = fake_prompt  # type: ignore[assignment]
        ctx = MagicMock()
        ctx.cwd = str(tmp_path)
        ctx.has_ui = has_ui
        await ext._on_tool_call(event, ctx)
        return bool(seen)

    # Interactive, non-YOLO: the prompt DOES appear (this is the case the
    # bullet exists for — the model must not read it as a refusal).
    for mode in (
        PermissionMode.DEFAULT,
        PermissionMode.AUTO_ACCEPT,
        PermissionMode.AUTO,
    ):
        assert await prompted(mode, has_ui=True), mode

    # ...and these are the cells that make "always" a lie.
    assert not await prompted(PermissionMode.YOLO, has_ui=True)
    assert not await prompted(PermissionMode.DEFAULT, has_ui=False)
    assert not await prompted(PermissionMode.AUTO_ACCEPT, has_ui=False)

    prompt = build_system_prompt(".")
    assert "always prompts" not in prompt
    assert "even in auto-accept-edits" not in prompt
    # MINOR 7: the bullet asserted the prompt "is expected", which the table
    # above falsifies — it appears in 3 of 15 cells. The comment beside the
    # code already said "may ask"; the emitted text now says the same thing.
    assert "may ask for approval" in prompt
    assert "is expected, not a refusal" not in prompt


def test_prompt_forbids_working_around_a_declined_approval() -> None:
    """(e) a DECLINE must terminate the attempt, not reroute it.

    ``.aelix`` is in ``_SENSITIVE_DIR_COMPONENTS`` so the write tool is gated —
    but ``bash`` is a different tool with a different rule key, so an agent that
    reads "denied" as an obstacle can simply re-run the same write as a heredoc
    and succeed. The bullet must close that door explicitly; "not a refusal"
    alone reads as encouragement to keep trying.
    """

    from aelix_coding_agent.builtin.permission import _SENSITIVE_DIR_COMPONENTS

    assert ".aelix" in _SENSITIVE_DIR_COMPONENTS  # why a prompt appears at all
    prompt = build_system_prompt(".")
    assert "not a refusal" in prompt
    assert "do not retry it via bash" in prompt
    assert "do not write elsewhere to dodge it" in prompt


async def test_prompt_covers_a_policy_BLOCK_and_not_only_a_user_decline(
    tmp_path,
) -> None:
    """(e) MINOR 3 — a decline is not the only way the write is refused.

    The bullet handled "the user declines" but said nothing about a BLOCK,
    which is a refusal by POLICY that no amount of retrying can change. Two
    measured sources, re-derived here against the real permission extension:

    - PLAN mode blocks every mutating tool on EVERY surface — the check sits
      above the read-only short-circuit precisely so it binds headless too
      (``permission.py:414-418``).
    - A DELEGATED headless child blocks on default / auto-accept-edits / auto
      (``permission.py:486-489``, ``headless_default == "block"``).

    Told only about declines, an agent that meets a BLOCK has no instruction
    covering it — the case where "try another way" is most tempting and most
    wrong.
    """

    from unittest.mock import MagicMock

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode

    event = ToolCallHookEvent(
        tool_name="write",
        args={"path": str(tmp_path / ".aelix" / "extensions" / "x.py"), "content": "x"},
    )

    async def outcome(
        mode: PermissionMode, *, has_ui: bool, delegated: bool = False
    ) -> object:
        ext = PermissionExtension()
        ext.posture.set(mode)
        if delegated:
            ext.headless_default = "block"

        async def fake_prompt(_event, _ctx):
            return None

        ext._prompt = fake_prompt  # type: ignore[assignment]
        ctx = MagicMock()
        ctx.cwd = str(tmp_path)
        ctx.has_ui = has_ui
        return await ext._on_tool_call(event, ctx)

    # PLAN blocks on every surface, interactive included.
    for has_ui in (True, False):
        result = await outcome(PermissionMode.PLAN, has_ui=has_ui)
        assert result is not None and result.block is True, has_ui

    # A delegated headless child blocks on the ordinary postures.
    for mode in (
        PermissionMode.DEFAULT,
        PermissionMode.AUTO_ACCEPT,
        PermissionMode.AUTO,
    ):
        result = await outcome(mode, has_ui=False, delegated=True)
        assert result is not None and result.block is True, mode

    # So the bullet must name the blocked case, and route it to the same stop
    # as a decline rather than to a workaround.
    prompt = build_system_prompt(".")
    bullet = next(ln for ln in prompt.splitlines() if "may ask for approval" in ln)
    assert "blocked" in bullet
    assert "stop and say so" in bullet


def test_signpost_token_cost_stays_bounded() -> None:
    """The block is a permanent per-turn tax; keep it near the measured budget.

    The measured prototype took the prompt from ~1844 to ~2704 chars. Four of
    the block's lines are ABSOLUTE paths whose length depends on where the
    package is installed (a deep site-packages path is far longer than a source
    checkout), so the budget is asserted on the PROSE — block length minus the
    emitted paths — which is what an author actually controls. This guards
    against the block growing into a chapter; it is not a style police.
    """

    prompt = build_system_prompt("/some/project")
    block = _agent_context._extension_signpost("/some/project")
    assert block in prompt

    emitted_paths = [
        line.split(": ", 1)[1].strip()
        for line in block.splitlines()
        if line.startswith("  - ")
    ]
    assert len(emitted_paths) == 4  # 2 write targets + 2 read pointers
    prose = len(block) - sum(len(p) for p in emitted_paths)
    # RAISED 800 -> 1200 by the adversarial review (measured 1096), then
    # 1200 -> 1320 by the truth audit (measured 1240). Every raise so far has
    # been bought by the same thing: a review finding of the form "this
    # sentence is not true", whose true version is longer than the false one.
    #
    #   +  a trigger clause on the header             (review MINOR 6)
    #   +  the trust condition on the project target  (review MAJOR 2)
    #   +  `aelix.on("tool_call", handler)` spelled out, and a grep hint that
    #      can actually find it                       (review MINOR 5)
    #   +  "if the user declines, stop" instead of "always prompts" (review MAJOR 1)
    #   +  a fallback for the surfaces with no /reload (review MINOR 3)
    #   +  `-E`, so the emitted grep is not a hard error (audit MAJOR 1)
    #   +  "too big to read whole ... then read at the line it reports"
    #      instead of an order to read a file that truncates (audit MAJOR 2)
    #   +  "or the write is blocked" alongside the decline (audit MINOR 3)
    #   +  the restart fallback when /reload does not re-discover (audit MINOR 4)
    #
    # Correctness outranks brevity in a block the model ACTS on: the measured
    # failure this block exists to fix was a confidently wrong answer, not a
    # slow one, and an instruction that ERRORS when executed is worse than no
    # instruction at all. The budget still exists to stop the block becoming a
    # chapter — every raise must cite the finding that paid for it.
    assert prose < 1320, prose


async def test_harness_options_carry_the_signpost() -> None:
    """The block reaches the REAL harness prompt, not just the helper."""

    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert "Extending yourself" in options.system_prompt
    assert "def setup(aelix)" in options.system_prompt


async def test_explicit_system_prompt_still_drops_the_signpost() -> None:
    """``--system-prompt`` remains a full override (no silent injection)."""

    options = await _build_harness_options(
        Args(system_prompt="CUSTOM"), Session(MemorySessionStorage())
    )
    assert options.system_prompt == "CUSTOM"


# --- AGENTS.md discovery -----------------------------------------------------


def test_discover_context_files_finds_agents_md(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use tabs. Run `make test`.\n", encoding="utf-8")
    context = discover_context_files(str(tmp_path))
    assert "Use tabs" in context
    assert "AGENTS.md" in context  # labeled with the source path


def test_discover_context_files_walks_up_tree(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    sub = tmp_path / "pkg" / "deep"
    sub.mkdir(parents=True)
    context = discover_context_files(str(sub))
    assert "root rules" in context  # parent AGENTS.md discovered from a child cwd


def test_discover_context_files_none_returns_empty(tmp_path) -> None:
    sub = tmp_path / "empty"
    sub.mkdir()
    # No AGENTS.md in `sub`; parents (tmp_path) also have none.
    assert discover_context_files(str(sub)) == ""


def test_discover_context_files_skips_binary(tmp_path) -> None:
    # A non-UTF-8 AGENTS.md must be skipped (UnicodeDecodeError is a ValueError,
    # not an OSError) — it must NOT crash CLI startup.
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\x00\x80not utf-8")
    assert discover_context_files(str(tmp_path)) == ""


def test_discover_context_files_truncates_oversized(tmp_path) -> None:
    from aelix_coding_agent.cli.agent_context import _MAX_CONTEXT_BYTES

    (tmp_path / "AGENTS.md").write_text("A" * (_MAX_CONTEXT_BYTES * 2), encoding="utf-8")
    context = discover_context_files(str(tmp_path))
    assert context  # truncated, NOT silently dropped
    assert len(context.encode("utf-8")) <= _MAX_CONTEXT_BYTES


# --- _build_harness_options wiring -------------------------------------------


async def test_build_harness_options_wires_seven_tools() -> None:
    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert {t.name for t in options.tools} == _TOOL_NAMES


async def test_build_harness_options_sets_coding_agent_system_prompt() -> None:
    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert options.system_prompt  # non-empty (was "" → no identity)
    assert "Aelix" in options.system_prompt


async def test_build_harness_options_explicit_system_prompt_overrides() -> None:
    parsed = Args(system_prompt="CUSTOM PROMPT")
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt == "CUSTOM PROMPT"  # --system-prompt wins


# --- cli flag wiring: --tools / --no-tools (active_tool_names) ---------------


def test_resolve_active_tools() -> None:
    from aelix_coding_agent.cli.entry import _resolve_active_tools

    assert _resolve_active_tools(Args(no_tools=True)) == []
    assert _resolve_active_tools(Args(tools=["read", "grep"])) == ["read", "grep"]
    assert _resolve_active_tools(Args()) is None


async def test_build_harness_options_wires_active_tool_names() -> None:
    """--tools / --no-tools flow into AgentHarnessOptions.active_tool_names."""

    opts_all = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts_all.active_tool_names is None  # default: all tools active

    opts_allow = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage())
    )
    assert opts_allow.active_tool_names == ["read"]

    opts_none = await _build_harness_options(
        Args(no_tools=True), Session(MemorySessionStorage())
    )
    assert opts_none.active_tool_names == []


async def test_build_harness_options_drops_tools_filter_on_reload() -> None:
    """#24-FU (adversarial-review MEDIUM): on reload the factory must build
    UNFILTERED (active_tool_names=None) instead of re-applying the launch --tools
    filter through the harness's RAISING validator — otherwise a --tools-named
    extension tool whose extension was since removed would raise inside _apply and
    brick the session. reload() step-6 restores the live filter instead."""

    opts_reload = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage()), on_reload=True
    )
    assert opts_reload.active_tool_names is None  # filter deferred to reload step-6

    # Non-reload rebuilds (/new, /fork, /resume, first build) still apply --tools.
    opts_build = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage()), on_reload=False
    )
    assert opts_build.active_tool_names == ["read"]


async def test_build_harness_options_appends_mcp_tools() -> None:
    """MCP tools passed to _build_harness_options join the harness toolset."""

    base = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    builtin_count = len(base.tools)

    sentinel = next(iter(base.tools))  # reuse a real AgentTool as the "mcp" tool
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), mcp_tools=[sentinel]
    )
    assert len(opts.tools) == builtin_count + 1


async def test_build_harness_options_trusted_loads_on_disk_extension(
    tmp_path, monkeypatch, capsys
) -> None:
    """Sprint P0 #10: a TRUSTED project loads its on-disk extension and the
    old post-hoc security warning is GONE (replaced by the trust gate)."""

    # Isolate discovery: cwd = tmp project with one project-local extension;
    # global agent dir → empty tmp so no real ~/.aelix extensions leak in.
    (tmp_path / ".aelix" / "extensions").mkdir(parents=True)
    (tmp_path / ".aelix" / "extensions" / "probe.py").write_text(
        'def setup(aelix):\n    aelix.register_flag("probe_flag", type="bool", default=True)\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), project_trusted=True
    )
    err = capsys.readouterr().err
    # The old cosmetic warning was removed in favor of the real gate.
    assert "full system permissions" not in err
    # built-ins (2) + the discovered probe (1)
    assert len(opts.extensions) == 3


async def test_build_harness_options_untrusted_suppresses_on_disk_extension(
    tmp_path, monkeypatch, capsys
) -> None:
    """Sprint P0 #10: an UNTRUSTED project drops its project-local on-disk
    extension (``no_project_local``) — only the 2 built-ins load."""

    (tmp_path / ".aelix" / "extensions").mkdir(parents=True)
    (tmp_path / ".aelix" / "extensions" / "probe.py").write_text(
        'def setup(aelix):\n    aelix.register_flag("probe_flag", type="bool", default=True)\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), project_trusted=False
    )
    # The project-local probe was NOT loaded; only Guardrail + Permission.
    assert len(opts.extensions) == 2


async def test_build_harness_options_no_warning_without_on_disk(
    tmp_path, monkeypatch, capsys
) -> None:
    """No on-disk extensions → no security warning (only the 2 built-ins load)."""

    monkeypatch.chdir(tmp_path)  # empty project, no .aelix/extensions
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    err = capsys.readouterr().err
    assert "full system permissions" not in err
    assert len(opts.extensions) == 2


# --- issue #44: settings_manager harness seam wiring -------------------------


async def test_build_harness_options_threads_settings_manager() -> None:
    """Issue #44: a passed SettingsManager reaches AgentHarnessOptions.settings_manager
    — the dormant enabler that makes harness.reload() stop raising invalid_state in
    production. The aelix-agent-core seam (field/property/reload) already exists; this
    asserts the coding-agent glue forwards the instance."""
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory()
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=sm
    )
    assert opts.settings_manager is sm


async def test_build_harness_options_settings_manager_defaults_none() -> None:
    """Issue #44: omitting settings_manager preserves the pre-#44 default (None),
    so no caller is forced to thread it and existing behavior is unchanged."""
    opts = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts.settings_manager is None


# --- steering / follow-up mode seed from persisted settings ------------------


async def test_build_harness_options_seeds_steering_and_follow_up() -> None:
    """A persisted /settings steering / follow-up change must SURVIVE restart:
    the harness options are seeded from the SettingsManager (they had get/set
    pairs but no startup consumer, so the harness always booted the default and
    the persisted value silently reverted on every relaunch / /new / /fork)."""
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({"steeringMode": "all", "followUpMode": "all"})
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=sm
    )
    assert opts.steering_mode == "all"
    assert opts.follow_up_mode == "all"


async def test_build_harness_options_steering_defaults_one_at_a_time() -> None:
    """No SettingsManager (or unset) → the pi-parity default "one-at-a-time",
    matching the AgentHarnessOptions dataclass default (no behaviour change)."""
    from aelix_ai.settings import SettingsManager

    opts_none = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts_none.steering_mode == "one-at-a-time"
    assert opts_none.follow_up_mode == "one-at-a-time"

    opts_unset = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=SettingsManager.in_memory({})
    )
    assert opts_unset.steering_mode == "one-at-a-time"
    assert opts_unset.follow_up_mode == "one-at-a-time"
