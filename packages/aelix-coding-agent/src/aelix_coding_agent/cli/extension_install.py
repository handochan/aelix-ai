"""``aelix extension`` — install + a pi-parity marketplace core.

Issue #19 (ADR-0185) shipped ``extension install <path|git|pypi>``: a
closed-network-native installer where all three source kinds resolve to a single
``pip install`` into the RUNNING interpreter's environment
(``sys.executable -m pip``), so the plugin's module becomes importable AND its
``entry_points(group="aelix.extensions")`` registration is discovered by the
loader's Tier-4 pass — no bespoke registry, no ``sys.path`` machinery. pip's own
``--index-url`` / ``git+file`` / ``ssh`` carry the self-hosted / air-gapped
requirement, and pip itself is the install ledger.

Issue #32-A (ADR-0186) adds the pi-parity **marketplace core** on top:

* ``extension source add|list|remove`` — a persisted list of install *sources*
  (a pip index URL / a git repo / a local path), stored on the
  ``SettingsManager`` (``extension_sources``, GLOBAL scope). ``source add`` is
  REGISTER-ONLY (add ≠ install — the owner-decided 2-step model).
* ``extension install <target>`` — #19's installer ENHANCED: a bare package
  NAME resolves against the registered **index** sources (their URLs join pip's
  ``--index-url`` / ``--extra-index-url``); git / path / url targets install
  directly. On success the install is RECORDED (so ``update`` can reinstall it).
* ``extension list`` — the installed inventory, read straight from
  ``entry_points(group="aelix.extensions")`` (the pip ledger — no separate
  record).
* ``extension update [<name>]`` — reinstall a recorded source with
  ``--upgrade`` (git → ``git+url``; path → the path; pypi → the package name +
  index sources). No name = every recorded installable.
* ``extension remove <name>`` — ``pip uninstall`` the distribution providing
  ``<name>`` (entry-point → distribution via ``EntryPoint.dist``), then drop any
  matching recorded source.

pip runs the package's build/setup code, so consent is **source-level** (shown +
y/N, deny-by-default; ``--yes`` for headless) on every install/update — a
manifest *capability* gate is impossible here because the manifest lives inside
the not-yet-built package.

pi parity: this is the Python-ecosystem swap of pi's package model
(``package-manager.ts`` + ``settings.packages``) — pip replaces npm,
``entry_points`` replaces the ``PiManifest`` package root, ``--index-url``
replaces ``.npmrc``, and ``extension_sources`` mirrors pi's ``packages``
``PackageSource[]`` (a DISTINCT field — pi's is an npm-package-with-sub-resources
model; an aelix source only records WHERE to install FROM). A discover-catalog is
OUT of scope (follow-up #65).

Issue #64 (ADR-0187) adds the **pre-pip integrity gate** (:func:`verify_and_pin`,
:mod:`aelix_coding_agent.cli.extension_pins`): a SHA-256 hash-pin with
Trust-On-First-Install that runs AFTER the consent prompt and BEFORE pip, refusing
an install whose bytes no longer match the recorded pin. It adds INTEGRITY only —
pip still runs the pack's build/setup code after a verify passes, so the
source-level ``y/N`` consent REMAINS the sole execution-trust boundary. Default
``tofi`` covers path artifacts + pinned git SHAs; pypi two-phase download-verify is
opt-in (``--verify-pypi`` / ``--strict``) in v1 pending real-index integration
testing (#61). Ed25519 provenance is a deferred, forward-compatible seam.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from . import extension_pins
from .config import get_agent_dir

if TYPE_CHECKING:
    from aelix_ai.settings import ExtensionSourceObject, SettingsManager

_USAGE = (
    "usage: aelix extension <command>\n"
    "  install <path | git-url | package[==version]>  [--yes] [--index-url URL] "
    "[--offline] [--no-verify] [--strict] [--repin] [--verify-pypi]\n"
    "  source add <path | git-url | index-url>        [--yes]\n"
    "  source list\n"
    "  source remove <path | git-url | index-url>\n"
    "  list\n"
    "  update [<name>]                                [--yes] [--offline] "
    "[--no-verify] [--strict] [--repin] [--verify-pypi]\n"
    "  remove <name>                                  [--yes]"
)

# Exit codes: 0 = success; the pip returncode (usually 1) = pip ran and failed;
# 2 = did NOT run pip (usage error, guard refusal, user abort, missing pip). The
# 3-way split lets a script tell "pip failed" from "never ran" (ADR-0185).
_EXIT_DIDNT_RUN = 2

#: The entry-point group the loader's Tier-4 pass discovers (loader.py:750).
ENTRY_POINT_GROUP = "aelix.extensions"

TargetKind = Literal["path", "git", "pypi"]
#: The kinds a registered *source* can take. ``index`` = a pip index URL used
#: only to RESOLVE a bare-name install (never installed directly); ``git`` /
#: ``path`` = a directly-installable extension; ``pypi`` = an install RECORD of
#: a bare-name install (spec = the package, kept so ``update`` can reinstall it).
SourceKind = Literal["index", "git", "path", "pypi"]

# A subprocess runner injectable for tests (default = the real pip call).
PipRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]

__all__ = [
    "ENTRY_POINT_GROUP",
    "InstalledExtension",
    "PipRunner",
    "SourceKind",
    "TargetKind",
    "build_download_args",
    "build_pip_args",
    "classify_source",
    "classify_target",
    "install_extension",
    "list_installed_extensions",
    "run_extension_command",
    "run_extension_command_async",
    "verify_and_pin",
]


def _env_truthy(name: str) -> bool:
    # Strict 1/true/yes/on (case-insensitive) — so ``PI_OFFLINE=0`` reads as OFF,
    # not "any non-empty" (review NIT). Mirrors the canonical env-flag idiom.
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def classify_target(target: str) -> TargetKind:
    """Classify an install target as a local path, a git URL, or a pypi spec.

    A local path WINS if it exists on disk (so ``./my-ext`` beats any URL
    heuristic); otherwise git-URL shapes (``git+…`` / ``ssh://`` / ``git@`` /
    ``.git`` / an http(s) URL containing ``.git``) classify as git; everything
    else is a pypi package spec (``name`` / ``name==1.2`` / ``name[extra]``).
    """

    if target.strip() and Path(target).expanduser().exists():
        return "path"
    low = target.lower()
    if (
        target.startswith("git+")
        or low.startswith(("git://", "ssh://", "git@"))
        or low.endswith(".git")
        or (low.startswith(("http://", "https://")) and ".git" in low)
    ):
        return "git"
    return "pypi"


def classify_source(target: str) -> SourceKind | None:
    """Classify a ``source add`` target as ``path`` / ``git`` / ``index``.

    Reuses :func:`classify_target`'s path + git heuristics, then maps a *plain*
    http(s) URL (one :func:`classify_target` would call ``pypi`` because it has
    no ``.git``) to ``index`` — a pip package index. Returns :data:`None` for a
    bare token / empty string: a *source* must be a path, a git URL, or an index
    URL — a bare package name is an install TARGET, not a source to register.
    """

    if not target.strip():
        return None
    kind = classify_target(target)
    if kind in ("path", "git"):
        return kind
    # kind == "pypi": only a real http(s) URL is a valid index source.
    if target.strip().lower().startswith(("http://", "https://")):
        return "index"
    return None


def _normalize_git_spec(target: str) -> str:
    """Return a pip-installable ``git+…`` VCS spec for a git target.

    pip's VCS grammar requires a ``git+<transport>://`` scheme, so the scp
    shorthand ``git@host:path`` (which has no ``://``) is rewritten to
    ``git+ssh://git@host/path`` (review LOW: a bare ``git+`` prefix on the
    scp form produces a spec pip rejects at requirement-parse time). Forms
    that already carry a scheme pass through with just the ``git+`` prefix.
    """

    if target.startswith("git+"):
        return target
    # scp shorthand: ``[user@]host:path`` with NO ``://`` scheme.
    if "://" not in target and "@" in target and ":" in target:
        userhost, _, path = target.partition(":")
        return f"git+ssh://{userhost}/{path}"
    return f"git+{target}"


def _install_spec(target: str, kind: TargetKind) -> str:
    """The exact pip-install argument for a target (also its recorded spec)."""

    if kind == "path":
        return str(Path(target).expanduser().resolve())
    if kind == "git":
        return _normalize_git_spec(target)
    return target


def _bare_package_name(target: str) -> str:
    """Strip version specifiers / extras from a pypi target → the base name.

    ``pkg==1.2`` / ``pkg>=1`` / ``pkg[extra]`` / ``pkg ; marker`` → ``pkg``.
    Used so a recorded pypi install stores an upgradeable NAME (``update`` runs
    ``pip install --upgrade <name>`` — a pinned ``==`` spec would never move).
    """

    name = target.strip()
    for sep in ("[", ";", " "):
        name = name.split(sep, 1)[0]
    for op in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "="):
        name = name.split(op, 1)[0]
    return name.strip()


def build_pip_args(
    target: str,
    kind: TargetKind,
    *,
    index_url: str | None = None,
    extra_index_urls: Iterable[str] | None = None,
    upgrade: bool = False,
) -> list[str]:
    """Build the ``python -m pip install …`` argv for a classified target.

    ``index_url`` / ``extra_index_urls`` apply to a ``pypi`` target only (a
    bare-name install resolved against registered index sources — the first
    index becomes ``--index-url``, the rest ``--extra-index-url``). ``upgrade``
    adds ``--upgrade`` (used by ``extension update``).
    """

    base = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        base.append("--upgrade")
    if kind == "path":
        return [*base, str(Path(target).expanduser().resolve())]
    if kind == "git":
        return [*base, _normalize_git_spec(target)]
    # pypi
    args = [*base, target]
    if index_url:
        args += ["--index-url", index_url]
    for extra in extra_index_urls or ():
        args += ["--extra-index-url", extra]
    return args


def _is_offline(explicit: bool) -> bool:
    # Mirror the CLI's --offline / PI_OFFLINE contract (entry.py) + an aelix
    # alias. Strict truthiness so PI_OFFLINE=0 reads as OFF (review NIT).
    return explicit or _env_truthy("PI_OFFLINE") or _env_truthy("AELIX_OFFLINE")


def _pip_available(runner: PipRunner | None) -> bool:
    """True when the DEFAULT runner can invoke pip on this interpreter.

    A missing pip makes ``python -m pip`` exit nonzero WITHOUT raising, so a
    returncode check would mislabel it as an install failure (review LOW).
    Skipped when a custom runner is injected (tests / an alt package manager).
    uv-managed venvs often ship without pip — hence the actionable hint at the
    call site.
    """

    return runner is not None or importlib.util.find_spec("pip") is not None


def _pip_missing_message() -> str:
    return (
        f"Error: pip is not available on this interpreter ({sys.executable}). "
        "Install it (e.g. `python -m ensurepip --upgrade`, or `uv pip install "
        "pip` in a uv project) and retry."
    )


def install_extension(
    target: str,
    *,
    yes: bool = False,
    index_url: str | None = None,
    extra_index_urls: Iterable[str] | None = None,
    offline: bool = False,
    upgrade: bool = False,
    no_verify: bool = False,
    strict: bool = False,
    repin: bool = False,
    verify_pypi: bool = False,
    agent_dir: str | None = None,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Install one extension via ``pip``; return a process-style exit code.

    ``0`` on success; the pip returncode when pip ran and failed; ``2`` when pip
    did NOT run (usage/guard error, user abort, a #64 verify refusal, or missing
    pip). ``runner`` and ``input_fn`` are injectable for tests.
    """

    if not target.strip():
        print("Error: install target is empty.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    kind = classify_target(target)
    if _is_offline(offline) and kind == "pypi" and not index_url:
        print(
            "Error: offline mode — a pypi install needs --index-url pointing at a "
            "self-hosted / local index (or use a path or git+file:// source).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    if not _pip_available(runner):
        print(_pip_missing_message(), file=sys.stderr)
        return _EXIT_DIDNT_RUN

    pip_args = build_pip_args(
        target,
        kind,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        upgrade=upgrade,
    )

    # Consent — pip runs the package's build/setup code (arbitrary at install
    # time), so the manifest capability gate cannot protect this path; the
    # source-level y/N IS the trust boundary. Deny-by-default (headless without
    # --yes, or a closed stdin, aborts).
    verb = "Upgrade" if upgrade else "Install"
    print(f"{verb} extension from {kind}: {target}")
    print(f"  → {' '.join(pip_args)}")
    print(
        "  pip will run the package's build/setup code. Only install sources you trust."
    )
    if not yes:
        try:
            reply = input_fn("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return _EXIT_DIDNT_RUN  # distinct from pip's own failure code

    run = runner if runner is not None else _default_runner

    # #64 (ADR-0187): pre-pip integrity gate — runs AFTER consent, BEFORE pip.
    # A refusal returns _EXIT_DIDNT_RUN (pip never ran); a rewritten argv (verified
    # pypi) and/or a pending pin (recorded only on install success) flow back here.
    if no_verify and strict:
        print(
            "Warning: --no-verify disables the requested --strict verification; "
            "installing with NO integrity check.",
            file=sys.stderr,
        )
    pending_pin: extension_pins.Pin | None = None
    cleanup_dir: str | None = None
    if not no_verify:
        try:
            verified = verify_and_pin(
                target,
                kind,
                pip_args,
                strict=strict,
                repin=repin,
                verify_pypi=verify_pypi,
                index_url=index_url,
                extra_index_urls=extra_index_urls,
                runner=run,
                agent_dir=agent_dir,
            )
            pip_args = verified.pip_args
            pending_pin = verified.pin
            cleanup_dir = verified.cleanup_dir
        except extension_pins.VerifyRefusal as exc:
            print(f"Verification refused — pip not run: {exc}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        except Exception as exc:  # noqa: BLE001 — an internal verify error
            if strict:
                print(
                    f"Verification error (strict) — pip not run: {exc}",
                    file=sys.stderr,
                )
                return _EXIT_DIDNT_RUN
            print(
                f"Warning: integrity verification skipped ({exc}); "
                "installing without a pin.",
                file=sys.stderr,
            )
            pending_pin = None

    try:
        result = run(pip_args)
        code = int(getattr(result, "returncode", 1))
        if code == 0:
            if pending_pin is not None:
                try:
                    _record_pin(pending_pin, agent_dir)
                except Exception as exc:  # noqa: BLE001 — pinning is best-effort
                    print(
                        f"Warning: could not record integrity pin: {exc}",
                        file=sys.stderr,
                    )
            print(
                "Installed. Restart aelix (or /reload in the TUI) so the loader "
                "discovers it via entry_points."
            )
        else:
            print(f"pip install failed (exit {code}).", file=sys.stderr)
        return code
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def _default_runner(pip_args: list[str]) -> subprocess.CompletedProcess[bytes]:
    # Inherit stdio so the user sees pip's live progress; never shell=True.
    return subprocess.run(pip_args, check=False)  # noqa: S603 — argv list, no shell


# =====================================================================
# === #64 (ADR-0187): pre-pip integrity verification gate ==============
# =====================================================================

#: A pinned commit SHA embedded in a git spec: ``…@<40-hex>`` at end-or-``#frag``.
_GIT_SHA_RE = re.compile(r"@([0-9a-fA-F]{40})(?=$|#)")


def _git_repo_identity(git_spec: str) -> str:
    """A git spec's repo identity — the normalized spec minus any ``@<sha>``.

    So ``git+https://h/r.git@<sha>`` and a later ``…@<other-sha>`` map onto the
    SAME pin identity (a ref move is a re-pin event, not a new blind trust).
    """

    return _GIT_SHA_RE.sub("", git_spec)


def _extract_git_sha(git_spec: str) -> str | None:
    """The pinned 40-hex commit SHA in a git spec, or None for a mutable ref."""

    m = _GIT_SHA_RE.search(git_spec)
    return m.group(1).lower() if m else None


def _pin_identity(target: str, kind: TargetKind) -> str:
    """The canonical pin-store key for a target (path→abs, git→repo, pypi→name)."""

    if kind == "path":
        return str(Path(target).expanduser().resolve())
    if kind == "git":
        return _git_repo_identity(_normalize_git_spec(target))
    # pypi: PEP 503 canonical name so 'some-pkg' / 'some_pkg' / 'Some.Pkg' — one
    # PyPI project — key ONE pin (a variant spelling must not TOFI a fresh trust).
    return extension_pins.canonicalize_name(_bare_package_name(target))


def build_download_args(
    spec: str,
    *,
    index_url: str | None,
    extra_index_urls: Iterable[str] | None,
    dest: str,
) -> list[str]:
    """``python -m pip download <spec> --dest <dest>`` (+ index flags).

    Fetches the FULL dependency closure into ``dest`` (no ``--no-deps``) so the
    subsequent ``pip install --no-index --find-links <dest>`` can resolve the
    pack's dependencies locally; the integrity pin still covers ONLY the
    top-level artifact (transitive deps stay unverified — the documented gap).
    """

    args = [sys.executable, "-m", "pip", "download", spec, "--dest", dest]
    if index_url:
        args += ["--index-url", index_url]
    for extra in extra_index_urls or ():
        args += ["--extra-index-url", extra]
    return args


def _rewrite_pypi_local(dest: str, spec: str, *, upgrade: bool) -> list[str]:
    """Install argv that installs the VERIFIED bytes from the local download dir."""

    base = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        base.append("--upgrade")
    return [*base, "--no-index", "--find-links", dest, spec]


def _print_verify(notice: str) -> None:
    print(f"  ⓘ verify: {notice}")


@dataclass(frozen=True)
class _VerifyResult:
    """The gate's output: the argv to run + an optional pin to record on success.

    ``cleanup_dir`` is a temp download dir (pypi two-phase) the caller must remove
    AFTER the install reads from it — the pin is only recorded on install success.
    """

    pip_args: list[str]
    pin: extension_pins.Pin | None
    cleanup_dir: str | None = None


def verify_and_pin(
    target: str,
    kind: TargetKind,
    pip_args: list[str],
    *,
    strict: bool,
    repin: bool,
    verify_pypi: bool,
    index_url: str | None,
    extra_index_urls: Iterable[str] | None,
    runner: PipRunner,
    agent_dir: str | None,
) -> _VerifyResult:
    """The pre-pip integrity gate (ADR-0187). Runs AFTER consent, BEFORE pip.

    Returns the argv to execute (rewritten to a local ``--no-index`` install for a
    verified pypi target) plus a :class:`~extension_pins.Pin` to record IFF the
    install succeeds. Raises :class:`~extension_pins.VerifyRefusal` to block the
    install (the caller maps that to exit-code 2, "pip never ran").

    Default ``tofi`` verifies path artifacts + pinned git SHAs (recording on first
    acquisition); ``strict`` additionally refuses unpinned sources, mutable git
    refs, and directory/editable paths. pypi two-phase download-verify is opt-in
    (``verify_pypi`` / ``strict``) in v1 — see ADR-0187 (needs real-index
    integration testing, #61, before it becomes default-on).
    """

    mode = "strict" if strict else "tofi"
    identity = _pin_identity(target, kind)
    resolved_dir = agent_dir or get_agent_dir()
    pins_path = extension_pins.pins_file_path(resolved_dir)
    pins = extension_pins.load_pins(pins_path)
    existing = pins.get(identity)

    if kind == "path":
        resolved = Path(target).expanduser().resolve()
        if resolved.is_file():
            # Stage a copy, then hash + install THAT copy so the bytes pip installs
            # are exactly the bytes verified — closes a check-vs-use TOCTOU on the
            # original path (mirrors the pypi --find-links flow).
            dest = tempfile.mkdtemp(prefix="aelix-verify-")
            try:
                staged = Path(dest) / resolved.name
                shutil.copy2(resolved, staged)
                observed = extension_pins.sha256_file(staged)
                decision = extension_pins.decide_generic(
                    existing, observed, mode=mode, repin=repin,
                    label=f"path {resolved.name}", field_name="sha256",
                )
                _print_verify(decision.notice)
                new_args = [*pip_args[:-1], str(staged)]
                pin = (
                    extension_pins.Pin(
                        identity=identity, kind="path", mode=mode,
                        sha256=observed, pinned_at=extension_pins.now_iso(),
                    )
                    if decision.record
                    else None
                )
                return _VerifyResult(new_args, pin, cleanup_dir=dest)
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
        # A directory / editable source has no single stable artifact.
        if strict:
            raise extension_pins.VerifyRefusal(
                "strict mode: a directory/editable path has no stable artifact to "
                "pin — install a built .whl/.tar.gz, or pass --no-verify"
            )
        _print_verify(
            "directory/editable source tree is unverifiable — NOT pinned "
            "(consent remains the only gate)"
        )
        return _VerifyResult(pip_args, None)

    if kind == "git":
        git_sha = _extract_git_sha(pip_args[-1])
        if git_sha is None:
            if strict:
                raise extension_pins.VerifyRefusal(
                    "strict mode: a git target must pin a full 40-hex commit SHA "
                    "(git+URL@<sha>); a mutable branch/tag is refused"
                )
            if existing is not None and existing.git_sha:
                # A pin-stripping downgrade: this repo was pinned to a commit but
                # is now being installed at a mutable ref. tofi proceeds (strict
                # would have refused above) but the recorded pin is NOT enforced.
                _print_verify(
                    f"⚠ previously pinned to commit {existing.git_sha[:12]}… but this "
                    "install uses a MUTABLE ref — pin NOT enforced (use git+URL@<sha>)"
                )
            else:
                _print_verify(
                    "git ref is not pinned to a commit SHA — NOT pinned "
                    "(pin with git+URL@<40-hex-sha>)"
                )
            return _VerifyResult(pip_args, None)
        decision = extension_pins.decide_generic(
            existing, git_sha, mode=mode, repin=repin,
            label=f"git {identity}", field_name="git_sha",
        )
        _print_verify(decision.notice)
        if not decision.record:
            return _VerifyResult(pip_args, None)
        return _VerifyResult(
            pip_args,
            extension_pins.Pin(
                identity=identity, kind="git", mode=mode,
                git_sha=git_sha, pinned_at=extension_pins.now_iso(),
            ),
        )

    # pypi — two-phase download-verify (opt-in in v1; see ADR-0187).
    if not (verify_pypi or strict):
        _print_verify(
            "pypi integrity verification is opt-in this release "
            "(enable with --verify-pypi or --strict) — consent is the gate"
        )
        return _VerifyResult(pip_args, None)

    bare = _bare_package_name(target)
    canonical = extension_pins.canonicalize_name(bare)
    dest = tempfile.mkdtemp(prefix="aelix-verify-")
    try:
        dl_args = build_download_args(
            target, index_url=index_url, extra_index_urls=extra_index_urls, dest=dest
        )
        print(f"  → verify (download): {' '.join(dl_args)}")
        dl_result = runner(dl_args)
        if int(getattr(dl_result, "returncode", 1)) != 0:
            raise extension_pins.VerifyRefusal(
                "pip download failed during verification — not installing"
            )
        artifact = extension_pins.find_top_level_artifact(Path(dest), canonical)
        if artifact is None:
            if strict:
                raise extension_pins.VerifyRefusal(
                    f"could not uniquely locate a downloaded artifact for {target!r} to verify"
                )
            _print_verify(
                "could not uniquely locate the downloaded artifact — NOT pinned; "
                "installing normally"
            )
            shutil.rmtree(dest, ignore_errors=True)
            return _VerifyResult(pip_args, None)
        observed = extension_pins.sha256_file(artifact)
        version = extension_pins.version_from_artifact(artifact.name, canonical)
        decision = extension_pins.decide_pypi(
            existing, observed, version, mode=mode, repin=repin, label=f"pypi {bare}",
        )
        _print_verify(decision.notice)
        new_args = _rewrite_pypi_local(dest, target, upgrade="--upgrade" in pip_args)
        pin = (
            extension_pins.Pin(
                identity=identity, kind="pypi", mode=mode,
                name=bare, version=version,
                sha256=observed, pinned_at=extension_pins.now_iso(),
            )
            if decision.record
            else None
        )
        # The install reads the verified bytes from ``dest``; the caller removes
        # it AFTER run() (a pin is only recorded on install success).
        return _VerifyResult(new_args, pin, cleanup_dir=dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def _record_pin(pin: extension_pins.Pin, agent_dir: str | None) -> None:
    """Persist ``pin`` into the sidecar (re-reads first to avoid clobbering)."""

    pins_path = extension_pins.pins_file_path(agent_dir or get_agent_dir())
    pins = extension_pins.load_pins(pins_path)
    pins[pin.identity] = pin
    extension_pins.save_pins(pins, pins_path)


@dataclass(frozen=True)
class _VerifyOpts:
    """The verify-gate flags, bundled so ``update`` can thread them uniformly."""

    no_verify: bool = False
    strict: bool = False
    repin: bool = False
    verify_pypi: bool = False


# =====================================================================
# === Installed-extension inventory (the pip ledger) ===================
# =====================================================================


@dataclass(frozen=True)
class InstalledExtension:
    """One discovered ``aelix.extensions`` entry point + its distribution."""

    ep_name: str
    dist_name: str | None
    version: str | None


def list_installed_extensions() -> list[InstalledExtension]:
    """Read the installed inventory from ``entry_points(group=...)``.

    This IS the ledger (ADR-0185): every ``aelix extension install`` lands a pip
    distribution whose ``aelix.extensions`` entry point the loader discovers.
    Fully getattr-guarded — a distribution missing ``.dist`` (older metadata)
    degrades to ``dist_name=None`` rather than raising.
    """

    out: list[InstalledExtension] = []
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — never let a bad env abort the listing
        return out
    for ep in eps:
        dist = getattr(ep, "dist", None)
        dist_name = getattr(dist, "name", None) if dist is not None else None
        version = getattr(dist, "version", None) if dist is not None else None
        out.append(
            InstalledExtension(
                ep_name=ep.name,
                dist_name=str(dist_name) if dist_name else None,
                version=str(version) if version else None,
            )
        )
    return out


def _canon(name: str) -> str:
    """Loose distribution-name canonicalization (case + ``_``/``-`` fold)."""

    return name.strip().lower().replace("_", "-")


def _find_distribution_for_name(name: str) -> str | None:
    """Map an extension ``<name>`` (entry-point OR distribution) to its dist.

    Matches, in order: exact/loose entry-point name, then exact/loose
    distribution name. Returns the distribution to hand to ``pip uninstall``, or
    :data:`None` when nothing installed provides ``<name>``.
    """

    target = _canon(name)
    installed = list_installed_extensions()
    for ext in installed:
        if _canon(ext.ep_name) == target:
            return ext.dist_name or ext.ep_name
    for ext in installed:
        if ext.dist_name and _canon(ext.dist_name) == target:
            return ext.dist_name
    return None


def _installed_dist_names() -> set[str]:
    """Snapshot the set of distribution names currently exposing an extension."""

    return {e.dist_name for e in list_installed_extensions() if e.dist_name}


# =====================================================================
# === Source-list persistence (SettingsManager-backed) ================
# =====================================================================


def _load_settings() -> SettingsManager:
    """Build the real settings manager the CLI reads/writes sources through.

    Mirrors the ``--list-models`` construction (entry.py): the SAME
    ``agent_dir`` as auth.json / mcp.json so ``settings.json`` is not split off.
    """

    from aelix_ai.settings import SettingsManager

    from .config import get_agent_dir

    return SettingsManager.create(
        cwd=str(Path.cwd()), agent_dir=Path(get_agent_dir())
    )


def _source_identity(spec: str, kind: str) -> str:
    """A comparable identity for dedupe — paths resolved, git normalized.

    A path is resolved to its absolute form and a git spec is run through
    :func:`_normalize_git_spec` so the SAME repo dedupes whether it arrived via
    ``source add <raw-url>`` (stored normalized) or an install-record (which
    normalizes through ``_install_spec``); without this the two forms
    (``https://x.git`` vs ``git+https://x.git``) would be treated as distinct and
    the repo would appear twice. An ``index`` spec is compared verbatim.
    """

    stripped = spec.strip()
    if kind == "path":
        try:
            return str(Path(stripped).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            return stripped
    if kind == "git":
        return _normalize_git_spec(stripped)
    return stripped


def _index_urls(sources: list[ExtensionSourceObject]) -> list[str]:
    """The registered pip index URLs, in registration order."""

    return [s.spec for s in sources if s.kind == "index" and s.spec]


def _upsert_source(
    sources: list[ExtensionSourceObject],
    spec: str,
    kind: str,
    *,
    name: str | None = None,
) -> tuple[list[ExtensionSourceObject], bool]:
    """Return ``(new_list, changed)`` after adding/refreshing one source.

    Dedupe is by ``(kind, identity)`` (a path resolved to absolute). An existing
    entry is refreshed only when the new call carries a ``name`` the stored one
    lacks (so recording an install can back-fill the dist name onto a source
    that was ``source add``-registered first). Otherwise a duplicate is a no-op.
    """

    from aelix_ai.settings import ExtensionSourceObject

    identity = _source_identity(spec, kind)
    out = list(sources)
    for i, existing in enumerate(out):
        if existing.kind == kind and _source_identity(existing.spec, existing.kind) == identity:
            if name and not existing.name:
                out[i] = ExtensionSourceObject(
                    spec=existing.spec, kind=existing.kind, name=name
                )
                return out, True
            return out, False
    out.append(ExtensionSourceObject(spec=spec, kind=kind, name=name))
    return out, True


def _remove_source(
    sources: list[ExtensionSourceObject], target: str
) -> tuple[list[ExtensionSourceObject], int]:
    """Return ``(new_list, removed_count)`` dropping sources matching ``target``.

    ``target`` matches a source by spec-identity OR by recorded ``name`` — so
    ``source remove <spec>`` and a post-uninstall cleanup by name both work
    through one path. The target is compared against a set of candidate
    identities (verbatim, path-resolved, git-normalized) so removal succeeds
    whether the user typed the raw or the normalized (``git+…`` / absolute) form.
    """

    stripped = target.strip()
    candidates = {stripped, _source_identity(stripped, "path"),
                  _source_identity(stripped, "git")}
    canon = _canon(stripped)
    kept: list[ExtensionSourceObject] = []
    removed = 0
    for s in sources:
        matches = (
            _source_identity(s.spec, s.kind) in candidates
            or (s.name is not None and _canon(s.name) == canon)
        )
        if matches:
            removed += 1
        else:
            kept.append(s)
    return kept, removed


# =====================================================================
# === Subcommand handlers (async — settings writes need a loop) ========
# =====================================================================


async def _persist(settings: SettingsManager, sources: list[ExtensionSourceObject]) -> None:
    """Write the source list and await the (async) flush so it hits disk."""

    settings.set_extension_sources(sources)
    await settings.flush()


async def _cmd_source(
    rest: list[str],
    *,
    settings: SettingsManager,
) -> int:
    """``extension source add|list|remove`` — manage the registered sources.

    No ``input_fn`` — ``source add`` is register-only (add ≠ install), so no
    subcommand here runs pip or prompts for consent.
    """

    if not rest:
        print(f"Error: source requires a subcommand.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    action, args = rest[0], rest[1:]

    if action == "list":
        sources = settings.get_extension_sources()
        if not sources:
            print("No extension sources registered.")
            return 0
        print("Extension sources:")
        for s in sources:
            suffix = f" (installed as {s.name})" if s.name else ""
            print(f"  [{s.kind}] {s.spec}{suffix}")
        return 0

    if action == "add":
        # Only a positional target (+ ignore a stray --yes for symmetry).
        positional = [a for a in args if not a.startswith("-")]
        if len(positional) != 1:
            print(
                f"Error: source add requires exactly one <path|git-url|index-url>.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        target = positional[0]
        kind = classify_source(target)
        if kind is None:
            print(
                f"Error: {target!r} is not a valid source. A source must be a local "
                "path, a git URL, or an http(s) index URL (a bare package name is an "
                "install target, not a source — use `aelix extension install`).",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        # Register-only (owner-decided 2-step: add ≠ install). Store the
        # NORMALIZED spec (path→absolute, git→git+scheme) so dedupe + display +
        # later install/update all agree with what an install-record writes.
        if kind == "path":
            spec = str(Path(target).expanduser().resolve())
        elif kind == "git":
            spec = _normalize_git_spec(target.strip())
        else:  # index
            spec = target.strip()
        sources = settings.get_extension_sources()
        new_sources, changed = _upsert_source(sources, spec, kind)
        if not changed:
            print(f"Source already registered: [{kind}] {spec}")
            return 0
        await _persist(settings, new_sources)
        print(f"Registered source: [{kind}] {spec}")
        if kind == "index":
            print("  Bare-name installs will resolve against this index.")
        else:
            print(f"  Install it with: aelix extension install {spec}")
        return 0

    if action == "remove":
        positional = [a for a in args if not a.startswith("-")]
        if len(positional) != 1:
            print(
                f"Error: source remove requires exactly one target.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        sources = settings.get_extension_sources()
        new_sources, removed = _remove_source(sources, positional[0])
        if removed == 0:
            print(f"No registered source matched {positional[0]!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        await _persist(settings, new_sources)
        print(f"Removed {removed} source(s).")
        return 0

    print(
        f"Error: unknown source subcommand {action!r} (add | list | remove).\n{_USAGE}",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


def _cmd_list() -> int:
    """``extension list`` — the installed inventory (entry-point ledger)."""

    installed = list_installed_extensions()
    if not installed:
        print("No extensions installed (via entry_points).")
        return 0
    print("Installed extensions:")
    for ext in installed:
        dist = f" [{ext.dist_name}]" if ext.dist_name else ""
        version = f" {ext.version}" if ext.version else ""
        print(f"  {ext.ep_name}{version}{dist}")
    return 0


async def _cmd_install(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension install <target>`` — #19 install + resolve + record."""

    parsed = _parse_install_flags(args)
    if isinstance(parsed, int):
        return parsed
    target = parsed.target
    index_url = parsed.index_url

    kind = classify_target(target)
    # Bare-name resolution: with no explicit --index-url, fold the registered
    # index sources into pip's index set (first → --index-url, rest → extra).
    extra_index_urls: list[str] = []
    if kind == "pypi" and index_url is None:
        registered = _index_urls(settings.get_extension_sources())
        if registered:
            index_url = registered[0]
            extra_index_urls = registered[1:]

    before = _installed_dist_names()
    code = install_extension(
        target,
        yes=parsed.yes,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        offline=parsed.offline,
        no_verify=parsed.no_verify,
        strict=parsed.strict,
        repin=parsed.repin,
        verify_pypi=parsed.verify_pypi,
        input_fn=input_fn,
        runner=runner,
    )
    if code == 0:
        await _record_install(settings, target, kind, before)
    return code


async def _record_install(
    settings: SettingsManager,
    target: str,
    kind: TargetKind,
    before: set[str],
) -> None:
    """Record a successful install so ``update`` can reinstall it (best-effort).

    Detects the newly-added distribution name by diffing the entry-point ledger
    before/after (``importlib.invalidate_caches`` first — a same-process install
    otherwise stays invisible to ``importlib.metadata``). Any failure here is
    swallowed: a missed record only degrades ``update``, never the install.
    """

    try:
        importlib.invalidate_caches()
        new_names = sorted(_installed_dist_names() - before)
        detected = new_names[0] if new_names else None
        if kind == "pypi":
            spec = _bare_package_name(target)
            name = detected or spec or None
            record_kind: SourceKind = "pypi"
        else:
            spec = _install_spec(target, kind)
            name = detected
            record_kind = kind  # "git" | "path"
        if not spec:
            return
        sources = settings.get_extension_sources()
        new_sources, changed = _upsert_source(sources, spec, record_kind, name=name)
        if changed:
            await _persist(settings, new_sources)
    except Exception as exc:  # noqa: BLE001 — recording is best-effort
        print(f"Warning: could not record install source: {exc}", file=sys.stderr)


async def _cmd_update(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension update [<name>]`` — reinstall recorded source(s) --upgrade."""

    name_filter: str | None = None
    yes = False
    offline = False
    no_verify = False
    strict = False
    repin = False
    verify_pypi = False
    for a in args:
        if a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
        elif a == "--no-verify":
            no_verify = True
        elif a == "--strict":
            strict = True
        elif a == "--repin":
            repin = True
        elif a == "--verify-pypi":
            verify_pypi = True
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif name_filter is None:
            name_filter = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN

    verify = _VerifyOpts(
        no_verify=no_verify, strict=strict, repin=repin, verify_pypi=verify_pypi
    )
    sources = settings.get_extension_sources()
    index_urls = _index_urls(sources)
    # Installable records only — an ``index`` source is a resolution hint, not a
    # thing to upgrade on its own.
    installable = [s for s in sources if s.kind in ("git", "path", "pypi")]

    if name_filter is not None:
        matched = [
            s
            for s in installable
            if (s.name is not None and _canon(s.name) == _canon(name_filter))
            or _source_identity(s.spec, s.kind) == name_filter.strip()
        ]
        if not matched:
            # Not recorded — treat <name> as a pypi package and upgrade it
            # against the registered index sources (covers a name install that
            # was never recorded, e.g. installed before this feature).
            return _upgrade_pypi_name(
                name_filter,
                index_urls,
                yes=yes,
                offline=offline,
                verify=verify,
                input_fn=input_fn,
                runner=runner,
            )
        targets = matched
    else:
        targets = installable
        if not targets:
            print("No recorded extension sources to update.")
            return 0

    worst = 0
    for s in targets:
        code = _upgrade_source(
            s,
            index_urls,
            yes=yes,
            offline=offline,
            verify=verify,
            input_fn=input_fn,
            runner=runner,
        )
        worst = worst or code  # first nonzero wins (report the earliest failure)
    return worst


def _upgrade_source(
    source: ExtensionSourceObject,
    index_urls: list[str],
    *,
    yes: bool,
    offline: bool,
    verify: _VerifyOpts,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """Reinstall one recorded source with ``--upgrade``."""

    if source.kind == "pypi":
        target = source.name or _bare_package_name(source.spec)
        return _upgrade_pypi_name(
            target,
            index_urls,
            yes=yes,
            offline=offline,
            verify=verify,
            input_fn=input_fn,
            runner=runner,
        )
    # git / path: the spec is directly installable.
    return install_extension(
        source.spec,
        yes=yes,
        offline=offline,
        upgrade=True,
        no_verify=verify.no_verify,
        strict=verify.strict,
        repin=verify.repin,
        verify_pypi=verify.verify_pypi,
        input_fn=input_fn,
        runner=runner,
    )


def _upgrade_pypi_name(
    name: str,
    index_urls: list[str],
    *,
    yes: bool,
    offline: bool,
    verify: _VerifyOpts,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``pip install --upgrade <name>`` against the registered index sources."""

    index_url = index_urls[0] if index_urls else None
    extra = index_urls[1:] if index_urls else []
    return install_extension(
        name,
        yes=yes,
        index_url=index_url,
        extra_index_urls=extra,
        offline=offline,
        upgrade=True,
        no_verify=verify.no_verify,
        strict=verify.strict,
        repin=verify.repin,
        verify_pypi=verify.verify_pypi,
        input_fn=input_fn,
        runner=runner,
    )


async def _cmd_remove(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension remove <name>`` — pip uninstall + drop the recorded source."""

    name: str | None = None
    yes = False
    for a in args:
        if a in ("-y", "--yes"):
            yes = True
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif name is None:
            name = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
    if name is None or not name.strip():
        print(f"Error: remove requires an extension <name>.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    dist = _find_distribution_for_name(name)
    if dist is None:
        print(
            f"Error: no installed extension provides {name!r} "
            f"(via entry_points group '{ENTRY_POINT_GROUP}').",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    if not _pip_available(runner):
        print(_pip_missing_message(), file=sys.stderr)
        return _EXIT_DIDNT_RUN

    pip_args = [sys.executable, "-m", "pip", "uninstall", "-y", dist]
    print(f"Remove extension: {name} → uninstall distribution {dist}")
    print(f"  → {' '.join(pip_args)}")
    if not yes:
        try:
            reply = input_fn("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return _EXIT_DIDNT_RUN

    run = runner if runner is not None else _default_runner
    result = run(pip_args)
    code = int(getattr(result, "returncode", 1))
    if code != 0:
        print(f"pip uninstall failed (exit {code}).", file=sys.stderr)
        return code
    # Drop any recorded source for this name/dist (best-effort — a missed
    # cleanup only leaves a stale Sources row, never fails the removal).
    try:
        sources = settings.get_extension_sources()
        new_sources, removed = _remove_source(sources, name)
        if removed == 0 and dist != name:
            new_sources, removed = _remove_source(new_sources, dist)
        if removed:
            await _persist(settings, new_sources)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not update source list: {exc}", file=sys.stderr)
    print("Removed. Restart aelix (or /reload in the TUI) so the loader drops it.")
    return 0


# =====================================================================
# === Flag parsing for `install` (shared by sync + async entries) ======
# =====================================================================


@dataclass(frozen=True)
class _InstallFlags:
    """Parsed ``install`` arguments (also carried into ``verify_and_pin``)."""

    target: str
    yes: bool
    offline: bool
    index_url: str | None
    no_verify: bool
    strict: bool
    repin: bool
    verify_pypi: bool


def _parse_install_flags(rest: list[str]) -> _InstallFlags | int:
    """Parse ``install`` args → :class:`_InstallFlags`, a help code, or an error."""

    target: str | None = None
    yes = False
    offline = False
    index_url: str | None = None
    no_verify = False
    strict = False
    repin = False
    verify_pypi = False
    only_positional = False  # set once a bare ``--`` is seen
    i = 0
    while i < len(rest):
        a = rest[i]
        if only_positional or not a.startswith("-"):
            if target is None:
                target = a
            else:
                print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
                return _EXIT_DIDNT_RUN
        elif a == "--":
            only_positional = True
        elif a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
        elif a == "--no-verify":
            no_verify = True
        elif a == "--strict":
            strict = True
        elif a == "--repin":
            repin = True
        elif a == "--verify-pypi":
            verify_pypi = True
        elif a == "--index-url":
            i += 1
            if i >= len(rest) or not rest[i]:
                print("Error: --index-url requires a URL.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            index_url = rest[i]
        elif a.startswith("--index-url="):
            value = a.split("=", 1)[1]
            if not value:
                print("Error: --index-url requires a URL.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            index_url = value
        elif a in ("-h", "--help"):
            print(_USAGE)
            return 0
        else:
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    if target is None or not target.strip():
        print(f"Error: install requires a non-empty target.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    return _InstallFlags(
        target=target,
        yes=yes,
        offline=offline,
        index_url=index_url,
        no_verify=no_verify,
        strict=strict,
        repin=repin,
        verify_pypi=verify_pypi,
    )


# =====================================================================
# === Public dispatch ==================================================
# =====================================================================


async def run_extension_command_async(
    args: list[str],
    *,
    settings: SettingsManager | None = None,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Dispatch ``aelix extension <subcommand> …`` (the async implementation).

    ``settings`` is the source-list store; :data:`None` builds the real
    ``SettingsManager`` (production). Tests inject ``SettingsManager.in_memory()``
    to stay off disk. Must run inside an event loop — the settings write path is
    async and each mutating handler ``await``s ``settings.flush()``.
    """

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else _EXIT_DIDNT_RUN
    sub, rest = args[0], args[1:]

    # ``list`` needs no settings — answer it before any settings construction.
    if sub == "list":
        return _cmd_list()

    if settings is None:
        settings = _load_settings()
        for err in settings.drain_errors():
            print(f"Warning: settings ({err.scope}): {err.error}", file=sys.stderr)

    if sub == "install":
        return await _cmd_install(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )
    if sub == "source":
        return await _cmd_source(rest, settings=settings)
    if sub == "update":
        return await _cmd_update(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )
    if sub == "remove":
        return await _cmd_remove(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )

    print(
        f"Error: unknown extension subcommand {sub!r} "
        "(install | source | list | update | remove).\n"
        f"{_USAGE}",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


def run_extension_command(
    args: list[str],
    *,
    settings: SettingsManager | None = None,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Synchronous entry for ``aelix extension …`` (wraps the async dispatch).

    Provided for direct/non-async callers and tests. The live CLI dispatches via
    :func:`run_extension_command_async` directly (it is already inside the
    ``_async_main`` event loop — a nested :func:`asyncio.run` would raise).
    """

    return asyncio.run(
        run_extension_command_async(
            args, settings=settings, input_fn=input_fn, runner=runner
        )
    )
