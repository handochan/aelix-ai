"""Notice a newer release at launch, and name the command that installs it.

A user who installs ``v0.1.0-beta.1`` has no way to learn that ``beta.2`` exists.
Adding the notice later does not reach them — it reaches whoever installs after
it ships. So the notice is the part that has to be in the first tag; performing
the update can follow, because by then everyone will hear about it.

WHY THE COMMAND IS DETECTED AND NOT A CONSTANT. There is no upgrade command that
works for every install, and the wrong one is worse than none. Measured on real
sandboxes built from local wheels:

* ``uv tool upgrade aelix`` cannot upgrade an ``install.sh`` install. Two
  independent reasons: ``install.sh`` builds in a ``mktemp -d`` that its own
  ``trap`` deletes, while uv persists that path into ``uv-receipt.toml`` and
  replays it (``Failed to read --find-links directory``); and the ``==$version``
  pin the checksum gate depends on makes uv answer ``Nothing to upgrade``.
* uv's own suggested remedy, ``uv tool install aelix@latest``, resolves from
  PyPI — where ``aelix`` is today a reservation placeholder whose summary reads
  "Placeholder, no code yet". Measured: it installs the placeholder, finds no
  entry points, prints "No executables ... removing tool", and leaves
  ``uv tool list`` empty. Following that hint DELETES the user's aelix.
* ``pip install -U aelix`` reaches the same placeholder.

So this module detects how the process was installed and prints the one command
that is right for it — and stays silent for a git checkout, where any advice
would be noise to a contributor.

THE FEED IS FIRST-PARTY ON PURPOSE. The obvious source, GitHub's releases API,
carries three measured traps: ``/releases/latest`` EXCLUDES prereleases and
404s for a repository whose only releases are betas (which aelix is, for the
whole beta); the list endpoint is ordered by publish time and not by version
(21 inversions over one project's 100 releases), so ``[0]`` is not the newest;
and the anonymous rate limit is 60/hour PER IP, with a conditional 304 still
costing one — so behind one corporate NAT the 61st launch in an hour gets a 403
and the person who sees it did nothing wrong. A static JSON file published to
the project's own Pages site has none of those properties, costs nothing, and
is what pi does for the same reason.

NEVER RAISES, NEVER BLOCKS, NEVER NAGS. Every failure — offline, DNS, timeout,
404, malformed JSON, an unparseable version, a clock that went backwards — is
"no information", not a problem to show the user. A startup complaint about a
failed *check* is worse than no check.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from aelix_coding_agent.cli.config import get_agent_dir

#: The published feed. A plain static file on the project's own Pages site,
#: written by the release ritual and gated by ``tests/test_latest_version_feed.py``
#: so it cannot silently drift from the version that actually shipped.
FEED_URL = "https://handochan.github.io/aelix-ai/latest-version.json"

#: Bytes we are willing to read from the feed. The real file is a few hundred
#: bytes; the cap is the same posture ``extension_catalog`` takes toward a
#: remote document, so a hostile or broken host cannot stream forever.
MAX_FEED_BYTES = 64 * 1024

#: Seconds before the network is consulted again. Nothing about the feed is
#: urgent, and a day's cadence is what keeps a shared NAT from ever mattering.
CHECK_INTERVAL_S = 24 * 60 * 60

#: How long the launch path will wait for the check before giving up on it for
#: this run. The banner must not be held for a version number.
FETCH_TIMEOUT_S = 4.0

_CACHE_FILENAME = "update_check.json"
_CACHE_VERSION = 1

#: ``cli/config.py`` returns this when the distribution metadata is missing —
#: i.e. someone is running from a source tree. Never notify them.
_DEV_VERSION = "0.0.0-dev"


class InstallMethod(StrEnum):
    """How this process's aelix got onto the machine.

    ``UNKNOWN`` is a first-class answer, not a failure: an install shape nobody
    anticipated gets a release link and no command, which is honest, rather than
    a guess that might uninstall them.
    """

    INSTALL_SH = "install.sh"
    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP = "pip"
    CHECKOUT = "checkout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Release:
    """One entry from the feed."""

    version: str
    tag: str
    prerelease: bool
    url: str


@dataclass(frozen=True)
class UpdateNotice:
    """What the user should be told, once we have decided to tell them."""

    current: str
    release: Release
    method: InstallMethod
    command: str | None


# === version comparison ====================================================


def _parse(raw: str) -> Any:
    """PEP 440 ``Version`` for ``raw``, or ``None`` when it is not one.

    ``packaging`` rather than a string compare, because a string compare is
    wrong in four of the eight orderings that matter here and one of them is
    fatal: the tag ``v0.1.0-beta.1`` and the installed version ``0.1.0b1`` are
    the SAME release spelled differently, so a ``!=`` check announces an update
    to a user who already has it, on every launch, forever. That is pi's own
    fallback bug (``version-check.js`` compares trimmed strings when semver
    cannot parse); it does not bite pi because its tag spelling matches its
    package.json, and it would bite us because ours does not.

    Moving tags (``nightly``, ``tip``, ``stable``) do not parse and must be
    SKIPPED rather than treated as newer.
    """

    try:
        from packaging.version import InvalidVersion, Version
    except Exception:  # noqa: BLE001 — a missing comparator means "no opinion"
        return None
    try:
        return Version(raw.lstrip("vV"))
    except InvalidVersion:
        return None


def choose_release(current: str, feed: dict[str, Any]) -> Release | None:
    """The release to offer, or ``None`` to say nothing.

    THE RULE, and why it is asymmetric:

    * On a prerelease, every newer release is eligible — prereleases AND
      stables. Someone who installed a beta chose *newer than stable*, not
      *prerelease forever*, and withholding ``0.1.0`` from a ``0.1.0b2`` user
      strands every beta tester on a dead branch the day 1.0 ships. That is the
      same failure this feature exists to prevent, one release later.
    * On a stable, only newer STABLES are eligible. That user opted out of the
      train; offering them ``0.2.0b1`` would nag every stable user the moment
      any beta is cut. ``AELIX_VERSION`` is how they opt back in.
    """

    cur = _parse(current)
    if cur is None:
        return None

    candidates: list[Release] = []
    for key in ("latest", "latestStable"):
        entry = feed.get(key)
        if not isinstance(entry, dict):
            continue
        version = entry.get("version")
        tag = entry.get("tag")
        url = entry.get("url")
        if not (
            isinstance(version, str) and isinstance(tag, str) and isinstance(url, str)
        ):
            continue
        parsed = _parse(version)
        if parsed is None or not (parsed > cur):
            continue
        candidates.append(
            Release(
                version=version,
                tag=tag,
                prerelease=bool(entry.get("prerelease", False)),
                url=url,
            )
        )

    if not cur.is_prerelease:
        candidates = [c for c in candidates if not c.prerelease]
    if not candidates:
        return None
    # ``max`` on the parsed version, never on the declaration order: the feed is
    # data and its ordering is not a promise we should depend on.
    return max(candidates, key=lambda c: _parse(c.version))


# === install-method detection ==============================================


def _dist_files() -> tuple[Path | None, Path | None]:
    """``(installer_path, direct_url_path)`` for the running distribution."""

    try:
        from importlib.metadata import distribution

        dist = distribution("aelix-coding-agent")
        base = getattr(dist, "_path", None)
        if base is None:
            return (None, None)
        base = Path(base)
        return (base / "INSTALLER", base / "direct_url.json")
    except Exception:  # noqa: BLE001 — introspection must never break a launch
        return (None, None)


def _is_editable(direct_url: Path | None) -> bool:
    """True for a ``pip install -e`` / ``uv sync`` checkout.

    Reads ``dir_info`` defensively: a non-editable local-directory install
    writes ``"dir_info": {}`` with no ``editable`` key at all, so indexing it
    raises ``KeyError`` — measured, and the reason this is not a one-liner.
    """

    if direct_url is None or not direct_url.is_file():
        return False
    try:
        data = json.loads(direct_url.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    info = data.get("dir_info")
    return isinstance(info, dict) and bool(info.get("editable", False))


def detect_install_method() -> InstallMethod:
    """How this process was installed. Never raises.

    The evidence, in the order it is decisive:

    ``direct_url.json`` with ``dir_info.editable``  -> a checkout
    ``sys.prefix/pipx_metadata.json``               -> pipx
    ``sys.prefix/uv-receipt.toml``                  -> a uv tool install, and its
        contents separate ``install.sh`` (which pins ``==`` and passes
        ``--find-links``) from a plain ``uv tool install aelix``
    ``INSTALLER`` == ``pip``                        -> pip
    """

    installer, direct_url = _dist_files()
    if _is_editable(direct_url):
        return InstallMethod.CHECKOUT

    prefix = Path(sys.prefix)
    try:
        if (prefix / "pipx_metadata.json").is_file():
            return InstallMethod.PIPX
        receipt = prefix / "uv-receipt.toml"
        if receipt.is_file():
            text = receipt.read_text(encoding="utf-8", errors="replace")
            # A local-directory tool install records the directory it came from;
            # that is a checkout by another name and must not be "upgraded".
            if "directory" in text:
                return InstallMethod.CHECKOUT
            # install.sh's signature: it pins an exact version so the SHA256SUMS
            # gate is load-bearing, and it installs from a --find-links dir.
            if "find-links" in text or "==" in text:
                return InstallMethod.INSTALL_SH
            return InstallMethod.UV_TOOL
    except Exception:  # noqa: BLE001
        return InstallMethod.UNKNOWN

    try:
        if (
            installer is not None
            and installer.is_file()
            and installer.read_text(encoding="utf-8").strip() == "pip"
        ):
            return InstallMethod.PIP
    except Exception:  # noqa: BLE001
        return InstallMethod.UNKNOWN
    return InstallMethod.UNKNOWN


#: Where ``install.sh`` lives, for the one method whose upgrade is "run the
#: installer again". Kept beside the command that uses it so the two cannot
#: drift apart.
_INSTALL_SH_URL = (
    "https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh"
)


def upgrade_command(method: InstallMethod, tag: str) -> str | None:
    """The command that upgrades THIS install, or ``None`` to advise nothing.

    ``None`` for a checkout (a contributor upgrades with ``git pull``, and being
    told otherwise is noise) and for ``UNKNOWN`` (a guess here can uninstall
    someone — see the module docstring). Both still get the release link.
    """

    if method is InstallMethod.INSTALL_SH:
        return f"AELIX_VERSION={tag} curl -fsSL {_INSTALL_SH_URL} | sh"
    if method is InstallMethod.UV_TOOL:
        return "uv tool upgrade aelix"
    if method is InstallMethod.PIPX:
        return "pipx upgrade aelix"
    if method is InstallMethod.PIP:
        return "pip install -U aelix"
    return None


# === the transport =========================================================


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that leaves HTTPS.

    Same guard ``cli/extension_catalog`` already applies to the catalog fetch.
    Worth stating why it is not optional here: ``install.sh``'s own downloader
    is ``curl -fSL`` with no ``--proto-redir``, and a local probe confirmed it
    follows an HTTPS->HTTP redirect and returns the plaintext body. The Python
    side of this repo refuses that on the same server; the update path is new
    code and has no reason to inherit the weaker policy.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def, override]
        if not newurl.lower().startswith("https://"):
            raise ValueError(f"update check refused an insecure redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_offline(explicit: bool = False) -> bool:
    """The CLI's ``--offline`` contract, read the way extensions read it.

    Deliberately reuses ``PI_OFFLINE`` / ``AELIX_OFFLINE`` rather than adding a
    name. ``cli/entry.py`` records the reason: every ``os.environ`` read is a
    new consumer a hostile cwd ``.env`` may try to drive, so the safest new
    control-plane name is none at all. The user-facing off switch is a settings
    key, not an environment variable.
    """

    if explicit:
        return True
    for name in ("PI_OFFLINE", "AELIX_OFFLINE"):
        value = os.environ.get(name, "").strip().lower()
        if value and value not in {"0", "false", "no", "off"}:
            return True
    return False


def default_fetch(url: str = FEED_URL, timeout: float = FETCH_TIMEOUT_S) -> dict[str, Any]:
    """Read the feed over HTTPS, bounded. Raises on anything unusual.

    In-process ``urllib`` inherits whatever trust store the CLI installed, so
    this lands in the same TLS posture ``aelix status`` reports rather than a
    second, invisible one.
    """

    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    req = urllib.request.Request(  # noqa: S310 — https asserted below and in the handler
        url, headers={"Accept": "application/json", "User-Agent": "aelix-update-check"}
    )
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310
        final = str(getattr(resp, "url", None) or url)
        if not final.lower().startswith("https://"):
            raise ValueError(f"update check ended on a non-HTTPS URL: {final!r}")
        raw = resp.read(MAX_FEED_BYTES + 1)
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("update feed is larger than the cap")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("update feed is not an object")
    return data


# === the cache =============================================================


def _cache_path(agent_dir: str | os.PathLike[str] | None = None) -> Path:
    base = Path(agent_dir) if agent_dir is not None else Path(get_agent_dir())
    return base / _CACHE_FILENAME


def read_cache(agent_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The persisted check state. A missing or corrupt file reads as empty.

    Same posture as ``tui/statusline_store``: a store that cannot be read is a
    store with nothing in it, never an exception on the launch path.
    """

    try:
        data = json.loads(_cache_path(agent_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(
    data: dict[str, Any], agent_dir: str | os.PathLike[str] | None = None
) -> None:
    """Persist atomically. Never raises."""

    path = _cache_path(agent_dir)
    payload = {"version": _CACHE_VERSION, **data}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        return


def is_due(cache: dict[str, Any], now: float) -> bool:
    """True when the network may be consulted again.

    A clock that moved backwards (a VM resuming from a snapshot, a corrected
    NTP step) would otherwise pin ``last_checked_at`` in the future and disable
    the check forever, so a future timestamp is treated as due rather than
    trusted.
    """

    last = cache.get("last_checked_at")
    if not isinstance(last, (int, float)):
        return True
    if last > now:
        return True
    return (now - last) >= CHECK_INTERVAL_S


# === the check =============================================================


def check_for_update(
    *,
    current_version: str,
    fetch: Any,
    now: float,
    agent_dir: str | os.PathLike[str] | None = None,
) -> UpdateNotice | None:
    """Decide what to say, if anything. Never raises.

    ``fetch`` is injected so the network is a test seam and so the caller owns
    the transport (and therefore the TLS posture ``aelix status`` reports). It
    is called with no arguments and must return the decoded feed ``dict``.

    The cache timestamp is written whether or not an update was found, so a
    failed check does not retry on every launch — and the RESULT is cached too,
    so the notice survives the interval without touching the network again.
    """

    if current_version == _DEV_VERSION:
        return None

    cache = read_cache(agent_dir)
    feed: dict[str, Any] | None = None

    if is_due(cache, now):
        try:
            fetched = fetch()
        except Exception:  # noqa: BLE001 — offline is not an error to report
            fetched = None
        if isinstance(fetched, dict):
            feed = fetched
        write_cache(
            {"last_checked_at": now, "feed": feed if feed is not None else cache.get("feed")},
            agent_dir,
        )
    if feed is None:
        cached = cache.get("feed")
        feed = cached if isinstance(cached, dict) else None
    if feed is None:
        return None

    release = choose_release(current_version, feed)
    if release is None:
        return None

    method = detect_install_method()
    return UpdateNotice(
        current=current_version,
        release=release,
        method=method,
        command=upgrade_command(method, release.tag),
    )


__all__ = [
    "CHECK_INTERVAL_S",
    "default_fetch",
    "is_offline",
    "FEED_URL",
    "FETCH_TIMEOUT_S",
    "InstallMethod",
    "MAX_FEED_BYTES",
    "Release",
    "UpdateNotice",
    "check_for_update",
    "choose_release",
    "detect_install_method",
    "is_due",
    "read_cache",
    "upgrade_command",
    "write_cache",
]
