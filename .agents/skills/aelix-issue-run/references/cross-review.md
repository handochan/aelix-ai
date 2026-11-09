# Cross-review by a different model

Internal passes converge. The implementer, the reviewer and the verifier read the same
diff with the same priors, and they rerun the same evidence. A reviewer built on a
different model reruns the inputs, which is where input-dependent bugs live. In this
repository it found real defects after implement, review, fix and verify had all
passed: #299 and #301 (2026-09-20), the #344 routing leaks (2026-09-30, a second pass
found two more), and three sibling entry points in #362 (2026-10-02).

Run it in round 1, next to the first verify, on hard or risky changes: security,
permissions, routing, sessions, process trees, anything with an interleaving.

## The run shape (Codex CLI)

```bash
mkdir -p /tmp/<n>-xreview
cat > /tmp/<n>-xreview/prompt.txt <<'EOF'
<the review prompt, below>
EOF
cd <lane worktree> && codex exec -C <lane worktree> -s read-only \
  "$(cat /tmp/<n>-xreview/prompt.txt)" < /dev/null > /tmp/<n>-xreview/review.txt 2>&1
echo "rc=$?" >> /tmp/<n>-xreview/review.txt
```

- **Close stdin** (`< /dev/null`). Without it, Codex prints "Reading additional input
  from stdin..." and waits.
- **Start each review as its own background job**, from its own shell call, and poll
  the file with bounded loops. Several runs started with `( ... ) &` from one shell
  died when that shell exited.
- **Run reviews one at a time.** The quota is shared. Three parallel runs with long
  prompts each hit the usage limit in about 10 minutes with no verdict. Keep each
  prompt to the product diff, and check `codex exec "Reply OK"` before you schedule
  more than one.
- **A cut-off run is not a clean bill.** If it ends on a usage limit or a content
  filter, record "ran, cut off, no findings either way", and salvage the interim leads:
  `awk '/^codex$/{f=1;next} /^exec$/{f=0} f' review.txt`, plus any probe files it
  wrote. Send those leads to the verifier to reproduce.

## The prompt

Ask for inputs, not opinions:

> Contract-correctness review of commits `<base>..HEAD`, which implement issues #a #b.
> For each issue: (1) list concrete inputs (arguments, key sequences, error shapes,
> modes, adapters, interleavings) for which the new behaviour differs from what the
> issue and the commit message promise, and show the code path; (2) list other entry
> points in the codebase that reach the same behaviour but were not changed; (3) list
> tests that would still pass if the core condition were reverted. Name the symbol or
> test for every claim. Report only what you verified by reading or running code, and
> say which. Reporting a clean category is better than inventing a finding.

- Name the pi file to compare against when there is one.
- Phrase security reviews as correctness of the stated contract ("inputs where the
  documented rule does not hold"), not as exploit hunting. Two "find bypasses" prompts
  were stopped by the content filter after 100k tokens, before any report.

## When the other model is unavailable

Use a fresh-context review instead: a new agent on the strongest model you have, given
only the issue, the commit and the trade-offs already accepted. Do not give it the
lane's claims or summaries. Ask for concrete inputs backed by probes, and say that
"no defect found" is a valid answer. Keep it read-only on the worktree, with probes in
its own scratch. A Gemini free-tier run once stalled for 40 minutes on quota retries
with no report. The first fresh-context review after that (#321, 2026-09-24) found a
must-fix that four internal passes had missed.

## When to stop

Further passes find less each time. In #362's five passes, pass 2 found sibling-path
bypasses, pass 3 embedder-only surfaces, pass 4 transformed answers, and pass 5 only an
adversarial `str` subclass and three test-gap mutants. Once a pass yields only
adversarial-embedder cases and surviving mutants, fix those as tests, run each mutant
yourself, and finish with one fresh-context verify of the last delta. With the
two-fix-round cap, the cross-review normally runs in round 1 only.
