"""Issue #19 (ADR-0185) — ``aelix extension install`` tests.

Unit-level with an injected pip runner + input_fn, so NO real pip runs and the
environment is never mutated. Covers target classification, pip-arg building,
the consent gate, the offline guard, arg parsing, and the entry.py verb dispatch.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_coding_agent.cli.extension_install import (
    build_pip_args,
    classify_target,
    install_extension,
    run_extension_command,
)


class _FakeRunner:
    """Records the pip argv it was handed; returns a chosen exit code."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> object:
        self.calls.append(argv)
        return SimpleNamespace(returncode=self.returncode)


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

    def _fake_run(args: list[str], **_kw: object) -> int:
        captured.append(args)
        return 0

    monkeypatch.setattr(ei, "run_extension_command", _fake_run)
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

    def _boom(*_a: object, **_k: object) -> int:
        raise AssertionError("run_extension_command called for non-extension argv")

    monkeypatch.setattr(ei, "run_extension_command", _boom)

    def _fake_parse(_argv: list[str]) -> object:
        raise SystemExit(0)  # short-circuit past the rest of _async_main

    monkeypatch.setattr(entry, "parse_args", _fake_parse)
    with pytest.raises(SystemExit):
        await entry._async_main(["extensions-are-cool"])  # NOT the 'extension' verb
