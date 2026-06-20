# 0149. TUI-first v1 — Sprint 2: Project Trust (A+) — gate project-local extensions/MCP + persistence

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08e` (Project Trust is a **since-pin** feature — pi 0.79.0–0.79.2;
ground truth fetched from pi HEAD, a documented since-pin adoption like the model-catalog refresh)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #10**, v1 Sprint 2 (security gates). Running `aelix` in an untrusted directory
executed **arbitrary code** from two ungated surfaces: project-local extensions
(`cwd/.aelix/extensions/*.py` → `exec_module`) and project-local MCP servers (`cwd/.aelix/mcp.json` →
subprocess spawn). The only prior "security" was a **post-hoc** stderr warning (`entry.py:372-381`) —
logged *after* loading. A recon pass (`.omc/specs/sprint-p0-10-project-trust-spec.md`) mapped pi's
Project Trust from HEAD and surfaced the scope decisions. The user chose **A+** (minimal gate +
on-disk persistence) with **deny-by-default** headless.

## Decision (A+)

All in `packages/aelix-coding-agent` — **zero protected `aelix-agent-core` change**.

- **CLI flags** (`cli/args.py`): `--approve`/`-a` → trust; `--no-approve`/`-na` → deny
  (`project_trust_override: bool | None`). The non-interactive escape hatch.
- **`cli/project_trust.py`** (new):
  - `has_trust_requiring_project_resources(cwd)` — narrowed to aelix's only two live loaders: True iff
    `cwd/.aelix/extensions/` (with entries) or `cwd/.aelix/mcp.json` exists. (pi's other 5 resources —
    skills/prompts/themes/SYSTEM.md/APPEND_SYSTEM.md — have no aelix loader, so they are not detected.)
  - `ProjectTrustStore` — on-disk `~/.aelix/agent/trust.json` (pi shape: `{absPath: bool|null}`, keys
    sorted), nearest-ancestor walk (trusting a parent trusts children; a child `false` overrides an
    ancestor `true`), validated on read (non-object / non-bool-null → treated as "no decision", never
    an accidental trust), atomic write (temp + `os.replace`), best-effort lock (no `filelock`/
    `proper-lockfile` dependency — single-user-local).
  - `resolve_project_trusted(...)` — pi order minus the extension event:
    `--approve/--no-approve` → no-dangerous-resources(=trust) → persisted store → `has_ui ? prompt :
    deny` (**deny-by-default headless**) → cancel → deny. Persists the prompt result unless a
    "session only" option was chosen.
- **Enforcement** (`cli/entry.py`): trust is resolved **once, before** the MCP connect and before the
  harness factory loads extensions, so **no project-local code runs before the gate**. When untrusted:
  `discover_and_load_extensions(..., no_project_local=True)` skips **only** the project-local
  `cwd/.aelix/extensions` tier (entry_points + explicit `-e <path>` still load); the MCP block drops
  **only** project-sourced `cwd/.aelix/mcp.json` contribs (`$AELIX_MCP_CONFIG`/global still connect) —
  `cli/config.py` now tags each contrib's source. Headless denial prints a clear stderr notice. The
  interactive prompt is a one-shot pre-`run_tui` `prompt_toolkit` selector (pi-faithful wording).

## Scope (pi-parity defaults, user-confirmed)

- Gate **only** auto-discovered `cwd/.aelix/*`; explicit `-e`/`$AELIX_MCP_CONFIG`/entry_points are user
  choices, never gated.
- **AGENTS.md is NOT gated** (pi parity — markdown, not code execution).
- The `project_trust` extension event + `ctx.is_project_trusted()` (which would touch protected
  `aelix-agent-core`) and `defaultProjectTrust` + `settings.json` suppression (which require first
  building a `SettingsManager` in the bootstrap) are **deferred to Phase B** — much of B gates
  resources aelix does not load yet.

## Verification

- **LIVE-VERIFIED the gate blocks code execution** (the gold standard, beyond the test suite): an
  `evil.py` in `cwd/.aelix/extensions/` writing a marker did **NOT** execute when the dir was untrusted
  (`no_project_local=True`), and **DID** execute with trust (`--approve` / gate off) — so the gate is
  real, not always-blocking. `resolve_project_trusted` returns deny headless-untrusted, trust on
  `--approve`, trust with no dangerous resources.
- Implemented + 4-lens review (security-enforcement / correctness / scope / test-adequacy) + fix as a
  dynamic Workflow. 14 findings, 2 confirmed non-LOW — **HIGH: the extension security integration test
  was tautological** (would not fail if the gate were removed — the same trap as ADR-0145 Wave 3) and
  **MEDIUM: no test that `-e`/entry_points still load when denied** — both fixed (the security test is
  now load-bearing). The remaining LOW items (TOCTOU, fsync, symlinked-parent key) are documented
  accepts under the single-user-local threat model (match pi).
- ruff clean; full gate green; diff confined to `aelix-coding-agent` + tests (+ `uv.lock` catching up
  to the A+B `aelix[tui]`/`[images]` meta extras) — **no protected-core touch**.

## Next

Sprint 3 — auth completeness (per-model headers + authHeader→Bearer) + tool cooperative abort
(read/edit/write/grep/find/ls) + RPC bash abort. Then Sprint 4 (core docs).
