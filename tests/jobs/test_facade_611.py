"""``ExtensionAPI.jobs`` and ``post_notice``: absent while off, bound to their extension and runtime when on (#722).

Verify S1: with the gate off both read as absent, exactly as on base
``a385c64a`` — ``hasattr(api, "jobs")`` is ``False``, ``getattr(api,
"post_notice", None)`` is ``None`` and a call raises a plain ``AttributeError``
with the interpreter's own wording (verify's ``ext_probe`` prints the same three
lines on both builds). On ``a80e81fa`` ``hasattr`` raised ``JobsInactive`` (a
``RuntimeError``), so an extension that probed for the feature failed to load.

Codex #18: a facade carries its extension's name and its runtime's lifetime.
On ``a80e81fa`` extension B replaced A's kind and rendered A's results
(``FORGED-B``), and a facade kept past a reload started a job for the new owner.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.extensions.api import ExtensionError, _ExtensionRuntime
from aelix_coding_agent.extensions.loader import load_extension_from_factory
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.registry import KindTaken
from aelix_coding_agent.jobs.service import JobStartRefused

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session


async def test_with_the_gate_off_jobs_and_post_notice_read_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", False)
    seen: dict[str, Any] = {}

    def factory(aelix: Any) -> None:
        seen["hasattr_jobs"] = hasattr(aelix, "jobs")
        seen["getattr_post_notice"] = getattr(aelix, "post_notice", None)
        seen["getmembers"] = [n for n, _ in inspect.getmembers(aelix) if n in ("jobs", "post_notice")]
        try:
            aelix.post_notice("x", "body")
        except AttributeError as exc:
            seen["error"] = (type(exc).__name__, str(exc), exc.name, exc.obj is aelix)

    await load_extension_from_factory(factory, name="probe")
    assert seen["hasattr_jobs"] is False
    assert seen["getattr_post_notice"] is None
    assert seen["getmembers"] == []
    # The interpreter's own error for a missing attribute, ``name`` and ``obj`` included
    # (what a ``__getattr__``-less class raises on 3.10+). The names do show in ``dir()``.
    assert seen["error"] == (
        "AttributeError",
        "'ExtensionAPI' object has no attribute 'post_notice'",
        "post_notice",
        True,
    )
    assert issubclass(activation.JobsInactive, AttributeError), "the service's own refusal reads as absent too"


async def test_another_extension_cannot_take_a_kind_and_a_stale_facade_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    service = fast_service()
    activation.install_service(service)
    try:
        await service.bind(make_harness(await make_session(tmp_path)))
        facades: dict[str, Any] = {}
        runtime_a = _ExtensionRuntime()

        def ext_a(aelix: Any) -> None:
            facades["a"] = aelix.jobs
            facades["a"].register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)

        def ext_b(aelix: Any) -> None:
            facades["b"] = aelix.jobs
            with pytest.raises(KindTaken):
                facades["b"].register_kind(_sleeper.SleeperAdapter(tag="FORGED-B "), _sleeper.Sleeper)

        await load_extension_from_factory(ext_a, name="ext-a", runtime=runtime_a)
        await load_extension_from_factory(ext_b, name="ext-b")
        assert service.registry.get(_sleeper.KIND).source == "ext-a"
        with pytest.raises(JobStartRefused, match="not one this extension registered"):
            await facades["b"].start(_sleeper.KIND, title="borrowed")

        job_id = await facades["a"].start(_sleeper.KIND, title="mine", params={"seconds": 30})
        assert [s.job_id for s in facades["a"].list()] == [job_id]
        assert facades["b"].list() == [] and facades["b"].status(job_id) is None
        assert await facades["b"].stop(job_id) is None

        runtime_a.invalidate()  # a reload or a session change
        with pytest.raises(ExtensionError):
            await facades["a"].start(_sleeper.KIND, title="after the reload")
        assert service.registry.get(_sleeper.KIND) is None, "no new job on a dead registration"
        assert service.snapshot(job_id).state == "running", "the running job keeps its own"
        await service.stop(job_id)
    finally:
        await service.shutdown()
        activation.install_service(None)
