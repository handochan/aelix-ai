"""``repr(Extension)`` leaked plugin-supplied secrets (#101).

SAME DEFECT, ONE LAYER UP FROM ADR-0206. That review hardened
``_ManifestEntry.__repr__`` (``loader.py:1197-1201``) because the carrier flowed into
``str(entry)`` on the load-error path and the default dataclass repr expanded the
whole ``PluginManifest`` — including ``contributes.mcp_servers[].env``, which is
where a plugin puts its API tokens. The ``Extension`` that carrier PRODUCES kept
the generated repr and the same manifest reference, so the redaction stopped one
object short.

MEASURED BEFORE THE FIX, on a real pack loaded through the real
``discover_and_load_extensions``::

    len(repr(ext))                              -> 1250
    "ghp_PLANTED_SECRET_abc123" in repr(ext)    -> True

WHY IT MATTERS MORE NOW. #101 adds a tool the MODEL calls to enumerate loaded
extensions. That tool never reprs an ``Extension`` (see
``aelix_status/snapshot.py``), but "the redaction is in the caller" is the
arrangement that produced this bug in the first place: ``_ManifestEntry`` was
redacted precisely because a caller could not be trusted to remember. An object
holding secrets should not be printable.

WHAT DEPENDED ON THE VERBOSE REPR: nothing. Searched for ``repr()`` /
``str()`` / f-string interpolation of an ``Extension`` across ``packages/`` and
``tests/`` before changing it; the only hits were ``_ManifestEntry`` (already
redacted, ``tests/extensions/test_hooks_gate_manifest.py:273``) and
``resolve_entry_point_manifest``'s result (``test_ep_manifest.py:1117``). The
loader's own error paths label entries by plugin id or by path, never by the
loaded ``Extension``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.api import Extension
from aelix_coding_agent.extensions.loader import discover_and_load_extensions

SECRET = "ghp_PLANTED_SECRET_abc123"

_MODULE_SRC = "def setup(aelix):\n    return None\n"


def _manifest_toml(module: str) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "leakypack"
        name = "Leaky Pack"
        version = "0.1.0"
        description = "Ships an MCP server whose env holds a credential"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/leakypack"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module}:setup"

        [capabilities]
        shell_exec = true

        [activation]
        on_startup_finished = true

        [[contributes.mcp_servers]]
        name = "secretsrv"
        transport = "stdio"
        command = "node"
        [contributes.mcp_servers.env]
        GITHUB_TOKEN = "{SECRET}"
    """).strip()


async def _load_leaky_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Extension:
    """Load a real pack whose parsed manifest really carries the credential.

    Deliberately NOT a hand-built ``Extension(manifest=...)``: the assertion is
    about what the shipped discovery path hands the rest of the process, and a
    fixture that assembles the object itself could not catch the loader wiring
    the manifest onto a different field, or not at all.
    """

    agent_dir = tmp_path / "agent"
    pkg = agent_dir / "extensions" / "leakypack"
    pkg.mkdir(parents=True)
    module = "leakypack_repr_mod"
    (tmp_path / f"{module}.py").write_text(_MODULE_SRC, encoding="utf-8")
    (pkg / "aelix-plugin.toml").write_text(_manifest_toml(module), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded = await discover_and_load_extensions(
        [], cwd=tmp_path / "proj", agent_dir=agent_dir
    )
    assert not loaded.errors, [(e.path, e.error) for e in loaded.errors]
    (ext,) = [e for e in loaded.extensions if e.name == "leakypack"]
    return ext


async def test_extension_repr_does_not_leak_a_manifest_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ext = await _load_leaky_extension(tmp_path, monkeypatch)

    # The secret really is reachable through the object — otherwise the
    # assertion below would pass on an Extension that simply lost its manifest.
    assert ext.manifest is not None
    assert ext.manifest.contributes.mcp_servers[0].env["GITHUB_TOKEN"] == SECRET

    rendered = repr(ext)
    assert SECRET not in rendered, rendered
    assert "PluginManifest(" not in rendered, rendered
    # Identity must survive: a repr that redacts by saying nothing makes every
    # log line and every debugger frame useless, which is how a redaction gets
    # reverted. Same shape as ``_ManifestEntry.__repr__``.
    assert "leakypack" in rendered


async def test_extension_repr_is_readable_without_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain ``.py`` extension still names itself.

    The manifest-less tiers are the majority of what loads, and their
    ``Extension.name`` is the only identity they have.
    """

    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "extensions" / "plainext.py").write_text(
        _MODULE_SRC, encoding="utf-8"
    )

    loaded = await discover_and_load_extensions(
        [], cwd=tmp_path / "proj", agent_dir=agent_dir
    )
    assert not loaded.errors, [(e.path, e.error) for e in loaded.errors]
    (ext,) = loaded.extensions

    rendered = repr(ext)
    assert "plainext.py" in rendered
    assert rendered.startswith("Extension(")
