# ADR-0180 — #57 CLI pipe robustness: non-TTY stdin hang guard + stdout EPIPE defense

- **Status:** Accepted — **LIVE**.
- **Date:** 2026-07-03
- **Sprint:** Post-moat floor hardening (#57).
- **Pi pin:** `earendil-works/pi@734e08e`. **This is aelix-original hardening, NOT a pi port** — see the corrected premise below.
- **Relates:** issue #57; ADR-0089 (print/json modes), ADR-0077 (rpc mode). pi refs #5571/#4984 (both unfixed in pi).

## Corrected premise (pi oracle)

Issue #57 assumed pi fixed these in v0.74.1→v0.80.2. **Verified false** (files byte-identical across every tag in that range): pi #5571 (stdin hang) was DECLINED as user error (workaround `</dev/null` — "no clean fix"); pi #4984 (EPIPE crash) was closed not-reproducible, and pi's only EPIPE guard (interactive dead-terminal → quiet `process.exit(129)`) predates the pin. The only EPIPE-guard PR (#5183) was never merged. So pi still hangs on a never-closing stdin pipe and has no print/RPC EPIPE handling. The aelix defects are real regardless (the hang is reachable with ZERO flags — any non-TTY stdin promotes `app_mode` to `"print"`), and the owner's recorded recommendation in #57 stands; we fix as **aelix-original**, taking pi's quiet-exit spirit (129) into the Python pipeline convention (141 = 128+SIGPIPE).

## Decision

**1. stdin hang guard (`cli/entry.py _read_piped_stdin`).** POSIX-only `select`-based FIRST-byte readiness deadline before the read-to-EOF: default 30s, `AELIX_STDIN_TIMEOUT` overrides (`0` disables = wait forever); on deadline, warn on stderr and proceed WITHOUT stdin input. Key shapes:
- `select` runs via `asyncio.to_thread` but always returns at the deadline — **no leaked thread**; a bare `wait_for` around the blocking read would strand the uncancellable OS read.
- **Fail-open**: any `select` failure (fd ≥ FD_SETSIZE from a replaced-stdin embedder, EBADF, `inf`/huge timeout → time_t OverflowError) degrades to the pre-guard blocking read — never crash a path that used to work; unbounded wait matches both old behavior and the wait-forever intent of `inf`.
- fileno()-less stdin (pytest capture, embedders) skips the gate. Windows unchanged (`select` is socket-only there).
- **Owner-accepted tradeoff (review MEDIUM, by-design):** a producer whose FIRST byte takes >30s loses its stdin input (warned on stderr, env-overridable). The alternative (wait forever) is exactly the CI-hang this issue exists to prevent.
- Once data/EOF is ready, read-to-EOF is unbounded: write-then-never-close remains a pathological hang (pi-parity).

**2. stdout EPIPE defense (quiet exit 141).** Previously NO BrokenPipeError guard existed anywhere: `-p` text died on the interpreter's shutdown flush ("Exception ignored…", exit 120); JSON mode suppressed every write error and kept emitting to a dead pipe, exiting 0; RPC's `_write` misrouted EPIPE through its missing-`.buffer` pytest fallback (re-raised uncaught) and event/response writes swallowed or stranded it. Now:
- `main_sync`: top-level `except BrokenPipeError` → `_stdout_to_devnull()` (dup2, so the shutdown flush can't re-raise) → `sys.exit(141)`. Flush moved INSIDE the guard (`suppress(ValueError)` — an already-closed stdout has nothing to flush). A `BaseException` pass-through adds the same flush hygiene to Ctrl+C/SystemExit/crash exits without changing their semantics.
- `print_mode`: text-path `except BrokenPipeError: raise` lands BEFORE the broad exit-1 catch; JSON `_emit` records death in a `stdout_dead` flag (subscribers must not raise — harness dispatch swallows listener errors, pi parity), stops writing, the residual-message loop breaks early (no wasted turns), and a post-loop raise surfaces it; the JSON header emit re-raises EPIPE while staying best-effort for everything else.
- `rpc_mode`: `_write` re-raises BrokenPipeError distinctly (fallback reserved for the pytest missing-`.buffer` case); BOTH remaining writers — the event pipe (`_on_agent_event`) and the dispatch-response write (the only stdout touch for event-less commands) — map EPIPE to `shutdown_event.set()` for a graceful transport shutdown instead of silent forever-drops / "Task exception was never retrieved" noise.
- A dead **stderr** must not abort a healthy run: the deadline warning print is suppress-guarded (an unguarded print raised BrokenPipeError that `main_sync` misclassified as stdout death, devnulling the LIVE stdout).

## Adversarial review (4 lenses → per-finding skeptic verify: 13 findings, 11 confirmed / 2 refuted)

All 11 confirmed findings addressed: 2 MEDIUM+1 LOW consolidated into the fail-open select guard; RPC dispatch-response MEDIUM+LOW → response-write guard; print-loop MEDIUM+LOW → in-loop dead check; main_sync exit-path LOW → BaseException flush hygiene; stderr LOW → warning suppress; flush NIT → suppress(ValueError); test MEDIUM (hang-instead-of-fail on regression, no pytest-timeout configured) → `asyncio.wait_for(…, 15)` + 2 new regression tests (inf fail-open, dead-stderr). The 30s slow-producer MEDIUM = owner-approved by-design (above). Refuted ×2: negative-env convention (returns to a sane default, warning names the env); blanket BrokenPipeError re-raise catching non-stdout EPIPE from `harness.prompt` (unreachable — every claimed source is sealed at a lower layer).

## Consequences

- `sleep 999 | aelix -p hi` returns in 30s with a warning instead of hanging forever; `aelix -p hi | head -1` exits 141 quietly instead of 120 with interpreter noise; JSON/RPC stop wasting turns/writes on dead consumers and shut down cleanly.
- Exit-code surface: 141 = stdout consumer gone (pipeline convention), joining 128+sig for SIGTERM/HUP. Documented here; no pi analogue for print/RPC (pi has none).
- Tests: 11 in `tests/cli/test_pipe_robustness.py` incl. a subprocess end-to-end (`--version` against a pre-broken pipe → returncode 141, no traceback/"Exception ignored").
- **Gate:** pytest 4550 pass / 1 skip (+11 vs 4539 baseline) · ruff clean · pyright 8 pre-existing `scripts/pyright_spike.py` errors only.
