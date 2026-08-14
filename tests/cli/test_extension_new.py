"""Issue #161 shape 2 — ``/extension new <name>`` asks, then scaffolds.

WHY THIS EXISTS RATHER THAN A BETTER SENTENCE. Shape 1 (the self-extension
block asks in prose) was built, shipped behind a live-model gate, and probed
against a real model in a real interactive TUI — twice per wording:

    prompt              user said              asked?
    ------------------  ---------------------  --------------------------------
    pre-change          "Please add it"        no — wrote global, silently
    "ask and wait"      "Do the work."         no — wrote global, silently
    "ask and wait"      "Please add it"        no — wrote global, but SAID why
    "STOP and ask"      "Please add it"        YES — asked, stopped its turn
    "STOP and ask"      "Do the work."         no — wrote global

One compliance in four, and the variable was the USER's phrasing. Issue #161
said to choose the shape on that measurement.

WHAT THESE TESTS CAN AND CANNOT PIN. They pin the command: that it asks before
writing, that cancelling writes nothing, that it refuses to overwrite, that the
scaffold it writes actually LOADS through the real extension loader, and that
the project-local answer says the thing that makes the two targets different.
They cannot pin that a model runs it — nothing in a test can. That is why the
signpost still carries both absolute paths, and why the claim in ADR-0219 is
"the user has a first-class way to make the choice", not "the model now asks".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.scaffold import (
    create_extension,
    extension_targets,
    starter_source,
    validate_extension_name,
)

# --- the pure half -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "   ", "has space", "has-dash", "9leading", "UPPER", "a/b", "../escape", "x" * 41],
    ids=[
        "empty",
        "blank",
        "space",
        "dash",
        "leading-digit",
        "uppercase",
        "slash",
        "traversal",
        "too-long",
    ],
)
def test_a_name_the_loader_could_not_import_is_refused(name: str) -> None:
    """The loader imports discovered files BY STEM.

    A stem that is not an identifier is a file that never loads, so writing it
    would be the confident-failure mode #117 exists to kill — with the command
    reporting success over a file the session will silently ignore.
    """

    assert validate_extension_name(name) is not None


def test_a_name_that_would_shadow_a_real_module_is_refused() -> None:
    """``aelix.py`` in the extensions directory shadows the real package.

    Once that directory is importable the shadow is process-wide, which is a
    far worse failure than a rejected name.
    """

    assert validate_extension_name("aelix") is not None
    assert validate_extension_name("aelix_agent_core") is not None
    assert validate_extension_name("weather") is None
    assert validate_extension_name("utc_time") is None


def test_the_two_targets_are_the_directories_the_loader_scans(tmp_path) -> None:
    """The command and the system prompt must not disagree about where.

    The signpost emits ``<agent dir>/extensions`` and
    ``<cwd>/.aelix/extensions``; so does this. Global first, matching the
    signpost's order and its measured reason.
    """

    targets = extension_targets(str(tmp_path / "proj"), str(tmp_path / "agent"))
    assert list(targets) == ["global", "project"]
    assert targets["global"] == tmp_path / "agent" / "extensions"
    assert targets["project"] == tmp_path / "proj" / ".aelix" / "extensions"


def test_create_refuses_to_overwrite(tmp_path) -> None:
    """``new`` on an existing name is a typo far more often than a request to
    discard working code — and this command should not inherit ``write``'s
    overwrite semantics just because it writes."""

    create_extension(tmp_path, "thing")
    with pytest.raises(FileExistsError):
        create_extension(tmp_path, "thing")


async def test_the_scaffold_is_a_working_extension_the_real_loader_accepts(
    tmp_path,
) -> None:
    """A stub with ``...`` in it teaches the #117 lesson badly.

    Driven through the SHIPPED loader, not through ``exec`` or an import: the
    thing that must be true is "aelix loads this", and only the loader can say
    so. A double that merely imports the file would pass over a scaffold the
    real discovery path rejects.
    """

    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    project = tmp_path / "proj"
    ext_dir = project / ".aelix" / "extensions"
    path = create_extension(ext_dir, "probe_tool")
    assert path.exists()

    result = await discover_and_load_extensions(
        [],
        cwd=project,
        agent_dir=tmp_path / "agent",
    )
    assert result.errors == [], result.errors
    names = [t.name for ext in result.extensions for t in ext.tools.values()]
    assert "probe_tool" in names


def test_the_scaffold_carries_a_prompt_snippet(tmp_path) -> None:
    """#120 and #161 meet here: a scaffolded tool should be NAMED in the prompt.

    Pi omits a custom tool from the "Available tools" list when it provides no
    ``promptSnippet``. A starter that omitted one would teach every new
    extension author to be invisible.
    """

    source = starter_source("probe_tool")
    assert "prompt_snippet=" in source
    # And the comment says what leaving it out means, because that IS the
    # documented behaviour rather than a bug.
    assert "still sent to the model" in source


def test_the_scaffold_names_the_tool_consistently() -> None:
    """A scaffold whose tool name and file stem disagree is a debugging trap."""

    source = starter_source("weather_now")
    assert 'name="weather_now"' in source
    assert "weather_now_tool" in source
    assert re.search(r"async def _weather_now_execute\(", source)


# --- the command -------------------------------------------------------------


class _Recorder:
    """A ``ctx``-shaped double that records what the command committed.

    The ``select`` hop is the REAL one the command uses —
    ``harness.runtime.ui.select`` — not a convenience attribute. A double that
    exposed ``select`` somewhere easier would pass while production could not
    find it, which is this repo's recorded trap.
    """

    def __init__(self, cwd: Path, answer: str | int | None) -> None:
        self.cwd = str(cwd)
        self.committed: list[str] = []
        self.asked: list[tuple[str, list[str]]] = []
        self._answer = answer
        self.extension_action = None

        recorder = self

        class _UI:
            async def select(self, title: str, options: list[str]) -> str | None:
                recorder.asked.append((title, list(options)))
                if isinstance(recorder._answer, int):
                    return options[recorder._answer]
                return recorder._answer

        class _Runtime:
            ui = _UI()

        class _Harness:
            runtime = _Runtime()

        self.harness = _Harness()
        self.chrome = None

    def commit(self, renderable: object) -> None:
        self.committed.append(str(getattr(renderable, "plain", renderable)))

    @property
    def text(self) -> str:
        return "\n".join(self.committed)


async def _run(ctx: _Recorder, args: str, agent_dir: Path) -> None:
    import aelix_coding_agent.cli.config as config_mod
    from aelix_coding_agent.tui.commands import _extension_handler

    original = config_mod.get_agent_dir
    config_mod.get_agent_dir = lambda: str(agent_dir)  # type: ignore[assignment]
    try:
        await _extension_handler(ctx, args)  # type: ignore[arg-type]
    finally:
        config_mod.get_agent_dir = original  # type: ignore[assignment]


async def test_it_asks_before_it_writes(tmp_path) -> None:
    """The whole point. Nothing is written until the user has answered."""

    project = tmp_path / "proj"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    ctx = _Recorder(project, answer=0)  # the global option

    await _run(ctx, "new weather", agent_dir)

    assert len(ctx.asked) == 1
    title, options = ctx.asked[0]
    assert "weather" in title
    assert len(options) == 2
    # Both options state the CONSEQUENCE, not just the location — that is what
    # makes the question answerable by someone who has not read the docs.
    assert "no trust gate" in options[0]
    assert "everyone who clones" in options[1]
    assert (agent_dir / "extensions" / "weather.py").exists()


async def test_cancelling_writes_nothing(tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    ctx = _Recorder(project, answer=None)

    await _run(ctx, "new weather", agent_dir)

    assert ctx.asked  # it did ask
    assert not (agent_dir / "extensions" / "weather.py").exists()
    assert not (project / ".aelix" / "extensions" / "weather.py").exists()
    assert "Cancelled" in ctx.text


async def test_the_project_answer_says_the_thing_that_makes_it_different(
    tmp_path,
) -> None:
    """The project-local tier fails SILENTLY when the project is untrusted.

    That is the measured fact the signpost lists the global target first for,
    and the reason asking beats guessing: the user is choosing between "works"
    and "works if trusted", and only one of those failure modes is visible.
    """

    project = tmp_path / "proj"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    ctx = _Recorder(project, answer=1)  # project-local

    await _run(ctx, "new weather", agent_dir)

    assert (project / ".aelix" / "extensions" / "weather.py").exists()
    assert "trusted" in ctx.text
    assert "no error" in ctx.text


async def test_a_bad_name_is_refused_before_anything_is_asked(tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = _Recorder(project, answer=0)

    await _run(ctx, "new has space", tmp_path / "agent")

    assert ctx.asked == []
    assert "module name" in ctx.text


async def test_no_dialog_surface_refuses_rather_than_guessing(tmp_path) -> None:
    """Degrading to "just pick one" would reinstate the defect exactly.

    The command exists to make the choice visible; a version of it that
    silently chooses is the pre-#161 behaviour with extra steps. It names both
    paths instead, so the user can still act.
    """

    project = tmp_path / "proj"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    ctx = _Recorder(project, answer=0)
    ctx.harness.runtime.ui = None  # type: ignore[assignment]

    await _run(ctx, "new weather", agent_dir)

    assert not (agent_dir / "extensions" / "weather.py").exists()
    assert not (project / ".aelix" / "extensions" / "weather.py").exists()
    assert str(agent_dir / "extensions" / "weather.py") in ctx.text
    assert str(project / ".aelix" / "extensions" / "weather.py") in ctx.text


async def test_bare_extension_still_opens_the_manager(tmp_path) -> None:
    """The subcommand must not have eaten the existing command."""

    project = tmp_path / "proj"
    project.mkdir()
    ctx = _Recorder(project, answer=0)
    opened: list[bool] = []

    async def _open() -> None:
        opened.append(True)

    ctx.extension_action = _open  # type: ignore[assignment]
    await _run(ctx, "", tmp_path / "agent")

    assert opened == [True]
    assert ctx.asked == []


def test_the_signpost_points_at_the_command() -> None:
    """A command the model never mentions is a command nobody finds.

    Issue #161's own text warns that shape 2 "brings shape 1's compliance
    problem back unless the signpost points at it".
    """

    from aelix_coding_agent.cli.agent_context import _extension_signpost

    block = _extension_signpost("/some/project", {"read", "write"})
    assert "/extension new <name>" in block
    # And the headless fallback is still there — there is no `/` command to run
    # in `-p` / `--mode json` / `--mode rpc` or in a delegated subagent.
    assert "use the first path below" in block
