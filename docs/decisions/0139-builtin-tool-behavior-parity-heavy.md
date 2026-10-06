# 0139. Built-in Tool Behavior Parity — HEAVY (image resize, ensureTool, bash spawn-hook)

Status: Accepted
**Amended 2026-10-06 (#288): the rg/fd download reads the one offline predicate
(`PI_OFFLINE` or `AELIX_OFFLINE`) — see the amendment at the end (review round 1,
2026-10-07: the export now precedes every verb).**
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

## Amendment (2026-10-06, #288) — the download reads the one offline predicate

**What was wrong.** "`PI_OFFLINE` offline env name kept" (Aelix-additive
divergences above) stayed true for this module after ADR-0185 added
`AELIX_OFFLINE` as an alias in `cli/extension_install.py` and ADR-0230 reused
both names in `update_check.py`. Offline was then decided in three places that
read different names and different values, and `cli/entry.py` exported
`PI_OFFLINE=1` only from `--offline` or a set `PI_OFFLINE`. Measured on
`aab1f210`, with `AELIX_OFFLINE=1`, an empty agent dir and no `fd` on `PATH`:
`tools_manager._is_offline()` was `False`, `ensure_tool("fd", silent=False)`
printed `fd not found. Downloading...`, and a socket recorder saw
`DNS api.github.com:443`; a real `aelix --provider fake --model m1 --tools find
-p …` against a local model stub recorded the same lookup. The update check and
the extension catalog were already off. The switch failed open on exactly the
path that fetches an executable.

**Decision.** One predicate, `aelix_coding_agent.util.offline.is_offline`, read
by every path that reaches the network on its own: `ensure_tool` (rg/fd),
`update_check` (which re-exports it, so `update_check.is_offline` is the same
object), `extension_install._is_offline` (catalog refresh and index-less pypi
install), and the export, `offline.export_if_offline`, which sets
`PI_OFFLINE=1` when the flag or either name is on, so children inherit it.
`cli/entry.py` calls it before any verb is dispatched (as pi's `main.ts:576-580`
@ `b223082bb` exports before `handlePackageCommand` at `:597`) and again with the
parsed `--offline`. Values: `1`/`true`/`yes`
(any case) on, as pi's `isOfflineModeEnabled` reads them; `0`/`false`/`no`/`off`
and blank off, as ADR-0185 decided; **anything else on** — the one divergence
from pi's `tools-manager.ts`, in the safe direction (pi's own `version-check.ts`
and `model-runtime.ts` treat any set `PI_OFFLINE` as offline, so pi is not
consistent on this either). No third name (ADR-0230). Telemetry: there is no
sink to gate. Provider calls and the login wizard's model listing stay
ungated: they are requests the user made. So do MCP servers, including the
remote `http` and `sse` transports: measured on `3012bd3e` with
`AELIX_OFFLINE=1` (and again with `--offline`), a global `mcp.json` naming a
local `http`, a local `sse` and an `https://mcp.example.invalid/mcp` server
produced `POST /mcp`, `GET /sse` and `CONNECT mcp.example.invalid:443`. Not
gated, deliberately: each server is an endpoint the user declared — the
global `mcp.json`, `$AELIX_MCP_CONFIG`, a project `.aelix/mcp.json` only once
Project Trust admits it, or an extension the user installed — the same class
as the provider endpoint, and offline here (as in pi) means "nothing aelix
decides to fetch on its own", not an air gap. Pi has no MCP client to compare
against. The guide says so in its "does not touch" paragraph.

**Where the names can come from.** Neither name can be set by a cwd `.env` on
its own: `^PI_` and `^AELIX_` are in `runtime_bootstrap._DOTENV_NEVER`, so
`load_dotenv` refuses both by default. Neither is on ADR-0203's
`_DOTENV_LOCKED` floor, so a user who exports `AELIX_DOTENV_ALLOW=AELIX_OFFLINE`
(or `PI_OFFLINE`) lets that repo's `.env` supply it — ADR-0203 measured
`PI_OFFLINE` as hatchable and kept it that way on purpose. Either way a `.env`
value never replaces one already in the environment, so a repo can at most turn
offline on, never off.

**Readings that change — both kept by the owner after review round 1.**
`PI_OFFLINE=0`/`false`/`no`/`off` now means online: the CLI no longer rewrites
it to `1` (it used to put everything offline, against ADR-0185's own review
note; pi's `isTruthyEnvFlag` reads `0` as off too). An unrecognised value turns
offline **on** — fail closed, stricter than pi's `1`/`true`/`yes` — including
under `aelix extension …`, which used to read it as off; ADR-0185's review note
points here.

**Guard.** `tests/util/test_offline.py`: the value table for both names, every
consumer agreeing with the predicate for every value, the CLI export, a real
child interpreter launched by the real `extension install` path seeing
`PI_OFFLINE=1`, and an AST scan that fails if any module other than the
predicate names `PI_OFFLINE` or `AELIX_OFFLINE` in code (the one write is in
the predicate module too). `tests/util/test_tools_manager.py`: the download
gate for `rg` and `fd`, `silent` true and false, both names, with a recording
guard. `tests/conftest.py` starts every test with neither name set and undoes
any export. Sabotage: each consumer reverted to its old copy, and each wrong
form of the predicate, the export or the gate named in the commit, turns it
red.

**Review round 1 (2026-10-07).** Two gaps, both fixed in the same commit.
(1) The extension, `docs` and `status` verbs return before `parse_args`, and
the export ran after it: measured on `3012bd3e`, `AELIX_OFFLINE=1 aelix
extension install <path> --yes --no-verify` launched its installer (a fake
`uv` on `PATH`) with `PI_OFFLINE` unset, and `… --offline` with neither name —
so "children inherit it" was false for exactly the verb that launches
children. The export now runs first, and the extension verb's own `--offline`
counts, read the way pi reads its flag (`args.includes("--offline")`). (2) The
download-gate test covered `rg` with `silent=False` only, while `grep`/`find`
call `ensure_tool` with the default `silent=True`, and its guard raised an
`AssertionError` that `ensure_tool`'s best-effort `except Exception` swallowed:
a gate that ran only when not silent, or only for `rg`, passed all 507 tests
in the eight offline-related test files (measured on `3012bd3e`).
The test now covers every (tool, silent) pair and records calls instead.
