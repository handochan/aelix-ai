"""Run the guide's copy recipe when PATH Python cannot supply certifi (#573).

The real Aelix CLI supplies the path; the actual platform shell runs the recipe;
the resulting bundle lets its provider request reach a real private-CA TLS
server. The local server returns an identifiable API error rather than a model
response: reaching it proves the TLS remedy, not model availability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.providers.test_tls_failure_not_retried_551 import _TlsServer
from tests.providers.test_tls_strict import _make_chain

_GUIDE = Path(__file__).resolve().parents[2] / "docs/guides/tls-and-corporate-ca.md"
_SHELLS = ["powershell", "pwsh"] if sys.platform == "win32" else ["sh"]
_ARRIVED = "aelix-573-corporate-ca-roundtrip"


def _certificates(data: bytes) -> list[str]:
    # Compare the copied roots, without imposing a new parser's certificate
    # policy on certifi's existing roots. PEM line endings differ in PowerShell.
    blocks = re.findall(b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", data, re.S)
    return [
        hashlib.sha256(ssl.PEM_cert_to_DER_cert(block.decode("ascii"))).hexdigest()
        for block in blocks
    ]


def _recipe(shell: str) -> str:
    language = "powershell" if shell != "sh" else "sh"
    blocks = re.findall(rf"```{language}\n(.*?)```", _GUIDE.read_text(encoding="utf-8"), re.S)
    return textwrap.dedent(next(block for block in blocks if "ca-bundle.pem" in block))


@pytest.mark.parametrize("shell", _SHELLS)
def test_the_guide_copy_works_without_a_usable_path_python(tmp_path: Path, shell: str) -> None:
    cli = Path(sys.executable).parent / ("aelix.exe" if sys.platform == "win32" else "aelix")
    assert cli.is_file(), "the test environment must install the workspace CLI"
    executable = shutil.which(shell)
    if not executable and shell == "pwsh" and os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("PowerShell 7 is optional locally; Windows CI provisions both shells")
    assert executable, f"this platform must provide its documented shell: {shell}"
    shim = tmp_path / "bin"
    shim.mkdir()
    python_called = tmp_path / "path-python-was-called"
    if sys.platform == "win32":
        (shim / "aelix.cmd").write_text(f'@echo off\n"{cli}" %*\n', encoding="utf-8")
        for name in ("python", "python3"):
            (shim / f"{name}.cmd").write_text(
                f'@echo off\n>>"{python_called}" echo unavailable\nexit /b 97\n',
                encoding="utf-8",
            )
    else:
        (shim / "aelix").symlink_to(cli)
        for name in ("python", "python3"):
            poison = shim / name
            poison.write_text(
                f"#!/bin/sh\nprintf unavailable >> {shlex.quote(str(python_called))}\nexit 97\n"
            )
            poison.chmod(0o755)

    ca, chain = _make_chain(tmp_path, conformant=True)
    (tmp_path / "corp-root.pem").write_bytes(ca.read_bytes())
    answer = json.dumps({"error": {"message": _ARRIVED}}).encode()
    server = _TlsServer(
        chain,
        answer=(
            b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(answer)}\r\nConnection: close\r\n\r\n".encode()
            + answer
        ),
    )
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text("{}")
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "probe": {
                        "baseUrl": f"https://localhost:{server.port}/v1",
                        "apiKey": "PROBE_KEY",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "probe-model",
                                "name": "probe",
                                "contextWindow": 8000,
                                "maxTokens": 100,
                                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            }
                        ],
                    }
                }
            }
        )
    )
    env = os.environ.copy()
    for key in (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env.update(
        {
            "PATH": str(shim) + os.pathsep + env.get("PATH", ""),
            "AELIX_CODING_AGENT_DIR": str(agent),
            "AELIX_SETTINGS_PATH": str(agent / "settings.json"),
            "AELIX_CODING_AGENT_SESSION_DIR": str(tmp_path / "sessions"),
            "PROBE_KEY": "sk-fake-573",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        }
    )
    shell_argv = (
        [executable, "-e"]
        if shell == "sh"
        else [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]
    )

    def call(argv: list[str], script: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            input=script,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )

    try:
        unavailable = call(
            shell_argv, "python -m certifi\n" + ("exit $LASTEXITCODE\n" if shell != "sh" else "")
        )
        assert unavailable.returncode == 97
        assert python_called.is_file()
        python_called.unlink()

        diagnostic = call([str(cli), "status", "--json", "--no-extensions"])
        assert diagnostic.returncode == 0, diagnostic.stderr
        bundle = Path(json.loads(diagnostic.stdout)["trust_store"]["certifi_path"])
        text = call([str(cli), "status", "--no-extensions"])
        assert "certifi bundle" in text.stdout and str(bundle) in text.stdout
        original = bundle.read_bytes()
        original_hash = hashlib.sha256(original).hexdigest()

        request = [str(cli), "--provider", "probe", "--model", "probe-model", "-p", "hi"]
        before = call(request)
        assert before.returncode != 0
        assert "TLS certificate verification failed" in before.stdout + before.stderr
        assert server.requests == 0

        target = tmp_path / "bundle copy"
        script = _recipe(shell)
        # The POSIX user pastes status's path into this documented placeholder.
        # Only the output HOME path is redirected to the test's private folder.
        script = re.sub(
            r"(?m)^certifi_bundle=.*$",
            lambda _: "certifi_bundle=" + shlex.quote(str(bundle)),
            script,
        )
        for spelling in ("$HOME/.aelix/ca-bundle.pem", "$HOME\\.aelix\\ca-bundle.pem"):
            script = script.replace(spelling, str(target / "ca-bundle.pem"))
        script = script.replace("$HOME/.aelix", str(target)).replace("$HOME\\.aelix", str(target))
        if shell == "sh":
            script += "\naelix --provider probe --model probe-model -p hi\n"
        else:
            script = "$ErrorActionPreference = 'Stop'\n" + script
            script += (
                "\n$ErrorActionPreference = 'Continue'\n"
                "aelix --provider probe --model probe-model -p hi\nexit $LASTEXITCODE\n"
            )
        followed = call(shell_argv, script)
        assert followed.returncode == 1, followed.stdout + followed.stderr
        assert _ARRIVED in followed.stdout + followed.stderr, followed.stdout + followed.stderr
        assert "TLS certificate verification failed" not in followed.stdout + followed.stderr
        assert server.requests >= 1
        copied = (target / "ca-bundle.pem").read_bytes()
        assert _certificates(copied) == _certificates(original) + _certificates(ca.read_bytes())
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == original_hash
        assert not python_called.exists(), "the recipe relied on PATH Python"
    finally:
        server.close()
