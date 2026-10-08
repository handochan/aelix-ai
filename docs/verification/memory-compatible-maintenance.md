# Memory-compatible beta.2 maintenance candidate

Issue: https://github.com/handochan/aelix-ai/issues/418

The release baseline is `v0.1.0-beta.2` (`1093cc25`). The candidate exposes
`ExtensionAPI.register_setting` and the actual contributed TUI menu from reviewed
PR404. Its bounded PLAN dependency uses the provenance mechanism from #188;
other beta.2 permission postures, child clamping and delegation consent remain
unchanged. Default/main beta.3 release work is tracked separately.

The four host packages use `0.1.0b2.post1`, tag `v0.1.0-beta.2.post1`.
The listed Memory version is 0.2.0, source
`git+https://github.com/handochan/aelix-memory.git@8c10ec3bf90c213404e4b427ef2d1d304ff98592`.

Local macOS Python 3.12 checks completed so far:

- Installed candidate wheels over actual PyPI beta.2; all four versions agree and
  product imports come from site-packages. Synthetic settings, a real version-3
  host session, extension install records, memory consent and a saved marker are
  preserved. Actual manifest verification is BOUND.
- Installed Memory deterministic suite: 83 passed, 1 optional semantic test skipped.
- Installed host settings, PLAN provenance and real modal regressions: 31 passed.
- Source setting/PLAN/modal/version focus: 34 passed; lint passes; type gate sees
  281 files with zero errors and retains all three inverse spike assertions.
- `actionlint` passes both candidate and release workflows.
- Actual PTY at 120x42: default OFF and menu reads create no memory storage;
  selecting ON persists into a fresh process in another project; selecting OFF
  disables globally. Both exit 0. A separate PTY case switches OFF externally
  while the ON row remains visible; selecting that row keeps OFF. Zero model calls.

The first full-suite attempt exposed local gate setup differences: inherited
NO_COLOR=1 and TERM=dumb, missing venv pip, and an unstaged SBOM rename. Corrected
setup reruns and remote CI are recorded separately below. No assertions were
relaxed. The actual installed candidate job runs all nine OS/Python combinations
and the release calls it against the exact uploaded dist artifact. Any failed job
blocks both GitHub and PyPI publication. Model runs and interactive Windows desktop
runs are not implied by those deterministic jobs.

Publication and provider verification are pending; do not treat this preparation
record as a published release.
