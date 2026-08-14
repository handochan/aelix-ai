# ADR-0219 — The prompt's tool list is derived, its paths share one escape, and the user picks the write target

- **Status**: Accepted (2026-08-14 — landed with the implementation)
- **Issues**: #120, #167, #161, #162
- **Supersedes nothing.** Closes the residual ADR-0218 §2 recorded.

---

## 1. The framing this batch inherited was wrong, and the measurement is what corrected it

The handoff written at `e087378` presented #120 as a choice between three shapes:

- **(a)** make the sentence flag-aware only,
- **(b)** move the enumeration into a chunk computed after tool registration,
- **(c)** delete the enumeration, noting "pi has an equivalent sentence, so this is a
  pi-divergence question".

**None of the three is pi's design, and the third premise is backwards.** Fetched
2026-08-14, `packages/coding-agent/src/core/system-prompt.ts` takes the tool list as a
PARAMETER:

```ts
const tools = selectedTools || ["read", "bash", "edit", "write"];
const visibleTools = tools.filter((name) => !!toolSnippets?.[name]);
const toolsList = visibleTools.length > 0
    ? visibleTools.map((name) => `- ${name}: ${toolSnippets![name]}`).join("\n")
    : "(none)";
```

and closes the open end with one sentence:

> In addition to the tools above, you may have access to other custom tools depending on
> the project.

`agent-session.ts:1023-1056` feeds it (`_rebuildSystemPrompt`), `:928-943`
(`setActiveToolsByName`) rebuilds on every active-set change, and `:2553` makes
`_refreshToolRegistry` end in that same call — so a `registerTool` at runtime rebuilds
too. `promptSnippet` / `promptGuidelines` are declared on `ToolDefinition`
(`coding-agent/src/core/extensions/types.ts:449-459`), i.e. on the extension-facing type,
so extension tools can opt in exactly like built-ins.

**So aelix's hard-coded literal was the divergence.** Porting pi's mechanism is parity,
not a new invention, and ADR-0216's rule ("pi's behaviour is not overridden without a
reason that survives every upstream sync") points at it rather than away from it. The
owner chose the full port on that basis.

The one measurement that made option (c) tempting is still true and is recorded here so
nobody re-derives it: the prose is **strictly redundant and strictly less informative**
than the wire. `read`'s prompt text was `read (read a file)`; its `description` — sent by
every adapter on every request (`openai_completions.py:749`, `openai_responses.py:303`,
`_google_shared.py:795`, `openai_codex_responses.py:254`) — is 298 characters and carries
the offset/limit truncation semantics. The seven built-ins are 5460 bytes of JSON per
request. Deleting the prose would have been defensible on redundancy grounds and wrong on
parity grounds; pi keeps a *shorter, different* text (`Read file contents`) precisely
because it is not the description.

---

## 2. What was built

### The tool list is derived from the active set

- `Tool` gains `prompt_snippet: str` and `prompt_guidelines: tuple[str, ...]`
  (`aelix_ai/tools.py`). Declared on `Tool` rather than on a separate definition type
  because aelix has none — `ExtensionAPI.register_tool` takes a `Tool` directly, so this
  is the one place a single rule reaches built-ins and extension tools alike.
- The seven built-ins carry pi's snippets **verbatim**. Two aelix tools that the old
  literal omitted now carry their own: `aelix_status` and `agent`. The issue's complaint
  that the sentence "omits `agent` and `aelix_status`" is answered by them being able to
  appear at all.
- `build_system_prompt(cwd, *, tools=None)` emits pi's bulleted block, `(none)` for the
  empty set, and pi's disclaimer sentence. `tools=None` means "the caller did not scope
  one" and yields `(none)` — pi's own default, and the fail-honest direction: the old
  literal over-claimed, an unscoped call now under-claims.
- `cli.entry._resolve_system_prompt` takes `tools` as a **required keyword argument**.
  Both production callers go through it. This is not defensive: the last time one of the
  two silently omitted an argument the other passed (`skills=`, #115), `/agents use`
  shipped a prompt that told the model about none of the profile's skills while
  `/skills` still listed them, and nothing visible broke.

### Guidelines follow their tool

Two of aelix's static guideline bullets named `read` and `bash`, so a `--no-tools` run was
still instructed to "read files" and to "verify your work … via bash". They moved onto
those tools' `prompt_guidelines`. Three of the four "Converging to an answer" bullets are
about tool-call loops and are now emitted only when there are tools to call.

Pi's four `edit` guideline bullets were **deliberately not ported**: aelix's `edit`
description already carries the same three rules and goes on the wire every request, so
re-emitting them would bill ~440 chars a turn for facts the model already has. Pi's `bash`
guideline was not ported either — it names `PI_*` environment variables, which do not
exist in aelix, and handing the model a namespace that is not there is the exact class of
overclaim this issue is about.

### The rebuild seam

Pi keeps the tool registry and the prompt builder in one class. Aelix split them: the
registry landed in `aelix_agent_core`, the prompt text stayed in `aelix_coding_agent`. The
join is a callback, `AgentHarnessOptions.system_prompt_rebuilder`, invoked from
`_action_set_active_tools` (pi's `setActiveToolsByName`) and from
`_refresh_extension_tools` (pi's `_refreshToolRegistry` — which reaches its rebuild for
free by ending in the setter; aelix's assigns `active_tool_names` directly to dodge the
validator, so the call is explicit). `cli.entry` supplies the callback; the kernel never
learns what a prompt says.

Three properties of the callback are load-bearing:

- It returns the **complete** prompt, not a base for the kernel to re-join.
  `/agents use` recomputes the skills catalog and the `AGENTS.md` chunk from live state,
  so a kernel-side re-join would silently revert them.
- It reads the skills **through a provider**, never a captured value. Pi's
  `_rebuildSystemPrompt` re-reads `_resourceLoader.getSkills()` on every call
  (`agent-session.ts:1046`) for the same reason: a snapshot would re-emit the previous
  profile's catalog on the next tool change.
- It closes over the shared mutable `Args`, so `--system-prompt` and a profile's
  `system_prompt: replace` are honoured for free — pi's `customPrompt` branch ignores
  `selectedTools` too, and a user who supplied their own prompt is not asking us to edit
  it.

Failures are swallowed with a debug log. The callback touches the filesystem (the docs
signpost globs, the extension signpost stats two files) and its callers are a tool
registration and an extension action; letting an `OSError` abort `register_tool` would
turn cosmetic staleness into a broken session.

`AgentProfileService.use` now composes the prompt **after** `set_active_tools` rather than
before it. It used to build an identity from a tool set that was about to change.

### One path-shaped escape (#167)

Five prompt sites emitted paths under two rules and neither was right for a path:

| site | was | measured failure |
|---|---|---|
| docs directory, `cwd_abs`, both write targets | `_escape_text` (`&`, `<`) | install under `/tmp/amp&test_…/r&d/src` → prompt emits `…/r&amp;d/…`, `is_dir()` **False** while the real one is **True** |
| the two `_package_pointer` targets | **raw** | a `<` in an install path was not neutralised at all — the ADR-0217 fence channel `a079188` closed for the cwd was open from the install side |

`_safe_prompt_path` is now the single rule: strip C0/DEL/C1, escape `<`, **leave `&`
alone**. A tag needs `<`; with every `<` escaped no interpolated path can open
`</project_instructions>` or `<available_skills>`, which is the whole structural guarantee
ADR-0217 rests on. `>`, `"`, `'` and `&` cannot start a tag, so escaping them buys nothing
and each one mangles a real path — and `&` is legal in a POSIX path component that
`git clone` recreates faithfully. The guard lives **on** `_package_pointer` rather than at
its two call sites, so a third pointer cannot be added unescaped.

The `<name>.py` / `<name>.md` placeholders are assembled outside the escape: their `<` is
deliberate prose, and escaping it would hand the model `&lt;name&gt;.py` as a filename.

### The user picks the write target (#161)

The self-extension block listed the global target first — for the measured reason that the
project-local tier is trust-gated and fails **silently** — and said nothing about who
decides. With no instruction, "listed first" is what the model acted on. It now asks, and
the ask carries a fallback because this block is emitted for `-p` / `--mode json` /
`--mode rpc` and for delegated subagents, where the honest reading of a bare "ask the
user" is "refuse". The fallback names the global target: the tier that cannot fail
silently when there is nobody to answer a trust prompt either.

This is issue #161's **shape 1** (prompt-only). Shape 2 — a real `/extension new <name>`
command — is the escalation if a live model ignores the ask, and that is a decision to be
taken on the measurement rather than in advance.

---

## 3. #162 — the paths stay absolute, and the documentation stops being partial

The base prompt emits **six** absolute paths on every turn and one of them is
`$HOME`-derived, so the OS account name reaches the provider on every request. Measured:
`- Working directory: …`, the two extension write targets, the two package pointers, and
the bundled docs directory; `username present: True`.

`~` was measured to work through **all six file tools** — `read`, `write`, `edit`, `ls`,
`find`, `grep` — because `expand_path` is pi-parity and is `os.path.expanduser`, which is
platform-independent. It was a real, cheap option.

**It was not taken.** The owner's call, on two grounds:

1. **Parity.** pi emits `getReadmePath()` / `getDocsPath()` / `getExamplesPath()` and
   `Current working directory: …` as absolute paths with no `~` anywhere. Abbreviating
   would be the divergence, and ADR-0216 governs.
2. **Windows.** The owner raised it and the measurement bore it out. `~` works in the file
   tools on Windows, but `_resolve_shell_win32` resolves the `bash` tool to
   `pwsh` → `powershell` → `cmd.exe` (`tools/bash.py:176-198`), and none of those expands
   `~` in an argument to an external program. One prompt path *is* handed to bash — the
   `grep -nE 'def (register_|on\()' <api.py>` hint — so a blanket `~` would have created
   a new Windows failure in exchange for a privacy gain that Windows would not get anyway
   (`C:\Users\<name>` is if anything the more identifying form).

What changed instead is `SECURITY.md`, which previously named only the working-directory
line and did not mention the username at all. It now enumerates all six, states the
username disclosure plainly, records that no flag suppresses them and why they are
absolute, and points at the one real mitigation (`before_provider_payload`, verified to
exist at `harness/hooks.py:644`). ADR-0217's rule — we do not assert a boundary we are not
keeping — applies to documentation too: a partial list reads as a complete one.

---

## 4. Cost, honestly

`build_system_prompt` with the seven built-ins went **4051 → 4615 chars (+564, +13.9%)**,
paid on every turn. The breakdown: the bulleted list is longer than the inline sentence it
replaced, the disclaimer sentence is ~108 chars, and `write`'s guideline is new. A
`--no-tools` run is **3547**, i.e. smaller than before, and a `--tools read` run is 4015 —
the cost scales with what the session actually has, which the literal never did.

The signpost prose budget rose **1320 → 1600** (measured 1520), bought by #161's ask
clause and its fallback, following that test's standing rule that every raise cites the
finding that paid for it.

There is a **wall-clock** cost too, and it is worth naming because pi does not pay it.
One rebuild is **5.0 ms** measured on this repo as the cwd — it re-walks for `AGENTS.md`
and re-formats the skills catalog, because `discover_context_files` reads the filesystem
on every call while pi's `_rebuildSystemPrompt` reads a pre-loaded `ResourceLoader`.
`register_tool` triggers one rebuild **per tool**, so an extension registering ten tools
spends ~50 ms at load. That is inside the noise of extension import itself and is not
worth a cache today; if a pack ever registers hundreds of tools it becomes one, and the
fix is to give the rebuild a loader rather than to skip it.

---

## 5. What is NOT closed

- **No real model has been run against any of this.** Every number here comes from real
  code building real prompt text, real `tool.execute()` calls, real adapter payloads and
  the real harness factory — none from a provider turn. `cli/agent_context.py`'s module
  docstring is binding on exactly this point: rewordings must re-run the probe, not merely
  sound safer, and #161's own issue text makes a live probe the gate for shape 1. The
  batch's live probe is a separate, explicit step; until it runs, **#161 stays open**.
- **#101's live-model completion criterion** is still unverified and is covered by the
  same probe.
- **`/extension new <name>`** (issue #161 shape 2) is unbuilt by design, pending that
  measurement.
