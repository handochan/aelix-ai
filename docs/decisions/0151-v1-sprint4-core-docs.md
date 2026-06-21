# 0151. v1 Sprint 4 — core user docs (getting-started + providers + models.json + extension authoring)

Status: Accepted
Date: 2026-06-21
Pi pin: `earendil-works/pi@734e08e`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

Sprint 4 of the TUI-first v1 track. The repo had design docs (`docs/00-04`,
`docs/decisions/`, `docs/contracts/`) and a README, but **no task-oriented user
guides**. The v1 assessment flagged the missing surfaces: getting-started,
provider/model setup, extension authoring (the `ExtensionAPI` is a key surface
with only the `examples/echo` sample), and a `models.json` usage doc (the loader
shipped in ADR-0140; only the doc was missing).

A recon pass pulled the **ground truth** from code rather than memory, which
surfaced one inaccuracy to fix:

- **`aelix auth login <provider>` does not exist.** `cli/args.py` has no
  subcommands and `cli/entry.py` has no `auth`/`login` handler — the README
  suggested it. Real auth = provider env var (`cli/.../_env_api_keys.py`),
  `--api-key`, or `models.json` `apiKey`. The README line was corrected.

Verified surfaces used as the doc source of truth: `cli/args.py` (the full flag
set + `print_help` text), `providers/_env_api_keys.py` (the provider→env-var
map), `models_json.py` + ADR-0140 (the schema: `providers` object;
`baseUrl`/`apiKey`/`api`/`headers`/`authHeader`/`compat`/`models`/`modelOverrides`;
env-var & `!command` indirection; `authHeader → Bearer`; path
`~/.aelix/agent/models.json`), `extensions/api.py` + `extensions/loader.py` +
`examples/echo/echo.py` (the `setup(aelix: ExtensionAPI)` factory,
`register_tool`/`register_command`/`register_provider`/`get_flag`/`on`, and the
file-path / dotted-module / `entry_points(group="aelix.extensions")` /
`.aelix/extensions/` loading channels gated by Project Trust per ADR-0149).

## Decision

New `docs/guides/` (a sibling of `docs/contracts/` and `docs/decisions/`), with a
`Status:` header per `docs/00-conventions.md`:

- `docs/guides/getting-started.md` — install (uv/pipx/pip + `tui`/`images`
  extras), set a key, run, the text/print/json/rpc modes, common flags, TUI slash
  commands + Esc-cancel + Ctrl+G, and the dev (`uv run aelix`) path with the
  `python -m aelix` demo caveat.
- `docs/guides/providers-and-models.md` — `<provider>/<model>` ids,
  `--list-models`, the three key sources (env var / `--api-key` / `models.json`),
  the provider→env-var table, the no-key guidance behaviour, and `--thinking` /
  `--offline`. Explicitly states there is **no** `aelix auth login`.
- `docs/guides/models-json.md` — the `~/.aelix/agent/models.json` schema with
  worked examples (custom provider, per-model headers + `authHeader`, env-var /
  `!command` indirection, built-in `modelOverrides`).
- `docs/guides/extension-authoring.md` — the `setup` factory, a worked tool +
  slash command (mirroring `examples/echo`), the `ExtensionAPI` surface table,
  and the three loading channels + Project Trust gating.
- `docs/guides/README.md` — guide index.

Plus: README "User guides" links + the `aelix auth login` correction;
`docs/README.md` structure list gains `guides/`.

## Consequences

- A new user can install, authenticate, pick a model, add a custom model, and
  write an extension from the docs alone — every command/flag/field is taken
  from code, not invented.
- One real README inaccuracy (`aelix auth login`) fixed.
- Docs-only sprint: **zero code change**, gate unaffected. Accuracy was verified
  by an adversarial review cross-checking every documented command/flag/API
  against the source, and by running the documented commands (`aelix --help`,
  `--list-models`).

## Files

- New: `docs/guides/{README,getting-started,providers-and-models,models-json,extension-authoring}.md`
- Changed: `README.md`, `docs/README.md`
