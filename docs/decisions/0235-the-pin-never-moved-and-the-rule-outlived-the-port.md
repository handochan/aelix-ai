# 0235. The pin never moved, and the rule outlived the port

Status: Accepted (2026-09-03).
Date: 2026-09-03
Relates: ADR-0003 (`## Pi Parity Binding Rule` — retired here),
ADR-0034 (the SHA pin, whose job changes rather than ends),
ADR-0086 (A 단계 closure — the port this rule was written to protect),
`docs/04-reference-projects.md` (Pi as Primary Reference).

Pi stops being a target and goes back to being a reference. Divergence from Pi
is no longer a bug requiring an ADR to justify it. Nothing is deleted: the
parity tests stay, the pin stays resolvable, and every ADR that cites it stays
correct.

This ADR records a change that already happened. It does not propose one.

## The measurement that says so

ADR-0034 pinned Pi at `earendil-works/pi@734e08e` on **2026-05-17** and defined
an update policy whose first clause is "each new sprint that imports new Pi
features MAY move the pin forward." The index header went further and recorded
that pin advances were **permitted starting B 단계**.

The Pin history table has **one row**. It is still the initial pin.

| what | count |
| --- | --- |
| ADRs in `docs/decisions/` | 231 |
| …mentioning parity | 176 |
| …citing SHA `734e08e` | 158 |
| …referencing ADR-0034 | 58 |
| test files citing the SHA | 60 |
| tests under `tests/pi_parity/` | 417, in 29 files |
| times the pin advanced | 0 |

The most recent ADR to cite the SHA is ADR-0227, dated **2026-08-16** — three
months after the pin was set, still quoting Pi as of 2026-05-17. So the project
did not drift away from Pi quietly and forget to say so; it kept citing a fixed
Pi with precision while Pi itself moved on unread. The rule stayed in force in
the documents and stopped being in force in the work.

## What the rule was for, and why it is finished

ADR-0003 closed with a binding rule: *"Aelix의 Pi와 다른 모든 동작은 명시적
ADR로 정당화되어야 합니다. ADR 없는 divergence는 버그로 간주됩니다."*

That rule was the right one while the port was in flight. It existed to stop a
half-finished reimplementation from accumulating accidental differences that
nobody had decided on — where "we did not get to it yet" and "we chose
otherwise" are indistinguishable from the outside.

ADR-0086 recorded A 단계 (the Phase 4 strict Pi-parity superset) **CLOSED
end-to-end** at this pin. Past that point the distinction the rule protects
stops paying for itself: there is no longer a large body of unported behaviour
for an accidental difference to hide in, and the cost of the rule — an ADR for
every deliberate deviation, measured against a snapshot that is now months old —
is charged against work that is no longer a port.

**Retired:** `ADR-0003 § Pi Parity Binding Rule`. Divergence from Pi needs no
ADR. The rest of ADR-0003 stands: Pi remains the primary implementation
reference, and permission/guardrail remain built-in rather than optional.

## What replaces it

Pi is a *verified reference implementation*, read at its current state:

- When a problem surfaces or a new surface is designed, **check Pi's latest code
  and issues first.** Much of Aelix is a port of it, so Pi has often already met
  the problem, and its answer is evidence worth having.
- Read it as evidence, not as a specification. Aelix may differ, and does not
  owe a document for differing.
- Design decisions still get ADRs. That is ordinary ADR practice
  (`docs/00-conventions.md`) and is unchanged — what ends is the obligation to
  write one *because Pi does it differently*.

## What this does not touch

**The 417 tests under `tests/pi_parity/` stay, and stay green.** They are not a
parity mandate; they are regression protection for behaviour that was ported and
verified. Deleting them would discard checked behaviour and buy nothing. They
assert what Aelix does, and the fact that Pi once agreed is now history rather
than an obligation. New ones are no longer required.

**ADR-0034 is not superseded — its job changes.** The pin stops being "the
version Aelix must match" and becomes the anchor that the 158 ADRs and 60 test
files citing `734e08e` resolve against. Those citations remain exact and must
keep resolving, so the pin must not be moved or removed to tidy anything up.
Its update policy is now moot rather than violated: there is nothing left to
advance the pin *for*, because current work does not measure itself against a
fixed Pi at all.

**No existing ADR is rewritten.** The 176 ADRs that discuss parity recorded
decisions that were correct when made, under a rule that was in force at the
time. Rewriting them backwards would destroy the record of how Aelix was built
and is a much larger error than leaving a retired rule visible in ADR-0003 with
a pointer here.

## Not done here

`docs/04-reference-projects.md` still states the goal as implementing pi agent
"거의 완벽에 가깝게" in Python. That sentence describes the port, which is done,
and is updated alongside this ADR — but the reference-perspective lists in that
document are unreviewed and may name comparisons that no longer earn their
place.

Nothing was measured about how far Pi's head has moved since `734e08e`. The
distance is not load-bearing for this decision — the rule is retired because the
port closed, not because upstream drifted — but anyone consulting Pi should
expect three-plus months of divergence from every SHA this repository quotes.
