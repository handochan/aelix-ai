"""``aelix status`` — answer "what would run here?" without starting a session (#101).

AELIX-ORIGINAL, twice over. pi has no ``status`` subcommand (its verb list is
``install / remove / uninstall / update / list / config / auth``), and pi has no
runtime-introspection surface of any kind to adapt. What this shares with pi is
only the mechanism underneath — the loader and the trust resolver are both
pi-faithful ports.

WHY IT IS NOT A ``RuntimeSnapshot``. ADR-0218 left the seam for "a possible
``aelix status`` CLI adapter over it, not two implementations", and the shared
half is honoured: scope classification, the emitted-value bound, extension
summarisation and the fail-closed trust rule all come from ``aelix_status``. The
ENVELOPE deliberately differs, because three of :class:`~aelix_status.snapshot.RuntimeSnapshot`'s
fields have no session-free answer:

``mode``
    ``resolve_app_mode(parsed, stdin_is_tty)`` describes a launch. There is no
    launch here. Re-deriving it from ``sys.argv`` would be exactly the "second
    opinion that can disagree with the one the process actually acted on" that
    ``snapshot.py`` refuses for the same field.
``active_tools`` / ``all_tools``
    A tool registry exists only inside a harness. This command builds none.

Emitting them as ``""`` / ``[]`` was the obvious shortcut and it is the #120
defect in miniature: an empty tool list does not read as "unknown", it reads as
"none", and a caller that branches on it branches wrongly. So they are ABSENT
from the JSON, and ``session_only`` names them — an explicit "we did not look"
that a script can assert on.

WHAT IT DOES RUN. Trust resolution and extension discovery, through the same two
calls ``entry.py`` makes, with ``has_ui=False``:

* an undecided directory is **denied** without a prompt — the ``--print`` /
  ``--mode json`` rule (``project_trust.py``, pi ``project-trust.ts``). A status
  command that popped a trust dialog would be unusable in a script, and one that
  silently trusted would report a permission the next real launch will not grant;
* discovery then runs with ``no_project_local=not project_trusted``, so the
  extension list this prints is the list a launch in this directory would load.

That last point is the reason the command loads at all rather than merely listing
files: an extension that is present but fails to import is precisely what someone
runs ``status`` to find, and only the loader knows. Loading executes the
extension module — the same code the next ``aelix`` in this directory would
execute, under the same gate. ``--no-extensions`` skips discovery entirely.

Shaped after ``cli/docs.py`` and ``cli/extension_install.py``: ``-h``/``--help``
prints the usage, exit **2** for a usage error, diagnostics on stderr and product
output on stdout, and no ``rich``/``prompt_toolkit`` import so it runs without
the ``[tui]`` extra.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from aelix_status.snapshot import ExtensionSnapshot

#: Same value and same meaning as ``docs._EXIT_USAGE`` and
#: ``extension_install._EXIT_DIDNT_RUN``: the user got it wrong and we did not do
#: the thing they asked. A third subcommand inventing a third number is how a
#: script that wraps all of them stops being able to branch on any of them.
_EXIT_USAGE = 2

_USAGE = (
    "usage: aelix status [--json] [--no-extensions]\n"
    "  aelix status                     report what a launch in this directory would be\n"
    "  aelix status --json              the same report as a JSON object\n"
    "  aelix status --no-extensions     skip discovery (imports no extension code)\n"
)

#: The fields :class:`~aelix_status.snapshot.RuntimeSnapshot` carries that only a
#: running session can answer. Named rather than nulled — see the module
#: docstring.
SESSION_ONLY = ("mode", "active_tools", "all_tools")


def _placeholder(value: str | None) -> str:
    return value if value else "—"


def _render_text(report: dict[str, Any], stream: TextIO) -> None:
    print(f"aelix {report['version']}", file=stream)
    print(file=stream)
    rows = [
        ("working directory", report["cwd"]),
        ("agent directory", _placeholder(report["agent_dir"])),
        (
            "project trust",
            "trusted"
            if report["project_trusted"]
            else "NOT trusted — .aelix/ resources here are skipped",
        ),
        ("manifest API level", str(report["manifest_api_level"])),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}", file=stream)

    exts = report.get("discovered_extensions")
    print(file=stream)
    if exts is None:
        print("Discovered extensions: not looked at (--no-extensions).", file=stream)
    elif not exts:
        print("Discovered extensions: none.", file=stream)
    else:
        print(f"Discovered extensions ({len(exts)}):", file=stream)
        name_w = max(len(e["name"]) for e in exts)
        scope_w = max(len(e["scope"]) for e in exts)
        for e in exts:
            manifest = "manifest" if e["has_manifest"] else "—"
            print(
                f"  {e['name'].ljust(name_w)}  {e['scope'].ljust(scope_w)}  "
                f"{_placeholder(e['version']).ljust(8)}  {manifest}",
                file=stream,
            )

    for err in report.get("extension_errors", ()):
        print(f"  ! {err}", file=stream)

    if exts is not None:
        print(
            "  (aelix's own always-on extensions — "
            + ", ".join(report["always_on_builtins"])
            + " — are part of the runtime, not of this directory, and load either way.)",
            file=stream,
        )

    print(file=stream)
    print(
        "Only a running session can answer: "
        + ", ".join(SESSION_ONLY).replace("_", " ")
        + ".",
        file=stream,
    )
    print(
        "`aelix status` starts no session and resolves no model, so it does not report them.",
        file=stream,
    )


async def _collect(*, discover: bool) -> dict[str, Any]:
    """The report. Every value is read here and now; nothing is cached."""

    # Imported here rather than at module scope so a plain ``aelix`` launch pays
    # nothing for a verb it is not running — the convention ``entry.py`` uses for
    # the ``docs`` and ``extension`` verbs.
    #
    # ``BUILTIN_ALWAYS_ON_NAMES`` is the ONE list, shared with ``/extension``'s
    # viewer. It used to live in ``tui/extension_manager.py`` and this used to
    # reach in there; measured, that pulled 166 modules including
    # ``prompt_toolkit`` and ``rich`` — an OPTIONAL extra, so on a headless
    # install it was an ImportError and this command would have died listing a
    # directory. ``extensions/always_on`` is import-clean.
    from aelix_agent_core.contracts import AELIX_API_LEVEL
    from aelix_status.snapshot import bounded_emitted_value, summarise_extensions

    from aelix_coding_agent.cli.config import VERSION, get_agent_dir
    from aelix_coding_agent.cli.project_trust import resolve_project_trusted
    from aelix_coding_agent.extensions.always_on import BUILTIN_ALWAYS_ON_NAMES

    cwd = Path.cwd()
    try:
        agent_dir: str | None = get_agent_dir()
    except Exception:  # noqa: BLE001 — only a display label and the scope hint
        agent_dir = None

    # NON-INTERACTIVE TWO WAYS OVER, and the resolver only needs one:
    # ``if not has_ui or prompt is None: return False``. So ``prompt=`` being
    # unpassed is what actually denies here, and ``has_ui=False`` is the
    # declaration of the same fact rather than a second mechanism. That is not a
    # guess — flipping ``has_ui`` to ``True`` on its own left all 13 behavioural
    # tests in ``tests/status/test_status_cli.py`` GREEN, which is why the pair
    # is pinned structurally there instead.
    #
    # Both are kept: passing ``has_ui=False`` while a future edit adds a
    # ``prompt=`` would still deny, and a reader of this call should not have to
    # know the resolver's boolean shape to see that nothing is asked.
    #
    # ``override=None`` because this command parses no ``--approve`` /
    # ``--no-approve`` — reporting a trust decision that a flag made would
    # describe a launch nobody is performing.
    project_trusted = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        agent_dir=agent_dir,
    )

    report: dict[str, Any] = {
        "version": VERSION,
        "cwd": str(cwd),
        "agent_dir": agent_dir,
        "project_trusted": project_trusted,
        "manifest_api_level": AELIX_API_LEVEL,
        "session_only": list(SESSION_ONLY),
        "always_on_builtins": sorted(BUILTIN_ALWAYS_ON_NAMES),
    }

    if not discover:
        report["discovered_extensions"] = None
        return report

    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    loaded = await discover_and_load_extensions(
        [],
        cwd=cwd,
        agent_dir=Path(agent_dir) if agent_dir else None,
        no_project_local=not project_trusted,
    )
    summaries: tuple[ExtensionSnapshot, ...] = summarise_extensions(
        loaded.extensions, cwd=str(cwd), agent_dir=agent_dir
    )
    report["discovered_extensions"] = [
        {
            "name": s.name,
            "scope": s.scope,
            "version": s.version,
            "has_manifest": s.has_manifest,
        }
        for s in summaries
    ]
    # Bounded like every other author-controlled string this package emits: a
    # load error carries the extension's own text, and #101 M2 measured a
    # 4138-character ``plugin.version`` reaching a report unbounded.
    report["extension_errors"] = [bounded_emitted_value(str(e)) for e in loaded.errors]
    return report


async def run_status_command(args: list[str]) -> int:
    """``aelix status`` — see the module docstring for what it does and does not ask."""

    as_json = False
    discover = True
    for arg in args:
        if arg in ("-h", "--help"):
            print(_USAGE, end="")
            return 0
        if arg == "--json":
            as_json = True
        elif arg == "--no-extensions":
            discover = False
        else:
            print(f"Error: unknown argument for `aelix status`: {arg}", file=sys.stderr)
            print(_USAGE, end="", file=sys.stderr)
            return _EXIT_USAGE

    report = await _collect(discover=discover)

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    _render_text(report, sys.stdout)
    return 0


__all__ = ["SESSION_ONLY", "run_status_command"]
