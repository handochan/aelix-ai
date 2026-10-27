"""``aelix update --models``: refresh the model catalogs now (#434, ADR-0264 §4.6).

Pi parity: ``pi update --models`` (``package-manager-cli.ts:605-631``). Every
source, forced past the hosted catalog's 4-hour interval, waited on for at most
15 s; each source's outcome goes to stderr, and the exit status is 1 when one
failed or the wait ran out. ``aelix update`` without ``--models`` exits 2 and
names it: until #434 ``aelix update`` was a one-word prompt, and a bare verb
that did something else would surprise.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["run_update_command"]

_USAGE = (
    "usage: aelix update --models\n"
    "  --models   refresh the model catalogs now (the published catalog of new models)"
)


def _line(outcome: object) -> str:
    source = getattr(outcome, "source", "?")
    what = getattr(outcome, "outcome", "?")
    text = f"model catalog {source}: {what}"
    revision = getattr(outcome, "revision", None)
    if revision:
        text += f" ({revision[:19]})"
    added = getattr(outcome, "added", 0)
    if what == "published" and added:
        text += f", {added} model(s) this release does not ship"
    error = getattr(outcome, "error", None)
    if error:
        text += f": {error}"
    return text


async def run_update_command(argv: list[str]) -> int:
    """``aelix update [--models]``; returns the exit status."""

    if argv in ([], ["-h"], ["--help"]):
        print(_USAGE, file=sys.stderr if not argv else sys.stdout)
        return 2 if not argv else 0
    if "--offline" in argv:
        # As ``aelix extension … --offline`` takes it: after the command too.
        from aelix_coding_agent.util.offline import export_if_offline

        argv = [arg for arg in argv if arg != "--offline"]
        export_if_offline(True)
        if not argv:
            print(_USAGE, file=sys.stderr)
            return 2
    if argv != ["--models"]:
        print(f"aelix update: unknown arguments {' '.join(argv)!r}\n{_USAGE}", file=sys.stderr)
        return 2

    from aelix_ai.oauth import AuthStorage

    from aelix_coding_agent.cli.config import get_agent_dir
    from aelix_coding_agent.hosted_catalog_source import catalog_base_url
    from aelix_coding_agent.model_refresh import refresh_catalogs
    from aelix_coding_agent.model_registry import ModelRegistry
    from aelix_coding_agent.util.offline import is_offline

    if is_offline():
        print(
            "model catalogs not refreshed: offline (--offline, AELIX_OFFLINE or PI_OFFLINE)",
            file=sys.stderr,
        )
        return 1
    if catalog_base_url() is None:
        print(
            "model catalogs not refreshed: the published catalog is off "
            "(AELIX_MODEL_CATALOG_URL is empty or not an https:// URL)",
            file=sys.stderr,
        )
        return 1
    storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage)
    result = await refresh_catalogs(registry, trigger="update", force=True)
    if result is None:
        print("model catalogs not refreshed: no answer within 15 s", file=sys.stderr)
        return 1
    failed = False
    for outcome in result.outcomes:
        if outcome.outcome == "not-applicable" and outcome.source != "aelix-hosted":
            continue  # #450's source is not in this build
        print(_line(outcome), file=sys.stderr)
        failed = failed or outcome.outcome in ("failed", "aborted")
    return 1 if failed or result.aborted else 0
