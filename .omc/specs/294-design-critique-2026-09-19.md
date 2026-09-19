# #294 design critique (critic pass on rev 1, 2026-09-19)

Baseline: `main` = `origin/main` = `e1fef5f`. Everything below was read or run against that commit, read-only.
Probes (this scratchpad, run with `uv run --no-sync python <file>` from the repo root):

- `measure_294.py`: the design's probe, re-run. Reproduces §0 [1]–[3] (ids differ).
- `critic_probe_294.py`: monkeypatches the design's §A.3/§A.4 into memory (rename_file, write-all + fsync `write_file`,
  `_publish_file`, `create(entries=)`, fork via `create`, re-armed `_append_line`), then checks claims. Labels [a]–[g].
- `critic_probe_294_decoder.py`: which wire changes make the released decoder drop a line.
- `critic_probe_294_fork_keys.py`: which unknown keys survive a released `fork`.
- `copyplug.py`: a pytest plugin that swaps `LocalFileSystem.copy_file` for a candidate. Used with
  `COPY_VARIANT=<literal|proposed> PYTHONPATH=<scratchpad> uv run --no-sync pytest tests/session/test_session_file_permissions.py tests/runtime/test_import_from_jsonl_real.py -q -p copyplug`.

## Verified: no change needed (listed once so the next reader need not re-check)

- **§0 [1]–[3] reproduce.** Output: `[1] … entries loaded = ['e58a9d6a'] | skipped = (3,) | orphaned = ('zz000002', 'zz000003')`,
  `[2] … picks …jsonl with 2 of 5 entries`, `[3] after reload: ['turn-1'] | orphaned = ('c75a7737',)`.
- **§0.1 mechanism.** Verified. `entries.py` `entry_from_json` (last line) raises `ValueError`. `_message_from_dict` raises
  on an unknown role. `jsonl_storage.py` `_parse_entry_line` wraps any `Exception` as `invalid_entry`.
  `_load_jsonl_storage` skips the line and then runs the orphan pass. The §A.1.3 required-key list matches
  `entry_from_json`'s `data[...]` and `.get(...)` reads exactly.
- **§0.2.** Verified. `JsonlSessionRepo.fork` / `fork_from` call `create` and then run an `append_entry` loop.
  `find_most_recent` sorts by mtime and sniffs only the header (`_is_valid_session_file`). `LocalFileSystem.copy_file`
  truncates with `os.open(... O_TRUNC)` and then runs `shutil.copyfile` onto the same path.
- **§0.3.** Verified. `_append_line` clears `_needs_newline` only after a successful write and never sets it on failure.
  `append_file` / `write_file` discard `os.write`'s return value.
- **§A.2.** Verified. `append_entry` and `set_leaf_id` each make one `append_file` call. `Session.move_to` makes two
  calls, a `leaf` then a `branch_summary`, and every prefix of those writes is valid.
- **§A.3, the OSError path.** Verified correct in these cases:
  - Nothing was written: the extra line is blank, `if line.strip()` filters it, and it is not counted as a skip.
    Skip numbering runs over non-blank lines only.
  - A partial fragment was written.
  - The healing `\n` was itself (part of) the failed write.

  **No path loses a durable entry.** Later entries parent on the in-memory leaf, because `Session.append_*` reads
  `get_leaf_id()` and never the failed id, so the orphan pass prunes nothing extra. The harness swallows append errors
  at DEBUG (`harness/core.py`, the `session.append_message raised on message_end` branch), so appends do continue
  straight after a failure. That makes the fix matter.
- **§A.4 callers.** Verified. `write_file` has one production caller, `JsonlSessionStorage.create`. `copy_file` has one,
  `AgentSessionRuntime.import_from_jsonl`. No other code in `packages/` writes session files: the write and edit tools
  use their own `WriteOperations` / `EditOperations`, and the other `os.replace` users are settings, trust, catalog and
  statusline files.
  - No `FileSystem` subclass, fake or `isinstance(…, FileSystem)` exists in `packages/` or `tests/`, so adding
    `rename_file` to the Protocol breaks no caller or test.
  - No test pins the Protocol's member set.
  - The temp name `<x>.jsonl.<hex>.tmp` is invisible to `list` and `find_most_recent` (both filter
    `endswith(".jsonl")`), and nothing else in the repo lists session directories.
- **§A.4 `create(entries=)`.** Verified (probe [b]). It writes bytes identical to header + append loop and gives the
  same leaf and labels, mode 0600, no temp left.
- **§A.5 feasibility.** Every listed conformance case passes against `MemorySessionStorage` as it is today. The label
  strip/clear, leaf fold, `set_leaf_id` (`not_found` raised before appending), `get_entries` copy, and path-to-root
  errors are the same code in both backends.
  - **"Every prefix is a session" holds for the described script.** A label becomes the leaf and resolves. A
    `set_leaf_id` target always precedes the entry. `set_leaf_id(None)` starts a new root. None of the asserted calls
    read `first_kept_entry_id`.
  - **Short-write proxy.** A `types.ModuleType` subclass that delegates to the real `os` and swaps in a 7-byte `write`
    works, and reopen is clean (probe [g]).
- **Kernel gates.** Verified. `session/{fs,jsonl_storage,jsonl_repo,entries}.py` are already in
  `_KERNEL_CHANGE_ALLOWLIST`. A docstring-only edit to `storage.py` passes `_is_prose_only` (AST compare). The
  vocabulary gate scans only `packages/aelix-agent-core/src/**/*.py`.
  `uv run --no-sync pytest tests/agents/test_p2_band_boundaries.py -k kernel` → `2 passed` on main.
- **`aelix.` namespace.** No concrete problem found. `custom_type` is never validated, parsed or used as a
  path/identifier. It is used only as a renderer dict key (`extensions/api.py`) and as a `[custom_type]` label
  (`tui/render.py`). `bash_execution` (`cli/repl.py` `handle_user_bash`) is the only first-party custom_type.
- **§D pi fact.** Verified. `legacy-v3.ts` `parseLegacyV3Entry` @ `36b60d2` rejects `type: "leaf"`, and
  `JSON.parse("")` throws on a blank line.

## Findings

### 1. BLOCKER: the §A.4 `copy_file` spec contradicts §0's "keep green" list, and on Windows it corrupts the copy

"stage into a temp … created 0600 (O_CREAT|O_EXCL), copy bytes (write-all), fsync" reads as a manual `os.write`
loop on that descriptor. That spelling breaks two things.

**(a) An existing test fails on every platform (measured).**
`tests/session/test_session_file_permissions.py::test_copy_file_is_owner_only_before_content_lands` spies on
`fs_module.shutil.copyfile`. It asserts one call whose `dst` already exists at 0600.

- A literal implementation never calls it: `observed=[] -> FAIL` (probe [c]).
- Run through `copyplug.py` with `COPY_VARIANT=literal`: `1 failed, 18 passed`.

**(b) Windows: the bytes are translated (inferred from the stdlib; not run on Windows).** `os.open` without `O_BINARY`
returns a text-mode descriptor on Windows. The stdlib itself adds the flag by hand for exactly this reason:
`tempfile._bin_openflags |= O_BINARY`, and `_pyio.FileIO.__init__` does `flags |= getattr(os, 'O_BINARY', 0)`. Both
were read from this interpreter. So a byte loop turns LF into CRLF, and CRLF into CR CR LF.

- `test_copy_file_preserves_mtime` and `test_copy_file_tightens_preexisting_loose_destination` assert
  `read_text() == "{}\n"`. They would fail on the gating windows-latest leg.
- `/import` would stop being a byte copy.
- The same text mode means Aelix-written session files are already CRLF on Windows. That is harmless for today's
  readers (universal newlines) but matters for item 3.

**Proposed design text** (replaces the `copy_file` bullet):

> `copy_file`:
>
> 1. Create the temp `f"{destination}.{uuid4().hex[:12]}.tmp"` with
>    `os.open(..., O_WRONLY|O_CREAT|O_EXCL|getattr(os,"O_BINARY",0), 0o600)` and close it.
> 2. `shutil.copyfile(source, temp)`. This is binary and byte-exact. The existing spy test keeps its meaning, because
>    its `dst` is now the temp and the temp exists at 0600.
> 3. Reopen the temp with `O_WRONLY|O_BINARY` and `fsync` it, then close.
> 4. `os.utime(temp, (src.st_atime, src.st_mtime))`, then `os.replace(temp, destination)`.
> 5. On `BaseException`, unlink the temp.
>
> Never push source bytes through a descriptor opened without `O_BINARY`.

Measured with `COPY_VARIANT=proposed`: `19 passed` (the permission tests plus `test_import_from_jsonl_real.py`).

Also:

- Add `tests/runtime/test_import_from_jsonl_real.py` to §E.
- Say that the windows CI legs are the verification for (b). Read their logs, not only the check mark.

### 2. SHOULD-FIX: §E's expected probe output for [2] is wrong, and the probe lives in a scratchpad

**The expectation cannot be met.** After the change, `fork` never calls `append_file`, so the probe's
`_FailAfter(fail_on_append=3)` never fires. The fork succeeds, and `find_most_recent(dst)` returns the complete fork.
Measured with the design patched in (probe [a]):
`fork raised=None; append_file calls during fork=0; find_most_recent(dst)=a file with 5 entries`.
The expected `[2] find_most_recent(dst) = None` is unreachable. The probe also no longer exercises any failure.

**Proposed change:**

- Replace `_FailAfter` with a `LocalFileSystem` subclass that fails `rename_file`, plus a variant whose `write_file`
  writes half the content and then raises.
- Expected output: `[2] fork raised: SessionError Failed to create session …(injected)` and
  `[2] find_most_recent(dst) = None`.
- Add a no-injection control line: `fork complete: 5 of 5`.
- Copy the probe to `.omc/specs/294-measure.py` and cite that path in the ADR, because §D quotes its output
  "verbatim". This session's scratchpad dies with the session (CLAUDE.md rule 4's reason).

### 3. SHOULD-FIX: the §A.6 torn-tail test is wrong at one cut per line on POSIX, and at two on Windows

"for every entry line k … and a cut strictly inside it" includes the cut that keeps the whole JSON object and drops
only the `\n`. That line parses, and ADR-0208 keeps it, as the design itself says in §0.

Measured on a 205-byte JSON line (probe [d]):

| Cut offset | Entries | `skipped_lines` |
| --- | --- | --- |
| 1 | `['m0','m1']` | `(4,)` |
| 204 | `['m0','m1']` | `(4,)` |
| 205 | `['m0','m1','m2']` | `recovery=None` |

On Windows the file is CRLF (item 1b). A cut that keeps `{…}\r` is read back through universal newlines as a
terminated, complete line, so it is kept as well.

**Proposed text:**

> Let J be line k's JSON bytes: split on `b"\n"`, then `rstrip(b"\r")`. Cut at c ∈ [1, len(J)-1] bytes into J and
> expect the entries before k, `skipped_lines == (k,)` and `orphaned_entries == ()`. One append then reloads with the
> new entry parented on the recovered leaf.
>
> Separately pin c = len(J), and on Windows also J + `\r`. That line is kept, `recovery is None`, and the next append
> heals the tail. This is ADR-0208's kept case, pinned so it stays a decision rather than an accident.

### 4. SHOULD-FIX: §A.1.3 permits what a released fork destroys, and omits value types

This is the rule #199 is told to follow.

**(a) A released fork drops keys.** `fork` and `fork_from` decode into dataclasses and re-encode with
`entry_to_json`. So a released Aelix silently drops every unknown key, except inside `CustomEntry.data`. Measured
(`critic_probe_294_fork_keys.py` on main):

| Unknown key at | Survives a released fork |
| --- | --- |
| header (`futureHeaderKey`) | False |
| entry (`futureKey`) | False |
| message (`futureMsgKey`) | False |
| content block (`futureBlockKey`) | False |
| `CustomEntry.data` (`futureDataKey`) | True |

The new `create(entries=)` fork behaves the same. "A known type may gain optional keys" therefore holds only for
readers, not for anything that must survive a branch or a `/fork`.

**(b) Changing a read key's JSON type also drops the line.** It is not only losing or renaming a required key.
Measured (`critic_probe_294_decoder.py`):

- These drop the line:
  - `message.content` as a string. That is pi's own `UserMessage` shape: `'str' object has no attribute 'get'`.
  - `content: null`.
  - A non-object content block.
  - A `message` that is not an object.
  - `tokensBefore: "1e3"` or `null`.
  - A non-string `parentId`.
- These are silently misread:
  - `display: "false"` reads as `True`.
  - `tokensBefore: 12.9` reads as `12`.
- An unknown content-block `type` is kept, but as a raw `dict` outside the typed pipeline. It is not a record carrier.

**Proposed rewrite of A.1.3**, plus two new lines:

> 3. A known type may gain an optional key only when losing it is acceptable. Every released Aelix drops unknown
>    header, entry, message and content-block keys when it forks. Only `CustomEntry.data` survives, so anything that
>    must survive a fork goes there.
>
> 3b. Never change the JSON type of a key the released decoder reads:
>
> - `message` is an object.
> - `message.content` is a list of objects.
> - `tokensBefore` is int()-coercible.
> - `parentId` and `leaf.targetId` are a string or null.
> - `display` is a JSON bool.
>
> 3c. A first-party `CustomMessageEntry.content` is a plain string. List content is decoded as raw dicts and handed
> untyped to `create_custom_message`.

Add to §A.6: "a released fork keeps an unknown `data` key and drops an unknown entry-level key". Put the same
sentence in the ADR's consequences for #199.

### 5. SHOULD-FIX: §A.3 re-arms on the wrong exception class and overstates what the fix guarantees

**(a) Wrong exception class.** Re-arm on `BaseException`, then translate only `OSError`. With the new write-all
loop, an append can stop between two `os.write` calls with no OSError involved: a second Ctrl-C raising
`KeyboardInterrupt` there, or a `CancelledError` in any `FileSystem` whose `append_file` awaits (e.g. `to_thread`).
Either leaves a fragment just as ENOSPC does. The OSError-only re-arm then fuses the next append onto it: the
§0 [3] cascade again. The fix is one word and costs at most a blank line.

**(b) The claim is false in one case.** "The fragment is skipped on load as its own unparseable line" does not hold
when the failure lands after the last JSON byte, or at `os.close` after a full write. Measured (probe [e]):

- memory `['turn-1','turn-3']`
- reload `['turn-1','turn-2 (caller told it FAILED)','turn-3']`, `recovery=None`, path `['turn-1','turn-3']`
- when that failed append was the last write, the reload's leaf is the entry the caller was told failed.

Neither result loses anything, but both contradict the design's text and are unpinned.

**Proposed text:**

> A failed append is never in memory. On disk it is one of three things:
>
> - absent;
> - a fragment, skipped on load;
> - a complete line, kept on load. It becomes an off-path sibling if later appends happened, or the resumed leaf if
>   it was the last write.
>
> Pin the all-but-the-newline twin in §A.6 as kept, with `recovery is None` and the entry off the path, so nobody
> later "fixes" it into a truncation (which §A.3 forbids for #137 reasons).

### 6. SHOULD-FIX: §E's `ruff format --check <changed .py files>` fails before any change

The repo deliberately does not run it: see the `.github/workflows/ci.yml` header and ADR-0152, "the codebase is
hand-formatted". Measured on main:

`uv run --no-sync ruff format --check session/{entries,fs,jsonl_repo,jsonl_storage,storage}.py tests/agents/test_p2_band_boundaries.py`
→ `6 files would be reformatted`

The implementer will either report a red step or reformat six hand-formatted files, one of them the gate test. The
kernel gate would pass, since its AST compare ignores layout, but the result is a large noise diff that ADR-0152
rejects.

**Proposed change:** drop it from §E, or restrict it to the two new test files. Keep `ruff check`.

### 7. SHOULD-FIX: the direction doc still contradicts the rule in #199's own row

§B limits the `docs/05-post-beta-direction.md` edit to the §11 paragraph and the row-10 cell ("Nothing else in that
file"). But appendix B row 3 is the #199 item, and it reads:

`| 3 | 자식 세션과 부모의 spawn 계보 기록 | 새 필드 또는 새 entry 타입 | … |`

A new entry type is what §A.1.1 forbids. A new field is what item 4(a) shows a released fork destroys. That row is
the first thing #199's implementer will read.

**Proposed change:** add to §B: row 3's anchor becomes "`CustomEntry` + `aelix.` `custom_type` (ADR-0242); 새 type과
기존 type의 새 필드는 출시본이 가지치기하거나(fork 시) 버린다".

### 8. SHOULD-FIX: the conformance catalogue needs "JSON values" stated, and JSONL's non-JSON failure needs a decision

Measured (probe [f]):

- **A tuple comes back as a list.** A tuple in `data` equals the original after Memory `get_entry` (True) but not
  after a JSONL reopen (False). The same happens for tuples in `details`, `ToolCallContent.input`, and so on.
- **JSONL raises outside the error taxonomy.** A dataclass in `data` makes JSONL raise a raw `TypeError`
  (`SessionError? False`), while Memory accepts it. A `custom_message` with `content=[TextContent(...)]` is exactly
  what the dataclass-typed API invites (`CustomMessageEntry` docstring; `harness/core.py` persists
  `PendingCustomMessageWrite.content` raw).
- **NaN is written as bare `NaN`.** The line reads `data": {"v": NaN}`, which pi's `JSON.parse` and any RFC 8259
  reader reject.

**Proposed text in §A.5:**

> Payloads (`data`, `details`, `content`, message fields) are JSON values: lists not tuples, finite floats, no
> dataclasses. The catalogue uses only those.

Then pick one of two options:

- **(i)** Pin "JSONL raises `TypeError` for a non-JSON payload; Memory does not" as a documented JSONL-only case.
- **(ii)** Translate it to `SessionError("invalid_entry")` in `_append_line` and `_publish_file`, serializing every
  line before staging.

Either way, say which in the ADR.

### 9. SHOULD-FIX: two §A.6 failure injections do not exercise the failure they claim to pin

**(a) `copy_file`.** Injecting only at `os.replace` proves that the old destination survives a failed publish. It
never exercises the §0.2 defect, "a failure mid-copy leaves a half file". Add an injection during the byte copy, via
an fs-module `shutil.copyfile` wrapper that writes half and then raises (with item 1's spelling). Assert that the old
destination bytes are intact, or that no destination exists, and that no `*.tmp` is left.

**(b) `write_file`.** A subclass that raises before calling `super()` never creates the temp, so "no `*.tmp` left" is
vacuous. The injected `write_file` must write half the content via `super()` and then raise.

### 10. NIT: two cross-references point at §E instead of §D

§0 says "(ADR-0208 — pi v4 drops it; see §E)" and the Pi paragraph says "§E records each one". The divergences are
listed in §D. §E is the verification commands.

### 11. NIT: the `entries.py` module docstring still says "There are 11 entry types"

The union has ten, and the file's own `# === Base + 10 entry types` comment agrees. §B already edits that module's
docstrings to state that the closed set is ten. Fix the "11" in the same pass, or the file will contradict itself
right next to the new sentence.

### 12. NIT: the stated reason does not justify `fsync` in `write_file`

§A.4 justifies the fsync as "a rename that outlives its data can replace a complete file with an empty one". That
applies only to a replace over an existing file, which is `copy_file`. `create` and `fork` always publish to a fresh
path. A zero-length `.jsonl` left by a power loss is already skipped by `find_most_recent` (`_is_valid_session_file`)
and by `list` (`invalid_session`).

Meanwhile it adds one fsync to every session start and to every test that creates a session.
`JsonlSessionRepo(` / `JsonlSessionStorage.create(` appear 147 times in 53 test files, plus the subprocess CLI tests.
On Windows each fsync is `FlushFileBuffers`.

**Proposed change:** either justify it ("forks and new sessions survive power loss"), or keep the fsync only in
`copy_file`. Either way, compare the windows leg's wall time against main.

### 13. NIT: the citation gate will need a hand repair

`session/session_cwd.py`'s docstring cites `session/fs.py:57-81`, which is exactly the `FileSystem` Protocol body
this change edits. What happens depends on where the edit lands:

- **Inside lines 57–81.** If `rename_file` is inserted there or the Protocol docstring changes, `--fix` cannot
  relocate the block. It prints "the block was EDITED, not moved — re-derive by hand".
- **After `copy_file`.** If `rename_file` is appended there, the gate stays silent, but the citation quietly stops
  covering the whole Protocol.

Either way, re-derive the range by hand and then run `--lock`.

`_export_html/format.py` and `tests/coding_agent/test_export_html.py` cite `fs.py:27-28`. That block moves if the
module docstring grows; `--fix` handles a move.

All of these are prose edits, so the kernel gate stays green even for the non-allowlisted `session_cwd.py`. Also add
`tests/test_citation_drift.py` to §E: it pins the count of ungated citations, and a new pi `*.ts:NNN` citation in the
new tests would fail it.

### 14. NIT: temp files left by a crash are never collected

A SIGKILL between `write_file` and `rename_file` leaves `<name>.jsonl.<hex>.tmp`, holding a whole transcript, forever.
The random suffix is never reused, and nothing sweeps the directory. Pi uses the deterministic `${path}.tmp`.

**Proposed change:** use `f"{path}.tmp"` (destinations are unique per create/fork), or sweep stale `*.jsonl.*.tmp` in
`list`. At minimum, record it in the ADR.

### 15. NIT: the backends validate the leaf at different times, and the suite should say which is allowed

`MemorySessionStorage.__init__` raises `invalid_session` when the folded leaf dangles. JSONL raises only in
`get_leaf_id`. No listed case hits this. But the rule "a new backend is added by adding a param here, and it must pass
unchanged" needs the contract spelled out: reopening a state with a dangling leaf may raise either at reopen or at
`get_leaf_id`.

### 16. NIT: "one write" in §A.2 needs precise wording

With the write-all loop, one `append_file` call can issue several `os.write` syscalls. Under a second writer (#137),
another line can land between them. Say that "one write" means one `FileSystem` call. Also drop `append_file`'s "≤
PIPE_BUF atomicity" docstring claim while that docstring is being rewritten: PIPE_BUF is a pipe/FIFO guarantee, not a
regular-file one.

### 17. NIT: pi files make §C's follow-up more than defence in depth

pi's harness v3 wrote `active_tools_change` records. It was introduced in pi `b63d263` (2026-05-28), and
`legacy-v3.ts` still accepts it. Aelix's decoder prunes such records and their whole subtree on `/import` of a pi
session today. So "keep unknown types as opaque nodes" fixes a present-day import loss, not only a future one. Note
it in the handoff's follow-up entry.

### 18. NIT: the one-process concurrency case is vacuous

`LocalFileSystem.append_file` never awaits, so `asyncio.gather` runs the appends back to back, and the case passes
without the lock. Use a subclass whose `append_file` does `await asyncio.sleep(0)` before writing, so that the
`asyncio.Lock`'s FIFO order is what is actually tested.

### 19. NIT: two bugs found in passing (out of scope; found by reading, not run)

- **(a) `!cmd` output never reaches the model.** `cli/repl.py` `handle_user_bash` records the output as a `CustomEntry`
  `bash_execution`, and its docstring says the output is "included in session context". `build_session_context`
  skips `custom` entries, so the one first-party type §A.1.4 cites already breaks §A.1.1's "must reach the model →
  `CustomMessageEntry`".
- **(b) Forking before the root copies everything.** `AgentSessionRuntime.fork(<root user msg>, position="before")`
  passes `entry_id=None`, and `get_entries_to_fork` treats that as "full copy".

File each separately if confirmed.
