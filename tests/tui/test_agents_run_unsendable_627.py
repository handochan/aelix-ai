"""#627 — ``/agents run`` refuses a task holding a NUL or half of a character
before it resolves the profile, so a project profile's confirmation is never
asked for a task that cannot run.

The runtime refuses such a task too (``runtime.spawn``), but only after this
handler has resolved the profile and, for a project profile, asked the human
to confirm it: measured by the round-1 review on ``1fb5d2ae``, one dialog
answered for nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tui.test_agents_run_command import _Bench, _FakeRuntime, _ok_result, _ScriptedUI

BAD = {"nul": "\x00", "lone surrogate": "\ud83d"}


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_a_task_that_cannot_be_sent_is_refused_before_the_project_dialog(
    tmp_path: Path, kind: str
) -> None:
    bench = _Bench(tmp_path)
    runtime = bench.bind(_FakeRuntime(result=_ok_result(), project_scoped={"reviewer"}))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))

    out = await bench.run(f"/agents run reviewer keep{BAD[kind]}going")

    assert ui.calls == [], "no dialog"
    assert runtime.resolve_calls == [], "the profile is not even looked up"
    assert runtime.spawn_calls == []
    holds = "a NUL character (U+0000)" if kind == "nul" else "half of a character"
    assert f"agents run failed: The task holds {holds}" in out
    assert BAD[kind] not in out


async def test_a_normal_task_still_reaches_the_project_dialog(tmp_path: Path) -> None:
    bench = _Bench(tmp_path)
    runtime = bench.bind(_FakeRuntime(result=_ok_result(), project_scoped={"reviewer"}))
    ui = bench.bind_ui(_ScriptedUI(["Run this profile"]))

    await bench.run("/agents run reviewer 검토 🙂 해 주세요")

    assert len(ui.calls) == 1
    assert runtime.spawn_calls == [("reviewer", "검토 🙂 해 주세요")]
