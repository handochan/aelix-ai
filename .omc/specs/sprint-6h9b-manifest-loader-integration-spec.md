# Sprint 6h₉b — Manifest v1 Loader Integration

Status: Binding (W1 spec; do not modify after W1 closure)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₉b` |
| Phase | 5b-foundation, sprint 2 of ~6 |
| Workflow | ADR-0032 W0-W6 |
| Scope class | Code + tests (extension loader extension); no behavior change for legacy extensions |
| Spec author | Main agent (architect READ-ONLY in current OMC profile) |
| Predecessor | Sprint 6h₉a (ADR-0088/0094/0095/0096/0097/0098 + contracts package) |
| Owning ADR closure | ADR-0099 (NEW — Sprint 6h₉b closure) |

This sprint is the **second sprint of Phase 5b-foundation**. It builds on Sprint 6h₉a's `PluginManifest` Pydantic contracts (`packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py`) and wires manifest discovery into the existing `discover_and_load_extensions` infrastructure from Sprint 5a (`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`).

---

## §1 — Background

### §1.1 — Existing loader infrastructure (Sprint 5a, ADR-0028 / ADR-0041)

`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` already ships a Pi-parity 4-tier extension loader:

| Tier | Source | Pi reference |
|---|---|---|
| 1 | Project-local `cwd/.aelix/extensions/` | Pi `loader.ts:594-596` |
| 2 | Global `~/.aelix/extensions/` (or `agent_dir` override) | Pi `loader.ts:598-600` |
| 3 | Explicit `configured_paths` | Pi `loader.ts:602-618` |
| 4 | Aelix-additive `entry_points(group="aelix.extensions")` — LAST priority per P-21 | Pi has no equivalent |

Per-entry try/except via `ExtensionLoadError` — one broken extension never aborts the wave (Pi parity, `loader.ts:437`).

Within a discovered directory, `_resolve_extension_entries(pkg_dir)` checks:
1. `pyproject.toml [tool.aelix] extensions = [...]` — Aelix port of Pi's `package.json "pi.extensions"` array
2. `__init__.py` fallback — Aelix port of Pi's `index.ts/index.js`

Module-path resolution via `_factory_from_module(module_path)` imports the module and looks for a top-level `setup` callable.

### §1.2 — Sprint 6h₉a contracts (this sprint's input)

Sprint 6h₉a (ADR-0094/0095/0096) introduced the `aelix-plugin.toml` v1 manifest with the following Pydantic models in `packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py`:

```python
class PluginManifest(BaseModel):
    plugin: PluginIdentity            # id, name, version, description, authors, repository, license, homepage
    api: PluginApi                    # level (current=1), min_level
    entry: PluginEntry                # python: "module:callable" form
    capabilities: Capabilities        # shell_exec / fs_write / fs_read_user / net / mcp_invoke /
                                      # ui_tui_trusted / ui_descriptor / ui_web_trusted / mcp_serve
    activation: Activation            # on_startup_finished / on_command / on_tool_call / on_session_start
    contributes: Contributes          # commands / tui_widgets / descriptors / tools / themes /
                                      # mcp_servers / hooks


def parse_manifest_toml(toml_text: str) -> PluginManifest:
    """Flattens [plugin.api] / [plugin.entry] into top-level keys before validation."""
```

`AELIX_API_LEVEL: int = 1` is the current ABI level (Sprint 6h₉a baseline).

`LICENSE_WHITELIST: frozenset[str]` is the 8-entry SPDX allowlist (MIT, Apache-2.0, BSD-3-Clause, BSD-2-Clause, MPL-2.0, ISC, Unlicense, `Apache-2.0 WITH LLVM-exception`).

The Sprint 6h₉a `PluginIdentity.validate_license` is currently warn-only (Phase 5b declaration policy per ADR-0096 §3.3.5).

### §1.3 — Gap (Sprint 6h₉b scope)

The Sprint 6h₉a manifest IS NOT yet wired into the loader. A plugin directory containing `aelix-plugin.toml` would be **silently ignored** by `_resolve_extension_entries`'s current `pyproject.toml [tool.aelix]` / `__init__.py` check.

Sprint 6h₉b closes this gap by integrating manifest detection into `_resolve_extension_entries`. The integration is **augmentation, NOT replacement**:

- Legacy `pyproject.toml [tool.aelix] extensions = [...]` keeps working (package-internal entry-list use case)
- Legacy `__init__.py` fallback keeps working (single-file extension)
- `aelix-plugin.toml` becomes the **NEW preferred** path (full manifest with identity, capabilities, activation, contributes)
- When multiple are present, **`aelix-plugin.toml` wins** (it carries the most information)

Pi reference note: Pi has **NO manifest** — Pi extensions are TypeScript `.ts` files in `~/.pi/agent/extensions/` discovered by file scan. Aelix's manifest is Aelix-additive per ADR-0096 §"Pi divergences". Sprint 6h₉b adds Aelix-additive surface; no Pi-parity invariant is violated.

### §1.4 — Out-of-scope items (defer to later sprints)

| Item | Owner sprint | Reason |
|---|---|---|
| Tier 1 `ExtensionUIContext` 27-method runtime implementation | Sprint 6h₉c | Phase 5b-foundation #3 |
| Tier 2 descriptor renderer (TUI Rich Renderable mapping) | Sprint 6h₉d | Phase 5b-foundation #4 |
| Tier 4 MCP client + subprocess hooks | Sprint 6h₉e | Phase 5b-foundation #5 |
| `aelix-server` FastAPI HTTP+WS skeleton | Sprint 6h₉f | Phase 5b-foundation #6 |
| **Lazy-load enforcement** (activation events actually gating extension load until trigger fires) | Phase 5c or later | Larger architectural change; current eager-load behavior preserved |
| **Capability enforcement** (refusing to inject adapters for undeclared capabilities) | Phase 6 | ADR-0096 §3.3.6 declaration vs enforcement split |
| **Strict license enforcement** (`--strict-licenses` flag) | Phase 6 | ADR-0096 §3.3.5 — Phase 6 default true |
| `pyproject.toml [tool.aelix]` deprecation | TBD | No deprecation in Sprint 6h₉b; both manifest forms coexist |
| Phase 6 capabilities for Web (`ui_web_trusted`) implementation | Phase 6 | Manifest accepts the flag now, but no consumer in Phase 5b |

---

## §2 — Scope

Sprint 6h₉b delivers **five deliverables** in four atomic commits (§4):

| # | Deliverable | Type | Touches |
|---|---|---|---|
| 1 | `Extension` dataclass gains `manifest: PluginManifest \| None` field | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` |
| 2 | `_resolve_extension_entries` detects `aelix-plugin.toml` first; new `_load_manifest_from_dir` helper | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` |
| 3 | `_factory_from_module` accepts `"module:callable"` colon form when manifest specifies it; `_invoke_factory` propagates `manifest` to the loaded `Extension` | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` |
| 4 | API_LEVEL gate + license warn at manifest parse time (manifest validation errors → `ExtensionLoadError`) | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` |
| 5 | Tests + ADR-0099 closure | Tests + Docs | `tests/extensions/test_manifest_v1_loader.py` (NEW), `docs/decisions/0099-sprint-6h9b-manifest-loader-integration.md` (NEW) |

---

## §3 — Per-deliverable specifications

### §3.1 — `Extension.manifest` field

**File**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`

Add a new optional field to the existing `Extension` dataclass (line ~570):

```python
from aelix_agent_core.contracts import PluginManifest

@dataclass
class Extension:
    name: str
    # ... existing fields preserved ...

    # Sprint 6h₉b §A — manifest-discovered extensions carry their parsed
    # PluginManifest; legacy ``pyproject.toml [tool.aelix]`` /
    # ``__init__.py`` discovery paths set this to ``None``.
    manifest: PluginManifest | None = None
```

Rules:
- `manifest` defaults to `None` for backward compatibility
- Legacy discovery paths (no `aelix-plugin.toml`) MUST leave `manifest = None`
- `aelix-plugin.toml`-discovered extensions MUST set `manifest` to the validated `PluginManifest` instance
- `Extension` keeps `@dataclass` (not frozen) — current behavior preserved

The `PluginManifest` import is from `aelix_agent_core.contracts` (Sprint 6h₉a package). If a circular import emerges (api.py ← contracts ← ...), import locally inside `__init__` or use `TYPE_CHECKING` guard. Verify no circular import at module load.

### §3.2 — `_resolve_extension_entries` augmentation

**File**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

Augment the existing `_resolve_extension_entries(pkg_dir)` function (currently at line ~263) to detect `aelix-plugin.toml` first:

```python
def _resolve_extension_entries(pkg_dir: Path) -> list[Path | _ManifestEntry] | None:
    """Sprint 6h₉b augmented resolver.

    Priority order (first match wins):

    1. ``aelix-plugin.toml`` — NEW preferred. Parse via Pydantic and
       return ``[_ManifestEntry(manifest, pkg_dir)]``.
    2. ``pyproject.toml [tool.aelix] extensions = [...]`` — legacy
       package-internal entry list (unchanged from Sprint 5a).
    3. ``__init__.py`` — single-file fallback (unchanged from Sprint 5a).

    Returns ``None`` if no manifest is present (signal: skip this
    subdirectory).
    """
```

`_ManifestEntry` is a NEW lightweight dataclass added to `loader.py`:

```python
@dataclass(frozen=True)
class _ManifestEntry:
    """Internal carrier for manifest-discovered extensions.

    A ``_ManifestEntry`` flows through ``load_extensions`` like a Path,
    but carries the parsed manifest + the plugin directory so the inner
    factory resolver can use ``[plugin.entry] python = "module:callable"``
    instead of falling back to the directory's ``setup`` convention.

    NOT exported.
    """
    manifest: PluginManifest
    pkg_dir: Path
```

The `discover_and_load_extensions` flow already returns `list[str | Path | ExtensionFactory]`. Sprint 6h₉b widens this to include `_ManifestEntry`. The widening is internal-only; the public signature `discover_and_load_extensions(configured_paths: list[str | Path | ExtensionFactory], ...)` is preserved (configured_paths remains the same type; only the internal `all_entries` widens).

Where to plug in:
- `_discover_in_dir` calls `_resolve_extension_entries` per subdirectory
- Sprint 6h₉b: `_resolve_extension_entries` returns either `list[Path]` (legacy) or `list[Path | _ManifestEntry]` (mixed) — flag-narrowing handles which branch the caller takes
- `discover_and_load_extensions` calls `_push_path(...)` which dedupes by `Path.resolve()`. For `_ManifestEntry`, dedupe by `pkg_dir.resolve()` (one manifest = one extension).

### §3.3 — Manifest detection helper

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

Add a new internal helper:

```python
def _load_manifest_from_dir(pkg_dir: Path) -> PluginManifest | None:
    """Load ``aelix-plugin.toml`` from ``pkg_dir`` if present.

    Returns:
        Parsed ``PluginManifest`` on success.
        ``None`` if no ``aelix-plugin.toml`` exists in ``pkg_dir``.

    Raises:
        ExtensionManifestError: on parse / validation failure (TOML
        syntax error, Pydantic validation error, API_LEVEL too low).

    Pi-additive — Pi has no manifest concept.
    """
    manifest_path = pkg_dir / "aelix-plugin.toml"
    if not manifest_path.exists():
        return None

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(
            f"Cannot read {manifest_path}: {exc}"
        ) from exc

    try:
        manifest = parse_manifest_toml(text)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ExtensionManifestError(
            f"Invalid manifest {manifest_path}: {exc}"
        ) from exc

    # API_LEVEL gate (ADR-0096 §"API_LEVEL policy")
    if manifest.api.min_level > AELIX_API_LEVEL:
        raise ExtensionManifestError(
            f"Plugin {manifest.plugin.id!r} requires API_LEVEL "
            f">= {manifest.api.min_level}, host has {AELIX_API_LEVEL}"
        )
    if manifest.api.level > AELIX_API_LEVEL:
        # Forward-compat best-effort: log warning, accept anyway
        logger.warning(
            "Plugin %r built for API_LEVEL %d, host has %d "
            "(loading anyway; behavior at undefined surfaces is best-effort)",
            manifest.plugin.id,
            manifest.api.level,
            AELIX_API_LEVEL,
        )

    # License whitelist (Phase 5b warn-only per ADR-0096 §"SPDX license whitelist v1")
    if manifest.plugin.license not in LICENSE_WHITELIST:
        logger.warning(
            "Plugin %r declares license %r outside the Sprint 6h₉a v1 "
            "whitelist; loading anyway (Phase 5b warn-only policy — "
            "Phase 6 will gate strict via --strict-licenses)",
            manifest.plugin.id,
            manifest.plugin.license,
        )

    return manifest
```

`ExtensionManifestError` is a NEW exception subclass:

```python
class ExtensionManifestError(Exception):
    """Sprint 6h₉b — raised on manifest parse / validation failure.

    Caught by the per-plugin try/except in ``discover_and_load_extensions``
    and surfaced as an ``ExtensionLoadError`` with a clear message.
    """
```

Module-level `logger` (use `logging.getLogger(__name__)` if not already present).

`AELIX_API_LEVEL` and `LICENSE_WHITELIST` import from `aelix_agent_core.contracts`. `parse_manifest_toml` import from `aelix_agent_core.contracts.manifest`. `ValidationError` import from `pydantic`.

### §3.4 — `_factory_from_module` colon-form support

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

Augment `_factory_from_module(module_path: str)` (currently at line ~389) to support `"module:callable"` colon form:

```python
def _factory_from_module(module_path: str) -> ExtensionFactory:
    """Import a module and return its factory callable.

    Sprint 6h₉b: now accepts ``"module:callable"`` colon-separated form
    (used by ``aelix-plugin.toml`` ``[plugin.entry] python``). Legacy
    bare-module form ``"module.path"`` still resolves to top-level
    ``setup`` for backward compat.

    Raises:
        AttributeError: if the specified callable does not exist.
    """
    if ":" in module_path:
        module_name, _, callable_name = module_path.partition(":")
        if not module_name or not callable_name:
            raise ValueError(
                f"Invalid module:callable form {module_path!r}; "
                "expected 'module.path:callable_name'"
            )
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name, None)
        if factory is None or not callable(factory):
            raise AttributeError(
                f"Module {module_name!r} has no top-level callable "
                f"{callable_name!r} (manifest [plugin.entry] python)."
            )
        return factory

    # Legacy form: bare module, look for top-level ``setup``
    module = importlib.import_module(module_path)
    factory = getattr(module, "setup", None)
    if factory is None or not callable(factory):
        raise AttributeError(
            f"Module {module_path!r} has no top-level callable 'setup'."
        )
    return factory
```

### §3.5 — `_resolve_factory` + `_invoke_factory` manifest propagation

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

`_resolve_factory(entry, *, cwd)` (line ~364) currently dispatches by entry type (callable / Path / str). Add `_ManifestEntry` handling:

```python
async def _resolve_factory(
    entry: str | Path | ExtensionFactory | _ManifestEntry,
    *,
    cwd: Path | None,
) -> tuple[ExtensionFactory, str, PluginManifest | None]:
    """Return ``(factory, display_name, manifest)`` for a single entry.

    Sprint 6h₉b: the return tuple now carries an optional manifest so
    ``_invoke_factory`` can attach it to the loaded ``Extension``.
    """

    if isinstance(entry, _ManifestEntry):
        py_entry = entry.manifest.entry.python
        if py_entry is None:
            raise ValueError(
                f"Manifest for plugin {entry.manifest.plugin.id!r} "
                "in {entry.pkg_dir} has no [plugin.entry] python; "
                "cannot load (Sprint 6h₉b requires python entry when "
                "any of capabilities.ui_tui_trusted / .ui_descriptor / "
                ".mcp_serve is True — see Sprint 6h₉a fold-in §A)"
            )
        factory = _factory_from_module(py_entry)
        return factory, entry.manifest.plugin.id, entry.manifest

    # ... existing callable / Path / str branches ...
    # All existing branches return ``(factory, display_name, None)`` —
    # they're legacy paths with no manifest.
```

`_invoke_factory(factory, runtime, *, name, manifest=None)` propagates:

```python
async def _invoke_factory(
    factory: ExtensionFactory,
    runtime: _ExtensionRuntime,
    *,
    name: str,
    manifest: PluginManifest | None = None,
) -> Extension:
    extension = Extension(name=name, manifest=manifest)
    api = ExtensionAPI(extension, runtime)
    result: Any = factory(api)
    if inspect.iscoroutine(result):
        await result
    return extension
```

Update `load_extensions(paths)` and `discover_and_load_extensions(...)` call sites to unpack the 3-tuple and pass `manifest` into `_invoke_factory`. Per-entry try/except boundary remains — manifest parse failures surface as `ExtensionLoadError`.

### §3.6 — Tests

**Location**: `tests/extensions/test_manifest_v1_loader.py` (NEW)

Minimum 14 test cases:

1. **Happy path**: directory with `aelix-plugin.toml` + Python file → loaded, `extension.manifest` is the parsed `PluginManifest`
2. **Legacy `pyproject.toml [tool.aelix]` path unchanged**: directory with only legacy form → loaded, `extension.manifest is None`
3. **Legacy `__init__.py` path unchanged**: directory with only `__init__.py` → loaded, `extension.manifest is None`
4. **Priority: `aelix-plugin.toml` wins over `pyproject.toml [tool.aelix]`**: directory with both → manifest path wins, `extension.manifest is not None`
5. **`module:callable` form resolves**: manifest with `python = "my_pkg:my_setup"` (NOT `setup`) → factory is `my_setup`
6. **Bare-module legacy form still resolves**: `_factory_from_module("my_pkg")` → resolves `my_pkg.setup`
7. **Invalid `module:callable` form**: empty module or empty callable → `ValueError`
8. **Missing `[plugin.entry] python` when required**: capabilities declare T1/T2 but no entry → `ValueError` from `_resolve_factory`
9. **API_LEVEL too low rejection**: manifest `[plugin.api] min_level = 99` → `ExtensionManifestError` → `ExtensionLoadError`, plugin NOT loaded
10. **API_LEVEL forward warn**: manifest `[plugin.api] level = 99, min_level = 1` → warning logged, plugin loaded
11. **License outside whitelist warn**: manifest `license = "Custom-1.0"` → warning logged, plugin loaded (Phase 5b warn-only)
12. **Malformed TOML**: `aelix-plugin.toml` contains syntax errors → `ExtensionManifestError` → `ExtensionLoadError`, one plugin fails but wave continues
13. **Pydantic validation error**: manifest missing required `version` → `ValidationError` → `ExtensionManifestError` → `ExtensionLoadError`
14. **Manifest carries activation/capabilities/contributes**: round-trip — load + assert `extension.manifest.activation.on_command == ["my-cmd"]`, etc.

Use `tmp_path` fixture + `pytest.LogCaptureFixture` for warning capture. Tests live in `tests/extensions/` (existing directory).

Add `tests/extensions/__init__.py` if not already present.

### §3.7 — ADR-0099 closure

**Location**: `docs/decisions/0099-sprint-6h9b-manifest-loader-integration.md` (NEW)

**Mandatory front-matter** (per ADR-0093 / ADR-0098 template):

```
# 0099. Sprint 6h₉b — Manifest v1 Loader Integration

Status: Accepted (Sprint 6h₉b / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**
```

**Required sections**:

1. `## Context` — Why Sprint 6h₉b exists. Sprint 6h₉a shipped the manifest contracts (Pydantic models + JSON Schemas) but the loader never sees them. Sprint 6h₉b wires manifest detection into the existing 4-tier `discover_and_load_extensions` infrastructure (Sprint 5a). The integration is augmentation — legacy `pyproject.toml [tool.aelix]` + `__init__.py` paths preserved.

2. `## Decision` — Five deliverables enumerated per §2 of this spec. Highlight:
   - `aelix-plugin.toml` becomes the **NEW preferred** discovery path
   - `module:callable` colon form supported (NEW; legacy bare-module form preserved)
   - API_LEVEL `min_level` gate (REJECT) + `level` forward warn (LOAD)
   - License whitelist warn-only (Phase 5b)
   - Capabilities / activation / contributes parsed and stored on `Extension` but NOT yet wired to runtime — wiring is Sprint 6h₉c/d/e/f

3. `## Aelix-additive divergences from Pi` — Lock these:
   - Pi has no manifest; Aelix `aelix-plugin.toml` is wholly Aelix-additive
   - Pi `loader.ts` has no API_LEVEL check; Aelix gates on `min_level`
   - Pi has no license whitelist; Aelix warns on non-SPDX entries
   - `_ManifestEntry` internal carrier is Aelix-additive (Pi loader passes raw paths only)
   - `_factory_from_module` colon form is Aelix-additive (Pi uses TS default exports)

4. `## Deferred items (Phase 5b/5c/6 carry-forward)` — Reference §1.4 of this spec verbatim.

5. `## Pi citations` (SHA `734e08e`):
   - `packages/coding-agent/src/core/extensions/loader.ts:575-621` — `discoverAndLoadExtensions` (already mirrored by Sprint 5a)
   - `packages/coding-agent/src/core/extensions/loader.ts:454-479` — `resolveExtensionEntries` (already mirrored, augmented this sprint)
   - `packages/coding-agent/src/core/extensions/loader.ts:481-518` — `discoverExtensionsInDir` (already mirrored)
   - `packages/coding-agent/docs/extensions.md` §"Auto-discovery" — base paths (Aelix translation)

6. `## Reference companions`:
   - ADR-0028 — original extension loader decision
   - ADR-0041 — Sprint 5a discovery enhancement
   - ADR-0096 — Manifest v1 schema (this sprint's input)
   - ADR-0094 — 4-tier extension architecture
   - ADR-0098 — Sprint 6h₉a closure (contracts package shipped)

7. `## Verification` — Reference §5 of this spec (ruff / pyright / pytest counts).

8. `## Phase` — "Sprint 6h₉b / Phase 5b-foundation (shipped). Next sprint: 6h₉c — ExtensionAPI Python surface (27-method `ctx.ui.*` Protocol)."

---

## §4 — Commit split plan (W6, 4 atomic commits)

### Commit 1 (§A) — `Extension.manifest` field

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`

**Commit message** (HEREDOC):

```
feat(extensions): add manifest field to Extension dataclass (Sprint 6h₉b §A)

Extends Extension with optional manifest: PluginManifest | None field.
Manifest-discovered extensions (aelix-plugin.toml) carry their parsed
PluginManifest; legacy pyproject.toml [tool.aelix] / __init__.py paths
keep manifest=None.

Sprint 6h₉a contract (PluginManifest) imported lazily where needed to
avoid circular imports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 2 (§B) — `_resolve_extension_entries` + `_load_manifest_from_dir` + `_ManifestEntry`

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

**Commit message** (HEREDOC):

```
feat(extensions): detect aelix-plugin.toml in extension discovery (Sprint 6h₉b §B)

_resolve_extension_entries now checks for aelix-plugin.toml FIRST,
falling back to legacy pyproject.toml [tool.aelix] / __init__.py
chains. New _load_manifest_from_dir helper parses + validates the
manifest with Pydantic (Sprint 6h₉a contracts).

New ExtensionManifestError exception bridges manifest validation
failures to the existing per-entry try/except in load_extensions.

New internal _ManifestEntry carrier flows manifest + pkg_dir through
the loader pipeline without changing the public discover_and_load
signature.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 3 (§C) — `_factory_from_module` colon form + manifest propagation + API_LEVEL + license

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py`

**Commit message** (HEREDOC):

```
feat(extensions): manifest entry resolution + API_LEVEL gate (Sprint 6h₉b §C)

_factory_from_module now accepts "module:callable" colon-separated form
used by aelix-plugin.toml [plugin.entry] python. Legacy bare-module
form unchanged (still resolves to top-level setup).

_resolve_factory / _invoke_factory pass the parsed manifest through to
the loaded Extension so runtime consumers (Sprint 6h₉c/d/e/f) can read
declared capabilities / activation / contributes.

API_LEVEL gate: min_level > AELIX_API_LEVEL rejects the plugin;
level > AELIX_API_LEVEL warns and accepts (forward-compat best-effort
per ADR-0096).

License whitelist: warn-only in Phase 5b (ADR-0096 §"SPDX license
whitelist v1"); strict gate deferred to Phase 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 4 (§D + §E) — Tests + ADR-0099 closure

**Stage**:
- `tests/extensions/test_manifest_v1_loader.py` (NEW)
- `tests/extensions/__init__.py` (NEW if missing)
- `docs/decisions/0099-sprint-6h9b-manifest-loader-integration.md` (NEW)

**Commit message** (HEREDOC):

```
test+docs: manifest loader tests + ADR-0099 closure (Sprint 6h₉b §D + §E)

14 tests covering:
- happy path: aelix-plugin.toml discovery + load
- legacy paths preserved (pyproject.toml [tool.aelix], __init__.py)
- priority: manifest wins over legacy
- module:callable colon form resolution
- API_LEVEL gating (reject min_level too high, warn level too high)
- license whitelist warn-only (Phase 5b)
- malformed TOML / Pydantic validation → ExtensionLoadError isolation
- manifest activation / capabilities / contributes round-trip

ADR-0099 closes Sprint 6h₉b with verification evidence and locks the
Aelix-additive divergences from Pi (manifest detection + API_LEVEL +
license whitelist all absent from Pi loader.ts).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §5 — Verification plan (W3-W5)

### W3 (verifier — evidence collection)

```sh
uv run ruff check 2>&1 | tail -3
uv run pyright 2>&1 | tail -5  # MUST show 8 baseline; zero new
uv run pytest 2>&1 | tail -5   # baseline 2417 + ~14 new = ~2431
python scripts/generate_contracts_schemas.py --check  # exit 0 (no contracts change)
```

Expected:
- ruff: clean
- pyright: 8 baseline preserved
- pytest: 2417 baseline + ~14 new manifest loader tests = ~2431 passed + 1 skipped
- schema --check: exit 0 (this sprint does not touch contracts package)

### W4 (code-reviewer — severity-rated review)

Focus areas:
- Manifest detection priority order correctness (`aelix-plugin.toml` MUST win over legacy paths)
- `_ManifestEntry` deduplication semantics (pkg_dir.resolve() — one manifest = one plugin)
- API_LEVEL gate boundary cases (== AELIX_API_LEVEL accepted; > AELIX_API_LEVEL rejected for min_level, warned for level)
- License warn-only behavior (NOT raising — Phase 5b policy)
- Per-plugin try/except containment (one bad manifest never aborts the wave)
- `_factory_from_module` colon form edge cases (empty module, empty callable, multiple colons)
- `Extension.manifest` field optional behavior (legacy paths set None)
- Cross-package import (aelix_coding_agent → aelix_agent_core.contracts) — no circular

Severity gates same as Sprint 6h₉a (MAJOR / MINOR / NIT).

### W5 (critic — Pi reference comparison + locked-decision audit)

Critic MUST:
- Verify the new ADR-0099 Pi citations against SHA `734e08e`
- Verify Sprint 6h₉b stays within Aelix-additive boundaries (no Pi behavior changed)
- Cross-check ADR-0099 against ADR-0094/0096/0098 (declared 4-tier architecture and manifest schema implemented as documented)
- Audit `_ManifestEntry` necessity (could Path with sidecar dict suffice?) — argue for the chosen approach
- Confirm Sprint 6h₉b commit boundary integrity (§A / §B / §C / §D+§E atomic)

### W6 (commits) — see §4

---

## §6 — User-imposed constraints (BINDING — verbatim)

Identical to Sprint 6h₉a §6:

| # | Constraint |
|---|---|
| C1 | DO NOT push (user pushes manually with `! git push origin main`) |
| C2 | DO NOT skip hooks (`--no-verify` banned) |
| C3 | DO NOT stage `.omc/project-memory.json` or `.omc/.project-memory.json.tmp.*` |
| C4 | DO NOT modify this spec after W1 commit |
| C5 | Co-Authored-By trailer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` on EVERY commit |
| C6 | HEREDOC format for commit messages |
| C7 | `git add <specific-paths>` — never `git add -A` or `git add .` |
| C8 | Pi pin `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` — no advance |
| C9 | UI work requires user consultation BEFORE starting — Sprint 6h₉b touches loader, NOT UI; no consultation gate triggered |
| C10 | Code review + Pi reference comparison thoroughly (W4 + W5 mandatory) |

---

## §7 — Pi citation map

| Citation | Use site |
|---|---|
| Pi `packages/coding-agent/src/core/extensions/loader.ts:575-621` | `discoverAndLoadExtensions` — Aelix `discover_and_load_extensions` mirror (existing from Sprint 5a; not modified in 6h₉b) |
| Pi `packages/coding-agent/src/core/extensions/loader.ts:454-479` | `resolveExtensionEntries` — Aelix `_resolve_extension_entries` mirror (augmented in 6h₉b §B) |
| Pi `packages/coding-agent/src/core/extensions/loader.ts:481-518` | `discoverExtensionsInDir` — Aelix `_discover_in_dir` mirror (not modified in 6h₉b) |
| Pi `packages/coding-agent/docs/extensions.md` §"Auto-discovery" | Base paths reference (Aelix translation) |

External references (not Pi):
- Neovim API_LEVEL — https://neovim.io/doc/user/api/ (informed manifest API_LEVEL design per ADR-0096)
- Harlequin entry-points + per-plugin try/except — https://github.com/tconbeer/harlequin/blob/main/src/harlequin/plugins.py

---

## §8 — Definition of Done

Sprint 6h₉b closure when ALL true:

- [ ] All 4 commits landed in main (per §4, atomic, HEREDOC, Co-Authored-By)
- [ ] `uv run ruff check` clean
- [ ] `uv run pyright` 8 baseline errors preserved (zero new)
- [ ] `uv run pytest` ~14 new manifest loader tests pass; baseline preserved
- [ ] `python scripts/generate_contracts_schemas.py --check` exit 0 (no contracts touched)
- [ ] `Extension.manifest` field added; legacy discovery paths set `manifest=None`
- [ ] `aelix-plugin.toml` detected by `_resolve_extension_entries`; legacy paths still work
- [ ] `_factory_from_module` accepts `"module:callable"` form
- [ ] API_LEVEL `min_level` gate rejects; `level` warn accepts
- [ ] License warn-only behavior; loading not blocked on unknown license
- [ ] Manifest validation failures route to `ExtensionLoadError` via `ExtensionManifestError`
- [ ] ADR-0099 follows ADR-0093 format
- [ ] No staged `.omc/project-memory.json` or temp files
- [ ] No push (user pushes manually)

---

## §9 — Glossary (new terms)

| Term | Definition |
|---|---|
| `_ManifestEntry` | Internal carrier dataclass in `loader.py` that flows manifest + pkg_dir through the loader pipeline. Not exported. |
| `ExtensionManifestError` | New exception class raised when manifest parsing or validation fails. Caught by per-plugin try/except. |
| `module:callable` form | Colon-separated entry specifier (e.g., `"my_plugin.ext:default"`) supported by `_factory_from_module` and used by `[plugin.entry] python` in `aelix-plugin.toml`. |
| Manifest priority | The order `_resolve_extension_entries` checks: (1) `aelix-plugin.toml`, (2) `pyproject.toml [tool.aelix]`, (3) `__init__.py`. First match wins. |

---

## §10 — Aelix-additive divergences from Pi (Sprint 6h₉b additions)

| # | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 1 | `aelix-plugin.toml` detection | Pi has no manifest | Aelix `_resolve_extension_entries` checks manifest first | Sprint 6h₉a contracts (ADR-0096) — capabilities declaration, API_LEVEL versioning, declarative contributes |
| 2 | `module:callable` colon form | Pi uses TS default exports (`export default ...`) | Aelix `_factory_from_module` parses colon form | TOML manifest needs explicit callable name; Python lacks "default export" convention |
| 3 | API_LEVEL gate | Pi has no formal ABI versioning | Aelix rejects when `min_level > host` | Neovim API_LEVEL pattern — plugin compat tracking across breaking changes |
| 4 | License whitelist warn | Pi accepts any license | Aelix warns on non-SPDX entries (Phase 5b) | Zed extension.toml precedent; Phase 6 will strict-gate |
| 5 | `ExtensionManifestError` exception class | Pi surfaces TS errors directly | Aelix has a typed manifest-failure exception | Cleaner per-plugin try/except routing |
| 6 | Manifest propagation to `Extension` | Pi Extension has no manifest field | Aelix `Extension.manifest: PluginManifest \| None` | Required for Sprint 6h₉c/d/e/f to consume declared capabilities/activation/contributes |

All divergences are net-additive — Pi behavior unchanged (no manifest = legacy `pyproject.toml`/`__init__.py` paths still work identically).

---

## §11 — Cross-ADR consistency check

| ADR | Cites in this sprint | Cited by 0099 |
|---|---|---|
| 0028 (original loader) | Sprint 5a baseline | ✓ |
| 0041 (Sprint 5a discovery enhancement) | Sprint 5a baseline | ✓ |
| 0094 (4-tier extension model) | Foundation reference | ✓ |
| 0096 (Manifest v1 schema) | THE input for this sprint | ✓ |
| 0098 (Sprint 6h₉a closure) | Contracts shipped | ✓ |
| 0099 (this sprint closure) | self | self |

---

## §12 — End of spec

Spec author: Main agent (W0+W1, Sprint 6h₉b)
Spec status: Binding (do not modify)
Spec scope: Sprint 6h₉b only

W2 executor: read this spec then execute commits 1-4 per §4 in order. Verify per §5. Honor §6 constraints. Output a sprint summary at W2 completion citing each commit SHA + verification evidence per §8 DoD.
