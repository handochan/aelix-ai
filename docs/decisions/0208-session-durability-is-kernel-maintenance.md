# 0208. Session-durability fixes are kernel maintenance, not delegation policy

Status: Accepted (2026-08-07).
Date: 2026-08-07
Relates: ADR-0197 (the band rule — product-core and the bundled extension carry
delegation policy; the kernel `aelix-agent-core` does not). ADR-0205 (the
precedent that the band rule "isolates delegation POLICY from the kernel; it
does not make the kernel unmaintainable" — a kernel edit that adds no delegation
surface is authorised by exception, naming its ADR).
GitHub: the session-durability track (beta re-survey 2026-08-07, "Track S").

**Provenance.** The session store is aelix-original plumbing; these are bug
fixes to it, not a parity change.

---

## The problem

The JSONL session store lives in the kernel band
(`packages/aelix-agent-core/src/aelix_agent_core/session/`). A beta re-survey
reproduced, end to end against the real CLI, three data-integrity defects in it:

1. **A single crash-truncated line permanently bricked `aelix --continue`** in a
   directory: the load path raised on the first bad line with no skip-and-continue,
   and `find_most_recent` re-selected the same dead file forever.
2. **A valid-but-unterminated tail silently fused with the next append** — losing
   both the last committed turn and the message just typed on resume (this one was
   caught by the reviewer as a hole in the first version of the recovery fix).
3. **Session files were created world-readable (0644)** while `auth.json` is 0600,
   so an agent that ran `env` or read a key wrote the credential into a
   world-readable transcript.

None of these can be fixed anywhere but the kernel — the defective code IS the
kernel session layer. Per ADR-0197's own principle, "core's unfinished debt is
fixed in core"; the only question the band rule asks is whether the change is
*delegation policy*, and none of these is.

## The decision

Authorise the following kernel files in `_KERNEL_CHANGE_ALLOWLIST`
(`tests/agents/test_p2_band_boundaries.py`), each a data-integrity fix with **no
delegation surface**:

- `session/jsonl_storage.py` — corrupt-line recovery: skip-and-continue past a
  malformed line, prune orphaned entries transitively, rewind the leaf to a
  surviving entry, and heal an unterminated tail on append (decided by whether
  the loaded bytes ended in `\n`, independent of whether recovery ran).
- `session/fs.py` — session files at 0600 and session dirs at 0700 via the
  O_CREAT mode + `os.fchmod` on the open fd (no world-readable creation window),
  plus a tighten-on-write pass over pre-existing 0644 files, and a 0600
  import-destination for `copy_file`.
- `session/jsonl_repo.py` — updated for the new load return shape (a frozen
  `_LoadedSession` carrying the `ends_with_newline` flag).
- `session/__init__.py` — re-exports the recovery type.

`test_kernel_has_no_subagent_surface` — the always-armed half of the band gate —
**still passes**: these changes add no `aelix_agents` import, no spawn behaviour,
no cap, no registry and no consent policy. They are session I/O correctness only.

## Consequences

- The kernel freeze remains *by exception*: this ADR is the written reason the
  four files were opened, exactly as the RPC-sprint post-mortem in the allowlist
  comment demands ("a reason that is not written down is a reason that stops
  existing").
- No behaviour change for a clean session file — recovery is a no-op when the
  file parses and ends in a newline; the strict header check is unchanged, so a
  corrupt header or wholesale-garbage file is still rejected.
- The single-writer session lock (the two-terminal orphaned-branch loss) is
  **out of scope** here — it is a design decision with a Windows leg (#46) and is
  tracked as a follow-up, not authorised by this ADR.
