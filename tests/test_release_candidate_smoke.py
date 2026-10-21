"""#382 — nothing is published before the candidate itself was installed and run.

THE GAP THIS CLOSES. ``release.yml`` built the dist and then ran ``publish`` (PyPI,
behind the ``pypi`` approval) and ``github-release`` side by side, each needing only
``build``. No job installed what they shipped. ci.yml's installer e2e jobs install the
NEWEST PUBLISHED Release, so they meet a candidate only after users can; RELEASING.md
checked the installer after ``publish``, when a defect costs the version number on
PyPI for good (ADR-0240). And install.sh / install.ps1 with no ``AELIX_VERSION`` take
the newest Release tag, betas included, so ``github-release`` alone made an uninstalled
build every user's default.

The owner's decision (2026-10-20, #382) is pi's order (build-binaries.yml at
fdce48eb4): build -> smoke on ubuntu, macos and windows -> the GitHub Release (needs
build + smoke) -> publish (needs the Release, then the environment approval). ADR-0269.

This file holds:

* the job graph — the smoke runs on all three OSes, no job that can publish
  (``contents: write`` or ``id-token: write``) runs before it, and nothing turns a red
  leg green (``continue-on-error``, a status function in a job ``if:``, a step ``if:``);
* the dry run (#535) — a manual run (``workflow_dispatch``, one input: the tag about to be
  pushed) starts ``build`` and the smoke and nothing else, each job that can publish
  keeps it out by its own ``if:``, and ``build`` checks that input as it checks a pushed
  tag;
* the smoke's commands — it installs THIS run's ``dist`` artifact with install.sh's own
  Step 5 command (find-links, the ``==`` pin, the ``[tui]`` default, the interpreter
  request the installers make for that version, computed by their rule), on the CPython
  uv downloads for a user with no Python in range; the candidate never comes from PyPI or
  a published Release; and it runs ``scripts/release_candidate_smoke.py`` under the tool
  environment's interpreter;
* that script's checks — the rows under "the smoke script's checks fire" show each check
  they name FIRE on the input it exists for; its exit status is read from a separate
  ``python -I`` process as the job reads it, and the ``turn`` check runs end to end
  against this tree's own console script, so a change to the flags or the models.json
  shape it relies on goes red on the branch, not on release day. ci.yml's windows legs
  run those rows too: they are the first Windows run of the turn's environment handling.
  Its failure line survives the cp1252 stdout a Windows leg gives it under ``-I`` (#535).

  Not every sub-check has a row of its own yet (review round 2, cross M5-M7; a
  follow-up): a base interpreter in a directory that only shares a string prefix with
  the managed one (``candidate-python-runner`` beside ``candidate-python``), an
  interpreter inside ``PY_SUPPORTED`` but outside a pre-0.1.0b3 request, and a wheel
  missing from SHA256SUMS (the unlisted row uses an sdist). A mutant that weakens one of
  those three passes this file.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
SCRIPT = REPO_ROOT / "scripts" / "release_candidate_smoke.py"

#: Every OS the installers serve: install.sh on Linux and macOS, install.ps1 on Windows.
SMOKE_OSES = {"ubuntu-latest", "macos-latest", "windows-latest"}

#: The job ids this file is about.
SMOKE = "candidate-smoke"

_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")

#: The only smoke steps allowed an ``if:``: uv's bootstrap is a different command per OS.
_BOOTSTRAP_IFS = {
    "Install uv (POSIX - the bootstrap install.sh Step 2 runs)": "runner.os != 'Windows'",
    "Install uv (Windows - the bootstrap install.ps1 Step 2 runs)": "runner.os == 'Windows'",
}

#: GitHub's status-check functions. A job ``if:`` that calls none of them is evaluated as
#: ``success() && (...)``, so the job runs only when every job it needs succeeded; one that
#: calls any of them replaces that default (``always()`` and ``!cancelled()`` run after a
#: failed or skipped need).
_STATUS_FUNCTION = re.compile(r"\b(always|cancelled|failure|success)\s*\(", re.IGNORECASE)


def _load_script() -> ModuleType:
    """``scripts/`` is not a package, so the smoke script is loaded by path."""

    spec = importlib.util.spec_from_file_location("release_candidate_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _ancestors(jobs: dict[str, Any], job_id: str) -> set[str]:
    seen: set[str] = set()
    stack = list(_needs(jobs[job_id]))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(_needs(jobs[current]))
    return seen


def _step(name_prefix: str) -> dict[str, Any]:
    found = [s for s in _jobs()[SMOKE]["steps"] if str(s.get("name", "")).startswith(name_prefix)]
    assert len(found) == 1, (name_prefix, [s.get("name") for s in _jobs()[SMOKE]["steps"]])
    return found[0]


def _installer_text(name: str) -> str:
    """An installer's text with LF endings (a Windows checkout may carry CRLF)."""

    return "\n".join((REPO_ROOT / name).read_text(encoding="utf-8").splitlines())


# === the job graph ============================================================


def test_the_smoke_runs_on_every_os_the_installers_serve() -> None:
    job = _jobs()[SMOKE]
    assert job["runs-on"] == "${{ matrix.os }}"
    assert set(job["strategy"]["matrix"]["os"]) == SMOKE_OSES
    assert set(job["strategy"]["matrix"]) == {"os"}, "no include/exclude: every OS is a leg"
    assert job["strategy"].get("fail-fast") is False, "a red leg must not cancel the others"
    assert "${{ matrix.os }}" in job["name"], "each leg is named by its OS"
    assert _needs(job) == {"build"}


def test_the_release_and_the_upload_both_wait_for_the_smoke() -> None:
    """The owner's order: the Release needs build + smoke; publish needs the Release.

    ``publish`` lists the smoke itself as well, so removing its edge to
    ``github-release`` cannot quietly ungate the irreversible upload.
    """

    jobs = _jobs()
    assert _needs(jobs["github-release"]) == {"build", SMOKE}
    assert _needs(jobs["publish"]) == {"build", SMOKE, "github-release"}
    assert jobs["publish"]["environment"]["name"] == "pypi", "the approval gate stays"


def test_no_job_that_can_publish_runs_before_the_smoke() -> None:
    """Any job holding a write permission (a Release, an OIDC upload token) is
    downstream of the smoke — including one added after this file was written."""

    jobs = _jobs()
    publishers = [
        job_id
        for job_id, job in jobs.items()
        if any(v == "write" for v in (job.get("permissions") or {}).values())
    ]
    assert {"publish", "github-release"} <= set(publishers), publishers
    for job_id in publishers:
        assert SMOKE in _ancestors(jobs, job_id), f"{job_id} can run before {SMOKE}"


def test_nothing_in_the_workflow_turns_a_red_job_or_step_green() -> None:
    """``needs`` is only half of the gate. ``continue-on-error`` on the smoke job - or
    only on its windows leg, the usual "experimental leg" idiom - makes a failed leg
    count as passed, and github-release and publish run; on a step it does the same for
    that one check. Review round 1 measured each of those passing every row this file had.
    Upstream jobs are held too: a build step that may fail green would hand the smoke a
    candidate the build had refused."""

    for job_id, job in _jobs().items():
        assert "continue-on-error" not in job, job_id
        for step in job.get("steps", []):
            assert "continue-on-error" not in step, (job_id, step.get("name", step.get("uses")))


def test_no_job_runs_after_a_failed_or_skipped_need() -> None:
    """``if: always()`` on github-release, or ``if: !cancelled()`` / ``always() &&
    needs.build.result == 'success'`` on publish, runs that job after the smoke failed
    or was skipped, whatever ``needs`` says. A job ``if:`` that calls no status function
    keeps GitHub's implicit ``success()``: build's ``!github.event.deleted`` is one."""

    for job_id, job in _jobs().items():
        condition = job.get("if")
        if condition is not None:
            assert not _STATUS_FUNCTION.search(str(condition)), (job_id, condition)


def test_every_smoke_step_runs_on_every_os() -> None:
    """A step ``if:`` such as ``runner.os != 'Windows'`` on the turn would leave the
    windows leg green without running it. Only uv's bootstrap differs by OS."""

    steps = _jobs()[SMOKE]["steps"]
    assert {s.get("name"): s["if"] for s in steps if "if" in s} == _BOOTSTRAP_IFS


def test_the_smoke_is_bounded_and_holds_no_write_permission() -> None:
    """Bounded like every ci.yml job (#429; tests/test_ci_hang_guard_429.py reads
    ci.yml only). It runs code from PyPI and from the candidate, so it can write
    nothing and leaves no token in the checkout."""

    job = _jobs()[SMOKE]
    assert isinstance(job["timeout-minutes"], int) and 0 < job["timeout-minutes"] <= 30
    assert job["permissions"] == {"contents": "read"}
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_the_smoke_pins_its_actions_to_a_commit() -> None:
    for step in _jobs()[SMOKE]["steps"]:
        if "uses" in step:
            assert _PINNED_ACTION.match(step["uses"]), step["uses"]


# === the dry run: build + smoke by hand, never a Release or an upload (#535) ===
#
# Before #535 only a tag push started release.yml, so candidate-smoke's macOS and Windows
# legs would first run on the v0.1.0-beta.3 tag itself. A manual run (workflow_dispatch)
# with the tag about to be pushed now runs build and candidate-smoke on the commit it is
# started on; github-release and publish run for a push only.

#: The condition a job that can publish carries: a tag push, never a manual run.
_PUSH_ONLY = "${{ github.event_name == 'push' }}"

#: Every job ``if:`` in release.yml, with what it means for an event. A condition not in
#: this table fails the graph row below: its meaning for a manual run must be written
#: here, by hand, before the graph can be read.
_CONDITIONS: dict[str, Any] = {
    "${{ !github.event.deleted }}": lambda event: not event["deleted"],
    _PUSH_ONLY: lambda event: event["event_name"] == "push",
}

#: (event, the jobs GitHub starts for it, each needed job assumed to pass).
_EVENTS = [
    pytest.param(
        {"event_name": "push", "deleted": False},
        {"build", SMOKE, "github-release", "publish"},
        id="the release tag is pushed",
    ),
    pytest.param({"event_name": "push", "deleted": True}, set(), id="the release tag is deleted"),
    pytest.param(
        {"event_name": "workflow_dispatch", "deleted": None},
        {"build", SMOKE},
        id="a manual run (the dry run)",
    ),
]


def _triggers() -> dict[str, Any]:
    """The ``on:`` block. YAML 1.1 reads the bare key ``on`` as ``True``."""

    doc = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    return doc["on"] if "on" in doc else doc[True]


def _can_publish(job: dict[str, Any]) -> bool:
    """A write permission (a Release, an OIDC upload token) or a deployment environment."""

    return "environment" in job or any(
        v == "write" for v in (job.get("permissions") or {}).values()
    )


def test_the_workflow_starts_on_a_release_tag_or_by_hand_and_nothing_else() -> None:
    """A tag push, or a manual run whose one input is the tag about to be pushed. Another
    input (a ``publish`` switch) or another trigger (a branch push, a schedule) would be
    a path past the push-only condition this file reads below."""

    triggers = _triggers()
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+*"]}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"tag"}
    assert inputs["tag"]["required"] is True and inputs["tag"]["type"] == "string"
    assert set(triggers["workflow_dispatch"]) == {"inputs"}


@pytest.mark.parametrize(("event", "expected"), _EVENTS)
def test_each_event_starts_exactly_these_jobs(event: dict[str, Any], expected: set[str]) -> None:
    """GitHub's selection, read from the file: a job starts when every job it needs
    started (and passed) and its ``if:`` holds. A manual run gets build and the smoke on
    every OS, and nothing after them."""

    jobs = _jobs()
    started: dict[str, bool] = {}

    def starts(job_id: str) -> bool:
        if job_id not in started:
            job = jobs[job_id]
            condition = job.get("if")
            assert condition is None or condition in _CONDITIONS, (
                f"{job_id}: teach _CONDITIONS what {condition!r} means for a manual run"
            )
            holds = condition is None or _CONDITIONS[condition](event)
            started[job_id] = holds and all(starts(n) for n in _needs(job))
        return started[job_id]

    assert {job_id for job_id in jobs if starts(job_id)} == expected


def test_a_manual_run_reaches_no_job_that_can_publish() -> None:
    """Each job that can publish keeps a manual run out by its own ``if:``, not only
    through a need: publish needs github-release, and removing github-release's
    condition alone, or publish's alone, must each be a failure here. Every job a manual
    run starts holds no write permission, no environment and no secret."""

    jobs = _jobs()
    publishers = {job_id for job_id, job in jobs.items() if _can_publish(job)}
    assert publishers == {"github-release", "publish"}, publishers
    for job_id in publishers:
        assert jobs[job_id].get("if") == _PUSH_ONLY, (job_id, jobs[job_id].get("if"))
    doc = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    assert doc["permissions"] == {"contents": "read"}
    for job_id in ("build", SMOKE):
        job = jobs[job_id]
        assert not _can_publish(job), job_id
        assert "secrets." not in yaml.safe_dump(job), job_id


def test_the_dry_run_checks_the_tag_it_names() -> None:
    """build reads the tag from one place: the ``tag`` input on a manual run, the pushed
    ref otherwise. The tag gate and the version/pin assert therefore refuse on a dry run
    exactly what they would refuse on the push. The input reaches the scripts through
    the environment only: expanded into a ``run:`` it would be shell text anyone who may
    start a run chooses."""

    build = _jobs()["build"]
    assert build["env"] == {"TAG": "${{ inputs.tag || github.ref_name }}"}
    for step in build["steps"]:
        assert "TAG" not in (step.get("env") or {}), step.get("name")
        assert "${{" not in str(step.get("run", "")), step.get("name")
    gate = next(
        s for s in build["steps"] if s.get("name") == "Validate release tag is strict semver"
    )
    assert gate["run"].splitlines()[0] == 'tag="$TAG"'
    pin = next(s for s in build["steps"] if s.get("id") == "pin")
    assert 'tag = os.environ["TAG"]' in pin["run"]
    ids = [s.get("id") or s.get("name") for s in build["steps"]]
    assert ids.index("Validate release tag is strict semver") == 0, "the gate runs first"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the step runs under bash on ubuntu-latest; on a Windows test leg `bash` may be "
        "WSL's launcher rather than a shell. Its text is the same on every OS, so the "
        "POSIX legs' run covers it"
    ),
)
@pytest.mark.parametrize(
    ("tag", "event", "status", "shown"),
    [
        ("v0.1.0-beta.3", "push", 0, "Release tag: v0.1.0-beta.3"),
        (
            "v0.1.0-beta.3",
            "workflow_dispatch",
            0,
            "Dry run (workflow_dispatch on main): build and candidate-smoke for",
        ),
        ("main", "workflow_dispatch", 1, "On a manual run (the dry run) the tag is the 'tag'"),
        ("v0.1.0.rc1", "workflow_dispatch", 1, "never a dot (v0.1.0.rc1)"),
    ],
)
def test_the_tag_gate_runs_the_same_on_a_manual_run(
    tag: str, event: str, status: int, shown: str
) -> None:
    """build's first step, executed by bash with the runner's variables: a manual run
    with a release tag passes and says it is a dry run; a manual run without one (the
    ``tag`` input left as the branch name) or with the dot form is refused."""

    gate = next(
        s
        for s in _jobs()["build"]["steps"]
        if s.get("name") == "Validate release tag is strict semver"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TAG": tag,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF_NAME": "main" if event == "workflow_dispatch" else tag,
        "GITHUB_SHA": "0" * 40,
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", gate["run"]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == status, (result.stdout, result.stderr)
    assert shown in result.stdout, result.stdout
    assert ("Dry run" in result.stdout) == (event != "push" and status == 0)


# === what the smoke installs, and how =========================================


def test_the_candidate_is_this_runs_artifact_never_a_release_or_an_index_file() -> None:
    """The candidate comes from the build's own artifact. Third-party dependencies still
    resolve from PyPI, by design (the installers' ``--find-links`` without
    ``--no-index``); what this holds is that no step fetches the first-party wheels from
    a Release or an index."""

    jobs = _jobs()
    upload = next(
        s
        for s in jobs["build"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    download = next(
        s
        for s in jobs[SMOKE]["steps"]
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    )
    assert download["with"]["name"] == upload["with"]["name"] == "dist"
    assert download["with"]["path"] == "${{ runner.temp }}/candidate-dist"
    scratch = _step("Scratch locations")["run"]
    assert 'echo "DIST=$RUNNER_TEMP/candidate-dist"' in scratch
    runs = "\n".join(str(s.get("run", "")) for s in jobs[SMOKE]["steps"])
    for elsewhere in (
        "releases/download",
        "gh release",
        "pip download",
        "pip install",
        "--no-index",
    ):
        assert elsewhere not in runs, elsewhere


def test_the_install_is_install_sh_step_5_with_the_candidate_in_place_of_the_release() -> None:
    """Byte for byte install.sh's command, with the artifact where install.sh puts the
    verified Release wheels, the tag's version where it puts the manifest's, and the
    default extra. A flag the installers gain or lose must reach the smoke too."""

    installer = _installer_text("install.sh")
    m = re.search(r'^(uv tool install [^\n]*"\$target")', installer, re.MULTILINE)
    assert m, 'install.sh Step 5 no longer runs `uv tool install ... "$target"`'
    assert 'target="aelix[$AELIX_EXTRAS]==$version"' in installer
    assert re.search(r'^AELIX_EXTRAS="\$\{AELIX_EXTRAS-tui\}"\s*$', installer, re.MULTILINE)
    want = (
        m[1]
        .replace('"$tmp"', '"$DIST"')
        .replace('"$AELIX_PYTHON"', '"$PYTHON_REQUEST"')
        .replace('"$target"', '"aelix[tui]==$VERSION"')
    )
    assert want == (
        'uv tool install --force --find-links "$DIST" --python "$PYTHON_REQUEST" '
        '"aelix[tui]==$VERSION"'
    )
    lines = [ln.strip() for ln in _step("Install the candidate")["run"].splitlines()]
    assert want in lines
    # An empty request is not a failure to uv: it ignores `--python ""` and takes the
    # newest interpreter (install.sh Step 0). The step refuses an empty build output.
    assert lines[0] == (
        ': "${VERSION:?build.outputs.version is empty}" '
        '"${PYTHON_REQUEST:?build.outputs.python_request is empty}"'
    )


def test_the_request_is_the_installers_rule_applied_to_the_tags_version() -> None:
    """Not a constant: the installers give '>=3.11,<3.15' to 0.1.0b3 and later and
    '>=3.11,<3.14' to anything older or not provably newer, so with a fixed range a
    0.1.0b2, a dev or a post release would be smoked under a range no installer asks for
    (review round 1, cross F3). build applies the script's rule - held to both installers'
    below - to the version its pin step proved, and the smoke installs under exactly that."""

    jobs = _jobs()
    build = jobs["build"]
    assert jobs[SMOKE]["env"]["PYTHON_REQUEST"] == "${{ needs.build.outputs.python_request }}"
    assert build["outputs"]["python_request"] == "${{ steps.request.outputs.python_request }}"
    ids = [s.get("id") for s in build["steps"]]
    assert ids.index("pin") < ids.index("request")
    step = build["steps"][ids.index("request")]
    assert step["env"] == {"VERSION": "${{ steps.pin.outputs.version }}"}
    assert [ln.strip() for ln in step["run"].strip().splitlines()] == [
        'request="$(uv run --no-project --python 3.12 python -I '
        'scripts/release_candidate_smoke.py request --version "$VERSION")"',
        'echo "Interpreter request for $VERSION: $request"',
        'echo "python_request=$request" >> "$GITHUB_OUTPUT"',
    ]


#: (version, the request both installers make for it): the rows review round 1 named
#: (0.1.0b2, 0.1.0b3, 0.1.0, 0.2.0.dev1, 0.1.0.post1, a GA) and the shapes a comparison
#: gets wrong - a two-digit pre-release, an alpha, an rc, a later patch's alpha, a local
#: version, other case.
_REQUEST_TABLE = [
    ("0.1.0b2", smoke.PY_BEFORE_B3),
    ("0.1.0b3", smoke.PY_SUPPORTED),
    ("0.1.0", smoke.PY_SUPPORTED),
    ("0.2.0.dev1", smoke.PY_BEFORE_B3),
    ("0.1.0.post1", smoke.PY_BEFORE_B3),
    ("1.0.0", smoke.PY_SUPPORTED),
    ("0.1.0b10", smoke.PY_SUPPORTED),
    ("0.1.0a9", smoke.PY_BEFORE_B3),
    ("0.1.0rc1", smoke.PY_SUPPORTED),
    ("0.1.1a1", smoke.PY_SUPPORTED),
    ("0.1.0b3+local", smoke.PY_BEFORE_B3),
    ("0.1.0B3", smoke.PY_BEFORE_B3),
]


def test_the_scripts_rule_answers_the_table() -> None:
    assert [(v, smoke.python_request_for(v)) for v, _ in _REQUEST_TABLE] == _REQUEST_TABLE


def test_the_scripts_ranges_are_both_installers() -> None:
    sh = _installer_text("install.sh").splitlines()
    assert f"py_supported='{smoke.PY_SUPPORTED}'" in sh
    assert f"py_before_b3='{smoke.PY_BEFORE_B3}'" in sh
    ps1 = _installer_text("install.ps1").splitlines()
    assert f"$PySupported = '{smoke.PY_SUPPORTED}'" in ps1
    assert f"$PyBeforeB3  = '{smoke.PY_BEFORE_B3}'" in ps1


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh is the POSIX installer and runs under sh on Linux and macOS; Windows "
        "users run install.ps1, whose rule the next test reads. The script's rule is plain "
        "Python, so the POSIX legs' comparison covers it on every OS"
    ),
)
def test_the_scripts_rule_is_install_shs() -> None:
    """install.sh's own ``python_request_for``, executed by sh over the table."""

    text = _installer_text("install.sh")
    ranges = [ln for ln in text.splitlines() if ln.startswith(("py_supported=", "py_before_b3="))]
    fn = re.findall(r"^python_request_for\(\) \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL)
    assert len(ranges) == 2 and len(fn) == 1, (ranges, fn)
    versions = [v for v, _ in _REQUEST_TABLE]
    loop = 'for v in "$@"; do printf \'%s\\n\' "$(python_request_for "$v")"; done'
    out = subprocess.run(
        ["sh", "-c", "\n".join([*ranges, fn[0], loop]), "sh", *versions],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ.get("PATH", "")},
        check=True,
    ).stdout.splitlines()
    assert list(zip(versions, out, strict=True)) == [
        (v, smoke.python_request_for(v)) for v in versions
    ]


def test_the_scripts_rule_is_install_ps1s() -> None:
    """install.ps1's ``Get-PythonRequest``, read: the script's pattern is its pattern
    verbatim, unparseable means the older range, and the pre-release order and the floor
    (0.1.0b3) are the same. tests/packaging_gate/test_install_ps1_parity.py executes it
    against install.sh's over a wider table wherever pwsh exists."""

    text = _installer_text("install.ps1")
    m = re.search(r"^function Get-PythonRequest \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL)
    assert m, "install.ps1 no longer defines Get-PythonRequest"
    body = [ln.strip() for ln in m[0].splitlines()]
    assert f"$m = [regex]::Match($Version, '{smoke._RELEASE_VERSION.pattern}')" in body
    assert "if (-not $m.Success) { return $PyBeforeB3 }" in body
    assert "$kind = 3" in body and "$num = 0" in body
    for pre, kind in smoke._PRE_RELEASE_KIND.items():
        assert f"if ($m.Groups[4].Value -ceq '{pre}') {{ $kind = {kind} }}" in body, pre
    floor = ", ".join(str(n) for n in smoke._FIRST_ON_PY_SUPPORTED)
    assert f"$want = @({floor})" in body
    assert "if ($got[$i] -gt $want[$i]) { return $PySupported }" in body
    assert "if ($got[$i] -lt $want[$i]) { return $PyBeforeB3 }" in body
    assert body[-2:] == ["return $PySupported", "}"]


def test_the_smoke_runs_on_the_interpreter_a_user_without_python_gets() -> None:
    """With uv's defaults the runner's own Python wins whenever one is in range
    (python-preference ``managed`` takes a Python on PATH before it downloads one), and
    no user without Python has it. ``only-managed`` into an empty install directory gives
    every leg the download such a user gets - the newest CPython inside the request - and
    ``installed`` checks the environment is based on it."""

    assert _step("Install the candidate")["env"] == {
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_PYTHON_DOWNLOADS": "automatic",
    }
    scratch = _step("Scratch locations")["run"]
    assert 'echo "UV_PYTHON_INSTALL_DIR=$RUNNER_TEMP/candidate-python"' in scratch
    assert '--managed-python-dir "$UV_PYTHON_INSTALL_DIR"' in _step("The environment holds")["run"]


def test_the_version_is_the_one_the_build_job_proved_equal_to_the_tag() -> None:
    jobs = _jobs()
    assert jobs[SMOKE]["env"]["VERSION"] == "${{ needs.build.outputs.version }}"
    assert jobs["build"]["outputs"]["version"] == "${{ steps.pin.outputs.version }}"
    pin = next(s for s in jobs["build"]["steps"] if s.get("id") == "pin")
    run = pin["run"]
    # Written only after the assert refused nothing: past its last exit.
    write = run.index('out.write(f"version={want}\\n")')
    assert 'os.environ["GITHUB_OUTPUT"]' in run
    assert run.index("if problems:") < run.rindex("sys.exit(1)") < write


def test_the_checks_run_under_the_tool_environments_interpreter() -> None:
    install = _step("Install the candidate")["run"]
    assert 'echo "CANDIDATE_PYTHON=$UV_TOOL_DIR/aelix/Scripts/python.exe"' in install
    assert 'echo "CANDIDATE_PYTHON=$UV_TOOL_DIR/aelix/bin/python"' in install
    script = '"$CANDIDATE_PYTHON" -I "$GITHUB_WORKSPACE/scripts/release_candidate_smoke.py"'
    installed = _step("The environment holds")["run"].strip()
    assert installed == (
        f'{script} installed --dist "$DIST" --version "$VERSION" '
        '--python-request "$PYTHON_REQUEST" --managed-python-dir "$UV_PYTHON_INSTALL_DIR"'
    )
    turn = _step("aelix --version, then one turn")["run"].strip()
    assert turn == (
        f'{script} turn --bin-dir "$UV_TOOL_BIN_DIR" --version "$VERSION" '
        '--scratch "$RUNNER_TEMP/candidate-turn"'
    )
    scratch = _step("Scratch locations")["run"]
    assert 'echo "UV_TOOL_DIR=$RUNNER_TEMP/candidate-tools"' in scratch
    assert 'echo "UV_TOOL_BIN_DIR=$RUNNER_TEMP/candidate-bin"' in scratch


def test_every_step_runs_in_a_shell_its_os_has() -> None:
    """bash by default (Git Bash on windows-latest); the one PowerShell step and the
    one curl step are each confined to the OS that has their bootstrap."""

    job = _jobs()[SMOKE]
    assert job["defaults"]["run"]["shell"] == "bash"
    for step in job["steps"]:
        if "run" not in step:
            continue
        shell = step.get("shell", "bash")
        if shell == "pwsh":
            assert step.get("if") == "runner.os == 'Windows'", step["name"]
        else:
            assert shell == "bash", step["name"]
    posix = _step("Install uv (POSIX")
    windows = _step("Install uv (Windows")
    assert posix["if"] == "runner.os != 'Windows'" and "astral.sh/uv/install.sh" in posix["run"]
    assert windows["shell"] == "pwsh" and "astral.sh/uv/install.ps1" in windows["run"]
    for name in (
        "Install the candidate",
        "The environment holds",
        "aelix --version, then one turn",
    ):
        assert _step(name)["working-directory"] == "${{ runner.temp }}", name


# === the smoke script's checks fire ===========================================


def _wheel(directory: Path, name: str, version: str, files: dict[str, bytes]) -> Path:
    """A pure wheel whose RECORD hashes ``files`` plus its METADATA and WHEEL."""

    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    payload = {
        **files,
        f"{dist_info}/METADATA": f"Name: {name}\nVersion: {version}\n".encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: hatchling\nRoot-Is-Purelib: true\n",
    }
    rows = []
    for rel, data in payload.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append(f"{rel},sha256={digest},{len(data)}")
    rows.append(f"{dist_info}/RECORD,,")
    path = directory / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in payload.items():
            zf.writestr(rel, data)
        zf.writestr(f"{dist_info}/RECORD", "\n".join(rows) + "\n")
    return path


def _candidate(dist: Path, version: str = "0.1.0b3") -> dict[str, Path]:
    """The build job's artifact: four wheels, an sdist, and ``sha256sum``'s manifest."""

    dist.mkdir(parents=True, exist_ok=True)
    wheels = {
        name: _wheel(dist, name, version, {f"{name.replace('-', '_')}/__init__.py": name.encode()})
        for name in smoke.PUBLISHED
    }
    (dist / f"aelix-{version}.tar.gz").write_bytes(b"sdist")
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
        for p in sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    ]
    (dist / "SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return wheels


def test_the_manifest_check_passes_the_build_jobs_own_shape(tmp_path: Path) -> None:
    _candidate(tmp_path)
    assert smoke.check_manifest(tmp_path, "0.1.0b3") == 5


_NOT_SHA256SUM_FORM = "the form sha256sum writes"


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("hash", "its sha256 is not the one SHA256SUMS lists"),
        ("unlisted", "is in the artifact but not in SHA256SUMS"),
        ("missing", "is listed but not in the artifact"),
        ("version", "install.sh would pin aelix==0.1.0b3 from it; the tag says 0.1.0b4"),
        ("absent", "no SHA256SUMS"),
        # The forms install.sh refuses (review round 1, cross F4): awk's field 2 must end
        # in .whl and grep wants "  <name>$", so each of these dies in install.sh Step 4
        # (measured with sha256sum and shasum in review round 2).
        ("binary-mode", _NOT_SHA256SUM_FORM),
        ("binary-mode, two spaces", _NOT_SHA256SUM_FORM),
        ("single-space", _NOT_SHA256SUM_FORM),
        ("crlf", "SHA256SUMS has CR line endings"),
        # Two forms install.sh ACCEPTS (measured, review round 2): the smoke is stricter
        # and refuses them too.
        ("upper-case hex", _NOT_SHA256SUM_FORM),
        ("listed twice", "SHA256SUMS lists aelix-0.1.0b3-py3-none-any.whl twice"),
    ],
)
def test_the_manifest_check_fires(tmp_path: Path, damage: str, message: str) -> None:
    _candidate(tmp_path)
    manifest = tmp_path / "SHA256SUMS"
    text = manifest.read_bytes().decode("utf-8")
    expect = "0.1.0b3"
    if damage == "hash":
        (tmp_path / "aelix-0.1.0b3.tar.gz").write_bytes(b"another sdist")
    elif damage == "unlisted":
        (tmp_path / "aelix_extra-0.1.0b3.tar.gz").write_bytes(b"x")
    elif damage == "missing":
        (tmp_path / "aelix-0.1.0b3.tar.gz").unlink()
    elif damage == "version":
        expect = "0.1.0b4"
    elif damage == "absent":
        manifest.unlink()
    elif damage == "binary-mode":  # what sha256sum -b / shasum -b write: '<hash> *<name>'
        manifest.write_bytes(text.replace("  ", " *").encode("utf-8"))
    elif damage == "binary-mode, two spaces":  # a synthetic '<hash>  *<name>'
        manifest.write_bytes(text.replace("  ", "  *").encode("utf-8"))
    elif damage == "single-space":
        manifest.write_bytes(text.replace("  ", " ").encode("utf-8"))
    elif damage == "crlf":  # written on Windows
        manifest.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    elif damage == "upper-case hex":
        first, _, rest = text.partition("\n")
        digest, _, name = first.partition("  ")
        manifest.write_bytes(f"{digest.upper()}  {name}\n{rest}".encode())
    else:
        manifest.write_bytes((text + text.splitlines()[0] + "\n").encode("utf-8"))
    with pytest.raises(smoke.SmokeFailure, match=re.escape(message)):
        smoke.check_manifest(tmp_path, expect)


def test_the_wheel_set_check_fires(tmp_path: Path) -> None:
    _candidate(tmp_path)
    assert set(smoke.candidate_wheels(tmp_path, "0.1.0b3")) == set(smoke.PUBLISHED)
    with pytest.raises(smoke.SmokeFailure, match="the tag says 0.1.0b4"):
        smoke.candidate_wheels(tmp_path, "0.1.0b4")
    _wheel(tmp_path, "aelix-server", "0.1.0b3", {"aelix_server/__init__.py": b""})
    with pytest.raises(smoke.SmokeFailure, match="not in the publish set"):
        smoke.candidate_wheels(tmp_path, "0.1.0b3")
    (tmp_path / "aelix_server-0.1.0b3-py3-none-any.whl").unlink()
    (tmp_path / "aelix_ai-0.1.0b3-py3-none-any.whl").unlink()
    with pytest.raises(smoke.SmokeFailure, match=re.escape("no wheel for ['aelix-ai']")):
        smoke.candidate_wheels(tmp_path, "0.1.0b3")


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        ("none", ""),
        ("package file, different bytes", "aelix_ai/__init__.py (different bytes)"),
        ("package file, not installed", "aelix_ai/__init__.py (not installed)"),
        # The measured case (review round 1, cross M4): PyPI's 0.1.0b2 and a 0.1.0b2
        # built from main differ ONLY here, so skipping .dist-info passes the index's build.
        ("METADATA only", "aelix_ai-0.1.0b3.dist-info/METADATA (different bytes)"),
        ("WHEEL only", "aelix_ai-0.1.0b3.dist-info/WHEEL (different bytes)"),
    ],
)
def test_the_byte_check_sees_another_build_of_the_same_version(
    tmp_path: Path, damage: str, expected: str
) -> None:
    """What uv installed is compared with the candidate wheel, file by file: a
    same-version file from an index would satisfy ``==<version>`` and still be
    another build (measured: PyPI's 0.1.0b2 against a 0.1.0b2 built from main
    differs in METADATA and WHEEL)."""

    wheel = _wheel(tmp_path, "aelix-ai", "0.1.0b3", {"aelix_ai/__init__.py": b"candidate"})
    site = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)
    dist_info = site / "aelix_ai-0.1.0b3.dist-info"
    if damage == "package file, different bytes":
        (site / "aelix_ai" / "__init__.py").write_bytes(b"from the index")
    elif damage == "package file, not installed":
        (site / "aelix_ai" / "__init__.py").unlink()
    elif damage == "METADATA only":
        metadata = dist_info / "METADATA"
        metadata.write_bytes(metadata.read_bytes() + b"Summary: the index's build\n")
    elif damage == "WHEEL only":
        (dist_info / "WHEEL").write_bytes(b"Wheel-Version: 1.0\nGenerator: hatchling 1.27.0\n")
    if damage == "none":
        assert smoke.check_installed_bytes(wheel, site) == 3
    else:
        with pytest.raises(smoke.SmokeFailure, match=re.escape(expected)):
            smoke.check_installed_bytes(wheel, site)


def test_top_level_packages_come_from_the_wheel(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        "aelix-coding-agent",
        "0.1.0b3",
        {
            "aelix_coding_agent/__init__.py": b"",
            "aelix_coding_agent/tui/__init__.py": b"",
            "aelix_status/__init__.py": b"",
        },
    )
    assert smoke.top_level_packages(wheel) == ["aelix_coding_agent", "aelix_status"]


@pytest.mark.parametrize(
    ("version", "inside"),
    [((3, 11, 0), True), ((3, 14, 5), True), ((3, 10, 14), False), ((3, 15, 0), False)],
)
def test_the_interpreter_check_reads_the_installers_range(
    version: tuple[int, int, int], inside: bool
) -> None:
    if inside:
        smoke.check_interpreter(">=3.11,<3.15", version)
    else:
        with pytest.raises(smoke.SmokeFailure, match="outside the installers' request"):
            smoke.check_interpreter(">=3.11,<3.15", version)
    with pytest.raises(smoke.SmokeFailure, match="cannot read clause"):
        smoke.check_interpreter("~=3.11", version)


def test_the_managed_interpreter_check_fires(tmp_path: Path) -> None:
    """The environment's base is the CPython uv downloaded into the job's empty
    ``UV_PYTHON_INSTALL_DIR``, not a Python the runner image carries."""

    root = tmp_path / "candidate-python"
    downloaded = root / "cpython-3.14.8-macos-aarch64-none"
    downloaded.mkdir(parents=True)
    smoke.check_managed_interpreter(root, str(downloaded))
    runners_own = tmp_path / "hostedtoolcache" / "Python" / "3.12.10" / "x64"
    runners_own.mkdir(parents=True)
    with pytest.raises(smoke.SmokeFailure, match="not on a CPython uv downloaded into"):
        smoke.check_managed_interpreter(root, str(runners_own))


def test_installed_refuses_a_request_the_installers_would_not_make(tmp_path: Path) -> None:
    _candidate(tmp_path, "0.1.0b2")
    with pytest.raises(
        smoke.SmokeFailure, match=re.escape("for 0.1.0b2 the installers ask for '>=3.11,<3.14'")
    ):
        smoke.run_installed(tmp_path, "0.1.0b2", smoke.PY_SUPPORTED, Path(sys.base_prefix))


#: One request a fake ``aelix`` sends the stub: (method, path after the provider's
#: baseUrl, Authorization header). ``_KEY`` is the header the real CLI builds: Bearer and
#: the key models.json names, read from the turn's environment.
_KEY = "<the fake key, as models.json names it>"
_Request = tuple[str, str, str | None]
_ONE_TURN: list[_Request] = [("POST", "/chat/completions", _KEY)]


def _fake_launcher(
    tmp_path: Path,
    version_line: str,
    turn_output: str = "no turn here",
    *,
    requests: list[_Request] | None = None,
    version_exit: int = 0,
    turn_exit: int = 0,
) -> list[str]:
    """A stand-in ``aelix`` that answers ``--version`` (exit ``version_exit``) and, for
    anything else, records its argv and environment, sends the stub ``requests`` (none by
    default) at the models.json provider's baseUrl, prints ``turn_output`` and exits
    ``turn_exit``."""

    script = tmp_path / "fake_aelix.py"
    record = tmp_path / "fake_aelix_turn.json"
    script.write_text(
        "import http.client, json, os, sys, urllib.parse\n"
        f"if sys.argv[1:] == ['--version']:\n"
        f"    print({version_line!r})\n"
        f"    sys.exit({version_exit})\n"
        f"with open({str(record)!r}, 'w', encoding='utf-8') as fh:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': dict(os.environ)}, fh)\n"
        f"requests = {list(requests or [])!r}\n"
        "if requests:\n"
        "    models = os.path.join(os.environ['AELIX_CODING_AGENT_DIR'], 'models.json')\n"
        "    with open(models, encoding='utf-8') as fh:\n"
        f"        provider = json.load(fh)['providers'][{smoke.SMOKE_PROVIDER!r}]\n"
        "    url = urllib.parse.urlsplit(provider['baseUrl'])\n"
        "    for method, suffix, auth in requests:\n"
        "        headers = {'content-type': 'application/json'}\n"
        f"        if auth == {_KEY!r}:\n"
        "            auth = 'Bearer ' + os.environ[provider['apiKey']]\n"
        "        if auth is not None:\n"
        "            headers['authorization'] = auth\n"
        f"        body = json.dumps({{'model': {smoke.SMOKE_MODEL!r}, 'stream': True}})\n"
        "        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)\n"
        "        conn.request(method, url.path + suffix,\n"
        "                     body=body if method == 'POST' else None, headers=headers)\n"
        "        conn.getresponse().read()\n"
        "        conn.close()\n"
        f"print({turn_output!r})\n"
        f"sys.exit({turn_exit})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_the_turn_check_fires_on_a_wrong_version(tmp_path: Path) -> None:
    with pytest.raises(smoke.SmokeFailure, match=r"printed '0\.1\.0b2'; the tag says '0\.1\.0b3'"):
        smoke.run_turn(_fake_launcher(tmp_path, "0.1.0b2"), "0.1.0b3", tmp_path / "s", 60.0)


def test_the_turn_check_fires_when_the_stub_is_not_reached(tmp_path: Path) -> None:
    with pytest.raises(smoke.SmokeFailure, match="it must exit 0 and print the stub's reply"):
        smoke.run_turn(_fake_launcher(tmp_path, "0.1.0b3"), "0.1.0b3", tmp_path / "s", 60.0)


def test_one_right_request_and_the_reply_pass_the_turn(tmp_path: Path) -> None:
    """The control for the rows below: the same fake, sending exactly the one request the
    real CLI sends and exiting 0, passes - so each of them is red for its own defect."""

    launcher = _fake_launcher(tmp_path, "0.1.0b3", smoke.SMOKE_REPLY, requests=_ONE_TURN)
    report = smoke.run_turn(launcher, "0.1.0b3", tmp_path / "s", 60.0)
    assert report[0] == "aelix --version: 0.1.0b3"
    assert "the stub saw 1 request of any method" in report[1], report


#: The stub's view of one right request, as the failure message shows it.
_RIGHT = f"('POST', '/v1/chat/completions', '{smoke.SMOKE_MODEL}', 'fake key')"


@pytest.mark.parametrize(
    ("requests", "version_exit", "turn_exit", "message"),
    [
        # Exactly one request: the right one, once, is not the same as "one of them is
        # right" (review round 2, cross M1).
        pytest.param(
            _ONE_TURN * 2, 0, 0, f"the stub saw [{_RIGHT}, {_RIGHT}]", id="the turn posted twice"
        ),
        # Of ANY method: the stub records a GET too, not only POSTs (cross, cat 1).
        pytest.param(
            [("GET", "/models", _KEY), *_ONE_TURN],
            0,
            0,
            "the stub saw [('GET', '/v1/models', None, 'fake key'), ",
            id="a GET besides the POST",
        ),
        # The key is compared (cross M2): none, and another one.
        pytest.param(
            [("POST", "/chat/completions", None)],
            0,
            0,
            f"'{smoke.SMOKE_MODEL}', 'other or none')]",
            id="no Authorization header",
        ),
        pytest.param(
            [("POST", "/chat/completions", "Bearer not-the-fake-key")],
            0,
            0,
            f"'{smoke.SMOKE_MODEL}', 'other or none')]",
            id="another key",
        ),
        # Exit statuses are read (cross M3, M4): the right output with exit 1 is red.
        pytest.param(
            _ONE_TURN, 0, 1, "'aelix -p' against the stub exited 1;", id="aelix -p exits 1"
        ),
        pytest.param(
            _ONE_TURN,
            1,
            0,
            "'aelix --version' exited 1 and printed '0.1.0b3'",
            id="aelix --version exits 1",
        ),
    ],
)
def test_the_turn_check_fires_on_a_wrong_turn(
    tmp_path: Path,
    requests: list[_Request],
    version_exit: int,
    turn_exit: int,
    message: str,
) -> None:
    launcher = _fake_launcher(
        tmp_path,
        "0.1.0b3",
        smoke.SMOKE_REPLY,
        requests=requests,
        version_exit=version_exit,
        turn_exit=turn_exit,
    )
    with pytest.raises(smoke.SmokeFailure, match=re.escape(message)):
        smoke.run_turn(launcher, "0.1.0b3", tmp_path / "s", 60.0)


def test_the_reply_alone_is_not_a_turn(tmp_path: Path) -> None:
    """Printing the right words is not enough: the stub must have been asked, once,
    for the smoke model, with the fake key. And the turn is asked for offline,
    from the scratch HOME and agent directories."""

    launcher = _fake_launcher(tmp_path, "0.1.0b3", turn_output=smoke.SMOKE_REPLY)
    with pytest.raises(smoke.SmokeFailure, match=r"the stub saw \[\]; expected one"):
        smoke.run_turn(launcher, "0.1.0b3", tmp_path / "s", 60.0)
    seen = json.loads((tmp_path / "fake_aelix_turn.json").read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[:5] == [
        "--offline",
        "--provider",
        smoke.SMOKE_PROVIDER,
        "--model",
        smoke.SMOKE_MODEL,
    ]
    assert argv[5] == "-p"
    env = seen["env"]
    assert env["AELIX_OFFLINE"] == "1"
    scratch = tmp_path / "s"
    for key in ("HOME", "USERPROFILE", "AELIX_CODING_AGENT_DIR", "AELIX_CODING_AGENT_SESSION_DIR"):
        assert Path(env[key]).is_relative_to(scratch), key
    models = json.loads((scratch / "agent" / "models.json").read_text(encoding="utf-8"))
    provider = models["providers"][smoke.SMOKE_PROVIDER]
    assert provider["apiKey"] == smoke.SMOKE_KEY_ENV and env[smoke.SMOKE_KEY_ENV] == smoke.SMOKE_KEY
    assert provider["baseUrl"].startswith("http://127.0.0.1:")


@pytest.mark.parametrize(
    "check",
    [
        "check_manifest",
        "check_interpreter",
        "check_managed_interpreter",
        "candidate_wheels",
        "check_installed_bytes",
    ],
)
def test_installed_runs_every_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: str
) -> None:
    """``installed`` is wired to each check: one that fails fails the subcommand."""

    _candidate(tmp_path)
    monkeypatch.setattr(smoke.importlib.metadata, "version", lambda _name: "0.1.0b3")
    monkeypatch.setattr(smoke, "check_installed_bytes", lambda _wheel, _site: 1)
    imported: list[str] = []
    monkeypatch.setattr(smoke.importlib, "import_module", imported.append)

    def fail(*_args: object) -> None:
        raise smoke.SmokeFailure(f"{check} fired")

    managed = Path(sys.base_prefix)
    report = smoke.run_installed(tmp_path, "0.1.0b3", smoke.PY_SUPPORTED, managed)
    assert imported == [
        "aelix",
        "aelix_agent_core",
        "aelix_ai",
        "aelix_coding_agent",
        smoke.TUI_MODULE,
    ]
    assert len(report) == 2 + len(smoke.PUBLISHED) + len(imported)
    monkeypatch.setattr(smoke, check, fail)
    with pytest.raises(smoke.SmokeFailure, match=f"{check} fired"):
        smoke.run_installed(tmp_path, "0.1.0b3", smoke.PY_SUPPORTED, managed)


def test_installed_fails_on_an_import_or_a_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _candidate(tmp_path)
    managed = Path(sys.base_prefix)
    monkeypatch.setattr(smoke, "check_installed_bytes", lambda _wheel, _site: 1)
    monkeypatch.setattr(smoke.importlib.metadata, "version", lambda _name: "0.1.0b2")
    with pytest.raises(smoke.SmokeFailure, match="is installed; the candidate is 0.1.0b3"):
        smoke.run_installed(tmp_path, "0.1.0b3", smoke.PY_SUPPORTED, managed)
    monkeypatch.setattr(smoke.importlib.metadata, "version", lambda _name: "0.1.0b3")

    def broken(module: str) -> None:
        if module == smoke.TUI_MODULE:
            raise ImportError("No module named 'prompt_toolkit'")

    monkeypatch.setattr(smoke.importlib, "import_module", broken)
    with pytest.raises(smoke.SmokeFailure, match="import aelix_coding_agent.tui failed"):
        smoke.run_installed(tmp_path, "0.1.0b3", smoke.PY_SUPPORTED, managed)


# === the script as the job runs it: a separate process and its exit status ===


def _run_script(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """``python -I scripts/release_candidate_smoke.py ...``, the way each step runs it.
    The step goes red only through this exit status."""

    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,  # above the turn's own two 180 s bounds
        check=False,
    )


def _this_trees_launcher_dir() -> Path:
    """The directory holding this checkout's own ``aelix`` console script, as uv's tool
    bin directory holds the candidate's."""

    scripts = Path(sysconfig.get_path("scripts"))
    assert shutil.which("aelix", path=str(scripts)), f"no aelix console script in {scripts}"
    return scripts


@pytest.mark.parametrize("case", ["installed, no manifest", "turn, another version"])
def test_a_failing_check_exits_1_after_one_error_line(tmp_path: Path, case: str) -> None:
    """Review round 1 found ``return 0`` after the ``::error::`` line passing every row:
    no row read the exit status. Here a check fails in a real process."""

    if case.startswith("installed"):
        (tmp_path / "dist").mkdir()
        args = [
            "installed",
            "--dist",
            str(tmp_path / "dist"),
            "--version",
            "0.1.0b3",
            "--python-request",
            smoke.PY_SUPPORTED,
            "--managed-python-dir",
            str(tmp_path / "python"),
        ]
        want = "::error::release candidate smoke (installed): no SHA256SUMS in "
    else:
        args = [
            "turn",
            "--bin-dir",
            str(_this_trees_launcher_dir()),
            "--version",
            "0.0.0",
            "--scratch",
            str(tmp_path / "scratch"),
        ]
        want = "::error::release candidate smoke (turn): 'aelix --version' exited 0 and printed "
    result = _run_script(*args, cwd=tmp_path)
    assert result.returncode == 1, (result.stdout, result.stderr)
    errors = [ln for ln in result.stdout.splitlines() if ln.startswith("::error::")]
    assert len(errors) == 1 and errors[0].startswith(want), result.stdout


#: A character cp1252 (a Windows runner's code page) has no byte for: U+2192.
_NOT_CP1252 = "→"


def _installed_without_a_manifest(dist: Path, tmp_path: Path) -> list[str]:
    """``installed`` arguments that fail its first file check: no SHA256SUMS in ``dist``,
    a message that quotes ``dist``."""

    dist.mkdir()
    return [
        "installed",
        "--dist",
        str(dist),
        "--version",
        "0.1.0b3",
        "--python-request",
        smoke.PY_SUPPORTED,
        "--managed-python-dir",
        str(tmp_path / "python"),
    ]


def test_a_failure_in_a_character_the_code_page_lacks_is_still_one_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#535: under ``-I`` a Windows leg's stdout is cp1252 with ``errors='strict'``
    (PYTHONIOENCODING is ignored), so printing a failure that quotes a character cp1252
    lacks raised ``UnicodeEncodeError`` and the leg showed a traceback, not the check.
    Here stdout and stderr are exactly that stream; the line prints, the character as
    an escape."""

    def cp1252() -> io.TextIOWrapper:
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    out, err = cp1252(), cp1252()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    args = _installed_without_a_manifest(tmp_path / f"dist{_NOT_CP1252}candidate", tmp_path)
    assert smoke.main(args) == 1
    for stream in (out, err):
        assert stream.errors == "backslashreplace"
        assert stream.encoding == "cp1252", "the encoding is left as it was"
    out.flush()
    buffer = out.buffer
    assert isinstance(buffer, io.BytesIO)
    lines = buffer.getvalue().decode("cp1252").splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("::error::release candidate smoke (installed): no SHA256SUMS in ")
    assert "dist\\u2192candidate" in lines[0], lines[0]


def test_a_passing_checks_report_in_a_character_the_code_page_lacks_prints_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2: the handler is set before any check runs, not only on the failure
    path, so the report lines a passing check prints (a path, aelix's own output) cannot
    raise ``UnicodeEncodeError`` on a cp1252 stdout either and turn a green leg red."""

    def cp1252() -> io.TextIOWrapper:
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    out, err = cp1252(), cp1252()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(
        smoke, "run_installed", lambda *_args: [f"read dist{_NOT_CP1252}candidate", "ok"]
    )
    args = _installed_without_a_manifest(tmp_path / "dist", tmp_path)
    assert smoke.main(args) == 0
    for stream in (out, err):
        assert stream.errors == "backslashreplace"
    out.flush()
    buffer = out.buffer
    assert isinstance(buffer, io.BytesIO)
    assert buffer.getvalue().decode("cp1252").splitlines() == [
        "candidate smoke (installed): read dist\\u2192candidate",
        "candidate smoke (installed): ok",
    ]


def test_the_job_shows_that_line_from_a_cp1252_stdout(tmp_path: Path) -> None:
    """The same failure in a separate ``python -I`` process, as each smoke step runs the
    script, with the stdout a Windows runner gives it built before the script starts:
    exit status 1, one ``::error::`` line, nothing on stderr."""

    args = _installed_without_a_manifest(tmp_path / f"dist{_NOT_CP1252}candidate", tmp_path)
    shim = (
        "import io, runpy, sys\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='cp1252', errors='strict', "
        "line_buffering=True)\n"
        "sys.argv = [sys.argv[1], *sys.argv[2:]]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", shim, str(SCRIPT), *args],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        check=False,
    )
    stdout = result.stdout.decode("cp1252")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert (result.returncode, stderr) == (1, ""), (stdout, stderr)
    errors = [ln for ln in stdout.splitlines() if ln.startswith("::error::")]
    assert len(errors) == 1 and "dist\\u2192candidate" in errors[0], stdout


def test_request_prints_the_installers_request(tmp_path: Path) -> None:
    """What build's ``request`` step reads into ``python_request``: the request alone."""

    for version, want in (("0.1.0b2", smoke.PY_BEFORE_B3), ("0.1.0b3", smoke.PY_SUPPORTED)):
        result = _run_script("request", "--version", version, cwd=tmp_path)
        assert (result.returncode, result.stdout, result.stderr) == (0, want + "\n", "")


def test_the_turn_runs_against_this_trees_cli(tmp_path: Path) -> None:
    """The release smoke's ``turn`` exactly as the job runs it - a separate ``python -I``
    process handed the directory of an ``aelix`` console script - against this
    checkout's own launcher. The release job runs it against the built wheels; running
    it here on every push, ci.yml's windows legs included, keeps the flags, the
    models.json shape and the child environment it depends on from drifting until
    release day. No network: the provider is the script's stub on 127.0.0.1, the key is
    fake, and HOME and every AELIX_* directory are under tmp_path."""

    version = importlib.metadata.version("aelix-coding-agent")
    result = _run_script(
        "turn",
        "--bin-dir",
        str(_this_trees_launcher_dir()),
        "--version",
        version,
        "--scratch",
        str(tmp_path / "scratch"),
        cwd=tmp_path,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "::error::" not in result.stdout
    lines = result.stdout.splitlines()
    assert lines[0] == f"candidate smoke (turn): aelix --version: {version}"
    assert "the stub saw 1 request of any method" in lines[1], lines
    assert not (tmp_path / "scratch" / "home" / ".aelix").exists(), "a write escaped to HOME"


# === the candidate's environment ==============================================


def test_the_scratch_environment_carries_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "HTTPS_PROXY",
        "XDG_DATA_HOME",
    }
    for name in planted:
        monkeypatch.setenv(name, "from-the-runner")
    env = smoke.scratch_env(tmp_path)
    assert not planted & set(env)
    assert env["PATH"] == os.environ["PATH"], "the boot keys come from os.environ"
    assert env[smoke.SMOKE_KEY_ENV] == smoke.SMOKE_KEY
    for key in ("HOME", "USERPROFILE", "AELIX_CODING_AGENT_DIR", "AELIX_CODING_AGENT_SESSION_DIR"):
        assert Path(env[key]).is_relative_to(tmp_path), key
    assert env["AELIX_OFFLINE"] == "1"


def _nt_environ(values: dict[str, str]) -> Any:
    """``os.environ`` as CPython builds it on Windows (os.py, the ``name == 'nt'``
    branch): keys stored and iterated UPPER-CASE, looked up case-insensitively. Built
    from ``os._Environ`` itself, so it behaves as the real one on every OS."""

    def check_str(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"str expected, not {type(value).__name__}")
        return value

    def encodekey(key: str) -> str:
        return check_str(key).upper()

    data = {encodekey(k): v for k, v in values.items()}
    return os._Environ(data, encodekey, str, check_str, str)


def test_the_scratch_environment_keeps_windows_boot_keys(tmp_path: Path) -> None:
    """Review round 1's blocker: on Windows ``os.environ`` iterates ``SYSTEMROOT``, not
    ``SystemRoot``, so matching its keys against the boot list dropped SystemRoot,
    SystemDrive and windir, and a child with no %SystemRoot% dies importing asyncio
    (WinError 10106, the #209 measurement in tests/env_sandbox.py) - ``aelix --version``
    imports asyncio. Every windows leg would have failed. Each key is looked up instead."""

    runner = {
        "Path": "C:\\hostedtoolcache;C:\\Windows\\system32",
        "SystemRoot": "C:\\Windows",
        "SystemDrive": "C:",
        "windir": "C:\\Windows",
        "ComSpec": "C:\\Windows\\system32\\cmd.exe",
        "PATHEXT": ".COM;.EXE;.BAT",
        "TEMP": "D:\\a\\_temp",
        "TMP": "D:\\a\\_temp",
        "OPENAI_API_KEY": "from-the-runner",
    }
    parent = _nt_environ(runner)
    assert "SYSTEMROOT" in list(parent) and "SystemRoot" not in list(parent), "not nt-shaped"
    env = smoke.scratch_env(tmp_path, parent)
    for key in ("SystemRoot", "SystemDrive", "windir", "PATHEXT", "TEMP", "TMP"):
        assert env.get(key) == runner[key], key
    assert env["PATH"] == runner["Path"]
    assert env["COMSPEC"] == runner["ComSpec"]
    assert not any(key.upper() == "OPENAI_API_KEY" for key in env)
