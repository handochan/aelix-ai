"""#244 — both READMEs name a `/settings` row; the row must exist and work.

Same shape as ``test_readme_known_limitations.py``, which exists because "the
README quietly goes false". Here the sentence is the one about the once-a-day
release check: it used to say only that ``/settings`` turns the check off, which
was false for as long as the row shipped undispatchable — the first Enter drew
``✖ Check for updates: 'check_for_updates'`` and wrote nothing. The fix makes
both READMEs MORE specific (they now name the row and say when it takes effect),
so they need a gate: rename the label, or break the toggle again, and this turns
red instead of the prose quietly going false a second time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.tui.settings_rows import apply_setting, build_settings_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
READMES = ("README.md", "README.ko.md")


def _row_label(key: str) -> str:
    sm = SettingsManager.in_memory({})
    return next(r for r in build_settings_rows(sm) if r.key == key).label


@pytest.mark.parametrize("name", READMES)
async def test_the_release_check_sentence_names_the_row_it_points_at(name: str) -> None:
    """The bolded label in the release-check paragraph IS the row's label.

    Bolded and beside ``/settings`` in both files (house style — ``README.md``
    already writes ``/settings`` → **Gitignore in @ menu** for #238's row), so
    the assertion is: some paragraph mentions ``/settings`` and the exact label.
    """

    label = _row_label("check_for_updates")
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    paragraphs = [p for p in text.split("\n\n") if "`/settings`" in p and "--offline" in p]
    assert paragraphs, f"{name}: no paragraph mentions both /settings and --offline"
    assert any(f"**{label}**" in p for p in paragraphs), (
        f"{name}: the release-check paragraph does not name the row as "
        f"**{label}** — the /settings menu and the README have drifted apart"
    )


async def test_the_row_the_readmes_point_at_can_actually_be_switched_off() -> None:
    """A named row is only a promise if selecting it does something.

    Driven, not looked up: the defect #244 fixed was a ``KeyError`` swallowed
    into a red line, which no amount of prose-checking would have seen.
    """

    sm = SettingsManager.in_memory({})
    row = next(r for r in build_settings_rows(sm) if r.key == "check_for_updates")
    assert row.read(sm) == "on"
    result = apply_setting(row, sm)
    assert result.kind == "ok", result.message
    assert sm.get_check_for_updates() is False
