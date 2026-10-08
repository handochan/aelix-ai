# 0241. The installer chooses the interpreter, because uv chooses the newest one

Status: Accepted (2026-09-13) — Amended 2026-10-08 (#192: CI runs the whole range)
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

## Amendment (2026-10-08, #192) — CI runs the whole range, and the strict remedy claims only what strict causes

**The matrix is the installer's range now.** `ci.yml`'s test job runs 3.11,
3.12 and **3.13**, each on `ubuntu-latest` and `windows-latest` — six legs, up
from four. Owner decision on #192: 3.13 on both runners; **no** `requires-python`
ceiling in the pyprojects (that stays with #278, for the reason "What this is
NOT" gives: a published wheel's ceiling does not steer `uv tool install`); the
installer's `>=3.11,<3.14` unchanged. So "This does not make 3.13 supported"
above is superseded: 3.13 is installed AND tested. `tests/test_ci_python_matrix.py`
derives the minors from `install.sh`'s `AELIX_PYTHON` default and asserts the
matrix equals them, on both runners with no `include`/`exclude`, so widening the
installer without a leg — or dropping one — goes red. `release.yml` stays on
3.12: it runs no tests, and `uv build --all-packages --wheel` under 3.12 and
3.13 at `8f7d98aa` (`SOURCE_DATE_EPOCH` fixed) gave five byte-identical
`py3-none-any` wheels.

**The ten failures this ADR counted were eleven at `8f7d98aa`: nine were the
product defect it named, two were tests that assumed an older interpreter.**
`8f7d98aa` under 3.13.13: `11 failed, 13656 passed, 44 skipped`. Nine are the
TLS remedy — `test_error_hints.py` (6), `test_terminal_text.py`,
`test_login_wizard.py`, `test_provider_error_quoting_186.py`. The other two,
`test_tls_strict.py::test_nothing_happens_when_strict_is_already_off` and
`::test_the_advice_stops_naming_a_ca_the_user_already_installed`, are guard
asserts (`assert strict_is_enabled() is False`) that held only before 3.13;
they now force 3.12's flags the way the 3.13 rows force 3.13's. `_strict_hint`
was a DENYLIST (strict on and code not in `{2, 18, 19, 20}`), so on 3.13 a bad
signature (7), a non-CA issuer (79) and every error with no code — the #99
shape, a marker-only match — were told "an RFC 5280 rule, not trust;
reinstall on 3.12". It is an ALLOWLIST now: codes 78 and 80–94, OpenSSL's
"Errors in case a check in X509_V_FLAG_X509_STRICT mode fails" block minus 79.
With no code, OpenSSL's message for one of those codes still counts. Measured
on real handshakes, 3.13.13 / OpenSSL 3.5.6: 85, 86, 89, 92 fail with strict
and pass without; 7 and 20 fail both ways.

**Code 79 is neither, so it gets a hedged remedy** (review round 2).
`X509_V_ERR_INVALID_CA` sits in the strict block, but OpenSSL raises it for two
chains: an issuer marked `CA:FALSE` fails 79 with strict on and off, and a
trust anchor with no basicConstraints whose keyUsage has keyCertSign fails 79
only with strict on (measured both ways on 3.12.13 and 3.13.13, OpenSSL 3.5.6).
The code cannot tell them apart, so on a strict interpreter 79 (or, with no
code, its message) names both causes and the `openssl s_client` check that
separates them, asserting neither; without strict it gets the CA advice, since
only the `CA:FALSE` chain can raise it there. Round 1 had given 79 the CA
advice alone, which told the second chain to install a CA it already trusted.

**Every remedy fits the TUI's error bound** (review round 3). The TUI prints an
error through `safe_error_for_terminal` — 8 lines, each cut at 200 characters
— at every error site (`render.py`, `login_wizard.py`); `aelix -p` does not
bound, and round 2 was checked there only. Its hedge was 10 lines / 1661
characters, so in the TUI on 3.13 the `CA:FALSE` user saw the strict branch's
action and `… (2 more lines omitted)` where `SSL_CERT_FILE` was. The hedge is
6 lines of at most 200 characters now, each branch's action on a line of its
own (the error line and a blank line come first, so 6 is the room there is).
Measuring every remedy against the same bound found that none fit, on `main`
too: the hostname remedy lost "check this provider's base URL" and the clock
remedy "check this machine's clock", each a single line cut at 200; the trust
remedy lost "installing that root CA system-wide is the fix" (OS store) or
"point SSL_CERT_FILE at a bundle" (certifi); the strict remedy and a relaxed
session's note were cut mid-sentence. All of them are broken into lines of at
most 200 characters now, and `test_tls_strict.py` holds every remedy, on both
trust stores, to the bound. The relaxed session's note is the one remedy with
variable text, and round 3 measured it with one 24-character host: its first
line was 133 fixed characters plus the host plus the verify message, 211 for
the TLS guide's example host `api.business.githubcopilot.com` with code 89, so
the TUI still cut that sentence's end (review round 4). The host and the
message have lines of their own now, a host past 192 characters continuing on
the next line, and the test sweeps five hosts up to DNS's 253-character
maximum against every strict-only message, 79's, the longest OpenSSL 3.6
has for any code (68 characters) and none. The exact condition, also tested: the note shows whole
for a host of up to 576 characters and a verify message of up to 187; a longer
host pushes its last lines past the TUI's 8, a longer message is cut at 200.
`aelix status` prints the note's one-line form unchanged.

**One number in this repo was wrong.** "Missing Authority Key Identifier" is
verify code **85**, not 95 (95 is `RPK_UNTRUSTED`): a real handshake against a
leaf without an AKI returns 85. `_error_hints.py`, the TLS guide's table and the
synthetic codes in the tests said 95; all say 85 now. Under the old denylist the
wrong number was harmless (95 was "not untrusted-issuer"); under an allowlist
it would have silently stopped earning the strict remedy.

**What the windows 3.13 leg adds that ubuntu 3.13 does not.** CPython 3.13
moved Windows' `time.monotonic()` from `GetTickCount64` (~15.6 ms) to
`QueryPerformanceCounter`. The ordering tests that were rewritten for the coarse
clock (#260, #313, #330) assert orders and bounds, not ties, so a finer clock
can only make them hold more often; the one case that needs a tie
(`_CoarseClock` in `tests/process_tree/test_the_drain_asks_the_pipe.py`) makes
it deterministic rather than reading the host clock. That is a reading of the
tests, not a measurement: only the branch's windows py3.13 leg measures it.

**The first run of the py3.13 legs found two more 3.13-only defects, both in
the product** (review round 5; branch CI run 37760507281: the four 3.11/3.12
legs green, ubuntu py3.13 2 failed, windows py3.13 47 failed). A local 3.13
suite had passed both times because neither shows on the box it ran on.

- **Windows: `ntpath.isabs` needs a drive from 3.13.** `ntpath.isabs("/opt/bin/uv")`
  and `ntpath.isabs("\\opt\\uv")` are True on 3.11 and 3.12 and False on
  3.13. All 47 windows failures were `tests/cli/test_extension_install.py`
  fixtures that handed the uv backend a POSIX literal - a test fault: a real
  Windows `shutil.which` hit carries the drive - and they build a native
  absolute path now. But the product asked the same question through
  `os.path.isabs` in five places in `cli/extension_install.py` - the uv
  backend's constructor, the PATH lookup for `uv`, `PIP_CONFIG_FILE`,
  `UV_CONFIG_FILE` and a typed `--index-url` - so one Windows machine read a
  drive-less `\x` as absolute on 3.12 and as relative on 3.13. All five use the
  module's `_is_absolute_path` now (a drive or a UNC share on Windows), which
  is 3.13's answer for every well-formed path on every interpreter. Every other
  absoluteness question in the product was read: `_shell._is_absolute` already
  required the drive, `pathlib` never changed, and the remaining
  `os.path.isabs` calls are POSIX-only branches or give the same answer both
  ways.
- **Every leg: `Process.wait()` resolves at the exit from CPython 3.13.15.**
  The two ubuntu failures were a real leak, not a race: see ADR-0238's #192
  amendment. CI's ubuntu py3.13 leg ran 3.13.16. The windows py3.13 leg passed
  those two cases - why was not measured (its shell is `cmd.exe` and its
  delegation stub keeps the child alive instead of leaving a holder) - and
  both graces wait on `wait_released` there too (on every leg it is stricter
  than the old `wait()` for a hook whose shell had already exited; ADR-0238).
