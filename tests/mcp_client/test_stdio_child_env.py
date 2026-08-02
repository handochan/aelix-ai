"""A manifest's ``env`` adds variables — it must not widen what a child inherits.

``_open_transport`` used to build ``{**os.environ, **env} if env else None``, so
the presence of an ``env`` table — not its contents — decided how much of the
parent environment a spawned program received. Measured at the real spawn, on
this box:

    manifest declares NO env        -> child sees   6 vars
    manifest declares ONE cosmetic  -> child sees 133 vars, incl. GITHUB_TOKEN

The trap is that the reason to write ``env`` is trivial. A line as innocuous as
``env = { NOTES_LOG = "debug" }`` reads as "add one variable" to every author
and reviewer, while actually handing an npx-downloaded program every provider
API key the user has exported. The manifest — the artifact whose whole promise
is that you can read it and know what a pack does — was what hid it.

MEASURED AT THE CHILD, NOT THE CALL SITE. The server writes its own
``os.environ`` to a marker file, so the only producer of the environment under
test is the shipped ``McpServerConnection`` path. A test that asserted on
locally-rebuilt parameters would pass while production still leaked — this repo
has shipped that mistake before (``prompt(images=)``: 22 green forwarding tests
over a feature broken end to end).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from aelix_agent_core.contracts import McpServerContrib
from aelix_coding_agent.mcp import McpServerConnection

# Present only in the parent. Reaching the child means the whole environment
# was handed over.
_SECRET = "AELIX_TEST_PARENT_ONLY_API_KEY"

_DUMPER = """
import json, os, sys
open({marker!r}, "w").write(json.dumps(sorted(os.environ)))
sys.exit(0)
"""


async def _child_env(
    tmp_path: Path, declared: dict[str, str] | None
) -> list[str]:
    """Spawn a stdio server through the real connection; return its environ."""

    marker = tmp_path / f"env-{'declared' if declared else 'bare'}.json"
    script = tmp_path / f"server-{marker.stem}.py"
    script.write_text(_DUMPER.format(marker=str(marker)), encoding="utf-8")

    contrib = McpServerContrib(
        name="probe",
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
        env=declared or {},
    )
    conn = McpServerConnection(contrib)
    # The child exits immediately, so the handshake fails. That is fine and is
    # the point: it was spawned by the production path.
    with pytest.raises(Exception):  # noqa: B017,PT011 — any connect failure
        await asyncio.wait_for(conn.connect(), timeout=30)
    await conn.disconnect()

    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.exists(), "the child never ran, so nothing was measured"
    return json.loads(marker.read_text(encoding="utf-8"))


@pytest.fixture
def parent_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(_SECRET, "sk-should-never-reach-the-child")
    return _SECRET


@pytest.mark.asyncio
async def test_declaring_env_does_not_hand_over_the_parent_environment(
    tmp_path: Path, parent_secret: str
) -> None:
    child = await _child_env(tmp_path, {"NOTES_LOG": "debug"})
    assert parent_secret not in child, (
        "a manifest that declares one cosmetic variable received the parent's "
        f"whole environment ({len(child)} vars)"
    )


@pytest.mark.asyncio
async def test_declaring_env_adds_exactly_what_it_declares(
    tmp_path: Path, parent_secret: str
) -> None:
    bare = await _child_env(tmp_path, None)
    declared = await _child_env(tmp_path, {"NOTES_LOG": "debug"})
    assert set(declared) - set(bare) == {"NOTES_LOG"}
    assert set(bare) - set(declared) == set()


@pytest.mark.asyncio
async def test_a_declared_variable_still_reaches_the_child(
    tmp_path: Path, parent_secret: str
) -> None:
    """The containment must not cost the feature it is containing."""

    assert "NOTES_LOG" in await _child_env(tmp_path, {"NOTES_LOG": "debug"})


@pytest.mark.asyncio
async def test_a_bare_manifest_gets_only_the_sdk_default_set(
    tmp_path: Path, parent_secret: str
) -> None:
    """Unchanged behaviour, pinned so the two branches cannot drift apart.

    ``None`` lets the SDK apply ``get_default_environment()`` itself; the
    declared branch applies the same function explicitly. If a future edit
    changes one, this and the delta test above fail together.
    """

    child = await _child_env(tmp_path, None)
    assert parent_secret not in child
    assert "PATH" in child
    assert len(child) < 20, f"expected the SDK's small set, got {len(child)}"


@pytest.mark.asyncio
async def test_a_declared_key_overrides_the_default_of_the_same_name(
    tmp_path: Path, parent_secret: str
) -> None:
    """Overlay order: the manifest wins on a collision.

    A server that needs its own PATH must be able to say so — the point of the
    fix is the inheritance surface, not forbidding overrides.
    """

    child = await _child_env(tmp_path, {"PATH": "/nowhere"})
    assert "PATH" in child
    assert parent_secret not in child


@pytest.mark.asyncio
async def test_the_parent_environment_is_genuinely_larger(
    tmp_path: Path, parent_secret: str
) -> None:
    """Guards against a vacuous suite.

    If the test runner's own environment were as small as the SDK default set,
    every assertion above would hold no matter what the code did.
    """

    assert len(os.environ) > 30, (
        "the parent environment is too small for the leak tests to mean "
        f"anything ({len(os.environ)} vars)"
    )
