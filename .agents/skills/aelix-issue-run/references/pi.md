# Consulting pi

Aelix began as a Python reimplementation of pi (`earendil-works/pi`), and much of it is
ported. Pi is the verified reference implementation, not a target. Full parity stopped
being a goal with ADR-0235, and diverging needs no ADR. Checking pi first is still the
rule on every new surface or defect, because it has often solved the same problem
already. The tests in `tests/pi_parity/` stay as regression tests.

- **Read pi at its current `origin/main`, without checking it out.** A local clone of
  pi may be someone's working tree, and it lags:
  ```bash
  git -C <pi clone> fetch -q origin
  git -C <pi clone> rev-parse --short origin/main          # write this SHA down
  git -C <pi clone> show origin/main:<path>
  ```
  Or take a snapshot for a session:
  `git -C <pi clone> archive <sha> packages | tar -x -C /tmp/pi-<sha>`.
- **Name the pi SHA** in anything you write from it (the ADR, the commit, a brief).
- **Decide divergence knowingly.** Write down what pi does, what aelix does instead,
  and why.
- **Closed pi issues are not decisions.** Pi's bot closes every new contributor's
  issue as `NOT_PLANNED` with a `github-actions` comment. If that is the only comment,
  the issue is unreviewed. Read its body, which may hold a tested design, and check
  the claim against pi's code.
- **Model rows and `thinkingLevelMap`s are not in pi's repository** any more.
  `packages/ai/src/models.generated.ts` is a list of imports of per-provider files
  whose data is not tracked. Read what pi publishes from
  `https://pi.dev/api/models/providers/<pi provider id>?types=chat` (unauthenticated;
  a JSON list of rows, each with `thinkingLevelMap`). The provider id is pi's own.
  The scripts that build the catalog are in pi's `packages/ai/scripts/`; their names
  have changed over time, so list them at `origin/main`.
- **Background or supervisable subagents:** pi's reference is `packages/durable`,
  example `test/examples/22-subagent-background.ts` (spawn, send, stop, status).
  Check whether pi's coding agent uses it before you assume it does.
