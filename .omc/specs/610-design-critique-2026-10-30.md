# #610 design critique (rev 1) — independent Claude subagent, 2026-10-30

Kept as handed back. Probe scripts were in /tmp/610-critique/ (not kept). Handling: design rev 2, section 16.

## #610 design critique (design rev 1, tree claude-610 @ f3e76fbb). Read-only; probe scripts only in /tmp/610-critique/

Findings are ranked by severity. Every file:line below I read myself. "Probe" means I ran it with `env -u VIRTUAL_ENV uv run --no-sync python`, and `aelix_agents` resolved to the worktree.

### BLOCKERS

**B1 (D9/D10, lane split A/B): the permission ceiling never reaches the model door's pre-filter, so under a YOLO parent a resume runs wider than its first run.**
- **How it happens:**
  - `_grant_for` computes `want = child_permission_mode(...)` (extension.py:1057-1064).
  - When `consent_is_required` is False, it returns `SpawnGrant(mode=want, disclosure=…)` and never calls `request_spawn_consent` (extension.py:1065-1085).
  - Under a YOLO parent that predicate is always False (consent.py:650-651).
  - `_run` launches with `_tighten(grant.mode, permission_floor)` (runtime.py:1106).
  - D10 threads the ceiling only into `request_spawn_consent`, and the design never mentions `_grant_for`.
- **Probe** (/tmp/610-critique/probe_consent.py), first run stored PLAN, parent now YOLO, profile `inherit`: `_grant_for want: yolo; consent_is_required: False -> grant.mode=yolo WITHOUT calling request_spawn_consent; disclosure: True; D9 intersection would be: plan`.
- **What breaks:** `-p --permission-mode yolo`, the shape `drive_p.py` uses, takes this path on every resume.
  - If lane A instead takes the plan's mode from D9, the child runs `plan` while the disclosure row says "at yolo" (extension.py:1381).
  - Either way the issue's done criterion "권한·도구 제한이 넓어지지 않았음" fails.
  - Lane A owns runtime and lane B owns extension/consent, so the enforcement point falls between the two lanes.
- **Fix:**
  - Write one `resume_clamp(stored, live_clamp)` (rank-min) and use it in three places: as `want` in `_grant_for`, at the top of `request_spawn_consent` before the no-UI return at :1322, and as `permission_floor=stored` in `resume_granted` (the belt).
  - Compose the disclosure from the intersected mode.
  - Test through the hook: parent YOLO, both with UI and with `-p`.

**B2 (D8/D9/D5): `model_flags` is stored as raw argv and replayed verbatim, so a forged or untrusted parent record becomes arbitrary child argv.**
- **Probe** (/tmp/610-critique/probe_argv_injection.py, `parse_args` with extra flags where the replayed list would sit):
  - `-e /tmp/evil.py` gives `extensions=['/tmp/evil.py']` alongside `no_extensions=True`. Explicit `-e` paths still load under `--no-extensions`, because no_discovery skips only tiers 1, 2 and 4 (loader.py:772-774, :797-860). That is code execution in a "plan" child.
  - A second `--session /other/top-level.jsonl` wins (last-wins). This bypasses D6, which validates the record's file, not the argv.
  - `--api-key sk-attacker` and `--append-system-prompt …` are both accepted.
  - Only `--permission-mode` is blunted: the later `plan` wins.
- **Preconditions:** attacker-written parent records. The realistic route is a session file opened in place with `--session`:
  - D5/D6 never refuse a parent outside a bucket (allocation does, child_session.py:433-438).
  - D6's origin check is satisfiable when both files arrive together.
  - The stored `cwd` is no hurdle, because `resolve_child_cwd` joins a relative value to the parent cwd (print_channel.py:569-572).
  - The only real hurdle is matching the absolute `profile_source`, which is guessable for bundled or user profiles.
  - A read-only profile gets no dialog.
- **Fix:**
  - Store typed `{model, provider, route_digest}`. Validate the charset (no leading `-`, no control chars) and that the digest is 64 hex. Rebuild through a resolver path that can emit only `--model`/`--provider`/`--require-registration-route`.
  - Validate the other stored fields: `thinking` against VALID_THINKING_LEVELS, `tools` ⊆ ALL_TOOL_NAMES, `permission_mode` as the enum, and `cwd` as absolute.
  - Refuse resume when the current parent is outside a bucket.
  - Derive the child path as `place_children(current parent).directory/<first key>.jsonl` and require `rel` to equal it. Drop the absolute `path` fallback. Refuse symlinks (lstat).

### MAJOR

**M1 (D10): with the ceiling below auto-accept-edits, a declaring profile gets a yes/cancel-only dialog.**
- **What breaks:** this is exactly what the 2026-07-27 owner rule removed. It happens because `consent_is_required` calls `_may_widen` without the ceiling.
- **Probe:** stored PLAN (e.g. the first run was `-p`), resumed in the TUI, profile `ask` or `auto` → `consent_is_required=True`, `may_widen(now)=True`, `may_widen(with ceiling)=False`, `options=['Run read-only (plan)', 'Cancel']`.
- **Fix:** add the ceiling to `_may_widen` and use that same predicate in `consent_is_required`, in the pre-filter and in `build_options`.

**M2 (§14 live plan vs the issue's done criteria):**
- **What breaks:**
  - The "not widened" check only narrows the parent. It never widens the parent after the first run (DEFAULT→YOLO, broader `--tools`), which is the direction that catches B1.
  - The new resume consent title (D10) is a TUI surface (CLAUDE.md rule 9), but no live step ever shows it: the drivers' `writer` profile never triggers a dialog.
  - The real-haiku run does not say how the provider request will be captured. The issue requires "provider 요청으로 본다"; a sitecustomize `httpx` pass-through tap in the child would do it.
- **Fix:** add three steps: first run under default, then shift+tab to yolo, then resume, and check that `ps` shows `--permission-mode plan`; a declaring-profile resume dialog under `uv run aelix`; and the wire tap on the real model.

**M3 (D5/D6): `/tree` branches of one session share its id, so two branches can both resume a child and interleave runs in its file.**
- **What breaks:**
  - This is the hazard D6 uses to reject forks.
  - D13 then reports the wrong last status: branch B does not see branch A's resume settle.
  - A human `aelix --session <child>` continuation goes undetected in the same way.
- **Fix:** record a child-file fingerprint (size or last entry id) in each settle, and refuse or warn when the file has moved past this branch's last settle.

**M4 (D8 refactor):**
- **Exception safety:** the receipt is built synchronously between the registry insert (runtime.py:1082) and the `try` (:1143). If `ChildLimits`, which calls `_pin_route`/`resolve_route` on a live registry, is computed there and raises, the `_children` row leaks; the docstring at :1030-1036 warns about exactly this. After 4 leaks (MAX_LIVE_CHILDREN) no delegation can start for the rest of the session. **Fix:** compute it before admission, or totally with a guard, as `_spawn_model`/`_parent_thinking` already do.
- **Unlisted fallout:**
  - `_cancelled_envelope` still calls `narrow_tools(...)`.dropped (runtime.py:1437).
  - RpcChannel recomputes everything and ignores `plan.limits` (rpc_channel.py:311-319); it should honour them or refuse a resume plan.
  - `test_host_late_binding.py:223` reads `kwargs["parent_model"]` from the argv builder.
- **Wrong claim:** runtime.py:765-777 is the dialog-vs-launch window inside `spawn()`, before `_run`. D8 does not close it. What D8 does close is the receipt-vs-argv window (:1083 vs the argv after `await receipt.open`).

**M5 (D12): "busy for the child process lifetime" cannot be implemented as written, and its rationale does not match the code.**
- **Why:**
  - Esc calls `_eager_abort`, which sends SIGKILL with no reaper (print_channel.py:1372-1379, :1554-1556). A reaper exists only for stop/stop_all (:948-1002) or the post-EOF linger.
  - After `_run` returns, nothing owns the process.
  - A `proc.wait()` callback never fires on CPython ≤3.13.14 while a grandchild holds the pipes (:848-866), so the busy flag would stick for the session.
  - A detached watcher must be joined by `stop_all` (ADR-0197).
- **Fix:**
  - Treat busy as the registry row plus a lazy, synchronous `proc.returncode is None` check on remembered procs.
  - Do the lock check at the hook and in `resume_granted` before `_run`. Not inside `_run` after admission, where a refusal would charge the budget and write start/pending/settle (an extra "run" and pending spend).
  - Use `SessionWriterLock(path, timeout=0)`: `try_acquire` blocks synchronously for up to 0.25 s by default (session_lock.py `DEFAULT_ACQUIRE_TIMEOUT`).
  - Report `lock.error` separately from contention.

### MINOR

- **m1 (D4):** "D12(병렬 멤버)…따라 바뀐다" is false. `_unfinished_member_block` deliberately has no redelegate line (aggregate.py:396-398), so batch members and batch Esc reports get no resume guidance. The D4 sentence also interpolates the profile name, a filename stem that may contain newlines; run it through `child_line`.
- **m2 (D2):** the ok member's first line is `[i/N ok] <body>` (aggregate.py:~277), so "머리줄에 · session" splices into the child's text. Put `Session: <id>` on its own line inside the frame. One pin is missing from the update list: `test_stopped_child_reports_604.py:1124` (real session). `test_tool_and_security.py:1058` needs no change, because `_bench` has no session.
- **m3 (D9):** the stated reason for the "first-run ceiling" also condemns the first run's stored mode, which already includes the batch live floor (runtime.py:1106). Store the consented `grant.mode` as the ceiling.
- **m4 (D7/D16):** `/agents run` and RPC children (`tool_call_id`/`mode` null) appear in the model's list and are resumable. That exposes a human-typed task the model never saw, and the D10 label "written by the model" would be false. Restrict resume to model-door records, or label them. Records-derived `task_preview` is untrusted under B2; fence it or drop it from the list.
- **m5 (D11/D13):** a child that never started (spawn failure: header + origin only) is "resumable". Its original task was never delivered, and the preamble says "continue". Require at least one user message.
- **m6 (D9):** after a models.json or registration change, the child exits with "requires its original explicit provider registration and route" (model_registry.py:403-412) only after consent and the budget are spent. Pre-check the digest at the hook.
- **m7 (D13):**
  - The parser accepts a ≤64 KiB instruction, and the hook then refuses it with a count that includes the preamble. State the instruction budget instead.
  - "A tool call that shows no result did not complete" is false for the parallel-path drops the design itself cites (loop.py:905-907). Say "may have run".
- **m8:** decide whether `limits`/`resumes` appear on every start or only on resumes; the `_START_KEYS`/`_SETTLE_KEYS` pins and the ADR-0243 table need one shape. The attach path must count `opening_attempted` the way `open()` does.

### Cut or simplify
- The process-lifetime busy map: in-process concurrency is unreachable until #611 (the tool is sequential, `/agents run` blocks, RPC needs an idle runtime). The registry row plus the lock check is enough.
- D7 reading every candidate child file at the hook: list from the records, validate only the chosen id, and cap the candidates scanned.

### Checked and found correct
- **D14:** all six built-in adapters go through `transform_messages`; the synthetic result is "No result provided" with `is_error=True`; anthropic sends `is_error` (_anthropic_transforms.py:249) and google sends `{"error":…}` (_google_shared.py:376-383); error and aborted turns are dropped. A torn tail after SIGKILL is skipped and healed with a newline (jsonl_storage.py:571-590). Reopening a child restores only messages and thinking, not permission or tools.
- **D15:** the fold is last-wins per key (_session_stats.py:298-305, pin test_tool_usage_stats.py:175); /stats shows "(N runs)".
- **D6:** forks copy receipts (repo_utils.py:82-131); `get_branch` includes entries before compaction (core.py:3133-3138).
- **D1:** `required` pin (:1446-1456) and `mode=""` (:1191) are as stated; the head is 1170/2048 bytes (measured).
- **D12:** the admission AST test allows synchronous statements; `_allocate` refuses an existing file (:457-459).
- **Rendering:** chain `{previous}` uses `result.summary`; `/agents run`'s TUI uses its own renderer (commands.py:1061); hook reasons pass verbatim into the tool result with no cap (loop.py:534-547).
- **Write guard:** `.aelix` is a sensitive write component (permission.py:474-476).

### Not checked
- No live run and no test suite.
- pi's current resume code.
- The parent-side lock on Windows.
- A read-only (viewer) parent that resumes, since its record appends would be swallowed.
- How the TUI card renders the `action`/`id` arguments.
- The README and SECURITY.md wording.

