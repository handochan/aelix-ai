# 0092. Sprint 6h₈ Phase 5a-iv — Image + Migrations + --continue Closure

Status: Accepted (Sprint 6h₈ / Phase 5a-iv / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₆ (ADR-0089) shipped the non-interactive CLI shell with three
items DEFERRED to Phase 5a-iii / Phase 5a-iv:

1. **`cli/file-processor.ts` image branch** (ADR-0089 P-387) — Sprint
   6h₆ stubbed image processing with a stderr warning + skip. Pi
   `utils/image-resize.ts` (176 LOC Photon WASM) + `utils/mime.ts`
   (74 LOC magic-byte detection) are the binding ports.
2. **`--continue` / `-c` auto-resume** (ADR-0089 P-393) — Pi `main.ts:280-281`
   dispatches to `SessionManager.continueRecent(cwd)` →
   `findMostRecentSession()`. Aelix `JsonlSessionRepo` ships `list()`
   sorted by `created_at` (header timestamp) but lacks the mtime-based
   `find_most_recent` Pi parity helper.
3. **`takeOverStdout`** (ADR-0089 P-403) — Pi's print-mode protector
   that redirects user-extension `console.log` to stderr. Aelix RPC
   mode already implements the Python equivalent
   (`contextlib.redirect_stdout` at `rpc_mode.py:1839`); print-mode
   wrapper depends on the extension framework (Sprint 6i+, ADR-0058).

Sprint 6h₇b (ADR-0091) followup carry-forward added:

4. **`migrations.ts` port** (ADR-0091) — Pi runs 7 cleanup migrations
   on cwd startup; all 7 target legacy shapes Aelix never had.

Sprint 6h₈ closes items 1, 2, and 4 with real ports and formally
documents item 3 as deferred to the extension framework sprint.

## Decision

### §B — Image branch (Pi `file-processor.ts:48-76` + `utils/image-resize.ts` + `utils/mime.ts`)

**NEW `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_detect.py`**
— port of Pi `utils/mime.ts:1-74`:

- `detect_image_mime_type(buffer: bytes) -> str | None` — magic-byte
  dispatch over the first 4100 bytes:
  - **JPEG**: `FF D8 FF` first 3 bytes; reject when 4th byte == `0xF7`
    (Pi truncated-JPEG-variant rejection at `mime.ts:8`).
  - **PNG**: 8-byte signature `89 50 4E 47 0D 0A 1A 0A` AND valid IHDR
    chunk (length 13) AND NOT animated APNG (`acTL` chunk absent before
    `IDAT`).
  - **GIF**: ASCII `GIF` at offset 0.
  - **WebP**: ASCII `RIFF` at offset 0 + `WEBP` at offset 8.
  - All other formats → `None`.
- `detect_image_mime_type_from_file(path)` — async wrapper reading first
  4100 bytes via `asyncio.to_thread`; swallows `OSError` → returns
  `None`.

**NEW `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_resize.py`**
— port of Pi `utils/image-resize.ts:1-176`:

- `@dataclass ImageResizeOptions` mirroring Pi:
  `max_width=2000, max_height=2000, max_bytes=4.5*1024*1024,
  jpeg_quality=80`.
- `@dataclass(frozen=True) ResizedImage` mirroring Pi `ResizedImage`
  interface (`image-resize.ts:13-21`) — `{data, mime_type,
  original_width, original_height, width, height, was_resized}`.
- `async def resize_image(img: ImageContent, options=None) ->
  ResizedImage | None` — Pi parity algorithm:
  1. Decode base64 → BytesIO → `Image.open()`.
  2. Apply EXIF auto-orientation via `ImageOps.exif_transpose()`.
  3. Already-compliant fast-path (width ≤ max_w AND height ≤ max_h AND
     base64 size < max_bytes) → return as-is with `was_resized=False`.
  4. Initial target dim calc (aspect-preserving snap to max_w/max_h).
  5. Iterative encode search: at each dim level try PNG + 5
     order-preserving-deduped JPEG qualities (default 80, then 85, 70,
     55, 40); first under max_bytes wins.
  6. If none fit at current dims, scale × 0.75 and retry until 1×1 or
     no further progress.
  7. Give-up → `None`.
- `format_dimension_note(result)` — Pi parity `formatDimensionNote`
  (`image-resize.ts:166-174`) — coordinate-mapping note for
  screenshot tools.

**MODIFY `packages/aelix-coding-agent/pyproject.toml`** — add
`Pillow>=11.0,<12.0`. Pillow ships pyright type stubs since 10.0 so
the 8-error baseline is preserved.

**MODIFY `packages/aelix-coding-agent/src/aelix_coding_agent/cli/file_processor.py`**:

- Replace Sprint 6h₆ image-skip stub (lines 91-99) with real image
  branch.
- Detect images via `detect_image_mime_type_from_file(path)` (magic
  bytes — Pi parity per `file-processor.ts:48-50`).
- When detected: read full bytes, base64-encode, optionally resize
  via `resize_image()` (gated by new `auto_resize_images: bool = True`
  parameter), wrap in `ImageContent`, append to `processed.images`.
- Add Pi-shape dimension note to text reference: `<file
  name="…">[dimension note]</file>`. When `was_resized=False` the
  body is empty (Pi parity `file-processor.ts:73`).
- Magic bytes win: `.jpg` files containing text fall through to text
  branch (Pi parity).

### §C — `migrations.py` NO-OP STUB (Pi `migrations.ts:1-315`)

Aelix has zero legacy data: `AuthStorage` ships fresh (Sprint 6c),
`JsonlSessionStorage` ships v3 strict from day one (Sprint 4a),
keybindings + extension framework are Phase 5b+ deferred. NO-OP STUB
is adequate (~30 LOC).

**NEW `packages/aelix-ai/src/aelix_ai/migrations.py`**:

- Module docstring enumerating the 7 Pi cleanups + the Aelix-fresh
  rationale per migration.
- `async def run_migrations(cwd: str | Path) -> dict[str, list[Any]]`
  returning `{"migrated_auth_providers": [], "deprecation_warnings": []}`
  (Pi return-shape preserved for future Phase 5b TUI startup hook).
- `async def show_deprecation_warnings(warnings: list[str]) -> None`
  no-op stub.

NO call site in `entry.py` for this sprint — future Phase 5b TUI may
add the startup hook.

### §D — `--continue` / `-c` auto-resume (Pi `main.ts:280-281`)

**NEW METHOD `JsonlSessionRepo.find_most_recent(cwd) ->
JsonlSessionMetadata | None`** in
`packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py`:

- mtime DESC sort (Pi parity per `findMostRecentSession`
  `session-manager.ts:480-493`). **Diverges from existing `list()`**
  which sorts by `created_at` (header timestamp); divergence
  documented here, `list()` kept as-is to avoid breaking the
  surface used by Sprint 4a-onwards callers.
- cwd-filtered via existing cwd-encoded directory layout.
- Filters each candidate through new `_is_valid_session_file`
  private helper (Pi parity `session-manager.ts:464-478` — read first
  512 bytes + parse first line JSON + validate `type == "session"`
  AND `id` non-empty string).
- Returns `None` when no valid sessions exist (silent fallback in
  caller).

**MODIFY `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py`**:

- Add `_validate_continue_flag(parsed) -> str | None` helper. Returns
  an error message when `--continue` is set together with any of:
  - `--no-session` (INCOMPATIBLE)
  - `--session <path>` (INCOMPATIBLE)
  - `--fork <id>` (INCOMPATIBLE)
  Compatible with `--print` / `--mode rpc` / `--session-dir` /
  positional messages (Pi parity per spec §D.2).
- Add `--continue` short-circuit before `_build_session`: when set,
  call `repo.find_most_recent(cwd)`. If found → `repo.open(metadata)`.
  Else → silent fallback to `_build_session(parsed, repo)` (Pi
  parity).
- `--resume` raises `NotImplementedError` with a stderr diagnostic
  pointing to Phase 5b TUI (ADR-0088).

### §E — `takeOverStdout` SKIP (documentation-only)

Per W0 audit:

- Aelix RPC mode already implements the Python equivalent via
  `contextlib.redirect_stdout(sys.stderr)` at `rpc_mode.py:1839`.
- Aelix JSON-mode builtin tools have ZERO `print()` / `sys.stdout`
  writes (audited W0).
- Print-mode user-extension corruption risk is real BUT the extension
  framework is itself deferred to ADR-0058 (Sprint 6i+); no real
  user-extension writes are reachable through Aelix today.

No code changes; this decision formally closes ADR-0089 P-403.

## Aelix-additive divergences from Pi

1. **Pillow (Python) instead of Photon WASM (Pi)** — Pillow is the
   idiomatic Python image library, ships pyright type stubs since
   10.0, and replaces Pi's WASM-based pipeline. Documented at the
   `resize_image()` docstring.
2. **`ImageOps.exif_transpose` instead of manual TIFF parsing
   (~83 LOC saved vs Pi)** — Pi `exif-orientation.ts` hand-parses
   TIFF orientation tags; Pillow's helper covers all 8 orientations
   identically and shipping pyright stubs since 10.0.
3. **`migrations.py` NO-OP stub instead of 315-LOC port** — Aelix has
   no legacy data per W0 §0 enumeration (P-439).
4. **`find_most_recent` mtime sort vs existing `list()` `created_at`
   sort** — `list()` semantics unchanged; new `find_most_recent`
   mirrors Pi `findMostRecentSession` mtime semantics. Divergence
   intentional and documented. **W5 MAJOR-1 fold-in:** the loader
   iterates candidates in mtime-DESC order until
   `load_jsonl_session_metadata` succeeds, rather than returning
   `None` when the single most-recent candidate's full metadata parse
   fails. Pi `session-manager.ts:489` returns a path; the caller
   opens. Aelix collapses both into one call and now falls through
   gracefully — regression test pinned at
   `tests/session/test_find_most_recent.py::test_falls_through_to_older_when_newer_metadata_parse_fails`.
5. **`takeOverStdout` skipped entirely** — Aelix RPC mode already
   protects via `contextlib.redirect_stdout`; print-mode wrapper
   deferred until the extension framework lands (Sprint 6i+,
   ADR-0058).
6. **Magic-byte detection replaces extension-based detection** —
   Sprint 6h₆ used `path.suffix in _IMAGE_EXTENSIONS`; Sprint 6h₈
   uses magic bytes (Pi parity) with extension as a fast-path
   optimisation hint only.
7. **`file_processor.py` uses `path.name` (basename) for the
   `<file name="…">` wrapper** — Pi uses `absolutePath`. This is an
   inherited Sprint 6h₆ divergence kept for backward-compat with
   the existing text branch test suite.
8. **`_is_valid_session_file` as a `@staticmethod` instead of a
   module-level function** — Pi has it as a module-private function
   in `session-manager.ts`; Aelix scopes it onto
   `JsonlSessionRepo` since the repo class owns the directory layout
   semantics.
9. **`_validate_continue_flag` runs BEFORE `--list-models`
   short-circuit (W5 MAJOR-2 fold-in)** — Pi `main.ts:280-281`
   dispatches `--continue` only inside the normal mode-resolution
   path so `--list-models` upstream short-circuit silently ignores
   conflict combos like `--list-models --continue --no-session`.
   Aelix elevates the validator above the list-models exit path so
   the spec-mandated stderr diagnostic surfaces consistently across
   exit paths. Pi behavioral parity at the user level is preserved
   (Pi never reaches the validator either), but Aelix's spec §D.2
   contract is now honored on every invocation. Regression test
   pinned at
   `tests/cli/test_continue_flag.py::test_list_models_with_continue_no_session_emits_error`.

## Deferred items

- `--resume` interactive TUI picker — Phase 5b TUI work (depends on
  TUI library land per ADR-0088).
- `--fork` interactive picker UI — Phase 5b (headless path already
  shipped Sprint 6h₆).
- `takeOverStdout` print-mode wrapper — Sprint 6i+ extension
  framework (ADR-0058).
- Theme reads from SettingsManager — Phase 5b.
- `branchSummary.skipPrompt` UI gating — Phase 5b.
- `migrations.py` call site in `entry.py` startup — Phase 5b TUI
  startup hook (Pi calls `runMigrations(cwd)` from `main.ts` before
  the prompt loop).

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `cli/file-processor.ts:1-100` — image branch source.
- `utils/image-resize.ts:1-176` — resize algorithm + dimension note.
- `utils/mime.ts:1-74` — magic-byte detection.
- `main.ts:280-281` — `--continue` dispatch.
- `core/session-manager.ts:464-478` — `isValidSessionFile`.
- `core/session-manager.ts:480-493` — `findMostRecentSession`.
- `migrations.ts:1-315` — 7 cleanup migrations (Aelix NO-OP stub
  rationale).

## Reference companions

- ADR-0091 — Sprint 6h₇b (SettingsManager standalone port; migrations
  carry-forward source).
- ADR-0090 — Sprint 6h₇a (Phase 5a-iii-α partial closure).
- ADR-0089 — Sprint 6h₆ (Phase 5a-i + 5a-ii closure; carry-forward
  source for image branch + `--continue` + `takeOverStdout`).
- ADR-0088 — Phase 5b TUI library decision (`--resume` picker target).
- ADR-0087 — A-stage closure ledger.
- ADR-0086 — carry-forward catalog.
- ADR-0058 — extension framework deferred (Sprint 6i+).
- ADR-0034 — Pi pin (Sprint 6h₈ row appended).

## Verification

- `ruff check` — clean.
- `pyright` — 8 baseline errors (intentional fixtures in
  `scripts/pyright_spike.py`); no new errors introduced. Pillow ships
  pyright type stubs since 10.0.
- `pytest` — **2269 → ~2339 net new tests pass** (19 image_detect +
  18 image_resize + ~12 file_processor net new + 4 migrations + 11
  find_most_recent + 14 continue_flag — exact totals confirmed via
  full pytest collection; 2 W4/W5 fold-in regression tests added on
  top of the W2 baseline `test_falls_through_to_older_when_newer_metadata_parse_fails`
  and `test_list_models_with_continue_no_session_emits_error`).
  All prior tests unchanged.
- RPC roster STAYS CLOSED at **29 supported / 0 deferred / 29 total**.
- Pi pin held at `734e08e…` (no advance — Sprint 6h₈ imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₈ / Phase 5a-iv (shipped).
