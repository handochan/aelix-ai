"""The launch-time update check: what it decides, and what it refuses to do.

The feature's whole value is that it is correct when it speaks and silent when
it is not sure, so most of what is asserted here is silence.

THE FIRST TEST IS THE ONE THAT MATTERS. ``_parse`` swallows an ImportError so a
missing comparator degrades to "no opinion" rather than crashing a launch — and
that is exactly how this feature could ship as a silent no-op. It nearly did:
``tests/packaging/`` used to shadow the real ``packaging`` distribution
(pytest puts ``tests/`` on ``sys.path``, and that directory has an
``__init__.py``), so ``import packaging`` succeeded, ``packaging.version`` did
not, and every decision below returned ``None`` while the suite went green. The
directory is now ``tests/packaging_gate/`` and this file starts by proving the
comparator is real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.update_check import (
    CHECK_INTERVAL_S,
    InstallMethod,
    check_for_update,
    choose_release,
    detect_install_method,
    is_due,
    is_offline,
    read_cache,
    upgrade_command,
    write_cache,
)
from aelix_coding_agent.update_check import _parse as parse_version


def _feed(latest: dict[str, Any] | None, stable: dict[str, Any] | None = None) -> dict:
    return {"schemaVersion": 1, "latest": latest, "latestStable": stable}


def _rel(version: str, prerelease: bool) -> dict[str, Any]:
    tag = "v" + version
    return {
        "version": version,
        "tag": tag,
        "prerelease": prerelease,
        "url": f"https://example.invalid/{tag}",
    }


# === the comparator ========================================================


def test_the_version_comparator_is_actually_available() -> None:
    """A missing or shadowed ``packaging`` turns this whole feature off, quietly.

    Asserted directly rather than inferred from behaviour, because every
    behavioural assertion in this file would also pass if the comparator were
    gone — they would all be measuring the degraded path.
    """

    assert parse_version("0.1.0b1") is not None, (
        "packaging.version is not importable, so the update check is a no-op. "
        "Check for a directory on sys.path named like a distribution."
    )
    import packaging.version

    assert Path(packaging.version.__file__).name == "version.py"


def test_the_tag_and_the_wheel_version_are_the_same_release() -> None:
    """``v0.1.0-beta.1`` and ``0.1.0b1`` are one release, two spellings.

    A ``!=`` check on the strings announces an update to a user who already has
    it — every launch, forever. That is pi's own fallback bug; it does not bite
    pi because its tag spelling matches its package.json.
    """

    assert parse_version("v0.1.0-beta.1") == parse_version("0.1.0b1")
    assert "0.1.0b1" != "v0.1.0-beta.1"  # the string compare that would misfire


@pytest.mark.parametrize(
    ("moving_tag",),
    [("nightly",), ("tip",), ("stable",), ("latest",)],
)
def test_a_moving_tag_is_skipped_not_treated_as_newer(moving_tag: str) -> None:
    assert parse_version(moving_tag) is None
    assert choose_release("0.1.0b1", _feed(_rel(moving_tag, True))) is None


# === the decision rule =====================================================


def test_a_prerelease_user_is_offered_the_next_prerelease() -> None:
    chosen = choose_release("0.1.0b1", _feed(_rel("0.1.0b2", True)))
    assert chosen is not None
    assert chosen.version == "0.1.0b2"


def test_a_prerelease_user_is_offered_the_stable_when_it_lands() -> None:
    """Withholding it would strand every beta tester on the day 1.0 ships —
    the same failure this feature exists to prevent, one release later."""

    chosen = choose_release("0.1.0b2", _feed(_rel("0.1.0", False)))
    assert chosen is not None
    assert chosen.version == "0.1.0"


def test_a_stable_user_is_never_offered_a_prerelease() -> None:
    """They opted out of the train. ``AELIX_VERSION`` is how they opt back in."""

    assert choose_release("0.1.0", _feed(_rel("0.2.0b1", True))) is None


def test_a_stable_user_is_offered_the_newer_stable_from_a_mixed_feed() -> None:
    feed = _feed(_rel("0.2.0b1", True), _rel("0.1.1", False))
    chosen = choose_release("0.1.0", feed)
    assert chosen is not None
    assert chosen.version == "0.1.1"


def test_the_same_version_is_not_an_update() -> None:
    assert choose_release("0.1.0b1", _feed(_rel("0.1.0b1", True))) is None
    # ...including when the feed spells it as the tag does.
    assert choose_release("0.1.0b1", _feed(_rel("0.1.0-beta.1", True))) is None


def test_an_older_feed_is_not_an_update() -> None:
    assert choose_release("0.2.0", _feed(_rel("0.1.0", False))) is None


def test_beta_10_is_newer_than_beta_2() -> None:
    """The ordering a string compare gets backwards."""

    chosen = choose_release("0.1.0b2", _feed(_rel("0.1.0b10", True)))
    assert chosen is not None
    assert "0.1.0b2" > "0.1.0b10"  # the naive compare, for the record


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"latest": None},
        {"latest": "not-a-dict"},
        {"latest": {"version": "0.9.0"}},  # no tag, no url
        {"latest": {"version": 9, "tag": "v9", "url": "u"}},  # wrong types
    ],
)
def test_a_malformed_feed_says_nothing(broken: dict[str, Any]) -> None:
    assert choose_release("0.1.0b1", broken) is None


# === install-method detection ==============================================


def _fake_dist(tmp_path: Path, installer: str | None, direct_url: dict | None) -> tuple:
    d = tmp_path / "aelix_coding_agent-0.1.0b1.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    ip = d / "INSTALLER"
    if installer is not None:
        ip.write_text(installer, encoding="utf-8")
    du = d / "direct_url.json"
    if direct_url is not None:
        du.write_text(json.dumps(direct_url), encoding="utf-8")
    return (ip if installer is not None else None, du if direct_url is not None else None)


def test_an_editable_checkout_is_detected_and_never_advised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contributor must not be told to reinstall over their own worktree."""

    import aelix_coding_agent.update_check as uc

    files = _fake_dist(
        tmp_path,
        "pip",
        {"url": "file:///repo", "dir_info": {"editable": True}},
    )
    monkeypatch.setattr(uc, "_dist_files", lambda: files)
    monkeypatch.setattr(uc.sys, "prefix", str(tmp_path / "prefix"))
    assert detect_install_method() is InstallMethod.CHECKOUT
    assert upgrade_command(InstallMethod.CHECKOUT, "v9.9.9") is None


def test_a_non_editable_local_directory_install_does_not_KeyError(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEASURED: a local-directory (non-editable) install writes
    ``"dir_info": {}`` with no ``editable`` key at all, so indexing it raises.
    That is why ``_is_editable`` uses ``.get`` — and why this test exists."""

    import aelix_coding_agent.update_check as uc

    files = _fake_dist(tmp_path, "uv", {"url": "file:///repo", "dir_info": {}})
    monkeypatch.setattr(uc, "_dist_files", lambda: files)
    monkeypatch.setattr(uc.sys, "prefix", str(tmp_path / "prefix"))
    assert detect_install_method() is not InstallMethod.CHECKOUT


@pytest.mark.parametrize(
    ("receipt", "expected"),
    [
        ('[tool]\nrequirements = [{ name = "aelix", specifier = "==0.1.0b1" }]\n', InstallMethod.INSTALL_SH),
        ('[tool.options]\nfind-links = ["/tmp/x"]\n', InstallMethod.INSTALL_SH),
        ('[tool]\nrequirements = [{ name = "aelix" }]\n', InstallMethod.UV_TOOL),
        ('[tool]\nrequirements = [{ name = "aelix", directory = "/repo" }]\n', InstallMethod.CHECKOUT),
    ],
)
def test_the_uv_receipt_separates_install_sh_from_a_plain_uv_tool_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt: str, expected: InstallMethod
) -> None:
    """The distinction that decides which command is printed.

    ``dist-info`` alone cannot make it: neither install writes
    ``direct_url.json``. The receipt can, because ``install.sh`` pins ``==`` and
    passes ``--find-links`` while a plain index install does neither.
    """

    import aelix_coding_agent.update_check as uc

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    monkeypatch.setattr(uc, "_dist_files", lambda: (None, None))
    monkeypatch.setattr(uc.sys, "prefix", str(prefix))
    assert detect_install_method() is expected


def test_pipx_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import aelix_coding_agent.update_check as uc

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(uc, "_dist_files", lambda: (None, None))
    monkeypatch.setattr(uc.sys, "prefix", str(prefix))
    assert detect_install_method() is InstallMethod.PIPX
    assert upgrade_command(InstallMethod.PIPX, "v9.9.9") == "pipx upgrade aelix"


def test_an_unrecognised_install_gets_a_link_and_no_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guess here can uninstall someone.

    MEASURED: ``uv tool install aelix@latest`` — uv's own suggested remedy —
    resolves the PyPI reservation placeholder, finds no entry points, prints
    "No executables ... removing tool", and leaves ``uv tool list`` empty.
    Saying nothing is strictly better than saying that.
    """

    import aelix_coding_agent.update_check as uc

    monkeypatch.setattr(uc, "_dist_files", lambda: (None, None))
    monkeypatch.setattr(uc.sys, "prefix", str(tmp_path / "nothing-here"))
    assert detect_install_method() is InstallMethod.UNKNOWN
    assert upgrade_command(InstallMethod.UNKNOWN, "v9.9.9") is None


def test_the_install_sh_command_carries_the_version_pin() -> None:
    """Re-running the installer unpinned would fetch whatever is newest, which
    is not necessarily what the user was just told about."""

    cmd = upgrade_command(InstallMethod.INSTALL_SH, "v0.1.0-beta.2")
    assert cmd is not None
    assert "AELIX_VERSION=v0.1.0-beta.2" in cmd
    assert "install.sh" in cmd


# === the cache =============================================================


def test_a_fresh_check_is_due_and_a_recent_one_is_not() -> None:
    assert is_due({}, now=1000.0)
    assert not is_due({"last_checked_at": 1000.0}, now=1000.0 + CHECK_INTERVAL_S - 1)
    assert is_due({"last_checked_at": 1000.0}, now=1000.0 + CHECK_INTERVAL_S)


def test_a_clock_that_went_backwards_does_not_disable_the_check_forever() -> None:
    """A VM resuming from a snapshot, or a corrected NTP step, would otherwise
    pin ``last_checked_at`` in the future and the check would never run again."""

    assert is_due({"last_checked_at": 9_999_999_999.0}, now=1000.0)


def test_a_corrupt_cache_reads_as_empty(tmp_path: Path) -> None:
    (tmp_path / "update_check.json").write_text("{not json", encoding="utf-8")
    assert read_cache(tmp_path) == {}


def test_the_cache_round_trips_and_is_written_atomically(tmp_path: Path) -> None:
    write_cache({"last_checked_at": 42.0}, tmp_path)
    assert read_cache(tmp_path)["last_checked_at"] == 42.0
    # No temp file left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["update_check.json"]


def test_writing_to_an_unwritable_place_does_not_raise(tmp_path: Path) -> None:
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    write_cache({"last_checked_at": 1.0}, blocked / "sub")  # must not raise


# === the whole check =======================================================


def test_a_failing_fetch_is_silence_not_an_error(tmp_path: Path) -> None:
    def boom() -> dict:
        raise OSError("no network")

    assert (
        check_for_update(
            current_version="0.1.0b1", fetch=boom, now=1.0, agent_dir=tmp_path
        )
        is None
    )
    # ...and the attempt is recorded, so it does not retry on the next launch.
    assert read_cache(tmp_path)["last_checked_at"] == 1.0


def test_a_second_launch_inside_the_interval_does_not_touch_the_network(
    tmp_path: Path,
) -> None:
    calls: list[int] = []

    def fetch() -> dict:
        calls.append(1)
        return _feed(_rel("0.1.0b2", True))

    first = check_for_update(
        current_version="0.1.0b1", fetch=fetch, now=1.0, agent_dir=tmp_path
    )
    assert first is not None
    second = check_for_update(
        current_version="0.1.0b1", fetch=fetch, now=2.0, agent_dir=tmp_path
    )
    assert calls == [1], "the network was consulted twice inside the interval"
    # The ANSWER is cached too, so the notice survives without a second fetch.
    assert second is not None
    assert second.release.version == "0.1.0b2"


def test_a_source_tree_run_is_never_notified(tmp_path: Path) -> None:
    """``cli/config.py`` reports ``0.0.0-dev`` when there is no distribution —
    i.e. a contributor. It sorts below everything, so without this guard the
    check would nag on every launch of every checkout.

    THE SENTINEL COUNTS, IT DOES NOT RAISE. This test used to prove nothing:
    its ``fetch`` raised ``AssertionError`` to mean "must not be called", and
    ``check_for_update`` catches every exception from ``fetch`` on purpose
    (offline is not an error to report). So with the guard deleted the fetch
    ran, its complaint was swallowed, the feed came back ``None`` and the
    function returned ``None`` anyway — green, for the wrong reason. Measured
    by deleting the guard: 38 passed. An exception is never a usable sentinel
    inside a function whose contract is to swallow exceptions.
    """

    calls: list[int] = []

    def fetch() -> dict:
        calls.append(1)
        return _feed(_rel("9.9.9", False))

    assert (
        check_for_update(
            current_version="0.0.0-dev", fetch=fetch, now=1.0, agent_dir=tmp_path
        )
        is None
    )
    assert calls == [], "a source checkout consulted the network"
    assert not (tmp_path / "update_check.json").exists(), (
        "a source checkout left a cache file behind"
    )


def test_a_hostile_feed_cannot_make_the_check_raise(tmp_path: Path) -> None:
    for payload in ("", [], {"latest": {"version": "../../etc"}}, {"latest": 3}):

        def fetch(p: Any = payload) -> Any:
            return p

        assert (
            check_for_update(
                current_version="0.1.0b1", fetch=fetch, now=1.0, agent_dir=tmp_path
            )
            is None
        )


@pytest.mark.parametrize("var", ["PI_OFFLINE", "AELIX_OFFLINE"])
def test_offline_is_read_from_the_names_that_already_exist(
    var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No new environment name. ``cli/entry.py`` records why: every
    ``os.environ`` read is a consumer a hostile cwd ``.env`` may try to drive."""

    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.delenv("AELIX_OFFLINE", raising=False)
    assert not is_offline()
    monkeypatch.setenv(var, "1")
    assert is_offline()
    # Strict truthiness, mirroring cli/extension_install.py: "0" reads as OFF.
    monkeypatch.setenv(var, "0")
    assert not is_offline()


# === the transport, which had no test until a sabotage round said so =======
#
# Every promise below was made in prose — in the module docstring, in the
# commit message, in SECURITY.md — and none of them was asserted. A sabotage
# round removed the byte cap, made the redirect handler `pass`, and deleted the
# non-HTTPS check on the final URL; all three left 38 tests green.


class _FakeResponse:
    """The two attributes ``default_fetch`` actually consults."""

    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self.url = url

    def read(self, amount: int | None = None) -> bytes:
        return self._body if amount is None else self._body[:amount]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _opener_returning(response: _FakeResponse) -> Any:
    class _Opener:
        def open(self, _req: Any, timeout: float | None = None) -> _FakeResponse:
            return response

    return lambda *_handlers: _Opener()


def test_a_feed_larger_than_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile or broken host must not be able to stream into memory.

    Reads ``MAX_FEED_BYTES + 1`` and rejects on the extra byte, so the cap is
    enforced on what was READ rather than on what a ``Content-Length`` header
    claimed — a header is the attacker's to write.
    """

    from aelix_coding_agent import update_check as uc

    body = b'{"schemaVersion": 1, "pad": "' + b"x" * uc.MAX_FEED_BYTES + b'"}'
    monkeypatch.setattr(
        uc.urllib.request, "build_opener", _opener_returning(
            _FakeResponse(body, "https://example.invalid/feed.json")
        )
    )
    with pytest.raises(ValueError, match="larger than the cap"):
        uc.default_fetch("https://example.invalid/feed.json")


def test_a_feed_inside_the_cap_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the test above: without it, a cap of zero would pass."""

    from aelix_coding_agent import update_check as uc

    monkeypatch.setattr(
        uc.urllib.request, "build_opener", _opener_returning(
            _FakeResponse(b'{"schemaVersion": 1}', "https://example.invalid/feed.json")
        )
    )
    assert uc.default_fetch("https://example.invalid/feed.json") == {"schemaVersion": 1}


def test_a_response_that_ended_on_plain_http_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belt to the redirect handler's braces.

    ``install.sh``'s downloader is ``curl -fSL`` with no ``--proto-redir``, and
    a local probe confirmed it follows HTTPS -> HTTP and returns the plaintext
    body. The Python side refuses that, and refuses it twice: in the handler,
    and again on the URL the response actually came from.
    """

    from aelix_coding_agent import update_check as uc

    monkeypatch.setattr(
        uc.urllib.request, "build_opener", _opener_returning(
            _FakeResponse(b'{"schemaVersion": 1}', "http://evil.invalid/feed.json")
        )
    )
    with pytest.raises(ValueError, match="non-HTTPS"):
        uc.default_fetch("https://example.invalid/feed.json")


def test_the_redirect_handler_refuses_to_leave_https() -> None:
    """The handler itself, not the path that installs it."""

    from aelix_coding_agent import update_check as uc

    handler = uc._HttpsOnlyRedirect()
    req = uc.urllib.request.Request("https://example.invalid/feed.json")
    with pytest.raises(ValueError, match="insecure redirect"):
        handler.redirect_request(req, None, 302, "Found", {}, "http://evil.invalid/x")


def test_the_redirect_handler_allows_https_to_https() -> None:
    """The control: a handler that refused everything would also pass above."""

    from aelix_coding_agent import update_check as uc

    handler = uc._HttpsOnlyRedirect()
    req = uc.urllib.request.Request("https://example.invalid/feed.json")
    result = handler.redirect_request(
        req, None, 302, "Found", {}, "https://example.invalid/moved.json"
    )
    assert result is not None


# === the command, which is the one that can destroy an install =============


def test_no_install_method_is_ever_advised_to_reinstall_from_pypi() -> None:
    """MEASURED, and the reason this feature detects instead of guessing.

    ``uv tool install aelix@latest`` is what uv itself suggests, and running it
    resolves the PyPI name reservation, finds no entry points and REMOVES the
    tool — the user's aelix is gone. ``pip install aelix`` lands in the same
    place. Every command this module can emit is checked against that shape,
    for every method, including ones added later.
    """

    forbidden = ("aelix@latest", "install aelix", "uninstall")
    for method in InstallMethod:
        command = upgrade_command(method, "v9.9.9")
        if command is None:
            continue
        for shape in forbidden:
            assert shape not in command, (
                f"{method.value} would be advised {command!r}, which contains "
                f"{shape!r} — the shape that uninstalls aelix"
            )


def test_the_methods_that_cannot_be_upgraded_safely_get_no_command() -> None:
    """A guess here uninstalls someone. Silence is the correct answer."""

    assert upgrade_command(InstallMethod.CHECKOUT, "v9.9.9") is None
    assert upgrade_command(InstallMethod.UNKNOWN, "v9.9.9") is None
