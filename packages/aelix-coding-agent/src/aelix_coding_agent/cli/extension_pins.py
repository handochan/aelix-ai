"""Issue #64 (ADR-0187) — the extension integrity **pin store** + hashing.

This module is the *pure* half of the #64 pre-pip integrity gate: it owns the
on-disk pin sidecar, SHA-256 hashing, downloaded-artifact discovery, and the
tofi/strict decision primitives. It imports NOTHING from
:mod:`aelix_coding_agent.cli.extension_install` (which orchestrates pip and calls
into here) so the two stay a clean pure/effectful split and this half is unit
testable with no pip at all.

Design (ADR-0187, owner-confirmed 2026-07-05):

* Trust material is a plain-JSON **sidecar** at ``<agent_dir>/extension_pins.json``
  — deliberately NOT ``SettingsManager`` (avoids the #32-A async-flush landmine and
  the pi-shaped-schema churn, and covers one-off path/git installs that never
  became a registered source). Writes are SYNC + atomic (``os.replace``).
* Each entry is keyed by a canonical **pin identity** (path→absolute, git→repo URL
  minus the ``@<sha>``, pypi→bare name) so a version bump / ref move maps onto the
  SAME entry and is detected as a re-pin event rather than a new blind trust.
* The scheme detects "same bytes as recorded", never "safe to run": pip runs the
  pack's build/setup code AFTER any verify passes, so the source-level ``y/N``
  consent prompt REMAINS the sole execution-trust boundary.

The ``keyId`` / ``sig`` fields on :class:`Pin` are an inert **forward-compat seam**
for Approach B (Ed25519 provenance, deferred to a later ADR): they let a signature
layer be added without moving the gate or changing the on-disk schema. Nothing in
v1 writes or reads them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "PINS_FILENAME",
    "SCHEMA_VERSION",
    "Pin",
    "VerifyRefusal",
    "decide_generic",
    "decide_pypi",
    "find_top_level_artifact",
    "load_pins",
    "pins_file_path",
    "save_pins",
    "sha256_file",
]

PINS_FILENAME = "extension_pins.json"
#: Bumped only on an incompatible on-disk change. An unknown-higher version is
#: read leniently (best-effort) rather than discarded — a downgrade must never
#: silently wipe an admin-provisioned pins file.
SCHEMA_VERSION = 1


class VerifyRefusal(Exception):
    """Raised to BLOCK an install: the caller turns this into exit-code 2.

    A refusal means "pip must not run" — the same class as a declined consent
    prompt, and distinct from pip's own failure returncode (ADR-0185 3-way exit).
    """


@dataclass(frozen=True)
class Pin:
    """One recorded integrity pin for a source identity.

    ``sha256`` anchors path/pypi artifacts; ``git_sha`` anchors git sources (a
    pinned 40-hex commit — tree immutability, not build bytes). ``version`` is the
    pypi version the ``sha256`` belongs to (so a version bump is a re-pin, not a
    same-version tamper). ``key_id`` / ``sig`` / ``sha256_statement`` are the
    Approach-B provenance fields (#67/ADR-0189): when an install is authenticated by a
    trusted Ed25519 signature the recorded pin carries the signer keyId, the base64
    detached signature, and the literal canonical statement that was signed (a durable
    post-install audit record, since the ``.aelixsig`` sits next to a temp/downloaded
    artifact that is cleaned up). They stay :data:`None` for an unsigned (integrity-only)
    pin — the #64 default path is unchanged.
    """

    identity: str
    kind: str  # "path" | "git" | "pypi"
    mode: str  # "tofi" | "strict" — the mode this pin was recorded under
    name: str | None = None
    version: str | None = None
    sha256: str | None = None
    git_sha: str | None = None
    pinned_at: str | None = None
    key_id: str | None = None  # Approach-B provenance (#67) — signer keyId when signed
    sig: str | None = None  # Approach-B provenance (#67) — base64 detached signature
    sha256_statement: str | None = None  # Approach-B (#67) — the canonical signed statement
    #: Unknown keys from a newer schema, preserved verbatim on rewrite so a
    #: downgrade round-trips instead of dropping fields it does not understand.
    extra: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind, "mode": self.mode}
        if self.name is not None:
            out["name"] = self.name
        if self.version is not None:
            out["version"] = self.version
        if self.sha256 is not None:
            out["sha256"] = self.sha256
        if self.git_sha is not None:
            out["gitSha"] = self.git_sha
        if self.pinned_at is not None:
            out["pinnedAt"] = self.pinned_at
        if self.key_id is not None:
            out["keyId"] = self.key_id
        if self.sig is not None:
            out["sig"] = self.sig
        if self.sha256_statement is not None:
            out["sha256Statement"] = self.sha256_statement
        out.update(self.extra)
        return out

    @classmethod
    def from_json(cls, identity: str, raw: dict[str, object]) -> Pin:
        known = {
            "kind",
            "mode",
            "name",
            "version",
            "sha256",
            "gitSha",
            "pinnedAt",
            "keyId",
            "sig",
            "sha256Statement",
        }
        extra = {k: v for k, v in raw.items() if k not in known}

        def _s(key: str) -> str | None:
            v = raw.get(key)
            return v if isinstance(v, str) else None

        # A commit SHA is hex-case-insensitive; normalize to lower so an
        # uppercase admin-provisioned gitSha still equals the (lowercased)
        # observed SHA in decide_generic.
        git_sha = _s("gitSha")
        return cls(
            identity=identity,
            kind=_s("kind") or "",
            mode=_s("mode") or "tofi",
            name=_s("name"),
            version=_s("version"),
            sha256=_s("sha256"),
            git_sha=git_sha.lower() if git_sha else None,
            pinned_at=_s("pinnedAt"),
            key_id=_s("keyId"),
            sig=_s("sig"),
            sha256_statement=_s("sha256Statement"),
            extra=extra,
        )


def now_iso() -> str:
    """UTC ISO-8601 timestamp for ``pinnedAt`` (seconds precision)."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def pins_file_path(agent_dir: str | os.PathLike[str]) -> Path:
    """The pin sidecar path under ``agent_dir`` (``<agent_dir>/extension_pins.json``).

    ``agent_dir`` is resolved by the caller via ``config.get_agent_dir()`` (which
    honors ``AELIX_CODING_AGENT_DIR``), so tests that set that env var isolate the
    pin file automatically — the same isolation lever as the rest of the CLI.
    """

    return Path(agent_dir) / PINS_FILENAME


def load_pins(path: Path) -> dict[str, Pin]:
    """Load ``path`` → ``{identity: Pin}``; ``{}`` on a missing/unreadable/bad file.

    A malformed or unreadable pins file degrades to an empty map rather than
    raising — a corrupt sidecar must never brick installs (in ``tofi`` it simply
    re-establishes pins; ``strict`` will then refuse for want of a pin, which is
    the safe direction).
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError covers json.JSONDecodeError AND a UnicodeDecodeError from a
        # non-UTF-8 sidecar — both must degrade to {}, never brick an install.
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("pins")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, Pin] = {}
    for identity, spec in entries.items():
        if isinstance(identity, str) and isinstance(spec, dict):
            out[identity] = Pin.from_json(identity, spec)
    return out


def save_pins(pins: dict[str, Pin], path: Path) -> None:
    """Atomically write ``pins`` to ``path`` (create parent dir; ``os.replace``).

    A same-dir temp file + ``os.replace`` gives an atomic swap so a crash mid-write
    never truncates an existing (possibly admin-provisioned) pins file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "pins": {identity: pin.to_json() for identity, pin in sorted(pins.items())},
    }
    body = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".extension_pins.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file behind on any failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 hex digest of a file (64 KiB chunks; constant memory)."""

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".tar.bz2", ".zip")


def canonicalize_name(name: str) -> str:
    """PEP 503 project-name canonicalization (fold runs of ``-_.`` to ``-``, lower)."""

    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _parse_artifact(filename: str) -> tuple[str, str] | None:
    """Parse a wheel/sdist filename → ``(canonical_name, version)`` or None.

    Wheel (PEP 427) ``{name}-{version}(-{build})?-{py}-{abi}-{plat}.whl`` — the
    name/version tokens never contain ``-`` (they are ``_``-escaped), so a plain
    ``split('-')`` is exact. Sdist (PEP 625) ``{name}-{version}.tar.gz`` — a PEP
    440 version carries no ``-``, so an ``rsplit('-', 1)`` is exact. Anything that
    does not parse cleanly returns :data:`None` — never a loose guess.
    """

    stripped: str | None = None
    low = filename.lower()
    is_wheel = low.endswith(".whl")
    for suffix in _ARTIFACT_SUFFIXES:
        if low.endswith(suffix):
            stripped = filename[: -len(suffix)]
            break
    if not stripped:
        return None
    if is_wheel:
        parts = stripped.split("-")
        if len(parts) < 5:  # name, version, py, abi, plat (build tag optional)
            return None
        dist, version = parts[0], parts[1]
    else:
        if "-" not in stripped:
            return None
        dist, version = stripped.rsplit("-", 1)
    if not dist or not version:
        return None
    return canonicalize_name(dist), version


def find_top_level_artifact(dest: Path, canonical_name: str) -> Path | None:
    """Return the single top-level wheel (preferred) or sdist EXACTLY matching name.

    ``pip download`` fetches the whole closure into ``dest``; the pin covers ONLY
    the top-level artifact (transitive deps stay unverified — the documented v1
    gap). Matching is by EXACT canonical name — never a prefix — so a
    prefix-colliding dependency (``jupyter_core`` for target ``jupyter``) is never
    mistaken for the target, and a decoy filename that merely sorts first cannot
    win. ``canonical_name`` must already be :func:`canonicalize_name`-normalized.
    Ambiguity (more than one exact-name artifact of the chosen tier — e.g. two
    platform wheels) returns :data:`None` rather than guessing which pip installs;
    the caller then degrades (tofi) or refuses (strict).
    """

    if not dest.is_dir():
        return None
    wheels: list[Path] = []
    sdists: list[Path] = []
    for p in sorted(dest.iterdir()):
        if not p.is_file():
            continue
        parsed = _parse_artifact(p.name)
        if parsed is None or parsed[0] != canonical_name:
            continue
        if p.name.lower().endswith(".whl"):
            wheels.append(p)
        else:
            sdists.append(p)
    chosen = wheels or sdists
    return chosen[0] if len(chosen) == 1 else None


def version_from_artifact(filename: str, canonical_name: str) -> str | None:
    """The version of an EXACT-name-matching wheel/sdist, else :data:`None`."""

    parsed = _parse_artifact(filename)
    if parsed is None or parsed[0] != canonical_name:
        return None
    return parsed[1]


# =====================================================================
# === tofi / strict decision primitives (pure) =========================
# =====================================================================


@dataclass(frozen=True)
class Decision:
    """Outcome of a verify comparison: whether to (re)record + a user notice."""

    record: bool
    notice: str


def _first_acquisition(
    label: str, mode: str, repin: bool, authenticated: bool = False
) -> Decision:
    """Shared first-acquisition outcome: record under tofi; refuse under strict.

    A valid trusted Ed25519 signature (``authenticated``, #67/ADR-0189) VOUCHES for the
    source, so it satisfies strict mode's "must be pre-provisioned" requirement — the
    signer's trusted key is the pre-provisioning, in place of a pinned digest. This
    closes the first-install-TOFU gap for a signed source WITHOUT a blind first trust.
    """

    if authenticated:
        return Decision(
            True, f"authenticated first acquisition of {label} by trusted signature — recording pin"
        )
    if mode == "strict" and not repin:
        raise VerifyRefusal(
            f"strict mode: no pre-provisioned pin for {label} "
            "(provision extension_pins.json out-of-band, or use --repin / tofi)"
        )
    return Decision(True, f"unverified first acquisition of {label} — recording pin (TOFI)")


def _mismatch(existing_sha: str | None, observed: str, label: str, repin: bool, ctx: str = "") -> Decision:
    """A digest differs from the recorded pin: re-pin with ``--repin`` else refuse."""

    if repin:
        return Decision(True, f"pin changed for {label} — re-pinning (--repin)")
    raise VerifyRefusal(
        f"integrity MISMATCH for {label}{ctx}: recorded {_short(existing_sha)} but "
        f"got {_short(observed)} — use --repin to accept the change"
    )


def decide_generic(
    existing: Pin | None,
    observed: str,
    *,
    mode: str,
    repin: bool,
    label: str,
    field_name: str = "sha256",
    authenticated: bool = False,
) -> Decision:
    """tofi/strict decision for a single-digest kind (path artifact / git SHA).

    * no existing pin → record (tofi first-acquisition), UNLESS ``strict`` without
      ``--repin`` (which refuses a source that has no pre-provisioned pin) — an
      ``authenticated`` trusted signature (#67) overrides that refusal (it vouches).
    * existing matches → no re-record (verified).
    * existing differs → REFUSE, unless ``--repin`` accepts the change (an
      authenticated signature does NOT bypass a same-identity byte change — that is
      exactly the drift/tamper signal, so ``--repin`` is still required).
    """

    if existing is None:
        return _first_acquisition(label, mode, repin, authenticated)
    prev = getattr(existing, field_name)
    if prev == observed:
        return Decision(False, f"integrity verified for {label} against recorded pin")
    return _mismatch(prev, observed, label, repin)


def decide_pypi(
    existing: Pin | None,
    observed_sha: str,
    observed_version: str | None,
    *,
    mode: str,
    repin: bool,
    label: str,
    authenticated: bool = False,
) -> Decision:
    """tofi/strict decision for pypi (version-aware).

    Same-version different-bytes is the tamper signal → REFUSE. A version change
    is a legitimate re-pin under tofi (record the new version), but ``strict``
    still requires ``--repin`` to move — UNLESS the new version is ``authenticated``
    by a trusted signature (#67), which vouches for the upgrade without ``--repin``.
    When the version cannot be determined (unparseable filename), fall back to a
    straight byte comparison — a mismatch is refused, never silently re-pinned
    (parity with :func:`decide_generic`).
    """

    if existing is None:
        return _first_acquisition(label, mode, repin, authenticated)
    version_known = existing.version is not None and observed_version is not None
    if not version_known:
        if existing.sha256 == observed_sha:
            return Decision(False, f"integrity verified for {label} against recorded pin")
        return _mismatch(existing.sha256, observed_sha, label, repin, ctx=" (version undetermined)")
    if existing.version == observed_version:
        if existing.sha256 == observed_sha:
            return Decision(False, f"integrity verified for {label} against recorded pin")
        return _mismatch(
            existing.sha256, observed_sha, label, repin, ctx=f" at the SAME version {observed_version}"
        )
    # Version changed — a legitimate upgrade under tofi, or a signature-vouched
    # upgrade under strict (#67: a trusted signature replaces the --repin gesture).
    if mode == "strict" and not repin and not authenticated:
        raise VerifyRefusal(
            f"strict mode: version change for {label} "
            f"({existing.version}→{observed_version}) needs --repin"
        )
    return Decision(
        True,
        f"version change for {label} ({existing.version}→{observed_version}) — re-pinning",
    )


def _short(digest: str | None) -> str:
    return f"{digest[:12]}…" if digest else "<none>"
