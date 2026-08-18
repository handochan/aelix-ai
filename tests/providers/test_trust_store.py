"""``describe_trust_store`` — the three states, and the two ways it could lie (#99 follow-up).

WHAT THIS FILE EXISTS FOR. ``cli/entry._inject_truststore`` swallowed every
failure with a bare ``except Exception: pass``. That made "truststore is doing
its job" and "truststore raised and we swallowed it" produce identical
observable state, so a user whose system-wide CA had gone invisible saw only an
opaque TLS error at their first model request. These tests pin the distinction
back open.

THE TWO LIES, each with its own test:

1. **Reading the record instead of the binding.** ``_error_hints`` already
   documents why: the injection is best-effort and embedders never run it, so a
   flag set at startup can lie; class identity cannot. A report that trusted a
   recorded success would claim the OS store on a process where the binding is
   plain ``ssl`` — the exact failure the record was added to *diagnose*.
2. **A zero that means "the detector did not fire".** A truststore context
   refuses ``get_ca_certs()``. Reporting that as ``0`` would read as "you trust
   nothing" when the truth is "this context does not answer that question".

NOTHING HERE INJECTS. ``truststore.inject_into_ssl()`` is a process-global
rebinding of a stdlib class; a test that performed it would leak into every
later test in the session. The failure path is driven through the REAL
``_inject_truststore`` (which, failing, injects nothing by construction), and
the success binding is simulated by monkeypatching ``ssl.SSLContext`` for the
duration of one test.
"""

from __future__ import annotations

import ssl
import sys
from typing import TYPE_CHECKING

import pytest
from aelix_ai.providers import _trust_store
from aelix_ai.providers._trust_store import (
    describe_trust_store,
    record_injection,
    reset_injection_record,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_record() -> object:
    """No test may inherit another's receipt — it is module-level process state."""

    reset_injection_record()
    yield
    reset_injection_record()


class _FakeTruststoreContext:
    """Stands in for ``truststore._api.SSLContext``.

    Only two things about it matter to the report and both are reproduced: the
    module its class lives in (that is what ``_os_trust_store_active`` reads),
    and that ``get_ca_certs`` refuses — measured, the real one raises
    ``NotImplementedError`` with an empty message.

    🔴 DELIBERATELY NOT AN ``ssl.SSLContext`` SUBCLASS. CPython's own
    ``SSLContext.verify_mode`` setter re-enters through the MODULE GLOBAL::

        super(SSLContext, SSLContext).verify_mode.__set__(self, value)

    so the moment ``ssl.SSLContext`` is rebound, a subclass sends
    ``ssl.create_default_context()`` into unbounded recursion — measured on
    CPython 3.12.1, ``RecursionError`` after 499 frames, raised out of
    ``create_default_context`` at its ``context.verify_mode = CERT_REQUIRED``
    line. Truststore itself wraps rather than subclasses for the same reason. A
    "realistic" fake here would have tested CPython's re-entrancy, not this
    module.

    (Stdlib line numbers are deliberately not cited: they are ambiguous stems the
    citation gate cannot resolve, and they move with every patch release.)
    """

    __module__ = "truststore._api"

    def get_ca_certs(self, binary_form: bool = False) -> list[object]:
        raise NotImplementedError


def _pretend_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both halves of the real injected shape, so the fake environment is coherent.

    Patching only the class would leave ``create_default_context`` building a
    real context — a process that says "OS store" through one lookup and
    "certifi" through another, which is not a state the product can be in.
    """

    monkeypatch.setattr(ssl, "SSLContext", _FakeTruststoreContext)
    monkeypatch.setattr(
        ssl, "create_default_context", lambda *a, **k: _FakeTruststoreContext()
    )


# ── state 1: active ──────────────────────────────────────────────────────────


def test_active_is_read_off_the_binding_not_the_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``active`` requires the LIVE binding, and says so without any receipt at all."""

    _pretend_injected(monkeypatch)
    # Deliberately no record_injection() call: an embedder that injected
    # truststore itself must still be reported as active.
    report = describe_trust_store()

    assert report.state == "active"
    assert report.reason is None
    assert report.ssl_context_class.startswith("truststore.")


def test_a_recorded_success_cannot_manufacture_an_active_state() -> None:
    """The lie this module was written to be incapable of.

    SABOTAGE: make ``describe_trust_store`` branch on ``_injection_attempted and
    not _injection_error`` instead of on ``_os_trust_store_active()``. That is the
    obvious implementation, it is what the swallowed ``pass`` invited, and it
    reports the OS store on a process that is certifi-only. This must go RED.
    """

    record_injection(None)  # injection "succeeded" ...
    report = describe_trust_store()

    # ... but nothing rebound ssl.SSLContext, so it did not.
    assert ssl.SSLContext.__module__ == "ssl", "guard: this test needs a clean binding"
    assert report.state != "active"
    assert report.ssl_context_class == "ssl.SSLContext"


# ── state 2: degraded ────────────────────────────────────────────────────────


def test_degraded_names_the_swallowed_exception() -> None:
    """A failure must be legible, not merely non-fatal."""

    record_injection(ImportError("no truststore wheel for this platform"))
    report = describe_trust_store()

    assert report.state == "degraded"
    assert report.reason is not None
    assert "ImportError" in report.reason
    assert "no truststore wheel for this platform" in report.reason
    # The consequence, not just the cause: this is the sentence that tells a
    # corporate-network user why their CA stopped counting.
    assert "certifi" in report.reason


def test_the_real_inject_truststore_records_its_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driven through the SHIPPED function, not a re-implementation of it.

    SABOTAGE: restore ``except Exception: pass`` in ``cli/entry._inject_truststore``.
    The state falls back to ``not-attempted`` — indistinguishable from an
    embedder — and this must go RED.
    """

    from aelix_coding_agent.cli.entry import _inject_truststore

    # ``None`` in sys.modules is the documented way to make ``import x`` raise,
    # and it makes the REAL import statement fail rather than a stub of it.
    monkeypatch.setitem(sys.modules, "truststore", None)

    _inject_truststore()

    report = describe_trust_store()
    assert report.state == "degraded"
    assert report.reason is not None
    # ``ModuleNotFoundError``, not ``ImportError``: ``None`` in ``sys.modules`` is
    # the halted-import path, and the receipt records the CONCRETE type because
    # "which import failed and how" is the whole diagnostic value.
    assert "ModuleNotFoundError" in report.reason
    assert "truststore" in report.reason
    # And it must still have degraded rather than raised — launch survives.
    assert ssl.SSLContext.__module__ == "ssl"


# ── state 3: not-attempted ───────────────────────────────────────────────────


def test_not_attempted_is_distinct_from_degraded() -> None:
    """An embedder that never injected is NOT a defect, and must not read as one.

    Collapsing these two into one "not active" state is the other half of the
    bug: it would tell every library user their trust store had failed.
    """

    report = describe_trust_store()

    assert report.state == "not-attempted"
    assert report.reason is not None
    assert "embedder" in report.reason
    assert report.backend is None, "no backend may be claimed when nothing is active"


# ── the fields that decide whether verification can work at all ──────────────


def test_capath_usability_is_measured_not_inferred_from_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``isdir()`` is the wrong question. OpenSSL looks certs up by hashed name.

    A capath full of ``.pem`` files with no ``c_rehash`` symlinks verifies
    nothing while passing an existence check — which is exactly the state a
    uv-managed interpreter can land in.

    SABOTAGE: return ``os.path.isdir(capath)``. The unhashed arm goes RED.
    """

    unhashed = tmp_path / "unhashed"
    unhashed.mkdir()
    (unhashed / "ca-certificates.pem").write_text("-----BEGIN CERTIFICATE-----\n")
    assert _trust_store._capath_has_hashed_certs(str(unhashed)) is False

    hashed = tmp_path / "hashed"
    hashed.mkdir()
    (hashed / "3513523f.0").write_text("-----BEGIN CERTIFICATE-----\n")
    assert _trust_store._capath_has_hashed_certs(str(hashed)) is True

    # Not a hash: right length, not hex. The 8-hex rule is the whole point.
    notahash = tmp_path / "notahash"
    notahash.mkdir()
    (notahash / "zzzzzzzz.0").write_text("x")
    assert _trust_store._capath_has_hashed_certs(str(notahash)) is False

    assert _trust_store._capath_has_hashed_certs(str(tmp_path / "missing")) is False
    assert _trust_store._capath_has_hashed_certs(None) is False


def test_an_unknowable_ca_count_is_none_and_never_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SABOTAGE: ``return len(ctx.get_ca_certs()) if ok else 0``.

    Zero reads as "you trust nothing" and would send a user chasing a CA bundle
    on a machine whose OS store is working fine. This must go RED.
    """

    _pretend_injected(monkeypatch)
    report = describe_trust_store()

    assert report.ca_count is None
    assert report.ca_count is not 0  # noqa: F632 — the point is the identity, not equality
    assert report.ca_count_unavailable is not None
    assert "enumerate" in report.ca_count_unavailable


def test_env_overrides_are_reported_because_they_beat_every_other_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``SSL_CERT_FILE`` silently wins over the compiled-in paths.

    A report that printed cafile/capath without it would be describing a trust
    configuration the process is not using.
    """

    bundle = tmp_path / "corp.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    report = describe_trust_store()
    assert report.env_overrides["SSL_CERT_FILE"] == str(bundle)


def test_as_dict_is_json_safe_and_complete() -> None:
    """``aelix status --json`` serialises this verbatim."""

    import json

    payload = describe_trust_store().as_dict()
    json.dumps(payload)  # must not raise

    for key in (
        "state",
        "reason",
        "ssl_context_class",
        "backend",
        "cafile",
        "cafile_exists",
        "capath",
        "capath_exists",
        "capath_has_hashed_certs",
        "python_is_uv_managed",
        "ca_count",
        "ca_count_unavailable",
        "env_overrides",
    ):
        assert key in payload, key
