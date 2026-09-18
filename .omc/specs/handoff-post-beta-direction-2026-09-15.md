# Handoff — post-beta direction

Date: 2026-09-15
Status: Draft strategy research completed; no implementation or GitHub mutations.

## 기준점

- Local/GitHub main: `026bd15db229d86d61b74fd3b8c1d8eb0f750715`.
- Latest Aelix prerelease: `v0.1.0-beta.2`, published 2026-09-09.
- CI for main: 34705932974, success. Full suite and live model/TUI/server were not run here.
- Planning draft: `docs/05-post-beta-direction.md`. It does not supersede an ADR.
- Pi research main: `8a7b0c03dfb702663acafb6dc29f8acaa4ffe391`;
  latest public release at inspection: v0.85.1.
- Existing untracked `.aelix/`, `.omc/specs/`, assets and tests were present and preserved.

## 바로 시작할 것

1. Recheck main and Project; another session may have changed them.
2. Reconcile existing open issues with shipped fixes before prioritizing by issue count.
   #198 is still In progress while CHANGELOG records a fix; verify before closure.
3. Agree next stabilization release outcomes and reuse #264/#266 for document alignment,
   #199 for child history/cost, #284 for architecture/complexity, #253 for Pack contributions.
4. Turn accepted proposals into individual Issue/ADR tasks. No issues, comments, invites,
   repository settings, release tags or PRs were created by this research.

## 이후 순서

- Stabilization and beginner documentation alongside a small useful extension.
- Durable execution/history contract and focused refactoring.
- Pack install/run UX, testkit, maintained catalog entries.
- Single-environment session server, then web; desktop and cron after their prerequisites.
- Analytics remains an independent domain package with an Aelix adapter; prove one real-model E2E.

## 측정·확인

- `uvx --from radon==6.0.1 radon cc -j packages src`: exit 0.
  Functions/methods 4,331; CC >20: 73; CC >50: 5.
  Top: `_async_main` 109, `parse_args` 92, `stream_openai_completions` 82.
- Default official catalog fetched via urllib: HTTP 200, extensions empty.
- Server source: one active RPC connection, no auth, per-connection runtime;
  bare factory does not load extensions.
- GitHub API: private vulnerability reporting disabled; required reviews null;
  enforce_admins false; six required CI checks present.
- Analytics README inspected at `/Users/handochan/dev/aelix-extensions/aelix-analytics/README.md`.
  It describes a local prototype/adapter and explicitly says real-model smoke not yet run.

## 실제로 적용되는 규칙

- One implementation issue per session/PR; keep Project, docs, ADR and handoff synchronized.
- Preserve core/extension direction and use current ADR-0235 instead of mandatory Pi parity.
- Runtime changes require real-model checks; TUI changes require direct TUI verification.
- Contributor CI/type command is `uv run python scripts/check_types.py`;
  format-check is not a current CI gate.
- A generic durable identity proposal must be reconciled with ADR-0197's no-delegation-policy kernel boundary.

## 반증된 것 — 다시 믿지 말 것

- The official catalog is not absent as an endpoint: it exists and currently contains zero entries.
- Aelix server is not wholly absent, and an RPC skeleton does not imply extension/TUI parity or remote readiness.
- Session JSONL persistence does not make child runs durable: child transports explicitly use `--no-session`.
- A high complexity maximum does not mean the whole codebase has that complexity; it is concentrated.
- An open issue is not proof the current code still has that bug.
- Pi main design, merged work, latest release contents, and promised support are distinct.
- Pi Durable Object SQLite PR #9131 was closed with merged_at null; do not call it shipped.
- Self-hosting the agent does not imply remote-model traffic stays inside the network.

## 같은 날 Claude Code 문서 교차 검토 반영

- User requested comparison with `.omc/specs/direction-post-beta2-2026-09-15.md`.
- Review: `.omc/specs/review-direction-post-beta2-codex-2026-09-15.md`.
  Updated planning draft; Claude source preserved.
- Added concrete core independence gap: 13 reverse imports, 10 runtime / 3 TYPE_CHECKING.
  Blocking coding-agent imports permits core module import but prevents Harness construction.
  A clean wheel-only environment remains a required future verification.
- Full-scope Ruff C901 >40 has six functions, not two: run_tui 248, _async_main 82,
  parse_args 80, stream_openai_completions 54, run_print_mode 42, process_responses_stream 42.
  Radon 3,592 top-level blocks and 4,331 functions/methods are different aggregations.
- Recursive Radon functions/methods/closures: 4,774; CC >20 74; CC >50 5.
  This is the recommended future comparison scope. Ruff version: 0.15.13.
- Reuse AgentProfile; stage install/discovery/run contracts. Analytics requires Python >=3.12,
  so keep the heavy domain runtime separate from the Aelix adapter.
- RPC UI response types exist but incoming responses are discarded. Session switching exists;
  listing and command execution are missing from the dispatch table.
- Corrected lineage: entry.parent_id is entry-tree linkage, not fork-only;
  session parent_session_path is distinct from a future spawn relationship.
- Pi retains a new JSONL backend. Do not describe legacy implementation removal as abandoning JSONL.
- Two broad work streams and one headline per release are proposals; hard deadlines,
  required example counts, and all-RPC-parity before any web slice were not adopted.
- Claude source changed concurrently from 525 to 600 lines; reread new sections and recorded
  both hashes in the review. It now agrees on Ruff six functions and retained Pi JSONL;
  some old two-function baseline text and the entry parent_id interpretation remain.
