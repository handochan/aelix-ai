#!/usr/bin/env python3
"""Smoke-test a release candidate after ``uv tool install`` put its BUILT wheels in a tool environment.

release.yml's ``candidate-smoke`` job (#382, ADR-0269) downloads the build job's wheels on
ubuntu, macos and windows, installs them the way install.sh / install.ps1 Step 5 do::

    uv tool install --force --find-links <dist> --python <request> 'aelix[tui]==<version>'

where ``<request>`` is what ``request --version <version>`` prints: the interpreter request
the installers make for that version (install.sh's ``python_request_for``, install.ps1's
``Get-PythonRequest``). The job sets ``UV_PYTHON_PREFERENCE=only-managed`` and an empty
``UV_PYTHON_INSTALL_DIR`` for that command, so uv downloads the newest CPython inside the
request - the interpreter the installers give a user who has no Python in that range - and
not whichever Python the runner image carries. It then runs this script under the tool
environment's OWN interpreter, so nothing else has to be installed to run it. Stdlib only,
Python 3.11+. Nothing is published until every leg of that job is green: ``github-release``
and ``publish`` both wait for it.

``request`` -- prints the interpreter request the installers make for ``--version``.

``installed`` -- what uv put in the environment is the candidate:

* ``--python-request`` is the request the installers make for ``--version``;
* ``SHA256SUMS`` (the manifest github-release attaches) is in the form ``sha256sum`` writes
  (``<sha256>  <name>``, lower-case hex, two spaces, LF, each name once). That is stricter
  than install.sh Step 4: the smoke refuses every form install.sh refuses (a ``*``
  binary-mode marker, one space, CRLF), and also upper-case hex and a duplicate line, which
  install.sh accepts; install.ps1 reads a looser form still. It holds for every file in the
  artifact, lists every wheel and sdist, and names
  ``--version`` where install.sh Step 5 reads the version it pins (the first
  ``aelix-<version>-py3-none-any.whl`` entry);
* this interpreter (the tool environment's) is inside that request, and its base is a
  CPython uv downloaded into ``--managed-python-dir``;
* the artifact holds exactly the four published wheels, all at ``--version``;
* every file each wheel's RECORD lists with a hash - its ``.dist-info`` files included - is
  installed here with that hash, and each distribution reports ``--version``. The
  ``==<version>`` pin keeps a NEWER PyPI ``aelix`` out (install.sh Step 5). A file of the
  SAME version from the index - one an earlier run of this tag uploaded, or any build other
  than this one - would satisfy the pin too; this is the check that would see it standing
  in for the wheel under test;
* every top-level package the wheels ship imports, and so does the TUI the installers'
  default ``[tui]`` extra exists for (``aelix_coding_agent.tui``: prompt-toolkit, rich).

``turn`` -- the real entry point runs a turn, with no network and no real credentials:

* ``aelix --version`` prints ``--version``;
* a stub OpenAI-compatible server listens on 127.0.0.1; a ``models.json`` provider points at
  it with a fake key; HOME and every AELIX_* directory are in a scratch directory; then
  ``aelix --offline --provider ... --model ... -p ...`` must exit 0 and print the stub's
  reply, and the stub must have seen exactly one request of any method: a chat-completions
  POST for the smoke model, carrying the fake key. The report counts the session files the
  turn wrote under the scratch session directory; none is required.

Why a ``-p`` turn and not ``--list-models``: measured on a 0.1.0b2 build (2026-10-20,
PYTHONPROFILEIMPORTTIME), ``--list-models`` imports 164 first-party modules and never
``openai``; it builds no client and streams nothing. The ``-p`` turn imports 175, ``openai``
and ``httpx`` among them, resolves the key through models.json, streams SSE through the
agent loop and writes a session (reported, not checked) - the path whose dependency range
#278 just moved.

Exit status 0 when every check passed; otherwise 1 and an ``::error::`` line naming the
first check that failed. An exception no check expected (a stub that cannot bind, a file
that cannot be read) ends in Python's traceback and exit status 1 instead.

That line survives a Windows leg's stdout (#535). The job runs this script with ``-I``,
which ignores PYTHONIOENCODING and PYTHONUTF8, and a redirected stdout on Windows is the
locale code page (cp1252) with ``errors='strict'``: a failure message quoting a character
cp1252 lacks (aelix's stderr, a path) raised ``UnicodeEncodeError`` inside the ``print`` and
the leg showed that traceback instead of the check. :func:`main` therefore first sets
stdout's and stderr's error handler to ``backslashreplace``, so such a character prints as
``\\u2192`` and every line stays one line; the encoding itself is left as it is.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import zipfile
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

#: The publish set (release.yml's build job asserts the same four, and drops aelix-server).
PUBLISHED = ("aelix", "aelix-ai", "aelix-agent-core", "aelix-coding-agent")

#: The module the default ``aelix`` launch (no -p) reaches, and the reason the
#: installers install ``aelix[tui]``. Importing it pulls prompt-toolkit and rich.
TUI_MODULE = "aelix_coding_agent.tui"

#: What the stub answers, and the names the smoke turn uses. None of them is a secret.
SMOKE_PROVIDER = "candidate-smoke"
SMOKE_MODEL = "smoke-echo"
SMOKE_KEY_ENV = "AELIX_CANDIDATE_SMOKE_KEY"
SMOKE_KEY = "fake-key-candidate-smoke-382"
SMOKE_REPLY = "candidate smoke reply 7f3a"

#: What a child needs from the parent to boot at all (tests/env_sandbox.py's
#: _CHILD_BOOT_KEYS: SystemRoot for Winsock, TEMP/TMP for tempfile, ...). Looked up one by
#: one with ``.get``, never matched against ``os.environ``'s own keys: on Windows those
#: iterate UPPER-CASE (``SYSTEMROOT``), so a match on ``SystemRoot`` finds nothing and the
#: child dies importing asyncio with WinError 10106 (#209).
_BOOT_KEYS = (
    "PATH",
    "SystemRoot",
    "SystemDrive",
    "windir",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
)


#: The two interpreter requests the installers choose between by the release they install
#: (install.sh's ``py_supported`` / ``py_before_b3``, install.ps1's ``$PySupported`` /
#: ``$PyBeforeB3``; #278, ADR-0265). tests/test_release_candidate_smoke.py holds these and
#: :func:`python_request_for` equal to both installers'.
PY_SUPPORTED = ">=3.11,<3.15"
PY_BEFORE_B3 = ">=3.11,<3.14"

#: install.ps1's ``Get-PythonRequest`` pattern, verbatim; install.sh's awk reads the same shape.
_RELEASE_VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)(?:(a|b|rc)([0-9]+))?$")
_PRE_RELEASE_KIND = {"a": 0, "b": 1, "rc": 2}
#: (major, minor, patch, kind, number) of 0.1.0b3, the first release the wider range is for.
_FIRST_ON_PY_SUPPORTED = (0, 1, 0, 1, 3)


class SmokeFailure(Exception):
    """One failed check; the message is what the job log shows."""


def python_request_for(version: str) -> str:
    """The interpreter request install.sh / install.ps1 Step 5 make for aelix ``version``.

    install.sh's ``python_request_for`` and install.ps1's ``Get-PythonRequest``: the wider
    range only for a version provably 0.1.0b3 or later - N.N.N with an optional aN / bN /
    rcN, compared as numbers, so 0.1.0b10 is after 0.1.0b3 - and the older one for anything
    else (a dev, post or local version, a typo).
    """

    match = _RELEASE_VERSION.fullmatch(version)
    if match is None:
        return PY_BEFORE_B3
    kind = _PRE_RELEASE_KIND.get(match[4] or "", 3)
    got = (int(match[1]), int(match[2]), int(match[3]), kind, int(match[5] or 0))
    return PY_SUPPORTED if got >= _FIRST_ON_PY_SUPPORTED else PY_BEFORE_B3


# --- installed -----------------------------------------------------------------------------


def parse_request(request: str) -> list[tuple[str, tuple[int, ...]]]:
    """``'>=3.11,<3.15'`` (spaces allowed) -> ``[('>=', (3, 11)), ('<', (3, 15))]``.

    Only ``>=`` and ``<`` clauses: the shape both installers' ranges have. Anything
    else is refused rather than half-understood.
    """

    clauses: list[tuple[str, tuple[int, ...]]] = []
    for raw in request.split(","):
        part = raw.strip()
        match = re.fullmatch(r"(>=|<)\s*(\d+(?:\.\d+)*)", part)
        if match is None:
            raise SmokeFailure(f"interpreter request {request!r}: cannot read clause {part!r}")
        clauses.append((match[1], tuple(int(x) for x in match[2].split("."))))
    if not clauses:
        raise SmokeFailure(f"interpreter request {request!r} has no clauses")
    return clauses


def check_interpreter(request: str, version: Sequence[int]) -> None:
    for op, bound in parse_request(request):
        have = tuple(version[: len(bound)])
        inside = have >= bound if op == ">=" else have < bound
        if not inside:
            shown = ".".join(str(x) for x in version[:3])
            raise SmokeFailure(
                f"the tool environment runs Python {shown}, outside the installers' "
                f"request {request!r}"
            )


def check_managed_interpreter(managed_root: Path, base_prefix: str) -> None:
    """The tool environment's base interpreter is one uv downloaded into ``managed_root``.

    The job empties ``UV_PYTHON_INSTALL_DIR`` and sets ``UV_PYTHON_PREFERENCE=only-managed``
    so the smoke runs on what a user with no Python in range gets: the newest CPython uv can
    download inside the request. A base outside that directory is the runner's own Python,
    which no such user has.
    """

    root = Path(os.path.abspath(managed_root))
    base = Path(os.path.abspath(base_prefix))
    inside = any(
        candidate.is_relative_to(parent)
        for candidate in (base, base.resolve())
        for parent in (root, root.resolve())
    )
    if not inside:
        raise SmokeFailure(
            f"the tool environment's interpreter is based on {base_prefix}, not on a CPython "
            f"uv downloaded into {managed_root}; the installers give a user with no Python "
            "in range the newest one uv can download"
        )


def _wheel_parts(path: Path) -> tuple[str, str]:
    """``aelix_coding_agent-0.1.0b3-py3-none-any.whl`` -> ``('aelix-coding-agent', '0.1.0b3')``."""

    match = re.fullmatch(r"([^-]+)-([^-]+)-py3-none-any\.whl", path.name)
    if match is None:
        raise SmokeFailure(f"{path.name}: not a pure-Python wheel name")
    return match[1].replace("_", "-").lower(), match[2]


def candidate_wheels(dist: Path, version: str) -> dict[str, Path]:
    """The four published wheels in ``dist``, keyed by distribution name, all at ``version``."""

    found: dict[str, Path] = {}
    problems: list[str] = []
    for wheel in sorted(dist.glob("*.whl")):
        name, have = _wheel_parts(wheel)
        if name not in PUBLISHED:
            problems.append(f"{wheel.name}: not in the publish set {PUBLISHED}")
            continue
        if have != version:
            problems.append(f"{wheel.name}: version {have}, the tag says {version}")
        found[name] = wheel
    missing = sorted(set(PUBLISHED) - set(found))
    if missing:
        problems.append(f"no wheel for {missing} in {dist}")
    if problems:
        raise SmokeFailure("; ".join(problems))
    return found


#: install.sh Step 5's anchor for the version it pins: the meta-package wheel is
#: the only ``aelix-`` entry (its siblings escape the hyphen: ``aelix_ai-...``).
_INSTALLER_VERSION_ENTRY = re.compile(r"aelix-([^-]+)-py3-none-any\.whl")
#: The line form ``sha256sum`` writes, which install.sh Step 4 reads: it lists the wheels
#: with awk, whose field 2 must END in ``.whl`` (a ``*`` binary-mode marker or a CR hides
#: every wheel), then finds each one's line with ``grep "  <name>$"`` (two spaces) and
#: checks it with ``sha256sum -c``. Lower-case hex, as ``sha256sum`` writes it: stricter
#: than install.sh, which also takes upper-case hex (and a duplicate line, refused below).
#: install.ps1's regex takes more still (any whitespace, ``*``).
_MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  ([^\s*]\S*)")


def check_manifest(dist: Path, version: str) -> int:
    """``SHA256SUMS`` in ``dist`` is a manifest both installers will accept for ``version``.

    Every line is in the form ``sha256sum`` writes (``_MANIFEST_LINE``, LF endings, each
    name once - it refuses every form install.sh Step 4 refuses, and more),
    every listed file is in ``dist`` with that sha256 (install.sh / install.ps1 Step 4
    abort on a mismatch), every wheel and sdist in ``dist`` is listed, and the version
    install.sh Step 5 reads out of it - its first ``aelix-<version>-py3-none-any.whl``
    entry - is ``version``. Returns how many files it verified.
    """

    manifest = dist / "SHA256SUMS"
    if not manifest.is_file():
        raise SmokeFailure(f"no SHA256SUMS in {dist}; the installers refuse a Release without one")
    # Bytes, not read_text(): universal newlines would turn CRLF into LF before the check.
    try:
        text = manifest.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SmokeFailure(f"SHA256SUMS is not UTF-8: {exc}") from exc
    if "\r" in text:
        raise SmokeFailure(
            "SHA256SUMS has CR line endings; install.sh's awk and grep read the CR as part of "
            "every name and find no wheel in it"
        )
    listed: dict[str, str] = {}
    pinned: str | None = None
    for line in text.split("\n"):
        if not line:
            continue
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise SmokeFailure(
                f"SHA256SUMS: the line {line!r} is not '<sha256>  <name>' (lower-case hex, two "
                "spaces, no '*' binary-mode marker), the form sha256sum writes; the smoke is "
                "stricter than install.sh Step 4, which refuses a '*' marker or one space"
            )
        name = match[2]
        if name in listed:
            raise SmokeFailure(f"SHA256SUMS lists {name} twice")
        listed[name] = match[1]
        entry = _INSTALLER_VERSION_ENTRY.fullmatch(name)
        if pinned is None and entry is not None:
            pinned = entry[1]
    problems: list[str] = []
    for name, want in listed.items():
        path = dist / name
        if not path.is_file():
            problems.append(f"{name} is listed but not in the artifact")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != want:
            problems.append(f"{name}: its sha256 is not the one SHA256SUMS lists")
    for path in sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")]):
        if path.name not in listed:
            problems.append(f"{path.name} is in the artifact but not in SHA256SUMS")
    if pinned != version:
        problems.append(f"install.sh would pin aelix=={pinned} from it; the tag says {version}")
    if problems:
        raise SmokeFailure("SHA256SUMS: " + "; ".join(problems))
    return len(listed)


def _record(wheel: Path) -> list[tuple[str, str]]:
    """``(path, 'sha256=...')`` for every hashed RECORD row of ``wheel``."""

    with zipfile.ZipFile(wheel) as zf:
        names = [n for n in zf.namelist() if re.fullmatch(r"[^/]+\.dist-info/RECORD", n)]
        if len(names) != 1:
            raise SmokeFailure(f"{wheel.name}: expected one .dist-info/RECORD, found {names}")
        text = zf.read(names[0]).decode("utf-8")
    rows = [(row[0], row[1]) for row in csv.reader(io.StringIO(text)) if len(row) >= 2 and row[1]]
    if not rows:
        raise SmokeFailure(f"{wheel.name}: RECORD lists no hashed file")
    return rows


def _digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return base64.urlsafe_b64encode(h.digest()).rstrip(b"=").decode("ascii")


def check_installed_bytes(wheel: Path, site_packages: Path) -> int:
    """Every hashed RECORD file of ``wheel`` is in ``site_packages`` with that hash.

    ``.dist-info`` files included: measured, PyPI's 0.1.0b2 and a 0.1.0b2 built from main
    differ ONLY in ``METADATA`` and ``WHEEL``, so a check of the packages alone would pass
    the index's build for the candidate. Returns how many files were compared.
    """

    rows = _record(wheel)
    mismatched: list[str] = []
    for rel, spec in rows:
        algorithm, _, want = spec.partition("=")
        target = site_packages / rel
        if not target.is_file():
            mismatched.append(f"{rel} (not installed)")
        elif _digest(target, algorithm) != want:
            mismatched.append(f"{rel} (different bytes)")
    if mismatched:
        shown = ", ".join(mismatched[:5])
        more = f" and {len(mismatched) - 5} more" if len(mismatched) > 5 else ""
        raise SmokeFailure(
            f"{wheel.name}: the environment does not hold this wheel's files - {shown}{more}. "
            "uv installed another build under this name and version (an index file, not "
            "the candidate), or did not finish installing it."
        )
    return len(rows)


def top_level_packages(wheel: Path) -> list[str]:
    """Top-level import packages ``wheel`` ships (a directory with an ``__init__.py``)."""

    return sorted(
        {rel.split("/")[0] for rel, _ in _record(wheel) if re.fullmatch(r"[^/]+/__init__\.py", rel)}
    )


def run_installed(dist: Path, version: str, request: str, managed_root: Path) -> list[str]:
    report: list[str] = []
    expected = python_request_for(version)
    if request != expected:
        raise SmokeFailure(
            f"the candidate was installed under the interpreter request {request!r}; for "
            f"{version} the installers ask for {expected!r}"
        )
    verified = check_manifest(dist, version)
    report.append(f"SHA256SUMS: {verified} files verified; install.sh would pin aelix=={version}")
    check_interpreter(request, tuple(sys.version_info[:3]))
    check_managed_interpreter(managed_root, sys.base_prefix)
    report.append(
        f"interpreter: Python {sys.version.split()[0]} ({sys.executable}), inside {request!r}, "
        f"uv-managed from {sys.base_prefix}"
    )

    site_packages = Path(sysconfig.get_paths()["purelib"])
    packages: list[str] = []
    for name, wheel in candidate_wheels(dist, version).items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise SmokeFailure(f"{name} is not installed in {site_packages}") from None
        if installed != version:
            raise SmokeFailure(f"{name} {installed} is installed; the candidate is {version}")
        count = check_installed_bytes(wheel, site_packages)
        report.append(f"{name} {installed}: {count} files byte-identical to {wheel.name}")
        packages.extend(top_level_packages(wheel))

    for module in [*sorted(set(packages)), TUI_MODULE]:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - any import failure is the finding
            raise SmokeFailure(f"import {module} failed: {type(exc).__name__}: {exc}") from exc
        report.append(f"import {module}: ok")
    return report


# --- turn ----------------------------------------------------------------------------------


class StubServer:
    """An OpenAI chat-completions endpoint on 127.0.0.1 that streams one fixed reply.

    Every request of a standard method (POST and ``_RECORDED_METHODS``) is recorded, so
    "exactly one request" in :func:`run_turn` counts a GET or a DELETE too, not only POSTs.
    Only a POST to ``.../chat/completions`` is answered; anything else gets a 404.
    """

    _RECORDED_METHODS = ("GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE")

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return None

            def _record_request(self) -> dict[str, Any]:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "model": body.get("model"),
                        "authorization": self.headers.get("authorization"),
                    }
                )
                return body

            def _not_found(self) -> None:
                self._record_request()
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                body = self._record_request()
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                chunks = [
                    {"delta": {"role": "assistant", "content": SMOKE_REPLY}, "finish_reason": None},
                    {"delta": {}, "finish_reason": "stop"},
                ]
                payload = b""
                for i, choice in enumerate(chunks):
                    event: dict[str, Any] = {
                        "id": "chatcmpl-candidate-smoke",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": body.get("model"),
                        "choices": [{"index": 0, **choice}],
                    }
                    if i == len(chunks) - 1:
                        event["usage"] = {
                            "prompt_tokens": 1,
                            "completion_tokens": 4,
                            "total_tokens": 5,
                        }
                    payload += b"data: " + json.dumps(event).encode() + b"\n\n"
                payload += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        for method in self._RECORDED_METHODS:
            setattr(_Handler, f"do_{method}", _Handler._not_found)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> StubServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def scratch_env(scratch: Path, parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """A hermetic environment for the candidate: of the runner's, only what boots a process.

    An allowlist, so no API key, token, proxy, ``XDG_*`` or aelix / pi variable of the
    runner's reaches the candidate; HOME and every directory aelix writes are in ``scratch``.
    ``parent`` is ``os.environ`` unless a test hands in another; each boot key is read with
    ``.get`` (see ``_BOOT_KEYS``), which is case-insensitive in Windows' ``os.environ``.
    """

    parent = os.environ if parent is None else parent
    env = {key: value for key in _BOOT_KEYS if (value := parent.get(key))}
    home = scratch / "home"
    drive, tail = os.path.splitdrive(str(home))
    agent = scratch / "agent"
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "HOMEDRIVE": drive,
            "HOMEPATH": tail,
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "AELIX_CODING_AGENT_DIR": str(agent),
            "AELIX_SETTINGS_PATH": str(agent / "settings.json"),
            "AELIX_CODING_AGENT_SESSION_DIR": str(scratch / "sessions"),
            "AELIX_OFFLINE": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "PYTHONIOENCODING": "utf-8",
            SMOKE_KEY_ENV: SMOKE_KEY,
        }
    )
    for directory in (home, agent, scratch / "sessions", scratch / "cwd"):
        directory.mkdir(parents=True, exist_ok=True)
    return env


def write_models_json(agent_dir: Path, base_url: str) -> None:
    models = {
        "providers": {
            SMOKE_PROVIDER: {
                "baseUrl": base_url,
                "apiKey": SMOKE_KEY_ENV,
                "api": "openai-completions",
                "models": [
                    {
                        "id": SMOKE_MODEL,
                        "name": "Release candidate smoke",
                        "contextWindow": 8192,
                        "maxTokens": 256,
                    }
                ],
            }
        }
    }
    (agent_dir / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")


def _run(
    command: Sequence[str], *, env: dict[str, str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(f"{' '.join(command)} did not finish in {timeout:.0f} s") from exc
    except OSError as exc:
        raise SmokeFailure(f"{' '.join(command)} could not start: {exc}") from exc


def run_turn(launcher: Sequence[str], version: str, scratch: Path, timeout: float) -> list[str]:
    report: list[str] = []
    env = scratch_env(scratch)
    cwd = scratch / "cwd"

    got = _run([*launcher, "--version"], env=env, cwd=cwd, timeout=timeout)
    printed = got.stdout.strip()
    if got.returncode != 0 or printed != version:
        raise SmokeFailure(
            f"'aelix --version' exited {got.returncode} and printed {printed!r}; the tag says "
            f"{version!r}. stderr: {got.stderr.strip()[-2000:]}"
        )
    report.append(f"aelix --version: {printed}")

    with StubServer() as stub:
        write_models_json(Path(env["AELIX_CODING_AGENT_DIR"]), stub.base_url)
        command = [
            *launcher,
            "--offline",
            "--provider",
            SMOKE_PROVIDER,
            "--model",
            SMOKE_MODEL,
            "-p",
            "Reply with the smoke sentence.",
        ]
        result = _run(command, env=env, cwd=cwd, timeout=timeout)
    if result.returncode != 0 or SMOKE_REPLY not in result.stdout:
        raise SmokeFailure(
            f"'aelix -p' against the stub exited {result.returncode}; it must exit 0 and print "
            "the stub's reply. "
            f"stdout: {result.stdout.strip()[-2000:]!r} stderr: {result.stderr.strip()[-2000:]!r}"
        )
    seen = [(r["method"], r["path"], r["model"], r["authorization"]) for r in stub.requests]
    want = [("POST", "/v1/chat/completions", SMOKE_MODEL, f"Bearer {SMOKE_KEY}")]
    if seen != want:
        shown = [
            (method, path, model, "fake key" if auth == f"Bearer {SMOKE_KEY}" else "other or none")
            for method, path, model, auth in seen
        ]
        raise SmokeFailure(
            f"the stub saw {shown}; expected one request in all, a chat-completions POST for "
            f"{SMOKE_MODEL} with the fake key"
        )
    report.append(
        f"aelix -p: exit 0, printed the stub's reply; the stub saw {len(seen)} request of any "
        f"method (POST chat-completions, {SMOKE_MODEL}, fake key)"
    )
    # Reported, not required: the turn takes the session-write path, but no check reads it.
    sessions = sorted(p.name for p in (scratch / "sessions").rglob("*.jsonl"))
    report.append(f"session files written under the scratch session dir: {len(sessions)}")
    return report


def find_launcher(bin_dir: Path) -> list[str]:
    found = shutil.which("aelix", path=str(bin_dir))
    if found is None:
        raise SmokeFailure(f"no 'aelix' launcher in {bin_dir} (uv tool bin)")
    # Absolute: the turn runs in a scratch cwd, where a relative --bin-dir means nothing.
    return [os.path.abspath(found)]


# --- main ----------------------------------------------------------------------------------


def _keep_output_lines_whole() -> None:
    """Make stdout and stderr print what their encoding lacks as escapes, never raise.

    See the module docstring's last paragraph (#535). Only the error handler changes; a
    stream that is not a text wrapper (a test's replacement, ``None`` under pythonw) is
    left alone.
    """

    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    _keep_output_lines_whole()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="the interpreter request the installers make")
    request.add_argument("--version", required=True)
    installed = sub.add_parser("installed", help="the environment holds the candidate's bytes")
    installed.add_argument("--dist", type=Path, required=True)
    installed.add_argument("--version", required=True)
    installed.add_argument("--python-request", required=True)
    installed.add_argument("--managed-python-dir", type=Path, required=True)
    turn = sub.add_parser("turn", help="the console script runs a turn against a local stub")
    turn.add_argument("--bin-dir", type=Path, required=True)
    turn.add_argument("--version", required=True)
    turn.add_argument("--scratch", type=Path, default=None)
    turn.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    if args.command == "request":
        print(python_request_for(args.version), flush=True)
        return 0
    try:
        if args.command == "installed":
            report = run_installed(
                args.dist, args.version, args.python_request, args.managed_python_dir
            )
        else:
            scratch = args.scratch or Path(tempfile.mkdtemp(prefix="aelix-candidate-smoke-"))
            report = run_turn(find_launcher(args.bin_dir), args.version, scratch, args.timeout)
    except SmokeFailure as exc:
        print(f"::error::release candidate smoke ({args.command}): {exc}", flush=True)
        return 1
    for line in report:
        print(f"candidate smoke ({args.command}): {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
