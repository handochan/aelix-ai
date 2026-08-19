"""Every JSON example in the shipped guides must be one the product accepts.

MEASURED DEFECT this exists for. Two of the three `models.json` examples in
``docs/guides/models-json.md`` carried ``"cost": { "input": …, "output": … }``,
while the validator requires all four of ``input``/``output``/``cacheRead``/
``cacheWrite``. Copy-pasting the documented example produced::

    Invalid models.json schema:
      - providers.my-provider.models.0.cost.cacheRead: Expected required property

and — this is the part that matters — ``load_custom_models`` answers a schema
error with ``empty_custom_models_result``, so the user does not lose one model,
they lose **the whole file**. The guide ships inside the wheel, so the broken
example was being distributed to every installed user.

The rule was also written backwards in prose: the page said *"cost is required
on a new model definition"*, when in fact omitting ``cost`` entirely succeeds
and a partial ``cost`` fails.

A doc example is code that nobody runs, so it rots the way uncovered code rots.
This runs it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDES = REPO_ROOT / "docs" / "guides"
BUNDLED = (
    REPO_ROOT
    / "packages"
    / "aelix-coding-agent"
    / "src"
    / "aelix_coding_agent"
    / "docs"
)

_JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.S)

#: Blocks that are deliberately NOT a whole ``models.json`` — a fragment shown
#: to illustrate one field, or a document with a different schema entirely.
#: Keyed by guide filename; the value is the set of block indexes to skip, with
#: a reason. Empty today: every JSON block in every guide is a full, valid
#: document, and keeping it empty is the point.
_NOT_A_MODELS_JSON: dict[str, dict[int, str]] = {}


def _blocks(path: Path) -> list[str]:
    return _JSON_BLOCK.findall(path.read_text(encoding="utf-8"))


def _guide_files() -> list[Path]:
    return sorted(GUIDES.glob("*.md"))


def test_the_scanner_finds_the_examples_it_is_supposed_to_check() -> None:
    """A zero from an empty scan would make every assertion below vacuous."""

    total = sum(len(_blocks(p)) for p in _guide_files())
    assert total >= 4, f"only {total} JSON blocks found across the guides"
    assert _blocks(GUIDES / "models-json.md"), "models-json.md has no examples"


@pytest.mark.parametrize("name", ["models-json.md"])
def test_every_models_json_example_validates(name: str) -> None:
    """The guide's own examples, through the product's own validator."""

    from aelix_coding_agent.models_json import validate_models_config

    skip = _NOT_A_MODELS_JSON.get(name, {})
    for index, block in enumerate(_blocks(GUIDES / name)):
        if index in skip:
            continue
        parsed = json.loads(block)
        errors = validate_models_config(parsed)
        assert not errors, (
            f"{name} example #{index} is rejected by the product: {errors}. "
            "A user who copy-pastes it loses their whole models.json, not just "
            "this model."
        )


def test_the_bundled_copy_carries_the_same_valid_examples() -> None:
    """The wheel's copy is what an installed user reads.

    ``tests/test_docs_bundle_sync.py`` already asserts the two are identical, so
    this is belt-and-braces — but it is cheap, and the failure it guards against
    (fixing the repo copy and shipping the broken one) is exactly the shape this
    repo keeps finding.
    """

    from aelix_coding_agent.models_json import validate_models_config

    bundled = BUNDLED / "models-json.md"
    assert bundled.is_file()
    for index, block in enumerate(_blocks(bundled)):
        assert not validate_models_config(json.loads(block)), (
            f"the WHEEL's models-json.md example #{index} is invalid"
        )


def test_every_json_example_in_every_guide_is_at_least_parseable() -> None:
    """Weaker assertion, wider net: a guide's JSON must be JSON.

    Only ``models-json.md`` has a schema to check against, but a syntax error in
    any example is a defect a reader hits immediately.
    """

    for path in _guide_files():
        for index, block in enumerate(_blocks(path)):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"{path.name} example #{index} is not valid JSON: {exc}"
                ) from exc


def test_the_prose_does_not_restate_the_cost_rule_backwards() -> None:
    """The sentence that was wrong in both directions.

    ``cost`` is optional; a PARTIAL ``cost`` is the error. The page said the
    opposite, which is worse than saying nothing.
    """

    text = (GUIDES / "models-json.md").read_text(encoding="utf-8")
    assert "`cost` is required on a new model definition" not in text
    assert "all-or-nothing" in text
    # And the four keys are named, because "all four" without the list is not
    # actionable from a page someone is copy-pasting out of.
    for key in ("input", "output", "cacheRead", "cacheWrite"):
        assert key in text
