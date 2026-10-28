"""#410 - ``--mode json`` exits 1 when the answer this run produced failed.

The owner's decision of 2026-10-20 (ADR-0268): JSON mode takes the exit code by
the rule text ``-p`` always used - the LAST message, if it is an assistant
message whose ``stop_reason`` is ``"error"`` or ``"aborted"``, makes the run
exit 1. Before, ``run_print_mode`` read that message in text mode only (as pi
still does), so a JSON run whose provider ``502`` or OAuth refresh retries were
exhausted exited **0** and a script could not see the failure in ``$?``.

Review round 2 narrowed the rule in both modes: the last message decides only
when THIS run appended it. A session whose last answer failed in an earlier
run, continued with no prompt or with a prompt an extension's ``input`` hook
handles, exits 0 (pi, and ``-p`` before, exited 1 printing the old error).

What does NOT change, and is pinned here so it cannot drift with the code:

* the stdout event stream - JSON mode writes no extra line for the failure
  (``tests/cli/test_print_mode_json_contract.py`` pins the shape);
* stderr - JSON mode writes nothing there for it either (ADR-0268 §3: a
  delegation parent reads a JSON child's stderr as a fallback summary);
* a signal's own exit code (``128 + sig``) still overrides the 1
  (``tests/cli/test_print_mode_signal_handlers.py``).

The real-CLI rows drive ``_async_main`` with the #379 / #408 fixtures: every
model and token request goes to an ``httpx.MockTransport`` recorder, keys are
fake, the agent dir is a temp dir, and no socket is opened.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantStartEvent,
)
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.agents_ext.test_print_channel_spawn import (
    _hermetic_env,
    _plan,
    _stub_channel,
)
from tests.cli.test_launch_route_344 import _FakePipedStdin
from tests.cli.test_print_mode import _new_harness, _new_runtime
from tests.cli.test_refresh_retry_379 import _MODEL, _Endpoints, _store, agent, endpoints
from tests.print_mode_child import CHILD_SOURCE

__all__ = ["agent", "endpoints"]  # the fixtures, re-used


def _scripted_stream(plan: list[str]) -> Any:
    """One answer per model call, in order: ``ok``, ``length``, ``error`` or ``aborted``."""

    calls = {"n": 0}

    async def fn(model: Any, context: Any, options: Any) -> Any:
        answer = plan[min(calls["n"], len(plan) - 1)]
        calls["n"] += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if answer == "ok":
            message = AssistantMessage(content=[TextContent(text="fine")], stop_reason="end_turn")
        elif answer == "length":
            # Cut off at the output token limit: a partial answer, not a failure.
            message = AssistantMessage(content=[TextContent(text="cut sh")], stop_reason="length")
        elif answer == "error":
            message = AssistantMessage(content=[], stop_reason="error", error_message="it broke")
        else:
            message = AssistantMessage(content=[], stop_reason="aborted")
        yield AssistantEndEvent(message=message)

    return fn


def _json_lines(out: str) -> list[dict[str, Any]]:
    """Every stdout line, parsed - a line that is not JSON fails the test here."""

    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def _cli(argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    rc = await entry_mod._async_main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _mode_flags(mode: str) -> list[str]:
    return ["--mode", "json", "-p", "hi"] if mode == "json" else ["-p", "hi"]


# === the real CLI: exhausted retries =========================================


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_exhausted_provider_502_retries_exit_1_in_both_modes(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """The issue's first case: every model request answers 502 until the retries run out."""

    (agent / "settings.json").write_text(json.dumps({"retry": {"maxRetries": 1, "baseDelayMs": 1}}), "utf-8")
    endpoints.model_plan = ["502"] * 10
    rc, out, err = await _cli(
        ["--no-session", "--provider", "anthropic", "--model", _MODEL["anthropic"], *_mode_flags(mode)],
        monkeypatch,
        capsys,
    )
    assert len(endpoints.model_auth) == 2  # the first request and its one retry
    assert rc == 1, (rc, err)
    if mode == "text":
        assert "upstream bad gateway" in err
        return
    lines = _json_lines(out)
    ends = [e for e in lines if e.get("type") == "auto_retry_end"]
    assert [(e["success"], e["attempt"]) for e in ends] == [(False, 1)]
    last = [e["message"] for e in lines if e.get("type") == "message_end" and e["message"].get("role") == "assistant"][-1]
    assert last["stop_reason"] == "error"
    assert "upstream bad gateway" in (last["error_message"] or "")
    # JSON mode reports the failure in the stream and the exit code only (ADR-0268 §3).
    assert "upstream bad gateway" not in err


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_exhausted_oauth_refresh_retries_exit_1_in_both_modes(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """The issue's second case (#379): the token endpoint answers 502 on every refresh."""

    _store(agent, "openai-codex")
    endpoints.plan = ["502"]  # one entry: repeated for every refresh
    rc, out, err = await _cli(
        ["--no-session", "--provider", "openai-codex", "--model", _MODEL["openai-codex"], *_mode_flags(mode)],
        monkeypatch,
        capsys,
    )
    assert endpoints.model_auth == []  # no request without a refreshed token
    assert endpoints.token_hits == ["502"] * 4  # the first refresh and three retries
    assert rc == 1, (rc, err)
    if mode == "text":
        assert "OAuth refresh failed for openai-codex" in err
        return
    lines = _json_lines(out)
    ends = [e for e in lines if e.get("type") == "auto_retry_end"]
    assert [e["success"] for e in ends] == [False]
    last = [e["message"] for e in lines if e.get("type") == "message_end" and e["message"].get("role") == "assistant"][-1]
    assert last["stop_reason"] == "error"
    assert "OAuth refresh failed for openai-codex" in (last["error_message"] or "")
    assert "OAuth refresh failed" not in err


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_a_run_that_succeeds_still_exits_0(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    endpoints.model_plan = ["502", "200"]  # recovered by the first retry
    rc, _out, err = await _cli(
        ["--no-session", "--provider", "anthropic", "--model", _MODEL["anthropic"], *_mode_flags(mode)],
        monkeypatch,
        capsys,
    )
    assert len(endpoints.model_auth) == 2
    assert rc == 0, (rc, err)


# === run_print_mode: the rule is text's rule ==================================


@pytest.mark.parametrize("mode", ["json", "text"])
@pytest.mark.parametrize(
    ("answers", "code"),
    [
        (["error"], 1),
        (["aborted"], 1),  # no signal: the aborted message itself, as in text
        (["ok"], 0),
        # Review round 2 (verify M5): cut off at the token limit is not a failure
        # in either mode (pi 441f5767c made text -p exit 1 for it; not followed).
        (["length"], 0),
        (["error", "ok"], 0),  # several prompts: the LAST answer decides
        (["ok", "error"], 1),
    ],
    ids=["error", "aborted", "ok", "length", "error-then-ok", "ok-then-error"],
)
async def test_the_last_answer_decides_the_exit_code_in_both_modes(
    capsys: pytest.CaptureFixture[str], mode: str, answers: list[str], code: int
) -> None:
    runtime = _new_runtime(_new_harness(_scripted_stream(answers)))
    exit_code = await run_print_mode(
        runtime,
        mode=mode,  # type: ignore[arg-type]
        messages=[f"prompt {n}" for n in range(2, len(answers) + 1)],
        initial_message="prompt 1",
    )
    captured = capsys.readouterr()
    assert exit_code == code
    if mode == "json":
        # Nothing but the event stream on stdout, and nothing at all on stderr.
        lines = _json_lines(captured.out)
        assert lines[-1].get("type") in ("agent_end", "agent_settled")
        assert captured.err == ""
    elif code == 1:
        assert captured.err.strip() in ("it broke", "Request aborted")
        assert captured.out == ""
    else:
        # The answer - a partial one for "length" - and nothing on stderr.
        assert captured.out == ("cut sh\n" if answers[-1] == "length" else "fine\n")
        assert captured.err == ""


class _TerminalStderr(io.StringIO):
    """A stderr that says it is a terminal, as it is for a person at a shell."""

    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("answer", ["error", "aborted"])
async def test_json_mode_writes_nothing_to_stderr_when_it_is_a_terminal(
    capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    """Review round 2 (cross M4): every other json row has stderr on a pipe.

    A rule that wrote the failure for a person at a terminal
    (``mode == "text" or sys.stderr.isatty()``) passed them all; ADR-0268 §3
    says JSON mode writes nothing to stderr, wherever it goes.
    """

    terminal = _TerminalStderr()
    runtime = _new_runtime(_new_harness(_scripted_stream([answer])))
    with contextlib.redirect_stderr(terminal):
        exit_code = await run_print_mode(runtime, mode="json", messages=[], initial_message="prompt 1")
    capsys.readouterr()
    assert exit_code == 1
    assert terminal.getvalue() == ""


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_a_compaction_that_shortens_the_session_does_not_hide_this_runs_failure(
    capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """Review round 2: "what the run added" is matched by identity, not by count.

    ``AgentHarness.compact`` rebuilds ``state.messages`` in place from the
    session branch (``clear()`` + ``extend()``), so a long session that compacts
    during the run can END with fewer messages than it started with, while its
    last message is this run's failed answer. Here eight earlier messages are
    replaced by a summary plus the run's own two, the same way. A rule that
    counted the messages before the first prompt and read only those past the
    count would find none and exit 0.
    """

    harness = _new_harness(_scripted_stream(["error"]))
    for n in range(4):
        harness.state.messages.append(UserMessage(content=[TextContent(text=f"earlier {n}")]))
        harness.state.messages.append(
            AssistantMessage(content=[TextContent(text=f"answer {n}")], stop_reason="end_turn")
        )
    started_with = len(harness.state.messages)
    real_prompt = harness.prompt

    async def _prompt_then_compact(*args: Any, **kwargs: Any) -> Any:
        result = await real_prompt(*args, **kwargs)
        kept = harness.state.messages[-2:]  # this run's prompt and its failed answer
        harness.state.messages.clear()
        harness.state.messages.extend([UserMessage(content=[TextContent(text="summary")]), *kept])
        return result

    harness.prompt = _prompt_then_compact  # type: ignore[method-assign]
    exit_code = await run_print_mode(
        _new_runtime(harness), mode=mode, messages=[], initial_message="prompt 1"  # type: ignore[arg-type]
    )
    captured = capsys.readouterr()
    assert len(harness.state.messages) < started_with
    assert exit_code == 1
    assert captured.err == ("it broke\n" if mode == "text" else "")


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_a_failed_answer_that_is_not_the_last_message_does_not_decide(
    capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """Pi's text rule reads ``state.messages[-1]``; JSON reads the same message.

    A session whose last message is not the assistant's (here a user message
    appended after the failed turn) exits 0 in text mode, so JSON must too - a
    rule that searched back for the last ASSISTANT message would answer 1 here
    and disagree with text.
    """

    harness = _new_harness(_scripted_stream(["error"]))
    real_prompt = harness.prompt

    async def _prompt_then_append(*args: Any, **kwargs: Any) -> Any:
        result = await real_prompt(*args, **kwargs)
        harness.state.messages.append(UserMessage(content=[TextContent(text="after")]))
        return result

    harness.prompt = _prompt_then_append  # type: ignore[method-assign]
    exit_code = await run_print_mode(
        _new_runtime(harness), mode=mode, messages=[], initial_message="prompt 1"  # type: ignore[arg-type]
    )
    capsys.readouterr()
    assert exit_code == 0


# === review round 2: only an answer THIS run appended decides ================

_HANDLED_BY_EXTENSION = """
from pathlib import Path
from aelix_agent_core.harness.hooks import InputHandled

def setup(aelix):
    def handle(event, ctx):
        if event.text == "ping":
            Path({marker!r}).write_text("handled", encoding="utf-8")
            return InputHandled()
    aelix.on("input", handle)
"""


@pytest.mark.parametrize("mode", ["json", "text"])
@pytest.mark.parametrize("this_run", ["no-prompt", "prompt-handled-by-an-extension"])
async def test_an_answer_that_failed_in_an_earlier_run_does_not_decide(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    mode: str,
    this_run: str,
) -> None:
    """Review round 2 (cross F1): the session's last answer failed in an EARLIER run.

    The first run saves a session that ends on an exhausted ``502``. This run
    continues it and makes no model request: with no prompt, or with a prompt
    an extension's ``input`` hook handles. Nothing it did failed, so it exits 0
    in both modes, writes nothing to stderr, and text mode prints nothing (the
    old failure is not an answer). pi exits 1 here in text mode, reading the
    session's last message whoever wrote it; so did ``-p`` before, printing
    the old error, and ``--mode json`` on the first #410 commit, silently.

    Two inputs, because a rule "a run with no prompt cannot fail" passes the
    first and not the second.
    """

    (agent / "settings.json").write_text(json.dumps({"retry": {"maxRetries": 1, "baseDelayMs": 1}}), "utf-8")
    base = [
        "--session-dir",
        str(tmp_path / "sessions"),
        "--provider",
        "anthropic",
        "--model",
        _MODEL["anthropic"],
    ]
    endpoints.model_plan = ["502"] * 10
    rc, _out, err = await _cli([*base, "-p", "hi"], monkeypatch, capsys)
    assert rc == 1 and "upstream bad gateway" in err  # the earlier run, saved
    requests = len(endpoints.model_auth)

    marker = tmp_path / "handled.txt"
    extension = tmp_path / "handled_by_extension.py"
    extension.write_text(_HANDLED_BY_EXTENSION.format(marker=str(marker)), encoding="utf-8")
    flags = ["--mode", "json"] if mode == "json" else ["-p"]
    prompt = ["ping"] if this_run == "prompt-handled-by-an-extension" else []
    rc, out, err = await _cli([*base, "-e", str(extension), "--continue", *flags, *prompt], monkeypatch, capsys)

    assert len(endpoints.model_auth) == requests  # this run asked the model nothing
    assert marker.exists() == bool(prompt)
    assert rc == 0, (rc, err)
    assert err == ""
    if mode == "json":
        assert [e.get("type") for e in _json_lines(out) if e.get("type") in ("message_end", "turn_end")] == []
    else:
        assert out == ""


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_an_earlier_runs_failed_answer_is_neither_the_verdict_nor_printed(
    capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """Review round 2: the same rule one level down, with a partial answer in it.

    The session already ends on an ``aborted`` answer that carries text when the
    run starts, and the run sends no prompt. It exits 0, and text mode prints
    nothing - the old answer is neither this run's failure nor an answer to
    print (before #410 ``-p`` printed ``Request aborted`` and exited 1).
    """

    harness = _new_harness(_scripted_stream(["ok"]))
    harness.state.messages.append(UserMessage(content=[TextContent(text="yesterday")]))
    harness.state.messages.append(AssistantMessage(content=[TextContent(text="half an ans")], stop_reason="aborted"))
    exit_code = await run_print_mode(
        _new_runtime(harness), mode=mode, messages=[], initial_message=None  # type: ignore[arg-type]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("mode", ["json", "text"])
async def test_a_prompt_whose_retries_ran_out_does_not_decide_when_a_later_one_succeeds(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Review round 2 (cross M2): an exhausted retry, then a prompt that succeeds.

    The ``error-then-ok`` row above uses an error that is not retried, so no
    row had an ``auto_retry_end`` with ``success: false`` followed by a
    successful last answer. A rule that remembered the issue's symptom (any
    exhausted retry) passed every row; the last answer decides, so this exits 0.
    """

    (agent / "settings.json").write_text(json.dumps({"retry": {"maxRetries": 1, "baseDelayMs": 1}}), "utf-8")
    endpoints.model_plan = ["502", "502"]  # the first prompt and its one retry; then 200
    flags = ["--mode", "json"] if mode == "json" else ["-p"]
    rc, out, err = await _cli(
        ["--no-session", "--provider", "anthropic", "--model", _MODEL["anthropic"], *flags, "first", "second"],
        monkeypatch,
        capsys,
    )
    assert len(endpoints.model_auth) == 3
    assert rc == 0, (rc, err)
    assert err == ""
    if mode == "text":
        assert out == "hello\n"
        return
    lines = _json_lines(out)
    assert [(e["success"], e["attempt"]) for e in lines if e.get("type") == "auto_retry_end"] == [(False, 1)]
    stops = [
        e["message"]["stop_reason"]
        for e in lines
        if e.get("type") == "message_end" and e["message"].get("role") == "assistant"
    ]
    assert stops[0] == "error" and stops[-1] == "end_turn", stops


# === the delegation parent: a real --mode json child ==========================


async def test_a_delegated_json_child_whose_model_errored_reports_exit_1(tmp_path: Path) -> None:
    """The in-tree consumer of the JSON child's exit code, end to end.

    ``PrintChannel`` spawns the repo's real ``--mode json`` print child
    (``tests/print_mode_child.py --errored``). Before #410 the child exited 0
    and only the stream said it failed; now the exit code agrees. The result
    is otherwise what it was: ``status="error"``, the child's own error as the
    summary - and no ``--- stderr ---`` section in ``details``, because JSON
    mode writes nothing to stderr for a failed answer (ADR-0268 §3).
    """

    script = tmp_path / "print_child.py"
    script.write_text(CHILD_SOURCE, encoding="utf-8")
    channel = _stub_channel(
        "",
        argv=[sys.executable, str(script), "--errored"],
        env=_hermetic_env(tmp_path),
    )
    result = await channel.run(_plan(tmp_path))
    assert result.exit_code == 1, result
    assert result.ok is False
    assert result.status == "error"
    assert result.stop_reason == "error"
    assert result.summary == "the provider said no"
    assert "--- stderr ---" not in (result.details or ""), result.details
