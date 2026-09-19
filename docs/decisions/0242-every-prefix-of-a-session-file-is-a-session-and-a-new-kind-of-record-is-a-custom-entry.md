# 0242. Every prefix of a session file is a session, and a new kind of record is a custom entry

Status: Accepted (2026-09-19)
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
6. **A reader ignores a `customType` it does not know and `data` keys it does
   not know.** Already true: `build_session_context` and
   `build_display_messages` skip `custom` entries.
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

## The suite: a backend is a fixture

`tests/session/test_storage_conformance.py` is the contract behind the
Protocol: protocol and metadata, ids, append/get, leaf, labels, `find_entries`,
path to root, a round trip of every `SessionTreeEntry` member with every
optional field set and unset, durability across `reopen()`, **every prefix of
the log is a session**, custom records, and concurrent appends. It runs once per
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
- Duplicate-id and missing-parent rejection in `append_entry`.
- A cross-process single writer (#137); fsync of appends; `move_to` as one
  line; rejecting `NaN`.
