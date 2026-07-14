"""OS-trust-store injection at CLI startup (issue #99).

Python verifies TLS against certifi's bundle, which holds only public root CAs.
A corporate root CA is installed in the OS trust store, so certifi never sees it
and every provider request dies as an opaque ``APIConnectionError("Connection
error.")`` — while VS Code / Copilot (Node, OS trust store) keep working on the
same network. ``truststore.inject_into_ssl()`` rebinds ``ssl.SSLContext``
process-wide, which covers every client site at once but must never block launch.

``inject_into_ssl`` mutates GLOBAL interpreter state, so the round-trip test here
extracts in a ``finally`` — leaking it would silently re-point every later test's
TLS verification at the OS store.
"""

from __future__ import annotations

import ssl
import sys
from typing import Any
from unittest.mock import patch

import pytest
from aelix_coding_agent.cli.entry import _inject_truststore


def test_injection_is_attempted() -> None:
    """The whole fix is this call happening."""

    with patch("truststore.inject_into_ssl") as inject:
        _inject_truststore()
    inject.assert_called_once_with()


def test_main_sync_injects_before_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring guard: defining the helper is inert unless main_sync calls it, and
    it must land BEFORE any provider work opens a TLS connection."""

    from aelix_coding_agent.cli import entry

    order: list[str] = []
    monkeypatch.setattr(entry, "_inject_truststore", lambda: order.append("inject"))
    monkeypatch.setattr(entry, "load_dotenv", lambda: order.append("dotenv"))
    monkeypatch.setattr(entry, "register_providers", lambda: order.append("providers"))
    monkeypatch.setattr(entry.asyncio, "run", lambda coro: (coro.close(), 0)[1])
    monkeypatch.setattr(entry.sys, "argv", ["aelix", "--version"])

    with pytest.raises(SystemExit) as exc:
        entry.main_sync()

    assert exc.value.code == 0
    assert order == ["inject", "dotenv", "providers"]


def test_missing_truststore_never_blocks_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wheel that failed to install must degrade to certifi, not crash the CLI."""

    # ``None`` in sys.modules makes ``import truststore`` raise ImportError.
    monkeypatch.setitem(sys.modules, "truststore", None)
    _inject_truststore()  # must not raise


def test_unsupported_platform_never_blocks_launch() -> None:
    """truststore raises on a platform whose trust store it cannot read."""

    with patch(
        "truststore.inject_into_ssl",
        side_effect=RuntimeError("unsupported platform"),
    ):
        _inject_truststore()  # must not raise


def test_injection_repoints_httpx_at_the_os_trust_store() -> None:
    """The behavioural claim behind "one line covers all ~10 client sites".

    Every aelix client site builds its context through ``ssl.create_default_
    context()`` (httpx does this internally), so rebinding ``ssl.SSLContext`` is
    what makes the OS store reach the SDKs, the OAuth flows and the Codex adapter
    without a ``verify=`` argument on any of them.
    """

    httpx = pytest.importorskip("httpx")
    truststore = pytest.importorskip("truststore")

    before: Any = httpx.create_ssl_context()
    assert not isinstance(before, truststore.SSLContext)

    _inject_truststore()
    try:
        after: Any = httpx.create_ssl_context()
        assert isinstance(after, truststore.SSLContext)
    finally:
        truststore.extract_from_ssl()

    # Global state restored — later tests must not inherit the injection.
    assert not isinstance(httpx.create_ssl_context(), truststore.SSLContext)
    assert ssl.SSLContext is not truststore.SSLContext
