"""#434 — the hosted catalog's client: the store, the source, publication (ADR-0264 §4.3, §8).

Every test runs the real client against a loopback stand-in for GitHub Pages
(:mod:`tests.hosted_catalog_fixtures_434`) with an isolated agent dir, so the
store is a real ``models-store.json`` and every request is a real HTTP request.
The revisions are built from this build's shipped catalog the way the publisher
builds them. Nothing here reaches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai import models as catalog
from aelix_ai.models_store import (
    HOSTED_ENTRY,
    AbortSignal,
    InMemoryModelsStore,
    ModelsStoreError,
    RefreshModelsContext,
    entry_stamp,
)
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent import hosted_catalog_source as source
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.models_store import FileModelsStore

from tests.hosted_catalog_fixtures_434 import (  # noqa: F401 - the fixture
    INDEX_PATH,
    NEW_OR_ID,
    CatalogServer,
    Response,
    catalog_server,
    index_for,
    new_row,
    revision,
    revision_path,
)

_ADDED = {"openrouter": {NEW_OR_ID: new_row()}}


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    return agent


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


def _stored(agent: Path) -> dict[str, Any]:
    document = json.loads((agent / "models-store.json").read_text(encoding="utf-8"))
    return document["entries"][HOSTED_ENTRY]


async def _refresh(registry: ModelRegistry, *, force: bool = False) -> Any:
    result = await registry.refresh_models(allow_network=True, force=force, trigger="test")
    (outcome,) = result.outcomes
    return outcome


# ── the store ─────────────────────────────────────────────────────────


def test_a_write_is_atomic_private_and_locked_on_a_sidecar(tmp_path: Path) -> None:
    store = FileModelsStore(tmp_path / "agent" / "models-store.json")

    assert store.write("k", {"checkedAt": 1}, base=None) is None

    path = tmp_path / "agent" / "models-store.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert (tmp_path / "agent" / "models-store.json.lock").exists()
    assert not (tmp_path / "agent" / "models-store.json.tmp").exists()
    document = json.loads(path.read_text())
    assert document["version"] == 1 and len(document["salt"]) == 64
    assert store.read("k") == {"checkedAt": 1}


def test_a_persist_loses_to_a_newer_stored_entry(tmp_path: Path) -> None:
    """§4.3 rule 2: compare under the lock; a newer entry comes back, nothing is written."""

    store = FileModelsStore(tmp_path / "models-store.json")
    store.write("k", {"checkedAt": 1, "etag": "a"}, base=None)
    base = entry_stamp(store.read("k"))
    store.write("k", {"checkedAt": 2, "etag": "b"}, base=base)  # another process, meanwhile

    newer = store.write("k", {"checkedAt": 3, "etag": "c"}, base=base)

    assert newer == {"checkedAt": 2, "etag": "b"}
    assert store.read("k") == {"checkedAt": 2, "etag": "b"}
    store.delete("k")
    assert store.write("k", {"checkedAt": 4}, base=base) == {}  # deleted meanwhile: {} not None
    assert InMemoryModelsStore({"k": {"checkedAt": 1}}).write("k", {}, base=None) == {"checkedAt": 1}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has an in-process lock only")
def test_another_process_holding_the_lock_fails_the_write_within_its_bound(tmp_path: Path) -> None:
    path = tmp_path / "models-store.json"
    store = FileModelsStore(path)
    store.write("k", {"checkedAt": 1}, base=None)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys, time; f = open(sys.argv[1], 'a'); "
            "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(6)",
            str(path) + ".lock",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(ModelsStoreError, match="locked by another aelix"):
            store.write("k", {"checkedAt": 2}, base=entry_stamp({"checkedAt": 1}))
        waited = time.monotonic() - started
        assert 1.5 < waited < 4.0, waited
        assert store.read("k") == {"checkedAt": 1}
    finally:
        holder.kill()
        holder.wait()
    assert store.write("k", {"checkedAt": 2}, base=entry_stamp({"checkedAt": 1})) is None


def test_an_unknown_version_is_neither_read_nor_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "models-store.json"
    original = json.dumps({"version": 2, "entries": {"aelix-hosted": {"url": "x"}}})
    path.write_text(original, encoding="utf-8")
    store = FileModelsStore(path)

    assert store.read("aelix-hosted") is None
    with pytest.raises(ModelsStoreError, match="version"):
        store.write("aelix-hosted", {}, base=None)
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"entries": 3}'])
def test_a_corrupted_file_reads_as_an_empty_store(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "models-store.json"
    path.write_text(content, encoding="utf-8")
    store = FileModelsStore(path)

    assert store.read("aelix-hosted") is None
    assert "models-store.json" in caplog.text
    assert store.write("aelix-hosted", {"checkedAt": 1}, base=None) is None
    assert store.read("aelix-hosted") == {"checkedAt": 1}


# ── the base URL and the transport rules ─────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, source.DEFAULT_BASE_URL),
        ("", None),
        ("   ", None),
        ("https://example.test/cat/", "https://example.test/cat"),
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://[::1]:8765", "http://[::1]:8765"),
        ("http://localhost:8765", None),
        ("http://example.test", None),
        ("ftp://example.test", None),
        ("http://user:pw@127.0.0.1:1", None),
    ],
)
def test_the_base_url_rule(value: str | None, expected: str | None) -> None:
    env = {} if value is None else {source.ENV_CATALOG_URL: value}
    assert source.catalog_base_url(env) == expected


def test_a_redirect_leaves_neither_https_nor_the_loopback_origin() -> None:
    assert source._redirect_allowed("https://a.test/x", "https://b.test/y")
    assert not source._redirect_allowed("https://a.test/x", "http://a.test/y")
    assert source._redirect_allowed("http://127.0.0.1:1/x", "http://127.0.0.1:1/y")
    assert not source._redirect_allowed("http://127.0.0.1:1/x", "http://127.0.0.1:2/y")
    assert not source._redirect_allowed("http://127.0.0.1:1/x", "https://a.test/y")


def test_a_cwd_dotenv_never_sets_the_catalog_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``.env`` floor: it decides what the global store holds, for every directory."""

    from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

    monkeypatch.delenv(source.ENV_CATALOG_URL, raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", source.ENV_CATALOG_URL)
    path = tmp_path / ".env"
    path.write_text(f"{source.ENV_CATALOG_URL}=https://evil.test\n", encoding="utf-8")
    load_dotenv(str(path))

    assert source.ENV_CATALOG_URL not in os.environ


# ── publication ──────────────────────────────────────────────────────


async def test_a_published_new_id_is_installed_without_a_generation_bump(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    raw = revision(add=_ADDED)
    catalog_server.publish(raw)
    registry = await _registry(agent)
    generation = registry._generation

    outcome = await _refresh(registry)

    assert outcome.outcome == "published" and outcome.added == 1
    assert catalog.installed_overlay() == hc.revision_name(raw)
    found = registry.find("openrouter", NEW_OR_ID)
    assert found is not None and found.api == "openai-completions"
    assert found.base_url == catalog.get_model("openrouter", "openai/gpt-5.4").base_url
    assert registry._generation == generation
    assert catalog.get_shipped_model("openrouter", NEW_OR_ID) is None
    headers = catalog_server.requests(INDEX_PATH)[0]
    assert headers["User-Agent"].startswith("aelix/") and "Authorization" not in headers


async def test_the_next_start_installs_the_cached_revision_offline(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    await _refresh(await _registry(agent))
    catalog.install_overlay(None)
    monkeypatch.setenv("AELIX_OFFLINE", "1")
    requests = len(catalog_server.log)

    registry = await _registry(agent)
    outcome = await _refresh(registry)

    assert registry.find("openrouter", NEW_OR_ID) is not None
    assert outcome.outcome == "skipped" and len(catalog_server.log) == requests


async def test_a_request_authenticated_before_a_publication_still_sends(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Round 1's probe: the generation bump would refuse it ("select the model again")."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    model = registry.find("openrouter", "openai/gpt-5.4")
    assert model is not None
    auth = await registry.get_api_key_and_headers(model)
    assert auth.ok and auth.validate is not None

    await _refresh(registry)

    assert registry.find("openrouter", NEW_OR_ID) is not None
    auth.validate(model)  # raises "Provider registration changed …" on a generation bump
    registry.refresh()  # the bump a full reload makes: now it refuses
    with pytest.raises(Exception, match="select the model again"):
        auth.validate(model)


async def test_a_known_id_takes_main_facts_and_keeps_its_transport(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """An absent map removes the shipped one; a field that fails validation keeps it."""

    target = next(
        mid
        for mid, row in catalog.shipped_catalog()["openrouter"].items()
        if row.get("thinkingLevelMap")
    )
    shipped = catalog.get_shipped_model("openrouter", target)

    def edit(document: dict[str, Any]) -> None:
        for row in document["providers"]["openrouter"]:
            if row["id"] == target:
                row.pop("thinkingLevelMap")
                row["name"] = "Renamed"
                row["contextWindow"] = "not a number"
                row["api"] = "anthropic-messages"  # transport is never read
                row["baseUrl"] = "https://evil.test"

    catalog_server.publish(revision(edit=edit))
    registry = await _registry(agent)
    await _refresh(registry)

    hosted = registry.find("openrouter", target)
    assert hosted is not None and shipped is not None
    assert hosted.name == "Renamed"
    assert hosted.thinking_level_map is None
    assert hosted.context_window == shipped.context_window
    assert (hosted.api, hosted.base_url) == (shipped.api, shipped.base_url)


async def test_a_refused_revision_keeps_the_overlay_this_process_has(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    good = revision(add=_ADDED)
    catalog_server.publish(good)
    registry = await _registry(agent)
    await _refresh(registry)

    def strip_maps(document: dict[str, Any]) -> None:
        for rows in document["providers"].values():
            for row in rows:
                row.pop("thinkingLevelMap", None)

    bad = revision(edit=strip_maps)
    catalog_server.publish(bad, etag='"v2"')
    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "failed" and "refused" in (outcome.error or "")
    assert catalog.installed_overlay() == hc.revision_name(good)
    catalog.install_overlay(None)
    assert (await _registry(agent)).find("openrouter", NEW_OR_ID) is None  # next start: none


async def test_a_failed_write_publishes_nothing_in_memory(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ModelsStoreError("the models store could not be written (read-only)")

    monkeypatch.setattr(registry._models_store, "write", refuse)
    outcome = await _refresh(registry)

    assert outcome.outcome == "failed" and "read-only" in (outcome.error or "")
    assert catalog.installed_overlay() is None
    assert registry.find("openrouter", NEW_OR_ID) is None


async def test_a_slow_run_publishes_the_newer_entry_another_process_stored(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """§4.3 rule 2: never an older index over a newer one, a withdrawal included."""

    raw = revision(add=_ADDED)
    names = catalog_server.publish(raw)
    registry = await _registry(agent)
    store = FileModelsStore(agent / "models-store.json")
    withdrawn = {
        "url": source.index_url(catalog_server.base),
        "index": index_for(hold="withdraw"),
        "checkedAt": time.time(),
        "revisions": {},
        "removed": {},
    }
    real_route = catalog_server.routes[INDEX_PATH]

    def index_then_race(headers: Any) -> Response:
        store.write(HOSTED_ENTRY, withdrawn, base=None)  # another aelix, meanwhile
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = index_then_race
    outcome = await _refresh(registry)

    assert outcome.outcome == "withdrawn"
    assert catalog.installed_overlay() is None
    assert _stored(agent)["index"] == withdrawn["index"]
    assert list(names) and registry.find("openrouter", NEW_OR_ID) is None


# ── the check's timing ───────────────────────────────────────────────


async def test_an_unforced_check_is_skipped_within_four_hours(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    count = len(catalog_server.log)

    assert (await _refresh(registry)).outcome == "skipped"
    assert len(catalog_server.log) == count
    assert (await _refresh(registry, force=True)).outcome == "unchanged"
    assert catalog_server.requests(INDEX_PATH)[-1].get("If-None-Match") == '"v1"'


async def test_a_failure_with_no_index_waits_fifteen_minutes_and_a_future_stamp_counts_as_none(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.routes[INDEX_PATH] = Response(500)
    registry = await _registry(agent)

    first = await _refresh(registry)
    assert first.outcome == "failed" and "HTTP 500" in (first.error or "")
    entry = _stored(agent)
    assert "failedAt" in entry and "checkedAt" not in entry
    assert (await _refresh(registry)).outcome == "skipped"

    store = FileModelsStore(agent / "models-store.json")
    stale = dict(entry, failedAt=entry["failedAt"] - source.FAILURE_INTERVAL - 1)
    store.write(HOSTED_ENTRY, stale, base=entry_stamp(entry))
    assert (await _refresh(registry)).outcome == "failed"

    future = dict(_stored(agent), failedAt=time.time() + 3600)
    store.write(HOSTED_ENTRY, future, base=entry_stamp(_stored(agent)))
    assert (await _refresh(registry)).outcome == "failed"  # in the future: absent


async def test_a_failure_with_an_index_stored_also_moves_checked_at(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    catalog_server.routes[INDEX_PATH] = Response(503)

    outcome = await _refresh(registry, force=True)

    entry = _stored(agent)
    assert outcome.outcome == "failed"
    assert entry["checkedAt"] == entry["failedAt"]
    assert catalog.installed_overlay() is not None  # the stored index and revision stay


async def test_the_validator_is_sent_only_when_every_stored_revision_verifies(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    raw = revision(add=_ADDED)
    name = hc.revision_name(raw)
    catalog_server.publish(raw)
    registry = await _registry(agent)
    await _refresh(registry)
    entry = _stored(agent)
    entry["revisions"][name] = entry["revisions"][name].replace("Acme", "Evil")
    FileModelsStore(agent / "models-store.json").write(
        HOSTED_ENTRY, entry, base=entry_stamp(_stored(agent))
    )

    outcome = await _refresh(registry, force=True)

    assert "If-None-Match" not in catalog_server.requests(INDEX_PATH)[-1]
    assert outcome.outcome in ("published", "unchanged")
    assert len(catalog_server.requests(revision_path(name))) == 2  # fetched again
    assert hc.verify_revision_bytes(name, _stored(agent)["revisions"][name].encode())


# ── what an index can say ────────────────────────────────────────────


async def test_a_404_keeps_the_overlay_for_the_process_and_the_next_start_has_none(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    del catalog_server.routes[INDEX_PATH]

    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "unavailable"
    assert registry.find("openrouter", NEW_OR_ID) is not None
    assert _stored(agent)["index"]["catalogs"] == []
    catalog.install_overlay(None)
    assert (await _registry(agent)).find("openrouter", NEW_OR_ID) is None


async def test_a_withdrawn_index_removes_the_overlay_at_once(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    catalog_server.publish(revision(add=_ADDED), index=index_for(hold="withdraw"), etag='"w"')

    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "withdrawn"
    assert catalog.installed_overlay() is None
    assert registry.find("openrouter", NEW_OR_ID) is None


async def test_a_rollback_older_than_this_build_leaves_it_on_its_shipped_catalog(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    older = revision(lineage=["0" * 32])
    catalog_server.publish(older, index=index_for(hc.revision_name(older), hold="rollback"))
    registry = await _registry(agent)

    outcome = await _refresh(registry)

    assert outcome.outcome == "not-applicable"
    assert catalog.installed_overlay() is None
    assert hc.revision_name(older) in _stored(agent)["revisions"]  # stored for other builds


async def test_a_dev_checkout_whose_catalog_never_reached_main_installs_nothing(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    monkeypatch.setattr(catalog, "_shipped_digest", "d" * 32)

    outcome = await _refresh(await _registry(agent))

    assert outcome.outcome == "not-applicable" and catalog.installed_overlay() is None


async def test_the_entry_for_this_version_is_selected(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """pi's ``selectModelCatalog``: the highest minimum not above this version (0.1.0b2)."""

    from aelix_coding_agent.cli import config

    old, mine, future = (
        revision(add={"openrouter": {f"acme/{tag}": new_row(tag)}}) for tag in ("b1", "b2", "c1")
    )
    catalog_server.publish(
        old,
        mine,
        future,
        index=index_for(
            ("0.1.0b1", hc.revision_name(old)),
            ("0.1.0b2", hc.revision_name(mine)),
            ("0.2.0", hc.revision_name(future)),
        ),
    )
    monkeypatch.setattr(config, "VERSION", "0.1.0b2")
    registry = await _registry(agent)

    await _refresh(registry)

    assert catalog.installed_overlay() == hc.revision_name(mine)
    assert registry.find("openrouter", "acme/b2") is not None
    assert registry.find("openrouter", "acme/c1") is None


@pytest.mark.parametrize("failure", ["mismatch", "404"])
async def test_a_bad_selected_revision_fails_and_keeps_the_stored_index(
    agent: Path, catalog_server: CatalogServer, failure: str  # noqa: F811
) -> None:
    first = revision(add=_ADDED)
    catalog_server.publish(first)
    registry = await _registry(agent)
    await _refresh(registry)
    second = revision(add={"openrouter": {"acme/second-1": new_row("Second")}})
    name = hc.revision_name(second)
    catalog_server.publish(second, etag='"v2"')
    if failure == "mismatch":
        catalog_server.routes[revision_path(name)] = Response(200, second + b" ")
    else:
        del catalog_server.routes[revision_path(name)]

    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "failed" and name[:19] in (outcome.error or "")
    assert catalog.installed_overlay() == hc.revision_name(first)
    assert list(_stored(agent)["revisions"]) == [hc.revision_name(first)]


async def test_a_failure_on_another_entrys_revision_still_installs_this_builds(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    mine = revision(add=_ADDED)
    other = revision(add={"openrouter": {"acme/other-1": new_row("Other")}})
    catalog_server.publish(
        mine,
        index=index_for(("0.1.0b2", hc.revision_name(mine)), ("0.3.0", hc.revision_name(other))),
    )

    outcome = await _refresh(await _registry(agent))

    assert outcome.outcome == "published"
    assert catalog.installed_overlay() == hc.revision_name(mine)
    assert "not stored" in (outcome.error or "")
    assert list(_stored(agent)["revisions"]) == [hc.revision_name(mine)]


async def test_an_index_over_its_cap_is_a_failure_and_the_revision_cap_is_streamed(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    catalog_server.routes[INDEX_PATH] = Response(200, b" " * (hc.MAX_INDEX_BYTES + 1))
    registry = await _registry(agent)
    outcome = await _refresh(registry)
    assert outcome.outcome == "failed" and "more than" in (outcome.error or "")

    raw = revision(add=_ADDED)
    catalog_server.publish(raw)
    monkeypatch.setattr(hc, "MAX_REVISION_BYTES", 1024)
    outcome = await _refresh(registry, force=True)
    assert outcome.outcome == "failed" and "more than 1024 bytes" in (outcome.error or "")


async def test_a_redirect_is_followed_three_times_and_no_further(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    raw = revision(add=_ADDED)
    catalog_server.publish(raw)
    catalog_server.routes["/hop3"] = catalog_server.routes[INDEX_PATH]
    catalog_server.routes["/hop2"] = Response(302, headers={"Location": "/hop3"})
    catalog_server.routes["/hop1"] = Response(302, headers={"Location": "/hop2"})
    catalog_server.routes[INDEX_PATH] = Response(302, headers={"Location": "/hop1"})
    registry = await _registry(agent)

    assert (await _refresh(registry)).outcome == "published"

    catalog_server.routes["/hop3"] = Response(302, headers={"Location": "/hop4"})
    catalog_server.routes["/hop4"] = Response(200, b"{}")
    outcome = await _refresh(registry, force=True)
    assert outcome.outcome == "failed" and "redirects" in (outcome.error or "")


# ── removed: what a hold takes out (§4.4) ────────────────────────────


async def test_a_hold_records_the_ids_no_revision_names_and_a_later_index_releases_them(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    newer = revision(add=_ADDED)
    catalog_server.publish(newer)
    registry = await _registry(agent)
    await _refresh(registry)
    base = revision()

    # An index without hold that drops the id records nothing.
    catalog_server.publish(base, etag='"plain"')
    await _refresh(registry, force=True)
    assert _stored(agent)["removed"] == {}
    assert not registry.is_removed("openrouter", NEW_OR_ID)

    # Back to the newer one, then a rollback (hold) to the base revision.
    catalog_server.publish(newer, etag='"again"')
    await _refresh(registry, force=True)
    catalog_server.publish(base, index=index_for(hc.revision_name(base), hold="rollback"), etag='"rb"')
    await _refresh(registry, force=True)
    assert [item[0] for item in _stored(agent)["removed"]["openrouter"]] == [NEW_OR_ID]
    assert registry.is_removed("openrouter", NEW_OR_ID)
    assert not registry.is_removed("openrouter", "openai/gpt-5.4")

    # An index naming it again releases it.
    catalog_server.publish(newer, etag='"released"')
    await _refresh(registry, force=True)
    assert _stored(agent)["removed"] == {}
    assert not registry.is_removed("openrouter", NEW_OR_ID)


def test_removed_keeps_at_most_its_cap_oldest_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "MAX_REMOVED", 2)
    old = revision(add={"openrouter": {f"acme/x{i}": new_row(f"x{i}") for i in range(3)}})
    stored = {"removed": {"openrouter": [["acme/early", 1.0]]}}

    out, pending = source._next_removed(
        stored, {"a": old}, {"b": revision()}, hold=True, complete=True, now=5.0
    )

    assert sorted(item[0] for item in out["openrouter"]) == ["acme/x1", "acme/x2"]
    assert pending == {}


# ── a store several builds share ─────────────────────────────────────


async def test_a_release_and_a_dev_checkout_alternating_never_empty_each_other(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    raw = revision(add=_ADDED)
    catalog_server.publish(raw)
    release_digest = catalog.shipped_digest()
    await _refresh(await _registry(agent))

    monkeypatch.setattr(catalog, "_shipped_digest", "d" * 32)  # the dev checkout
    catalog.install_overlay(None)
    dev = await _registry(agent)
    assert (await _refresh(dev, force=True)).outcome == "not-applicable"
    assert hc.revision_name(raw) in _stored(agent)["revisions"]

    monkeypatch.setattr(catalog, "_shipped_digest", release_digest)  # the release again
    catalog.install_overlay(None)
    assert (await _registry(agent)).find("openrouter", NEW_OR_ID) is not None


async def test_two_versions_on_one_agent_dir_each_install_their_own_entry(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import config

    b2 = revision(add={"openrouter": {"acme/for-b2": new_row("b2")}})
    b3 = revision(add={"openrouter": {"acme/for-b3": new_row("b3")}})
    catalog_server.publish(
        b2, b3, index=index_for(("0.1.0b2", hc.revision_name(b2)), ("0.1.0b3", hc.revision_name(b3)))
    )
    for version, wanted in (("0.1.0b2", "acme/for-b2"), ("0.1.0b3", "acme/for-b3"), ("0.1.0b2", "acme/for-b2")):
        monkeypatch.setattr(config, "VERSION", version)
        catalog.install_overlay(None)
        registry = await _registry(agent)
        await _refresh(registry, force=True)
        assert registry.find("openrouter", wanted) is not None, version
        catalog.install_overlay(None)
        assert (await _registry(agent)).find("openrouter", wanted) is not None, version


# ── the effective catalog ────────────────────────────────────────────


def _install(raw: bytes) -> hc.ParsedRevision:
    parsed = hc.parse_revision(json.loads(raw), catalog.shipped_catalog())
    catalog.install_overlay(parsed.rows, revision=hc.revision_name(raw))
    return parsed


def test_guard_twos_namespaces_read_the_effective_rows() -> None:
    from aelix_coding_agent.cli.runtime_bootstrap import openrouter_namespaces

    before = set(openrouter_namespaces())
    assert "acme" not in before
    _install(revision(add=_ADDED))
    assert "acme" in set(openrouter_namespaces())


async def test_copilot_enablement_posts_shipped_ids_only_and_quotes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_ai.oauth import github_copilot as gc

    _install(
        revision(add={"github-copilot": {"claude-sonnet-9-9": new_row("New", family="claude-sonnet")}})
    )
    assert catalog.get_model("github-copilot", "claude-sonnet-9-9") is not None
    posted: list[str] = []

    async def record(_token: str, model_id: str, _enterprise: Any = None) -> bool:
        posted.append(model_id)
        return True

    monkeypatch.setattr(gc, "enable_github_copilot_model", record)
    await gc.enable_all_github_copilot_models("tok", None)

    assert "claude-sonnet-9-9" not in posted
    assert sorted(posted) == sorted(m.id for m in catalog.get_shipped_models("github-copilot"))


async def test_a_copilot_policy_path_quotes_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from aelix_ai.oauth import github_copilot as gc

    seen: list[str] = []

    class Client:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def post(self, url: str, **_k: Any) -> httpx.Response:
            seen.append(url)
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(gc.httpx, "AsyncClient", Client)
    await gc.enable_github_copilot_model("tid=1;proxy-ep=proxy.foo.example", "a/b c", None)

    assert seen and seen[0].endswith("/models/a%2Fb%20c/policy")


def test_the_reset_fixture_leaves_no_overlay_installed() -> None:
    """``tests/conftest.py``'s ``_no_hosted_catalog`` ran after the tests above."""

    assert catalog.installed_overlay() is None
    assert catalog.get_model("openrouter", NEW_OR_ID) is None


# ── the refresher: one shared run per registry ───────────────────────


async def test_concurrent_triggers_share_one_run_and_the_last_waiter_aborts_it(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import refresh_catalogs

    raw = revision(add=_ADDED)
    catalog_server.publish(raw)
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()
    real_route = catalog_server.routes[INDEX_PATH]

    def slow(headers: Any) -> Response:
        asyncio.run_coroutine_threadsafe(gate.wait(), loop).result(timeout=10)
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = slow
    registry = await _registry(agent)
    first = asyncio.ensure_future(refresh_catalogs(registry, trigger="a"))
    second = asyncio.ensure_future(refresh_catalogs(registry, trigger="b"))
    await asyncio.sleep(0.2)
    gate.set()
    results = await asyncio.gather(first, second)

    assert results[0] is results[1]
    assert len(catalog_server.requests(INDEX_PATH)) == 1

    # The last waiter leaving aborts the run: nothing is published.
    catalog.install_overlay(None)
    gate.clear()
    waiter = asyncio.ensure_future(refresh_catalogs(registry, trigger="c", force=True))
    await asyncio.sleep(0.2)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    await asyncio.sleep(0.3)
    assert catalog.installed_overlay() is None


async def test_a_superseded_run_publishes_nothing(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    signal = AbortSignal()
    published: list[bool] = []
    real_publish = registry._publish

    def publish_after_a_newer_run(*args: Any) -> bool:
        registry._refresh_runs[HOSTED_ENTRY] += 1  # a newer run of the source started
        published.append(real_publish(*args))
        return published[-1]

    registry._publish = publish_after_a_newer_run  # type: ignore[method-assign]
    result = await registry.refresh_models(allow_network=True, signal=signal, trigger="t")

    assert published == [False]
    assert result.outcomes[0].outcome == "aborted"
    assert catalog.installed_overlay() is None


async def test_offline_no_trigger_runs_and_refresh_models_honours_it(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import refresh_catalogs, start_background_refresh

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    monkeypatch.setenv("AELIX_OFFLINE", "1")

    assert await refresh_catalogs(registry, trigger="x") is None
    assert start_background_refresh(registry, trigger="startup") is None
    assert (await _refresh(registry)).outcome == "skipped"
    assert catalog_server.log == []


async def test_a_delegated_child_never_refreshes_at_start_up(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import start_background_refresh

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", "1")
    assert start_background_refresh(registry, trigger="rpc-startup") is None
    monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", "0")
    task = start_background_refresh(registry, trigger="rpc-startup")
    assert task is not None
    await task
    assert catalog.installed_overlay() is not None


# ── the event ────────────────────────────────────────────────────────


async def test_each_run_emits_model_catalog_refresh_on_the_live_harness(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    from aelix_agent_core.harness.hooks import HookBus, ModelCatalogRefreshHookEvent
    from aelix_coding_agent.model_refresh import bind_catalog_events

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    events: list[ModelCatalogRefreshHookEvent] = []
    bus = HookBus(ctx_factory=lambda: None)

    def handler(event: ModelCatalogRefreshHookEvent, _ctx: Any) -> None:
        events.append(event)

    def broken(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("a handler that fails")

    bus.on("model_catalog_refresh", handler, error_mode="continue")
    bus.on("model_catalog_refresh", broken, error_mode="continue")

    class Harness:
        hooks = bus

    unbind = bind_catalog_events(registry, lambda: Harness)
    await _refresh(registry)
    unbind()
    await _refresh(registry)

    assert len(events) == 1
    (event,) = events
    assert (event.source, event.trigger, event.outcome, event.added) == (
        "aelix-hosted",
        "test",
        "published",
        1,
    )
    assert event.revision and event.revision.startswith("sha256-")
    assert "127.0.0.1" not in repr(event)


@pytest.mark.parametrize("how", ["aborted", "superseded"])
async def test_a_run_stopped_while_it_persists_never_reaches_memory(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch, how: str  # noqa: F811
) -> None:
    """Rule 2's second check: after the persist, before memory (Codex review 1: untested)."""

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    signal = AbortSignal()
    store = registry._models_store
    real_write = store.write

    def write_then_stop(*args: Any, **kwargs: Any) -> Any:
        answer = real_write(*args, **kwargs)
        if how == "aborted":
            signal.abort()
        else:
            registry._refresh_runs[HOSTED_ENTRY] += 1  # a newer run started meanwhile
        return answer

    monkeypatch.setattr(store, "write", write_then_stop)
    result = await registry.refresh_models(allow_network=True, signal=signal, trigger="t")

    assert result.outcomes[0].outcome == "aborted"
    assert "revisions" in _stored(agent)  # persisted (pi's order) ...
    assert catalog.installed_overlay() is None  # ... and not published in memory
    assert registry.find("openrouter", NEW_OR_ID) is None


async def test_an_aborted_context_publishes_nothing(agent: Path, catalog_server: CatalogServer) -> None:  # noqa: F811
    catalog_server.publish(revision(add=_ADDED))
    signal = AbortSignal()
    calls: list[Any] = []
    context = RefreshModelsContext(
        source=HOSTED_ENTRY,
        trigger="t",
        stored=None,
        allow_network=True,
        force=False,
        signal=signal,
        base_url=catalog_server.base,
        _publish=lambda p: calls.append(p) or True,
    )
    signal.abort()

    outcome = await source.refresh_hosted(context, version="0.1.0b2")

    assert outcome.outcome == "aborted"
    assert calls == []


async def test_a_skipped_check_adopts_what_another_aelix_stored(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """The skip stands for the stored entry: a hold another process stored reaches this one."""

    newer = revision(add=_ADDED)
    catalog_server.publish(newer)
    open_tui = await _registry(agent)
    await _refresh(open_tui)
    base = revision()
    catalog_server.publish(base, index=index_for(hc.revision_name(base), hold="rollback"), etag='"rb"')
    other = await _registry(agent)  # another aelix on the same agent dir
    await _refresh(other, force=True)
    catalog.install_overlay(None)  # the open process is unaware of it so far
    catalog.install_overlay(
        hc.parse_revision(json.loads(newer), catalog.shipped_catalog()).rows,
        revision=hc.revision_name(newer),
    )
    requests = len(catalog_server.log)

    outcome = await _refresh(open_tui)

    assert outcome.outcome == "skipped" and len(catalog_server.log) == requests
    assert catalog.installed_overlay() == hc.revision_name(base)
    assert open_tui.is_removed("openrouter", NEW_OR_ID)


async def test_after_a_404_a_skipped_check_keeps_the_overlay(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    del catalog_server.routes[INDEX_PATH]
    assert (await _refresh(registry, force=True)).outcome == "unavailable"

    assert (await _refresh(registry)).outcome == "skipped"
    assert registry.find("openrouter", NEW_OR_ID) is not None


@pytest.mark.parametrize("answer", ["304", "404"])
async def test_a_failed_write_on_a_304_or_a_404_is_a_failure_not_an_exception(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch, answer: str  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    if answer == "404":
        del catalog_server.routes[INDEX_PATH]

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ModelsStoreError("the models store is locked by another aelix")

    monkeypatch.setattr(registry._models_store, "write", refuse)
    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "failed" and "locked" in (outcome.error or "")
    assert catalog.installed_overlay() is not None


def test_a_url_with_userinfo_is_refused_so_no_basic_auth_goes_out() -> None:
    """httpx turns ``user:pass@`` into an Authorization header whatever ``auth=`` says (Codex 1)."""

    assert source.catalog_base_url({source.ENV_CATALOG_URL: "https://u:p@catalog.test"}) is None
    assert source.catalog_base_url({source.ENV_CATALOG_URL: "https://u@catalog.test"}) is None
    assert not source._redirect_allowed("https://a.test/x", "https://u:p@b.test/y")
    assert not source._redirect_allowed("http://127.0.0.1:1/x", "http://u:p@127.0.0.1:1/y")


def test_both_locks_share_one_two_second_deadline(tmp_path: Path) -> None:
    """The in-process lock's wait counts against the file lock's (Codex review 1: 3.2 s)."""

    import threading

    from aelix_coding_agent import models_store as file_store

    path = tmp_path / "models-store.json"
    store = FileModelsStore(path)
    store.write("k", {"checkedAt": 1}, base=None)
    thread_lock = file_store._thread_lock(path)
    thread_lock.acquire()
    timer = threading.Timer(1.2, thread_lock.release)
    timer.start()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys, time; f = open(sys.argv[1], 'a'); "
            "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(6)",
            str(path) + ".lock",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(ModelsStoreError):
            store.write("k", {"checkedAt": 2}, base=entry_stamp({"checkedAt": 1}))
        assert time.monotonic() - started < 2.6
    finally:
        timer.cancel()
        holder.kill()
        holder.wait()


async def test_losing_the_persist_to_another_aelixs_404_keeps_this_process_overlay(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """A running process adopts a newer entry as running, not as a start-up (Codex 1)."""

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    store = FileModelsStore(agent / "models-store.json")
    real_route = catalog_server.routes[INDEX_PATH]

    def index_then_404_elsewhere(headers: Any) -> Response:
        current = _stored(agent)
        store.write(
            HOSTED_ENTRY,
            {**current, "index": index_for(), "revisions": {}, "unavailable": True, "checkedAt": time.time()},
            base=entry_stamp(current),
        )
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = index_then_404_elsewhere
    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "unavailable"
    assert registry.find("openrouter", NEW_OR_ID) is not None


async def test_a_second_registry_composes_an_overlay_the_first_installed(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """The overlay is process-global; each registry composes it when it next publishes (Codex 1)."""

    catalog_server.publish(revision(add=_ADDED))
    first = await _registry(agent)
    second = ModelRegistry.in_memory(AuthStorage(agent / "auth.json"))
    second._models_store = FileModelsStore(agent / "models-store.json")
    await _refresh(first)
    assert first.find("openrouter", NEW_OR_ID) is not None
    assert second.find("openrouter", NEW_OR_ID) is None  # composed before it

    outcome = await _refresh(second)

    assert outcome.outcome == "skipped"
    assert second.find("openrouter", NEW_OR_ID) is not None


async def test_a_304_still_fetches_a_revision_the_store_lacks(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """§4.3 step 3: "the next check, 304 included, fetches the revisions still missing" (Codex 1)."""

    mine = revision(add=_ADDED)
    other = revision(add={"openrouter": {"acme/other-1": new_row("Other")}})
    names = catalog_server.publish(
        mine, other, index=index_for(("0.1.0b2", hc.revision_name(mine)), ("0.3.0", hc.revision_name(other)))
    )
    del catalog_server.routes[revision_path(hc.revision_name(other))]
    registry = await _registry(agent)
    await _refresh(registry)
    assert list(_stored(agent)["revisions"]) == [hc.revision_name(mine)]
    catalog_server.routes[revision_path(hc.revision_name(other))] = Response(200, other)
    real_route = catalog_server.routes[INDEX_PATH]
    catalog_server.routes[INDEX_PATH] = lambda headers: Response(304)  # a server that ignores validators

    outcome = await _refresh(registry, force=True)

    assert outcome.outcome == "unchanged" and outcome.error is None
    assert sorted(_stored(agent)["revisions"]) == sorted(names)
    assert real_route is not None


async def test_a_partial_rollback_keeps_its_removed_ids_pending_until_complete(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """Codex 1: a hold whose other revision failed lost the removal for good."""

    newer = revision(add=_ADDED)
    catalog_server.publish(newer)
    registry = await _registry(agent)
    await _refresh(registry)
    base = revision()
    other = revision(add={"openrouter": {"acme/other-1": new_row("Other")}})
    catalog_server.publish(
        base,
        other,
        index=index_for(("0.1.0b2", hc.revision_name(base)), ("0.3.0", hc.revision_name(other)), hold="rollback"),
        etag='"rb"',
    )
    del catalog_server.routes[revision_path(hc.revision_name(other))]
    await _refresh(registry, force=True)
    assert _stored(agent)["pendingRemoved"] == {"openrouter": [NEW_OR_ID]}
    assert not registry.is_removed("openrouter", NEW_OR_ID)

    catalog_server.routes[revision_path(hc.revision_name(other))] = Response(200, other)
    await _refresh(registry, force=True)

    assert "pendingRemoved" not in _stored(agent)
    assert registry.is_removed("openrouter", NEW_OR_ID)


async def test_a_withdrawal_counts_the_rows_it_removed(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    await _refresh(registry)
    catalog_server.publish(revision(), index=index_for(hold="withdraw"), etag='"w"')

    outcome = await _refresh(registry, force=True)

    assert (outcome.outcome, outcome.removed) == ("withdrawn", 1)



async def test_another_entrys_revision_that_is_not_utf8_leaves_this_builds_installed(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """Independent review 1: a UnicodeDecodeError after the fetch stored nothing, every run."""

    mine = revision(add=_ADDED)
    other = b"\xff\xfe not text"
    catalog_server.publish(
        mine, index=index_for(("0.1.0b2", hc.revision_name(mine)), ("0.3.0", hc.revision_name(other)))
    )
    catalog_server.routes[revision_path(hc.revision_name(other))] = Response(200, other)
    registry = await _registry(agent)

    outcome = await _refresh(registry)

    assert outcome.outcome == "published" and "not UTF-8" in (outcome.error or "")
    assert catalog.installed_overlay() == hc.revision_name(mine)
    assert (await _refresh(registry)).outcome == "skipped"  # stored, so the 4 hours hold


def test_a_hand_edited_store_never_stops_a_launch(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Independent review 1: a hash-matching, deeply nested revision raised RecursionError at create()."""

    deep = b"[" * 200_000 + b"]" * 200_000
    name = hc.revision_name(deep)
    entry = {
        "url": "http://127.0.0.1:9/v1/index.json",
        "index": index_for(name),
        "checkedAt": 1.0,
        "revisions": {name: deep.decode()},
    }
    store = InMemoryModelsStore({HOSTED_ENTRY: entry})

    update = source.load_cached(store, base_url="http://127.0.0.1:9", version="0.1.0b2")

    assert update.action == "remove"


async def test_a_store_of_an_unknown_version_is_not_fetched_for_at_all(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """The downgrade case (§4.3): nothing read, nothing rewritten, and nothing downloaded either."""

    catalog_server.publish(revision(add=_ADDED))
    path = agent / "models-store.json"
    path.write_text(json.dumps({"version": 2, "entries": {}}), encoding="utf-8")
    registry = await _registry(agent)

    assert (await _refresh(registry)).outcome == "skipped"
    assert (await _refresh(registry, force=True)).outcome == "failed"
    assert catalog_server.log == []
    assert json.loads(path.read_text())["version"] == 2


async def test_a_failed_write_backs_off_for_fifteen_minutes(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ModelsStoreError("the models store could not be written (read-only)")

    monkeypatch.setattr(registry._models_store, "write", refuse)
    assert (await _refresh(registry)).outcome == "failed"
    requests = len(catalog_server.log)

    assert (await _refresh(registry)).outcome == "skipped"
    assert len(catalog_server.log) == requests
    assert (await _refresh(registry, force=True)).outcome == "failed"  # forced: it tries


def test_an_empty_object_store_is_an_empty_store_and_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "models-store.json"
    path.write_text("{}", encoding="utf-8")
    store = FileModelsStore(path)

    assert store.read("aelix-hosted") is None
    assert store.write("aelix-hosted", {"checkedAt": 1}, base=None) is None
    assert json.loads(path.read_text())["version"] == 1


async def test_a_trigger_waits_at_most_its_bound_and_the_last_waiter_aborts(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    """The 15 s bound is the trigger's (pi ``fed6009cc``); here 0.3 s (independent review 1)."""

    from aelix_coding_agent.model_refresh import refresh_catalogs

    catalog_server.publish(revision(add=_ADDED))
    real_route = catalog_server.routes[INDEX_PATH]

    def slow(headers: Any) -> Response:
        time.sleep(1.0)
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = slow
    registry = await _registry(agent)
    started = time.monotonic()

    assert await refresh_catalogs(registry, trigger="t", timeout=0.3) is None
    assert time.monotonic() - started < 0.9
    await asyncio.sleep(1.2)
    assert catalog.installed_overlay() is None  # the run was aborted, not published late


async def test_a_forced_trigger_supersedes_an_unforced_run(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import refresher

    catalog_server.publish(revision(add=_ADDED))
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()
    real_route = catalog_server.routes[INDEX_PATH]
    calls = {"n": 0}

    def first_waits(headers: Any) -> Response:
        calls["n"] += 1
        if calls["n"] == 1:
            asyncio.run_coroutine_threadsafe(gate.wait(), loop).result(timeout=10)
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = first_waits
    registry = await _registry(agent)
    unforced = asyncio.ensure_future(refresher().refresh(registry, trigger="a"))
    await asyncio.sleep(0.2)
    forced = await refresher().refresh(registry, trigger="b", force=True)
    gate.set()
    first = await unforced

    assert forced is not None and forced.outcomes[0].outcome == "published"
    assert first is None or first.outcomes[0].outcome == "aborted"
    assert catalog.installed_overlay() is not None


async def test_teardown_cancels_a_running_refresh_before_it_publishes(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import refresher, start_background_refresh

    catalog_server.publish(revision(add=_ADDED))
    real_route = catalog_server.routes[INDEX_PATH]

    def slow(headers: Any) -> Response:
        time.sleep(0.5)
        return real_route(headers)  # type: ignore[operator]

    catalog_server.routes[INDEX_PATH] = slow
    registry = await _registry(agent)
    task = start_background_refresh(registry, trigger="startup")
    assert task is not None
    await asyncio.sleep(0.1)

    await refresher().cancel_all()
    await asyncio.sleep(0.8)

    assert task.done() and catalog.installed_overlay() is None


def test_the_registrys_first_check_refuses_an_aborted_run(agent: Path) -> None:
    from aelix_ai.models_store import ModelsPublication

    registry = ModelRegistry.in_memory(AuthStorage(agent / "auth.json"))
    registry._models_store = InMemoryModelsStore()
    registry._refresh_runs[HOSTED_ENTRY] = 1
    signal = AbortSignal()
    signal.abort()

    published = registry._publish(
        HOSTED_ENTRY, 1, signal, ModelsPublication(persist={"checkedAt": 1}, base=None)
    )

    assert published is False and registry._models_store.read(HOSTED_ENTRY) is None


async def test_the_write_backoff_still_adopts_what_another_aelix_stored(
    agent: Path, catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Review round 2 (N3): the backoff stops the network, not the adoption."""

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)
    other = await _registry(agent)
    real_write = registry._models_store.write

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ModelsStoreError("the models store is locked by another aelix")

    monkeypatch.setattr(registry._models_store, "write", refuse)
    assert (await _refresh(registry)).outcome == "failed"
    monkeypatch.setattr(registry._models_store, "write", real_write)
    catalog.install_overlay(None)
    await _refresh(other)  # another aelix stores the revision
    catalog.install_overlay(None)
    requests = len(catalog_server.log)

    outcome = await _refresh(registry)

    assert outcome.outcome == "skipped" and len(catalog_server.log) == requests
    assert registry.find("openrouter", NEW_OR_ID) is not None


async def test_a_404_keeps_the_pending_removals_of_a_partial_hold(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    newer = revision(add=_ADDED)
    catalog_server.publish(newer)
    registry = await _registry(agent)
    await _refresh(registry)
    base = revision()
    other = revision(add={"openrouter": {"acme/other-1": new_row("Other")}})
    catalog_server.publish(
        base,
        other,
        index=index_for(("0.1.0b2", hc.revision_name(base)), ("0.3.0", hc.revision_name(other)), hold="rollback"),
        etag='"rb"',
    )
    del catalog_server.routes[revision_path(hc.revision_name(other))]
    await _refresh(registry, force=True)
    del catalog_server.routes[INDEX_PATH]

    await _refresh(registry, force=True)

    assert _stored(agent)["pendingRemoved"] == {"openrouter": [NEW_OR_ID]}


def test_a_refused_catalog_url_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    env = {source.ENV_CATALOG_URL: "http://example.test/once"}
    for _ in range(3):
        assert source.catalog_base_url(env) is None
    assert caplog.text.count("the hosted model catalog is off") == 1
