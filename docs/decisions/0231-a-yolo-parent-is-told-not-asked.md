# 0231. A YOLO parent is told, not asked

Status: Accepted (2026-08-19).
Date: 2026-08-19
Relates: ADR-0197 §(i) (the spawn-consent gate this amends), ADR-0157 (the ceiling
no dialog may exceed), ADR-0199 §(c)/§(h)/§(k) (one dialog per tool call; the chain
mitigations), ADR-0203 (no new environment names).
GitHub: #196. **Reverses finding OC-1 for the YOLO parent, and only for it.**

A user running `--permission-mode yolo` was still shown a confirmation modal on every
delegation. They reported it as a bug. It was not: it was a decision, taken twice, with
the loose-parent case explicitly in view both times.

## What was already decided, and why it is being changed anyway

**OC-1**, filed under *"OPTION-C decisions — owner-settled, do not re-litigate"*:

> shift+tab was consent to the PARENT's tool calls, with a tool card on screen — not
> consent to an unattended child process the model just chose.

The **2026-07-27 amendment** then removed most spawn dialogs and named this cell as the
one it was keeping. ADR-0197's `inherit` row: *"When the parent is loose the clamp
itself is write-capable and the dialog fires through `grants_write_authority`. **This is
the owner's headline case.**"* And, in the paragraph headed *what it does not touch*:
*"a clamp that already grants write authority — finding OC-1's shift+tab case — ...
**still prompts, at every scope, on every delegation.**"*

What changed is not that reasoning. It is a **measurement of the widget that reasoning
produced**. Under a YOLO parent `build_options` renders exactly two rows:

```
parent=yolo  approval=inherit -> ['Run with the inherited posture (yolo)', 'Cancel']
parent=yolo  approval=ask     -> ['Run with the inherited posture (yolo)', 'Cancel']
parent=yolo  approval=auto    -> ['Run with the inherited posture (auto-accept-edits)', 'Cancel']
```

No widening rung — constraint 4 forbids offering a rung below the clamp. Two options,
no substantive second answer. That is the same arity and the same shape as the dialog
the 2026-07-27 amendment abolished — ADR-0197 describes it as *"one where `build_options`
could only render ['Run read-only (plan)', 'Cancel']"* — and it fails ADR-0197's own
test verbatim:

> A dialog whose only substantive answer is "yes" is not a gate; it is practice at
> dismissing gates.

The aggravating fact is that YOLO users meet it and nothing else. `permission_mode.py`
promises *"skip the permission PROMPT for all mutating tools ... YOLO bypasses the
prompt, NOT the floor"*, and the spawn dialog was the last prompt that was not skipped.
A user who chose the no-prompt posture was being trained to dismiss the only modal the
posture ever showed them.

## Exactly what moved

Measured over the full 80-cell live-UI lattice (5 parents x 4 `approval_mode` x 4
scopes): 48 cells prompted before, **12 changed, and every one of them is
`parent=yolo`**.

| parent | approval | clamp | prompted | now | disclosed |
|---|---|---|---|---|---|
| yolo | inherit | `yolo` | yes | **no** | yes |
| yolo | ask | `yolo` | yes | **no** | yes |
| yolo | auto | `auto-accept-edits` | yes | **no** | yes |
| yolo | deny | `plan` | no | no | no |

`auto-accept-edits` and `auto` — the other two write-capable parents — are untouched,
and that asymmetry is the point. `auto-accept-edits` says auto-accept *edits*; `auto`
says route bash through the classifier. Neither says "do not ask me about anything".
Only `yolo` does.

The predicate keeps its single-source-of-truth property: `consent_is_required` gained
`parent`, and all three call sites — both doors in `consent.py` and
`AgentsExtension._grant_for`'s pre-filter — already had it in scope.

## The cost, stated rather than hidden

**Measured: an `auto-accept-edits` or `auto` child is refused a write to `.aelix/`, and
a `yolo` child is not.** So a delegated YOLO child can author the parent's next agent
profile or project extension — the write-to-exec escalation that OC-5 closed for the
widening path. This dialog was the only moment in the product where a human learned
*before the fact* that a `yolo`-posture child was about to exist.

Residual R7's accounting does not cover the gap it leaves. R7 lists four things that
bound a spawn nobody confirmed, and its first and largest is the clamp — but its own
first sentence is *"a `plan` child is refused **every** mutating tool at branch (b)"*.
Under YOLO the clamp bounds nothing. Reusing R7 here would be applying an argument
outside its domain.

So the modal is replaced rather than deleted.

## The disclosure, and the route that did not work

One line, composed by `build_disclosure_line`, carried on the `SpawnGrant`:

```
delegating to scout at yolo — 3 tasks, from /home/alice/.aelix/agents/scout.md
```

**The first implementation of this was inert, and the round that caught it is the
reason this section exists.** It rode `ctx.on_partial` — the agent tool's own
card — on the strength of ADR-0199 §(l) surface 2, which calls that card *"the
permanent record: it stays in the transcript after the turn ends"*. That
sentence is false. Measured with a positive control: feeding the
`tool_execution_update` event `ctx.on_partial` produces through the real
`EventRenderer` yields **zero** commits, while `tool_execution_start` through the
same harness yields one. `tui/render.py` explicitly no-ops that event and nothing
else in the product consumes it. ADR-0199 now carries the correction inline.

The tests did not catch it because they all asserted the `SpawnGrant.disclosure`
FIELD and the throttle's header string — never that a user would see anything.
The sabotage round said the same thing independently: *"never emit the pre-run
frame"* left 1548 tests green.

**What it uses instead: a statusline row**, written through the same
`SubagentProgressBridge` that already owns every other row here, so it inherits
the live-`ui` read (finding OC-7), the headless no-op and the `suppress` that
keeps a delegation from dying of a statusline write.

Five decisions in it worth recording.

**Pre-spawn is the only part that was missing.** The finished tool card's own
footer already names the posture — `_usage_line` renders
`[agent scout · … · yolo · …]` — so a post-hoc transcript line would have
duplicated a shipped surface. What the removed modal uniquely provided was the
moment BEFORE the child runs, and that is what the row supplies: written before
the group opens, cleared in the same `finally` that closes it.

**Nothing in the extension band can reach the transcript before a child runs.**
`ExtensionUIContext` has no commit verb; `ctx.append_entry` writes a session
entry that renders only on replay; `_commit_update_notice` is a `run_tui`
closure. Adding one would be a product-core contract change, i.e. a band
decision. This change did not need one.

**It fires only where the child can write.** `disclosure_is_required` is
`parent is YOLO and grants_write_authority(clamped)` — so a `deny` profile,
which clamps to `plan` and never opened a dialog, gets no row either.
Announcing every ordinary delegation would be the statusline version of the
click-through trainer the dialog was removed for.

**The withdraw is guarded on the same condition as the write.** `_clear_row`
pops its own bookkeeping and then calls `ui.set_status(key, None)`
*unconditionally*, so an unguarded withdraw writes a status key for every
delegation that never had a disclosure. Measured — it broke
`test_statusline_row_set_and_cleared`'s "one key per child", which is a shipped
test doing exactly its job.

**Both doors emit it.** `_grant_for`'s pre-filter RETURNS its own `SpawnGrant`
without ever calling `request_spawn_consent_batch`, and `/agents run` lives in
`runtime` and never sees `AgentsExtension` at all. So `SubagentHost` gained an
`on_disclosure` hook, the extension wires it to the same bridge writer, and the
`/agents run` path announces and withdraws around its own `_run`. Deleting the
model door's emit left 1548 tests green before the end-to-end tests below
existed.

The profile name and source path are sanitised with the same `_sanitize_field`
the consent title uses: both come from disk, both land in a rendered row, and a
newline would let a hostile profile invent a second one.

**Headless gets no row.** A headless parent already consents on its own (§(e))
and always did; there is nobody to tell, every `ui.*` raises in print/json/rpc
mode, and the honest record there is the `subagent_*` event stream.

## Chain is included, and the counter-argument is answered

`consent.py` records an explicit argument for keeping the chain dialog: a chain's child
is handed text minted by the previous child, so *"`Cancel` is a real answer here in a
way it is not for a single read-only spawn"*. That is true, and it is still true — but
it argues about whether `Cancel` is meaningful, not about whether a YOLO user asked to
be shown it. The three chain mitigations ADR-0199 §(h) actually relies on — the
substituted-text fence, the disclosure row, and `_may_widen` constraint 6 removing the
widening rung — are all untouched by this. What a YOLO chain loses is a modal whose
other option was "run at the posture you already chose".

## Two sentences that stayed false for a commit, and one caveat

Found by review after the change had landed, not by the tests:

* **`tool.py`'s `agent` description — the text the MODEL reads** — still said
  *"A delegated agent is READ-ONLY unless the user explicitly approves more at a
  prompt."* Under YOLO nobody is prompted and the child is not read-only. That
  is the worst audience for a false sentence: a model that believes its children
  cannot write will not reason about what they might do. It now names the
  parent's own posture as the other route to authority.
* **The CHANGELOG's "This SUPERSEDES the entry below" framing** contradicted this
  repo's own convention. The entry it superseded sits in
  `## [0.1.0-beta.1] — not yet released` and the repo has no tags; `92b3f35` set
  the rule for exactly this case — an unreleased line is CORRECTED in place, and
  `[Unreleased]` carries the note for anyone tracking main.

And one caveat on the safety fact this decision turns on. The `.aelix/` write
refusal that binds `auto-accept-edits` and `auto` children — and that a `yolo`
child escapes — is confirmed by executing the real Guardrail + Permission
ladder. But it rests entirely on `headless_default="block"` at branch (d).
There is no `.aelix`-specific denial and no guardrail rule covering it, so the
protection evaporates the moment that one field is not set. That is worth
knowing before anyone cites it as a boundary.

## What did not change

The clamp; the guardrail hard floor inside the child; `MAX_DELEGATIONS_PER_PROMPT` and
`MAX_LIVE_CHILDREN`; the statusline row and the `subagent_*` events; the headless silent
downgrade; the ceiling that no dialog ever hands out `auto` or `yolo`; the project-scope
widening ban; fail-closed allow-list decoding of a dialog answer; and the fact that
Ctrl+C and shift+tab stay live during a running turn.

## Tests

`_dialog_expected` — the helper that states the policy independently of the
implementation — gained `parent` and checks it first, so the exhaustive 60-cell matrix
test compares against the RULE rather than a paraphrase of the code. Its follow-up
assertion used to read *"the skipped cells are exactly the harmless ones"*; that is no
longer true, so it FORKS rather than weakening: a skipped write-capable cell must
produce a disclosure naming the profile and the posture, and a skipped read-only cell
must produce none.

Nine tests used YOLO as a way to FORCE a dialog — for the fail-closed allow-list, the
option count, the absence of a memo, the ceiling above `auto-accept-edits`. Each was
repointed at `auto-accept-edits` or `auto`, which still prompt, so every invariant they
protect is asserted over the same shape. `_PROMPTING_PARENTS` exists so that narrowing
is visible as a decision rather than as a shrinking parametrisation.

The complement is pinned directly: over the whole lattice, a write-capable child is
either asked about or disclosed, and never both.

And the row itself is now driven END TO END rather than asserted as a field — a real
delegation through the real extension, reading the statusline the product writes, with
`_FakeUI` extended to record every value a key ever held. The last point is the one that
matters: a row that is written and then correctly cleared ends at `None`, which an
end-state assertion cannot tell apart from a row that was never written. `MODE_META[YOLO]`,
SECURITY.md's Scope entry, the `agent` tool's model-facing description and the beta.1
CHANGELOG entry all have gates of their own, each pinned on the CLAIM rather than the
phrasing — reverting the mode description had left 1788 tests green, and restoring the
model-facing sentence left 1554.

Three sabotage rounds. Round one: 16/19 RED, and the three that were green were the
disclosure on the MODEL door, the pre-spawn emission, and the mode description — i.e.
everything about whether a user would actually be told. Round two, against the
rewritten mechanism: 10/13 RED, the green ones being `/agents run` (no coverage at
all), the headless guard's inner layer, and a SECURITY.md assertion that a stray
occurrence of the word `yolo` elsewhere in the section was satisfying. Round three:
every one of those RED. One green in round three was a weak sabotage of mine rather
than a hole — it edited a heading and left the claim standing; removing the paragraph
is RED.
