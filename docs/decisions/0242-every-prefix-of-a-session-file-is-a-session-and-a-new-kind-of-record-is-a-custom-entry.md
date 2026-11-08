# 0242. Every prefix of a session file is a session, and a new kind of record is a custom entry

Status: Accepted (2026-09-19). Amended 2026-11-07 (#721, #611 S1a): rule 5, a write
whose outcome is unknown is settled before the next parent is chosen.
Date: 2026-09-19
Supersedes/relates: ADR-0208 (session durability — **extended**; its
non-destructive recovery stays exactly as it is), ADR-0022 (the v3 JSONL format
this keeps), ADR-0211 (the message decoder, which must stay total), ADR-0235 (pi
is a reference, not a target, so the divergences below are recorded, not
argued). Owner decision #10 in `docs/05-post-beta-direction.md` §11 ("저장
방식"): **JSONL stays; operation records are added; SQLite comes later as a
backend, not a migration.**
Issue: #294. First consumer of the record rules: #199 (child-session records),
which is not part of this change.
Design spec: `.omc/specs/294-design-2026-09-19.md` (rev 2).
Probe: `.omc/specs/294-measure.py` (the same file runs on `main` and on the
branch).

The session store had no written contract. The `SessionStorage` Protocol is ten
signatures; what a write *is*, what the file may look like after a crash, and how
a newer Aelix may add a record without breaking an older one were answered only
by whatever the code happened to do. Three of those answers lost data.

## What was broken, measured on `main` e1fef5f

The probe's output on `main`, as recorded in the design (§0) — ids differ run to
run:

```
[1] unknown type: lines on disk = 5 | entries loaded = ['efa3238d'] | skipped = (3,) | orphaned = ('zz000002', 'zz000003')
[2] fork raised: SessionError Failed to append session entry c5487a19: [Errno 28] No space left on device (injected)
[2] find_most_recent(dst) picks 2026-09-19T07-20-31-445Z_a86a556b-….jsonl with 2 of 5 entries
[3] torn append raised: SessionError
[3] in memory before reload: ['turn-1', 'turn-3', 'turn-4']
[3] after reload           : ['turn-1'] | orphaned = ('4e05945d',)
```

The probe as kept in `.omc/specs/` also prints a no-injection control fork,
which on `main` reads `[2] fork complete: 5 of 5` (re-measured for this ADR on
`main` e1fef5f; every other line reproduced).

1. **An unknown entry `type` costs every entry below it.** `entry_from_json`
   raises on an unknown `type`, `_parse_entry_line` turns that into a skipped
   line, and ADR-0208's orphan pass then prunes every entry whose parent chain
   runs through it. That is what beta.1 and beta.2 do, and they are released, so
   it can never change for them. The same happens for an unknown message `role`,
   a missing required key, and a key whose JSON type changed (`message.content`
   as a string).
2. **A fork interrupted part-way left a truncated session that `--continue`
   resumed.** `fork` and `fork_from` wrote the header, then one `append_entry`
   per entry; `find_most_recent` sorts by mtime and sniffs only the header, so
   it picked the two-of-five file. Import had the same shape: `copy_file`
   truncated the destination in place (`O_TRUNC`) and then copied onto it, so a
   failure mid-copy left a half file — and when that name already held a
   complete session, the session was gone.
3. **One failed append lost every turn after it on the next load.** The healing
   newline flag was never set on a failure, so the next append was glued onto
   the fragment; the fused line did not parse, and every later entry descends
   from the entry inside it. The harness makes this reachable: its
   `message_end` path catches every append exception and logs it at DEBUG, so
   the turn goes on appending. Separately, `append_file` and `write_file`
   ignored `os.write`'s return value, so a short write dropped the tail of a
   line with no exception at all — the same ending.

After this change — the same probe file, run on the branch:

```
[1] unknown type: lines on disk = 5 | entries loaded = ['6b8aa0ab'] | skipped = (3,) | orphaned = ('zz000002', 'zz000003')
[2] fork raised: SessionError Failed to create session …/2026-09-19T08-23-30-806Z_45fd11b0-f154-4809-85bf-782a2a6d1913.jsonl: [Errno 28] No space left on device (injected at rename_file)
[2] find_most_recent(dst) = None | left in the session dir: []
[2] fork raised: SessionError Failed to create session …/2026-09-19T08-23-30-807Z_f537de02-65e5-4f51-9179-b05bfa05f671.jsonl: [Errno 28] No space left on device (injected mid write_file)
[2] find_most_recent(dst) = None | left in the session dir: []
[2] fork complete: 5 of 5
[3] torn append raised: SessionError
[3] in memory before reload: ['turn-1', 'turn-3', 'turn-4']
[3] after reload           : ['turn-1', 'turn-3', 'turn-4'] | orphaned = ()
```

[1] is unchanged **on purpose**: it is the released loader, and the rules below
exist because of it. [2] injects where each side can fail — `main` builds a fork
by appending, so it fails the third `append_file`; the branch publishes the fork
as one file, so it fails the rename and, separately, a staging write that lands
half its bytes. The probe prints the file's basename; the message carries the full
path.

## The rules

### 1. Records evolve only through `CustomEntry` — the part #199 must read

1. **The entry `type` set is closed** at the ten on disk today: `message`,
   `thinking_level_change`, `model_change`, `compaction`, `branch_summary`,
   `custom`, `custom_message`, `label`, `session_info`, `leaf`. A new kind of
   record is a `CustomEntry` (`type: "custom"`, `customType`, `data`) — every
   released Aelix decodes it and keeps its descendants. A record that must reach
   the model or the transcript is a `CustomMessageEntry` instead.
2. **The message `role` set is closed** at `user` / `assistant` / `toolResult`.
3. **Everything that must survive goes inside `CustomEntry.data`.** A fork
   (`fork`, `fork_from`) decodes into dataclasses and re-encodes, so every
   released Aelix drops every key it does not know — except inside `data`:

   | unknown key at | survives a released fork |
   | --- | --- |
   | header (`futureHeaderKey`) | no |
   | entry (`futureKey`) | no |
   | message (`futureMsgKey`) | no |
   | content block (`futureBlockKey`) | no |
   | `CustomEntry.data` (`futureDataKey`) | **yes** |

   So a known type may gain an optional key only where losing it on a fork is
   acceptable (readers ignore unknown keys), and a required key is never removed
   or renamed. Required today: `message.message`;
   `thinking_level_change.thinkingLevel`; `model_change.provider` + `modelId`;
   `compaction.summary` + `firstKeptEntryId` + `tokensBefore`;
   `branch_summary.fromId` + `summary`; `custom.customType`;
   `custom_message.customType` + `content` + `display`; `label.targetId`; `id`
   and `timestamp` on all. The header may gain keys with the same fork caveat,
   but **`version` stays `3`**: any other value makes every released Aelix
   refuse the whole file.
4. **Never change the JSON type of a key the released decoder reads.**
   `message` is an object; `message.content` is a list of objects (a string —
   pi's own `UserMessage` shape — drops the line); `tokensBefore` is an int;
   `parentId` and `leaf.targetId` are a string or null; `display` is a JSON
   bool (the string `"false"` reads as `True`). A first-party
   `CustomMessageEntry.content` is a plain string.
5. **First-party record kinds use a `customType` in the `aelix.` namespace**
   (#199 names its own, e.g. `aelix.child_session`). Extensions choose their
   own through `append_entry`; the prefix keeps the two apart. `custom_type` is
   only ever a renderer key and a display label — nothing parses it. The one
   existing first-party type, `bash_execution`, predates the rule and keeps its
   name. Documented, not enforced.

   **Amended 2026-09-20 (#299).** `bash_execution` keeps its name and is no
   longer a `CustomEntry`: a `!cmd` record is a `CustomMessageEntry`, because
   rule 1.1 says a record that must reach the model is one. When this ADR was
   written it was the standing example of a `custom` entry, and that framing
   was the defect — the output of `!cmd` never reached the model, which made
   `!cmd` and `!!cmd` ("exclude from context") the same command. `!!cmd` is
   still recorded as nothing at all. Sessions written before this hold a
   `custom` `bash_execution` entry; they load exactly as they always did,
   under rule 6, and their output stays invisible. Nothing reads the old
   shape, so there is no migration.

   Moving a record onto the message tier makes its SIZE the session's problem,
   which the first cut of this change had not noticed: the entry is re-sent on
   every later turn and every resume, so `!python3 -c "print('x'*1000000)"`
   wrote a 1,000,047-char `UserMessage` and a 2,000,455-byte session file.
   `content` now carries a capped copy (pi's own numbers for this path, 2000
   lines / 50KB via `truncate_tail`) with a visible notice of what was cut, and
   `details` carries the command, the exit code and the truncation counts — not
   a second copy of the output, which is what doubled the file. The full output
   still reaches the user's screen. **The general rule this is an instance of:
   what a `CustomMessageEntry` records is context, so it is bounded at the
   writer the way a tool result is** — a `CustomEntry` only has to be small
   enough to store.

   **Corrected 2026-09-21 (#299), before the branch merged.** The paragraph
   above was written about a cap that was not yet one. A cross-review measured
   nine shapes of `!` output against it and five came back wrong, four of them
   for one reason: the cap ran `truncate_tail` over text it had stripped a
   single `"\n"` from. That helper splits on `"\n"` raw, so the empty element a
   trailing newline leaves behind is a LINE that costs 0 bytes — it is the one
   line that fits, and the long line above it is dropped whole. Stripping one
   `"\n"` hides that for exactly one shape. `print('x'*60000); print()`
   recorded `(no output)` for 60KB the user had just watched go past, which is
   worse than the defect this issue was filed about because it is silent and it
   looks like an answer; and on Windows the `\r` of a CRLF survived as content
   and ate a byte of the cap, which is how the windows CI legs found it. The
   writer now strips the WHOLE trailing terminator run, counts it, and decides
   the cap against the ORIGINAL bytes — that last part closes the fifth shape,
   `"x" * 50KB + "\n"`, which was recorded at 51,201 bytes with no notice: a
   cap that did not cap. The helper's own bug is #309; it has five callers and
   pinned parity tests, so it is its own change.

   **Amended 2026-09-22 (#309), and the workaround above is gone.** The helper
   counts lines the way pi does now (ADR-0137, amended), so this writer no
   longer has to: `_cap_for_the_record` is `truncate_tail` and a notice. The
   nine shapes are unchanged, body for body and notice for notice — they are
   asserted in `tests/cli/test_user_bash_reaches_the_model.py` and were
   re-measured across the removal — and the counts in the record's `details`
   are the helper's own now, except `original_bytes`, which stays the
   COMMAND's: the pair reads "it printed this much, the record keeps this
   much". What survives here is one line of CRLF normalisation of the trailing
   run, which is not about truncation at all — it is about a Windows child and
   a POSIX child recording the same thing.

   Two claims in the paragraph above were also wrong on the facts. The helper
   has SEVEN callers, not five, and the list above misses two of them. One is
   `rpc/rpc_mode.py` `_handle_bash`, which caps an ad-hoc RPC `bash` at 256
   lines / 32KB and lost the same bodies (measured: a single line of 40,000
   bytes — 40,001 with its terminator — came back as `output: ""` with
   `truncated: true`). The other is `_cap_for_the_record` itself, the writer
   this entry documents: the workaround wrapped `truncate_tail` rather than
   replacing it, so the function whose docstring counted the helper's callers
   was itself one of them — and neither that docstring nor the paragraph above
   counted it. Counted on both trees with
   `grep -rn "_truncate import\|truncate_tail(\|truncate_head(" packages/`:
   `tools/bash.py`, `tools/grep.py`, `tools/find.py`, `tools/ls.py`,
   `tools/read.py`, `cli/repl.py` and `rpc/rpc_mode.py`. The hit in
   `cli/agent_context.py` is a docstring, not a call.
   And it had no pinned parity tests — `grep -rn truncat tests/pi_parity/*.py`
   matches nothing. Its behaviour was pinned in `tests/tools/test_bash_tool.py`,
   whose truncation cases all used `printf` WITHOUT a trailing newline, one of
   them saying so in a comment ("→ exactly 100 lines (deterministic)"). That is
   how a defect in every ordinary command's output survived them.

   The command is capped too, at 1024 characters, and it is the same rule.
   `!#` followed by 60,000 characters produced a 60,019-byte model message
   with no output in it at all — an unbounded record through a different door.
   This half is a divergence from pi (ADR-0235), which renders
   ``Ran `${msg.command}` `` with whatever it was handed.
6. **A reader ignores a `customType` it does not know and `data` keys it does
   not know.** Already true: `build_session_context` and
   `build_display_messages` skip `custom` entries.

   **Clarified 2026-09-20 (#299): this is a reader rule, and reading it as a
   writer rule is what cost #299.** "A reader skips `custom`" is not "a
   `custom` entry is a fine place to keep something the model needs" — it is
   the opposite, and it is why rule 1.1 puts such a record in
   `custom_message`. A writer choosing between the two asks one question: must
   this reach the model or the transcript? `custom_message` if yes (it becomes
   a `UserMessage` in `build_session_context` and a rich `CustomMessage` in
   `build_display_messages`), `custom` if no. The rule itself is unchanged: a
   `custom` entry still contributes no message on either tier.
7. **Payloads are JSON values** — lists not tuples, string keys, finite
   floats, no dataclasses — in `data`, `details`, `content` and message
   fields, and keeping them that way is the writer's job. JSONL refuses only
   what it cannot convert: what `json.dumps` cannot encode (a dataclass, a
   `set`, `bytes`, a circular reference), and a `message` that is not a
   dataclass, which `entry_to_json`'s `asdict` rejects first. Either raises
   `SessionError("invalid_entry")` **before a byte is written**, where both
   used to raise a raw `TypeError`. It does not catch the
   rest. A tuple is written as a list and a non-string key as a string, and
   both come back changed after a reload — a tuple that worked as a dict key
   before the reload is an unhashable list after it. `NaN` and `±Infinity` are
   written bare, which Aelix reads back and strict readers (pi's among them)
   do not. Refusing any of them would turn entries that persist today into
   write errors, so the store stays as lenient as it was; a test pins what
   each one turns into. The in-memory backend does not serialize and keeps
   every payload as given.

### 2. One storage call is one line in one `append_file` call

`append_entry` and `set_leaf_id` each append exactly one `\n`-terminated JSON
object with exactly one `FileSystem.append_file` call; the healing `\n`, when
one is needed, rides in the same call. "One write" means one `FileSystem` call:
`LocalFileSystem.append_file` may issue several `os.write` syscalls after a
short write, and a second writer on the file (#137, not solved here) could land
between them. The old docstrings' "≤ PIPE_BUF is atomic" claims are gone —
`PIPE_BUF` is a pipe and FIFO guarantee, not a regular-file one.

A caller that writes several entries for one operation must keep **every
prefix of the log a valid session**, because the file can end after any line.
`Session.move_to` writes a `leaf` line and then a `branch_summary` line; the
prefix that stops between them is valid (the leaf moved, the summary is
missing), so it stays as it is — pi v3 does the same.

### 3. A failed or short write never costs the next one

- `append_file` and `write_file` loop until every byte is written; a write that
  makes no progress raises `EIO` instead of spinning.
- `_append_line` serializes first — an entry it cannot convert raises
  `invalid_entry` with nothing written. Then **any** exception out of
  the write (`OSError`, a second Ctrl-C between two `os.write` calls, a
  cancelled awaiting `FileSystem`) re-arms the healing newline before it
  propagates; only `OSError` is translated to `SessionError("storage")`. The
  in-memory state changes only after the write returned.
- That assumes the write is over once the call is, and the `FileSystem`
  Protocol now says so: an implementation that hands the bytes to a worker
  thread waits for the worker even when it is cancelled. Otherwise the lock is
  released while the worker is still writing, and its bytes land inside the
  next line. Measured with a thread-backed `FileSystem` that goes on writing in
  5-byte chunks after a cancellation: `['a', 'c']` in memory, `['a']` after a
  reload. `LocalFileSystem` writes synchronously, so it cannot do this.
- What a failed append leaves on disk is one of three things, and none of them
  loses anything:
  - **nothing** — the next append starts with a blank line, which the loader
    filters without calling it damage;
  - **a fragment** — skipped on load as its own unparseable line; nothing is
    pruned, because nothing descends from an entry that never reached memory;
  - **a complete line** — the failure hit after the last JSON byte. It is kept
    on load with `recovery is None`: an off-path sibling when later appends
    happened, the resumed leaf when it was the last write. The caller was told
    it failed and it is on disk anyway — at-least-once, pinned by a test so
    nobody "fixes" it into a truncation.
    **Narrowed 2026-11-07 (#721), rule 5:** the line is still kept and still
    never truncated, but the caller is now told `outcome_unknown`, not
    `storage`, and the next write reads the file back and takes the line in,
    so an entry `Session` appends next is its child, not its sibling. The
    off-path sibling remains only for a caller that builds an entry on a leaf
    it read before that next write and hands it to `append_entry`, which keeps
    the parent it is given
    (`test_a_complete_line_whose_newline_was_lost_is_kept_off_the_path`).
- The fragment is **not** truncated away (`ftruncate` to the pre-append size):
  with a second writer on the file that would cut the other writer's bytes.

### 4. Whole files appear complete or not at all

- `FileSystem` gains `rename_file(source, destination)` (pi `renameFile`):
  atomically replace `destination` with `source`, same directory.
  `LocalFileSystem.rename_file` is `os.replace`. **A custom `FileSystem`
  implementation must add it.**
- On Windows that rename is retried for about a second (delays 0.05, 0.1, 0.2,
  0.3 and 0.35 s) when it answers `PermissionError`. Every session create and
  every import now ends in a rename, and Windows refuses one while another
  process holds the file — an antivirus scanner, the search indexer or a sync
  client opening the temp that was just closed. pip retries `os.replace` for
  the same reason. The temp is complete by then, so waiting cannot publish
  anything partial; a refusal that outlasts the retries still fails loudly and
  leaves no file. A POSIX `PermissionError` is final at once. Only simulated
  here (a test patches the platform and the rename); the windows-latest legs run
  the real path but never contend with a scanner on purpose.
- `JsonlSessionStorage.create` takes `entries=` and publishes header plus one
  line per entry in **one** publish: stage `<path>.tmp` with `write_file`, then
  `rename_file` it over `<path>` (pi `publishFileAtomically`, same temp name).
  On any failure the temp is removed best effort. Every line is serialized
  before anything is staged. The bytes are the ones header-then-append wrote.
- `fork` and `fork_from` publish through that `create` instead of looping
  `append_entry`. A failed write or rename still raises
  `SessionError("storage")`; the difference is that no session file is
  published (and an import keeps the destination it would have replaced). The
  staged temp is removed best effort — on Windows a temp that another process
  keeps locked can outlive the removal, but it never ends in `.jsonl`. An
  exception does not prove that nothing was published, though: an interrupt
  (Ctrl-C, a cancellation) that lands just after the rename leaves the complete
  file, and it is not rolled back. Either way the file is whole or absent; a
  test pins both sides of the rename.
- `copy_file` (import) stages `<destination>.<12 hex>.tmp` — created `O_EXCL`
  at 0600, filled by `shutil.copyfile` (byte-exact), fsynced — and renames it
  over the destination. That fsync is **the only one in the store**:
  `copy_file` can replace an existing complete session, and a rename that
  reaches the disk before its data would leave an empty file after a power
  loss. Its temp name is unique per call, unlike a publish's `<path>.tmp`: a
  publish always targets a fresh session name, but an import's destination is
  named after its source, so two processes importing the same file share it.
  With one shared temp, one could remove or overwrite the other's half-written
  copy and then rename it into place (the Codex cross-review reproduced it,
  with threads standing in for the two processes).
- **No rewrite-on-open.** pi v4 rewrites a file with a torn tail when it opens
  it (read from its source; see the divergences below). Here a reader — a
  second terminal running `--continue`, or `/resume` — can see another
  writer's line mid-write; rewriting would publish a file without that line,
  and the writer's completed append would then land in the replaced inode and
  be lost. Recovery stays non-destructive (ADR-0208).
  **Amended 2026-09-20 (#137, ADR-0244): the decision stands, half of the
  reason no longer holds.** A session file now has at most one live writer, so
  an opener that took the write lock could not race anyone by rewriting — that
  half of the premise is gone. The other half is untouched and is now the whole
  reason: a terminal that chose **Open read-only** holds no lock at all, the
  owner keeps appending underneath it, and a rewrite by that reader would
  destroy exactly the line described above; and on a filesystem without
  `flock`, ADR-0244 degrades to no ownership check, so there is no single
  writer to appeal to. Turning it on would also be a destructive operation,
  which is a separate decision from either ADR.

### 5. A write whose outcome is unknown is settled before the next parent is chosen

**Added 2026-11-07 (#721, #611 S1a; Codex round 5 findings 3 and 4).**

`Session` used to read a fresh id and the current leaf, build the entry, and
only then hand it to `append_entry`. After an append whose line landed and then
raised, that leaf was the one from before the line, so the next entry became
the landed line's sibling, and the landed line left the current branch while
staying in the file. On resume it was neither in the conversation nor written
again. Measured on `main` `fc8f3048` with the real `Session` and loader and a
`FileSystem` that writes the whole line and then raises `ENOSPC`, a
`custom_message` appended that way and one message after it:

```
main:   raised SessionError:storage          whole_file_notices=1 branch_notices=0 branch=[message, message]
branch: raised SessionError:outcome_unknown  whole_file_notices=1 branch_notices=1 branch=[message, aelix.job_notice, message]
```

**The failure classes of one write** (`JsonlSessionStorage._append_line`):

| what happened | the caller gets | pending |
| --- | --- | --- |
| an entry that cannot be encoded | `SessionError("invalid_entry")`, before a byte is written (rule 1.7, unchanged) | no |
| `FileSystem.append_file` raised `NotAttempted`: nothing was written | `SessionError("storage")`; the same write can be retried as it stands | no |
| any other `OSError` from the write | `SessionError("outcome_unknown")` — `SessionErrorCode` gains it (ADR-0035; this ADR owns it) | yes |
| anything else, `CancelledError`, `KeyboardInterrupt` and `SystemExit` among them | re-raised as itself, unchanged: a cancel stays a cancel | yes |

`NotAttempted(OSError)` is new in `session/fs.py`. `LocalFileSystem.append_file`
raises it when the directory, the `open` or the first `os.write` fails, since
none of those wrote a byte; a failure after the first byte is the plain
`OSError`. A custom `FileSystem` may raise it; one that raises plain `OSError`
for both is safe, because the store then reads the file back.

**Pending, and how it is settled.** The store keeps the entry whose outcome is
unknown, with the line it wrote. Every write — `append_entry`, `set_leaf_id`,
`append_to_leaf`, `place_entry`, `withdraw_tail` — takes the lock and settles it
first: the file is read back with the loader's own rule
(`_parse_jsonl_content`). The line landed when that exact line is whole in the
file and the loader keeps an entry with its id: it is then taken into memory as
the newest line and the leaf moves to it, as a reload would move it. If not, it
is dropped, and the heal flag stays armed, so the next write opens a fresh line
after any fragment. A read that fails refuses the write that asked with
`outcome_unknown` ("unresolved"), writes nothing, and leaves the leaf where it
was until a read succeeds.

What is taken in (fix round 1 of #721, Codex finding 2): the caller's own entry
while it still encodes to the line written, because the harness tells its
messages apart by identity (Esc-withdraw's `is_withdrawn`); otherwise the entry
the loader parsed from the file. So a payload the caller changed before the
entry is settled does not reach memory: measured on `ed76ea76`, a custom entry
whose dict was changed from `{"k": 1}` to `{"k": 2}` after its line landed and
raised read `{"k": 2}` in memory and `{"k": 1}` in the file, and now reads
`{"k": 1}` in both (`test_a_pending_entry_its_caller_changed_is_adopted_as_the_file_holds_it`;
changed into a `set` JSON cannot hold, the same,
`test_a_pending_entry_changed_into_what_json_cannot_hold_is_adopted_from_the_file`;
the identity case is
`test_an_unchanged_pending_entry_is_adopted_as_the_callers_own_object`). After
the settle the caller's own entry is memory's, as after any append that
succeeded, and a change to it reaches memory and not the file: the verify
round's `identity.py` (`.omc/specs/721-verify-r2-probes/storage/`) read memory
`{"v": 2}`, file `{"v": 1}` for a change after a successful append, and memory
`{"v": 3}`, file `{"v": 1}` for one after the settle; `ed76ea76` gives the same
values through `identity_settled.py` (its readers do not settle, so `identity.py`
stops there at `I3`). That aliasing is older than #721 and stays: copying what is taken in
would break the identity the harness relies on.
Matching the line, not only the id, is what keeps an `append_entry` that reused
a stored id, and did not land, from being adopted on the strength of the older
line: on `ed76ea76` the verify round's `dupid.py` read `"same": false`, memory
holding an entry a reload does not; now memory equals the reload
(`test_an_append_that_reused_a_stored_id_and_did_not_land_is_not_adopted`).

**Four Protocol methods** (`SessionStorage` is pi's 10 plus these):

- `append_to_leaf(build)` — under one hold of the lock: settle, call
  `build(new_id, leaf)`, append what it returns, move the leaf. Every
  `Session.append_*` goes through it, so the parent is chosen after the
  outcome is settled. The same hold also chains concurrent appends: of eight
  `Session.append_message` calls started together on a `FileSystem` that
  yields mid-line, all eight were children of the seed on `main` and one was
  on the branch after a reload; now they are one chain of eight.
- `place_entry(entry_id, build, *, same)` — writes the entry under an id the
  caller chose, once. If the id is stored, it is returned adopted when
  `same(existing)` and refused with `PlacementConflict` (code
  `invalid_entry`) otherwise, writing nothing either way; else it is built on
  the leaf and appended. A placement whose own write raised `outcome_unknown`
  is placed again and adopted, with no duplicate line. What it stores and
  returns is the entry parsed back from the line it wrote (a deep copy in the
  memory store), never the built object, so `same` compares against what was
  placed. Measured on `ed76ea76` with Codex's probe: a placement of
  `{"value": 1}` whose caller then changed that dict and placed `{"value": 2}`
  was adopted, memory `{"value": 2}`, file `{"value": 1}`, and the same retry
  after a reopen was a conflict; now it is a conflict in the process too, with
  `{"value": 1}` in both
  (`test_a_placement_compares_against_what_was_written_not_the_callers_dict`).
  A build that returns another id is refused with `ValueError` before anything
  is written (`test_place_entry_refuses_a_build_that_returns_another_id`, on
  both backends; only the store itself reaches it, since
  `Session.place_custom_message` builds the id it places).
  `Session.place_custom_message` is its `Session` form; #611 S1b's notices
  are its first producer.
- `withdraw_tail(target_leaf, is_withdrawn)` — ADR-0286's withdrawal as one
  store operation (fix round 1 of #721, Codex finding 1). Under one hold of the
  lock: settle, read the path, write the leaf move, append each entry
  `is_withdrawn` does not claim with a fresh id on the leaf the one before it
  left. `Session.withdraw_tail` delegates to it. It used to be `move_to`
  followed by the survivors' appends from `Session`, and `ed76ea76` made that
  worse: a leaf move whose line landed and then raised left the survivors
  unwritten, and the next write adopted the moved leaf — Codex's probe read
  `[seed, after failure]` where base `fc8f3048` had kept
  `[seed, must survive, after failure]`. Once the move is written the
  withdrawal is owed: a write that raises after that keeps the rest, the
  failed one first, and the next write finishes it before its own; a survivor
  whose copy landed is taken in, not written twice. A leaf move whose outcome
  was unknown and that did not land is a withdrawal that did not happen. A
  survivor that can no longer be encoded is logged and skipped rather than
  owed for ever. With the path read inside the same hold, an append that comes
  in meanwhile is no longer left behind on the old branch (Codex's
  `withdraw-concurrent`: `[seed, survivor]` on `ed76ea76`). Tests: the
  `withdraw_tail` cases in `tests/session/test_append_resolution_611.py` and
  three in the conformance suite.
- `resolve_pending()` — settles what an earlier write left open: a pending
  outcome, then the rest of an interrupted `withdraw_tail` (the only thing it
  writes). `Session.move_to` and `append_label` call it first and keep the
  parent they name: they are never redirected. `move_to` settling before it
  validates, and `set_leaf_id` doing the same under its lock, are each pinned
  with a target read from the file
  (`test_move_to_finds_a_target_read_from_the_file_before_anything_else_settles`,
  `test_set_leaf_id_finds_a_target_read_from_the_file`).

**The leaf is read settled** (fix round 1 of #721, Codex finding 5).
`Session.get_leaf_id` and `Session.get_branch` call `resolve_pending` first, best
effort: a read that cannot settle answers from the store as it stands, as
before, and the next write raises the reason. So every reader that takes the tip
through `Session` — the RPC `clone`, the bridge's branch in `rpc/control.py`,
`/clone` in `tui/shell.py`, the harness's Esc-withdraw point — sees a line that
landed and then raised. Measured on `ed76ea76`: Codex's `rpc-clone-stale-tip`
(the real handler, the real JSONL fork) cloned `[seed]` of `[seed, landed
answer]` (`tests/rpc/test_clone_takes_the_settled_tip_721.py`), and the verify
round's `harness_safe_leaf.py` left the landed answer twice in the file after an
Esc-withdraw of the next prompt
(`test_esc_withdraw_after_a_landed_and_raised_answer_writes_no_copy_of_it`). A
reader that holds the store itself (`Session.get_storage().get_leaf_id()`) gets
the store's answer unsettled.

`MemorySessionStorage` never has a pending entry (a write either changed memory
or raised before it did); its `append_to_leaf`, `place_entry` and
`withdraw_tail` write through `append_entry`, so a test double that overrides
`append_entry` covers them, and a withdrawal cut that way stays cut: there is no
file to settle it from. `ReadOnlySessionStorage` refuses the three new writes
with `read_only`, and its `resolve_pending` delegates (a store it wraps has
written nothing and owes nothing). A custom `SessionStorage` must implement the
four; the conformance suite runs them on every backend. `is_withdrawn` and
`same` are called under the store's lock and must not call back into it.

One caller branched on `storage`: `_record_session_start_model`
(`cli/entry.py`) warns and goes on for `storage` and now for `outcome_unknown`
too. The harness's `message_end` path logs any failed append at DEBUG and goes
on, as before; the next write settles it.

Tests: `tests/session/test_append_resolution_611.py` (the case above in place,
each `append_*` kind, labels, `move_to`, `withdraw_tail`, `set_leaf_id`, the
placements, the four failure classes, an unreadable file, `LocalFileSystem`'s
`NotAttempted`, the memory and read-only stores, a harness turn, concurrent
appends, and from fix round 1 the interrupted withdrawals, adoption, readers and
the settle-before-validate pins) and the `append_to_leaf` / `place_entry` /
`withdraw_tail` / `resolve_pending` cases in
`tests/session/test_storage_conformance.py`. Every fault in them is injected
through a `FileSystem` double or a patched `os`; none is a real full disk.

pi, read from `origin/main` `085c84e98`'s source, not run, takes the other two
shapes. `packages/durable/src/storage/jsonl/storage.ts` poisons the store once
a commit applied to memory and its append failed ("JSONL storage is poisoned
and must be reopened"): every later call raises until a reopen, whose recovery
truncates torn tails. `packages/coding-agent/src/core/session-manager.ts`
`_appendEntry` rolls memory back when its synchronous `_persist` throws,
reading the failure as "not written", so a line that landed and then raised
becomes the next entry's sibling, as it did here. Aelix keeps writing and
settles from the file instead (ADR-0235: recorded, not argued).

Limits: the read-back reads the whole file, once per failure. Lines a second
writer added (a filesystem without `flock`, ADR-0244) do not confuse it, but
that writer is still #137's problem. An `append_entry` that reuses an id
already stored is still not rejected (below); when its line lands, memory and a
reload both resolve that id to the newer entry, so the older entry's
descendants walk their path through it. An owed withdrawal lives in memory: a
process that dies between the leaf move and the last survivor leaves a file
whose reload ends at the move or at the last survivor that landed (the verify
round's `.omc/specs/721-verify-r2-probes/storage/withdraw_once.py`, re-run on
fix round 2: of `[seed, a, b, c, d]` withdrawn back to `seed` keeping `a`, `c`
and `d`, a crash after `a` landed reloads `[seed, a]`; 126 of its 168 cases
reload short), as before #721. A placement's `same` compares against the line parsed back, so a
same-payload retry whose payload a JSON round trip changes (a tuple comes back
a list, an `int` key a string, and `NaN` is not equal to itself) is a
`PlacementConflict` in the process as after a reopen (`identity.py`'s `I4`
rows, all three; `test_a_same_payload_retry_that_json_cannot_round_trip_is_a_conflict`,
the tuple case); no product caller places such a payload, and rule 3 leaves
JSON values to the caller. `storage` means nothing was
written for a session append only; publishing a new file (`create`, a fork)
still reports a failure after its rename as `storage` (`_publish_file`; Codex's
`storage-after-publish` probe on `ed76ea76`: `raised="storage"`, the file there).

## The suite: a backend is a fixture

`tests/session/test_storage_conformance.py` is the contract behind the
Protocol: protocol and metadata, ids, append/get, leaf, labels, `find_entries`,
path to root, a round trip of every `SessionTreeEntry` member with every
optional field set and unset, durability across `reopen()`, **every prefix of
the log is a session**, custom records, concurrent appends, and (since #721)
`append_to_leaf`, `place_entry`, `withdraw_tail` and `resolve_pending`. It runs once per
backend through one parametrized fixture — Memory and JSONL today — and a new
backend (SQLite, when the server asks for one) is added by registering its
factory there, and it must pass unchanged. That is the shape pi uses for its
three backends (read from pi `36b60d2`'s source, not measured: one suite,
`harness/session/testing/conformance/storage.ts`, run by its JSONL, memory and
`sqlite-node` backends). What only JSONL can get wrong — bytes, torn tails,
CRLF, atomic publish, and the released behaviour the rules above rest on —
lives in `tests/session/test_jsonl_write_discipline.py`.

## Durability level, and the `.tmp` that can be left behind

The store's durability level is a **process crash**, not a power loss. Appends
and publishes are not fsynced; `copy_file` is, for the reason above. No
directory is ever fsynced. pi's session store is at the same level (read from pi
`36b60d2`'s source, not measured: nothing under `harness/session/jsonl` or in
`env/nodejs.ts` fsyncs; its separate `pico3` store does, by default). A crash
between staging and rename leaves `<file>.tmp` (a publish) or
`<file>.<12 hex>.tmp` (an import): owner-only, and not ending in `.jsonl`, so
neither `list` nor `find_most_recent` ever sees it. The next publish to the same
path overwrites a publish's temp; an import's temp stays. Nothing sweeps either.

## Windows session files become LF-only

On Windows `os.open` without `O_BINARY` returns a text-mode descriptor, so every
session line Aelix wrote there ended in `\r\n` (the stdlib adds `O_BINARY` by
hand for this reason — `tempfile`, `_pyio.FileIO`). Every descriptor that writes
session bytes now adds `getattr(os, "O_BINARY", 0)`: the bytes on disk are the
bytes Aelix wrote, LF only, on every platform. Files written before this are
CRLF and still load and take appends (readers use universal newlines; a file
torn between `\r` and `\n` reads as terminated) — such a file simply ends up
with mixed endings, which a test pins. The windows-latest CI legs are the
verification for this and for `copy_file`.

## Divergences from pi (ADR-0235: recorded, not argued)

Every pi fact in this section was read from pi `origin/main` `36b60d2`'s
source, not measured by running pi. pi moved to format 4. This change takes
pi's **shape** — one conformance suite that every backend runs, one transaction
per line, a torn line never salvaged, whole files published by temp and rename
— and not its format.

- **A complete but unterminated final line is kept** (ADR-0208's decision). pi
  v4 drops it: `splitCompleteLines` (`jsonl/storage.ts`) keeps only the lines
  that end in `\n`.
- **No rewrite-on-open** (above). pi v4's `openV4` republishes a torn file
  through `publishFileAtomically` as it opens it.
- **No format 4** — no values, lists or usage ledger. Owner decision #10.
- **No duplicate-id or missing-parent rejection in `append_entry`.** pi v4 has
  it (`validateCommittedWrites`, `commit.ts`); the v3 contract never did.

One more, which decides whether a session moves to pi: pi's legacy-v3 importer
(`jsonl/legacy-v3.ts`, `parseLegacyV3Entry`) accepts neither `leaf` records nor
blank lines, so an Aelix session file is not guaranteed to import into pi today
— any branch move writes a `leaf` line, and a failed append can leave a blank
one.

## Consequences for #199

- One child-session record is **one** `CustomEntry` line with an
  `aelix.`-prefixed `customType` and everything it needs inside `data`.
- A start and a settle are two complete records, never two halves of one —
  the file can end between them, and the prefix must still make sense.
- A reader folds them last-wins by a stable key carried in `data`.

## Not done here

- Format 4, values, lists, a usage ledger; SQLite (owner decision #10).
- Rewrite-on-open to drop torn bytes; an `aelix session repair` verb
  (ADR-0208's TODO stays open); sweeping stale `.tmp` files.
- Keeping unknown entry types as opaque tree nodes. More than defence in depth:
  pi's harness v3 wrote `active_tools_change` records (its legacy-v3 importer
  still accepts them), so `/import` of such a pi session prunes today. Follow-up
  candidate.
- Duplicate-id and missing-parent rejection in `append_entry`. (`place_entry`,
  #721, writes an id at most once; `append_entry` still takes what it is given.)
- ~~A cross-process single writer (#137)~~ — **done, 2026-09-20: ADR-0244.**
  A session file now has at most one live writer; the second terminal is asked
  whether to fork it, view it read-only, or stop.
- fsync of appends; `move_to` as one line; rejecting `NaN`.

## Note (2026-11-08, ADR-0293, #722)

Two first-party record kinds follow §1 (rules 1.3 and 1.5, an `aelix.` custom type carrying everything in its data): `aelix.job` facts
(`start`, `process`, `cleanup`, `supervision`, `outcome`; `v: 1`, each complete, placed with
rule 5's `place_entry` under an id derived from the fact's own `fid` — `jr-` + 16 hex of its sha256,
through `Session.place_custom_entry` — so a retry adopts a line that landed instead of writing it
again), and the
notice delivery, a `custom_message` placed with rule 5's `place_entry` under the id `nt-` + 12 hex
of `sha256(notice_id)`, so a notice is written at most once and its delivery is one `get_entry`.
A reader that does not know them skips them (`build_session_context` turns the delivery into the
user-role text the model read). Inert until #611 S3.
