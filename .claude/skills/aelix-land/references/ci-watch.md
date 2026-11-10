# Watching CI, hangs and flakes

## Pin the run, count the jobs

`gh run list --limit 1` returns the newest run of any workflow. On `main`, the
Dependency Graph workflow runs on every push and finishes in seconds, while CI is still
going. List runs with their workflow name, and pick the CI run's id:

```bash
gh run list --branch <branch> --limit 6 \
  --json databaseId,headSha,status,conclusion,workflowName \
  -q '.[] | "\(.databaseId) \(.headSha[0:7]) \(.status)/\(.conclusion // "-") [\(.workflowName)]"'
```

Wait on that id with `gh run watch <id> --exit-status --interval 30`, in the
background. Do not poll with a sleep loop, which has returned with the run still
`in_progress`. Do not wrap the watch in `timeout` either: macOS has none, and the
wrapper exited 127 at once while the run went on. `gh run watch` also exits 1 on a
transient API error with the run still going. Whatever the watch says, read the state
afterwards:

```bash
gh run view <id> --json status,conclusion,jobs \
  -q '.status, .conclusion, (.jobs | length), (.jobs[] | "\(.conclusion // .status) \(.name)")'
```

Count the jobs against `ci.yml`. It has 12: `lint + test` on ubuntu-latest and
windows-latest with Python 3.11 to 3.14 (8), `install.ps1 e2e` on two shells (2), and
`install.sh e2e` on ubuntu and macOS (2). A pull request's branch gets both a `push`
and a `pull_request` run.

## Time limits and hangs

Every job is bounded (#429, ADR-0274). `lint + test` has a 40-minute job limit on
Ubuntu and 60 on Windows, and the pytest step has 35 and 55
(`docs/development/ci-hang-diagnostics.md` holds the measured basis). A test whose
setup, call or teardown takes 240 s dumps every thread's stack and ends the process. A
180 s watchdog after pytest finishes catches a hang at interpreter exit.

- **A job killed on its step limit:** read the first timeout traceback. Download the
  log with `gh api repos/<owner>/<repo>/actions/jobs/<job id>/logs`, which works before
  the run completes.
- **Killed on its step limit after a green summary line:** the tests passed, and the
  step ran out of time. Check the step-duration trend issue (#527 at the time of
  writing) before blaming the PR, and rerun the failed jobs with
  `gh run rerun <id> --failed`.
- **When the slowest green step comes within 15% of its limit,** remeasure and move the
  limits, the test that pins them and the docs together (the ci-hang-diagnostics page
  says how).

## Flake triage

1. **Search first.** `gh issue list --state all --search "<test_name> in:title"`, then
   the same in the body. #427 duplicated #406 because nobody searched.
2. **Keep the full failure text** (`--tb=long`), not only the FAILED line.
3. **Before blaming the newest merge, stress both sides of it** under load, and compare
   the failure rates with a significance check. In one case, 2,560 runs on each side
   showed an older race, not the merge that looked guilty.
4. **A failure only one leg shows** is often a platform trap
   (aelix-tests-that-bite, `references/platform-traps.md`), not a flake.
5. **One rerun that passes** proves nothing about the cause. Record both results.
