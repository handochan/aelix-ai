"""Actual local-git extension installs through both pip and uv, in isolated venvs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".omc/probes/402-live"
OUT.mkdir(parents=True, exist_ok=True)
ENV = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL") if name in os.environ}
ENV.update({"PIP_CONFIG_FILE": os.devnull, "UV_CONFIG_FILE": os.devnull})


def run(argv: list[str], cwd: Path, env=ENV, *, expected=0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    return result


results = []
for backend in ("uv", "pip"):
    directory = OUT / backend
    directory.mkdir(exist_ok=True)
    environment = directory / "venv"
    run(["uv", "venv", "--python", "3.12", str(environment)], directory)
    python = environment / "bin/python"
    install = ["uv", "pip", "install", "--python", str(python)]
    for member in ("aelix-ai", "aelix-agent-core", "aelix-coding-agent"):
        install.extend(
            [
                "-e",
                str(ROOT / "packages" / member)
                + ("[tui]" if member == "aelix-coding-agent" else ""),
            ]
        )
    if backend == "pip":
        install.extend(["pip", "hatchling"])
    setup = run(install, directory)
    (directory / "setup.log").write_text(setup.stdout + setup.stderr)
    repository = directory / "repository"
    shutil.copytree(
        ROOT / "packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter", repository
    )
    package = repository / "aelix_starter/__init__.py"
    project = repository / "pyproject.toml"
    initial = package.read_text()
    project_initial = project.read_text()
    run(["git", "init", "-b", "main"], repository)
    run(["git", "config", "user.name", "Aelix isolated probe"], repository)
    run(["git", "config", "user.email", "probe@example.invalid"], repository)

    def commit(
        letter: str,
        version: str,
        *,
        package=package,
        initial=initial,
        project=project,
        project_initial=project_initial,
        repository=repository,
    ) -> str:
        package.write_text(initial + f'\nREVISION = "{letter}"\n')
        project.write_text(project_initial.replace('version = "0.1.0"', f'version = "{version}"'))
        run(["git", "add", "."], repository)
        run(["git", "commit", "-m", letter], repository)
        return run(["git", "rev-parse", "HEAD"], repository).stdout.strip()

    first = commit("A", "0.1.0")
    commit("B", "0.2.0")
    cwd = directory / "cwd"
    cwd.mkdir()
    agent = directory / "agent"
    env = dict(ENV)
    env.update(
        {
            "AELIX_CODING_AGENT_DIR": str(agent),
            "AELIX_SETTINGS_PATH": str(directory / "settings.json"),
            "AELIX_DEFAULT_CATALOG": "",
        }
    )
    cli = str(environment / "bin/aelix")
    spec = repository.as_uri().replace("file:", "git+file:", 1) + f"@main#x=@{first}"

    def command(
        label: str, args: list[str], expected=0, *, cli=cli, cwd=cwd, env=env, directory=directory
    ) -> subprocess.CompletedProcess[str]:
        result = run([cli, "extension", *args], cwd, env, expected=expected)
        (directory / f"{label}.log").write_text(result.stdout + result.stderr)
        return result

    def revision(*, python=python, cwd=cwd, env=env) -> str:
        return run(
            [str(python), "-c", "import aelix_starter; print(aelix_starter.REVISION)"], cwd, env
        ).stdout.strip()

    installed = command("mutable-install", ["install", spec, "--yes"])
    assert "NOT pinned" in installed.stdout and "integrity verified" not in installed.stdout
    assert revision() == "B"
    assert not (agent / "extension_pins.json").exists()
    commit("C", "0.3.0")
    updated = command("mutable-update", ["update", "--yes"])
    assert "NOT pinned" in updated.stdout and "integrity verified" not in updated.stdout
    assert revision() == "C"
    assert not (agent / "extension_pins.json").exists()
    refused = command("strict-refusal", ["install", spec, "--yes", "--strict"], expected=2)
    assert "must pin a full 40-hex" in refused.stderr
    assert revision() == "C"
    pinned = repository.as_uri().replace("file:", "git+file:", 1) + f"@{first}"
    command("real-pin", ["install", pinned, "--yes"])
    assert revision() == "A"
    pins = json.loads((agent / "extension_pins.json").read_text())
    assert next(iter(pins["pins"].values()))["gitSha"] == first
    verified = command("strict-real-pin", ["install", pinned, "--yes", "--strict"])
    assert "integrity verified" in verified.stdout
    assert revision() == "A"
    results.append(
        {
            "backend": backend,
            "mutable_installed": "B",
            "mutable_updated": "C",
            "strict_refused_exit": 2,
            "real_pinned_installed": "A",
            "strict_real_pin_exit": 0,
        }
    )
    print(json.dumps(results[-1]), flush=True)

(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
