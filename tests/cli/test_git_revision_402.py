"""The installer's URL-path revision alone can establish a git commit pin."""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_install as ei

from tests.cli.test_extension_install import (
    _FakeRunner,
    _read_pins,
)
from tests.cli.test_extension_install import (
    _isolate_settings as _isolate_settings,
)
from tests.cli.test_extension_install import (
    _no_ambient_pip_index as _no_ambient_pip_index,
)

SHA = "a" * 40
OTHER = "b" * 40


@pytest.mark.parametrize(
    "spec",
    [
        f"git+https://h/r.git@main#x=@{SHA}",
        f"git+https://h/r.git#subdirectory=pkg@{SHA}",
        f"git+https://h/r.git?x=@{SHA}",
        f"git+https://u@{SHA}/r.git",
        f"git+ssh://{SHA}@h/r.git@main",
        f"pkg @ git+https://h/r.git@main#x=@{SHA}",
        f"git+https://h/r.git@{SHA}@main",
        f"git+file:///tmp/r%23fragment@{SHA}",
        f"git+file:///tmp/r%3Fquery@{SHA}",
    ],
)
def test_nonrevision_sha_cannot_verify_a_mutable_install(spec: str) -> None:
    assert ei._extract_git_sha(spec) is None


@pytest.mark.parametrize(
    "spec",
    [
        f"git+https://h/r.git@{SHA}",
        f"git+https://user@h/r.git@{SHA.upper()}#x=@{OTHER}",
        f"git+ssh://git@h/r.git@{SHA}?x=@{OTHER}#subdirectory=pkg",
        f"pkg[extra] @ git+https://h/r.git@{SHA}#egg=pkg",
        f"git+file:///tmp/a%20b/r.git@{SHA}",
    ],
)
def test_real_path_revision_is_the_pin(spec: str) -> None:
    assert ei._extract_git_sha(spec) == SHA


def test_repo_identity_removes_only_the_actual_revision() -> None:
    spec = f"git+https://user@h/r.git@{SHA}?x=@{OTHER}#subdirectory=pkg@{OTHER}"
    assert ei._git_repo_identity(spec) == (
        f"git+https://user@h/r.git?x=@{OTHER}#subdirectory=pkg@{OTHER}"
    )
    mutable = f"git+https://h/r.git@main#x=@{SHA}"
    assert ei._git_repo_identity(mutable) == mutable
    assert ei._git_repo_identity(f"git+file:///tmp/r.git@{SHA}") == "git+file:///tmp/r.git"


@pytest.mark.parametrize("suffix", [f"#x=@{SHA}", f"#subdirectory=pkg@{SHA}", f"?x=@{SHA}"])
def test_strict_refuses_before_running_installer(suffix: str) -> None:
    # Old code first recorded a false TOFI pin and then allowed strict mode.
    assert (
        ei.install_extension(f"git+https://h/r.git@main{suffix}", yes=True, runner=_FakeRunner())
        == 0
    )
    runner = _FakeRunner()
    assert (
        ei.install_extension(
            f"git+https://h/r.git@main{suffix}", yes=True, strict=True, runner=runner
        )
        == 2
    )
    assert runner.calls == []


def test_tofi_does_not_record_or_verify_a_fragment_pin(tmp_path: Path, capsys) -> None:
    spec = f"git+https://h/r.git@main#x=@{SHA}"
    for _ in range(2):
        assert ei.install_extension(spec, yes=True, runner=_FakeRunner()) == 0
        text = capsys.readouterr().out
        assert "NOT pinned" in text
        assert "integrity verified" not in text
        assert _read_pins(tmp_path) == {}


def test_update_cannot_reuse_a_fragment_as_a_strict_pin() -> None:
    spec = f"git+https://h/r.git@main#x=@{SHA}"
    assert ei.install_extension(spec, yes=True, runner=_FakeRunner()) == 0
    settings = SettingsManager.in_memory(
        {"extensionSources": [{"spec": spec, "kind": "git", "name": "r"}]}
    )
    runner = _FakeRunner()
    assert (
        ei.run_extension_command(["update", "--yes", "--strict"], settings=settings, runner=runner)
        == 2
    )
    assert runner.calls == []


@pytest.mark.parametrize("control", ["\t\t", "\r\r", "\n\n"])
def test_raw_url_controls_cannot_turn_commit_drift_into_first_trust(
    tmp_path: Path, capsys, control: str
) -> None:
    for sha in (SHA, OTHER):
        spec = f"git+https://h/r.git@{sha}{control}"
        assert ei._extract_git_sha(spec) is None
        assert ei.install_extension(spec, yes=True, runner=_FakeRunner()) == 0
        assert "NOT pinned" in capsys.readouterr().out
        assert _read_pins(tmp_path) == {}
        runner = _FakeRunner()
        assert ei.install_extension(spec, yes=True, strict=True, runner=runner) == 2
        assert runner.calls == []


@pytest.mark.parametrize("suffix", ["#egg=probe402", "#x=unicode-probe"])
def test_named_url_preserves_unicode_ref_before_fragment(
    tmp_path: Path, capsys, suffix: str
) -> None:
    url = f"git+https://h/r.git@{SHA}\u00a0{suffix}"
    spec = f'probe402 @ {url} ; python_version >= "3"'
    assert ei._git_url_part(spec) == url
    assert ei._extract_git_sha(spec) is None
    assert ei.install_extension(spec, yes=True, runner=_FakeRunner()) == 0
    assert "NOT pinned" in capsys.readouterr().out
    assert _read_pins(tmp_path) == {}
    runner = _FakeRunner()
    assert ei.install_extension(spec, yes=True, strict=True, runner=runner) == 2
    assert runner.calls == []


def test_named_reference_ascii_separators_do_not_change_the_pin() -> None:
    spec = f'probe402[extra]\t@\tgit+https://h/r.git@{SHA}\t; python_version >= "3"'
    assert ei._extract_git_sha(spec) == SHA
    assert ei._pin_identity(spec, "git") == "git+https://h/r.git"
