# 0195. Registry-aware startup model resolution (#98) + OS trust store & cause-preserving TLS diagnostics (#99)

Status: Accepted
Date: 2026-07-14

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
(#98 is a DIVERGENCE from pi, not a gap in it: pi's `findInitialModel` resolves
against the live available-model set. The `truststore` half is aelix-native by
necessity — pi is Node, which reads the OS trust store natively, so there is no
pi code to mirror; parity is of BEHAVIOR, not of source.)

## Context

Two user reports on the same build (`0.1.0b1`, run from a source checkout).

**#98** — every fresh session raised at the FIRST user message, regardless of
which model was configured, and only `/model` cured it:

```
✖ No provider registered for api='unknown'. Sprint 6a ships the Anthropic adapter ...
```

**#99** — GitHub Copilot returned a bare `Connection error.` plus `c153fbe`'s TLS
hint. The reporter read it as an auth problem.

### #98 root cause

`api='unknown'` is not a failed resolution — it is `Model`'s dataclass default
(`streaming.py:167`), i.e. a `Model()` built without `api=`. `resolve_model`
consulted ONLY the build-time bundled catalog (35 providers) and never the live
`ModelRegistry`; a catalog miss fell to a bare `Model(id, provider)` that omitted
`api` and `base_url`. Nothing gated it: the banner prints `id`/`base_url` but
never `api`, so launch looked healthy, and the graceful guard at `entry.py` was
scoped to `if app_mode in ("print","json")`. `rg is_runnable .../cli/` returned
ZERO hits — the gate existed only on the two `/model` paths, which is precisely
why `/model` cured the session (the picker hands a live registry `Model` straight
to `set_model`, bypassing `resolve_model` entirely).

Three inputs reached the bare return, all reproduced:

- **(A)** empty provider — `defaultModel` persisted without `defaultProvider`.
- **(C)** `--model <id>` with no `--provider`: the seed guard was
  `if parsed.model is None and parsed.provider is None:`, so an explicit
  `--model` permanently suppressed the persisted `defaultProvider` — contradicting
  the block's own stated intent ("CLI > settings; we only fill the gap").
- **(D)** a NON-EMPTY but uncatalogued provider (a `models.json` custom provider,
  or an extension `register_provider`). The registry knew it; `resolve_model`
  never asked. Gating on `not model.provider` does NOT catch this.

The divergence is structural: pi's `findInitialModel` (`model-resolver.ts:637`,
SHA `734e08e`) is a 5-priority cascade over the live available set. Its faithful
Python port EXISTS and is fully tested — `core/model_resolver.py:649` — with ZERO
production callers. CLI startup used an ad-hoc catalog-only resolver instead.

A latent credential defect sat on the adjacent line. The sibling-api backfill
(added by `df6e0d9`, the #71 fix) did `api=siblings[0].api` for an uncatalogued
id under a known provider. `github-copilot` spans THREE apis and its first
sibling is `claude-haiku-4.5` → `anthropic-messages`, with `base_url` omitted.
`providers/anthropic.py` does `base_url=model.base_url or None`, collapsing `""`
to the AsyncAnthropic default host — so a **GitHub Copilot OAuth bearer was sent
to `https://api.anthropic.com`** (verified: `AsyncAnthropic(base_url=None).base_url`
is that host). Tracked as #100.

### #99 root cause

Not a code bug. The hint's detection is narrow and correct — `_TLS_MARKERS` holds
cert-specific substrings only, so a 401 provably cannot reach the TLS branch
(`str(openai.AuthenticationError(...))` is `'Error code: 401 - Bad credentials'`).
Every Copilot host was live-probed and serves a valid cert, so the known
wrong-host defect cannot produce a TLS error either. The reporter hit a genuine
certificate-verification failure; "인증" is ambiguous in Korean (auth vs certificate).

But three product defects made it undiagnosable:

- aelix was **certifi-only with no CA configuration surface at all**:
  `rg "verify=|trust_env|ssl_context|truststore" packages/aelix-ai/src/` → NO HITS
  across all 10 client sites. A corporate root CA installed system-wide is
  invisible to aelix while VS Code (Node/Electron → OS store) keeps working on the
  same machine. This is the Python-vs-Node trust split, and it fully explains the
  report with no aelix routing bug.
- `describe_provider_error` **discarded the entire cause chain** — `_causes()` was
  computed and used only as a boolean. The cert's `verify_message` names both the
  reason AND the host and was thrown away (`rg "verify_code|verify_message"` → zero
  hits). The user saw only the constant `Connection error.`
- The hint was **cause-blind**: it fired on every X509 verify failure while
  asserting exactly one cause, so hostname-mismatch and expired-cert failures got
  identical, misleading `SSL_CERT_FILE` advice.

## Decision

### #98

1. **`resolve_model` gains `registry` and `default_provider` parameters.** After a
   catalog miss it consults the live `ModelRegistry`, which knows `models.json`
   custom providers and (post-`bind_model_registry`) extension models.
2. **Ambiguity stays UNRESOLVED on purpose.** A bare id is accepted only when
   exactly one provider serves it. The bundled catalog alone serves `gpt-5.4` from
   seven providers; guessing an owner would dispatch the turn — and the
   credentials with it — to whichever sorted first. That guess IS the #100 leak.
   Trigger (A) therefore still yields `api='unknown'`, now gated with an
   actionable message rather than an internal error.
3. **The sibling backfill adopts only an UNANIMOUS sibling `api`**, and carries
   `base_url` only when it too is unanimous. Five catalog providers span several
   apis and every one of them includes `anthropic-messages`. Unanimity means the
   adapter choice cannot cross vendors; pinning the host explicitly means no SDK
   default can be reached. Closes #100.
4. **`default_provider` is passed as its own lowest-precedence argument, never
   merged into `parsed.provider`.** Writing the persisted provider into the flag
   would impersonate an explicit `--provider`, hijacking both the
   `<provider>/<model>` shorthand and the OpenRouter-env path — each gated on that
   flag's ABSENCE — and silently rerouting the turn to the persisted vendor.
   `resolve_model` owns the whole precedence ladder; callers pass sources
   separately and never pre-merge.
5. **`is_runnable` startup gate**, placed AFTER `bind_model_registry` so extension
   providers are visible. Interactive **warns** and points at `/model` (the cure,
   so a warning beats refusing to launch); print/json **hard-refuses**. The
   pre-existing print/json guard keyed on `not turn_model.provider`, which misses
   trigger (D).
6. **`is_runnable` additionally refuses a hostless model** whose api IS supported,
   because the adapter would resolve it to its SDK's first-party vendor host. This
   guards the live vector `_registry_lookup` preserves by design: an extension
   `register_provider` model may legitimately omit `base_url`, and it must be
   returned verbatim (dropping it to "no match" would let the sibling backfill
   stamp the catalog's api over the one the extension declared).

### #99

7. **Adopt `truststore`**; `truststore.inject_into_ssl()` once at CLI startup,
   best-effort (a missing wheel / unsupported platform / unreachable platform
   store must never block launch). Process-wide, so it covers all 10 client sites
   without touching any of them. CLI-entry only — a library import must never have
   this side effect.
8. **`_os_trust_store_active()` reads the LIVE binding** (`ssl.SSLContext.__module__`)
   rather than a flag recorded at injection time. Injection is best-effort and
   embedders never run it, so a startup flag can lie; class identity cannot.
9. **The untrusted-issuer hint is worded from that live answer, never asserted.**
   Telling a certifi-only process's user "aelix trusts your OS store" dead-ends
   exactly the #99 reporter: they have ALREADY installed the CA system-wide — that
   is why VS Code works for them — so they would be told the fix is the thing they
   already did, while the one remedy that works certifi-only sits behind a
   conditional they would read as not applying.
10. **The cause chain is preserved**, so `Connection error.` never appears alone;
    and the hint branches on OpenSSL `verify_code`: 18/19/20 → private-CA remedy;
    62 (hostname mismatch) → the chain verified, so no CA advice; 9/10 (validity
    window) → check the clock. Codes 62/10/9 all still stringify with "certificate
    verify failed", so `_TLS_MARKERS` cannot tell them apart — the code can.
    `verify_code` is read with `getattr` defaults: it exists ONLY on
    OpenSSL-raised errors, so an absent code keeps today's generic advice and
    synthetic test errors do not silently disable the branch.
11. **`enable_github_copilot_model` propagates a deterministic `TransportError`**
    (TLS/DNS/connect/proxy) instead of swallowing it — that swallow is what let
    `/login` report success on a network where every turn dies at message 1, and is
    the best explanation for why #99's reporter read a TLS failure as auth. A
    `TimeoutException` is deliberately EXEMPT despite being a `TransportError`
    subclass: it is transient, and failing `/login` over one discards a completed
    device flow the user must redo by hand. Only a retry-proof failure is worth
    that price. `enable_all_...` re-raises only when EVERY model was unreachable.

## Consequences

- #98's three triggers are closed by one change, though only (C) and (D) RESOLVE;
  (A) is correctly gated instead. #100 is closed on two layers: `resolve_model` no
  longer produces the model, and `is_runnable` refuses a hostless one even if it did.
- Verified end-to-end: with `telnaut` declared in `models.json`, the real CLI went
  from the internal `api='unknown'` error to resolving `openai-completions` +
  the declared host and reaching a genuine DNS lookup.
- `tests/cli/test_runtime_bootstrap.py`'s `test_resolve_model_unknown_provider_is_bare`
  ASSERTED the bug as intended behaviour, justified by a comment repeating the
  false "entry-point guard saves it" premise. The suite was green precisely because
  nothing exercised the TUI startup path. Corrected here, along with that premise
  where it appears in `runtime_bootstrap.py`'s comment and docstring.
- Gate: 5405 passed, 1 skipped, 0 failed; `ruff` clean.

### Known limitations / follow-ups

- **`find_initial_model` is still dead code.** This moves `resolve_model` toward
  pi's behaviour without the wholesale swap, which stays the pi-faithful endgame.
  The ordering constraint that forces the split gate remains: extension providers
  reach the registry only at `bind_model_registry`, AFTER the harness is built.
- **The interactive gate is advisory.** It warns and is not re-gated at turn time,
  so on that path the credential-egress guard is a loud warning rather than a
  hard stop. Deliberate — `/model` is the cure — but worth revisiting.
- **#99's field fix is UNVERIFIED.** truststore was verified structurally and the
  diagnostics against locally generated certs with real handshakes; "it will pick
  up the reporter's corporate CA" is sound inference, not evidence. Needs the
  reporter's `verify_code`: 18/19/20 confirms; 62/10 reopens the routing question.
- **`is_runnable` fails OPEN** when no adapter is registered, so every gate depends
  on `register_providers()` having run. Safe rather than a hole — an empty set
  means no adapter exists and dispatch would raise before any turn could stream.
- The 42 `azure-openai-responses` catalog models ship `base_url=''` and are
  currently blocked only because their api has no adapter. Registering one would
  make the new hostless guard block all 42 until a per-deployment endpoint story
  exists.
- #85 §B's premise that `SSL_CERT_FILE` means "no code change needed" is superseded
  by decision 7: requiring an env var the incumbent tool does not require is itself
  the defect.
