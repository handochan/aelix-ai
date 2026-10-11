# #390 literal descriptor text — implementation handoff (2026-10-12)

## 기준점과 소유권

- Issue: <https://github.com/handochan/aelix-ai/issues/390>. Coordinator verified owner approval,
  OPEN, no competing PR and Project 1 In progress before assigning this lane.
- Exclusive worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-390`;
  branch `fix/390-literal-descriptors`; atomic issue lock owned by coordinator.
- Frozen implementation base: `df6770fce956c63ae1b96d79de70459cc5847702`.
  Local `origin/main` observed `162bc5136517436fca365fd73d33ebee2eb32c3c`.
  These are observations, not a claim about current remote main or final parent.
- Own `.venv`: CPython 3.12.13, Rich 14.3.4. No environment sync, credentials, model,
  GitHub operation, other-tree mutation, rebase, merge or sub-agent spawning in this lane.
- Read original AGENTS.md/CLAUDE.md and the memory issue-to-PR skill. The issue's approved
  decision and `/tmp/aelix-390-preflight-2026-10-11.md` informed scope; source was re-read
  at the frozen base. No #365 author/reviewer/root evidence was consulted.

## 구현

`DescriptorRenderer.build_tool_renderable` now uses literal `rich.text.Text` after existing
heading/value callbacks for titles, explicit/key-derived table headers, dict/scalar table
cells and form names/values. Grid items and text bodies already used `Text`. Empty-grid title
was already literal and now reuses the title rather than constructing `Text(Text(...))`.
Absent and empty titles build successfully across all views, with both empty and populated rows.

Only displayed strings change. Projection JSON, row dictionaries, lookup keys, rows/text paths,
namespace/id and tool matching are preserved. Existing #177 control and right-to-left policies
remain at the transcript boundary. Shared builder callers such as render_tool_result and
management modals also display markup literally; this is the intended styling compatibility
change. It does not extend transcript sanitization to every chrome surface.

No production schema, protocol, dependency, render module, session serializer or kernel change.
ADR-0112's historical residual now points to its #390 amendment; extension-authoring guide,
bundled guide and CHANGELOG describe the literal contract.

Production SHA-256 at freeze preparation:

- descriptor builder file: `006ff86c76432ccf5eb27f52d9f181e43a9956b9f3aae1ed43b818dc7ce6f238`
- unchanged transcript renderer: `732e19467e435af02eb6a013f254be7a07f5eca4ebf09b4f69bd0a57665f5359`
- unchanged session entries: `0fcea740833424aecc8dc5d7ae30830fabb70a47018e75c941931e60d566abb7`
- unchanged agent loop: `00d69c102ce7bc23ace00e6c68376eb8638e7a9eb2a18f842d52feb3f4bc8ed8`

## 최신 Pi 참조

Rechecked before changing this surface: Pi main
`97195ec3c4a40665a9008c3658e67b0782f33b18`, commit time `2026-10-11T16:36:26Z`.
[getTextOutput at this SHA](https://github.com/earendil-works/pi/blob/97195ec3c4a40665a9008c3658e67b0782f33b18/packages/coding-agent/src/core/tools/render-utils.ts#L39-L64)
still removes ANSI and sanitizes binary output. Retain Aelix's existing projection/display split;
Pi has no matching descriptor-view contract here. Bounded primary issue searches for
`hyperlink OR OSC OR markup` and `sanitize hyperlink` returned capability/delivery issues;
[10628](https://github.com/earendil-works/pi/issues/10628),
[7930](https://github.com/earendil-works/pi/issues/7930) and
[8665](https://github.com/earendil-works/pi/issues/8665) were read. No matching descriptor fix was
verified by these searches. Legacy Pi citation pins were not advanced.

## 실행 명령과 결과

Artifacts: `/tmp/aelix-390-impl-2026-10-12/` (outside source tree).

1. Before production edits, new regression selection ran against base `df6770f`:
   `.venv/bin/python -m pytest -p no:cacheprovider -q tests/tui/test_descriptor_literal_text_390.py
   -k 'visible_literals and (javascript or custom) or shared_builder_defaults' --tb=short`.
   **40 failed, 12 passed, 203 deselected** (`predecessor-red.log`). All ten vulnerable
   positions failed on synthesized OSC 8 or parsed/malformed markup; empty-grid title, grid
   items and text bodies passed. This is not a claim every previous branch was vulnerable.
2. After the narrow patch, the initial new suite **255 passed** (`new-regressions.log`).
   Final new suite has **256 cases**, including raw identity/tool-match preservation.
3. Focused command:
   `env -u NO_COLOR .venv/bin/python -m pytest -p no:cacheprovider -q
   tests/tui/test_descriptor_literal_text_390.py tests/tui/test_render_terminal_safety_177.py
   tests/tui/test_descriptors.py tests/session/test_message_roundtrip_provenance.py
   tests/test_docs_bundle_sync.py --tb=short`: **508 passed in 1.17s**
   (`focused-env-clean.log`). An earlier same command without removing inherited `NO_COLOR=1`
   gave **30 failed, 478 passed** (`focused.log`): all failures were expected ANSI red suppressed
   by Rich's no-colour setting. Production spans and clean links were intact; no component code
   was changed. The new suite's console explicitly uses `no_color=False`.
4. `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/check_types.py`:
   **288 files analysed, 0 errors; all 3 inverse assertions still error; PASS** (`type-gate.log`).
5. `.venv/bin/python -m pyright --pythonplatform Windows --pythonpath "$PWD/.venv/bin/python"
   --outputjson`: **288 files, 0 errors, 1 existing warning** (`windows-static.json`).
   Warning is SessionStorage TMetadata covariance; this is a Windows-model static check,
   not an actual Windows run.
6. `.venv/bin/python -m ruff check .`: **All checks passed**.
   `.venv/bin/python -m ruff format --check tests/tui/test_descriptor_literal_text_390.py` and
   `--check --range 463-535 packages/aelix-coding-agent/src/aelix_coding_agent/tui/descriptors.py`:
   **already formatted**. Unrelated existing formatting outside the changed builder was retained.
7. After inspecting actual source anchors, `.venv/bin/python scripts/check_citations.py --fix`:
   **0 relocations, 547 anchors / 934 gated citations**; `--check`: **none drifted**.
   No gated citation references the changed descriptor lines; no lock rewrite was needed.
8. `.venv/bin/python scripts/sync_bundled_docs.py`: **9 guides, 1 updated, 0 removed**.
   `git diff --check`: clean.
9. `env -u NO_COLOR -u FORCE_COLOR .venv/bin/python -m pytest -p no:cacheprovider -q
   tests/cli/test_docs_signpost.py --tb=short`: **14 passed, 1 failed**
   (`docs-signpost-inherited.log`). extension-authoring is **52477B > 51200B**; own base was
   already **52090B**, and #390 adds 387B. Coordinator owns the #365 guide-budget repair and
   deliberate merge onto the final parent; no unrelated budget redesign was mixed into #390.
10. After staging the new test and handoff into the `git ls-files` corpus, citation `--check`
    again passed **934 gated / none drifted**; staged and unstaged diff checks were clean.
    `env -u NO_COLOR -u FORCE_COLOR .venv/bin/python -m pytest -p no:cacheprovider -q
    tests/test_citation_drift.py tests/test_docs_bundle_sync.py tests/cli/test_docs_signpost.py
    --tb=short`: **53 passed, 1 inherited guide-cap failure**
    (`staged-docs-citations.log`), same 52477B/51200B result.

The new tests assert terminal bytes and literal content across thirteen displayed positions,
ten safe/unsafe/BiDi-bearing link targets, ordinary/malformed/nested markup, Korean/emoji,
all views, title presence, exact Unicode/markup keys, colliding form labels and raw nested paths.
Both live events and explicit named replay use the descriptor. A real session-entry JSON
roundtrip preserves the named tool result; empty tool_name persists and bypasses lookup to the
default card. Existing #177 byte tests separately keep clean ANSI component OSC 8 links and SGR,
drop unsafe/control/BiDi targets, and preserve span alignment. This is model-free verification.

## 바로 시작할 것 / 이후 순서

1. Coordinator deliberately rebases frozen #390 onto #365's final parent after #365 closeout,
   preserving descriptor/test blob identity and merging the overlapping extension guide.
2. Independent verification must sabotage each critical wrapping branch separately:
   title, table header, dict cell, scalar cell, form label, form value. Confirm the new byte/content
   assertions fail, not an implementation-string assertion. Keep original and restored blobs.
3. Independent adversarial/Codex and Claude reviews, repairs/reverification if needed.
4. Coordinator runs current actual TUI at 80×24 and 120×40, real OpenRouter tool fixture and
   resume, named replay boundary, canonical full tests and final-parent gates. Capture visible
   literal content and zero descriptor OSC 8 plus #177 control leakage. Ordinary empty-name
   resume only proves default-card replay; it does not prove descriptor replay.
5. Update this handoff with final-head evidence, create the single-issue PR and collect its own
   16 CI conclusions. Project/issue completion and merge remain coordinator/user-controlled.

Not executed by this implementation lane: sabotage, independent/Claude review, actual TUI,
real model/provider or resume, full suite, actual Windows, GitHub writes, PR/CI, rebase or merge.
Do not treat these focused checks as issue completion or approval.

## 반증된 것 — 다시 믿지 말 것

- #177's escape cleanup alone forbids all OSC output: false; Rich can synthesize OSC 8 later.
- Component link validation protects descriptor markup: false; the renderables bypass it.
- Every previous descriptor branch parsed markup: false; grid/text bodies and empty-grid title
  were already literal.
- Zero OSC 8 alone proves correctness: false; lost content or fallback can also emit none.
- Sanitizing JSON/keys before projection is harmless: false; exact matching/collisions regress.
- Every persisted result loses its tool name: false; named results survive the real serializer.
  Ordinary loop-produced empty names remain a different replay path and are unchanged.
- A `force_terminal=True` console guarantees ANSI colours: false when `NO_COLOR=1` is inherited.

## Coordinator final-parent and current execution — 2026-10-12 KST

Rebased immutable38e87637 on final365 c3b33bbaddb3a791713319c0766fd699d9d23e35:
858bc06ce40eaf2668103c065c0fae4fea0a6008, no conflicts. Every289 tracked
Python blob under packages/src/scripts and the new literal regression blob are
identical to the source independently reviewed at38e; descriptor blob is
2a8bc349728d50866510d06952935cf2570f0621. Guide48482B, headroom2718B, bundle
identical; inherited guidecap now passes. Proof .omc/probes/390-final/parent-rebase-proof.json.

Fresh source-first Codex verdict: no blocker, unique588 passed (298+234+56).
Six isolated wrapper removals each produce real literal/OSC assertion failures;
baseline490 is overlapping coverage, not additional unique tests. Report and
failed fixture/oracle/environment diagnostics: .omc/probes/390-independent/.
Postrebase canonical focused552 passed4.63s; citations934/no drift.
Author canonical types288/0+inverse3 and Windows static288/0 existingwarning1
remain bound by all-runtime-blob identity. Existing whole-file formatter edits
outside this issue are identical to the predecessor and explicitly not green.

Actual uv TUI on858 with real OpenRouter gpt-4o-mini used all five descriptors,
then completed an ordinary model-only turn.80x24 and120x40, all exits0.
Live OSC8/OSC52/clear-screen=0 versus baseline394 liveOSC8=12. Directly inspected
8 virtual-terminal PNGs: title/header/cell/form markup visible as literal text,
grid title folds literally, malformed body and empty-grid title retain content.
Ordinary runtime persisted empty names and resumed as five default-card
fallbacks (descriptorusedFalse). A separate owned named fixture was made
through the actual entry decoder/encoder preserving call-id/content/metadata;
its real TUI resume used all five descriptors withOSC8=0/exit0. This is distinct
from ordinary replay. UTF8 Korean and emoji and pyte Korean content are present;
the Menlo-only image rasterizer has missing Unicode glyphs. These are actual
PTY captures with a virtual terminal, not OS-window font/layout screenshots.
Three explicit scratchroots and currentcwd isolate session/settings/agent,
all owned artifacts scanned for the actual selected key. Results, commands,
raw bytes and direct observations: .omc/probes/390-root/fixed-v1/.

Canonical frozen full on858, uv run --no-sync pytest -p no:cacheprovider -q:
16211 passed,25 skipped,104 warnings,687.76s,exit0; wrapper692.84s,
ended18:46:18UTC, HEAD/index/tracked source clean before and after.
Logs and metadata: .omc/probes/390-final/full-suite-py312.out/full-command.json.

Original fresh OpenRouter Claude static on38e returned APPROVED/no blocker,
149.70s/exit0. Its read/grep content access was blob-allowlisted, but the
temporary review guard did not constrain find's fallback parent-pattern
enumeration or log those two find patterns. Conditional gap reproduced using
owned fake files only, original report/probe preserved; this is a temporary
review-fixture gap, not a product finding. Strengthened fresh static lane
uses only read/grep, exact Git blobs, lexical/symlink/untracked path refusal,
no contextual author/root/handoff evidence, three roots/noSession.41 guard
selftests passed without reading credentials. Its final result is pending
and will be recorded in own probes/PR; no earlier read/grep source conclusion
is relabeled as failed. Runtime/source/full evidence remains unchanged.

Final amend after this section changes this handoff only; prove all other
tracked blobs match858 before push. PR base fix/365-provider-model-ownership.
Own exact final-head16 CI jobs and strengthened static review are still
required. Do not infer main merge approval or phase completion. Source395
was implemented on858 in its own tree and must rebase after author freeze
onto this final handoff parent, retaining permission/new-test blob identity.
