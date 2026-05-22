# 0089. Sprint 6h₆ Phase 5a-i + 5a-ii — Non-Interactive CLI Closure

Status: Accepted (Sprint 6h₆ / Phase 5a-i + 5a-ii / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

ADR-0086 closed A 단계 (Phase 4 strict Pi-parity superset) end-to-end
with SUPPORTED 29 / DEFERRED 0 / total 29 on the RPC discriminator
union. ADR-0087 closed the Sprint 6h₅d non-UI carry-forward roster.
B 단계 (Phase 5 — CLI / runner-mode / interactive surface) opens
here with Sprint 6h₆.

Sprint 6h₆ ports the **non-interactive** half of the Pi CLI entry
point (`main.ts` 716 LOC) — the `--print` / `--mode text|json|rpc`
/ `--help` / `--version` paths plus the supporting hand-rolled arg
parser (`cli/args.ts` 354 LOC), file-arg processor
(`cli/file-processor.ts`), initial-message builder
(`cli/initial-message.ts`), and the print-mode lifecycle
(`modes/print-mode.ts` 158 LOC). The **interactive** half (Pi TUI
surface) is deferred to Phase 5b under ADR-0088.

Pi line citations at SHA `734e08e…`:
- `main.ts:96-113` — `resolveAppMode`
- `main.ts:423-716` — `main()` body
- `cli/args.ts:1-354` — hand-rolled linear parser
- `cli/args.ts:123-129` — `--print` opportunistic positional eat (P-396 `---` escape)
- `cli/args.ts:154-160` — `--list-models` optional value (P-397 `@` exclusion)
- `cli/args.ts:167-180` — unknown-flag passthrough (P-398 `@` exclusion)
- `cli/file-processor.ts` — `@file` text + image branch
- `cli/initial-message.ts` — message-builder w/ Pi `.shift()` side effect
- `modes/print-mode.ts:1-158` — print-mode lifecycle
- `config.ts` — APP_NAME / VERSION resolution

## Decisions

### Decisions roster (P-385 ~ P-413)

W0 binding (P-385 ~ P-394) and W4/W5 audit add-ons (P-395 ~ P-413)
are captured in the binding spec
(`.omc/specs/sprint-6h6-phase-5a-spec.md`). The narrative summary:

- **P-385** — `main.ts` ported as `cli/entry.py:main_sync` reduced
  for non-interactive scope (lifecycle steps 1-12).
- **P-386** — `cli/args.ts` ported as `cli/args.py` hand-rolled
  parser. `argparse` / `click` rejected because they cannot cleanly
  express Pi's three parser-specific features:
  1. `--print` opportunistic positional eat (peek next token, swallow
     unless it starts with `@` or `-`, with `---` escape per P-396).
  2. `--list-models [search]` ambiguous optional value.
  3. Unknown `--ext-flag value` extension passthrough.
- **P-387** — `cli/file-processor.ts` ported. Image branch deferred
  to Phase 5a-iii (depends on `image-resize` + provider-agnostic
  ImageContent shape).
- **P-388** — `cli/initial-message.ts` ported. The Pi `.shift()`
  side effect on `parsed.messages` is preserved (mutating list pop)
  so residual messages flow through the print-mode loop correctly.
- **P-389** — `modes/print-mode.ts` ported as
  `modes/print_mode.py`. 9-step Pi lifecycle preserved (signal
  handlers → rebind → JSON header → initial rebind → initial
  message → residual messages → text-mode printout → cleanup).
- **P-390** — `config.ts` ported as `cli/config.py`. APP_NAME =
  `"aelix"` per ADR-0083 / ADR-0085 HTML export precedent (Pi value
  `"pi"` not propagated — Aelix-additive divergence).
- **P-391** — `resolve_app_mode` ported verbatim from
  `main.ts:96-113`.
- **P-392** — `to_print_output_mode` ported. Print mode handles
  both `"print"` (text) and `"json"` output variants per Pi mapping.
- **P-393** — `[project.scripts] aelix = "...:main_sync"` wired so
  `aelix --version` works after `pip install -e .`. The
  `__main__.py` module wires `python -m aelix_coding_agent` to the
  same entry.
- **P-394** — `tests/cli/` test package mirrors the Pi-parity test
  suite shape (one test module per source module + shared
  `__init__.py`).

W4 / W5 audit add-ons:

- **P-395** — `print_mode._rebind` subscribes on `harness` (not
  `session`) because Aelix has no `Session.subscribe` surface yet
  and does NOT call `bind_extensions` (no `Session.bind_extensions`
  in Aelix). Carry-forward to Sprint 5a-iii when the Session surface
  ports.
- **P-396** — `--print` `---` triple-dash escape (W5 MAJOR). Pi
  `args.ts:123-129` allows messages starting with `---` to flow
  through positionally (e.g., `aelix --print ---rule line`).
  Inline predicate replaces the `_is_value` call so the `---` test
  is in the right place.
- **P-397** — `--list-models` `@` exclusion (W5 MAJOR). Pi
  `args.ts:154-160` excludes both `-` AND `@` from the optional
  pattern so `aelix --list-models @config.json` keeps the `@file`
  arg in `file_args` instead of consuming it as the search pattern.
- **P-398** — Unknown-flag `@` exclusion (W5 MAJOR). Pi
  `args.ts:167-180` excludes both `-` AND `@` from the
  passthrough-value position so `--my-ext @input.txt msg` leaves
  the `@file` in `file_args` and `msg` in `messages`.
- **P-399** — `--mode` missing-value emits an Aelix error
  diagnostic (stricter than Pi's silent no-op). Aelix-additive
  divergence motivated by the Sprint 6h₅d typed-CLI investments
  (Sprint 6h₅d §C P-375 / §E AgentHarness.session) raising the
  expected-error-visibility bar.
- **P-400** — `print_help` is abbreviated for 5a-i scope. Pi's
  env-vars section, full examples, built-in tool names, and
  extension-supplied flags are deferred to Sprint 5a-iii (they
  depend on the extension loader + SettingsManager surfaces that
  are not yet wired).
- **P-401** — `--append-system-prompt` is parsed and recorded on
  :attr:`Args.append_system_prompt` but currently ignored at the
  harness boundary pending the ResourceLoader port (Sprint 5a-iii).
- **P-402** — TTY second-pass demotion (Pi re-evaluates TTY status
  after stdin consumption to decide whether to keep interactive
  mode alive) is deferred to Phase 5b because interactive mode is
  itself deferred to Phase 5b.
- **P-403** — Pi `killTrackedDetachedChildren` has no Aelix
  equivalent because Aelix does not yet expose a detached-children
  tracker; deferred until the Bash extension lands a tracker
  surface.
- **P-404** — Double-dispose race on the signal path is mitigated
  by `_safe_dispose` (suppress + idempotent dispose). The
  belt-and-suspenders dispose in `_async_main` `finally`
  + `run_print_mode` `finally` is intentional and the
  contextlib.suppress in both locations makes the second dispose a
  no-op. Verify in Sprint 5a-iii once the runtime gains a public
  `is_disposed` predicate.

### Aelix-additive divergences from Pi (documented)

1. **APP_NAME = "aelix"** — preserves the Sprint 6h₃ HTML export
   precedent. Pi's `"pi"` value is intentionally not propagated.
2. **`argparse` / `click` rejected** — hand-rolled parser for byte-
   for-byte Pi parity (P-386 rationale).
3. **`SettingsManager` / `--list-models` / image-resize /
   migrations / session-picker deferred** to Sprint 5a-iii / 5a-iv.
4. **Interactive mode raises NotImplementedError** — Phase 5b TUI
   carry-forward; stderr diagnostic points to ADR-0088.
5. **`takeOverStdout` punted** — low-priority; Aelix builtins emit
   through harness events (not raw stdout) so the JSON stream stays
   clean without the redirect.
6. **P-395** — `print_mode._rebind` subscribes on `harness` not
   `session`; does NOT call `bind_extensions`. Carry-forward to
   Sprint 5a-iii when Session surface ports.
7. **P-399** — `--mode` missing-value emits Aelix error diagnostic
   (stricter than Pi silent no-op).
8. **P-400** — `print_help` abbreviated for 5a-i scope (Pi env-vars
   section, examples, built-in tool names, extension flags deferred
   to 5a-iii).
9. **P-401** — `--append-system-prompt` parsed but ignored pending
   ResourceLoader port (5a-iii).
10. **P-402** — TTY second-pass demotion deferred to Phase 5b
    (interactive mode prerequisite).
11. **P-403** — `killTrackedDetachedChildren` no Aelix equivalent
    (detached children deferred).
12. **P-404** — Double-dispose race on signal path (suppress +
    dispose idempotency to verify in 5a-iii).

### Closed W6 fixes (must-fix triage)

- **P-396** — `--print` `---` triple-dash escape — Pi parity
  `args.ts:123-129` inline predicate.
- **P-397** — `--list-models` `@` exclusion — Pi parity
  `args.ts:154-160` dual `-` AND `@` guard.
- **P-398** — Unknown-flag `@` exclusion — Pi parity
  `args.ts:167-180` dual `-` AND `@` guard.
- **W4 MAJOR** — `print_help(out: object | None)` → `print_help(out:
  TextIO | None)` + `stream: TextIO = ...` annotation upgrade. The
  `TextIO` import is guarded by `TYPE_CHECKING` so the runtime cost
  is zero.

## Verification gates (W6 closure)

- `uv run pytest 2>&1 | tail -3` — **2077 passed, 1 skipped** (2074
  baseline + 3 new Pi-parity regression tests for P-396 / P-397 /
  P-398).
- `uv run ruff check 2>&1 | tail -2` — clean.
- `uv run pyright 2>&1 | tail -3` — **8 errors** baseline preserved.
- `uv run python -m aelix_coding_agent --version` → `0.1.0`.
- `uv run aelix --version` → `0.1.0`.

## Carry-forward to Sprint 5a-iii / 5a-iv

| Item | Owner sprint | Notes |
|---|---|---|
| `SettingsManager` (disk-persisted user settings, model defaults, theme prefs) | 5a-iii | Required for `--list-models` (P-282 / P-291 cross-reference) |
| `--list-models` real implementation | 5a-iii | Currently emits stderr "deferred to 5a-iii" diagnostic |
| `cli/file-processor.ts` image branch (`image-resize` + ImageContent inflation) | 5a-iii | Currently text-only |
| Pi migrations (session schema migrations + settings migrations) | 5a-iv | Pi `migrations/*` not yet ported |
| Pi session-picker (`--resume` interactive picker) | 5a-iv | Requires Phase 5b TUI primitives |
| `--append-system-prompt` wire to harness | 5a-iii | Parser captures, harness ignores (P-401) |
| `ResourceLoader` port | 5a-iii | Resolves `@`-prefixed system prompts + `--append-system-prompt` files |
| `takeOverStdout` (only if a builtin starts corrupting JSON stream) | demand-driven | Aelix builtins don't corrupt stream today |
| Pi `print_help` parity (env vars, examples, built-in tool names, extension flags) | 5a-iii | Currently abbreviated (P-400) |
| Pi `Session.subscribe` / `Session.bind_extensions` surface | 5a-iii | P-395 carry-forward |
| TTY second-pass demotion (interactive mode prerequisite) | 5b | P-402 carry-forward |
| `killTrackedDetachedChildren` (Bash extension prerequisite) | demand-driven | P-403 carry-forward |
| `is_disposed` predicate on runtime for double-dispose verification | 5a-iii | P-404 carry-forward |

## Consequences

- `aelix --print "msg"` ships end-to-end on Phase 5a-i exit; the
  one-shot text agent loop works against any provider.
- `aelix --mode json` ships end-to-end on Phase 5a-ii exit; the JSON
  line-delimited event stream works for headless consumers.
- `aelix --mode rpc` continues to work against the Sprint 6d JSONL
  RPC protocol; Phase 5a-ii adds no new RPC commands.
- `aelix` (no flags, no stdin pipe) raises NotImplementedError with
  stderr diagnostic pointing to ADR-0088 — Phase 5b is the owner.

## References

- Pi `main.ts:1-716` at SHA `734e08e…`
- Pi `cli/args.ts:1-354` at SHA `734e08e…`
- Pi `cli/file-processor.ts` at SHA `734e08e…`
- Pi `cli/initial-message.ts` at SHA `734e08e…`
- Pi `modes/print-mode.ts:1-158` at SHA `734e08e…`
- Pi `config.ts` at SHA `734e08e…`
- ADR-0088 — Phase 5b TUI library decision (companion ADR; this ADR
  raises NotImplementedError pointing there).
- ADR-0087 — Sprint 6h₅d non-UI carry-forward closure (A 단계 last
  cleanup before B 단계 opens).
- ADR-0086 — A 단계 closure ledger.
- ADR-0083 — Runtime callback Pi parity (`with_session` / `setup` /
  `forkFrom` — interactive mode will exercise these).
- ADR-0034 — Pi reference version pin (Sprint 6h₆ row appended).
