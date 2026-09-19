# #199 design critique — rev 0 of `.omc/specs/199-design-2026-09-19.md`

Critic pass. I did not write the design. Everything here was read from code, not remembered.

- **Aelix:** `main` `e1fef5f`, main checkout, read-only.
- **#294:** `/tmp/wt-294` `HEAD` `8aaede2`, read only with `git show` (ADR-0242, `jsonl_storage.py`, `fs.py`). I never ran anything inside that tree.
- **Pi:** `36b60d2`, read with `git show`/`git grep`.
- **Probes:** three throwaway scripts in the scratchpad, run with `uv run --no-sync python …` from the main checkout. They wrote only to scratchpad temp dirs. Their output is in §P.
- **Not done:** no test suite, no real model, no Windows run.

**Counts: 2 BLOCKER · 12 SHOULD-FIX · 12 NIT.**

---

## BLOCKER

### 1. BLOCKER — the parent-session seam can lose records, or never produce them. Read the session once, at admission, from a source that does not depend on a hook.

**The design (A.3)** says: `SubagentHost.session: Callable[[], Any | None]` (the extension's `ExtensionContext`), "consistent with the host's live-callable rule". The start record is written before the child runs and the settle afterwards, both through that getter. That is wrong on three paths.

**Evidence:**
- `AgentsExtension._ctx` is the context of the most recent **hook**. It is set only in `_on_before_agent_start` and `_on_tool_call` (`extension.py:575`, `:609`).
- The only session accessor on it is `ExtensionContext.session_manager` → `ReadonlySessionManager.get_session()` (`extensions/api.py:1252`, `:237`). That is backed by `harness/core.py:3427` `_SessionManagerView`, which returns the session captured when the context was built.
- Every attribute read goes through `ExtensionContext.__getattribute__` (`api.py:1146`), which calls `runtime.assert_active()` and raises `ExtensionError("stale")` once the runtime is invalidated.
- The runtime is invalidated in two places:
  - `AgentSessionRuntime` emits `session_shutdown` and invalidates the runner **immediately after** (`agent_session_runtime.py:388-402`).
  - `AgentHarness.dispose()` invalidates after its cleanups (`core.py:3119`).

**Path (a): teardown or session switch while a delegation is running.** This covers quit, and RPC `new_session`/`switch_session`/`fork`, which can arrive mid-turn (`rpc_mode.py:_handle_new_session` does not wait for idle).
- `_on_session_shutdown` → `stop_all` aborts the children. But `_run` returns only after `PrintChannel.run` drains (`runtime.py:_SESSION_DRAINING` documents that `run` "measurably" returns *after* `stop_all`'s last await).
- So the settle is written **after** `invalidate`. A live getter then either raises "stale", so the settle is lost and a finished run reads as "interrupted", or, once a new hook has refreshed `_ctx`, returns the **new** session. In that case the settle and the usage land in a session that never ran that child, and it counts spend it did not have.

**Path (b): `/agents run` before any hook ran.** Examples: it is the first command in a fresh TUI, or the first one after `/new`, `/resume`, `/fork` or `/reload`.
- `_ctx` is then `None` or stale. `/agents run` calls `runtime.spawn(resolved, task)` (`tui/commands.py:1257`) with no session in hand.
- The design's fallback applies, so the child runs `--no-session` and nothing is recorded, although the parent has a file. That is the user-typed door failing #199's core requirement in ordinary flows.

**Path (c):** the design's own precedent contradicts the "live" reading. `parent_tools` and `parent_context_files` are read **once at admission** into the frozen plan: "the plan is the frozen record of what the parent's authority was when the spawn was admitted" (`runtime.py:854-865`).

**Proposed text (replaces A.3's last bullet):**
> The parent `Session` is read **once per spawn**, inside `_run`'s `try`, right after the registry insert. Every record of that spawn (start, pending ledger, settle, final ledger) is appended to that captured object. It is never re-read.
>
> `AgentsExtension` gains `session: Callable[[], Session | None] | None`, wired in `cli/entry.py` to the runtime host's **current** harness session (for example `lambda: holder["runtime"].harness.session`, the same late-bound pattern as `no_context_files`). It is consulted first. `ctx.session_manager.get_session()` is only a fallback, read under `suppress(ExtensionError)`.
>
> No session, or a session with no file path, means the child runs `--no-session` and nothing is written to disk.

Add tests:
- a settle after `stop_all` + invalidate still lands in the original session and not in the new one;
- `/agents run` with `_ctx is None` records;
- `/agents run` with a stale `_ctx` (after a `/new`) records.

### 2. BLOCKER — stats cannot tell unconfirmed child spend from zero spend. Add a pending ledger line.

**The design (A.3, A.4)** writes `aelix.usage` only after a settle ("a crash between them loses one ledger line (under-count), never double counts"). The kernel folds only ledger lines. So in each of these cases stats show an exact-looking bill (`cost_known=True`) that omits real spend:
- a parent killed mid-delegation;
- an unsettled start after a switch (item 1);
- a crash between the settle and the ledger line;
- simply while a child is running.

**Evidence:**
- `docs/05-post-beta-direction.md` §5 (owner-adopted): "사용량도 부모 합계와 자식 기록을 중복 계산하지 않고, 실패·재시도 비용과 **미확정 사용량을 구분한다**" ("usage must not count parent totals and child records twice, and must separate failure/retry cost and unconfirmed usage"). Stage 2 of the same section asks for the "결과 불명" (outcome unknown) state to be distinguished.
- `SessionStats.cost_known`'s own rule (`_session_stats.py`): "A wrong bill is worse than an absent one."
- `format_session_cost` already renders `≥ $X` when `cost_known=False` (`tui/stats_dashboard.py:168-198`), so no new display state is needed.
- The design's "start with no settle = unknown" is visible only to readers of `aelix.child_session`. The kernel cannot read that type without learning delegation vocabulary.

**Proposed text:**
> A.3: after the child file exists and **before** `channel.run`, append `aelix.usage {v:1, key, state:"pending"}`. After the settle, append `aelix.usage {v:1, key, state:"final", usage:{…}, cost_known}`.
>
> A.4: fold last-wins by `key`. A `final` line adds its usage. A key whose last line is `pending` adds nothing and sets `cost_known=False`.
>
> `SessionStats.tool_usage` carries `pending: int`. A crash between settle and final ledger now leaves `pending`, which reads as unknown rather than a silent under-count. Delete the "under-count, never double counts" sentence.

Nothing can be spent before the pending line exists, because it is written before the spawn. `--no-session` parents get the same semantics in memory.

---

## SHOULD-FIX

### 3. SHOULD-FIX — `_run` write mechanics: where the allocation sits, one settle, and no extra lock.

**Evidence:**
- **Row leak.** The registry insert is `runtime.py:829`, but the `try`/`finally` that pops the row starts at `:869`. Any `await` placed between them, such as the child-file publish or the start append, leaks the row on cancel or exception. `_admit_live` then counts it forever, and the runtime instance survives `/new`/`/resume` (`extension.py:176-178`).
- **The AST gate does not catch it.** `tests/agents_ext/test_batch_executor.py:485-544` bans awaits only in `run.body[admit : insert+1]`.
- **The publish can suspend.** It suspends on Windows: `fs._replace` sleeps between rename retries (wt-294 `fs.py:81-102`).
- **`admitted` depends on the first publish.** `batch._member`'s `admitted` flag relies on the first `_publish` right after the insert (`batch.py`, `_tap`). An allocation that raises before that publish misclassifies the member as never started.
- **The design's per-runtime `asyncio.Lock` does more harm than good** (probe §P.3):
  - Under `LocalFileSystem` it never contends, because nothing yields (`append_file` is a synchronous `os.write`, and `asyncio.Lock.acquire` returns without suspending when free). It is a no-op.
  - It does not cover the harness's own `append_message` or other extensions' fire-and-forget `append_entry` (`core.py:3877`).
  - It adds a suspension point to cancel-path writes. With a shared lock whose holder suspends once, a **second** cancel (a second Ctrl+C re-cancels the gather children) lost **both** settles (case C). The same case with `asyncio.shield` kept both (D). With no lock and a non-suspending write, both were kept (E).
- **Settles on the non-cancel exception path.** `channel.run` can raise things other than `CancelledError`: `write_prompt_file` sits outside its `try` (`print_channel.py:980`). The design writes settles only "on cancel".

**Proposed text for A.3:**
> 1. Registry insert.
> 2. The first `_publish`, unchanged.
> 3. `try:`
>    - capture the session (item 1);
>    - allocate the child file;
>    - append start + pending ledger;
>    - build `SpawnPlan(..., session_path=…)`;
>    - `channel.run`;
>    - append settle + final ledger;
>    - set `settled = True`.
> 4. `finally:` if not `settled`, append the settle (`cancelled` for `CancelledError`, `error` with the exception text otherwise) under `asyncio.shield`; then pop the row.
>
> No per-runtime lock. `JsonlSessionStorage` already serializes appends. Never hold anything across the child publish.

### 4. SHOULD-FIX — child paths must be absolute. A parent opened with a relative `--session` breaks every delegation that runs in a subdirectory.

**Evidence:**
- Probe §P.2 [3]: `load_jsonl_session_metadata(fs, "sessions/…/x.jsonl")` → `meta.path` stays relative, and `repo.open(...)` → `storage._file_path` is relative (`isabs=False`).
- `_resolve_session_metadata` passes a path-like argument through as typed (`entry.py:398-399`).
- The child runs in `plan.cwd`, which may be a model-chosen subdirectory (`print_channel.resolve_child_cwd`). There it resolves `--session sessions/…/<stem>/child.jsonl` against its own cwd. That gives a "Failed to read session header" `SessionError`, exit 1, and an error envelope on every such delegation.
- Records would also store relative paths that break later.

**Proposed text for A.1:** "The child directory is derived from `os.path.abspath(parent_file)`, resolved in the parent. Every path in a record is absolute." Add a test: parent opened with a relative `--session`, child `cwd` set to a subdirectory.

### 5. SHOULD-FIX — Windows `MAX_PATH`: the nested `<stem>/<ts>_<uuid>.jsonl` adds about 66 characters to the parent's path.

**Evidence.** Probe §P.4 uses `jsonl_repo._encode_cwd` and root `C:\Users\runneradmin\.aelix\sessions`:

| cwd length | parent path | nested child `.tmp` | child named `<spawn_id>.jsonl` |
| --- | --- | --- | --- |
| 23 chars | 132 | 198 | 153 |
| 68 chars | 177 | **243** | 198 |
| 111 chars | 220 | **286 (> 260)** | 241 |

- The publish stages `<path>.tmp` (ADR-0242 §4), so the staged name is the longest.
- Windows' 260-character limit applies unless `LongPathsEnabled=1`, which uv-installed Pythons do not arrange.
- The windows-latest legs pass today with long `tmp_path` cwds only if the runner has long paths enabled. That is not evidence for users.

**Proposed text for A.1:**
> Child files are named `<spawn_id>.jsonl` (`sub-<12hex>.jsonl`, 22 characters). The header already carries the timestamp and the session id, and pickers never see these names. This also removes any need to import `jsonl_repo`'s private `_create_timestamp`.
>
> Allocation `OSError`, including a path that is too long, falls back to `--no-session` with `child: null, error`.
>
> Add a test on the windows leg under a long cwd.

(A spawn id collision inside one parent directory is on the order of 2^-48 per pair. `_publish_file` uses `os.replace`, so either check `exists` first or accept that.)

### 6. SHOULD-FIX — `cost_known` has no source, and cancelled runs are never priced.

**Evidence:**
- `SubagentUsage` has no `cost_known` field.
- `print_channel.apply_cost_fallback` (`:607-636`) returns `None` on every path. When it finishes, "registry has no such model" (`registry.find(...) is None`) and "priced at $0.00" (a `:free` model) both leave `state.cost == 0.0`.
- On Ctrl+C no envelope is built (map §5), so the fallback never runs and the cancelled settle carries `cost = 0` for spend that did happen.

**Proposed text for A.3:**
> `apply_cost_fallback` returns `bool` (priced). `cost_known` = (any `message_end` carried a provider cost) or (the fallback priced) or (every token counter is zero).
>
> In `_run`'s `finally`, call `apply_cost_fallback(child.stream, host.model_registry())` before building a cancelled or errored settle. It is synchronous and never raises.

### 7. SHOULD-FIX — `tool_call_id`, `mode` and `index` do not reach `_run`.

**Evidence:**
- `_run(grant, resolved, task, *, child_cwd, timeout_ms, on_event, permission_floor, charge_budget)` (`runtime.py:771-782`) has none of the three.
- `run_batch(...)` gets `call` but no tool-call id (`extension.py:1083-1108`).
- The index exists only in `batch._member`.

**Proposed text for A.3:** "A private `record: SpawnRecordMeta(tool_call_id, mode, index) | None` keyword on `spawn_granted` and `run_batch` carries them into `_run`. The Protocol's `spawn` is untouched (S2, `test_protocol_has_no_consent_parameter`). `/agents run` passes `None`, which is recorded as nulls."

### 8. SHOULD-FIX — "every exit path" includes paths `_run` never sees. Scope the records and give a table.

**Evidence:**
- **Model-door human declines and every hook refusal** return `ToolCallResult(block=True, …)` from `_on_tool_call` (`extension.py:614-695`). Those refusals are: unavailable, depth, parse, profile, cwd, over-budget. `_execute` never runs, so no `_run`, no spawn id, no session write path.
- **`/agents run` declines** return `declined_result(id=_new_id())` before `_run` (`runtime.py:568-573`).
- **In-`_run` refusals** (drain, live cap, budget) return `_error_result` before the insert (`runtime.py:813-826`).
- **Batch refusals.** The batch wall budget and `TaskTooLarge` never call `spawn_granted` (`batch.py:463-471`, `:380-386`). Chain NOT RUN steps have no envelope at all.
- All of these are already persisted as toolResult text (or are a human's `/agents run` choice) and spent nothing.

**Proposed text for A.3:** "Records exist for **admitted** spawns only, meaning a registry row existed: start, pending, settle, final. Other outcomes get no record; the toolResult text already persists them." Add a per-exit-path table:

| Exit path | Writer | Records |
| --- | --- | --- |
| ok | `_run` | start, pending, settle, final |
| error | `_run` | start, pending, settle, final |
| timeout | `_run` | start, pending, settle, final |
| aborted | `_run` | start, pending, settle, final |
| exec failure | `_run` | start, pending, settle, final |
| raised before exec | `_run` `finally` | start, pending, settle, final |
| cancel | `_run` `finally` | start, pending, settle, final |
| not admitted | — | none |

This also keeps "start without settle" as the only unknown state.

### 9. SHOULD-FIX — A.6's premise is false: `/agents show` never renders `profile_to_argv`.

**Evidence:**
- `_render_agent_profile` renders `shlex.join(profile_to_flags(profile, prompt_path=profile.file_path))` (`tui/commands.py:786`).
- `profile_to_argv`'s only callers are `print_channel.build_child_argv` and `rpc_channel.build_rpc_child_argv` (grep).
- So the dry run has never shown `--mode json -p --no-session`, `--permission-mode`, the trust flags or `--no-agents`.
- The map's §1.2 and hazard 5, `resolver.py:300-302` ("P1 uses this ONLY for /agents show"), and `build_child_argv`'s docstring ("the same call the /agents show dry-run renders") are all stale.

**Proposed text for A.6:** "`/agents show` keeps rendering the profile flags. `--session <path>`, like `--permission-mode`, the trust flags and `--no-agents`, is spawn-time state and is not shown. `profile_to_argv(..., session_path=None)` emits `--session <p>` or `--no-session`." Fix the two stale docstrings in the same change.

### 10. SHOULD-FIX — hazard 14 is not addressed: a child file resumes as an unclamped top-level session.

**Evidence:**
- `aelix --session <child.jsonl>` goes through `_build_session` → `repo.open` like any file (`entry.py:430-437`). Nothing marks the file as a child's.
- The clamp (`--permission-mode`), tool narrowing, `--no-agents` and `AELIX_SUBAGENT_DEPTH` are argv and env state (`print_channel.build_child_argv`, `build_child_env`), not stored in the file. The resumed session therefore runs with the user's full posture and tools, and can delegate. Its own children would nest under `<child-stem>/`.

**Proposed text (new §A.11):**
> A child file is a record, not a resumable identity. Pick one:
> - `_build_session` warns on stderr (or refuses without `--force`) when the first entry is `aelix.child_origin` — a cheap product-core check;
> - document it.
>
> For reading a child file, point users at `aelix --export <child.jsonl>`. `entry.py:_run_export` loads with `repo.open`, renders HTML, and writes nothing to the session. Never point them at `--session`.

### 11. SHOULD-FIX — a pi_parity pin breaks when `SessionStats` gains `tool_usage`.

**Evidence:** `tests/pi_parity/test_phase_4_10_strict_superset.py:184-208` asserts `fields - expected == {"cost_known"}`. Its docstring says every Aelix-additive field "must be named here". CLAUDE.md rule 11 says not to break `tests/pi_parity/`.

**Proposed text for §B/§C:** "Amend that set to `{"cost_known", "tool_usage"}` deliberately, and specify `tool_usage`'s shape (NIT 20)."

### 12. SHOULD-FIX — two owner-memo items are not mapped to decisions.

**(a) `roll_up_usage` → `_session_stats.py`.** Appendix B #4 in `docs/05` says to connect `aggregate.roll_up_usage` to `harness/_session_stats.py` ("부모/자식 이중 합산 금지, `tokens`는 최대값" — no double counting between parent and child; `tokens` takes the maximum). The design writes a new fold in the kernel and leaves `roll_up_usage` (`aggregate.py:116-142`) as a second, unmentioned copy of the rule.

Proposed text: "The kernel owns the neutral fold (flows summed, levels never). `roll_up_usage` keeps only the display-side level max for the `[total]` line, and a test pins the two equal on flows."

**(b) "the `details` slot → a link".** The design never says what happens to `ToolResult.details`.

Proposed text: "The persisted link is the `aelix.child_session` record, because `details` is never persisted (#168). Live `details` stays the uncapped summary string that `_join_details` and the tool card consume." Also decide whether live `ToolExecutionEndEvent` consumers (`--mode json` stdout, RPC; the future web viewer, owner decision #5) get `{session_id, path}` there.

### 13. SHOULD-FIX — §D cannot observe what it claims, and it skips the riskiest paths.

**Evidence:**
- `--mode json -p` prints no stats, so "stats include the child cost" is not observable from the listed command.
- CLAUDE.md rule 9 requires launching the TUI for the `/cost` and `/stats` display change listed in §B.
- Rule 10 requires live runs of changed runtime paths. Cancel, teardown and `/agents run` are the paths A.3 changes most.

**Proposed text for §D.** Add these runs:
1. `uv run aelix --agents …` in the TUI: delegate, then check `/cost` and `/stats`, including `≥ $X` while a run is pending.
2. Ctrl+C mid-delegation: expect settle `cancelled`, the pending→final ledger, and `--continue` (with the same `--session-dir`) resuming the parent.
3. Quit mid-delegation: the settle lands in the parent (item 1).
4. `/agents run` right after `/new`: recorded (item 1).
5. A `--no-session` parent with `AELIX_CODING_AGENT_SESSION_DIR` set: no file anywhere.
6. An early-exit child (a profile with a bad `--model`): the child file holds header + origin, and the settle is `error`.

### 14. SHOULD-FIX — some docs become false, and §B does not list them.

**Evidence:**
- ADR-0201 (`0201-…md:281-285`): "`--no-session` must be appended by the channel … twelve session files per prompt in the user's `/resume` picker".
- ADR-0197 argv text (`0197-…md:506-509`): the prefix is `--mode json -p --no-session`.
- `docs/guides/agent-profiles.md:99`: the `output_cap` row, if A.9 changes.
- AGENTS.md: the work is not done while an ADR or `docs/` disagrees with the code.

**Proposed text for the §B docs row:** add amendment notes that point at ADR-0243 in ADR-0201 and ADR-0197, the guide row, and the two stale docstrings from item 9.

---

## NIT

**15. NIT — store a relative link as well.** An absolute `path` breaks when `~/.aelix/sessions` moves or a parent and its `<stem>/` move together. Add `rel` (`<stem>/<name>`, relative to the parent file's directory), and have readers try `rel`, then `path`, then a lookup by `session_id`.

**16. NIT — edge placements.**
- **Parent placed directly in the sessions root.** Its `<stem>/` becomes a top-level directory, so the global `list()` sees child files. That feeds id-prefix resolution for `--session <id>`/`--resume <id>` (probe §P.2 [2]).
- **Parent name not ending in `.jsonl`.** `with_suffix("")` collides with the file itself.
- **Rule:** strip only a `.jsonl` suffix, otherwise append `.children`. When the parent's directory equals the sessions root, fall back to `--no-session` (or use `.children`).

**17. NIT — the origin entry must have `parent_id=None`.** Otherwise the child's startup `get_branch()` → `get_path_to_root` raises `invalid_session` (`jsonl_storage.get_path_to_root`) and the child crashes before its first turn. Say so, and pin it.

**18. NIT — record field hygiene.**
- **`permission_mode`:** write it as `.value`. A non-JSON value makes `_encode_line` raise `invalid_entry`, and the record is lost.
- **Aelix version:** add `aelix_version` to the origin and start records. `docs/05` §5 stage 1 lists "모델·확장 버전" (model and extension versions).
- **Settle `model`/`provider`:** take them from the envelope (what actually ran), not from the start's requested `spawn_model`.
- **Bad values in the fold:** it ignores non-finite or negative numbers, and a record with one counts as `cost_known=False`. ADR-0242 rule 1.7: NaN round-trips bare.

**19. NIT — state the stats semantics exactly.**
- The ledger is read over the **whole root→leaf path**, including entries before the latest compaction, while parent messages are the post-compaction `_state.messages`.
- Record the divergence from Pi, which sums over all entries of the file (`agent-session.ts getSessionStats` iterates `getEntries()`), per ADR-0235.
- List the residual under-counts: child compaction calls, null-usage retry attempts, an in-flight turn at timeout.

**20. NIT — specify `tool_usage`'s shape.** For example `ToolUsage(tokens: SessionStatsTokens, cost, cost_known, runs, pending)`. Say it stays off the RPC wire (`rpc_mode._session_stats_to_dict` enumerates its keys, verified).

**21. NIT — A.5 marker.**
- Pin the exact wording in a test.
- Keep absolute paths (home directory, username) out of the model's context.
- The marker flows into chain `{previous}` (`batch._run_chain`).
- Write "no claim" when allocation failed or the child never ran.

**22. NIT — §C should list the existing pins it has to move deliberately.**
- `tests/agents_ext/test_child_argv_contract.py:92`
- `tests/agents/test_rpc_sprint_pins.py:260`, `:358`
- `tests/agents_ext/test_reduce_consumes_real_print_mode_output.py:162`: the header now appears for sessioned children.
- `test_profile_resolver.py:351` keeps passing with the default `session_path=None`.

**23. NIT — ADR-0242 wording.**
- "custom entries never enter context" is rule **1.6**, not "§A.1.5".
- ADR-0242's *Not done here* lists "a usage ledger" (pi v4's separate store). Say that `aelix.usage` is a rule-1 `CustomEntry` record, not that store, so a reviewer does not read it as a contradiction.

**24. NIT — operational side effects for the CHANGELOG.**
- Every delegation now persists a full child transcript, up to 12 per prompt, with no retention policy.
- `stats-history.jsonl` rows (`shell._record_history`, cumulative per session) include child spend from #199 onward, so per-project History totals jump.

**25. NIT — `/agents run` records.**
- The task text lives only in the child file ("Task: …"). A bounded task preview in the start record makes the parent record self-describing.
- A "recorded at …" line in the `/agents run` panel needs an additive `SubagentResult` field. Per precedent that does not bump `CONTRACT_VERSION`. Otherwise leave it to A.10.

**26. NIT — re-check the ADR number at merge.** 0243 is free today (0242 lands with #294), but the owner runs parallel sessions (CLAUDE.md rule 5).

---

## §V Verification ledger (the design's own claims)

| Claim | Verdict | Evidence |
| --- | --- | --- |
| A.1: `<bucket>/<stem>/` is invisible to `list`, `list(cwd)`, `find_most_recent` and id-prefix resolution | **Verified** | `jsonl_repo.list` and `find_most_recent` skip `kind == "directory"`. `_list_session_dirs` returns top-level directories only. Probe §P.2 [1]: a newer child is not picked and not listed. |
| No other walker of the sessions root | **Verified** | The only `list_dir|iterdir|glob|walk|scandir` hits under `packages/*/src` on the sessions root are in `jsonl_repo`. `aelix-server` only calls `repo.create`. `/tree` is fork lineage (`commands.py:1574`). |
| No existing directory is named `<stem>` | **Verified** | Buckets come from `create_dir`. Temps are files (`<path>.tmp`, `<dest>.<12hex>.tmp`). |
| Edge cases break invisibility or paths | **Refuted for** a root-placed parent, a non-`.jsonl` parent name, a relative `--session` parent | Items 16 and 4. |
| A.2: `--mode json -p --session <abs>` opens and appends | **Verified** | `entry.py:398` `looks_like_path` (`/`, `\\`, `.jsonl`) → `load_jsonl_session_metadata` → `repo.open(meta, cwd_override=cwd)` (`:437`). The override is in memory only (`jsonl_repo.open`). The startup cwd check exists only on in-session `switch_session` (`session_cwd.py`). The session is built at `:2156`, before the model/key gate at `:3121`, so an early-exit child still opened the file. `_seed_startup_state` may append `thinking_level_change` (harmless). `parse_args` accepts the child argv with `unknown_flags == {}` (probe §P.5). |
| Child cwd ≠ header cwd | **Verified harmless** | In-memory override; no rejection. |
| RPC: `--session` replaces `--no-session` | **Verified** | `rpc_channel.py:193` appends `--no-session` itself; `--mode rpc --session` parses (§P.5). |
| A.3: `spawn_id` minted after admission, unique | **Verified** | `runtime.py:827`, `_new_id` = 48 random bits. |
| A.3: `spawn_id` present on every path | **Refuted** | Item 8. |
| A.3: `SubagentHost.session` via the live `ExtensionContext` is enough | **Refuted** | Item 1. |
| A cancelled task's `finally` can await an append | **Verified with a caveat** | §P.3: yes, unless a second cancel meets a suspension point. |
| The harness abort path writes to the session concurrently | **Verified: it does not** | `core.py:abort` cancels the turn task; abort close-out stays off the write path. |
| A.4: stats already read the branch | **Verified** | `_cost_is_complete` (`core.py:2645`). |
| A.4 can pass the kernel gates | **Verified** | The gates ban only `subagent` (case-insensitive) and `aelix_agents`. `harness/core.py` and `_session_stats.py` are already on `_KERNEL_CHANGE_ALLOWLIST`; the comment convention still wants an ADR line. |
| RPC wire unchanged | **Verified** | `rpc_mode._session_stats_to_dict` enumerates keys. |
| "Branch semantics like messages" | **Partial** | Item 19. |
| A.5: the current marker is false after the call | **Verified** | `loop._to_tool_result_message` drops `details`; the TUI renders no `details` for `agent`. |
| A.6: the dry run shows the child argv | **Refuted** | Item 9. |
| A.7: a fork keeps the records | **Verified** | ADR-0242 rule 1.3 table: `CustomEntry.data` survives. |
| Nothing deletes sessions | **Verified** | No `.delete(` callers in `packages/*/src`. |
| §0: compaction never cuts at a custom entry or a toolResult | **Verified** | `compaction.find_valid_cut_points` (`:426-449`). |
| Stats consumers see child spend once | **Verified by reading** | The footer (`shell._refresh_context_usage`: input, output, cost), `/cost`, `/session`, `/stats`, History (`shell._record_history`) and RPC all read `get_session_stats`, so all of them get the merged totals. Only `/cost` and `/stats` would show `tool_usage` separately. No consumer walks child files. |
| Windows file handles | **No conflict on our side** | `LocalFileSystem.append_file` opens and closes on every call (wt-294 `fs.py:231-252`), so a running child holds no long-lived handle. The parent writes the child file once (the publish) before the spawn. The Windows exposures are path length (item 5) and the rename-retry sleep during the publish (item 3). |

## §H Hazard coverage (map's 18)

| # | Coverage |
| --- | --- |
| 1 | covered |
| 2 | covered |
| 3 | covered |
| 4 | covered |
| 5 | **false premise** (item 9) |
| 6 | partial (items 7, 8) |
| 7 | covered, mechanics gaps (items 3, 6) |
| 8 | covered |
| 9 | covered, `cost_known` undefined (item 6) |
| 10 | mostly; crash window → item 2 |
| 11 | not listed (item 19) |
| 12 | covered (option b) |
| 13 | covered; absolute path only (item 15) |
| 14 | **missing** (item 10) |
| 15 | open (A.9) |
| 16 | covered (item 21) |
| 17 | partial (items 1, 3) |
| 18 | covered; say "stub-only" explicitly |

**New hazards found here:**
- stale or pre-hook context (item 1);
- relative parent path (item 4);
- Windows path length (item 5);
- the pi_parity pin (item 11);
- the dry-run premise (item 9);
- lock + second cancel losing settles (item 3).

---

## §Q The two OPEN owner questions — recommendations. Both are OWNER DECISIONS.

### A.9 `output_cap` — recommend (c)

Keep 51,200 bytes per child in **single** mode. Give a **parallel or chain call** one shared rendered budget of 64 KiB, split evenly across members. Apply it where the batch result is rendered (`aggregate.render_batch_result` → `_member_block`, re-capped with `envelope.cap_summary`), so the chain `{previous}` hand-off keeps each step's own capped summary. The per-call constant lives in `aelix_agents`; the band gates `_CAP_NAME_RE` and `_P3_CAP_NAMES` forbid it in product-core.

**Evidence (numbers):**
- **Worst case today:** 8 × 51,200 bytes → a 410,318-byte toolResult → `estimate_tokens` **102,579** (probe §P.2 [4]). `MAX_PARALLEL_TASKS = 8` covers parallel and chain (`tool.py:101`).
- **12 children per prompt** (`MAX_DELEGATIONS_PER_PROMPT`) → about 153.6k estimated tokens across two calls.
- **Parent budget:** auto-compaction triggers at `context_window − 16,384` (`core._AUTO_COMPACT_RESERVE_TOKENS`).

  | Model (catalog) | Context window | Threshold | One worst-case batch |
  | --- | --- | --- | --- |
  | `openai/gpt-4o` | 128,000 | 111,616 | **92%** |
  | `anthropic/claude-haiku-4.5` (the live-check model) | 200,000 | 183,616 | 56% |

  A toolResult is never a cut point (`find_valid_cut_points`), so that turn cannot shed it.
- **One capped child** = 12,800 estimated tokens: 6.4% of 200k, 11.5% of 111,616. That is tolerable, and it matches pi's per-task 50 KiB, which pi applies to parallel only (`PER_TASK_OUTPUT_CAP`); pi's single and chain are uncapped.
- **Typical size:**
  - The bundled profiles ask for "a paragraph or two plus the citations" (`explorer.md`) and "Keep it tight" (`general-purpose.md`).
  - The owner's real delegation on 2026-09-19 produced a **129-character** toolResult in total (`docs/05` §11, #6 check).
  - No wider distribution exists. Probe §P.1 scanned 2,770 local session files: 0 `agent` toolCalls. **Not measured beyond that one point.**
- **Under (c):** 8 members get 8,192 bytes each (about 2,048 tokens), still 2–8× a typical report. The worst case per call drops about 6.3× (410 KB → 64 KiB, about 16.4k tokens). The full text is in each child file.
- **Under (b)** (16 KiB per child): the worst case is still 8 × 16 KiB = 131 KB (about 32.8k tokens), and single-mode reports from `general-purpose` would truncate earlier for no gain.

### A.10 viewer — recommend a follow-up issue, as the design proposes

- Recording alone meets "자식 세션이 기록될 것" (child sessions are recorded).
- A read-only view already exists at zero cost: `aelix --export <child.jsonl>` (`entry.py:_run_export` → `repo.open` → `build_context` → HTML; no session write). The ADR and CHANGELOG should say so, and warn against `--session <child>` (item 10).
- A TUI surface (`/agents runs`) needs its own live check (rule 9). It also needs `tool_name` on replayed toolResults or a records reader.
- Out of scope, but worth noting for the web track (owner decision #5): RPC consumers can already read the parent file's records. Nothing new is needed on the wire.

---

## §P Probes (scratchpad; commands and output)

**P.1** `uv run --no-sync python …/scratchpad/probe_agent_results.py` (sizes only, no content printed):
```
session files scanned: 2770
files whose first user message starts with 'Task: ': 0
agent toolCalls: 0; results paired: 0 modes={}
```

**P.2** `uv run --no-sync python …/scratchpad/probe_placement.py` (temp dirs under the scratchpad):
```
[1] find_most_recent(cwd) -> <parent>.jsonl | is parent: True
[1] list(cwd) ids: ['3012f107-…'] | child listed: False
[1] list() ids   : ['3012f107-…'] | child listed: False
[2] root-placed parent; global list() sees its child: True
[3] --session sessions/--…-proj--/2026-…_3012f107-….jsonl -> meta.path: sessions/…  | storage._file_path absolute: False
[4] 8x51,200-byte batch: 410,318 bytes -> estimate_tokens = 102,579
[4] one capped child: 51,200 bytes -> estimate_tokens = 12,800
[4] 16 KiB child: estimate_tokens = 4,096
```

**P.3** `uv run --no-sync python …/scratchpad/probe_cancel_finally.py`:
```
A single cancel, non-suspending write: written=['settle-0'] of 1
B single cancel, uncontended lock: written=['settle-0'] of 1
C 2 members, shared lock, yielding holder, 2nd cancel: written=[] of 2
D same as C, writes shielded: written=['settle-0', 'settle-1'] of 2
E 2 members, NO lock, 2nd cancel: written=['settle-0', 'settle-1'] of 2
```

**P.4** Path lengths, from `jsonl_repo._encode_cwd`; see the table in item 5.

**P.5** `parse_args` on child argv:
```
['--mode','json','-p','--session','/abs/dir/x.jsonl','Task: hi']     -> session set, unknown_flags {}, diagnostics []
['--mode','json','-p','--session','C:\\Users\\a b\\s\\x.jsonl','Task: hi'] -> same
['--mode','rpc','--session','/abs/x.jsonl','--permission-mode','plan','--no-agents'] -> same
```
