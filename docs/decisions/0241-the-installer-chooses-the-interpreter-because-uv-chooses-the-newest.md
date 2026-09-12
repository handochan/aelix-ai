# 0241. The installer chooses the interpreter, because uv chooses the newest one

Status: Accepted (2026-09-13)
Date: 2026-09-13
Supersedes/relates: ADR-0192 (GitHub Release as the checksum channel; `install.sh`
as the documented install path) — this constrains the last step of that path and
changes nothing about the checksum gate. ADR-0047 (`openai` as a direct
dependency of `aelix-ai`, capped `<2.0`) — that ceiling is what makes a newer
interpreter fatal rather than merely untested.
Issue: #263 (this change), #262 (the crash it stops reaching users), #192 (the
gap this is half of; the `requires-python` bound and the CI matrix are still open
there).
Design spec: none — the whole argument is the measurements below.

`uv tool install` reads neither `.python-version` (which pins 3.12) nor
`uv.lock`. It resolves an interpreter fresh and takes the **newest** one it can
find. `install.sh` and `install.ps1` never said otherwise, so the supported
interpreter was whatever the user's machine happened to offer.

## What that resolved to

Measured on one box carrying 3.11 through 3.14, both commands against the
published `v0.1.0-beta.2`:

| command | interpreter |
| --- | --- |
| `uv tool install --force 'aelix==0.1.0b2'` | **3.14.5** |
| `uv tool install --force --python '>=3.11,<3.14' 'aelix==0.1.0b2'` | 3.13.13 |

CI runs 3.11 and 3.12. So the top row is an interpreter this project has never
executed — which #192 already recorded, and already cost a day when 3.13 turned
on `X509_V_FLAG_X509_STRICT` and only aelix-on-3.13 refused a corporate TLS
chain.

## Why 3.14 is worse than untested

`aelix-ai` pins `openai>=1.66,<2.0`. Python 3.14 made `typing.Union[...]` a
slotted object; every `openai` up to and including 2.7.1 still does

```python
# openai/_models.py:697
cast(CachedDiscriminatorType, union).__discriminator__ = details
```

which on 3.14 is

```
AttributeError: 'typing.Union' object has no attribute '__discriminator__'
                and no __dict__ for setting new attributes
```

— the message in #262, reproduced verbatim in a 3.14 environment synced from
this repo's own lock.

**It is latent, and that is the part that matters.** `construct_type` validates
a union through pydantic FIRST and only falls through to that write when
validation fails. Measured on 3.14, same build: `aelix --provider github-copilot
--model gpt-5.6-luna -p "reply with exactly: OK"` **succeeds**, while #262's
three parallel agents all died at ~6 s. The one-shot prompt that is the obvious thing to try
passes. What failed was real work, and it failed as an `AttributeError` from
inside a vendored SDK, which is not a clue anyone can act on.

Scope, and stated at the strength it was actually measured at. By reading the
installed sources: only `openai` performs that write — `anthropic` 0.102.0 and
`google-genai` declare the same `CachedDiscriminatorType` protocol and never
assign to it, which is a source-level claim and not a live one, since neither
provider was exercised on 3.14. By live run on 3.14: one `openai-completions`
call (openrouter) and one `openai-responses` call (github-copilot,
`gpt-5.6-luna`) both succeeded, which establishes that the crash is
CONDITIONAL — it does not establish that any provider is safe.

## Decision

Both installers pass `--python '>=3.11,<3.14'`, exposed as `AELIX_PYTHON`.

**A range, not `--python 3.13`.** A single version forces a download onto a box
where 3.12 is installed and fine. With the range uv takes any local 3.11-3.13
and downloads only when it has none — measured both ways, including the download
leg.

**The range is written in four places, and a test holds them together.**
`install.sh`, `install.ps1`, `_PY_REQUEST` in
`tests/packaging_gate/test_install_ps1_parity.py`, and — since the Windows e2e
grew the post-condition below — `assert-install-ps1.ps1` twice, as the numeric
bounds it compares and as the literal in its failure message. A review flagged
the duplication; it fails CLOSED (a stale post-condition rejects a valid
interpreter rather than accepting a broken one), so it is a maintenance cost
rather than a hazard, and the answer is a checked invariant rather than a
comment asking people to remember: the parity test derives `-lt 11` / `-gt 13`
from `_PY_REQUEST` and asserts both, and asserts both scripts request the same
value.

**Asserted on the invocation LINE, not on the file.** The first version of that
test searched the whole script, and a review broke it by deleting the flag from
the real command and parking the string in a trailing comment elsewhere —
`sh -n` said OK and the suite came back byte-identical to baseline. Sabotage
now caught in every direction tried: flag dropped from either script, either
default changed, and the trailing-comment trick.

**And a post-condition, because the text is not the behaviour.** That parity
test proves the flag is *in the file*. `.github/scripts/assert-install-ps1.ps1`
now reads the tool environment's `pyvenv.cfg` and fails the Windows e2e if
`version_info` is outside 3.11-3.13 — so the one host this repo cannot execute
either script on locally is the one that checks the flag did something. Verified
both ways against real environments before shipping: a 3.13 env passes, a 3.14
env fails with the #262 reason in the message. `install.sh` has no such e2e
because CI never executes it at all — a real gap, older than this issue, and not
closed here; it was executed by hand three ways instead (default, `AELIX_PYTHON=3.12`,
`AELIX_PYTHON=` empty), plus the 3.14 -> 3.13 upgrade path.

**`${AELIX_PYTHON:->=3.11,<3.14}`, with `:-` and not the `-` its siblings use.**
`uv tool install --python ""` does not fail. uv IGNORES an empty request and
goes back to the newest interpreter — measured: exit 0, environment on 3.14.5,
the precise state this ADR exists to prevent. The hole is reachable by habit
rather than by malice: `AELIX_EXTRAS=` installs the bare CLI and the README
teaches that spelling, so the same form on this variable would disarm the gate
silently. `:-` makes empty mean the default, which is what `install.ps1` already
did (`if ($env:AELIX_PYTHON)` is false for an empty string), so this removes a
POSIX/Windows divergence rather than adding one. Widening stays available but
has to be said out loud: `AELIX_PYTHON='>=3.11'`. The parity test asserts this
BY EXECUTION — it runs the assignment line under `sh` for unset / empty / set —
because a substring check would have passed the broken `-` form.

## What this is NOT

**It is NOT a stopgap, and the first draft of this ADR said it was.** That
draft claimed uv honours a package's own `requires-python` ceiling, so #192's
metadata bound would retire the flag. A Codex cross-review disputed it; the
measurement behind the claim had used the wrong install path, and reproducing it
properly settles the question the other way. Against one wheel declaring
`Requires-Python: <3.14,>=3.11`, on the same box:

| how it is installed | interpreter |
| --- | --- |
| `uv tool install --find-links <dir> pkg==0.1.0` — **this script's path** | **3.14.5**, exit 0, and the package imports there |
| `uv tool install <the .whl file>` | **3.14.5** |
| `uv tool install <a local project dir>` — what the first draft measured | 3.13.13 |
| `pip install --find-links <dir> pkg` | refuses: *requires a different Python* |

uv picks the interpreter BEFORE it resolves, so a published wheel's ceiling
never steers it. Only the project-directory path reads it, and no user of these
scripts takes that path. **So the flag is permanent.** #192's bound is still
worth having — it is what makes `pip install aelix` refuse cleanly instead of
breaking later, and it is the honest statement of what the project supports —
but it does not remove this flag, and #192 should not be planned as though it
does.

**Widening the default is not a one-line edit.** When 3.14 becomes supported
(#262; measured floor `openai>=2.7.2`), a wider default here reaches EVERY
release these scripts can install, and `AELIX_VERSION` pins arbitrarily old
tags — `0.1.0b2` carries `openai<2.0` forever. So the default can widen only
once no installable release breaks on the wider range, or once the request is
derived from the release being installed. Widening on the day the fix ships
would hand a pinning user the exact crash this ceiling was added to stop.

**This does not make 3.13 supported.** It makes 3.13 *installed*. The suite on
3.13 at `aa80007` is `10 failed, 10630 passed, 23 skipped`, and all ten sit in
one theme — `test_tls_strict.py`, `test_error_hints.py`, `test_terminal_text.py`
and `test_login_wizard.py` hard-code the 3.12 world (`# Strict OFF (3.12)` above
`assert strict_is_enabled() is False`).

An earlier draft called those ten "stale assertions, product code is clean."
That is not defensible, and the cross-review is the reason it does not stand
here: `_UNTRUSTED_ISSUER_CODES` is `{2, 18, 19, 20}`, so verify code **7**
(`CERT_SIGNATURE_FAILURE`) falls through to `_strict_hint`, which tells the user
"the same host works in every other tool on this machine" and to reinstall on
3.12. A certificate with a genuinely bad signature fails with code 7 *whether or
not strict is on*, so on 3.13 that is confidently wrong advice about a real
problem — a diagnostic defect the 3.13 run exposes, not a test that went stale.
It predates this change and is not fixed here. Whoever takes the ten on for
#192's matrix leg inherits it, and should treat it as product work rather than
an assertion refresh.

## Cost

An install on a 3.14-only machine downloads a 3.13 (~25 MiB) it would otherwise
have skipped.

**And where managed downloads are off, this turns a success into a failure.**
Measured in `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` (3.14.2 the only
interpreter) with `UV_PYTHON_DOWNLOADS=never`: without the flag, exit 0; with
it, `error: No interpreter found for Python >=3.11, <3.14 in managed
installations or search path`. Air-gapped hosts and images pinned to 3.14 now
fail at install time instead of succeeding and dying mid-turn later. That is the
better failure of the two, and it is still a failure that did not exist before —
so both installers' error paths name the request and `AELIX_PYTHON` rather than
reporting a bare "uv tool install failed".

## What this does NOT close

**`uv tool install` typed by hand still lands on 3.14, and uv routes users
there.** Measured, isolated tool dirs:

```
uv tool install aelix@latest                 -> version_info = 3.14.5
uv tool install --force 'aelix==0.1.0b2'     -> 3.14.5, OVERWRITING an
                                                environment install.sh had
                                                correctly built on 3.13.13
```

and `uv tool upgrade aelix` prints, unprompted:

> hint: `aelix` is pinned to `0.1.0b2` … reinstall with
> `uv tool install aelix@latest` to upgrade to a new version

So the protection holds exactly as long as the user stays inside the documented
curl line, and uv itself hands them the command that breaks it. Combined with
the finding above — that a published wheel's `requires-python` ceiling does not
bind this path — **#192 will not close it either.** A script cannot cover
commands the user types directly.

The durable fix is a runtime guard in aelix: refuse, or warn loudly, at startup
on `sys.version_info >= (3, 14)` while `openai<2.0` is pinned, naming #262. That
also converts the latent mid-turn `AttributeError` into a first-second failure,
which is the property this ADR spends its first half explaining is missing. It
is product code in a different package, on a different issue, and is NOT done
here — it is filed so that the next person does not read this ADR as a claim
that the hole is closed.
