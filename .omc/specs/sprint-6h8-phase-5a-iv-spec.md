# Sprint 6h₈ — Phase 5a-iv — Image + Migrations + --continue BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."

**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (NO advance this sprint).

**Closes carry-forwards:**
- ADR-0089 P-387 (image branch in `file_processor.py`)
- ADR-0089 P-393 (`--continue` / `-c` auto-resume)
- ADR-0091 (migrations carry-forward)
- ADR-0089 P-403 (`takeOverStdout` decision — resolved here as SKIP)

**Carry-forward to:** Phase 5b TUI (`--resume` interactive picker, theme reads, `branchSummary.skipPrompt`) + Sprint 6i+ (extension framework, `takeOverStdout` print-mode wrapper per ADR-0058).

---

## §0 — W0 findings (P-433 ~ P-444)

**P-433** — Pi `cli/file-processor.ts` is 100 LOC (Agent A). Image branch occupies lines 48-76 (~27 LOC); text branch ~24 LOC; remainder is dispatch and helpers.

**P-434** — Pi `utils/image-resize.ts` is 176 LOC (Agent A). Photon WASM backend; algorithm: (1) load + apply EXIF rotation (manual TIFF parsing), (2) check-already-compliant fast path, (3) initial-dim downscale toward 2000×2000, (4) iterative encode search trying PNG + 5 JPEG qualities (80/85/70/55/40), (5) dimension fallback 0.75× when none fit under 4.5 MB.

**P-435** — Pi `utils/mime.ts` is 74 LOC (Agent A). Magic-byte detection: JPEG (`FF D8 FF` first 3 bytes, 4th byte != `0xF7`), PNG (`89 50 4E 47 0D 0A 1A 0A` AND not animated APNG), GIF (ASCII `GIF` at offset 0), WebP (`RIFF` at offset 0 + `WEBP` at offset 8). Reads first 4100 bytes from disk.

**P-436** — Aelix `ImageContent` is already Pi parity (Sprint 6b, `packages/aelix-ai/src/aelix_ai/messages.py:38-52`): `mime_type` + `data` (base64) + legacy `source`. Both Anthropic and OpenAI providers already wire it through; HTML export already renders. SettingsManager `ImageSettings.auto_resize` already present (Sprint 6h₇b).

**P-437** — Aelix `file_processor.py:91-99` currently skips images with a stderr warning (ADR-0089 P-387 stub). Sprint 6h₈ REPLACES this stub with real image processing.

**P-438** — `Pillow>=11.0,<12.0` to be added to `packages/aelix-coding-agent/pyproject.toml`. Use `Image.open` + `ImageOps.exif_transpose` (replaces ~83 LOC of manual EXIF/TIFF parsing in Pi) + `image.thumbnail((w,h), Image.LANCZOS)` + `image.save(buf, format=, quality=)`. Pyright type stubs bundled since Pillow 10.0.

**P-439** — Pi `migrations.ts` is 315 LOC (Agent B) with 7 cleanup migrations all targeting LEGACY data Aelix never had (oauth.json → auth.json, settings.apiKeys → auth.json, v0.30.0 session bug, commands → prompts rename, keybindings rename, tools → bin relocation, extension deprecation). Aelix `AuthStorage` (Sprint 6c) ships fresh, `JsonlSessionStorage` (Sprint 4a) v3 strict from day one, keybindings/extensions are Phase 5b deferred. NO-OP STUB adequate (~30 LOC).

**P-440** — Pi `main.ts:280-281` `--continue` dispatch → `SessionManager.continueRecent(cwd, sessionDir)` → `findMostRecentSession()` (Pi `core/session-manager.ts:480-493`) = readdir + `isValidSessionFile` filter + mtime DESC sort + first or null.

**P-441** — Aelix `JsonlSessionRepo` (Agent C) currently MISSING `find_most_recent` method. Aelix `list()` sorts by `created_at` — DIVERGES from Pi's mtime semantics. Sprint 6h₈ adds `find_most_recent(cwd)` mirroring Pi mtime semantics; existing `list()` `created_at` divergence is documented in ADR-0092 (kept as-is, no rewrite).

**P-442** — `--continue` edge cases (Agent C): 5 conflicts require validation (`--no-session`, `--session <path>`, `--fork <id>` INCOMPATIBLE; `--print`, `--mode rpc`, `--session-dir`, positional messages COMPATIBLE; empty cwd → silent new-session fallback per Pi parity).

**P-443** — `takeOverStdout` SKIP (Agent E): Aelix `rpc_mode.py:1839` already uses `contextlib.redirect_stdout(sys.stderr)` (Python equivalent of Pi's `takeOverStdout`). Aelix builtin tools have ZERO `print()` / `sys.stdout` writes. Print-mode user-extension corruption risk is deferred until the extension framework lands (Sprint 6i+, ADR-0058). Documented in ADR-0092 §"Deferred Items".

**P-444** — Pyright baseline: 8 errors all from `scripts/pyright_spike.py` (intentional fixtures). Adding Pillow MUST NOT introduce new pyright errors (bundled type stubs since Pillow 10.0).

---

## §A — Scope LOC table

| Item | Files | Prod LOC | Test LOC |
|---|---|---|---|
| §B `image_detect.py` + `image_resize.py` + `file_processor.py` wire | 3 prod + 1 dep | ~350-400 | ~280-420 |
| §C `migrations.py` stub | 1 prod | ~30-50 | ~30 |
| §D `--continue` + `find_most_recent` + dispatch + validation | 2 prod | ~145-205 | ~130-200 |
| §E `takeOverStdout` SKIP | (docs only) | 0 | 0 |
| §G ADR-0092 + ADR-0034/README amends | 3 docs | (docs) | (docs) |
| **Total** | — | **~525-655** | **~440-650** |

---

## §B — Pi `file-processor.ts` IMAGE branch + `utils/image-resize.ts` + `utils/mime.ts` port

### B.1 Source files (Pi, READ-ONLY reference)

- `packages/coding-agent/src/cli/file-processor.ts` (100 LOC; image branch lines 48-76)
- `packages/coding-agent/src/utils/image-resize.ts` (176 LOC — Photon WASM-based)
- `packages/coding-agent/src/utils/mime.ts` (74 LOC — magic-byte image detection)

### B.2 Target files (Aelix)

- **NEW** `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_resize.py` (~200-250 LOC) — Pillow-based port
- **NEW** `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_detect.py` (~100-150 LOC) — magic-byte mime detection (port of `mime.ts`)
- **MODIFY** `packages/aelix-coding-agent/src/aelix_coding_agent/cli/file_processor.py` — replace image-skip stub (current lines 91-99) with real image processing via `image_resize.py`
- **MODIFY** `packages/aelix-coding-agent/pyproject.toml` — add `Pillow>=11.0,<12.0` to `dependencies` list
- **MODIFY** `uv.lock` — sync after Pillow add

### B.3 Algorithm spec (BINDING — Pi parity)

- **Max dimensions:** 2000 × 2000 px (Pi `image-resize.ts:6-7, 27`)
- **Max encoded size:** 4.5 MB base64 (Pi `image-resize.ts:8, 23`)
- **JPEG quality steps (in order):** 80 (default), 85, 70, 55, 40 (Pi `image-resize.ts:121`)
- **Filter:** Lanczos3 (Pi `image-resize.ts:108` `photon.SamplingFilter.Lanczos3` → Aelix `PIL.Image.Resampling.LANCZOS`)
- **EXIF auto-orientation:** Pillow `ImageOps.exif_transpose()` (Aelix-additive divergence vs Pi's manual TIFF parser; saves ~83 LOC — documented in ADR-0092)
- **Iterative encode search** (Pi `image-resize.ts:121-152`): try PNG + 5 JPEG quality candidates in order, pick first under 4.5 MB; if none fit, scale dimensions × 0.75 and retry; terminate at 1×1 or when no progress is made
- **Give-up condition:** Return `None` (Python) if image cannot fit under 4.5 MB even at 1×1

### B.4 Magic-byte detection (BINDING — Pi parity, in `image_detect.py`)

- **JPEG:** `FF D8 FF` first 3 bytes (Pi `mime.ts:6-9`); 4th byte must NOT equal `0xF7` (rejects truncated JPEG variant)
- **PNG:** `89 50 4E 47 0D 0A 1A 0A` (Pi `mime.ts:10-12`); MUST also fail `is_animated_png()` check (rejects animated APNG)
- **GIF:** ASCII `GIF` (`47 49 46`) at offset 0 (Pi `mime.ts:13-15`)
- **WebP:** `RIFF` (`52 49 46 46`) at offset 0 + `WEBP` (`57 45 42 50`) at offset 8 (Pi `mime.ts:16`)
- **All other formats:** return `None` (no detection match → not an image)
- **Read window:** first 4100 bytes (sufficient for APNG `acTL` chunk scan)

### B.5 Aelix divergence from current `file_processor.py` (BINDING)

- Aelix currently detects images by **extension** (`.png` / `.jpg` / `.jpeg` / `.gif` / `.webp`) per `_IMAGE_EXTENSIONS` at `file_processor.py:24-26`
- Sprint 6h₈ MUST switch to **magic-byte detection** (Pi parity) — read first 4100 bytes, dispatch via `image_detect.detect_image_mime(bytes) -> str | None`
- Extension check stays as a fast-path optimization (skip non-image extensions before opening file)
- If extension says image but magic bytes disagree → trust magic bytes (return None / treat as text per Pi behavior)

### B.6 `ImageContent` shape

Already Aelix parity (Sprint 6b, `packages/aelix-ai/src/aelix_ai/messages.py:38-52`): `mime_type` + `data` fields ready. NO type changes needed in this sprint.

### B.7 `format_dimension_note(result)` helper (Pi `image-resize.ts:166-174`)

- Port verbatim — emits `[Image: original WxH, displayed at WxH. Multiply coordinates by S to map to original image.]`
- Used for tool-side coordinate mapping (e.g., screenshot tools); included so future Phase 5b TUI / screenshot tools can call it without re-porting
- Place in `image_resize.py` (same module as `resize_image`)

### B.8 Return shape

`resize_image(data: bytes) -> ResizedImage | None` where `ResizedImage` is a dataclass / TypedDict with fields: `data: bytes` (encoded), `mime_type: str` (`image/png` or `image/jpeg`), `original_width: int`, `original_height: int`, `displayed_width: int`, `displayed_height: int`, `scale: float`. Exact name/shape `[W2 to verify]` — must match Pi `ResizedImage` interface where reasonable Python translation allows.

### B.9 `file_processor.py` wire (replaces current lines 91-99 stub)

After fast-path extension filter, open file, read first 4100 bytes, call `image_detect.detect_image_mime`. If image: read full bytes, call `image_resize.resize_image(bytes)`. If `None` returned (give-up), emit stderr warning (matching current behavior message style) and skip. If success: yield `ImageContent(mime_type=..., data=base64.b64encode(...))` block.

---

## §C — Pi `migrations.ts` NO-OP STUB

### C.1 Decision

Per W0 Agent B analysis: Aelix has ZERO legacy data. Pi's `runMigrations()` orchestrates 7 cleanup migrations all targeting LEGACY data that Aelix never had (see P-439 enumeration). NO-OP STUB is adequate.

### C.2 Target

- **NEW** `packages/aelix-ai/src/aelix_ai/migrations.py` (~30-50 LOC)
  - Module-level docstring explaining the no-op rationale (Aelix fresh + each Pi target is either deferred to Phase 5b or absent by design)
  - `async def run_migrations(cwd: str | Path) -> dict[str, list[Any]]` returning `{"migrated_auth_providers": [], "deprecation_warnings": []}` (mirrors Pi `runMigrations` return shape so future call sites can match without re-typing)
  - Module docstring should cite ADR-0092 and reference Pi `migrations.ts:1-315`
- **NO call site** in `entry.py` for now (no startup hook needed; document Phase 5c future integration in ADR-0092)
- **NEW** `tests/test_migrations.py` (~30 LOC) — smoke test verifying return shape `{"migrated_auth_providers": [], "deprecation_warnings": []}` is empty lists

### C.3 No future call site is required

Phase 5b TUI may eventually call `run_migrations(cwd)` from a startup hook; this sprint leaves that integration deferred. The stub is shipped so the symbol exists for downstream wiring without code archaeology.

---

## §D — `--continue` / `-c` auto-resume

### D.1 Source (Pi)

- `main.ts:280-281` dispatch: `if (parsed.continue) return SessionManager.continueRecent(cwd, sessionDir)`
- `core/session-manager.ts:480-493` `findMostRecentSession(sessionDir): string | null` — readdir + `isValidSessionFile` filter + sort by mtime DESC + return first or null
- `core/session-manager.ts:464-478` `isValidSessionFile(path)` — opens header line, checks `type: "session"` + `id` non-empty

### D.2 Target (Aelix)

**NEW method** `JsonlSessionRepo.find_most_recent(cwd: str) -> JsonlSessionMetadata | None` in `packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py`:
- mtime-descending sort (NOT `created_at` — current `list()` divergence, document in ADR-0092)
- cwd-filtered (use existing cwd-encoded directory layout)
- `_is_valid_session_file(path) -> bool` private helper — read JSONL header line, validate `type == "session"` AND `id` non-empty
- Returns metadata for first valid file or `None`

**MODIFY** `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` — add `--continue` short-circuit before `_build_session()`:
- If `parsed.continue_session`: call `repo.find_most_recent(cwd)`. If found, `repo.open(metadata)` and proceed; else fallback to `_build_session(parsed, repo)` (Pi parity silent fallback)
- If `parsed.resume`: raise `NotImplementedError` with a stderr diagnostic pointing to Phase 5b TUI picker
- If `parsed.fork`: keep existing path (already wired Sprint 6h₆)

**ADD argument validation** in `entry.py` (or `args.py` post-parse) with clear stderr error messages:
- `--continue` + `--no-session` → error: incompatible
- `--continue` + `--session <path>` → error: incompatible
- `--continue` + `--fork <id>` → error: incompatible
- `--continue` + `--print "msg"` → OK (append to most recent, then run print mode)
- `--continue` + `--mode rpc` → OK (RPC against most recent)
- `--continue` with zero sessions in cwd → silent fallback to new session (Pi parity, NOT an error)

### D.3 Tests

- **NEW** `tests/session/test_find_most_recent.py` (~80-120 LOC) — mtime sort + cwd filter + `_is_valid_session_file` filter + empty case
- **NEW** `tests/cli/test_continue_flag.py` (~50-80 LOC) — `entry.py` dispatch + 5 edge case validation cases (3 errors, 2 ok, 1 silent fallback)

---

## §E — `takeOverStdout` SKIP (Aelix-additive divergence — documentation only)

### E.1 Decision

Per W0 Agent E analysis:
- Aelix RPC mode already implements `contextlib.redirect_stdout(sys.stderr)` (Python equivalent of Pi's `takeOverStdout`) at `rpc_mode.py:1839`
- Aelix JSON-mode builtin tools have ZERO `print()` or `sys.stdout` writes (audited Agent E)
- Print mode could theoretically be corrupted by user extensions, but the extension framework is itself deferred to ADR-0058 (Sprint 6i+)

### E.2 Action

Document in ADR-0092 §"Deferred Items":

> `takeOverStdout` — Aelix-equivalent already present in RPC mode (`contextlib.redirect_stdout` at `rpc_mode.py:1839`); print-mode wrapper deferred until extension framework lands (Sprint 6i+, ADR-0058).

### E.3 Code changes

**NONE.** No production code changes for §E. This section is documentation-only and exists to formally close ADR-0089 P-403.

---

## §F — Tests aggregation

| Test file | LOC | Items covered |
|---|---|---|
| NEW `tests/util/test_image_detect.py` | ~80-120 | All 4 magic-byte detectors (JPEG/PNG/GIF/WebP) + animated PNG rejection + truncated JPEG (0xF7) rejection + non-image returns None |
| NEW `tests/util/test_image_resize.py` | ~120-180 | aspect-ratio preserve, Lanczos3 quality, 5 JPEG quality steps, encoded-size fallback chain, EXIF auto-orient (8 orientations), 1×1 give-up, None return on unsupported |
| MODIFIED `tests/cli/test_file_processor.py` | ~80-120 added | image branch end-to-end (replaces current image-skip warning test) + magic-byte detection integration |
| NEW `tests/test_migrations.py` | ~30 | `run_migrations()` smoke test returning empty lists |
| NEW `tests/session/test_find_most_recent.py` | ~80-120 | mtime sort + cwd filter + invalid-header skip + empty case |
| NEW `tests/cli/test_continue_flag.py` | ~50-80 | `--continue` dispatch + 5 edge case conflicts |

**Total new/modified test LOC:** ~440-650.

---

## §G — ADR-0092 + ADR-0034/README amends

### G.1 NEW `docs/decisions/0092-sprint-6h8-phase-5a-iv.md`

- **Status:** Accepted
- **Date:** 2026-05-22
- **Decisions:** per §B / §C / §D / §E above
- **Pi citations:** `cli/file-processor.ts:48-76`, `utils/image-resize.ts:1-176`, `utils/mime.ts:1-74`, `main.ts:280-281`, `core/session-manager.ts:464-493`, `migrations.ts:1-315`
- **Aelix-additive divergences (enumerate explicitly):**
  1. `Pillow` (Python) instead of Photon WASM (Pi)
  2. `ImageOps.exif_transpose` instead of manual TIFF parsing (~83 LOC saved)
  3. `migrations.py` is NO-OP STUB (Aelix fresh, no legacy data)
  4. `find_most_recent` mtime sort (Pi parity) vs existing `list()` `created_at` sort — divergence documented, `list()` kept as-is
  5. `takeOverStdout` skipped entirely (Aelix RPC mode already protects via `contextlib.redirect_stdout`; print-mode deferred to extension framework Sprint 6i+)
  6. Magic-byte detection (Pi parity) replaces current extension-based detection (`file_processor.py:24-26`)
- **Deferred items (Phase 5b / Sprint 6i+):**
  - `--resume` interactive TUI picker (Phase 5b)
  - `takeOverStdout` print-mode wrapper (Sprint 6i+ extension framework, ADR-0058)
  - `--fork` interactive picker UI (already Sprint 6h₆ headless path; picker UI Phase 5b)
- **Reference companions:** ADR-0091, ADR-0090, ADR-0089, ADR-0087, ADR-0086, ADR-0034

### G.2 AMEND `docs/decisions/0034-pi-reference-version-pin.md`

Append Sprint 6h₈ row to the sprint-tracking table; **no Pi pin advance** this sprint.

### G.3 AMEND `docs/decisions/README.md`

Append ADR-0092 row to the ADR index.

---

## §H — Atomic commit plan (EXACTLY 6 commits)

**Commit 1** — `feat(util): port Pi utils/mime.ts → image_detect.py magic-byte detection (Sprint 6h₈ §B P-435)`
- `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_detect.py` (NEW)
- `tests/util/test_image_detect.py` (NEW)

**Commit 2** — `feat(util): port Pi utils/image-resize.ts → image_resize.py + Pillow dep (Sprint 6h₈ §B P-434/P-438)`
- `packages/aelix-coding-agent/pyproject.toml` (MODIFY — add `Pillow>=11.0,<12.0`)
- `uv.lock` (MODIFY — sync after Pillow add)
- `packages/aelix-coding-agent/src/aelix_coding_agent/util/image_resize.py` (NEW)
- `tests/util/test_image_resize.py` (NEW)

**Commit 3** — `feat(cli): file_processor.py image branch wire + magic-byte detection (Sprint 6h₈ §B)`
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/file_processor.py` (MODIFY — replace image-skip stub, add magic-byte detection path)
- `tests/cli/test_file_processor.py` (MODIFY — replace image-skip warning test with real image processing test)

**Commit 4** — `feat(migrations): NO-OP stub (Aelix fresh — Sprint 6h₈ §C P-439)`
- `packages/aelix-ai/src/aelix_ai/migrations.py` (NEW)
- `tests/test_migrations.py` (NEW)

**Commit 5** — `feat(cli): --continue / -c auto-resume + JsonlSessionRepo.find_most_recent (Sprint 6h₈ §D)`
- `packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py` (MODIFY — add `find_most_recent` method + `_is_valid_session_file` helper)
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` (MODIFY — `--continue` short-circuit before `_build_session` + flag validation)
- `tests/session/test_find_most_recent.py` (NEW)
- `tests/cli/test_continue_flag.py` (NEW)

**Commit 6** — `docs: ADR-0092 (Sprint 6h₈ closure) + ADR-0034/README amends`
- `docs/decisions/0092-sprint-6h8-phase-5a-iv.md` (NEW)
- `docs/decisions/0034-pi-reference-version-pin.md` (AMEND — Sprint 6h₈ row)
- `docs/decisions/README.md` (AMEND — ADR-0092 row)

Each commit: HEREDOC message + trailer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## §I — Verification gates

### I.1 Per-commit gates (run after each commit)

- `uv run ruff check 2>&1 | tail -2` — clean
- `uv run pyright 2>&1 | tail -3` — 8 baseline preserved (Pillow type stubs bundled, no new errors expected)
- `uv run pytest 2>&1 | tail -3` — current 2269 + N new tests (estimate 50-100 new tests across §B/§C/§D)

### I.2 Smoke verifications

**After C2 smoke:**
```
uv run python -c "from aelix_coding_agent.util.image_resize import resize_image; print(resize_image.__doc__[:50])"
```
→ no import error; docstring prefix prints.

**After C3 smoke:** create a small test PNG, pipe through `aelix --print --file @test.png "describe"` (or test-harness equivalent). Verify image is base64-encoded and sent (NOT skipped with stderr warning).

**After C5 smoke:**
- `uv run aelix --continue` in a cwd with prior sessions → opens most recent (validate by inspecting session file path / id)
- `uv run aelix --continue` in an empty cwd → silent fallback to new session (no error, no warning)
- `uv run aelix --continue --no-session` → exits with incompatible-flags error
- `uv run aelix --continue --session /path` → exits with incompatible-flags error
- `uv run aelix --continue --fork <id>` → exits with incompatible-flags error

### I.3 Final gates (before C6)

- All 5 prior commits green on lint/types/tests
- Manual smoke verifications recorded in W3 verification notes
- `uv run pytest -q` summary captured for W3 reviewer

---

## §J — Workflow

W2 executor (opus) → W3 verification → W4 code-reviewer (opus) → W5 critic (opus, Pi parity audit via `curl` against pinned Pi commit) → W6 commits (NO push).

---

## §K — Out-of-scope (BINDING — 10 non-goals)

1. **NO Phase 5b TUI work** (`--resume` picker, theme reads, `branchSummary.skipPrompt`) — deferred
2. **NO extension framework** — Sprint 6i+
3. **NO `takeOverStdout` real port** — deferred (§E SKIP decision)
4. **NO real migrations port** — Aelix fresh, no-op stub adequate
5. **NO new RPC commands**
6. **NO Pi pin advance**
7. **NO change to `ImageContent` shape** (already Aelix parity Sprint 6b)
8. **NO `--fork` picker UI** — interactive picker is Phase 5b
9. **NO photon WASM port** — Pillow is the Aelix equivalent
10. **NO settings hooks beyond `auto_resize`** — already present (Sprint 6h₇b `ImageSettings`)

---

## §L — Phase 5b preview (next sprint after 6h₈)

After 6h₈, only Phase 5b TUI items remain in carry-forward:
- `--resume` interactive picker (depends on TUI surface area)
- Theme reads from SettingsManager
- `branchSummary.skipPrompt` UI gating

Phase 5b is the final non-trivial Pi parity gap before Aelix reaches feature-complete vs Pi. After Phase 5b closes, remaining carry-forwards are extension framework (Sprint 6i+, ADR-0058) and any minor cleanup items.

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
