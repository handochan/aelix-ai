"""PKCE generator — Sprint 6c · Phase 4.3 · §C.

Pi parity: ``packages/ai/src/utils/oauth/pkce.ts`` (SHA 734e08e).

IMPORTANT — Pi hashes the **base64url-encoded verifier string** (not
the raw 32 verifier bytes). Read Pi ``pkce.ts:23-31`` carefully:

.. code-block:: javascript

    const verifierBytes = new Uint8Array(32);
    crypto.getRandomValues(verifierBytes);
    const verifier = base64urlEncode(verifierBytes);  // string
    // ...
    const data = encoder.encode(verifier);  // bytes of the b64url STRING
    const hashBuffer = await crypto.subtle.digest("SHA-256", data);
    const challenge = base64urlEncode(new Uint8Array(hashBuffer));

The Python port mirrors this exactly — we encode the verifier string
back to bytes (``verifier.encode("ascii")``) before hashing.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def _base64url(data: bytes) -> str:
    """Encode bytes as ``base64url`` (RFC 4648 §5) — no padding.

    Pi parity: ``pkce.ts:9-15`` ``base64urlEncode``. Strips trailing
    ``=`` padding and substitutes the URL-safe alphabet.
    """

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` per RFC 7636.

    Pi parity: ``pkce.ts:21-34`` ``generatePKCE``. ``challenge_method``
    is fixed to ``"S256"`` so callers don't need a separate parameter
    (Pi hard-codes the same).
    """

    verifier_bytes = secrets.token_bytes(32)
    verifier = _base64url(verifier_bytes)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


__all__ = ["_base64url", "generate_pkce"]
