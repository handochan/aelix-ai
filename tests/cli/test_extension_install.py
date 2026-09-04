"""Issue #19 (ADR-0185) — ``aelix extension install`` tests.

Unit-level with an injected pip runner + input_fn, so NO real pip runs and the
environment is never mutated. Covers target classification, pip-arg building,
the consent gate, the offline guard, arg parsing, and the entry.py verb dispatch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import ExtensionSourceObject, SettingsManager
from aelix_coding_agent.cli import extension_install as _ei_mod
from aelix_coding_agent.cli.extension_install import (
    build_pip_args,
    classify_source,
    classify_target,
    install_extension,
    run_extension_command,
    run_extension_command_async,
)

from tests.env_sandbox import sandbox_home


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point settings I/O at a throwaway file so tests NEVER touch ~/.aelix.

    The sync ``run_extension_command`` shim builds a real ``SettingsManager``
    (via ``_load_settings``) when no ``settings`` is injected; without this the
    install/record path would read and WRITE the developer's real settings.json.
    ``AELIX_SETTINGS_PATH`` pins the GLOBAL settings file directly (honored by
    ``SettingsManager.create`` — the coding-agent env is ``AELIX_CODING_AGENT_DIR``,
    which only sets the agent DIR, so the settings-path override is the reliable
    isolation lever). The project scope is ``cwd/.aelix/settings.json`` (absent in
    this repo), and every stateful test injects an in-memory manager anyway.
    """

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    # Also decouple the PROJECT scope (``cwd/.aelix/settings.json``) — chdir into
    # the throwaway dir so a real repo-root ``.aelix/settings.json`` can never
    # merge into a ``settings=None`` test's manager (review-hardening LOW).
    monkeypatch.chdir(tmp_path)


#: The REAL pip-config search path, captured before ``_no_ambient_pip_index``
#: stubs it out, so the ordering test can still exercise the genuine function.
_REAL_PIP_CONFIG_CANDIDATES = _ei_mod._pip_config_candidates

#: Every ambient variable that can steer index resolution on either backend.
_INDEX_ENV_NAMES = (
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_CONFIG_FILE",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_CONFIG_FILE",
    "UV_NO_INDEX",
    "UV_FIND_LINKS",
)

#: The REAL uv-config search path, captured before the hermetic fixture stubs it.
_REAL_UV_CONFIG_FILES = _ei_mod._uv_config_files


@pytest.fixture(autouse=True)
def _no_ambient_pip_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the uv backend's index translation depend on the TEST, not the host.

    The uv backend translates pip's ambient index configuration (pip honors
    ``PIP_INDEX_URL`` / ``pip.conf``, uv honors neither), so a developer box or CI
    image carrying ``/etc/pip.conf`` — or a ``~/.config/uv/uv.toml`` that SUPPRESSES
    the translation — would otherwise silently change what these tests observe.
    Clearing the env and emptying BOTH config search paths makes "no ambient index,
    no uv config" the default; the translation tests opt back in.
    """

    for name in _INDEX_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_ei_mod, "_pip_config_candidates", lambda env=None: [])
    monkeypatch.setattr(
        _ei_mod, "_uv_config_files", lambda env=None, cwd=None: []
    )


def _mem_settings() -> SettingsManager:
    """A disk-free settings manager for asserting on the persisted source list."""

    return SettingsManager.in_memory()


class _FakeRunner:
    """Records the pip argv it was handed; returns a chosen exit code."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


def _yes(_prompt: str) -> str:
    return "y"


def _no(_prompt: str) -> str:
    return "n"


# === classify_target =====================================================


def test_classify_local_path_wins(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    assert classify_target(str(tmp_path / "ext")) == "path"


@pytest.mark.parametrize(
    "url",
    [
        "git+https://git.corp/ext.git",
        "https://github.com/x/ext.git",
        "ssh://git@host/ext",
        "git@host:x/ext.git",
        "git://host/ext",
    ],
)
def test_classify_git_urls(url: str) -> None:
    assert classify_target(url) == "git"


@pytest.mark.parametrize("spec", ["my-ext", "my-ext==1.2.0", "my_ext[extra]"])
def test_classify_pypi_specs(spec: str) -> None:
    assert classify_target(spec) == "pypi"


# === build_pip_args ======================================================


def test_build_pip_args_path_resolves_absolute(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    args = build_pip_args(str(tmp_path / "ext"), "path", index_url=None)
    assert args[1:4] == ["-m", "pip", "install"]
    assert args[-1] == str((tmp_path / "ext").resolve())


def test_build_pip_args_git_adds_scheme() -> None:
    args = build_pip_args("https://git.corp/ext.git", "git", index_url=None)
    assert args[-1] == "git+https://git.corp/ext.git"
    # An already-prefixed git+ spec is left as-is.
    args2 = build_pip_args("git+ssh://git@host/ext", "git", index_url=None)
    assert args2[-1] == "git+ssh://git@host/ext"


def test_build_pip_args_pypi_index_url() -> None:
    args = build_pip_args("my-ext", "pypi", index_url="https://idx.corp/simple")
    assert args[-3:] == ["my-ext", "--index-url", "https://idx.corp/simple"]


# === install_extension consent + result ==================================


def test_install_confirm_yes_runs_pip(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner(returncode=0)
    code = install_extension(str(tmp_path / "ext"), input_fn=_yes, runner=runner)
    assert code == 0
    assert len(runner.calls) == 1


def test_install_confirm_no_aborts_without_pip(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), input_fn=_no, runner=runner)
    assert code == 2  # abort = "did not run pip", distinct from a pip failure
    assert runner.calls == []  # pip NEVER invoked on a declined install


def test_install_yes_flag_skips_prompt(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()

    def _boom(_p: str) -> str:  # must NOT be called under --yes
        raise AssertionError("prompt shown despite yes=True")

    runner = _FakeRunner()
    code = install_extension(
        str(tmp_path / "ext"), yes=True, input_fn=_boom, runner=runner
    )
    assert code == 0
    assert len(runner.calls) == 1


def test_install_closed_stdin_denies(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()

    def _eof(_p: str) -> str:
        raise EOFError

    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), input_fn=_eof, runner=runner)
    assert code == 2  # closed stdin denies → "did not run pip"
    assert runner.calls == []


def test_install_failure_normalises_the_installer_code_and_shows_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The installer's own code is SHOWN, not returned (#154 round 2).

    pip's codes are not disjoint from this verb's — ``VIRTUALENV_NOT_FOUND = 3``
    is ``_INSTALL_NOT_BOUND`` and ``UNKNOWN_ERROR = 2`` is ``_EXIT_DIDNT_RUN`` —
    so passing them through made "installed but inert" and "pip refused to run"
    indistinguishable. 1 now means "the installer ran and failed", always.
    """

    (tmp_path / "ext").mkdir()
    runner = _FakeRunner(returncode=7)
    code = install_extension(str(tmp_path / "ext"), yes=True, runner=runner)
    assert code == 1
    assert "(exit 7)" in capsys.readouterr().err


def test_install_offline_pypi_without_index_refused() -> None:
    runner = _FakeRunner()
    code = install_extension("some-pkg", yes=True, offline=True, runner=runner)
    assert code == 2
    assert runner.calls == []  # guarded before pip


def test_install_offline_pypi_with_index_ok() -> None:
    runner = _FakeRunner()
    code = install_extension(
        "some-pkg", yes=True, offline=True, index_url="https://idx.corp", runner=runner
    )
    assert code == 0
    assert "--index-url" in runner.calls[0]


def test_install_offline_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_OFFLINE", "1")
    runner = _FakeRunner()
    code = install_extension("some-pkg", yes=True, runner=runner)
    assert code == 2  # env offline blocks pypi-without-index too


# === run_extension_command arg parsing ===================================


def test_command_requires_target() -> None:
    assert run_extension_command(["install"]) == 2


def test_command_unknown_subcommand() -> None:
    assert run_extension_command(["frobnicate"]) == 2


def test_command_no_args_usage() -> None:
    assert run_extension_command([]) == 2


def test_command_help() -> None:
    assert run_extension_command(["--help"]) == 0


def test_command_parses_flags(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = run_extension_command(
        ["install", str(tmp_path / "ext"), "--yes", "--index-url", "https://idx"],
        runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1


def test_command_index_url_missing_value() -> None:
    assert run_extension_command(["install", "pkg", "--index-url"]) == 2


def test_command_unknown_flag() -> None:
    assert run_extension_command(["install", "pkg", "--bogus"]) == 2


def test_command_double_target_rejected(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    assert run_extension_command(["install", str(tmp_path / "ext"), "extra"]) == 2


# === entry.py verb dispatch ==============================================


async def test_async_main_routes_extension_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli import entry
    from aelix_coding_agent.cli import extension_install as ei

    (tmp_path / "ext").mkdir()
    captured: list[list[str]] = []

    async def _fake_run(args: list[str], **_kw: object) -> int:
        captured.append(args)
        return 0

    # Dispatch awaits the ASYNC entry directly (it is already inside the
    # asyncio.run loop) — patch that, not the sync shim.
    monkeypatch.setattr(ei, "run_extension_command_async", _fake_run)
    code = await entry._async_main(["extension", "install", str(tmp_path / "ext")])
    assert code == 0
    assert captured == [["install", str(tmp_path / "ext")]]


# === review-fix coverage =================================================


def test_classify_empty_target_is_pypi_not_path() -> None:
    # Review MEDIUM: "" must NOT classify as a path (which would install the cwd).
    assert classify_target("") == "pypi"
    assert classify_target("   ") == "pypi"


def test_install_empty_target_refused() -> None:
    runner = _FakeRunner()
    assert install_extension("", yes=True, runner=runner) == 2
    assert install_extension("   ", yes=True, runner=runner) == 2
    assert runner.calls == []  # cwd is never installed


def test_command_empty_target_refused() -> None:
    runner = _FakeRunner()
    assert run_extension_command(["install", ""], runner=runner) == 2
    assert runner.calls == []


def test_git_scp_shorthand_rewritten_to_ssh() -> None:
    # Review LOW: git@host:path has no :// so a bare git+ prefix is unparseable
    # by pip; rewrite to git+ssh://git@host/path.
    args = build_pip_args("git@github.com:org/repo.git", "git", index_url=None)
    assert args[-1] == "git+ssh://git@github.com/org/repo.git"
    # A scheme'd git URL keeps the plain git+ prefix.
    assert build_pip_args("https://h/r.git", "git", index_url=None)[-1] == "git+https://h/r.git"


def test_build_pip_args_pypi_without_index() -> None:
    import sys as _sys

    assert build_pip_args("my-ext", "pypi", index_url=None) == [
        _sys.executable, "-m", "pip", "install", "my-ext",
    ]


@pytest.mark.parametrize("reply", ["y", "Y", " y ", "yes", "YES", " Yes "])
def test_consent_accept_variants_run_pip(tmp_path: Path, reply: str) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), input_fn=lambda _p: reply, runner=runner)
    assert code == 0
    assert len(runner.calls) == 1


@pytest.mark.parametrize("reply", ["n", "no", "", "  ", "nope", "x"])
def test_consent_decline_variants_abort(tmp_path: Path, reply: str) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), input_fn=lambda _p: reply, runner=runner)
    assert code == 2
    assert runner.calls == []


def test_install_offline_path_not_blocked(tmp_path: Path) -> None:
    # Review LOW: offline only blocks pypi-without-index; a local path installs fine.
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), yes=True, offline=True, runner=runner)
    assert code == 0
    assert len(runner.calls) == 1


def test_missing_pip_returns_didnt_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review LOW: a missing backend is detected up front (exit 2), not mislabeled
    # as an install failure. The pre-check applies to the DEFAULT runner only, so
    # this uses runner=None + a forced-missing find_spec — the guard returns
    # before any real subprocess.
    #
    # #113: `uv` must be knocked out TOO. Since the uv fallback landed, a missing
    # pip alone no longer aborts — on a machine with uv on PATH (this dev image
    # included) leaving `which` real would drive a REAL `uv pip install` here.
    from aelix_coding_agent.cli import extension_install as ei

    (tmp_path / "ext").mkdir()
    monkeypatch.setattr(ei.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(ei.shutil, "which", lambda _name: None)
    code = install_extension(str(tmp_path / "ext"), yes=True)  # default runner
    assert code == 2


def test_pi_offline_zero_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review NIT: PI_OFFLINE=0 must read as OFF (strict truthiness), so a
    # pypi-without-index install is NOT blocked.
    monkeypatch.setenv("PI_OFFLINE", "0")
    runner = _FakeRunner()
    code = install_extension("some-pkg", yes=True, runner=runner)
    assert code == 0  # not blocked
    assert len(runner.calls) == 1


def test_command_index_url_empty_value_rejected() -> None:
    assert run_extension_command(["install", "pkg", "--index-url="]) == 2
    assert run_extension_command(["install", "pkg", "--index-url", ""]) == 2


def test_command_offline_flag_blocks_pypi() -> None:
    runner = _FakeRunner()
    code = run_extension_command(["install", "some-pkg", "--offline"], runner=runner)
    assert code == 2
    assert runner.calls == []


def test_command_double_dash_allows_dash_target(tmp_path: Path) -> None:
    dash = tmp_path / "-weird"
    dash.mkdir()
    runner = _FakeRunner()
    code = run_extension_command(["install", "--yes", "--", str(dash)], runner=runner)
    assert code == 0
    assert runner.calls[0][-1] == str(dash.resolve())


def test_command_install_help() -> None:
    assert run_extension_command(["install", "--help"]) == 0
    assert run_extension_command(["-h"]) == 0


async def test_async_main_non_extension_argv_not_intercepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a normal chat message must NOT be swallowed by the verb
    # dispatch — only a leading literal 'extension' routes.
    from aelix_coding_agent.cli import entry
    from aelix_coding_agent.cli import extension_install as ei

    async def _boom(*_a: object, **_k: object) -> int:
        raise AssertionError("run_extension_command called for non-extension argv")

    monkeypatch.setattr(ei, "run_extension_command_async", _boom)

    def _fake_parse(_argv: list[str]) -> object:
        raise SystemExit(0)  # short-circuit past the rest of _async_main

    monkeypatch.setattr(entry, "parse_args", _fake_parse)
    with pytest.raises(SystemExit):
        await entry._async_main(["extensions-are-cool"])  # NOT the 'extension' verb


# === #32-A (ADR-0186): classify_source ===================================


def test_classify_source_path(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    assert classify_source(str(tmp_path / "ext")) == "path"


@pytest.mark.parametrize(
    "url",
    ["git+https://git.corp/e.git", "https://github.com/x/e.git", "git@h:x/e.git"],
)
def test_classify_source_git(url: str) -> None:
    assert classify_source(url) == "git"


@pytest.mark.parametrize(
    "url", ["https://pypi.corp/simple", "http://idx.local/simple/"]
)
def test_classify_source_plain_url_is_index(url: str) -> None:
    # A plain http(s) URL (no .git) is a pip INDEX, not a git repo or a pypi name.
    assert classify_source(url) == "index"


@pytest.mark.parametrize("bad", ["", "   ", "my-ext", "my-ext==1.2"])
def test_classify_source_bare_name_is_invalid(bad: str) -> None:
    # A bare package name is an install TARGET, not a registrable source.
    assert classify_source(bad) is None


# === #32-A: source add / list / remove ===================================


def test_source_add_index_persists() -> None:
    mem = _mem_settings()
    code = run_extension_command(
        ["source", "add", "https://pypi.corp/simple"], settings=mem
    )
    assert code == 0
    sources = mem.get_extension_sources()
    assert sources == [
        ExtensionSourceObject(spec="https://pypi.corp/simple", kind="index")
    ]


def test_source_add_git_persists() -> None:
    mem = _mem_settings()
    code = run_extension_command(
        ["source", "add", "https://github.com/x/ext.git"], settings=mem
    )
    assert code == 0
    assert mem.get_extension_sources()[0].kind == "git"


def test_source_add_path_resolves_absolute(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    mem = _mem_settings()
    code = run_extension_command(
        ["source", "add", str(tmp_path / "ext")], settings=mem
    )
    assert code == 0
    s = mem.get_extension_sources()[0]
    assert s.kind == "path"
    assert s.spec == str((tmp_path / "ext").resolve())  # normalized


def test_source_add_bare_name_rejected() -> None:
    mem = _mem_settings()
    code = run_extension_command(["source", "add", "some-pkg"], settings=mem)
    assert code == 2
    assert mem.get_extension_sources() == []  # never registered


def test_source_add_dedupes() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://idx/simple", "kind": "index"}]}
    )
    code = run_extension_command(
        ["source", "add", "https://idx/simple"], settings=mem
    )
    assert code == 0  # idempotent
    assert len(mem.get_extension_sources()) == 1


def test_source_add_requires_target() -> None:
    assert run_extension_command(["source", "add"], settings=_mem_settings()) == 2


def test_source_list_empty() -> None:
    assert run_extension_command(["source", "list"], settings=_mem_settings()) == 0


def test_source_list_populated(capsys: pytest.CaptureFixture[str]) -> None:
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": "https://idx/simple", "kind": "index"},
                {"spec": "git+https://h/r.git", "kind": "git", "name": "r"},
            ]
        }
    )
    assert run_extension_command(["source", "list"], settings=mem) == 0
    out = capsys.readouterr().out
    assert "https://idx/simple" in out
    assert "git+https://h/r.git" in out
    assert "installed as r" in out


def test_source_remove_by_spec() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://idx/simple", "kind": "index"}]}
    )
    code = run_extension_command(
        ["source", "remove", "https://idx/simple"], settings=mem
    )
    assert code == 0
    assert mem.get_extension_sources() == []


def test_source_remove_nonexistent_errors() -> None:
    mem = _mem_settings()
    assert run_extension_command(["source", "remove", "nope"], settings=mem) == 2


def test_source_unknown_action() -> None:
    assert run_extension_command(["source", "frob"], settings=_mem_settings()) == 2


def test_source_no_action() -> None:
    assert run_extension_command(["source"], settings=_mem_settings()) == 2


# === #32-A: install resolution against registered index sources ==========


def test_install_bare_name_resolves_index_source() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://idx.corp/simple", "kind": "index"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["install", "some-pkg", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    argv = runner.calls[0]
    assert "--index-url" in argv
    assert argv[argv.index("--index-url") + 1] == "https://idx.corp/simple"


def test_install_explicit_index_url_wins_over_registered() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://registered/simple", "kind": "index"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["install", "some-pkg", "--yes", "--index-url", "https://explicit/simple"],
        settings=mem,
        runner=runner,
    )
    assert code == 0
    argv = runner.calls[0]
    assert argv[argv.index("--index-url") + 1] == "https://explicit/simple"
    assert "https://registered/simple" not in argv  # registered NOT folded in


def test_install_multiple_index_sources_first_primary_rest_extra() -> None:
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": "https://a/simple", "kind": "index"},
                {"spec": "https://b/simple", "kind": "index"},
            ]
        }
    )
    runner = _FakeRunner()
    run_extension_command(["install", "pkg", "--yes"], settings=mem, runner=runner)
    argv = runner.calls[0]
    assert argv[argv.index("--index-url") + 1] == "https://a/simple"
    assert "--extra-index-url" in argv
    assert argv[argv.index("--extra-index-url") + 1] == "https://b/simple"


def test_install_git_ignores_index_sources() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://idx/simple", "kind": "index"}]}
    )
    runner = _FakeRunner()
    run_extension_command(
        ["install", "https://h/r.git", "--yes"], settings=mem, runner=runner
    )
    argv = runner.calls[0]
    assert "--index-url" not in argv  # git target does not consume index sources
    assert argv[-1] == "git+https://h/r.git"


# === #32-A: install recording ============================================


def test_install_path_records_source(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    mem = _mem_settings()
    run_extension_command(
        ["install", str(tmp_path / "ext"), "--yes"], settings=mem, runner=_FakeRunner()
    )
    recorded = mem.get_extension_sources()
    assert len(recorded) == 1
    assert recorded[0].kind == "path"
    assert recorded[0].spec == str((tmp_path / "ext").resolve())


def test_install_pypi_records_bare_name() -> None:
    mem = _mem_settings()
    run_extension_command(
        ["install", "some-pkg==1.2", "--yes"], settings=mem, runner=_FakeRunner()
    )
    recorded = mem.get_extension_sources()
    assert recorded[0].kind == "pypi"
    assert recorded[0].spec == "some-pkg"  # version specifier stripped


def test_install_failure_does_not_record() -> None:
    mem = _mem_settings()
    code = run_extension_command(
        ["install", "some-pkg", "--yes"], settings=mem, runner=_FakeRunner(returncode=1)
    )
    assert code == 1
    assert mem.get_extension_sources() == []  # only successful installs record


# === #32-A: list installed ===============================================


def test_list_installed_empty(capsys: pytest.CaptureFixture[str]) -> None:
    from aelix_coding_agent.cli import extension_install as ei

    # No settings interaction; deterministic empty inventory.
    assert run_extension_command(["list"]) == 0
    # (the real env has no aelix.extensions unless one is pip-installed)
    _ = capsys.readouterr()
    _ = ei  # keep import referenced for the patch-based test below


def test_list_installed_populated(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Since issue #91 `list` annotates each endpoint with its manifest-binding
    verdict (via `classify_installed_endpoints`), so an ABSENT pack is visibly
    inert and a BOUND one is not. See tests/cli/test_extension_verify.py for the
    end-to-end classification."""

    from aelix_coding_agent.cli import extension_install as ei
    from aelix_coding_agent.extensions.ep_manifest import EpOutcome

    monkeypatch.setattr(
        ei,
        "classify_installed_endpoints",
        lambda **_kw: [
            ei.EndpointStatus(
                "myext", "my-ext-dist", "1.0.0", EpOutcome.BOUND, "bound", "myext"
            ),
            ei.EndpointStatus(
                "inertext", "inert-dist", "2.0.0", EpOutcome.ABSENT, "no manifest", None
            ),
        ],
    )
    assert run_extension_command(["list"]) == 0
    out = capsys.readouterr().out
    # Both endpoints are listed with dist + version.
    assert "myext" in out and "my-ext-dist" in out and "1.0.0" in out
    assert "inertext" in out and "inert-dist" in out and "2.0.0" in out
    # The ABSENT one carries the inert annotation; the BOUND one does not.
    assert "no manifest" in out and "inert" in out
    myext_line = next(line for line in out.splitlines() if "myext" in line)
    assert "inert" not in myext_line


# === #32-A: update =======================================================


def test_update_all_empty_is_noop() -> None:
    assert run_extension_command(["update"], settings=_mem_settings()) == 0


def test_update_git_source_upgrades() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "git+https://h/r.git", "kind": "git", "name": "r"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(["update", "--yes"], settings=mem, runner=runner)
    assert code == 0
    argv = runner.calls[0]
    assert "--upgrade" in argv
    assert argv[-1] == "git+https://h/r.git"


def test_update_skips_index_sources() -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://idx/simple", "kind": "index"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(["update", "--yes"], settings=mem, runner=runner)
    assert code == 0
    assert runner.calls == []  # an index source is a resolution hint, not upgradeable


def test_update_named_pypi_uses_index_sources() -> None:
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": "https://idx/simple", "kind": "index"},
                {"spec": "some-pkg", "kind": "pypi", "name": "some-pkg"},
            ]
        }
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["update", "some-pkg", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    argv = runner.calls[0]
    assert "--upgrade" in argv
    assert argv[argv.index("--index-url") + 1] == "https://idx/simple"


def test_update_unrecorded_name_treated_as_pypi() -> None:
    mem = _mem_settings()
    runner = _FakeRunner()
    code = run_extension_command(
        ["update", "ghost-pkg", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    assert runner.calls[0][-1] == "ghost-pkg"  # upgraded as a bare pypi name


# === #32-A: remove =======================================================


def test_remove_uninstalls_and_drops_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent.cli import extension_install as ei

    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("myext", "my-ext-dist", "1.0.0")],
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "some", "kind": "pypi", "name": "myext"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["remove", "myext", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    argv = runner.calls[0]
    assert argv[3:] == ["uninstall", "-y", "my-ext-dist"]
    assert mem.get_extension_sources() == []  # recorded source dropped too


def test_remove_unknown_name_errors() -> None:
    mem = _mem_settings()
    assert run_extension_command(["remove", "ghost", "--yes"], settings=mem) == 2


def test_remove_requires_name() -> None:
    assert run_extension_command(["remove"], settings=_mem_settings()) == 2


def test_remove_pip_failure_keeps_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent.cli import extension_install as ei

    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("myext", "my-ext-dist", "1.0.0")],
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "some", "kind": "pypi", "name": "myext"}]}
    )
    code = run_extension_command(
        ["remove", "myext", "--yes"], settings=mem, runner=_FakeRunner(returncode=1)
    )
    assert code == 1
    assert len(mem.get_extension_sources()) == 1  # failed uninstall keeps the record


# === #32-A: top-level dispatch ===========================================


def test_extension_unknown_subcommand() -> None:
    assert run_extension_command(["frobnicate"], settings=_mem_settings()) == 2


async def test_async_add_then_remove_roundtrip() -> None:
    # Multi-step within ONE event loop (reuses the same in-memory manager, the
    # way the live TUI does — avoids per-call asyncio.run loop churn).
    mem = _mem_settings()
    assert await run_extension_command_async(
        ["source", "add", "https://idx/simple"], settings=mem
    ) == 0
    assert len(mem.get_extension_sources()) == 1
    assert await run_extension_command_async(
        ["source", "remove", "https://idx/simple"], settings=mem
    ) == 0
    assert mem.get_extension_sources() == []


# === #32-A: git-spec normalization consistency (review-fix) ==============


async def test_git_source_add_then_install_dedupes() -> None:
    # `source add <raw-url>` stores the normalized git+ spec, so a later
    # install-record of the SAME repo (which normalizes via _install_spec) must
    # NOT create a duplicate entry.
    mem = _mem_settings()
    assert await run_extension_command_async(
        ["source", "add", "https://github.com/x/ext.git"], settings=mem
    ) == 0
    stored = mem.get_extension_sources()
    assert len(stored) == 1
    assert stored[0].spec == "git+https://github.com/x/ext.git"  # normalized at store
    # Install the same repo (raw url) — recording dedupes on normalized identity.
    assert await run_extension_command_async(
        ["install", "https://github.com/x/ext.git", "--yes"],
        settings=mem,
        runner=_FakeRunner(),
    ) == 0
    assert len(mem.get_extension_sources()) == 1  # still ONE, not duplicated


def test_source_remove_matches_normalized_git() -> None:
    # A git source stored in normalized form is removable by the RAW url too.
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "git+https://h/r.git", "kind": "git"}]}
    )
    assert run_extension_command(
        ["source", "remove", "https://h/r.git"], settings=mem
    ) == 0
    assert mem.get_extension_sources() == []


# === #32-A: handler-level persistence (flush invariant, disk-backed) ======


async def test_source_add_persists_to_disk_through_handler(tmp_path: Path) -> None:
    # Review HIGH: guards invariant #1 (a handler MUST await settings.flush()).
    # In-memory assertions pass even if flush is dropped (set_* updates the merged
    # view synchronously); only a FRESH manager over the same FILE proves the
    # awaited disk write happened.
    from aelix_ai.settings import SettingsManager

    settings_path = tmp_path / "disk-settings.json"

    def _fresh() -> SettingsManager:
        return SettingsManager.create(cwd=str(tmp_path), agent_dir=tmp_path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(settings_path))
    try:
        mgr = _fresh()
        code = await run_extension_command_async(
            ["source", "add", "https://idx.corp/simple"], settings=mgr
        )
        assert code == 0
        # Reconstruct from disk — fails if the handler dropped `await flush()`.
        reloaded = _fresh()
        specs = [s.spec for s in reloaded.get_extension_sources()]
        assert "https://idx.corp/simple" in specs
    finally:
        monkeypatch.undo()


async def test_install_record_persists_to_disk_through_handler(
    tmp_path: Path,
) -> None:
    # Same guard on the install-record write path.
    from aelix_ai.settings import SettingsManager

    (tmp_path / "ext").mkdir()
    settings_path = tmp_path / "disk-settings2.json"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(settings_path))
    try:
        mgr = SettingsManager.create(cwd=str(tmp_path), agent_dir=tmp_path)
        code = await run_extension_command_async(
            ["install", str(tmp_path / "ext"), "--yes"],
            settings=mgr,
            runner=_FakeRunner(),
        )
        assert code == 0
        reloaded = SettingsManager.create(cwd=str(tmp_path), agent_dir=tmp_path)
        assert any(
            s.kind == "path" for s in reloaded.get_extension_sources()
        )
    finally:
        monkeypatch.undo()


# === #32-A: install-record dist-name capture (non-empty diff branch) ======


def test_install_records_detected_dist_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review MEDIUM: exercise the branch where the before/after ledger diff
    # DETECTS a new distribution, so `name` is captured (fake runner alone always
    # yields an empty diff → detected=None).
    from aelix_coding_agent.cli import extension_install as ei

    calls = {"n": 0}

    def _ledger() -> list[Any]:
        # Empty before the install, one dist after (n increments per call).
        calls["n"] += 1
        if calls["n"] <= 1:
            return []
        return [ei.InstalledExtension("newext", "new-ext-dist", "1.0.0")]

    monkeypatch.setattr(ei, "list_installed_extensions", _ledger)
    mem = _mem_settings()
    code = run_extension_command(
        ["install", "new-ext-dist", "--yes"], settings=mem, runner=_FakeRunner()
    )
    assert code == 0
    recorded = mem.get_extension_sources()
    assert recorded[0].name == "new-ext-dist"  # detected name captured


# === #32-A: update-all aggregation with a failing source ==================


def test_update_all_reports_first_failure_but_attempts_all() -> None:
    # Review MEDIUM: two installable sources, the first fails — exit code
    # propagates the failure AND every source is still attempted (loop-continue).
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": "git+https://h/a.git", "kind": "git", "name": "a"},
                {"spec": "git+https://h/b.git", "kind": "git", "name": "b"},
            ]
        }
    )
    runner = _FakeRunner(returncode=1)
    code = run_extension_command(["update", "--yes"], settings=mem, runner=runner)
    assert code == 1  # first failure propagated
    assert len(runner.calls) == 2  # both sources attempted despite the failure


# === #64 (ADR-0187): integrity verification gate =========================


def _read_pins(tmp_path: Path) -> dict[str, Any]:
    """The pin store the CLI wrote (agent_dir is tmp_path/agent via the fixture)."""

    from aelix_coding_agent.cli import extension_pins as ep

    return ep.load_pins(ep.pins_file_path(tmp_path / "agent"))


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class _DownloadRunner:
    """Fakes ``pip download`` by writing a wheel into ``--dest``; records argv."""

    def __init__(
        self,
        *,
        wheel_name: str = "some_pkg-1.2-py3-none-any.whl",  # valid PEP 427 name (_)
        wheel_bytes: bytes = b"WHEEL-BYTES-V1",
        returncode: int = 0,
        install_returncode: int | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.wheel_name = wheel_name
        self.wheel_bytes = wheel_bytes
        self.returncode = returncode  # the `pip download` returncode
        # The `pip install --no-index` returncode (defaults to `returncode`).
        self.install_returncode = install_returncode

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if "download" in argv and "--dest" in argv:
            dest = Path(argv[argv.index("--dest") + 1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / self.wheel_name).write_bytes(self.wheel_bytes)
            return subprocess.CompletedProcess(args=argv, returncode=self.returncode)
        rc = self.install_returncode if self.install_returncode is not None else self.returncode
        return subprocess.CompletedProcess(args=argv, returncode=rc)


# --- path (file) tofi ---------------------------------------------------


def test_verify_path_file_records_pin(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"artifact-bytes")
    code = install_extension(str(whl), yes=True, runner=_FakeRunner())
    assert code == 0
    pins = _read_pins(tmp_path)
    identity = str(whl.resolve())
    assert identity in pins
    assert pins[identity].sha256 == _sha(b"artifact-bytes")
    assert pins[identity].kind == "path"


def test_verify_path_file_match_reinstalls(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"same")
    install_extension(str(whl), yes=True, runner=_FakeRunner())  # records
    r2 = _FakeRunner()
    code = install_extension(str(whl), yes=True, runner=r2)  # verifies match
    assert code == 0
    assert len(r2.calls) == 1  # pip DID run — bytes matched the pin


def test_verify_path_file_tamper_refused(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"good")
    install_extension(str(whl), yes=True, runner=_FakeRunner())
    whl.write_bytes(b"EVIL")  # same identity, different bytes
    r2 = _FakeRunner()
    code = install_extension(str(whl), yes=True, runner=r2)
    assert code == 2  # refused
    assert r2.calls == []  # pip NEVER ran on the tampered artifact


def test_verify_path_file_repin_accepts_change(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"good")
    install_extension(str(whl), yes=True, runner=_FakeRunner())
    whl.write_bytes(b"NEWBYTES")
    r2 = _FakeRunner()
    code = install_extension(str(whl), yes=True, repin=True, runner=r2)
    assert code == 0
    assert len(r2.calls) == 1
    assert _read_pins(tmp_path)[str(whl.resolve())].sha256 == _sha(b"NEWBYTES")


def test_verify_no_verify_skips(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"x")
    code = install_extension(str(whl), yes=True, no_verify=True, runner=_FakeRunner())
    assert code == 0
    assert _read_pins(tmp_path) == {}  # nothing pinned under --no-verify


# --- path (directory / editable) degrade --------------------------------


def test_verify_path_dir_degrades_no_pin(tmp_path: Path) -> None:
    d = tmp_path / "ext"
    d.mkdir()
    code = install_extension(str(d), yes=True, runner=_FakeRunner())
    assert code == 0  # installs
    assert _read_pins(tmp_path) == {}  # a directory has no stable artifact to pin


def test_verify_strict_dir_refused(tmp_path: Path) -> None:
    d = tmp_path / "ext"
    d.mkdir()
    r = _FakeRunner()
    code = install_extension(str(d), yes=True, strict=True, runner=r)
    assert code == 2  # strict refuses an unpinnable directory
    assert r.calls == []


# --- git ----------------------------------------------------------------


def test_verify_git_mutable_ref_tofi_degrades(tmp_path: Path) -> None:
    code = install_extension("git+https://h/r.git", yes=True, runner=_FakeRunner())
    assert code == 0  # tofi proceeds
    assert _read_pins(tmp_path) == {}  # no SHA to pin


def test_verify_git_sha_records_and_verifies(tmp_path: Path) -> None:
    sha = "a" * 40
    spec = f"git+https://h/r.git@{sha}"
    assert install_extension(spec, yes=True, runner=_FakeRunner()) == 0
    pins = _read_pins(tmp_path)
    ident = "git+https://h/r.git"  # repo identity — the @<sha> is stripped
    assert ident in pins
    assert pins[ident].git_sha == sha
    r2 = _FakeRunner()
    assert install_extension(spec, yes=True, runner=r2) == 0  # same SHA verifies
    assert len(r2.calls) == 1


def test_verify_git_sha_change_refused(tmp_path: Path) -> None:
    install_extension(f"git+https://h/r.git@{'a' * 40}", yes=True, runner=_FakeRunner())
    r2 = _FakeRunner()
    code = install_extension(f"git+https://h/r.git@{'b' * 40}", yes=True, runner=r2)
    assert code == 2  # same repo, different commit → refused
    assert r2.calls == []
    r3 = _FakeRunner()
    assert (
        install_extension(
            f"git+https://h/r.git@{'b' * 40}", yes=True, repin=True, runner=r3
        )
        == 0
    )  # --repin accepts the move


def test_verify_strict_git_mutable_refused(tmp_path: Path) -> None:
    r = _FakeRunner()
    code = install_extension("git+https://h/r.git", yes=True, strict=True, runner=r)
    assert code == 2  # strict refuses a mutable ref
    assert r.calls == []


# --- pypi (opt-in two-phase) --------------------------------------------


def test_verify_pypi_default_skips_download(tmp_path: Path) -> None:
    r = _FakeRunner()
    code = install_extension("some-pkg", yes=True, index_url="https://idx", runner=r)
    assert code == 0
    assert len(r.calls) == 1  # only the install; verification is opt-in
    assert "download" not in r.calls[0]
    assert _read_pins(tmp_path) == {}


def test_verify_pypi_optin_two_phase_records_and_rewrites(tmp_path: Path) -> None:
    r = _DownloadRunner()
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r
    )
    assert code == 0
    assert len(r.calls) == 2
    download, install = r.calls
    assert "download" in download and "--index-url" in download
    dest = download[download.index("--dest") + 1]
    # The install runs against the VERIFIED local bytes — same dir, spec present,
    # and the network index is NOT consulted (the load-bearing invariant).
    assert "--no-index" in install
    assert install[install.index("--find-links") + 1] == dest
    assert "some-pkg" in install
    assert "--index-url" not in install
    pins = _read_pins(tmp_path)
    assert "some-pkg" in pins
    assert pins["some-pkg"].sha256 == _sha(b"WHEEL-BYTES-V1")
    assert pins["some-pkg"].version == "1.2"
    assert not Path(dest).exists()  # temp download dir cleaned after success


def test_verify_pypi_optin_tamper_refused(tmp_path: Path) -> None:
    # First install pins v1.2 bytes; a later download of DIFFERENT bytes for the
    # SAME version is refused (the install pip is never run).
    install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx",
        runner=_DownloadRunner(wheel_bytes=b"WHEEL-BYTES-V1"),
    )
    r2 = _DownloadRunner(wheel_bytes=b"TAMPERED")
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r2
    )
    assert code == 2
    assert len(r2.calls) == 1  # download ran; install did NOT
    assert "download" in r2.calls[0]


def test_verify_pypi_strict_first_acquisition_refused(tmp_path: Path) -> None:
    r = _DownloadRunner()
    code = install_extension(
        "some-pkg", yes=True, strict=True, index_url="https://idx", runner=r
    )
    assert code == 2  # strict refuses a source with no pre-provisioned pin
    assert len(r.calls) == 1  # download ran; install did NOT
    assert _read_pins(tmp_path) == {}


def test_verify_pypi_strict_with_provisioned_pin_ok(tmp_path: Path) -> None:
    # An admin provisions the pin out-of-band; strict then installs it.
    from aelix_coding_agent.cli import extension_pins as ep

    pins_path = ep.pins_file_path(tmp_path / "agent")
    ep.save_pins(
        {
            "some-pkg": ep.Pin(
                identity="some-pkg", kind="pypi", mode="strict",
                name="some-pkg", version="1.2", sha256=_sha(b"WHEEL-BYTES-V1"),
            )
        },
        pins_path,
    )
    r = _DownloadRunner()
    code = install_extension(
        "some-pkg", yes=True, strict=True, index_url="https://idx", runner=r
    )
    assert code == 0  # matches the provisioned pin
    assert len(r.calls) == 2  # download + local install


# --- update re-verifies (flags threaded) --------------------------------


def test_update_threads_strict_flag(tmp_path: Path) -> None:
    # A recorded mutable-git source updated under --strict is refused (proves the
    # verify flags reach install_extension through the update path).
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "git+https://h/r.git", "kind": "git", "name": "r"}]}
    )
    r = _FakeRunner()
    code = run_extension_command(
        ["update", "--yes", "--strict"], settings=mem, runner=r
    )
    assert code == 2  # strict + mutable git ref → refused
    assert r.calls == []


# --- review MEDIUM: the generic verify-error skip branch (fail-open/closed) ---


def test_verify_internal_error_tofi_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli import extension_install as ei

    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"x")

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("verify exploded")

    monkeypatch.setattr(ei, "verify_and_pin", _boom)
    r = _FakeRunner()
    code = install_extension(str(whl), yes=True, runner=r)
    assert code == 0  # tofi FAIL-OPEN: an internal verify bug still installs
    assert len(r.calls) == 1
    assert _read_pins(tmp_path) == {}  # but nothing is pinned


def test_verify_internal_error_strict_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli import extension_install as ei

    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"x")

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("verify exploded")

    monkeypatch.setattr(ei, "verify_and_pin", _boom)
    r = _FakeRunner()
    code = install_extension(str(whl), yes=True, strict=True, runner=r)
    assert code == 2  # strict FAIL-CLOSED: pip never runs on a verify error
    assert r.calls == []


# --- review LOW: strict path(file) / git(sha) end-to-end through the gate ---


def _provision_pin(tmp_path: Path, pin: object) -> None:
    from aelix_coding_agent.cli import extension_pins as ep

    ep.save_pins({pin.identity: pin}, ep.pins_file_path(tmp_path / "agent"))  # type: ignore[attr-defined]


def test_verify_strict_path_file_first_refused(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"x")
    r = _FakeRunner()
    code = install_extension(str(whl), yes=True, strict=True, runner=r)
    assert code == 2  # strict refuses an unpinned first acquisition
    assert r.calls == []


def test_verify_strict_path_file_provisioned_ok(tmp_path: Path) -> None:
    from aelix_coding_agent.cli import extension_pins as ep

    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"good")
    _provision_pin(
        tmp_path,
        ep.Pin(
            identity=str(whl.resolve()), kind="path", mode="strict",
            sha256=_sha(b"good"),
        ),
    )
    r = _FakeRunner()
    code = install_extension(str(whl), yes=True, strict=True, runner=r)
    assert code == 0  # matches the provisioned pin
    assert len(r.calls) == 1


def test_verify_strict_git_sha_first_refused(tmp_path: Path) -> None:
    r = _FakeRunner()
    code = install_extension(
        f"git+https://h/r.git@{'a' * 40}", yes=True, strict=True, runner=r
    )
    assert code == 2
    assert r.calls == []


def test_verify_strict_git_sha_provisioned_ok(tmp_path: Path) -> None:
    from aelix_coding_agent.cli import extension_pins as ep

    sha = "a" * 40
    _provision_pin(
        tmp_path,
        ep.Pin(
            identity="git+https://h/r.git", kind="git", mode="strict", git_sha=sha
        ),
    )
    r = _FakeRunner()
    code = install_extension(f"git+https://h/r.git@{sha}", yes=True, strict=True, runner=r)
    assert code == 0
    assert len(r.calls) == 1


def test_verify_git_uppercase_provisioned_pin_matches(tmp_path: Path) -> None:
    # An admin hand-edits an UPPERCASE gitSha; it must still equal the (always
    # lowercased) observed SHA — load normalizes case.
    import json

    from aelix_coding_agent.cli import extension_pins as ep

    pins_path = ep.pins_file_path(tmp_path / "agent")
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(
        json.dumps(
            {
                "version": 1,
                "pins": {
                    "git+https://h/r.git": {
                        "kind": "git", "mode": "strict", "gitSha": "A" * 40
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    r = _FakeRunner()
    code = install_extension(
        f"git+https://h/r.git@{'a' * 40}", yes=True, strict=True, runner=r
    )
    assert code == 0  # uppercase provisioned == lowercased observed
    assert len(r.calls) == 1


def test_verify_git_pin_strip_downgrade_tofi_proceeds(tmp_path: Path) -> None:
    # A repo pinned to a commit, then reinstalled at a MUTABLE ref under tofi:
    # proceeds (documents the downgrade); strict would have refused.
    sha = "a" * 40
    install_extension(f"git+https://h/r.git@{sha}", yes=True, runner=_FakeRunner())
    r2 = _FakeRunner()
    code = install_extension("git+https://h/r.git", yes=True, runner=r2)
    assert code == 0
    assert len(r2.calls) == 1


# --- review LOW: drift detection through the `update` path ---


def test_update_path_drift_refused_then_repin(tmp_path: Path) -> None:
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"good")
    mem = _mem_settings()
    run_extension_command(
        ["install", str(whl), "--yes"], settings=mem, runner=_FakeRunner()
    )
    whl.write_bytes(b"EVIL")  # the recorded path source now holds different bytes
    r2 = _FakeRunner()
    code = run_extension_command(["update", "--yes"], settings=mem, runner=r2)
    assert code == 2  # update re-verifies and refuses the drift
    assert r2.calls == []
    r3 = _FakeRunner()
    code = run_extension_command(["update", "--yes", "--repin"], settings=mem, runner=r3)
    assert code == 0  # --repin accepts the change
    assert len(r3.calls) == 1
    assert _read_pins(tmp_path)[str(whl.resolve())].sha256 == _sha(b"EVIL")


# --- review LOW/NIT: remaining pypi-gate branch coverage ---


def test_verify_pypi_pin_not_recorded_on_install_failure(tmp_path: Path) -> None:
    # verify passes (download+hash) but the local install fails → no pin, temp gone.
    r = _DownloadRunner(install_returncode=1)
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r
    )
    assert code == 1  # pip install failure propagates
    assert _read_pins(tmp_path) == {}  # a pin is recorded only on success
    dest = r.calls[0][r.calls[0].index("--dest") + 1]
    assert not Path(dest).exists()  # temp dir cleaned even on install failure


def test_verify_pypi_download_failure_refused(tmp_path: Path) -> None:
    r = _DownloadRunner(returncode=1)  # `pip download` fails
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r
    )
    assert code == 2  # verify refusal — pip install never runs
    assert len(r.calls) == 1  # only the download attempt
    dest = r.calls[0][r.calls[0].index("--dest") + 1]
    assert not Path(dest).exists()


def test_verify_pypi_artifact_not_found_tofi_degrades(tmp_path: Path) -> None:
    r = _DownloadRunner(wheel_name="unrelated_pkg-9.9-py3-none-any.whl")
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r
    )
    assert code == 0  # tofi degrades → installs normally (original index argv)
    assert _read_pins(tmp_path) == {}
    assert len(r.calls) == 2
    assert "--no-index" not in r.calls[1]  # degrade uses the original index install


def test_verify_pypi_artifact_not_found_strict_refused(tmp_path: Path) -> None:
    r = _DownloadRunner(wheel_name="unrelated_pkg-9.9-py3-none-any.whl")
    code = install_extension(
        "some-pkg", yes=True, strict=True, index_url="https://idx", runner=r
    )
    assert code == 2  # strict refuses when the artifact can't be uniquely located
    assert len(r.calls) == 1


def test_verify_pypi_same_version_repin(tmp_path: Path) -> None:
    install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx",
        runner=_DownloadRunner(wheel_bytes=b"V1"),
    )
    r2 = _DownloadRunner(wheel_bytes=b"TAMPER")  # same version, changed bytes
    assert install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r2
    ) == 2  # refused without --repin
    r3 = _DownloadRunner(wheel_bytes=b"TAMPER")
    assert install_extension(
        "some-pkg", yes=True, verify_pypi=True, repin=True, index_url="https://idx",
        runner=r3,
    ) == 0  # --repin accepts
    assert _read_pins(tmp_path)["some-pkg"].sha256 == _sha(b"TAMPER")


def test_verify_pypi_version_bump_repins_under_tofi(tmp_path: Path) -> None:
    install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx",
        runner=_DownloadRunner(wheel_name="some_pkg-1.2-py3-none-any.whl", wheel_bytes=b"V1"),
    )
    r2 = _DownloadRunner(wheel_name="some_pkg-2.0-py3-none-any.whl", wheel_bytes=b"V2")
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r2
    )
    assert code == 0  # a new version legitimately re-pins under tofi (no --repin)
    pins = _read_pins(tmp_path)
    assert pins["some-pkg"].version == "2.0"
    assert pins["some-pkg"].sha256 == _sha(b"V2")


def test_verify_pypi_alias_shares_one_pin(tmp_path: Path) -> None:
    # 'some_pkg' and 'some-pkg' are ONE project → one canonical pin identity, so
    # an alias spelling cannot sidestep the pin via a fresh TOFI.
    install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx",
        runner=_DownloadRunner(wheel_bytes=b"V1"),
    )
    r2 = _DownloadRunner(wheel_bytes=b"TAMPER")  # alias spelling, changed bytes
    code = install_extension(
        "some_pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r2
    )
    assert code == 2  # same canonical identity → tamper caught, not re-TOFI'd
    assert set(_read_pins(tmp_path)) == {"some-pkg"}  # one entry, not two


def test_verify_pypi_sdist_through_gate(tmp_path: Path) -> None:
    r = _DownloadRunner(wheel_name="some-pkg-1.2.tar.gz", wheel_bytes=b"SDIST")
    code = install_extension(
        "some-pkg", yes=True, verify_pypi=True, index_url="https://idx", runner=r
    )
    assert code == 0
    pins = _read_pins(tmp_path)
    assert pins["some-pkg"].sha256 == _sha(b"SDIST")
    assert pins["some-pkg"].version == "1.2"
    assert "--no-index" in r.calls[1]


# =========================================================================
# === #113: the installer BACKEND (pip preferred, uv fallback) ============
# =========================================================================
#
# The official install path is ``uv tool install``, whose venv ships WITHOUT
# pip — so before this, EVERY install/update/remove aborted (exit 2) for anyone
# who followed the README, and it aborted before printing what was attempted.
#
# All of these are network-free and subprocess-free: backend RESOLUTION is
# driven by monkeypatched ``importlib.util.find_spec`` / ``shutil.which``, argv
# SHAPE is asserted on the pure builders, and the end-to-end rows monkeypatch
# ``resolve_install_backend`` (an injected runner deliberately pins the backend
# to pip, so it cannot be used to reach the uv dialect).


def _ei() -> Any:
    from aelix_coding_agent.cli import extension_install as ei

    return ei


def _force_env(
    monkeypatch: pytest.MonkeyPatch, *, pip: bool, uv: str | None
) -> None:
    """Pin what ``detect_install_backend`` sees: pip importable? uv on PATH?"""

    ei = _ei()
    monkeypatch.setattr(
        ei.importlib.util, "find_spec", lambda name: object() if pip else None
    )
    monkeypatch.setattr(ei.shutil, "which", lambda name: uv if name == "uv" else None)


def _force_backend(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    """Make every call site resolve to ``backend``, injected runner or not."""

    ei = _ei()
    monkeypatch.setattr(ei, "resolve_install_backend", lambda _runner: backend)


def _uv_backend(path: str = "/opt/bin/uv") -> Any:
    return _ei().InstallBackend(name="uv", uv_path=path)


# --- resolution ----------------------------------------------------------


def test_backend_prefers_pip_even_when_uv_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pip WINS whenever importable: it is the only backend that can `download`,
    # i.e. the only one that can verify. uv is a fallback, never a preference.
    _force_env(monkeypatch, pip=True, uv="/opt/bin/uv")
    backend = _ei().detect_install_backend()
    assert backend is not None
    assert backend.name == "pip"
    assert backend.supports_download is True


def test_backend_falls_back_to_uv_when_pip_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_env(monkeypatch, pip=False, uv="/opt/bin/uv")
    backend = _ei().detect_install_backend()
    assert backend is not None
    assert backend.name == "uv"
    assert backend.uv_path == "/opt/bin/uv"
    assert backend.supports_download is False  # `uv pip` has no `download`


def test_backend_is_none_when_neither_pip_nor_uv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_env(monkeypatch, pip=False, uv=None)
    assert _ei().detect_install_backend() is None


def test_injected_runner_short_circuits_to_the_pip_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # THE load-bearing seam: an injected runner means the caller owns backend
    # availability, so resolution never probes and always yields the pip-shaped
    # dialect. Without this the whole injected-runner cluster would flip to `uv
    # pip install …` argv on any machine whose interpreter lacks pip.
    ei = _ei()
    _force_env(monkeypatch, pip=False, uv="/opt/bin/uv")
    backend = ei.resolve_install_backend(_FakeRunner())
    assert backend is not None
    assert backend.name == "pip"
    assert ei.resolve_install_backend(None) is not None  # the env says uv
    assert ei.resolve_install_backend(None).name == "uv"


def test_pip_available_short_circuit_and_backend_agreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ei = _ei()
    _force_env(monkeypatch, pip=False, uv=None)
    assert ei._pip_available(None) is False  # nothing usable
    assert ei._pip_available(_FakeRunner()) is True  # injected → always available
    _force_env(monkeypatch, pip=False, uv="/opt/bin/uv")
    assert ei._pip_available(None) is True  # uv alone is enough to INSTALL


# --- argv shape per backend ----------------------------------------------


def test_uv_backend_install_argv_shape() -> None:
    uv = _uv_backend()
    prefix = [uv.uv_path, "pip", "install", "--python", sys.executable]
    assert uv.install_prefix() == prefix
    assert build_pip_args("some-pkg", "pypi", backend=uv) == [*prefix, "some-pkg"]
    assert build_pip_args(
        "some-pkg", "pypi", backend=uv, upgrade=True, index_url="https://idx"
    ) == [*prefix, "--upgrade", "some-pkg", "--index-url", "https://idx"]
    assert build_pip_args("https://h/o/r.git", "git", backend=uv) == [
        *prefix,
        "git+https://h/o/r.git",
    ]


def test_pip_backend_install_argv_is_the_historical_shape() -> None:
    # backend=None keeps the pre-#113 argv byte-for-byte.
    prefix = [sys.executable, "-m", "pip", "install"]
    assert build_pip_args("some-pkg", "pypi") == [*prefix, "some-pkg"]
    assert build_pip_args("some-pkg", "pypi", backend=_ei().PIP_BACKEND) == [
        *prefix,
        "some-pkg",
    ]


def test_install_prefix_is_not_shared_mutable_state() -> None:
    # build_pip_args appends --upgrade to the prefix; a cached list would leak
    # the flag into the next call.
    uv = _uv_backend()
    first = build_pip_args("a", "pypi", backend=uv, upgrade=True)
    second = build_pip_args("b", "pypi", backend=uv)
    assert "--upgrade" in first
    assert "--upgrade" not in second


def test_uninstall_argv_per_backend() -> None:
    ei = _ei()
    uv = _uv_backend()
    # `uv pip uninstall` never prompts and rejects an unknown -y.
    assert uv.uninstall_args("my-dist") == [
        uv.uv_path, "pip", "uninstall", "--python", sys.executable, "my-dist",
    ]
    assert "-y" not in uv.uninstall_args("my-dist")
    assert ei.PIP_BACKEND.uninstall_args("my-dist") == [
        sys.executable, "-m", "pip", "uninstall", "-y", "my-dist",
    ]


@pytest.mark.parametrize(
    "bad", [None, "uv", "./uv", "node_modules/.bin/uv", "../uv", ""]
)
def test_uv_backend_refuses_a_bare_or_relative_uv_path(bad: str | None) -> None:
    # A uv backend may ONLY carry an absolute executable. A bare name (or a relative
    # one) is resolved against PATH at exec time — and a `.`/empty/relative PATH entry
    # (node_modules/.bin, direnv, a stray leading `:`) makes that a file in the CURRENT
    # WORKING DIRECTORY. Standing in a cloned repo would then run its ./uv as the
    # installer, before the extension is ever fetched (CWE-426). The pip backend shells
    # out to the absolute sys.executable; the uv backend must meet the same bar, so the
    # invariant is enforced in the constructor and no call site can bypass it.
    with pytest.raises(ValueError, match="ABSOLUTE uv_path"):
        _ei().InstallBackend(name="uv", uv_path=bad)


def test_pip_backend_needs_no_uv_path() -> None:
    # The constraint is uv-only: the pip backend is still constructible bare.
    assert _ei().InstallBackend(name="pip").uv_path is None


@pytest.mark.parametrize("relative", ["uv", "./uv", "node_modules/.bin/uv"])
def test_relative_uv_on_path_is_not_a_usable_backend(
    monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    # shutil.which returns a RELATIVE hit whenever a PATH entry is relative or empty.
    # Resolution must treat that as "no uv found" and fall through to the actionable
    # no-backend message rather than shelling out to a cwd binary.
    _force_env(monkeypatch, pip=False, uv=relative)
    assert _ei().detect_install_backend() is None


def test_absolute_uv_on_path_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard rejects relative paths ONLY — the normal absolute hit is unaffected.
    _force_env(monkeypatch, pip=False, uv="/usr/local/bin/uv")
    backend = _ei().detect_install_backend()
    assert backend is not None
    assert backend.uv_path == "/usr/local/bin/uv"


def test_cwd_relative_uv_aborts_the_install_instead_of_executing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # End to end with the DEFAULT runner (no injected seam, so a real subprocess would
    # be spawned if resolution accepted the relative hit): the install must refuse.
    (tmp_path / "ext").mkdir()
    _force_env(monkeypatch, pip=False, uv="./uv")
    ran: list[list[str]] = []
    monkeypatch.setattr(_ei_mod, "_default_runner", lambda argv: ran.append(argv))
    code = install_extension(str(tmp_path / "ext"), yes=True)
    assert code == 2
    assert ran == []  # nothing was executed
    err = capsys.readouterr().err
    assert "no usable package installer" in err
    assert "--with pip" in err


# --- end-to-end on the uv backend ----------------------------------------


def test_install_on_uv_backend_runs_uv_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    uv = _uv_backend()
    _force_backend(monkeypatch, uv)
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner()
    code = install_extension(str(tmp_path / "ext"), yes=True, runner=runner)
    assert code == 0
    assert runner.calls[0][:5] == [
        uv.uv_path, "pip", "install", "--python", sys.executable,
    ]
    # And the user is warned that a `uv tool install --force` wipes this.
    assert "uv tool install --force aelix" in capsys.readouterr().out


def test_pip_backend_install_prints_no_uv_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ext").mkdir()
    install_extension(str(tmp_path / "ext"), yes=True, runner=_FakeRunner())
    assert "uv backend" not in capsys.readouterr().out


def test_remove_on_uv_backend_uses_uv_uninstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ei = _ei()
    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("myext", "my-ext-dist", "1.0.0")],
    )
    uv = _uv_backend()
    _force_backend(monkeypatch, uv)
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "some", "kind": "pypi", "name": "myext"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["remove", "myext", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    assert runner.calls[0] == [
        uv.uv_path, "pip", "uninstall", "--python", sys.executable, "my-ext-dist",
    ]
    assert mem.get_extension_sources() == []


def test_update_on_uv_backend_carries_upgrade_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv = _uv_backend()
    _force_backend(monkeypatch, uv)
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "some-pkg", "kind": "pypi", "name": "some-pkg"}]}
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["update", "some-pkg", "--yes"], settings=mem, runner=runner
    )
    assert code == 0
    assert runner.calls[0][:6] == [
        uv.uv_path, "pip", "install", "--python", sys.executable, "--upgrade",
    ]


# --- fail closed: pypi verification the uv backend cannot execute --------


@pytest.mark.parametrize(
    "flag", ["verify_pypi", "strict", "require_signature"]
)
def test_uv_backend_refuses_pypi_verification(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `uv pip` has no `download`, so the two-phase verify cannot run. Refuse —
    # never silently install a package the user asked us to verify. NOTE the
    # predicate is all THREE flags, not --require-signature alone: --verify-pypi
    # and --strict route into build_download_args just the same.
    _force_backend(monkeypatch, _uv_backend())
    runner = _FakeRunner()
    code = install_extension(
        "some-pkg", yes=True, index_url="https://idx", runner=runner, **{flag: True}
    )
    assert code == 2
    assert runner.calls == []  # nothing ran — not even the plain install
    err = capsys.readouterr().err
    assert "some-pkg" in err  # names WHAT was refused
    assert "download" in err
    assert "--with pip" in err  # and how to fix it


def test_uv_backend_allows_plain_pypi_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verification is opt-in, so an ordinary pypi install still works on uv.
    uv = _uv_backend()
    _force_backend(monkeypatch, uv)
    runner = _FakeRunner()
    code = install_extension(
        "some-pkg", yes=True, index_url="https://idx", runner=runner
    )
    assert code == 0
    assert runner.calls[0][:5] == [
        uv.uv_path, "pip", "install", "--python", sys.executable,
    ]


def test_uv_backend_allows_no_verify_pypi_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --no-verify never enters the gate, so nothing is silently dropped.
    _force_backend(monkeypatch, _uv_backend())
    runner = _FakeRunner()
    code = install_extension(
        "some-pkg", yes=True, no_verify=True, verify_pypi=True,
        index_url="https://idx", runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1


def test_uv_backend_verifies_a_signed_path_artifact_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A PATH target is staged, hashed and pinned entirely in-process — the runner
    # is never called for a download — so --strict verification is fully honored
    # on the uv backend. Refusing on the FLAG instead of the pypi predicate would
    # have killed this, the workflow that actually works today.
    uv = _uv_backend()
    _force_backend(monkeypatch, uv)
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"artifact-bytes")
    runner = _FakeRunner()
    code = install_extension(
        str(whl), yes=True, strict=True, repin=True, runner=runner
    )
    assert code == 0
    assert len(runner.calls) == 1  # install only; no download phase
    assert runner.calls[0][:5] == [
        uv.uv_path, "pip", "install", "--python", sys.executable,
    ]
    # The STAGED copy is what gets installed (check-vs-use TOCTOU closure), and it
    # is still the last argv element after the uv prefix swap.
    assert runner.calls[0][-1].endswith("ext-1.0.whl")
    assert _read_pins(tmp_path)[str(whl.resolve())].sha256 == _sha(b"artifact-bytes")


def test_uv_backend_require_signature_on_path_reaches_the_signature_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # --require-signature on a PATH target must NOT hit the backend refusal — it is
    # verified entirely locally. There is no .aelixsig here so it still exits 2, but
    # from the SIGNATURE gate, which is the proof the backend let it through.
    _force_backend(monkeypatch, _uv_backend())
    whl = tmp_path / "ext-1.0.whl"
    whl.write_bytes(b"artifact-bytes")
    runner = _FakeRunner()
    code = install_extension(str(whl), yes=True, require_signature=True, runner=runner)
    assert code == 2
    err = capsys.readouterr().err
    assert "Verification refused" in err
    assert "no usable package installer" not in err
    assert "no `download` subcommand" not in err


def test_uv_backend_git_strict_install_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pinned git SHA is verified by inspecting the argv — no pip, no download.
    _force_backend(monkeypatch, _uv_backend())
    runner = _FakeRunner()
    sha = "a" * 40
    code = install_extension(
        f"https://h/o/r.git@{sha}", yes=True, strict=True, repin=True, runner=runner
    )
    assert code == 0
    assert len(runner.calls) == 1
    assert runner.calls[0][-1] == f"git+https://h/o/r.git@{sha}"


@pytest.mark.parametrize(
    ("kind", "no_verify", "strict", "verify_pypi", "require_signature", "expected"),
    [
        ("pypi", False, False, False, False, False),  # verification is opt-in
        ("pypi", False, True, False, False, True),
        ("pypi", False, False, True, False, True),
        ("pypi", False, False, False, True, True),
        ("pypi", True, True, True, True, False),  # --no-verify: gate never runs
        ("path", False, True, True, True, False),  # local: no download needed
        ("git", False, True, True, True, False),  # argv inspection only
    ],
)
def test_uv_verify_refusal_predicate_table(
    kind: str,
    no_verify: bool,
    strict: bool,
    verify_pypi: bool,
    require_signature: bool,
    expected: bool,
) -> None:
    ei = _ei()
    assert (
        ei.uv_pypi_verify_unsupported(
            _uv_backend(), kind, no_verify=no_verify, strict=strict,
            verify_pypi=verify_pypi, require_signature=require_signature,
        )
        is expected
    )


def test_pip_backend_never_triggers_the_refusal() -> None:
    ei = _ei()
    assert (
        ei.uv_pypi_verify_unsupported(
            ei.PIP_BACKEND, "pypi", no_verify=False, strict=True,
            verify_pypi=True, require_signature=True,
        )
        is False
    )


# --- no backend at all: the abort names what was attempted ----------------


def test_no_backend_install_abort_names_target_and_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The old guard printed "pip is not available…" BEFORE anything named the
    # target, and advised `ensurepip`, which is wrong for a uv tool venv.
    _force_env(monkeypatch, pip=False, uv=None)
    (tmp_path / "ext").mkdir()
    code = install_extension(str(tmp_path / "ext"), yes=True)  # default runner
    assert code == 2
    err = capsys.readouterr().err
    assert str(tmp_path / "ext") in err  # WHAT was being installed
    assert "source: path" in err  # and how it classified
    assert "uv tool install --force --with pip aelix" in err
    assert "no usable package installer" in err


def test_no_backend_abort_quotes_a_windows_path_without_escaping_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#208: the refused target is printed literally, not through ``repr()``.

    ``f"{target!r}"`` escapes every backslash, so a Windows target came back as
    ``'C:\\\\Users\\\\x\\\\ext'`` — not a string the user could compare against
    what they typed or paste back into a shell. Platform-independent: the
    doubling is a property of ``repr()``, not of the OS, so a backslash-bearing
    target reproduces it anywhere.
    """
    target = r"C:\Users\x\ext"
    _force_env(monkeypatch, pip=False, uv=None)
    code = install_extension(target, yes=True)
    assert code == 2
    err = capsys.readouterr().err
    assert target in err  # verbatim, backslashes intact
    assert "\\\\" not in err  # and not doubled anywhere
    assert f"cannot install '{target}'" in err  # still quoted, same quotes


def test_no_backend_remove_abort_names_extension_and_distribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ei = _ei()
    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("myext", "my-ext-dist", "1.0.0")],
    )
    _force_env(monkeypatch, pip=False, uv=None)
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "some", "kind": "pypi", "name": "myext"}]}
    )
    code = run_extension_command(["remove", "myext", "--yes"], settings=mem)
    assert code == 2
    err = capsys.readouterr().err
    assert "myext" in err
    assert "my-ext-dist" in err
    assert "--with pip" in err
    assert len(mem.get_extension_sources()) == 1  # nothing dropped on a refusal


def test_no_backend_upgrade_abort_says_upgrade(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _force_env(monkeypatch, pip=False, uv=None)
    code = install_extension("some-pkg", yes=True, upgrade=True)
    assert code == 2
    assert "cannot upgrade 'some-pkg'" in capsys.readouterr().err


def test_backend_symbols_are_exported() -> None:
    ei = _ei()
    assert "InstallBackend" in ei.__all__
    assert "resolve_install_backend" in ei.__all__
    assert "PipRunner" in ei.__all__  # unchanged public seam
    assert "AmbientIndexConfig" in ei.__all__
    assert "read_pip_index_config" in ei.__all__
    assert "uv_ambient_index_config" in ei.__all__
    assert "uv_ambient_index_env" in ei.__all__
    assert "display_argv" in ei.__all__


# --- #113: pip's ambient index configuration, translated for uv ----------
#
# pip reads PIP_INDEX_URL / PIP_EXTRA_INDEX_URL / pip.conf; uv reads NONE of them
# (verified live: `uv pip install` with PIP_INDEX_URL *and* PIP_CONFIG_FILE both
# pointed at a dead index still resolved from public PyPI, while the same command
# under UV_INDEX_URL refused to connect). Since the uv backend is the DEFAULT on
# the official `uv tool install` path, an org that pins pip to an internal mirror
# would silently resolve private extension names from PyPI — dependency confusion
# whose payload is build/setup code running at install time, and invisible in the
# consent block because the argv carried no index at all. So the settings are read
# and emitted as explicit flags, which also makes them visible before the y/N.


def _write_pip_conf(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _use_pip_conf(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    monkeypatch.setattr(
        _ei_mod, "_pip_config_candidates", lambda env=None: [str(p) for p in paths]
    )


def _uv_env(backend: Any = None, kind: str = "pypi", **kw: Any) -> dict[str, str]:
    return _ei().uv_ambient_index_env(backend or _uv_backend(), kind, **kw)


def test_uv_env_translates_pip_index_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The translation travels in the ENVIRONMENT, never on argv: an ambient index URL
    # routinely carries a basic-auth password, and /proc/<pid>/cmdline is mode 0444.
    monkeypatch.setenv("PIP_INDEX_URL", "https://internal.corp/simple")
    uv = _uv_backend()
    assert build_pip_args("internal-corp-ext", "pypi", backend=uv) == [
        *uv.install_prefix(),
        "internal-corp-ext",
    ]
    assert _uv_env(uv) == {"UV_INDEX_URL": "https://internal.corp/simple"}


def test_uv_env_translates_pip_extra_index_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pip splits its list settings on whitespace, so every entry survives — as the
    # space-separated list uv reads from UV_EXTRA_INDEX_URL.
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://a/simple https://b/simple")
    uv = _uv_backend()
    assert build_pip_args("ext", "pypi", backend=uv) == [*uv.install_prefix(), "ext"]
    assert _uv_env(uv) == {"UV_EXTRA_INDEX_URL": "https://a/simple https://b/simple"}


def test_uv_env_translates_pip_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(
        conf,
        "[global]\nindex-url = https://internal.corp/simple\n"
        "extra-index-url = https://mirror/simple\n",
    )
    _use_pip_conf(monkeypatch, conf)
    uv = _uv_backend()
    assert build_pip_args("ext", "pypi", backend=uv) == [*uv.install_prefix(), "ext"]
    assert _uv_env(uv) == {
        "UV_INDEX_URL": "https://internal.corp/simple",
        "UV_EXTRA_INDEX_URL": "https://mirror/simple",
    }


def test_pip_conf_install_section_beats_global_and_underscores_normalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(
        conf,
        "[global]\nindex_url = https://global/simple\n"
        "[install]\nindex-url = https://install/simple\n",
    )
    _use_pip_conf(monkeypatch, conf)
    cfg = _ei().read_pip_index_config()
    assert cfg.index_url == "https://install/simple"


def test_later_pip_conf_wins_and_env_beats_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The candidate list is LOW → HIGH precedence, and the environment beats all of
    # them — pip's own ordering.
    low, high = tmp_path / "low.conf", tmp_path / "high.conf"
    _write_pip_conf(low, "[global]\nindex-url = https://low/simple\n")
    _write_pip_conf(high, "[global]\nindex-url = https://high/simple\n")
    _use_pip_conf(monkeypatch, low, high)
    assert _ei().read_pip_index_config().index_url == "https://high/simple"
    assert _ei().read_pip_index_config().origin == str(high)
    monkeypatch.setenv("PIP_INDEX_URL", "https://env/simple")
    cfg = _ei().read_pip_index_config()
    assert cfg.index_url == "https://env/simple"
    assert cfg.origin == "PIP_INDEX_URL"


def test_pip_config_file_devnull_disables_every_config_file() -> None:
    # pip's documented "ignore all config files" escape hatch, honored identically.
    assert _REAL_PIP_CONFIG_CANDIDATES({"PIP_CONFIG_FILE": os.devnull}) == []
    assert _REAL_PIP_CONFIG_CANDIDATES({}) != []


def test_pip_config_candidate_order_is_pips_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The REAL search path (captured before the hermetic fixture stubs it): system
    # dirs, then user, then this environment's sys.prefix, and PIP_CONFIG_FILE last
    # so it outranks them all.
    env = {
        "XDG_CONFIG_DIRS": "/etc/xdg",
        "XDG_CONFIG_HOME": "/home/u/.config",
        "PIP_CONFIG_FILE": "/tmp/explicit.conf",
    }
    paths = _REAL_PIP_CONFIG_CANDIDATES(env)
    # pip names the file after the platform, so the SITE entry must be spelled the
    # same way the function spells it — not pinned to the POSIX basename.
    basename = "pip.ini" if sys.platform == "win32" else "pip.conf"
    assert paths[-1] == "/tmp/explicit.conf"
    assert paths[-2] == os.path.join(sys.prefix, basename)
    if sys.platform == "win32":
        # pip's USER kind on Windows is legacy ``~/pip`` then ``%APPDATA%/pip``; the
        # XDG variables set above are POSIX-only and must not contribute at all.
        assert os.path.join(os.path.expanduser("~"), "pip", basename) in paths
        assert not [p for p in paths if p.startswith(("/etc", "/home/u"))]
    else:
        assert "/etc/pip.conf" in paths
        assert "/home/u/.config/pip/pip.conf" in paths
        assert paths.index("/etc/pip.conf") < paths.index(
            "/home/u/.config/pip/pip.conf"
        )


def test_unreadable_or_malformed_pip_conf_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A broken pip.conf must never break `extension install`.
    bad = tmp_path / "pip.conf"
    _write_pip_conf(bad, "this is not ini [[[\nindex-url\n")
    _use_pip_conf(monkeypatch, bad, tmp_path / "does-not-exist.conf")
    assert not _ei().read_pip_index_config()


def test_aelix_index_source_outranks_the_ambient_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A registered aelix index source is a COMMAND-LINE value the user typed: it keeps
    # its flag (unchanged from pre-#113) and, since uv's precedence is CLI > env, it
    # beats the ambient default index — which is therefore not translated at all.
    monkeypatch.setenv("PIP_INDEX_URL", "https://ambient/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://ambient-extra/simple")
    uv = _uv_backend()
    assert build_pip_args(
        "ext", "pypi", backend=uv, index_url="https://aelix/simple"
    ) == [
        *uv.install_prefix(),
        "ext",
        "--index-url",
        "https://aelix/simple",
    ]
    assert _uv_env(uv, index_url="https://aelix/simple") == {
        "UV_EXTRA_INDEX_URL": "https://ambient-extra/simple"
    }


def test_translation_never_duplicates_the_chosen_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The index that will already be the default must not ALSO be offered as an extra.
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://dup/simple https://idx/simple")
    uv = _uv_backend()
    argv = build_pip_args(
        "ext", "pypi", backend=uv, index_url="https://idx/simple"
    )
    assert argv.count("--index-url") == 1
    assert _uv_env(uv, index_url="https://idx/simple") == {
        "UV_EXTRA_INDEX_URL": "https://dup/simple"
    }


@pytest.mark.parametrize(
    "name",
    [
        "UV_INDEX",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_CONFIG_FILE",
        "UV_NO_INDEX",
        "UV_FIND_LINKS",
    ],
)
def test_uv_own_index_env_suppresses_the_translation(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # Someone who configured uv directly owns that decision — a translated value would
    # silently override it. UV_CONFIG_FILE / UV_NO_INDEX / UV_FIND_LINKS count too:
    # each is a deliberate uv resolution decision an overlay would break.
    monkeypatch.setenv("PIP_INDEX_URL", "https://internal.corp/simple")
    monkeypatch.setenv(name, "https://their-uv-index/simple")
    uv = _uv_backend()
    assert build_pip_args("ext", "pypi", backend=uv) == [*uv.install_prefix(), "ext"]
    assert _uv_env(uv) == {}
    # …but pip's own view of the world is unchanged (uv_ambient_* is the gated one).
    assert _ei().read_pip_index_config().index_url == "https://internal.corp/simple"
    assert not _ei().uv_ambient_index_config()


# --- review: uv's PRIMARY configuration is a FILE, not an env var --------
#
# ~/.config/uv/uv.toml (and a project uv.toml / pyproject [tool.uv]) sets no UV_INDEX*
# variable, so an env-var-only suppression test let a stale pip.conf be translated on
# top of a deliberate uv pin — and uv's CLI/env > file precedence made the translation
# WIN. Confirmed live against uv 0.11.14: the same install contacted the pip.conf host
# instead of the uv.toml one.


@pytest.mark.parametrize("key", ["index-url", "index", "default-index", "no-index"])
def test_a_uv_toml_index_pin_suppresses_the_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, "[global]\nindex-url = https://pip-conf-pin.invalid/simple\n")
    _use_pip_conf(monkeypatch, conf)
    uv_toml = tmp_path / "uv.toml"
    uv_toml.write_text(f'{key} = "https://uv-config-pin.invalid/simple"\n', "utf-8")
    monkeypatch.setattr(
        _ei_mod, "_uv_config_files", lambda env=None, cwd=None: [str(uv_toml)]
    )
    assert not _ei().uv_ambient_index_config()
    assert _uv_env() == {}


def test_a_pyproject_tool_uv_index_pin_suppresses_the_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, "[global]\nindex-url = https://pip-conf-pin.invalid/simple\n")
    _use_pip_conf(monkeypatch, conf)
    proj = tmp_path / "pyproject.toml"
    proj.write_text(
        '[project]\nname = "x"\n[tool.uv]\nindex-url = "https://p.invalid/simple"\n',
        "utf-8",
    )
    monkeypatch.setattr(
        _ei_mod, "_uv_config_files", lambda env=None, cwd=None: [str(proj)]
    )
    assert not _ei().uv_ambient_index_config()


def test_a_uv_config_without_index_keys_does_not_suppress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This very repo's pyproject has [tool.uv.workspace] / [tool.uv.sources] and no
    # index pin — suppressing on the mere PRESENCE of a uv config would disable the
    # translation for anyone standing in a uv workspace.
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, "[global]\nindex-url = https://internal.corp/simple\n")
    _use_pip_conf(monkeypatch, conf)
    proj = tmp_path / "pyproject.toml"
    proj.write_text('[tool.uv.workspace]\nmembers = ["packages/*"]\n', "utf-8")
    monkeypatch.setattr(
        _ei_mod, "_uv_config_files", lambda env=None, cwd=None: [str(proj)]
    )
    assert _ei().uv_ambient_index_config().index_url == "https://internal.corp/simple"


def test_a_broken_uv_config_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, "[global]\nindex-url = https://internal.corp/simple\n")
    _use_pip_conf(monkeypatch, conf)
    bad = tmp_path / "uv.toml"
    bad.write_text("this is not toml [[[\n", "utf-8")
    monkeypatch.setattr(
        _ei_mod,
        "_uv_config_files",
        lambda env=None, cwd=None: [str(bad), str(tmp_path / "absent.toml")],
    )
    assert _ei().uv_ambient_index_config().index_url == "https://internal.corp/simple"


def test_uv_config_search_path_is_uvs_own(tmp_path: Path) -> None:
    # UV_CONFIG_FILE wins outright; otherwise the nearest project config walking UP
    # from cwd, then the user-level $XDG_CONFIG_HOME/uv/uv.toml.
    assert _REAL_UV_CONFIG_FILES({"UV_CONFIG_FILE": "/x/uv.toml"}) == ["/x/uv.toml"]
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "uv.toml").write_text("", "utf-8")
    paths = _REAL_UV_CONFIG_FILES(
        {"XDG_CONFIG_HOME": "/home/u/.config"}, cwd=str(nested)
    )
    assert str(tmp_path / "uv.toml") in paths
    assert paths[-1] == os.path.join("/home/u/.config", "uv", "uv.toml")


def test_pip_backend_argv_is_never_index_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pip reads its own configuration natively, so the argv must stay byte-for-byte
    # what it was before #113 — no injected flags, on either the default or the
    # explicit pip backend.
    monkeypatch.setenv("PIP_INDEX_URL", "https://internal.corp/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://mirror/simple")
    prefix = [sys.executable, "-m", "pip", "install"]
    assert build_pip_args("ext", "pypi") == [*prefix, "ext"]
    assert build_pip_args("ext", "pypi", backend=_ei().PIP_BACKEND) == [*prefix, "ext"]


@pytest.mark.parametrize("kind", ["path", "git"])
def test_non_pypi_targets_are_never_index_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "https://internal.corp/simple")
    uv = _uv_backend()
    target = str(tmp_path) if kind == "path" else "https://h/o/r.git"
    assert "--index-url" not in build_pip_args(target, kind, backend=uv)


class _EnvCapturingRunner(_FakeRunner):
    """A runner that also records the ENVIRONMENT its child would have inherited."""

    def __init__(self, returncode: int = 0) -> None:
        super().__init__(returncode)
        self.envs: list[dict[str, str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.envs.append(dict(os.environ))
        return super().__call__(argv)


def test_consent_block_shows_the_translated_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The substitution stays auditable BEFORE the y/N — as a named env line, since the
    # value no longer rides on argv — and the child really does inherit it.
    monkeypatch.setenv("PIP_INDEX_URL", "https://internal.corp/simple")
    _force_backend(monkeypatch, _uv_backend())
    runner = _EnvCapturingRunner()
    code = install_extension("internal-corp-ext", yes=True, runner=runner)
    assert code == 0
    out = capsys.readouterr().out
    assert "UV_INDEX_URL=https://internal.corp/simple" in out
    assert "--index-url" not in runner.calls[0]
    assert runner.envs[0]["UV_INDEX_URL"] == "https://internal.corp/simple"
    # …and the parent's environment is restored afterwards.
    assert "UV_INDEX_URL" not in os.environ


# --- review (BLOCKING): a credentialed ambient index must never be disclosed ---
#
# A corporate pip.conf is a 0600 file pip never exposes, and basic-auth IN the index
# URL is the standard Nexus/Artifactory form. Putting it on the uv argv published it
# to every user on the host (/proc/<pid>/cmdline is 0444, `ps -eo args` prints it) and
# into terminal scrollback / the TUI transcript / CI job logs via the consent block —
# a disclosure path that did not exist before #113.

_CREDENTIALED = "https://deployer:hunter2@nexus.corp.invalid/repository/pypi/simple"


def test_a_credentialed_ambient_index_never_reaches_argv_or_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, f"[global]\nindex-url = {_CREDENTIALED}\n")
    _use_pip_conf(monkeypatch, conf)
    _force_backend(monkeypatch, _uv_backend())
    runner = _EnvCapturingRunner()
    assert install_extension("some-ext", yes=True, runner=runner) == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert "hunter2" not in " ".join(runner.calls[0])
    # Shown, but redacted the way pip redacts it — the host is still auditable.
    assert "https://deployer:****@nexus.corp.invalid/repository/pypi/simple" in out
    # …and the real credential still reaches uv, through the environment.
    assert runner.envs[0]["UV_INDEX_URL"] == _CREDENTIALED


def test_display_argv_redacts_auth_and_quotes_every_element() -> None:
    ei = _ei()
    assert ei._redact_auth("https://u:p@h/x") == "https://u:****@h/x"
    assert ei._redact_auth("https://tok@h/x") == "https://****@h/x"
    assert ei._redact_auth("https://h/x") == "https://h/x"
    assert ei._redact_auth("some-ext") == "some-ext"  # non-URL passes through
    # One element can never break out of its line (no forged `Proceed? [y/N] y`),
    # nor repaint the screen with an escape sequence.
    shown = ei.display_argv(["pip", "install", "a\nProceed? [y/N] y", "b\x1b[2J"])
    assert "\n" not in shown
    assert "\x1b" not in shown
    assert shown.count("\\n") == 1
    # Ordinary non-ASCII text survives (shlex quotes it, but never escapes it).
    assert "ünïcode" in ei.display_argv(["ünïcode"])


# --- review (IMPORTANT): consent-block line injection via a pip.conf value ---
#
# configparser joins indented continuation lines with \n and the old code printed the
# value raw, so a writable pip.conf could inject a forged `Proceed? [y/N] y` plus a
# decoy argv line into the module's sole trust boundary.


def test_a_multiline_pip_conf_index_value_is_rejected_not_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(
        conf,
        "[global]\nindex-url = https://evil.invalid/simple\n"
        "\tProceed? [y/N] y\n"
        "\t  -> /usr/bin/uv pip install --python /x some-ext\n",
    )
    _use_pip_conf(monkeypatch, conf)
    assert not _ei().read_pip_index_config()  # not printable → not an index URL
    _force_backend(monkeypatch, _uv_backend())
    runner = _EnvCapturingRunner()
    assert install_extension("some-ext", yes=True, runner=runner) == 0
    out = capsys.readouterr().out
    assert "Proceed? [y/N] y" not in out
    assert "evil.invalid" not in out
    assert "UV_INDEX_URL" not in runner.envs[0]


def test_an_extra_index_list_keeps_only_its_usable_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pip splits the list on whitespace and each token stands alone, so a junk token is
    # dropped without discarding its healthy neighbours.
    conf = tmp_path / "pip.conf"
    _write_pip_conf(
        conf,
        "[global]\nextra-index-url = https://a.invalid/simple --no-verify "
        "https://b.invalid/simple\n",
    )
    _use_pip_conf(monkeypatch, conf)
    assert _ei().read_pip_index_config().extra_index_urls == (
        "https://a.invalid/simple",
        "https://b.invalid/simple",
    )


@pytest.mark.parametrize(
    "value",
    ["not-a-url", "ftp://h/x", "https://", "  ", "https://h/x\x1b[2J"],
)
def test_only_a_real_index_url_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    conf = tmp_path / "pip.conf"
    _write_pip_conf(conf, f"[global]\nindex-url = {value}\n")
    _use_pip_conf(monkeypatch, conf)
    assert _ei().read_pip_index_config().index_url is None


# --- review (IMPORTANT): pip's cross-file merge model ---------------------
#
# Oracle values below are pip 24.0's own `create_command("install").parse_args()`
# resolution for the identical fixtures (pip's default index_url is
# https://pypi.org/simple, which is "no ambient pin" — index_url is None here).


def test_extra_index_url_does_not_accumulate_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pip's Configuration dict REPLACES `global.extra-index-url` per file and
    # _update_defaults does `defaults[dest] = val`, never `+=`. Accumulating let a
    # decommissioned org mirror pip had discarded back into the resolution set — a
    # dependency-confusion vector the pip backend does not have.
    low, high = tmp_path / "org.conf", tmp_path / "user.conf"
    _write_pip_conf(low, "[global]\nextra-index-url = https://ORG-EXTRA/simple\n")
    _write_pip_conf(high, "[global]\nextra-index-url = https://USER-EXTRA/simple\n")
    _use_pip_conf(monkeypatch, low, high)
    cfg = _ei().read_pip_index_config()
    assert cfg.extra_index_urls == ("https://USER-EXTRA/simple",)  # pip oracle
    assert cfg.index_url is None


def test_section_precedence_is_resolved_after_the_cross_file_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pip resolves [global] → [install] → env over the ALREADY-merged dict, so an org's
    # low-precedence [install] pin beats a user's high-precedence [global] one. The
    # old per-file pass inverted exactly the guarantee this feature exists for.
    low, high = tmp_path / "org.conf", tmp_path / "user.conf"
    _write_pip_conf(low, "[install]\nindex-url = https://ORG-INSTALL/simple\n")
    _write_pip_conf(high, "[global]\nindex-url = https://USER-GLOBAL/simple\n")
    _use_pip_conf(monkeypatch, low, high)
    cfg = _ei().read_pip_index_config()
    assert cfg.index_url == "https://ORG-INSTALL/simple"  # pip oracle
    assert cfg.origin == str(low)


# --- review (IMPORTANT): PIP_CONFIG_FILE disables the per-user config ------


def test_an_existing_pip_config_file_skips_the_user_configs() -> None:
    # pip's `should_load_user_config`: an EXISTING PIP_CONFIG_FILE means the per-user
    # files are not loaded at all. Keeping them let a stale ~/.config/pip/pip.conf win
    # the index for a CI job that had deliberately pinned one explicit config.
    explicit = os.path.join(os.path.dirname(os.__file__), "os.py")  # any real file
    env = {
        "HOME": "/home/u",
        "XDG_CONFIG_HOME": "/home/u/.config",
        "XDG_CONFIG_DIRS": "/etc/xdg",
        "PIP_CONFIG_FILE": explicit,
    }
    paths = _REAL_PIP_CONFIG_CANDIDATES(env)
    assert paths[-1] == explicit
    if sys.platform != "win32":
        assert "/etc/pip.conf" in paths  # GLOBAL still loads
        assert os.path.join(sys.prefix, "pip.conf") in paths  # SITE still loads
        assert not [p for p in paths if p.endswith("/.config/pip/pip.conf")]
        assert not [p for p in paths if p.endswith("/.pip/pip.conf")]
    # A PIP_CONFIG_FILE that does NOT exist leaves the user configs in place.
    missing = dict(env, PIP_CONFIG_FILE="/nonexistent/explicit.conf")
    assert any(
        p.endswith(os.path.join("pip", "pip.conf")) or p.endswith("pip.ini")
        for p in _REAL_PIP_CONFIG_CANDIDATES(missing)
    )


def test_a_pinned_explicit_config_hides_a_stale_user_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / ".config"
    (home / "pip").mkdir(parents=True)
    _write_pip_conf(
        home / "pip" / "pip.conf",
        "[global]\nindex-url = https://STALE-USER-PIN.invalid/simple\n"
        "extra-index-url = https://USER-EXTRA/simple\n",
    )
    explicit = tmp_path / "explicit.conf"
    _write_pip_conf(explicit, "[global]\nextra-index-url = https://EXPLICIT-EXTRA/simple\n")
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "absent"))
    monkeypatch.setenv("PIP_CONFIG_FILE", str(explicit))
    monkeypatch.setattr(_ei_mod, "_pip_config_candidates", _REAL_PIP_CONFIG_CANDIDATES)
    cfg = _ei().read_pip_index_config()
    assert cfg.index_url is None  # pip oracle: pypi.org, i.e. no ambient pin
    assert cfg.extra_index_urls == ("https://EXPLICIT-EXTRA/simple",)  # pip oracle
