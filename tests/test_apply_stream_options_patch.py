"""Sprint 6a (Phase 4.1, P-41) — ``applyStreamOptionsPatch`` deep-merge tests.

Pi parity: ``packages/agent/src/harness/agent-harness.ts:89-129`` at SHA
``734e08e``. Verifies the verbatim port of Pi's delete-on-undefined deep
merge with empty-result collapse-to-``None`` semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_agent_core.harness.hooks import _apply_stream_options_patch

_FIXTURE = (
    Path(__file__).parent
    / "pi_parity"
    / "fixtures"
    / "pi_apply_stream_options_patch_734e08e.json"
)


def test_fixture_pi_sha_pin() -> None:
    fx = json.loads(_FIXTURE.read_text())
    assert fx["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def _run_case(case: dict) -> object:
    return _apply_stream_options_patch(dict(case["base"]), case["patch"])


def test_scalar_overwrite_transport() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "scalar_overwrite_transport")
    assert _run_case(case) == case["expected"]


def test_scalar_clear_with_null() -> None:
    """Scalar ``None`` is an explicit overwrite, not a delete (Pi parity)."""

    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "scalar_clear_with_null")
    assert _run_case(case) == case["expected"]


def test_scalar_add_timeout() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "scalar_add_timeout")
    assert _run_case(case) == case["expected"]


def test_scalar_add_max_retries_and_delay() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(
        c for c in fx["cases"] if c["name"] == "scalar_add_max_retries_and_delay"
    )
    assert _run_case(case) == case["expected"]


def test_headers_merge_adds_key() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "headers_merge_adds_key")
    assert _run_case(case) == case["expected"]


def test_headers_merge_overwrites_key() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "headers_merge_overwrites_key")
    assert _run_case(case) == case["expected"]


def test_headers_delete_with_null_value() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "headers_delete_with_null_value")
    assert _run_case(case) == case["expected"]


def test_headers_clear_with_null() -> None:
    """Top-level ``headers: None`` deletes the headers key entirely."""

    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "headers_clear_with_null")
    assert _run_case(case) == case["expected"]


def test_headers_emptied_collapses_to_none() -> None:
    """Headers reduced to empty dict drop the key entirely (Pi line 111)."""

    fx = json.loads(_FIXTURE.read_text())
    case = next(
        c for c in fx["cases"] if c["name"] == "headers_emptied_collapses_to_none"
    )
    assert _run_case(case) == case["expected"]


def test_metadata_delete_with_null() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "metadata_delete_with_null")
    assert _run_case(case) == case["expected"]


def test_cache_retention_overwrite() -> None:
    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "cache_retention_overwrite")
    assert _run_case(case) == case["expected"]


def test_no_patch_returns_base_or_none() -> None:
    """``patch is None`` with empty ``base`` collapses to ``None``."""

    fx = json.loads(_FIXTURE.read_text())
    case = next(c for c in fx["cases"] if c["name"] == "no_patch_returns_base_or_none")
    assert _run_case(case) == case["expected"]


@pytest.mark.parametrize(
    "base,patch,expected",
    [
        ({"headers": {"x": "1"}}, {"headers": {"x": "1"}}, {"headers": {"x": "1"}}),
        (
            {"headers": {"a": "1", "b": "2"}, "transport": "sse"},
            {"headers": {"a": None}},
            {"headers": {"b": "2"}, "transport": "sse"},
        ),
    ],
)
def test_idempotency_and_isolation(
    base: dict, patch: dict, expected: dict
) -> None:
    """Re-applying the same patch must be idempotent; base must not mutate."""

    base_copy = dict(base)
    result = _apply_stream_options_patch(base, patch)
    assert result == expected
    # base must not have been mutated.
    assert base == base_copy
