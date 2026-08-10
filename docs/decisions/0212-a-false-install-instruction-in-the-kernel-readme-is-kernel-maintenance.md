# 0212. Correcting a false install instruction in the kernel README is kernel maintenance

Status: Accepted (2026-08-10).
Date: 2026-08-10
Relates: ADR-0197 (the band rule — the kernel `aelix-agent-core` carries no delegation policy;
a kernel edit that adds no delegation surface is authorised by exception, naming its ADR).
ADR-0205 / ADR-0208 / ADR-0209 / ADR-0210 (the same exception mechanism, for the manifest
contract's own documentation and for session/stats data-integrity fixes). ADR-0205 is the
closest precedent: it authorised a **docstring-and-description-only** kernel edit on the
grounds that the published contract was the only place the text could live.
GitHub: #144 (the placeholder-install correction), #143 (the packaging sprint it rode in on).

**Provenance.** `packages/aelix-agent-core/README.md` is aelix-original packaging prose. This
is a factual correction to it, not a parity change and not a code change.

---

## The problem

`packages/aelix-agent-core/README.md` tells the reader:

```bash
pip install aelix
```

That instruction is false, and it is false in the way that costs the reader time rather than
announcing itself. Measured in a clean venv (`python3 -m venv`, not the repo `.venv`):
`pip install --no-deps aelix` **succeeds**, installs five files that are all
`aelix-0.0.0a0.dist-info/*`, leaves **no `aelix` executable** on `PATH`, and
`python -c "import aelix"` then raises `ModuleNotFoundError`. The published wheel is 1,331
bytes. The four PyPI names exist only as a metadata-only `0.0.0a0` name reservation.

A 404 teaches the reader something. A silent success teaches them the wrong thing.

#144 fixed this everywhere it appears — the root `README.md`, `README.ko.md`, the getting-started
guide, and the two sibling package READMEs (`aelix-ai`, `aelix-coding-agent`) — and stopped at
the kernel, because the band gate
(`tests/agents/test_p2_band_boundaries.py::test_kernel_untouched_vs_merge_base`) is
**path-scoped to `packages/aelix-agent-core` with no file-type filter**, so a README edit trips
it exactly as a `harness/core.py` edit would. Verified by doing it: with the caveat added and
no allowlist entry, the gate fails with
`['packages/aelix-agent-core/README.md']`.

Leaving it is not a neutral act. `packages/aelix-agent-core/README.md` is that package's
`readme = "README.md"` in its own `pyproject.toml`, i.e. its **PyPI long description** — the page
a visitor to `pypi.org/project/aelix-agent-core/` actually reads. The one README still carrying
the false instruction is the one served to strangers, while the two that were correctable are
served to the same strangers a click away. Three sibling packages currently disagree with each
other in public about whether `pip install aelix` works.

## The decision

Authorise `packages/aelix-agent-core/README.md` in `_KERNEL_CHANGE_ALLOWLIST`
(`tests/agents/test_p2_band_boundaries.py`), and add to it the caveat its two siblings already
carry, **verbatim** — same wording, same block-quote form, same position directly under the
`pip install aelix` fence — so the three package pages read as one voice rather than three
paraphrases of one fact.

**Why this is inside the band rule rather than an exception to it.** The gate's own docstring
states the principle: *"The band rule isolates delegation POLICY from the kernel; it does not
make the kernel unmaintainable."* The rule exists because P2/P3 put spawn, capability, consent
and registry decisions in `aelix_coding_agent` / `aelix_agents` on purpose, and a kernel that
quietly grew its own copy of any of that would dissolve the boundary the whole design rests on.
That is a question about **what the kernel is allowed to know how to do**.

A README is not something the kernel does. This edit adds no import, no symbol, no runtime
behaviour, no spawn site, no capability, no consent path and no registry — it changes no byte
that Python ever parses. Ask the only question the band rule actually asks — *is this
delegation policy?* — and the answer is no by construction: there is no execution here to be
policy about. `test_kernel_has_no_subagent_surface`, the always-armed half of the gate that
does not depend on a merge base, is unaffected and still passes; that is the half that would
catch a real band violation, and it is untouched precisely because nothing about the kernel's
surface changed.

The remaining question is the one the allowlist comment says matters — *did delegation create
this requirement?* No. The requirement came from PyPI name reservation and a beta install
story, which the kernel package participates in only because it is published at all. And the
text can live nowhere else: `readme = "README.md"` in
`packages/aelix-agent-core/pyproject.toml` names this file and no other as the long
description, so correcting the sentence anywhere else leaves the published page wrong. This is
the same shape as ADR-0205 — documentation whose only possible home is inside the kernel
package, because the kernel package is what publishes it.

The alternative — a false instruction knowingly left standing on a public page because a
delegation-policy gate happens to be path-scoped — trades an honest artifact for a green test.
That is the failure mode the band rule was written to prevent, not an application of it.

## Consequences

- The kernel freeze stays *by exception* with a written reason, per ADR-0197. This entry is the
  first documentation-only one in the family; it is listed separately from the code entries so
  the record shows plainly what class of change it bought.
- **The allowlist is path-granular, and this entry is a README.** Once listed, any *later* edit
  to `packages/aelix-agent-core/README.md` — including one that added delegation prose — passes
  the merge-base gate unreviewed. That is a pre-existing property of the list (already true of
  `pyproject.toml` and `harness/core.py`), not something this ADR introduces, but it is worth
  writing down: the file is now on the honour system, and the guard that remains is
  `test_kernel_has_no_subagent_surface` plus review.
- No behaviour change of any kind: no test, no import graph and no built artifact is affected.
  The file ships in the `aelix-agent-core` sdist and as its PyPI long description, which is the
  entire point.
- What is **not** authorised here: any edit to kernel *code*, and any further widening of the
  allowlist. A future documentation-only kernel edit needs its own line and its own reason —
  "0212 already opened the READMEs" is not one, and there is only one such README.
