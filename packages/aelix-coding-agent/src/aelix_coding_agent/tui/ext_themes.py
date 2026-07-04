"""Issue #21 (ADR-0184) — manifest ``contributes.themes`` → theme registry.

A manifest may bundle color themes as plugin-root-relative TOML files:

.. code-block:: toml

    [contributes]
    themes = [{ path = "themes/solarized.toml" }]

Each theme file is aelix-native (the 6 :data:`~aelix_coding_agent.tui.themes.
THEME_ROLES` roles — pi's ~50-token JSON schema is the fidelity ceiling, not
adopted here since the aelix ``Theme`` only styles those roles):

.. code-block:: toml

    name = "solarized"
    [roles]
    assistant = "cyan"
    tool = "yellow"
    error = "red"

Unlike ``contributes.tui_widgets`` (whose ``factory`` executes plugin code and
so is trust-gated), a theme is pure DATA — no code runs — so there is NO
``ui_tui_trusted`` gate (gate-when-code-runs). The only safety fences are
path-traversal (the file must stay inside the plugin dir) and a size/parse
cap so a malformed file cannot brick startup.

:func:`apply_manifest_themes` is reconciled inside ``run_tui``'s ``_rebind``:
it rebuilds the FULL registered set from the current extensions on every
startup / resume / fork / #24 reload, so a removed plugin's themes vanish
(``register_themes`` replaces wholesale — pi ``setRegisteredThemes``). Themes
are only REGISTERED (available in the ``/settings`` picker), never
auto-selected — the user's persisted theme is untouched.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.tui import themes as theme_registry

if TYPE_CHECKING:
    from collections.abc import Collection

    from aelix_coding_agent.extensions.widget_protocols import Theme

logger = logging.getLogger(__name__)

# A theme file has no business being large; cap the read so an adversarial or
# accidental huge file cannot stall startup.
_MAX_THEME_BYTES = 256 * 1024

# The theme ``name`` is rendered into the /settings picker; unlike role colors
# it is not schema-validated, so cap its length and reject control/escape chars
# (SGR-injection / row-spoofing) — symmetric with the per-color validation.
_MAX_THEME_NAME_LEN = 64


def apply_manifest_themes(
    runner: Any,
    *,
    pending: Collection[str] = (),
) -> None:
    """Reconcile the theme registry against the CURRENT extension set.

    Walks each loaded extension's ``manifest.contributes.themes``, resolves
    each ``path`` against the plugin dir (``Extension.resolved_path``) with a
    traversal fence, parses the TOML, builds a :class:`Theme`, and hands the
    full collected list to :func:`~aelix_coding_agent.tui.themes.
    register_themes` (wholesale replace = reconcile). Never raises past a
    contrib: a bad path/file/parse is skipped with a warning. ``runner`` is
    duck-typed (``None`` → clears the registered set).
    """

    collected: list[tuple[Theme, str | None]] = []
    try:
        extensions = list(getattr(runner, "extensions", None) or [])
    except Exception:  # noqa: BLE001 — a raising runner property must not stop
        logger.warning("extension runner unreadable; clearing manifest themes", exc_info=True)
        extensions = []
    for ext in extensions:
        name = getattr(ext, "name", "<unknown>")
        manifest = getattr(ext, "manifest", None)
        contribs = manifest.contributes.themes if manifest is not None else []
        if not contribs:
            continue
        if name in pending:
            # Defensive fence: contributes.themes forces eager loading
            # (loader._is_lazy_eligible), so a pending lazy shell should never
            # carry theme contribs — and a pending shell has no resolved_path.
            logger.warning(
                "extension %r themes skipped: plugin is pending lazy activation",
                name,
            )
            continue
        pkg_dir_raw = getattr(ext, "resolved_path", None)
        if not pkg_dir_raw:
            logger.warning(
                "extension %r themes skipped: no plugin directory recorded", name
            )
            continue
        pkg_dir = Path(pkg_dir_raw).resolve()
        for index, contrib in enumerate(contribs):
            loaded = _load_theme(name, index, pkg_dir, contrib.path)
            if loaded is not None:
                collected.append(loaded)
    theme_registry.register_themes(collected)


def _load_theme(
    ext_name: str, index: int, pkg_dir: Path, rel_path: str
) -> tuple[Theme, str] | None:
    """Resolve + parse one theme file → ``(theme, resolved_path)``.

    ``None`` (with a warning) on any fault. The returned path is the
    ``.resolve()``d, traversal-fenced target (not the raw join) so
    :class:`ThemeInfo` never surfaces a ``..``-laden display path.
    """

    try:
        target = (pkg_dir / rel_path).resolve()
    except Exception:  # noqa: BLE001 — a pathological path string must not raise
        logger.warning("extension %r themes[%d]: bad path %r", ext_name, index, rel_path)
        return None
    # Path-traversal fence: the theme file must stay inside the plugin dir.
    if not target.is_relative_to(pkg_dir):
        logger.warning(
            "extension %r themes[%d]: path %r escapes the plugin directory; skipped",
            ext_name,
            index,
            rel_path,
        )
        return None
    if not target.is_file():
        logger.warning(
            "extension %r themes[%d]: theme file %s not found", ext_name, index, target
        )
        return None
    # Enforce the size cap BEFORE reading the file into memory: stat first (a
    # multi-GB regular file inside the plugin dir would otherwise be fully read
    # and OOM every startup/resume/fork/reload), then bound the read at cap+1 so
    # a file that grows between stat() and read() (TOCTOU) still cannot exhaust
    # memory — the post-read length check below rejects that grown case.
    try:
        if target.stat().st_size > _MAX_THEME_BYTES:
            logger.warning(
                "extension %r themes[%d]: theme file %s exceeds %d bytes; skipped",
                ext_name,
                index,
                target,
                _MAX_THEME_BYTES,
            )
            return None
        with target.open("rb") as fh:
            raw = fh.read(_MAX_THEME_BYTES + 1)
    except Exception:  # noqa: BLE001 — unreadable / stat-fail file → skip
        logger.warning("extension %r themes[%d]: cannot read %s", ext_name, index, target)
        return None
    if len(raw) > _MAX_THEME_BYTES:
        logger.warning(
            "extension %r themes[%d]: theme file %s exceeds %d bytes; skipped",
            ext_name,
            index,
            target,
            _MAX_THEME_BYTES,
        )
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 — malformed TOML → skip
        logger.warning("extension %r themes[%d]: malformed TOML in %s", ext_name, index, target)
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name:
        logger.warning(
            "extension %r themes[%d]: %s has no top-level string 'name'; skipped",
            ext_name,
            index,
            target,
        )
        return None
    # Security fence (ADR-0184 review): the name reaches the /settings picker
    # unvalidated (colors are validated, the name was not). Reject C0/DEL
    # control + escape chars (SGR-injection, newline row-spoofing, built-in
    # impersonation) and cap the length — a legitimate theme name has neither.
    if len(name) > _MAX_THEME_NAME_LEN or any(ch < " " or ch == "\x7f" for ch in name):
        logger.warning(
            "extension %r themes[%d]: %s theme name %r has control chars or is "
            "too long; skipped",
            ext_name,
            index,
            target,
            name,
        )
        return None
    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        logger.warning(
            "extension %r themes[%d]: %s '[roles]' is not a table; using empty",
            ext_name,
            index,
            target,
        )
        roles = {}
    return theme_registry.build_theme_from_data(name, roles), str(target)
