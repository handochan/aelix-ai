"""The shipped self-hosted example verifies certificates — gated, not assumed.

WHY THIS FILE EXISTS. The example builds its own ``httpx.AsyncClient`` because a
private CA bundle is the one deviation no aelix config key can express. That is
exactly the shape that invites ``verify=False``, and the example used to teach
it. It no longer does — but when the fix landed it landed with NO gate, and that
was measured, not guessed: reverting the client to ``verify=False`` and deleting
the environment read left ``ruff check`` clean and all 70 related tests green.
Nothing in the repo could see it. ``pyproject.toml``'s ruff ``select`` has no
``S``, so flake8-bandit's S501 never runs either.

So this file pins two things the rest of the suite cannot:

1. the example's source never spells ``verify=False`` (with a positive control,
   so a detector that silently stopped matching fails loudly); and
2. ``SELFHOSTED_CA_BUNDLE`` really reaches httpx — the branch the lifetime gate
   deliberately erases, because ``tests/extensions/test_selfhosted_example_client_lifetime.py``
   has an autouse fixture that unsets the variable (correctly: a stale value
   pointing at a missing file raises ``FileNotFoundError`` inside the client
   constructor and would take that gate down before it measured anything).

Both assertions are about the CLIENT THE EXAMPLE HANDS TO THE SDK, read off the
constructor call, not off the source. A future edit that keeps the environment
read but drops it on the floor still fails here.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import ssl
from pathlib import Path
from typing import Any

import httpx
import pytest
from aelix_ai.streaming import Context, Model, SimpleStreamOptions
from aelix_coding_agent.examples.selfhosted import selfhosted

#: The variable the example reads its CA bundle path from.
_CA_BUNDLE_ENV = "SELFHOSTED_CA_BUNDLE"

#: A real CA certificate, so ``ssl.create_default_context(cafile=...)`` loads it
#: rather than raising. Self-signed, CN=aelix-example-test-ca, expires 2044.
#: Generated once with ``openssl req -x509 -newkey rsa:2048 -nodes -subj
#: '/CN=aelix-example-test-ca' -days 7000``; its key was discarded, so nothing
#: can ever be signed with it.
_TEST_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDITCCAgmgAwIBAgIUBydSJoRpBRWMHslcc17XQ6wFO/YwDQYJKoZIhvcNAQEL
BQAwIDEeMBwGA1UEAwwVYWVsaXgtZXhhbXBsZS10ZXN0LWNhMB4XDTI2MDgyMDA0
NTkzM1oXDTQ1MTAxOTA0NTkzM1owIDEeMBwGA1UEAwwVYWVsaXgtZXhhbXBsZS10
ZXN0LWNhMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA+E/5kmxig8UN
53JrgFziSaa0F+Co1Of0ZzqUQVvcUJArCMQahxZGrv3Y901VqmX6Va/XukNMIvYo
FFpYXR5xba4/r3z19kT90ntc+4x3b6NBfam/RwmbroYRy2GJk9pJ597qkGbekC3K
A3axwXh9cOweZDeJa46liPb1FjMUXSeMsideogdAbazze8tf+xSO8SupYvrNHjzd
FBcKZ7eZUwHqzHoX31rM0Pzy9lYy6jGYmUK2pZQd5QI5u+1VOGZg5I5p5HnXMLNY
0gS3mqn37VbvDoIAAou85tfhC3JGHzr4NToTfQ73h3UIMIyvkIdkdP0rQ8l+11Fp
gc68fb8BHwIDAQABo1MwUTAdBgNVHQ4EFgQU7QwgPc5pdxHCWAlxE3wtHe96Kzgw
HwYDVR0jBBgwFoAU7QwgPc5pdxHCWAlxE3wtHe96KzgwDwYDVR0TAQH/BAUwAwEB
/zANBgkqhkiG9w0BAQsFAAOCAQEAUjUh1iU+5FJxOEse4dY28dkaKgZAiJ20fSme
jxXzoy+fxWqHOlNjdSbkL42IE5I8sPqVbucsZeH6suYnu0zdjRQdBwBaYqyPzirf
Ji03UEedkFPxe8PMAKDEHHxfpzXI8Pg9nW7S9Fq7uHxPDsbNV2cu9bhYu3dUGSvE
B1dYZE65MXGQGStpR/GFG/kE+0tklhR4JmOXm+0NTAPBbgBqXIyu2ac3l6zyUxjS
Vs+dkLj95w/ZYKaRzYa7/GLvOtRwCCZRXuQXFyF1KJTCNoRKtXhOAfG7ydnLjbxI
V+jQxngMgqEcl9JbcxMLoSsFQIcgxd0UUoEEdit0oXmsxM9RKg==
-----END CERTIFICATE-----
"""


# === (1) the source never disables verification ============================

_DISABLED = re.compile(r"verify\s*=\s*(False|0)\b")


def _example_source() -> str:
    return Path(inspect.getsourcefile(selfhosted) or "").read_text(encoding="utf-8")


def test_the_example_never_disables_certificate_verification() -> None:
    """``verify=False`` accepts ANY certificate from anyone on the path.

    Comments are stripped first, because the example deliberately NAMES the
    anti-pattern in prose ("NOT verify=False: that would accept ANY
    certificate…") and that sentence must be allowed to stay — deleting the
    warning to satisfy a grep would be the wrong repair.
    """

    code = "\n".join(
        line.split("#", 1)[0] for line in _example_source().splitlines()
    )
    assert not _DISABLED.search(code), (
        "the shipped example disables TLS verification — it is the one thing "
        "this example must not teach"
    )


def test_the_detector_can_see_a_disabled_client() -> None:
    """Positive control. Without this, a regex that stopped matching would make
    the test above pass by seeing nothing, which is the failure mode a static
    gate has and a behavioural one does not."""

    doctored = _example_source().replace(
        "http_client=httpx.AsyncClient(verify=verify)",
        "http_client=httpx.AsyncClient(verify=False)",
    )
    code = "\n".join(line.split("#", 1)[0] for line in doctored.splitlines())
    assert _DISABLED.search(code), "the detector cannot see the state it exists to reject"


# === (2) the CA bundle really reaches httpx ================================


def _model() -> Model:
    return Model(
        id="gpt5mini",
        name="gpt5mini",
        provider="selfhosted",
        api="selfhosted-openai",
        # Port 1 on loopback: refused immediately, no timeout, no DNS.
        base_url="http://127.0.0.1:1/v1/gpt5mini",
    )


def _context() -> Context:
    return Context(messages=[], system_prompt="")


def _opts() -> SimpleStreamOptions:
    return SimpleStreamOptions(api_key="t")


@contextlib.contextmanager
def _capture_verify() -> Any:
    """Record the ``verify`` every ``httpx.AsyncClient`` is built with."""

    seen: list[Any] = []
    real = httpx.AsyncClient

    class _Spy(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            seen.append(kw.get("verify", "<not passed>"))
            super().__init__(*a, **kw)

    httpx.AsyncClient = _Spy  # type: ignore[misc]
    try:
        yield seen
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]


async def _construct_only() -> None:
    """Drive the example far enough to build its client, and no further.

    The client is built at the top of the stream_fn, before any I/O, so the
    first ``anext`` constructs it and then fails to connect to a port nothing is
    listening on. The connection error is the point at which we stop.
    """

    agen = selfhosted._selfhosted_stream(_model(), _context(), _opts())
    with contextlib.suppress(Exception):
        async for _ in agen:
            break
    with contextlib.suppress(Exception):
        await agen.aclose()


@pytest.mark.asyncio
async def test_an_unset_bundle_leaves_ordinary_system_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_CA_BUNDLE_ENV, raising=False)

    with _capture_verify() as seen:
        await _construct_only()

    assert seen, "the example built no httpx client at all — the probe missed it"
    assert seen[0] is True, (
        f"expected plain system trust with the variable unset, got {seen[0]!r}"
    )


@pytest.mark.asyncio
async def test_a_set_bundle_reaches_httpx_as_a_verifying_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The branch the lifetime gate erases. This is the whole security fix."""

    bundle = tmp_path / "ca.pem"
    bundle.write_text(_TEST_CA_PEM, encoding="utf-8")
    monkeypatch.setenv(_CA_BUNDLE_ENV, str(bundle))

    with _capture_verify() as seen:
        await _construct_only()

    assert seen, "the example built no httpx client at all — the probe missed it"
    verify = seen[0]
    assert isinstance(verify, ssl.SSLContext), (
        f"expected an SSLContext built from the bundle, got {verify!r} — a bare "
        "path string still works on httpx 0.28 but is deprecated"
    )
    assert verify.verify_mode is ssl.CERT_REQUIRED
    assert verify.check_hostname is True
    assert verify.cert_store_stats()["x509"] >= 1, (
        "the context loaded no certificates — the bundle did not reach it"
    )


@pytest.mark.asyncio
async def test_a_missing_bundle_fails_closed_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fallback to system trust on a bad path would be a silent downgrade:
    the operator asked for a specific CA and would not be told they did not get
    it. Both spellings raise at construction; this pins that they still do."""

    monkeypatch.setenv(_CA_BUNDLE_ENV, str(tmp_path / "does-not-exist.pem"))

    # Driven directly rather than through ``_construct_only``, which suppresses
    # the connection error the other arms rely on — and would swallow this one.
    agen = selfhosted._selfhosted_stream(_model(), _context(), _opts())
    with pytest.raises(FileNotFoundError):
        async for _ in agen:  # pragma: no cover - the first step raises
            break
