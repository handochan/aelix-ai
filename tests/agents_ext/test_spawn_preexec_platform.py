"""The delegation spawn must not pass ``preexec_fn`` on Windows (#200).

``pdeathsig`` has been inert off Linux since it was written, so the bug was
never about what the hook does — it was that ``subprocess`` refuses the
*argument*: "preexec_fn is not supported on Windows platforms", raised in
``Popen.__init__`` before a child exists. Both channels wrap their spawn in
``except Exception`` and convert a failure into an error envelope, so this
never looked like a crash. Every delegation just came back ``error``, and 52
of the 238 remaining ``windows-latest`` failures were downstream of it.

No ``skipif``: the branch is a ``sys.platform`` read, so patching it drives
both sides on any runner. A case that only runs on the platform we do not have
is not a regression guard — same reasoning as
``tests/cli/test_stdio_encoding_win32.py``.
"""

from __future__ import annotations

import contextlib

import pytest
from aelix_agents import reaper
from aelix_agents.reaper import pdeathsig, pdeathsig_preexec
from aelix_coding_agent.rpc import rpc_client


class _StopSpawn(Exception):
    """Abort ``start()`` at the spawn boundary — nothing past it is under test."""


def test_windows_gets_no_preexec_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reaper.sys, "platform", "win32", raising=True)

    assert pdeathsig_preexec() is None


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_still_gets_the_hook(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """darwin too, even though ``pdeathsig`` no-ops there.

    The hook is cheap and self-guarding, and narrowing this to linux would
    make the seam a second platform policy competing with the one already
    inside ``pdeathsig``. One decision, one place.
    """

    monkeypatch.setattr(reaper.sys, "platform", platform, raising=True)

    assert pdeathsig_preexec() is pdeathsig


@pytest.mark.parametrize(
    ("preexec", "expected"),
    [(None, False), (pdeathsig, True)],
    ids=["windows-omits-the-key", "posix-passes-the-hook"],
)
async def test_the_rpc_spawn_only_passes_preexec_fn_when_there_is_one(
    preexec: object, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``None`` must not be passed as None — it must not be passed at all.

    ``rpc_client`` builds its kwargs conditionally (``:395``), so the windows
    branch drops the key entirely. Both forms happen to be accepted by
    subprocess today; pinning the omission keeps the rpc path safe if that
    check is ever flattened into an unconditional assignment.

    Spies on the spawn rather than asserting on the options object: reading
    back a dataclass default would pass whatever the call site does, which is
    the defect this file exists to catch, not a test of it.
    """

    seen: dict[str, object] = {}

    async def _spy(*_argv: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise _StopSpawn

    monkeypatch.setattr(rpc_client.asyncio, "create_subprocess_exec", _spy)
    client = rpc_client.RpcClient(
        rpc_client.RpcClientOptions(
            argv=["aelix", "--mode", "rpc"],
            preexec_fn=preexec,  # type: ignore[arg-type]
        )
    )

    with contextlib.suppress(_StopSpawn):
        await client.start()

    assert ("preexec_fn" in seen) is expected
