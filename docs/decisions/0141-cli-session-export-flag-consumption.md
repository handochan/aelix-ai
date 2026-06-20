# 0141. CLI Session/Export Flag Consumption (--session-dir / --session / --fork / --export)

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #5**: six CLI flags were parsed into `Args` but never
consumed — `--export`, `--fork`, `--session`, `--session-dir`, `--models`,
`--api-key`. `--fork`/`--session` appeared only in `_validate_continue_flag`,
so a fresh session was silently created instead of forking/opening; the
rest did nothing.

**Process note:** pi's `main.ts` flag-dispatch semantics were fetched
directly (`raw.githubusercontent.com` at the pin) and the aelix
session-repo / auth / export API surface was mapped before wiring. Two
flags turned out to be blocked on absent subsystems (see Deferred).

## Decision — wire the 4 flags with full API support

All in `cli/entry.py` (non-protected CLI layer):

- **`--session-dir <path>`** — Pi precedence `flag > PI_SESSION_DIR env >
  default`. `sessions_root = parsed.session_dir or get_session_dir()`
  passed to `JsonlSessionRepo(sessions_root=…)` (`None` → repo's
  `~/.aelix/sessions` default).
- **`--session <id|path>`** — new `_resolve_session_metadata` (Pi
  `resolveSessionPath`): path-like (`/`, `\`, or `.jsonl` — Pi's exact
  structural heuristic, no on-disk existence check) →
  `load_jsonl_session_metadata`; else session-id prefix matched
  cwd-local-first then global via `repo.list`. → `repo.open(meta,
  cwd_override=cwd)`.
- **`--fork <id|path>`** — same resolution → `repo.fork_from(meta, cwd)`
  (clones into cwd, stamps `parent_session_path`).
- **`--export <src> [out]`** — new `_run_export`: terminal early-exit
  action (placed right after `--list-models`, before mode/stdin
  processing, matching Pi's `exportFromFile`). Loads the session →
  `build_context().messages` → `export_html(messages, out,
  session_basename)`; `out = parsed.messages[0]` or the
  `aelix-session-<basename>.html` default. Prints the resolved path.

`_build_session` gained `fs` + `cwd` params and the `--session`/`--fork`
branches; resolution failures raise `SessionError("not_found", …)` which
the main flow catches and surfaces as `Error: …` + exit 1 (no traceback).

## Deferred (blocked on absent subsystems — NOT silently ignored)

Both emit a one-line stderr warning when set (honest, not inert):

- **`--api-key <key>`** — Pi calls `authStorage.setRuntimeApiKey(
  model.provider, key)`, but the aelix **agent-run path constructs no
  AuthStorage** (`_build_harness_options` uses a bare `resolve_model`; no
  `get_api_key_and_headers` wired — the Phase 4 provider-auth
  carry-forward). Wiring it requires that auth plumbing first. The
  `ModelRegistry`/`AuthStorage` from P0 #4 only exist on the
  `--list-models` branch.
- **`--models <patterns>`** — Pi's `--models` is **scoped-model patterns**
  (`resolveModelScope` glob match for Ctrl+P cycling), NOT a models.json
  path. That is a whole subsystem (overlaps the P1 `/scoped-models` gap).

## Intentional divergences (documented)

1. **`--session` global (cross-project) match**: Pi prompts
   `promptConfirm("Fork this session…")` and forks on yes / exits on no.
   Aelix opens the resolved session directly (an interactive mid-startup
   prompt is deferred with the broader selector-UI work). Path/local
   resolution is faithful.

## Consequences

- `--session-dir`/`--session`/`--fork`/`--export` now do real work;
  `--api-key`/`--models` warn rather than silently drop the flag.
- Tests: new `tests/cli/test_session_flags.py` (22 tests) — resolution
  (path / id-prefix / cross-cwd-global / not-found / bare-filename-as-id),
  `_build_session` (no-session / open-by-path+content+cwd_override /
  open-by-id / fork-lineage / not-found-raises / default-create),
  `_run_export` (render + default path) + e2e (`--export`, bad-path exit 1,
  `--session-dir` flag-vs-env precedence, `--continue` reopen, deferred
  warnings + key non-leak, `--session` not-found exit 1 + message).

## Adversarial review (4-lens workflow)

8 confirmed findings: 1 production (the `--session`/`--fork` path-detection
heuristic had an extra `os.path.exists(arg)` disjunct diverging from Pi —
removed, so a bare session-id colliding with a cwd filename is no longer
mis-routed to the path loader) + 7 test-coverage/quality gaps on correct
code (cross-cwd id resolution, flag-vs-env precedence, `--continue` reopen,
not-found message, key non-leak, cwd_override/content load) — all addressed.
- Gate green; the 3 pre-existing `test_append_system_prompt` failures
  (repo `AGENTS.md` → `append_system_prompt`, cwd-coupling) are unrelated.
