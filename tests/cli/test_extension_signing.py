"""Issue #67 (ADR-0189) — Ed25519 detached-signature provenance tests.

Two surfaces, mirroring #64:

* PURE module tests (``extension_signing``) — keygen/sign/verify primitives, the
  canonical statement, the trust store, and the :func:`gate_signature` decision matrix,
  driven with ``tmp_path`` + a real ``cryptography`` (no pip, no network).
* INTEGRATION tests through the CLI verbs + the install gate — ``keygen`` / ``sign`` /
  ``trust`` and ``install --require-signature``, with an injected pip runner (never real
  pip) and an in-memory settings manager.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest
from aelix_ai.settings import ExtensionSourceObject, SettingsManager
from aelix_coding_agent.cli import extension_pins as ep
from aelix_coding_agent.cli import extension_signing as es
from aelix_coding_agent.cli.extension_install import (
    _cmd_sign,
    install_extension,
    run_extension_command,
    run_extension_command_async,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the agent dir + settings file at throwaway paths (never touch ~/.aelix)."""

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.chdir(tmp_path)


class _FakeRunner:
    """Records the pip argv; returns a chosen exit code (never runs real pip)."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


def _yes(_prompt: str) -> str:
    return "y"


def _agent_dir(tmp_path: Path) -> str:
    return str(tmp_path / "agent")


def _make_key(tmp_path: Path, label: str | None = None) -> tuple[str, str, Path]:
    """Generate a key in the isolated agent dir; return (keyId, pub_b64, pem_path)."""

    return es.keygen(_agent_dir(tmp_path), label=label)


def _make_artifact(tmp_path: Path, name: str = "acme_ext-1.0-py3-none-any.whl") -> Path:
    art = tmp_path / name
    art.write_bytes(b"PK\x03\x04 fake wheel bytes for " + name.encode())
    return art


def _trust(tmp_path: Path, key_id: str, pub_b64: str, label: str | None = None) -> None:
    path = es.trusted_keys_path(_agent_dir(tmp_path))
    store = es.load_trusted_keys(path)
    keys = dict(store.keys)
    keys[key_id] = es.TrustedKey(
        key_id=key_id, public_key=pub_b64, label=label, added_at=ep.now_iso()
    )
    es.save_trusted_keys(es.TrustStore(keys=keys, revoked=store.revoked), path)


# === keygen / keyId primitives ===========================================


def test_keygen_produces_loadable_keypair(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    assert pem.is_file()
    # Reloading the PEM yields a working private key whose pubkey → the same keyId.
    priv = es.load_private_key(pem)
    reloaded = es.key_id_for(es._public_raw(priv))
    assert reloaded == key_id
    assert es.public_key_id(pub_b64) == key_id  # keyId derives from the public bytes


@pytest.mark.skipif(os.name != "posix", reason="POSIX perms only")
def test_keygen_private_key_is_0600_and_dir_0700(tmp_path: Path) -> None:
    _, _, pem = _make_key(tmp_path)
    assert oct(os.stat(pem).st_mode & 0o777) == "0o600"
    assert oct(os.stat(pem.parent).st_mode & 0o777) == "0o700"


def test_keygen_refuses_overwrite_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id, _, path = _make_key(tmp_path)
    priv = es.load_private_key(path)
    # Force the next keygen to regenerate the SAME key (→ same keyId → same path) so the
    # no-clobber guard is exercised deterministically despite random key generation.
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed

    monkeypatch.setattr(ed.Ed25519PrivateKey, "generate", staticmethod(lambda: priv))
    with pytest.raises(FileExistsError):
        es.keygen(_agent_dir(tmp_path))
    # --force overwrites the same-keyId file.
    kid2, _, path2 = es.keygen(_agent_dir(tmp_path), force=True)
    assert kid2 == key_id and path2 == path


def test_keyid_derivation_stable_across_raw_and_pem(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    from_raw = es.key_id_for(es._b64d(pub_b64))
    priv = es.load_private_key(pem)
    from_pem = es.key_id_for(es._public_raw(priv))
    assert from_raw == from_pem == key_id


def test_public_key_id_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        es.public_key_id("not base64 @@@")
    with pytest.raises(ValueError):
        es.public_key_id(es._b64e(b"too short"))  # not 32 bytes


# === sign / verify round-trip ============================================


def test_sign_produces_verifiable_aelixsig(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    art = _make_artifact(tmp_path)
    priv = es.load_private_key(pem)
    sidecar, skid = es.sign_artifact(art, priv, kind="path", name="acme-ext", version="1.0")
    assert skid == key_id
    assert sidecar == es.aelixsig_path_for(art)
    raw = es.read_aelixsig(sidecar)
    assert raw is not None and raw["keyId"] == key_id
    st = cast("dict[str, object]", raw["statement"])
    assert st["sha256"] == ep.sha256_file(art)
    assert st["kind"] == "path" and st["keyId"] == key_id
    sig_b64 = raw["sig"]
    assert isinstance(sig_b64, str)
    # The signature verifies against the canonical bytes of the statement.
    assert es._verify_raw(es._b64d(pub_b64), es._b64d(sig_b64), es.canonical_bytes(st))


def test_canonical_bytes_is_order_independent() -> None:
    a = es.canonical_bytes({"b": 1, "a": 2})
    b = es.canonical_bytes({"a": 2, "b": 1})
    assert a == b == b'{"a":2,"b":1}'


# === gate_signature decision matrix ======================================


def _gate(
    tmp_path: Path,
    art: Path,
    sidecar: Path | None,
    observed: str,
    *,
    require_signature: bool = False,
    trusted_key: str | None = None,
    canonical_name: str | None = None,
    version: str | None = None,
    kind: str = "path",
) -> es.SignatureOutcome:
    return es.gate_signature(
        kind=kind, identity=str(art.resolve()), sidecar_path=sidecar,
        observed_sha256=observed, canonical_name=canonical_name, version=version,
        git_sha=None, require_signature=require_signature, trusted_key=trusted_key,
        agent_dir=_agent_dir(tmp_path),
    )


def test_gate_trusted_valid_authenticates(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    out = _gate(tmp_path, art, sidecar, ep.sha256_file(art))
    assert out.authenticated and out.key_id == key_id and out.sig and out.statement_json


def test_gate_tampered_artifact_refuses_even_default(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    art.write_bytes(b"TAMPERED")  # digest no longer matches the signed statement
    with pytest.raises(ep.VerifyRefusal, match="statement mismatch"):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art), require_signature=False)


def test_gate_corrupt_signature_from_trusted_key_refuses(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    raw = es.read_aelixsig(sidecar)
    assert raw is not None
    wrong_sig = es._b64e(b"\x00" * 64)  # a wrong (but well-formed) signature
    st = cast("dict[str, object]", raw["statement"])
    es.write_aelixsig(sidecar, key_id=key_id, statement=st, sig_b64=wrong_sig)
    with pytest.raises(ep.VerifyRefusal, match="FAILED to verify"):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art))


def test_gate_untrusted_default_degrades_to_unsigned(tmp_path: Path) -> None:
    _, _, pem = _make_key(tmp_path)  # NOT added to the trust store
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    out = _gate(tmp_path, art, sidecar, ep.sha256_file(art))
    assert not out.authenticated and out.notice and "untrusted key" in out.notice


def test_gate_untrusted_require_signature_refuses(tmp_path: Path) -> None:
    _, _, pem = _make_key(tmp_path)
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    with pytest.raises(ep.VerifyRefusal, match="untrusted key"):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art), require_signature=True)


def test_gate_missing_signature(tmp_path: Path) -> None:
    art = _make_artifact(tmp_path)
    missing = es.aelixsig_path_for(art)  # never written
    # default → unsigned (no notice, no brick)
    out = _gate(tmp_path, art, missing, ep.sha256_file(art))
    assert not out.authenticated and out.notice is None
    # require → refuse
    with pytest.raises(ep.VerifyRefusal, match="no valid .aelixsig"):
        _gate(tmp_path, art, missing, ep.sha256_file(art), require_signature=True)


def test_gate_trusted_key_restriction(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    # A different required keyId → the (otherwise trusted) signature is not accepted.
    with pytest.raises(ep.VerifyRefusal, match="untrusted key"):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art),
              require_signature=True, trusted_key="0000000000000000")
    # The matching keyId → authenticates.
    out = _gate(tmp_path, art, sidecar, ep.sha256_file(art), trusted_key=key_id)
    assert out.authenticated


def test_gate_revoked_key_loses_even_if_first_party(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    monkeypatch.setattr(es, "FIRST_PARTY_KEYS", {key_id: pub_b64})
    art = _make_artifact(tmp_path)
    sidecar, _ = es.sign_artifact(art, es.load_private_key(pem), kind="path")
    # First-party → authenticates out of the box.
    assert _gate(tmp_path, art, sidecar, ep.sha256_file(art)).authenticated
    # Revoke it → require_signature now refuses (revocation wins over first-party).
    path = es.trusted_keys_path(_agent_dir(tmp_path))
    es.save_trusted_keys(es.TrustStore(revoked=(key_id,)), path)
    with pytest.raises(ep.VerifyRefusal):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art), require_signature=True)


def test_gate_pypi_name_and_version_binding(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path, "acme_ext-1.0.tar.gz")
    es.sign_artifact(
        art, es.load_private_key(pem), kind="pypi", name="Acme_Ext", version="1.0",
        out=tmp_path / "sig.aelixsig",
    )
    observed = ep.sha256_file(art)
    # canonical name matches (Acme_Ext ≡ acme-ext); authenticates.
    out = es.gate_signature(
        kind="pypi", identity="acme-ext", sidecar_path=tmp_path / "sig.aelixsig",
        observed_sha256=observed, canonical_name="acme-ext", version="1.0", git_sha=None,
        require_signature=True, trusted_key=None, agent_dir=_agent_dir(tmp_path),
    )
    assert out.authenticated
    # A different version presented → statement mismatch → refuse.
    with pytest.raises(ep.VerifyRefusal, match="version"):
        es.gate_signature(
            kind="pypi", identity="acme-ext", sidecar_path=tmp_path / "sig.aelixsig",
            observed_sha256=observed, canonical_name="acme-ext", version="2.0", git_sha=None,
            require_signature=True, trusted_key=None, agent_dir=_agent_dir(tmp_path),
        )


# === trust store ==========================================================


def test_trusted_keys_round_trip_and_corrupt_degrades(tmp_path: Path) -> None:
    path = es.trusted_keys_path(_agent_dir(tmp_path))
    store = es.TrustStore(
        keys={"abc": es.TrustedKey(key_id="abc", public_key="AAAA", label="x")},
        revoked=("def",),
    )
    es.save_trusted_keys(store, path)
    loaded = es.load_trusted_keys(path)
    assert loaded.keys["abc"].public_key == "AAAA" and loaded.revoked == ("def",)
    # A corrupt file degrades to empty (the safe direction = no trust), never raises.
    path.write_text("{ not json", encoding="utf-8")
    assert es.load_trusted_keys(path).keys == {}


def test_resolve_public_key_merges_first_party(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, "FIRST_PARTY_KEYS", {"fp": "FPKEY"})
    store = es.TrustStore(keys={"u": es.TrustedKey(key_id="u", public_key="UKEY")}, revoked=("fp",))
    assert es.resolve_public_key(store, "u") == "UKEY"
    assert es.resolve_public_key(store, "fp") is None  # revoked wins over first-party
    assert es.resolve_public_key(store, "missing") is None


# === decide_* authenticated behavior (#67 layering) =======================


def test_authenticated_first_acquisition_records_under_strict() -> None:
    # Without a signature, strict + no pin refuses; an authenticated signature vouches.
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_generic(None, "sha", mode="strict", repin=False, label="x")
    d = ep.decide_generic(None, "sha", mode="strict", repin=False, label="x", authenticated=True)
    assert d.record and "authenticated" in d.notice


def test_authenticated_version_bump_records_under_strict() -> None:
    existing = ep.Pin(identity="p", kind="pypi", mode="strict", version="1.0", sha256="old")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(existing, "new", "2.0", mode="strict", repin=False, label="p")
    d = ep.decide_pypi(existing, "new", "2.0", mode="strict", repin=False, label="p", authenticated=True)
    assert d.record


def test_authenticated_does_not_bypass_same_version_mismatch() -> None:
    # A same-version byte change is the tamper signal — still refused even authenticated
    # (the signature vouches for FIRST trust / a version bump, not a silent re-pin).
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="old")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(existing, "new", "1.0", mode="tofi", repin=False, label="p", authenticated=True)


# === CLI verbs: keygen / sign / trust =====================================


def test_cli_keygen_writes_key_and_prints_public(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = run_extension_command(["keygen", "--label", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "signing key" in out and "public key" in out
    # The private key landed under the isolated agent dir, never printed.
    keys = list((Path(_agent_dir(tmp_path)) / "keys").glob("*.pem"))
    assert len(keys) == 1
    assert "PRIVATE KEY" not in out


def test_cli_trust_add_consent_denied_by_default(tmp_path: Path) -> None:
    key_id, pub_b64, _ = _make_key(tmp_path)
    # Closed stdin (EOF) → deny.
    rc = run_extension_command(
        ["trust", "add", key_id, "--public-key", pub_b64],
        input_fn=lambda _p: (_ for _ in ()).throw(EOFError()),
    )
    assert rc == 2
    assert es.load_trusted_keys(es.trusted_keys_path(_agent_dir(tmp_path))).keys == {}


def test_cli_trust_add_rejects_mismatched_keyid(tmp_path: Path) -> None:
    _, pub_b64, _ = _make_key(tmp_path)
    rc = run_extension_command(
        ["trust", "add", "deadbeefdeadbeef", "--public-key", pub_b64, "--yes"],
        input_fn=_yes,
    )
    assert rc == 2  # keyId does not match the public key


def test_cli_trust_add_list_remove_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key_id, pub_b64, _ = _make_key(tmp_path)
    assert run_extension_command(["trust", "add", key_id, "--public-key", pub_b64, "--yes"]) == 0
    capsys.readouterr()
    run_extension_command(["trust", "list"])
    assert key_id in capsys.readouterr().out
    assert run_extension_command(["trust", "remove", key_id]) == 0
    assert es.load_trusted_keys(es.trusted_keys_path(_agent_dir(tmp_path))).keys == {}


def test_cli_trust_revoke(tmp_path: Path) -> None:
    key_id, pub_b64, _ = _make_key(tmp_path)
    run_extension_command(["trust", "add", key_id, "--public-key", pub_b64, "--yes"])
    assert run_extension_command(["trust", "revoke", key_id]) == 0
    store = es.load_trusted_keys(es.trusted_keys_path(_agent_dir(tmp_path)))
    assert key_id in store.revoked


async def test_cli_trust_add_then_remove_async(tmp_path: Path) -> None:
    key_id, pub_b64, _ = _make_key(tmp_path)
    rc = await run_extension_command_async(
        ["trust", "add", key_id, "--public-key", pub_b64, "--yes"], input_fn=_yes
    )
    assert rc == 0
    rc = await run_extension_command_async(["trust", "remove", key_id])
    assert rc == 0


def test_cli_sign_verb(tmp_path: Path) -> None:
    key_id, _, _ = _make_key(tmp_path)
    art = _make_artifact(tmp_path)
    rc = run_extension_command(
        ["sign", str(art), "--key", key_id, "--name", "acme-ext", "--version", "1.0"]
    )
    assert rc == 0 and es.aelixsig_path_for(art).is_file()


# === install gate integration ============================================


def test_install_require_signature_trusted_records_provenance_pin(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 0 and len(runner.calls) == 1
    pin = ep.load_pins(ep.pins_file_path(_agent_dir(tmp_path)))[str(art.resolve())]
    assert pin.key_id == key_id and pin.sig and pin.sha256_statement


def test_install_require_signature_untrusted_refuses_and_skips_pip(tmp_path: Path) -> None:
    _, _, pem = _make_key(tmp_path)  # not trusted
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 2 and runner.calls == []


def test_install_invalid_sig_trusted_key_fail_closed_by_default(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")
    art.write_bytes(b"TAMPERED after signing")  # statement no longer matches
    runner = _FakeRunner()
    # DEFAULT path (no --require-signature) still refuses — tampering evidence.
    rc = install_extension(str(art), yes=True, runner=runner, input_fn=_yes)
    assert rc == 2 and runner.calls == []


def test_install_no_verify_plus_require_signature_hard_error(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, no_verify=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 2 and runner.calls == []


def test_install_unsigned_default_tofi_still_installs(tmp_path: Path) -> None:
    art = _make_artifact(tmp_path, "plain-2.0-py3-none-any.whl")  # no sidecar
    runner = _FakeRunner()
    rc = install_extension(str(art), yes=True, runner=runner, input_fn=_yes)
    assert rc == 0 and len(runner.calls) == 1


def test_install_first_party_key_verifies_out_of_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    monkeypatch.setattr(es, "FIRST_PARTY_KEYS", {key_id: pub_b64})
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")
    runner = _FakeRunner()
    # EMPTY user trust store — only the first-party constant vouches.
    rc = install_extension(
        str(art), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 0 and len(runner.calls) == 1


def test_install_require_signature_on_directory_refuses(tmp_path: Path) -> None:
    src = tmp_path / "srctree"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    runner = _FakeRunner()
    rc = install_extension(
        str(src), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 2 and runner.calls == []


def test_install_require_signature_on_git_refuses(tmp_path: Path) -> None:
    runner = _FakeRunner()
    rc = install_extension(
        "git+https://example.com/x.git@" + "a" * 40,
        yes=True, require_signature=True, runner=runner, input_fn=_yes,
    )
    assert rc == 2 and runner.calls == []


# === dependency guarantee =================================================


def test_cryptography_importable_from_package_boundary() -> None:
    # Enforces the direct-dependency promotion (#67) independently of google-auth:
    # the signing path imports Ed25519 through the coding-agent package.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    assert Ed25519PrivateKey.generate() is not None


# === review-driven: fail-closed on internal error + hostile sidecar ========


class _DownloadRunner:
    """Fakes `pip download` by writing a wheel into --dest; records every argv."""

    def __init__(self, wheel_name: str, wheel_bytes: bytes) -> None:
        self.wheel_name = wheel_name
        self.wheel_bytes = wheel_bytes
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if "download" in argv and "--dest" in argv:
            dest = Path(argv[argv.index("--dest") + 1])
            (dest / self.wheel_name).write_bytes(self.wheel_bytes)
        return subprocess.CompletedProcess(args=argv, returncode=0)


def test_require_signature_fails_closed_on_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # HIGH regression: an internal verify error (not a VerifyRefusal) must NOT silently
    # install unsigned when a signature is REQUIRED — the generic handler now honors
    # require_signature, not just strict.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    es.sign_artifact(art, es.load_private_key(pem), kind="path")

    def _boom(_p: Path) -> str:
        raise OSError("simulated hashing failure")

    monkeypatch.setattr(ep, "sha256_file", _boom)
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 2 and runner.calls == []  # fail CLOSED
    # DEFAULT path (no required signature) keeps the shipped #64 behavior: install unpinned.
    runner2 = _FakeRunner()
    rc = install_extension(str(art), yes=True, runner=runner2, input_fn=_yes)
    assert rc == 0 and len(runner2.calls) == 1


def test_deep_nested_aelixsig_require_signature_refuses(tmp_path: Path) -> None:
    # HIGH regression: a maliciously DEEP JSON .aelixsig makes json.loads raise
    # RecursionError (not a ValueError). read_aelixsig now absorbs it → treated as no
    # signature → --require-signature refuses (fail-closed), never an uncaught traceback.
    art = _make_artifact(tmp_path)
    es.aelixsig_path_for(art).write_text("[" * 60000 + "]" * 60000, encoding="utf-8")
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, require_signature=True, runner=runner, input_fn=_yes
    )
    assert rc == 2 and runner.calls == []
    # And on the DEFAULT path it degrades to an unsigned TOFI install (not a crash).
    runner2 = _FakeRunner()
    rc = install_extension(str(art), yes=True, runner=runner2, input_fn=_yes)
    assert rc == 0 and len(runner2.calls) == 1


def test_gate_malformed_sidecar_cells(tmp_path: Path) -> None:
    # A present .aelixsig whose JSON omits 'sig' (structurally malformed): default
    # degrades to unsigned WITH a notice (not silent, not authenticated); --require
    # refuses.
    art = _make_artifact(tmp_path)
    sidecar = es.aelixsig_path_for(art)
    sidecar.write_text(json.dumps({"aelixsig": 1, "keyId": "abc"}), encoding="utf-8")
    out = _gate(tmp_path, art, sidecar, ep.sha256_file(art))
    assert not out.authenticated and out.notice and "malformed" in out.notice
    with pytest.raises(ep.VerifyRefusal, match="malformed"):
        _gate(tmp_path, art, sidecar, ep.sha256_file(art), require_signature=True)


def test_gate_corrupt_sidecar_emits_notice(tmp_path: Path) -> None:
    # A present-but-unparseable .aelixsig is a visible signal on the default path
    # (distinct from a truly-absent sidecar, which stays silent).
    art = _make_artifact(tmp_path)
    sidecar = es.aelixsig_path_for(art)
    sidecar.write_text("{ not valid json", encoding="utf-8")
    out = _gate(tmp_path, art, sidecar, ep.sha256_file(art))
    assert not out.authenticated and out.notice and "corrupt" in out.notice
    # A truly-absent sidecar → silent (no notice).
    missing = _make_artifact(tmp_path, "plain-1.0-py3-none-any.whl")
    out2 = _gate(tmp_path, missing, es.aelixsig_path_for(missing), ep.sha256_file(missing))
    assert out2.notice is None


def test_install_pypi_require_signature_end_to_end(tmp_path: Path) -> None:
    # MEDIUM gap: the full pypi provenance flow (download → hash → gate → --no-index
    # rewrite → provenance pin) driven end-to-end with a signature over the exact
    # downloaded bytes.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    wheel_name = "acme_ext-1.0-py3-none-any.whl"
    wheel_bytes = b"the exact acme_ext 1.0 wheel bytes"
    tmp_wheel = tmp_path / wheel_name
    tmp_wheel.write_bytes(wheel_bytes)
    sidecar = tmp_path / "oob.aelixsig"
    es.sign_artifact(
        tmp_wheel, es.load_private_key(pem), kind="pypi", name="acme-ext", version="1.0",
        out=sidecar,
    )
    runner = _DownloadRunner(wheel_name, wheel_bytes)
    rc = install_extension(
        "acme-ext==1.0", yes=True, verify_pypi=True, require_signature=True,
        signature_path=str(sidecar), runner=runner, input_fn=_yes,
    )
    assert rc == 0
    assert any("download" in c for c in runner.calls)
    assert any("--no-index" in c for c in runner.calls)  # installs the verified bytes
    pin = ep.load_pins(ep.pins_file_path(_agent_dir(tmp_path)))["acme-ext"]
    assert pin.key_id == key_id and pin.version == "1.0" and pin.sig


def test_install_pypi_signature_version_mismatch_refuses(tmp_path: Path) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    wheel_name = "acme_ext-1.0-py3-none-any.whl"
    wheel_bytes = b"acme_ext bytes v1"
    tmp_wheel = tmp_path / wheel_name
    tmp_wheel.write_bytes(wheel_bytes)
    sidecar = tmp_path / "oob.aelixsig"
    # Sign as version 2.0 but the downloaded wheel is 1.0 → statement mismatch → refuse.
    es.sign_artifact(
        tmp_wheel, es.load_private_key(pem), kind="pypi", name="acme-ext", version="2.0",
        out=sidecar,
    )
    runner = _DownloadRunner(wheel_name, wheel_bytes)
    rc = install_extension(
        "acme-ext==1.0", yes=True, verify_pypi=True, require_signature=True,
        signature_path=str(sidecar), runner=runner, input_fn=_yes,
    )
    assert rc == 2
    assert not any("--no-index" in c for c in runner.calls)  # pip install never ran


def test_install_signature_out_of_band_path(tmp_path: Path) -> None:
    # --signature overrides the sibling sidecar location for a path install.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    art = _make_artifact(tmp_path)
    oob = tmp_path / "detached" / "art.aelixsig"
    oob.parent.mkdir()
    es.sign_artifact(art, es.load_private_key(pem), kind="path", out=oob)
    assert not es.aelixsig_path_for(art).exists()  # no sibling sidecar
    runner = _FakeRunner()
    rc = install_extension(
        str(art), yes=True, require_signature=True, signature_path=str(oob),
        runner=runner, input_fn=_yes,
    )
    assert rc == 0 and len(runner.calls) == 1


def test_parse_install_flags_maps_signature_and_trusted_key() -> None:
    from aelix_coding_agent.cli.extension_install import _parse_install_flags

    parsed = _parse_install_flags(
        ["--require-signature", "--trusted-key", "abc", "--signature", "/s.aelixsig", "pkg"]
    )
    assert not isinstance(parsed, int)
    assert parsed.require_signature and parsed.trusted_key == "abc"
    assert parsed.signature_path == "/s.aelixsig" and parsed.target == "pkg"
    # '=' forms too.
    parsed2 = _parse_install_flags(["--trusted-key=x", "--signature=/y", "pkg"])
    assert not isinstance(parsed2, int)
    assert parsed2.trusted_key == "x" and parsed2.signature_path == "/y"
    # empty value is rejected (both forms).
    assert _parse_install_flags(["--signature=", "pkg"]) == 2
    assert _parse_install_flags(["--trusted-key", "", "pkg"]) == 2


async def test_update_require_signature_refuses_unsigned(tmp_path: Path) -> None:
    # The update path threads --require-signature through its own parser + _VerifyOpts.
    # Async so set_extension_sources' scheduled write has a running loop (sync-context
    # would raise 'no current event loop'); call the async dispatch directly (no nested
    # asyncio.run).
    art = _make_artifact(tmp_path)  # recorded but UNSIGNED
    settings = SettingsManager.in_memory()
    settings.set_extension_sources(
        [ExtensionSourceObject(spec=str(art.resolve()), kind="path", name="acme")]
    )
    await settings.flush()
    runner = _FakeRunner()
    rc = await run_extension_command_async(
        ["update", "acme", "--require-signature"],
        settings=settings, runner=runner, input_fn=_yes,
    )
    assert rc == 2 and runner.calls == []
    # '=' form empty value is rejected on the update parser too.
    rc = await run_extension_command_async(["update", "acme", "--signature="], settings=settings)
    assert rc == 2


def test_cli_keygen_passphrase_round_trip(tmp_path: Path) -> None:
    key_id, _, pem = es.keygen(_agent_dir(tmp_path), passphrase=b"s3cret")
    # Loads with the right passphrase → same keyId; wrong/None passphrase raises.
    priv = es.load_private_key(pem, passphrase=b"s3cret")
    assert es.key_id_for(es._public_raw(priv)) == key_id
    with pytest.raises((TypeError, ValueError)):
        es.load_private_key(pem, passphrase=None)


def test_cli_sign_kind_pypi_pem_key_and_invalid_kind(tmp_path: Path) -> None:
    _, _, pem = _make_key(tmp_path)
    art = _make_artifact(tmp_path, "acme_ext-1.0.tar.gz")
    # --kind pypi + --key as a PEM PATH (not a keyId) → sidecar statement kind == pypi.
    rc = run_extension_command(
        ["sign", str(art), "--key", str(pem), "--name", "acme-ext",
         "--version", "1.0", "--kind", "pypi"]
    )
    assert rc == 0
    raw = es.read_aelixsig(es.aelixsig_path_for(art))
    assert raw is not None
    assert cast("dict[str, object]", raw["statement"])["kind"] == "pypi"
    # An invalid --kind is rejected (exit 2).
    assert run_extension_command(["sign", str(art), "--key", str(pem), "--kind", "bogus"]) == 2


def test_cli_trust_list_first_party_and_revoked_and_eq_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_id, pub_b64, _ = _make_key(tmp_path)
    # '=' form of trust add.
    assert run_extension_command(
        ["trust", "add", key_id, f"--public-key={pub_b64}", "--label=acme"], input_fn=_yes
    ) == 0
    # A first-party key + a revocation both render in `trust list`.
    monkeypatch.setattr(es, "FIRST_PARTY_KEYS", {"feedface0000feed": "AAAA"})
    run_extension_command(["trust", "revoke", "feedface0000feed"])
    capsys.readouterr()
    run_extension_command(["trust", "list"])
    out = capsys.readouterr().out
    assert "[first-party]" in out and "(REVOKED)" in out and key_id in out


# === Track D: verify_signed_document (catalog document verification) ========

_DOC = b'{"catalog":[{"name":"acme","version":"1.0"}]}'


def _sign_doc(tmp_path: Path, document: bytes = _DOC, *, kind: str = "catalog") -> bytes:
    """Produce a `.aelixsig` envelope over `document` using an isolated fresh key."""

    _, _, pem = _make_key(tmp_path)
    return es.sign_document(document, es.load_private_key(pem), kind=kind)


def _trusted_sign_doc(
    tmp_path: Path, document: bytes = _DOC, *, kind: str = "catalog"
) -> tuple[str, bytes]:
    """Sign `document` with a key that is also ADDED to the trust store; return (keyId, sidecar)."""

    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    return key_id, es.sign_document(document, es.load_private_key(pem), kind=kind)


def _verify_doc(
    tmp_path: Path,
    document: bytes,
    sidecar: bytes | None,
    *,
    require_signature: bool = False,
    kind: str = "catalog",
    trusted_key: str | None = None,
) -> es.SignatureOutcome:
    return es.verify_signed_document(
        document, sidecar, kind=kind, agent_dir=_agent_dir(tmp_path),
        require_signature=require_signature, trusted_key=trusted_key, identity="https://ex/catalog.json",
    )


def test_parse_aelixsig_bytes_leniency() -> None:
    # A well-formed object parses; every hostile/malformed shape degrades to None (never
    # raises), reusing read_aelixsig leniency INCLUDING the RecursionError deep-JSON guard.
    assert es._parse_aelixsig_bytes(b'{"a": 1}') == {"a": 1}
    assert es._parse_aelixsig_bytes(b"[1, 2, 3]") is None  # non-object
    assert es._parse_aelixsig_bytes(b"{ not json") is None  # bad JSON
    deep = (b"[" * 60000) + (b"]" * 60000)  # maliciously deep → json raises RecursionError
    assert es._parse_aelixsig_bytes(deep) is None


def test_sign_document_binds_kind_and_document_digest(tmp_path: Path) -> None:
    # The publisher path stamps kind='catalog' by default and binds sha256 over the RAW
    # document bytes (independent of any catalog field).
    _, _, pem = _make_key(tmp_path)
    sidecar = es.sign_document(_DOC, es.load_private_key(pem))
    env = json.loads(sidecar)
    st = env["statement"]
    assert st["kind"] == "catalog"
    assert st["sha256"] == hashlib.sha256(_DOC).hexdigest()


def test_verify_document_round_trip_authenticates(tmp_path: Path) -> None:
    key_id, sidecar = _trusted_sign_doc(tmp_path)
    out = _verify_doc(tmp_path, _DOC, sidecar)
    assert out.authenticated and out.key_id == key_id and out.sig and out.statement_json
    st = json.loads(out.statement_json)
    assert st["kind"] == "catalog" and st["sha256"] == hashlib.sha256(_DOC).hexdigest()


def test_verify_document_one_byte_tamper_refuses_even_default(tmp_path: Path) -> None:
    # Affirmative tampering evidence: a signature by a TRUSTED key whose digest no longer
    # matches the presented bytes refuses ALWAYS — even on the beta default path.
    _key_id, sidecar = _trusted_sign_doc(tmp_path)
    tampered = _DOC + b" "  # one appended byte → different sha256
    with pytest.raises(ep.VerifyRefusal, match="statement mismatch"):
        _verify_doc(tmp_path, tampered, sidecar, require_signature=False)


def test_verify_document_kind_mismatch_refuses(tmp_path: Path) -> None:
    # A pack signature (kind='pack') presented as a catalog is rejected by the kind
    # cross-check — a valid signature over the WRONG kind cannot be replayed.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    sidecar = es.sign_document(_DOC, es.load_private_key(pem), kind="pack")
    with pytest.raises(ep.VerifyRefusal, match="statement mismatch"):
        _verify_doc(tmp_path, _DOC, sidecar)  # verified with default kind='catalog'


def test_verify_document_trusted_invalid_sig_refuses(tmp_path: Path) -> None:
    # A trusted keyId whose signature does not verify → refuse ALWAYS (tampering evidence).
    _key_id, sidecar = _trusted_sign_doc(tmp_path)
    env = json.loads(sidecar)
    env["sig"] = es._b64e(b"\x00" * 64)  # a wrong (but well-formed) signature
    with pytest.raises(ep.VerifyRefusal, match="FAILED to verify"):
        _verify_doc(tmp_path, _DOC, json.dumps(env).encode("utf-8"))


def test_verify_document_untrusted_key_notice_default_raise_require(tmp_path: Path) -> None:
    sidecar = _sign_doc(tmp_path)  # signer NOT added to the trust store
    out = _verify_doc(tmp_path, _DOC, sidecar)
    assert not out.authenticated and out.notice and "untrusted key" in out.notice
    with pytest.raises(ep.VerifyRefusal, match="untrusted key"):
        _verify_doc(tmp_path, _DOC, sidecar, require_signature=True)


def test_verify_document_trusted_key_restriction(tmp_path: Path) -> None:
    key_id, sidecar = _trusted_sign_doc(tmp_path)
    # A non-matching required keyId → the (otherwise trusted) signature is refused.
    with pytest.raises(ep.VerifyRefusal, match="untrusted key"):
        _verify_doc(tmp_path, _DOC, sidecar, require_signature=True, trusted_key="0" * 16)
    # The matching keyId → authenticates.
    assert _verify_doc(tmp_path, _DOC, sidecar, trusted_key=key_id).authenticated


def test_verify_document_absent_sidecar_silent_vs_require(tmp_path: Path) -> None:
    # sidecar is None = truly absent → silent unsigned on default; refuse under require.
    out = _verify_doc(tmp_path, _DOC, None)
    assert not out.authenticated and out.notice is None
    with pytest.raises(ep.VerifyRefusal, match="no valid .aelixsig"):
        _verify_doc(tmp_path, _DOC, None, require_signature=True)


def test_verify_document_malformed_fields_sidecar(tmp_path: Path) -> None:
    # Valid JSON object but missing statement/sig → malformed: notice on default, refuse
    # under require.
    bad = json.dumps({"aelixsig": 1, "keyId": "abc"}).encode("utf-8")
    out = _verify_doc(tmp_path, _DOC, bad)
    assert not out.authenticated and out.notice and "malformed" in out.notice
    with pytest.raises(ep.VerifyRefusal, match="malformed"):
        _verify_doc(tmp_path, _DOC, bad, require_signature=True)


def test_verify_document_corrupt_and_deep_json_sidecar_degrade(tmp_path: Path) -> None:
    # Present-but-unparseable bytes (bad JSON OR maliciously deep JSON) degrade to a
    # visible "corrupt" notice on default (never an escaping traceback); refuse under
    # require. This is the hostile-sidecar leniency, bytes edition.
    out = _verify_doc(tmp_path, _DOC, b"{ not valid json")
    assert not out.authenticated and out.notice and "corrupt" in out.notice
    deep = (b"[" * 60000) + (b"]" * 60000)
    out2 = _verify_doc(tmp_path, _DOC, deep)
    assert not out2.authenticated and out2.notice and "corrupt" in out2.notice
    with pytest.raises(ep.VerifyRefusal, match="no valid .aelixsig"):
        _verify_doc(tmp_path, _DOC, deep, require_signature=True)


def test_verify_document_first_party_and_revoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id, pub_b64, pem = _make_key(tmp_path)
    monkeypatch.setattr(es, "FIRST_PARTY_KEYS", {key_id: pub_b64})
    sidecar = es.sign_document(_DOC, es.load_private_key(pem))
    # First-party → authenticates out of the box (empty user trust store).
    assert _verify_doc(tmp_path, _DOC, sidecar).authenticated
    # Revoke → revocation wins even over first-party, so require now refuses.
    es.save_trusted_keys(
        es.TrustStore(revoked=(key_id,)), es.trusted_keys_path(_agent_dir(tmp_path))
    )
    with pytest.raises(ep.VerifyRefusal):
        _verify_doc(tmp_path, _DOC, sidecar, require_signature=True)


# === Issue #88: `sign --kind catalog` ↔ catalog verifier round-trip ==========
#
# The catalog verifier (`_make_catalog_verifier` → verify_signed_document, kind='catalog')
# demands a kind='catalog' statement. Before #88 the `sign` verb only did path|pypi, so a
# catalog signed as kind='path' was REFUSED on a statement mismatch. These drive the new
# `_cmd_sign` catalog branch straight through the real catalog verifier via `_verify_doc`.

_CATALOG = _DOC  # a catalog.json document (`{"catalog":[…]}`) reused as the signed bytes


def test_cmd_sign_catalog_round_trip_authenticates(tmp_path: Path) -> None:
    # e2e: `sign --kind catalog` writes a kind='catalog' envelope over the RAW catalog
    # bytes that the catalog verifier accepts once the signer is trusted.
    key_id, pub_b64, pem = _make_key(tmp_path)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_CATALOG)
    rc = _cmd_sign([str(catalog), "--key", str(pem), "--kind", "catalog"])
    assert rc == 0
    sidecar_path = catalog.with_name(catalog.name + es.AELIXSIG_SUFFIX)
    assert sidecar_path.is_file()  # sibling catalog.json.aelixsig
    _trust(tmp_path, key_id, pub_b64)
    out = _verify_doc(tmp_path, catalog.read_bytes(), sidecar_path.read_bytes())
    assert out.authenticated and out.key_id == key_id and out.sig and out.statement_json
    st = json.loads(out.statement_json)
    assert st["kind"] == "catalog"
    assert st["sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()


def test_cmd_sign_catalog_tamper_refuses(tmp_path: Path) -> None:
    # Appending a byte to the catalog after signing → the digest no longer matches the
    # signed statement → the (trusted-key) verify refuses even on the default path.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_CATALOG)
    assert _cmd_sign([str(catalog), "--key", str(pem), "--kind", "catalog"]) == 0
    sidecar = catalog.with_name(catalog.name + es.AELIXSIG_SUFFIX).read_bytes()
    tampered = _CATALOG + b" "  # one appended byte → a different sha256
    with pytest.raises(ep.VerifyRefusal, match="statement mismatch"):
        _verify_doc(tmp_path, tampered, sidecar)


def test_cmd_sign_kind_path_catalog_verify_refuses(tmp_path: Path) -> None:
    # REGRESSION guard — documents exactly why kind='catalog' is required. Signing the
    # SAME catalog with `--kind path` routes to sign_artifact, which stamps kind='path';
    # catalog-verifying it (kind='catalog') is refused on the kind cross-check. This is the
    # statement mismatch #88 closes by adding the kind='catalog' sign path.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_CATALOG)
    rc = _cmd_sign([str(catalog), "--key", str(pem), "--kind", "path"])
    assert rc == 0
    sidecar = catalog.with_name(catalog.name + es.AELIXSIG_SUFFIX).read_bytes()
    with pytest.raises(ep.VerifyRefusal, match="statement mismatch"):
        _verify_doc(tmp_path, catalog.read_bytes(), sidecar)  # default kind='catalog'


def test_cmd_sign_catalog_out_path_honored(tmp_path: Path) -> None:
    # A custom --out path receives the envelope (no sibling sidecar is written), and it
    # verifies just like the default location.
    key_id, pub_b64, pem = _make_key(tmp_path)
    _trust(tmp_path, key_id, pub_b64)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_CATALOG)
    out = tmp_path / "detached" / "catalog.aelixsig"
    out.parent.mkdir()
    rc = _cmd_sign(
        [str(catalog), "--key", str(pem), "--kind", "catalog", "--out", str(out)]
    )
    assert rc == 0
    assert out.is_file()
    assert not catalog.with_name(catalog.name + es.AELIXSIG_SUFFIX).exists()  # no sibling
    result = _verify_doc(tmp_path, catalog.read_bytes(), out.read_bytes())
    assert result.authenticated and result.key_id == key_id


def test_cmd_sign_rejects_unknown_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The kind validation now names 'catalog' too: an unknown kind is rejected (exit 2)
    # with the updated message.
    _, _, pem = _make_key(tmp_path)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_CATALOG)
    rc = _cmd_sign([str(catalog), "--key", str(pem), "--kind", "bogus"])
    assert rc == 2
    assert "must be 'path', 'pypi', or 'catalog'" in capsys.readouterr().err
