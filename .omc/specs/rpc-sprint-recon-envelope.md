# Event-envelope recon — ADR-0198 and the cross-channel parity test

> **Provenance.** Recovered from the P3 recon workflow's journal
> (`~/.claude/projects/<proj>/subagents/workflows/wf_fa42eb2d-e07/journal.jsonl`) after the session
> scratchpad holding the original prose note was wiped — the exact landmine
> `p3-kickoff-handoff.md` warned about. The structured findings below are the recon agent's own
> return value verbatim; the surrounding prose of the original note did not survive.
>
> Every `path:line` was opened against **`6d7ec9a`**, which is P3's base. P3 landed on top of it and
> moved things in `aelix_agents/`; the `rpc/` and kernel citations are untouched by P3 (its
> product-core and kernel deltas are both byte-empty). **Re-verify before relying on a line number.**

**Scope read:** `docs/decisions/0198-print-mode-json-envelope-contract.md`, `modes/print_mode.py`, `aelix_agents/stream.py`, `aelix_agents/envelope.py`, the envelope conformance tests

## Summary

EXISTS: `--mode json` emits one `dataclasses.asdict` of a kernel event dataclass per line (print_mode.py:161 -> rpc_mode.py:258-268). `--mode rpc` uses the SAME serializer (rpc_mode.py:1929), so the event halves are already shape-identical. A strong real-child conformance test exists for json (tests/cli/test_print_mode_json_contract.py) pinning sequence, snake_case fields, discriminators and framing. P2's consumer side (LineAssembler + reduce_line + build_result) obeys ADR-0198 D6 with an explicit 8 MiB limit and a 4 MiB per-line drop budget.

MISSING: there is NO version field on the wire anywhere (CONTRACT_VERSION is a Python-object binding check only); no RpcChannel; no rpc-side conformance test; no test in the repo drives a real `--mode rpc` child; RpcClient has no explicit reader `limit=` and JsonlLineReader has no per-line budget, so `dropped_lines` is structurally unfillable on rpc; no `reduce_event(state, dict)` entry point (only `reduce_line(str)`).

The parity test's field classification (must-match vs cannot-compare) is in section 5 of the note; the exit_code landmine (envelope.py:201-205 vs a server that never exits on its own) is the highest-value finding.

## Contradictions — committed docs vs the code

The highest-value entries: an accepted document asserting something the code does not do. Several of these are why this sprint was split out of P3.

- **Doc says:** Spec §8 Phase 3: 'The existing RpcClient test harness (tests/rpc/*, tests/pi_parity/test_phase_4_4_strict_superset.py) already drives real --mode rpc children and is the ready-made bed.'
  <br>**Code says:** No test in the repo spawns a real --mode rpc aelix child. tests/rpc/test_rpc_client_lifecycle.py:88's own comment reads 'RpcClient configured to launch the inline stub instead of -m aelix'; every tests/rpc/test_rpc_mode_*.py drives run_rpc_mode in-process against a fake harness; test_phase_4_4_strict_superset.py only imports symbols. P3 must BUILD the bed, not reuse it.
  <br>`.omc/specs/multiagent-profiles-teams-architecture-spec.md:378 vs tests/rpc/test_rpc_client_lifecycle.py:88`
- **Doc says:** Spec §9: the new ADR 'Versions the --mode json / rpc event schema + SubagentResult.'
  <br>**Code says:** Nothing on the wire carries a version. ADR-0198 versions only the SubagentResult dataclass (D8). No kernel event dataclass and no session-metadata header has a version field, and CONTRACT_VERSION is a binding-time Python-object check that is never transmitted.
  <br>`.omc/specs/multiagent-profiles-teams-architecture-spec.md:411 vs packages/aelix-agent-core/src/aelix_agent_core/types.py:165-290 and packages/aelix-coding-agent/src/aelix_coding_agent/subagent_contract.py:34-45`
- **Doc says:** ADR-0198 'Partial fulfilment': the P3 follow-up is 'the cross-channel parity test asserting that the same turn produces equivalent EVENT SEQUENCES on both channels.'
  <br>**Code says:** Spec §8 Phase 3 — the ratified P3 scope — asks instead for 'cross-channel parity test (PrintChannel ≡ RpcChannel SubagentResult for the same task)'. These are two different assertions; satisfying only the SubagentResult form leaves ADR-0198's own named follow-up unfulfilled.
  <br>`docs/decisions/0198-print-mode-json-envelope-contract.md:242-247 vs .omc/specs/multiagent-profiles-teams-architecture-spec.md:392`
- **Doc says:** ADR-0198 D1: the wire is snake_case, and the only camelCase survivors are the content discriminators 'text'/'toolCall' plus the {type, …} envelope itself.
  <br>**Code says:** True of --mode json. On --mode rpc the SAME fd interleaves a command/response envelope that is camelCase by design (_camel at rpc_types.py:27-34, the per-command key map 'set_model': {'model_id': 'modelId'} at :383, and RpcSuccessResponse/RpcErrorResponse.to_json at :463-511). One rpc stdout stream carries two spellings.
  <br>`docs/decisions/0198-print-mode-json-envelope-contract.md:66-71 vs packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py:463-511`
- **Doc says:** ADR-0198 D6: 'The contract is therefore on the reader: pass an explicit limit=, read fixed chunks, and assemble lines yourself, dropping an in-progress line that exceeds a stated per-line budget and resynchronising at the next newline. Never raise.'
  <br>**Code says:** RpcClient — the class P3 is told to wrap — passes NO explicit limit= (rpc_client.py:120-127) and its JsonlLineReader has no per-line budget, no drop and no counter (_jsonl.py:61-73). It avoids the ceiling only incidentally, because _jsonl.py:106 uses read(4096) rather than readline().
  <br>`docs/decisions/0198-print-mode-json-envelope-contract.md:173-176 vs packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:120-127`
- **Doc says:** ADR-0197 §(h): 'Never -m aelix (rpc/rpc_client.py:466 does exactly that and it is a live bug — python -m aelix is the umbrella meta-package demo).'
  <br>**Code says:** Spec §8 Phase 3's 'Reuses:' line nevertheless names rpc_client.py:425-450,158-204,267 as the RpcChannel reuse surface, and rpc_client.py:466 still builds [sys.executable, '-m', 'aelix', '--mode', 'rpc']. src/aelix/__main__.py:1 self-describes as 'Demo entry point' and src/aelix is not in the wheel's packages list (pyproject.toml:110).
  <br>`docs/decisions/0197-subagent-runtime-seam-and-aelix-agents.md:504-507 vs packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:466`
- **Doc says:** ADR-0198 D3: 'The first line has no type, and may not be there at all.'
  <br>**Code says:** Corrected inside the same ADR at :207-212 and pinned as a correction by tests/cli/test_print_mode_json_contract.py:308-325 — a subagent runs --no-session, the header is guarded by 'if session is not None' (print_mode.py:176), so for the delegation case the first line IS agent_start. D3's phrasing must not be copied into the P3 rpc-half record.
  <br>`docs/decisions/0198-print-mode-json-envelope-contract.md:99-108 vs tests/cli/test_print_mode_json_contract.py:308-325`
- **Doc says:** Spec §9: 'Nothing parses child output until this [envelope] ADR lands.'
  <br>**Code says:** Honoured for json (ADR-0198 landed with P2's PrintChannel), but the rpc half is explicitly undelivered while spec §8 Phase 3 simultaneously asks for an RpcChannel that parses rpc child output. Taken literally, the RpcChannel cannot ship before the rpc-half envelope record does.
  <br>`.omc/specs/multiagent-profiles-teams-architecture-spec.md:412 vs docs/decisions/0198-print-mode-json-envelope-contract.md:242-247`

## Findings (34)

- The `--mode json` wire format is a bare `dataclasses.asdict` of kernel event dataclasses — there is no serializer, no field map, and therefore no place to add a version. The emitter is one line.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:161`
- `--mode rpc` emits its events through the IDENTICAL serializer, so the event halves of the two channels are already shape-identical by construction; a parity test will not discover field-name divergence.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1927-1940`
- Both channels share `_dataclass_to_dict` = `dataclasses.asdict(value)`.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:258-268`
- NO VERSION FIELD EXISTS ON THE WIRE. No event dataclass in the kernel union carries one, and the session-metadata header carries only id/created_at/cwd/path/parent_session_path.
  <br>`packages/aelix-agent-core/src/aelix_agent_core/types.py:165-290`
- `CONTRACT_VERSION` is a binding-time check on a Python object, never transmitted and never read off a child stream.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/subagent_contract.py:34-45`
- The typeless header is emitted only when `mode == "json"` AND `session is not None`, under a try/except that swallows everything but BrokenPipeError. `--mode rpc` emits no header at all.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:174-185`
- `message_end.message` (assistant) fields are all snake_case and always present (asdict emits defaults): content, stop_reason, error_message, usage, timestamp, api, provider, model, response_id, role.
  <br>`packages/aelix-ai/src/aelix_ai/messages.py:108-139`
- The content-block discriminators are `text`/`image`/`thinking`/`toolCall` — Literal defaults, not field names, which is why `toolCall` survives asdict as the one camelCase island.
  <br>`packages/aelix-ai/src/aelix_ai/messages.py:42-93`
- `tool_execution_start` carries the same arguments under `args` while the tool-call CONTENT BLOCK uses `input` — reading the wrong key yields {} not an error.
  <br>`packages/aelix-agent-core/src/aelix_agent_core/types.py:194-199`
- ADR-0198's own words scope the parity test to EVENT SEQUENCES, while spec §8 scopes it to SubagentResult. These are two different tests and only one is in the stated P3 deliverable.
  <br>`docs/decisions/0198-print-mode-json-envelope-contract.md:242-247`
- Spec §8 Phase 3 states the parity test is `PrintChannel ≡ RpcChannel SubagentResult for the same task`.
  <br>`.omc/specs/multiagent-profiles-teams-architecture-spec.md:392`
- The 64 KiB ceiling is asyncio's StreamReader default; P2 worked around it with an EXPLICIT 8 MiB limit= at spawn plus fixed-chunk reads, never readline().
  <br>`packages/aelix-coding-agent/src/aelix_agents/print_channel.py:794-813`
- `STREAM_LIMIT_BYTES = 8 MiB` exists specifically so the 4 MiB per-line BUDGET fires (drop+count) instead of the reader's unrecoverable ValueError.
  <br>`packages/aelix-coding-agent/src/aelix_agents/print_channel.py:113-118`
- `LineAssembler` drops over-budget lines, counts them, and resyncs at the next newline; the count rides out on SubagentResult.dropped_lines.
  <br>`packages/aelix-coding-agent/src/aelix_agents/stream.py:138-155`
- `RpcClient.start` calls create_subprocess_exec with NO explicit `limit=`, so its readers carry the 65536 default — violating ADR-0198 D6's reader contract. It is harmless today only because the reader uses read(4096), not readline().
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:120-127`
- `JsonlLineReader.feed` accumulates into an unbounded str buffer with no maximum, no drop and no counter — so an RpcChannel built on it can never populate `dropped_lines` and has an unbounded parent-memory line buffer.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:61-73`
- THE PARITY LANDMINE: build_result treats any non-None non-zero exit_code as failure. A --mode json child exits 0 on its own; a --mode rpc child is a server killed by the parent to end the run, so its returncode is an artifact of teardown, not of task success.
  <br>`packages/aelix-coding-agent/src/aelix_agents/envelope.py:201-205`
- An rpc child handles SIGTERM/SIGHUP as a graceful shutdown_event, so RpcClient.stop()'s terminate→1s→kill can yield either a clean exit or -9 depending on timing.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:2001-2012`
- BYTE-LEVEL PARITY IS IMPOSSIBLE: print-mode uses json.dumps with the default ensure_ascii=True while the rpc framer uses ensure_ascii=False, so identical Korean text is \uXXXX-escaped on one channel and raw UTF-8 on the other. Values match after json.loads; bytes never will.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:19-31`
- The rpc channel interleaves a SECOND envelope on the same fd — `{type:"response", command, success, data|error, id}` — and that envelope is camelCase while the events beside it are snake_case. ADR-0198 D1's snake_case claim is only half-true of rpc.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py:463-511`
- The camelCase mapping for rpc commands/responses is explicit (`_camel`, and per-command key maps such as set_model→modelId).
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py:27-34`
- `--mode rpc` redirects the child's sys.stdout into sys.stderr for its whole life (pi's takeOverStdout) while `--mode json` explicitly declines to — so the two channels have structurally different stderr content, and SubagentResult.details/error are not parity surfaces on failure paths.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1846-1849`
- NO test in the repo drives a real `--mode rpc` aelix child: the only RpcClient process test launches an inline stub via cli_path, and every rpc_mode test drives run_rpc_mode in-process with a fake harness.
  <br>`tests/rpc/test_rpc_client_lifecycle.py:88`
- tests/pi_parity/test_phase_4_4_strict_superset.py is a symbol/closure pin that imports names; it spawns no process.
  <br>`tests/pi_parity/test_phase_4_4_strict_superset.py:1-40`
- The json conformance test consumes a REAL child through a REAL pipe using the delegation's own LineAssembler, pumping both pipes concurrently — this harness is the reusable bed for the parity test.
  <br>`tests/cli/test_print_mode_json_contract.py:206-253`
- The conformance test pins that camelCase spellings are ABSENT (not merely unused) on the wire.
  <br>`tests/cli/test_print_mode_json_contract.py:363-365`
- The conformance test pins that an errored JSON run still exits 0 — the stream, not the exit code, is the evidence.
  <br>`tests/cli/test_print_mode_json_contract.py:386-394`
- `PrintChannel` exposes injectable argv_builder/env_builder seams that exist precisely so tests can drive the real pump/reaper/envelope against a scripted child; an RpcChannel without the same seam is untestable the same way.
  <br>`packages/aelix-coding-agent/src/aelix_agents/print_channel.py:697-708`
- `reduce_line` takes a str, while RpcClient.prompt_and_wait returns list[dict] — today the only bridge is a re-serialize round trip (which the conformance test performs).
  <br>`packages/aelix-coding-agent/src/aelix_agents/stream.py:406-468`
- elapsed_ms is wall-clock from time.monotonic and an rpc run additionally pays RpcClient's 100 ms STARTUP_GRACE_MS, so it can never be compared literally.
  <br>`packages/aelix-coding-agent/src/aelix_agents/print_channel.py:765`
- usage.cost has two independent sources (child-reported cost, else a parent-side live model-registry fallback), so it is only comparable when both channels are given the same stubbed usage and the same registry.
  <br>`packages/aelix-coding-agent/src/aelix_agents/print_channel.py:425-457`
- `--mode rpc` is dispatched BEFORE the no-usable-model guard, which is gated `if app_mode in ("print", "json")` — so an rpc child with no API key starts the server instead of dying fast, a failure-taxonomy divergence any parity fixture will hit.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:2157-2196`
- Spec §10's claim that both channels flow through the single `_build_harness_options` build DOES hold — there is exactly one call site for every mode.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1928`
- The only snapshot infrastructure in the repo is pyte terminal-grid snapshots for the TUI; tests/contracts is descriptor Pydantic models. There is no golden-file infra applicable to the envelope, and given the ensure_ascii divergence a golden-file approach would be actively wrong.
  <br>`tests/tui/test_snapshots.py:1-30`

## Gaps — what this sprint must build or change (13)

| What | Where | Kind |
|---|---|---|
| Write the cross-channel parity test asserting an equivalent SubagentResult for the same task on PrintChannel and RpcChannel. MUST-MATCH: ok, status, summary, truncated, stop_reason, usage.input/output/cache_read/cache_write, usage.tokens (level not sum), usage.turns, profile, dropped_tools, permission_mode. CANNOT-COMPARE-LITERALLY: id (unless both plans are forced to the same literal id), elapsed_ms, exit_code, usage.cost (unless usage is stubbed and the registry is shared), details, error (failure paths only), dropped_lines (structurally 0 on rpc). | `tests/agents_ext/ (new file) — reuse the harness at tests/cli/test_print_mode_json_contract.py:206-253` | new-file |
| Write the rpc-side conformance test analogous to tests/cli/test_print_mode_json_contract.py: a REAL `--mode rpc` child, both pipes pumped, asserting the event sequence, snake_case message_end fields, the text/toolCall discriminators, AND the interleaved camelCase `{type:"response"}` frames a consumer must skip. Nothing like it exists. | `tests/rpc/ (new file)` | new-file |
| Build the real `--mode rpc` child test bed from scratch. Spec §8 says one already exists; it does not. Budget for it explicitly. | `tests/rpc/` | new-file |
| Decide and encode what `exit_code` means for a long-lived channel. Passing a terminate-derived returncode into build_result turns every successful rpc delegation into ok=False/status=error. `None` is the only value the current predicate treats as non-evidence. | `packages/aelix-coding-agent/src/aelix_agents/envelope.py:201-205` | modification |
| Add a `reduce_event(state, dict)` sibling to `reduce_line(state, str)` so a channel that already holds parsed dicts (RpcClient.prompt_and_wait returns list[dict]) does not have to re-serialize. Additive; `reduce_line` becomes json.loads + reduce_event. | `packages/aelix-coding-agent/src/aelix_agents/stream.py:406-468` | additive |
| Build the RpcChannel with its OWN argv (mirroring aelix_agents.print_channel.build_child_argv). Do NOT reuse RpcClient._build_argv — it launches `-m aelix`, the umbrella DEMO package, which ADR-0197 §(h) already names a live bug, and which spec §8 nevertheless lists as reuse surface. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:466` | new-file |
| Give RpcChannel the same injectable argv_builder/env_builder seams PrintChannel has, or the parity test cannot drive it against a scripted child and becomes an integration test needing a real model. | `packages/aelix-coding-agent/src/aelix_agents/print_channel.py:697-708 (pattern to mirror)` | new-file |
| Give the rpc read path a per-line budget + dropped-line counter (LineAssembler has one; JsonlLineReader does not), or accept and document that SubagentResult.dropped_lines is structurally unfillable on rpc. Note the kernel/product-core band rule: _jsonl.py is product-core, so a budget added THERE is a product-core modification — prefer wrapping in aelix_agents. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:61-73` | modification |
| Pass an explicit `limit=` when the RpcChannel spawns its child, per ADR-0198 D6's reader contract. RpcClient.start passes none today. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:120-127` | modification |
| Write the rpc HALF of the envelope record that ADR-0198 explicitly deferred to P3 — including the fact that the rpc stream carries a camelCase response envelope beside snake_case events, that it emits no session header, that stdout is redirected into stderr, and that the no-usable-model guard does not apply to it. | `docs/decisions/ (new ADR)` | new-file |
| Decide whether to add a wire-level version field. Spec §9 asked for a versioned `--mode json` / rpc event schema; neither the ADR nor the code delivers one. Adding it is a MODIFICATION to the shared serializer both modes use and touches the kernel's event shape indirectly; the alternative is to amend spec §9's wording. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:258-268` | modification |
| Add a regression test that a `message_update` event does not double-count into _StreamState (D4's ignore rule is documented but unenforced). | `tests/agents_ext/test_stream_reduce.py` | test-only |
| Add a test for the D5 zero-stdout startup-death case (child dies before the loop, exits non-zero, writes no stdout at all) against the contract file — currently only the errored-STREAM case is pinned. | `tests/cli/test_print_mode_json_contract.py` | test-only |

## Open questions the plan author must settle

1. Which parity test does P3 owe — ADR-0198's event-sequence form, spec §8's SubagentResult form, or both? They are different tests and the docs disagree.
2. What is `exit_code` for a long-lived channel? `None` is the only value envelope.build_result treats as non-evidence (envelope.py:203); anything else turns a successful rpc delegation into ok=False/status=error. This is a contract decision, not a test detail.
3. Does the RpcChannel grow a per-line budget and a dropped_lines counter, or is that asymmetry accepted and documented? Note that adding the budget to rpc/_jsonl.py would be a product-core modification and may collide with the band rule — wrapping in aelix_agents is the safer shape.
4. Is a wire-level version field in scope? Spec §9 asked for a versioned event schema and neither ADR-0198 nor the code delivers one; adding it is a modification to the serializer BOTH modes share, while the alternative is amending spec §9's wording to match reality.
5. Does the RpcChannel get the same injectable argv_builder/env_builder seams PrintChannel has (print_channel.py:697-708)? Without them the parity test cannot drive it against a scripted child and degrades into an integration test needing a real model.
6. Does P3 also write the rpc HALF of the envelope record (ADR-0198's other named follow-up), or only the parity test? Spec §9's 'nothing parses child output until this lands' arguably makes the record a prerequisite for the RpcChannel itself.
7. Should `--mode rpc` emit the session-metadata header for symmetry with `--mode json`, or is its absence the intended shape? Nothing documents the divergence today.
8. The ensure_ascii divergence (print_mode.py:161 default True vs _jsonl.py:31 False) also means the 4 MiB per-line budget bites ~2x earlier for CJK on the json channel. Is that acceptable, or should the two emitters be aligned?
