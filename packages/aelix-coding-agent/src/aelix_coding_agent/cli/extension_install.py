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

Issue #113 replaces the hardcoded ``sys.executable -m pip`` assumption with an
:class:`InstallBackend` (:func:`resolve_install_backend`). The official install path
is ``uv tool install``, whose venv ships **without pip**, so every install/update/
remove aborted before the consent prompt for anyone who followed the README. pip is
still preferred (it alone can ``download``, hence verify); ``uv pip … --python
<interp>`` is the install/uninstall-only fallback, and a pypi verification request
that the uv backend cannot execute REFUSES rather than installing unverified.

Two hardening rules ride along with that fallback, because the uv backend is the
DEFAULT on the official install path and must therefore meet pip's security bar:
the ``uv`` executable is only ever accepted as an ABSOLUTE path (a relative
``shutil.which`` hit would let a cwd binary run as the installer), and pip's
ambient index configuration — which uv does not read — is translated into explicit
``--index-url`` / ``--extra-index-url`` flags so an org's mirror pin survives the
switch instead of silently falling back to public PyPI.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse, urlsplit, urlunsplit

from ..extensions.ep_manifest import (
    EpApiLevelRefusal,
    EpOutcome,
    entry_point_provenance,
    environment_site_dirs,
    resolve_entry_point_manifest,
)
from . import extension_catalog, extension_pins, extension_signing
from .config import get_agent_dir

if TYPE_CHECKING:
    from aelix_ai.settings import ExtensionSourceObject, SettingsManager

_USAGE = (
    "usage: aelix extension <command>\n"
    "  install <path | git-url | package[==version]>  [--yes] [--index-url URL] "
    "[--offline] [--no-verify] [--strict] [--repin] [--verify-pypi] "
    "[--require-signature] [--trusted-key ID] [--signature PATH] "
    "[--trust-extension-path DIST]\n"
    "  source add <path | git-url | index-url>        [--yes]\n"
    "  source add --catalog <url | file | git>\n"
    "  source list\n"
    "  source remove <path | git-url | index-url>\n"
    "  list\n"
    "  verify [<name>]                                [--trust-extension-path DIST]\n"
    "  index <dir>                                    [--out FILE] [--name NAME] "
    "[--relative]\n"
    "  discover [<query>]                             [--refresh] [--offline] "
    "[--no-default-catalog]\n"
    "  discover install <name>                        [--catalog CAT] [--yes] "
    "[--index-url URL] [--offline] [--no-verify] [--strict] [--repin] [--verify-pypi] "
    "[--require-signature] [--trusted-key ID] [--signature PATH] "
    "[--trust-extension-path DIST]\n"
    "  update [<name>]                                [--yes] [--offline] "
    "[--no-verify] [--strict] [--repin] [--verify-pypi] [--require-signature] "
    "[--trusted-key ID] [--signature PATH]\n"
    "  remove <name>                                  [--yes]\n"
    "  keygen                                         [--label L] [--passphrase] "
    "[--force] [--out DIR]\n"
    "  sign <artifact> --key <keyId|pem>              [--name N] [--version V] "
    "[--kind path|pypi] [--out FILE]\n"
    "  trust add <keyId> --public-key <b64>           [--label L] [--source S] [--yes]\n"
    "  trust list | remove <keyId> | revoke <keyId>"
)

# Exit codes: 0 = success; 1 = the installer ran and FAILED; 2 = did NOT run the
# installer (usage error, guard refusal, user abort, missing pip). The 3-way
# split lets a script tell "pip failed" from "never ran" (ADR-0185).
_EXIT_DIDNT_RUN = 2

#: The installer RAN and failed. Its own returncode is PRINTED, never returned —
#: because pip's exit codes are not disjoint from this verb's. ``pip`` defines
#: ``VIRTUALENV_NOT_FOUND = 3`` (raised under ``--require-venv`` /
#: ``PIP_REQUIRE_VIRTUALENV``, which orgs set globally), which is exactly
#: :data:`_INSTALL_NOT_BOUND`; and ``UNKNOWN_ERROR = 2``, which is exactly
#: :data:`_EXIT_DIDNT_RUN` (that collision predates #154). Passing pip's code
#: through therefore made the documented 4-way split conditional on which codes
#: pip happened to pick: a script reading 3 could not tell "installed but inert"
#: (something IS on disk) from "pip refused to run at all" (nothing is) — the
#: opposite conclusion about the same machine. Normalising here is what makes
#: ":data:`_INSTALL_NOT_BOUND`'s premise — 1 already means the installer ran and
#: failed" true rather than merely usual. MEASURED:
#: ``PIP_REQUIRE_VIRTUALENV=1 python -m pip install --dry-run nothing-xyz`` → 3.
_INSTALLER_FAILED = 1

# ``extension verify`` exit codes (issue #91, ADR-0207) — STABLE, a CI gate keys
# on them: 0 = every reported endpoint's manifest is BOUND; 1 = at least one is
# not (ABSENT / MALFORMED / MISPLACED / FENCED / UNPROVEN / UNTRUSTED / API
# refusal), so the loader would drop that pack's declarative contributions;
# 2 (``_EXIT_DIDNT_RUN``) = it did not get as far as verifying (usage error, or a
# named target that matches nothing installed).
_VERIFY_NOT_BOUND = 1

#: ``install`` / ``discover install`` / ``update`` exit code (issue #154): the
#: installer DID put the distribution on disk, but the host's own import-free
#: resolver — the SAME primitive ``verify`` reads — says at least one endpoint of
#: the pack just written will not bind its manifest.
#:
#: ``update`` returns it for the same fact and therefore the same code: "updated,
#: and now inert" is "installed, and inert" — an upgrade is an install, the
#: evidence is identical, and a verb-dependent code would put back exactly the
#: ambiguity the normalisation below removed. It also keeps ``update && restart``
#: stopping on the one outcome where restarting cannot help.
#:
#: A DISTINCT code rather than ``_VERIFY_NOT_BOUND`` (1) on purpose. 1 means
#: "the installer ran and FAILED" on this verb (:data:`_INSTALLER_FAILED`, which
#: normalises pip's own code precisely so that premise holds unconditionally),
#: and ADR-0185 exists so a script can tell that apart from "pip never ran" (2).
#: Collapsing "installed but inert" onto 1 would destroy that split and tell a
#: script nothing landed on disk, when something did. 3 keeps 0 =
#: installed-and-bindable, so an existing ``install && …`` chain still stops —
#: which is the point — while a script that cares can distinguish the three
#: failure shapes.
#:
#: The GATE matches ``verify``'s exactly: non-zero iff some reported endpoint is
#: not ``BOUND``. The two commands ran the same primitive and disagreed in what
#: they told the user; after #154 they agree in verdict, in wording, and in gate.
_INSTALL_NOT_BOUND = 3

#: The post-install line for a pack with nothing to report. A module constant so
#: :func:`install_extension` (which prints it for the public seam's callers) and
#: the ``install`` / ``update`` verdict reporter cannot drift apart.
_INSTALLED_RESTART = (
    "Installed. Restart aelix (or /reload in the TUI) so the loader "
    "discovers it via entry_points."
)

#: The post-install line when this command HAS no verdict — attribution came back
#: empty, so there was no distribution to classify (issue #154 round 2).
#:
#: :data:`_INSTALLED_RESTART` is a promise: the loader will discover this pack via
#: entry_points. Printing it for a pack we did not identify makes that promise on
#: no evidence — and it is exactly the promise #154 exists to stop making. It was
#: still reachable after the first fix on two narrower paths: a repeat
#: ``install git+URL`` (a git URL names no distribution and a no-op reinstall
#: moves nothing in the entry-point ledger), and a repeat ``install <dir>`` of a
#: tree whose name is unreadable without executing it (setup.py-only, a
#: ``[tool.poetry]`` name, a ``dynamic`` name). Same command, same pack, same
#: environment, opposite answers — decided only by whether pip happened to move
#: the ledger. It also covers the target that is simply not an extension: a dist
#: with no ``aelix.extensions`` endpoint is one the loader will NEVER discover,
#: which "Restart aelix … so the loader discovers it" flatly denies.
#:
#: Parsing pip's "Successfully installed …" would not rescue those: a no-op
#: install prints "Requirement already satisfied" instead, so an output-parsing
#: scheme still needs this floor.
#:
#: The exit code stays 0 deliberately. 0 on this verb has always meant "the
#: installer put it on disk", and it did; :data:`_INSTALL_NOT_BOUND` must keep
#: meaning "this build KNOWS it will not bind", which is a claim we cannot make
#: here. Failing on "unknown" would invent a verdict in the other direction — the
#: symmetric defect — and would break every ``install && …`` chain for packs that
#: are in fact fine. What changes is the SENTENCE: it no longer claims the pack
#: will load, and it names the command that can settle it.
_INSTALLED_NO_VERDICT = (
    "Installed, but this build has no binding verdict for it: this command could "
    "not tie the install to an installed extension distribution, so it had "
    "nothing to classify. This is NOT a claim that the pack will load.\n"
    "To get the verdict: aelix extension verify"
)

#: The entry-point group the loader's Tier-4 pass discovers (loader.py:1475).
ENTRY_POINT_GROUP = "aelix.extensions"

TargetKind = Literal["path", "git", "pypi"]
#: The kinds a registered *source* can take. ``index`` = a pip index URL used
#: only to RESOLVE a bare-name install (never installed directly); ``git`` /
#: ``path`` = a directly-installable extension; ``pypi`` = an install RECORD of
#: a bare-name install (spec = the package, kept so ``update`` can reinstall it);
#: ``catalog`` = an ADVISORY discover-catalog document (#65/ADR-0188 — a
#: ``file`` / ``https`` / ``git`` location browsed by ``extension discover``,
#: never installed or upgraded directly; ``update``'s installable filter skips it).
SourceKind = Literal["index", "git", "path", "pypi", "catalog"]

# A subprocess runner injectable for tests (default = the real pip call).
PipRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]

__all__ = [
    "ENTRY_POINT_GROUP",
    "AmbientIndexConfig",
    "EndpointStatus",
    "InstallBackend",
    "InstalledExtension",
    "PipRunner",
    "classify_installed_endpoints",
    "SourceKind",
    "TargetKind",
    "build_download_args",
    "build_pip_args",
    "classify_source",
    "classify_target",
    "display_argv",
    "install_extension",
    "list_installed_extensions",
    "read_pip_index_config",
    "resolve_install_backend",
    "run_extension_command",
    "run_extension_command_async",
    "uv_ambient_index_config",
    "uv_ambient_index_env",
    "verify_and_pin",
]


def _env_truthy(name: str) -> bool:
    # Strict 1/true/yes/on (case-insensitive) — so ``PI_OFFLINE=0`` reads as OFF,
    # not "any non-empty" (review NIT). Mirrors the canonical env-flag idiom.
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def classify_target(target: str) -> TargetKind:
    """Classify an install target as a local path, a git URL, or a pypi spec.

    A local path WINS if it exists on disk (so ``./my-ext`` beats any URL
    heuristic); otherwise git-URL shapes classify as git:

    * a ``git+…`` VCS spec, or a ``git://`` / ``ssh://`` / ``git@`` transport;
    * anything ending in ``.git`` (the common ``https://host/o/r.git`` form);
    * an http(s) URL whose **PATH** ends in ``.git`` once pip's ``@<rev>`` suffix
      and any trailing slash are stripped — so ``…/r.git/`` (trailing slash),
      ``…/r.git?ref=main`` (query), ``…/r.git@<sha>`` (pinned revision) and
      ``…/r.git#egg=x`` (fragment) are all git.

    Everything else is a pypi package spec (``name`` / ``name==1.2`` /
    ``name[extra]``).

    The http(s) test is anchored to the parsed PATH and never sees the netloc.
    A bare ``".git" in url`` substring test used to match the HOST too, so every
    catalog served from a ``*.github.io`` host — including
    :data:`extension_catalog.DEFAULT_CATALOG_URL` — was misrouted to ``git`` and
    the built-in marketplace catalog was 100% unfetchable (#111 A-1).
    """

    if target.strip() and Path(target).expanduser().exists():
        return "path"
    low = target.lower()
    if (
        target.startswith("git+")
        or low.startswith(("git://", "ssh://", "git@"))
        or low.endswith(".git")
        or _http_url_path_is_git(low)
    ):
        return "git"
    return "pypi"


def _http_url_path_is_git(low: str) -> bool:
    """True when ``low`` is an http(s) URL whose PATH (not host) ends in ``.git``.

    Anchoring on the parsed path is the whole point: a bare ``".git" in url``
    substring test also matched the HOST, misrouting every ``*.github.io`` catalog —
    the built-in default among them — to a git clone (#111 A-1). ``urlparse`` drops
    the query string and the ``#egg=…`` fragment for us; the trailing slash and pip's
    ``@<rev>`` suffix are stripped here so ``…/r.git/``, ``…/r.git?ref=main`` and
    ``…/r.git@<sha>`` all still read as git.

    The ``@<rev>`` strip is deliberately a TRAILING-only one: a ``path.split("@", 1)``
    truncates at the FIRST ``@`` anywhere in the path, so a perfectly ordinary git URL
    whose path merely CONTAINS one — ``https://gitea.corp/team@eu/ext.git/``, a
    ``~user@dept`` path, a ``…/r@1.git`` version-tagged segment — lost its ``.git``
    suffix and was misrouted to ``pypi`` (``pip install <the raw url>`` instead of
    ``git+…``, and on the catalog side an https GET instead of a clone). Splitting off
    the LAST ``@`` and accepting the strip ONLY when it exposes ``.git`` keeps the
    revision-pin case working without touching any other ``@``.
    """

    if not low.startswith(("http://", "https://")):
        return False
    path = urlparse(low).path.rstrip("/")
    if path.endswith(".git"):
        return True
    head, sep, _rev = path.rpartition("@")
    return bool(sep) and head.rstrip("/").endswith(".git")


def classify_source(target: str) -> SourceKind | None:
    """Classify a ``source add`` target as ``path`` / ``git`` / ``index``.

    Reuses :func:`classify_target`'s path + git heuristics, then maps a *plain*
    http(s) URL (one :func:`classify_target` would call ``pypi`` because its
    PATH does not end in ``.git``) to ``index`` — a pip package index. That is
    the branch a catalog URL such as :data:`extension_catalog.DEFAULT_CATALOG_URL`
    takes. Returns :data:`None` for a
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
    backend: InstallBackend | None = None,
) -> list[str]:
    """Build the install argv for a classified target, in ``backend``'s dialect.

    ``index_url`` / ``extra_index_urls`` apply to a ``pypi`` target only (a
    bare-name install resolved against registered index sources — the first
    index becomes ``--index-url``, the rest ``--extra-index-url``). ``upgrade``
    adds ``--upgrade`` (used by ``extension update``).

    ``backend`` defaults to :data:`PIP_BACKEND` (``python -m pip install …``);
    the ``uv`` backend swaps only the PREFIX (``uv pip install --python <interp>``)
    — every *flag* after it is spelled identically by both, which is what makes one
    builder serve both (#113).

    The flags match, but the ambient CONFIGURATION each tool reads does not: pip
    honors ``PIP_INDEX_URL`` / ``PIP_EXTRA_INDEX_URL`` / ``pip.conf``, uv honors none
    of them. That translation is real but it does NOT happen here: an ambient index
    URL routinely carries a basic-auth password (the standard Nexus/Artifactory form)
    and argv is world-readable, so the uv backend receives it through the ENVIRONMENT
    instead (:func:`uv_ambient_index_env`). This builder's output therefore contains
    only values the user typed — and is byte-identical to the pre-#113 pip argv for
    the pip backend.
    """

    base = (backend or PIP_BACKEND).install_prefix()
    if upgrade:
        base.append("--upgrade")
    if kind == "path":
        return [*base, str(Path(target).expanduser().resolve())]
    if kind == "git":
        return [*base, _normalize_git_spec(target)]
    # pypi
    extras = list(extra_index_urls or ())
    args = [*base, target]
    if index_url:
        args += ["--index-url", index_url]
    for extra in extras:
        args += ["--extra-index-url", extra]
    return args


def _is_offline(explicit: bool) -> bool:
    # Mirror the CLI's --offline / PI_OFFLINE contract (entry.py) + an aelix
    # alias. Strict truthiness so PI_OFFLINE=0 reads as OFF (review NIT).
    return explicit or _env_truthy("PI_OFFLINE") or _env_truthy("AELIX_OFFLINE")


def _is_offline_fetchable(loc: str) -> bool:
    """True when a catalog location may be fetched while ``--offline`` (guard ④).

    An ALLOWLIST of no-egress / air-gap transports — the documented offline-fetch set
    (ADR-0192): ``file://``, ``git+file://``, ``git+ssh://`` (an intranet SSH remote),
    and a bare local filesystem path (stored specs are normalized to an absolute path).
    EVERY other transport is a remote fetch and is skipped offline: ``https://`` /
    ``git+https://`` and, critically, the unencrypted git-daemon transports
    ``git://`` / ``git+git://`` (which a 2-item ``https`` blocklist let egress while
    ``--offline`` — the leak this allowlist closes). A stored git target is always
    normalized to ``git+<transport>://`` (:func:`_normalize_git_spec`), so a
    scheme-bearing non-allowlisted location is treated as remote.
    """

    low = loc.strip().lower()
    if low.startswith(("file://", "git+ssh://", "git+file://")):
        return True  # an explicit air-gap URL scheme
    # No scheme → a bare local filesystem path (allowed); any OTHER scheme-bearing
    # location is a remote transport (https / git+https / git+git / ssh / …) → skip.
    return not ("://" in low or low.startswith(("git+", "git@", "ssh://")))


# =====================================================================
# === #113: the installer BACKEND (pip preferred, uv fallback) =========
# =====================================================================

#: The package managers that can install into the RUNNING interpreter's env.
BackendName = Literal["pip", "uv"]


@dataclass(frozen=True)
class InstallBackend:
    """How aelix drives a package manager against ``sys.executable``'s environment.

    The official install path is ``uv tool install`` (``install.sh``), and a **uv
    tool venv ships WITHOUT pip** — so ``python -m pip`` is simply absent for
    everyone who followed the README, and every ``extension install`` / ``update``
    / ``discover install`` aborted before the consent prompt (#113).

    Two implementations, selected by :func:`resolve_install_backend`:

    * ``pip`` — FULL capability: install, uninstall, and the ``pip download``
      half of the #64 two-phase pypi verify gate. Preferred whenever
      ``importlib.util.find_spec("pip")`` resolves.
    * ``uv`` — install + uninstall ONLY, via ``uv pip … --python <interp>``.
      Selected when pip is absent and a ``uv`` executable is on PATH.

    The asymmetry is real, not conservatism: ``uv pip`` offers
    compile/sync/install/uninstall/freeze/list/show/tree/check and has **no
    ``download`` subcommand** (verified against the shipped uv 0.11.14), so the
    "fetch the closure → hash it → install the verified bytes" flow that
    :func:`build_download_args` implements cannot be reproduced. A verification
    that cannot run must FAIL CLOSED — see :func:`uv_pypi_verify_unsupported`.

    ``uv_path`` is REQUIRED and must be ABSOLUTE for the ``uv`` backend. The pip
    backend shells out to ``sys.executable``, which is always absolute; a uv
    backend carrying a bare name (or a relative hit from a ``.``/empty/relative
    ``PATH`` entry, which ``shutil.which`` happily returns) would let a file in the
    current working directory be executed as the installer — CWE-426, untrusted
    search path, triggered merely by ``cd``-ing into a cloned repo. The invariant is
    enforced here so no code path can construct a PATH-searched backend.
    """

    name: BackendName
    #: Absolute path to the ``uv`` executable — REQUIRED for the ``uv`` backend.
    uv_path: str | None = None

    def __post_init__(self) -> None:
        if self.name != "uv":
            return
        if not self.uv_path or not os.path.isabs(self.uv_path):
            raise ValueError(
                "InstallBackend(name='uv') requires an ABSOLUTE uv_path; got "
                f"{self.uv_path!r}. A bare or relative program name would be "
                "resolved against PATH (and therefore against the current working "
                "directory for a `.`/empty/relative PATH entry) at exec time."
            )

    @property
    def supports_download(self) -> bool:
        """True when this backend can fetch a dependency closure into a directory.

        ``pip download`` only. Gates the pypi half of :func:`verify_and_pin`.
        """

        return self.name == "pip"

    @property
    def label(self) -> str:
        """A short human name for messages (``pip`` / ``uv``)."""

        return self.name

    def install_prefix(self) -> list[str]:
        """The argv prefix that installs into this interpreter's environment."""

        if self.name == "uv":
            # __post_init__ guarantees an absolute uv_path — never a PATH lookup.
            return [str(self.uv_path), "pip", "install", "--python", sys.executable]
        return [sys.executable, "-m", "pip", "install"]

    def uninstall_args(self, dist: str) -> list[str]:
        """The argv that uninstalls ``dist`` from this interpreter's environment.

        ``uv pip uninstall`` never prompts, so it takes no ``-y`` (passing one
        would be an unknown-flag error); pip's ``-y`` stays required.
        """

        if self.name == "uv":
            return [
                str(self.uv_path),
                "pip",
                "uninstall",
                "--python",
                sys.executable,
                dist,
            ]
        return [sys.executable, "-m", "pip", "uninstall", "-y", dist]


#: The canonical full-capability backend (also what an injected runner implies).
PIP_BACKEND = InstallBackend(name="pip")


# ---------------------------------------------------------------------
# --- ambient index configuration: pip reads it, uv does not ----------
# ---------------------------------------------------------------------

#: uv's OWN index environment. When any of these is set the user has configured
#: uv deliberately, so aelix does not overlay pip's ambient config on top of it.
#: ``UV_CONFIG_FILE`` / ``UV_NO_INDEX`` / ``UV_FIND_LINKS`` count too: each one is a
#: deliberate uv resolution decision that a translated pip value would override.
_UV_INDEX_ENV = (
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_CONFIG_FILE",
    "UV_NO_INDEX",
    "UV_FIND_LINKS",
)

#: Keys in a ``uv.toml`` / ``[tool.uv]`` table that constitute uv's own index config.
_UV_CONFIG_INDEX_KEYS = (
    "index",
    "index-url",
    "extra-index-url",
    "default-index",
    "find-links",
    "no-index",
)

#: The uv env vars aelix SETS on the child so a translated pip index never has to be
#: spelled on argv (``/proc/<pid>/cmdline`` is world-readable; an environment block is
#: not). They are the exact env equivalents of ``--index-url`` / ``--extra-index-url``.
_UV_TRANSLATED_INDEX_ENV = "UV_INDEX_URL"
_UV_TRANSLATED_EXTRA_INDEX_ENV = "UV_EXTRA_INDEX_URL"

#: pip config keys that select an index, normalized (``_`` → ``-``, ``--`` stripped).
_PIP_INDEX_KEY = "index-url"
_PIP_EXTRA_INDEX_KEY = "extra-index-url"

#: pip resolves config in SECTION order — ``[global]``, then the command's own
#: section, then the environment — over the already-merged cross-file dictionary.
#: ``"env"`` is a synthetic section standing in for ``PIP_*`` (highest).
_PIP_CONFIG_SECTIONS = ("global", "install")
_PIP_ENV_SECTION = "env"


@dataclass(frozen=True)
class AmbientIndexConfig:
    """pip's ambient index settings, as configuration the uv backend can be given.

    ``origin`` names where the winning ``index_url`` came from (an env var or a
    config file path) so a refusal/notice can be specific.
    """

    index_url: str | None = None
    extra_index_urls: tuple[str, ...] = ()
    origin: str | None = None

    def __bool__(self) -> bool:
        return self.index_url is not None or bool(self.extra_index_urls)


def _is_index_url(value: str) -> bool:
    """True when ``value`` is a usable index URL — the ONLY shape allowed to escape.

    An ambient index value is attacker-influenced input (anything that can write a
    ``pip.conf`` on a candidate path, or export ``XDG_CONFIG_HOME``, chooses it), and
    it reaches both a subprocess environment and the consent block — the module's sole
    trust boundary. ``configparser`` joins indented continuation lines with ``\\n``, so
    an unvalidated value could inject whole extra LINES into the consent block: a
    forged ``Proceed? [y/N] y`` plus a decoy argv line naming an innocuous index. A
    strict "printable, and parses as an http(s)/file URL with a host" test drops that
    class outright instead of trying to escape it after the fact.
    """

    v = value.strip()
    if not v or not v.isprintable():
        return False
    parsed = urlparse(v)
    if parsed.scheme in ("http", "https"):
        return bool(parsed.netloc)
    return parsed.scheme == "file" and bool(parsed.path)


def _redact_auth(value: str) -> str:
    """``https://user:pw@host/x`` → ``https://user:****@host/x`` (pip's own rule).

    A port of ``pip._internal.utils.misc.redact_auth_from_url``: a password becomes
    ``****`` and a bare username becomes ``****`` outright. Non-URL argv elements pass
    through untouched. Corporate Nexus/Artifactory mirrors carry basic-auth IN the
    URL, so any index value that reaches the screen must go through here first.
    """

    try:
        split = urlsplit(value)
    except ValueError:
        return value
    if not split.netloc or "@" not in split.netloc:
        return value
    userinfo, _, host = split.netloc.rpartition("@")
    if not userinfo:
        return value
    user, sep, _password = userinfo.partition(":")
    redacted = f"{user}:****@{host}" if sep else f"****@{host}"
    return urlunsplit(split._replace(netloc=redacted))


def _printable(value: str) -> str:
    """Escape control characters so an argv element cannot PAINT extra lines.

    ``shlex.quote`` stops a shell breakout but a raw ``\\n`` still moves the cursor:
    the consent block would show a forged ``Proceed? [y/N] y`` on its own line. ``\\x1b``
    is worse — it can repaint the whole screen. Only non-printables are touched, so
    ordinary non-ASCII text survives intact.
    """

    if value.isprintable():
        return value
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
        for c in value
    )


def display_argv(args: Iterable[str]) -> str:
    """Render an argv for the consent block: credentials redacted, elements quoted.

    :func:`_redact_auth` keeps a basic-auth password out of terminal scrollback, the
    TUI transcript and CI job logs; :func:`_printable` plus ``shlex.quote`` keep every
    element on exactly one line, so no value can forge a ``Proceed? [y/N] y``.
    """

    return shlex.join(_printable(_redact_auth(a)) for a in args)


def _pip_config_candidates(env: Mapping[str, str] | None = None) -> list[str]:
    """pip's config-file search path, LOW → HIGH precedence.

    Mirrors ``pip._internal.configuration.Configuration.iter_config_files``: the
    GLOBAL (system) files, then the USER files (legacy ``~/.pip`` first, then the XDG
    location), then the environment's own ``sys.prefix`` SITE config, then
    ``PIP_CONFIG_FILE``.

    Two ``PIP_CONFIG_FILE`` rules, both pip's:

    * set to ``os.devnull`` → NO config file is read at all (pip's ``_load_config_files``
      returns early);
    * set to a path that EXISTS → the per-user files are skipped entirely
      (``should_load_user_config``). Keeping them in the list let a stale
      ``~/.config/pip/pip.conf`` win the index for a CI job that had deliberately
      pinned one explicit config — an index the operator had taken out of the picture.
      GLOBAL and SITE are unaffected; they load either way.
    """

    env = os.environ if env is None else env
    explicit = env.get("PIP_CONFIG_FILE")
    if explicit == os.devnull:
        return []
    load_user = not (explicit and os.path.exists(explicit))

    basename = "pip.ini" if sys.platform == "win32" else "pip.conf"
    out: list[str] = []
    user: list[str] = []
    if sys.platform == "win32":
        # pip's USER kind is [legacy ~/pip/pip.ini, %APPDATA%/pip/pip.ini], in that order.
        user.append(os.path.join(os.path.expanduser("~"), "pip", basename))
        appdata = env.get("APPDATA")
        if appdata:
            user.append(os.path.join(appdata, "pip", basename))
    else:
        xdg_dirs = env.get("XDG_CONFIG_DIRS") or "/etc/xdg"
        out += [
            os.path.join(d, "pip", basename) for d in xdg_dirs.split(os.pathsep) if d
        ]
        out.append(os.path.join("/etc", basename))
        if sys.platform == "darwin":
            out.append(f"/Library/Application Support/pip/{basename}")
        user.append(os.path.join(os.path.expanduser("~"), ".pip", basename))
        xdg_home = env.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        user.append(os.path.join(xdg_home, "pip", basename))
    if load_user:
        out += user
    out.append(os.path.join(sys.prefix, basename))
    if explicit:
        out.append(explicit)
    return out


def _read_pip_config_items(path: str) -> dict[str, str]:
    """One pip config file's index items as ``{"<section>.<key>": raw_value}``.

    RAW and per-section on purpose: pip merges FILES into a single
    ``{section}.{key}`` dictionary first (a later file REPLACES a key outright) and
    only then resolves section precedence over the merged result. Returning a
    pre-merged ``(index, extras)`` pair per file made both of those impossible to
    express — see :func:`read_pip_index_config`.
    """

    import configparser

    parser = configparser.RawConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return {}
    except (OSError, UnicodeDecodeError, configparser.Error):
        # A malformed or unreadable pip.conf must never break `extension install`.
        return {}

    items: dict[str, str] = {}
    for section in _PIP_CONFIG_SECTIONS:
        if not parser.has_section(section):
            continue
        for raw_key, raw_val in parser.items(section):
            key = raw_key.strip().lower().replace("_", "-").lstrip("-")
            if key in (_PIP_INDEX_KEY, _PIP_EXTRA_INDEX_KEY):
                items[f"{section}.{key}"] = raw_val
    return items


def read_pip_index_config(env: Mapping[str, str] | None = None) -> AmbientIndexConfig:
    """The index configuration **pip** would honor, resolved in pip's precedence.

    Two stages, exactly as pip does it:

    1. Merge the config FILES low → high (:func:`_pip_config_candidates`) into one
       ``{section}.{key}`` dictionary — a later file REPLACES a key
       (``Configuration._dictionary``), it never appends to it.
    2. Resolve in pip's option order ``[global]`` → ``[install]`` → ``PIP_*`` env,
       last writer wins for BOTH keys (``ConfigOptionParser._update_defaults`` does
       ``defaults[dest] = val``), splitting ``extra-index-url`` on whitespace only at
       that final step.

    The single accumulating pass this replaces got both directions wrong: it let
    ``extra-index-url`` PILE UP across files (so a decommissioned org mirror pip had
    discarded re-entered the resolution set — a dependency-confusion vector the pip
    backend does not have), and it let a low-precedence ``[install]`` pin lose to a
    high-precedence ``[global]`` one (pip resolves sections LAST, over the merged
    dict, so ``install`` beats ``global`` no matter which file each came from) —
    silently inverting the org-pin-survives-the-switch guarantee this exists for.

    Values that are not printable http(s)/file URLs are dropped (:func:`_is_index_url`).
    """

    env = os.environ if env is None else env
    # Stage 1 — cross-file merge, later file replaces the key.
    merged: dict[str, tuple[str, str]] = {}
    for path in _pip_config_candidates(env):
        for key, value in _read_pip_config_items(path).items():
            merged[key] = (value, path)
    for key, name in (
        (_PIP_INDEX_KEY, "PIP_INDEX_URL"),
        (_PIP_EXTRA_INDEX_KEY, "PIP_EXTRA_INDEX_URL"),
    ):
        raw = env.get(name) or ""
        if raw.strip():
            merged[f"{_PIP_ENV_SECTION}.{key}"] = (raw, name)

    # Stage 2 — section precedence over the merged dict, last writer wins.
    index: str | None = None
    origin: str | None = None
    extras: tuple[str, ...] = ()
    for section in (*_PIP_CONFIG_SECTIONS, _PIP_ENV_SECTION):
        hit = merged.get(f"{section}.{_PIP_INDEX_KEY}")
        if hit is not None and _is_index_url(hit[0]):
            index, origin = hit[0].strip(), hit[1]
        hit = merged.get(f"{section}.{_PIP_EXTRA_INDEX_KEY}")
        if hit is not None:
            # pip splits a config/env list on whitespace; each token stands alone, so
            # an unusable token is dropped without discarding its healthy neighbours.
            extras = tuple(t for t in hit[0].split() if _is_index_url(t))
    return AmbientIndexConfig(
        index_url=index, extra_index_urls=extras, origin=origin
    )


def _read_uv_config_table(path: str) -> dict[str, object]:
    """uv's settings table from ``uv.toml`` / a ``pyproject.toml`` ``[tool.uv]``."""

    import tomllib

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        # Same swallow-everything contract as _read_pip_config_items: a broken config
        # file must never break `extension install`.
        return {}
    if os.path.basename(path) == "pyproject.toml":
        tool = data.get("tool")
        uv = tool.get("uv") if isinstance(tool, dict) else None
        return uv if isinstance(uv, dict) else {}
    return data


def _uv_config_files(env: Mapping[str, str] | None = None, cwd: str | None = None) -> list[str]:
    """uv's own config files, in the order uv consults them.

    ``UV_CONFIG_FILE`` if set; otherwise the nearest project ``uv.toml`` /
    ``pyproject.toml`` walking up from ``cwd``, then the user-level
    ``$XDG_CONFIG_HOME/uv/uv.toml``.
    """

    env = os.environ if env is None else env
    explicit = env.get("UV_CONFIG_FILE")
    if explicit:
        return [explicit]
    out: list[str] = []
    try:
        here = Path(cwd) if cwd is not None else Path.cwd()
        here = here.resolve()
    except (OSError, RuntimeError, ValueError):
        here = None  # type: ignore[assignment]
    if here is not None:
        for parent in (here, *here.parents):
            for name in ("uv.toml", "pyproject.toml"):
                candidate = parent / name
                if candidate.is_file():
                    out.append(str(candidate))
    xdg_home = env.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    out.append(os.path.join(xdg_home, "uv", "uv.toml"))
    return out


def _uv_has_own_index_config(env: Mapping[str, str] | None = None) -> bool:
    """True when uv already has index configuration of its own (env OR a config file).

    An env-var-only test missed uv's PRIMARY configuration mechanism: a ``uv.toml`` /
    ``[tool.uv]`` / ``UV_CONFIG_FILE`` index pin sets no ``UV_INDEX*`` variable, so a
    stale ``pip.conf`` was translated on top of it — and since uv's precedence is
    CLI > env > file, the translation WON. That is the exact silent index switch this
    machinery exists to prevent, just in the other direction.
    """

    env = os.environ if env is None else env
    if any(env.get(name) for name in _UV_INDEX_ENV):
        return True
    return any(
        any(key in _read_uv_config_table(path) for key in _UV_CONFIG_INDEX_KEYS)
        for path in _uv_config_files(env)
    )


def uv_ambient_index_config(env: Mapping[str, str] | None = None) -> AmbientIndexConfig:
    """The pip index configuration the **uv** backend must be told about explicitly.

    pip reads ``PIP_INDEX_URL`` / ``PIP_EXTRA_INDEX_URL`` and ``pip.conf``; uv reads
    NONE of them (verified: ``uv pip install`` with ``PIP_INDEX_URL`` and
    ``PIP_CONFIG_FILE`` both pointed at a dead index still resolved from PyPI, while
    the same command under ``UV_INDEX_URL`` refused to connect). Without this
    translation an org that pins pip to an internal mirror would SILENTLY resolve
    from public PyPI the moment aelix fell back to uv — which is the DEFAULT on the
    official ``uv tool install`` path — turning every private extension name into a
    dependency-confusion target whose build code runs at install time.

    Returns an empty config when uv is already configured itself
    (:func:`_uv_has_own_index_config`): explicit uv configuration is theirs to own.
    """

    env = os.environ if env is None else env
    if _uv_has_own_index_config(env):
        return AmbientIndexConfig()
    return read_pip_index_config(env)


def uv_ambient_index_env(
    backend: InstallBackend | None, kind: TargetKind, *, index_url: str | None = None
) -> dict[str, str]:
    """The env the uv child needs so a translated pip index NEVER touches argv.

    ``--index-url <value>`` on argv publishes the value to every other user on the
    host: ``/proc/<pid>/cmdline`` is mode 0444 and ``ps -eo args`` prints it. A
    corporate ``pip.conf`` is a 0600 file whose basic-auth password pip never exposes,
    so translating it onto the uv command line CREATED a secret-disclosure path that
    did not exist before #113. uv reads ``UV_INDEX_URL`` / ``UV_EXTRA_INDEX_URL`` as
    the exact equivalents of those flags, and an environment block is not world-readable
    the way ``cmdline`` is — so the translation goes through the environment instead.

    Only the AMBIENT (pip.conf / ``PIP_*``) values move. An aelix ``index_url`` from a
    registered index source is a command-line value the user typed and keeps its flag
    (unchanged from pre-#113), and uv's CLI > env precedence keeps it winning.
    """

    if backend is None or backend.name != "uv" or kind != "pypi":
        return {}
    ambient = uv_ambient_index_config()
    out: dict[str, str] = {}
    # An explicit aelix --index-url outranks the ambient default index entirely.
    if ambient.index_url and not index_url:
        out[_UV_TRANSLATED_INDEX_ENV] = ambient.index_url
    extras = [e for e in ambient.extra_index_urls if e != (index_url or ambient.index_url)]
    if extras:
        out[_UV_TRANSLATED_EXTRA_INDEX_ENV] = " ".join(extras)
    return out


@contextmanager
def _patched_environ(overrides: Mapping[str, str]) -> Iterator[None]:
    """Temporarily apply ``overrides`` to ``os.environ`` (the child inherits it)."""

    if not overrides:
        yield
        return
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def detect_install_backend() -> InstallBackend | None:
    """Probe the environment for a usable backend; ``None`` when there is none.

    pip WINS when importable — it is the only backend that can verify. A missing
    pip makes ``python -m pip`` exit nonzero WITHOUT raising, so probing the
    module up front avoids mislabeling "no pip" as "install failed" (review LOW).

    A **non-absolute** ``shutil.which`` hit is treated as NO uv at all. ``which``
    resolves against ``PATH`` verbatim, so a ``.`` / empty / relative ``PATH``
    entry (routine with ``node_modules/.bin``, direnv, a stray leading ``:``)
    yields a cwd-relative program — and the installer would then execute a file
    out of whatever repository the user happens to be standing in, before the
    extension is ever fetched (CWE-426). Falling through to
    :func:`_backend_missing_message` is the safe outcome: it tells the user to
    install uv or re-install aelix ``--with pip``, both of which land an absolute
    executable.
    """

    if importlib.util.find_spec("pip") is not None:
        return PIP_BACKEND
    uv = shutil.which("uv")
    if uv is not None and os.path.isabs(uv):
        return InstallBackend(name="uv", uv_path=uv)
    return None


def resolve_install_backend(runner: PipRunner | None) -> InstallBackend | None:
    """The backend to use, honoring the injected-runner seam.

    An injected ``runner`` means **the caller owns backend availability** (tests,
    or an alternate package manager driving the argv itself), so it resolves to
    :data:`PIP_BACKEND` unconditionally — never probing, never failing. That
    preserves both halves of the historical ``_pip_available`` short-circuit: an
    injected runner is always "available", AND the argv keeps its ``python -m pip``
    shape, which the whole injected-runner test cluster asserts on. Environment
    detection therefore only ever runs for the DEFAULT runner (a real subprocess).
    """

    if runner is not None:
        return PIP_BACKEND
    return detect_install_backend()


def _pip_available(runner: PipRunner | None) -> bool:
    """True when SOME install backend is usable (pip, or uv as the fallback).

    Kept as the named seam it has always been; the pip-specific probe now lives
    in :func:`detect_install_backend`. Injecting a runner still short-circuits to
    "available" (see :func:`resolve_install_backend`).
    """

    return resolve_install_backend(runner) is not None


def _backend_missing_message(action: str, what: str) -> str:
    """The no-backend abort — names WHAT was attempted, then the real remedies.

    The old text advised ``python -m ensurepip --upgrade``, which is the wrong
    move for a ``uv tool`` venv (the case that actually broke) and was documented
    nowhere. It also fired BEFORE the target was ever printed, so the user could
    not tell what had been refused.
    """

    return (
        f"Error: cannot {action} {what} — no usable package installer.\n"
        f"  This interpreter ({sys.executable}) has no pip, and no `uv` "
        "executable is on PATH.\n"
        "  aelix installed with `uv tool install` (install.sh) runs in a tool "
        "venv that ships WITHOUT pip.\n"
        "  Fix — pick one:\n"
        "    • uv tool install --force --with pip aelix   "
        "(re-installs aelix with pip inside; restores signature verification)\n"
        "    • install uv (https://docs.astral.sh/uv/) — aelix then installs via "
        "`uv pip install`\n"
        "    • run aelix from a virtualenv that has pip "
        "(`python -m ensurepip --upgrade`)"
    )


def uv_pypi_verify_unsupported(
    backend: InstallBackend,
    kind: TargetKind,
    *,
    no_verify: bool,
    strict: bool,
    verify_pypi: bool,
    require_signature: bool,
) -> bool:
    """True when the requested pypi verification cannot run on ``backend``.

    The ONLY thing the uv backend cannot do is the two-phase pypi
    download-and-verify, so the predicate is exactly the one that routes into
    :func:`build_download_args`: ``kind == "pypi"`` AND one of ``--verify-pypi`` /
    ``--strict`` / ``--require-signature`` — not ``--require-signature`` alone.

    Deliberately NOT refused, because they never invoke the runner for a download
    and work fully on uv:

    * ``--require-signature`` / ``--strict`` on a **path** target — staged, hashed
      and ``.aelixsig``-checked entirely locally, then installed from the staged copy;
    * ``--strict`` on a **git** target — pin enforcement is pure argv inspection;
    * anything under ``--no-verify`` — the gate never runs, so nothing is dropped.
    """

    if backend.supports_download or no_verify or kind != "pypi":
        return False
    return bool(verify_pypi or strict or require_signature)


def _uv_verify_refusal_message(target: str, backend: InstallBackend) -> str:
    """Fail-closed text for a pypi verify request the uv backend cannot honor."""

    return (
        f"Error: refusing to install {target!r} — pypi integrity/signature "
        "verification was requested, but the active installer backend is "
        f"`{backend.label}`, which cannot download a distribution for inspection "
        "(`uv pip` has no `download` subcommand).\n"
        "  Installing anyway would silently drop a check you explicitly asked "
        "for, so this fails closed.\n"
        "  Fix: give aelix a real pip — `uv tool install --force --with pip "
        "aelix` — then retry.\n"
        "  (path and git targets verify fine on the uv backend; this limit is "
        "pypi-only. `--no-verify` also proceeds, unverified.)"
    )


def _uv_volatility_notice() -> str:
    """Warn that a ``uv tool`` re-install wipes extensions installed this way."""

    return (
        "  note: uv backend — extensions install into this interpreter's "
        "environment. If aelix lives in a `uv tool` venv, re-running "
        "`uv tool install --force aelix` (or install.sh) RECREATES that venv "
        "from its receipt and REMOVES every extension installed here."
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
    require_signature: bool = False,
    trusted_key: str | None = None,
    signature_path: str | None = None,
    agent_dir: str | None = None,
    announce: bool = True,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Install one extension via the resolved backend; return an exit code.

    ``0`` on success; :data:`_INSTALLER_FAILED` (1) when the installer ran and
    failed — its own returncode is printed, not returned, because pip's codes
    collide with this verb's (see that constant); ``2`` when it did NOT run
    (usage/guard error, user abort, a #64 verify refusal, no usable backend, or a
    #113 fail-closed refusal). ``require_signature`` / ``trusted_key``
    / ``signature_path`` drive the #67 Ed25519 provenance branch. ``runner`` and
    ``input_fn`` are injectable for tests (an injected runner also pins the backend
    to the pip dialect — see :func:`resolve_install_backend`).

    ``announce`` prints :data:`_INSTALLED_RESTART` on success. Both CLI verbs —
    ``extension install`` and ``extension update`` — pass :data:`False` and print
    their own line instead, because that unconditional "Installed. Restart aelix"
    is the whole of issue #154: the host already knows, import-free, whether the
    pack it just wrote to disk can bind, and said "restart" to a pack that will
    never load. It stays defaulted :data:`True` for the PUBLIC seam, whose callers
    have no verdict of their own to print; the CLI reaches it through
    :func:`_cmd_install` and :func:`_upgrade_and_report`, which do.
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

    # #67 (ADR-0189): --no-verify skips the WHOLE gate, so it cannot honor a REQUIRED
    # signature — that is a hard usage error (refuse before prompting), NOT the
    # warn-and-continue treatment --no-verify + --strict gets (that combo still runs
    # consent; here a required signature would be silently dropped).
    if no_verify and require_signature:
        print(
            "Error: --no-verify cannot be combined with --require-signature "
            "(a required signature would be skipped).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    verb = "Upgrade" if upgrade else "Install"

    # #113: pick the installer backend. The old guard aborted on "no pip" BEFORE
    # anything named the target, so the user could not tell what had been refused —
    # both refusals below now spell out the target and its source kind.
    backend = resolve_install_backend(runner)
    if backend is None:
        print(
            _backend_missing_message(verb.lower(), f"{target!r} (source: {kind})"),
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    # Fail CLOSED: a pypi verify/signature request the uv backend cannot execute is
    # refused, never downgraded to an unverified install. Scoped to the exact
    # predicate that routes into build_download_args — path/git verification still
    # works on uv (it is entirely local).
    if uv_pypi_verify_unsupported(
        backend,
        kind,
        no_verify=no_verify,
        strict=strict,
        verify_pypi=verify_pypi,
        require_signature=require_signature,
    ):
        print(_uv_verify_refusal_message(target, backend), file=sys.stderr)
        return _EXIT_DIDNT_RUN

    pip_args = build_pip_args(
        target,
        kind,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        upgrade=upgrade,
        backend=backend,
    )

    # #113: pip's ambient index config translated for uv. It travels in the ENV, never
    # on argv (a credentialed mirror URL would otherwise be published to every user on
    # the host via /proc/<pid>/cmdline) — and it is still SHOWN, redacted, so the
    # resolved index is visible before the y/N.
    ambient_env = uv_ambient_index_env(backend, kind, index_url=index_url)

    # Consent — pip runs the package's build/setup code (arbitrary at install
    # time), so the manifest capability gate cannot protect this path; the
    # source-level y/N IS the trust boundary. Deny-by-default (headless without
    # --yes, or a closed stdin, aborts).
    print(f"{verb} extension from {kind}: {target}")
    print(f"  → {display_argv(pip_args)}")
    for name in (_UV_TRANSLATED_INDEX_ENV, _UV_TRANSLATED_EXTRA_INDEX_ENV):
        if name in ambient_env:
            shown = " ".join(_redact_auth(v) for v in ambient_env[name].split())
            print(f"  → {name}={shown}  (from pip's ambient configuration)")
    print(
        "  pip will run the package's build/setup code. Only install sources you trust."
    )
    if backend.name == "uv":
        print(_uv_volatility_notice())
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
                require_signature=require_signature,
                trusted_key=trusted_key,
                signature_path=signature_path,
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
            # Fail CLOSED whenever verification was REQUIRED — strict (#64) OR a required
            # signature (#67). An internal error (mkdtemp/copy/hash/download failure, or a
            # maliciously deep .aelixsig) must never silently drop a demanded check and
            # install unverified. Only the unenforced default path degrades to a pin-less
            # install (the shipped #64 tofi behavior).
            if strict or require_signature:
                gate = "required-signature" if require_signature else "strict"
                print(
                    f"Verification error ({gate}) — pip not run: {exc}",
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
        with _patched_environ(ambient_env):
            result = run(pip_args)
        code = int(getattr(result, "returncode", 1))
        if code != 0:
            # The installer's own code is SHOWN, not returned — see
            # :data:`_INSTALLER_FAILED` for why passing it through made this
            # verb's documented exit split conditional on pip's choice of codes.
            print(f"{backend.label} install failed (exit {code}).", file=sys.stderr)
            return _INSTALLER_FAILED
        if pending_pin is not None:
            try:
                _record_pin(pending_pin, agent_dir)
            except Exception as exc:  # noqa: BLE001 — pinning is best-effort
                print(
                    f"Warning: could not record integrity pin: {exc}",
                    file=sys.stderr,
                )
        if announce:
            print(_INSTALLED_RESTART)
        return 0
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

    **pip-only** (#113): ``uv pip`` has no ``download`` subcommand, so there is no
    uv dialect of this argv. Reaching it on the ``uv`` backend is impossible —
    :func:`uv_pypi_verify_unsupported` refuses the install first
    (:attr:`InstallBackend.supports_download` is the capability flag).
    """

    args = [sys.executable, "-m", "pip", "download", spec, "--dest", dest]
    if index_url:
        args += ["--index-url", index_url]
    for extra in extra_index_urls or ():
        args += ["--extra-index-url", extra]
    return args


def _rewrite_pypi_local(dest: str, spec: str, *, upgrade: bool) -> list[str]:
    """Install argv that installs the VERIFIED bytes from the local download dir.

    Pip-shaped by construction: it is only ever reached after
    :func:`build_download_args` succeeded, which is the ``pip`` backend alone
    (#113). ``--no-index`` / ``--find-links`` do exist in ``uv pip install``, so a
    uv dialect becomes a two-line change the day a uv download route exists.
    """

    base = PIP_BACKEND.install_prefix()
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
    require_signature: bool = False,
    trusted_key: str | None = None,
    signature_path: str | None = None,
    index_url: str | None,
    extra_index_urls: Iterable[str] | None,
    runner: PipRunner,
    agent_dir: str | None,
) -> _VerifyResult:
    """The pre-pip integrity gate (ADR-0187) + Ed25519 provenance (#67/ADR-0189).

    Runs AFTER consent, BEFORE pip. Returns the argv to execute (rewritten to a local
    ``--no-index`` install for a verified pypi target) plus a
    :class:`~extension_pins.Pin` to record IFF the install succeeds. Raises
    :class:`~extension_pins.VerifyRefusal` to block the install (the caller maps that to
    exit-code 2, "pip never ran").

    Default ``tofi`` verifies path artifacts + pinned git SHAs (recording on first
    acquisition); ``strict`` additionally refuses unpinned sources, mutable git
    refs, and directory/editable paths. pypi two-phase download-verify is opt-in
    (``verify_pypi`` / ``strict`` / ``require_signature``) in v1 — see ADR-0187 (needs
    real-index integration testing, #61, before it becomes default-on).

    #67: when a ``.aelixsig`` from a TRUSTED key is present and valid, the recorded pin
    is stamped with the signer keyId/sig/statement and the source is treated as
    vouched-for (no blind first-acquisition/re-TOFI). A present-but-invalid signature
    from a trusted key refuses ALWAYS; ``require_signature`` refuses anything lacking a
    valid trusted signature. Git provenance is not wired in v1 (path + pypi only) —
    ``require_signature`` on a git target refuses.
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
                # #67: the .aelixsig sits next to the ORIGINAL artifact (its sha256
                # equals the staged copy's, so the binding is exact). --signature
                # overrides the sibling location for an out-of-band sidecar.
                sig = extension_signing.gate_signature(
                    kind="path", identity=identity,
                    sidecar_path=(
                        Path(signature_path) if signature_path
                        else extension_signing.aelixsig_path_for(resolved)
                    ),
                    observed_sha256=observed, canonical_name=None, version=None,
                    git_sha=None, require_signature=require_signature,
                    trusted_key=trusted_key, agent_dir=resolved_dir,
                )
                if sig.notice:
                    _print_verify(sig.notice)
                decision = extension_pins.decide_generic(
                    existing, observed, mode=mode, repin=repin,
                    label=f"path {resolved.name}", field_name="sha256",
                    authenticated=sig.authenticated,
                )
                _print_verify(decision.notice)
                new_args = [*pip_args[:-1], str(staged)]
                pin = (
                    extension_pins.Pin(
                        identity=identity, kind="path", mode=mode,
                        sha256=observed, pinned_at=extension_pins.now_iso(),
                        key_id=sig.key_id, sig=sig.sig,
                        sha256_statement=sig.statement_json,
                    )
                    if decision.record
                    else None
                )
                return _VerifyResult(new_args, pin, cleanup_dir=dest)
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
        # A directory / editable source has no single stable artifact — and no artifact
        # to sign, so a required signature cannot be honored either.
        if require_signature:
            raise extension_pins.VerifyRefusal(
                "--require-signature: a directory/editable path has no artifact to "
                "sign/verify — install a signed built .whl/.tar.gz"
            )
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
        # #67: git provenance is not wired in v1 (path + pypi only) — a git commit SHA
        # already gives tree immutability. Honor fail-closed: refuse a REQUIRED signature
        # rather than silently ignoring it.
        if require_signature:
            raise extension_pins.VerifyRefusal(
                "--require-signature: git provenance is not supported in this release "
                "(sign the built artifact and install it via path/pypi; a git+URL@<sha> "
                "already pins the commit tree)"
            )
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

    # pypi — two-phase download-verify (opt-in in v1; see ADR-0187). --require-signature
    # also forces the download so the signed bytes can be verified before install.
    if not (verify_pypi or strict or require_signature):
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
            if strict or require_signature:
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
        # #67: a pypi .aelixsig cannot ride `pip download`, so it is supplied out-of-band
        # via --signature, bound to the DOWNLOADED artifact's sha256 (air-gap: the
        # verifier never fetches the sidecar itself).
        sig = extension_signing.gate_signature(
            kind="pypi", identity=identity,
            sidecar_path=Path(signature_path) if signature_path else None,
            observed_sha256=observed, canonical_name=canonical, version=version,
            git_sha=None, require_signature=require_signature, trusted_key=trusted_key,
            agent_dir=resolved_dir,
        )
        if sig.notice:
            _print_verify(sig.notice)
        decision = extension_pins.decide_pypi(
            existing, observed, version, mode=mode, repin=repin, label=f"pypi {bare}",
            authenticated=sig.authenticated,
        )
        _print_verify(decision.notice)
        new_args = _rewrite_pypi_local(dest, target, upgrade="--upgrade" in pip_args)
        pin = (
            extension_pins.Pin(
                identity=identity, kind="pypi", mode=mode,
                name=bare, version=version,
                sha256=observed, pinned_at=extension_pins.now_iso(),
                key_id=sig.key_id, sig=sig.sig, sha256_statement=sig.statement_json,
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
    require_signature: bool = False
    trusted_key: str | None = None
    signature_path: str | None = None


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


def _catalog_locations(sources: list[ExtensionSourceObject]) -> list[str]:
    """The registered discover-catalog locations, in registration order (#65)."""

    return [s.spec for s in sources if s.kind == "catalog" and s.spec]


def _normalize_catalog_spec(target: str) -> str:
    """Normalize a ``source add --catalog`` location, or ``""`` if it is not one.

    A git target → :func:`_normalize_git_spec`; an ``https`` / ``file://`` URL →
    verbatim; an existing or path-shaped token → an absolute path. Returns ``""``
    (the caller reports "not a valid catalog location") for a bare bareword — a
    package-name-looking token is an install TARGET, not a catalog — and for any
    plaintext-``http`` transport (TLS is required; refused here AND at fetch time,
    no bypass). The stored spec is what ``discover --refresh`` feeds to
    :func:`extension_catalog.fetch_all`.
    """

    raw = target.strip()
    if not raw:
        return ""
    low = raw.lower()
    kind = classify_target(raw)
    if kind == "git":
        spec = _normalize_git_spec(raw)
        # Refuse a plaintext-http git transport (git+http:// or http://…​.git).
        return "" if spec.lower().startswith(("git+http://", "http://")) else spec
    if kind == "path":
        return str(Path(raw).expanduser().resolve())
    # classify_target's "pypi" catch-all: a real URL, a path-shaped token, or a
    # bare name. Only https/file URLs and path-shaped tokens are catalog locations.
    if low.startswith("http://"):
        return ""  # plaintext refused (TLS required)
    if low.startswith(("https://", "file://")):
        return raw
    if "/" in raw or raw.startswith(("~", ".")) or raw.endswith(".json"):
        return str(Path(raw).expanduser().resolve())
    return ""  # a bare bareword is not a catalog location


def _catalog_identity(spec: str) -> str:
    """A comparable identity for a catalog location (Track D — merge/tombstone).

    Runs the SAME normalization ``source add --catalog`` applies before storing
    (:func:`_normalize_catalog_spec`), so the built-in default and a user-stored
    duplicate of the same location compare EQUAL (dedupe) and a tombstone written
    for one form matches the other. Falls back to the stripped raw string for a
    token :func:`_normalize_catalog_spec` rejects (so an identity is always total).
    """

    return _normalize_catalog_spec(spec) or spec.strip()


def _effective_default_identity() -> str | None:
    """The normalized identity of the built-in default catalog, or ``None``.

    Resolves :func:`extension_catalog.resolve_default_catalog_url` (env override →
    the built-in :data:`~extension_catalog.DEFAULT_CATALOG_URL`) and normalizes it.
    The built-in default is LIVE, so this normally returns the official catalog's
    https identity; ``None`` only when the default was explicitly disabled
    (``AELIX_DEFAULT_CATALOG=""``) OR the configured value is not a valid catalog
    location. Offline is NOT considered here — this is the persistent identity a
    tombstone / ``source remove <default>`` keys off (ADR-0192).
    """

    raw = extension_catalog.resolve_default_catalog_url()
    if raw is None:
        return None
    return _normalize_catalog_spec(raw) or None


def _legacy_suppression_keys(identity: str) -> tuple[str, ...]:
    """Tombstone keys that suppress ``identity`` — current form + legacy alias.

    Before the W1-A ``classify_target`` repair (#111 A-1) an https catalog URL whose
    HOST contained ``.git`` (every ``*.github.io`` location, including the built-in
    default) was misclassified as a git target, so its stored identity was
    ``git+<url>``. A user who opted the default out on such a build holds a tombstone
    under that corrupted key. Matching it here keeps the opt-out honoured instead of
    silently re-enabling an outbound fetch the user deliberately disabled; without it
    the repair would reverse an explicit user consent decision (ADR-0192 amendment).

    The alias is emitted ONLY for an identity the old bug could actually have
    corrupted. That predicate is exactly the pre-repair ``classify_target`` http
    branch — ``low.startswith(("http://", "https://")) and ".git" in low`` — because a
    catalog identity that branch called ``pypi`` was stored VERBATIM, so no
    ``git+<identity>`` row or tombstone can exist for it. Aliasing every https
    identity instead silently swallowed a DELIBERATE registration: with
    ``AELIX_DEFAULT_CATALOG=https://gitea.corp/team/catalog`` (Gitea makes the ``.git``
    suffix optional, so the same URL is also a valid clone remote) a user's
    ``source add --catalog git+https://gitea.corp/team/catalog`` — a git CLONE, a
    different resource from the https GET — was rewritten to the GET and its entries
    vanished from ``discover``, and a ``git+`` tombstone the bug could never have
    written suppressed the default outright.
    """

    if identity.startswith("git+"):
        return (identity,)
    low = identity.lower()
    if not (low.startswith(("http://", "https://")) and ".git" in low):
        return (identity,)
    return (identity, f"git+{identity}")


def _default_is_suppressed(settings: SettingsManager, identity: str) -> bool:
    """True when a persisted opt-out tombstone covers the default ``identity``."""

    suppressed = set(settings.get_suppressed_default_catalogs())
    return any(key in suppressed for key in _legacy_suppression_keys(identity))


def _effective_catalog_locations(
    settings: SettingsManager, *, offline: bool
) -> list[str]:
    """Compose the built-in default catalog with the stored locations (Track D).

    Merge order (ADR-0192): the built-in default (normalized) goes FIRST, then the
    stored catalog sources in registration order — UNLESS the default is dropped:

    * disabled (``AELIX_DEFAULT_CATALOG=""``) or invalid default → stored only;
    * ``offline`` and the default is a network transport (not in the air-gap
      allowlist — see :func:`_is_offline_fetchable`) → dropped (guard ④: an
      internet-reachable default is meaningless off-network, dropped SILENTLY here —
      no notice row);
    * the default identity is in ``suppressed_default_catalogs`` (a persisted
      opt-out tombstone — including the pre-W1-A ``git+`` form, see
      :func:`_legacy_suppression_keys`) → dropped;
    * a stored source already carries the default's identity → the STORED entry
      wins (deduped to one row, so it appears exactly once) — including a row
      stored under the pre-W1-A ``git+`` identity (see
      :func:`_legacy_suppression_keys`), which is REPAIRED to the https form in
      place rather than merely deduped.

    The legacy branch is the stored-source half of the same migration the tombstone
    check does. ``source add --catalog <default>`` is the documented undo of
    ``source remove <default>``, and on a pre-W1-A build it stored the default as
    ``git+https://…/catalog.json``. Comparing only the repaired identity would stop
    matching it, so the user would get the default TWICE: once correctly, and once as
    a ``git+`` row that ``discover --refresh`` tries to ``git clone`` — a permanent
    warning row, or exit 2 ("every registered catalog failed") when it is their only
    source and the default is suppressed. Substituting the repaired identity keeps
    exactly one working row. The stored settings are left alone on purpose: this
    function is pure, and rewriting a user's ``extensionSources`` is a job for an
    explicit ``source`` command, not a read path.

    Pure + fetch-free — it only augments the location LIST (guard ①).
    """

    stored = _catalog_locations(settings.get_extension_sources())
    default = _effective_default_identity()
    if default is None:
        return stored
    if offline and not _is_offline_fetchable(default):
        return stored
    if _default_is_suppressed(settings, default):
        return stored
    stored_ids = [_catalog_identity(s) for s in stored]
    legacy = set(_legacy_suppression_keys(default)) - {default}
    if default in stored_ids or any(sid in legacy for sid in stored_ids):
        # ONE pass over both forms. An early ``default in stored_ids`` return would
        # skip the legacy repair, so a user holding BOTH rows — reachable through the
        # documented undo, since ``_upsert_source`` compares catalog identities
        # verbatim and APPENDS a second row when ``source add --catalog <default>`` is
        # re-run on a repaired build — kept the broken ``git+`` row in the fetch list:
        # a permanent ``⚠ git+https://…/catalog.json`` on every ``discover --refresh``
        # (git-cloning a JSON document), and exit 2 when it is their only source.
        out: list[str] = []
        emitted = False
        for spec, sid in zip(stored, stored_ids, strict=True):
            if sid != default and sid not in legacy:
                out.append(spec)
                continue
            if emitted:
                continue  # collapse every duplicate/legacy form to one row
            emitted = True
            out.append(default if sid in legacy else spec)
        return out
    return [default, *stored]


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


async def _add_catalog_source(target: str, *, settings: SettingsManager) -> int:
    """Register ``target`` as a ``kind="catalog"`` discover-catalog source (#65).

    Register-only (add ≠ browse): the location is normalized + persisted; nothing
    is fetched here. ``discover --refresh`` fetches it later. The spec is stored
    normalized (git → ``git+…``, path → absolute, url → verbatim) so it dedupes +
    displays consistently with what ``fetch_all`` will read.
    """

    low = target.strip().lower()
    if low.startswith("http://") or low.startswith("git+http://"):
        print(
            "Error: refusing a plain-HTTP catalog location (TLS required). "
            "Use https://, a file:// path, or a git+ssh source.",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN
    spec = _normalize_catalog_spec(target)
    if not spec:
        print(
            f"Error: {target!r} is not a valid catalog location "
            "(expected a path, a git URL, or an https/file URL).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN
    # Re-activation (ADR-0192): adding a location whose identity was opted out via
    # ``source remove <default>`` CLEARS that tombstone, so ``source add --catalog
    # <default>`` is the undo of the opt-out. The dedupe in
    # :func:`_effective_catalog_locations` then shows it exactly once. A pre-W1-A
    # ``git+`` tombstone for the same location is cleared too (see
    # :func:`_legacy_suppression_keys`) — otherwise the undo would leave the built-in
    # row still reading "suppressed".
    identity = _catalog_identity(spec)
    keys = set(_legacy_suppression_keys(identity))
    suppressed = settings.get_suppressed_default_catalogs()
    reactivated = any(key in suppressed for key in keys)
    if reactivated:
        settings.set_suppressed_default_catalogs([s for s in suppressed if s not in keys])
        await settings.flush()
    sources = settings.get_extension_sources()
    new_sources, changed = _upsert_source(sources, spec, "catalog")
    if not changed:
        if reactivated:
            print(f"Re-activated the opted-out catalog: [catalog] {spec}")
            return 0
        print(f"Source already registered: [catalog] {spec}")
        return 0
    await _persist(settings, new_sources)
    print(f"Registered source: [catalog] {spec}")
    if reactivated:
        print("  (cleared a prior opt-out of this catalog)")
    print("  Browse it with: aelix extension discover")
    return 0


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
        # Guard ③ (ADR-0192): show the built-in default catalog as its own row
        # (present / suppressed) so an opt-out is visible. The built-in default is
        # LIVE, so this row normally renders; it is absent only when the default was
        # disabled (``AELIX_DEFAULT_CATALOG=""``) or repointed at an invalid value.
        default_id = _effective_default_identity()
        default_row: str | None = None
        if default_id is not None:
            state = (
                "suppressed"
                if _default_is_suppressed(settings, default_id)
                else "present"
            )
            default_row = f"  [catalog] {default_id}  (built-in default — {state})"
        if not sources and default_row is None:
            print("No extension sources registered.")
            return 0
        print("Extension sources:")
        if default_row is not None:
            print(default_row)
        for s in sources:
            suffix = f" (installed as {s.name})" if s.name else ""
            print(f"  [{s.kind}] {s.spec}{suffix}")
        return 0

    if action == "add":
        # Only a positional target (+ ignore a stray --yes for symmetry). The
        # --catalog flag selects kind="catalog" explicitly (classify_source cannot
        # infer catalog vs index — both are plain http URLs).
        as_catalog = "--catalog" in args
        positional = [a for a in args if not a.startswith("-")]
        if len(positional) != 1:
            print(
                f"Error: source add requires exactly one <path|git-url|index-url>.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        target = positional[0]
        if as_catalog:
            return await _add_catalog_source(target, settings=settings)
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
        target = positional[0]
        sources = settings.get_extension_sources()
        new_sources, removed = _remove_source(sources, target)
        # Tombstone (ADR-0192): removing the built-in default catalog is a PERSISTED
        # opt-out — its identity is appended to ``suppressed_default_catalogs`` so it
        # stays absent across runs (identity-scoped, so an env repoint escapes it).
        # ``source add --catalog <default>`` clears it again.
        tombstoned = False
        default_id = _effective_default_identity()
        if default_id is not None and _catalog_identity(target) == default_id:
            suppressed = settings.get_suppressed_default_catalogs()
            if not _default_is_suppressed(settings, default_id):
                settings.set_suppressed_default_catalogs([*suppressed, default_id])
                await settings.flush()
                tombstoned = True
        if removed == 0 and not tombstoned:
            print(f"No registered source matched {target!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        if removed:
            await _persist(settings, new_sources)
        parts: list[str] = []
        if removed:
            parts.append(f"Removed {removed} source(s).")
        if tombstoned:
            parts.append("(built-in default opted out)")
        print(" ".join(parts))
        return 0

    print(
        f"Error: unknown source subcommand {action!r} (add | list | remove).\n{_USAGE}",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


# =====================================================================
# === Manifest-binding status (issue #91 — `verify` + `list` note) =====
# =====================================================================


@dataclass(frozen=True)
class EndpointStatus:
    """One ``aelix.extensions`` endpoint plus the IMPORT-FREE verdict on whether
    its ``aelix-plugin.toml`` will bind.

    Produced by :func:`classify_installed_endpoints`, which runs the SAME
    provenance gate and metadata-only resolver the loader runs at discovery —
    so a ``bound`` status here is exactly the loader binding the manifest, and a
    non-``bound`` one is exactly the loader dropping that pack's declarative
    contributions. Nothing here imports the pack.

    ``outcome`` is the :class:`~aelix_coding_agent.extensions.ep_manifest.EpOutcome`
    resolution reached, or :data:`None` for an
    :class:`~aelix_coding_agent.extensions.ep_manifest.EpApiLevelRefusal` — the
    pack declares a host API level this build cannot satisfy, so it is
    incompatible rather than merely unproven.
    """

    ep_name: str
    dist_name: str | None
    version: str | None
    outcome: EpOutcome | None
    reason: str
    plugin_id: str | None

    @property
    def bound(self) -> bool:
        """True only when the manifest resolved to ``BOUND``."""
        return self.outcome is EpOutcome.BOUND

    def label(self) -> str:
        """``<ep-name> [<dist> <version>]`` for the human report."""
        if not self.dist_name:
            return self.ep_name
        ver = f" {self.version}" if self.version else ""
        return f"{self.ep_name} [{self.dist_name}{ver}]"


#: The ABSENT hint — the ONE outcome whose cause is a packaging footgun rather
#: than an authoring mistake the reason already names. A setuptools build with
#: default configuration drops ``aelix-plugin.toml`` (and ``themes/*.toml``)
#: from the wheel, so the dist installs, ``setup()`` runs, the pack looks
#: healthy — and every declarative contribution is silently inert because the
#: manifest is simply not there to read.
_ABSENT_HINT = (
    "the distribution installed but ships no aelix-plugin.toml. The usual cause "
    "is a setuptools build with DEFAULT configuration, which DROPS "
    "aelix-plugin.toml (and themes/*.toml) from the wheel at build time — "
    "setup() still runs, so the pack looks installed, but every declarative "
    "contribution is silently inert. Fix: ship the manifest inside the wheel "
    "via package-data (setuptools needs include_package_data + MANIFEST.in, or "
    "an explicit [tool.setuptools.package-data]), or build with hatchling, which "
    "ships package data files by default. A buildable hatchling scaffold that "
    "gets this right ships INSIDE aelix, at aelix_coding_agent/examples/starter/ "
    "(in a source checkout: "
    "packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/); the "
    "packaging rules are in `aelix docs extension`, 'Packaging your "
    "extension'."
)


def classify_installed_endpoints(
    *, trusted_ep_dists: frozenset[str] = frozenset()
) -> list[EndpointStatus]:
    """Resolve every installed ``aelix.extensions`` endpoint's manifest, IMPORT-FREE.

    Runs the loader's own discovery gate for each endpoint — the sys.path
    provenance fence (:func:`~aelix_coding_agent.extensions.ep_manifest.
    entry_point_provenance`) first, then the metadata-only resolver
    (:func:`~aelix_coding_agent.extensions.ep_manifest.resolve_entry_point_manifest`)
    — and records the verdict WITHOUT importing a single line of plugin code.
    This is the primitive both ``extension verify`` (the CI gate) and
    ``extension list`` (the human annotation) read.

    ``trusted_ep_dists`` are PEP 503 distribution names the operator vouched for
    with ``--trust-extension-path`` — the developer running an ``pip install -e``
    pack, whose dist lives outside the environment's real site directories and
    would otherwise be refused by the provenance fence.
    """

    out: list[EndpointStatus] = []
    try:
        eps = list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001 — a broken env yields an empty inventory, never a crash
        return out
    # Computed once: the environment's real site directories do not change while
    # we classify, and each probe touches the filesystem.
    trusted_dirs = environment_site_dirs()
    for ep in eps:
        dist = getattr(ep, "dist", None)
        dist_name = getattr(dist, "name", None) if dist is not None else None
        version = getattr(dist, "version", None) if dist is not None else None
        dn = str(dist_name) if dist_name else None
        vn = str(version) if version else None

        prov = entry_point_provenance(
            ep, trusted_dirs=trusted_dirs, allowed_dists=trusted_ep_dists
        )
        if prov is not None:
            # UNTRUSTED_PATH — refused before any manifest read (fail-closed).
            out.append(EndpointStatus(ep.name, dn, vn, prov.outcome, prov.reason, None))
            continue
        try:
            res = resolve_entry_point_manifest(ep)
        except EpApiLevelRefusal as exc:
            out.append(EndpointStatus(ep.name, dn, vn, None, str(exc), None))
            continue
        except Exception as exc:  # noqa: BLE001 — a broken dist is a failed verify, not a crash
            out.append(
                EndpointStatus(
                    ep.name,
                    dn,
                    vn,
                    EpOutcome.UNPROVEN,
                    f"cannot resolve the manifest of entry point {ep.name!r}: {exc}",
                    None,
                )
            )
            continue
        plugin_id = res.manifest.plugin.id if res.manifest is not None else None
        out.append(EndpointStatus(ep.name, dn, vn, res.outcome, res.reason, plugin_id))
    return out


def _print_endpoint_status(status: EndpointStatus) -> None:
    """Render ONE endpoint verdict — the SINGLE renderer ``verify`` and the
    post-install report both call (issue #154).

    Shared on purpose. The defect #154 reports is not that install lacked a
    check; it is that install and ``verify``, running the SAME primitive at the
    SAME instant, told the user opposite things. One renderer makes a wording
    drift between them impossible.
    """

    if status.bound:
        print(f"  BOUND     {status.label()}  (manifest: plugin {status.plugin_id!r})")
        return
    # ``outcome is None`` is the API-level refusal — the pack says in writing it
    # cannot run on this host, so it is INCOMPATIBLE, not merely unproven.
    state = status.outcome.value.upper() if status.outcome is not None else "INCOMPATIBLE"
    print(f"  {state:<9} {status.label()}")
    print(f"      {status.reason}")
    if status.outcome is EpOutcome.ABSENT:
        print(f"      hint: {_ABSENT_HINT}")


def _endpoint_is_refused(status: EndpointStatus) -> bool:
    """True when the loader drops this endpoint ENTIRELY — no carrier at all.

    Exactly two outcomes do that (loader.py ``_entry_point_manifests``): the API
    refusal (``outcome is None`` here) and ``UNTRUSTED_PATH``. Every other
    non-bound outcome still yields a carrier and runs ``setup()``; it only loses
    the manifest, hence its declarative contributions. The distinction is worth
    keeping because "will not load at all" and "loads without its declarations"
    call for different actions, and claiming the stronger one falsely would be
    its own defect.
    """

    return status.outcome is None or status.outcome is EpOutcome.UNTRUSTED_PATH


def _installed_ext_dists() -> dict[str, str | None]:
    """Snapshot ``dist name -> version`` for every dist exposing an endpoint.

    The version rides along so an UPGRADE of an already-installed pack is
    attributable to the install that caused it — a name-only diff sees nothing
    change and would report the new build's verdict on nobody.
    """

    out: dict[str, str | None] = {}
    for ext in list_installed_extensions():
        if ext.dist_name:
            out[ext.dist_name] = ext.version
    return out


def _target_dist_hint(target: str, kind: TargetKind) -> str | None:
    """The distribution name the TARGET ITSELF names, when it names one.

    The before/after diff catches a first install and an upgrade, but not a
    reinstall of the same version — pip is a no-op there ("Requirement already
    satisfied"), nothing in the ledger moves, and a diff-only attribution would
    fall silent on the second ``install`` of the very pack that cannot bind. So
    the target is asked as well:

    * ``pypi`` — the bare name IS the distribution name, by definition;
    * ``path`` — a wheel/sdist filename carries it, and a source tree carries it
      in ``[project] name``;
    * ``git`` — NOTHING. A repository name is not a distribution name and
      guessing would risk attributing an unrelated installed pack to this
      install, which is the one error this whole function must not make.

    Whatever comes back is only ever INTERSECTED with what is actually installed,
    so a wrong guess yields nothing rather than a false accusation.

    This reads only what a tree DECLARES, so it is silent for the trees whose name
    lives somewhere it cannot look without executing them — setup.py-only, a
    ``[tool.poetry]`` name, a ``dynamic`` name. Those, and git, are what
    :func:`_dists_recorded_from` answers by reading pip's own record instead of
    guessing; whatever neither can name gets :data:`_INSTALLED_NO_VERDICT`.
    """

    if kind == "pypi":
        return _bare_package_name(target) or None
    if kind == "git":
        return None
    path = Path(target).expanduser()
    name = path.name
    if name.endswith(".whl"):
        # PEP 427: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}
        return name.split("-", 1)[0] or None
    for suffix in (".tar.gz", ".zip", ".tar.bz2"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            return stem.rsplit("-", 1)[0] or None
    pyproject = path / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if isinstance(project, dict):
        declared = project.get("name")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    return None


def _target_source_key(target: str, kind: TargetKind) -> str | None:
    """A comparison key for the SOURCE this install came from, or ``None``.

    Pairs with :func:`_recorded_source_key`, which reads the same key back out of
    an installed distribution's PEP 610 ``direct_url.json`` — the record pip (and
    uv) writes for every DIRECT reference saying "this dist came from exactly
    this URL". A ``pypi`` install has no such record by definition (PEP 610 is
    only for direct references), and its bare name already IS the dist name.

    Normalised rather than string-compared, because the two sides are written by
    different code: pip stores ``file://`` + the RESOLVED path for a tree,
    percent-encoded, and stores a git URL with ``git+`` and any ``@<rev>`` /
    ``#egg=`` stripped off. So a ``file:`` URL is reduced to its filesystem path
    and everything else to the bare URL. A key that fails to match yields no
    attribution — never a wrong one.

    The path branch resolves SYMLINKS (:func:`os.path.realpath`), not merely
    ``abspath``. It has to, because :func:`_install_spec` — which builds the
    argument pip actually receives — resolves them too (``Path.resolve()``), and
    pip records what it was given. Normalising one side less than the other made
    the keys unequal for every symlinked target, so a perfectly HEALTHY pack
    installed through a link lost its clean line to the no-verdict floor on a
    repeat install. ``realpath`` is ``abspath`` plus link resolution plus ``..``
    collapsing (in that order, which is the only correct one once links are in
    play), so it subsumes what was here before; on a path that does not exist it
    degrades to exactly the old normalisation rather than raising.
    """

    if kind == "pypi":
        return None
    if kind == "git":
        spec = target[4:] if target.startswith("git+") else target
        spec = spec.split("#", 1)[0]
        head, sep, tail = spec.rpartition("@")
        # ``…/repo@v1`` is a revision; ``ssh://git@host/repo`` is an authority —
        # the difference is whether the ``@`` comes after the last ``/``.
        if sep and "/" not in tail:
            spec = head
        return _url_fs_key(spec)
    try:
        return os.path.realpath(os.path.expanduser(target)).rstrip("/") or None
    except (OSError, ValueError):  # pragma: no cover — realpath on a hostile cwd
        return None


def _url_fs_key(url: str) -> str | None:
    """``file:`` URL → its RESOLVED filesystem path; anything else → the URL, unslashed.

    Symlinks are resolved for the same reason :func:`_target_source_key` resolves
    them: this function normalises BOTH sides of the PEP 610 comparison (the
    target's ``git+file://`` spec and the ``url`` pip recorded), and the two are
    only equal when both are reduced to the same real location.
    """

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme == "file":
        from urllib.request import url2pathname

        try:
            path = url2pathname(parsed.path)
        except (OSError, ValueError):  # pragma: no cover — malformed percent-escape
            return None
        return os.path.realpath(path).rstrip("/") or None
    return url.rstrip("/") or None


def _recorded_source_key(dist: importlib.metadata.Distribution) -> str | None:
    """The source key pip recorded for ``dist`` in PEP 610 ``direct_url.json``."""

    try:
        raw = dist.read_text("direct_url.json")
    except Exception:  # noqa: BLE001 — an unreadable dist is simply not a match
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url:
        return None
    return _url_fs_key(url)


def _dists_recorded_from(
    target: str, kind: TargetKind, after: Mapping[str, str | None]
) -> frozenset[str]:
    """Installed extension dists whose PEP 610 record names THIS exact target.

    The last attribution arm, and the only one that survives a no-op reinstall of
    a target that names no distribution: pip itself wrote down where each
    directly-referenced dist came from, so this is a LOOKUP, not the guess
    :func:`_target_dist_hint` refuses to make. Intersected with ``after`` so it
    can only ever return a distribution that is installed AND exposes an
    ``aelix.extensions`` endpoint.

    Consulted ONLY when the ledger diff and the target's own name found nothing.
    A stale record — an author who renamed the distribution built from the same
    directory — would otherwise let a superseded dist ride along with the one
    that actually moved; when something moved, that movement is the better
    answer and this arm never runs.
    """

    key = _target_source_key(target, kind)
    if key is None:
        return frozenset()
    wanted = {_canon(name): name for name in after}
    out: set[str] = set()
    try:
        for dist in importlib.metadata.distributions():
            raw_name = getattr(dist, "name", None)
            if not raw_name:
                continue
            installed = wanted.get(_canon(str(raw_name)))
            if installed is None or installed in out:
                continue
            if _recorded_source_key(dist) == key:
                out.add(installed)
    except Exception:  # noqa: BLE001 — a broken env attributes nothing, never crashes
        return frozenset()
    return frozenset(out)


def _attributed_dists(
    target: str,
    kind: TargetKind,
    before: Mapping[str, str | None],
    after: Mapping[str, str | None],
) -> frozenset[str]:
    """The distributions THIS install is responsible for — never the whole env.

    ``classify_installed_endpoints`` reports every installed endpoint. Printing
    all of them after installing one pack would be noise, and would blame the new
    pack for a broken endpoint that was already there before the command ran.
    Attribution is therefore narrow sources, in order of how much they know:

    * a dist that appeared (first install),
    * a dist whose version moved (upgrade),
    * the dist the target itself names, IF it is installed (reinstall),
    * failing all of those, the dist pip RECORDED as coming from this exact
      target (:func:`_dists_recorded_from`) — the arm that answers a repeat
      ``git+URL`` install and a repeat install of a tree whose name cannot be
      read without executing it.

    The first three are unioned; the fourth is a fallback rather than a fourth
    union member, because it is the only one that can name a dist this command
    did not touch (a stale PEP 610 record survives a rename). When something
    really did move, that movement is the better answer.

    One distribution can expose several endpoints, so this is a set of DISTS and
    the report filters endpoints by dist membership — not the other way round.

    An EMPTY result is a real outcome, not a bug: it means this command cannot
    name what landed. :func:`_report_install_binding` must then say so rather
    than fall back to a success line (:data:`_INSTALLED_NO_VERDICT`).
    """

    attributed = {name for name in after if name not in before}
    attributed |= {
        name for name, version in after.items() if name in before and before[name] != version
    }
    hint = _target_dist_hint(target, kind)
    if hint:
        want = _canon(hint)
        attributed |= {name for name in after if _canon(name) == want}
    if attributed:
        return frozenset(attributed)
    return _dists_recorded_from(target, kind, after)


def _report_install_binding(
    dists: frozenset[str], *, trusted_ep_dists: frozenset[str] = frozenset()
) -> int:
    """Print what the host already knows about the pack just installed (#154).

    Runs :func:`classify_installed_endpoints` — import-free, offline, the same
    primitive ``verify`` and ``list`` read — and keeps only the endpoints owned by
    ``dists``. Returns ``0`` when every one of them is BOUND (and prints exactly
    the pre-#154 line, so a healthy install gains no new output at all), or
    :data:`_INSTALL_NOT_BOUND` when any is not.

    An EMPTY ``dists`` means attribution could not tie the install to an installed
    extension distribution, so there is no verdict to report: that prints
    :data:`_INSTALLED_NO_VERDICT` and returns ``0`` — the package is on disk, but
    nothing here may claim it will load. The one thing this must never do is
    invent a verdict, in either direction.
    """

    if not dists:
        print(_INSTALLED_NO_VERDICT)
        return 0
    # A same-process install is invisible to importlib.metadata until the path
    # caches are dropped.
    importlib.invalidate_caches()
    owned = {_canon(name) for name in dists}
    statuses = [
        s
        for s in classify_installed_endpoints(trusted_ep_dists=trusted_ep_dists)
        if s.dist_name is not None and _canon(s.dist_name) in owned
    ]
    unbound = [s for s in statuses if not s.bound]
    if not unbound:
        print(_INSTALLED_RESTART)
        return 0

    refused = [s for s in unbound if _endpoint_is_refused(s)]
    print(
        f"Installed, but this build already knows {len(unbound)} of "
        f"{len(statuses)} endpoint(s) will NOT bind:"
    )
    for status in unbound:
        _print_endpoint_status(status)
    if refused:
        print("  → not loaded at all on this build.")
    if len(refused) < len(unbound):
        print(
            "  → their declarative contributions (tools, hooks, themes, TUI "
            "widgets, MCP servers) are ignored until the manifest binds."
        )
    bound = len(statuses) - len(unbound)
    if bound:
        print(
            f"{bound} endpoint(s) of this install DID bind — restart aelix "
            f"(or /reload in the TUI) to pick those up."
        )
    # The pip package is already on disk by the time any of this is knowable, and
    # silently undoing what the user asked for would be its own defect. So: say
    # so plainly, and hand over the exact command rather than performing it.
    # ``statuses`` is filtered on ``dist_name is not None``, so this is non-empty
    # whenever ``unbound`` is.
    affected = sorted({s.dist_name for s in unbound if s.dist_name})
    print("The distribution is installed; nothing was undone. To remove it:")
    for dist in affected:
        print(f"  aelix extension remove {dist}")
    print(f"To re-check: aelix extension verify {affected[0]}")
    return _INSTALL_NOT_BOUND


def _cmd_list() -> int:
    """``extension list`` — the installed inventory (entry-point ledger).

    Since issue #91 each row is annotated with its manifest-binding verdict, so
    the silent failure the marketplace footgun produces — *installed, no
    manifest, declarations inert* — is VISIBLE here instead of only discoverable
    by noticing a contribution never showed up. The classification is
    import-free (see :func:`classify_installed_endpoints`).
    """

    statuses = classify_installed_endpoints()
    if not statuses:
        print("No extensions installed (via entry_points).")
        return 0
    print("Installed extensions:")
    for s in statuses:
        if s.bound:
            note = ""
        elif s.outcome is EpOutcome.ABSENT:
            note = "  — no manifest; declarative contributions inert"
        elif s.outcome is None:
            note = "  — INCOMPATIBLE with this host; not loaded (run 'extension verify')"
        elif s.outcome is EpOutcome.UNTRUSTED_PATH:
            note = "  — REFUSED (untrusted sys.path provenance); not loaded (run 'extension verify')"
        else:
            note = (
                f"  — manifest not bound ({s.outcome.value}); declarations inert "
                f"(run 'extension verify')"
            )
        print(f"  {s.label()}{note}")
    return 0


def _cmd_index(args: list[str]) -> int:
    """``extension index <dir> [--out FILE] [--name NAME] [--relative]`` (#68).

    Generate a ``catalog.json`` from a directory of built distributions, so a
    private or air-gapped operator stops hand-writing the document and keeping
    its hashes in sync with the wheels by hand. Reads ``name`` / ``version`` /
    ``description`` from each artifact's own core metadata and hashes its bytes.

    Writes ``<dir>/catalog.json`` unless ``--out`` says otherwise; ``--out -``
    prints to stdout so the document can be piped or reviewed before it lands.
    Exit 0 on success (including an empty directory, which yields an empty but
    valid catalog), ``_EXIT_DIDNT_RUN`` (2) on a usage or filesystem error.
    """

    directory: str | None = None
    out: str | None = None
    doc_name: str | None = None
    relative = False
    pending = iter(args)
    for a in pending:
        if a in ("-h", "--help"):
            print(_USAGE)
            return 0
        if a in ("--out", "--name"):
            val = next(pending, None)
            if val is None:
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            if a == "--out":
                out = val
            else:
                doc_name = val
        elif a.startswith("--out="):
            out = a.split("=", 1)[1]
        elif a.startswith("--name="):
            doc_name = a.split("=", 1)[1]
        elif a == "--relative":
            relative = True
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif directory is None:
            directory = a
        else:
            print(f"Error: index takes one <dir>.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN

    if directory is None:
        print(f"Error: index requires a <dir>.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    # Resolved BEFORE the scan, not after: the scan yields paths built from this
    # root, and `--relative` measures them against it. Resolving only at the
    # comparison left `index . --relative` measuring a bare `foo.whl` against an
    # absolute directory, which is a ValueError — and it landed outside the
    # try/except below, so the command that exists for a portable wheelhouse
    # died with a raw traceback.
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        print(f"Error: {directory!r} is not a directory.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    try:
        artifacts = extension_catalog.scan_artifacts(root)
    except OSError as exc:
        print(f"Error: cannot read {directory!r}: {exc}", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    document = extension_catalog.build_index_catalog(
        artifacts,
        name=doc_name,
        # A relative source resolves against the PROCESS working directory (see
        # classify_target), not against the catalog — so relative is opt-in and
        # the default is absolute.
        relative_to=root if relative else None,
    )
    payload = json.dumps(document, indent=2, sort_keys=False) + "\n"

    if out == "-":
        sys.stdout.write(payload)
        return 0

    target = (
        Path(out).expanduser()
        if out
        else root / extension_catalog.DEFAULT_CATALOG_FILENAME
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write {str(target)!r}: {exc}", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    count = len(document.get("extensions", []))  # type: ignore[arg-type]
    skipped = sum(
        1
        for c in root.iterdir()
        if c.is_file() and c.name.endswith(extension_catalog.INDEX_ARTIFACT_SUFFIXES)
    ) - len(artifacts)
    noun = "extension" if count == 1 else "extensions"
    print(f"Wrote {target} ({count} {noun} from {len(artifacts)} artifacts).")
    if skipped > 0:
        print(f"Skipped {skipped} archive(s) with no readable metadata.")
    if count:
        print("Register it with:")
        # as_uri(), not "file://" + path: on Windows the hand-built form yields
        # file://C:\... — two slashes, so urlparse() reads the whole tail as a
        # netloc and the printed command is refused as a remote host (issue #205).
        print(f"  aelix extension source add --catalog {target.resolve().as_uri()}")
    return 0


def _cmd_verify(args: list[str]) -> int:
    """``extension verify [<name>] [--trust-extension-path DIST]`` — the
    import-free BOUND gate (issue #91, ADR-0207).

    Reports, per installed ``aelix.extensions`` endpoint, whether its
    ``aelix-plugin.toml`` will bind — using the loader's own provenance gate and
    metadata-only resolver, so nothing here imports the pack. Exit status is
    STABLE for CI: 0 iff every reported endpoint is BOUND; ``_VERIFY_NOT_BOUND``
    (1) if any is ABSENT / MALFORMED / MISPLACED / FENCED / UNPROVEN /
    UNTRUSTED / API-incompatible; ``_EXIT_DIDNT_RUN`` (2) on a usage error or a
    named target that matches nothing installed.

    The intended catalog-submission flow: install the candidate into a clean
    environment, run ``aelix extension verify <name>``, and gate on the exit
    code — a BOUND manifest is what makes a catalog entry an auditable,
    gated set of contributions rather than a black-box entry-point pack.
    """

    target: str | None = None
    trusted: set[str] = set()
    pending = iter(args)
    for a in pending:
        if a in ("-h", "--help"):
            print(_USAGE)
            return 0
        if a == "--trust-extension-path":
            val = next(pending, None)
            if val is None:
                print(
                    "Error: --trust-extension-path requires a DIST name.",
                    file=sys.stderr,
                )
                return _EXIT_DIDNT_RUN
            trusted.add(val)
        elif a.startswith("--trust-extension-path="):
            trusted.add(a.split("=", 1)[1])
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif target is None:
            target = a
        else:
            print(
                f"Error: verify takes at most one <name>.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN

    statuses = classify_installed_endpoints(trusted_ep_dists=frozenset(trusted))

    if target is not None:
        want = _canon(target)
        statuses = [
            s
            for s in statuses
            if _canon(s.ep_name) == want
            or (s.dist_name is not None and _canon(s.dist_name) == want)
        ]
        if not statuses:
            print(
                f"Error: no installed aelix.extensions endpoint matches {target!r} "
                f"(via entry_points group {ENTRY_POINT_GROUP!r}).",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN

    if not statuses:
        print("No extensions installed (via entry_points); nothing to verify.")
        return 0

    failed = 0
    for s in statuses:
        if not s.bound:
            failed += 1
        # ONE renderer, shared with the post-install report (#154) — see
        # :func:`_print_endpoint_status`.
        _print_endpoint_status(s)

    total = len(statuses)
    if failed:
        print(
            f"\nverify: {failed} of {total} endpoint(s) NOT bound; their "
            f"declarative contributions are ignored until the manifest binds."
        )
        return _VERIFY_NOT_BOUND
    print(f"\nverify: all {total} endpoint(s) BOUND.")
    return 0


async def _cmd_install(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension install <target>`` — #19 install + resolve + record + VERDICT.

    Since issue #154 the command ends by asking the host's own import-free
    resolver whether the pack it just wrote to disk can actually bind, and reports
    THAT instead of an unconditional "Installed. Restart aelix". The check was
    already written, already local, and already read by ``verify`` and ``list``;
    install simply never called it, so the two commands contradicted each other
    at the same instant on the same build.

    Exit: ``0`` when pip succeeded and every attributed endpoint is BOUND (output
    byte-identical to pre-#154 — a healthy install gains no noise);
    :data:`_INSTALL_NOT_BOUND` (3) when pip succeeded and some attributed endpoint
    is not; :data:`_INSTALLER_FAILED` (1) when the installer ran and failed; ``2``
    when it never ran.

    ``0`` is ALSO returned when attribution came back empty — pip put something on
    disk and this command cannot name it. That is not a success claim and does not
    print one: see :data:`_INSTALLED_NO_VERDICT`.
    """

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

    before = _installed_ext_dists()
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
        require_signature=parsed.require_signature,
        trusted_key=parsed.trusted_key,
        signature_path=parsed.signature_path,
        # The success line is OURS now — we know something install_extension does
        # not: whether the thing it just wrote can bind.
        announce=False,
        input_fn=input_fn,
        runner=runner,
    )
    if code != 0:
        return code
    await _record_install(settings, target, kind, set(before))
    # Never let a classification fault turn a completed install into a crash: the
    # package IS on disk. But a fault means we do NOT have the verdict, so the
    # line printed is the no-verdict one, not the success one — otherwise stdout
    # would promise the pack loads while stderr says the check never ran.
    # ``classify_installed_endpoints`` already degrades a broken environment to an
    # empty inventory, so this is belt-and-braces.
    try:
        return _report_install_binding(
            _attributed_dists(target, kind, before, _installed_ext_dists()),
            trusted_ep_dists=frozenset(parsed.trust_extension_paths),
        )
    except Exception as exc:  # noqa: BLE001 — reporting must not fail the install
        print(
            f"Warning: could not check whether the pack binds ({exc}).",
            file=sys.stderr,
        )
        print(_INSTALLED_NO_VERDICT)
        return 0


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
    """``extension update [<name>]`` — reinstall recorded source(s) ``--upgrade``.

    Each pack ends with the same import-free binding verdict ``install`` prints
    (see :func:`_upgrade_and_report`); with several packs the run also closes with
    a summary, because per-pack reports bury the outcome
    (:func:`_print_update_summary`).

    Exit: ``0`` when every pack upgraded and every attributed endpoint is BOUND —
    or when there was no verdict to give, which is not a success claim and does
    not print one; :data:`_INSTALL_NOT_BOUND` (3) when every installer run
    succeeded but some attributed endpoint will not bind; :data:`_INSTALLER_FAILED`
    (1) when an installer ran and failed; ``2`` when one never ran. The last two
    outrank 3 (:func:`_worse_update_code`).
    """

    name_filter: str | None = None
    yes = False
    offline = False
    no_verify = False
    strict = False
    repin = False
    verify_pypi = False
    require_signature = False
    trusted_key: str | None = None
    signature_path: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
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
        elif a == "--require-signature":
            require_signature = True
        elif a == "--trusted-key" or a == "--signature":
            i += 1
            if i >= len(args) or not args[i].strip():
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            if a == "--trusted-key":
                trusted_key = args[i]
            else:
                signature_path = args[i]
        elif a.startswith("--trusted-key=") or a.startswith("--signature="):
            flag, value = a.split("=", 1)
            if not value.strip():
                print(f"Error: {flag} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            if flag == "--trusted-key":
                trusted_key = value
            else:
                signature_path = value
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif name_filter is None:
            name_filter = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    verify = _VerifyOpts(
        no_verify=no_verify, strict=strict, repin=repin, verify_pypi=verify_pypi,
        require_signature=require_signature, trusted_key=trusted_key,
        signature_path=signature_path,
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
            code, _dists = _upgrade_pypi_name(
                name_filter,
                index_urls,
                yes=yes,
                offline=offline,
                verify=verify,
                input_fn=input_fn,
                runner=runner,
            )
            return code
        targets = matched
    else:
        targets = installable
        if not targets:
            print("No recorded extension sources to update.")
            return 0

    worst = 0
    results: list[tuple[str, int, frozenset[str]]] = []
    for s in targets:
        code, dists = _upgrade_source(
            s,
            index_urls,
            yes=yes,
            offline=offline,
            verify=verify,
            input_fn=input_fn,
            runner=runner,
        )
        results.append((s.name or _source_identity(s.spec, s.kind), code, dists))
        worst = _worse_update_code(worst, code)
    _print_update_summary(results)
    return worst


def _worse_update_code(worst: int, code: int) -> int:
    """Fold one pack's exit code into the run's, HARDEST failure first.

    Before #154 reached this verb, ``update`` folded with "first nonzero wins".
    That rule was complete because every code it could see meant *something went
    wrong*. :data:`_INSTALL_NOT_BOUND` breaks it: 3 is the one code that ASSERTS
    the installer succeeded ("it is on disk, and it will not bind"). Under
    first-nonzero-wins, an early inert pack followed by a pack pip failed on
    outright would exit 3 — telling a script everything landed when something did
    not. So 1 / 2 displace a previously recorded 3, and 3 is reported only when it
    is true of every target. Among the genuine failures the original rule stands:
    the earliest one wins.
    """

    if worst == 0:
        return code
    if code == 0:
        return worst
    if worst == _INSTALL_NOT_BOUND and code != _INSTALL_NOT_BOUND:
        return code
    return worst


def _print_update_summary(results: list[tuple[str, int, frozenset[str]]]) -> None:
    """Close a multi-pack ``update`` with the verdict, so it is not buried.

    A bare ``extension update`` walks every recorded source, and each pack now
    prints ``install``'s full report — which for a pack that cannot bind is a
    multi-line block ending in the ABSENT packaging hint. Three of those and the
    outcome has scrolled away; the last thing on screen would be one arbitrary
    pack's remove command. So the run ends by naming what happened to all of them.

    Printed ONLY when there are several packs AND at least one is not clean. A run
    where every pack bound stays byte-identical to what this verb printed before
    #154 touched it (one :data:`_INSTALLED_RESTART` per pack, nothing else), which
    keeps the compatible path free of new noise; and a single-pack update needs no
    summary because its own report is already the last thing printed.
    """

    if len(results) < 2:
        return
    bound = [label for label, code, dists in results if code == 0 and dists]
    unbound = [label for label, code, _ in results if code == _INSTALL_NOT_BOUND]
    unknown = [label for label, code, dists in results if code == 0 and not dists]
    failed = [
        label
        for label, code, _ in results
        if code not in (0, _INSTALL_NOT_BOUND)
    ]
    if not (unbound or unknown or failed):
        return
    print(
        f"update summary: {len(results)} pack(s) — {len(bound)} bound, "
        f"{len(unbound)} NOT bound, {len(unknown)} no verdict, {len(failed)} failed."
    )
    for title, names in (
        ("NOT bound", unbound),
        ("no verdict", unknown),
        ("failed", failed),
    ):
        if names:
            print(f"  {title}: {', '.join(sorted(names))}")
    if unbound or unknown:
        # ``verify <name>`` only works for a row labelled with a DISTRIBUTION
        # name. A recorded source whose dist name was never detected is labelled
        # with its path instead (``_source_identity``), and
        # ``verify /path/to/pack`` matches nothing installed — exit 2. Offering
        # it there would make the summary's one actionable line the one thing
        # that cannot work, for exactly the rows that need it. The argument-less
        # form settles every endpoint at once and is true for any label.
        named = all(
            os.sep not in label and "/" not in label
            for label in (*unbound, *unknown)
        )
        print(
            "To re-check one: aelix extension verify <name>"
            if named
            else "To re-check: aelix extension verify"
        )


def _upgrade_and_report(
    target: str,
    *,
    yes: bool,
    index_url: str | None = None,
    extra_index_urls: Iterable[str] | None = None,
    offline: bool,
    verify: _VerifyOpts,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> tuple[int, frozenset[str]]:
    """``install_extension(..., upgrade=True)`` + the SAME verdict ``install`` prints.

    ``update`` reinstalls through :func:`install_extension`, which announces an
    unconditional :data:`_INSTALLED_RESTART` — the exact promise issue #154 exists
    to stop making, still alive on this verb after ``install`` stopped making it.
    MEASURED on the build that fixed ``install``, throwaway venv, real pip:
    ``extension update legacypack --yes`` printed "Installed. Restart aelix (or
    /reload in the TUI) …" and exited 0, while ``extension verify legacypack`` —
    same build, same instant, same pack — printed "1 of 1 endpoint(s) NOT bound"
    and exited 1. An upgrade IS an install; the host knows exactly as much about
    the bytes it just wrote either way, and nothing about ``--upgrade`` makes that
    promise any safer to make.

    So: ``announce=False``, a before/after ledger snapshot around the call, and the
    result through :func:`_report_install_binding` — the same three moves
    :func:`_cmd_install` makes, so the two verbs cannot drift apart again.

    Returns the exit code AND the dists attribution named, because
    :func:`_cmd_update` may be upgrading many packs and has to summarise them: an
    empty set with code ``0`` is "no verdict", a non-empty set with code ``0`` is
    "bound", and the non-zero codes speak for themselves.
    """

    before = _installed_ext_dists()
    code = install_extension(
        target,
        yes=yes,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        offline=offline,
        upgrade=True,
        no_verify=verify.no_verify,
        strict=verify.strict,
        repin=verify.repin,
        verify_pypi=verify.verify_pypi,
        require_signature=verify.require_signature,
        trusted_key=verify.trusted_key,
        signature_path=verify.signature_path,
        # The success line is OURS now — same reason as _cmd_install: we know
        # something install_extension does not, namely whether it can bind.
        announce=False,
        input_fn=input_fn,
        runner=runner,
    )
    if code != 0:
        return code, frozenset()
    try:
        # A same-process install stays invisible to importlib.metadata until the
        # path caches are dropped. ``install`` gets this for free from
        # _record_install, which ``update`` does not run.
        importlib.invalidate_caches()
        dists = _attributed_dists(
            target, classify_target(target), before, _installed_ext_dists()
        )
        return _report_install_binding(dists), dists
    except Exception as exc:  # noqa: BLE001 — reporting must not fail the upgrade
        print(
            f"Warning: could not check whether the pack binds ({exc}).",
            file=sys.stderr,
        )
        print(_INSTALLED_NO_VERDICT)
        return 0, frozenset()


def _upgrade_source(
    source: ExtensionSourceObject,
    index_urls: list[str],
    *,
    yes: bool,
    offline: bool,
    verify: _VerifyOpts,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> tuple[int, frozenset[str]]:
    """Reinstall one recorded source with ``--upgrade``, and report its verdict."""

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
    return _upgrade_and_report(
        source.spec,
        yes=yes,
        offline=offline,
        verify=verify,
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
) -> tuple[int, frozenset[str]]:
    """``pip install --upgrade <name>`` against the registered index sources."""

    index_url = index_urls[0] if index_urls else None
    extra = index_urls[1:] if index_urls else []
    return _upgrade_and_report(
        name,
        yes=yes,
        index_url=index_url,
        extra_index_urls=extra,
        offline=offline,
        verify=verify,
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

    # #113: the abort names the extension AND the distribution it resolved to, so
    # a backend-less environment still tells the user what was being removed.
    backend = resolve_install_backend(runner)
    if backend is None:
        print(
            _backend_missing_message("remove", f"{name!r} (distribution {dist!r})"),
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    pip_args = backend.uninstall_args(dist)
    print(f"Remove extension: {name} → uninstall distribution {dist}")
    print(f"  → {display_argv(pip_args)}")
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
        print(f"{backend.label} uninstall failed (exit {code}).", file=sys.stderr)
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
# === `discover` — browse/search an advisory catalog (#65) =============
# =====================================================================


def _print_entry_row(entry: extension_catalog.CatalogEntry) -> None:
    """One ``discover`` result row: ``  <name> <version?>  — <desc?>  (catalog: …)``."""

    ver = entry.display_version()
    ver_part = f" {ver}" if ver else ""
    desc_part = f"  — {entry.description}" if entry.description else ""
    catalog = entry.catalog_name or "?"
    print(f"  {entry.name}{ver_part}{desc_part}   (catalog: {catalog})")


def _make_catalog_verifier(
    agent_dir: str, *, signature_required: frozenset[str]
) -> extension_catalog.DocumentVerifier:
    """Adapt :func:`extension_signing.verify_signed_document` into a document verifier.

    The returned callable matches :data:`extension_catalog.DocumentVerifier`
    (``(document, sidecar, location) -> None``): it verifies the fetched catalog
    bytes against their ``.aelixsig`` sidecar, translating a fail-closed
    :class:`extension_pins.VerifyRefusal` into a :class:`extension_catalog.CatalogError`
    (so :func:`extension_catalog.fetch_all` degrades ONLY that catalog to an error row,
    never admitting unverified entries). ``signature_required`` is the set of location
    identities that MUST carry a valid trusted signature (guard ⑤ — the official /
    default catalog). The default catalog URL is LIVE, but the set stays EMPTY while
    ``FIRST_PARTY_KEYS`` is unprovisioned, so today the default is admitted
    best-effort over TLS; committing the first-party key auto-upgrades it. A
    location outside that set verifies best-effort (an unsigned intranet catalog is
    admitted while first-party keys are empty; a present-but-INVALID trusted signature
    still refuses).
    """

    def verify(document: bytes, sidecar: bytes | None, location: str) -> None:
        require = _catalog_identity(location) in signature_required
        try:
            extension_signing.verify_signed_document(
                document,
                sidecar,
                kind="catalog",
                agent_dir=agent_dir,
                require_signature=require,
                identity=location,
            )
        except extension_pins.VerifyRefusal as exc:
            raise extension_catalog.CatalogError(
                f"catalog signature verification refused for {location}: {exc}"
            ) from exc

    return verify


async def _cmd_discover(
    rest: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension discover [<query>] [--refresh]`` / ``discover install <name>``.

    Browse or search the registered advisory catalogs (#65/ADR-0188), or resolve a
    name and DELEGATE to the unchanged gated install. The catalog is advisory: it
    only picks WHAT to install; consent + ``verify_and_pin`` (#64) + pip run
    unchanged, always on the RESOLVED ``entry.source`` — never the friendly name.
    """

    if rest and rest[0] == "install":
        return await _cmd_discover_install(
            rest[1:], settings=settings, input_fn=input_fn, runner=runner
        )

    # Browse: optional <query> positional + --refresh + Track-D --offline /
    # --no-default-catalog (both ephemeral — this-run-only, no tombstone write).
    query: str | None = None
    refresh = False
    offline_flag = False
    no_default_catalog = False
    for a in rest:
        if a == "--refresh":
            refresh = True
        elif a == "--offline":
            offline_flag = True
        elif a == "--no-default-catalog":
            no_default_catalog = True
        elif a in ("-h", "--help"):
            print(_USAGE)
            return 0
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif query is None:
            query = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN

    offline = _is_offline(offline_flag)
    # --no-default-catalog drops the built-in default for THIS run only (no tombstone)
    # → stored catalogs only; otherwise merge the built-in default in (guard ④ drops an
    # https default when offline, silently, inside the effective-locations helper).
    if no_default_catalog:
        locations = _catalog_locations(settings.get_extension_sources())
    else:
        locations = _effective_catalog_locations(settings, offline=offline)
    if not locations:
        print(
            "No catalog registered. Register one with: "
            "aelix extension source add --catalog <url|file|git>"
        )
        return 0

    agent_dir = get_agent_dir()
    if refresh:
        # The ONLY fetch/writer path — never let one bad source abort the rest.
        # Guard ④: offline fetches ONLY the air-gap allowlist (file:// / path /
        # git+ssh / git+file, see _is_offline_fetchable); every other transport
        # (https / git+https / git:// / git+git / ssh) is skipped with a per-source
        # notice. The built-in network default was already dropped upstream.
        fetch_locations = locations
        if offline:
            fetch_locations = []
            for loc in locations:
                if _is_offline_fetchable(loc):
                    fetch_locations.append(loc)
                else:
                    print(f"  ⓘ skipped {loc}: offline (network transport)")
        # Guard ⑤ (progressive hardening, ADR-0192 §amendment): the official / default
        # catalog is signature-required ONLY once a first-party trust anchor exists to
        # verify it (FIRST_PARTY_KEYS non-empty) — consistent with #67 per-extension
        # signing, which is warn-default until a trusted key is present. Before the
        # maintainer provisions the first-party catalog key the default stays
        # advisory-over-TLS (best-effort: an unsigned/untrusted default is admitted, a
        # present-but-INVALID trusted signature still refuses); committing the key
        # auto-upgrades it to fail-closed. The verifier gates each catalog's raw bytes
        # before parse/cache; a refusal degrades only that catalog to an error row.
        default_id = _effective_default_identity()
        signature_required = (
            frozenset({default_id})
            if (default_id is not None and extension_signing.FIRST_PARTY_KEYS)
            else frozenset()
        )
        verifier = _make_catalog_verifier(
            agent_dir, signature_required=signature_required
        )
        catalogs = extension_catalog.fetch_all(fetch_locations, verifier=verifier)
        if fetch_locations:
            extension_catalog.save_catalogs(
                catalogs, extension_catalog.cache_file_path(agent_dir)
            )
        else:
            # Offline skipped EVERY registered location (all network transports) →
            # never overwrite a previously-cached (online) catalog with an empty set.
            # A no-op refresh preserves the cache; the per-source "skipped" notices
            # above already explain why nothing was fetched.
            print("  ⓘ offline: no air-gap-reachable catalog to refresh; cache unchanged.")
    else:
        catalogs = extension_catalog.load_cached_catalog(agent_dir)

    # Surface a per-catalog fetch/parse failure without hiding the healthy ones.
    for cat in catalogs:
        if cat.error:
            print(f"  ⚠ {cat.label()}: {cat.error}", file=sys.stderr)

    # A refresh where EVERY registered catalog failed is a hard error (scriptable),
    # distinct from a successful-but-empty catalog (exit 0).
    if refresh and catalogs and all(cat.error for cat in catalogs):
        print(
            "Error: every registered catalog failed to fetch (see warnings above).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    if not refresh and not catalogs:
        print(
            "No cached catalog data. Fetch it with: aelix extension discover --refresh"
        )
        return 0

    entries = extension_catalog.search_entries(catalogs, query)
    if not entries:
        if query:
            print(f"No extensions match {query!r}.")
        else:
            print("No extensions found in the registered catalog(s).")
        return 0

    print(f"Discover ({len(entries)} match{'' if len(entries) == 1 else 'es'}):")
    for entry in entries:
        _print_entry_row(entry)
    if not refresh:
        stamps = [c.fetched_at for c in catalogs if c.fetched_at]
        if stamps:
            print(
                f"  (cached snapshot; oldest fetch {min(stamps)} — "
                "update with: aelix extension discover --refresh)"
            )
    return 0


async def _cmd_discover_install(
    rest: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``discover install <name> [--catalog CAT] [install flags]`` — resolve + delegate.

    Reads the CACHED catalogs (no implicit network refresh), resolves ``<name>`` to
    a single entry (REFUSING an ambiguous name with a candidate list), then hands
    the RESOLVED ``entry.source`` — never the friendly name — to the unchanged
    :func:`_cmd_install`, so consent + ``verify_and_pin`` + pip + ``_record_install``
    + the #154 post-install verdict all run exactly as for a direct install.

    That delegation is why ``discover install`` needs no verdict logic of its own,
    and why its exit code (including :data:`_INSTALL_NOT_BOUND`) is IDENTICAL to
    ``install``'s by construction rather than by two implementations agreeing.
    """

    name: str | None = None
    catalog_name: str | None = None
    install_flags: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--catalog":
            i += 1
            if i >= len(rest) or not rest[i].strip():
                print("Error: --catalog requires a catalog name.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            catalog_name = rest[i]
        elif a.startswith("--catalog="):
            catalog_name = a.split("=", 1)[1]
            if not catalog_name.strip():
                print("Error: --catalog requires a catalog name.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
        elif a in (
            "--index-url",
            "--trusted-key",
            "--signature",
            "--trust-extension-path",
        ):
            # A value-taking install flag — pass BOTH tokens through untouched so the
            # value is never mistaken for the <name> positional.
            install_flags.append(a)
            i += 1
            if i >= len(rest):
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            install_flags.append(rest[i])
        elif a.startswith("-"):
            # Any other flag (--yes/--offline/--no-verify/--strict/--repin/
            # --verify-pypi/--require-signature/--index-url=…/--trusted-key=…) rides
            # straight into _cmd_install, which validates it and rejects unknowns.
            install_flags.append(a)
        elif name is None:
            name = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    if name is None or not name.strip():
        print(
            f"Error: discover install requires an extension <name>.\n{_USAGE}",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    catalogs = extension_catalog.load_cached_catalog(get_agent_dir())
    resolved, candidates = extension_catalog.resolve_entry(
        catalogs, name, catalog=catalog_name
    )
    if resolved is None:
        if len(candidates) > 1:
            # If every candidate is in the SAME catalog, --catalog cannot
            # disambiguate — the catalog itself lists the name twice.
            labels = {c.catalog_name for c in candidates}
            if len(labels) == 1:
                only = next(iter(labels)) or "?"
                print(
                    f"Error: catalog {only!r} lists {name!r} more than once — "
                    "fix the catalog (a name must be unique within a catalog):",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Error: {name!r} is ambiguous across catalogs — "
                    "pass --catalog <name> to choose one of:",
                    file=sys.stderr,
                )
            for cand in candidates:
                label = cand.catalog_name or "?"
                print(f"  {cand.name}  →  {cand.source}   (catalog: {label})", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        print(
            f"Error: no catalog entry named {name!r} "
            "(try: aelix extension discover --refresh).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    print(
        f"Resolved {name} -> {resolved.source} (from catalog {resolved.catalog_name or '?'})"
    )
    # Delegate to the UNCHANGED install path with the RESOLVED spec (never the
    # friendly name), so a name→spec redirection is visible at consent. Flags come
    # first, then ``--``, then the source as the sole positional — so a resolved
    # spec that legitimately begins with ``-`` is never misparsed as a flag.
    return await _cmd_install(
        [*install_flags, "--", resolved.source],
        settings=settings,
        input_fn=input_fn,
        runner=runner,
    )


# =====================================================================
# === #67 (ADR-0189): Ed25519 provenance verbs — keygen / sign / trust =
# =====================================================================


def _cmd_keygen(args: list[str]) -> int:
    """``extension keygen`` — generate a publisher Ed25519 signing key (local, no pip).

    Writes the PRIVATE key to ``<agent_dir>/keys/<keyId>.pem`` at 0600 and prints ONLY
    the keyId + base64 public key (for out-of-band ``trust add`` distribution). No pip,
    no consent prompt — it only creates a key in the user's own key dir.
    """

    label: str | None = None
    force = False
    use_passphrase = False
    out_dir: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print("usage: aelix extension keygen [--label L] [--passphrase] [--force] [--out DIR]")
            return 0
        if a == "--force":
            force = True
        elif a == "--passphrase":
            use_passphrase = True
        elif a in ("--label", "--out"):
            i += 1
            if i >= len(args) or not args[i].strip():
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            if a == "--label":
                label = args[i]
            else:
                out_dir = args[i]
        elif a.startswith("--label="):
            label = a.split("=", 1)[1]
        elif a.startswith("--out="):
            out_dir = a.split("=", 1)[1]
        else:
            print(f"Error: unknown flag {a!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    passphrase: bytes | None = None
    if use_passphrase:
        import getpass

        pw = getpass.getpass("Passphrase for the new key (blank = none): ")
        passphrase = pw.encode("utf-8") if pw else None

    try:
        key_id, public_b64, path = extension_signing.keygen(
            get_agent_dir(), label=label, passphrase=passphrase, force=force, out_dir=out_dir
        )
    except extension_signing.CryptoUnavailable as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    except (FileExistsError, OSError) as exc:
        print(f"Error: could not write the key ({exc}).", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    print(f"Generated Ed25519 signing key {key_id}")
    print(f"  private key : {path}  (keep secret — 0600, never commit)")
    print(f"  public key  : {public_b64}")
    hint = f"aelix extension trust add {key_id} --public-key {public_b64}"
    if label:
        hint += f" --label {label!r}"
    print(f"  distribute  : {hint}")
    return 0


def _cmd_sign(args: list[str]) -> int:
    """``extension sign <artifact> --key <keyId|pem> …`` — write a ``.aelixsig`` (local)."""

    artifact: str | None = None
    key_ref: str | None = None
    name: str | None = None
    version: str | None = None
    kind = "path"
    out: str | None = None
    use_passphrase = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(
                "usage: aelix extension sign <artifact> --key <keyId|pem-path> "
                "[--name N] [--version V] [--kind path|pypi|catalog] [--passphrase] [--out FILE]"
            )
            return 0
        if a == "--passphrase":
            use_passphrase = True
        elif a in ("--key", "--name", "--version", "--kind", "--out"):
            i += 1
            if i >= len(args) or not args[i].strip():
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            value = args[i]
            key_ref, name, version, kind, out = _assign_sign_flag(
                a, value, key_ref, name, version, kind, out
            )
        elif a.startswith(("--key=", "--name=", "--version=", "--kind=", "--out=")):
            flag, value = a.split("=", 1)
            if not value.strip():
                print(f"Error: {flag} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            key_ref, name, version, kind, out = _assign_sign_flag(
                flag, value, key_ref, name, version, kind, out
            )
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif artifact is None:
            artifact = a
        else:
            print(f"Error: unexpected argument {a!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    if artifact is None:
        print("Error: sign requires an <artifact> path.", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    art = Path(artifact).expanduser()
    if not art.is_file():
        print(f"Error: artifact {artifact!r} is not a file.", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    if key_ref is None:
        print("Error: sign requires --key <keyId|pem-path>.", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    if kind not in ("path", "pypi", "catalog"):
        print("Error: --kind must be 'path', 'pypi', or 'catalog'.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    key_path = _resolve_key_path(key_ref)
    if not key_path.is_file():
        print(f"Error: no private key at {key_path}.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    passphrase: bytes | None = None
    if use_passphrase:
        import getpass

        pw = getpass.getpass("Key passphrase: ")
        passphrase = pw.encode("utf-8") if pw else None

    try:
        priv = extension_signing.load_private_key(key_path, passphrase=passphrase)
        if kind == "catalog":
            # A catalog document (catalog.json) is signed over its RAW bytes with a
            # kind="catalog" statement — the exact form verify_signed_document expects
            # (sign_artifact would stamp kind="path" and the catalog verifier would
            # reject it on a statement mismatch).
            import json

            sidecar_bytes = extension_signing.sign_document(
                art.read_bytes(), priv, kind="catalog", name=name, version=version
            )
            sidecar = (
                Path(out).expanduser()
                if out
                else art.with_name(art.name + extension_signing.AELIXSIG_SUFFIX)
            )
            sidecar.write_bytes(sidecar_bytes)
            key_id = str(json.loads(sidecar_bytes.decode("utf-8")).get("keyId", "?"))
        else:
            sidecar, key_id = extension_signing.sign_artifact(
                art, priv, kind=kind, name=name, version=version,
                out=Path(out).expanduser() if out else None,
            )
    except extension_signing.CryptoUnavailable as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    except Exception as exc:  # noqa: BLE001 — bad passphrase / unreadable key / bad PEM
        print(f"Error: could not sign ({exc}).", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    print(f"Signed {art.name} → {sidecar}  (keyId {key_id})")
    return 0


def _assign_sign_flag(
    flag: str,
    value: str,
    key_ref: str | None,
    name: str | None,
    version: str | None,
    kind: str,
    out: str | None,
) -> tuple[str | None, str | None, str | None, str, str | None]:
    """Route one ``sign`` value flag onto its variable (keeps the parse loop flat)."""

    if flag == "--key":
        key_ref = value
    elif flag == "--name":
        name = value
    elif flag == "--version":
        version = value
    elif flag == "--kind":
        kind = value
    elif flag == "--out":
        out = value
    return key_ref, name, version, kind, out


def _resolve_key_path(key_ref: str) -> Path:
    """A ``--key`` value → a PEM path: a keyId maps to ``<agent_dir>/keys/<id>.pem``."""

    kp = Path(key_ref).expanduser()
    if kp.suffix == ".pem" or "/" in key_ref or kp.exists():
        return kp
    return Path(get_agent_dir()) / "keys" / f"{key_ref}.pem"


def _cmd_trust(rest: list[str], *, input_fn: Callable[[str], str]) -> int:
    """``extension trust add|list|remove|revoke`` — manage the Ed25519 trust store.

    Verification-key trust lives in ``<agent_dir>/trusted_keys.json`` (a sync sidecar,
    NOT ``SettingsManager`` — no async-flush landmine). ``add`` is an explicit trust
    decision, so it is consent-gated (y/N, deny-by-default) like ``source add`` / install.
    """

    if not rest:
        print(
            "Error: trust requires a subcommand (add | list | remove | revoke).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN
    action, args = rest[0], rest[1:]
    path = extension_signing.trusted_keys_path(get_agent_dir())
    store = extension_signing.load_trusted_keys(path)

    if action == "list":
        if not extension_signing.FIRST_PARTY_KEYS and not store.keys and not store.revoked:
            print("No trusted keys. Add one with: aelix extension trust add <keyId> --public-key <b64>")
            return 0
        print("Trusted verification keys:")
        for kid in sorted(extension_signing.FIRST_PARTY_KEYS):
            marker = " (REVOKED)" if kid in store.revoked else ""
            print(f"  {kid}  [first-party]{marker}")
        for kid in sorted(store.keys):
            tk = store.keys[kid]
            label = f" — {tk.label}" if tk.label else ""
            marker = " (REVOKED)" if kid in store.revoked else ""
            print(f"  {kid}{label}{marker}")
        for kid in sorted(store.revoked):
            if kid not in extension_signing.FIRST_PARTY_KEYS and kid not in store.keys:
                print(f"  {kid}  (REVOKED)")
        return 0

    if action == "add":
        return _trust_add(args, store=store, path=path, input_fn=input_fn)

    if action in ("remove", "revoke"):
        key_id = next((a for a in args if not a.startswith("-")), None)
        if key_id is None or not key_id.strip():
            print(f"Error: trust {action} requires a <keyId>.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        if action == "remove":
            if key_id not in store.keys:
                extra = (
                    " (it is a first-party key — use `trust revoke` to disable it)"
                    if key_id in extension_signing.FIRST_PARTY_KEYS
                    else ""
                )
                print(f"Error: no user-added trusted key {key_id!r}{extra}.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            new_keys = {k: v for k, v in store.keys.items() if k != key_id}
            extension_signing.save_trusted_keys(
                extension_signing.TrustStore(keys=new_keys, revoked=store.revoked), path
            )
            print(f"Removed trusted key {key_id}.")
            return 0
        # revoke — append to the local revoked list (wins even over first-party).
        if key_id in store.revoked:
            print(f"Key {key_id} is already revoked.")
            return 0
        extension_signing.save_trusted_keys(
            extension_signing.TrustStore(keys=store.keys, revoked=(*store.revoked, key_id)), path
        )
        print(f"Revoked key {key_id} (it can no longer authenticate installs).")
        return 0

    print(
        f"Error: unknown trust subcommand {action!r} (add | list | remove | revoke).",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


def _trust_add(
    args: list[str],
    *,
    store: extension_signing.TrustStore,
    path: Path,
    input_fn: Callable[[str], str],
) -> int:
    """``trust add <keyId> --public-key <b64> [--label L] [--source S] [--yes]``."""

    key_id: str | None = None
    public_b64: str | None = None
    label: str | None = None
    source: str | None = None
    yes = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-y", "--yes"):
            yes = True
        elif a in ("--public-key", "--label", "--source"):
            i += 1
            if i >= len(args) or not args[i].strip():
                print(f"Error: {a} requires a value.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            if a == "--public-key":
                public_b64 = args[i]
            elif a == "--label":
                label = args[i]
            else:
                source = args[i]
        elif a.startswith(("--public-key=", "--label=", "--source=")):
            flag, value = a.split("=", 1)
            if flag == "--public-key":
                public_b64 = value
            elif flag == "--label":
                label = value
            else:
                source = value
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif key_id is None:
            key_id = a
        else:
            print(f"Error: unexpected argument {a!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    if key_id is None or not key_id.strip():
        print("Error: trust add requires a <keyId>.", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    if public_b64 is None or not public_b64.strip():
        print("Error: trust add requires --public-key <base64>.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    # The supplied keyId MUST match the public key bytes (keyId = sha256(pub)[:16]) —
    # so a typo can never trust a key under the wrong identity.
    try:
        derived = extension_signing.public_key_id(public_b64)
    except ValueError as exc:
        print(f"Error: invalid public key ({exc}).", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    if derived != key_id:
        print(
            f"Error: keyId {key_id!r} does not match the public key (its keyId is {derived!r}).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    print(f"Trust Ed25519 key {key_id}" + (f" ({label})" if label else ""))
    print("  A trusted key authenticates any extension it signs. Only trust keys you vouch for.")
    if not yes:
        try:
            reply = input_fn("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return _EXIT_DIDNT_RUN

    new_keys = dict(store.keys)
    new_keys[key_id] = extension_signing.TrustedKey(
        key_id=key_id, public_key=public_b64.strip(), label=label,
        added_at=extension_pins.now_iso(), source=source,
    )
    extension_signing.save_trusted_keys(
        extension_signing.TrustStore(keys=new_keys, revoked=store.revoked), path
    )
    print(f"Trusted key {key_id}.")
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
    require_signature: bool = False
    trusted_key: str | None = None
    signature_path: str | None = None
    #: ``--trust-extension-path DIST`` (repeatable). NEVER reaches pip — it is
    #: consumed entirely by the post-install verdict (#154), where it means the
    #: same thing it means to ``verify`` and to ``aelix`` itself: vouch for ONE
    #: distribution whose install root sits outside the environment's real site
    #: directories. Measured reachable on the install path (``PIP_TARGET`` /
    #: ``--target`` + a matching ``PYTHONPATH``): without it, install would print
    #: UNTRUSTED_PATH and exit 3 at a pack that is merely unvouched, not broken —
    #: and would keep doing so on every reinstall.
    trust_extension_paths: tuple[str, ...] = ()


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
    require_signature = False
    trusted_key: str | None = None
    signature_path: str | None = None
    trust_paths: list[str] = []
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
        elif a == "--require-signature":
            require_signature = True
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
        elif a == "--trusted-key":
            i += 1
            if i >= len(rest) or not rest[i].strip():
                print("Error: --trusted-key requires a keyId.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            trusted_key = rest[i]
        elif a.startswith("--trusted-key="):
            value = a.split("=", 1)[1]
            if not value.strip():
                print("Error: --trusted-key requires a keyId.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            trusted_key = value
        elif a == "--signature":
            i += 1
            if i >= len(rest) or not rest[i].strip():
                print("Error: --signature requires a path.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            signature_path = rest[i]
        elif a.startswith("--signature="):
            value = a.split("=", 1)[1]
            if not value.strip():
                print("Error: --signature requires a path.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            signature_path = value
        elif a == "--trust-extension-path":
            i += 1
            if i >= len(rest) or not rest[i].strip():
                print(
                    "Error: --trust-extension-path requires a DIST name.",
                    file=sys.stderr,
                )
                return _EXIT_DIDNT_RUN
            trust_paths.append(rest[i])
        elif a.startswith("--trust-extension-path="):
            value = a.split("=", 1)[1]
            if not value.strip():
                print(
                    "Error: --trust-extension-path requires a DIST name.",
                    file=sys.stderr,
                )
                return _EXIT_DIDNT_RUN
            trust_paths.append(value)
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
        require_signature=require_signature,
        trusted_key=trusted_key,
        signature_path=signature_path,
        trust_extension_paths=tuple(trust_paths),
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

    # ``list`` + the #67 provenance verbs need no settings/source-list — answer them
    # before any settings construction (keygen/sign/trust use the trust-store sidecar
    # and the key dir under agent_dir, not the SettingsManager-backed source list).
    if sub == "list":
        return _cmd_list()
    if sub == "verify":
        return _cmd_verify(rest)
    # #68 — reads a directory and writes a document; no source list involved.
    if sub == "index":
        return _cmd_index(rest)
    if sub == "keygen":
        return _cmd_keygen(rest)
    if sub == "sign":
        return _cmd_sign(rest)
    if sub == "trust":
        return _cmd_trust(rest, input_fn=input_fn)

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
    if sub in ("discover", "search"):
        return await _cmd_discover(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )
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
        "(install | source | list | verify | index | discover | update | remove | "
        "keygen | sign | trust).\n"
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
