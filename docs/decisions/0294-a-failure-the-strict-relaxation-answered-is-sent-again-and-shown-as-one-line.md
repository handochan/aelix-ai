# 0294. A failure the strict relaxation answered is sent again by its adapter and shown as one line

Status: Accepted (2026-11-06, #703)
Date: 2026-11-06
Issue: #703. Lane `impl-tls` of the beta.3 polish batch; the owner's report of 2026-11-06:
"python3.13이상 에서 ssl strict 검증 처음에 에러나는거 warning등 안내를 조금 간략화 하던가 하는게
좋을 듯. 매번 오류를 봐야함."
Relates: ADR-0128 (the #551 and #572 amendments: the TLS retry verdict, whose `resend` value
this acts on below the harness; dated note there), ADR-0251 §4 (an OAuth refresh's
`retry_reason`, unchanged; §3 below sends a refresh again before it), ADR-0195 (TLS attribution and remedies),
ADR-0241 (the #192 amendment: the strict remedy and the relaxed session's note), ADR-0268 (what
`--mode json` writes to stderr; dated note there), ADR-0235 (pi is a reference, not a target),
#184 (the relaxation), #551, #572.
pi: `earendil-works/pi` `4ffe32c6f`. pi has no strict relaxation: Node does not set OpenSSL's
`X509_V_FLAG_X509_STRICT`, so it never meets this failure, and its retry reads the error text
alone (`packages/ai/src/utils/retry.ts`, `isRetryableAssistantError`). Nothing to port; aelix
diverges knowingly.
Tests: `tests/providers/test_strict_relaxed_once_703.py`, `tests/tui/test_login_wizard.py`
(`test_a_failure_the_relaxation_answered_drops_the_note_the_notice_line_says`),
`tests/tui/test_tls_notice_after_banner_703.py`, `tests/docs/test_adr0294_numbering_703.py`,
and the #551 and #572 files' rows that now see the request sent again before the turn.

## Context

On Python 3.13 a TLS-intercepting proxy's certificate can fail RFC 5280 strict checking alone
(a leaf with no Authority Key Identifier, code 85), which nothing else on the machine enforces.
Since #184 the adapter that sees the failure re-verifies the host with strict on and off and,
only when the chain verifies without it, relaxes strict for the session
(`aelix_ai.providers._tls_strict`). The relaxation is not persisted, by design: it is made on
a measurement, in this process, against this chain.

So on every launch the first request failed, and the user saw it as an error. Measured on
`679e5e5c` in docker (`python:3.13-slim`, Python 3.13.15, OpenSSL 3.5.7), the real CLI against
a local server whose CA is in the container's trust store and whose leaf has no Authority Key
Identifier, fake keys, a fake Codex OAuth token in a scratch agent dir
(`.omc/specs/beta3-polish-2026-11-06/tls/live/`):

```text
TUI, openai-completions   ✖ Connection error. — [SSL: CERTIFICATE_VERIFY_FAILED] ... (red)
                          + the four-line note (Relaxation.remedy), then the 2 s retry,
                          the answer and "✓ Retry succeeded (attempt 1)"; 4.5 s to the answer
TUI, openai-codex         ✖ [SSL: CERTIFICATE_VERIFY_FAILED] ... + the note; no answer
-p, openai-completions    exit 0, nothing on stderr, 4.4 s
-p, openai-codex          exit 1, the error and the note on stderr (#572 made it retry)
--mode json               message_end(error) with the note, auto_retry_start, the answer, auto_retry_end
/login, GitHub Copilot    ✖ OAuth login failed: ... + the note + "Run /login again"
```

## Decision

1. **The adapter sends the request again, once, before anyone sees the failure.** The four
   built-in adapters that relax - `anthropic`, `openai_completions`, `openai_responses`,
   `openai_codex_responses` - are wrapped in `_strict_resend.resend_when_strict_relaxed`. When
   the stream's FIRST event is an `error` whose verdict is `resend` (#572: the relaxation
   answers it), the wrapper drops it and runs the adapter again, which builds a new client and
   with it an SSL context without `VERIFY_X509_STRICT`; the turn, a compaction's summary
   request, or whatever reads the stream sees only that request's answer. No error event, no
   `message_end` error, no `auto_retry_*` events, no failed answer in the session, no 2 s wait.
   Not sent again: an abort (read again once the first attempt is closed, since closing it
   awaits, and before the request goes out a second time - fix round 1), an error after
   anything streamed, and a caller-owned client (`options.client` keeps the context it was
   built with; the turn's retry, which reads the same `resend` verdict, is what is left to
   it). Nor a failure after the provider answered. Being the first event does not show that:
   every built-in adapter calls `on_response` (the harness's `after_provider_response`) once
   the response is in hand and yields `start` after it, so a hook whose own HTTPS request fails
   on strict there is the first event too (measured before fix round 1: the answered request
   was sent a second time, 2 POSTs). The adapter says so instead -
   `describe_provider_error(..., after_response=True)` - and such a failure never carries
   `resend`: `repeats` when no relaxation answers it, no verdict when one does, as before #572
   (the turn's retry pattern then decides it on its words, as it decides any hook's failure).
   So what the adapter sends again is always the request's own TLS handshake, before any
   response. At
   most once: a request sent again that fails with the `resend` verdict once more - code 79
   under a relaxation measured on 79, which a `CA:FALSE` issuer gives with or without strict -
   goes out marked `repeats`, so nothing retries it and its advice is shown once (#551).
2. **The relaxation is announced once, on one line.** `_tls_strict.add_relaxation_listener`
   registers a callback that `maybe_relax_strict_for_session` calls when it makes the
   relaxation (once per process). The line is `Relaxation.notice()`:

   ```text
   TLS strict relaxed: api.openai.com (Missing Authority Key Identifier) · aelix docs tls
   ```

   The host, the rule its certificate failed and the TLS guide (`aelix docs tls`, an alias of
   `tls-and-corporate-ca` added for this line); what strict is and what relaxing it keeps is
   the guide's to say. Not `aelix status`, as it first did: that command runs in a process of
   its own, where nothing was relaxed, and shows strict `ON` (fix round 1). Not the line fix
   round 1 left, `TLS: RFC 5280 strict relaxed for this session (<host>: <rule>) — aelix docs
   tls-and-corporate-ca`: 84 fixed characters, 125 for `localhost`, and the TUI wrapped it at
   120 columns (measured). Now 40 fixed characters: for `api.openai.com`,
   `api.anthropic.com`, `chatgpt.com` and `api.business.githubcopilot.com` with `Missing
   Authority Key Identifier`, `Basic Constraints of CA cert not marked critical` or `CA cert
   does not include key usage extension` it is 83 to 118 characters (fix round 2). The TUI
   commits it dim (`tui/shell.py`, at once on the loop, so it lands above whatever its caller
   prints next - but under the banner: until the banner is up the line is held); `-p`,
   `--mode json` and `--mode rpc` write it to stderr (`cli/tls_notice.announce_on_stderr`,
   registered by `cli/entry.py` for the headless modes): their stdout is the answer, the event
   stream or the protocol. ADR-0268 §3 kept a JSON run's stderr free of the failure line; this
   is not one (dated note there), and a delegation parent reads it after the child's answer.
   A relaxation made before any listener was registered - an extension's `session_start` runs
   before either mode registers one (`cli/entry.py`) - is handed to the first listener
   registered after it, so it is still said once (fix round 1; before, it was said nowhere).
   The callers that measure and relax are a turn and a summary request (the four adapters), an
   OAuth token refresh (`aelix_ai.oauth._high_level`, since fix round 1, below), the Copilot
   device-code poll and policy batch, and `/login`'s wizard; a second provider that meets the
   same proxy in the same run builds a relaxed client and meets nothing, so the line is printed
   once. The first version of this ADR said every caller reached the relaxation; the OAuth
   refresh did not (next item).
3. **An OAuth token refresh measures, relaxes and asks once more (fix round 1).** With a login
   whose bearer has expired, the refresh is the launch's first request, so it met the wall
   first. It never measured: #551 marked its certificate failure "not transient" and every
   launch ended in `OAuth refresh failed for github-copilot: [SSL: CERTIFICATE_VERIFY_FAILED]
   ... Run /login`, the issue's complaint in another form (measured in docker before the fix:
   one handshake, exit 1). `get_oauth_api_key_from_credentials` now calls
   `maybe_relax_strict_for_session` on a refresh's failure, as the adapters do, and when the
   relaxation answers it asks the provider once more: every built-in refresh opens a new client
   per call, which builds its context without strict, and a handshake that failed sent nothing,
   so the refresh token was not spent. The line above says it. Any other failure, and a second
   one, goes on to #379's classification as before.
4. **The check goes where the request goes (fix round 1).** The confirmation handshakes used
   to connect to the host directly. Behind an explicit `HTTPS_PROXY` that intercepts TLS - the
   usual corporate shape - the request goes through a `CONNECT` tunnel and the chain that fails
   is the proxy's; a direct connection reaches a different chain or nothing, so both
   handshakes failed, nothing was relaxed, and the strict advice came on every run (measured).
   `_tls_strict._handshake` now resolves the proxy as the clients' `httpx` does
   (`httpx._utils.get_environment_proxies`: `HTTPS_PROXY`, `ALL_PROXY`, their lower-case forms
   and `NO_PROXY`, matched most specific first) and opens the same tunnel, sending the Basic
   credentials the proxy URL carries, as httpx does, within the same 5 s.
5. **What stays.** A failure the relaxation does not answer - an untrusted root, a wrong host,
   an expired certificate, or any of them while a relaxation is in force - keeps today's error
   and advice, byte for byte, sent once (#551). `Relaxation.remedy()`, the four-line note, is
   still what a certificate failure gets while the relaxation is in force: that failure is not
   strictness, and its last line says so. A failure after the response that the relaxation
   answers (§1: an `on_response` hook's own request) is strictness and is not sent again, so
   its note ends "This failure came after the provider had answered (a hook's own request), so
   the relaxation does not send it again." (`remedy(after_response=True)`, fix round 2; it
   ended "If the request still fails, the cause is not certificate strictness.", which read as
   if strictness were not the cause). `/login`'s wizard does not send its failed request
   again; its failure drops the note when the relaxation answered it - the dim line above it
   says it - and keeps "Run /login again", and that sign-in passes. This said the request
   could not be sent again because a device code is single-use; the measured failure was the
   Copilot device-code request itself, before any code existed (fix round 1).
6. **Not persisted.** Relaxing on the next launch before anything failed would act on a
   measurement made in another process, against a chain that may have changed; the module
   docstring's measure-first rule stands. Each launch pays one failed handshake and the
   relaxation's two checks again, and says so in one line.

Why in the adapter and not in the turn's retry: the turn's retry (#572) already sends the
request again, but only after the failure has been a `message_end` error - drawn in red,
written to the session, sent to `--mode json` and rpc as an error, followed by a retry
countdown and a 2 s wait - and a compaction's summary request would show the same through
#409's retry. Presenting that sequence differently in four surfaces is the TUI's most fragile
kind of change; below the harness there is nothing to present. The cost is that the adapter
runs twice for one request: `on_payload` (the harness's `before_provider_payload`) fires
twice, as it does for the turn's retry - the first payload never left the machine: what is
sent again is a handshake that failed before any response and before any byte of the request
was written (§1) - while `before_provider_request` fires once and `on_response` once, for the
answer.

## Measured after

Same container and servers, this change's tree:

```text
TUI, openai-completions   dim "TLS: RFC 5280 strict relaxed ... — aelix status", then the answer; 2.3 s
                          (the pointer is the guide since fix round 1; rows below it)
TUI, openai-codex         the dim line, then the answer; 0.75 s
TUI, untrusted root       the same screen as 679e5e5c (diffed), advice once, no retry
-p, openai-completions    exit 0, stderr = the one line, 2.9 s (the OpenAI SDK's own 2 retries remain)
-p, openai-codex          exit 0, stderr = the one line, 0.43 s
-p, untrusted root        exit 1, stderr byte-identical to 679e5e5c, 5 handshakes (3 SDK + 2 check)
--mode json               message_end(stop) only; stderr = the one line
--mode rpc                the answer; stderr = the one line; lab then openai-codex in one process: one line
/login, GitHub Copilot    the dim line, "✖ OAuth login failed: [SSL: ...] Missing Authority Key Identifier",
                          "Run /login again ..."; the note is gone
```

Fix round 1, same lab rebuilt (Python 3.13.15, OpenSSL 3.5.7), `44a32b73` before and the
round's tree after (`.omc/specs/beta3-polish-2026-11-06/tls/fix1/`):

```text
-p, github-copilot, expired bearer  before: exit 1, "OAuth refresh failed ... Run /login", 1 handshake
                                    after:  exit 0, 0.87 s, the answer, stderr = the one line;
                                            5 handshakes (refresh, check x2, refresh, chat), 2 requests
-p, explicit HTTPS_PROXY MITM,      before: exit 1, the strict advice, 3 CONNECTs (the check went direct)
    no direct route                 after:  exit 0, 2.13 s, the answer, stderr = the one line;
                                            6 CONNECTs (3 SDK, check x2, the request sent again), 1 request
-p, an extension's session_start    before: exit 0, stderr empty (the line said nowhere)
    sends the first request         after:  exit 0, stderr = the one line
-p, an after_provider_response      before: exit 0, 2 POSTs to the provider, the hook ran twice
    hook fails on strict            after:  exit 1, 1 POST, the hook ran once, its error is shown
-p, untrusted root                  after:  exit 1, stderr byte-identical to 679e5e5c and 44a32b73
TUI, openai-completions             after:  the dim line once (ESC[2m), the answer, 2.16 s
TUI, session_start request          before: no line; after: the dim line once, under the banner
```

Fix round 2, a new lab (the same image, Python 3.13.15, OpenSSL 3.5.7) and the round's tree
(`.omc/specs/beta3-polish-2026-11-06/tls/fix2/`):

```text
-p, localhost, code 85              exit 0, the answer, stderr = the line, 81 characters
-p, api.business.githubcopilot.com  exit 0, the answer, stderr = the line, 118 characters
    (/etc/hosts; an intermediate whose basicConstraints is not critical: code 89)
TUI 120x40, the same two            the dim line once, on one row (81 and 118), then the answer
-p and TUI, an after_provider_      exit 1, 1 POST, the hook ran once; the note ends "This
    response hook fails on 85       failure came after the provider had answered ... does not
                                    send it again."
-p, untrusted root                  stderr byte-identical to 679e5e5c's (fix round 1's run)
```

## Limits

- `aelix status` is a process of its own: it shows strict `ON` and the interpreter, never this
  session's relaxation (`describe_trust_store` reports it only in the process that made it).
  The TLS guide said otherwise; it no longer does, and the notice points at the guide.
- The check tunnels through an `http://` proxy only, with the Basic credentials its URL
  carries. Through an `https://` or SOCKS proxy, or one that wants another authentication
  scheme (NTLM, Negotiate), it answers "no" and nothing is relaxed: the strict advice, as
  before. The proxy is read through a private httpx name (`httpx._utils`), pinned by the
  `httpx<1.0` requirement; without it the check connects directly, as it did before.
- The GitHub Copilot device-code poll and the policy batch after `/login` keep their own
  paths (#184, #551): the poll relaxes on a strict-only failure and its next poll, an
  interval later, is the retry; an all-failed policy batch relaxes and is sent once more. Both
  call the same `maybe_relax_strict_for_session`, so the one line says it. They are not
  wrapped and need not be: neither shows the failure as an error.
- The Google and Vertex adapters do not relax at all (read: `google_generative_ai.py` and
  `google_vertex.py` call `describe_provider_error` and never
  `maybe_relax_strict_for_session`, and are not wrapped): a strict-only failure there keeps
  the strict advice, `repeats`, and is not retried. Not changed here - whether their SDK's
  failure carries the request's host, and whether a new client is built per request, was not
  measured.
- A `message_end` hook cannot turn a `resend` failure into another verdict by rewording it:
  the harness classifies an adapter-described error as the adapter described it (ADR-0128's
  #572 amendment, fix round 1, as #379's `_SetupFailure`); a plain-`str` error a hook rewrites
  is classified on its new words.
- The OpenAI and Anthropic SDKs still retry the failed connection twice inside the first
  request (`max_retries` 2, ADR-0128's #551 follow-up), so that request makes 3 handshakes and
  about 1.5 s before the relaxation's check runs.
- The relaxation's check is two blocking handshakes on the event loop (up to 5 s each), as
  before; the TUI does not repaint during them.
- An extension provider that does not use a built-in adapter is not wrapped: a failure it
  describes with the `resend` verdict reaches the turn, which retries it (#572), and the user
  sees that sequence. Its stream can apply `resend_when_strict_relaxed` itself.
- `/login`'s "Run /login again" line still follows any login failure while a relaxation is in
  force, even one the relaxation does not answer (a pre-existing wording, not changed here).
  The wizard does not send its failed request again, though its first one (Copilot's
  device-code request) could be: nothing single-use was spent.
