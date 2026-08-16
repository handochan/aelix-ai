# 0222. `aelix status` reports the directory without becoming a session

Status: Accepted (2026-08-15).
Date: 2026-08-15
Relates: ADR-0218 (#101 — shipped `RuntimeSnapshot` and the `aelix_status` tool, and left
this verb explicitly unbuilt: *"`aelix status` CLI 어댑터 미구현"*), ADR-0203 (the
permission ladder and the `.env` default-deny), ADR-0216 (project trust on reload follows
pi), ADR-0220 (#120 — the prompt must not under-claim; §2 below is that rule applied to a
JSON payload).
GitHub: #101 (parent epic #53).
Pi: `earendil-works/pi@main` — `packages/coding-agent/src/cli/args.ts` and
`src/cli/auth-command.ts` fetched and read on 2026-08-15. Claims below come from that
fetch, not from recall.

## 1. What was decided

`aelix status` is a subcommand that answers **"what would a launch in this directory
be?"** — version, agent directory, the project-trust decision, and which extensions load
here — and that answers it *without constructing a harness, resolving a model, or opening
a session*.

It runs the two things it reports on, through the same calls `entry.py` makes:
`resolve_project_trusted(...)` and `discover_and_load_extensions(...)`. It does not
re-derive either.

## 2. The decision that cost the most thought: three fields are ABSENT, not empty

`RuntimeSnapshot` carries eight fields. Three of them have no session-free answer:

| Field | Why not |
| --- | --- |
| `mode` | `resolve_app_mode(parsed, stdin_is_tty)` describes a **launch**. There is none here. `snapshot.py` already refuses to re-derive this field for exactly this reason — a second opinion can disagree with the one the process acted on. |
| `active_tools` | A tool registry exists only inside a harness. |
| `all_tools` | Same. Reconstructing it from the built-in list plus the loaded extensions would reimplement `entry.py`'s `--tools` / profile policy, badly. |

The obvious shortcut was to emit them as `""` and `[]`. That is **#120 in miniature**: an
empty tool list does not read as *unknown*, it reads as *none*, and a script that branches
on `len(all_tools) == 0` branches wrongly. So the keys are omitted from the JSON entirely
and a `session_only` list names them. "We did not look" and "we looked and found nothing"
are different answers and the payload can express both — the same distinction is made one
level down, where `--no-extensions` yields `discovered_extensions: null` while a directory
with no extensions yields `[]`.

This is why the command emits its own envelope rather than a `RuntimeSnapshot`. ADR-0218's
seam — *"one runtime-introspection source with a possible `aelix status` CLI adapter over
it, not two implementations that drift"* — is honoured where it is about **logic**: scope
classification, the emitted-value bound, extension summarisation and the fail-closed trust
rule are all imported from `aelix_status`. Only the shape differs, and it differs because
the two callers genuinely know different things.

## 3. It loads extensions, and that is deliberate

The command could have listed files without importing them. It imports, because *an
extension that is present but fails to load is the main thing someone runs this command to
find*, and only the loader knows that.

The safety argument is that this executes exactly the code the next `aelix` in this
directory would execute, under exactly the same gate:

- trust is resolved first, non-interactively, and discovery is then called with
  `no_project_local=not project_trusted`;
- `--no-extensions` skips discovery entirely, and that is a promise about *execution*, not
  about display — pinned by a test whose planted extension writes a file at import time.

**Non-interactive means denied, not asked.** An undecided directory with `.aelix/`
resources resolves to `False` with no prompt. A status command that popped a trust dialog
would be unusable in a script; one that silently trusted would report a permission the
next real launch will not grant. This follows the `--print` / `--mode json` rule already in
`project_trust.py`.

There is a subtlety here that the first revision of the code got half-wrong, and it is
recorded because the comment was wrong before the code was. The resolver denies on
`if not has_ui or prompt is None`. Passing **no** `prompt=` is what actually denies;
`has_ui=False` is inert on its own. Measured: flipping `has_ui` to `True` left **all 13**
behavioural tests green. Both are now passed anyway — belt and braces — and the pair is
pinned structurally (`test_the_trust_call_is_non_interactive_in_both_ways`), because no
behavioural test can see a difference that makes none today.

## 4. Provenance: AELIX-ORIGINAL, with a named nearest neighbour

pi's command list, read from `packages/coding-agent/src/cli/args.ts:254-260` on
2026-08-15, is seven verbs: `install`, `remove`, `uninstall`, `update`, `list`, `config`,
`auth`. There is no `status` and no `docs`; this is aelix's **second** original subcommand,
after ADR-0218's `aelix docs`.

Claiming "pi has nothing like it" would overstate it. The nearest neighbour is
`pi auth check [--provider …] [--json]` (`src/cli/auth-command.ts:43`): a
non-interactive, JSON-capable readiness report. It is the precedent for the *shape* — a
verb that reports rather than acts, with a machine-readable flag. It is not the same
question: `auth check` asks whether a **provider** is ready; `aelix status` asks what this
**directory** is. pi has no notion of project trust to report, which is most of why the
verb has no pi analogue.

## 5. One list moved, and the move was forced by measurement

`BUILTIN_ALWAYS_ON_NAMES` — the three extensions prepended on every run — lived in
`tui/extension_manager.py`. The first revision of this command imported it from there.

Measured: importing `aelix_coding_agent.tui.extension_manager` pulls **166** modules
including `prompt_toolkit` and `rich`. The `[tui]` extra is **optional**, so on a headless
install that is not a startup cost, it is an `ImportError` — `aelix status` would have died
listing a directory. The constant is data about the runtime, not about the viewer that
renders it, so it now lives at `extensions/always_on.py` and the viewer imports it. The
regression is pinned by a **subprocess** test: by the time pytest reaches this file both
packages are already in `sys.modules`, so the in-process version of that check passes over
a command that cannot run.

## 6. What is NOT closed

- **No live-model run.** Nothing here involves a model — the command starts no session by
  construction — so there is nothing for one to verify. Stated so the absence is not read
  as an omission.
- **The report does not include skills, MCP servers, prompt templates, agents or themes.**
  All five are gated by the same trust decision and all five would be answerable the same
  way. They are out of scope because #101's snapshot did not carry them either, and adding
  fields to this envelope that `RuntimeSnapshot` lacks would start the drift ADR-0218's
  seam exists to prevent.
- **`extension_errors` is bounded but not deduplicated.** Two packs failing the same way
  print two lines.
- **Exit code is 0 even when an extension failed to load.** The command succeeded in
  reporting; the failure is content, not status. A caller that wants to branch reads the
  JSON. This is a choice, and a reasonable person could want `1` instead.
