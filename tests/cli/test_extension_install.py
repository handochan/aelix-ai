"""Issue #19 (ADR-0185) — ``aelix extension install`` tests.

Unit-level with an injected pip runner + input_fn, so NO real pip runs and the
environment is never mutated. Covers target classification, pip-arg building,
the consent gate, the offline guard, arg parsing, and the entry.py verb dispatch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import ExtensionSourceObject, SettingsManager
from aelix_coding_agent.cli.extension_install import (
    build_pip_args,
    classify_source,
    classify_target,
    install_extension,
    run_extension_command,
    run_extension_command_async,
)


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


def test_install_pip_failure_propagates(tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    runner = _FakeRunner(returncode=7)
    code = install_extension(str(tmp_path / "ext"), yes=True, runner=runner)
    assert code == 7


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
    # Review LOW: a missing pip module is detected up front (exit 2), not
    # mislabeled as a pip install failure. The pre-check applies to the DEFAULT
    # runner only, so this uses runner=None + a forced-missing find_spec — the
    # guard returns before any real subprocess.
    from aelix_coding_agent.cli import extension_install as ei

    (tmp_path / "ext").mkdir()
    monkeypatch.setattr(ei.importlib.util, "find_spec", lambda _name: None)
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
    from aelix_coding_agent.cli import extension_install as ei

    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("myext", "my-ext-dist", "1.0.0")],
    )
    assert run_extension_command(["list"]) == 0
    out = capsys.readouterr().out
    assert "myext" in out and "my-ext-dist" in out and "1.0.0" in out


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
