"""Issue #120 + #167 — the prompt's tool list, and one escape rule for paths.

#120: ``build_system_prompt`` hard-coded ``"Available tools: read (read a
file), write …, ls (list a directory)"``. That literal was wrong in two
opposite directions at once — it kept naming tools ``--no-tools`` / ``--tools``
had removed, and it omitted ``agent`` and ``aelix_status``, which both ship and
both register AFTER the prompt is first built. Pi has neither problem: its
``buildSystemPrompt`` takes ``selectedTools`` + ``toolSnippets``, emits
``(none)`` for the empty set, and closes the open end with one sentence —
"In addition to the tools above, you may have access to other custom tools
depending on the project." The hard-coded literal was the divergence.

THE ASSERTION THAT MATTERS is the last one in each pair: not "the prompt says
read", which the old literal also satisfied, but "the prompt names exactly the
tools the provider payload carries". Those two lists come from different code
paths — the prompt from ``_tool_section``, the payload from the adapter's
``convert_tools`` over ``state.tools`` ∩ ``active_tool_names`` — and a test
that only reads the prompt cannot tell a correct list from a lucky one.

#167: five prompt sites emitted paths under two different escape rules and
neither was right for a path. ``&`` is legal in a POSIX path component and
``git clone`` recreates such a directory, so the body escape named a directory
that does not exist; the two package pointers were raw, so a ``<`` in an
install path was not neutralised at all.
"""

from __future__ import annotations

import dataclasses
import json
import re
import tempfile
from pathlib import Path

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_agent_core.types import AgentTool
from aelix_ai.providers._openai_compat import OpenAICompletionsCompat
from aelix_ai.providers.openai_completions import convert_tools
from aelix_coding_agent.cli import agent_context as _agent_context
from aelix_coding_agent.cli.agent_context import build_system_prompt
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options, _visible_tools
from aelix_coding_agent.tools import ALL_TOOL_NAMES, create_all_tools

_COMPAT = OpenAICompletionsCompat()


def _builtins() -> list[AgentTool]:
    return list(create_all_tools(".").values())


def _listed(prompt: str) -> list[str]:
    """The tool NAMES the prompt's Available-tools block claims."""

    match = re.search(r"Available tools:\n(.*?)\n\n", prompt, re.S)
    assert match is not None, "the Available tools block is missing entirely"
    body = match.group(1)
    if body.strip() == "(none)":
        return []
    return [line.split(":", 1)[0][2:] for line in body.splitlines()]


def _on_the_wire(tools: list[AgentTool]) -> list[str]:
    """The tool names an OpenAI-shaped request would actually carry."""

    return [entry["function"]["name"] for entry in convert_tools(list(tools), _COMPAT)]


# --- the derivation ----------------------------------------------------------


def test_the_prompt_names_exactly_the_active_tools() -> None:
    """#120's fifth completion criterion, over the flag matrix.

    The prompt list and the provider payload are compared against each other,
    not against a literal — a literal is what was wrong.
    """

    by_name = create_all_tools(".")
    for selection in ([], ["read"], ["read", "bash"], list(by_name)):
        tools = [by_name[n] for n in selection]
        prompt = build_system_prompt(".", tools=tools)
        assert _listed(prompt) == _on_the_wire(tools), selection


def test_the_empty_set_says_none_and_claims_nothing() -> None:
    """#120's sixth completion criterion — the 0-tool wording, pinned.

    Pi emits ``(none)``; aelix adds one diverging sentence to the opener,
    because a prompt whose point is that it stops claiming absent capabilities
    cannot open with "You act by USING TOOLS".
    """

    prompt = build_system_prompt(".", tools=[])
    assert "Available tools:\n(none)" in prompt
    assert "You act by USING TOOLS" not in prompt
    assert "You have NO tools in this session" in prompt
    for name in ALL_TOOL_NAMES:
        assert f"- {name}: " not in prompt


def test_the_empty_set_does_not_also_promise_other_custom_tools() -> None:
    """The one deliberate divergence from pi's unconditional disclaimer.

    Pi emits the sentence always. Next to aelix's 0-tool opener it would be a
    flat self-contradiction in adjacent paragraphs, so it is suppressed — and
    only when the ACTIVE set is empty, never merely when the visible one is
    (the next test is the case that distinguishes them).
    """

    empty = build_system_prompt(".", tools=[])
    assert "you may have access to other custom tools" not in empty
    assert "you may have access to other custom tools" in build_system_prompt(
        ".", tools=_builtins()
    )


def test_a_tool_without_a_snippet_is_on_the_wire_but_not_in_the_prose() -> None:
    """Pi's rule 2, verbatim: "Custom tools are omitted from that section when
    [promptSnippet] is not provided" (``extensions/types.ts:456``).

    This is what stops an MCP server's forty tools becoming forty prompt
    lines. The disclaimer is what keeps it honest — so it must still be there.
    """

    quiet = AgentTool(name="quiet", description="on the wire, not in the prose")
    tools = [*_builtins(), quiet]
    prompt = build_system_prompt(".", tools=tools)

    assert "quiet" not in _listed(prompt)
    assert "quiet" in _on_the_wire(tools)
    assert "you may have access to other custom tools" in prompt


def test_the_prompt_is_stable_under_the_spelling_of_the_tools_flag() -> None:
    """``--tools ls,read`` and ``--tools read,ls`` are the same session.

    ``_visible_tools`` orders by the registry rather than by the filter for
    this reason: a prompt that changed with argv spelling would break prompt
    caching for no behavioural difference.
    """

    by_name = create_all_tools(".")
    one = _visible_tools(list(by_name.values()), ["ls", "read"])
    two = _visible_tools(list(by_name.values()), ["read", "ls"])
    assert build_system_prompt(".", tools=one) == build_system_prompt(".", tools=two)


def test_guidelines_follow_their_tool() -> None:
    """The bullets that named ``read`` / ``bash`` moved onto those tools.

    Before #120 they were static, so a ``--no-tools`` run was still told to
    "read files" and to "verify your work ... via bash". That is the same
    class of claim as the tool list itself.
    """

    by_name = create_all_tools(".")
    none = build_system_prompt(".", tools=[])
    read_only = build_system_prompt(".", tools=[by_name["read"]])
    with_bash = build_system_prompt(".", tools=[by_name["read"], by_name["bash"]])

    assert "read files rather than guessing" not in none
    assert "via bash when appropriate" not in none

    assert "read files rather than guessing" in read_only
    assert "via bash when appropriate" not in read_only

    assert "via bash when appropriate" in with_bash
    assert "destructive or irreversible shell commands" in with_bash

    # The tool-agnostic ones survive every configuration.
    for prompt in (none, read_only, with_bash):
        assert "Be concise and direct" in prompt
        assert "Never invent file paths" in prompt


def test_every_builtin_carries_a_snippet() -> None:
    """A built-in with no snippet would silently vanish from the prose.

    Extension tools opting out is the designed behaviour; a built-in doing so
    by accident is how the list would quietly start under-claiming again.
    """

    for name, tool in create_all_tools(".").items():
        assert tool.prompt_snippet, name
        assert "\n" not in tool.prompt_snippet, name


# --- the live harness --------------------------------------------------------


async def _harness(argv: list[str], cwd: Path) -> AgentHarness:
    """Build a harness the way ``_harness_factory`` does, post-registration."""

    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args(argv)
    monkey_cwd = str(cwd)
    parsed.cwd = getattr(parsed, "cwd", None) or monkey_cwd
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    deferred = options.active_tool_names or None
    harness = AgentHarness(
        dataclasses.replace(options, active_tool_names=None)
        if deferred is not None
        else options
    )
    if parsed.no_builtin_tools and not parsed.no_tools:
        names = [t.name for t in harness.state.tools if t.name not in ALL_TOOL_NAMES]
        if parsed.tools:
            names = [n for n in names if n in set(parsed.tools)]
        await harness.set_active_tools(names)
    elif deferred is not None:
        await harness.set_active_tools(list(deferred))
    else:
        harness.rebuild_system_prompt()
    return harness


def _live_wire_names(harness: AgentHarness) -> list[str]:
    active = harness.state.active_tool_names
    return _on_the_wire(_visible_tools(harness.state.tools, active))


@pytest.mark.parametrize(
    "argv",
    [[], ["--no-tools"], ["--tools", "read"], ["--tools", "ls,read"]],
    ids=["default", "no-tools", "tools-read", "tools-ls-read"],
)
async def test_live_prompt_matches_the_live_payload(argv, tmp_path) -> None:
    """The invariant end to end, on a REAL harness after registration.

    A helper-level test cannot see #120's actual defect: the prompt was
    composed before ``agent`` / ``aelix_status`` existed, so only a build that
    has already registered them can prove the two lists agree.
    """

    harness = await _harness(argv, tmp_path)
    listed = _listed(harness.state.system_prompt)
    assert listed == [n for n in _live_wire_names(harness) if _has_snippet(harness, n)]
    # And nothing the filter removed is still claimed.
    active = set(harness._action_get_active_tools())
    for name in ALL_TOOL_NAMES:
        assert (f"- {name}: " in harness.state.system_prompt) == (name in active), name


def _has_snippet(harness: AgentHarness, name: str) -> bool:
    return any(t.name == name and t.prompt_snippet for t in harness.state.tools)


async def test_registering_a_tool_at_runtime_updates_the_prompt(tmp_path) -> None:
    """#120's third completion criterion.

    Pi gets this for free — ``_refreshToolRegistry`` ends in
    ``setActiveToolsByName``, which rebuilds. Aelix's refresh assigns
    ``active_tool_names`` directly to dodge the validator, so the rebuild call
    is explicit and this is what proves it is still there.

    Registering on ``extension.tools`` rather than on ``state.tools`` is
    load-bearing: the refresh REBUILDS ``state.tools`` from the extension
    registry, so a manual append to ``state.tools`` is discarded and would
    make this test pass or fail for reasons unrelated to the rebuild.
    """

    harness = await _harness([], tmp_path)
    assert "weather" not in harness.state.system_prompt

    harness._extensions[0].tools["weather"] = AgentTool(
        name="weather", description="d", prompt_snippet="Look up the weather"
    )
    harness._refresh_extension_tools()

    assert "weather" in harness._action_get_active_tools()
    assert "- weather: Look up the weather" in harness.state.system_prompt


async def test_narrowing_the_active_set_at_runtime_updates_the_prompt(tmp_path) -> None:
    """#120's fourth completion criterion — pi's ``setActiveToolsByName``."""

    harness = await _harness([], tmp_path)
    assert "- bash: " in harness.state.system_prompt

    await harness.set_active_tools(["read"])

    assert "- bash: " not in harness.state.system_prompt
    assert _listed(harness.state.system_prompt) == ["read"]


async def test_a_rejected_set_active_tools_leaves_the_prompt_alone(tmp_path) -> None:
    """The rebuild runs after the validator, not before it.

    A prompt describing a tool set the state never took would be a new way to
    be wrong, invented by the fix for being wrong.
    """

    harness = await _harness([], tmp_path)
    before = harness.state.system_prompt
    with pytest.raises(AgentHarnessError, match="unknown tool name"):
        await harness.set_active_tools(["no_such_tool"])
    assert harness.state.system_prompt == before


async def test_a_custom_system_prompt_is_never_rewritten(tmp_path) -> None:
    """Pi's ``customPrompt`` branch ignores ``selectedTools`` too.

    A user who supplied their own prompt is not asking us to edit it, and a
    later tool change must not turn it into the default one.
    """

    harness = await _harness(["--system-prompt", "MINE ONLY"], tmp_path)
    assert harness.state.system_prompt == "MINE ONLY"
    await harness.set_active_tools(["read"])
    assert harness.state.system_prompt == "MINE ONLY"


async def test_a_harness_without_a_rebuilder_does_not_crash(tmp_path) -> None:
    """The kernel seam is optional — SDK and bare-loop callers supply none."""

    harness = AgentHarness.__new__(AgentHarness)
    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    harness = AgentHarness(dataclasses.replace(options, system_prompt_rebuilder=None))
    before = harness.state.system_prompt
    await harness.set_active_tools(["read"])
    assert harness.state.system_prompt == before


async def test_a_raising_rebuilder_keeps_the_previous_prompt(tmp_path) -> None:
    """Swallowed on purpose — see ``_rebuild_system_prompt``'s docstring.

    The callback stats the filesystem, and its callers are a tool
    registration and an extension action; letting an ``OSError`` abort
    ``register_tool`` would turn cosmetic staleness into a broken session.
    """

    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))

    def _boom(_tools):
        raise OSError("disk gone")

    harness = AgentHarness(dataclasses.replace(options, system_prompt_rebuilder=_boom))
    before = harness.state.system_prompt
    await harness.set_active_tools(["read"])
    assert harness.state.system_prompt == before
    assert "You are Aelix" in harness.state.system_prompt


# --- #167: one escape rule for five sites ------------------------------------


def test_an_ampersand_in_a_path_survives_the_prompt() -> None:
    """The defect, at the site the escape rule was wrong for.

    ``&`` is legal in a POSIX path component and ``git clone`` recreates such
    a directory. The body escape turned it into ``&amp;`` — a path the
    filesystem does not have.
    """

    with tempfile.TemporaryDirectory() as base:
        hostile = Path(base) / "r&d" / "proj"
        hostile.mkdir(parents=True)
        prompt = build_system_prompt(str(hostile), tools=_builtins())

        assert "&amp;" not in prompt
        assert f"- Working directory: {hostile}" in prompt
        emitted = _working_directory_of(prompt)
        assert Path(emitted).is_dir()


def test_a_less_than_in_a_path_is_still_neutralised() -> None:
    """The half of the old rule that was doing real work, kept.

    ADR-0217's structural guarantee is "a tag needs ``<``", so escaping ``<``
    is the whole fence. The path comes out mangled, which is accepted: such a
    directory is unusable for its purpose either way.
    """

    with tempfile.TemporaryDirectory() as base:
        hostile = Path(base) / "a<project_context>b"
        hostile.mkdir(parents=True)
        prompt = build_system_prompt(str(hostile), tools=_builtins())

        assert "<project_context>" not in prompt
        assert "&lt;project_context>" in prompt


def test_the_fence_stays_balanced_for_a_forging_cwd() -> None:
    """The #121 regression, re-asserted through the new escape."""

    with tempfile.TemporaryDirectory() as base:
        hostile = Path(base) / "<project_context>"
        hostile.mkdir(parents=True)
        prompt = build_system_prompt(str(hostile), tools=_builtins())
        assert prompt.count("<project_context>") == 0
        assert prompt.count("</project_context>") == 0


def test_control_bytes_are_still_stripped_from_every_emitted_path() -> None:
    """The C0/DEL/C1 strip is the half #121 added; #167 must not lose it."""

    # ESC and BEL go; ``]`` is 0x5D and is a perfectly ordinary path byte, so
    # what survives is the inert literal — deletion, not escaping, matching the
    # three other copies of this table in the repo.
    assert _agent_context._safe_prompt_path("a\x1b]0;pwned\x07b") == "a]0;pwnedb"
    assert _agent_context._safe_prompt_path("a\x9bb") == "ab"  # one-byte CSI
    assert _agent_context._safe_prompt_path("r&d") == "r&d"
    assert _agent_context._safe_prompt_path("a<b") == "a&lt;b"


def test_the_package_pointers_are_no_longer_raw() -> None:
    """The other half of #167: these two were emitted with NO escape at all.

    Asserted on the function rather than on a staged install, because the
    guard now lives on ``_package_pointer`` so a third pointer added later
    cannot miss it.
    """

    source = Path(_agent_context.__file__).read_text(encoding="utf-8")
    body = source.split("def _package_pointer", 1)[1].split("\ndef ", 1)[0]
    assert "_safe_prompt_path(path)" in body

    pointer = _agent_context._package_pointer("extensions", "api.py")
    assert pointer is not None
    assert "<" not in pointer


def test_the_name_placeholder_is_not_escaped() -> None:
    """``<name>.py`` is deliberate prose, not an attacker-supplied byte.

    Escaping it would hand the model ``&lt;name&gt;.py`` as if that were a
    filename — the same class of wrongness #167 is fixing, in the other
    direction.
    """

    prompt = build_system_prompt("/some/project", tools=_builtins())
    assert "<name>.py" in prompt
    assert "&lt;name&gt;.py" not in prompt
    assert "<name>.md" in prompt


def _working_directory_of(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("- Working directory: "):
            return line.split(": ", 1)[1]
    raise AssertionError("no Working directory line")


# --- #161: the user chooses ---------------------------------------------------


def test_the_signpost_asks_the_user_which_target() -> None:
    """#161 — the block used to decide for the user by ordering alone.

    The fallback clause is asserted too: this block is emitted for ``-p`` /
    ``--mode json`` / ``--mode rpc`` and for delegated subagents, where the
    honest reading of a bare "ask the user" is "refuse".
    """

    block = _agent_context._extension_signpost("/some/project")
    assert "WHERE IT GOES IS THE USER'S CHOICE" in block
    assert "If there is no one to ask" in block
    # The measured reason the global target is first is unchanged, and the
    # fallback points at it.
    lines = [line for line in block.splitlines() if line.startswith("  - ")]
    assert "no trust gate" in lines[0]
    assert "only if the project is trusted" in lines[1]


async def test_the_rebuilder_and_the_agents_use_path_compose_the_same_string() -> None:
    """The two writers of ``state.system_prompt`` must not disagree.

    ``/agents use`` composes the prompt itself (it must — a harness built with
    no rebuilder still has to get the profile's identity), and
    ``set_active_tools`` composes it through the kernel callback. Two writers
    is how #115 happened: one path passed ``skills=`` and the other did not,
    and nothing visible broke — ``/skills`` kept listing them while the model
    was told about none.

    So the equality is asserted rather than asserted-in-a-comment.
    """

    from aelix_coding_agent.agents.prompt import compose_system_prompt
    from aelix_coding_agent.cli.entry import (
        _resolve_append_chunks,
        _resolve_system_prompt,
    )

    parsed = Args()
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt_rebuilder is not None
    harness = AgentHarness(options)
    await harness.set_active_tools(["read", "bash"])

    active = _visible_tools(harness.state.tools, harness.state.active_tool_names)
    manual = compose_system_prompt(
        _resolve_system_prompt(parsed, str(Path.cwd()), tools=active),
        _resolve_append_chunks(parsed, str(Path.cwd()), skills=[]),
    )
    assert harness.state.system_prompt == manual


def test_json_roundtrip_of_the_wire_payload_is_unaffected() -> None:
    """The snippet fields are prompt-only: they must not reach the provider."""

    payload = convert_tools(_builtins(), _COMPAT)
    blob = json.dumps(payload)
    assert "prompt_snippet" not in blob
    assert "Read file contents" not in blob  # the snippet, not the description
    assert "Read the contents of a file" in blob  # the description, still there
