"""ADR-0197 §(c)/§(f)/§(l) — ``/agents run <name> <task>`` in the TUI.

``/agents run`` is a product-core BUILT-IN and it could not be anything else:
``shell.py:3172-3189`` runs ``match_command`` first and only falls through to
``dispatch.try_execute`` when no built-in claims the leading word, and
``extensions/command_dispatch.py::_split_command`` splits an extension command on
the FIRST SPACE, so an extension command name can never contain one. Spec §6.3's
``register_command("run", …)`` under ``/agents`` is therefore not implementable
as written, and ``test_an_extension_cannot_serve_agents_run`` pins both halves of
that argument so nobody re-derives it.

What the branch is allowed to do is the other half of the file: it CALLS a bound
Protocol and does nothing else. It never spawns, never builds argv, never parses
a child stream, and never authors consent — ``spawn()`` takes its own spawn-time
consent internally (§(i)) and returns a ``status="declined"`` result rather than
raising, which is why a decline renders as a calm yellow line and not a failure
panel.

Driven through the real ``BUILTIN_COMMANDS`` dispatch with a fake
``CommandContext`` whose ``commit`` records renderables — the house idiom from
``tests/tui/test_commands.py`` and ``test_agents_command.py``. The runtime is a
hand-written double: this file is about the COMMAND, and a real
``_SubagentRuntimeImpl`` would drag a real subprocess in with it.

No ``__init__.py`` in ``tests/tui`` — local convention.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.streaming import Model
from aelix_coding_agent.extensions.command_dispatch import _split_command
from aelix_coding_agent.subagent_contract import (
    ResolvedProfile,
    SubagentResult,
    SubagentUsage,
)
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    _render_subagent_result,
    _sanitize_child_field,
    match_command,
)
from rich.console import Console


def _render(renderable: object) -> str:
    buffer = io.StringIO()
    # Wide on purpose: the result panel carries a usage grid and a source path,
    # and a narrow console would turn substring assertions into a function of
    # how long pytest made ``tmp_path``.
    Console(file=buffer, width=300, no_color=True).print(renderable)
    return buffer.getvalue()


class _FakeChrome:
    pass


@dataclass
class _FakeProfile:
    """The shape ``ResolvedProfile.profile`` carries — only ``name`` is read."""

    name: str
    file_path: str
    scope: str = "user"
    description: str = ""


@dataclass
class _FakeDiscovery:
    """The shape ``ctx.agent_profiles()`` returns — only ``profiles`` is read."""

    profiles: list[_FakeProfile]
    diagnostics: list[Any] = field(default_factory=list)


class _RefusedProjectScope(Exception):
    """The refusal shape ``_SubagentRuntimeImpl.resolve_profile`` raises.

    Deliberately NOT ``ProfileError``: ``SubagentRuntime`` is a structural
    Protocol with no exception hierarchy, so the command must recognise the
    refusal the same way a third-party runtime's would be recognised — by the
    message. Using the real class here would let a type check masquerade as the
    message sniff that actually ships.
    """


@dataclass
class _FakeRuntime:
    """A ``SubagentRuntime``-shaped double that records what it was asked."""

    contract_version: int = 1
    result: SubagentResult | None = None
    project_scoped: set[str] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)
    resolve_calls: list[tuple[str, bool]] = field(default_factory=list)
    spawn_calls: list[tuple[str, str]] = field(default_factory=list)
    spawn_raises: Exception | None = None

    def resolve_profile(
        self, name_or_path: str, *, allow_project: bool = False
    ) -> ResolvedProfile:
        self.resolve_calls.append((name_or_path, allow_project))
        if name_or_path in self.unknown:
            raise ValueError(f"unknown agent profile {name_or_path!r}")
        scope = "project" if name_or_path in self.project_scoped else "user"
        if scope == "project" and not allow_project:
            raise _RefusedProjectScope(
                f"agent profile {name_or_path!r} (/repo/.aelix/agents/"
                f"{name_or_path}.md) is project-local and needs a per-identity "
                "confirmation; a model-chosen delegation cannot give one."
            )
        return ResolvedProfile(
            name=name_or_path,
            profile=_FakeProfile(name=name_or_path, file_path="/repo/x.md"),  # type: ignore[arg-type]
            source_path=f"/repo/.aelix/agents/{name_or_path}.md",
            scope=scope,
        )

    async def spawn(self, resolved: ResolvedProfile, task: str, **_: Any) -> SubagentResult:
        self.spawn_calls.append((resolved.name, task))
        if self.spawn_raises is not None:
            raise self.spawn_raises
        assert self.result is not None
        return self.result

    def list(self) -> list[Any]:  # pragma: no cover - Protocol completeness
        return []

    def status(self, id: str) -> Any:  # noqa: A002 - Protocol spelling
        raise KeyError(id)

    async def stop(self, id: str) -> None:  # noqa: A002 - Protocol spelling
        return None

    async def stop_all(self) -> None:
        return None


class _ScriptedUI:
    """The extension UI seam, scripted. ``answers`` is popped left to right."""

    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, list[str]]] = []

    async def select(
        self, title: str, options: list[str], opts: Any = None
    ) -> str | None:
        self.calls.append((title, list(options)))
        return self.answers.pop(0) if self.answers else None


class _RaisingUI:
    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        raise RuntimeError("no terminal attached")


def _ok_result(**overrides: Any) -> SubagentResult:
    base: dict[str, Any] = {
        "id": "sa-1",
        "profile": "scout",
        "ok": True,
        "status": "ok",
        "summary": "Found 3 files under packages/.",
        "usage": SubagentUsage(input=120, output=45, cache_read=7, cost=0.0123, turns=2),
        "elapsed_ms": 4200,
        "permission_mode": "plan",
    }
    base.update(overrides)
    return SubagentResult(**base)


class _Bench:
    """A real (offline) harness plus a ``CommandContext`` pointed at it."""

    def __init__(self, tmp_path: Path) -> None:
        self.harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="probe-model", provider="probe"),
                system_prompt="BASELINE",
            )
        )
        self.committed: list[Any] = []
        self.ctx = CommandContext(
            chrome=_FakeChrome(),  # type: ignore[arg-type]
            harness=self.harness,
            commit=self.committed.append,
            cwd=str(tmp_path),
            commands=list(BUILTIN_COMMANDS),
        )

    def bind(self, runtime: Any) -> _FakeRuntime:
        # Through the REAL seam, not a patched attribute: ``bind_subagents``
        # carries the version/depth/double-bind refusals, so binding this way
        # keeps the test honest about what a bound runtime even is.
        self.harness.runtime.bind_subagents(runtime)
        return runtime

    def bind_ui(self, ui: Any) -> Any:
        self.harness.runtime.bind_ui(ui)
        return ui

    async def run(self, line: str) -> str:
        self.committed.clear()
        command = match_command(line, self.ctx.commands)
        assert command is not None and command.handler is not None
        parts = line.split(maxsplit=1)
        await command.handler(self.ctx, parts[1] if len(parts) > 1 else "")
        return "".join(_render(item) for item in self.committed)


@pytest.fixture()
def bench(tmp_path: Path) -> _Bench:
    return _Bench(tmp_path)


# === degradation =============================================================


async def test_run_without_runtime_degrades(bench: _Bench) -> None:
    """The ORDINARY state: ``[features] agents`` defaults to False in P2."""

    assert bench.harness.runtime.subagents is None
    out = await bench.run("/agents run scout do a thing")

    assert "Delegation is unavailable" in out
    assert "[features] agents" in out
    assert "--agents" in out

    # THE SETTINGS CLAUSE MUST NOT READ AS SUFFICIENT ON ITS OWN. The flag is
    # consumed once per process when the harness is built, so a user who only
    # toggles ``/settings`` lands back on this exact line with nothing to tell
    # them why — the journey this wording exists to stop. ``--agents`` therefore
    # comes first and the restart is stated.
    assert "restart" in out.lower(), out
    assert out.index("--agents") < out.index("[features] agents"), out


async def test_run_requires_name_and_task(bench: _Bench) -> None:
    runtime = bench.bind(_FakeRuntime(result=_ok_result()))

    for line in ("/agents run", "/agents run scout", "/agents run scout    "):
        out = await bench.run(line)
        assert "needs a name and a task" in out, line
    assert runtime.spawn_calls == []


async def test_a_missing_task_is_never_treated_as_an_empty_one(bench: _Bench) -> None:
    """Whitespace is not a task. A blank prompt would burn a real child run."""

    runtime = bench.bind(_FakeRuntime(result=_ok_result()))
    await bench.run("/agents run scout \t \n ")

    assert runtime.spawn_calls == []


# === the happy path ==========================================================


async def test_run_dispatches_to_bound_runtime(bench: _Bench) -> None:
    runtime = bench.bind(_FakeRuntime(result=_ok_result()))
    await bench.run("/agents run scout list the files in packages/")

    assert runtime.resolve_calls == [("scout", False)]
    assert runtime.spawn_calls == [("scout", "list the files in packages/")]


async def test_the_model_door_default_is_never_widened_here(bench: _Bench) -> None:
    """``allow_project`` starts False even though this door is user-typed.

    The identity gate is deliberately stricter than the authority gate: consent
    to a project-local IDENTITY is taken by the explicit confirmation below, not
    implied by the user having typed the command (finding B5).
    """

    runtime = bench.bind(_FakeRuntime(result=_ok_result()))
    await bench.run("/agents run scout a task")

    assert runtime.resolve_calls[0][1] is False


async def test_run_renders_result_panel(bench: _Bench) -> None:
    bench.bind(_FakeRuntime(result=_ok_result()))
    out = await bench.run("/agents run scout list files")

    assert "Found 3 files under packages/." in out
    assert "scout" in out
    # The granted posture is the only after-the-fact record of what authority
    # the user handed that child — §(l) requires it on its own line.
    assert "permission" in out and "plan" in out
    assert "turns" in out and "2" in out
    assert "cost" in out


async def test_permission_mode_is_shown_even_when_the_runtime_omits_it(
    bench: _Bench,
) -> None:
    """``—`` rather than a dropped row: the question must stay answerable."""

    bench.bind(_FakeRuntime(result=_ok_result(permission_mode=None)))
    out = await bench.run("/agents run scout list files")

    assert "permission" in out


async def test_a_widened_grant_is_visible_in_the_panel(bench: _Bench) -> None:
    """A child that was granted write access must SAY so where a human reads it."""

    bench.bind(_FakeRuntime(result=_ok_result(permission_mode="auto-accept-edits")))
    out = await bench.run("/agents run scout edit the readme")

    assert "auto-accept-edits" in out


async def test_the_child_model_is_shown_on_the_result_panel(bench: _Bench) -> None:
    """``SubagentResult.model`` is already populated (``envelope.py:384-385``) but
    P2 named it nowhere. A profile with no ``model:`` runs the persisted default
    at a different price, and before this row the only way to notice was the
    bill — so the grid states ``provider/id`` on its own line."""

    bench.bind(
        _FakeRuntime(result=_ok_result(model="claude-opus-4-8", provider="anthropic"))
    )
    out = await bench.run("/agents run scout list files")

    assert "model" in out
    assert "anthropic/claude-opus-4-8" in out


async def test_the_model_row_is_omitted_when_the_child_named_no_model(
    bench: _Bench,
) -> None:
    """A child that errored before any ``message_end`` has no model to report.
    Unlike ``permission`` — a security fact that stays answerable as ``—`` — an
    absent model is simply dropped: there is nothing to name."""

    bench.bind(_FakeRuntime(result=_ok_result(model=None, provider=None)))
    out = await bench.run("/agents run scout list files")

    assert "permission" in out
    assert "model" not in out


def test_a_hostile_child_model_cannot_drive_the_result_grids_terminal() -> None:
    """FINDING 1 (HIGH). ``model``/``provider`` are read off the child's own
    ``message_end`` verbatim (``stream.py:558-563`` → ``envelope.py:384-385``), so
    they are attacker-controlled. Rich ``Text`` blocks MARKUP but writes raw
    content ESC / C1 to the terminal, so a ``\\x1b[2J`` in the model would clear
    the parent's screen and a one-byte ``\\x9b`` would drive its cursor.

    Rendered through a ``force_terminal`` console (the real-TTY path, where Rich
    does NOT strip) AND a ``no_color`` one (which isolates child bytes from Rich's
    own SGR), the child's injected sequences are gone and the field is width
    bound. The width AND the control-strip are both load-bearing: remove either
    step from ``_sanitize_child_field`` and this test fails.
    """

    hostile = "gpt\x1b[2J\x1b]0;pwn\x07\x9b31m\n" + "Z" * 3000
    result = SubagentResult(
        id="s",
        profile="scout",
        ok=True,
        status="ok",
        summary="ok",
        usage=SubagentUsage(turns=1),
        model=hostile,
        provider="anthropic",
    )
    parts = _render_subagent_result(result)

    # The real-TTY path: Rich emits its OWN SGR here, so we assert the child's
    # SPECIFIC injections are absent rather than a blanket "no ESC" (impossible
    # under force_terminal, and Finding 1's proof used exactly this console).
    tty = io.StringIO()
    tty_console = Console(file=tty, width=120, force_terminal=True)
    for part in parts:
        tty_console.print(part)
    out_tty = tty.getvalue()
    assert "\x1b[2J" not in out_tty  # clear-screen — never Rich's own
    assert "\x1b]" not in out_tty  # OSC (set window title) — never Rich's own
    assert "\x9b" not in out_tty  # C1: the one-byte CSI — never Rich's own
    assert "\x07" not in out_tty  # BEL
    # Width bound: the 3000-char run is truncated, so only a handful survive.
    assert out_tty.count("Z") < 100

    # The no-color path isolates child bytes from Rich's own styling, so here the
    # blanket assertion the reviewer asked for IS meaningful.
    plain = io.StringIO()
    plain_console = Console(file=plain, width=120, no_color=True)
    for part in parts:
        plain_console.print(part)
    out_plain = plain.getvalue()
    assert "\x1b" not in out_plain
    assert "\x9b" not in out_plain
    # The provider is still composed onto the de-fanged id — no dangling slash.
    assert "anthropic/" in out_plain


def test_sanitize_child_field_strips_control_collapses_and_bounds() -> None:
    """The unit behind Finding 1, pinned directly so a later grid edit cannot
    quietly drop a step: collapse whitespace (newline → space), delete C0/DEL/C1
    (ESC and the one-byte CSI), bound the width, and return ``""`` for a value
    that was nothing but control bytes (the caller then omits the row)."""

    assert _sanitize_child_field("a\nb") == "a b"
    assert "\n" not in _sanitize_child_field("a\nb\nc")
    defanged = _sanitize_child_field("x\x1b[31m\x9b\x07y")
    assert (
        "\x1b" not in defanged
        and "\x9b" not in defanged
        and "\x07" not in defanged
    )
    assert _sanitize_child_field("\x1b\x00\x9b") == ""  # all-control → empty
    assert len(_sanitize_child_field("Z" * 5000)) <= 40  # width bound


async def test_run_renders_failure_panel(bench: _Bench) -> None:
    bench.bind(
        _FakeRuntime(
            result=_ok_result(
                ok=False, status="error", summary="", error="child exited 1"
            )
        )
    )
    out = await bench.run("/agents run scout break something")

    assert "child exited 1" in out
    assert "error" in out


async def test_dropped_tools_are_surfaced(bench: _Bench) -> None:
    """The child's tool set is the INTERSECTION with the parent's live grant.

    A profile asking for more silently gets less; unsaid, a half-equipped agent
    reads as a bad model rather than as a narrowed grant.
    """

    bench.bind(_FakeRuntime(result=_ok_result(dropped_tools=("bash", "write"))))
    out = await bench.run("/agents run scout look around")

    assert "dropped tools" in out
    assert "bash" in out and "write" in out


async def test_truncation_is_announced_with_the_details_size(bench: _Bench) -> None:
    bench.bind(
        _FakeRuntime(
            result=_ok_result(
                summary="short", truncated=True, details="x" * 5000
            )
        )
    )
    out = await bench.run("/agents run scout read everything")

    assert "truncated" in out
    assert "5000 bytes" in out


async def test_a_spawn_that_raises_does_not_kill_the_repl(bench: _Bench) -> None:
    bench.bind(_FakeRuntime(spawn_raises=RuntimeError("boom")))
    out = await bench.run("/agents run scout do it")

    assert "agents run failed" in out
    assert "boom" in out


async def test_an_unknown_profile_surfaces_the_runtimes_own_message(
    bench: _Bench,
) -> None:
    bench.bind(_FakeRuntime(unknown={"ghost"}, result=_ok_result()))
    out = await bench.run("/agents run ghost do it")

    assert "agents run failed" in out
    assert "ghost" in out


# === the decline (§(i)) ======================================================


async def test_run_renders_declined_result(bench: _Bench) -> None:
    """A decline is not a failure — nothing ran, so nothing broke.

    ``spawn`` returns ``status="declined"`` rather than raising precisely so this
    stays a calm one-liner; a red failure panel here would train users to stop
    reading them.
    """

    bench.bind(
        _FakeRuntime(
            result=SubagentResult(
                id="sa-2",
                profile="builder",
                ok=False,
                status="declined",
                summary="",
                permission_mode="plan",
            )
        )
    )
    out = await bench.run("/agents run builder rewrite everything")

    assert "declined" in out.lower()
    assert "builder" in out
    assert "✖" not in out


# === project-scoped identity (finding B5) ====================================


async def test_run_project_scoped_profile_prompts(bench: _Bench) -> None:
    """A repo-authored identity takes ONE explicit human confirmation first."""

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(profile="reviewer"), project_scoped={"reviewer"})
    )
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    out = await bench.run("/agents run reviewer check the diff")

    assert len(ui.calls) == 1
    assert "reviewer" in ui.calls[0][0]
    assert runtime.resolve_calls == [("reviewer", False), ("reviewer", True)]
    assert runtime.spawn_calls == [("reviewer", "check the diff")]
    assert "Found 3 files" in out


async def test_the_prompt_names_the_file_the_user_would_have_to_read(
    bench: _Bench,
) -> None:
    """The PATH, not just the name, when a listing is wired.

    A bare name is exactly what a project-vs-user collision weaponises: a repo's
    ``reviewer`` WINS against ``~/.aelix/agent/agents/reviewer.md``
    (``agents/service.py:100-101``), so "reviewer" alone tells the human nothing
    about which file they are about to run.
    """

    bench.bind(_FakeRuntime(result=_ok_result(), project_scoped={"reviewer"}))
    bench.ctx.agent_profiles = lambda: _FakeDiscovery(
        [_FakeProfile(name="reviewer", file_path="/repo/.aelix/agents/reviewer.md", scope="project")]
    )
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    await bench.run("/agents run reviewer check the diff")

    assert "/repo/.aelix/agents/reviewer.md" in ui.calls[0][0]


async def test_a_user_scoped_namesake_is_not_offered_as_the_path(
    bench: _Bench,
) -> None:
    """Only a PROJECT-scoped listing entry may supply the path.

    The refusal being confirmed is about the project file; naming the user's own
    same-named profile in the dialog would describe the wrong file entirely.
    """

    bench.bind(_FakeRuntime(result=_ok_result(), project_scoped={"reviewer"}))
    bench.ctx.agent_profiles = lambda: _FakeDiscovery(
        [_FakeProfile(name="reviewer", file_path="/home/u/.aelix/agents/reviewer.md")]
    )
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    await bench.run("/agents run reviewer check the diff")

    assert "/home/u/" not in ui.calls[0][0]
    assert "(path unavailable)" in ui.calls[0][0]


async def test_a_broken_listing_does_not_block_the_gate(bench: _Bench) -> None:
    """Discovery is best-effort here — it decorates the prompt, it is not it."""

    def _boom() -> Any:
        raise RuntimeError("discovery exploded")

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    bench.ctx.agent_profiles = _boom
    bench.bind_ui(_ScriptedUI(["Run this profile"]))
    await bench.run("/agents run reviewer check the diff")

    assert runtime.spawn_calls == [("reviewer", "check the diff")]


@pytest.mark.parametrize(
    "answer",
    [None, "Cancel", "run this profile", "Run this profile ", "", "Yes"],
    ids=["esc", "cancel", "wrong-case", "trailing-space", "empty", "wrong-word"],
)
async def test_a_project_identity_is_unreachable_without_the_affirmative(
    bench: _Bench, answer: str | None
) -> None:
    """WHY §(i) MAY SKIP THE CONSENT DIALOG FOR A READ-ONLY PROJECT SPAWN.

    The spawn-time consent dialog used to be a second place a human saw WHICH
    identity was about to run, and as of the 2026-07-27 policy change it does
    not fire for a read-only, unwidenable delegation — which is exactly the
    project-scoped case (a project profile can never be widened, §(i)
    constraint 1). This test is the proof that nothing was opened up.

    ``allow_project=True`` is the ONLY way any runtime will return a
    project-scoped :class:`ResolvedProfile`, and this handler is the ONLY caller
    in the source tree that ever passes it (the model door hardcodes ``False``
    at both of its sites — the ``tool_call`` hook and the ``execute()``
    re-check). It is reached only from the affirmative branch of
    ``_confirm_project_agent_for_run``, which is matched by IDENTITY against
    ``PROJECT_AGENT_CONFIRM_OPTIONS[0]``. So: every answer that is not that
    exact string leaves the identity unresolved and starts nothing.
    """

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    bench.bind_ui(_ScriptedUI([answer]))
    out = await bench.run("/agents run reviewer check the diff")

    assert ("reviewer", True) not in runtime.resolve_calls
    assert runtime.spawn_calls == []
    assert "declined" in out.lower()


async def test_declining_the_identity_prompt_starts_nothing(bench: _Bench) -> None:
    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    bench.bind_ui(_ScriptedUI(["Cancel"]))
    out = await bench.run("/agents run reviewer check the diff")

    assert "declined" in out.lower()
    assert runtime.spawn_calls == []


async def test_escape_declines_the_identity_prompt(bench: _Bench) -> None:
    """Esc surfaces as ``None`` from ``select`` and must not read as consent."""

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    bench.bind_ui(_ScriptedUI([None]))
    await bench.run("/agents run reviewer check the diff")

    assert runtime.spawn_calls == []


async def test_a_raising_dialog_declines_and_never_runs(bench: _Bench) -> None:
    """A broken or headless UI is a decline, never a yes.

    ``HEADLESS_UI_CONTEXT.select`` raises ``NotImplementedError`` by design, so
    this is also the no-TUI path: fail closed.
    """

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    bench.bind_ui(_RaisingUI())
    out = await bench.run("/agents run reviewer check the diff")

    assert "declined" in out.lower()
    assert runtime.spawn_calls == []


async def test_no_ui_bound_at_all_declines(bench: _Bench) -> None:
    """The default binding is the headless singleton; it must refuse."""

    runtime = bench.bind(
        _FakeRuntime(result=_ok_result(), project_scoped={"reviewer"})
    )
    await bench.run("/agents run reviewer check the diff")

    assert runtime.spawn_calls == []
    assert runtime.resolve_calls == [("reviewer", False)]


async def test_a_non_project_failure_is_never_turned_into_a_prompt(
    bench: _Bench,
) -> None:
    """Only the project-scope refusal opens the identity gate.

    An unrelated resolve failure must surface as an error, not as an invitation
    to confirm an identity that does not exist.
    """

    runtime = bench.bind(_FakeRuntime(unknown={"ghost"}, result=_ok_result()))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    out = await bench.run("/agents run ghost do it")

    assert ui.calls == []
    assert runtime.spawn_calls == []
    assert "agents run failed" in out


async def test_a_second_runtime_reaches_the_gate_by_TYPE_not_by_wording(
    bench: _Bench,
) -> None:
    """MEDIUM #8 — the refusal is on the CONTRACT now, not in one message.

    ``_is_project_scope_refusal`` used to be nothing but
    ``"per-identity confirmation" in str(exc)``, a phrase produced by exactly
    one implementation. A ``aelix-team`` runtime implementing ``resolve_profile``
    exactly as the Protocol docstring instructs — and wording its refusal any
    other way — fell to ``✖ agents run failed``, and there was then NO path in
    the product to run a project-scoped profile under it: the dialog was never
    offered, so the user could not cure it and could not learn why.

    The runtime below raises :class:`ProjectScopeRefused` with a message that
    shares NO wording with the bundled implementation's. It must still get the
    per-identity dialog and the ``allow_project=True`` retry.
    """

    from aelix_coding_agent.subagent_contract import ProjectScopeRefused

    class _ForeignWording(_FakeRuntime):
        def resolve_profile(
            self, name_or_path: str, *, allow_project: bool = False
        ) -> ResolvedProfile:
            self.resolve_calls.append((name_or_path, allow_project))
            if not allow_project:
                raise ProjectScopeRefused("this identity lives in the repo")
            return ResolvedProfile(
                name=name_or_path,
                profile=_FakeProfile(name=name_or_path, file_path="/repo/x.md"),  # type: ignore[arg-type]
                source_path=f"/repo/.aelix/agents/{name_or_path}.md",
                scope="project",
            )

    runtime = bench.bind(_ForeignWording(result=_ok_result()))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    await bench.run("/agents run reviewer check the diff")

    assert len(ui.calls) == 1, "the typed refusal must open the identity gate"
    assert runtime.resolve_calls == [("reviewer", False), ("reviewer", True)]
    assert runtime.spawn_calls == [("reviewer", "check the diff")]


async def test_an_untrusted_directory_refusal_does_not_open_the_dialog(
    bench: _Bench,
) -> None:
    """Only the PER-IDENTITY refusal is curable here.

    ``agents/discovery.py:357-363`` raises its own project-local refusal when the
    DIRECTORY is untrusted, and no answer to a per-identity dialog can fix that
    — the retry would raise it again. A prompt whose answer changes nothing is
    how users are trained to click through consent dialogs, so the sniff is
    narrow enough to tell the two apart.
    """

    class _Untrusted(_FakeRuntime):
        def resolve_profile(
            self, name_or_path: str, *, allow_project: bool = False
        ) -> ResolvedProfile:
            self.resolve_calls.append((name_or_path, allow_project))
            raise RuntimeError(
                f"agent profile {name_or_path!r} (/repo/.aelix/agents/x.md) is "
                "project-local and this directory is not trusted; pass "
                "--approve to trust this directory for this run."
            )

    runtime = bench.bind(_Untrusted(result=_ok_result()))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    out = await bench.run("/agents run reviewer check the diff")

    assert ui.calls == []
    assert runtime.resolve_calls == [("reviewer", False)]
    assert runtime.spawn_calls == []
    assert "not trusted" in out


async def test_the_confirm_dialog_reuses_the_startup_copy(bench: _Bench) -> None:
    """Same words as ``--agent``'s startup gate (ADR-0196), one source.

    Two prompts asking for the same consent in different words is how a user
    learns to click through one of them.
    """

    from aelix_coding_agent.cli.entry import (
        PROJECT_AGENT_CONFIRM_OPTIONS,
        project_agent_confirm_body,
    )

    bench.bind(_FakeRuntime(result=_ok_result(), project_scoped={"reviewer"}))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))
    await bench.run("/agents run reviewer check the diff")

    title, options = ui.calls[0]
    assert options == list(PROJECT_AGENT_CONFIRM_OPTIONS)
    assert title == project_agent_confirm_body("reviewer", "(path unavailable)")


# === the built-in wins (§(c)) ================================================


def test_builtin_still_shadows_extension() -> None:
    """``/agents run …`` resolves to the built-in ``agents`` command.

    ``shell.py:3172-3189`` consults ``match_command`` first and only reaches
    ``dispatch.try_execute`` on a miss, so a built-in always wins the leading
    word — which is the whole reason this branch lives in product-core.
    """

    command = match_command("/agents run scout do it", list(BUILTIN_COMMANDS))

    assert command is not None
    assert command.name == "agents"
    assert command.handler is not None


def test_an_extension_cannot_serve_agents_run() -> None:
    """The second half of §(c): an extension command name cannot hold a space.

    ``_split_command`` splits on the FIRST space and keeps the remainder as raw
    args, so a hypothetical ``register_command("agents run", …)`` could never be
    matched — and ``register_command("run", …)`` under ``/agents`` is not a thing
    the dispatcher has a concept of.
    """

    name, args = _split_command("/agents run scout do it")

    assert name == "agents"
    assert args == "run scout do it"


# === no regression to P1 =====================================================


async def test_existing_subcommands_unchanged(bench: _Bench) -> None:
    """list / show / use degrade exactly as they did before the run branch."""

    assert "Agent profiles are unavailable." in await bench.run("/agents")
    assert "Agent profiles are unavailable." in await bench.run("/agents list")
    assert "Agent profiles are unavailable." in await bench.run("/agents show scout")
    assert "Agent profile switching is unavailable." in await bench.run(
        "/agents use scout"
    )


async def test_unknown_subcommand_still_reports_usage(bench: _Bench) -> None:
    out = await bench.run("/agents wat")

    assert "Unknown /agents subcommand" in out
    assert "run <name> <task>" in out


async def test_usage_string_advertises_run() -> None:
    from aelix_coding_agent.tui.commands import _AGENTS_USAGE

    assert "run <name> <task>" in _AGENTS_USAGE
