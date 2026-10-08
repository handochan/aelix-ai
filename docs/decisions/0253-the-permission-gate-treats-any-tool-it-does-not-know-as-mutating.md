# 0253. The permission gate treats any tool it does not know as mutating

Status: Accepted (2026-10-06); amended 2026-10-07 (#389, §11: every approval prompt holds Yes); amended 2026-10-08 (#389 review rounds 2 and 3, §11.1 and §11.2: nothing in aelix's approval prompt is cut, §8's value bound included; an extension's `select` / `confirm` is not covered and is #399); amended 2026-10-08 (#399, §11.3: an extension's `select` / `confirm`, the spawn-consent dialog and the permission gate's `ctx.ui.select` fallback wrap, and hold a title taller than the modal; aelix's own pickers stay as they were)
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
review round 2 `.omc/probes/188-live/fix3/`. #389 (§11): `tests/tui/test_approval_dialog.py`
(the `#389` section) and `tests/builtin/test_permission_unknown_tools_188.py`
(`test_the_generic_fallback_title_carries_the_whole_command_and_path`); probes
`.omc/probes/389-live/impl/`.

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
  through with `path` below the fold. §9 corrects it.) **Superseded by §11.1
  (2026-10-08):** each value was cut at 200 characters and each key at 60, with a marker.
  Once the hold existed the cut only did harm: a body that fit the screen took Yes at
  once, so the rest of a 417-character `content` was approved without ever being drawn.
  Values and keys are now shown whole.
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
  own dialog, and it is left to a follow-up issue. **Superseded by §11 (#389, 2026-10-07):**
  every kind is held now.
- **Tests that could not see a cap.** Every "every argument" row used exactly eight
  arguments, so a renderer that stopped at eight, or one whose count line said nine and
  printed eight, passed. Rows with 9, 20 and 60 arguments, the decisive one last, now
  cover the dialog body and the `ctx.ui.select` title. The 60-character key bound and the
  one-row-per-argument layout that the docs describe are pinned too. (The key bound is
  gone since §11.1.)

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

## 11. Every approval prompt holds Yes (#389, 2026-10-07)

§9 held Yes for `kind="other"` only. aelix's own `bash`, `write` and `edit` prompts still
drew their body in ADR-0159's scrolling window with no footer and took Yes at once. The
defect was reproduced on `402a8013` before any change (real CLI, pty 80x24, scripted mock
model, `.omc/probes/389-live/impl/runs_before/`):

- `bash`: `true arg000 … arg399 ; echo TAIL_MARKER_389 >> out.txt` showed up to `arg130`,
  and 1 ran it once.
- `write`: 121 lines whose last is the decisive one. The preview stopped at 40 lines, and 1
  wrote the file.
- `edit`: the same, and 1 applied the edit.
- `write` of one 265-character line: the body fit the screen, but `_render_diff` cut the
  row at the Panel width with `…`, so the end of the line was never drawn, and 1 wrote it.

Decision:

- **One viewport for every kind.** `_ArgumentViewport` is renamed `_BodyViewport` and is
  the body of every approval prompt. The scrolling body ADR-0159 introduced, which
  followed a cursor, is gone. The hold rule is §9's, unchanged: Yes, "Yes, for this
  session" and any other approving row wait until every line of the current body has
  been drawn by a real paint. No, Esc and Ctrl+C always answer. A key typed before the
  first paint is dropped.
- **Nothing in an approval body is elided.** The write and edit diffs are rendered with
  `max_lines` set to their own line count and no width cap, so a row wider than the
  Panel wraps instead of ending in `…`. `_MAX_BODY_LINES` is removed. (This bullet kept
  §8's bound on one argument value of a tool aelix did not build, 200 characters with a
  marker. §11.1 removed it.)
- **The body cannot steer the terminal.** Every model- or author-chosen string in the
  body and title (the command, the path, the file text, the edit text, the tool name)
  goes through `safe_for_terminal` first, with newlines and tabs kept in multi-line text
  and a space for a control in a one-line row. On `402a8013`, `ESC [8m` in a command
  reached prompt-toolkit's `ANSI` parser and hid the rest of the line. That text was
  drawn, so the hold would have counted it as shown. `\x01 … \x02` was passed to the
  terminal raw. The escape is now dropped and its literal (`[8m`) stays visible.
  Argument values of other tools were already `repr`'d. (§11.1 replaced the dropping:
  each removed character is now drawn as its name. Whitespace is the stated exception,
  §11.2.)
- **PgUp/PgDn move a page.** A press scrolls one screenful less one line, so a page keeps
  one line of context and skips none. #188's five lines took 23 presses for a 121-line
  write at 80x24 (ceil(112 / 5); this said 25 until review round 2 measured it).
  Ctrl+↑/↓ still move one line.
- **The footer keeps "Yes held" on screen.** When the full sentence is wider than the
  modal, a shorter form is used. A three-digit count at 80 columns made the full sentence
  81 cells, and the footer's own `…` cut "Yes held". (Round 1's short form ended in
  "Yes held" and was itself cut at 40 columns; §11.1 moved it to the front.)
- **The `ctx.ui.select` fallback title carries the whole command or path.** It stopped at
  120 characters. Only a host that binds a UI without the approval dialog reaches it. The
  TUI always wires the dialog, and `-p`, json and rpc have no UI.

pi (`27c7b6ff4`) has no permission gate. Its `examples/extensions/permission-gate.ts`
puts the whole command in the title of `ctx.ui.select`, and `ExtensionSelectorComponent`
draws that title as a wrapped `Text` with no height limit. pi's TUI writes into the
terminal's own scrollback, so a long title can be scrolled back to, but pi does not
check that it was. aelix draws its prompts as a height-capped modal (ADR-0159), so it
needs this viewport and the hold.

Costs and residuals:

- **A long write needs paging.** At 80x24 the body shows 14 rows, so a page is 13. The
  121-line write took nine PgDn presses before Yes answered (measured live), and a
  10,000-line file of short lines takes about 770. A 10,000-line body of 80-character
  lines (20,005 rows) renders in 0.34 s, and only a width change renders it again.
  Whether a very large write should be refused outright is left open.
- **The fallback still shows no content.** On the `ctx.ui.select` path, `write` and
  `edit` show only the path. That path has no viewport to hold.
- A resize while the prompt is open still forgets what was drawn (§9), so a resized
  prompt holds Yes until it is scrolled through again.

Evidence (`.omc/probes/389-live/impl/`): `runs_before/` and `runs_after/` (pty screens on
`402a8013` and on the fix), `red_on_402a8013.out` (the new rows against the old code: 39
fail), `sabotage.out` (each fix piece reverted separately, each red).

### 11.1 Review round 2 (2026-10-08)

The verification pass of the first #389 commit failed with five blocking items, and a
Codex cross-review, stopped by a content filter, left two unconfirmed candidates
(`.omc/specs/batch-beta3-r1-results.json`, keys "#389 verify" and "#389 codex"). Each was
reproduced before it was fixed, in-process on `402a8013` and on round 1's `1c8eb79a`, and
live in a pty (`.omc/probes/389-live/r2/`).

- **The edit body is the list the edit tool applies.** The tool does not apply
  `args["edits"]`; it applies `prepare_edit_arguments(args)` (pi's `prepareArguments`),
  which parses `edits` sent as a JSON string and appends a top-level `oldText`/`newText`
  pair. The dialog drew `edits` only, so a call with both shapes showed `-hello`/`+HELLO`,
  and 1 also applied the hidden pair (live on both commits). The body is now built from
  that function's output, each edit headed `@@ edit i of n @@` when there are several; an
  entry the tool refuses is shown as it is, and arguments it cannot read as edits are
  shown raw. The JSON-string shape is rejected by schema validation before the gate
  today; the body follows the tool's function rather than that ordering.
- **The path the write lands on.** The same sweep found the write and edit tools pass the
  path through `expand_path` (one leading `@` dropped, `~` expanded, NFC, unusual spaces
  made ASCII), so `@~/.bashrc` writes the home directory's `.bashrc`. When that changes the
  path, a row under it says `The tool writes to: …`. `bash` runs `command` as sent; a
  user's `shellCommandPrefix` and an extension's spawn hook are the user's and the
  extension's own configuration and are not shown. Arguments of other tools are shown as
  the loop hands them to `execute`.
- **Nothing is cut, for any kind.** §8's 200-character value bound and 60-character key
  bound are removed. The no-exception rule is: aelix's approval prompt (every kind) takes
  no approving answer on content that has not been drawn. A long value now costs PgDn, not consent; the
  `ctx.ui.select` fallback title carries every value whole as well.
- **Removed characters are named, not deleted.** `safe_for_terminal` deleted ESC, CR, VT
  and the rest, so `echo a;ESC[8m echo b` was shown as `echo a;[8m echo b` while the shell
  ran the ESC. Each character it would remove is now drawn as its caret name (`^[`, `^M`,
  `^?`) or, past C0, as `<U+202E>`, in reverse video, so it does not read as the same
  letters typed. A newline and a tab stay as they are in a body and are named in a
  one-row field (a path, a tool name, an option label); a file's lines split at `\n` only,
  so a CR stays on its line. The hold counts the rows after this: 400 CRs are 800 cells.
  The option rows go through the same function, since the #161 redirect label carries the
  model's file name. Whitespace kinds are not shown (a tab is drawn as spaces, a final
  newline as none): §11.2.
- **Model text is never Rich markup.** A `str` Panel title is parsed as markup, so a path
  or tool name with `[/]` raised `MarkupError`; the fallback then drew
  `<rich.console.Group object at 0x…>`, one row, nothing was held, and 1 answered (Codex's
  candidate A, confirmed in-process and live on `402a8013` and `1c8eb79a`). The title is a
  `Text`, and the fallback for a Panel that cannot render is the same rows as plain text.
- **The viewport counts what the painter draws.** Rich wraps by its own cell widths and
  prompt-toolkit paints by wcwidth's (pyte agrees with wcwidth). For a regional indicator
  Rich says 1 cell and wcwidth 2, for a skin-tone modifier 0 and 2, so
  `echo` + 30 x U+1F1E6 + `; echo TAIL` was one row to the viewport, painted 30 cells wider,
  and was cut at the border: counted as drawn, tail never on screen, Yes taken (Codex's
  candidate B, confirmed in-process, in pyte and live). Each body line is now measured by
  `cells_at_most` (the larger of the two counts, a zero-width character counted as one);
  a line that does not fit first gives back the Panel's padding and is then cut into rows
  that each fit, breaking after a space where it can. Terminals set to draw East Asian
  ambiguous characters two cells wide are not modelled, as nowhere else in the TUI.
- **An extension's `select` and `confirm`: reverted, now #399 (§11.2).** pi's
  `permission-gate.ts`, ported verbatim, calls `ctx.ui.select` with the whole command in
  the title; aelix draws the title as one row cut at column 80 with no marker, and Enter
  runs the command (pi's `ExtensionSelectorComponent` wraps it,
  `extension-selector.ts:48`). Round 2 wrapped and held that title; round 3 took the
  change out because it broke ordinary pickers.
- **The text.** The footer's short form starts with "Yes held"
  (`Yes held · 112/126 hidden ↑0 ↓112 · PgUp/PgDn`), so the footer's own `…` reaches it only
  below 9 columns; round 1's form was drawn `… · Ye…` at 40. #188's step took 23 presses,
  not 25.
- **A test for the redirect row.** The #161 redirect row approves a write and was held
  correctly, but a hold that checked only Yes and "Yes, for this session", or one that put
  REDIRECT among the always-answerable rows, left every test green. Rows now press 3, p,
  P and Enter on it while held (no answer), page to the end, and press it again.

Residuals kept as follow-ups: the `ctx.ui.select` fallback shows no write or edit
content; a very large approval needs many PgDn; a resize forgets what was drawn.

### 11.2 Review round 3 (2026-10-08)

The verification of round 2 failed on one blocking regression, and a full Codex
cross-review added four findings (`.omc/specs/batch-beta3-r2-results.json`, keys
"#389 verify r2" and "#389 codex r2"; probes `.omc/probes/389-live/r2verify/`,
`r2cross/` and `r3/`).

- **Scope: aelix's own approval prompt only. An extension's `select` / `confirm` is
  not covered and is issue #399.** (Superseded by §11.3, 2026-10-08: #399 covers
  them now.) Round 2's held `select` withheld its title whenever
  the option, detail and hint rows left it no row, and then held Enter for good, even for
  a one-row title. `/model` (30 models) at 80x22, `/settings` at 80x16 and an 8-option
  `select` at 80x16 could be left only with Esc; on `61f03b67` Enter answers in each.
  Codex also found that `select` and `confirm` took Enter before any paint when the title
  was predicted to fit, that a CR in a title was drawn as a space, and that a mutant
  holding only the first option passed every test. All of that, and the spawn-consent
  dialog's own cuts (a task at 300 characters, a directory at 68, ADR-0199 S4), move to
  #399. `tui/context.py` is as on `61f03b67`, and `aelix_agents/consent.py` differs only
  in three comment lines (two re-derived `approval_dialog.py` citations and the layout
  they describe), so an
  extension's `select` title and `confirm` message are drawn as before: one row per line,
  cut at the screen edge with no marker, Enter or `y` taken at once. A permission-gate
  extension can therefore still have a command run whose tail was never drawn. The
  permission gate's own `ctx.ui.select` fallback keeps sending the whole command or path
  (it does not depend on `select`); when aelix's own `select` draws it, a row wider than
  the screen is cut there, which is #399 too.
- **Whitespace is not shown (decision).** Rich expands a tab into spaces, so a command
  with a tab and the same command with spaces draw the same screen, and a `write` whose
  content ends in a newline draws the same as one without (Codex, measured). Accepted
  and documented rather than fixed: whitespace kinds are not distinguished; every other
  character `safe_for_terminal` removes is drawn by name in reverse video.
- **A cut row could end one cell past the edge.** After a split, the next row can open
  with the carried space; when it fills to the width and a two-cell character follows,
  one split after the space left `width - 1` cells plus two (rows of 80, 1 and 81 cells
  at 80 columns, the verifier's probe). `_cut` now keeps splitting while the next
  character does not fit.
- **A name in an option label is pinned in reverse video.** Dropping SGR 7 from the
  option rows left every test green; a row now checks the style. Review round 4 pinned
  the rest: the write and edit diff rows, the Panel title and the head row
  (`Create/overwrite`, `Edit`, `Tool:`, `The tool writes to:`) draw a name in reverse
  video, and a newline or tab in an option label is named on its one row, never obeyed.
- **Residual: the Panel title and the option labels are cut at the screen edge.** Only
  the body is whole. A path longer than the title row loses its tail there, and the
  #161 redirect label (`Only this project (<cwd>/.aelix/extensions/<name>)`) is cut at
  the edge of the option row in an ordinary project at 80 columns, so `3` approves a
  target whose tail is drawn in neither. The body holds the full path the model chose,
  and the directory in the label comes from aelix, not the model.

### 11.3 An extension's `select` and `confirm` (#399, 2026-10-08)

§11.2 left an extension's `ctx.ui.select` / `ctx.ui.confirm` out. Reproduced on `8f7d98aa`
before any change (real CLI, pty, scripted mock model, pi's `permission-gate.ts` ported,
`.omc/probes/399-live/impl/runs_before/`): the title stopped at `arg009 arg` at 80x24 with
no marker, and Enter ran the 400-word command, end included; `confirm` took `y` the same
way; a CR in a title was drawn as a space and `ESC [8m` as `[8m`.

Decision (2026-10-08). The four points the issue left open, as review round 2 settled
them; round 1's text called its own layout an owner decision, and it was not one (the
owner did not decide round 1's layout, which round 2 reverses):

- **aelix's own pickers are main's, byte for byte, at every size.** `/model`, `/settings`
  (with #404's extension toggles), `/resume`, `/trust`, the theme picker, `/thinking`,
  `/login`, `/logout`, the session-in-use prompt, `/extension new`'s placement question
  and `/agents run`'s project-agent confirm pass `own=True` (the last two reach
  `runtime.ui.select`, through `extensions/ext_ui.py`'s `select_declared`, which passes
  the keyword only to a `select` that declares it), and `own=True` is
  main's code path itself: `_picker_frame` in a plain `FormattedTextControl`, no wrapping
  of the title, no hold, no PgUp/PgDn binding, a key typed before the first paint taken.
  Round 1 wrapped their titles too, so at 80x16 `/trust`'s question took 8 rows and
  `Do not trust` (the only answer that saves a revocation) and its counter fell off the
  modal; at 12x8 `/settings` held Enter after a paint and at 20x8 lost its choices.
  `/trust`'s own wrapping is #380, not this issue. `tests/tui/test_own_pickers_marked.py`
  pins `own=True` at each call site, read from every product source file (review round
  3: it read `tui/shell.py` only and missed the two in `tui/commands.py`).
- **An extension's or the model's title wraps, like pi's.** `select` wraps its title and
  `confirm` its title and message to the window's width (`tui/context.py`'s `_title_rows`,
  built on the approval prompt's `_shown` and `_cut`, reused, not copied). It wraps by
  grapheme cluster, as pi's `Intl.Segmenter` does (`tui/width.py`'s `graphemes`, from
  `wcwidth.iter_graphemes`, now a declared `[tui]` dependency): a cluster is measured
  whole and not split across rows, so a combining mark stays on its letter's row; a
  cluster ending in a zero-width character is not put on the window's last column, where
  prompt-toolkit drops the mark, and a row ending in one gets a space after it, since
  prompt-toolkit also writes the mark into the next cell (review round 4: the accent was
  drawn twice). The shared `_cut` does the same for the approval prompt's body. Known
  limit: a single cluster wider than a whole row (an accented letter in a one-column
  row, a ZWJ family emoji in a six-column row) is cut by code point, `_cut`'s fallback,
  since a row wider than the screen would be clipped at its edge and counted as drawn. Each character
  `safe_for_terminal` would remove is drawn by name in reverse video (`^M`, `^[`, `^I`,
  `<U+202E>`); a newline starts a row. Before, `select` drew a CR as a space and
  `ESC [8m` as `[8m`; `confirm` already showed `^M` and `^[` through prompt-toolkit's
  control-character mapping (not in reverse video) and passed `U+202E` raw.
- **A title that fits is drawn whole and never held; the option rows scroll.** "Fits"
  means the title and at least the FIRST row of the highlighted option fit the modal (the lane's
  reading of "fits the modal": a title that fills it leaves nothing to answer). The
  rows under it shrink first: the hint, the closing rule, the detail, the rule under the
  title, then fewer option rows in the options window (the highlighted one always on
  screen, with `⋮` markers while they fit), the counter last: in the smallest layout
  the highlighted option is drawn without its `(n/N)` counter, and a label of several
  lines is cut from the bottom, as main cuts it (review round 4, Codex round 3: `Deploy?`
  over an eight-line option in an eight-row modal said "too small" and held Enter, where
  main drew it and Enter selected). Option rows are drawn as
  main draws them (#179): one ANSI text split into screen rows, so a label with a
  newline is one row per line (review round 3: it was drawn as `^J`). Round 1 scrolled a title
  that fit to keep every option row on screen: a 5-row question over 8 options in the
  11-row modal of an 80x16 terminal showed 1 of its 5 rows, held Enter and wanted 4 PgDn.
- **Only a title taller than that scrolls and holds.** It scrolls in the approval
  prompt's own `_BodyViewport` (its footer, PgUp/PgDn a page, Ctrl+Up/Down a row) above
  the fullest set of rows under it that still leaves it four rows (else the highlighted
  option alone, a multi-line label cut to the rows that leave the title four and never to
  fewer than its first). Every option (Enter, Space and Ctrl+J in `select`, `y` and `Y` in
  `confirm`) waits until every title row has been drawn by a real paint, and an approving
  key typed before the first paint is dropped. Esc and Ctrl+C (and `n` in `confirm`)
  always answer. A held `confirm` draws `[y/n]` on a row of its own under the footer.
- **The smallest held dialog** is one title row, the footer and the highlighted option's
  first row.
  When even that does not fit, the dialog says `The terminal is too small for this
  question. Enter is held; Esc cancels.` and only Esc and Ctrl+C (and `n` in `confirm`,
  and a row in `cancel_options`) answer, however much was drawn at a larger size before (Codex round
  1: spawn consent in an 80x8 terminal drew no option at all). When the terminal grows
  again the dialog is laid out afresh: a title then drawn whole answers at once, and a
  scrolled one once every row has been drawn.
- **The dialog's own cancel row is not held.** `select` takes `cancel_options` (not on the
  `ExtensionUIContext` protocol): rows the caller names as its own refusal answer while
  the title is held. Spawn consent names its `Cancel` and the permission gate's
  `ctx.ui.select` fallback its `No` and `No, provide reason`; they reach it through
  `extensions/ext_ui.py`'s `select_with_cancel`, which passes the keyword only to a
  `select` that declares it, so every other host is called as before. An extension's own
  `No` row is held like any other: aelix cannot know what an extension's label does.
- **Which calls are an extension's or the model's.** The default: an extension's
  `ctx.ui.select` reaches `AelixTUIContext.select` itself (the bound UI is that object),
  so the method's default is the mark that can reach it, and aelix's own pickers are the
  calls marked. The permission fallback, spawn consent and the descriptor confirm pass
  no `own`. `own` is not on the protocol.
- **The spawn-consent cuts go** (ADR-0199 §(c), amended): the single-task dialog shows the
  whole task (it was cut at `TASK_PREVIEW_CHARS`, 300) and both dialogs the whole
  directory (it was elided to `DIALOG_FIELD_CHARS`, 68, in the middle). Whitespace is still
  collapsed and control characters still deleted.
- **Out of scope**: option, tab and detail rows are not sanitised or wrapped (#179); a
  batch member is still one row of up to 72 characters (`BATCH_TASK_PREVIEW_CHARS`), the
  row `batch_dialog_fits` counts.

Residuals:

- A directory or a task now wraps, so a model can lay text out to look like a row of its
  own at a known width (a flattened `… Permission: plan …` inside the `Directory:` row
  breaking at column 80). It cannot hide the real rows (no escape survives, the real
  `Permission:` row is always drawn after it), and the `Directory:` row cannot be broken
  into rows of the title string. Before, the 68-character elision bounded it to one row.
- The login wizard hands an extension's login provider aelix's own (`own=True`) `select`
  and `confirm` (`login_registry.LoginContext`): main's dialog, unwrapped and never held.
- `confirm` from an extension's descriptor (`DescriptorRenderer`'s confirm) is the
  default: its text is the extension's.
- A resize forgets what was drawn, narrow to wide as well as wide to narrow (as §9); the
  view keeps its place, so the re-wrapped rows above it are reached with PgUp.
- `tabbed` and `multiselect` keep `_picker_frame`'s bottom truncation.

Evidence (`.omc/probes/399-live/impl/`): `runs_before/` and `runs_after/` (pty screens on
`8f7d98aa` and on the fix, pickers at 80x16, 80x22, 80x24 and 120x40), `red_on_8f7d98aa.out`,
`sabotage.out`, `probes/` (in-process paints).
Review round 2 (`.omc/probes/399-live/r2/`): the pickers live in a pty at 12x8, 20x8,
80x16, 80x22, 80x24 and 120x40 against `dfb4ddcc`, cell for cell (`kit/pickers.py`,
`cell-dumps.tar.gz`), and the sabotage rows that stayed green in round 1
(`out/sabotage.txt`).
Review round 3 (`.omc/probes/399-live/r3/`): `/extension new` and the project-agent
confirm live against `8428e16c` at the same six sizes, cell for cell (`out/own2-*.log`),
the in-process probe of every round-2 finding before and after (`out/r3probe.txt`), and
the mutants that stayed green in round 2 (`out/sabotage*.txt`).
Review round 4 (`.omc/probes/399-live/r4/`): the rows red before the fix
(`out/red_on_ab7d.txt`), the mutants of verify round 3 and Codex round 3
(`out/sabotage.txt`), and the live kit with the multi-line option scenario (`kit/`).
