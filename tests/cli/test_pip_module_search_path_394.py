"""#394: real CPython children must not search the installer's cwd for pip.

The trusted pip shim is supplied through an absolute PYTHONPATH, so these tests
measure module selection even in a uv venv without pip. It records execution and
stages a download fixture; it does not install distributions. Real pip/uv CLI
installation is a separate live check. Production argv reaches each child intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_install as ei
from aelix_coding_agent.cli import extension_pins

_WHEEL_BYTES = b"t394-downloaded-artifact"
_SHIM = """\
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if ORIGIN == "hostile":
    Path(__file__).with_name("executed").write_text("cwd pip ran", encoding="utf-8")
if args[0] == "download":
    dest = Path(args[args.index("--dest") + 1])
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "probe394-1.0-py3-none-any.whl").write_bytes(b"t394-downloaded-artifact")
print(json.dumps({
    "origin": ORIGIN,
    "file": __file__,
    "cwd": os.getcwd(),
    "args": args,
    "safe_path": sys.flags.safe_path,
    "pip_config": os.environ.get("PIP_CONFIG_FILE"),
}))
"""


class _ModuleProbe:
    def __init__(self, hostile: Path, trusted: Path, config: Path) -> None:
        self.hostile = hostile
        self.trusted = trusted
        self.config = config
        self.calls: list[tuple[list[str], dict]] = []
        self.env = {**os.environ, "PYTHONPATH": str(trusted), "PIP_CONFIG_FILE": str(config)}
        # A host's opt-in must not make the pre-fix counterfactual pass safely.
        self.env.pop("PYTHONSAFEPATH", None)
        self.env.pop("PYTHONINSPECT", None)

    @property
    def marker(self) -> Path:
        return self.hostile / "pip" / "executed"

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=self.env,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        self.calls.append((list(argv), json.loads(result.stdout)))
        return result

    def assert_trusted(self, operation: str) -> dict:
        argv, observed = self.calls[-1]
        assert argv[:4] == [sys.executable, "-P", "-m", "pip"]
        assert observed["origin"] == "trusted"
        assert Path(observed["file"]) == self.trusted / "pip" / "__main__.py"
        assert Path(observed["cwd"]) == self.hostile
        assert observed["safe_path"] is True
        assert observed["args"][0] == operation
        assert observed["pip_config"] == str(self.config)
        assert not self.marker.exists()
        return observed


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _ModuleProbe:
    hostile = tmp_path / "hostile-cwd"
    trusted = tmp_path / "trusted-root"
    for root, origin in ((hostile, "hostile"), (trusted, "trusted")):
        package = root / "pip"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(f"ORIGIN = {origin!r}\n{_SHIM}", encoding="utf-8")
    config = tmp_path / "user-pip.conf"
    config.write_text("[global]\ndisable-pip-version-check = true\n", encoding="utf-8")
    monkeypatch.chdir(hostile)
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    return _ModuleProbe(hostile, trusted, config)


@pytest.mark.parametrize("operation", ["install", "uninstall", "download", "verified-install"])
def test_each_pip_command_blocks_automatic_cwd_search(
    probe: _ModuleProbe, tmp_path: Path, operation: str
) -> None:
    dest = str(tmp_path / "downloads")
    if operation == "install":
        argv = ei.build_pip_args("probe394", "pypi")
    elif operation == "uninstall":
        argv = ei.PIP_BACKEND.uninstall_args("probe394")
    elif operation == "download":
        argv = ei.build_download_args("probe394", index_url=None, extra_index_urls=None, dest=dest)
    else:
        argv = ei._rewrite_pypi_local(dest, "probe394", upgrade=True)

    # Exact pre-fix Python prefix: demonstrate the attack with the same cwd/env.
    assert argv[1] == "-P"
    probe([argv[0], *argv[2:]])
    _, before = probe.calls[-1]
    assert before["origin"] == "hostile"
    assert before["safe_path"] is False
    assert Path(before["file"]) == probe.hostile / "pip" / "__main__.py"
    assert probe.marker.read_text(encoding="utf-8") == "cwd pip ran"
    probe.marker.unlink()

    probe(argv)
    after = probe.assert_trusted("install" if operation == "verified-install" else operation)
    assert after["args"] == before["args"]  # Only Python module lookup changes.


@pytest.mark.parametrize("no_verify", [True, False])
def test_typed_relative_install_runs_trusted_pip_in_the_typed_cwd(
    probe: _ModuleProbe, no_verify: bool
) -> None:
    wheel = probe.hostile / "probe394-1.0-py3-none-any.whl"
    wheel.write_bytes(_WHEEL_BYTES)
    assert (
        ei.install_extension(
            f"./{wheel.name}", yes=True, no_verify=no_verify, runner=probe, announce=False
        )
        == 0
    )
    assert len(probe.calls) == 1
    observed = probe.assert_trusted("install")
    installed_path = Path(observed["args"][-1])
    if no_verify:
        assert installed_path == wheel
    else:
        assert installed_path != wheel  # Default verification installs staged bytes.
        pins = extension_pins.load_pins(
            extension_pins.pins_file_path(probe.hostile.parent / "agent")
        )
        assert pins[str(wheel)].sha256 == hashlib.sha256(_WHEEL_BYTES).hexdigest()


def test_remove_flow_runs_trusted_pip_and_drops_the_record(
    probe: _ModuleProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ei,
        "list_installed_extensions",
        lambda: [ei.InstalledExtension("probe394", "probe394-dist", "1.0")],
    )
    settings = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "probe394", "kind": "pypi", "name": "probe394"}]}
    )
    assert (
        ei.run_extension_command(["remove", "probe394", "--yes"], settings=settings, runner=probe)
        == 0
    )
    assert len(probe.calls) == 1
    assert probe.assert_trusted("uninstall")["args"] == ["uninstall", "-y", "probe394-dist"]
    assert settings.get_extension_sources() == []


def test_verified_pypi_flow_downloads_and_rewrites_with_the_same_protection(
    probe: _ModuleProbe,
) -> None:
    assert (
        ei.install_extension(
            "probe394",
            yes=True,
            verify_pypi=True,
            upgrade=True,
            index_url="https://index.invalid/simple",
            runner=probe,
            announce=False,
        )
        == 0
    )
    assert len(probe.calls) == 2
    download_argv, download = probe.calls[0]
    assert download_argv[:4] == [sys.executable, "-P", "-m", "pip"]
    assert download["origin"] == "trusted"
    assert download["safe_path"] is True
    assert Path(download["cwd"]) == probe.hostile
    assert "--index-url" in download["args"]
    installed = probe.assert_trusted("install")["args"]
    dest = download["args"][download["args"].index("--dest") + 1]
    assert installed == ["install", "--upgrade", "--no-index", "--find-links", dest, "probe394"]
    assert not Path(dest).exists()
    pin = extension_pins.load_pins(extension_pins.pins_file_path(probe.hostile.parent / "agent"))[
        "probe394"
    ]
    assert pin.version == "1.0"
    assert pin.sha256 == hashlib.sha256(_WHEEL_BYTES).hexdigest()


def test_explicit_pythonpath_still_applies_with_safe_path(probe: _ModuleProbe) -> None:
    # The user's configured environment remains trusted (ADR-0255 §12).
    # -P removes only Python's automatic cwd entry; it is not -I or -E.
    probe.env["PYTHONPATH"] = str(probe.hostile)
    probe(ei.PIP_BACKEND.uninstall_args("probe394"))
    _, observed = probe.calls[-1]
    assert observed["safe_path"] is True
    assert observed["origin"] == "hostile"
    assert probe.marker.exists()
