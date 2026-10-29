"""Build the next hosted catalog revision, check it, and say what it would change.

#172, ADR-0264 §4.2. aelix publishes its own corrected catalog, without
transport, from a scheduled job in ``handochan/aelix-catalog``. That job holds
no aelix logic of its own: its ``build`` job checks out aelix-ai at the newest
commit ``main``'s CI passed and runs this script, which

1. fetches models.dev's ``api.json`` and OpenRouter's public ``/api/v1/models``
   (recording each body's sha256 and the fetch time for the index entry);
2. runs ``refresh_catalog.py``'s additive plan over the COMMITTED catalog, each
   new reasoning row's thinking map derived (§7 Q2), and applies it to the
   checkout. The ``off: null`` the plan finds for shipped rows (#507,
   ADR-0264 §10, 2026-10-26), and the levels under it a model refuses (#397), are
   reported, not applied: a shipped row publishes as ``main`` has it, and the
   fold-back commits the fill;
3. computes the lineage from ``main``'s first-parent history and writes the
   revision with :mod:`aelix_ai.hosted_catalog`'s one serializer;
4. validates, and publishes nothing on any failure: the pin, overlay and
   contract tests against the applied checkout; additivity; no transport field
   in any row; every row through the client's parser, with every new row
   inheriting the generator's transport through the client's ladder; the size
   and row caps; the provider set; the additions cap; the lineage's head;
5. stops when the index already names this revision (nothing time-dependent is
   hashed, so a slot with nothing new commits nothing);
6. writes the revision, the next index and a job summary to ``--out``.

The catalog repository's ``publish`` job then takes ONLY
``v1/revisions/<name>.json`` and ``v1/index.json`` from that directory, checks
them with its own stdlib code and commits them; it runs nothing from here.

Usage::

    python scripts/publish_catalog.py build --fetch --catalog-repo ../aelix-catalog --out out/
    python scripts/publish_catalog.py build --models-dev api.json --openrouter models.json --out out/
    python scripts/publish_catalog.py lineage              # print main's catalog lineage

Exit status: 0 when the revision was written, is already current, or the
catalog repository is held (``result.json``'s ``state`` says ``publish``,
``unchanged`` or ``held``), 1 when a check failed (``state`` is ``failed`` and
the summary lists why; nothing else is written), 2 on bad usage (argparse's
own, and a ``build`` with neither ``--fetch`` nor both upstream files), which
writes nothing, ``result.json`` included. An exception no check expected (a
fetch that fails) ends in Python's traceback and exit status 1 with no
``result.json``; nothing is published either way.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refresh_catalog as rc  # noqa: E402
from aelix_ai import hosted_catalog as hc  # noqa: E402
from aelix_ai.catalog_inherit import transport_of  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_REL = "packages/aelix-ai/src/aelix_ai/models_generated.json"
MODELS_PY_REL = "packages/aelix-ai/src/aelix_ai/models.py"
HOLD_REL = "control/hold.json"

MINIMUM_AELIX_VERSION = "0.1.0b2"
"""The index entry's floor (ADR-0264 §4.2): every checkout reports ``0.1.0b2`` until R's
release commit, and the released ``0.1.0b2`` wheel has no client, so this admits only
builds that carry one. Raise it, as pi raises ``minimumPiVersion``, only when published
values need client behaviour older clients lack. A thinking level an older client does not
know does not raise it (§10 2026-10-30, #617): that client drops a new id whose map names
the level and keeps its shipped map for a known id. ``release_report`` never lists a kept map."""

DEFAULT_MAX_ADDITIONS = 100
"""At most this many ids new since the previous published revision (or ``main``'s catalog
for the first one); a dispatch's ``max_additions`` raises it for one run (§4.2 step 3)."""

KEEP_PUBLISHED_TESTS: tuple[str, ...] = (
    "tests/test_catalog_corrections_are_pinned.py",
    "tests/test_catalog_overlay.py",
    "tests/test_hosted_catalog_contract.py",
)
"""Run against the applied checkout, so no revision carries a value the pins reject."""

Catalog = dict[str, dict[str, dict[str, Any]]]


class BuildFailed(Exception):
    """A check failed; nothing is published. ``failures`` lists every one that did."""

    def __init__(self, failures: list[str], summary: str = "") -> None:
        super().__init__("; ".join(failures))
        self.failures = failures
        self.summary = summary


# ── git ────────────────────────────────────────────────────────────────


def git(repo: Path, *args: str) -> bytes:
    """``git -C repo args`` stdout; raises with git's stderr on failure."""

    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise BuildFailed([f"git {' '.join(args)}: {message}"])
    return proc.stdout


def catalog_at(repo: Path, ref: str) -> Catalog:
    """The catalog as committed at ``ref``."""

    return json.loads(git(repo, "show", f"{ref}:{CATALOG_REL}").decode("utf-8"))


def catalog_lineage(repo: Path, ref: str) -> list[str]:
    """The digest of every version the catalog had on ``ref``'s first-parent history, oldest first.

    ADR-0264 §4.2, the ordering rule. Consecutive commits that leave the
    catalog's content unchanged are one version; a revert (A, B, A) repeats a
    digest, as the ADR records. At most the newest :data:`hc.MAX_LINEAGE`.
    Refuses a shallow clone, whose history would silently cut the lineage.
    """

    if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise BuildFailed(["the checkout is shallow; the lineage needs full history"])
    log = git(repo, "log", "--first-parent", "--reverse", "--format=%H", ref, "--", CATALOG_REL)
    digests: list[str] = []
    for sha in log.decode("ascii").split():
        digest = hc.catalog_digest(catalog_at(repo, sha))
        if not digests or digests[-1] != digest:
            digests.append(digest)
    return digests[-hc.MAX_LINEAGE :]


def latest_release_tag(repo: Path, ref: str) -> str | None:
    """The newest ``v*`` tag reachable from ``ref``, or ``None``."""

    try:
        tag = git(repo, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", ref)
    except BuildFailed:
        return None
    return tag.decode("utf-8").strip() or None


def levels_at(repo: Path, ref: str) -> list[str] | None:
    """``EXTENDED_THINKING_LEVELS`` as ``aelix_ai/models.py`` spelled it at ``ref``, or ``None``.

    Read with ``ast`` from the source, never imported: the summary asks what an
    older release's parser would accept, and that release's levels are part of
    its parser (a map naming ``max`` is a field a pre-#433 build rejects).
    """

    try:
        source = git(repo, "show", f"{ref}:{MODELS_PY_REL}").decode("utf-8")
    except BuildFailed:
        return None
    for node in ast.walk(ast.parse(source)):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target, value = (first.id if isinstance(first, ast.Name) else None), node.value
        else:
            continue
        if target == "EXTENDED_THINKING_LEVELS" and value is not None:
            try:
                levels = ast.literal_eval(value)
            except ValueError:
                return None
            if isinstance(levels, list) and all(isinstance(level, str) for level in levels):
                return levels
    return None


# ── upstream ───────────────────────────────────────────────────────────


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_UPSTREAM_USAGE = "build needs --fetch, or both --models-dev and --openrouter"


def load_upstream(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """models.dev's body, OpenRouter's body, and the index entry's ``upstream`` record."""

    if args.fetch:
        fetched_at = _now()
        dev_raw, dev = rc.fetch_json(rc.UPSTREAM_URL)
        or_raw, listing = rc.fetch_json(rc.OPENROUTER_URL)
    else:
        if not (args.models_dev and args.openrouter):
            # main() refuses this as bad usage (exit 2) before a build starts.
            raise ValueError(_UPSTREAM_USAGE)
        dev_path, or_path = Path(args.models_dev), Path(args.openrouter)
        dev_raw, or_raw = dev_path.read_bytes(), or_path.read_bytes()
        dev, listing = json.loads(dev_raw), json.loads(or_raw)
        stamp = max(dev_path.stat().st_mtime, or_path.stat().st_mtime)
        fetched_at = args.fetched_at or (
            _dt.datetime.fromtimestamp(stamp, _dt.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    record = {
        "modelsDevSha256": hashlib.sha256(dev_raw).hexdigest(),
        "openRouterSha256": hashlib.sha256(or_raw).hexdigest(),
        "fetchedAt": fetched_at,
    }
    if not isinstance(dev, dict) or not isinstance(listing, dict):
        raise BuildFailed(["models.dev or OpenRouter did not return a JSON object"])
    return dev, listing, record


def ladder_inputs_for(
    catalog: Catalog, upstream: dict[str, Any]
) -> dict[str, dict[str, dict[str, str]]]:
    """Every row's ``family`` and ``releaseDate``, where models.dev lists the id for that provider.

    Exactly what the generator's ladder reads (``catalog_inherit``'s module
    docstring), so the client asks the generator's question with its inputs.
    """

    out: dict[str, dict[str, dict[str, str]]] = {}
    for provider, rows in catalog.items():
        upstream_models = rc.upstream_models_for(provider, upstream)
        for model_id in rows:
            listed = upstream_models.get(model_id)
            if listed is None:
                continue
            inputs: dict[str, str] = {}
            family = listed.get("family")
            if isinstance(family, str) and family:
                inputs["family"] = family.lower()
            release = rc.released(model_id, upstream_models)
            if release:
                inputs["releaseDate"] = release
            if inputs:
                out.setdefault(provider, {})[model_id] = inputs
    return out


# ── the checks (§4.2 step 3) ───────────────────────────────────────────


def _facts(row: Mapping[str, Any]) -> dict[str, Any]:
    return {name: row[name] for name in hc.FACT_FIELDS if name in row}


def check_additive(committed: Catalog, applied: Catalog) -> list[str]:
    """Every committed row is present with identical content, nothing dropped, no provider added."""

    problems: list[str] = []
    if set(applied) != set(committed):
        problems.append(f"additivity: providers changed ({sorted(set(applied) ^ set(committed))})")
    for provider, rows in committed.items():
        for model_id, row in rows.items():
            if model_id not in applied.get(provider, {}):
                problems.append(f"additivity: {provider}/{model_id} dropped")
            elif applied[provider][model_id] != row:
                problems.append(f"additivity: {provider}/{model_id} changed")
        if list(applied.get(provider, {}))[: len(rows)] != list(rows):
            problems.append(f"additivity: {provider} rows moved")
    return problems


def check_no_transport(revision: Mapping[str, Any]) -> list[str]:
    """No published row carries ``api``, ``baseUrl``, ``headers``, ``compat`` or ``provider``."""

    problems: list[str] = []
    for provider, rows in revision["providers"].items():
        for row in rows:
            carried = [name for name in hc.TRANSPORT_FIELDS if name in row]
            if carried:
                problems.append(f"no-transport: {provider}/{row.get('id')} carries {carried}")
    return problems


def _expected(row: Mapping[str, Any]) -> dict[str, Any]:
    """A row as the client's parser leaves it: the output cap clamped to the window (§4.3)."""

    out = dict(row)
    out["maxTokens"] = min(out["maxTokens"], out["contextWindow"])
    return out


def check_client_parse(
    raw: bytes, committed: Catalog, applied: Catalog, *, levels: Sequence[str] | None = None
) -> list[str]:
    """The client's parser takes every row as written, and every new row inherits the generator's transport.

    Parsed over the COMMITTED catalog, the way a client on the lineage's last
    catalog parses it: every row's effective facts must equal the applied
    row's (a field the client keeps from its shipped row passes only when the
    two are the same value), no row may be dropped, and every new row's
    inherited transport must be the one the generator wrote.
    """

    try:
        parsed = hc.parse_revision(json.loads(raw.decode("utf-8")), committed, levels=levels)
    except hc.RevisionRefused as exc:
        return [f"client-parse: the revision is refused ({exc})"]
    problems = [
        f"client-parse: {note.provider}/{note.model_id} dropped ({note.field}: {note.reason})"
        for note in parsed.dropped
    ]
    # A FACT the client keeps from its shipped row is no failure on its own:
    # the facts comparison below fails it when the value it keeps differs from
    # what the generator wrote. ``openrouter/auto`` is the case: its shipped
    # cost is OpenRouter's negative "price varies" sentinel, outside the
    # parser's 0..10000, so every client keeps exactly the shipped value. A
    # ladder input the parser rejects is a failure, loudly (§4.2 step 3): the
    # client would compare new ids against a sibling without it.
    problems += [
        f"client-parse: {note.provider}/{note.model_id} {note.field} rejected ({note.reason})"
        for note in parsed.kept
        if note.field in hc.LADDER_FIELDS
    ]
    if parsed.ignored_providers:
        problems.append(f"client-parse: providers ignored {list(parsed.ignored_providers)}")
    for provider, rows in applied.items():
        for model_id, row in rows.items():
            effective = parsed.rows.get(provider, {}).get(model_id)
            if effective is None:
                problems.append(f"client-parse: {provider}/{model_id} missing after parse")
                continue
            is_new = model_id not in committed.get(provider, {})
            if is_new and transport_of(effective) != transport_of(row):
                problems.append(
                    f"inheritance: {provider}/{model_id} inherits another transport "
                    "on the client than the generator wrote"
                )
            if _facts(effective) != _facts(_expected(row)):
                problems.append(f"client-parse: {provider}/{model_id} facts differ after parse")
    return problems


def check_caps(raw: bytes, revision: Mapping[str, Any]) -> list[str]:
    """At most 16 MiB, 20,000 rows and 5,000 rows for one provider."""

    problems: list[str] = []
    if len(raw) > hc.MAX_REVISION_BYTES:
        problems.append(f"caps: {len(raw)} bytes, over {hc.MAX_REVISION_BYTES}")
    providers = revision["providers"]
    total = sum(len(rows) for rows in providers.values())
    if total > hc.MAX_ROWS:
        problems.append(f"caps: {total} rows, over {hc.MAX_ROWS}")
    for provider, rows in providers.items():
        if len(rows) > hc.MAX_PROVIDER_ROWS:
            problems.append(f"caps: {provider} has {len(rows)} rows, over {hc.MAX_PROVIDER_ROWS}")
    return problems


def check_providers(revision: Mapping[str, Any], committed: Catalog) -> list[str]:
    """Every committed provider is present, and no other: a revision cannot add a provider."""

    published = set(revision["providers"])
    problems = [f"providers: {p} missing" for p in sorted(set(committed) - published)]
    problems += [
        f"providers: {p} is not a catalogued provider" for p in sorted(published - set(committed))
    ]
    return problems


def new_ids(revision: Mapping[str, Any], previous: Mapping[str, set[str]]) -> dict[str, list[str]]:
    """Ids the revision lists that ``previous`` (provider to ids) does not."""

    out: dict[str, list[str]] = {}
    for provider, rows in revision["providers"].items():
        before = previous.get(provider, set())
        fresh = [row["id"] for row in rows if row["id"] not in before]
        if fresh:
            out[provider] = fresh
    return out


def check_additions_cap(fresh: Mapping[str, list[str]], cap: int) -> list[str]:
    """At most ``cap`` ids new since the previous published revision (or ``main``'s catalog)."""

    count = sum(len(ids) for ids in fresh.values())
    if count > cap:
        return [
            f"additions-cap: {count} ids are new since the previous publication, over {cap} "
            "(a dispatch's max_additions raises it for one run)"
        ]
    return []


def check_lineage_head(lineage: Sequence[str], committed: Catalog) -> list[str]:
    """The lineage's last digest is the committed catalog's at the source commit."""

    digest = hc.catalog_digest(committed)
    if not lineage or lineage[-1] != digest:
        last = lineage[-1] if lineage else None
        return [f"lineage-head: ends in {last}, the committed catalog is {digest}"]
    return []


def run_pinned_tests(repo: Path) -> list[str]:
    """The pin, overlay and contract tests against the applied checkout."""

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *KEEP_PUBLISHED_TESTS],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0:
        return []
    tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
    return [f"tests: the pin, overlay or contract test failed on the applied checkout:\n{tail}"]


# ── the build ──────────────────────────────────────────────────────────


def previous_ids(
    catalog_repo: Path | None, index: Mapping[str, Any] | None
) -> dict[str, set[str]] | None:
    """The ids of the revision the current index names for this floor, or ``None`` for the first."""

    if catalog_repo is None or index is None:
        return None
    for entry in index.get("catalogs") or []:
        if entry.get("minimumAelixVersion") == MINIMUM_AELIX_VERSION:
            path = catalog_repo / "v1" / "revisions" / f"{entry['revision']}.json"
            if not path.is_file():
                raise BuildFailed(
                    [f"the index names {entry['revision']}, which is not in the repository"]
                )
            document = json.loads(path.read_bytes())
            return {
                provider: {row["id"] for row in rows}
                for provider, rows in document["providers"].items()
            }
    return None


def next_index(previous: Mapping[str, Any] | None, entry: Mapping[str, Any]) -> dict[str, Any]:
    """The index this publication writes: ``entry`` replaces the one for its floor.

    A publication never carries ``hold``: one runs only while no hold exists,
    and its index is the first after a ``release-hold``.
    """

    from packaging.version import Version

    kept = [
        dict(other)
        for other in (previous or {}).get("catalogs") or []
        if other.get("minimumAelixVersion") != entry["minimumAelixVersion"]
    ]
    catalogs = sorted([*kept, dict(entry)], key=lambda e: Version(e["minimumAelixVersion"]))
    return {"schema": hc.INDEX_SCHEMA, "catalogs": catalogs}


def typed_string_movers(additions: Mapping[str, Mapping[str, Any]], applied: Catalog) -> list[str]:
    """New rows that can move a typed string (§4.4, §7 Q4).

    An id whose first ``/``-segment names another catalogued provider, and an
    id another provider also lists.
    """

    lines: list[str] = []
    for provider, rows in sorted(additions.items()):
        for model_id in sorted(rows):
            reasons = []
            head = model_id.split("/", 1)[0] if "/" in model_id else None
            if head and head in applied and head != provider:
                reasons.append(f"names provider {head}")
            others = sorted(
                q for q, q_rows in applied.items() if q != provider and model_id in q_rows
            )
            if others:
                reasons.append(f"also listed by {', '.join(others)}")
            if reasons:
                lines.append(f"{provider}/{model_id}: {'; '.join(reasons)}")
    return lines


def release_report(
    raw: bytes,
    committed: Catalog,
    release_catalog: Catalog,
    release_levels: Sequence[str] | None,
) -> list[str]:
    """What the latest release's client would make of this revision (§4.2 step 6)."""

    lines: list[str] = []
    try:
        parsed = hc.parse_revision(
            json.loads(raw.decode("utf-8")), release_catalog, levels=release_levels
        )
    except hc.RevisionRefused as exc:
        return [f"- its whole-revision bounds would REFUSE this revision: {exc}"]
    lines.append("- its whole-revision bounds accept this revision")
    lines.append(
        f"- rows it would add: {parsed.added_count}; rows it would drop: {len(parsed.dropped)}"
    )
    for note in parsed.no_transport:
        lines.append(f"  - no transport: {note.provider}/{note.model_id} ({note.reason})")
    for note in parsed.dropped:
        if note.field != "transport":
            lines.append(
                f"  - dropped: {note.provider}/{note.model_id} ({note.field}: {note.reason})"
            )
    differ = []
    for provider, rows in committed.items():
        for model_id, row in rows.items():
            if model_id in release_catalog.get(provider, {}):
                continue
            inherited = parsed.added.get(provider, {}).get(model_id)
            if inherited is not None and transport_of(inherited) != transport_of(row):
                differ.append(f"{provider}/{model_id}")
    lines.append(
        f"- rows main committed after it whose inherited transport differs from main's: {len(differ)}"
    )
    lines += [f"  - {name}" for name in differ]
    return lines


def build_publication(
    *,
    committed: Catalog,
    lineage: Sequence[str],
    upstream: dict[str, Any],
    openrouter_listing: dict[str, Any],
    upstream_record: Mapping[str, str],
    source_commit: str,
    previous_index: Mapping[str, Any] | None = None,
    previous: Mapping[str, set[str]] | None = None,
    max_additions: int = DEFAULT_MAX_ADDITIONS,
    published_at: str | None = None,
    release: tuple[str, Catalog, Sequence[str] | None] | None = None,
    levels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Steps 2-6 over loaded inputs: the revision, the next index, the summary, or :class:`BuildFailed`.

    Pure apart from the inputs: it writes nothing and runs no test (the CLI
    does both). Returns ``{"state", "applied", "revision", "raw", "name",
    "index", "summary"}``; ``state`` is ``"publish"`` or ``"unchanged"``.
    """

    planned = rc.plan_catalog(committed, upstream, rc.openrouter_by_id(openrouter_listing))
    applied = rc.merge(committed, planned.additions)
    inputs = ladder_inputs_for(applied, upstream)
    revision = hc.build_revision(applied, lineage, inputs)
    raw = hc.serialize_revision(revision)
    name = hc.revision_name(raw)
    fresh = new_ids(
        revision, previous if previous is not None else {p: set(r) for p, r in committed.items()}
    )

    failures: list[str] = []
    failures += check_additive(committed, applied)
    failures += check_no_transport(revision)
    failures += check_client_parse(raw, committed, applied, levels=levels)
    failures += check_caps(raw, revision)
    failures += check_providers(revision, committed)
    failures += check_additions_cap(fresh, max_additions)
    failures += check_lineage_head(lineage, committed)

    rows = sum(len(r) for r in revision["providers"].values())
    entry = {
        "minimumAelixVersion": MINIMUM_AELIX_VERSION,
        "revision": name,
        "publishedAt": published_at or _now(),
        "sourceCommit": source_commit,
        "upstream": dict(upstream_record),
        "rows": rows,
        "newRows": sum(len(ids) for ids in fresh.values()),
    }
    index = next_index(previous_index, entry)
    try:
        hc.parse_index(index)
    except hc.IndexInvalid as exc:
        failures.append(f"index: {exc}")

    current = None
    for other in (previous_index or {}).get("catalogs") or []:
        if other.get("minimumAelixVersion") == MINIMUM_AELIX_VERSION:
            current = other.get("revision")
    unchanged = current == name and "hold" not in (previous_index or {})

    summary = _summary(
        planned=planned,
        applied=applied,
        committed=committed,
        name=name,
        raw=raw,
        entry=entry,
        fresh=fresh,
        unchanged=unchanged,
        release=release,
        failures=failures,
    )
    if failures:
        raise BuildFailed(failures, summary)
    return {
        "state": "unchanged" if unchanged else "publish",
        "applied": applied,
        "revision": revision,
        "raw": raw,
        "name": name,
        "index": index,
        "summary": summary,
    }


def _summary(
    *,
    planned: Any,
    applied: Catalog,
    committed: Catalog,
    name: str,
    raw: bytes,
    entry: Mapping[str, Any],
    fresh: Mapping[str, list[str]],
    unchanged: bool,
    release: tuple[str, Catalog, Sequence[str] | None] | None,
    failures: list[str],
) -> str:
    lines = ["# Model catalog build", ""]
    if failures:
        lines += ["**FAILED - nothing is published.**", ""]
        lines += [f"- {failure}" for failure in failures]
        lines.append("")
    elif unchanged:
        lines += [f"**Unchanged**: the index already names `{name}`. Nothing to publish.", ""]
    lines += [
        f"- revision: `{name}` ({len(raw):,} bytes; {entry['rows']:,} rows)",
        f"- source commit: `{entry['sourceCommit']}`; lineage: {len(json.loads(raw)['lineage'])} digests",
        f"- upstream: models.dev `{entry['upstream']['modelsDevSha256'][:16]}`, "
        f"OpenRouter `{entry['upstream']['openRouterSha256'][:16]}`, fetched {entry['upstream']['fetchedAt']}",
        f"- ids new since the previous publication: {entry['newRows']}",
        "",
        "## Rows added over main's catalog, per provider",
        "",
    ]
    for provider, rows in sorted(planned.additions.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"- {provider}: {len(rows)}")
    if not planned.additions:
        lines.append("- none")
    lines += ["", "## Thinking map of each new reasoning row (ADR-0264 7 Q2)", ""]
    reasoning = sorted(
        (provider, model_id, source)
        for (provider, model_id), source in planned.map_sources.items()
        if source != "not reasoning"
    )
    lines += [f"- {provider}/{model_id}: {source}" for provider, model_id, source in reasoning]
    if not reasoning:
        lines.append("- none")
    lines += [
        "",
        "## Shipped rows' off, and the levels under it, upstream says a model refuses (#507, #397; not published)",
        "",
    ]
    lines += [f"- {p}/{m}: off: null, from {source}" for p, m, source in planned.off_fills]
    lines += [
        f"- {p}/{m}: its off string contradicts {source}; fix by hand"
        for p, m, source in planned.off_conflicts
    ]
    lines += [f"- {p}/{m}: {lvl}: null, from {source}" for p, m, lvl, source in planned.floor_fills]
    lines += [f"- {p}/{m}: {lvl}, {why}; fix by hand" for p, m, lvl, why in planned.floor_conflicts]
    if not (
        planned.off_fills or planned.off_conflicts or planned.floor_fills or planned.floor_conflicts
    ):
        lines.append("- none")
    lines += [
        "",
        "## Fields every client keeps from its shipped row (same value; ADR-0264 4.3)",
        "",
    ]
    try:
        kept = hc.parse_revision(json.loads(raw.decode("utf-8")), committed).kept
    except hc.RevisionRefused:
        kept = []
    lines += [f"- {n.provider}/{n.model_id} {n.field}: {n.reason}" for n in kept] or ["- none"]
    lines += ["", "## New rows that can move a typed string (ADR-0264 4.4)", ""]
    movers = typed_string_movers(planned.additions, applied)
    lines += [f"- {line}" for line in movers] or ["- none"]
    lines += ["", "## Against the latest release", ""]
    if release is None:
        lines.append("- no release tag to compare with")
    else:
        tag, release_catalog, release_levels = release
        lines.append(f"Release `{tag}` ({sum(len(r) for r in release_catalog.values()):,} rows):")
        lines += release_report(raw, committed, release_catalog, release_levels)
    lines.append("")
    return "\n".join(lines)


# ── cli ────────────────────────────────────────────────────────────────


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def cmd_build(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    catalog_repo = Path(args.catalog_repo).resolve() if args.catalog_repo else None

    hold = catalog_repo / HOLD_REL if catalog_repo else None
    if hold is not None and hold.is_file():
        # §4.2 The hold: a held repository builds nothing. The catalog
        # repository's own workflow stops first; this is the same rule again,
        # so a build started by hand cannot walk past it either.
        summary = (
            "# Model catalog build\n\n**Held** - `control/hold.json` exists, so nothing is "
            "built or published until `release-hold`.\n\n```json\n"
            + hold.read_text(encoding="utf-8", errors="replace")[:4000]
            + "\n```\n"
        )
        _write(out / "summary.md", summary.encode("utf-8"))
        _write(out / "result.json", json.dumps({"state": "held", "failures": []}).encode("utf-8"))
        print(summary)
        return 0

    result: dict[str, Any] = {"state": "failed", "failures": []}
    summary = ""
    catalog_file = repo / CATALOG_REL
    original = catalog_file.read_bytes()
    try:
        source = args.source_commit or git(repo, "rev-parse", "HEAD").decode("ascii").strip()
        committed = catalog_at(repo, source)
        if json.loads(original) != committed:
            raise BuildFailed(["the checkout's catalog is not the source commit's committed one"])
        lineage = catalog_lineage(repo, source)
        upstream, listing, record = load_upstream(args)
        index_path = catalog_repo / "v1" / "index.json" if catalog_repo else None
        previous_index = (
            json.loads(index_path.read_bytes()) if index_path and index_path.is_file() else None
        )
        tag = args.release_ref or latest_release_tag(repo, source)
        release = (tag, catalog_at(repo, tag), levels_at(repo, tag)) if tag else None
        built = build_publication(
            committed=committed,
            lineage=lineage,
            upstream=upstream,
            openrouter_listing=listing,
            upstream_record=record,
            source_commit=source,
            previous_index=previous_index,
            previous=previous_ids(catalog_repo, previous_index),
            max_additions=args.max_additions,
            published_at=args.published_at,
            release=release,
        )
        summary = built["summary"]
        if built["state"] == "publish" and not args.no_tests:
            rc.write_catalog(built["applied"], catalog_file)
            failures = run_pinned_tests(repo)
            if failures:
                raise BuildFailed(failures)
        result = {
            "state": built["state"],
            "revision": built["name"],
            "rows": built["index"]["catalogs"][-1]["rows"] if built["index"]["catalogs"] else 0,
            "failures": [],
        }
        if built["state"] == "publish":
            _write(out / "v1" / "revisions" / f"{built['name']}.json", built["raw"])
            _write(out / "v1" / "index.json", hc.canonical_bytes(built["index"]))
        status = 0
    except BuildFailed as exc:
        result = {"state": "failed", "failures": exc.failures}
        summary = exc.summary or (
            "# Model catalog build\n\n**FAILED - nothing is published.**\n\n"
            + "\n".join(f"- {failure}" for failure in exc.failures)
            + "\n"
        )
        status = 1
    finally:
        if not args.leave_applied:
            catalog_file.write_bytes(original)
    _write(out / "summary.md", summary.encode("utf-8"))
    _write(out / "result.json", json.dumps(result, indent=2).encode("utf-8"))
    print(summary)
    return status


def cmd_lineage(args: argparse.Namespace) -> int:
    for digest in catalog_lineage(Path(args.repo).resolve(), args.ref):
        print(digest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build and check the next revision")
    build.add_argument("--fetch", action="store_true", help="download models.dev and OpenRouter")
    build.add_argument("--models-dev", help="a models.dev api.json on disk")
    build.add_argument("--openrouter", help="OpenRouter's /api/v1/models body on disk")
    build.add_argument("--fetched-at", help="the fetch time to record for files on disk")
    build.add_argument("--out", required=True, help="where the revision, index and summary go")
    build.add_argument("--repo", default=str(REPO_ROOT), help="the aelix-ai checkout")
    build.add_argument("--catalog-repo", help="the aelix-catalog checkout (its current index)")
    build.add_argument("--source-commit", help="the commit to build from (default HEAD)")
    build.add_argument("--max-additions", type=int, default=DEFAULT_MAX_ADDITIONS)
    build.add_argument("--release-ref", help="the release to compare with (default: newest v* tag)")
    build.add_argument("--published-at", help="the index entry's publishedAt (default now)")
    build.add_argument(
        "--no-tests", action="store_true", help="skip the pin/overlay/contract tests"
    )
    build.add_argument(
        "--leave-applied", action="store_true", help="leave the applied catalog in the checkout"
    )
    build.set_defaults(func=cmd_build)

    lineage = sub.add_parser("lineage", help="print the catalog lineage of a ref")
    lineage.add_argument("--repo", default=str(REPO_ROOT))
    lineage.add_argument("--ref", default="HEAD")
    lineage.set_defaults(func=cmd_lineage)

    args = parser.parse_args(argv)
    if args.command == "build" and not args.fetch and not (args.models_dev and args.openrouter):
        build.error(_UPSTREAM_USAGE)  # exit status 2, before anything is written
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
