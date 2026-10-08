"""Install the actual release wheels over beta.2 and verify Memory integration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

MEMORY_COMMIT = "8c10ec3bf90c213404e4b427ef2d1d304ff98592"
MEMORY_SOURCE = f"git+https://github.com/handochan/aelix-memory.git@{MEMORY_COMMIT}"
PACKAGES = {"aelix", "aelix-ai", "aelix-agent-core", "aelix-coding-agent"}


def check(dist: Path, memory_tests: Path, output: Path) -> None:
    import zipfile

    wheels = sorted(p for p in dist.glob("*.whl") if not p.name.startswith("aelix_server-"))
    assert len(wheels) == 4, "Expected the four published host wheels"
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for name in ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"):
                assert any(path.endswith(f"licenses/{name}") for path in archive.namelist())

    with tempfile.TemporaryDirectory(prefix="aelix-memory-release-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        agent = root / "agent"
        agent.mkdir()
        env = {**os.environ, "AELIX_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1"}
        env["AELIX_MEMORY_HOME"] = str(root / "memory")
        env.pop("PYTHONPATH", None)
        env.pop("AELIX_MEMORY_EMBEDDING_MODEL", None)
        venv = root / "venv"
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cli = python.with_name("aelix.exe" if os.name == "nt" else "aelix")

        def run(*args: str) -> str:
            result = subprocess.run(
                list(args), cwd=project, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180,
            )
            if result.returncode:
                raise RuntimeError(f"Candidate check failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
            return result.stdout.strip()

        run("uv", "venv", str(venv), "--python", sys.executable)
        run("uv", "pip", "install", "--python", str(python), "aelix[tui]==0.1.0b2")
        run(str(cli), "extension", "install", MEMORY_SOURCE, "--yes")
        assert not (root / "memory").exists(), "Installation created memory storage"
        settings = agent / "settings.json"
        settings.write_text('{"toolCardMaxLines": 9, "featuresAgents": false}\n', encoding="utf-8")
        run(str(python), "-m", "aelix_memory", "--project", str(project), "on")
        run(str(python), "-m", "aelix_memory", "--project", str(project), "remember",
            "--key", "upgrade", "--title", "Synthetic upgrade marker", "Keep UPGRADE-7401.")
        seed = '''import asyncio, os
from aelix_agent_core.session.jsonl_repo import JsonlSessionRepo, JsonlSessionCreateOptions
from aelix_ai.messages import UserMessage
async def seed():
    repo = JsonlSessionRepo(sessions_root=os.path.join(os.environ['AELIX_CODING_AGENT_DIR'], 'sessions'))
    session = await repo.create(JsonlSessionCreateOptions(cwd=os.getcwd()))
    await session.append_message(UserMessage(content='UPGRADE-7401 synthetic session', timestamp=1))
asyncio.run(seed())
'''
        run(str(python), "-c", seed)
        before = {p.relative_to(agent): p.read_bytes() for p in agent.rglob("*") if p.is_file()}
        assert any(p.suffix == ".jsonl" for p in before), "Missing synthetic host session"
        assert len(before) >= 3, "Missing installed extension records"
        run("uv", "pip", "install", "--python", str(python), "--reinstall", "--no-deps",
            *(str(wheel.resolve()) for wheel in wheels))
        for path, data in before.items():
            assert (agent / path).read_bytes() == data, f"Upgrade modified {path}"
        metadata = json.loads(run(str(python), "-c", '''import importlib.metadata, json
from pathlib import Path
import aelix_ai, aelix_agent_core, aelix_coding_agent, aelix_memory
from aelix_coding_agent.extensions.api import ExtensionAPI
assert callable(ExtensionAPI.register_setting)
for package in (aelix_ai, aelix_agent_core, aelix_coding_agent, aelix_memory):
    assert 'site-packages' in Path(package.__file__).parts, package.__file__
print(json.dumps({p: importlib.metadata.version(p) for p in
    ['aelix', 'aelix-ai', 'aelix-agent-core', 'aelix-coding-agent']}))
'''))
        assert set(metadata) == PACKAGES and len(set(metadata.values())) == 1
        version = next(iter(metadata.values()))
        assert version in run(str(cli), "--version")
        verify = run(str(cli), "extension", "verify", "aelix-memory")
        assert "BOUND" in verify and "all 1 endpoint(s) BOUND" in verify
        prefix = (str(python), "-m", "aelix_memory", "--project", str(project))
        assert json.loads(run(*prefix, "status"))["mode"] == "on"
        assert "UPGRADE-7401" in run(*prefix, "list")
        run("uv", "pip", "install", "--python", str(python), "pytest>=8,<10", "pytest-asyncio>=0.24,<2")
        tests = run(str(python), "-m", "pytest", "-q", str(memory_tests.resolve()))
        assert "passed" in tests and "failed" not in tests
        host_tests = Path(__file__).resolve().parent.parent / "tests"
        focused = run(str(python), "-m", "pytest", "-q",
            str(host_tests / "extensions/test_settings_contributions.py"),
            str(host_tests / "tui/test_run_tui_extension_settings.py"),
            str(host_tests / "builtin/test_plan_tool_provenance.py"))
        assert "passed" in focused and "failed" not in focused
        result = {
            "passed": True, "os": platform.system(), "python": platform.python_version(),
            "packages": metadata, "memory_version": "0.2.0", "memory_source": MEMORY_SOURCE,
            "upgrade_from": "0.1.0b2", "default_off_created_no_storage": True,
            "global_on_and_memory_preserved": True, "settings_sessions_and_extension_records_preserved": True,
            "memory_tests": tests.splitlines()[-1], "manifest": "BOUND",
            "installed_host_settings_plan_and_modal_tests": focused.splitlines()[-1],
            "wheels": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wheels},
            "live_model_run": False, "interactive_desktop_run": False,
        }
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--memory-tests", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("memory-release-check.json"))
    args = parser.parse_args()
    check(args.dist.resolve(), args.memory_tests, args.output)
