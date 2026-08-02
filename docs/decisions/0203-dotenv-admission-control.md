# 0203. A cwd `.env` carries credentials, and a short checked list — nothing else

Status: Accepted (2026-08-01)
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
| `OPENROUTER_BASE_URL` | `runtime_bootstrap.resolve_model` → `Model.base_url` | traffic (Authorization header + full prompt) redirected to a 127.0.0.1 listener |

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
  earlier draft of this bullet said it did.
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
   patched. `GOOGLE_API_KEY` (admitted; the registered name is `GEMINI_API_KEY`)
   looks like the same shape; that one was **not** measured end-to-end and is not
   claimed.
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

