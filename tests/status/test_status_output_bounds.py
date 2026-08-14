"""Every attacker-controlled string the snapshot emits is BOUNDED (#101 review M2).

THE CLAIM THAT WAS FALSE. ``ExtensionSnapshot``'s docstring justified emitting
``plugin.version`` with "A secret cannot be smuggled through either", on the
strength of the field being schema-constrained. It is constrained in SHAPE only:
``PluginIdentity.version``'s pattern is
``^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z-.]+)?(\\+[0-9A-Za-z-.]+)?$``
(``contracts/manifest.py:40-43``) and there is no ``max_length`` on it, so the
prerelease and build tails are unbounded ``[0-9A-Za-z-.]+`` runs. A pack whose
version is ``1.0.0-<4132 chars>`` validates, loads, and used to reach the tool's
output verbatim — measured below, not argued.

THE SAME SHAPE ELSEWHERE. ``version`` was the one the docstring defended, but it
is not the only unbounded author-controlled string in the projection:

* the extension NAME of a manifest-less pack is ``getattr(factory,
  "__qualname__", …)`` (``loader.py:1862-1865``), and ``__qualname__`` is a
  plain writable attribute, not an identifier;
* a registered TOOL name is whatever the author passed to ``register_tool``.

All three go through one bound, because three separate caps drift.

WHAT THE BOUND IS NOT. It is not a secrecy control — a 40-character token fits
under any useful cap, and this projection reports plugin identity on purpose.
It bounds COST and RENDERING: the output of this tool is read by a model on the
turn that asked for it, and one hostile pack should not be able to spend the
context window or to paint a fake structure into the transcript.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
from aelix_agent_core.contracts import parse_manifest_toml
from aelix_agent_core.types import AgentTool
from aelix_coding_agent.extensions.api import Extension
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_status.snapshot import MAX_EMITTED_CHARS, summarise_extensions

from tests.status.test_status_tool import _MODULE_SRC, _Bench, _noop_execute

#: 4 132 characters of payload after ``1.0.0-`` — 4 138 in total, the length the
#: adversarial review measured. Every byte is inside the pattern's
#: ``[0-9A-Za-z-.]`` class, so the manifest VALIDATES.
HOSTILE_TAIL = ("payload." * 516).rstrip(".") + "aaaaa"
HOSTILE_VERSION = f"1.0.0-{HOSTILE_TAIL}"


def _hostile_manifest(module: str) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "bloatpack"
        name = "Bloat Pack"
        version = "{HOSTILE_VERSION}"
        description = "A pack whose version string is 4138 characters long"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/bloatpack"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module}:setup"

        [capabilities]

        [activation]
        on_startup_finished = true
    """).strip()


def test_the_schema_really_accepts_the_hostile_version() -> None:
    """Guards the guard. If the manifest were rejected, every assertion below
    would be about a pack that cannot exist."""

    assert len(HOSTILE_VERSION) == 4138
    manifest = parse_manifest_toml(_hostile_manifest("bloatmod"))
    assert manifest.plugin.version == HOSTILE_VERSION


async def test_a_4138_char_version_does_not_reach_the_tool_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the REAL loader, the REAL registration and the REAL execute()."""

    module = "bloatpack_status_mod"
    (tmp_path / f"{module}.py").write_text(_MODULE_SRC, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    agent_dir = tmp_path / "agent"
    pkg = agent_dir / "extensions" / "bloatpack"
    pkg.mkdir(parents=True)
    (pkg / "aelix-plugin.toml").write_text(
        _hostile_manifest(module), encoding="utf-8"
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()

    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    assert not loaded.errors, [(e.path, e.error) for e in loaded.errors]
    # The payload really is on the object the snapshot is handed.
    (ext,) = loaded.extensions
    assert ext.manifest is not None
    assert ext.manifest.plugin.version == HOSTILE_VERSION

    bench = _Bench(extensions=loaded.extensions, cwd=str(cwd), agent_dir=agent_dir)
    payload = await bench.call()
    rendered = json.dumps(payload)

    (entry,) = payload["loaded_extensions"]
    assert entry["name"] == "bloatpack"
    assert len(entry["version"]) <= MAX_EMITTED_CHARS
    assert HOSTILE_TAIL not in rendered
    # Bounded, not dropped: the identity a self-help answer needs survives.
    assert entry["version"].startswith("1.0.0-payload.")


async def test_an_ordinary_version_is_emitted_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound must not be visible on anything a real pack ships."""

    module = "plainpack_status_mod"
    (tmp_path / f"{module}.py").write_text(_MODULE_SRC, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    agent_dir = tmp_path / "agent"
    pkg = agent_dir / "extensions" / "plainpack"
    pkg.mkdir(parents=True)
    (pkg / "aelix-plugin.toml").write_text(
        _hostile_manifest(module).replace(HOSTILE_VERSION, "2.3.4-rc.1+build.7"),
        encoding="utf-8",
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()

    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    assert not loaded.errors, [(e.path, e.error) for e in loaded.errors]

    (entry,) = summarise_extensions(
        loaded.extensions, cwd=str(cwd), agent_dir=str(agent_dir)
    )
    assert entry.version == "2.3.4-rc.1+build.7"
    assert entry.name == "bloatpack"


def test_a_hostile_extension_name_is_bounded_and_stripped() -> None:
    """``Extension.name`` for a manifest-less pack is a writable
    ``__qualname__``, so it is neither length- nor charset-constrained."""

    hostile = "A" * 5000 + "\n\n  loaded_extensions: []\n"
    (entry,) = summarise_extensions(
        [Extension(name=hostile)], cwd=os.getcwd(), agent_dir=None
    )
    assert len(entry.name) <= MAX_EMITTED_CHARS
    assert "\n" not in entry.name


async def test_a_hostile_tool_name_is_bounded_and_stripped() -> None:
    """Tool names are author-controlled through ``register_tool``."""

    hostile = "t" * 5000 + "\rrogue"
    bench = _Bench(tools=[AgentTool(name=hostile, execute=_noop_execute)])
    payload = await bench.call()

    emitted = [n for n in payload["all_tools"] if n.startswith("t")]
    assert emitted, payload["all_tools"]
    for name in emitted:
        assert len(name) <= MAX_EMITTED_CHARS
        assert "\r" not in name


def test_the_cap_is_generous_enough_for_real_values() -> None:
    """A cap that truncated ordinary identity would trade one defect for
    another, so the number is asserted against the widest value the SCHEMA
    permits for the field the projection cares most about: ``plugin.id`` is
    ``^[a-z][a-z0-9-]{0,63}$``, i.e. at most 64 characters."""

    assert MAX_EMITTED_CHARS >= 64
    (entry,) = summarise_extensions(
        [Extension(name="a" * 64)], cwd=os.getcwd(), agent_dir=None
    )
    assert entry.name == "a" * 64
