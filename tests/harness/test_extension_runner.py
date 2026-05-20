"""Pi parity: ``ExtensionRunner.getRegisteredCommands()`` aggregation.

Sprint 6h₁ §G unit coverage for ``_extension_runner.py``.

Sprint 6h₁ W6 (P-224/P-229) — :meth:`ExtensionRunner.get_registered_commands`
now returns :class:`ResolvedCommand` with Pi disambiguation suffixes and
forwards the owning extension's :class:`ExtensionSourceInfo`.
"""

from __future__ import annotations

from aelix_agent_core.harness._extension_runner import ExtensionRunner, ResolvedCommand
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionSourceInfo,
    RegisteredCommand,
)


def _make_command(name: str, extension_name: str) -> RegisteredCommand:
    return RegisteredCommand(
        name=name,
        handler=lambda **_: None,
        description=f"desc {name}",
        source=extension_name,
    )


def test_empty_extension_list_yields_empty() -> None:
    runner = ExtensionRunner(extensions=[])
    assert runner.get_registered_commands() == []


def test_default_extensions_is_empty() -> None:
    """Default factory produces an empty list (Pi parity: empty runner)."""

    runner = ExtensionRunner()
    assert runner.get_registered_commands() == []


def test_single_extension_no_commands() -> None:
    ext = Extension(name="ext1")
    runner = ExtensionRunner(extensions=[ext])
    assert runner.get_registered_commands() == []


def test_aggregates_across_extensions() -> None:
    """Pi parity: insertion order across extensions + commands."""

    ext1 = Extension(name="ext1")
    ext1.commands["a"] = _make_command("a", "ext1")
    ext1.commands["b"] = _make_command("b", "ext1")
    ext2 = Extension(name="ext2")
    ext2.commands["c"] = _make_command("c", "ext2")
    runner = ExtensionRunner(extensions=[ext1, ext2])
    resolved = runner.get_registered_commands()
    assert [r.invocation_name for r in resolved] == ["a", "b", "c"]
    # Each ResolvedCommand wraps the original RegisteredCommand verbatim.
    assert [r.command.name for r in resolved] == ["a", "b", "c"]


def test_extension_runner_sees_runtime_command_registration() -> None:
    """Pi parity: ``getRegisteredCommands`` reflects mutations.

    Extension authors call ``api.register_command(...)`` post-load; the
    runner must surface those without a re-bind.
    """

    ext = Extension(name="dyn")
    runner = ExtensionRunner(extensions=[ext])
    assert runner.get_registered_commands() == []
    ext.commands["late"] = _make_command("late", "dyn")
    resolved = runner.get_registered_commands()
    assert [r.invocation_name for r in resolved] == ["late"]
    assert resolved[0].command.name == "late"


# === Sprint 6h₁ W6 — P-224 disambiguation regression ==========================


def test_colliding_command_names_get_pi_disambiguation_suffix() -> None:
    """Pi parity: ``runner.ts:512-551``.

    When two extensions register the same command name, the first occurrence
    keeps the bare name and the second gets ``{name}:1``; a third would
    receive ``{name}:2`` and so on.
    """

    ext1 = Extension(name="ext1")
    ext1.commands["deploy"] = _make_command("deploy", "ext1")
    ext2 = Extension(name="ext2")
    ext2.commands["deploy"] = _make_command("deploy", "ext2")
    runner = ExtensionRunner(extensions=[ext1, ext2])
    resolved = runner.get_registered_commands()
    assert [r.invocation_name for r in resolved] == ["deploy", "deploy:1"]
    # Original command name on the wrapped record is preserved verbatim
    # so callers that dispatch by ``RegisteredCommand.name`` see the
    # original (non-disambiguated) form.
    assert [r.command.name for r in resolved] == ["deploy", "deploy"]


def test_three_way_collision_uses_incrementing_suffix() -> None:
    """Pi parity: third + fourth colliding registrations get ``:2`` and ``:3``."""

    extensions: list[Extension] = []
    for i in range(4):
        ext = Extension(name=f"ext{i}")
        ext.commands["build"] = _make_command("build", f"ext{i}")
        extensions.append(ext)
    runner = ExtensionRunner(extensions=extensions)
    resolved = runner.get_registered_commands()
    assert [r.invocation_name for r in resolved] == [
        "build",
        "build:1",
        "build:2",
        "build:3",
    ]


def test_explicit_disambiguation_collision_skips_suffix() -> None:
    """Pi parity: ``while (invocation_name in taken): idx += 1``.

    When an extension registers the literal ``foo:1`` AND two other
    extensions register ``foo``, the second ``foo`` cannot reuse
    ``foo:1`` and must bump to ``foo:2``.
    """

    ext1 = Extension(name="ext1")
    ext1.commands["foo"] = _make_command("foo", "ext1")
    ext2 = Extension(name="ext2")
    ext2.commands["foo:1"] = _make_command("foo:1", "ext2")
    ext3 = Extension(name="ext3")
    ext3.commands["foo"] = _make_command("foo", "ext3")
    runner = ExtensionRunner(extensions=[ext1, ext2, ext3])
    resolved = runner.get_registered_commands()
    names = [r.invocation_name for r in resolved]
    # foo (ext1), foo:1 (ext2, unique), foo (ext3 → bumps past foo:1 → foo:2).
    assert names == ["foo", "foo:1", "foo:2"]


# === Sprint 6h₁ W6 — P-229 source_info forward regression =====================


def test_source_info_forwarded_from_owning_extension() -> None:
    """Pi parity (P-229): :class:`ExtensionSourceInfo` is attached at
    resolution time from the owning extension — :class:`RegisteredCommand`
    itself does not carry it.
    """

    src = ExtensionSourceInfo(
        source="project", base_dir="/p", identifier="cmd-pack"
    )
    ext = Extension(name="cmd-pack", source_info=src)
    ext.commands["hello"] = _make_command("hello", "cmd-pack")
    runner = ExtensionRunner(extensions=[ext])
    resolved = runner.get_registered_commands()
    assert len(resolved) == 1
    assert resolved[0].source_info is src


def test_source_info_none_when_extension_has_none() -> None:
    """Pi parity: when the owning extension has no :attr:`source_info`,
    the :class:`ResolvedCommand.source_info` is ``None``.
    """

    ext = Extension(name="no-src")
    ext.commands["hello"] = _make_command("hello", "no-src")
    runner = ExtensionRunner(extensions=[ext])
    resolved = runner.get_registered_commands()
    assert resolved[0].source_info is None


def test_resolved_command_is_dataclass_with_three_fields() -> None:
    """Closure: :class:`ResolvedCommand` shape ``{command, invocation_name, source_info}``."""

    fields = set(ResolvedCommand.__dataclass_fields__.keys())
    assert fields == {"command", "invocation_name", "source_info"}
