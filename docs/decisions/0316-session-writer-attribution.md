# 0316. Report a cooperating session writer at the point of contention

Status: Accepted. Amended 2026-11-21 (#868) to limit beta.3 attribution to POSIX.

Date: 2026-11-21

Issue: [aelix-ai #868](https://github.com/handochan/aelix-ai/issues/868), R7 for [Studio #30](https://github.com/handochan/aelix-studio/issues/30)

Design base: `b7e9827be345dba2ad54900d4fa36841af522ad4`

Related decisions: ADR-0244, ADR-0275, ADR-0302, ADR-0235

## Context

The native macOS base rejects a second writer with generic wording. The pinned
ACP adapter maps native startup and branch refusal text to `-33001` with null
holder. The two-process branch probe measured a retained clone when the return
switch could not reacquire the original. A same-process signed reply in the
handoff model could reach the contender after a subsequent acquisition.

## Owner decisions

Owner, 2026-11-21, “제안대로 (권장)” on the Studio launch marker and cooperating
local launcher, “이대로 확정 (권장)” on the wire names, “제안대로 (권장)” on
the private locator and authenticated loopback proof, and “이 문구로 (권장)”
on the English headings and follow-up copy. The owner narrowed beta.3 attribution
on 2026-11-21 with “POSIX만 먼저, Windows는 후속 (권장)”.

## Decision

1. **Keep kernel admission authoritative.** The ADR-0244 writer lock decides
   admission. A real held POSIX acquisition can publish proof in beta.3. Lock I/O failure,
   contention and unsupported locking retain their existing outcomes. The
   runtime distinguishes contention from a lock I/O failure before cleaning
   up an unadopted file. If a contender acquired a newly published transcript,
   it keeps the transcript.

2. **Identify a cooperating launch at signing.** On POSIX, native interactive,
   print and JSON launches publish CLI origin. A Studio-managed Host and qualified ACP
   adapter pass `--session-writer-kind=studio` to the native RPC worker.
   Unmarked RPC, unmarked delegates and library writers without a specified
   origin remain unknown. Bind the kind for the lifetime of the launch,
   including transitions from an initial read-only or in-memory session.
   The trust boundary includes the local account and trusted OS principals.
   A principal with the same authority can deliberately declare Studio or
   construct the locator and responder. Fresh proof on each refusal suppresses
   any detected handoff. A change after verification can still race with display.

3. **Bind proof to the exact file held by the kernel.** Each acquisition gets a
   fresh secret and epoch. After acquisition, a private locator names a bounded
   IPv4 loopback responder. The challenge binds a fresh nonce, canonical lock
   slot and the identity read from the live kernel descriptor or handle. Both
   sides verify the currently named slot through checked local directory
   handles. The contender reopens the slot and rereads the locator before
   accepting the answer. An unavailable or changed identity yields unknown.
   The 75 ms budget covers connect, send and receive. Filesystem operations
   can take additional time.

   Revoke proof before releasing the kernel lock. Keep the locator for atomic
   replacement because a directory move can let old and successor locks coexist
   at the same canonical slot. The release after the successor published in
   the POSIX regression test left the successor's label available. POSIX fork
   detaches inherited proof sockets and the child's copies of descriptors
   without unlocking the parent. The fork test measures this on filelock 3.12
   and the locked dependency.

4. **Preserve the choices and control-v1 contract.** In interactive startup
   and live `/resume`, verified origin changes the heading to “This session
   is open in Aelix Studio.” or “This session is open in the Aelix CLI.”
   Unknown uses the existing heading. Post-choice text describes fork and
   read-only behavior without attributing it to a particular writer.

   Opted-in stdio startup contention keeps exit 1, empty stdout and the full
   legacy diagnostic. Its final stderr record is nonce-correlated and
   LF-framed. Require clean EOFs and one reserved-prefix line. A newline in
   a POSIX path can introduce extra prefix lines in the legacy diagnostic.
   Those streams yield an ordinary startup failure with the diagnostic
   preserved. Established opted-in stdio advertises `sessionLockErrors` and
   carries `errorDetails` on a direct switch or explicit branch return
   contention. The details carry the failure kind and optional holder kind.
   Unopted clients retain their existing envelopes.
   [Contract and examples](../contracts/control-v1.md#session-lock-errors-on-opted-in-stdio-868-adr-0316)

5. **Verify the original ACP binding after branch failure.** A native branch
   may publish a clone before reporting failed return to the original. The
   adapter verifies the same worker generation, terminal settlement and
   original binding using a correlated classic `get_state` while the mutation
   fence is held. With no trustworthy terminal result or original binding,
   quarantine the worker before any later mutation. Explicit load can reopen
   the stored original mapping after disposal releases its leases.

   The native return failure preserves the clone file with only the original
   ACP mapping. A later failure after ACP commits the new clone ID preserves
   both mappings. The post-commit tool restoration fault injection measured
   both mappings and a subsequent write on the verified original binding.
   The managed Host opt-in is integer `1` at
   `initialize.clientCapabilities._meta["aelix/sessionLockHolder"]`. ACP
   retains the current `-33001` shape and sends a known holder only to
   an opted-in client.

## Alternatives and consequences

A record, mtime or PID cannot prove a live acquisition through crash and
takeover. Putting owner bytes in the lock sidecar couples proof to Windows'
sidecar deletion rules. A stdout startup frame or a replacement for the
existing error string would change the established RPC contract.

Pi classic at `65dda728` opens the selected session directly and uses
process-local leaf state. Aelix retains ADR-0244 writer exclusion.
[Pi switch source](https://github.com/earendil-works/pi/blob/65dda7289b7116dbcf9c1ca50494b023fed391db/packages/coding-agent/src/core/agent-session-runtime.ts#L215)

The installed Studio renderer and artifact pins require separate qualification.
Application attestation, history synchronization, and extra extension or
general RPC-client projections remain outside beta.3 scope.

## Note (2026-11-21, #868) — Windows attribution deferred

**Windows.** In beta.3, no holder locator is published, no responder starts,
and readers return `unknown` without a challenge. On the Windows runner profile,
the ancestor-directory ACL check rejected holder proof with `untrusted lock
proof permissions`, as shown by the directory-pin failure in PR #880. The
Windows backend remains in the tree for #881. Filelock continues to
enforce writer exclusion. The existing writer-lock tests passed in that run.
`test_windows_marked_writer_keeps_generic_contention` covers the generic
startup and switch text and the unknown-holder wire shape for a Studio-marked
writer. [PR #880 CI run 44861771991](https://github.com/handochan/aelix-ai/actions/runs/44861771991)
