"""Live extension-owned settings rows, separate from built-in SettingsManager fields."""

from __future__ import annotations

import inspect
from collections import Counter
from typing import TYPE_CHECKING

from .settings_rows import ApplyResult, SettingsRow

if TYPE_CHECKING:
    from aelix_ai.settings import SettingsManager

    from aelix_coding_agent.extensions.api import RegisteredSetting


def _setting_on_off(value: bool) -> str:
    return "on" if value else "off"


def extension_settings_rows(
    contributions: dict[str, RegisteredSetting], existing: list[SettingsRow]
) -> list[SettingsRow]:
    """Build live plugin rows; owner callbacks are read only by the menu itself."""
    labels = {row.label for row in existing}
    counts = Counter(s.label for s in contributions.values())
    reserved = labels | set(counts)
    rows = []
    for key, setting in contributions.items():
        label = setting.label
        if label in labels or counts[label] > 1:
            qualified = f"{label} ({key.removeprefix('extension:')})"
            label = qualified
            suffix = 2
            while label in reserved or label in labels:
                label = f"{qualified} ({suffix})"
                suffix += 1
        labels.add(label)

        def read(_sm: SettingsManager, contribution: RegisteredSetting = setting) -> str:
            try:
                return _setting_on_off(contribution.get_value())
            except Exception:
                return "unavailable"

        rows.append(
            SettingsRow(
                key=key,
                label=label,
                kind="bool",
                read=read,
                help=setting.description,
                live=True,
                extension_setting=setting,
            )
        )
    return rows


async def apply_extension_setting(row: SettingsRow) -> ApplyResult:
    """Toggle through the owner, await persistence, and confirm the actual value."""
    setting = row.extension_setting
    if setting is None:
        return ApplyResult(kind="error", message="This row has no extension setting.")
    try:
        desired = not setting.get_value()
        result = setting.set_value(desired)
        if inspect.isawaitable(result):
            await result
        if setting.get_value() != desired:
            return ApplyResult(kind="error", message=f"{row.label}: value did not change.")
        return ApplyResult(kind="ok", message=f"{row.label} → {_setting_on_off(desired)} (global)")
    except Exception:
        # Extension errors can contain credentials/paths; keep the UI diagnostic generic.
        return ApplyResult(kind="error", message=f"{row.label}: setting unavailable.")
