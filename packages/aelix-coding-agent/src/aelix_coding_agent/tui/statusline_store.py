"""WP-2 (ADR-0160) — the coding-agent-owned statusline (footer) config store.

The enabled footer-segment id set + a ``use_theme_colors`` flag persist HERE, in
a NEW coding-agent-owned JSON file at ``get_agent_dir()/statusline.json``, NOT in
the pi-parity-pinned :class:`aelix_ai.settings.Settings` dataclass. The pinned
``Settings`` loader silently DROPS unknown JSON fields, so a ``status_line`` field
added there would no-op invisibly — hence this separate store (the same posture
:class:`aelix_coding_agent.cli.project_trust.ProjectTrustStore` uses for the
trust map).

``load()`` NEVER raises: a missing or corrupt file degrades to the registry
default-enabled set (mirroring the footer-data degrade posture so an adversarial
store can only hide a segment the user explicitly unchecked — it can never vanish
the ADR-0159 permission badge, whose omission rule lives inside the producer).
``save()`` is atomic (temp + ``os.replace``), creating the agent dir on demand.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from aelix_coding_agent.cli.config import get_agent_dir

_VERSION = 1
_FILENAME = "statusline.json"


@dataclass
class StatuslineConfig:
    """The persisted statusline configuration.

    :param enabled: the ordered enabled footer-segment id set (rendering order is
        still the registry order; this set only gates membership).
    :param use_theme_colors: a forward-looking flag (the footer is a single plain
        joined string today; per-segment theme palette is a follow-on — ADR-0160
        open-risk). Stored so the ``/statusline`` picker can round-trip it.
    """

    enabled: list[str] = field(default_factory=list)
    use_theme_colors: bool = True


class StatuslineStore:
    """Atomic JSON store for :class:`StatuslineConfig` (modeled on
    :class:`~aelix_coding_agent.cli.project_trust.ProjectTrustStore`).

    :param path: the JSON file path. Defaults to ``get_agent_dir()/statusline.json``.
    :param default_enabled: the fallback enabled-id set used when the file is
        missing/corrupt. The caller (``run_tui``) passes the registry
        default-enabled ids so a fresh install renders the byte-identical
        pre-ADR-0160 footer.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        default_enabled: list[str] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else Path(get_agent_dir()) / _FILENAME
        self._default_enabled = list(default_enabled or [])

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StatuslineConfig:
        """Load the config; NEVER raises.

        Missing file → defaults. Corrupt JSON / wrong shape → defaults (the
        footer must always render). A partially-valid file keeps the valid keys
        and falls back per-field, so a hand-edited file with a typo in one key
        still loads the rest.
        """

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return StatuslineConfig(enabled=list(self._default_enabled))
        if not isinstance(raw, dict):
            return StatuslineConfig(enabled=list(self._default_enabled))
        enabled = raw.get("enabled")
        if isinstance(enabled, list) and all(isinstance(x, str) for x in enabled):
            resolved_enabled = list(enabled)
        else:
            resolved_enabled = list(self._default_enabled)
        use_theme = raw.get("use_theme_colors")
        return StatuslineConfig(
            enabled=resolved_enabled,
            use_theme_colors=bool(use_theme) if isinstance(use_theme, bool) else True,
        )

    def save(self, config: StatuslineConfig) -> None:
        """Atomically persist ``config`` (temp + ``os.replace``; keys sorted).

        Creates the agent dir on demand. Mirrors
        :meth:`ProjectTrustStore._write`: a partial write never corrupts the
        store, and a concurrent reader sees either the old or the new file.
        """

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "enabled": list(config.enabled),
                "use_theme_colors": bool(config.use_theme_colors),
                "version": _VERSION,
            },
            indent=2,
            sort_keys=True,
        )
        tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


__all__ = ["StatuslineConfig", "StatuslineStore"]
