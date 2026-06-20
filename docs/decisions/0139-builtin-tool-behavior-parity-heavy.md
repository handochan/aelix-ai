# 0139. Built-in Tool Behavior Parity — HEAVY (image resize, ensureTool, bash spawn-hook)

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

The HEAVY tail of gap-inventory **P0 #3** — the three items deferred from
ADR-0137 (Wave 1) and ADR-0138 (Wave 2) because they need new subsystems
(network download, image processing, env plumbing) rather than per-tool
rewrites:

1. **read image resize + `ctx.model`** — Pi resizes images to 2000×2000 / 4.5 MB
   before sending them to the model, emits a coordinate-mapping dimension note,
   and a non-vision-model omission note keyed on `ctx.model.input`.
2. **`ensureTool` rg/fd auto-download** — Pi guarantees ripgrep/fd by downloading
   the platform release binary on demand, so `grep`/`find` honor `.gitignore`.
3. **bash `commandPrefix` / `spawnHook` / `shellPath`** — Pi's `BashToolOptions`
   plumbing for command wrapping, env/cwd rewriting, and explicit shell path.

**Process note:** per the ADR-0138 lesson (delicate exact ports are unreliable to
delegate), all four pi sources (`read.ts`, `bash.ts`, `image-resize.ts`,
`tools-manager.ts`, `shell.ts`, `config.ts`) were fetched **directly into the
main context** via `raw.githubusercontent.com` at the pin and ported by hand.
The image-resize algorithm was **already ported** in ADR-0092
(`util/image_resize.py`, Pillow), so item 1 was a wiring task, not a new port.

## Decision

### Item 1 — read image resize + `ctx.model` non-vision note

- **`aelix_ai.tools.ToolExecutionContext`** (non-protected `aelix-ai`): new
  optional `model: Any | None = None` field. Pi parity `ctx.model` on the tool
  execute signature. `Any`-typed to avoid a hard import coupling onto streaming.
- **`aelix_agent_core.loop.py`** (PROTECTED core, **user-authorized** — `ctx.model`
  was explicitly named in the sprint scope): single-line wiring
  `model=config.model` in the `ToolExecutionContext(...)` construction at the
  tool-execution site. `config.model` was already in scope (used at `:240`/`:282`).
- **`tools/read.py`**: rewrote the image branch to Pi `read.ts:249-277` —
  `_get_non_vision_image_note(ctx.model)` (`!model || "image" in model.input`),
  `auto_resize_images` option (default `True`), `resize_image` + `format_dimension_note`
  (the ADR-0092 port), `Read image file [mime]` text note, and the canonical
  `ImageContent(mime_type, data)` shape (Pi `{type:"image", data, mimeType}`) in
  place of the legacy `source=` data URL. Resize-failure (returns `None`) yields a
  text-only note with **no** image attachment (Pi parity).

### Item 2 — bash `commandPrefix` / `spawnHook` / `shellPath`

- **`util/shell_env.py`** (new): `get_shell_env()` — Pi `getShellEnv`
  (`shell.ts:108-120`): process env with `get_bin_dir()` prepended to `PATH`
  (case-insensitive key, idempotent). Lazy-imports `cli.config` to avoid the
  `bash → shell_env → cli.config → cli/__init__ → repl → bash` import cycle.
- **`tools/bash.py`**: `BashSpawnContext` dataclass + `BashSpawnHook` type
  (`bash.ts:129-135`); `_resolve_spawn_context` (base env = `get_shell_env`, then
  the hook). `create_bash_tool` reads `command_prefix` / `shell_path` / `spawn_hook`
  from options. `command_prefix` is prepended `${prefix}\n${command}`. `shell_path`
  is validated in `_resolve_shell` (raises Pi's `Custom shell path not found: {path}`
  message). The spawn context's `command`/`cwd`/`env` flow to `operations.exec`;
  `_LocalBashOperations` falls back to `get_shell_env()` when no env is supplied
  (Pi `env ?? getShellEnv()`).

### Item 3 — `ensureTool` rg/fd download + grep/find wiring

- **`cli/config.py`**: `get_bin_dir()` — Pi `getBinDir` (`config.ts:483-485`,
  `~/.aelix/agent/bin`).
- **`util/tools_manager.py`** (new): port of `tools-manager.ts` — `TOOLS` config
  (fd `sharkdp/fd`, rg `BurntSushi/ripgrep`; platform/arch asset-name matrix),
  `get_tool_path` (local bin-dir → system-PATH `--version` probe), `ensure_tool`
  (existing → `PI_OFFLINE` skip → Android/Termux skip → download), `_download_tool`
  (GitHub API latest version → release download → extract → recursive binary
  discovery → move + `chmod 755`). Blocking download runs in `asyncio.to_thread`.
- **`tools/grep.py` / `tools/find.py`**: `await ensure_tool("rg"/"fd")` supplies
  the resolved binary to `_try_ripgrep` / `_try_fd`; descriptions restore Pi's
  verbatim **"Respects .gitignore."**. The pure-Python fallback is retained (Pi
  hard-errors when rg/fd is unavailable) as a documented intentional divergence.

## Aelix-additive divergences (documented, not defects)

- **Pillow vs Photon/WASM** for resize (inherited from ADR-0092).
- **Python stdlib extraction** (`tarfile` `filter="data"` / `zipfile` with
  traversal validation) instead of shelling out to `tar`/`unzip`/`powershell` —
  more portable + path-traversal-safe.
- **`urllib`** download instead of `fetch`; **`PI_OFFLINE`** offline env name kept.
- **grep/find pure-Python fallback** when rg/fd unavailable (Pi rejects). Means
  ".gitignore respect" is the default-case truth, not the offline-fallback truth.
- **best-effort Android detection** (`sys.platform == "android"` /
  `getandroidapilevel`) — Python cannot read Node's `os.platform() === "android"`.
- **`$SHELL`-first** shell resolution retained (pre-existing W4 divergence),
  ordered *after* an explicit `shell_path`.

## Consequences

- The read tool now returns `ImageContent(mime_type, data)`; both the OpenAI
  (`item.data` preferred, `openai_completions.py:208`) and Anthropic
  (`block.data if block.data else block.source`) adapters already prefer this
  shape (it is what `cli/file_processor.py` has emitted since ADR-0092), so there
  is no serialization regression. The legacy `test_read_image_emits_data_url_base64`
  was replaced by a 6-test suite (resize / no-resize / non-vision / vision /
  resize-failure / large-resized dimension note).
- A session-wide `tests/conftest.py` redirects the bin dir to a temp + stubs the
  network primitives so **no** test ever downloads a binary or pollutes
  `~/.aelix/agent/bin`; `tests/tools/conftest.py` forces the python fallback for
  the existing grep/find behavior tests (module-object `monkeypatch`, reload-safe).
  The download path is covered by mocked-I/O tests in `tests/util/test_tools_manager.py`.
- `--offline` / `PI_OFFLINE` now also gates rg/fd download (previously inert for
  tools). On a first online `grep`/`find` with no system rg/fd, the binary is
  fetched once into `~/.aelix/agent/bin` and reused thereafter.

## Review (adversarial workflow)

A 6-lens adversarial review (parity ×3, security, correctness, tests; each
finding verified by a default-refute skeptic) produced 11 findings, **5
confirmed** (0 BLOCKING). All addressed:

- **MAJOR (grep, pre-existing since Sprint 5b)** — the rg branch capped on raw
  output LINES, not matches. With `-C context`, rg interleaves context lines +
  `--` separators, so the line cap dropped real matches and mis-fired the limit
  notice. The `ensureTool` work elevated this from latent (rg often absent →
  correct python fallback) to active (rg now the default path). **Fixed:**
  `_relativize_rg_line` now reports `is_match` (lineno separator `:` vs `-`);
  `_try_ripgrep` caps on **match** count, keeps each kept match's context, and
  block-trims the partial next block on break. Match lines parse reliably via the
  `:` branch (paths rarely contain `:`); ambiguous context lines (path contains
  `-`) fall through as non-matches so they never over-count. Residual finer
  divergence (context-line *grouping* is rg-merged vs pi's per-match `formatBlock`,
  and a `-`-in-path context line displays absolute) is a documented limitation of
  text-mode rg — full fidelity needs the `--json` port (tracked follow-up).
- **MINOR (security, this sprint's code)** — zip-member containment used
  `str.startswith`, which accepts a sibling dir sharing the prefix. **Fixed** to
  `Path.is_relative_to` (not currently exploitable — randomized extract-dir name +
  `zipfile` ignores symlink members + trusted release URL — but the right primitive).
- **MINOR (tests)** — the rg `-H` lock-in test skipped when no system rg, and the
  fd-backed `_try_fd` path had no coverage. **Fixed:** both now run deterministically
  via a stubbed `subprocess.run` (asserts the `-H` / `--no-require-git` parity flags
  are passed + relativization), plus match-count-cap tests.
- **INFO (grep)** — zero matches emitted empty text. **Fixed** to pi's
  `"No matches found"` (grep.ts:308-310), mirroring find's empty-result guard.

## Tests

- `tests/tools/test_read_tool.py` (+6 image tests), `tests/tools/test_bash_tool.py`
  (+6 spawn-hook/prefix/shell-path tests), `tests/tools/test_grep_tool.py`
  (+ "No matches found" + rg `-H` lock-in + 2 match-count-cap tests),
  `tests/tools/test_find_tool.py` (+ fd-backed relativize + exact-limit boundary),
  `tests/conftest.py` (new, session network/bin guard), `tests/tools/conftest.py`
  (new), `tests/util/test_tools_manager.py` (new, 25 incl. zip sibling-prefix),
  `tests/util/test_shell_env.py` (new, 4). Gate green: **3280 passed, 1 skipped
  (pre-existing, unrelated), 0 failures** (the rg/fd lock-in tests no longer skip —
  they run deterministically via stubbed subprocess). No regressions;
  `~/.aelix/agent/bin` stays empty.
