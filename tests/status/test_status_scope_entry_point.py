"""``classify_scope`` must have a bucket for the ENTRY-POINT tier (#101 review M1).

THE SHAPE THIS COVERS IS THE COMMON ONE. ``aelix extension install`` and every
marketplace pack land as a pip-installed distribution advertising an
``aelix.extensions`` endpoint — the tier ``loader._discover_via_entry_points``
serves. Before this file existed the snapshot reported those packs as scope
``"explicit"`` (a manifest-bound pack, located by its site-packages
``resolved_path``) or ``"unclassified"`` (a manifest-less one, whose
``Extension.name`` is a qualname). ``"explicit"`` means "the user typed
``-e <path>``", which is the opposite of an installed pack, and a tool whose
entire job is reporting the truth about the runtime must not invent a provenance.

EVERY PACK HERE IS A REAL INSTALLED-WHEEL IMAGE — a real ``dist-info`` with a
real ``RECORD``, built by ``install_dist`` and discovered through the real
``discover_and_load_extensions``, reusing ``tests/extensions/test_ep_wiring.py``'s
fixtures rather than a second notion of "installed". A hand-built ``Extension``
would prove only that the classifier reads the field the test just set.
"""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_status.snapshot import (
    SCOPE_ENTRY_POINT,
    classify_scope,
    summarise_extensions,
)

from tests.extensions.test_ep_manifest import install_dist
from tests.extensions.test_ep_wiring import (  # noqa: F401 — fixtures
    EXT_PY,
    MANIFEST,
    isolated,
    markers,
    plugin_toml,
    site,
)


def _install_bound_pack(site_dir: Path) -> None:
    """An installed pack whose manifest is PROVEN from its own RECORD.

    This is the shape ``aelix extension install`` produces, and the one that
    used to report ``"explicit"``: the loader routes it through
    ``_ManifestEntry`` and sets ``resolved_path`` to its site-packages
    directory, which is path-like and inside neither directory tier.
    """

    install_dist(
        site_dir,
        "boundpack",
        entry_points={"aelix.extensions": {"bound-ep": "boundpack.ext:setup"}},
        files={
            "boundpack/__init__.py": "",
            "boundpack/ext.py": EXT_PY.format(name="boundpack"),
            f"boundpack/{MANIFEST}": plugin_toml(
                "boundpack", entry="boundpack.ext:setup"
            ),
        },
    )


def _install_manifestless_pack(site_dir: Path) -> None:
    """An installed pack with NO manifest — the pre-#91 endpoint shape.

    Its ``Extension.name`` is the factory qualname, so it used to be reported
    as ``"unclassified"``: true but useless, and indistinguishable from a
    prepended built-in.
    """

    install_dist(
        site_dir,
        "barepack",
        entry_points={"aelix.extensions": {"bare-ep": "barepack.ext:setup"}},
        files={
            "barepack/__init__.py": "",
            "barepack/ext.py": EXT_PY.format(name="barepack"),
        },
    )


async def test_a_manifest_bound_installed_pack_is_scoped_entry_point(
    site: Path,  # noqa: F811
    markers: Path,  # noqa: F811, ARG001 — ordering only
    isolated: dict[str, Path],  # noqa: F811
) -> None:
    _install_bound_pack(site)

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    (ext,) = result.extensions
    assert ext.name == "boundpack"
    # The field that used to say "explicit": a site-packages path.
    assert ext.resolved_path is not None
    assert str(site) in ext.resolved_path

    scope = classify_scope(
        ext, cwd=str(isolated["cwd"]), agent_dir=str(isolated["agent_dir"])
    )
    assert scope == SCOPE_ENTRY_POINT


async def test_a_manifest_less_installed_pack_is_scoped_entry_point(
    site: Path,  # noqa: F811
    markers: Path,  # noqa: F811, ARG001 — ordering only
    isolated: dict[str, Path],  # noqa: F811
) -> None:
    _install_manifestless_pack(site)

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    (ext,) = result.extensions
    # A qualname, not a path — which is why the name cannot carry the answer.
    assert ext.name == "setup"
    assert ext.resolved_path is None

    scope = classify_scope(
        ext, cwd=str(isolated["cwd"]), agent_dir=str(isolated["agent_dir"])
    )
    assert scope == SCOPE_ENTRY_POINT


async def test_the_snapshot_projection_reports_the_tier(
    site: Path,  # noqa: F811
    markers: Path,  # noqa: F811, ARG001 — ordering only
    isolated: dict[str, Path],  # noqa: F811
) -> None:
    """End to end through the projection the tool actually emits."""

    _install_bound_pack(site)
    _install_manifestless_pack(site)

    result = await discover_and_load_extensions([], **isolated)
    assert [e.error for e in result.errors] == []

    summarised = summarise_extensions(
        result.extensions,
        cwd=str(isolated["cwd"]),
        agent_dir=str(isolated["agent_dir"]),
    )
    assert {s.scope for s in summarised} == {SCOPE_ENTRY_POINT}
    assert {s.name for s in summarised} == {"boundpack", "setup"}


async def test_the_loader_records_the_tier_rather_than_the_classifier_guessing(
    site: Path,  # noqa: F811
    markers: Path,  # noqa: F811, ARG001 — ordering only
    isolated: dict[str, Path],  # noqa: F811
) -> None:
    """Where the label comes from, pinned.

    ``classify_scope`` reads ``Extension.source_info.source``. If the loader
    stopped writing it, the two tests above would silently fall back to the
    path heuristic and report ``"explicit"`` for the bound pack again — so the
    writer is asserted directly, not only through its effect.
    """

    _install_bound_pack(site)
    _install_manifestless_pack(site)

    result = await discover_and_load_extensions([], **isolated)
    assert [e.error for e in result.errors] == []
    assert {
        (e.name, e.source_info.source if e.source_info else None)
        for e in result.extensions
    } == {("boundpack", "entry_points"), ("setup", "entry_points")}
