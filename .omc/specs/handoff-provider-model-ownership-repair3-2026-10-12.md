# #365 repair 3 author handoff — 2026-10-12 KST

## Current authorized boundary

User explicitly approved further #365 repair and independent validation on 2026-10-12 KST
("승인합니다. 이어서 진행하세요"). This resumes repair 3 after the skill's two failed automatic
repair-cycle boundary. The two failures are retained: 815f initial interval finding, first
repair 64985 retry-classification failure, then repair2 e854 terminal-loop escape. Approval
is not a merge approval or a clean/complete verdict. The root batch handoff is append-only
by root; author did not write it. Current implementation source starts from frozen
`e854648a36cb3516f1083fc2447bd032d730c27b`, parent
`fe1c030e14c296493b5d063f6d4431958de3ff6a`, own tree codex-365/branch
fix/365-provider-model-ownership. Root/coordinator own atomic lock updates and rebase.

Policies: original AGENTS.md/CLAUDE.md reread; aelix-issue-to-pr skill procedure 5 reread.
User's explicit approval overrides the prior automatic-stop for this repair, not its history.
No new dependency, core→coding-agent import, broad classifier change or blanket retry disable.

## Confirmed counterfactual and narrow correction

Read immutable reviewer REVIEW.md/test_fatal_queue.py/test_fatal_registry.py/test_fatal_child.py
under /Users/handochan/dev/aelix-ai-worktrees/codex-review-365-fatal/.omc/probes/365-fatal-independent.
The fatal record prevented regex retry/overflow, but message_end could rebuild error into
stop/tool_use. The low-level loop then consumed tools/steering/follow-ups before outer harness
classification; next auth dispatch cleared the record. Actual registry and real print/RPC child
cases reproduced it with synthetic SDK transport only.

Original three files were copied byte-for-byte into own ignored
.omc/probes/365-repair3-author. Command (own venv, sanitized env):
`.venv/bin/python -m pytest -c pyproject.toml -q .omc/probes/365-repair3-author/test_fatal_queue.py
.omc/probes/365-repair3-author/test_fatal_registry.py .omc/probes/365-repair3-author/test_fatal_child.py`.
Before product edit: **14 failed / 20 passed / 6.07 s / exit1**;
after edit: **34 passed / 5.24 s / exit0**. Logs repair3-counterfactual.out and
repair3-independent-fixed.out, /tmp/aelix-365-author-2026-10-11 (historical artifact directory retained).
Reviewer sources/venv/index/refs were never mutated.

Production change is three files: AgentLoopConfig's optional pure synchronous
should_stop_after_response is appended for positional ABI; loop asks it on the final
message_end response BEFORE tool scan/execute and queue drain; Core supplies the existing
_SetupFailure final identity. Presentation/content/stop_reason remain hook-editable while
functional termination is independent. Queued inputs remain pending. Transient OAuth setup
failures end their current loop and use existing outer retry_reason logic; fatal closing
failures cannot reopen this user prompt. New user prompts, default None/false kernel predicates,
normal SDK503/OAuth retries, metadata/options headers, no-auth/custom stream and summaries keep
existing behavior. No repeated getter/OAuth/command query was added.

## Executed verification

- New kernel default None/false/true predicate controls, SDK queues/tools and separate user
  prompt recovery plus committed-input/FIFO checks; existing #320, loop, #379/#197:
  **191 passed / 1.39 s** (repair3-own-corrected.out). First own FIFO assertion expected all
  steering messages on the next request, but default is one-at-a-time; product code unchanged,
  assertion corrected to one consumed, second retained, then consumed once on the third prompt.
  Original 2fail/186pass diagnostic retained in repair3-own-first.out; fixture lint import
  redefinition corrected via explicit fixture re-export. These are not product repair cycles.
- Own real print/RPC child wire fixtures extended with same-route re-registration plus
  message_end stop replacement and scheduled follow_up/steer: **25 passed / 12.08 s**
  (repair3-own-child.out). Requests are all HTTPX intercepted; only fake tags/host/path/model recorded.
- Prior immutable independent examples: **66 passed / 12.81 s** (repair3-prior66.out), explicit
  own pytest config; earlier reviewer tree remains untouched.
- Main focused set (registered ownership, auth/wire/child, dispatch/default callers, #379 OAuth
  reasons, #197 retry recovery, #320 inputs, loops/hooks/next-turn, main/prefix/branch summaries):
  **543 passed / 14.22 s** (repair3-main-focused.out).
- Canonical types: **288 files / 0 errors / inverse3 maintained** (repair3-types-first.out).
  Windows static type model with explicit own Python: **288 files / 0 errors / existing warning1**
  (repair3-windows-types.json). Physical Windows execution belongs to coordinator CI.
- Ruff all passes. Bundle sync updated extension-authoring. Docs/citation tests:
  **39 passed / 3.36 s** (repair3-docs-final.out). Citation fix moved 50 refs/22 files; 5 ambiguous
  references were re-derived from actual loop statements at 226/265/276/406-415/771. Final gate
  **934 gated / zero drift / 18 generic recorded**, 547 locked anchors. Partial automatic fix
  failure retained; manual reanchor/check logs repair3-citations-reanchored.out and
  repair3-citations-final.out. No behavior change in those TUI/envelope comments.
- AST boundary vs e854 ignoring inert docs: repair3-ast-scope.json. Executable product delta is
  harness/core.py, loop.py, types.py; test delta is new fatal_request_loop fixture and child wire
  cases. Other Python changes are citation/doc-only. Diff whitespace check passes.
- New staged test was absent in the base, which caused the first AST utility attempt to fail
  git-show; utility now records new files explicitly. This was evidence-tool setup, not runtime failure.

## Freeze and remaining owner work

This repair is an amend of the one #365 commit on parent fe1. Record exact final SHA with
`git rev-parse HEAD`; source/venv/index/refs freeze after handoff. Root/coordinator independently
review this new boundary, compare own fresh kit, run real OR/TUI cases/activeSDK503/new user turn,
full pytest, remote PR/16CI, future base changes and any authorized merge. No author full/live/
credentials/GH/rebase/push/merge work occurred. Previous candidate green tests/live are not the
repaired-head verdict. Root original batch handoff was not edited.

## Disproved premises

- Typed retry classification alone does not terminate execution: loop continuation through
  hook-mutated messages is independent of the outer retry loop.
- The stop predicate must run before tools and queues, not should_stop_after_turn after tools.
- Failure messages may stay display-editable; functional stop must follow authoritative state.
- A fatal response must not consume pending input or duplicate committed input merely because
  a display hook rebuilt stop_reason. New prompts retain normal configured FIFO/drain behavior.
- Default steering drains one item per attempt, not all; test the real queue policy.
- User approval resumed repair3 explicitly; it did not erase past failures or authorize merge.

## Full-gate test/document repair (2026-10-12 KST)

Coordinator's full run on frozen `df6770fce956c63ae1b96d79de70459cc5847702`
ended with **3 failed / 15951 passed / 25 skipped / 104 warnings / 694.00 s / exit1**.
The failed log remains `.omc/probes/365-final/full-suite-py312.out`. These failures
are legacy structural/fixture assumptions and the guide budget, not a further
fatal-dispatch repair cycle. The prior two failed repairs and user's explicit
2026-10-12 approval above remain recorded. Coordinator restored author-exclusive
ownership only after that full run ended; no full/live/remote action was performed
by author and no other tree was read or changed for this repair.

Changes relative to df6770 are limited to two tests, ADR-0262, the extension guide
and its byte-identical bundle, and this handoff:

- P2's kernel allowlist adds only `session/compaction.py`,
  `session/branch_summarization.py` and `types.py`. Its comment and ADR-0262's
  "Kernel boundary" record the already implemented generic validator forwarding
  and appended terminal predicate. Existing core/loop entries are explained;
  no delegation policy exception or broad directory allowance was added.
- #375's all-Model-field/base_url-only comparison now runs separately for JSON
  and explicit-extension sources. The latter first proves its lower JSON row
  existed, then proves that row and all catalog rows were replaced. Every field
  on the extension Model survives; only base_url differs after the env override.
  Both `find`, `get_all` and actual launch resolution remain covered.
- Extension-authoring's duplicated provider table/late-registration explanation
  is consolidated into "Launch and rebuilds", retaining #367 holds and #365
  ownership, closing fatality, tools/queues, fresh-turn and key/header contracts.
  Custom adapter prose is shortened without removing TLS/cleanup instructions.
  Source/bundle are **48,095 bytes / 3,105 bytes below the unchanged 51,200-byte
  cap**, with 875 lines (also below read's line cap). The single-read promise stands.

Executed with own Python3.12 venv and the existing sanitized runner
`/tmp/aelix-365-author-2026-10-11/run_check.py` (historical log directory retained):

- `python -m pytest -q tests/agents/test_p2_band_boundaries.py
  tests/cli/test_docs_signpost.py tests/cli/test_openrouter_base_url_375.py`:
  **47 passed / 1.68 s**, gate-repair-focus.out.
- Related ownership/wire/child/fatal-loop/#320/#379/compaction/docs/citation set:
  **527 passed / 20.86 s**, gate-repair-related.out. Exact 13 test paths appear
  in the author freeze receipt; all model requests are existing synthetic fixtures.
- `ruff check .`: exit0; canonical `scripts/check_types.py`: **288 files / zero
  errors / inverse3 maintained**; own-interpreter Windows pyright: **288 files /
  zero errors / existing covariance warning1**. Logs gate-repair-ruff.out,
  gate-repair-types.out and gate-repair-windows-types.json.
- `scripts/check_citations.py --check`: **934 gated / no drift / 18 recorded
  generic anchors**; bundle sync updated only extension-authoring. No source
  citation was moved because no runtime source line changed. Diff whitespace clean.

`/tmp/aelix-365-author-2026-10-11/gate-repair-runtime-proof.json` records per-path
SHA256s: **all 289 tracked Python files in packages/src/scripts are byte-identical
to df6770**, which also proves identical runtime AST. Only the two named test
files change among tests. This permits bounded transfer of df6770 runtime evidence;
the test/doc delta still requires coordinator's independent verification and the
new frozen full/CI verdict. No author claim that full passed or that live evidence
alone approves this amended head. Parent remains fe1; rebase, full, live, remote
and merge remain coordinator/root-owned. Record the new exact SHA from the freeze
receipt, and preserve author source/venv/index/refs after the single-commit amend.

## Coordinator closeout before PR — 2026-10-12 KST (18:30UTC)

Canonical frozen full at eb33298cd0ee62f46c5ba0aff6ddc5558d7bff51:
uv run --no-sync pytest -p no:cacheprovider -q — 15955 passed, 25 skipped,
104 warnings, 671.72s, exit0. Wrapper wall676.77s; HEAD and tracked/index clean
before and after. Command and full output: .omc/probes/365-final/full-command.json
and full-suite-py312.out. The earlier df6770 full3fail/15951pass and all prior
fatal-repair failures remain archived; they were not relabeled as passes.

Fresh source-first Codex runtime review at df6770: no blocker, 500 unique tests
(374 related, immutable100, source-derived18, actual Print/RPC8). Its immutable
report is codex-review-365-approved/.omc/probes/365-approved-independent/review.md.
Fresh gate-delta reviewer at eb33298c: APPROVE, primary47 plus independent23
(including21 correctly refused adverse injections); supplemental48 overlaps
primary and is not additional unique coverage. Report and per-file proof:
.omc/probes/365-root/approved-repair3/gate-delta-independent/review.md.
Both author and coordinator separately proved all289 tracked Python blobs under
packages/src/scripts byte-identical between df6770 and eb33298c. Existing P2
formatter changes are identical on base/head and remain separately recorded;
Ruff lint, canonical type288/0 plus inverse3, Windows static288/0 (existing
covariance warning1), citation934/no drift and docs/wheel gates passed.

Actual coordinator uv TUI at df6770 used real OpenRouter gpt-4o-mini normal
controls and three explicit scratch roots. Same-route re-registration,
message_end stop/tool replacement and follow_up/steer continuations refuse:
child auth1/wire0; visibly active main marker auth1/permission0/execute0/wire0.
Active SDK503 retry sends only the first503 wire (auth2/wire1), then closes
auto_retry_end(success=False). Pending FIFO survives; a subsequent separate user
prompt runs normally with preserved input (two local responses). Stale route /
old header three SDK cases, late unregister request/payload hooks and NoExt child
refusals send0; same-owner/local-model/header controls succeed. Fake wire is
loopback only. The older child custom marker was dropped by CLI policy; the
separate visible main marker proves tool nonexecution. Results and directly
inspected virtual-terminal PNGs are .omc/probes/365-root/approved-repair3/.
These are captures of actual PTY output, not OS window screenshots. Current
runtime byte identity binds this evidence to eb33298c; fresh live OAuth login
with owner credentials was not run.

Restricted Claude CLI returned weekly quota exit1, no source verdict, and its
original failure remains preserved. A separate fresh Anthropic Claude through
OpenRouter static lane at df6770 returned APPROVE/no concrete blocker in166.83s:
1250 exact copied Git blobs allowlisted, read/grep/find only,40-call budget,
three scratch roots/noSession/no contextual author/live/handoff evidence.
It ran through the host under review; this is bounded static review, not CLI
quota success or runtime/full approval. Inherited constructor coding imports
remain; the accurate claim is no new upward ModelRegistry import. Actual key
absence was scanned across owned artifacts.

The final commit amendment after this section changes this handoff only.
Record its SHA in .omc/probes/365-final/final-source-freeze-proof.json and prove
all other tracked blobs identical to eb33298c before push. PR is stacked on
fix/289-root-command-routing (fe1c030e14c296493b5d063f6d4431958de3ff6a).
Its own final-head 16 Actions jobs remain required; do not count older-head
checks. Historical2 failed fatal repair cycles and the user's explicit
2026-10-12 approval of repair3 remain above. No merge is authorized.
Next source issue is390, whose frozen literal descriptor change will rebase
onto this final parent before current live/full/PR measurements.
