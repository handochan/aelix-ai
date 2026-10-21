# 0269. The release candidate is installed and run on every OS before anything is published

Status: Accepted (2026-10-20; owner decision on #382). Revised in review round 2
(2026-10-21) before merge: the request is computed per version, the interpreter is
the one a user without Python gets, and the gate holds against `continue-on-error`
and status functions. Amended 2026-10-22 (#535): a manual run of `release.yml` is
the dry run before the tag - `build` and `candidate-smoke` only - and the smoke
script's `::error::` line survives a Windows leg's cp1252 stdout (the amendment
at the end).
Date: 2026-10-20
Issue: #382 (beta.3 exit condition 2: the candidate artifact itself is verified)
Relates: ADR-0147 (release engineering; `release.yml`), ADR-0192 (the
`github-release` job and `install.sh`), ADR-0240 (`publish` runs for every tag;
dated note there), ADR-0241 and ADR-0265 (the interpreter request the
installers make, which the smoke makes too).
Pi reference: `earendil-works/pi@fdce48eb4`, `.github/workflows/build-binaries.yml`
(`build` -> `smoke-test-binaries` -> `stage-github-release` -> `publish-npm`).

## Context

Until #382, `release.yml` ran `build` and then `publish` (PyPI, behind the `pypi`
environment approval) and `github-release` side by side, each needing only
`build`. No job installed what those two shipped. Measured at `7faf4b9a` by
reading the job graph (`.omc/probes/382-live/impl/probe_job_graph.py`):

```
publish: needs=['build'] ... installs-built-dist=False
github-release: needs=['build'] ... installs-built-dist=False
DEFECT: ['publish', 'github-release'] can run before the candidate is installed and run on ubuntu, macos and windows
```

Three things made that worse than it looks:

- ci.yml's `install-sh-e2e` and `install-ps1-e2e` install the **newest
  published** Release. They meet a candidate only after users can.
- RELEASING.md checked the installer after `publish`. A defect found there
  costs the version number: PyPI never takes a file name twice (ADR-0240).
- `install.sh` and `install.ps1` with no `AELIX_VERSION` install the newest
  Release tag, betas included. With `github-release` needing only `build`, a
  build nobody had installed became every installer user's default the moment
  that job ran.

Owner decision (2026-10-20, #382): **both `publish` and `github-release` wait
for a candidate install smoke, in pi's order**, accepting that the Release
appears later by the smoke's duration and that a flaky smoke holds it back
until a re-run.

## Decision

**1. The job order.** `build` -> `candidate-smoke` (ubuntu-latest, macos-latest,
windows-latest; `fail-fast: false`; `timeout-minutes: 20`) -> `github-release`
(`needs: [build, candidate-smoke]`) -> `publish` (`needs: [build,
candidate-smoke, github-release]`, then the `pypi` approval). `publish` names
the smoke as well as the Release, so dropping one edge cannot ungate the
irreversible upload. A tag deletion still skips everything through `build`'s
`if: !github.event.deleted`.

**2. What the smoke installs: this run's artifact, the way the installers
install a Release.** The `dist` artifact `build` uploaded is downloaded into
`$RUNNER_TEMP`, and installed with install.sh Step 5's own command, with the
artifact where install.sh puts the Release's verified wheels:

```bash
uv tool install --force --find-links "$DIST" --python "$PYTHON_REQUEST" "aelix[tui]==$VERSION"
```

`--find-links` adds the four first-party wheels and PyPI stays the index for
third-party dependencies (never `--no-index`); `==$VERSION` keeps a newer PyPI
`aelix` from outranking them; `[tui]` is the installers' default extra.
`VERSION` is a new `build` output written by the pin-assert step only after
every published version and pin matched the tag. `PYTHON_REQUEST` is another:
`build`'s `request` step applies the installers' own rule to that version
(`scripts/release_candidate_smoke.py request`, install.sh's
`python_request_for` / install.ps1's `Get-PythonRequest`) - `>=3.11,<3.15` for
0.1.0b3 and later, `>=3.11,<3.14` for anything older or not provably newer (a
dev, post or local version). The step runs from `$RUNNER_TEMP`, not the
checkout. The candidate never comes from PyPI or a published Release; its
third-party dependencies do come from PyPI, as a user's do.
`tests/test_release_candidate_smoke.py` holds the command equal to install.sh's,
with those three substitutions, and the script's rule equal to install.sh's by
running it (`sh`, POSIX legs) and to install.ps1's by reading it, over 0.1.0b2,
0.1.0b3, 0.1.0, 0.2.0.dev1, 0.1.0.post1, 1.0.0 and six harder shapes.

**2a. Which interpreter each leg tests: the one a user without Python gets.**
With uv's defaults (`python-preference = managed`, downloads automatic) a
Python already on PATH inside the request wins over a download; a user with no
Python in range gets uv's download of the newest CPython inside it. Measured
with uv 0.13.4 on macOS (`.omc/probes/382-live/r2/`), for `>=3.11,<3.15`: no
Python in range on PATH (macOS's own `/usr/bin/python3` is 3.9) -> a download of
3.14.8; the same box with 3.11 and 3.12 on PATH
-> 3.12.13; that box with `UV_PYTHON_PREFERENCE=only-managed` and an empty
`UV_PYTHON_INSTALL_DIR` -> 3.14.8 again (3.13.16 for `>=3.11,<3.14`). The
install step sets those two (and `UV_PYTHON_DOWNLOADS=automatic`), so every leg
(ubuntu-latest, macos-latest, windows-latest) runs the candidate on the newest
CPython build uv downloads for that OS inside the request, never the runner
image's Python. `installed` fails unless the tool environment is based
on that download. The other interpreters stay covered where tests run: ci.yml's
legs, 3.11 to 3.14 from source on ubuntu and windows.

**3. What it checks** (`scripts/release_candidate_smoke.py`, stdlib only, run
under the tool environment's own interpreter):

- `installed` — the request it was installed under is the installers' for the
  tag's version; `SHA256SUMS` is in the form `sha256sum` writes
  (`<sha256>  <name>`, lower-case hex, two spaces, LF, each name once). That is
  stricter than install.sh Step 4: the smoke refuses every form install.sh
  refuses (a `*` binary-mode marker - `sha256sum -b` and `shasum -b` write
  `<sha256> *<name>` - one space, CRLF), and also upper-case hex and a
  duplicate line, which install.sh accepts (measured over 24 forms in review
  round 2, `.omc/probes/382-live/r2verify/v_manifest_forms.out.txt`);
  install.ps1 reads a looser form still. It holds for every file in the
  artifact, lists every wheel and sdist, and names the tag's version where
  install.sh Step 5 reads the version it pins; the interpreter is inside the request and is based on uv's
  download (2a); the artifact is exactly the four published wheels at the tag's
  version; **every file each wheel's RECORD lists - its `.dist-info` files
  included - is installed with that hash**; every top-level package and
  `aelix_coding_agent.tui` import. The byte check is what sees a same-version
  file from an index standing in for the candidate, which the `==` pin cannot:
  measured, PyPI's `0.1.0b2` against a `0.1.0b2` built from `7faf4b9a` differs
  ONLY in `METADATA` and `WHEEL`, and the check names both.
- `turn` — `aelix --version` prints the tag's version; then, from scratch HOME
  and `AELIX_*` directories, with a fake key resolved through a `models.json`
  provider pointing at a stub OpenAI chat-completions server on 127.0.0.1,
  `aelix --offline --provider candidate-smoke --model smoke-echo -p ...` exits 0
  and prints the stub's reply, and the stub saw exactly one request of any
  method: a chat-completions POST for that model, carrying the fake key. The
  session the turn writes is reported, not required. Of the runner's environment
  the candidate gets only the boot keys (PATH, SystemRoot, SystemDrive, windir,
  COMSPEC, PATHEXT, TEMP, TMP, TMPDIR), each read with `os.environ.get` -
  Windows' `os.environ` iterates them upper-case, and a child without
  `SystemRoot` dies importing asyncio (#209).

Each check exits 1 after one `::error::` line naming it. A failure outside the
checks shows differently: uv's own message and a non-zero exit for the uv
bootstrap or the install, a Python traceback for an exception no check
anticipated (RELEASING.md, *Cutting a beta* step 7; step 6 before #535).

**Nothing turns a red leg green.** `needs` alone is not the gate: no job or
step in `release.yml` carries `continue-on-error`, no job `if:` calls a status
function (`always()`, `!cancelled()`, ...; an `if:` without one keeps GitHub's
implicit `success()`), the only smoke steps with an `if:` are the per-OS uv
bootstrap pair, and the matrix has no `include` / `exclude`. Breaking any of
them passed every row at `a5790c33` (measured in review rounds 1 and 2); each
is a test now.

**A `-p` turn rather than `--list-models`.** Measured on a `0.1.0b2` build with
`PYTHONPROFILEIMPORTTIME` (`.omc/probes/382-live/impl/import_witness.py`):
`--list-models` imports 164 first-party modules and never `openai`, builds no
client and streams nothing; the `-p` turn imports 175, `openai` and `httpx`
among them, and goes through key resolution, the OpenAI client, SSE, the agent
loop and a session write. That is the path #278 just moved (`openai>=2.7.2,<3`).
The `aelix` meta-package's own module and the TUI are not reached by `-p`;
`installed` imports them.

**4. uv for the smoke comes the way the installers' Step 2 gets it** — Astral's
installer, newest uv (`curl … | sh` on POSIX, `irm … | iex` in `pwsh` on
Windows) — not `astral-sh/setup-uv`. That action verifies nothing past uv
0.11.18 without a `checksum:` per runner OS (#393, `tests/test_ci_uv_pin.py`),
and no macOS sha256 for the pinned 0.12.23 is on record. The job holds no
write permission and publishes nothing, so its uv decides what the smoke sees,
never what ships; ci.yml's installer e2e jobs bootstrap uv the same way. Its
checkout keeps no credentials (`persist-credentials: false`): it runs code from
PyPI and from the candidate.

**5. One divergence from pi, knowingly.** pi stages a **draft** Release and makes
it public only after npm. A draft's assets are not downloadable by the
installers, and the window below runs `install.sh` / `install.ps1` against
this very Release, so aelix's Release is public (a pre-release for a
hyphenated tag) as soon as the smoke passed. The consequence is in "What you
give up".

**6. The window before `publish`.** Because `publish` needs `github-release`,
its `pypi` approval is asked for only once the Release exists. RELEASING.md's
*Cutting a beta* now has the maintainer run `install.sh` and `install.ps1` with
`AELIX_VERSION=<tag>` in that window and approve only then — the installers
take the first-party wheels from the Release, so this works before the PyPI
upload — and says what to do when the smoke or the window check fails: nothing
was published, so re-run a flake or fix and move the tag.

## What you give up

- **The Release appears later**, by the smoke's duration (one uv bootstrap, one
  install, two short aelix runs per OS, in parallel).
- **A flaky leg holds back the Release** until "Re-run failed jobs".
- **`github-release` now gates `publish`.** Before, they were independent (run
  34310880829: `publish` died in `twine check` while the Release was made). Now
  a failed Release job (e.g. a stale SBOM) leaves PyPI untouched until a retag.
- **A defect found in the window is already the installers' default** for
  anyone who does not set `AELIX_VERSION` — the price of decision 5.
  RELEASING.md says to reject the deployment and delete the Release
  (`gh release delete <tag> --yes` keeps the tag) and retag.
- **The smoke's uv is not pinned.** A uv regression on release day blocks the
  release — the same regression a new user's installer would hit that day.
- **One interpreter per leg.** Each leg runs the newest CPython uv downloads
  inside the request; a user whose machine already has an older Python in range
  runs on that one, which the smoke does not try (ci.yml's source-tree legs
  run 3.11 to 3.14, but not from the built wheels). Every leg now downloads a
  CPython on each run (25.4 MiB for macOS arm64 3.14.8).
- **The workflow itself has not run on a Windows runner before merge.** The
  turn's logic and its child environment do run there before then:
  `tests/test_release_candidate_smoke.py` runs on ci.yml's four gating
  windows-latest legs on every branch push and PR, and its turn rows run the
  script in a separate `python -I` process against this checkout's own `aelix`
  console script - the first Windows run of `scratch_env` and of the exit
  status. What first runs on Windows at the tag push is the rest: the
  PowerShell uv bootstrap, the Git Bash steps, `uv tool install` of the built
  wheels, and the scripts under the tool environment's interpreter.
  (Since #535 that first run can be a dry run before the tag: the amendment
  at the end.)

## Not decided here

- **Upgrade verification.** Exit condition 2 says "install *and upgrade*". The
  smoke installs onto a clean runner; installing the candidate over the
  previous release (as re-running the installer does) and resuming a session
  that release wrote is not covered.
- **An automatic installer job after `github-release`** that runs the real
  `install.sh` / `install.ps1` against the new Release and that `publish` also
  needs. Not asked for; the window in decision 6 is manual.
- **`timeout-minutes` on `build`, `github-release` and `publish`.** Only the new
  job is bounded; a bound on `publish` must not cut an upload in half
  (ADR-0240's half-release), so it is its own decision.
- **Pinning the smoke's uv** to ci.yml's 0.12.23 via setup-uv, which needs the
  macOS sha256 on record.

## Verification

Probes and outputs are in `.omc/probes/382-live/impl/`. `release.yml` itself
could not run before a tag push (until the #535 dry run: the amendment at the end);
instead `run_release_jobs_locally.py` executes the
`run:` blocks of `build` and `candidate-smoke` verbatim with bash on macOS
(runner variables, `GITHUB_ENV` / `GITHUB_PATH` / `GITHUB_OUTPUT` simulated, the
artifact copied in; the uv bootstrap not run — the local uv 0.11.19 stands in).

- A `0.1.0b3` candidate (a scratch copy of `7faf4b9a` with the five versions
  bumped, tag `v0.1.0-beta.3`): `build outputs: {'version': '0.1.0b3'}`;
  `SHA256SUMS: 8 files verified`; Python 3.14.5 inside the request; 9 / 44 / 70
  / 198 files byte-identical across the four wheels; seven imports; `aelix
  --version: 0.1.0b3`; the `-p` turn green — `ALL STEPS GREEN`.
- A `0.1.0b2` candidate (`7faf4b9a` as is), while PyPI holds a different
  `0.1.0b2`: uv 0.11.19 took the local wheels (all bytes match); the same check
  against an install of PyPI's `0.1.0b2` fails on `METADATA` and `WHEEL`.
- A wheel that drops a module (`openai_completions.py` added to `aelix-ai`'s
  wheel `exclude`, a defect only a built artifact shows): red at
  `import aelix_agents ... No module named 'aelix_ai.providers.openai_completions'`.
- A newer shadow `aelix 9.9.9` beside the candidate: the job's pinned command
  installs `0.1.0b3` and passes; without the pin uv takes `9.9.9` and
  `installed` fails.

Review round 2 (`.omc/probes/382-live/r2/`, 2026-10-21). Each fix was
reproduced on `a5790c33` first, and every named mutant goes red after it:

- **The Windows child environment.** A real `os._Environ` built with the nt
  key encoding: before, `scratch_env` handed the candidate `[COMSPEC, PATH,
  PATHEXT, TEMP, TMP]` and no `SYSTEMROOT` / `SYSTEMDRIVE` / `WINDIR`; after,
  all eight boot keys, and still not the planted `OPENAI_API_KEY`.
- **The gate's survivors.** On `a5790c33` all 36 rows passed under
  `continue-on-error` on the job, on the windows leg only and on the turn step;
  `if: always()` on github-release; `if: !cancelled()` and `if: always() &&
  needs.build.result == 'success'` on publish; `if: runner.os != 'Windows'` on
  the turn; `return 0` after the `::error::` line; a byte check that skips
  `.dist-info`; a matrix `exclude` of windows; `if: always()` on a smoke step;
  `continue-on-error` on build's pin step. Each is red now, and so are eleven
  new mutants of the round-2 fixes (scratch_env iterating, three manifest
  loosenings, three request-rule errors, a hard-coded request, `installed`
  accepting any request, no `only-managed`, the managed check off).
- **The request.** A fixed `>=3.11,<3.15` differed from install.sh's
  `python_request_for` for 0.1.0b2, 0.2.0.dev1 and 0.1.0.post1; the computed
  one equals it for all six versions named.
- **The manifest.** A `*` binary-mode, a single-space and a CRLF `SHA256SUMS`
  were accepted by the smoke and refused by install.sh's own Step 4 lines; all
  three are refused now, and the build job's form still passes.
- **End to end** (`run_release_jobs_r2.py`: the `run:` blocks verbatim with
  bash on macOS, checkout / setup-uv / the artifact copy simulated, and the
  Astral uv bootstrap now run for real - uv 0.13.4 - with 3.11 and 3.12 on
  PATH): `v0.1.0-beta.3` - build
  outputs `{'version': '0.1.0b3', 'python_request': '>=3.11,<3.15'}`, uv
  downloaded CPython 3.14.8, `installed` and `turn` green; `v0.1.0-beta.2` -
  `'>=3.11,<3.14'`, CPython 3.13.16, green. With `only-managed` deleted from
  the step, uv took the PATH's 3.12.13 and `installed` failed: "the tool
  environment's interpreter is based on .../cpython-3.12.13-macos-aarch64-none,
  not on a CPython uv downloaded into .../candidate-python".

## Amendment 2026-10-22 (#535): the dry run before the tag

**Context.** `release.yml` started on a release-tag push and nothing else. The
last run of the workflow was the `v0.1.0-beta.2` tag's (34315063972, 2026-09-09),
before #382 added `candidate-smoke`; so the job had not run on any runner, and its
first run on macOS and Windows would be the `v0.1.0-beta.3` tag itself. A red leg
there publishes nothing (the gate holds), but beta.3 waits on a fix and a moved
tag while the update-check feed already names it. Measured on the tree this
amends (`.omc/probes/b4-d-live/535/repro_dispatch_before.txt`): the triggers are
`['push']`, so a manual run does not start the workflow at all; and adding a bare
`workflow_dispatch:` alone - the plausible first fix - gives a manual run all four
jobs, `github-release` (`contents: write`) and `publish` (`id-token: write`, the
`pypi` environment) among them. The second gap was in the script: each smoke step
runs it with `-I`, which ignores PYTHONIOENCODING, and a redirected stdout on
Windows is cp1252 with `errors='strict'`. Measured with exactly that stdout
(`repro_encoding_before.txt`): a failure quoting U+2192 exited 1 with no
`::error::` line and a `UnicodeEncodeError` traceback in its place.

**Decision.**

- **A manual run is the dry run.** `release.yml` gains `workflow_dispatch` with
  one input, `tag` (required, a string): the tag about to be pushed. `build`
  reads its tag from one place, `TAG: ${{ inputs.tag || github.ref_name }}` (the
  input on a manual run, the pushed ref otherwise), so the tag gate and the
  version/pin assert refuse on a dry run exactly what they would refuse on the
  push; the input reaches the scripts through the environment, never expanded
  into a `run:`. The gate's step says when a run is a dry run and prints the SHA
  it builds. `candidate-smoke` is unchanged and runs on every OS.
- **A manual run never publishes.** `github-release` and `publish` each carry
  `if: ${{ github.event_name == 'push' }}`, and each condition guards its own job.
  Without `publish`'s, a manual run still never reaches PyPI, because `publish`
  needs `github-release`, which does not start. Without `github-release`'s, a
  manual run creates the Release (`contents: write`), and only `publish`'s own
  condition keeps it off PyPI. So neither condition is redundant, and the test
  requires each job to carry its own. (Review round 2 corrected this bullet: it
  said removing either one kept a manual run off the Releases page.) The
  condition calls no status function, so decision 1's implicit `success()` still
  applies. The `pypi` environment's approval is a third layer, not the gate.
- **The smoke's output lines stay whole.** `scripts/release_candidate_smoke.py`'s
  `main()` first sets stdout's and stderr's error handler to `backslashreplace`;
  a character the code page lacks prints as `\u2192`. The encoding is left as it
  is: the runner's own decoding of a Windows step's output is not measured, and
  an escape is ASCII under any of them.
- RELEASING.md's *Cutting a beta* runs the dry run as step 5, after the commit and
  before the tag (`gh workflow run release.yml --ref main -f tag=<tag>`); the
  later steps move down by one. The GA flow points at it before its tag.

`tests/test_release_candidate_smoke.py` holds it: the triggers are exactly the tag
push and the manual run with its one input; GitHub's job selection read from the
file (a job starts when its needs started and its `if:` holds, each condition's
meaning written into the test by hand) gives the tag push all four jobs, the tag
deletion none and a manual run `build` and `candidate-smoke`; each job holding a
write permission or an environment carries the push-only condition itself, and
no job a manual run starts holds one or reads a secret; `build`'s tag comes from
that one expression and no build `run:` expands one; the gate's own script, run
by bash, passes a manual run with a release tag and refuses `main` and the dot
form; and the script prints one `::error::` line from a cp1252 stdout, in-process
and as a separate `python -I` process.

**pi, and where this diverges.** pi's `build-binaries.yml` (fdce48eb4, lines 3-16)
also takes `workflow_dispatch`, with a required `tag` and an optional
`source_ref` "for release recovery", but a manual run there runs the whole graph -
`stage-github-release`, `publish-npm`, `publish-github-release`. pi has no dry run
without publishing; it is a contrast, not the precedent. aelix's manual run is
only the rehearsal. Recovery stays the single force-update of the tag
(RELEASING.md, *Retagging after a pre-upload failure*): a manual run on the tag's
ref after a failed tag run builds and smokes again, and publishes nothing.

**What you give up.**

- **The dry run is a separate run.** It builds the commit; the tag run builds it
  again. Both build the same tree with the same pinned uv, but the smoke's uv and
  the third-party dependencies it resolves from PyPI are those of the day each
  runs, so a green dry run does not make the tag run's smoke a formality.
- **The dry run of a version already on PyPI** (`main` before its bump, as now at
  `0.1.0b2`) may meet the index's same-version file. uv 0.11.19 took the build's
  (above); a uv that takes the index's turns `installed` red on the bytes. That
  is the byte check working, not a defect in the build.
- **Anyone who may start a run can start a dry run** - the same people who may
  push a tag. It holds no write permission and no secret, so it costs runner
  minutes and nothing else.

**Not done here.** Running it. The issue asks for one run before the beta.3 tag,
with all three legs green and the run id recorded here and in
`docs/05-post-beta-direction.md`'s beta.3 exit condition 2; no run has happened
yet.

**Verification** (`.omc/probes/b4-d-live/535/`). Before: the triggers probe and the
encoding probe above. After: a manual run on `main` and on the tag ref each give
`['build', 'candidate-smoke']` and no job that can publish
(`repro_dispatch_after.txt`); the encoding probe gives exit 1 and one
`::error::` line with `\u2192` (`repro_encoding_after.txt`). On a throwaway
worktree (`sabotage.out.txt`) each fix piece reverted - the whole workflow, the
trigger alone, `github-release`'s condition alone, `publish`'s alone, the tag
source, the script's handler - and eleven wrong forms - `!= 'pull_request'`, a
condition that lets an input through, a `publish` switch input, push on `main`
too, the smoke skipped on a manual run, the input expanded into the gate's
script, the dry-run line dropped, stderr only, `errors='replace'`,
`encoding='utf-8'` without a handler, the handler set only on the success path -
each turns at least one row red; the fixed tree passes all 78.
