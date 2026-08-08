# Beta-readiness audit — 2026-08-01

**Base:** `main` = `0c9da7d` (moved from `c3af0a3` mid-audit).
**Method:** 8 parallel dimension audits → one adversarial verifier per dimension (instructed to REFUTE) →
synthesis → completeness critic. 18 agents, ~2.6 M subagent tokens, 1205 tool calls.
**Owner's beta bar:** (a) advertised features run stably, (b) docs match code, (c) basic multi-agent works,
(d) Windows officially supported, (e) self-extension usable, (f) extensions + marketplace fully supported
incl. private/air-gapped catalogs. Plus overall code health and a backlog triage.

**Verdict: GO_WITH_SCOPE_CUT.** Two of the six bars (d Windows, f marketplace) cannot be met on any sane beta
timeline and should be cut truthfully rather than faked. Timeline **4–5 weeks** (the synthesis said 2.5–3; the
completeness critic raised it after finding an entire unaudited subsystem — see §6).

Everything below was measured. Claims that were refuted during verification have already been dropped.

---

## 1. The one-paragraph state of the world

The engineering is in better shape than the release surface. The kernel is coherent, `ruff` is clean and
enforced, CI is green on both legs, the band rule is machine-enforced, the air-gapped private-catalog path
works end to end, publisher signing works with correct fail-closed refusals, and the self-extension loop
mechanically closes. What is not shippable is the **release surface** (zero tags, zero releases, all five
PyPI names 404, and the getting-started guide teaches a `pip install` that cannot work), the **install
integrity gate** (inert — see B2), and the **execution floor** (a cloned repo runs arbitrary code on
`cd repo && aelix`, with no prompt — see B3).

---

## 2. Blockers — must land or be retracted before a tag

| # | Title | Size | Issue |
|---|---|---|---|
| **B1** | Every documented install path is dead | S | #76, #111 A-4 |
| **B2** | `install.sh`'s SHA-256 gate is non-binding; five package names unclaimed on a public repo | S | adj. #73 |
| **B3** | A cloned repo executes arbitrary code with zero prompts (cwd `.env`) | M | adj. #112, #121 |
| **B4** | Manifest `contributes.mcp_servers` spawns subprocesses before the harness builds | S | ADR-0203, unmerged |
| **B5** | Write guardrails fold open on case / trailing whitespace — **live on macOS**, mis-filed as Windows | S | #108 |
| **B6** | Four manifests declare `OS Independent` while Windows is unsupported | XS | #103, #110, #111 |
| **B7** | "Extensions are verified with Ed25519 provenance" is false on the default path | XS | #76 Ph3, #111 B-8 |

### B1 — every documented install path is dead

Re-verified at HEAD: `git tag -l | wc -l` → 0; `gh api .../releases --jq length` → 0; `git ls-remote --tags
origin` → empty. The unpinned path dies at `install.sh:124-125` (`die "could not resolve a release tag"`);
the pinned path README calls "recommended" dies at `install.sh:133-134` (`die "SHA256SUMS not found for
'v0.1.0-beta.1'"`). `docs/guides/getting-started.md:12-27` — the target of the homepage's "Get started"
button (`site/index.html:156`) — offers only five `pip install` commands and never mentions `install.sh`.
All five names measured 404 on PyPI. README uses future tense; the guide it links uses present tense.

### B2 — the SHA-256 gate is non-binding, and the names are unclaimed

`install.sh:154-158` sets `target="aelix[$AELIX_EXTRAS]"`; `:165` runs
`uv tool install --force --find-links "$tmp" "$target"` with **no version constraint**, and `:161-164`
deliberately keeps the PyPI index enabled ("Never use `--no-index`"). Measured with the exact command form:
a local dir holding `cowsay 4.0` + `uv tool install --force --find-links <dir> cowsay` → `+ cowsay==6.1`.
PyPI won. The verifier also measured that **ties go to find-links**, so an exact version pin is a *complete*
fix, not a partial one.

Blast radius is total but the surface is one name: `pyproject.toml:29-33` pins the three sub-packages
`==0.1.0b1`, so only top-level `aelix` is hijackable — and a malicious `aelix` declares its own dependencies.
Repo visibility measured `PUBLIC`. `install.sh:6-10` repeats the falsified promise in its own header.

> This is the only item that is **time-critical independent of shipping**. Reserve the names now.
> Delete the stale `dist-reservation/` first — its built wheels carry the pre-fix stable `0.0.0`.

### B3 — cwd `.env` → arbitrary code at startup, no prompt

Executed at HEAD, not inferred. `main_sync` calls `load_dotenv()` (`cli/entry.py:2294`) **before**
`_async_main`; `load_dotenv` (`cli/runtime_bootstrap.py:49-70`) `setdefault`s the cwd `.env` into
`os.environ` with **no key filter**. The trust gate lives far below.

Measured doors (each independently reproduced):

| Variable | Effect |
|---|---|
| `AELIX_CODING_AGENT_DIR` | relocates the **ungated global extension tier** into the repo → arbitrary Python at import. Probe wrote `rce-at-import` with no `--approve` and no trust prompt |
| `AELIX_MCP_CONFIG` | `/bin/sh -c` at startup. `cli/config.py:134` resolves the env tier first and `entry.py:1829` gates **only `source=="project"`** — the docstring calling env/global "explicit user choices … NEVER gated" is a false premise. Probe wrote `MCP-ENV-RCE` |
| `AELIX_SETTINGS_PATH` | global-settings takeover → `defaultProjectTrust: "always"` self-promotes the repo to trusted, opening the *project* tier too (extensions, skills, profiles, MCP). **Disk key is camelCase**; snake_case silently does not parse (a test false-greened on this) |
| `AELIX_AUTH_PATH` | a later `/login` writes OAuth tokens into the cloned repo |
| `AELIX_CODING_AGENT_SESSION_DIR` | every transcript lands in the cloned repo |
| `OPENROUTER_BASE_URL` | prompts + `Authorization` header to an attacker host — one `--print` sent 12 times via auto-retry |

`aelix_agents/print_channel.py:448-455` documents that these same variables **pass through to every delegated
child wholesale**.

> **The fix must be a boundary rule, not a variable allowlist.** Refuse to let a cwd `.env` set any
> `AELIX_*` / `XDG_*` / `EDITOR` control variable at `cli/runtime_bootstrap.py:49-70`. An enumerated
> two-variable fix was measured to leave three doors open.
> `setdefault` semantics mean an allow/deny list does not break the legitimate workflow (a user-exported
> value stays authoritative), and `.env` must keep working as the owner's own API-key path — this repo
> uses it that way.
> **Do not open a public issue** — private Security Advisory until patched.

Compounds with: ungated `AGENTS.md` system-prompt injection (`entry.py:785-786`, walk to filesystem root at
`agent_context.py:378-382`) and vacuous trust frozen into the `_harness_factory` closure
(`entry.py:1925-1933`) so `/reload` never re-asks. Correct the false security claims in
`get_features_agents` / `get_default_project_trust` / `config.py` in the same pass.

### B4 — ungated MCP subprocess from a manifest

`cli/entry.py:1846-1873` scans manifests → flattens `contributes.mcp_servers` → `McpClientManager(...)` →
`connect_all()`; `mcp/client.py:135-151` builds `StdioServerParameters(command=…, env={**os.environ,
**contrib.env})`. A manifest containing **no Python and declaring no capabilities** executes an arbitrary
command on every start. Capability sweep re-run independently: exactly **two** real gates exist
(`loader.py:1097`/`:1413` `ui_tui_trusted`, `loader.py:1441` `shell_exec`) — nothing between the scan and the
spawn reads a capability.

The fix is already written, tested and green but **unmerged**: `878004b` (hoist the gate above factory
invocation) + `8a4415e` (gate stdio on `shell_exec`, http/sse on `net`) + ADR-0203. Verified:
`git merge-base --is-ancestor 878004b HEAD` → NO; `docs/decisions/` tops out at 0202.

**Ordering is load-bearing: #91 must NOT land before this gate**, or every `aelix extension install` becomes a
remote-exec primitive. Confirmed mechanically — `loader.py:759` passes `include_entry_points=False` inside
`scan_extension_manifests`, and that same scan feeds the spawn.

Also: declaring **any** `env` key silently switches the child from a 6-variable allowlist to the full parent
environment (measured 6 lines vs 136 lines containing `ANTHROPIC_API_KEY`) — and the repo's own copy-me
`examples/echo/aelix-plugin.toml` declares exactly such a key.

**Note (from the critic): B4 does not close B3's `AELIX_MCP_CONFIG` path.** That config carries no manifest
and no `[capabilities]` block, so there is nothing for a capability gate to read. Two separate fixes.

### B5 — write guardrails fold open on case, live on macOS

Reproduced **on Linux** at HEAD, which is what proves it is not a Windows defect: `_is_auto_allowable_write`
returns `auto_allowed=True` for `.ENV`, `Id_Rsa`, `.SSH/AUTHORIZED_KEYS`, `.AELIX/agents/e.md` and `.env `
(trailing space), while the lowercase/untrailed forms all correctly return `False`.
`grep -n 'casefold|\.lower()|normcase' builtin/permission.py` → zero hits; `_SENSITIVE_BASENAMES` (`:216-233`)
and `_SENSITIVE_DIR_COMPONENTS` (`:253-255`) are all-lowercase frozensets. Needs a case-insensitive
filesystem — **macOS default APFS**, which `install.sh:89` explicitly targets.

`permission.py:300-305` names the `.aelix` component as "the HARD PREREQUISITE for bounded widening" per
ADR-0197 — `.AELIX/agents/e.md` folding open voids it. Guardrail half: `guardrail.py:125` `_write_dotenv`
does `path.rsplit("/",1)[-1]` while its siblings at `:135/:145/:155` all normalize backslashes first.

> A **casefold-only fix is the worst outcome** — it closes #108 F-2 and looks fixed while leaving the
> trailing-whitespace/dot and `::$DATA` stream-suffix family open. Normalize all four shapes.
> Re-title #108 off "Windows".

### B6 — `OS Independent` vs. no Windows support

`grep 'OS Independent'` → `pyproject.toml:18`, `aelix-agent-core:18`, `aelix-ai:18`, `aelix-coding-agent:19`
(`aelix-server` has no classifiers and is dropped from the publish set at `release.yml:82-89`).
`grep -rin windows` over README/README.ko/docs/guides/site/CHANGELOG/RELEASING/install.sh/SECURITY → **zero
hits**. `ci.yml:33` is the only test runner (ubuntu-latest; the matrix varies python only). Zero
`.ps1`/`.bat`/`.cmd` files. All nine issues open (#103-#109, #46, #110).

**The repo holds two contradictory owner triages**: #110 stamps this P0 ("Windows CI 없이 코드만 고치면
지원이 아니라 스냅샷이다") while #111:163 — the issue the audit itself calls *the* pre-announce gate — files
it under "베타 이후로 미뤄도 되는 것". Resolve before announcing.

### B7 — the Ed25519 claim is false on the default path

Present-tense and unqualified at `README.md:38-40`, `README.ko.md:39-41` (Korean is *stronger*: "기본입니다")
and `site/index.html:181-182`. Code: `extension_signing.py:661-684` — an absent `.aelixsig` is accepted
**silently** (TOFI), and only `--require-signature` refuses. `extension_install.py:1009-1011` — pypi two-phase
hashing is opt-in in v1. `extension_catalog.py:24-27` — catalog sha256 is display-only. `extension_pins.py:23-26`
— `keyId`/`sig` are "an inert forward-compat seam … Nothing in v1 writes or reads them."

Decisively: `extension_signing.py:111` is `FIRST_PARTY_KEYS: dict[str, str] = {}`, so
`extension_install.py:2665`'s `signature_required` is **always the empty frozenset** — signing the catalog
would not help, because no client would check it. `SECURITY.md:71-72` already states the truth
("without it, unsigned sources are accepted on first use"), so the beta would ship two contradictory
statements about its own security posture.

Publisher signing itself **does** work end to end — keygen → sign → trust add → `--require-signature`, with
correct fail-closed refusals on both unsigned and tampered wheels. That is what makes the overstatement
unnecessary.

---

## 3. Readiness by dimension

| Dimension | Verdict | One line |
|---|---|---|
| release | **NOT READY** | Zero tags/releases/workflow runs; the SHA-256 gate is bypassed by the command that consumes it |
| docs-honesty | **NOT READY** | Tone is unusually honest and SECURITY.md/CHANGELOG are exemplary — but the install story is fiction and the strongest security headline is falsified |
| marketplace | **NOT READY** | Plumbing is good and the private/air-gap path works end to end; #91 makes every catalog-installed pack's declarative layer inert and the official catalog is empty |
| windows | **NOT READY** | Nothing landed; nine issues open, four manifests say OS Independent, every runner is ubuntu-latest |
| self-extension | READY w/ caveats | The loop measurably closes (bare `.py` at the signposted path loads with `errors=[]`, tool reaches the model's schema set, 13/13 reload tests green) — but the hero claim was never measured against the *shipped* prompt |
| multi-agent | READY w/ caveats | Live, well-governed, default-OFF. Real gaps are authoring docs and two observability holes, not stability |
| code-health | READY w/ caveats | ruff clean and enforced, hook bus has zero dead channels, band rule machine-enforced — but the second documented gate (pyright) is a measured no-op that is also unconditionally red |
| backlog | NOT READY | 69 open issues; #111's tag-blocking items are genuinely closed and re-probed; what remains are the announcement-day surfaces |

### What the audit confirmed is genuinely good

- **Air-gapped / private catalog works today, end to end** — a `file://` catalog registers, discovers offline
  and installs offline; a private index resolves bare names. This is the owner's stated enterprise goal and
  it is *better than the current pitch*.
- **Publisher signing** works with correct fail-closed refusals (unsigned *and* tampered wheels).
- **Multi-agent delegation** is live, default-OFF, governed (`MAX_DELEGATIONS_PER_PROMPT=12`, concurrency cap,
  consent dialog). The dimension's own BLOCKER was downgraded by its verifier.
- **Self-extension mechanically closes** — measured, not asserted.
- `ruff` clean and CI-enforced; band rule machine-enforced (`tests/cli/test_p2_import_direction.py`);
  hook bus has zero dead channels; CI green on both legs; SECURITY.md and CHANGELOG are exemplary.

---

## 4. Scope cuts (recommended)

**Windows (bar d) → declare macOS/Linux only for beta.** XL to actually deliver, with an *unknown* failure
count: #109's 85–110 estimate was computed against 375 files / 5,073 tests and the suite is now 419 files.
Critically, **adding a `windows-latest` job first would DELETE coverage of exactly the broken subsystems** —
all 12 `win32` markers in the suite are subtractive `skipif` and there is not one `skipif(!=win32)`. A naive
job reports green over auth locking, real-child spawn, pipe robustness and file permissions. Correct order:
fix the 25 `setenv("HOME")` tests → *write* Windows-asserting tests → then add the leg at `continue-on-error`.

**Marketplace (bar f) → ship "extensions + private/air-gapped catalogs".** #91 is L and order-constrained
behind B4. Two verifier corrections worth carrying: the **imperative API works fine** on an installed pack
(`register_tool`/`register_command` run, `errors=[]`) so only the *declarative* layer is lost; and 3 of the 7
`contributes` families are already truthfully documented as inert. Real losses: hooks, themes, tui_widgets,
mcp_servers, `[capabilities]`, lazy activation, plugin identity. Stop marking those as "Activated" at
`extension-authoring.md:147-150`. **Explicitly do not seed the official catalog before #91** — a submitted
pack would install inert and its author would take the blame.

---

## 5. Sequencing

### Wave 1 — close the execution floor, claim the names (M, ~1 week)
Make it safe for a user to exist, and stop a land-grab that is open right now on a public repo.
- B3 as a **`load_dotenv` boundary rule** (not a variable list)
- Merge `878004b` + `8a4415e` + ADR-0203 (B4) — review, not construction
- Normalize case + trailing whitespace/dots + stream suffixes in `_is_auto_allowable_write` and
  `guardrail._write_dotenv` (B5); re-title #108
- Reserve all five PyPI names after deleting stale `dist-reservation/` (B2, half)
- Decide the MCP `env`-key escalation, or at minimum remove it from `examples/echo/aelix-plugin.toml`

*Why first:* two items are exploitable independent of shipping, and the capability gate is a hard
prerequisite for #91 later.

### Wave 2 — make the documented install work (S-M, ~3 days)
- Pin `aelix[$extras]==<version parsed from SHA256SUMS>` at `install.sh:165`; fix the header at `:6-10`
- Rewrite `getting-started.md`'s Install section to mirror README's `curl|sh`, PyPI clearly future-gated
- Push a throwaway **hyphenated** rehearsal tag — free, because `release.yml:110-112` gates the irreversible
  PyPI job off for hyphenated tags — then delete it
- Add the tag↔package-version assertion; narrow the tag regex to hyphen-only (`v0.1.0.rc1` currently passes
  the regex, publishes to PyPI irreversibly with `skip-existing:false`, and is marked **stable**)
- Cut `v0.1.0-beta.1`; verify `install.sh` end to end on Linux **and macOS**

### Wave 3 — make every claim true or retract it (M, ~1 week, dominated by decision latency)
B7 retraction on three surfaces · B6 platform policy + classifiers · marketplace re-scope · `--offline`
"no-op" wording on all three surfaces incl. `cli/args.py:679` · disclose the rg/fd binary fetch · delete or
wire the five inert `--help` flags · README.ko/AGENTS.md/demo-caption/RELEASING.md corrections · mark
`[capabilities]` advisory · fill the empty GitHub About box.

### Wave 4 — make failure legible (M, ~1 week)
`/reload` output + TUI-surfaced load errors · the N≥4 real-model self-extension probe · delegated-child spend
in `/cost`/`/stats`/statusline · persist or expose a child transcript · document agent-profile frontmatter,
MCP and the RPC protocol · **merge PR #119** · chase #98/#99 reporter confirmations.

### Wave 5 — durable gates (M-L, ~1–1.5 weeks, can trail the announcement)
pyright include fix + quarantine `scripts/pyright_spike.py` + add to CI + triage 34 unseen errors ·
`RUF100` · five `py.typed` markers · SBOM glob assertion · `.claude` in the hatchling excludes ·
Windows CI **in the correct order** · pin `pages.yml`'s four floating action tags.

---

## 6. What the completeness critic found (raises the estimate to 4–5 weeks)

The critic ran probes, not speculation. Three findings change the plan:

**A ninth dimension is missing: state durability.** Session persistence appears in zero of the eight
dimensions, and first contact produced two user-visible integrity defects:
- **A crash-truncated line permanently bricks `aelix --continue` in a directory.** `_load_jsonl_storage`
  (`jsonl_storage.py:274` → `_parse_entry_line:221`) raises on the first bad line with no skip-and-continue,
  while `_is_valid_session_file` validates the **header only** — so `find_most_recent` re-selects the same
  bricked file forever. It still appears in the `/resume` picker. No `--repair`, no partial load, and the
  error never tells the user to delete it.
- **Two terminals on one session silently discard a branch.** `grep -rn 'flock|fcntl|lockf'` over
  `session/` → **zero hits**, while `auth_storage.py:171` and `settings/storage.py:191` both use `fcntl.flock`.
  Measured: appends A-1, B-1, A-2 land on disk (4 lines), reopen replays `['A-turn-1','A-turn-2']` — B's turn
  orphaned and invisible forever. `--continue` deterministically resolves the same file for two terminals in
  one cwd.
- **Session JSONL is 0644 and unredacted** while `auth.json` is 0600. Measured `DIR 0o756`, `FILE 0o646`.
  Every prompt and tool result verbatim — so an agent that runs `env` or reads a key file writes the
  credential into a world-readable file. No scrubbing pass exists.

**Upgrade/migration is an entirely missing dimension.** `run_migrations` has **zero production callers**
(its own docstring says the wiring hook never landed); the session header is hard-pinned
(`jsonl_storage.py:126`, `version != 3 → SessionError`); and `upgrade`/`uninstall` appear **0 times** across
README, README.ko, getting-started, `install.sh` and RELEASING.md. `uv tool install --force` *is* the upgrade
command — so **B2's hijack window reopens on every upgrade**, not only at first install.

**Two corrections to the plan itself:**
- **Wave 1's B3 as originally scoped leaves an RCE open** (`AELIX_MCP_CONFIG`) — hence the boundary-rule
  rewrite above. Shipping the enumerated version produces a beta that *believes* its floor is closed.
- **#122 must move out of `defer`.** After `/resume`, `get_session_stats()` aggregates only
  `self._state.messages`, which is empty until the next turn — so `/context`, `/cost`, `/session`, `/stats`
  and the chrome meter show zero or stale values, and one acceptance criterion is literally "Session A values
  never appear in session B's footer after switch." That is a cross-session leak in the UI plus a wrong bill.
  **It is a prerequisite for S2** (rolling child spend into a parent total that is itself zero/stale).

Also raised: zero performance/load tests (`grep -rln 'time.perf_counter|benchmark' tests/` → 0 files of 419),
no session retention/GC, and a **Korean/CJK display-width bug** — `tui/context.py:137` `_visible_len` is
codepoint-based (`한국어 확장 설명입니다` → 12 vs `get_cwidth` 22), so `/context`, `/model`, `/extension` and
the stats picker frames under-span ~45% for Hangul. `get_cwidth` is already imported correctly at
`tui/chrome.py:73,302` — a one-line fix. Only 7 of 419 test files contain any Hangul.

---

## 6.5 OWNER DECISIONS — TAKEN 2026-08-01

The owner **declined both recommended scope cuts** and chose to deliver the full bar. Recorded verbatim so
nobody re-litigates it:

| # | Question | Decision | vs. recommendation |
|---|---|---|---|
| 1 | Windows | **Delay the beta 4–6 weeks and actually support Windows** | declined (a); chose (b) |
| 2 | Marketplace | **Fix #91 and delay the beta** | declined (a); chose (b) |
| 3 | Security gate | **Gate — Wave 1 lands before any tag** | as recommended (a) |
| 4 | README hero | **Measure it** (N≥4 real-model probe against the shipped signpost) | as recommended (a) |

**Consequences of 1+2, stated plainly:**

- §4's scope cuts are **void**. §3's Windows and marketplace verdicts stay NOT_BETA_READY until the work
  actually lands — they are no longer retractable by copy edits.
- §5's five-wave, 4–5-week plan is **superseded by §5b**. Realistic critical path is **7–9 weeks**
  (≈10–12 if run serially), given this repo's demonstrated p50→p90 gap.
- Wave 3's Windows/marketplace *retractions* are dropped from the doc-honesty work. The Ed25519 retraction
  (B7), `--offline` wording, rg/fd disclosure and the small corrections all still stand.
- Two ordering constraints become load-bearing rather than advisory:
  1. **#91 must land after B4's capability gate.** Non-negotiable now that #91 is in scope.
  2. **Windows-asserting tests must be written BEFORE the `windows-latest` leg.** The 12 existing `win32`
     markers are all subtractive `skipif`; a leg added first reports green over the broken subsystems.

**Full suite verified green at HEAD `0c9da7d`: 7425 passed, 1 skipped, 0 failed (392 s).** Closes the first
item in §8.

---

## 5a. WAVE 1 STATUS — 2026-08-02 — ✅ MERGED TO MAIN (`14ba8c2`, pushed)

Branch `fix/wave1-execution-floor` merged `--no-ff` into `main` and pushed (`e185eb1..14ba8c2`).
**Full suite: 7645 passed, 1 skipped, 0 failed** on the exact tree that landed. `ruff check` clean.

| Item | Status |
|---|---|
| **B3** cwd `.env` | ✅ **shipped by a PARALLEL SESSION** as `e185eb1` (ADR-0203). My duplicate implementation was **discarded** — theirs is default-deny with value-shape checks and is strictly stronger. Independently re-verified with my own end-to-end probes: both RCEs blocked, credentials still load. |
| **B4** capability gates | ✅ merged (`878004b` + `8a4415e`), **ADR renumbered 0203 → 0204** |
| **B5** write-guard folding | ✅ `58af48f` — 13 bypasses → 0 |
| MCP `env`-key escalation | ✅ `74ad972` — declaring `env` no longer widens inheritance (6→133 became 6→7) |
| **Gate 0** PyPI names | ⛔ **not done — needs owner approval** (outward-facing publish) |

### What the collision cost, so it is not repeated

The owner runs several sessions at once. This one spent a full implementation cycle on B3 while another
session was shipping the same fix. **Check `git fetch && git log origin/main` before starting any item from
this document.** Two concrete things theirs caught that mine would not have:

* Vertex's primary auth path is **ADC, which uses no API key at all**, and Cloudflare's base_url is the
  catalog's only `{ENV_VAR}` template — a credentials-only rule silently hid **43 models** (measured 847 →
  804). A deny-list happened to dodge this; an allow-list had to earn it.
* `BASH_ENV` — bash sources it on every non-interactive shell, and `tools/bash.py` passes `get_shell_env()`
  to every `bash -c`. It carries no `AELIX_` prefix and I did not have it.

### ADR numbering — RESOLVED HERE, nothing required of the #91 branch

`main` owns **0203** (dotenv admission control), so the capability-gates ADR had to move. Renumbering it to
0204 collided a **second** time: `worktree-issue-91-ep-manifest` already carries
`0204-entry-point-manifests-…`, and a trial merge of both produced **two files numbered 0204** with git
flagging nothing but the index table — a duplicate nobody would be told about. The gates ADR therefore took
**0205**, so the coordination cost lands on the merged branch rather than on one someone else is working in.

Main now holds 0202, 0203, **0205**; #91 keeps 0204 and needs no rename.

**Still for the #91 branch:** it cites `ADR-0203` **8 times** (3 in its own ADR, 5 in `packages/`+`tests/`)
meaning the *gates* ADR. Those are stale whatever number the gates took, and are that branch's to fix →
`ADR-0205`.

Landmine confirmed by a failing test: the capability `Field(description=...)` strings cite the ADR number, and
that prose is the **only per-flag text that reaches `docs/contracts/manifest.schema.json`** — so renumbering
drifts the checked-in schema. Re-run `python scripts/generate_contracts_schemas.py` after any such edit.

### Merge safety — measured, not assumed

Trial merges into a scratch worktree at `origin/main`, before pushing:

* This branch → main: **0 conflicts**.
* `worktree-issue-91-ep-manifest` → main: **1 conflict** (`docs/decisions/README.md`) both **with and
  without** this branch. Not worsened. That conflict is the ADR index table and pre-dates this work.
* `docs/extension-authoring-honesty` → main: 1 conflict (`docs/guides/extension-authoring.md`) — **also
  pre-existing**, reproduced against plain `origin/main`.
* `feat/89-default-catalog-url` and the workflow worktree: **zero file overlap**.
* The main checkout's 48 dirty entries are **all untracked** (`.omc/` specs, `docs/assets`, scratch HTML);
  zero tracked modifications, and none of them collides with a path this merge adds.

### Open questions for the owner

1. **Gate 0** — reserve the five PyPI names. Still a live race on a public repo. **UNRESOLVED.**
2. **MCP `env`-key escalation** — declaring any `env` key hands the child the full parent environment
   (measured 6 lines vs 136 containing `ANTHROPIC_API_KEY`), and the repo's own copy-me
   `examples/echo/aelix-plugin.toml` declares one. **UNRESOLVED.**

---

## 5b. Revised sequencing (post-decision)

### Gate 0 — immediate, hours (do not wait for anything)
Reserve all five PyPI names after deleting the stale `dist-reservation/`. Fill the empty GitHub About box.
Both are live races on a public repo and neither blocks on any decision above.

### Wave 1 — the execution floor (~1 week) — BLOCKS THE TAG
Unchanged from §5: B3 as a `load_dotenv` boundary rule · merge `878004b` + `8a4415e` + ADR-0203 (B4) ·
B5 normalization (case + trailing whitespace/dots + stream suffixes) · the MCP `env`-key escalation decision.

### Then three tracks, parallelizable across worktrees

**Track W — Windows (4–6 weeks, the critical path).** Strict order:
1. Fix the 25 `setenv("HOME")` tests that leak to the runner profile, and **write Windows-asserting tests**
   (there is not one `skipif(!=win32)` today).
2. `_resolve_shell` win32 branch with **force-ASK on non-bash shells** (the tree-sitter classifier is
   bash-only) · three kill sites (`AttributeError` + a real process-tree killer) · `install.ps1` ·
   `connect_read_pipe(sys.stdin)` in rpc mode · `%APPDATA%` config dir · `WT_SESSION` image probe ·
   `sys.stdout.reconfigure(encoding="utf-8")` in print mode · the Ctrl+G `notepad` fallback.
3. `windows-latest` CI leg at `continue-on-error`, then burn the failure count down. #109's 85–110 is a
   **floor**, not an estimate — it predates ~20% suite growth.
4. Keep `OS Independent` only once the leg is green; until then it is still B6.

**Track M — #91 + marketplace (1–2 weeks, starts only after B4 merges).** The design is decided and recorded
in `project_gate_order_mcp_hole_and_91_design` / the `worktree-issue-91-ep-manifest` worktree: *"prove →
bind, doubt → downgrade, refuse only on evidence of attack"*; `ep.load()` deleted entirely; manifest bound to
`ep.value`'s module path, never to the dist; `pkg_dir = manifest_path.resolve().parent` as the single
producer. Real cost is the acceptance criteria (prove no module import during discovery; prove the theme
path-fence holds inside site-packages), not the synthetic `.dist-info`. Then seed the official catalog —
**not before**.

**Track S — state durability (~1 week, newly surfaced by §6, was in no plan).** Session `flock` ·
skip-and-continue or `--repair` for a corrupt line, and stop `find_most_recent` re-selecting a bricked file ·
session files 0600 · **#122** (promoted out of defer; it is a prerequisite for S2, not independent).

### Wave 2 — install truth + release rehearsal (~3 days, any time after Gate 0)
Pin `aelix[$extras]==<version from SHA256SUMS>` · rewrite `getting-started.md` · **push a throwaway hyphenated
rehearsal tag** (free — `release.yml:110-112` gates the irreversible PyPI job off for hyphenated tags) to
exercise a pipeline that has **never run once** · tag↔version assertion · hyphen-only regex.
Run this early despite the later tag date: it de-risks the one pipeline nobody has ever observed.

### Wave 3 — doc honesty (~1 week, minus the two retractions)
B7 Ed25519 · `--offline` wording on all three surfaces · rg/fd disclosure · the five inert `--help` flags ·
README.ko / AGENTS.md / demo caption / RELEASING.md · advisory `[capabilities]` table (still true — the seven
unenforced capabilities are not in Track M's scope).

### Wave 4 — legibility (~1 week)
`/reload` output · the **N≥4 real-model self-extension probe** (decision 4) · child spend · child transcript ·
agent-profile / MCP / RPC docs · **merge PR #119** · #98/#99 reporter confirmations.

### Wave 5 — durable gates (~1–1.5 weeks, partly trailing)
pyright include + quarantine + CI + 34 errors · `RUF100` · `py.typed` ×5 · SBOM glob assertion ·
`.claude` excludes · `pages.yml` SHA pins. Windows CI now belongs to Track W step 3.

---

## 7. Owner decisions (original recommendations — superseded for 1 and 2 by §6.5)

1. **Windows** — bar (d) vs. #111:163 vs. #110 P0. Recommend **(a) declare macOS/Linux only**, drop/qualify
   the classifiers, keep #110 as the post-beta epic. Nothing user-facing claims Windows today, so it costs one
   paragraph and removes a false machine-readable signal.
2. **Marketplace** — recommend **(a) re-scope** to "extensions + private/air-gapped catalogs". The air-gap
   story is genuinely better than the pitch. Do **not** seed the catalog before #91.
3. **Ed25519 claim** — recommend **(a) retract now** to match SECURITY.md, **(b) provision the first-party key
   for GA**. Signing works; the overstatement is unnecessary.
4. **The README hero** ("The agent extends itself") — recommend **(a) measure it**: N≥4 real-model probe
   against the *shipped* signpost in a clean install; keep the caption if it passes, soften if not; re-record
   the tape without the hand-staged `extension_guide.md` that does not ship. Score the
   *confident-false-success* rate, not just pass/fail. Cheapest high-leverage item in the assessment.
5. **Do the security items gate the announcement?** Recommend **(a) gate** — Wave 1 before any tag. The
   "no users means no victims" argument is technically correct but inverts the product's own positioning.
   **Reserve the names immediately regardless of the answer** — that one is a live race.

---

## 8. Risks / what remains unmeasured

- **No delegation has ever run against a real model or a real aelix child on either channel.**
  `tests/agents_ext` is network-free and key-free; the RpcChannel bed drives `python -c` stubs. Token
  accounting, provider auth propagation, streaming cadence, timeouts and cost roll-up are all unverified.
  Precedent: `prompt(images=)` — 22 green forwarding tests over a feature broken end to end.
- **The release pipeline has never executed** — 0 runs under the Release workflow (CI 90, Dependency Graph 7,
  Pages 3, Release 0). The artifact round-trip, the SBOM glob in `gh release create`, the `--prerelease`
  branch and the notes fallback are YAML readings, not observations.
- **`install.sh`'s macOS branch has never executed anywhere** — CI is ubuntu-only, no `macos-latest` job.
  macOS is likely a plurality of beta users and is where B5 is live.
- **Merging `8a4415e` newly requires `shell_exec=true` for stdio MCP and `net=true` for http/sse**, so any
  existing user manifest becomes a refusal. Check `examples/` and any wild `.aelix/extensions/` first.
- **#91's 1.25–2.0 session estimate is inflated** — the verifier built the "unprecedented" synthetic
  `.dist-info` in five lines and it resolved first try. The real cost is the acceptance criteria (prove no
  module import during discovery; prove the theme path-fence holds inside site-packages), still unsized.
- **The Copilot OAuth ToS question (#86, #111 B-10) is an external dependency.** `README.md:94` marks
  Enterprise supported while #85 records live verification on individual and Business seats only. If the
  announcement leads with Copilot seat reuse and the terms forbid it, the launch narrative collapses.
- **This repo's demonstrated pattern is 2+ self-inflicted defects per sprint under adversarial review** (the
  first RPC commit shipped two regressions; W1-A shipped PATH hijacking and credential argv leakage;
  #114/#118 shipped two more). Treat every size above as p50, not p90.

---

## 9. Backlog disposition (summary)

**Beta blockers:** B1–B7 above.
**Beta should:** #91 (fix *or* re-scope), child spend visibility, child transcript, pyright gate, the five
inert `--help` flags, `/reload` output, **PR #119** (first external contribution, +13/-7, mutation-tested),
agent-profile / MCP / RPC docs, `--offline` wording, rg/fd disclosure, `release.yml` tag guard, `py.typed`,
advisory `[capabilities]` table, the real-model self-extension probe, the small honesty corrections.
**Promoted out of defer by the critic:** #122.

**Explicitly post-beta:** Windows (#103-#109, #46, #110) · #91 if re-scoped · #16 remainder + #123 (the owner
left richer orchestration to extensions; #123 itself opens "Not a beta item") · enforcing the seven unenforced
capabilities · #14 loop governor + #52 (both honestly disclosed under Known limitations) · #101 full offline
docs · #30 / #47 / #45 / #68 / #63 · the TUI polish backlog (#58, #59, #48, #42, #41, #40, #38, #37, #27, #25)
· #83 + #69 (merge #69 into #83) · #73 PyPI Trusted Publishing (the beta tag is hyphenated so the publish job
never starts) · #120 · #34 / #29 / #26.

---

## 10. Landmines for whoever picks this up

- **`main` moves often** — it moved `c33e867` → `c3af0a3` → `0c9da7d` during this audit alone. `git fetch`
  and re-check before branching.
- **Do not open a public issue for B3.** Private Security Advisory until patched.
- **Do not land #91 before B4's capability gate.**
- **Do not add a `windows-latest` CI leg before writing Windows-asserting tests** — the 12 existing `win32`
  markers are all subtractive `skipif`, so the leg would manufacture false confidence.
- **A casefold-only fix for B5 is the worst outcome** — it looks fixed and leaves the string-shape family open.
- **`defaultProjectTrust` is camelCase on disk; `features.agents` is snake_case.** The two settings use
  different conventions and a test already false-greened on it.
- Worktree pytest needs `PYTHONPATH` (the venv's editable installs point at the MAIN tree):
  `PYTHONPATH=<wt>/packages/aelix-coding-agent/src:<wt>/packages/aelix-agent-core/src:<wt>/packages/aelix-ai/src`
- **Never run anything alongside the full suite** (2 cores → exit 144 and induced flakes).
- Full raw dossier (8 audits + 8 verifications + synthesis + critique):
  `.../workflows/wf_10ecba4d-988/journal.jsonl`.
