"""Issue #19 (ADR-0185) — ``aelix extension install <path|git|pypi>``.

A minimal, closed-network-native extension installer. All three source kinds
resolve to a single ``pip install`` into the RUNNING interpreter's environment
(``sys.executable -m pip``), so the plugin's module becomes importable AND its
``entry_points(group="aelix.extensions")`` registration is discovered by the
loader's Tier-4 pass — no bespoke registry, no ``sys.path`` machinery. pip's own
``--index-url`` / ``git+file`` / ``ssh`` carry the self-hosted / air-gapped
requirement, and pip itself is the install ledger (a future ``list``/``remove``
reads ``importlib.metadata`` entry points — no separate record needed).

pip runs the package's build/setup code, so consent is **source-level** (shown +
y/N, deny-by-default; ``--yes`` for headless) — a manifest *capability* gate is
impossible here because the manifest lives inside the not-yet-built package.

pi parity: this is the Python-ecosystem swap of pi's ``npm install`` package
model (``package-manager.ts``) — pip replaces npm, ``entry_points`` replaces the
``PiManifest`` package root, ``--index-url`` replaces ``.npmrc``.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

_USAGE = (
    "usage: aelix extension install <path | git-url | package[==version]> "
    "[--yes] [--index-url URL] [--offline]"
)

# Exit codes: 0 = installed; the pip returncode (usually 1) = pip ran and
# failed; 2 = did NOT run pip (usage error, guard refusal, user abort, missing
# pip). The 3-way split lets a script tell "pip failed" from "never ran"
# (review LOW) — a deliberate divergence from the repo's return-1-for-errors
# convention toward the standard-CLI usage-error code (documented, ADR-0185).
_EXIT_DIDNT_RUN = 2

TargetKind = Literal["path", "git", "pypi"]

# A subprocess runner injectable for tests (default = the real pip call).
PipRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]

__all__ = [
    "PipRunner",
    "TargetKind",
    "build_pip_args",
    "classify_target",
    "install_extension",
    "run_extension_command",
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


def build_pip_args(
    target: str, kind: TargetKind, *, index_url: str | None
) -> list[str]:
    """Build the ``python -m pip install …`` argv for a classified target."""

    base = [sys.executable, "-m", "pip", "install"]
    if kind == "path":
        return [*base, str(Path(target).expanduser().resolve())]
    if kind == "git":
        return [*base, _normalize_git_spec(target)]
    # pypi
    args = [*base, target]
    if index_url:
        args += ["--index-url", index_url]
    return args


def _is_offline(explicit: bool) -> bool:
    # Mirror the CLI's --offline / PI_OFFLINE contract (entry.py) + an aelix
    # alias. Strict truthiness so PI_OFFLINE=0 reads as OFF (review NIT).
    return explicit or _env_truthy("PI_OFFLINE") or _env_truthy("AELIX_OFFLINE")


def install_extension(
    target: str,
    *,
    yes: bool = False,
    index_url: str | None = None,
    offline: bool = False,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Install one extension via ``pip``; return a process-style exit code.

    ``0`` on success, ``1`` on user-abort / pip failure, ``2`` on a usage/guard
    error. ``runner`` and ``input_fn`` are injectable for tests.
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

    # pip must be importable on THIS interpreter for the DEFAULT runner (a
    # missing pip makes ``python -m pip`` exit nonzero WITHOUT raising, so a
    # returncode check would mislabel it as an install failure — review LOW).
    # Skipped when a custom runner is injected (tests / an alt package manager).
    # NOTE: uv-managed venvs often ship without pip — hence the actionable hint.
    if runner is None and importlib.util.find_spec("pip") is None:
        print(
            f"Error: pip is not available on this interpreter ({sys.executable}). "
            "Install it (e.g. `python -m ensurepip --upgrade`, or `uv pip install "
            "pip` in a uv project) and retry.",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    pip_args = build_pip_args(target, kind, index_url=index_url)

    # Consent — pip runs the package's build/setup code (arbitrary at install
    # time), so the manifest capability gate cannot protect this path; the
    # source-level y/N IS the trust boundary. Deny-by-default (headless without
    # --yes, or a closed stdin, aborts).
    print(f"Install extension from {kind}: {target}")
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
    result = run(pip_args)
    code = int(getattr(result, "returncode", 1))
    if code == 0:
        print(
            "Installed. Restart aelix (or /reload in the TUI) so the loader "
            "discovers it via entry_points."
        )
    else:
        print(f"pip install failed (exit {code}).", file=sys.stderr)
    return code


def _default_runner(pip_args: list[str]) -> subprocess.CompletedProcess[bytes]:
    # Inherit stdio so the user sees pip's live progress; never shell=True.
    return subprocess.run(pip_args, check=False)  # noqa: S603 — argv list, no shell


def run_extension_command(
    args: list[str],
    *,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Dispatch ``aelix extension <subcommand> …``. v1 = ``install`` only."""

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else _EXIT_DIDNT_RUN
    sub, rest = args[0], args[1:]
    if sub != "install":
        print(
            f"Error: unknown extension subcommand {sub!r} (v1 supports 'install').\n"
            f"{_USAGE}",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    target: str | None = None
    yes = False
    offline = False
    index_url: str | None = None
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
            # End-of-options: everything after is a positional target (lets a
            # path that begins with '-' be installed — review NIT).
            only_positional = True
        elif a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
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
    return install_extension(
        target,
        yes=yes,
        index_url=index_url,
        offline=offline,
        input_fn=input_fn,
        runner=runner,
    )
