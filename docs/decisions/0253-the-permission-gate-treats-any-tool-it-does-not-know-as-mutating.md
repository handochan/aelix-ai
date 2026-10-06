# 0253. The permission gate treats any tool it does not know as mutating

Status: Accepted (2026-10-06)
Date: 2026-10-06
Amends: **ADR-0157** ("mutating" is no longer a name set, so PLAN's guarantee and the
AUTO_ACCEPT / AUTO auto-allow now reach every tool), **ADR-0197** §(e) (the child's
headless floor now applies to every tool aelix did not build) and §(i) (the `agent`
tool's pass through the ladder is keyed on what the tool object is). Dated notes in each.
Relates: ADR-0004 (the guardrail floor, unchanged), ADR-0101 (the MCP adapter and its
`<server>__<tool>` names), ADR-0158 (the AUTO classifier), ADR-0235 (pi is the reference,
not a parity mandate), #29 (`AgentTool.mutates` and persisted rules, the next step),
#127 / #128 (attackers already running code in the process, out of scope).
Issue: #188 (P0). Owner direction: 2026-09-19 (`docs/05-post-beta-direction.md` §11, the
issue comment): fix the gate's default, do not split MCP out for this, and never let an
MCP `readOnlyHint` lower the gate. The four points the direction left open were decided by
the main loop on 2026-10-06 (§3).
pi: `b223082bb`. pi has no built-in permission gate. Its example
`examples/extensions/permission-gate.ts` checks `bash` commands only, and its MCP extension
copies the server's annotations onto the tool as hints that "come from the tool's author
and are not verified; permission extensions can use them" (`core/extensions/types.ts:512-513`).
aelix has a gate, and §2.3 explains why it does not use those hints.
Tests: `tests/builtin/test_permission_unknown_tools_188.py` (new),
`tests/docs/test_readme_known_limitations.py` (rewritten for the fix; pins the guardrail
wording),
`tests/builtin/test_sensitive_aelix_dir.py`, `tests/cli/test_extending_aelix_skill.py`
(premises moved), `tests/tui/test_approval_dialog.py` (the held Yes, §9). Probes:
`.omc/probes/188-live/impl/`, for review round 1 `.omc/probes/188-live/fix2/`, and for
review round 2 `.omc/probes/188-live/fix3/`.

## 1. What was measured

The gate (`builtin/permission.py`) decided whether a call was mutating with
`is_mutating = event.tool_name in _MUTATING`, where
`_MUTATING = _BASH_TOOLS | _WRITE_TOOLS` held eight bare names (`bash`, `sh`, `shell`,
`execute_command`, `write`, `edit`, `create_file`, `write_file`). Any other name was
treated as read-only: allowed with no prompt in every posture, and not blocked by PLAN
either, because PLAN tested the same flag.

Tools aelix did not build never have one of those names. The MCP adapter names a tool
`<server>__<tool>` (`mcp/adapter.py`, and `mcp/manager.py` always passes the server name
as the prefix). Extensions and packs choose their own names.

Measured on `aab1f210`, through the real gate with the real built-in tool objects
(`probe_matrix.before.*.txt`):

| tool | default, interactive | plan, interactive or headless | delegated child (`default`) |
| --- | --- | --- | --- |
| built-in `write` | prompts | blocked | blocked |
| MCP `fs__write_file` | **allowed, no prompt** | **allowed** | **allowed** |
| extension `deploy` | **allowed, no prompt** | **allowed** | **allowed** |
| extension `grep` (shadowing the built-in) | **allowed, no prompt** | **allowed** | **allowed** |

The extension `grep` row is measured at the gate. In the CLI, application tools (the
built-ins and MCP tools) win the harness merge over extension tools, so an extension `grep`
reaches the gate in place of the built-in only in an SDK setup that assembles tools
differently.

The real CLI showed the same thing with a local stdio MCP server whose `write_file` really
writes (`live/headless_base.out`, `live/tui_base.out`). In the TUI at `default` it wrote
`out.txt` with no dialog, and with `--permission-mode plan` it wrote it too. In `-p` mode
at `plan` it wrote it as well, in a parent and in a simulated delegated child alike.

## 2. Decision

### 2.1 The gate trusts tool objects that aelix built, not names

A new module, `aelix_coding_agent/tools/provenance.py`, records the identity of each tool
object that aelix's own factories return:

| provenance | objects | how the gate treats it |
| --- | --- | --- |
| `read_only` | `read`, `grep`, `find`, `ls` (their `create_*_tool` factories), the bundled `aelix_status` (`create_status_tool`) | allowed in every posture, PLAN included, with no prompt |
| `bash` | `create_bash_tool` | bash rule keys, the AUTO classifier |
| `write` | `create_write_tool`, `create_edit_tool` | write rule keys, the AUTO_ACCEPT / AUTO auto-allow inside the project |
| `delegation` | `aelix_agents`' `create_agent_tool` (and `with_description`, which carries the mark over to its copy) | not prompted or blocked here; see §3 (b) |
| none | everything else: MCP, extension, pack, SDK-built tools | **mutating** |

The gate looks up the tool the same way the loop does. The `tool_call` hook carries the
turn's `AgentContext`, and the loop runs `tool_map = {t.name: t for t in context.tools}`.
The gate takes the last tool with the call's name from that same list, so the object it
classifies is the object that runs.

It is keyed on object identity (`id()` plus a weak reference that guards against id
reuse), not on names and not on a field of the tool:

- **Not on names.** An extension tool called `read` is not aelix's `read`. A name
  allowlist, the obvious repair, would let it through.
- **Not on a field.** `AgentTool` is a frozen dataclass that anyone can build, and copy
  with `dataclasses.replace`. A `read_only=True` field would be a claim made by the tool's
  own author, which is §2.3's objection in another form. Adding a field that tools
  *declare*, for their own UX, is #29.
- **Fails closed.** An event with no context, a tool missing from the context, or a
  `dataclasses.replace` copy of a built-in has no provenance, so it is mutating.

### 2.2 What "mutating" now gets, posture by posture

For a tool with no provenance:

- **`default`**: the four-option prompt. The dialog shows the tool's name, the number of
  arguments and **every** argument, one row each (`kind="other"`), never a diff built as
  if the tool were aelix's `write`. No argument is left off and the rows are not capped
  by count: each value is cut at 200 characters and each key at 60, with a marker saying
  how much was cut, so one long value is a few lines rather than dozens. That does not
  keep the body on screen. When the body is taller than the room the modal has, a
  footer row under it, outside the scrolled lines, says how many lines are hidden (above
  and below) and that PgUp/PgDn scroll to them, and the approving answers ("Yes", "Yes,
  for this session") are held until every line of the body has been drawn. No, Esc and
  Ctrl+C always answer. See §9. A host with a UI but no approval dialog asks through
  `ctx.ui.select`, and its title carries the same rows on one line. See §8.
- **`plan`**: blocked, on the interactive and the headless path alike, with a reason that
  names the tool and says why. The model otherwise retries a "harmless" MCP search it
  cannot know was refused for being unverifiable.
- **`auto-accept-edits` and `auto`**: never auto-allowed. The in-project write
  short-circuit is for aelix's own `write` / `edit`. A `path` argument says nothing about
  what an MCP or extension tool does with it. The AUTO classifier reads a bash grammar, so
  its verdict means nothing for a tool that is not aelix's `bash`, even one named `shell`.
  So the tool asks, like `bash` does when the classifier says ASK.
- **`yolo`**: allowed with no prompt, like every mutating tool.
- **Headless**: §3 (d).
- **"Yes, for this session"** records `tool:<exact name>`, and `tool:` keys are compared
  by **equality**, never `fnmatch`. A tool's name is chosen by its author, and a grant for
  `srv__*` would otherwise cover every tool on that server. Built-in `write:` / `bash:`
  grants are namespaced by provenance too, so a `write:src/*` grant given to aelix's
  `write` no longer covers an extension's `write_file`. It did before.

### 2.3 An MCP `readOnlyHint` never lowers the gate

This is the owner's direction (2026-09-19). The hint is a claim made by the thing being
gated. pi carries it as an unverified hint for permission extensions to read. aelix's own
gate does not read it. Relaxing per server or per tool is a rule the **user** grants
(#29), not one the server declares.

### 2.4 What did not change

- **`GuardrailExtension`** still matches its hard-deny patterns (`rm -rf`, fork bombs,
  `.env` and `.git` writes) on bare tool names (`bash`, `shell`, `sh`, `execute_command`;
  `write`, `edit`, `create_file`, `write_file`), whatever registered the tool. An extension
  tool with one of those names is checked like aelix's own. An MCP tool is not, because the
  manager always names it `<server>__<tool>`. It runs first and can only block, so a name
  match there over-includes rather than relaxes. The READMEs and the CHANGELOG say exactly
  this (§8: the first wording said it looked only at the built-in tools).
- **Headless parents** (`-p`, `--mode json`, `--mode rpc`) keep
  `headless_default = "allow"` for every mutating tool (SECURITY.md).
- **aelix's own tools**: the before/after matrices are identical for `read`, `bash`,
  `write` and `agent` in every posture (`probe_matrix.*.txt`).

## 3. The four open points (main loop, 2026-10-06)

**(a) The read-only allowlist is keyed by provenance**, which is §2.1. A third-party
`read`, `grep` or `aelix_status`, or an MCP tool adapted with no prefix and called `read`,
is mutating. Each of these is a test row.

**(b) `agent` keeps ADR-0197's treatment, exactly.** That treatment is: the permission
gate returns `None` for the bundled `agent` tool in **every** posture, PLAN and a headless
child included, and never records a session grant for it. Delegation consent is
`aelix_agents/consent.py`'s, keyed on profile + source path + posture and never
persisted. The child's posture is clamped to at most the parent's (a PLAN parent gets
PLAN children). Treating `agent` as mutating would route it through `_rule_key`'s
args-blind `tool:agent`, so one "Yes, for this session" would approve every profile
against every task. The only change is what earns the pass: the `delegation` provenance
of the object `aelix_agents` builds, not the name. A third-party tool called `agent` is
mutating.

**(c) In `auto-accept-edits` / `auto`, a tool with no provenance is never auto-allowed,
whether or not it has a path argument.** The main loop decided the no-path case. The
with-path case is **the implementing lane's choice**, made for §2.2's reason (a `path` key
carries no meaning for a tool aelix did not write) and not an owner decision. The main loop
kept it in review round 1, and the owner may revisit it (§8).

**(d) Headless and the delegated child: a tool with no provenance gets exactly what aelix's
own `bash` gets when nothing auto-allows it.** Measured through the real CLI with the
local MCP server (`live/headless_fix.out`; a child is simulated with
`AELIX_SUBAGENT_DEPTH=1`, which is what flips `headless_default` to `"block"`):

| posture | `-p` / json / rpc parent | delegated child |
| --- | --- | --- |
| `default` | allowed (unchanged) | **blocked** (was allowed) |
| `auto-accept-edits` | allowed (unchanged) | **blocked** (was allowed) |
| `auto` | allowed (unchanged) | **blocked** (was allowed) |
| `plan` | **blocked** (was allowed) | **blocked** (was allowed) |
| `yolo` | allowed (unchanged) | allowed (unchanged) |

The child column is a behaviour change for any MCP or extension tool that is active in a
child, and the CHANGELOG says so. Today a child's tools are narrowed to the built-in names
(`print_channel.narrow_tools`) and `AELIX_MCP_CONFIG` is removed from its environment, so
the rows matter for a tool that still reaches a child, such as one an inherited extension
registers.

## 4. Every site that keys a permission, guardrail or consent decision on a tool name

Swept on `aab1f210`. "Before → after" is for a tool aelix did not build.

| site | before | after |
| --- | --- | --- |
| `builtin/permission.py` `_on_tool_call` PLAN block | name in `_MUTATING`: not blocked | provenance: blocked |
| `builtin/permission.py` read-only short-circuit | name: silently allowed | provenance: falls through to the prompt |
| `builtin/permission.py` AUTO_ACCEPT write short-circuit (`not is_bash` + path) | name: auto-allowed with an in-project path | `provenance == "write"` only: asks |
| `builtin/permission.py` AUTO branch (`is_bash` → classifier; else write auto-allow) | `shell` / `sh` / `execute_command` classified; others auto-allowed with a path | classifier for aelix's `bash` only, auto-allow for aelix's `write` / `edit` only: asks |
| `builtin/permission.py` headless branch | never reached (allowed earlier) | reached: `headless_default` decides |
| `_rule_key` / `_session_wildcard` | names in `_WRITE_TOOLS` shared the `write:` namespace | `tool:<name>`, exact match (`_gate_rule_key`) |
| `_is_session_allowed` | `fnmatch` for every key | `tool:` keys by equality |
| `_request_kind` / `_summary` (dialog body, `ctx.ui.select` title) | `write_file` rendered as a write diff; titled by its path | `other`: the name and every argument, each value bounded (§8); Yes held until the whole body was on screen (§9) |
| `_extension_redirect` (#161) | offered on the name `write` | offered on aelix's `write` object only |
| `builtin/guardrail.py` rules (`applies_to_tools=_BASH_TOOLS` / `_WRITE_TOOLS`) | no rule fires on an MCP tool | unchanged (§2.4) |
| `builtin/policy.py` `PolicyExtension` (SDK allow/deny lists) | name-keyed; can only block | unchanged: a name match can only block |
| `aelix_agents/extension.py` `_on_tool_call` (`tool_name != AGENT_TOOL_NAME`) | spawn consent on the name `agent` | unchanged: the bundled extension registers first, so a later extension's `agent` loses at the harness merge (`setdefault`); the gate now also prompts for a non-aelix `agent` |
| `aelix_agents/print_channel.py` `narrow_tools` (`& ALL_TOOL_NAMES`) | a child gets built-in names only | unchanged (availability, not consent) |
| `cli/entry.py` `_resolve_active_tools` (`--tools`) | availability filter by name | unchanged (availability, not consent) |
| `extensions/api.py` `register_tool` | an extension's tool joins the registry; app tools win a name clash | unchanged; its tools now ask (§2.2) |
| subprocess hooks (`contributes.hooks`) | user-written, can only block | unchanged |
| settings | no persisted permission rules exist | none to change (#29) |
| skills | no tool registration path (skills are prompt files) | none to change |

## 5. Rejected alternatives

- **Name allowlist of read-only built-ins** (`{"read", "grep", "find", "ls"}`): fails
  §3 (a) the moment an extension registers `grep`.
- **Splitting MCP out into an extension**: the owner rejected it as the fix on 2026-09-19.
  The tool would still register by name and pass the same way, and so would every
  extension tool.
- **Trusting `readOnlyHint`**: §2.3.
- **Substring rules** (`__write`, `__exec`): both over- and under-inclusive (#188's own
  scope note).

## 6. Consequences and residuals

- **UX cost.** A read-only MCP tool, a docs search say, now asks every time in `default`.
  "Yes, for this session" covers that one tool until exit. A per-tool rule that persists
  is #29.
- **SDK and extension authors.** A `ToolCallHookEvent` built by hand without `context` is
  an unknown tool to this gate. A built-in tool rebuilt with `dataclasses.replace` loses its
  mark unless re-marked. Both fail closed.
- **In-process code** can import `mark_builtin` and mark its own tools. That attacker can
  replace the gate itself (#127 / #128), so this ADR does not claim otherwise.
- **The guardrail** still goes by bare name (§2.4): it checks an extension tool named
  `shell` or `write_file` and does not check any MCP tool. The README limitation says so.

## 7. Evidence

`.omc/probes/188-live/impl/`: `probe_matrix.{before,after}.{allow,block}.txt` (the gate
matrix), `live/headless_{base,fix}.out` (30 real-CLI `-p` runs each),
`live/tui_{base,fix}.out` plus `live/tui/*/screens.txt` (pty captures: the dialog for
`fs__write_file` and for the `readOnlyHint` tool `fs__peek`, a refusal and an approval, a
session grant, the PLAN block, a silent built-in `read`, and the shift+tab toasts),
`red_on_base.out`, `sabotage.out`.

## 8. Review round 1 (2026-10-07)

An independent verification pass and a Codex cross-review of the first commit found no
remaining silent approval and no PLAN bypass. They found four things the first commit got
wrong, and each is now fixed and pinned (`.omc/probes/188-live/fix2/`):

- **The dialog hid arguments.** `build_approval_view`'s `other` branch printed
  `list(args.items())[:6]` with no sign of the rest. The branch predates #188, but #188 made
  it the consent surface for every MCP and extension tool. The loop keeps argument keys the
  schema does not name, so a model could send six harmless keys first and `path` seventh:
  measured live, the dialog listed `label0` … `label5`, and answering Yes wrote
  `hidden.txt`. It is the #166 class: a prompt asking to change something that does not
  show what. The body now prints every argument (§2.2). A count bound with a "+N more"
  line was the other option. It was not taken, because a "+N more" line would still need a
  way to see the rest before Yes. (This round also claimed that bounding each value keeps
  the body short. It does not keep it on screen, and review round 2 measured Yes going
  through with `path` below the fold. §9 corrects it.)
- **The generic `ctx.ui.select` fallback showed only "Allow <name>?"** for these tools. On
  `aab1f210` it showed the command or path of a tool named `shell` or `write_file`, by
  name. The title now carries every argument, as the dialog does.
- **The guardrail wording** in the README, the CHANGELOG and §2.4 said it looked only at
  the built-in tools. It goes by bare name, so an extension's `shell` is checked (§2.4).
- **Two holes in the tests.** Reverting `_gate_session_wildcard` to the name-based
  wildcard left every test green, although it re-opens the escalation that existed on
  `aab1f210`: "Yes, for this session" for an extension `write_file` stored `write:src/*`,
  and aelix's own `write` to `src/evil.py` then ran with no prompt. An extension `shell`
  granted aelix's `bash` the same way. Handing the dialog an empty argument dict also left
  every test green. Both are now rows.

**Kept as decided by the main loop (2026-10-07). The owner may revisit any of them.**

- "Yes, for this session" approves an unknown tool for **every** argument
  (`tool:<exact name>`). It is not narrowed by path or command, because what an argument
  means to a third-party tool is unknown.
- `-p` / `--mode json` / `--mode rpc` parents run MCP and extension tools outside `plan`
  with no prompt, as they run `bash` (§3 (d)).
- PLAN blocks read-only MCP tools as well, until per-tool rules exist (#29).
- Delegated children now refuse MCP and extension tools in `default`,
  `auto-accept-edits` and `auto` (§3 (d), CHANGELOG).
- In `auto-accept-edits` and `auto`, an unknown tool with a path argument is not
  auto-allowed (§3 (c), the lane's choice).

## 9. Review round 2 (2026-10-07)

The second verification pass found that round 1's dialog still let Yes through for an
argument the user had not seen (`.omc/probes/188-live/verify2/`, reproduced on `1efb91d1` in
`.omc/probes/188-live/fix3/`). Every row was in the body, but the body sits in a
height-capped modal, it scrolled with no sign that it could, and the hint line mentioned
only ↑/↓, digits, Enter and Esc. At 80x24, six filler arguments with 198-character values
(under the 200 bound, so no marker) filled the visible part, and `path` and `content` were
below it. Answering 1 wrote the file. At 120x40, forty short fillers did the same. The
rows were reachable with PageDown, but nothing said so.

- **Yes waits for the whole body.** For `kind="other"` the body is drawn by a control
  that renders its own slice of the lines, at the height the window actually gives it,
  and records every line it handed to the screen. "Yes" and "Yes, for this session" (any
  row that is not No) are not taken until every line of the current body has been
  drawn. Scrolling without a repaint in between does not count, so keys typed ahead of
  the screen cannot approve, and a jump to the end does not count the lines it skipped.
  A width change re-wraps the body, so it forgets what was drawn. No, Esc and Ctrl+C
  always answer.
- **A footer says what is hidden.** The blank row between the body and the options is
  now a footer, a separate one-row window, so it never scrolls: "11 of 25 lines hidden
  (↑0 ↓11) · PgUp/PgDn to scroll · Yes held until all seen". It is blank when the body
  fits, and such a prompt looks as before and takes Yes from its first paint on. A key
  typed before that first paint is held, even for a body that fits, because nothing has
  been on screen yet: it is dropped, not queued, so Yes has to be pressed again once the
  prompt is drawn (`test_yes_typed_before_the_first_paint_is_held`). When the modal has no room
  for the body at all, it says the arguments do not fit and Yes stays held. On a terminal
  narrower than the footer the text is cut at the end, after the counts and the keys.
- **Scope.** The hold applies to `kind="other"`, the body of every tool aelix did not
  build. aelix's own `bash`, `write` and `edit` prompts keep ADR-0159's scrolling body
  unchanged because #188 is about the tools aelix did not build, not because those
  prompts are safe from the same failure. Their command or path starts the body, but a
  command taller than the body is cut the same way, with no footer, and Yes runs it. The
  third verification pass found this, and it was measured again on `9c0c5869` at 80x24
  (`.omc/probes/188-live/fix4/runs_tui/d4-bash-long-80x24-9c0c5869.screens.txt`). The
  command was `true arg000 … arg399 ; echo TAIL_MARKER_188 > out.txt`. The prompt showed rows
  up to `arg130`, nothing said more was hidden, and 1 ran it and wrote `out.txt`.
  `aab1f210` and `1efb91d1` behave the same way
  (`.omc/probes/188-live/verify3/runs_tui/d4o-*`, `d4r-*`). It is the #166 class in aelix's
  own dialog, and it is left to a follow-up issue.
- **Tests that could not see a cap.** Every "every argument" row used exactly eight
  arguments, so a renderer that stopped at eight, or one whose count line said nine and
  printed eight, passed. Rows with 9, 20 and 60 arguments, the decisive one last, now
  cover the dialog body and the `ctx.ui.select` title. The 60-character key bound and the
  one-row-per-argument layout that the docs describe are pinned too.

Measured live in a pty (real CLI, local stdio MCP server, scripted mock model,
`.omc/probes/188-live/fix3/tui_fix3.summary.txt`): with `path` off screen, 1, y, 2, s and
Enter at 80x24, and 1, 2 and Enter at 120x40, leave the dialog open and the server
uncalled. PgDn until the footer drops "Yes held", then 1 (or 2), writes the file, in
`default` and in `auto`. Esc while held denies, and `auto-accept-edits` holds the same way.
In a body that fits, a 1 pressed once the prompt is on screen answers at once.

## 10. Review round 3 (2026-10-07)

The third verification pass (`.omc/probes/188-live/verify3/evidence.txt`) found CI's
type gate red on round 2's commit. `_ArgumentViewport.footer_control` built its one line
as `[("bold" if text else "", text)]`, which pyright infers as `list[tuple[str, str]]`,
not prompt-toolkit's `StyleAndTextTuples`. `list` is invariant, so the `get_line`
argument did not type-check. Round 2's message said the gate passed because its gate
script piped the output to `tail -2`, which kept only the two `OK` lines printed after
the `FAIL` block. The line is now annotated `StyleAndTextTuples`, and the gate is quoted
in full from the command CI runs
(`.omc/probes/188-live/fix4/types.before.out`, `types.after.out`). The same pass also
noted that §9 overstated two things. It said a prompt that fits answers "exactly as
before", but a key typed before the first paint is now held. It also gave a reason for
leaving `bash`, `write` and `edit` out of the hold that does not hold for a long command.
§9 is corrected above. Nothing else in the dialog's behaviour changed.
