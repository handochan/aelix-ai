"""Issue #67 (ADR-0189) — Ed25519 detached-signature **provenance** for extension packs.

Approach B of ADR-0187's phased hybrid: layers real provenance (a signature from a
stable, *trusted* Ed25519 key) on top of #64's SHA-256 hash-pin + TOFI integrity gate
(:mod:`aelix_coding_agent.cli.extension_pins`), WITHOUT moving the gate or changing its
3-way exit contract. This module is the *pure* half — it owns keygen, signing,
verification, keyId derivation, the canonical signing statement, the ``.aelixsig``
sidecar, the shipped :data:`FIRST_PARTY_KEYS` constant, and the sync
``<agent_dir>/trusted_keys.json`` trust store. It imports only from
:mod:`~aelix_coding_agent.cli.extension_pins` (for :class:`~.extension_pins.VerifyRefusal`,
``now_iso``, and the atomic-write idiom) and NOTHING from
:mod:`~aelix_coding_agent.cli.extension_install` (which orchestrates pip + consent and
calls into here) — the same clean pure/effectful split as ``extension_pins`` itself.

Design (ADR-0189, owner-confirmed 2026-07-05):

* The signature covers a canonical-JSON **statement** binding the artifact digest to
  the canonical pin identity, name/version, kind, and signer keyId — so a signature is
  inseparable from *which bytes*, *which package*, *which version*, and *who signed*.
  :func:`gate_signature` verification is TWO-STEP and BOTH must hold: (1) the Ed25519
  signature verifies against the trusted key's public bytes, AND (2) every statement
  field equals the value the gate INDEPENDENTLY observed. Step (2) is what stops a
  cryptographically-valid signature over an *attacker-chosen* statement.
* Trust is a merged set: the in-tree :data:`FIRST_PARTY_KEYS` constant UNION the user's
  ``trusted_keys.json`` MINUS a local ``revoked`` list (revocation wins, even over
  first-party — air-gap-native, no online CRL/OCSP). A public key carried *inside* the
  artifact or a catalog is NEVER a trust source.
* Consent (the source-level ``y/N``) REMAINS the sole execution-trust boundary: a valid
  signature proves provenance/integrity, never that the code is safe to run.

Fail-closed posture (owner-confirmed): a signature that is PRESENT but INVALID against a
*trusted* key — a bad signature, or a statement that disagrees with the observed bytes —
is affirmative tampering evidence and refuses ALWAYS (even without ``--require-signature``).
An ABSENT signature degrades to the #64 TOFI path so no air-gap install is bricked;
``--require-signature`` opts into refusing anything lacking a valid trusted signature.

``cryptography`` (Ed25519) is imported lazily with an actionable message so a stripped
environment degrades to a clear error rather than an import crash. The private-signing
half (:func:`keygen`, :func:`sign_artifact`) is publisher tooling; verification never
needs a private key.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import extension_pins

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = [
    "AELIXSIG_SUFFIX",
    "AELIXSIG_VERSION",
    "FIRST_PARTY_KEYS",
    "SCHEMA_VERSION",
    "STATEMENT_VERSION",
    "TRUSTED_KEYS_FILENAME",
    "CryptoUnavailable",
    "SignatureOutcome",
    "TrustStore",
    "TrustedKey",
    "aelixsig_path_for",
    "build_statement",
    "canonical_bytes",
    "gate_signature",
    "key_id_for",
    "keygen",
    "load_private_key",
    "load_trusted_keys",
    "public_key_id",
    "read_aelixsig",
    "resolve_public_key",
    "save_trusted_keys",
    "sign_artifact",
    "sign_document",
    "trusted_keys_path",
    "verify_signed_document",
    "write_aelixsig",
]

TRUSTED_KEYS_FILENAME = "trusted_keys.json"
#: Bumped only on an incompatible on-disk change to ``trusted_keys.json``. An
#: unknown-higher version is read leniently (never discarded) so a downgrade cannot
#: silently wipe an admin-provisioned trust store.
SCHEMA_VERSION = 1
#: The ``statement.v`` a signer stamps; bump to evolve the signed-field set.
STATEMENT_VERSION = 1
#: The ``aelixsig`` envelope version in a ``.aelixsig`` sidecar.
AELIXSIG_VERSION = 1
AELIXSIG_SUFFIX = ".aelixsig"

#: In-tree, trusted-by-release Ed25519 public keys (``{keyId: base64 raw-32}``).
#:
#: This is the trust anchor for first-party signed packs — its keyIds verify out of the
#: box, with no ``trust add``. It ships automatically in the wheel (it is source, not a
#: bundled data file). It is DELIBERATELY EMPTY in v1: a real first-party key is
#: provisioned OUT-OF-BAND by a maintainer (``aelix extension keygen`` → commit the
#: printed public key here; the private key stays in the maintainer's custody, never in
#: the repo). Shipping the *mechanism* empty avoids anchoring first-party trust to a key
#: generated in an ephemeral build environment. A ``revoked`` entry in the user trust
#: store overrides a first-party key here (revocation always wins).
FIRST_PARTY_KEYS: dict[str, str] = {}


class CryptoUnavailable(RuntimeError):
    """``cryptography`` (Ed25519) could not be imported — publisher/verify tooling only.

    Raised by :func:`keygen` / :func:`sign_artifact` when the dependency is absent.
    :func:`gate_signature` maps a verify-time crypto failure to a
    :class:`~.extension_pins.VerifyRefusal` (fail-closed) instead, so a missing dep can
    never silently downgrade a present signature.
    """


def _crypto():
    """Lazily import the Ed25519 primitives, or raise :class:`CryptoUnavailable`.

    ``cryptography`` is a declared dependency (ADR-0189) but the import is kept lazy +
    guarded so the whole ``aelix extension`` CLI never hard-fails at import time on an
    environment where it is somehow absent — only the sign/verify code paths do, with an
    actionable message (mirrors ``extension_install._pip_available``).
    """

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - exercised only on a stripped env
        raise CryptoUnavailable(
            "Ed25519 signing/verification requires the 'cryptography' package. "
            "Install it (e.g. `pip install cryptography`) and retry."
        ) from exc
    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey


# =====================================================================
# === base64 / keyId / canonical statement (pure) ======================
# =====================================================================


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    """Strict base64 decode (``validate=True`` rejects junk rather than silently
    dropping it) — a malformed key/sig must not decode to attacker-friendly bytes."""

    return base64.b64decode(text.encode("ascii"), validate=True)


def key_id_for(raw_public: bytes) -> str:
    """A stable 16-hex keyId = ``sha256(raw_public_32)[:16]``.

    Derived from the RAW 32-byte public key, so the keyId is identical whether the key
    was loaded from PEM or raw bytes. It is an INDEX/label only — the full stored public
    key is the verification material, never a key selected by truncated keyId alone.
    """

    return hashlib.sha256(raw_public).hexdigest()[:16]


def public_key_id(public_b64: str) -> str:
    """Validate a base64 raw-32 Ed25519 public key and return its keyId.

    Raises :class:`ValueError` on malformed base64 or a wrong-length key — so
    ``trust add`` can confirm the caller-supplied keyId actually matches the key bytes
    (no crypto import needed; keyId derivation is a plain SHA-256).
    """

    raw = _b64d(public_b64.strip())
    if len(raw) != 32:
        raise ValueError(f"an Ed25519 public key is 32 raw bytes, got {len(raw)}")
    return key_id_for(raw)


def build_statement(
    *,
    kind: str,
    key_id: str,
    name: str | None = None,
    version: str | None = None,
    sha256: str | None = None,
    git_sha: str | None = None,
) -> dict[str, object]:
    """The signed statement object (canonicalized by :func:`canonical_bytes`).

    The PRIMARY, machine-independent binding is ``sha256`` — the exact artifact bytes;
    presenting a signature alongside any other artifact fails the digest cross-check.
    ``name``/``version`` bind the package identity (cross-checked for pypi) as
    defense-in-depth. ``git_sha`` anchors a git commit (statement is git-ready even
    though the install-time git branch is not wired in v1).

    Deliberately does NOT bind a path ``identity`` (the install-target absolute path is
    machine-specific — a publisher signing a wheel cannot know where an installer will
    place it; the digest already pins the exact bytes).
    """

    st: dict[str, object] = {"v": STATEMENT_VERSION, "kind": kind, "keyId": key_id}
    if name is not None:
        st["name"] = name
    if version is not None:
        st["version"] = version
    if sha256 is not None:
        st["sha256"] = sha256
    if git_sha is not None:
        st["gitSha"] = git_sha
    return st


def canonical_bytes(statement: dict[str, object]) -> bytes:
    """Deterministic UTF-8 serialization of a statement (the exact bytes signed).

    ``sort_keys`` + compact separators + ``ensure_ascii`` make sign-side and verify-side
    produce byte-identical input regardless of dict insertion order or platform. (RFC
    8785 JCS would be the formal choice; there is no JCS library in-env and a
    self-produced statement over ASCII/ints/strings has no JCS-vs-sorted-keys divergence,
    so sorted-keys compact JSON is the pragmatic, dependency-free canonical form.)
    """

    return json.dumps(
        statement, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


# =====================================================================
# === `.aelixsig` detached-signature sidecar (pure I/O) ================
# =====================================================================


def aelixsig_path_for(artifact: str | os.PathLike[str]) -> Path:
    """The sibling sidecar path for a path artifact (``<artifact>.aelixsig``)."""

    return Path(str(artifact) + AELIXSIG_SUFFIX)


def _aelixsig_bytes(*, key_id: str, statement: dict[str, object], sig_b64: str) -> bytes:
    """Serialize a detached-signature envelope to its exact ``.aelixsig`` bytes.

    The single source of truth for the envelope shape, shared by the path writer
    (:func:`write_aelixsig`) and the bytes publisher (:func:`sign_document`). Carries the
    literal signed ``statement`` + the base64 signature + the keyId, but NEVER a public
    key (a key shipped with the artifact is not a trust source — the keyId must resolve in
    the local trust set at verify time).
    """

    payload = {
        "aelixsig": AELIXSIG_VERSION,
        "keyId": key_id,
        "statement": statement,
        "sig": sig_b64,
    }
    return (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")


def write_aelixsig(
    path: str | os.PathLike[str],
    *,
    key_id: str,
    statement: dict[str, object],
    sig_b64: str,
) -> None:
    """Write a self-describing detached-signature sidecar (atomic)."""

    _atomic_write_bytes(
        Path(path), _aelixsig_bytes(key_id=key_id, statement=statement, sig_b64=sig_b64)
    )


def read_aelixsig(path: str | os.PathLike[str]) -> dict[str, object] | None:
    """Load a ``.aelixsig`` → its raw dict, or :data:`None` if missing/unreadable/bad.

    A missing or malformed sidecar returns :data:`None` (the caller then treats the
    source as unsigned, or refuses under ``--require-signature``) — reading it never
    raises.
    """

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        # RecursionError guards a maliciously DEEP JSON sidecar (attacker-controlled):
        # json's C parser raises it, and it is NOT a ValueError — without this it would
        # escape to install_extension's generic handler. Degrading to None here means a
        # corrupt/hostile sidecar reads as "no signature" → refused under
        # --require-signature (fail-closed), unsigned/TOFI on the default path.
        return None
    return raw if isinstance(raw, dict) else None


def _parse_aelixsig_bytes(raw: bytes) -> dict[str, object] | None:
    """Parse a ``.aelixsig`` envelope from BYTES → its dict, or :data:`None` if bad.

    The bytes-input sibling of :func:`read_aelixsig` for a sidecar that was FETCHED (a
    catalog's ``<location>.aelixsig``) rather than read from a path. Reuses the same
    leniency — a decode/JSON error, a non-object, or a :class:`RecursionError` (a
    maliciously DEEP JSON envelope, which json's C parser raises and which is NOT a
    :class:`ValueError`) all degrade to :data:`None` so a hostile document sidecar reads
    as "no signature" rather than an escaping traceback (mirrors :281-287).
    """

    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


# =====================================================================
# === Trust store (`<agent_dir>/trusted_keys.json`, sync sidecar) ======
# =====================================================================


@dataclass(frozen=True)
class TrustedKey:
    """One user-trusted Ed25519 verification key."""

    key_id: str
    public_key: str  # base64 raw-32
    label: str | None = None
    added_at: str | None = None
    source: str | None = None
    #: Unknown keys from a newer schema, preserved verbatim on rewrite.
    extra: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"publicKey": self.public_key}
        if self.label is not None:
            out["label"] = self.label
        if self.added_at is not None:
            out["addedAt"] = self.added_at
        if self.source is not None:
            out["source"] = self.source
        out.update(self.extra)
        return out

    @classmethod
    def from_json(cls, key_id: str, raw: dict[str, object]) -> TrustedKey:
        known = {"publicKey", "label", "addedAt", "source"}
        extra = {k: v for k, v in raw.items() if k not in known}

        def _s(key: str) -> str | None:
            v = raw.get(key)
            return v if isinstance(v, str) else None

        return cls(
            key_id=key_id,
            public_key=_s("publicKey") or "",
            label=_s("label"),
            added_at=_s("addedAt"),
            source=_s("source"),
            extra=extra,
        )


@dataclass(frozen=True)
class TrustStore:
    """The loaded trust store: user keys + a revoked keyId list."""

    keys: dict[str, TrustedKey] = field(default_factory=dict)
    revoked: tuple[str, ...] = ()


def trusted_keys_path(agent_dir: str | os.PathLike[str]) -> Path:
    """The trust-store path under ``agent_dir`` (``<agent_dir>/trusted_keys.json``).

    Resolved by the caller via ``config.get_agent_dir()`` (honors
    ``AELIX_CODING_AGENT_DIR``), so tests isolate the trust store via that env var — the
    same lever as the ``extension_pins.json`` sidecar.
    """

    return Path(agent_dir) / TRUSTED_KEYS_FILENAME


def load_trusted_keys(path: Path) -> TrustStore:
    """Load ``path`` → :class:`TrustStore`; empty on a missing/unreadable/bad file.

    A corrupt trust store degrades to empty (the safe direction = *no* trust, never a
    silent grant) rather than raising — a bad file must never brick an install.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return TrustStore()
    if not isinstance(raw, dict):
        return TrustStore()
    keys: dict[str, TrustedKey] = {}
    entries = raw.get("keys")
    if isinstance(entries, dict):
        for key_id, spec in entries.items():
            if isinstance(key_id, str) and isinstance(spec, dict):
                keys[key_id] = TrustedKey.from_json(key_id, spec)
    revoked_raw = raw.get("revoked")
    revoked = tuple(k for k in revoked_raw if isinstance(k, str)) if isinstance(revoked_raw, list) else ()
    return TrustStore(keys=keys, revoked=revoked)


def save_trusted_keys(store: TrustStore, path: Path) -> None:
    """Atomically write ``store`` to ``path`` (create parent; ``os.replace``)."""

    payload = {
        "version": SCHEMA_VERSION,
        "keys": {kid: store.keys[kid].to_json() for kid in sorted(store.keys)},
        "revoked": sorted(set(store.revoked)),
    }
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")


def resolve_public_key(store: TrustStore, key_id: str) -> str | None:
    """The base64 public key trusted for ``key_id``, or :data:`None` if not trusted.

    Effective trust = :data:`FIRST_PARTY_KEYS` UNION the user store, MINUS ``revoked``.
    A revoked keyId always loses — even a first-party one.
    """

    if key_id in store.revoked:
        return None
    if key_id in store.keys:
        return store.keys[key_id].public_key
    return FIRST_PARTY_KEYS.get(key_id)


# =====================================================================
# === Ed25519 primitives (lazy crypto) =================================
# =====================================================================


def _public_raw(private_key: Ed25519PrivateKey) -> bytes:
    _, serialization, _, _ = _crypto()
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _sign(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    return private_key.sign(data)


def _verify_raw(public_raw: bytes, sig: bytes, data: bytes) -> bool:
    """True iff ``sig`` is a valid Ed25519 signature of ``data`` by ``public_raw``.

    Catches :class:`InvalidSignature` (raised for a real tamper AND a malformed/wrong-
    length signature) plus ``ValueError`` (bad key length) so no crypto exception escapes
    as an uncaught traceback — the caller maps a :data:`False` to a refusal.
    """

    InvalidSignature, _, _, Ed25519PublicKey = _crypto()
    try:
        pub = Ed25519PublicKey.from_public_bytes(public_raw)
        pub.verify(sig, data)
        return True
    except (InvalidSignature, ValueError):
        return False


# =====================================================================
# === Publisher tooling: keygen + sign (effectful, private key) ========
# =====================================================================


def keygen(
    agent_dir: str | os.PathLike[str],
    *,
    label: str | None = None,
    passphrase: bytes | None = None,
    force: bool = False,
    out_dir: str | os.PathLike[str] | None = None,
) -> tuple[str, str, Path]:
    """Generate an Ed25519 keypair; write the PRIVATE key PKCS8 PEM at 0600.

    Returns ``(key_id, public_key_b64, private_key_path)``. The private key is written to
    ``<agent_dir>/keys/<keyId>.pem`` (or ``<out_dir>/<keyId>.pem``) with the keys dir at
    0700 and the file at 0600; it is NEVER returned as bytes or logged. Refuses to
    overwrite an existing key file unless ``force``. ``label`` is advisory metadata for
    the caller to surface — it is not embedded in the key.
    """

    _, serialization, Ed25519PrivateKey, _ = _crypto()
    private_key = Ed25519PrivateKey.generate()
    raw_pub = _public_raw(private_key)
    key_id = key_id_for(raw_pub)

    keys_dir = Path(out_dir) if out_dir is not None else Path(agent_dir) / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(keys_dir, 0o700)

    path = keys_dir / f"{key_id}.pem"
    if path.exists() and not force:
        raise FileExistsError(
            f"a key already exists at {path} (keyId {key_id}); pass --force to overwrite"
        )

    encryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )
    _atomic_write_bytes(path, pem, mode=0o600)
    return key_id, _b64e(raw_pub), path


def load_private_key(path: Path, *, passphrase: bytes | None = None) -> Ed25519PrivateKey:
    """Load a PKCS8 PEM private key (raising :class:`CryptoUnavailable` if no crypto)."""

    _, serialization, _, _ = _crypto()
    key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
    return key  # type: ignore[return-value]


def sign_artifact(
    artifact: Path,
    private_key: Ed25519PrivateKey,
    *,
    kind: str,
    name: str | None = None,
    version: str | None = None,
    out: Path | None = None,
) -> tuple[Path, str]:
    """Sign ``artifact`` and write its ``.aelixsig`` sidecar. Returns ``(path, keyId)``.

    Computes the artifact's SHA-256 (via :func:`extension_pins.sha256_file`), builds the
    canonical statement binding it to ``name``/``version``/``kind``/keyId, signs the
    canonical bytes, and writes the detached sidecar (default: ``<artifact>.aelixsig``).
    """

    raw_pub = _public_raw(private_key)
    key_id = key_id_for(raw_pub)
    sha256 = extension_pins.sha256_file(artifact)
    statement = build_statement(
        kind=kind, key_id=key_id, name=name, version=version, sha256=sha256
    )
    sig = _sign(private_key, canonical_bytes(statement))
    sidecar = out if out is not None else aelixsig_path_for(artifact)
    write_aelixsig(sidecar, key_id=key_id, statement=statement, sig_b64=_b64e(sig))
    return sidecar, key_id


def sign_document(
    document: bytes,
    private_key: Ed25519PrivateKey,
    *,
    kind: str = "catalog",
    name: str | None = None,
    version: str | None = None,
) -> bytes:
    """Sign an in-memory ``document`` → the ``.aelixsig`` envelope BYTES.

    The bytes-in/bytes-out publisher sibling of :func:`sign_artifact` for a document (e.g.
    a catalog's ``catalog.json``). Computes the SHA-256 over the RAW document bytes, builds
    the canonical statement binding it to ``kind`` (default ``"catalog"``)/keyId, signs the
    canonical statement bytes, and returns the serialized detached-signature envelope for
    the publisher to write beside the document (``catalog.json.aelixsig``). The digest is
    the exact same independent one :func:`verify_signed_document` recomputes, so the two
    round-trip.
    """

    raw_pub = _public_raw(private_key)
    key_id = key_id_for(raw_pub)
    sha256 = hashlib.sha256(document).hexdigest()
    statement = build_statement(
        kind=kind, key_id=key_id, name=name, version=version, sha256=sha256
    )
    sig = _sign(private_key, canonical_bytes(statement))
    return _aelixsig_bytes(key_id=key_id, statement=statement, sig_b64=_b64e(sig))


# =====================================================================
# === The verify gate (called from verify_and_pin, fail-closed) ========
# =====================================================================


@dataclass(frozen=True)
class SignatureOutcome:
    """The result of a signature check for one install.

    ``authenticated`` is :data:`True` only when a trusted key's signature verified AND
    its statement matched the observed bytes — the caller then stamps ``key_id``/``sig``/
    ``statement_json`` onto the recorded :class:`~.extension_pins.Pin` and lets
    ``decide_*`` treat the source as vouched-for (no blind first-acquisition/re-TOFI).
    A refusal is raised as :class:`~.extension_pins.VerifyRefusal`, never returned.
    """

    authenticated: bool = False
    key_id: str | None = None
    sig: str | None = None
    statement_json: str | None = None
    notice: str | None = None


def _statement_mismatch(
    statement: dict[str, object],
    *,
    kind: str,
    key_id: str,
    sha256: str | None,
    canonical_name: str | None,
    version: str | None,
    git_sha: str | None,
) -> str | None:
    """Step (2): the signed statement must equal what the gate independently observed.

    Returns a human reason string on the FIRST disagreeing field, else :data:`None`.
    Without this a cryptographically-valid signature over an attacker-chosen statement
    would pass — the signature only proves the signer produced *these* bytes; this check
    proves *these* bytes describe *this* install. ``sha256`` is the primary binding;
    ``canonical_name``/``version`` cross-check the package identity for pypi (PEP 503
    canonicalized on both sides so ``My_Pkg`` ≡ ``my-pkg``).
    """

    if statement.get("kind") != kind:
        return f"kind {statement.get('kind')!r} != {kind!r}"
    if statement.get("keyId") != key_id:
        return "keyId"
    if sha256 is not None and statement.get("sha256") != sha256:
        return "sha256 (bytes differ from the signed digest)"
    if canonical_name is not None:
        st_name = statement.get("name")
        if not isinstance(st_name, str) or extension_pins.canonicalize_name(st_name) != canonical_name:
            return f"name {st_name!r} != {canonical_name!r}"
    if version is not None and statement.get("version") not in (None, version):
        return f"version {statement.get('version')!r} != {version!r}"
    if git_sha is not None and statement.get("gitSha") != git_sha:
        return "gitSha"
    return None


def gate_signature(
    *,
    kind: str,
    identity: str,
    sidecar_path: Path | None,
    observed_sha256: str | None,
    canonical_name: str | None,
    version: str | None,
    git_sha: str | None,
    require_signature: bool,
    trusted_key: str | None,
    agent_dir: str,
) -> SignatureOutcome:
    """Check a pack's signature against the local trust set (fail-closed).

    The decision matrix (all refusals raise :class:`~.extension_pins.VerifyRefusal` →
    the caller maps that to exit-code 2, "pip never ran"):

    * no ``.aelixsig`` present → ``--require-signature`` REFUSES; else unsigned (TOFI).
    * present, keyId not in the trust set (or ``--trusted-key`` mismatch) → REFUSE under
      ``--require-signature``; else treated as unsigned with a warning.
    * present, keyId trusted, signature INVALID or statement ≠ observed → REFUSE ALWAYS
      (tampering evidence, even without ``--require-signature``).
    * present, keyId trusted, signature valid AND statement == observed → AUTHENTICATED.
    """

    sidecar = read_aelixsig(sidecar_path) if sidecar_path is not None else None
    if sidecar is None:
        if require_signature:
            raise extension_pins.VerifyRefusal(
                f"--require-signature: no valid .aelixsig signature found for {identity}"
            )
        # A present-but-unreadable/corrupt sidecar is a visible tamper signal on the
        # default (opt-out) path — surface it (mirrors the malformed-fields notice
        # below) rather than degrading silently. A truly-ABSENT sidecar stays silent
        # (an ordinary unsigned source; the #19 air-gap path must not grow noise).
        present = sidecar_path is not None and sidecar_path.exists()
        return SignatureOutcome(
            notice="ignoring an unreadable/corrupt .aelixsig — treated as unsigned"
            if present
            else None
        )

    return _verify_statement(
        sidecar,
        kind=kind,
        observed_sha256=observed_sha256,
        canonical_name=canonical_name,
        version=version,
        git_sha=git_sha,
        trusted_key=trusted_key,
        agent_dir=agent_dir,
        identity=identity,
        require_signature=require_signature,
    )


def _verify_statement(
    sidecar: dict[str, object],
    *,
    kind: str,
    observed_sha256: str | None,
    canonical_name: str | None,
    version: str | None,
    git_sha: str | None,
    trusted_key: str | None,
    agent_dir: str,
    identity: str,
    require_signature: bool,
) -> SignatureOutcome:
    """Shared verify core: the malformed → untrusted → verify → cross-check matrix.

    Extracted from :func:`gate_signature` so it and :func:`verify_signed_document` apply
    an identical decision matrix to a PARSED ``.aelixsig`` dict. The upstream None/absent/
    corrupt distinction stays with each caller (a path artifact vs. a fetched document
    differ in how "the sidecar is missing"); everything from the malformed-fields check
    onward is this function. All refusals raise :class:`~.extension_pins.VerifyRefusal`;
    a degrade returns a non-authenticated :class:`SignatureOutcome`.
    """

    key_id = sidecar.get("keyId")
    statement = sidecar.get("statement")
    sig_b64 = sidecar.get("sig")
    if not (isinstance(key_id, str) and isinstance(statement, dict) and isinstance(sig_b64, str)):
        if require_signature:
            raise extension_pins.VerifyRefusal(
                f"--require-signature: malformed .aelixsig for {identity}"
            )
        return SignatureOutcome(notice="ignoring a malformed .aelixsig — treated as unsigned")

    store = load_trusted_keys(trusted_keys_path(agent_dir))
    public_b64 = resolve_public_key(store, key_id)
    if trusted_key is not None and key_id != trusted_key:
        # --trusted-key restricts the accepted signer regardless of the wider trust set.
        public_b64 = None
    if public_b64 is None:
        if require_signature:
            raise extension_pins.VerifyRefusal(
                f"signature by untrusted key {key_id} for {identity} — trust it with "
                f"`aelix extension trust add {key_id} --public-key <b64>`, or omit --require-signature"
            )
        return SignatureOutcome(
            notice=f"signed by untrusted key {key_id} — treated as unsigned "
            "(`aelix extension trust add` to trust it)"
        )

    # A trusted keyId: from here any failure is affirmative tampering evidence → refuse
    # ALWAYS (even without --require-signature). Crypto-load failure likewise refuses
    # rather than silently downgrading a present signature.
    try:
        # base64 junk raises binascii.Error, a ValueError subclass — covered below.
        ok = _verify_raw(_b64d(public_b64), _b64d(sig_b64), canonical_bytes(statement))
    except (CryptoUnavailable, ValueError) as exc:
        raise extension_pins.VerifyRefusal(
            f"could not verify the signature by trusted key {key_id} for {identity} ({exc})"
        ) from exc
    if not ok:
        raise extension_pins.VerifyRefusal(
            f"signature by trusted key {key_id} FAILED to verify for {identity} — "
            "refusing (tampering evidence)"
        )
    mismatch = _statement_mismatch(
        statement,
        kind=kind,
        key_id=key_id,
        sha256=observed_sha256,
        canonical_name=canonical_name,
        version=version,
        git_sha=git_sha,
    )
    if mismatch is not None:
        raise extension_pins.VerifyRefusal(
            f"signature statement mismatch for {identity} ({mismatch}) — "
            "refusing (tampering evidence)"
        )
    return SignatureOutcome(
        authenticated=True,
        key_id=key_id,
        sig=sig_b64,
        statement_json=canonical_bytes(statement).decode("utf-8"),
        notice=f"authenticated by trusted key {key_id}",
    )


def verify_signed_document(
    document: bytes,
    sidecar: bytes | None,
    *,
    kind: str = "catalog",
    agent_dir: str,
    require_signature: bool,
    trusted_key: str | None = None,
    identity: str | None = None,
) -> SignatureOutcome:
    """Verify a fetched ``document`` against an in-memory ``.aelixsig`` (fail-closed).

    The bytes-in analog of :func:`gate_signature` for a document (a catalog's fetched
    ``catalog.json`` + its sibling ``<location>.aelixsig`` bytes) rather than a path
    artifact. Mirrors the gate 1:1 (via the shared :func:`_verify_statement` core), with
    two differences:

    * ``observed_sha256`` is computed INDEPENDENTLY over the RAW ``document`` bytes here
      (``hashlib.sha256(document).hexdigest()``) — NEVER read from any catalog field — and
      is the ONLY binding cross-checked (``canonical_name``/``version``/``git_sha`` are
      :data:`None`; the ``kind`` cross-check keeps a pack signature from being replayed as
      a catalog one, and vice-versa).
    * "no sidecar" is ``sidecar is None`` (absent → silent unsigned) vs. present-but-
      unparseable bytes (corrupt → a visible notice); a truly-absent sidecar must not
      brick an air-gap/intranet catalog while :data:`FIRST_PARTY_KEYS` is empty.

    Fail-closed cases RAISE :class:`~.extension_pins.VerifyRefusal` (a trusted key whose
    signature is INVALID / whose digest mismatches / whose statement disagrees — ALWAYS,
    even in beta warn-default; plus absent/malformed/untrusted under ``require_signature``).
    Degrade cases RETURN a non-authenticated outcome on the default path (absent = silent,
    corrupt/malformed = notice, untrusted keyId = warning).
    """

    label = identity or kind
    parsed = _parse_aelixsig_bytes(sidecar) if sidecar is not None else None
    if parsed is None:
        if require_signature:
            raise extension_pins.VerifyRefusal(
                f"--require-signature: no valid .aelixsig signature found for {label}"
            )
        # Bytes were fetched but do not parse → a visible corrupt signal (mirrors the
        # gate's present-but-unreadable notice); a truly-absent sidecar stays silent.
        return SignatureOutcome(
            notice="ignoring an unreadable/corrupt .aelixsig — treated as unsigned"
            if sidecar is not None
            else None
        )

    observed_sha256 = hashlib.sha256(document).hexdigest()
    return _verify_statement(
        parsed,
        kind=kind,
        observed_sha256=observed_sha256,
        canonical_name=None,
        version=None,
        git_sha=None,
        trusted_key=trusted_key,
        agent_dir=agent_dir,
        identity=label,
        require_signature=require_signature,
    )


# =====================================================================
# === Atomic writers (mirror extension_pins.save_pins) =================
# =====================================================================


def _atomic_write_text(path: Path, body: str, *, mode: int | None = None) -> None:
    _atomic_write_bytes(path, body.encode("utf-8"), mode=mode)


def _atomic_write_bytes(path: Path, body: bytes, *, mode: int | None = None) -> None:
    """Same-dir temp file + ``os.replace`` atomic swap; optional post-write chmod.

    A crash mid-write never truncates an existing (possibly admin-provisioned) file.
    ``chmod`` runs AFTER the replace so an umask cannot leave a private key briefly
    world-readable (mirrors ``login_wizard`` / ``auth.json``).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
