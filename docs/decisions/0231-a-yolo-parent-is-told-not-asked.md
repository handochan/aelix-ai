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

## The disclosure, and why it is on the tool card

One line, composed by `build_disclosure_line`, carried on the `SpawnGrant`:

```
delegating to scout at yolo — 3 tasks, from /home/alice/.aelix/agents/scout.md
```

Four decisions in it worth recording.

**It is on the TOOL CARD, because that is the only durable surface reachable from this
side of the band.** `ctx.ui.notify` is a 3-second status toast (`tui/context.py`);
`set_status` is a height-1 row cleared when the child ends; neither leaves anything in
scrollback. `ExtensionContext` has no method that commits to the transcript, and adding
one is a public-API change this did not need: `ctx.on_partial` already streams the
agent tool's own card, so the disclosure rides it as a persistent header.

**It is on every frame, not emitted once.** `on_partial` REPLACES the card rather than
appending, so a one-off partial would be overwritten by the first progress frame ~200ms
later and the user would have been told for a fifth of a second. It is also emitted
once BEFORE the children start, from `throttle.card()` with an empty snapshot table —
"the human learns a yolo child exists" is only worth something before the child acts.

**The header lives on `PartialThrottle`, not inside `format_card`.** `format_card`'s
`N == 1` output is byte-identical to what P2 shipped and a test pins it; keeping the
function a pure function of the snapshots means that pin does not have to learn about
consent. Every non-YOLO delegation passes an empty header and its card is unchanged.

**It fires only where the child can write.** `disclosure_is_required` is
`parent is YOLO and grants_write_authority(clamped)` — so a `deny` profile, which
clamps to `plan` and never opened a dialog, gets no line either. Announcing every
ordinary delegation would be the scrollback version of the click-through trainer the
dialog was removed for.

The profile name and source path are sanitised with the same `_sanitize_field` the
consent title uses: both come from disk, both land in a Rich-rendered transcript, and a
newline would let a hostile profile invent a second line.

**It is composed at both producers.** `_grant_for`'s pre-filter RETURNS its own
`SpawnGrant` without ever calling `request_spawn_consent_batch`, so a disclosure
composed only inside `consent.py` would be skipped on exactly the model door — the one
where the model chose the profile, the tasks and the directory.

**Headless gets no disclosure.** A headless parent already consents on its own (§(e))
and always did; there is nobody to tell, every `ui.*` raises in print/json/rpc mode, and
the honest record there is the `subagent_*` event stream.

## Chain is included, and the counter-argument is answered

`consent.py` records an explicit argument for keeping the chain dialog: a chain's child
is handed text minted by the previous child, so *"`Cancel` is a real answer here in a
way it is not for a single read-only spawn"*. That is true, and it is still true — but
it argues about whether `Cancel` is meaningful, not about whether a YOLO user asked to
be shown it. The three chain mitigations ADR-0199 §(h) actually relies on — the
substituted-text fence, the disclosure row, and `_may_widen` constraint 6 removing the
widening rung — are all untouched by this. What a YOLO chain loses is a modal whose
other option was "run at the posture you already chose".

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
