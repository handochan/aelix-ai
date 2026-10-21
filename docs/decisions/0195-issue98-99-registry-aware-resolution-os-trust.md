# 0195. Registry-aware startup model resolution (#98) + OS trust store & cause-preserving TLS diagnostics (#99)

Status: Accepted — amended by ADR-0249 (2026-09-30, #344): Decision 4 and Known-limitations bullet 1; amended by ADR-0250 (2026-10-02, #362): Decisions 2 and 4; amended 2026-10-20 (#411): Decision 10 — see its amendment below; dated note 2026-10-22 (#551): the same attribution decides the retry; amended 2026-10-22 (#556): the remedy's last line names a copy of certifi's bundle — see the amendment at the end
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

   **#362 amendment 2026-10-02** ([ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)):
   pi's order replaces the guess with a rule, not with a guess. A bare id several
   providers serve goes to settings `defaultProvider` when it is one of them (and,
   while the user holds a credential of their own, only when that credential
   authenticates it or the user defined it — Decision 4's note), else
   to the **one** of them the user holds a credential of their own for (exported,
   `/login`, `models.json` — never a cwd `.env`'s), else — when several of them
   are the user's — to the **sole user-defined** one among those (ADR-0250's rule
   U: a gateway the user defined that lists `openai/gpt-4o` verbatim keeps it),
   else it is pi's error listing
   them (`Model "gpt-4o-mini" is ambiguous across providers: …`), refused before
   any request instead of held as `api='unknown'`. "Unresolved on purpose" stands
   for every case where no such credential decides.
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

   **#344 amendment 2026-09-30** ([ADR-0249](0249-a-model-openrouter-cannot-serve-is-resolved-before-openrouter-from-env.md)):
   the ladder gains a rung in front of the OpenRouter-env path. With
   `OPENROUTER_API_KEY` set and no `--provider`, a `--model` whose prefix names a
   user-defined provider (models.json custom, a built-in re-pointed by a
   models.json `baseUrl`, an extension `register_provider`), an id exactly one
   such provider serves, or a prefix naming a catalogued provider that is not an
   OpenRouter namespace, is resolved inside that provider first — from
   configuration only, no credential consulted. The slash prefix is matched
   case-insensitively, and a catalog hit adopts a models.json provider-level
   `baseUrl`. Everything this decision says about `default_provider` stands.

   **#362 amendment 2026-10-02** ([ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)):
   the ladder is pi's `resolveCliModel` order now — explicit provider, known
   prefix, the whole string as an id, the swap to an authenticated raw match, the
   raw fallback, a custom id — with no OpenRouter-env rung at all: an OpenRouter
   key reaches an id this build cannot place only through guard 2 (an OpenRouter
   credential of the user's own). `default_provider` stays its own argument and
   the weakest signal: it breaks a bare-id tie and homes a bare id nothing else
   claims; merged into `parsed.provider` it would still impersonate `--provider`
   and switch the whole order off. Its value is the MERGED settings, which a
   project `.aelix/settings.json` sets, so (the fourth #362 verification, ADR-0250
   §2.1) while the user holds a credential of their own it counts in both arms only
   for a provider that credential authenticates or a provider the user defined.
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

    **#411 amendment 2026-10-20** (below): truststore's OS verifiers set
    `verify_code` too, and their codes for a wrong host and for the validity
    window branch the same way; a TLS failure behind httpcore's `from None` is
    attributed by the verification error the link carries, not only by text.
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
- Verified end-to-end: with `selfhosted` declared in `models.json` [this provider id
  was renamed after this ADR was written; the run itself used the old id], the real CLI went
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
  **#344 amendment 2026-09-30** ([ADR-0249](0249-a-model-openrouter-cannot-serve-is-resolved-before-openrouter-from-env.md) §2.3):
  no longer true for the launch model — `_build_harness_options` binds the
  registry right after `discover_and_load_extensions`, on every (re)build, and
  resolves the model after that (pi's order), so `--model <ext-provider>/<id>`
  and `--provider <ext-provider>` select an extension provider at launch. The
  post-build `is_runnable` gate stays where it is; a provider an extension
  registers in `session_start` still arrives after the resolve (and since #367,
  2026-10-03, is refused as a launch model, as in pi — ADR-0250 §2.11).
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

## Amendment (2026-10-20, #411) — the OS verifier's failures are attributed, and its codes earn the same remedies

**What was wrong.** On macOS and Windows `truststore` verifies the chain with
the operating system, not OpenSSL. The error is still an
`ssl.SSLCertVerificationError`, but its text is the OS's and names none of
`_TLS_MARKERS`, and httpcore re-raises the `ConnectError` that carries it
`from None` (`_sync/connection_pool.py:256`): `__cause__` cleared,
`__context__` cut. Decision 10's attribution walk respects that cut, so it
found only the wrappers, whose text was the OS's too. Measured on macOS 26.6
with truststore 0.10.4, against a local server whose self-signed certificate
no keychain holds, through the real CLI (`aelix -p`, a `models.json` provider,
fake key): `Connection error. — ('“localhost” certificate is not trusted',)`
and no remedy, for `openai-completions`, `openai-responses`,
`anthropic-messages` and `google-generative-ai`, in `-p`, `--mode json`,
`--mode rpc` and the TUI. The chain, measured for the async OpenAI and
Anthropic clients and httpx's sync and async ones: `APIConnectionError → httpx.ConnectError →
httpcore.ConnectError` (`args[0]` is the error, `__cause__` None,
`__suppress_context__` True) `→ SSLCertVerificationError` with `verify_code`
-67843. Decision 10's "`verify_code` exists ONLY on OpenSSL-raised errors" was
false for this path: truststore sets it on both platforms.

**Decision 10 is amended in seven parts** (parts 5–7, and the review-round-2
sentences in parts 1–3, come from the issue's second review round, which held
every remedy to where it is true).

1. *Attribution looks behind the cut for the verification error the link
   carries, and for nothing else.* A link whose `__cause__` is None and whose
   `__context__` is suppressed is attributed to a TLS failure when that
   context is an `SSLCertVerificationError` and the link carries it — holds it
   as an argument (httpcore's `map_exceptions` builds `ConnectError(exc)`) or
   says exactly its text. An unrelated error raised `from None` inside an
   `except` does neither, and the existing row for it stays green. The cause
   TEXT keeps the old walk: a first draft crossed the cut for every error, and
   a refused connection's `Connection error. — All connection attempts failed`
   became anyio's `multiple connection attempts failed (2 sub-exceptions)`
   (measured on `aelix -p`); now it reads as before, in the CLI and the TUI.
   Review round 2: an `ssl.SSLError` carried the same way that is NOT a
   verification error is not attributed, even when its reason names a
   certificate — a TLS 1.3 server that demands a client certificate sends
   `certificate_required` (`TLSV13_ALERT_CERTIFICATE_REQUIRED`, measured
   through httpcore's `ReadError`), and no CA helps a client that sent none.
2. *The OS's untrusted-issuer texts are markers*, for a wrapper that kept only
   text: macOS `certificate is not trusted` (measured; English for a CLI
   process even with `AppleLanguages` `ko-KR`), Windows'
   `which is not trusted by the trust provider` (`CERT_E_UNTRUSTEDROOT`),
   `could not be built to a trusted root authority` (`CERT_E_CHAINING`), and
   truststore's fallback when Windows has no text for the code,
   `Certificate chain policy error 0x800b0109` / `0x800b010a` (the text names
   the code). Only texts that name an untrusted issuer: a wrapper that kept
   only a wrong-host or validity text gets no remedy rather than the CA one.
   Windows' texts are localized; its code is not. Review round 2 dropped two
   generic texts round 1 had listed: truststore's macOS ≤ 10.13
   `Recoverable trust failure occurred` — an untrusted root, a wrong host, an
   expired and a not-yet-valid leaf all give it there, with code 5 (measured
   on macOS 26.6 with that branch forced) — and the bare
   `Certificate chain policy error` prefix, truststore's fallback for any
   code, a wrong host and an expired leaf included.
3. *The OS codes branch like OpenSSL's.* Hostname: 62, macOS -67602
   (`errSecHostNameMismatch`), Windows `0x800B010F` (`CERT_E_CN_NO_MATCH`).
   Clock: 9, 10, macOS -67818 (`errSecCertificateExpired`) and -67819
   (`errSecCertificateNotValidYet`), Windows `0x800B0101` (`CERT_E_EXPIRED`).
   Every other code keeps the untrusted-issuer default, except the verdicts
   of part 6. The macOS codes were measured with a probe CA as the only anchor
   (`load_verify_locations`, which truststore passes to
   `SecTrustSetAnchorCertificates`; nothing was added to a keychain): a
   wrong-host leaf -67602, an expired leaf AND a not-yet-valid one -67818, the
   same leaves unanchored -67843, a leaf whose issuer the server did not send
   -25318 (`errSecCreateChainFailed`). -67819 was never seen. The Windows
   codes are read off `winerror.h` and `truststore/_windows.py` (`dwError`, a
   DWORD, so unsigned): no Windows box ran them. On macOS 10.14 and later the
   codes are negative and on Windows above 2^31, so neither meets OpenSSL's
   0–95. On macOS ≤ 10.13 truststore sets the `SecTrustResultType` instead,
   0–7, which does (5 is also OpenSSL's code for a CRL signature it cannot
   decrypt): that path is recognised by truststore's own texts for those
   results, never by the code (part 6).
4. *The strict split holds.* An OS code is never in the strict-only set or
   79, so it never gets the strict remedy; and it cannot be strict's: with
   the OS verifying, truststore switches OpenSSL's verification off. Measured
   on macOS with Python 3.12.13 and 3.14.5: a chain whose leaf lacks the
   Authority Key Identifier fails a stdlib context with code 85 under strict
   and verifies under truststore with strict on and off.
5. *Text never attributes a chain that carries the HTTP status a server
   answered with* (review round 2). A status — an int `status_code` (openai,
   anthropic), `code` (google-genai), `status` (the Codex adapter),
   `oauth_status_code` (aelix's OAuth sign-in and refresh errors,
   `oauth_http_error`, which `/login` describes), or a `response` carrying one
   (httpx) — means a server read the request and answered it, so a
   certificate phrase in the error's text is the server's words, not this
   machine's verifier. Measured at round 1 through `aelix -p`
   (`openai-completions`) against a plain-`http://` server whose 400 quoted
   the prompt: `… certificate is not trusted …` got the corporate-CA remedy,
   and `certificate verify failed` had got it on every build before #411.
   With any link of the attribution chain answered — the status error, or a
   wrapper raised from it that repeats its text (the anthropic adapter's
   `_AuthError` shape) — no link's text counts, the long-standing OpenSSL
   markers included; a verification error object, which only a verifier
   raises, still does. `oauth_status_code` was added in the narrowed finish
   (2026-10-21): the second review's verify ran the real Copilot device-code
   request against a plain-`http://` 503 whose body was Envoy's report of its
   upstream's `CERTIFICATE_VERIFY_FAILED`, and `/login` printed the
   corporate-CA remedy under it; now none. An error that carries no int
   status is still read by its text (known limitations).
6. *An OS verdict no remedy fits gets none* (review round 2). The failure is
   still attributed (`is_tls_verification_error` is true) and the OS's own
   text is shown, with nothing under it.
   - macOS rejects the certificate ITSELF, whatever its issuer: -67901
     (`errSecCertificateValidityPeriodTooLong`, `is not standards compliant`),
     -67735 (`errSecUnsupportedKeySize`, `is using a broken key size`), -67609
     (`errSecInvalidExtendedKeyUsage`, `is not permitted for this usage`). No
     CA install satisfies them, and round 1's CA remedy named a CA the request
     already trusted (the cross-review's -67901 run had it in
     `SSL_CERT_FILE`). Measured on macOS 26.6 against a probe CA: a 900-day
     leaf -67901 and an RSA-1024 leaf -67735 whether or not the CA is an
     anchor, so "the chain is fine" would be false too; a clientAuth-only
     leaf -67609 once anchored (unanchored, -25318 comes first).
   - macOS ≤ 10.13's results name no cause: truststore's text for each
     `SecTrustResultType` (`Recoverable trust failure occurred` for 5,
     `User specified that certificate is not trusted` for 3, a user's
     explicit "never trust", and the rest of its table) gets no remedy, with
     its code or as text alone.
7. *The printed `SSL_CERT_FILE` example is the platform's own* (review round
   2). It was Linux's `export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`
   everywhere, and since this amendment the CA remedy reaches macOS and
   Windows, where that directory is empty (macOS: following the line
   literally fails with `[Errno 2] No such file or directory`, measured) and
   `export` is no PowerShell command. Linux keeps it; Windows gets
   `$env:SSL_CERT_FILE = "$HOME\.aelix\ca-bundle.pem"`, introduced as
   PowerShell; macOS and other systems get
   `export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"` — the bundle the TLS
   guide's recipe makes (certifi's roots plus the CA). Measured in zsh on
   macOS 26.6: that recipe with the probe CA, then the new line, and
   `aelix -p` reached the server. `sys.platform` is read when the remedy is
   built. No example carries a shell comment: zsh, macOS's default shell, does
   not take `#` as one at the prompt. On Windows the guide makes the bundle
   with `Get-Content … | Set-Content -Encoding ascii` (narrowed finish):
   Windows PowerShell 5.1's `>` writes UTF-16LE, and OpenSSL loads no
   certificate from such a file (`NO_CERTIFICATE_OR_CRL_FOUND`, measured on a
   UTF-16LE copy written by `Out-File -Encoding unicode`). The recipe ran in
   PowerShell 7.6.5 on macOS (the bundle loaded with the probe CA in it), not
   on Windows.

**pi.** pi gives no TLS remedy at all: it appends the cause chain's messages
(`packages/coding-agent/src/utils/tools-manager.ts`,
`packages/ai/src/utils/models-error.ts` @ `fdce48eb4`). Nor does it use the OS
store: nothing in it asks Node for one (no `--use-system-ca`, no
`tls.getCACertificates`), and its one CA setting is `test.sh` passing
`NODE_EXTRA_CA_CERTS` — a PEM file Node adds to its bundled roots — through to
its own test run. The divergence stands (ADR-0235).

**Tests.** `tests/providers/test_tls_os_verifier_411.py`. Every OS row drives
the real httpx, httpcore and OpenAI SDK through truststore's own `SSLContext`,
sync and async; off macOS and Windows, truststore's `_verify_peercerts_impl`
is replaced by one raising exactly what the macOS or Windows backend raises,
so the Linux legs run the same chain. On macOS the real verifier runs the
untrusted, wrong-host and expired rows; on Windows the real verifier runs the
untrusted row — the first measurement of that path, on CI. Five non-TLS
failures through the same stack get no remedy: refused (sync and async, whose
text is pinned), a failed lookup, a handshake that times out, a proxy's 407,
TLS to a plain-HTTP server. Review round 2 adds: a plain-HTTP 400 that quotes
each marker family through the openai and anthropic SDKs, httpx's, the Codex
adapter's and google-genai's status errors, and a wrapper raised from one
(part 5); the verdicts of part 6, simulated on every runner and run on the
real macOS verifier (the 900-day, RSA-1024 and clientAuth-only leaves, and
truststore's ≤ 10.13 branch forced); a TLS 1.3 server that demands a client
certificate (part 1); and the example for each of `linux`, `darwin`, `win32`
and `freebsd14`, each remedy whole within the TUI's bound (part 7). The
narrowed finish adds `oauth_http_error`'s status error for each marker family
and the real Copilot device-code request against a plain-HTTP 503 (part 5).

**Known limitations.**

- Windows is read, not run, except for the CI row above.
- macOS -67901, -67735 and -67609 get no remedy — only the OS's own text:
  aelix does not say what to do about a certificate macOS rejects on its own.
  Those three are the ones measured here. Every other OS code, on macOS or
  Windows, keeps the untrusted-issuer default, so a rejection no CA can fix
  that is not on the list would still get the CA remedy.
- An error that carries no int status is still read by its text. An openai
  `APIError` raised mid-stream whose body `code` is a string (`server_error`)
  is one: certificate words in it still get the CA remedy (measured on an
  error built in-process, not from a live stream).
- On macOS ≤ 10.13 no failure gets a remedy, an untrusted root included: its
  result code cannot say which failure it is. Measured with truststore's
  branch forced on macOS 26.6, not on a real 10.13.
- The printed example's macOS and Windows path is a placeholder the user
  creates; only Linux's names a file the system keeps.
- On Windows a CA from `SSL_CERT_FILE` is tried after the system store, and
  when both fail truststore re-raises the system store's error, so a wrong
  host or an expired leaf under such a CA reads as untrusted (read off
  `truststore/_windows.py`).
- The cause text still shows the error's `str()`, which for truststore's
  one-argument error is a tuple: `('“localhost” certificate is not trusted',)`.
- On a strict interpreter `maybe_relax_strict_for_session` still re-tests the
  host twice for an OS-verifier failure, which can never relax anything there.
- An OAuth token refresh that fails on TLS names no remedy on any platform
  (`OAuthRefreshError` quotes `str(cause)`); that is not this decision's
  boundary.


**2026-10-22 note (#551, ADR-0128 amendment).** Decision 10's attribution also
decides the retry now: a failure it attributes is not sent again by aelix's own
retry layers - the turn, a summary request, an OAuth refresh's `retry_reason`,
the Copilot device-code poll or the Codex adapter's connection retries - unless
the session's strict relaxation answers it (`_error_hints.tls_failure_repeats_on_retry`).
The OpenAI and Anthropic SDKs' own two connection retries inside one request still
run (3 handshakes; ADR-0128). Before, the
turn retried the SDKs' `Connection error.` three times and the TUI showed this
remedy once per attempt. The known limit above (an error with no int status is
read by its text) carries over: such an error with certificate words is not
retried either.

## Amendment (2026-10-22, #556) — the remedy's last line names a copy of certifi's bundle

**What was wrong.** Part 7 of the #411 amendment changed the example line and
kept the line under it, which dates from #99:
``or append the corporate CA to the bundle printed by `python -m certifi`, then retry.``
`python -m certifi` prints the path of the `cacert.pem` inside the installed
certifi package, so the line reads as "add the CA to that file" - the step the
TLS guide warns against, because reinstalling or upgrading certifi replaces the
file and the failure comes back with no clue. Measured in a throwaway venv
(certifi 2026.4.22, Python 3.12.13): a probe CA appended to that file loaded
(121 CAs), and after `uv pip install --reinstall certifi` there were 120. Since
#411 the line reaches macOS and Windows too, where it also did not fit the
example above it: that example points at `~/.aelix/ca-bundle.pem`, which IS the
guide's copy, and the "or" offered certifi's own file as the alternative.

**Decision.** The last line names the guide's copy and warns off certifi's own
file. Part 7's examples, and the remedy's five lines, stay.

- Where the example names `ca-bundle.pem` (macOS, Windows and every system but
  Linux), the line says what that file is:
  ``where ca-bundle.pem is a copy of the bundle `python -m certifi` prints with the corporate CA appended (not certifi's own file, which an upgrade silently reverts), then retry.``
- On Linux, whose example is the system's own bundle, the same copy follows
  `or at`, as the alternative.

The line is 174 characters (157 on Linux), inside the TUI's 200. The guide's recipe sentence,
which read "append the corporate CA to the one `python -m certifi` prints and
point at a **copy**", now says to make a copy of that bundle with the CA
appended and point at the copy, in both copies of the guide. Measured on macOS
26.6 with the real CLI (`-p`, `--mode json`, the TUI at 200 and 90 columns;
truststore active and blocked): the new line is printed whole. Following it in
zsh - the guide's `cat "$(python -m certifi)" corp-root.pem > ~/.aelix/ca-bundle.pem`,
then the printed `export` line - the request to a local server whose leaf the
probe CA signed completed its handshake, and certifi's own file hashed the same
before and after.

**pi.** Unchanged: pi prints no TLS remedy, and its one CA setting,
`NODE_EXTRA_CA_CERTS` (passed through by `test.sh` @ `fdce48eb4`), adds a PEM
file to Node's bundled roots instead of replacing them, so there is no copy to
make. The divergence stands (ADR-0235).

**Tests.** `tests/providers/test_tls_remedy_names_a_copy_556.py`: the last line
pinned for `linux`, `darwin`, `win32` and `freebsd14`, with and without the OS
store; the line fits the example above it, in five lines of at most 200
characters; the guide's recipes write the file the examples set, and its prose
says to make a copy (both copies).

**Known limitations.**

- The line still names `python -m certifi`, which assumes a `python` with
  certifi on the user's PATH. On macOS 26.6 a stock PATH has no `python`, and
  `/usr/bin/python3 -m certifi` answers `No module named certifi`; aelix's own
  interpreter has certifi. Not changed here.
- The macOS and Windows path is still a placeholder the user creates; the line
  now says what goes in it, not the command that makes it (the guide has both
  shells' commands).
