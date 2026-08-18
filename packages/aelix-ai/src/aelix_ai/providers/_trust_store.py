"""What this process actually trusts for TLS, and why — the #99 follow-up.

``_error_hints._os_trust_store_active`` answers the BINARY question by reading
the live ``ssl.SSLContext`` binding, and its docstring gives the reason that
rules this module too: *a flag set at startup can lie; class identity cannot.*
So nothing here reports "the OS store is active" from a record. The binding says
active or not; the record is consulted only for the part the binding cannot
know — **why** it is not active.

WHY THIS EXISTS. ``cli/entry.py``'s injection was best-effort with a bare
``except Exception: pass``. That is correct as a launch policy and wrong as a
diagnostic: it made "truststore is doing its job" and "truststore raised on
import and we swallowed it" produce identical observable state. A user on a
corporate network then sees only an opaque TLS failure at their first model
request, with no way — and no way for us — to tell which half they are in. The
reporter that prompted this could not be reproduced for exactly that reason.

Three states, and the middle one is the one the silent ``pass`` erased:

``active``
    The binding is truststore's. The OS store is in play.
``degraded``
    Injection ran and FAILED. :data:`_injection_error` names the exception.
    certifi-only — a system-wide corporate CA is invisible.
``not-attempted``
    Nobody injected. The normal state for an embedder importing this library,
    and for any entry point that is not ``main_sync``. NOT a defect.

WHY ``ca_count`` IS OFTEN ``None`` AND NEVER ``0``. Truststore's context raises
``NotImplementedError`` from ``get_ca_certs()`` — measured, on both a system
CPython 3.12 and a uv-managed 3.13. Reporting that as ``0`` would say "you trust
nothing" when the truth is "this context does not answer that question", and a
zero that means *the detector did not fire* is the failure mode this repo has
already shipped once. So the count is ``None`` plus
:attr:`TrustStoreReport.ca_count_unavailable` naming which it is.

WHAT ACTUALLY DECIDES VERIFICATION, and why the paths below are the useful part:
OpenSSL resolves a ``cafile`` and a ``capath`` at build time (overridable by
``SSL_CERT_FILE`` / ``SSL_CERT_DIR``). Measured across the two install methods
this project ships::

    uv sync,  system CPython 3.12   cafile=/usr/lib/ssl/cert.pem  EXISTS
    uv tool install, uv CPython 3.13 cafile=/etc/ssl/cert.pem      MISSING

``install.sh`` installs with ``uv tool install``, so the second row is what every
non-contributor gets. It is not fatal on Debian/Ubuntu — ``capath`` still holds
the hashed symlinks and truststore's OpenSSL backend falls back to a candidate
list — but "``capath`` exists" is NOT the same test as "``capath`` is usable",
which is why :attr:`TrustStoreReport.capath_has_hashed_certs` is measured
separately rather than inferred from the directory existing.
"""

from __future__ import annotations

import os
import ssl
import sys
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.providers._error_hints import _os_trust_store_active

#: Set by :func:`record_injection`. Read ONLY to explain a non-active binding —
#: never to claim an active one. Module-level because the injection is a
#: process-global act and this is its process-global receipt.
_injection_attempted: bool = False
_injection_error: str | None = None


def record_injection(error: BaseException | None) -> None:
    """Record the outcome of the one ``truststore.inject_into_ssl()`` call.

    Called by ``cli/entry.py::_inject_truststore``. ``error=None`` means the call
    returned; anything else is the exception it swallowed so launch could
    continue. This must never raise — it runs on the launch path, before
    anything that could report a failure here.
    """

    global _injection_attempted, _injection_error
    _injection_attempted = True
    _injection_error = None if error is None else f"{type(error).__name__}: {error}"


def reset_injection_record() -> None:
    """Forget the receipt. For tests, which must not inherit a prior module state."""

    global _injection_attempted, _injection_error
    _injection_attempted = False
    _injection_error = None


@dataclass(frozen=True)
class TrustStoreReport:
    """Measured TLS trust state. Every field is read at construction time."""

    state: str
    """``active`` | ``degraded`` | ``not-attempted``. From the LIVE binding."""

    reason: str | None
    """Why ``state`` is not ``active``. ``None`` when it is."""

    ssl_context_class: str
    """The class ``ssl.SSLContext`` is bound to right now, module-qualified."""

    backend: str | None
    """Truststore's selected platform backend, read off the function it imported."""

    truststore_version: str | None
    python_version: str
    python_is_uv_managed: bool
    openssl_version: str

    cafile: str | None
    cafile_exists: bool
    capath: str | None
    capath_exists: bool
    capath_has_hashed_certs: bool

    env_overrides: dict[str, str] = field(default_factory=dict)

    certifi_path: str | None = None
    certifi_exists: bool = False

    ca_count: int | None = None
    ca_count_unavailable: str | None = None

    strict_verification: bool = False
    """``VERIFY_X509_STRICT`` — on by default from Python 3.13, off before it.

    Reported because it is the difference between "your CA is missing" and "your
    CA is fine and only this interpreter objects", and those need opposite
    actions. A real report turned on exactly this bit.
    """

    strict_relaxed_for: str | None = None
    """The host whose measured strict-only rejection relaxed strict this session."""

    strict_relaxed_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """A plain JSON-safe mapping, for ``aelix status --json``."""

        return {
            "state": self.state,
            "reason": self.reason,
            "ssl_context_class": self.ssl_context_class,
            "backend": self.backend,
            "truststore_version": self.truststore_version,
            "python_version": self.python_version,
            "python_is_uv_managed": self.python_is_uv_managed,
            "openssl_version": self.openssl_version,
            "cafile": self.cafile,
            "cafile_exists": self.cafile_exists,
            "capath": self.capath,
            "capath_exists": self.capath_exists,
            "capath_has_hashed_certs": self.capath_has_hashed_certs,
            "env_overrides": dict(self.env_overrides),
            "certifi_path": self.certifi_path,
            "certifi_exists": self.certifi_exists,
            "ca_count": self.ca_count,
            "ca_count_unavailable": self.ca_count_unavailable,
            "strict_verification": self.strict_verification,
            "strict_relaxed_for": self.strict_relaxed_for,
            "strict_relaxed_reason": self.strict_relaxed_reason,
        }


def _truststore_backend() -> tuple[str | None, str | None]:
    """``(backend, version)`` read off the imported truststore, not re-derived.

    Truststore picks its backend at import with a ``platform.system()`` ladder
    and binds ``_configure_context`` from ``_windows`` / ``_macos`` / ``_openssl``.
    Re-running that ladder here would be a second opinion that can disagree with
    the one the process is actually using, so this follows the binding instead —
    the same rule ``_os_trust_store_active`` follows for the class.
    """

    module = sys.modules.get("truststore")
    if module is None:
        return None, None
    version = getattr(module, "__version__", None)
    api = sys.modules.get("truststore._api")
    configure = getattr(api, "_configure_context", None)
    backend_module = getattr(configure, "__module__", None)
    backend = backend_module.rsplit(".", 1)[-1].lstrip("_") if backend_module else None
    return backend, version


def _capath_has_hashed_certs(capath: str | None) -> bool:
    """Does ``capath`` hold OpenSSL's ``<8-hex>.<n>`` lookup names?

    This is the test truststore's OpenSSL backend itself applies, and it is a
    strictly stronger question than ``isdir()``: OpenSSL looks certificates up in
    a capath BY HASHED FILENAME, so a directory full of ``.pem`` files with no
    ``c_rehash`` symlinks verifies nothing while passing an existence check.
    """

    if not capath or not os.path.isdir(capath):
        return False
    try:
        names = os.listdir(capath)
    except OSError:
        return False
    for name in names:
        stem, _, suffix = name.partition(".")
        if len(stem) == 8 and suffix.isdigit():
            try:
                int(stem, 16)
            except ValueError:
                continue
            return True
    return False


def _ca_count() -> tuple[int | None, str | None]:
    """``(count, unavailable_reason)`` — never a misleading ``0``.

    A truststore context refuses ``get_ca_certs()`` outright (measured:
    ``NotImplementedError`` with an empty message). That refusal is information —
    it means the OS store is answering, and the OS store does not enumerate — so
    it is reported as such rather than flattened into a number.
    """

    try:
        context = ssl.create_default_context()
    except Exception as exc:  # noqa: BLE001 — a diagnostic must not raise
        return None, f"could not build a default SSL context ({type(exc).__name__})"
    try:
        return len(context.get_ca_certs()), None
    except NotImplementedError:
        return None, "the active context does not enumerate its CAs (OS store)"
    except Exception as exc:  # noqa: BLE001
        return None, f"get_ca_certs() failed ({type(exc).__name__})"


def _certifi() -> tuple[str | None, bool]:
    try:
        import certifi
    except Exception:  # noqa: BLE001 — certifi is a transitive dep, not a promise
        return None, False
    try:
        path = certifi.where()
    except Exception:  # noqa: BLE001
        return None, False
    return path, os.path.exists(path)


def describe_trust_store() -> TrustStoreReport:
    """Measure TLS trust state now. Pure inspection — injects nothing.

    Safe to call from anywhere, including a status command that must not alter
    the process it is describing.
    """

    active = _os_trust_store_active()
    if active:
        state, reason = "active", None
    elif _injection_attempted:
        state = "degraded"
        reason = (
            f"truststore injection failed and was swallowed so launch could "
            f"continue — {_injection_error}. TLS verifies against certifi only, "
            f"so a CA installed system-wide (e.g. a corporate root) is invisible."
        )
    else:
        state = "not-attempted"
        reason = (
            "nothing injected truststore in this process — the normal state for "
            "an embedder, and for any entry point other than the `aelix` CLI. "
            "TLS verifies against whatever OpenSSL resolves by default."
        )

    backend, version = _truststore_backend()
    paths = ssl.get_default_verify_paths()
    ca_count, ca_unavailable = _ca_count()

    # Read live, like everything else here: `strict_is_enabled` builds a fresh
    # context, and `session_relaxation` reports only a relaxation that was
    # actually measured and installed.
    from aelix_ai.providers._tls_strict import session_relaxation, strict_is_enabled

    strict = strict_is_enabled()
    relaxation = session_relaxation()
    certifi_path, certifi_exists = _certifi()

    overrides = {
        name: os.environ[name]
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
        if os.environ.get(name)
    }

    return TrustStoreReport(
        state=state,
        reason=reason,
        ssl_context_class=f"{ssl.SSLContext.__module__}.{ssl.SSLContext.__qualname__}",
        backend=backend if active else None,
        truststore_version=version,
        python_version=".".join(str(p) for p in sys.version_info[:3]),
        # ``base_prefix`` rather than ``executable``: a venv's executable lives in
        # the venv, but its base_prefix still names the interpreter it was built
        # from — which is the thing that carries the baked-in OpenSSL paths.
        python_is_uv_managed=f"{os.sep}uv{os.sep}python{os.sep}" in sys.base_prefix,
        openssl_version=ssl.OPENSSL_VERSION,
        cafile=paths.cafile,
        cafile_exists=bool(paths.cafile and os.path.exists(paths.cafile)),
        capath=paths.capath,
        capath_exists=bool(paths.capath and os.path.isdir(paths.capath)),
        capath_has_hashed_certs=_capath_has_hashed_certs(paths.capath),
        env_overrides=overrides,
        certifi_path=certifi_path,
        certifi_exists=certifi_exists,
        ca_count=ca_count,
        ca_count_unavailable=ca_unavailable,
        strict_verification=strict,
        strict_relaxed_for=relaxation.host if relaxation else None,
        strict_relaxed_reason=relaxation.describe() if relaxation else None,
    )


__all__ = [
    "TrustStoreReport",
    "describe_trust_store",
    "record_injection",
    "reset_injection_record",
]
