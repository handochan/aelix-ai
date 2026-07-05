"""Issue #65 (ADR-0188) — unit tests for the pure discover-catalog module.

No pip, no network: exercises the lenient/capped parse, the transport dispatch
in :func:`fetch_catalog` (local path, ``file://``, ``http://`` refusal, injected
``https`` opener, injected git runner), ``fetch_all`` degradation, the atomic
cache save/load round-trip + lenient ``load_cached_catalog``, and the pure
``search_entries``/``resolve_entry`` primitives. Also asserts the ADR-0188
NO-PIN-SEEDING invariant: the module never touches ``extension_pins``.

Env isolation via ``AELIX_CODING_AGENT_DIR`` (NOT ``AELIX_AGENT_DIR``); every
path input is an explicit ``tmp_path``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from aelix_coding_agent.cli import extension_catalog as ec


@pytest.fixture(autouse=True)
def _isolate_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the coding-agent dir at a clean temp dir (belt-and-braces isolation)."""

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))


def _catalog_doc(*entries: dict[str, object], **top: object) -> str:
    payload: dict[str, object] = {"schemaVersion": 1, "extensions": list(entries)}
    payload.update(top)
    return json.dumps(payload)


# =====================================================================
# === Parse — lenient + capped =========================================
# =====================================================================


def test_parse_unknown_keys_ignored_and_preserved() -> None:
    doc = _catalog_doc(
        {
            "name": "widget",
            "source": "git+ssh://host/widget.git",
            "description": "a widget",
            "future_field": {"nested": 1},
        },
        name="Corp Catalog",
        updated="2026-07-05",
        someUnknownTopKey=42,
    )
    cat = ec.parse_catalog(doc, location="https://intranet/catalog.json")
    assert cat.name == "Corp Catalog"
    assert cat.updated == "2026-07-05"
    assert cat.fetched_at is not None
    assert len(cat.entries) == 1
    entry = cat.entries[0]
    assert entry.name == "widget"
    assert entry.source == "git+ssh://host/widget.git"
    assert entry.description == "a widget"
    assert entry.catalog_name == "Corp Catalog"
    # Unknown per-entry key round-trips through extra (forward compat).
    assert entry.extra["future_field"] == {"nested": 1}


def test_parse_entry_missing_name_or_source_is_skipped() -> None:
    doc = _catalog_doc(
        {"source": "path:/x"},  # no name -> skipped
        {"name": "no-source"},  # no source -> skipped
        {"name": "  ", "source": "path:/blank"},  # blank name -> skipped
        {"name": "keep", "source": "pypi-keep"},  # valid
    )
    cat = ec.parse_catalog(doc, location="loc")
    assert [e.name for e in cat.entries] == ["keep"]


def test_parse_non_json_raises() -> None:
    with pytest.raises(ec.CatalogError):
        ec.parse_catalog("{not valid json", location="loc")


def test_parse_non_object_root_raises() -> None:
    with pytest.raises(ec.CatalogError):
        ec.parse_catalog("[1, 2, 3]", location="loc")


def test_parse_missing_extensions_array_raises() -> None:
    with pytest.raises(ec.CatalogError):
        ec.parse_catalog(json.dumps({"schemaVersion": 1}), location="loc")


def test_parse_extensions_not_a_list_raises() -> None:
    with pytest.raises(ec.CatalogError):
        ec.parse_catalog(json.dumps({"extensions": {"a": 1}}), location="loc")


def test_parse_byte_cap_refused() -> None:
    oversized = b" " * (ec.MAX_CATALOG_BYTES + 1)
    with pytest.raises(ec.CatalogError, match="byte cap"):
        ec.parse_catalog(oversized, location="loc")


def test_parse_entry_count_cap_refused() -> None:
    entries = [{"name": f"e{i}", "source": f"pypi-e{i}"} for i in range(ec.MAX_CATALOG_ENTRIES + 1)]
    doc = json.dumps({"extensions": entries})
    with pytest.raises(ec.CatalogError, match="refusing to parse"):
        ec.parse_catalog(doc, location="loc")


# =====================================================================
# === fetch_catalog — transport dispatch ===============================
# =====================================================================


def test_fetch_local_bare_path(tmp_path: Path) -> None:
    f = tmp_path / "catalog.json"
    f.write_text(_catalog_doc({"name": "loc-ext", "source": "path:/loc"}), encoding="utf-8")
    cat = ec.fetch_catalog(str(f))
    assert [e.name for e in cat.entries] == ["loc-ext"]
    assert cat.location == str(f)


def test_fetch_file_url(tmp_path: Path) -> None:
    f = tmp_path / "catalog.json"
    f.write_text(_catalog_doc({"name": "file-ext", "source": "pypi-file"}), encoding="utf-8")
    location = f.as_uri()  # file:///abs/path/catalog.json
    cat = ec.fetch_catalog(location)
    assert [e.name for e in cat.entries] == ["file-ext"]
    assert cat.location == location


def test_fetch_missing_local_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ec.CatalogError, match="not found"):
        ec.fetch_catalog(str(tmp_path / "nope.json"))


def test_fetch_http_refused_for_tls() -> None:
    with pytest.raises(ec.CatalogError, match="TLS required"):
        ec.fetch_catalog("http://intranet/catalog.json")


def test_fetch_https_via_injected_opener() -> None:
    body = _catalog_doc({"name": "https-ext", "source": "pypi-https"}).encode("utf-8")
    seen: list[tuple[str, float]] = []

    def opener(url: str, timeout: float) -> bytes:
        seen.append((url, timeout))
        return body

    cat = ec.fetch_catalog("https://intranet/catalog.json", opener=opener, timeout=5.0)
    assert [e.name for e in cat.entries] == ["https-ext"]
    assert seen == [("https://intranet/catalog.json", 5.0)]


def test_fetch_https_opener_error_becomes_catalog_error() -> None:
    def opener(url: str, timeout: float) -> bytes:
        raise ConnectionError("boom")

    with pytest.raises(ec.CatalogError, match="failed to fetch"):
        ec.fetch_catalog("https://intranet/catalog.json", opener=opener)


def test_fetch_https_over_cap_refused() -> None:
    def opener(url: str, timeout: float) -> bytes:
        return b" " * (ec.MAX_CATALOG_BYTES + 1)

    with pytest.raises(ec.CatalogError, match="byte cap"):
        ec.fetch_catalog("https://intranet/catalog.json", opener=opener)


def test_fetch_git_via_injected_runner() -> None:
    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        dest = Path(argv[-1])  # ["git","clone","--depth","1",url,dest]
        (dest / ec.DEFAULT_CATALOG_FILENAME).write_text(
            _catalog_doc({"name": "git-ext", "source": "git+ssh://host/git-ext.git"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    cat = ec.fetch_catalog("git+ssh://host/repo.git", git_runner=git_runner)
    assert [e.name for e in cat.entries] == ["git-ext"]


def test_fetch_git_clone_failure_raises() -> None:
    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=b"fatal: repo not found")

    with pytest.raises(ec.CatalogError, match="git clone failed"):
        ec.fetch_catalog("git+ssh://host/missing.git", git_runner=git_runner)


def test_fetch_git_missing_root_catalog_raises() -> None:
    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        # Clone "succeeds" but writes no catalog.json at the root.
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    with pytest.raises(ec.CatalogError, match=ec.DEFAULT_CATALOG_FILENAME):
        ec.fetch_catalog("git+ssh://host/empty.git", git_runner=git_runner)


# =====================================================================
# === fetch_all — degrade bad source to Catalog(error=...) =============
# =====================================================================


def test_fetch_all_degrades_bad_source(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(_catalog_doc({"name": "ok", "source": "pypi-ok"}), encoding="utf-8")
    bad = str(tmp_path / "missing.json")

    cats = ec.fetch_all([str(good), bad])
    assert len(cats) == 2

    ok_cat = cats[0]
    assert ok_cat.error is None
    assert [e.name for e in ok_cat.entries] == ["ok"]

    bad_cat = cats[1]
    assert bad_cat.location == bad
    assert bad_cat.error is not None
    assert bad_cat.entries == ()
    assert bad_cat.fetched_at is not None


# =====================================================================
# === Cache sidecar — atomic save/load round-trip ======================
# =====================================================================


def test_cache_file_path(tmp_path: Path) -> None:
    assert ec.cache_file_path(tmp_path) == tmp_path / ec.CATALOG_CACHE_FILENAME


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    entry = ec.CatalogEntry(
        name="round",
        source="git+ssh://host/round.git",
        description="round trip",
        version="1.2.3",
        versions=("1.2.3", "1.2.2"),
        sha256="deadbeef",
        homepage="https://example/round",
        catalog_name="Corp",
        extra={"weird": [1, 2]},
    )
    cat = ec.Catalog(
        location="https://intranet/catalog.json",
        name="Corp",
        updated="2026-07-05",
        entries=(entry,),
        fetched_at=ec.now_iso(),
    )
    path = ec.cache_file_path(tmp_path / "agent")
    ec.save_catalogs([cat], path)

    loaded = ec.load_catalogs(path)
    assert len(loaded) == 1
    lc = loaded[0]
    assert lc.location == cat.location
    assert lc.name == "Corp"
    assert lc.updated == "2026-07-05"
    assert lc.fetched_at == cat.fetched_at
    assert len(lc.entries) == 1
    le = lc.entries[0]
    assert le.name == "round"
    assert le.source == "git+ssh://host/round.git"
    assert le.description == "round trip"
    assert le.version == "1.2.3"
    assert le.versions == ("1.2.3", "1.2.2")
    assert le.sha256 == "deadbeef"
    assert le.homepage == "https://example/round"
    assert le.extra["weird"] == [1, 2]


def test_save_is_atomic_leaves_no_tmp(tmp_path: Path) -> None:
    path = ec.cache_file_path(tmp_path / "agent")
    ec.save_catalogs([ec.Catalog(location="loc", entries=())], path)
    assert path.is_file()
    # No leftover ``.extension_catalog_cache.*.tmp`` sidecars after an atomic swap.
    leftovers = list(path.parent.glob(".extension_catalog_cache.*.tmp"))
    assert leftovers == []


def test_save_overwrites_existing(tmp_path: Path) -> None:
    path = ec.cache_file_path(tmp_path / "agent")
    ec.save_catalogs([ec.Catalog(location="first", entries=())], path)
    ec.save_catalogs([ec.Catalog(location="second", entries=())], path)
    loaded = ec.load_catalogs(path)
    assert [c.location for c in loaded] == ["second"]


def test_load_cached_catalog_missing_is_empty(tmp_path: Path) -> None:
    assert ec.load_cached_catalog(tmp_path / "no-such-agent-dir") == []


def test_load_cached_catalog_corrupt_is_empty(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    ec.cache_file_path(agent_dir).write_text("{not valid json", encoding="utf-8")
    assert ec.load_cached_catalog(agent_dir) == []


def test_load_non_dict_root_is_empty(tmp_path: Path) -> None:
    path = ec.cache_file_path(tmp_path)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert ec.load_catalogs(path) == []


def test_load_cached_catalog_round_trip(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    cat = ec.Catalog(
        location="loc",
        name="C",
        entries=(ec.CatalogEntry(name="x", source="pypi-x"),),
        fetched_at=ec.now_iso(),
    )
    ec.save_catalogs([cat], ec.cache_file_path(agent_dir))
    loaded = ec.load_cached_catalog(agent_dir)
    assert [c.location for c in loaded] == ["loc"]
    assert [e.name for e in loaded[0].entries] == ["x"]


# =====================================================================
# === search_entries ===================================================
# =====================================================================


def _sample_catalogs() -> list[ec.Catalog]:
    cat_a = ec.Catalog(
        location="https://a/catalog.json",
        name="Alpha",
        entries=(
            ec.CatalogEntry(name="alpha-lint", source="pypi-alpha-lint", description="a linter", catalog_name="Alpha"),
            ec.CatalogEntry(name="shared", source="pypi-shared-a", description="from alpha", catalog_name="Alpha"),
        ),
    )
    cat_b = ec.Catalog(
        location="https://b/catalog.json",
        name="Beta",
        entries=(
            ec.CatalogEntry(name="beta-fmt", source="git+ssh://b/fmt.git", description="a formatter", catalog_name="Beta"),
            ec.CatalogEntry(name="shared", source="pypi-shared-b", description="from beta", catalog_name="Beta"),
        ),
    )
    return [cat_a, cat_b]


def test_search_none_returns_all() -> None:
    cats = _sample_catalogs()
    assert len(ec.search_entries(cats, None)) == 4


def test_search_empty_returns_all() -> None:
    cats = _sample_catalogs()
    assert len(ec.search_entries(cats, "   ")) == 4


def test_search_substring_on_name() -> None:
    cats = _sample_catalogs()
    hits = ec.search_entries(cats, "FMT")  # case-insensitive
    assert [e.name for e in hits] == ["beta-fmt"]


def test_search_substring_on_description() -> None:
    cats = _sample_catalogs()
    hits = ec.search_entries(cats, "linter")
    assert [e.name for e in hits] == ["alpha-lint"]


def test_search_order_is_registration_then_entry() -> None:
    cats = _sample_catalogs()
    hits = ec.search_entries(cats, "shared")
    # Alpha's shared before Beta's shared (registration + entry order, stable).
    assert [e.source for e in hits] == ["pypi-shared-a", "pypi-shared-b"]


# =====================================================================
# === resolve_entry ====================================================
# =====================================================================


def test_resolve_exact_one() -> None:
    cats = _sample_catalogs()
    resolved, candidates = ec.resolve_entry(cats, "beta-fmt")
    assert resolved is not None
    assert resolved.source == "git+ssh://b/fmt.git"
    assert len(candidates) == 1


def test_resolve_case_insensitive() -> None:
    cats = _sample_catalogs()
    resolved, _ = ec.resolve_entry(cats, "BETA-FMT")
    assert resolved is not None and resolved.name == "beta-fmt"


def test_resolve_ambiguous_refuses_with_candidates() -> None:
    cats = _sample_catalogs()
    resolved, candidates = ec.resolve_entry(cats, "shared")
    assert resolved is None  # never a silent first-match
    assert len(candidates) == 2
    assert {c.source for c in candidates} == {"pypi-shared-a", "pypi-shared-b"}


def test_resolve_catalog_narrows_by_label() -> None:
    cats = _sample_catalogs()
    resolved, candidates = ec.resolve_entry(cats, "shared", catalog="Beta")
    assert resolved is not None
    assert resolved.source == "pypi-shared-b"
    assert len(candidates) == 1


def test_resolve_catalog_narrows_by_location() -> None:
    cats = _sample_catalogs()
    resolved, _ = ec.resolve_entry(cats, "shared", catalog="https://a/catalog.json")
    assert resolved is not None and resolved.source == "pypi-shared-a"


def test_resolve_unknown_name_is_empty() -> None:
    cats = _sample_catalogs()
    resolved, candidates = ec.resolve_entry(cats, "does-not-exist")
    assert resolved is None
    assert candidates == []


# =====================================================================
# === NO-PIN-SEEDING invariant (ADR-0188 owner decision 2) =============
# =====================================================================


def test_module_never_imports_extension_pins() -> None:
    """Static guard: the module must not *import* the #64 pin store.

    Seeding ``extension_pins.json`` from an unauthenticated catalog would let
    ``verify_and_pin`` re-hash attacker bytes, match the attacker-seeded pin, and
    print a false-green "integrity verified". The catalog ``sha256`` is
    display-only; this module must be fully decoupled from the pin store.

    Prose docstrings mention ``extension_pins`` deliberately, so this walks the
    AST and inspects import statements + call targets — not raw substrings.
    """

    import ast

    tree = ast.parse(Path(ec.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)

    assert not any("extension_pins" in name for name in imported)
    # Never calls the pin-store writers (their only legitimate caller is #64).
    assert "save_pins" not in called
    assert "_record_pin" not in called


def test_module_namespace_has_no_pin_writer() -> None:
    # Nothing named after the pin store / its writers leaked into the namespace.
    assert not hasattr(ec, "save_pins")
    assert not hasattr(ec, "_record_pin")
    assert not hasattr(ec, "extension_pins")
    assert "extension_pins" not in vars(ec)


# =====================================================================
# === Security / correctness hardening (new fixes) =====================
# =====================================================================


def test_parse_sanitizes_display_and_skips_control_source() -> None:
    """Untrusted catalog display fields lose control/escape chars; a control char
    in a functional ``source`` skips the whole entry (it can never be a valid spec).

    The document is built with :func:`json.dumps` so the control chars are properly
    ESCAPED on the wire — that is the real vector (they re-materialize on parse).
    """

    doc = _catalog_doc(
        {
            "name": "safe\nEvil:",
            "source": "pypi-safe",
            "description": "ok\x1b[32m verified",
            "versions": ["1.0\n", "2.0"],
            "homepage": "https://x\ny",
        },
        {"name": "evil2", "source": "pkg\n rm"},  # control char in source -> skipped
        name="corp\nFAKE",
    )
    cat = ec.parse_catalog(doc, location="loc")

    # The catalog label (document name) is scrubbed of raw newline/escape chars.
    assert "\n" not in cat.label()
    assert "\x1b" not in cat.label()
    assert cat.label() == "corp FAKE"

    # Only the sanitizable entry survives; the control-char-source entry is dropped.
    assert [e.name for e in cat.entries] == ["safe Evil:"]
    assert "evil2" not in {e.name for e in cat.entries}

    entry = cat.entries[0]
    for value in (entry.name, entry.description, entry.homepage, *entry.versions):
        assert value is not None
        assert "\n" not in value
        assert "\x1b" not in value
        assert ec._CONTROL_RE.search(value) is None
    # Interior newlines collapse to spaces (not silently deleted).
    assert entry.name == "safe Evil:"
    assert entry.homepage == "https://x y"


def test_fetch_git_http_refused_before_clone() -> None:
    """A ``git+http://`` (and bare ``.git``-suffixed plain-http) source is refused
    for TLS BEFORE any clone runs — the injected git runner is never called."""

    calls: list[list[str]] = []

    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    with pytest.raises(ec.CatalogError):
        ec.fetch_catalog("git+http://evil/x.git", git_runner=git_runner)
    assert calls == []  # refusal happened before any clone

    # A bare, ``.git``-suffixed plain-http URL is git-shaped and likewise refused.
    with pytest.raises(ec.CatalogError):
        ec.fetch_catalog("http://evil/x.git", git_runner=git_runner)
    assert calls == []


def test_fetch_all_git_missing_binary_degrades() -> None:
    """A missing ``git`` binary (FileNotFoundError) degrades to an error row —
    ``fetch_all`` must never raise for one bad source."""

    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("git")

    cats = ec.fetch_all(["git+ssh://h/r.git"], git_runner=git_runner)
    assert len(cats) == 1
    assert cats[0].error is not None
    assert cats[0].entries == ()


def test_fetch_all_git_timeout_degrades() -> None:
    """A hung clone (subprocess.TimeoutExpired) degrades the same way as a missing
    binary: an error row with no entries, never an escaping exception."""

    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=60.0)

    cats = ec.fetch_all(["git+ssh://h/r.git"], git_runner=git_runner)
    assert len(cats) == 1
    assert cats[0].error is not None
    assert cats[0].entries == ()


def test_fetch_file_url_remote_host_refused(tmp_path: Path) -> None:
    """A ``file://`` URL with a remote host is refused (would silently target the
    wrong file), while a host-less ``file:///`` URL still works."""

    with pytest.raises(ec.CatalogError, match="not supported"):
        ec.fetch_catalog("file://server/share/c.json")

    f = tmp_path / "catalog.json"
    f.write_text(_catalog_doc({"name": "local", "source": "pypi-local"}), encoding="utf-8")
    cat = ec.fetch_catalog(f.as_uri())  # file:///abs/path/catalog.json
    assert [e.name for e in cat.entries] == ["local"]


def test_https_only_redirect_rejects_http_downgrade() -> None:
    """The redirect handler refuses a cross-scheme downgrade to ``http://`` and
    otherwise returns a normal redirect ``Request`` for an ``https://`` target."""

    import urllib.request

    handler = ec._HttpsOnlyRedirect()
    req = urllib.request.Request("https://a")

    with pytest.raises(ec.CatalogError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://evil/x")

    result = handler.redirect_request(req, None, 302, "Found", {}, "https://ok/x")
    assert isinstance(result, urllib.request.Request)


def test_fetch_local_byte_cap_via_stat(tmp_path: Path) -> None:
    """A local file whose ``st_size`` exceeds the cap is refused at the stat check —
    before any read (guards a huge/hostile document). Sparse file keeps it cheap."""

    big = tmp_path / "big.json"
    with big.open("wb") as f:
        f.seek(ec.MAX_CATALOG_BYTES + 1)
        f.write(b"\0")
    with pytest.raises(ec.CatalogError, match="byte cap"):
        ec.fetch_catalog(str(big))


def test_parse_versions_list_and_homepage() -> None:
    """A ``versions`` array keeps string members in order (non-string/empty dropped)
    and ``homepage`` is carried; ``display_version`` falls back to ``versions[0]``."""

    doc = _catalog_doc(
        {
            "name": "vers",
            "source": "pypi-vers",
            "versions": ["1.2", 3, "", "1.1"],
            "homepage": "https://x",
        }
    )
    cat = ec.parse_catalog(doc, location="loc")
    entry = cat.entries[0]
    assert entry.versions == ("1.2", "1.1")
    assert entry.homepage == "https://x"
    assert entry.version is None
    assert entry.display_version() == "1.2"


def test_fetch_git_symlink_catalog_refused(tmp_path: Path) -> None:
    """A cloned repo whose root ``catalog.json`` is a symlink (to an outside file)
    is refused — a malicious repo must not exfiltrate an arbitrary host file."""

    import os

    secret = tmp_path / "secret.txt"
    secret.write_text(_catalog_doc({"name": "leaked", "source": "pypi-leak"}), encoding="utf-8")

    def git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        dest = Path(argv[-1])  # ["git","clone","--depth","1",url,dest]
        os.symlink(secret, dest / ec.DEFAULT_CATALOG_FILENAME)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    with pytest.raises(ec.CatalogError, match="symlink"):
        ec.fetch_catalog("git+ssh://h/r.git", git_runner=git_runner)
