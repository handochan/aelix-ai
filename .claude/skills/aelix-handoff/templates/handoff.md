# Handoff: {{topic}}, {{YYYY-MM-DD}}

<!--
Copy to <notes>/handoff-<topic>-<YYYY-MM-DD>.md and keep it current during the session.
Every number names its command and commit. No placeholder survives the session:
write "not run" and why instead. Quote decisions verbatim with their date; label
your own calls as yours.
-->

## Latest state (read this first)

- {{one line per lane or thread: where it stands right now, with the SHA}}
- **Decisions this session:** {{date}}: "{{verbatim quote}}" ({{what it decided}})
- **If this session dies:** {{where each lane's transcript is, and what to measure
  before continuing: checkpoints, uncommitted state}}

## Baseline

- `origin/main`: `{{sha}}` ({{the last thing merged, #n}})
- Last full suite: {{N passed, M skipped, K failed}} on `{{sha}}`
  (`uv run pytest -p no:cacheprovider -q`, {{duration}}), or "not run on this base"
- Canonical plan: `{{path or URL}}`
- Active locks held by this session: {{issues}}. Others seen: {{issues and owners}}

## Lanes

| lane | issues | worktree / branch | base | scratch | brief | last checkpoint | round |
|---|---|---|---|---|---|---|---|
| {{name}} | #{{n}} | `{{wt}}` / `{{branch}}` | `{{sha}}` | `{{/tmp/...}}` | `{{path}}` | `{{sha}}` | {{impl / r1 verify / fix r1 / ...}} |

Persisted hand-backs and evidence: {{durable paths}}.

## Start here

1. {{the one next action, with the command}}. {{Why this comes first, if order
   matters.}}

## Then

2. {{next}}
3. {{next}}

## Rules that bit (this session)

- {{date, #n}}: {{what went wrong}}. {{What to do instead.}}

## Disproved: do not believe again

- "{{the premise as it was believed}}". {{What was measured instead, with the
  evidence path.}}

## Open for the maintainer

- {{commands only the maintainer can run, decisions waiting, drafts waiting for an OK}}
