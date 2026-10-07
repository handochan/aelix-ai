"""#393 — the uv that CI and the release build run, pinned to a version that works.

THE FAILURE THIS PREVENTS. CI pinned uv 0.11.14. Every uv before 0.11.27 panics
on a ``name @ git+file:///C:/...`` requirement: its resolver appends ``@<sha>``,
and the ``:`` of the drive letter followed by that ``@`` trips the URL parser's
AmbiguousAuthority check — exit 2, "The channel closed unexpectedly"
(astral-sh/uv#19887, fixed by astral-sh/uv#20086 in 0.11.27). The windows leg
therefore could not install a git+file source on the uv backend at all, and
issue 131's real-backend test skipped that install on win32 whatever uv ran.
That test now skips only for a uv older than 0.11.27, so a pin moved back below
it would quietly turn the windows measurement off again; this file fails first.

THE CHECKSUM. ``astral-sh/setup-uv`` at the pinned v8.2.0 verifies the archive it
downloads only against its own bundled ``KNOWN_CHECKSUMS`` table, which ends at
0.11.18; for a newer version it logs "No checksum found" at debug level and
installs whatever it downloaded (from Astral's mirror by default). So every
setup-uv step that pins a version past that table must carry ``checksum:`` — one
sha256 per runner OS the job runs on. Whether each hash is RIGHT is checked by
the action itself on CI (a mismatch fails the step); this file checks that there
is one to check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/release.yml")

#: The first uv release carrying astral-sh/uv#20086.
GIT_FILE_DRIVE_FIXED = (0, 11, 27)
#: The newest uv whose sha256 setup-uv v8.2.0 bundles (src/download/checksum/known-checksums.ts).
ACTION_KNOWS_CHECKSUMS_UP_TO = (0, 11, 18)

_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")


def _version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    assert match, f"uv pin {text!r} is not an exact X.Y.Z version"
    return (int(match[1]), int(match[2]), int(match[3]))


def _setup_uv_steps() -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """``(workflow, job id, job, step)`` for every ``astral-sh/setup-uv`` step."""

    found = []
    for rel in WORKFLOWS:
        doc = yaml.safe_load((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for job_id, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                    found.append((rel, job_id, job, step))
    return found


def _runner_oses(job: dict[str, Any]) -> set[str]:
    runs_on = job["runs-on"]
    if "matrix.os" in str(runs_on):
        return set(job["strategy"]["matrix"]["os"])
    return {str(runs_on)}


def test_every_setup_uv_step_is_found() -> None:
    """The two this file guards: ci.yml's test job and release.yml's build job.
    A rename that hides one from the walk must not pass the rows below vacuously."""

    where = sorted((rel, job_id) for rel, job_id, _job, _step in _setup_uv_steps())
    assert where == [
        (".github/workflows/ci.yml", "test"),
        (".github/workflows/release.yml", "build"),
    ]


@pytest.mark.parametrize("rel", WORKFLOWS)
def test_ci_runs_a_uv_that_installs_a_git_file_drive_path(rel: str) -> None:
    for wf, job_id, _job, step in _setup_uv_steps():
        if wf != rel:
            continue
        pinned = _version(str(step["with"]["version"]))
        assert pinned >= GIT_FILE_DRIVE_FIXED, (
            f"{rel} job {job_id!r} pins uv {pinned}: uv before 0.11.27 panics on "
            "'name @ git+file:///C:/...' (astral-sh/uv#19887)"
        )


def test_ci_and_the_release_build_pin_the_same_uv() -> None:
    pins = {
        (rel, job_id): str(step["with"]["version"]) for rel, job_id, _job, step in _setup_uv_steps()
    }
    assert len(set(pins.values())) == 1, pins


@pytest.mark.parametrize("rel", WORKFLOWS)
def test_a_uv_past_the_actions_checksum_table_carries_its_own(rel: str) -> None:
    for wf, job_id, job, step in _setup_uv_steps():
        if wf != rel:
            continue
        if _version(str(step["with"]["version"])) <= ACTION_KNOWS_CHECKSUMS_UP_TO:
            continue
        checksum = str(step["with"].get("checksum", ""))
        hashes = set(_SHA256.findall(checksum))
        oses = _runner_oses(job)
        assert len(hashes) == len(oses), (
            f"{rel} job {job_id!r}: setup-uv installs uv unverified past 0.11.18 "
            f"unless `checksum:` gives one sha256 per runner OS {sorted(oses)}; "
            f"found {len(hashes)}"
        )
        if len(oses) > 1:
            # One expression choosing by OS: each OS must be named in it.
            assert "runner.os" in checksum, checksum
