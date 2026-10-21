# TLS, corporate CAs, and `SSL_CERT_FILE`

Status: Accepted

If `aelix` cannot reach a provider and the error mentions
`CERTIFICATE_VERIFY_FAILED` — or, on macOS and Windows, says a certificate
`is not trusted` — this page tells you which of the four possible causes you
have, and what to do about each. Start with `aelix status` — it answers most
of the question before you try anything.

Nothing here requires network access to read: this guide ships inside the
wheel, so `aelix docs tls-and-corporate-ca` prints it on a closed network.

## First: ask the build what it trusts

```
$ aelix status
...
TLS trust:
  store            OS certificate store (a CA installed system-wide IS trusted)
  ssl.SSLContext   truststore._api.SSLContext
  backend          openssl (truststore 0.10.4)
  cafile           /usr/lib/ssl/cert.pem
  capath           /usr/lib/ssl/certs  (hashed certs present)
  CAs loaded       not reportable — the active context does not enumerate its CAs (OS store)
  RFC 5280 strict  off
  interpreter      Python 3.12.1, OpenSSL 3.0.13 30 Jan 2024
```

`aelix status --json` prints the same thing under a `trust_store` key if you
want to attach it to a bug report. Two lines carry most of the answer:

**`store`** is the one that decides whether installing a CA system-wide is
enough.

| value | what it means |
|---|---|
| `OS certificate store` | `truststore` is active. A CA installed system-wide **is** trusted — you should not need `SSL_CERT_FILE` at all. |
| `degraded` | `truststore` was supposed to be active and failed. The reason is printed next to it. Verification is falling back to the bundled `certifi` roots, so a system-wide CA is **invisible** — this is the case `SSL_CERT_FILE` exists for. |
| `not-attempted` | Nothing injected a trust store in this process. Normal when you are embedding the library rather than running the CLI. |

**`RFC 5280 strict`** is the one that surprises people. See
[Python 3.13 rejects certificates 3.12 accepts](#4-python-313-rejects-certificates-312-accepts).

## The four causes, and how to tell them apart

`aelix` reads the verify code out of the failure and says which one you have.
The message is the diagnosis; you do not have to guess. The advice appears only
when this machine's own verifier rejected the connection: an HTTP error that
carries the status the server sent back — a `400` that quotes your prompt, a
gateway's `502` about its own upstream, a sign-in endpoint's `503` during
`/login` — gets none, whatever certificate words it contains. One known
exception: an error a provider reports in the middle of a streamed answer
with no numeric status (an OpenAI-style error whose code is a word such as
`server_error`) is still read by its words, so certificate words in it can
still bring the corporate-CA advice.

**aelix does not retry a failure that gets this diagnosis.** Sending the
request again would verify the same certificate against the same trust store,
so the turn is not sent again and the TUI shows the advice once, instead of once
per [automatic retry](providers-and-models.md#automatic-retry) (before #551 it
waited 2 s, 4 s and 8 s and stacked the advice five times). The OpenAI and
Anthropic SDKs still make their own two connection retries inside the one
request: 3 handshakes and about 2 s before the advice (measured; the Gemini
adapters make 1). The same holds for
a compaction's summary request, a `/login` token refresh, the GitHub Copilot
sign-in's polling and the OpenAI Codex adapter's own connection retries. The
one exception is the strict-mode case below: when `aelix` relaxes strict
checking for the session, the request that failed under it is sent again.

**On macOS and Windows the operating system does the verifying.** With
`store: OS certificate store`, `truststore` hands the chain to the platform's
own verifier, so the error is the OS's, not OpenSSL's: macOS says
`“api.example.com” certificate is not trusted`, Windows says
`A certificate chain processed, but terminated in a root certificate which is
not trusted by the trust provider.` (in the system's language). Its code is
the OS's too, and `aelix` maps it to the same four causes:

| cause | OpenSSL code | macOS code | Windows code |
|---|---|---|---|
| 1. untrusted issuer | 2, 18, 19, 20 | -67843, -25318 | `0x800B0109`, `0x800B010A` |
| 2. wrong hostname | 62 | -67602 | `0x800B010F` |
| 3. clock | 9, 10 | -67818 (both ends of the window), -67819 | `0x800B0101` |
| 4. strict mode | 78, 80–94 (and 79) | never | never |
| none of the four: no advice | — | -67901, -67735, -67609; every result on macOS 10.13 and older | — |

The macOS codes were measured (macOS 26.6), except -67819: Apple documents it
for a certificate that is not yet valid, but macOS reported one here as -67818.
The Windows ones are read off Windows' error table. Any other code gets the
untrusted-issuer advice, as an unlisted OpenSSL code does.

Some failures get no advice, only the operating system's own words, because
none of the four fits. macOS rejects some certificates on their own, whoever
issued them: a validity period longer than it allows (-67901,
`is not standards compliant`), a key that is too small (-67735,
`is using a broken key size`), a certificate not issued for server use
(-67609, `is not permitted for this usage`). No CA you install fixes those;
whoever runs the endpoint, or the proxy that mints its certificates, has to
replace the certificate. And on macOS 10.13 and older the verifier gives the
same result for an untrusted issuer, a wrong hostname and an expired
certificate, so `aelix` cannot say which one you have.

On Windows a CA that `SSL_CERT_FILE` points at
(rather than one installed system-wide) is tried only after the system store,
and when both fail the system store's error is the one reported: a wrong
hostname or an expired certificate under such a CA then reads as an untrusted
issuer.

### 1. A proxy is intercepting HTTPS with a private root CA

The common corporate case. Verify codes 2, 18, 19, 20 (self-signed, or an
issuer nothing trusts). The message begins:

> TLS certificate verification failed — a proxy or firewall is likely
> intercepting HTTPS with a private root CA (common on corporate networks).

**Fix, in order of preference:**

1. **Install the CA system-wide and let `truststore` find it.** On
   Debian/Ubuntu, drop the PEM into `/usr/local/share/ca-certificates/` with a
   `.crt` extension and run `sudo update-ca-certificates`; on RHEL/Fedora, use
   `/etc/pki/ca-trust/source/anchors/` and `sudo update-ca-trust`; on macOS,
   add it to the System keychain and mark it trusted. Then check that
   `aelix status` reports `store: OS certificate store`. This fixes every tool
   on the machine, not just `aelix`.
2. **Point `SSL_CERT_FILE` at a bundle that contains it.** Needed when you
   cannot modify the system store, or when `store` says `degraded`. On
   Debian/Ubuntu the system keeps one, and a CA installed with
   `update-ca-certificates` is in it:

   ```sh
   export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
   ```

   If your platform has no such combined bundle — macOS and Windows keep none
   that a system-wide install updates — append the corporate CA to the one
   `python -m certifi` prints and point at a **copy**:

   ```sh
   cat "$(python -m certifi)" corp-root.pem > ~/.aelix/ca-bundle.pem
   export SSL_CERT_FILE=~/.aelix/ca-bundle.pem
   ```

   In PowerShell on Windows, make the copy with `Set-Content -Encoding ascii`
   and set the variable with `$env:`. Do not use `cat … >` there: Windows
   PowerShell 5.1's `>` writes UTF-16, which OpenSSL cannot read as PEM
   (`no certificate or crl found`).

   ```powershell
   Get-Content (python -m certifi), corp-root.pem | Set-Content -Encoding ascii "$HOME\.aelix\ca-bundle.pem"
   $env:SSL_CERT_FILE = "$HOME\.aelix\ca-bundle.pem"
   ```

   This PowerShell recipe has not been run on Windows yet; it was checked
   only with PowerShell 7 on macOS, where the bundle it wrote loaded with the
   corporate CA in it.

   The advice `aelix` prints carries the line for the platform it runs on:
   the Debian/Ubuntu one on Linux, the `~/.aelix/ca-bundle.pem` one on macOS
   (and other systems), the PowerShell one on Windows.

   Do not edit `certifi`'s own file in place — reinstalling or upgrading the
   package silently reverts it, and the failure comes back weeks later with no
   apparent cause.

`SSL_CERT_FILE` is read by OpenSSL itself, so it also applies to any subprocess
`aelix` launches. `SSL_CERT_DIR` works the same way for a hashed directory.

### 2. The hostname does not match

Verify code 62. The chain is fine, so **adding a CA will not help** and the
message deliberately does not offer one:

> The trust chain itself is fine, so adding a CA will not help: check this
> provider's base URL for a typo, or a proxy/gateway answering for a different
> host.

Check the `baseUrl` you set in `models.json` (see
[models-json.md](models-json.md)), or whether a gateway is answering on a host
it does not hold a certificate for.

### 3. The clock is wrong

Verify codes 9 and 10 — a certificate that is expired or not yet valid. Almost
always a container or VM whose clock has drifted, not a certificate problem.
Fix the clock; do not add a CA and do not disable verification.

### 4. Python 3.13 rejects certificates 3.12 accepts

This one is worth its own section, because it does not look like a certificate
problem at all: the same corporate CA works everywhere else on the machine, the
system trust store is healthy, and `openssl s_client` reports
`Verify return code: 0 (ok)` — yet `aelix` fails.

Python **3.13** turns on OpenSSL's `X509_V_FLAG_X509_STRICT` in
`ssl.create_default_context()`. Python 3.12 does not. Strict mode enforces
clauses of RFC 5280 that essentially nothing else on a developer machine
enforces, and many corporate MITM CAs — generated once by an appliance, years
ago — violate at least one:

| verify code | strict-only failure |
|---|---|
| 85 | Missing Authority Key Identifier |
| 89 | Basic Constraints of CA cert not marked critical |
| 92 | CA cert does not include key usage extension |

Those three are the common ones, each measured failing with strict on and
passing with it off (Python 3.13.13, OpenSSL 3.5.6). The strict-only set is
codes 78 and 80–94 — OpenSSL's own list of strict-mode errors, less 79. If you
see one of those, that is what you are looking at. **The certificate is not the
problem and no CA bundle will fix it.**

Code 79 (`invalid CA certificate`) can be either, and the code alone cannot say
which. OpenSSL raises it for an issuer marked `CA:FALSE` with or without
strict — there strict is not the cause — and, only with strict on, for a root
CA that has no Basic Constraints extension, an older appliance shape (both
measured on Python 3.12.13 and 3.13.13, OpenSSL 3.5.6). So on a strict
interpreter `aelix` names both causes and the `openssl s_client` check that
separates them: `Verify return code: 0 (ok)` there means it is the strict case.

Any other code — an untrusted root (18–20), a bad signature (7) — gets the
advice for that failure even with strict on, because strict mode does not
produce it. Neither does the operating system's verifier: with
`store: OS certificate store` on macOS or Windows, OpenSSL does not verify the
chain at all, so this section does not apply there (measured on macOS with
Python 3.14: a chain missing its Authority Key Identifier verified with strict
on).
`aelix status` showing `RFC 5280 strict  on` tells you the flag is set, not
that it is the cause.

`aelix` handles this for you. When a connection fails certificate
verification on a strict interpreter, it re-tests that host twice — once with
strict on, once with it off — whatever the verify code, as long as the error
names the host (the Google adapters do not take this path). Only if the
handshake **fails with strict and succeeds without it** does it relax that one
flag for the rest of the session, saying so in place of the advice. For the
OpenAI and Anthropic APIs the automatic retry then sends the request again,
now without strict, and a request that failed on strict alone passes; that
retry is the only one a certificate failure gets. An OpenAI Codex request is
not retried, so its next prompt is the one that passes:

```text
RFC-5280 strict verification relaxed for this session after the host below failed it but verified against this machine's trust store without it.
  host: api.business.githubcopilot.com
  failed on: Basic Constraints of CA cert not marked critical
Certificate verification, hostname checking and expiry are still enforced. If the request still fails, the cause is not certificate strictness.
```

Everything else about verification stays on: the chain is still checked against
the same trust store, and the hostname is still matched. Restarting `aelix`
starts over from strict. `aelix status` reports whether a relaxation is in
effect.

Why this is scoped so narrowly matters if you are reviewing it: the relaxation
never fires for an untrusted issuer, a hostname mismatch or an expired
certificate, because those fail with or without strict and the second probe
does not pass.

You can avoid the situation entirely by running `aelix` on Python 3.12, and you
can check which interpreter you actually got — `uv tool install` ignores
`.python-version`, so the installed CLI may not be on the interpreter a
contributor checkout uses:

```sh
aelix status --json | python -c "import json,sys; print(json.load(sys.stdin)['trust_store']['interpreter'])"
```

## What `aelix` will not do

It will not disable certificate verification, and there is no flag to make it.
An agent runtime holds provider credentials and executes what a model tells it
to; a switch that turns off transport authentication for all of them is not
worth having. The narrow, evidence-gated strict relaxation above is the only
verification behaviour that changes at runtime, and it changes one RFC clause
after measuring that the clause is the cause.

## Reporting a TLS problem

Include the output of:

```sh
aelix status --json
```

and the full error text. The verify code in that text is what distinguishes the
four cases above, and `trust_store` says what the build was verifying against
when it happened. Neither contains credentials.

## See also

- [credentials.md](credentials.md) — provider keys and
  environment variables.
- [models-json.md](models-json.md) — custom `baseUrl`s, which is where
  hostname-mismatch failures usually come from.
- [private-catalog.md](private-catalog.md) — running an extension catalog on a
  closed network.
