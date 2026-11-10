# {{role}} for #{{issue}}: {{one-line scope}}

<!--
Template for an implementation or fix-round brief. Replace every {{...}}.
Delete a section only when it truly does not apply, and say why in its place.
Check every "decided" phrase against a real maintainer message before you send it.
-->

You are {{role, e.g. "fix round 1 of 2"}} for aelix issue #{{issue}}
({{issue title, short}}). Your final message is your return value. Read
`.agents/skills/aelix-issue-run/lane-rules.md` in your worktree first. It is binding.

## Batch facts

- **base:** `{{base sha}}` ({{what it is, e.g. origin/main at #722}})
- **worktree / branch:** `{{worktree}}` on `{{branch}}` at `{{head sha}}`, with its
  own venv (`env -u VIRTUAL_ENV uv run --frozen ...`)
- **scratch:** `{{/tmp/<n>-<role>/}}`. **evidence:** `{{durable dir}}`
- **main checkout (prose files, `.env`):** `{{path}}`
- **others running now (do not touch):** {{sessions, worktrees, scratch dirs}}
- **ADR numbers reserved for you:** {{numbers, or "none: dated notes only"}}
- **credential for live runs:** `{{NAME}}` from `{{file}}`, by name only
- **commit trailer:** `{{exact line}}`

## Read first

- The issue(s): `gh issue view {{n}} --comments`. The acceptance checks there are the
  contract.
- {{previous round's verify report and cross-review, with their reproducer paths, for a fix round}}
- {{the design or contract document, with the section that matters}}
- {{ADRs this touches}}

## Decisions you may quote

Quote only these as the maintainer's, verbatim and dated:
- {{date}}: "{{verbatim}}"

Everything else in this brief is the lead's call; label it so if you write it down.

## The work

<!--
One numbered item per finding or deliverable. Each says: what is wrong (with the
reviewer's id), the reproducer to run on the current head, what "fixed" means, and
the test that must fail on the current head and pass after.
-->

1. **{{short name}}** ({{finding ids}}). {{What is wrong, with the measured shape.}}
   Reproducer: `{{command}}`. Fixed means: {{observable outcome}}. Pin it with a test
   that fails on `{{head sha}}`.
2. ...

Before you change code, do an **entry-point sweep**: list every path that reaches
the same decision (other modes `-p`/json/rpc, adapters, handlers, restored sessions,
settings defaults), each marked guarded or not, with evidence. Fix them all, or say
why not.

## How to work

- Checkpoint after each green step: `git commit -m "wip({{area}}): #{{n}} {{round}} N - <what>"`.
- Tests: new and touched tests on 3.12 with
  `-W error::pytest.PytestUnraisableExceptionWarning`, then once on 3.11 and 3.14.
  {{docker / coarse clock, when the change needs them}}
- Mutants: {{the previous round's mutant list to rerun}}, plus your own on the changed
  lines.
- Live: {{the exact live rows to rerun, scratch only}}.
- Docs: each user-facing sentence names its test or measurement and its condition.
  Grep for sibling sentences that make the same claim.
- Commit message: write the folded message (`{{base}}` to your final tree) to
  `{{scratch}}/commit-msg.txt`.

## Return

Plain text, or the structured fields your caller gives:
- the checkpoints and the files they touch;
- for each work item: what changed, the test that pins it, and how that test fails on
  `{{head sha}}`;
- mutants: killed/total, survivors by name;
- live rows: the command and what you saw;
- test and gate counts, each with its command;
- anything you did not fix or did not run, with the reason.
