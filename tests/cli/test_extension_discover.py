"""Issue #65 (ADR-0188) — ``aelix extension discover`` (advisory catalog) tests.

Unit-level, no network + no pip: catalog sources are registered as ``file://``
locations pointing at a JSON document written to ``tmp_path`` (so
``discover --refresh`` fetches over the air-gap-native ``file://`` transport,
never a live opener), and the delegated install runs through an injected
``_FakeRunner`` that records the pip argv. Covers ``source add --catalog``
persistence (in-memory + disk round-trip), ``--refresh`` cache writing,
cache-backed filtering + empty states, the resolve-and-DELEGATE contract (the
installer receives ``entry.source``, never the friendly name), ambiguous /
unknown refusals, and the HARD invariant that the discover code path never
writes the #64 pin sidecar.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from aelix_ai.settings import ExtensionSourceObject, SettingsManager
from aelix_coding_agent.cli import extension_catalog
from aelix_coding_agent.cli import extension_pins as ep
from aelix_coding_agent.cli.extension_install import (
    _catalog_identity,
    _effective_catalog_locations,
    _effective_default_identity,
    _make_catalog_verifier,
    run_extension_command,
    run_extension_command_async,
)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin settings + agent-dir I/O at throwaway paths so tests never touch ~/.aelix.

    ``AELIX_SETTINGS_PATH`` pins the GLOBAL settings file (the reliable isolation
    lever); ``AELIX_CODING_AGENT_DIR`` sets the agent dir ``get_agent_dir()``
    returns — the catalog cache + pin sidecar both live under it, so the discover
    handlers (which read ``get_agent_dir()`` directly, uninjected) land in tmp.
    ``chdir`` decouples the project scope. Mirrors test_extension_install.py.
    """

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.chdir(tmp_path)


def _agent_dir(tmp_path: Path) -> Path:
    return tmp_path / "agent"


def _mem_settings() -> SettingsManager:
    return SettingsManager.in_memory()


class _FakeRunner:
    """Records the pip argv it was handed; returns a chosen exit code."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


def _write_catalog(
    path: Path, entries: list[dict[str, object]], *, name: str | None = None
) -> str:
    """Write a catalog JSON document → return a ``file://`` URI for registration."""

    doc: dict[str, object] = {"schemaVersion": 1, "extensions": entries}
    if name is not None:
        doc["name"] = name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path.as_uri()


def _seed_cache(tmp_path: Path, catalogs: list[extension_catalog.Catalog]) -> None:
    """Write the merged cache sidecar directly (no fetch), as ``--refresh`` would."""

    extension_catalog.save_catalogs(
        catalogs, extension_catalog.cache_file_path(_agent_dir(tmp_path))
    )


# === source add --catalog persistence ====================================


def test_source_add_catalog_persists_in_memory(tmp_path: Path) -> None:
    uri = _write_catalog(tmp_path / "catalog.json", [], name="corp")
    mem = _mem_settings()
    code = run_extension_command(
        ["source", "add", "--catalog", uri], settings=mem
    )
    assert code == 0
    sources = mem.get_extension_sources()
    assert sources == [ExtensionSourceObject(spec=uri, kind="catalog")]


def test_source_add_catalog_dedupes(tmp_path: Path) -> None:
    uri = _write_catalog(tmp_path / "catalog.json", [])
    mem = _mem_settings()
    assert run_extension_command(["source", "add", "--catalog", uri], settings=mem) == 0
    # A second add of the SAME location is idempotent.
    assert run_extension_command(["source", "add", "--catalog", uri], settings=mem) == 0
    assert len(mem.get_extension_sources()) == 1


def test_source_add_catalog_distinct_from_index(tmp_path: Path) -> None:
    # The SAME https URL registered with vs without --catalog yields distinct
    # kinds (the flag selects "catalog"; classify_source cannot infer it).
    mem = _mem_settings()
    assert run_extension_command(
        ["source", "add", "https://host.corp/simple"], settings=mem
    ) == 0
    assert run_extension_command(
        ["source", "add", "--catalog", "https://host.corp/catalog.json"], settings=mem
    ) == 0
    kinds = sorted(s.kind for s in mem.get_extension_sources())
    assert kinds == ["catalog", "index"]


async def test_source_add_catalog_persists_to_disk(tmp_path: Path) -> None:
    # Guards the async-flush invariant: only a FRESH manager over the same FILE
    # proves the handler awaited settings.flush() (an in-memory read would pass
    # even if the write were dropped).
    uri = _write_catalog(tmp_path / "catalog.json", [], name="corp")
    settings_path = tmp_path / "disk-settings.json"

    def _fresh() -> SettingsManager:
        return SettingsManager.create(cwd=str(tmp_path), agent_dir=tmp_path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(settings_path))
    try:
        mgr = _fresh()
        code = await run_extension_command_async(
            ["source", "add", "--catalog", uri], settings=mgr
        )
        assert code == 0
        reloaded = _fresh()
        recorded = reloaded.get_extension_sources()
        assert [(s.spec, s.kind) for s in recorded] == [(uri, "catalog")]
    finally:
        monkeypatch.undo()


# === discover --refresh (fetch + cache write) =============================


def test_discover_refresh_fetches_and_writes_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uri = _write_catalog(
        tmp_path / "catalog.json",
        [
            {"name": "foo-ext", "source": "foo-pkg==1.2", "description": "does foo"},
            {"name": "bar-ext", "source": "bar-pkg", "description": "does bar"},
        ],
        name="corp",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    code = run_extension_command(["discover", "--refresh"], settings=mem)
    assert code == 0
    out = capsys.readouterr().out
    assert "foo-ext" in out and "bar-ext" in out
    # The cache sidecar was written under the agent dir and holds both entries.
    cache = extension_catalog.load_cached_catalog(_agent_dir(tmp_path))
    names = {e.name for c in cache for e in c.entries}
    assert names == {"foo-ext", "bar-ext"}


def test_discover_no_catalog_registered_empty_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_extension_command(["discover"], settings=_mem_settings())
    assert code == 0
    assert "No catalog registered" in capsys.readouterr().out


def test_discover_registered_but_no_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uri = _write_catalog(tmp_path / "catalog.json", [{"name": "x", "source": "x"}])
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    # No --refresh and no cache yet → honest "no cached data" (not a crash).
    code = run_extension_command(["discover"], settings=mem)
    assert code == 0
    assert "No cached catalog" in capsys.readouterr().out


# === discover <query> (cache-backed filtering) ===========================


def test_discover_query_filters_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uri = _write_catalog(tmp_path / "catalog.json", [{"name": "x", "source": "x"}])
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location=uri,
                name="corp",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="foo-pkg", description="the foo one",
                        catalog_name="corp",
                    ),
                    extension_catalog.CatalogEntry(
                        name="bar-ext", source="bar-pkg", description="the bar one",
                        catalog_name="corp",
                    ),
                ),
                fetched_at=extension_catalog.now_iso(),
            )
        ],
    )
    # Query reads the cache (no --refresh) and filters case-insensitively.
    code = run_extension_command(["discover", "foo"], settings=mem)
    assert code == 0
    out = capsys.readouterr().out
    assert "foo-ext" in out
    assert "bar-ext" not in out


def test_discover_query_no_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uri = _write_catalog(tmp_path / "catalog.json", [{"name": "x", "source": "x"}])
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location=uri,
                entries=(
                    extension_catalog.CatalogEntry(name="foo-ext", source="foo-pkg"),
                ),
                fetched_at=extension_catalog.now_iso(),
            )
        ],
    )
    code = run_extension_command(["discover", "nomatch-zzz"], settings=mem)
    assert code == 0
    assert "No extensions match" in capsys.readouterr().out


def test_search_is_discover_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_extension_command(["search"], settings=_mem_settings())
    assert code == 0
    assert "No catalog registered" in capsys.readouterr().out


# === discover install <name> — resolve + DELEGATE ========================


def test_discover_install_delegates_resolved_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location="file:///corp",
                name="corp",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="foo-pkg==1.2", catalog_name="corp",
                    ),
                ),
                fetched_at=extension_catalog.now_iso(),
            )
        ],
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["discover", "install", "foo-ext", "--yes", "--no-verify"],
        settings=_mem_settings(),
        runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    # The installer receives the RESOLVED spec, NEVER the friendly catalog name.
    assert "foo-pkg==1.2" in argv
    assert "foo-ext" not in argv
    out = capsys.readouterr().out
    assert "Resolved foo-ext -> foo-pkg==1.2" in out


def test_discover_install_ambiguous_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The SAME name in two catalogs → refuse with a candidate list, no install.
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location="file:///a",
                name="cat-a",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="pkg-from-a", catalog_name="cat-a",
                    ),
                ),
            ),
            extension_catalog.Catalog(
                location="file:///b",
                name="cat-b",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="pkg-from-b", catalog_name="cat-b",
                    ),
                ),
            ),
        ],
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["discover", "install", "foo-ext", "--yes"],
        settings=_mem_settings(),
        runner=runner,
    )
    assert code == 2  # ambiguous → refusal, "did not run pip"
    assert runner.calls == []  # NO install runs on an ambiguous name
    err = capsys.readouterr().err
    assert "ambiguous" in err
    # Both candidate specs are surfaced so the operator can pick a --catalog.
    assert "pkg-from-a" in err and "pkg-from-b" in err


def test_discover_install_ambiguous_disambiguated_by_catalog(
    tmp_path: Path,
) -> None:
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location="file:///a",
                name="cat-a",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="pkg-from-a", catalog_name="cat-a",
                    ),
                ),
            ),
            extension_catalog.Catalog(
                location="file:///b",
                name="cat-b",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="foo-ext", source="pkg-from-b", catalog_name="cat-b",
                    ),
                ),
            ),
        ],
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["discover", "install", "foo-ext", "--catalog", "cat-b", "--yes", "--no-verify"],
        settings=_mem_settings(),
        runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1
    assert "pkg-from-b" in runner.calls[0]  # the chosen catalog's spec
    assert "pkg-from-a" not in runner.calls[0]


def test_discover_install_unknown_name_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location="file:///corp",
                name="corp",
                entries=(
                    extension_catalog.CatalogEntry(name="foo-ext", source="foo-pkg"),
                ),
            )
        ],
    )
    runner = _FakeRunner()
    code = run_extension_command(
        ["discover", "install", "ghost-ext", "--yes"],
        settings=_mem_settings(),
        runner=runner,
    )
    assert code == 2
    assert runner.calls == []
    assert "no catalog entry named" in capsys.readouterr().err


def test_discover_install_requires_name(tmp_path: Path) -> None:
    assert run_extension_command(
        ["discover", "install", "--yes"], settings=_mem_settings()
    ) == 2


# === HARD invariant: the discover path never writes the pin sidecar ======


def test_discover_never_writes_pin_sidecar(tmp_path: Path) -> None:
    # A full discover flow (refresh + browse + install of a pypi entry under the
    # default gate) must NOT create extension_pins.json — catalog code is barred
    # from seeding the #64 pin store (ADR-0188 owner decision 2).
    uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "foo-ext", "source": "foo-pkg==1.2", "sha256": "d" * 64}],
        name="corp",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    assert run_extension_command(["discover", "--refresh"], settings=mem) == 0
    assert run_extension_command(["discover", "foo"], settings=mem) == 0
    runner = _FakeRunner()
    assert run_extension_command(
        ["discover", "install", "foo-ext", "--yes"], settings=mem, runner=runner
    ) == 0
    # No pin sidecar exists even though the catalog entry carried a sha256.
    pins_path = ep.pins_file_path(_agent_dir(tmp_path))
    assert not pins_path.exists()
    assert ep.load_pins(pins_path) == {}


# === source add --catalog: transport + shape refusals ====================


async def test_source_add_catalog_rejects_plain_http(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #6: a plain-HTTP catalog location is MITM-rewritable → refused AT ADD (TLS
    # required), for both a bare http URL and a git+http transport, and NOTHING is
    # persisted (the refusal is before the store write).
    mem = _mem_settings()
    code = await run_extension_command_async(
        ["source", "add", "--catalog", "http://host/c.json"], settings=mem
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "TLS required" in err or "plain-HTTP" in err
    assert mem.get_extension_sources() == []

    code = await run_extension_command_async(
        ["source", "add", "--catalog", "git+http://h/x.git"], settings=mem
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "TLS required" in err or "plain-HTTP" in err
    assert mem.get_extension_sources() == []


async def test_source_add_catalog_rejects_bare_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #10: a bareword (no separator/scheme) is an install TARGET, not a catalog
    # location — refused at add, storing nothing.
    mem = _mem_settings()
    code = await run_extension_command_async(
        ["source", "add", "--catalog", "foobar"], settings=mem
    )
    assert code == 2
    assert "not a valid catalog location" in capsys.readouterr().err
    assert mem.get_extension_sources() == []


# === discover --refresh: all-failed vs mixed exit semantics ==============


async def test_discover_refresh_all_failed_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #12: a refresh where EVERY registered catalog failed is a hard error (2),
    # distinct from a successful-but-empty catalog — with a per-source ⚠ warning row
    # AND the "every registered catalog failed" summary line.
    missing = (tmp_path / "missing.json").as_uri()  # file:// to a non-existent path
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": missing, "kind": "catalog"}]}
    )
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 2
    err = capsys.readouterr().err
    assert "⚠" in err
    assert "every registered catalog failed" in err


async def test_discover_refresh_mixed_good_and_bad(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #12 (mixed): one good + one missing catalog → exit 0, the good entries list on
    # stdout AND the ⚠ row still prints for the bad one (one failure never hides the
    # healthy catalogs).
    good = _write_catalog(
        tmp_path / "good.json",
        [{"name": "good-ext", "source": "good-pkg", "description": "the good one"}],
        name="good",
    )
    missing = (tmp_path / "missing.json").as_uri()
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": good, "kind": "catalog"},
                {"spec": missing, "kind": "catalog"},
            ]
        }
    )
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 0
    captured = capsys.readouterr()
    assert "good-ext" in captured.out
    assert "⚠" in captured.err


# === discover install: leading-dash source + intra-catalog dup ===========


async def test_discover_install_resolved_source_with_leading_dash(
    tmp_path: Path,
) -> None:
    # #11: a resolved source that legitimately starts with '-' must reach pip as a
    # positional (the '--' delegate guard), not be misparsed as a flag. Before the
    # fix this errored exit 2; assert it now succeeds and the runner sees the spec.
    uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "dashy", "source": "-weird-pkg"}],
        name="corp",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    runner = _FakeRunner()
    code = await run_extension_command_async(
        ["discover", "install", "dashy", "--yes", "--no-verify"],
        settings=mem,
        runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1
    assert "-weird-pkg" in runner.calls[0]


async def test_discover_install_duplicate_name_in_one_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #8: a SINGLE catalog listing the same name twice cannot be disambiguated by
    # --catalog → refuse with a "fix the catalog" message and run NO install
    # (distinct from the cross-catalog ambiguity that says "pass --catalog").
    uri = _write_catalog(
        tmp_path / "catalog.json",
        [
            {"name": "dup", "source": "pkg-one"},
            {"name": "dup", "source": "pkg-two"},
        ],
        name="corp",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    runner = _FakeRunner()
    code = await run_extension_command_async(
        ["discover", "install", "dup", "--yes"],
        settings=mem,
        runner=runner,
    )
    assert code == 2
    assert runner.calls == []  # NO install runs on an intra-catalog duplicate
    err = capsys.readouterr().err
    assert "lists 'dup' more than once" in err
    assert "fix the catalog" in err


# === discover install: the catalog hash NEVER seeds the #64 pin ==========


async def test_discover_install_git_entry_pins_source_sha_not_catalog_hash(
    tmp_path: Path,
) -> None:
    # #15: a git entry installed under the DEFAULT verify gate records a #64 git pin
    # from the SOURCE's pinned commit SHA — and the catalog's display-only sha256
    # ("deadbeef") must never leak into the pin store (ADR-0188: an unauthenticated
    # network hash must not manufacture a false-green "verified" pin).
    from aelix_coding_agent.cli.config import get_agent_dir

    sha = "a" * 40
    uri = _write_catalog(
        tmp_path / "catalog.json",
        [
            {
                "name": "gitext",
                "source": f"git+https://h/r.git@{sha}",
                "sha256": "deadbeef",  # display-only — must NOT reach the pin store
            }
        ],
        name="corp",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    runner = _FakeRunner(returncode=0)
    code = await run_extension_command_async(
        ["discover", "install", "gitext", "--yes"],  # DEFAULT gate — no --no-verify
        settings=mem,
        runner=runner,
    )
    assert code == 0
    assert len(runner.calls) == 1

    pins_path = ep.pins_file_path(get_agent_dir())
    pins = ep.load_pins(pins_path)
    git_pins = [p for p in pins.values() if p.kind == "git"]
    assert len(git_pins) == 1
    assert git_pins[0].git_sha == sha  # pinned from the SOURCE, not the catalog
    # The catalog's display hash appears NOWHERE in the persisted pin store.
    assert "deadbeef" not in pins_path.read_text(encoding="utf-8")


# === discover --refresh over the real git transport (offline) ============


async def test_discover_refresh_git_catalog_via_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #17: exercise the CLI discover → module git shallow-clone path OFFLINE against
    # a local bare repo (git+file://) — the committed catalog entry must list.
    import os
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git binary not available for the offline git-catalog transport test")

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True, capture_output=True)
    (work / "catalog.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "gitcat",
                "extensions": [
                    {"name": "git-ext", "source": "git-pkg", "description": "from git"}
                ],
            }
        ),
        encoding="utf-8",
    )
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "-C", str(work), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(work), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(bare)],
        check=True,
        capture_output=True,
    )

    spec = "git+file://" + str(bare)
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": spec, "kind": "catalog"}]}
    )
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 0
    assert "git-ext" in capsys.readouterr().out


# === discover (no --refresh): the cached-snapshot staleness hint =========


async def test_discover_cached_snapshot_staleness_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # #18: browsing the cache (no --refresh) prints a "cached snapshot" hint carrying
    # the OLDEST fetch stamp across the merged catalogs; a --refresh (which rewrites
    # the cache in this turn) omits the hint entirely.
    older = "2020-01-01T00:00:00+00:00"
    newer = "2026-01-01T00:00:00+00:00"
    uri = _write_catalog(tmp_path / "catalog.json", [{"name": "x", "source": "x"}])
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": uri, "kind": "catalog"}]}
    )
    _seed_cache(
        tmp_path,
        [
            extension_catalog.Catalog(
                location="file:///a",
                name="cat-a",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="a-ext", source="a-pkg", catalog_name="cat-a"
                    ),
                ),
                fetched_at=older,
            ),
            extension_catalog.Catalog(
                location="file:///b",
                name="cat-b",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="b-ext", source="b-pkg", catalog_name="cat-b"
                    ),
                ),
                fetched_at=newer,
            ),
        ],
    )
    code = await run_extension_command_async(["discover"], settings=mem)
    assert code == 0
    out = capsys.readouterr().out
    assert "cached snapshot" in out
    assert older in out  # the OLDEST stamp is the one surfaced

    # A --refresh rewrites the cache this turn → the staleness hint is absent.
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 0
    assert "cached snapshot" not in capsys.readouterr().out


# === Track D (ADR-0192): built-in default catalog merge + opt-out tombstone =

_OFFICIAL = "https://official/catalog.json"


def test_catalog_identity_normalizes_git_forms() -> None:
    # A stored git catalog and a raw ``.git`` URL of the SAME repo share an identity
    # (so a default and a stored dup compare EQUAL for dedupe/tombstone matching).
    assert _catalog_identity("https://h/r.git") == _catalog_identity("git+https://h/r.git")
    assert _catalog_identity("git+https://h/r.git") == "git+https://h/r.git"


def test_effective_locations_no_default_when_dormant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Beta placeholder empty + env unset → no built-in default (dormant): the
    # effective list is exactly the stored catalogs.
    monkeypatch.delenv("AELIX_DEFAULT_CATALOG", raising=False)
    assert _effective_default_identity() is None
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "file:///corp/c.json", "kind": "catalog"}]}
    )
    assert _effective_catalog_locations(mem, offline=False) == ["file:///corp/c.json"]


def test_effective_locations_default_first_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    # No stored → just the default (normalized).
    assert _effective_catalog_locations(_mem_settings(), offline=False) == [_OFFICIAL]
    # With a stored catalog the built-in default leads, stored follows.
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "file:///corp/c.json", "kind": "catalog"}]}
    )
    assert _effective_catalog_locations(mem, offline=False) == [
        _OFFICIAL,
        "file:///corp/c.json",
    ]


def test_effective_locations_dedupes_stored_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stored catalog carrying the default's identity wins → shown exactly once.
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": _OFFICIAL, "kind": "catalog"}]}
    )
    assert _effective_catalog_locations(mem, offline=False) == [_OFFICIAL]


def test_effective_locations_tombstone_drops_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = SettingsManager.in_memory({"suppressedDefaultCatalogs": [_OFFICIAL]})
    assert _effective_catalog_locations(mem, offline=False) == []


def test_effective_locations_offline_drops_https_default_keeps_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = _mem_settings()
    # An https default is unreachable offline → dropped silently.
    assert _effective_catalog_locations(mem, offline=True) == []
    assert _effective_catalog_locations(mem, offline=False) == [_OFFICIAL]
    # A file:// default is air-gap reachable → kept even offline.
    file_default = (tmp_path / "d.json").as_uri()
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", file_default)
    assert _effective_catalog_locations(mem, offline=True) == [file_default]


def test_env_repoint_escapes_stale_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tombstone{URL-A} + env=URL-B → URL-B is present (identity-scoped opt-out, so
    # an enterprise repoint is never blocked by a stale opt-out of a different URL).
    url_b = "https://b/catalog.json"
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", url_b)
    mem = SettingsManager.in_memory(
        {"suppressedDefaultCatalogs": ["https://a/catalog.json"]}
    )
    assert _effective_catalog_locations(mem, offline=False) == [url_b]


async def test_source_remove_default_writes_tombstone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = _mem_settings()
    code = await run_extension_command_async(["source", "remove", _OFFICIAL], settings=mem)
    assert code == 0
    # The identity is tombstoned (persisted opt-out) and the default disappears.
    assert mem.get_suppressed_default_catalogs() == [_OFFICIAL]
    assert "built-in default opted out" in capsys.readouterr().out
    assert _effective_catalog_locations(mem, offline=False) == []
    # source list shows the built-in row marked suppressed (guard ③).
    assert await run_extension_command_async(["source", "list"], settings=mem) == 0
    listed = capsys.readouterr().out
    assert "built-in default" in listed and "suppressed" in listed
    # discover now reports no registered catalog (only the tombstoned default existed).
    assert await run_extension_command_async(["discover"], settings=mem) == 0
    assert "No catalog registered" in capsys.readouterr().out


async def test_source_remove_default_second_time_is_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Once tombstoned, a repeat remove writes no new tombstone and removes no stored
    # source → the standard no-match error (exit 2), per the spec.
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = SettingsManager.in_memory({"suppressedDefaultCatalogs": [_OFFICIAL]})
    code = await run_extension_command_async(["source", "remove", _OFFICIAL], settings=mem)
    assert code == 2
    assert mem.get_suppressed_default_catalogs() == [_OFFICIAL]


async def test_source_add_default_clears_tombstone_reappears_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    mem = SettingsManager.in_memory({"suppressedDefaultCatalogs": [_OFFICIAL]})
    code = await run_extension_command_async(
        ["source", "add", "--catalog", _OFFICIAL], settings=mem
    )
    assert code == 0
    assert capsys.readouterr().out  # a registration/re-activation line printed
    # The opt-out is cleared and the location is registered as a stored source.
    assert mem.get_suppressed_default_catalogs() == []
    assert [s.spec for s in mem.get_extension_sources()] == [_OFFICIAL]
    # Dedupe: the built-in default + the stored copy collapse to a single row.
    assert _effective_catalog_locations(mem, offline=False) == [_OFFICIAL]


async def test_non_default_catalog_remove_add_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-default catalog's remove/add never touches the tombstone list.
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    other = (tmp_path / "other.json").as_uri()
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": other, "kind": "catalog"}]}
    )
    code = await run_extension_command_async(["source", "remove", other], settings=mem)
    assert code == 0
    assert mem.get_extension_sources() == []
    assert mem.get_suppressed_default_catalogs() == []  # no tombstone for a non-default
    code = await run_extension_command_async(
        ["source", "add", "--catalog", other], settings=mem
    )
    assert code == 0
    assert mem.get_suppressed_default_catalogs() == []


async def test_discover_refresh_offline_skips_https_keeps_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # --offline skips https/git+https catalogs with a per-source notice and still
    # fetches file:// / path catalogs (air-gap transports).
    file_uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "local-ext", "source": "local-pkg"}],
        name="local",
    )
    https_spec = "https://corp/catalog.json"
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": https_spec, "kind": "catalog"},
                {"spec": file_uri, "kind": "catalog"},
            ]
        }
    )
    code = await run_extension_command_async(
        ["discover", "--refresh", "--offline"], settings=mem
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "local-ext" in out
    assert "skipped" in out and https_spec in out
    # The https catalog was never fetched → absent from the cache; only file cached.
    locs = {c.location for c in extension_catalog.load_cached_catalog(_agent_dir(tmp_path))}
    assert file_uri in locs
    assert https_spec not in locs


async def test_discover_refresh_offline_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # AELIX_OFFLINE=1 gates transport identically to the --offline flag.
    monkeypatch.setenv("AELIX_OFFLINE", "1")
    file_uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "local-ext", "source": "local-pkg"}],
        name="local",
    )
    https_spec = "https://corp/catalog.json"
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": https_spec, "kind": "catalog"},
                {"spec": file_uri, "kind": "catalog"},
            ]
        }
    )
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 0
    out = capsys.readouterr().out
    assert "local-ext" in out
    assert "skipped" in out and https_spec in out


def test_is_offline_fetchable_allowlist() -> None:
    # Guard ④ (ADR-0192) is an ALLOWLIST, not a 2-item blocklist: only file / git+ssh
    # / git+file / bare local paths are fetched offline; EVERY remote transport is
    # skipped — including the unencrypted git-daemon git+git:// / git:// egress the
    # old (https, git+https) blocklist let through.
    from aelix_coding_agent.cli.extension_install import _is_offline_fetchable

    assert _is_offline_fetchable("file:///corp/c.json")
    assert _is_offline_fetchable("git+ssh://git@intranet/repo.git")
    assert _is_offline_fetchable("git+file:///srv/repo.git")
    assert _is_offline_fetchable("/abs/local/catalog.json")
    assert not _is_offline_fetchable("https://corp/catalog.json")
    assert not _is_offline_fetchable("git+https://corp/repo.git")
    assert not _is_offline_fetchable("git+git://corp/repo.git")  # the closed leak
    assert not _is_offline_fetchable("git://corp/repo.git")
    assert not _is_offline_fetchable("ssh://git@corp/repo.git")


async def test_discover_refresh_offline_skips_git_daemon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A git+git:// (unencrypted git-daemon, remote) source must NOT be cloned while
    # --offline — it is skipped with a notice and never reaches the cache (no egress).
    file_uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "local-ext", "source": "local-pkg"}],
        name="local",
    )
    git_spec = "git+git://corp/catalog.git"
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": git_spec, "kind": "catalog"},
                {"spec": file_uri, "kind": "catalog"},
            ]
        }
    )
    code = await run_extension_command_async(
        ["discover", "--refresh", "--offline"], settings=mem
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "local-ext" in out
    assert "skipped" in out and git_spec in out
    locs = {c.location for c in extension_catalog.load_cached_catalog(_agent_dir(tmp_path))}
    assert file_uri in locs
    assert git_spec not in locs


async def test_discover_refresh_offline_all_network_preserves_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Data-loss guard (ADR-0192): an offline refresh whose registered sources are ALL
    # network transports skips every source and must NOT overwrite a previously-cached
    # (online) catalog with an empty set — the save is a no-op, the prior cache lives.
    file_uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "local-ext", "source": "local-pkg"}],
        name="local",
    )
    seed_mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": file_uri, "kind": "catalog"}]}
    )
    assert (
        await run_extension_command_async(
            ["discover", "--refresh", "--offline"], settings=seed_mem
        )
        == 0
    )
    capsys.readouterr()
    # Now only an https source is registered → offline skips it; the seeded cache must
    # remain intact (never blown away to empty).
    net_mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "https://corp/catalog.json", "kind": "catalog"}]}
    )
    code = await run_extension_command_async(
        ["discover", "--refresh", "--offline"], settings=net_mem
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "cache unchanged" in out
    cached = extension_catalog.load_cached_catalog(_agent_dir(tmp_path))
    assert any(
        getattr(e, "name", None) == "local-ext"
        for c in cached
        for e in getattr(c, "entries", ())
    )


def test_catalog_error_scrubs_control_chars(tmp_path: Path) -> None:
    # ADR-0192 security: a fetched .aelixsig keyId flows into a verification refusal →
    # CatalogError → Catalog.error, which the CLI/TUI render RAW. The error channel is
    # scrubbed of ANSI/control bytes by construction so attacker sidecar content cannot
    # paint a forged "authenticated" badge or clear the screen.
    file_uri = _write_catalog(
        tmp_path / "catalog.json", [{"name": "x", "source": "x"}], name="c"
    )

    def _evil_verifier(document: bytes, sidecar: bytes | None, location: str) -> None:
        raise ep.VerifyRefusal("untrusted key \x1b[2J\x1b[32m✓ trusted — " + location)

    cats = extension_catalog.fetch_all([file_uri], verifier=_evil_verifier)
    assert len(cats) == 1
    err = cats[0].error or ""
    assert err  # the refusal degraded this catalog to an error row (with a message)
    assert "\x1b" not in err
    assert not any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in err)


def test_catalog_verifier_requires_signature_only_for_default(tmp_path: Path) -> None:
    # Guard ⑤: the adapter demands a signature for the default identity (an unsigned
    # default → CatalogError) but verifies other catalogs best-effort (unsigned
    # admitted while first-party keys are empty). It also translates a fail-closed
    # refusal into a CatalogError so fetch_all degrades only that source.
    verifier = _make_catalog_verifier(
        str(_agent_dir(tmp_path)), signature_required=frozenset({_OFFICIAL})
    )
    with pytest.raises(extension_catalog.CatalogError):
        verifier(b'{"extensions": []}', None, _OFFICIAL)
    # A non-required catalog with no signature is admitted (no raise).
    verifier(b'{"extensions": []}', None, "https://corp/other.json")


async def test_default_catalog_is_signature_required_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Guard ⑤ live end-to-end: with a default configured (a file:// URL so no network
    # is touched), an UNSIGNED default catalog degrades to an error row and a
    # refresh where it is the only catalog exits 2. Dormant in beta ONLY because the
    # shipped default URL is empty — the mechanism itself is wired.
    file_default = _write_catalog(
        tmp_path / "official.json",
        [{"name": "o", "source": "o-pkg"}],
        name="official",
    )
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", file_default)
    mem = _mem_settings()
    code = await run_extension_command_async(["discover", "--refresh"], settings=mem)
    assert code == 2
    assert "signature" in capsys.readouterr().err.lower()


async def test_discover_no_default_catalog_flag_is_ephemeral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --no-default-catalog drops the built-in default for THIS run only, writing NO
    # tombstone: a stored catalog still lists, the default is absent, and the
    # suppressed list is untouched afterward.
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _OFFICIAL)
    file_uri = _write_catalog(
        tmp_path / "catalog.json",
        [{"name": "stored-ext", "source": "stored-pkg"}],
        name="stored",
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": file_uri, "kind": "catalog"}]}
    )
    code = await run_extension_command_async(
        ["discover", "--refresh", "--no-default-catalog"], settings=mem
    )
    assert code == 0
    locs = {c.location for c in extension_catalog.load_cached_catalog(_agent_dir(tmp_path))}
    assert file_uri in locs
    assert _OFFICIAL not in locs  # the built-in default was skipped this run
    assert mem.get_suppressed_default_catalogs() == []  # ephemeral — no tombstone
