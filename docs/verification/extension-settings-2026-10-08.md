# Extension settings verification

Date: 2026-10-08. Local platform: macOS arm64, CPython 3.12.13.
Contract: [ADR-0256](../decisions/0256-extension-owned-global-settings-toggles.md).
Host implementation base: `cd6f7a5c31adf34e5a43c254026aa81d96d60339`.
Integration base after rebase: `28dbcaf4a1a9a97e46d88cec4b90c1ddeb486321`.

## Deterministic host checks

```bash
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor uv run pytest \
  tests/tui tests/extensions tests/harness tests/packaging_gate \
  tests/test_docs_bundle_sync.py -q
uv run ruff check .
uv run python scripts/check_types.py
```

The affected host suite passed **2589 tests** in 99.25 seconds on the implementation
base, then **2710 tests** in 109.77 seconds after rebasing onto the integration
base. Lint passes. The
type gate analysed 287 files with zero errors and preserved all three inverse
narrowing assertions. The focused contributed-settings and real modal/key
pipeline tests pass independently (10 tests); separate review reproduced the
async-staleness and label-dispatch defects before their repairs, then verified
both repairs. A failed lazy activation now clears partial settings contributions,
with a failing-before/passing-after regression in the actual loader path.

The first broader run encountered three environment issues: inherited
`NO_COLOR=1` suppressed ANSI expected by terminal tests, the installed verification
memory wheel added a real discovery entry point to tests expecting one fixture,
and a missing venv pip made scaffold tests choose system Python 3.9. Unsetting the
colour override, uninstalling the verification wheel from the host's disposable
test venv, and installing pip there resolved those failures. No production code
was changed for those environment issues. Installed-extension verification uses
a separate environment.

Whole-repository `ruff format --check` is intentionally not a host CI gate
(see `.github/workflows/ci.yml`); existing manually expanded layouts do not pass
that command. This change preserves those layouts and does not reformat unrelated
code. New settings helpers/tests are formatted; complete lint remains enforced.

## Actual terminal and installed extension

Built Aelix Memory 0.2.0 wheel and installed it in the host's isolated venv. Launched
the real `uv run aelix` in a 120-column, 42-row PTY that answers cursor-position
requests, with temporary agent/memory/session directories, no tools or project
resources, and an explicit model selection to avoid first-run login onboarding.
No model request or credential entry was made in this TUI run.

- Startup and opening `/settings` left the default-OFF memory home absent.
- The actual `Memory off` row changed to `Memory on` after Enter; SQLite persisted ON.
- A fresh process in a different project displayed `Memory on` immediately.
- Enter there persisted global OFF and the reopened menu showed `Memory off`.
- Both processes quit with exit 0.

The memory repository records the synthetic terminal screens in
`docs/verification/settings-tui.json` and installed-wheel model checks in
`docs/verification/live-global-memory.json`. Its deterministic installed-wheel
suite and optional real local semantic-model test are separate from these host
tests. The live fixture used OpenRouter `openai/gpt-5.4-mini` in nine fresh
processes: single global opt-in, automatic project learning and isolated recall,
preference correction, and OFF from another project all passed. No user memory,
credentials, raw transcript or model weights are committed.
