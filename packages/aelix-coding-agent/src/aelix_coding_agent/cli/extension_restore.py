"""#926 (ADR-0321) — recorded extensions this environment no longer has.

``uv tool install --force`` and ``uv tool upgrade`` rebuild aelix's tool environment
from its ``uv-receipt.toml``, and an extension ``aelix extension install`` put there is
not in that receipt, so a reinstall removed it with no word. Its install record
(``extensionSources``, ADR-0186) and its pin (``extension_pins.json``, ADR-0187)
stayed. This module joins the two:

* :func:`missing_recorded_extensions` — every install record (``git`` / ``path`` /
  ``pypi``) whose distribution is not installed, and whether it can be put back at
  exactly its pinned identity: a git record at a 40-hex commit whose pin holds that
  commit, a path record whose file the pin's sha256 covers, a package record whose
  pin holds a version and its sha256 (``--verify-pypi``) in an environment whose
  installer can check that (pip; the uv backend cannot, #113).
* ``aelix extension list`` names them, as recorded but not installed;
  ``aelix extension restore`` reinstalls them through :func:`install_extension`, so
  the pin gate compares what is fetched with the pin and refuses a change without
  ``--repin``.
* :func:`restore_at_start` — an interactive start in a ``uv tool`` environment
  restores them before extensions load; a headless start names the command.

The ledger is the install RECORD, not the pin store. A pin outlives
``aelix extension remove`` (which drops the record only) and strict mode
provisions pins for sources that were never installed (ADR-0187), so "pinned and
not installed" alone would reinstall what the user removed or never asked for.
And only a record an install wrote: one that names the distribution it installed.
``aelix extension source add`` registers a source with no name and installs
nothing, so a source that was only registered stays uninstalled even when a pin
for it was provisioned (Codex review round 2 M1). A record is installed when a
distribution of its name is, never by its source: two records of one git commit
at two ``#subdirectory=`` targets are two extensions (Codex review round 2 M5).

Never an older version than the user last installed (#926 verify r3 F1). Of the
records of one name only the last in list order is used. A record is not its
extension's latest install when a later row with no name is one of it (the same git
repository, or a wheel of the same distribution: aelix 0.1.0 and earlier wrote such a
row for an install over an installed distribution) or when its pin holds another
commit; it is named with the command for the latest install and not restored. A later
git or path row with no name that is tied to no record keeps the automatic restore
(start and ``restore --all``) off every earlier record; ``restore <name>`` still
restores one.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from aelix_ai.utils.terminal_text import safe_for_terminal
from packaging.utils import canonicalize_name

from . import extension_catalog, extension_pins
from . import extension_install as EI
from .config import get_agent_dir

if TYPE_CHECKING:
    from aelix_ai.settings import ExtensionSourceObject, SettingsManager

__all__ = [
    "RecordedExtension",
    "missing_recorded_extensions",
    "missing_summary",
    "read_only_settings",
    "restore_at_start",
    "run_restore_command",
]

#: How long one start-up restore may run before it is stopped and reported.
_START_RESTORE_TIMEOUT_S = 120.0

_RESTORE_USAGE = "usage: aelix extension restore [--all | <name>] [--yes] [--offline] [--repin]"


@dataclass(frozen=True)
class RecordedExtension:
    """One install record whose distribution this environment does not have."""

    source: ExtensionSourceObject
    #: The name shown and matched: the record's distribution name when it has one.
    label: str
    #: What ``restore`` installs — the record at its pinned identity — or ``None``.
    target: EI.InstallTarget | None
    #: ``target`` is a package whose pin is a version + sha256 (download-verified).
    verify_pypi: bool
    #: ``git <repo>@<sha12>``, ``file <path>``, ``<name>==<version>``.
    detail: str
    #: Why it cannot be restored at a pin, when ``target`` is ``None``.
    problem: str | None
    #: The command that reinstalls it by hand (for a ``stale`` one: the latest install).
    command: str
    #: Not its extension's latest install: ``problem`` says why, ``command`` installs
    #: the latest (#926 verify r3 F1).
    stale: bool = False
    #: A later record with no name, tied to no record, that may be a newer install of
    #: it: the automatic restore leaves this one alone (``restore <name>`` does not).
    held_by: str | None = None


def read_only_settings() -> SettingsManager:
    """The global settings, read and never written.

    ``extension list`` and the start-up check only read the install records.
    :meth:`SettingsManager.create` loads each scope through
    ``FileSettingsStorage.with_lock``, which, when ``settings.json`` exists, opens it
    read-write and holds an exclusive ``flock`` on it while it reads (POSIX; no lock
    on Windows), and, when it does not, reads nothing and creates nothing. These
    two callers take no lock and open nothing for writing: the file's text is read
    the way the manager reads it (the agent dir's ``settings.json``, or
    ``AELIX_SETTINGS_PATH``) and parsed in memory.
    """

    from aelix_ai.settings import SettingsManager
    from aelix_ai.settings.storage import FileSettingsStorage

    override = os.environ.get("AELIX_SETTINGS_PATH")
    if override:
        storage = FileSettingsStorage(Path.cwd(), Path(override).parent, global_path=Path(override))
    else:
        storage = FileSettingsStorage(Path.cwd(), Path(get_agent_dir()))
    text = storage.read("global")
    data = json.loads(text) if text else {}
    return SettingsManager.in_memory(data if isinstance(data, dict) else {}, project_trusted=False)


class _Installed:
    """What this environment has: the canonical names of its distributions."""

    def __init__(self) -> None:
        importlib.invalidate_caches()
        self.names: set[str] = set()
        try:
            dists = list(importlib.metadata.distributions())
        except Exception:  # noqa: BLE001 — a broken environment has nothing installed
            dists = []
        for dist in dists:
            try:
                name = dist.name
            except Exception:  # noqa: BLE001 — a dist-info whose METADATA cannot be read
                continue
            if name:
                self.names.add(canonicalize_name(str(name)))

    def has_name(self, name: str | None) -> bool:
        return bool(name) and canonicalize_name(str(name)) in self.names


def _quoted(text: str) -> str:
    import shlex

    return safe_for_terminal(shlex.quote(text))


def _describe(
    source: ExtensionSourceObject,
    pins: dict[str, extension_pins.Pin],
    *,
    can_verify_pypi: bool,
) -> RecordedExtension:
    """The record, and the exact target its pin allows — or why there is none.

    ``can_verify_pypi``: this environment's installer can download a package and
    check it against a version + sha256 pin (pip; the uv backend cannot, #113).
    """

    spec = source.spec
    if source.kind == "git":
        sha = EI._extract_git_sha(spec)
        identity = EI._pin_identity(spec, "git")
        repo = EI._target_source_key(spec, "git") or identity
        label = source.name or repo.rsplit("/", 1)[-1].removesuffix(".git") or spec
        pin = pins.get(identity)
        command = f"aelix extension install {_quoted(spec)}"
        detail = f"git {repo}@{sha[:12]}" if sha else f"git {repo}"
        if sha is None:
            problem = "recorded at a branch or tag, not a commit, so nothing pins it"
        elif pin is None or pin.kind != "git" or not pin.git_sha:
            problem = "no pin records its commit (installed with --no-verify?)"
        elif pin.git_sha != sha:
            # A verified install of another commit moves the pin, so it is read as the
            # later install (verify r3 F1); --no-verify breaks that (ADR-0321 limits).
            return RecordedExtension(
                source,
                label,
                None,
                False,
                detail,
                f"its pin holds {pin.git_sha[:12]}, a commit installed after this record",
                f"aelix extension install {_quoted(_at_commit(spec, pin.git_sha))}",
                stale=True,
            )
        else:
            return RecordedExtension(
                source, label, extension_catalog.CatalogSpec(spec), False, detail, None, command
            )
        return RecordedExtension(source, label, None, False, detail, problem, command)

    if source.kind == "path":
        command = f"aelix extension install {_quoted(spec)}"
        try:
            target = EI._recorded_path_target(spec)
        except extension_catalog.CatalogError as exc:
            return RecordedExtension(
                source, source.name or spec, None, False, f"file {spec}", str(exc), command
            )
        path = Path(EI._split_path_target(target)[0]).expanduser()
        label = source.name or EI._target_dist_hint(target, "path") or path.name
        pin = pins.get(EI._pin_identity(target, "path"))
        detail = f"file {path}"
        if path.is_dir():
            problem = "a directory has no single artifact to pin (ADR-0187)"
        elif not path.is_file():
            problem = "the file it was installed from is gone"
        elif pin is None or pin.kind != "path" or not pin.sha256:
            problem = "no pin records the file's sha256"
        else:
            return RecordedExtension(source, label, target, False, detail, None, command)
        return RecordedExtension(source, label, None, False, detail, problem, command)

    name = source.name or EI._bare_package_name(spec)
    pin = pins.get(extension_pins.canonicalize_name(name))
    if pin is not None and pin.kind == "pypi" and pin.version and pin.sha256:
        exact = f"{name}=={pin.version}"
        if not can_verify_pypi:
            return RecordedExtension(
                source,
                name,
                None,
                False,
                exact,
                "its pin is a version and a sha256, which only pip can check, and this "
                "environment has no pip",
                f"uv pip install --python {_quoted(sys.executable)} pip, then "
                f"aelix extension restore {_quoted(name)}",
            )
        return RecordedExtension(
            source,
            name,
            extension_catalog.CatalogSpec(exact),
            True,
            exact,
            None,
            f"aelix extension install {_quoted(exact)} --verify-pypi",
        )
    return RecordedExtension(
        source,
        name,
        None,
        False,
        name,
        "no pin records a version and its sha256 (a package pin needs --verify-pypi)",
        f"aelix extension install {_quoted(name)}",
    )


def _at_commit(spec: str, sha: str) -> str:
    """``spec`` (a git record at a 40-hex commit) at commit ``sha`` instead."""

    url = EI._git_url_part(spec)
    head = url.partition("#")[0].partition("?")[0]
    return spec.replace(url, head[:-40] + sha + url[len(head) :], 1)


def _is_installed(rec: RecordedExtension, installed: _Installed) -> bool:
    """A distribution of the name the install recorded is installed (ADR-0321 D3).

    Not by the source it came from: the key read from PEP 610's ``direct_url.json``
    was its ``url``, which leaves the ``subdirectory`` field out, so two extensions
    of one commit read as one and the second was said to be installed (Codex review
    round 2 M5).
    """

    return installed.has_name(rec.source.name)


def _install_records(sources: list[ExtensionSourceObject]) -> list[ExtensionSourceObject]:
    """The records an install wrote: an installable kind and the distribution's name.

    ``source add`` registers a source and installs nothing (ADR-0186's 2-step), and
    its row has no name; an install records the name of the distribution it
    installed (since 0.1.0b1, ``_record_install``). So a row with no name is not
    evidence that anything was installed (Codex review round 2 M1).
    """

    return [
        s
        for s in sources
        if s.kind in ("git", "path", "pypi") and s.spec.strip() and (s.name or "").strip()
    ]


def _wheel_of(source: ExtensionSourceObject) -> str | None:
    """The canonical distribution a path row's wheel filename names, or ``None``."""

    if source.kind != "path":
        return None
    try:
        wheel = EI._wheel_name_version(EI._recorded_path_target(source.spec), "path")
    except extension_catalog.CatalogError:
        return None
    return str(wheel[0]) if wheel is not None else None


def _repo_of(source: ExtensionSourceObject) -> str | None:
    """A git row's repository (its pin identity, with the subdirectory), or ``None``."""

    if source.kind != "git":
        return None
    try:
        return EI._pin_identity(source.spec, "git")
    except ValueError:
        return None


def _is_one_of(row: ExtensionSourceObject, record: ExtensionSourceObject) -> bool:
    """``row`` (no name) installed ``record``'s extension: its git repository, or a
    wheel whose filename names its distribution."""

    repo = _repo_of(row)
    if repo is not None:
        return _repo_of(record) == repo
    return _wheel_of(row) == canonicalize_name(str(record.name))


def _latest_records(
    sources: list[ExtensionSourceObject],
) -> list[tuple[ExtensionSourceObject, ExtensionSourceObject | None, ExtensionSourceObject | None]]:
    """``(record, later, held_by)`` for the last install record of each name.

    ``later``: the last later row with no name that is one of this record's extension
    (:func:`_is_one_of`), so this record is not the latest install. ``held_by``: the
    first later git or path row with no name that is one of no record's extension: it
    may be a newer install of this one, or a source only registered; nothing tells.
    """

    records = _install_records(sources)
    last = {canonicalize_name(str(r.name)): r for r in records}
    out = []
    for i, record in enumerate(sources):
        if last.get(canonicalize_name(str(record.name))) is not record:
            continue
        nameless = [
            r for r in sources[i + 1 :] if r.kind in ("git", "path") and not (r.name or "").strip()
        ]
        later = [r for r in nameless if _is_one_of(r, record)]
        held = [r for r in nameless if not any(_is_one_of(r, n) for n in records)]
        out.append((record, later[-1] if later else None, held[0] if held else None))
    return out


def missing_recorded_extensions(
    sources: list[ExtensionSourceObject],
    *,
    agent_dir: str | None = None,
    can_verify_pypi: bool | None = None,
) -> list[RecordedExtension]:
    """Every install record whose distribution is not installed here.

    Only records an install wrote (:func:`_install_records`): a source that was only
    registered is not listed and not restored. Of the records of one name only the
    last is used, and it is ``stale`` (not restored, named with the command for the
    latest install) or ``held_by`` a later row as :func:`_latest_records` finds.
    ``can_verify_pypi`` defaults to what this environment's installer can do
    (:func:`~aelix_coding_agent.cli.extension_install.detect_install_backend`).
    """

    latest = _latest_records(sources)
    if not latest:
        return []  # every start reaches here; scanning the environment costs a read per dist
    if can_verify_pypi is None:
        backend = EI.detect_install_backend()
        can_verify_pypi = backend is not None and backend.supports_download
    pins = extension_pins.load_pins(extension_pins.pins_file_path(agent_dir or get_agent_dir()))
    installed = _Installed()
    found: list[RecordedExtension] = []
    for source, later, held_by in latest:
        try:
            rec = _describe(source, pins, can_verify_pypi=can_verify_pypi)
        except Exception as exc:  # noqa: BLE001 — one bad record never hides the rest
            rec = RecordedExtension(
                source,
                source.name or source.spec,
                None,
                False,
                source.spec,
                f"the record cannot be read ({exc})",
                f"aelix extension install {_quoted(source.spec)}",
            )
        if _is_installed(rec, installed):
            continue
        if later is not None:
            rec = replace(
                rec,
                target=None,
                verify_pypi=False,
                problem=f"a later record installed it from {safe_for_terminal(later.spec)}",
                command=f"aelix extension install {_quoted(later.spec)}",
                stale=True,
            )
        elif held_by is not None and rec.target is not None:
            rec = replace(rec, held_by=held_by.spec)
        found.append(rec)
    return found


def missing_summary(records: list[RecordedExtension]) -> str | None:
    """One line for ``extension verify`` and ``aelix status``: what is recorded and gone."""

    if not records:
        return None
    names = ", ".join(safe_for_terminal(r.label) for r in records)
    return (
        f"{len(records)} recorded extension(s) are not installed in this environment "
        f"({names}); `aelix extension restore` puts back the ones pinned "
        "(`aelix extension list` says which)."
    )


def print_missing_section(records: list[RecordedExtension]) -> None:
    """``extension list``'s rows for records this environment does not have."""

    if not records:
        return
    print("Recorded but not installed (an aelix reinstall or `uv tool upgrade` removes them):")
    for rec in records:
        label = safe_for_terminal(rec.label)
        print(f"  {label}  ({safe_for_terminal(rec.detail)})")
        if rec.held_by is not None:
            print(f"      not restored automatically: {_held_reason(rec)}")
        elif rec.target is not None:
            print(f"      restore at its pin: aelix extension restore {_quoted(rec.label)}")
        elif rec.stale:
            print(f"      not its latest install: {safe_for_terminal(rec.problem or '')}")
            print(f"      install the latest: {rec.command}")
        else:
            print(f"      not restorable at a pin: {safe_for_terminal(rec.problem or '')}")
            print(f"      reinstall: {rec.command}")


def _held_reason(rec: RecordedExtension) -> str:
    """Why the automatic restore leaves a ``held_by`` record alone, and the way back."""

    return (
        f"a later record ({safe_for_terminal(rec.held_by or '')}) names no extension and may "
        f"be a newer install of it; `aelix extension restore {_quoted(rec.label)}` restores "
        "this one"
    )


def _restore_one(
    rec: RecordedExtension,
    *,
    index_urls: list[str],
    yes: bool,
    offline: bool,
    repin: bool,
    input_fn: Callable[[str], str],
    runner: EI.PipRunner | None,
    child_runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    target: EI.InstallTarget | None = None,
) -> int:
    """Install one record at its pinned identity (or ``target``), then report its verdict."""

    target = target if target is not None else rec.target
    assert target is not None
    kind = EI.classify_target(target)
    before = EI._installed_ext_dists()
    # The index rules of `extension install` (_cmd_install): the registered indexes
    # resolve a package name, and offline they are the dependency index of a git or
    # path install too, where no index at all was `--no-index` (Codex r2 S3).
    use_index = kind == "pypi" or EI._is_offline(offline)
    code = EI.install_extension(
        target,
        yes=yes,
        index_url=index_urls[0] if index_urls and use_index else None,
        extra_index_urls=index_urls[1:] if use_index else [],
        offline=offline,
        repin=repin,
        verify_pypi=rec.verify_pypi,
        announce=False,
        input_fn=input_fn,
        runner=runner,
        child_runner=child_runner,
    )
    if code != 0:
        return code
    importlib.invalidate_caches()
    try:
        dists = EI._attributed_dists(target, kind, before, EI._installed_ext_dists())
        return EI._report_install_binding(dists)
    except Exception:  # noqa: BLE001 — the package is on disk; no verdict is not a failure
        return 0


def _parse_restore_args(args: list[str]) -> tuple[str | None, bool, bool, bool] | int:
    name: str | None = None
    yes = offline = repin = every = False
    for a in args:
        if a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
        elif a == "--repin":
            repin = True
        elif a == "--all":
            every = True
        elif a in ("-h", "--help"):
            print(_RESTORE_USAGE)
            return 0
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_RESTORE_USAGE}", file=sys.stderr)
            return EI._EXIT_DIDNT_RUN
        elif name is None:
            name = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_RESTORE_USAGE}", file=sys.stderr)
            return EI._EXIT_DIDNT_RUN
    if every and name is not None:
        print(f"Error: --all and a name are exclusive.\n{_RESTORE_USAGE}", file=sys.stderr)
        return EI._EXIT_DIDNT_RUN
    return name, yes, offline, repin


def _matches(rec: RecordedExtension, name: str) -> bool:
    want = name.strip()
    if canonicalize_name(want) in {
        canonicalize_name(rec.label),
        canonicalize_name(rec.source.name or rec.label),
    }:
        return True
    return EI._source_identity(rec.source.spec, rec.source.kind) == want or rec.source.spec == want


async def run_restore_command(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: EI.PipRunner | None,
    settings_unreadable: str | None = None,
) -> int:
    """``aelix extension restore [--all | <name>]`` — reinstall at the pinned identity.

    Exit codes are ``update``'s: ``0`` restored (or nothing to restore), ``1`` an
    installer failed, ``2`` one did not run (no pin to restore it at, a pin
    mismatch without ``--repin``, a declined consent, a settings file that could not
    be read), ``3`` restored but some endpoint will not bind; the hardest one wins
    (:func:`EI._worse_update_code`).

    ``settings_unreadable``: why the global settings file did not load. The records
    are in it, so nothing can be said about what to restore (Codex r2 S4: it said
    "every recorded extension is installed" and exited 0).
    """

    parsed = _parse_restore_args(args)
    if isinstance(parsed, int):
        return parsed
    name, yes, offline, repin = parsed
    if settings_unreadable is not None:
        print(
            "Error: the settings file that holds the install records could not be read "
            f"({safe_for_terminal(settings_unreadable)}), so restore cannot tell what to "
            "restore; nothing was installed.",
            file=sys.stderr,
        )
        return EI._EXIT_DIDNT_RUN
    sources = settings.get_extension_sources()
    backend = EI.resolve_install_backend(runner)
    records = missing_recorded_extensions(
        sources, can_verify_pypi=backend is not None and backend.supports_download
    )
    if name is not None:
        records = [r for r in records if _matches(r, name)]
        recorded = any(
            canonicalize_name(str(s.name)) == canonicalize_name(name.strip())
            or EI._source_identity(s.spec, s.kind) == name.strip()
            for s in _install_records(sources)
        )
        if not records and recorded:
            print(f"{safe_for_terminal(name)} is installed; nothing to restore.")
            return 0
        if not records:
            print(
                f"Error: no recorded extension named {safe_for_terminal(name)!r} is missing "
                "from this environment (see `aelix extension list`).",
                file=sys.stderr,
            )
            return EI._EXIT_DIDNT_RUN
    if not records:
        print("Nothing to restore: every recorded extension is installed.")
        return 0
    index_urls = EI._index_urls(sources)
    worst = 0
    for rec in records:
        label = safe_for_terminal(rec.label)
        target = rec.target
        if target is None and rec.stale:
            print(
                f"Not restoring {label} ({safe_for_terminal(rec.detail)}): it is not the "
                f"latest install ({safe_for_terminal(rec.problem or '')})."
            )
            print(f"  Install the latest with: {rec.command}")
            worst = EI._worse_update_code(worst, EI._EXIT_DIDNT_RUN)
            continue
        if target is None:
            print(
                f"Cannot restore {label} ({safe_for_terminal(rec.detail)}) at a pin: "
                f"{safe_for_terminal(rec.problem or '')}."
            )
            print(f"  Reinstall it with: {rec.command}")
            worst = EI._worse_update_code(worst, EI._EXIT_DIDNT_RUN)
            continue
        if rec.held_by is not None and name is None:
            print(f"Not restoring {label} automatically: {_held_reason(rec)}.")
            worst = EI._worse_update_code(worst, EI._EXIT_DIDNT_RUN)
            continue
        if rec.held_by is not None:
            print(
                f"Note: a later record ({safe_for_terminal(rec.held_by)}) names no extension; "
                f"if it is a newer install of {label}, install that instead."
            )
        if _is_installed(rec, _Installed()):
            continue  # an earlier restore of this run brought it along (a dependency)
        print(f"Restoring {label} ({safe_for_terminal(rec.detail)})")
        code = _restore_one(
            rec,
            index_urls=index_urls,
            yes=yes,
            offline=offline,
            repin=repin,
            input_fn=input_fn,
            runner=runner,
            target=target,
        )
        worst = EI._worse_update_code(worst, code)
    return worst


# =====================================================================
# === the start-up restore (owner 2026-11-26: "receipt 등록 + 시작 시 복원") =
# =====================================================================


def _in_uv_tool_environment() -> bool:
    """True when this interpreter is a ``uv tool`` environment (it has a receipt).

    The only environments a reinstall rebuilds from a receipt. A ``pip install``
    venv keeps its other packages on an upgrade, and a source checkout's ``.venv``
    is ``uv sync``'s to manage, so neither restores at start.
    """

    return (Path(sys.prefix) / "uv-receipt.toml").is_file()


def _quiet_runner(
    pip_args: list[str], cwd: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    """The installer child for a start-up restore: output captured, time bounded.

    :func:`~aelix_ai.utils._process_tree.run_contained` (#221): no stdin, a session
    (POSIX) or process group and job (win32) of its own, and on the timeout or a
    ``KeyboardInterrupt`` the whole tree is ended, so the ``git`` a ``uv pip
    install`` of a git source starts does not outlive it (#926 verify r1 F9).
    """

    from aelix_ai.utils._process_tree import run_contained

    try:
        result = run_contained(pip_args, timeout=_START_RESTORE_TIMEOUT_S, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        err = (
            exc.stderr or b""
        ) + f"\nerror: stopped after {_START_RESTORE_TIMEOUT_S:.0f} s".encode()
        result = subprocess.CompletedProcess(pip_args, 124, exc.stdout or b"", err)
    # Into whatever stdout is now: restore_at_start holds it, to quote the reason.
    for chunk in (result.stdout, result.stderr):
        if chunk:
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
    return result


def _reason(text: str) -> str:
    """The line that says why: the installer's last ``error`` line, else the last line."""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.lower().startswith(("error", "verification refused"))]
    picked = (errors or lines or ["no output"])[-1]
    return safe_for_terminal(picked[:200])


class _CtrlCRaises:
    """Ctrl+C raises ``KeyboardInterrupt`` at once inside this block.

    Under ``asyncio.run`` the first Ctrl+C only cancels the main task, and that takes
    effect at its next ``await``: after a restore child has run to its end (up to
    :data:`_START_RESTORE_TIMEOUT_S`), and then as a traceback out of the extension
    loader (#926 verify r1 F4). The default handler is put in for the restore and
    the previous one back after it. Main thread only; elsewhere this does nothing.
    """

    def __init__(self) -> None:
        self._previous: object = None
        self._set = False

    def __enter__(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            self._previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal.default_int_handler)
            self._set = True
        except (ValueError, OSError, TypeError):
            pass

    def __exit__(self, *exc: object) -> None:
        if self._set and self._previous is not None:
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signal.SIGINT, self._previous)  # type: ignore[arg-type]


#: Stands in for a space inside a `quoted command` while the text is wrapped.
_KEEP = "\x00"


def _say(text: str, command: str | None = None) -> None:
    """One start-up line, broken at spaces to the terminal's width; ``command`` after it.

    The lines print above the TUI's banner, where the terminal would break a long
    one inside a word or a command (#926 verify r1 F11). A command is never broken
    across lines (verify r2 N6: copying the visible lines split it in two): a
    `backquoted` one in the text keeps its spaces, and ``command`` gets a line of its
    own, as long as it is. Not a terminal: one line, the command at its end.
    """

    try:
        tty = sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        tty = False
    if not tty:
        print(text if command is None else f"{text} {command}", flush=True)
        return
    width = max(40, shutil.get_terminal_size((120, 24)).columns - 1)
    kept = re.sub(r"`[^`]*`", lambda m: m.group(0).replace(" ", _KEEP), text)
    filled = textwrap.fill(
        kept,
        width=width,
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    print(filled.replace(_KEEP, " "), flush=True)
    if command is not None:
        print(f"  {command}", flush=True)


def restore_at_start(
    app_mode: str,
    *,
    offline: bool,
    no_extensions: bool,
    settings: SettingsManager | None = None,
    in_tool_env: Callable[[], bool] = _in_uv_tool_environment,
    child_runner: Callable[..., subprocess.CompletedProcess[bytes]] = _quiet_runner,
) -> None:
    """Restore, before extensions load, the recorded extensions a reinstall removed.

    Called from ``cli/entry.py`` after the app mode is known and before the harness is
    built (which loads extensions). ``offline`` is the parsed ``--offline``; the
    offline environment names count too (:func:`~aelix_coding_agent.util.offline.is_offline`).

    Interactive only, exact pins only (never ``--repin``), one line for what is
    restored and one per result. ``--offline`` skips with a notice that names the
    command. A headless start (``-p``, ``--mode json|rpc``) installs nothing and
    prints one stderr line naming the command. A delegated agent, ``--no-extensions``
    and an environment that is not a ``uv tool`` one do nothing. Ctrl+C during the
    restore stops it, names what was not restored, and the start goes on (lane's
    call, ADR-0321 D5). Never raises and never blocks the start: a failure is a
    line, and the start goes on.
    """

    try:
        from ..subagent_contract import subagent_depth
        from ..util.offline import is_offline

        offline = is_offline(offline)
        if no_extensions or subagent_depth() >= 1 or not in_tool_env():
            return
        if settings is None:
            settings = read_only_settings()
        sources = settings.get_extension_sources()
        records = missing_recorded_extensions(sources)
        if not records:
            return
        names = ", ".join(safe_for_terminal(r.label) for r in records)
        if app_mode != "interactive":
            print(
                f"aelix: {len(records)} recorded extension(s) are not installed in this "
                f"environment ({names}); restore them with: aelix extension restore",
                file=sys.stderr,
            )
            return
        for rec in records:
            label = safe_for_terminal(rec.label)
            if rec.held_by is not None:
                _say(
                    f"aelix: not restoring {label} automatically: a later record "
                    f"({safe_for_terminal(rec.held_by)}) names no extension and may be a newer "
                    "install of it; restore this one with:",
                    f"aelix extension restore {_quoted(rec.label)}",
                )
            elif rec.stale:
                _say(
                    f"aelix: {label} is recorded but not installed, and that record is not its "
                    f"latest install ({safe_for_terminal(rec.problem or '')}); install the "
                    "latest with:",
                    rec.command,
                )
            elif rec.target is None:
                _say(
                    f"aelix: {label} is recorded but not installed, and cannot be restored at "
                    f"a pin ({safe_for_terminal(rec.problem or '')}); reinstall it with:",
                    rec.command,
                )
        restorable = [r for r in records if r.target is not None and r.held_by is None]
        if not restorable:
            return
        shown = ", ".join(safe_for_terminal(r.label) for r in restorable)
        if offline:
            _say(
                f"aelix: offline, so not restoring {shown} (recorded, not installed); "
                "run `aelix extension restore` when online."
            )
            return
        _say(f"aelix: restoring {shown}, recorded but no longer installed in this environment...")
        index_urls = EI._index_urls(sources)
        try:
            with _CtrlCRaises():
                for rec in restorable:
                    _restore_at_start_one(rec, index_urls, child_runner)
        except KeyboardInterrupt:
            left = [
                safe_for_terminal(r.label) for r in restorable if not _is_installed(r, _Installed())
            ]
            _say(
                f"aelix: restore stopped (Ctrl+C); not restored: {', '.join(left) or 'none'}. "
                "They stay recorded: `aelix extension restore` puts them back."
            )
        importlib.invalidate_caches()
    except Exception as exc:  # noqa: BLE001 — the start must never fail on this
        print(
            f"aelix: could not check for recorded extensions ({safe_for_terminal(str(exc))}).",
            file=sys.stderr,
        )


def _restore_at_start_one(
    rec: RecordedExtension,
    index_urls: list[str],
    child_runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    """One start-up restore and its line. ``KeyboardInterrupt`` goes to the caller."""

    label = safe_for_terminal(rec.label)
    if _is_installed(rec, _Installed()):
        return  # an earlier restore of this run brought it along (a dependency)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = _restore_one(
                rec,
                index_urls=index_urls,
                yes=True,
                offline=False,
                repin=False,
                input_fn=lambda _prompt: "n",
                runner=None,
                child_runner=child_runner,
            )
    except Exception as exc:  # noqa: BLE001 — a failure is a line, never a crash
        code = EI._INSTALLER_FAILED
        out.write(str(exc))
    if code == 0:
        _say(f"aelix: restored {label} ({safe_for_terminal(rec.detail)}).")
    elif code == EI._INSTALL_NOT_BOUND:
        _say(
            f"aelix: restored {label}, but it does not bind on this build; "
            "see `aelix extension verify`."
        )
    else:
        _say(
            f"aelix: could not restore {label} ({_reason(out.getvalue())}); retry with:",
            f"aelix extension restore {_quoted(rec.label)}",
        )
