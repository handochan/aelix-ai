"""#769 — a background command the job service no longer keeps whole reads as before, from its records and its file.

What a reader of an ended ``bash`` job sees comes from its snapshot's
``client_start`` (the output file's path, from the ``start`` fact), its exit
record (``bytes``, ``output_unconfirmed``) and the output file itself — all
persisted — so once the service keeps it only as its records read, every
surface reads it as before. The REAL ``bash`` tool, kind and service, real
shells (``tests/tools/test_bash_background_611.py``'s rig).

Fails on ``363accf2``: ``fast_service`` gets ``settled_kept``, which
``JobService`` does not take (a ``TypeError``), and nothing is ever let go.
"""

from __future__ import annotations

import gc
import sys
import tempfile
import weakref
from pathlib import Path

import pytest

from tests.jobs.conftest import jobs_active, wait_for  # noqa: F401 — the gate's test seam
from tests.jobs.test_retention_769 import _reads, _trimmed
from tests.tools.test_bash_background_611 import _make_rig

pytestmark = [
    pytest.mark.usefixtures("jobs_active"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell syntax (the rig's commands)"),
]


@pytest.fixture(autouse=True)
def _own_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Output files go under this test's own temp dir, never the developer's."""

    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


async def test_a_command_let_go_reads_as_before_on_every_surface(tmp_path: Path) -> None:
    """``/jobs``, ``job`` (list, status, result by bytes), ``job_stop``, the footer and the
    snapshot: the same before and after, for a command whose output is longer than a page.

    Fails on ``363accf2``: ``settled_kept`` is not a parameter of the service.
    """

    rig = await _make_rig(tmp_path, settled_kept=64)
    try:
        long = await rig.start("for i in $(seq 1 3000); do echo \"줄 $i 가나다 🙂\"; done")
        short = await rig.start("echo short")
        for job_id in (long, short):
            assert (await rig.ended(job_id)).state == "done"
        await rig.harness.prompt_notices()
        before = {job_id: await _reads(rig.service, job_id) for job_id in (long, short)}
        snaps = {job_id: rig.service.snapshot(job_id) for job_id in (long, short)}
        rig.service._settled_kept = 0
        rig.service._trim_later()
        await _trimmed(rig.service)
        assert list(rig.service._settled) == [long, short] and rig.service._jobs == {}
        for job_id in (long, short):
            after = await _reads(rig.service, job_id)
            for surface, text in before[job_id].items():
                assert after[surface] == text, (job_id, surface)
            snap = rig.service.snapshot(job_id)
            assert snap.payload["bytes"] == snaps[job_id].payload["bytes"]  # type: ignore[union-attr]
            assert snap.client_start == snaps[job_id].client_start  # type: ignore[union-attr]
        assert "… more: job(action=\"result\"" in before[long]["job result"]
        assert "short" in before[short]["job result"]
        # The live snapshot's payload carries what the notice was rendered from
        # (the tail, the command); the one read back carries the exit record's.
        # No surface reads the difference: the assertions above.
        assert "tail" in snaps[long].payload and "tail" not in rig.service.snapshot(long).payload  # type: ignore[union-attr]
    finally:
        await rig.service.shutdown()


async def test_a_command_let_go_frees_its_runner(tmp_path: Path) -> None:
    """The job and its runner (its launch, its exit summary) are freed once it is let go."""

    rig = await _make_rig(tmp_path, settled_kept=0)
    try:
        job_id = await rig.start("sleep 0.2; echo bye")
        held = rig.service._jobs[job_id]
        refs = {"job": weakref.ref(held), "runner": weakref.ref(held.runner)}
        del held
        await rig.ended(job_id)
        await rig.harness.prompt_notices()
        await wait_for(lambda: job_id in rig.service._settled)

        def freed() -> bool:
            gc.collect()
            return all(ref() is None for ref in refs.values())

        await wait_for(freed, 5.0)
        assert rig.service.snapshot(job_id).state == "done"  # type: ignore[union-attr]
    finally:
        await rig.service.shutdown()

