"""#634 — the ``agent`` schema declares the keys that choose the call first.

An upstream that decodes tool arguments against the schema can emit keys only
in the order the schema declares them: once a key is written, every key
declared before it is out of reach, and the object closes without it. Measured
on OpenRouter's Ionstream upstream (``deepseek/deepseek-v4.1-flash-1017``,
2026-10-30, raw SSE): with the order ``profile, mode, task, tasks, cwd, action,
id`` it delivered 0 of 38 ``agent(action="resume")`` calls with their ``task``,
whatever the instruction's language or line breaks; with ``profile, action, id,
mode, task, tasks, cwd`` it delivered 10 of 10. Unconstrained upstreams wrote
the same resume ``action, id, profile, task`` (6 of 6 on Parasail), and every
upstream kept ``task``/``tasks`` on a new delegation, which is written ``profile,
mode, task|tasks``. The measurements are in
``.omc/specs/634-progress-2026-10-31.md``; the raw probes are kept locally
(``.omc/specs/resume-task-dropped-2026-10-30/``, not in the repo).

The model that met the bare "needs 'task'" refusal in a live run retried the
same call ten times; the refusal now says the call arrived without one and
gives a way out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.tool import AGENT_TOOL_PARAMETERS, AgentCallError, parse_agent_call
from aelix_ai.providers._openai_compat import OpenAICompletionsCompat
from aelix_ai.providers.openai_completions import convert_tools

from tests.agents_ext.test_agent_resume_tool_610 import _RESUME, _delegated, _nothing_spent
from tests.agents_ext.test_child_session_records import _records
from tests.agents_ext.test_tool_and_security import _bench, _call, _text, _write_profile

PINNED = ["profile", "action", "id", "mode", "task", "tasks", "cwd"]

# The orders models wrote these calls in, as the probes recorded them on
# upstreams that do not constrain key order (the resume) and on Ionstream (the
# two new delegations, which it delivered whole under both orders). ``cwd`` is
# appended where a call carries one: it was not in the probes, and every
# description names it after the task it applies to.
WRITTEN = {
    "resume": ["action", "id", "profile", "task"],
    "single": ["profile", "mode", "task"],
    "single in a directory": ["profile", "mode", "task", "cwd"],
    "parallel": ["profile", "mode", "tasks"],
    "chain in a directory": ["profile", "mode", "tasks", "cwd"],
}


def _delivered(declared: list[str], wanted: list[str]) -> list[str]:
    """What an order-enforcing decoder delivers of ``wanted``: each key while it
    is declared after the last one written. A key declared earlier can no
    longer be written, and is lost."""

    delivered: list[str] = []
    for key in wanted:
        if not delivered or declared.index(key) > declared.index(delivered[-1]):
            delivered.append(key)
    return delivered


def test_the_keys_that_choose_the_call_are_declared_before_what_it_carries() -> None:
    """The exact order, pinned. Moving ``action`` or ``id`` back behind ``task``
    is the defect itself: an order-enforcing upstream then drops every resume's
    instruction, and the model is refused over a key it wrote."""

    assert list(AGENT_TOOL_PARAMETERS["properties"]) == PINNED
    # ``profile`` is the one required key, so a decoder that honours
    # ``required`` writes it first whatever the model's own order.
    assert AGENT_TOOL_PARAMETERS["required"] == ["profile"]


def test_the_order_survives_into_the_request_a_provider_receives(tmp_path: Path) -> None:
    """What an upstream decodes against is the schema as SENT: the registered
    tool through the OpenAI-compatible converter, serialised as the request
    body is. Nothing on the way sorts the keys."""

    _write_profile(tmp_path / "agent" / "agents" / "w.md", "w")
    tool = _bench(tmp_path).tool
    [wire] = json.loads(json.dumps(convert_tools([tool], OpenAICompletionsCompat())))
    assert list(wire["function"]["parameters"]["properties"]) == PINNED


@pytest.mark.parametrize("shape", sorted(WRITTEN))
def test_no_key_a_call_carries_is_declared_before_one_written_ahead_of_it(
    shape: str,
) -> None:
    """Each call shape survives an order-enforcing decoder whole: the required
    key first, then the model's keys in the order it writes them."""

    declared = list(AGENT_TOOL_PARAMETERS["properties"])
    required = AGENT_TOOL_PARAMETERS["required"]
    wanted = required + [key for key in WRITTEN[shape] if key not in required]
    delivered = _delivered(declared, wanted)
    assert delivered == wanted, f"{shape}: {set(wanted) - set(delivered)} would be dropped"


def test_the_order_before_634_loses_the_resume_instruction_in_that_model() -> None:
    """The model above against the pre-#634 order reproduces what Ionstream
    delivered — ``profile, action, id`` and no ``task`` — so it is a model of
    the failure and not only of the fix."""

    before = ["profile", "mode", "task", "tasks", "cwd", "action", "id"]
    resume = ["profile", "action", "id", "task"]
    assert _delivered(before, resume) == ["profile", "action", "id"]


@pytest.mark.parametrize(
    "args",
    [
        {k: v for k, v in _RESUME.items() if k != "task"},
        {**_RESUME, "task": None},
        {**_RESUME, "task": ""},
    ],
)
def test_a_resume_that_arrived_without_its_instruction_says_so(args: dict[str, Any]) -> None:
    """The refusal says the call ARRIVED without it — a model sure it wrote one
    learns that it did not get here — and gives two ways on, counted: the same
    call once more, then a fresh delegation. It names no provider: the model
    cannot pick one."""

    with pytest.raises(AgentCallError) as caught:
        parse_agent_call(args)
    message = str(caught.value)
    assert message.startswith("action='resume' needs 'task'")
    assert "this call arrived without one" in message
    assert "Send the call again once, with 'task'" in message
    assert "do not send it a third time: start a fresh agent() run" in message
    assert not re.search(r"(?i)provider|openrouter|ionstream|upstream", message)


async def test_that_refusal_is_a_tool_result_and_spends_nothing(tmp_path: Path) -> None:
    """At the hook: the turn goes on with an error result the model reads, and
    no dialog, budget, record or child comes of it."""

    scene = await _delegated(tmp_path)
    before = await _records(scene.session)
    result = await _call(
        scene.bench,
        {"profile": "scout", "action": "resume", "id": scene.child_id},
        tool_call_id="tc-2",
    )
    assert "this call arrived without one" in _text(result)
    await _nothing_spent(scene, result, before)
