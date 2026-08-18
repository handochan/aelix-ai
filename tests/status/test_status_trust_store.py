"""``aelix status`` reports which trust store is live — the #99 follow-up surface.

WHY IT IS IN ``status`` AND NOT BEHIND A FLAG. Someone whose provider requests
are dying behind a corporate TLS proxy does not know to ask for a trust-store
dump; they run the one diagnostic verb the product has. Before this, that verb
answered every question about a launch EXCEPT the one that separates "your proxy
intercepts HTTPS" from "your key is wrong" — and the injection that was supposed
to handle the former swallowed its own failures silently.

WHAT IS DRIVEN. The shipped ``_collect`` and the shipped ``_render_text``, over a
real ``describe_trust_store()``. The report is not stubbed: its whole value is
that it describes the process actually running, so a double would describe the
double.
"""

from __future__ import annotations

import io
import json

import pytest
from aelix_ai.providers._trust_store import record_injection, reset_injection_record
from aelix_coding_agent.cli.status import _collect, _render_text, run_status_command


@pytest.fixture(autouse=True)
def _clean_record() -> object:
    reset_injection_record()
    yield
    reset_injection_record()


async def _report() -> dict[str, object]:
    return await _collect(discover=False)


@pytest.mark.asyncio
async def test_trust_store_is_reported_even_with_no_extensions() -> None:
    """``--no-extensions`` is the flag a TLS diagnosis WOULD use.

    Someone chasing a certificate wall has every reason to skip importing
    extension code, and that is exactly the run where this block must answer.
    """

    report = await _report()

    assert "trust_store" in report
    trust = report["trust_store"]
    assert isinstance(trust, dict)
    assert trust["state"] in {"active", "degraded", "not-attempted"}


@pytest.mark.asyncio
async def test_a_degraded_trust_store_is_visible_in_the_text_render() -> None:
    """SABOTAGE: drop the ``! reason`` line from ``_render_trust_store``.

    The state word alone does not tell a user WHAT failed or what it costs them.
    This must go RED.
    """

    record_injection(ImportError("no truststore wheel for this platform"))
    report = await _report()

    stream = io.StringIO()
    _render_text(report, stream)
    text = stream.getvalue()

    assert "TLS trust:" in text
    assert "certifi only" in text
    assert "no truststore wheel for this platform" in text
    # The consequence, in the user's terms — not just the exception.
    assert "corporate root" in text


@pytest.mark.asyncio
async def test_the_render_never_prints_a_ca_count_it_could_not_measure() -> None:
    """A number here is a claim. ``0`` would read as "you trust nothing".

    SABOTAGE: render ``ca_count`` with ``or 0``. The ``not reportable`` arm
    disappears and this must go RED whenever the active context refuses to
    enumerate.
    """

    report = await _report()
    trust = report["trust_store"]
    assert isinstance(trust, dict)

    stream = io.StringIO()
    _render_text(report, stream)
    text = stream.getvalue()

    if trust["ca_count"] is None:
        assert "not reportable" in text
        assert "CAs loaded      0" not in text
    else:
        assert f"{trust['ca_count']}" in text


@pytest.mark.asyncio
async def test_json_carries_the_whole_report_for_scripts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` is how a bug report gets this off a user's machine verbatim."""

    record_injection(RuntimeError("platform store unreachable"))
    rc = await run_status_command(["--json", "--no-extensions"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    trust = payload["trust_store"]
    assert trust["state"] == "degraded"
    assert "platform store unreachable" in trust["reason"]
    # The interpreter fields are what distinguish a uv-managed install (what
    # install.sh produces) from a contributor's system Python.
    assert "python_is_uv_managed" in trust
    assert "openssl_version" in trust


@pytest.mark.asyncio
async def test_capath_that_exists_but_holds_no_hashed_certs_is_called_out() -> None:
    """"exists" and "usable" are different claims, and the render must not merge them.

    SABOTAGE: render the capath row from ``capath_exists`` alone. The warning
    disappears on the one configuration that silently verifies nothing, and this
    must go RED.
    """

    report = await _report()
    trust = report["trust_store"]
    assert isinstance(trust, dict)
    trust["capath"] = "/etc/ssl/certs"
    trust["capath_exists"] = True
    trust["capath_has_hashed_certs"] = False

    stream = io.StringIO()
    _render_text(report, stream)
    text = stream.getvalue()

    assert "holds no hashed certs" in text


@pytest.mark.asyncio
async def test_environment_supplied_paths_are_neutered_before_they_are_printed() -> None:
    """This block prints strings that came from OUTSIDE the program.

    ``cafile``/``capath`` come from OpenSSL, ``env_overrides`` from the process
    environment, and the whole point of the block is that a user pastes its
    output into a bug report. The first version printed all of them raw, with
    ``safe_for_terminal`` one import away — written in the same sprint, for
    exactly this class of string.

    SABOTAGE: drop any of the ``clean()`` calls in ``_render_trust_store``. The
    escape survives to the rendered text and this goes RED.
    """

    hostile = "/etc/ssl/\x1b[2Jcerts\x9b31m\x1b]0;PWNED\x07"
    report = await _report()
    trust = report["trust_store"]
    assert isinstance(trust, dict)
    trust["cafile"] = hostile
    trust["cafile_exists"] = True
    trust["capath"] = hostile
    trust["capath_exists"] = True
    trust["capath_has_hashed_certs"] = True
    trust["env_overrides"] = {"SSL_CERT_FILE": hostile}
    trust["reason"] = f"something failed {hostile}"

    stream = io.StringIO()
    _render_text(report, stream)
    text = stream.getvalue()

    assert "\x1b" in hostile and "\x9b" in hostile  # positive control
    for ch in ("\x1b", "\x9b", "\x07"):
        assert ch not in text, repr(ch)
    # The inert literal survives, so the path is still recognisable.
    assert "[2Jcerts" in text
