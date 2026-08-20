"""Session-wide test guards.

P0 #3 HEAVY (ADR-0139): ``grep``/``find`` now call
:func:`aelix_coding_agent.util.tools_manager.ensure_tool`, which may download
ripgrep/fd from GitHub when the binary is absent. To keep the whole suite
hermetic (no network, no rate-limit flakiness) and to avoid polluting
``~/.aelix/agent/bin``, this autouse fixture:

- redirects the tool bin dir to a clean per-session temp dir, and
- makes the network primitives raise, so any real download attempt fails fast
  (``ensure_tool`` then returns ``None`` and the caller uses its fallback).

Tests that exercise the download path itself
(``tests/util/test_tools_manager.py``) re-stub these primitives with working
mocks inside the test body, which override this autouse setup (same
function-scoped ``monkeypatch``, last write wins).
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.util import tools_manager as _tm


@pytest.fixture(autouse=True)
def _no_real_tool_downloads(tmp_path_factory, monkeypatch):
    bin_dir = tmp_path_factory.mktemp("aelix_bin")
    monkeypatch.setattr(_tm, "_bin_dir", lambda: str(bin_dir))

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(_tm, "_get_latest_version", _blocked)
    monkeypatch.setattr(_tm, "_download_file", _blocked)


@pytest.fixture(autouse=True)
def _no_default_catalog(monkeypatch):
    """Disable the built-in default discover catalog for the whole suite.

    ``extension_catalog.DEFAULT_CATALOG_URL`` is a LIVE https location (the official
    marketplace catalog on GitHub Pages). Since the W1-A ``classify_target`` repair
    (#111 A-1) it is correctly classified as an https catalog instead of being
    corrupted into a ``git+…`` spec, so any test that reaches
    ``_effective_catalog_locations`` without pinning the env would inject it into the
    effective locations and ``discover --refresh`` would make a REAL outbound request
    — non-hermetic, and it flips "every registered catalog failed" assertions.
    Emptying ``AELIX_DEFAULT_CATALOG`` (guard ②) keeps the suite offline.

    Tests that need a default OPT IN explicitly — ``monkeypatch.setenv`` to a
    ``file://`` / synthetic URL, or ``monkeypatch.delenv`` to restore the built-in
    value (both override this function-scoped fixture; last write wins).
    """

    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", "")


@pytest.fixture(autouse=True)
def _restore_api_registry():
    """Undo per-test writes to the PROCESS-GLOBAL api-provider registry.

    ``aelix_ai.api_registry._PROVIDERS`` is module state that outlives a test.
    Two shapes of test write to it and neither cleans up: anything that runs a
    real extension ``setup(aelix)`` registering a custom wire adapter (the
    shipped ``examples/selfhosted`` example does exactly this, through
    ``register_api_adapter``), and anything that drives a harness reload —
    ``reset_api_providers()`` is CLEAR-ONLY by design (``api_registry.py:120``;
    aelix has no lazy provider cache to re-fill, each provider package registers
    itself on import), so it empties the built-ins and nothing puts them back.

    Combine the two and a later test sees a registry holding ONLY the
    extension's adapter. MEASURED before this fixture existed:
    ``pytest tests/test_extension_login_provider.py tests/cli/test_entry_router.py``
    failed with ``supported: selfhosted-openai`` where the assertion wanted
    ``No API key found for`` — the built-in adapters were simply gone. Order
    dependent, so it hid from any run that happened not to pair them.

    Snapshot-and-restore rather than clear-and-rebuild: whatever was registered
    when the test started is what it gets back, so this cannot itself become the
    thing that decides which adapters exist.
    """

    from aelix_ai import api_registry

    before = dict(api_registry._PROVIDERS)  # noqa: SLF001 — the state being guarded
    try:
        yield
    finally:
        api_registry._PROVIDERS.clear()  # noqa: SLF001
        api_registry._PROVIDERS.update(before)  # noqa: SLF001
