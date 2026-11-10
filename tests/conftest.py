"""Session-wide test guards.

P0 #3 HEAVY (ADR-0139): ``grep``/``find`` now call
:func:`aelix_coding_agent.util.tools_manager.ensure_tool`, which may download
ripgrep/fd from GitHub when the binary is absent. To keep the whole suite
hermetic (no network, no rate-limit flakiness) and to avoid polluting
``~/.aelix/agent/bin``, this autouse fixture:

- redirects the tool bin dir to a clean temp dir of the test's own, made the
  first time code asks for it (#459), and
- makes the network primitives raise, so any real download attempt fails fast
  (``ensure_tool`` then returns ``None`` and the caller uses its fallback).

Tests that exercise the download path itself
(``tests/util/test_tools_manager.py``) re-stub these primitives with working
mocks inside the test body, which override this autouse setup (same
function-scoped ``monkeypatch``, last write wins).
"""

from __future__ import annotations

import itertools
import os
import queue
import threading
import types
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.cli.config import ENV_AGENT_DIR, ENV_SESSION_DIR
from aelix_coding_agent.util import tools_manager as _tm

from tests import session_root_guard

# Before any test runs, so the protected roots are the developer's, not a sandbox's.
session_root_guard.install()


@pytest.fixture(autouse=True)
def _isolated_session_root(
    monkeypatch: pytest.MonkeyPatch, _run_session_root: str
) -> Iterator[None]:
    """Pin a session root per test, and run the in-process guard around it (#458).

    The suite never set ``AELIX_CODING_AGENT_SESSION_DIR``, so a test that ran
    the CLI with only ``AELIX_CODING_AGENT_DIR`` isolated wrote its session into
    the developer's ``~/.aelix/sessions`` (about 7,450 on the owner's machine).
    The pin covers in-process code that reads the variable and every child that
    inherits the test's environment. It is a default: it applies only when, at
    the test's start, the variable is unset, empty (``get_session_dir`` reads
    ``''`` as unset, so its callers use ``~/.aelix/sessions``) or still the run's
    own root. So a module-, class- or session-scoped fixture that set the
    variable to a value reaches its tests and their children. That value stays
    in effect until the fixture is torn down: for the rest of its module or
    class, and for a session-scoped fixture (not autouse) for every test that
    runs after it, until the run ends, whether or not that test requests it. A
    test that sets the variable itself wins (same ``monkeypatch``, last write
    wins; ``test_child_session_records.py``). The guard is a best-effort net: in
    this process it blocks the operations ``tests/session_root_guard.py`` lists,
    under a protected root, and fails the test in teardown. It misses others (a
    rename of ``~/.aelix`` or the home and, on POSIX, an ``rmtree`` of either
    among them, listed there), and judges no child.
    """

    current = os.environ.get(ENV_SESSION_DIR)
    if not current or current == _run_session_root:
        monkeypatch.setenv(ENV_SESSION_DIR, str(session_root_guard.next_session_root()))
    with session_root_guard.guard() as violations:
        yield
    if violations:
        pytest.fail(session_root_guard.describe(violations), pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _run_session_root() -> Iterator[str]:
    """Give the run a session root too, and delete every root when the run ends.

    A module- or session-scoped fixture is set up before any test's own pin.
    Measured with a scratch HOME, the packaging gate's wheel builds start
    children at that point, and a child started from ``os.environ`` gets this
    root (the guard runs per test and does not watch such fixtures). The value
    is what ``_isolated_session_root`` compares against to tell a wider
    fixture's own value from this default. The roots sit in the system temp
    dir, so every one is removed here.
    """

    root = str(session_root_guard.next_session_root())
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(ENV_SESSION_DIR, root)
        yield root
    session_root_guard.remove_session_roots()


@pytest.fixture
def own_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``AELIX_CODING_AGENT_DIR`` at a directory of the test's own (#497).

    Unlike the session root, nothing pins the agent dir for every test
    (ADR-0026, #497 amendment, says why and what a pin measured). So a module
    whose code under test resolves ``get_agent_dir()`` opts in: ``pytestmark =
    pytest.mark.usefixtures("own_agent_dir")``. ``_build_harness_options`` is
    the common case: it lists ``<agent dir>/extensions`` and loads what is
    there, and with the developer's own extensions installed there a test would
    run them. A test that runs ``_async_main`` sandboxes the home instead
    (``tests.env_sandbox.sandbox_home``), as its siblings do. The directory is
    not created, and a test that sets the variable itself wins (the same
    ``monkeypatch``, last write wins). ``tests/session_root_guard.py`` fails a
    test that reaches the developer's real agent dir anyway.
    """

    agent_dir = tmp_path / "isolated-agent-dir"
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    return agent_dir


@pytest.fixture(autouse=True)
def _no_user_system_prompt_files(tmp_path_factory, monkeypatch):
    """Keep #287 discovery out of the developer's default global directory.

    Explicit AELIX_CODING_AGENT_DIR fixtures still exercise real discovery;
    only the default global prompt-file lookup is redirected. Other resources
    retain their existing test setup rather than sharing this new override.
    """
    from aelix_coding_agent.cli import system_prompt_files

    configured_agent_dir = system_prompt_files.get_agent_dir
    isolated = tmp_path_factory.getbasetemp() / "empty_system_prompt_agent"

    def agent_dir():
        if os.environ.get("AELIX_CODING_AGENT_DIR"):
            return configured_agent_dir()
        return str(isolated)

    monkeypatch.setattr(system_prompt_files, "get_agent_dir", agent_dir)


_per_test_numbers = itertools.count()


@pytest.fixture(scope="session")
def _per_test_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The one parent of every ``_per_test_dir`` in the run, made once (#459)."""

    return tmp_path_factory.mktemp("aelix_per_test")


@pytest.fixture
def _per_test_dir(_per_test_root: Path) -> Path:
    """A directory of the test's own, for the autouse fixtures; not created (#459).

    ``tmp_path_factory.mktemp`` (and so ``tmp_path``) lists the whole basetemp to
    find the next free number, and the basetemp keeps every numbered directory
    until the run ends. A fixture that called it for every test made each test
    pay for every test before it: on Windows the fastest tenth of the tests took
    0.003 s at the start of a run and up to 0.1 s near its end. This path is a
    counter under one parent, so it lists nothing, and whoever needs the
    directory creates it.
    """

    return _per_test_root / str(next(_per_test_numbers))


@pytest.fixture(autouse=True)
def _no_real_tool_downloads(_per_test_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Created on the first call, so the many tests that never resolve a tool
    # create nothing; one that does gets a fresh, empty dir, as before.
    bin_dir = _per_test_dir / "bin"

    def _bin_dir() -> str:
        bin_dir.mkdir(parents=True, exist_ok=True)
        return str(bin_dir)

    monkeypatch.setattr(_tm, "_bin_dir", _bin_dir)

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(_tm, "_get_latest_version", _blocked)
    monkeypatch.setattr(_tm, "_download_file", _blocked)


@pytest.fixture(autouse=True)
def _no_dotenv_provenance_record(monkeypatch):
    """Start every test with no ``AELIX_DOTENV_ADMITTED`` (#362, ADR-0250).

    ``load_dotenv`` writes that record into the REAL ``os.environ`` (it must
    reach delegated children), and a test that runs the loader cleans the keys
    it loaded but not the record. Measured on the design prototype without this
    fixture: 27 more failures in the affected subset (46 vs 19), each a later
    test reading an earlier test's record — a key it had exported was taken for
    a planted one. ``setenv`` first, so ``monkeypatch`` records the OUTER value
    even when there is none and undoes a record the test's own loader wrote
    (``delenv`` alone records nothing for an absent name).
    """

    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "")
    monkeypatch.delenv("AELIX_DOTENV_ADMITTED")


@pytest.fixture(autouse=True)
def _no_offline_switch(monkeypatch):
    """Start every test online, and undo any ``PI_OFFLINE=1`` a test exported (#288).

    ``cli/entry.py`` exports ``PI_OFFLINE=1`` into the REAL ``os.environ`` (it
    must reach children) — before every verb since #288 review round 1, so any
    test that drives ``_async_main`` with an offline name or an ``extension …
    --offline`` would otherwise turn every later test offline (round 1 measured
    16 extension tests flipped by one such leak). ``setenv`` first, as above: on
    an absent name ``delenv`` alone records nothing to undo. It also keeps a
    developer's own exported ``PI_OFFLINE`` / ``AELIX_OFFLINE`` out of the suite.
    """

    for name in ("PI_OFFLINE", "AELIX_OFFLINE"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


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
def _no_hosted_catalog(monkeypatch):
    """Keep the hosted model catalog (#434, ADR-0264 §4.3) out of every test that does not ask.

    Its overlay, the refresher's shared runs and the settings seed the launch
    records are process-global, so a test that installs an overlay (or seeds a
    default) would leak it into the next one: after every test, no overlay is
    installed and the refresher and the seed are reset. ``AELIX_MODEL_CATALOG_URL``
    is emptied (the source is off), so a ``ModelRegistry.create()`` reads no
    user's real ``models-store.json`` into the catalog and no trigger reaches
    GitHub Pages. A test that needs the source sets the variable itself
    (a loopback ``http://127.0.0.1:<port>`` is allowed) and installs what it needs.
    """

    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", "")
    yield
    from aelix_ai.models import install_overlay
    from aelix_coding_agent.cli.runtime_bootstrap import clear_settings_seed
    from aelix_coding_agent.model_refresh import reset_refresher

    install_overlay(None)
    reset_refresher()
    clear_settings_seed()


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


@pytest.fixture
def mkdir_or_skip() -> Callable[..., Path]:
    """Stage a directory whose NAME is the attack payload, or skip that payload.

    Several security gates in this suite put the payload in a path COMPONENT —
    C0/C1 control bytes, ``<``, ``>``, ``"`` — because that is how the payload
    really arrives. The premise is stated in the code under test and in every
    one of those tests: "POSIX permits every byte but ``/`` and NUL in a path
    component" (``print_channel.py:704``), so a ``git clone`` or an unpacked
    tarball creates such a directory and no privilege is needed.

    That premise is FALSE on NTFS, which rejects ``\\x00``–``\\x1f`` and
    ``<>:"/\\|?*`` in a name outright. The fixture cannot be constructed, so
    every one of those cases died in setup with ``OSError: [WinError 123]`` —
    14 of them on the first Windows run.

    SKIPPING IS RIGHT HERE, AND THE REPO'S USUAL RULE IS NOT. The rule is
    ``tests/cli/test_stdio_encoding_win32.py``'s: do not gate on the platform,
    inject the condition and run everywhere, because "a test that only runs on
    the platform we cannot run on is not a regression guard". That argument
    needs a condition to inject. Here the OPERATING SYSTEM refuses to create the
    input, so the defence is genuinely unreachable rather than merely untested,
    which is the case ``tests/oauth/test_auth_storage.py:64`` and
    ``tests/agents_ext/test_child_trust_argv.py:189`` already skip for.

    PER PAYLOAD, NOT PER TEST. NTFS accepts plenty of what these tables carry —
    ``\\x9b`` (the one-byte CSI) is a legal filename byte, and so are ``&``,
    ``'`` and every non-ASCII component in them. A blanket ``skipif`` on the
    test would throw that Windows coverage away, and would go on throwing away
    whatever payload someone adds next. Attempting the ``mkdir`` and skipping on
    the refusal keeps each legal payload running and needs no table of which
    bytes which filesystem allows.
    """

    def _mkdir(path: Path, *, parents: bool = False) -> Path:
        try:
            path.mkdir(parents=parents)
        except OSError as exc:
            pytest.skip(
                f"the filesystem refuses this payload as a directory name ({exc}) — "
                "the POSIX path-component premise this case rests on does not hold "
                "here, so the defence is unreachable, not unverified"
            )
        return path

    return _mkdir


#: The file name the running test's frame shows in a faulthandler dump (#689).
RUNNING_TEST_LABEL_FILE = "<pytest: the test running now (#689)>"


def _labelled_wait(labels: queue.SimpleQueue[str]) -> str:
    return labels.get()


class _RunningTestLabel:
    """One idle thread whose stack names the test that is running (#689).

    pytest's ``faulthandler_timeout`` dump is written from C after 240 s and
    names no test. When the main thread is idle in the event loop's
    ``select``, its stack has no test frame either. That is how #689's three
    CI dumps looked. The test was found by counting progress dots against a
    collection list, and the count in the issue's comment missed the 57 dots
    that were flushed after its dump.

    faulthandler prints every thread's frames as ``line N in <code name>``.
    This thread blocks in ``SimpleQueue.get``, which is C, inside a function
    whose code is renamed to the running test's nodeid. So its top frame in
    a dump names the test once the thread has taken the label; a test that
    holds the GIL from its very start can leave an earlier label there
    (docs/development/ci-hang-diagnostics.md). It writes nothing, so a
    normal ``-q`` log does not change. On Windows the timeout dump lists
    every thread too (#689's windows-latest dump, job 126387390673, has all
    16).
    """

    def __init__(self) -> None:
        self._labels: queue.SimpleQueue[str] = queue.SimpleQueue()
        threading.Thread(target=self._hold, name="aelix-running-test-label", daemon=True).start()

    def show(self, label: str) -> None:
        self._labels.put(label)

    def _hold(self) -> None:
        label = self._labels.get()
        while True:
            # faulthandler prints ``co_name`` (measured on 3.11, 3.12 and 3.14).
            code = _labelled_wait.__code__.replace(
                co_name=label, co_filename=RUNNING_TEST_LABEL_FILE
            )
            label = types.FunctionType(code, {})(self._labels)


_running_test_label: _RunningTestLabel | None = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    """Put the running test's nodeid on the label thread's stack (#689)."""

    global _running_test_label
    if not item.config.pluginmanager.hasplugin("faulthandler"):
        return (yield)
    if _running_test_label is None:
        _running_test_label = _RunningTestLabel()
    _running_test_label.show(item.nodeid)
    try:
        return (yield)
    finally:
        _running_test_label.show(f"no test running; the last one was {item.nodeid}")


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Bound interpreter exit after pytest's per-test watchdog is disarmed.

    A stranded non-daemon executor worker can leave all tests green and then
    hang threading._shutdown forever (#428). Use the original stderr, not
    pytest's temporary capture fd, and arm after pytest's own fault-handler
    teardown. Disabling exit_on_timeout also disables this final watchdog.
    """

    if not config.pluginmanager.hasplugin("faulthandler"):
        return
    if not config.getini("faulthandler_exit_on_timeout"):
        return

    import faulthandler
    import sys

    faulthandler.dump_traceback_later(180, exit=True, file=sys.__stderr__)
