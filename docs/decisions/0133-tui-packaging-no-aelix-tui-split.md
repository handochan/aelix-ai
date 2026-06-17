# 0133. TUI Packaging — `aelix-tui` Not Split; pi-tui's Role Filled by prompt-toolkit + Rich

Status: Accepted
Date: 2026-06-16
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

ADR-0015 (Monorepo Layout) planned a standalone `packages/aelix-tui/`
package mapped 1:1 to pi's `packages/tui`, to be created at Phase 5. In
reality the interactive TUI shipped as the `aelix_coding_agent/tui/`
subpackage **inside** `aelix-coding-agent` plus a gated `[tui]` optional
extra (ADR-0088 Q1 / ADR-0104). The standalone `aelix-tui` (and likewise
`aelix-rpc`, `aelix-web-ui`) were never created.

The question raised: should `aelix_coding_agent/tui/` be extracted into a
separate `aelix-tui` package "as ADR-0015 planned", for pi parity?

To answer it correctly, pi was inspected directly at the pinned SHA
(`earendil-works/pi@734e08e`) rather than relying on the ADR-0015 plan
text.

## Findings — what pi actually does (verified at the pinned SHA)

`pi-monorepo` uses npm workspaces with 5 packages: `ai`, `agent`,
`coding-agent`, `tui`, `web-ui`. The two load-bearing facts:

**`packages/tui` = `@earendil-works/pi-tui` (v0.74.1)**

- Description: *"Terminal User Interface **library** with differential
  rendering for efficient text-based applications."* README: *"Minimal
  terminal UI **framework** … for flicker-free interactive CLI
  applications."*
- Dependencies: `get-east-asian-width`, `marked` (+ optional `koffi`).
  **Zero application dependencies** — it does not depend on `agent`,
  `coding-agent`, or `ai`.
- **No `bin`.** It is a published, reusable library.
- Built-in components: Text, Input, Editor, Markdown, Loader, SelectList,
  SettingsList, Spacer, Image, Box, Container — a generic, app-agnostic
  terminal-widget toolkit (an Ink/blessed analogue).

**`packages/coding-agent` = `@earendil-works/pi-coding-agent` (v0.74.1)**

- Description: *"Coding agent CLI with read, bash, edit, write tools and
  session management."*
- **Owns the binary**: `bin: { "pi": "dist/cli.js" }`.
- **Depends on `@earendil-works/pi-tui ^0.74.1`** — i.e. the dependency
  direction is `coding-agent → tui`.
- **Contains the chat UI**: `src/modes/interactive/components/`
  (assistant-message, footer, diff, model-selector, countdown-timer,
  login-dialog, …), `src/core/slash-commands.ts`,
  `src/core/footer-data-provider.ts`, `src/cli/session-picker.ts`.

So in pi the `pi` binary, the slash-command surface, and the entire chat
UI all live **inside `coding-agent`**, which *consumes* the generic
`pi-tui` toolkit. `pi-tui` is a library/application boundary
(reusable rendering framework, independently versioned + published), not
a "UI vs logic" split within the coding agent.

### pi → Aelix mapping (corrected)

| pi | Role | Aelix reality |
| --- | --- | --- |
| `packages/tui` (`pi-tui`) | generic terminal UI framework (no `bin`, no app deps) | **prompt-toolkit + Rich** — ADR-0088 chose NOT to port pi-tui's ~9,000 LOC component tree and adopted these third-party libraries for the toolkit role |
| `coding-agent` `modes/interactive/` + `core/slash-commands` | chat UI built on pi-tui; owns the `pi` bin | **`aelix_coding_agent/tui/`** (chat UI built on prompt-toolkit + Rich) + `cli/entry.py` (owns the `aelix` bin) |

`aelix_coding_agent/tui/` corresponds to pi's **coding-agent chat UI**,
which pi keeps inside `coding-agent`. It does **not** correspond to
pi's `packages/tui`. Once ADR-0088 replaced pi-tui's toolkit role with
prompt-toolkit + Rich, the `aelix-tui` package row of ADR-0015 had no
remaining content to hold.

Confirmed by the Aelix import graph: every reverse reference into
`aelix_coding_agent/tui/` is the launcher only (`modes/__init__.py`
`run_tui` + `cli/entry.py` dispatch); the TUI itself imports
`aelix_coding_agent.extensions.*` and `aelix_coding_agent.cli.*`, i.e.
it sits **on top of** coding-agent, exactly as pi's chat UI sits on top
of pi-tui + coding-agent internals.

## Decision

**Do NOT split `aelix_coding_agent/tui/` into a separate `aelix-tui`
package.** The current structure — chat UI inside `aelix-coding-agent`,
generic toolkit role filled by prompt-toolkit + Rich — is already the
pi-faithful arrangement. Extracting the chat UI would *diverge* from pi
(which keeps that code in `coding-agent`) and would perpetuate the
ADR-0015 category error.

This ADR records the decision and **supersedes the stale forward-looking
package rows of ADR-0015**:

- `packages/tui → aelix-tui` — superseded. pi-tui's role is filled by
  prompt-toolkit + Rich (ADR-0088); the chat UI lives in
  `aelix-coding-agent` (= pi). No `aelix-tui` package.
- `Pi --mode rpc → aelix-rpc` — superseded. The RPC surface ships inside
  `aelix_coding_agent/rpc/` (ADR-0056); no standalone `aelix-rpc`
  package.
- `packages/web-ui → aelix-web-ui` — superseded. The Web frontend lives
  in a **separate repository** `aelix-web` (ADR-0097 D3), not an in-repo
  package.

The actually-shipped workspace is the correct one: `aelix-ai`,
`aelix-agent-core`, `aelix-coding-agent` (TUI inside, `[tui]`/`[images]`
extras), `aelix-server` (Aelix-additive, ADR-0097 / ADR-0103).

## Consequences

- **No code movement.** `aelix_coding_agent/tui/`, the `aelix` console
  script, and the `[tui]` / `[images]` extras stay as-is.
- ADR-0015 gains an amendment note pointing here; its mapping table and
  workspace tree are corrected by reference (history preserved per
  `00-conventions.md` §"큰 변경은 기존 문서를 삭제하기보다 새 ADR을 추가").
- ADR-0097's architecture diagram line `apps/aelix-tui (Python — Phase
  5c)` is corrected: the TUI is `aelix_coding_agent/tui/`, not a separate
  `apps/aelix-tui`.
- The "TUI is the most pi-faithful surface" assessment stands; the
  earlier "packaging drift" framing is resolved as *the plan was wrong,
  the implementation is right*.

## Alternatives considered

- **Extract only a generic widget toolkit into `aelix-tui`** (mirror
  pi-tui's library/app boundary by pulling the app-agnostic primitives
  out of `widgets/overlay/themes/render` and leaving the chat UI in
  coding-agent). This is the *only* split that would be pi-faithful, but
  the current `tui/` modules are tightly coupled to coding-agent
  (extensions + CLI bootstrap), so there is no clean app-agnostic subset
  to extract today; pi-tui's reusable-framework role is already served by
  prompt-toolkit + Rich. Rejected as unjustified churn — revisit only if
  an independently-reusable Aelix widget framework becomes a goal.
- **Move the whole `tui/` folder into `aelix-tui`** (the literal original
  request). Rejected: pi keeps this exact code (chat UI) inside
  `coding-agent`, so this maximizes divergence from pi and entrenches the
  ADR-0015 mis-mapping. Only meaningful for non-parity goals (lean
  installs / independent UI versioning), which the `[tui]` extra already
  covers.

## References

- ADR-0015 — Monorepo Layout (uv workspaces). This ADR supersedes its
  `aelix-tui` / `aelix-rpc` / `aelix-web-ui` mapping rows.
- ADR-0088 — Phase 5b TUI Library Decision (prompt-toolkit + Rich +
  Aelix widget layer; pi-tui not ported).
- ADR-0104 — Sprint 6h₁₀a TUI shell; `[tui]` optional extra (Q1).
- ADR-0097 — Multi-Frontend Architecture (separate `aelix-web` repo;
  `aelix-server` Aelix-additive). Its `apps/aelix-tui` diagram line is
  corrected here.
- ADR-0103 — Sprint 6h₉f `aelix-server` (FastAPI WS gateway).
- ADR-0056 — Aelix JSONL RPC (rpc surface inside `aelix-coding-agent`).
- pi @ `734e08edf82ff315bc3d96472a6ebfa69a1d8016`:
  `packages/tui/package.json` (library, no bin),
  `packages/coding-agent/package.json` (`bin.pi`, depends on `pi-tui`),
  `packages/coding-agent/src/modes/interactive/` (chat UI).
