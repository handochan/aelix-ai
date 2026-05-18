"""Sprint 6c · Phase 4.3 — PKCE generator tests."""

from __future__ import annotations

import hashlib
import re
from unittest.mock import patch

from aelix_ai.oauth._pkce import _base64url, generate_pkce

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def test_base64url_no_padding() -> None:
    """``_base64url`` strips trailing ``=`` padding."""

    assert _base64url(b"") == ""
    assert _base64url(b"f") == "Zg"  # would be "Zg==" with padding
    assert "=" not in _base64url(b"\x00" * 32)


def test_base64url_charset() -> None:
    """Only URL-safe characters appear in the output."""

    out = _base64url(bytes(range(256)))
    assert _BASE64URL_RE.match(out)


def test_generate_pkce_returns_strings() -> None:
    v, c = generate_pkce()
    assert isinstance(v, str)
    assert isinstance(c, str)


def test_generate_pkce_verifier_length() -> None:
    """32 random bytes → 43-char base64url (no padding)."""

    v, _ = generate_pkce()
    assert len(v) == 43


def test_generate_pkce_challenge_length() -> None:
    """SHA-256 → 32 bytes → 43-char base64url (no padding)."""

    _, c = generate_pkce()
    assert len(c) == 43


def test_generate_pkce_charset() -> None:
    v, c = generate_pkce()
    assert _BASE64URL_RE.match(v)
    assert _BASE64URL_RE.match(c)


def test_generate_pkce_challenge_is_sha256_of_verifier_string() -> None:
    """Pi parity (pkce.ts:23-31): challenge = base64url(SHA256(verifier as string))."""

    with patch(
        "aelix_ai.oauth._pkce.secrets.token_bytes",
        return_value=b"\x00" * 32,
    ):
        v, c = generate_pkce()
    # The verifier is the base64url-encoding of 32 zero bytes.
    expected_v = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert v == expected_v
    # Challenge is SHA256 of the verifier STRING (Pi parity).
    expected_c = _base64url(hashlib.sha256(expected_v.encode("ascii")).digest())
    assert c == expected_c


def test_generate_pkce_deterministic_for_fixed_input() -> None:
    """Same verifier bytes → same challenge."""

    with patch(
        "aelix_ai.oauth._pkce.secrets.token_bytes",
        return_value=b"\x01" * 32,
    ):
        v1, c1 = generate_pkce()
        v2, c2 = generate_pkce()
    assert v1 == v2
    assert c1 == c2


def test_generate_pkce_different_each_call_under_real_random() -> None:
    """Without patching, two calls produce distinct verifiers."""

    v1, _ = generate_pkce()
    v2, _ = generate_pkce()
    assert v1 != v2
