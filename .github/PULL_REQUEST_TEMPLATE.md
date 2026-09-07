<!--
Thanks for sending a PR. Everything below is short on purpose — delete any
heading that does not apply. `CONTRIBUTING.md` has the detail.
-->

## What this changes

<!-- One or two sentences. What behaviour is different after this lands? -->

Fixes #

## Why

<!--
If there is an issue, what in it were you solving? If there is not, what did
you observe that made this necessary?
-->

## How it was verified

<!--
Paste the commands you ran and what they printed. Not "tests pass" — the output.
If you could not run something, say so; that is a useful answer and it will not
be held against you. An unverified claim costs the reviewer more time than an
honest gap.
-->

```
uv run ruff check .
uv run python scripts/check_types.py
uv run pytest -p no:cacheprovider -q
```

<!--
Behaviour changes need a test. Make it a real one: revert your production hunk
and confirm the test goes red. A test that passes with and without your change
is documentation, not a gate.

TUI change? Run `uv run aelix` and say what you saw.
Provider / streaming / session / tool-execution change? Run it against a real
model, not a mock.
-->

## Checklist

- [ ] `uv sync --all-packages` (the bare `uv sync` cannot collect the suite)
- [ ] Behaviour change is pinned by a test that fails without the fix
- [ ] `uv run python scripts/check_citations.py --fix` if this shifts line
      numbers (it fails `tests/test_citation_drift.py` in files you never
      touched — this has caught most outside PRs so far)
- [ ] Edited a guide? Both copies — `docs/guides/` and the wheel-bundled
      `packages/aelix-coding-agent/src/aelix_coding_agent/docs/`
- [ ] Touched `.gitignore` or a `pyproject.toml` `exclude`? Ran
      `tests/packaging_gate/`

<!--
If a fork CI run is sitting at `action_required` with no jobs, that is a gate on
the maintainer's side, not a problem with your branch. Say so in a comment —
that is a useful thing to post, not nagging.

If you used an AI assistant: fine and common here. Say so, and hold its output
to the same standard. Numbers it reported that you did not see yourself are not
measurements.
-->
