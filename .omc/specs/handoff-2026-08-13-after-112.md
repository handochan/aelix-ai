# Handoff — after #112, 2026-08-13

Written at main `3077b01`. Every claim below is marked **[measured]** or
**[read-only]**. That distinction is not decoration: the handoff *this* session
inherited (`handoff-115-155-156.md`) carried three claims that turned out to be
false, and each cost real time. Do not trust an unmarked assertion here, because
there are none.

---

## State

Main went `02f9a8f` → `3077b01` this session. Suite **8375 → 8439** passed / 1
skipped, `ruff` clean, type gate **249 files / 0 errors**.

| commit | what | CI |
|---|---|---|
| `871a6be` | #115 + #155 + #156 (ADR-0214) | ✅ |
| `c0f905a` | a regression `871a6be` itself shipped | ✅ |
| `884e686` | #147 retry-interrupt (ADR-0215) | ✅ |
| `74c0fa8` | #158 + #160 | ✅ |
| `d6b861c` | #159 | ✅ |
| `3077b01` | #112 pi-parity (ADR-0216) | was in_progress at handoff — **check it** |

**79 issues open** [measured — and note `gh issue list` defaults to 30, which is
how this session first reported "59"].

### Worktrees, all clean

- `/workspaces/aelix-147` @ `3077b01` (`fix/112-pi-parity-trust`)
- `/workspaces/aelix-honesty` @ `d6b861c` (`fix/158-160-shipped-artifacts`)
- ⚠️ `/workspaces/aelix-ai` (the owner's main checkout) is still at `02f9a8f`
  and needs `git pull`. Do not edit it — the owner runs concurrent sessions.

---

## Recommended next work, in order

1. **#121** — AGENTS.md project-trust policy. Needs an ADR, not a sprint. It now
   carries a finding this session attached as a comment: an untrusted
   `AGENTS.md` can **forge the #115 skills catalog** [measured: 2
   `<available_skills>` tags, forged block first, attacker-chosen `<location>`
   that the real catalog's preamble tells the model to `read`]. The forging
   vocabulary did not exist before `871a6be`, so this session created the
   amplification. Bounded — AGENTS.md is already an arbitrary-instruction
   channel by design — but it is exactly what #121 exists to decide.
2. **#101** — offline self-help. Unblocked by #115: the channel and a packaged
   skills tier both exist now, so the bundled `aelix-help` skill has somewhere
   to live [measured: two skills ship in a real `uv build` wheel].
3. **#161** — filed this session. The user, not the #117 signpost, should choose
   global vs project-local for a new extension. Sizing is already in the issue.
   Matters *more* after ADR-0216, because project-local trust now persists.
4. **#120** — the system prompt hardcodes 7 tool names. `cli/skills_prompt.
   skills_catalog_visible` already computes "is `read` active" and is a usable
   foundation.
5. **#157**, then the Windows track (#103/#105/#107/#108).

**#147 is deliberately left open.** The interrupt loss is fixed and live-verified,
but the turn abort is *cooperative* — a provider holding the socket still ties up
the request until the transport gives up. That residual is the issue's original
proxy scenario.

---

## Traps this session paid for. All three are the same trap.

**The verification tool shared the defect with the thing it verified.** Three
times in one session:

1. `/agents use` lost the #115 skills catalog, and the whole test file stayed
   green because `expected_prompt_for` — the *double* for production — omitted
   `skills=` exactly as production did. Broken compared against broken.
2. #147's first live tmux rig **passed against the unfixed build**. Cause
   [measured]: the `openai` SDK carries `DEFAULT_MAX_RETRIES = 2` and the adapter
   sets it client-level, so **the SDK absorbs 5xx before aelix's retry loop sees
   anything**. The trigger that actually reaches aelix is one the SDK does *not*
   retry but `_RETRYABLE_ERROR_PATTERN` does — a 200 whose SSE body ends with no
   `finish_reason`.
3. The `run_tui` stub in `test_entry_router.py` would have swallowed a new kwarg
   silently under `**kwargs`. It takes the kwarg explicitly instead, so a future
   kwarg breaks it once, on purpose.

**Rules that follow, and they are cheap to obey:**
- A passing live gate proves nothing until the same gate has been run against
  the **unfixed** build.
- A double that omits what production omits proves nothing.
- Do not give a test double `**kwargs` for a signature you want pinned.

### Other landmines [all measured]

- First-run onboarding **eats tmux keystrokes** in a fresh agent dir. Send
  `Escape` first or your input lands in a picker's filter.
- A non-streaming stub provider makes the TUI retry 3× and turns the next typed
  line into a *steering* message. Stubs must emit SSE for TUI paths.
- `models.json` → `"models"` is a **list** of `{"id": ...}`, not a dict. And
  `register_provider` takes a `ProviderConfigInput` (with `models: dict[str,
  Model]`), not a dict.
- `/login`'s API-key picker lists `ENV_API_KEYS` ∪ **extension**-registered
  providers — models.json providers do **not** appear there.
- `parse_profile` needs `scope=`; `ProfileScope = Literal["bundled", "user",
  "project", "explicit"]`.
- A stray real API key in the environment made a `sk-fake` probe hit the live
  API. Use a stub provider, or a path that fails before the API.
- Worktree pytest needs `PYTHONPATH` set to that worktree's four
  `packages/*/src`, or it imports the main checkout's copy.

---

## Decisions the owner made this session, so they are not re-litigated

- **#115 scope**: catalog **and** `/skill:<name>`, prompt templates wired onto
  the same machinery, and **two** starter skills shipped.
- **#112**: **pi parity, wontfix.** A gating fix was built and measured working
  and was declined. Reason recorded in ADR-0216: pi keeps this surface
  permissive deliberately, and diverging would have to be re-justified at every
  upstream sync.
  - Correction worth carrying forward: ADR-0203 and ADR-0214 are **not**
    precedent for diverging from pi on security. ADR-0203 covered a surface pi
    does not have; ADR-0214's skills gate was pi *parity*
    (`trust-manager.ts` lists skills). #112 would have been the first time pi's
    own behaviour was overridden. This session initially argued the opposite and
    was wrong.
  - Accepted residual: implicit trust now **persists** where it used to die with
    the process. `/trust` shipped in the same commit as the human's remaining
    "no".

## Open owner question

None blocking. #121 needs a policy decision when it is picked up (four options
are listed in the issue), and this session's forging finding should be read
alongside them.
