# TLS, corporate CAs, and `SSL_CERT_FILE`

Status: Accepted

If `aelix` cannot reach a provider and the error mentions
`CERTIFICATE_VERIFY_FAILED`, this page tells you which of the four possible
causes you have, and what to do about each. Start with `aelix status` — it
answers most of the question before you try anything.

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
[Python 3.13 rejects certificates 3.12 accepts](#python-313-rejects-certificates-312-accepts).

## The four causes, and how to tell them apart

`aelix` reads the OpenSSL verify code out of the failure and says which one you
have. The message is the diagnosis; you do not have to guess.

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
   cannot modify the system store, or when `store` says `degraded`:

   ```sh
   export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
   ```

   If your platform has no such combined bundle, append the corporate CA to
   the one `python -m certifi` prints and point at a **copy**:

   ```sh
   cat "$(python -m certifi)" corp-root.pem > ~/.aelix/ca-bundle.pem
   export SSL_CERT_FILE=~/.aelix/ca-bundle.pem
   ```

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
| 89 | Basic Constraints of CA cert not marked critical |
| 92 | CA cert does not include key usage extension |
| 95 | Missing Authority Key Identifier |

If you see one of those, or `aelix status` shows
`RFC 5280 strict  on`, that is what you are looking at. **The certificate is
not the problem and no CA bundle will fix it.**

`aelix` handles this for you. When a connection fails with a strict-only code,
it re-tests the same host twice — once with strict on, once with it off — and
only if the handshake **fails with strict and succeeds without it** does it
relax that one flag for the rest of the session, saying so:

> RFC 5280 strict verification relaxed for this session after
> `api.business.githubcopilot.com` failed it

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

- [providers-and-models.md](providers-and-models.md) — provider keys and
  environment variables.
- [models-json.md](models-json.md) — custom `baseUrl`s, which is where
  hostname-mismatch failures usually come from.
- [private-catalog.md](private-catalog.md) — running an extension catalog on a
  closed network.
