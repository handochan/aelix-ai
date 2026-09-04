"""What a REAL child actually does with the authority we gave it — finding B10.

Every other test in this package asserts on a string: that a flag appears in a
list, that a mode round-trips through ``parse_args``, that the intersection
computed the set we expected. This file is the one that spawns
``-m aelix_coding_agent`` for real, lets a model ask it to write a file, and then
looks on disk.

That distinction is the whole point of B10. ``cli/args.py`` swallows an
unrecognised ``--key value`` pair into ``parsed.unknown_flags`` with NO
diagnostic, so a renamed or mistyped authority flag produces a completely green
suite and a child with wider authority than anyone approved. A test that only
inspects argv cannot tell the difference; a test that watches for the file can.

HOW A REAL CHILD RUNS WITHOUT A PROVIDER. The child loads an extension
(``-e``, loader tier 3) that registers a custom wire-protocol adapter and a
models.json provider pointing at it. The "model" emits one ``write`` tool call
and then a final text turn, so the child executes a genuine tool call through
its genuine permission ladder — no network, no API key, no flakiness. The
profile's own ``model:``/``provider:`` fields select it, which means the argv
under test is the argv the spawner really builds.

SCOPE, STATED PLAINLY. Two of the child's three authority levers are observed
here end to end: the TOOL GRANT (``--tools``, §(l)) and the ABSENCE of a tool.
The third — ``--permission-mode`` (§(e)/§(i)) — is asserted one hop short,
against the real ``PermissionExtension`` seeded from the real parsed argv.

The hop this file was written around — product-core PARSING
``parsed.permission_mode`` and then never consuming it — has since been closed
by WS-E's ``cli/entry.py`` hunk, so
:func:`test_entry_consumes_the_permission_mode_flag` now pins the consumption
(and the child-only headless floor that rides with it) instead of recording its
absence. The remaining follow-up is the one that test always named: promote
:func:`test_the_gate_the_child_runs_realizes_the_argv_posture` into a full
end-to-end write assertion against a real child.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agents.print_channel import PrintChannel, SpawnPlan, build_child_env
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.env_sandbox import child_env

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="real-child spawn semantics are POSIX"
)

_STUB_API = "stub-api"
_STUB_PROVIDER = "stubprov"
_STUB_MODEL = "stubmodel"

# The extension the child loads. It registers a wire-protocol adapter whose
# "model" calls ``write`` once and then finishes, which is the smallest thing
# that exercises the child's real tool + permission ladder.
_STUB_EXTENSION = textwrap.dedent(
    '''
    """Test-only provider stub for the realized-posture suite."""

    from __future__ import annotations

    import os
    from typing import Any

    from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantStartEvent,
        Model,
        ModelCost,
    )
    from aelix_coding_agent.model_registry import ProviderConfigInput

    TARGET = os.environ["PROBE_WRITE_TARGET"]


    def setup(api: Any) -> None:
        turns = {"n": 0}

        async def stream(model, context, options):
            turns["n"] += 1
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            if turns["n"] == 1:
                yield AssistantEndEvent(
                    message=AssistantMessage(
                        content=[
                            ToolCallContent(
                                tool_call_id="probe-1",
                                tool_name="write",
                                input={"path": TARGET, "content": "written"},
                            )
                        ],
                        stop_reason="tool_use",
                        usage={"input": 11, "output": 22, "total_tokens": 33},
                        provider="stubprov",
                        model="stubmodel",
                    )
                )
            else:
                yield AssistantEndEvent(
                    message=AssistantMessage(
                        content=[TextContent(text="probe-done")],
                        stop_reason="end_turn",
                        usage={"input": 5, "output": 6, "total_tokens": 44},
                        provider="stubprov",
                        model="stubmodel",
                    )
                )

        api.register_api_adapter("stub-api", stream)
        api.register_provider(
            "stubprov",
            ProviderConfigInput(
                name="Stub",
                api_key="STUB_KEY",
                models={
                    "stubmodel": Model(
                        id="stubmodel",
                        provider="stubprov",
                        api="stub-api",
                        base_url="http://127.0.0.1:1/v1",
                        context_window=100000,
                        max_tokens=4096,
                        cost=ModelCost(
                            input=1.0, output=2.0, cache_read=0.1, cache_write=0.2
                        ),
                    )
                },
            ),
        )
    '''
)

_MODELS_JSON = {
    "providers": {
        _STUB_PROVIDER: {
            "baseUrl": "http://127.0.0.1:1/v1",
            "apiKey": "STUB_KEY",
            "api": _STUB_API,
            "models": [
                {
                    "id": _STUB_MODEL,
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {
                        "input": 1.0,
                        "output": 2.0,
                        "cacheRead": 0.5,
                        "cacheWrite": 0.25,
                    },
                    "contextWindow": 100000,
                    "maxTokens": 4096,
                }
            ],
        }
    }
}


class _Fixture:
    """One hermetic child: its home, its agent dir, its cwd and its target."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.agent_dir = self.home / "agent"
        self.cwd = tmp_path / "repo"
        for path in (self.home / ".config", self.agent_dir, self.cwd):
            path.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "models.json").write_text(
            json.dumps(_MODELS_JSON), encoding="utf-8"
        )
        self.extension = tmp_path / "stub_provider_ext.py"
        self.extension.write_text(_STUB_EXTENSION, encoding="utf-8")
        self.target = self.cwd / "written-by-the-child.txt"

    def env(self) -> dict[str, str]:
        """No credentials, no shared state (finding I10).

        ``tests/conftest.py``'s download guard is an in-process
        ``monkeypatch.setattr`` and does not reach a child interpreter, so every
        value a real child needs is spelled out here.
        """

        return child_env(
            self.home,
            XDG_CONFIG_HOME=str(self.home / ".config"),
            AELIX_CODING_AGENT_DIR=str(self.agent_dir),
            PI_OFFLINE="1",
            STUB_KEY="not-a-real-key",
            PROBE_WRITE_TARGET=str(self.target),
        )

    def profile(self, *, tools: tuple[str, ...] | None) -> AgentProfile:
        return AgentProfile(
            name="scribe",
            description="Writes a file when it is allowed to.",
            body="You are a scribe.",
            file_path=str(self.root / "scribe.md"),
            scope="user",
            tools=tools,
            model=_STUB_MODEL,
            provider=_STUB_PROVIDER,
            extensions=(str(self.extension),),
        )

    async def run(
        self, *, tools: tuple[str, ...] | None, mode: PermissionMode
    ) -> Any:
        profile = self.profile(tools=tools)
        env = self.env()
        channel = PrintChannel(
            grace=1.0, env_builder=lambda p: build_child_env(p, base=env)
        )
        plan = SpawnPlan(
            id="sub-realized",
            resolved=ResolvedProfile(
                name=profile.name,
                profile=profile,
                source_path=profile.file_path,
                scope=profile.scope,
            ),
            task="write the file",
            cwd=str(self.cwd),
            parent_cwd=str(self.cwd),
            permission_mode=mode,
            parent_tools=("read", "write", "ls"),
            timeout_ms=90_000,
        )
        return await asyncio.wait_for(channel.run(plan), 120)


# === The tool grant, realized (§(l)) ==========================================


async def test_a_real_child_writes_when_its_grant_carries_write(
    tmp_path: Path,
) -> None:
    """The CONTROL, and it is not optional.

    Without it the denial test below could pass for any reason at all — a child
    that never started, a model that never called the tool, a typo in the target
    path. This proves the whole apparatus reaches a real write.
    """

    fixture = _Fixture(tmp_path)
    result = await fixture.run(tools=("read", "write"), mode=PermissionMode.YOLO)
    assert fixture.target.exists(), result.summary
    assert fixture.target.read_text(encoding="utf-8") == "written"
    assert result.ok is True
    assert result.summary == "probe-done"
    assert result.usage.turns == 2


async def test_a_real_child_denied_the_write_tool_cannot_write(
    tmp_path: Path,
) -> None:
    """The intersection is STRUCTURAL — the child does not have the tool at all.

    ``narrow_tools`` clamps the profile's request to the parent's live grant and
    the result is rendered into ``--tools``, so this is not a gate the child
    could be talked out of: the write tool is not in the process.
    """

    fixture = _Fixture(tmp_path)
    result = await fixture.run(tools=("read",), mode=PermissionMode.YOLO)
    assert not fixture.target.exists()
    # The run still ENDS, with an envelope, rather than hanging or crashing.
    assert result.summary == "probe-done"
    assert result.dropped_tools == ()


async def test_a_real_child_with_no_tools_at_all_still_returns_an_envelope(
    tmp_path: Path,
) -> None:
    """``tools: []`` → ``--no-tools``, never ``--tools ''``.

    The inversion this guards against is total: ``--tools ''`` parses to ``[]``,
    which ``_resolve_active_tools`` reads as falsy → ``None`` → EVERY tool
    active. A child that was meant to have nothing would have everything.
    """

    fixture = _Fixture(tmp_path)
    result = await fixture.run(tools=(), mode=PermissionMode.YOLO)
    assert not fixture.target.exists()
    assert result.summary == "probe-done"


async def test_the_child_command_line_is_accepted_by_a_real_aelix(
    tmp_path: Path,
) -> None:
    """The B10 end-to-end check: no token on the spawner's argv is rejected.

    A real ``-m aelix_coding_agent`` parsed everything we sent it, built a
    harness, ran two turns and exited 0. Any flag we misspelled would either
    have errored the child or been silently ignored — and the ``--tools``
    assertions above would still have passed, which is precisely why this one
    exists alongside them.
    """

    fixture = _Fixture(tmp_path)
    result = await fixture.run(tools=("read", "write"), mode=PermissionMode.YOLO)
    assert result.exit_code == 0
    assert result.status == "ok"
    assert result.permission_mode == "yolo"
    assert result.usage.input == 16
    assert result.usage.tokens == 44


# === The permission mode, one hop short (§(e)/§(i)) ===========================


def _spawner_argv(tmp_path: Path, mode: PermissionMode) -> list[str]:
    from aelix_agents.print_channel import build_child_argv

    profile = _Fixture(tmp_path).profile(tools=("read", "write"))
    return build_child_argv(
        profile,
        prompt_path=str(tmp_path / "prompt.md"),
        task="write the file",
        permission_mode=mode,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
    )


def _write_event(path: str = "src/app.py") -> ToolCallHookEvent:
    return ToolCallHookEvent(tool_call_id="t1", tool_name="write", args={"path": path})


class _HeadlessCtx:
    """A child's context: no UI, and any prompt would hang on a missing terminal."""

    has_ui = False
    cwd = "/proj"

    class _UI:
        async def select(self, title: str, options: list[str]) -> str:
            raise AssertionError(f"a child must never prompt (title={title!r})")

    ui = _UI()


@pytest.mark.parametrize(
    ("mode", "blocked"),
    [
        (PermissionMode.PLAN, True),
        (PermissionMode.DEFAULT, True),
        (PermissionMode.YOLO, False),
    ],
)
async def test_the_gate_the_child_runs_realizes_the_argv_posture(
    tmp_path: Path, mode: PermissionMode, blocked: bool
) -> None:
    """Spawner argv → real ``parse_args`` → real ``PermissionExtension`` verdict.

    Everything in this chain is production code except the one assignment WS-E
    owns (see the module docstring), so this pins the SEMANTICS of the posture
    the spawner grants: a ``plan`` grant really does deny a write, and a ``yolo``
    grant really does not — which is what makes the flag worth carrying.

    ``headless_default="block"`` because that is how ``cli/entry.py`` will
    configure a delegated child (``subagent_depth() > 0``). Note it is the BELT,
    not the braces: finding B4 showed the AUTO_ACCEPT write branch returns above
    it, which is why the spawner-side clamp is the real guarantee.
    """

    parsed = parse_args(_spawner_argv(tmp_path, mode)[3:])
    assert parsed.permission_mode == mode.value
    child_gate = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode(parsed.permission_mode)),
        headless_default="block",
    )
    verdict = await child_gate._on_tool_call(_write_event(), _HeadlessCtx())  # type: ignore[arg-type]
    if blocked:
        assert isinstance(verdict, ToolCallResult)
        assert verdict.block is True
    else:
        assert verdict is None


def test_entry_consumes_the_permission_mode_flag() -> None:
    """THE HOP THAT WAS MISSING, now pinned in its closed form (WS-E, §(e)).

    This test was originally the negative tripwire: it recorded, as an executed
    fact, that ``cli/entry.py`` built a bare ``PermissionPosture()`` and never
    read ``parsed.permission_mode``, so the flag PARSED in a real child and
    changed nothing. It was written to fail the moment WS-E landed. It did, and
    this is the inverted form — strictly stronger, because the whole child
    authority guarantee is argv-shaped (finding B10) and a silent regression
    here would restore an auto-approving child with a green suite.

    Asserted against the source rather than by driving ``_async_main``: the two
    statements live inline in a 900-line coroutine that reaches the network, the
    filesystem and the TUI before it returns, and the SPELLING is what B10 is
    about. ``tests/cli/test_permission_mode_flag.py`` owns the parse side and
    :func:`test_the_gate_the_child_runs_realizes_the_argv_posture` above owns the
    semantics; this pins that the two are actually connected.
    """

    from aelix_coding_agent.cli import entry

    source = Path(entry.__file__).read_text(encoding="utf-8")
    # The clamp result the spawner computed reaches the child's live posture.
    assert "permission_posture.set(PermissionMode(parsed.permission_mode))" in source
    # And the CHILD-ONLY headless floor that rides with it — the belt to the
    # clamp's braces (§(e); it is NOT sufficient alone, finding B4).
    assert 'permission_ext.headless_default = "block"' in source
    assert "if subagent_depth() > 0:" in source
