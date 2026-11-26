# 0321. An aelix reinstall keeps the installed extensions

Status: Accepted (2026-11-26, #926; D1, D2, D3, D4 and D5 amended the same day after the
first review round, and again after the second; D1, D3, D4 and D5 after the third).
Date: 2026-11-26
Issue: [#926](https://github.com/handochan/aelix-ai/issues/926)
Amends: ADR-0186 (install records are also the restore ledger) and ADR-0187 (pins gate a restore).

## Context

`aelix extension install` puts an extension into the interpreter's own
environment with `uv pip install` (pip when the environment has it, ADR-0185,
#113). For the official install path that environment is a `uv tool` environment,
and every `uv tool` operation rebuilds it to exactly what its `uv-receipt.toml`
names. The lead measured it on 2026-11-26 with two hand-made wheels (a tool, and a
package with an `aelix.extensions` entry point added with `uv pip install`), on uv
0.11.19 and 0.13.10 with the same results:

| Case | Result |
| --- | --- |
| A `uv tool install --force ==0.2.0` (what install.sh and install.ps1 ran) | version 0.2.0, extension gone |
| B the same without `--force`; F an unpinned `uv tool install` again | gone |
| C `uv tool upgrade` on a receipt pinned `==0.1.0` (nothing to upgrade) | gone |
| D `uv pip install` of the new tool version into the same environment | kept, receipt still says `==0.1.0` |
| E `uv tool install --force ==0.2.0 --with dummypack==0.1.0` | kept |
| G installed `--with`, then `uv tool upgrade` | kept |
| H installed `--with`, then `--force` without `--with` | gone |

So only receipt entries survive. The issue's machine showed the cost: after an
install-line update, `aelix extension list` said `No extensions installed (via
entry_points).` while `extension_pins.json` held two pins and `settings.json`
two install records, and the tools of both extensions were gone with no message.

The owner chose the direction on 2026-11-26: "receipt 등록 + 시작 시 복원 (권장)".
The options it was chosen over were "설치기만 기존 venv에서 교체" (an in-place
`uv pip install` of aelix, case D) and, earlier, "pi처럼 구조 변경 (v0.2.0)". The
vehicle, the same day: "0.1.1 패치 릴리스 (권장)".

## Decision

### D1. The installers carry what the environment holds into the new receipt

Before `uv tool install --force`, install.sh and install.ps1 run one Python
program (`carry.py`, the same text in both, held equal by
`tests/packaging_gate/test_install_carry.py`) with the CURRENT environment's own
interpreter, `-I`. It needs no aelix command, because the installers are served
from `main` to every user and install whatever release is newest or
`AELIX_VERSION` names (0.1.0 included). `collect` writes a requirements file:

- each receipt requirement other than aelix whose distribution is still installed
  and is not an extension, read back from uv's receipt form (`specifier`, `git`
  with `rev`/`tag`/`branch`/`subdirectory`, `directory`, `path`, `editable`,
  `url`, `extras`, `marker`); a form it cannot read back is named, not carried;
- each installed distribution with an `aelix.extensions` entry point (aelix's
  own five excluded: `aelix`, `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`,
  `aelix-server`), from its PEP 610 `direct_url.json`: a git checkout as
  `name @ git+URL@<commit_id>` (and `#subdirectory=`), a directory that still
  exists as its file URL (`-e` for an editable one), a file that still exists as
  its file URL, an `https` archive as its URL, and no `direct_url.json` (an index
  install) as `name==version`. `aelix extension install <file>` installs a staged
  copy whose directory is gone; the path pin (ADR-0187) names the original, which
  is carried when its sha256 still equals the pin's. An extension the receipt
  names at an older commit is carried at what is installed, so an
  `aelix extension install` since the last update wins. The extras and marker the
  receipt asked for it with stay on that line (`Keep_Ext[feature]>=1,<2 ;
  python_version >= "3.11"` is carried as `keep-ext[feature]==1 ; …`; Codex review
  round 2 S1 measured `keep-ext==1`, the extra's dependency dropped, and success
  reported; `test_an_extension_the_receipt_asks_for_with_extras_keeps_them`). The
  command printed for one left out keeps those extras too, at collect and when the
  check takes one out (`aelix extension install 'keepext-926[feature]==0.1.0'`, a
  `<path>[feature]`, `name[feature] @ git+…`; verify round 3 F4: the command was
  `'keepext-926==0.1.0'`, and run with `--index-url` it installed keepext-926
  without featdep-926, its extra's dependency;
  `test_the_command_for_one_left_out_keeps_the_receipt_s_extras`); an editable line
  cannot carry extras, and its `carrying` line says they are left off.

The reinstall takes the file as `--with-requirements`, which uv records in the new
receipt one requirement per line (measured, uv 0.11.19), so the next `uv tool`
operation that runs keeps them (case G). A plain `uv tool upgrade aelix` over an
install-line install does not run: it exits 1 while the receipt names the
installer's deleted `--find-links` directory (#464, Known limits), and CI makes
the directory again before it measures the upgrade.

Not carried, and named: a non-git checkout, a directory or file that is gone, a
path with `#` in it (uv 0.11.19 reads `%23` in a file URI as a fragment:
`Distribution not found at: file:///…/sp%20ace#x/editext`), and a source with a
control character; each line says there is no command that reinstalls it from the
installer, and why. A distribution that records no files is named with its
command, because carried with nothing to compare any bytes would pass. A receipt
requirement whose distribution is no longer installed (one `aelix extension
remove` took out) is named as having nothing to reinstall. Every carried item has
a line ready for the case it is left out later: `aelix extension install
<source>` for a git commit, a directory or a file (no `--repin`: the source is
the one that was installed, so a pin it left holds it); `uv pip install --python <the environment's python>
<requirement>` for an `https` archive, an editable directory and a receipt
requirement; and for an index install `aelix extension install 'name==version'`
with the note that it resolves from PyPI or an index `aelix extension source add`
registered and takes `--index-url` for another (a one-off `--index-url` is not
recorded anywhere; measured in Linux docker, the printed command with
`--index-url` installed it).

No one input stops the rest (Codex review round 1 L1, L2). An `extension_pins.json`,
`uv-receipt.toml`, `direct_url.json`, `RECORD` or `entry_points.txt` in a shape
`carry.py` does not expect names that one item, or, for the receipt, the receipt,
and the others are carried (`test_a_pins_file_in_another_shape_drops_nothing` and
its neighbours: `{"pins": ["bad"]}` raised `AttributeError` out of `collect`
before, and the installer then carried nothing and named nothing). An extension
whose `METADATA` cannot be read (missing, empty or unreadable) while its
`entry_points.txt` can is named from its dist-info directory and its entry points,
with no command (Codex review round 2 M4: `collect` exited 0 and the reinstall
removed it unnamed;
`test_an_extension_whose_metadata_cannot_be_read_is_named_from_its_directory`).
A walk of the environment that importlib breaks off part-way says so, and names
nothing past where it broke. When the current environment's Python does not run
(`-I -c pass` fails) or `carry.py` cannot finish, the installer names each
extension from the environment's own `*.dist-info/entry_points.txt` files, by the
project name and version its `METADATA` gives, or by the directory's name, said
to be that, when `METADATA` gives none (verify round 2 N7), and says how to
restore: `aelix extension restore --all` when the new aelix has that command (it
asks `extension restore --help`), else the `aelix extension install <source>`
form. The shell's own `…/bin/python: not found` line is no longer printed. The
probe is `-c pass`, not `-c ''`: Windows PowerShell 5.1, and pwsh with
`$PSNativeCommandArgumentPassing = 'Legacy'`, drop an empty argument, Python
then exits 2, and a healthy environment was rebuilt without its extensions
(Codex review round 2 M2, reproduced in pwsh 7's Legacy mode). Every other native
argument in install.ps1's Step 5 and 6 is a word, a Windows path (which cannot
hold a double quote), the interpreter range or `aelix[<extras>]==<version>`: none
is empty, and none holds a double quote, the two shapes Legacy passing mangles.

`check` then runs on the NEW environment's interpreter and keeps an extension only
when the files its `RECORD` lists (less `*.dist-info`, byte-compiled files, paths
outside site-packages such as the installer's own script launchers, and files not
on disk, which Python 3.12+ drops and 3.11 lists) hash to the same sha256 of their
bytes on disk as they did before, and its entry points (every group), its
`Requires-Dist` and its `Provides-Extra` read as they did. Those three are
compared in one spelling (spaces, quotes, `name (spec)` against `namespec`, the
order of a specifier's parts, the case of a name), because a git or directory
extension is rebuilt by its build backend
(`test_check_keeps_one_whose_metadata_differs_only_in_spelling`). Not `RECORD`'s own hashes: uv 0.11.19 and 0.13.10
install a wheel whose `RECORD` claims other bytes for a file and keep that `RECORD`
as it came (measured: the file held the impostor line, the installed `RECORD` the
original hash), so comparing two `RECORD`s passes an impostor that copied the
original one (`test_check_takes_out_a_wheel_whose_record_claims_the_old_bytes`).
Measured in Linux docker with uv 0.13.10, an index install answered on an extra
index by such an impostor: the installer as it was before this check read bytes
carried it into the environment, and this one took it out and named it.
One that differs or is missing is taken out by a second reinstall with the rest,
and named.

If the install with the carried set fails, aelix is installed alone and the carried
requirements are added back one at a time on top of the ones that went in (lane's
call, verify round 1 F5: one extension uv cannot install had cost every other
one). One that uv cannot install with the release (an index install from an
index the installer does not see, a directory whose build now fails, an extension
that conflicts with the release or with one added before it) is left out, and
the rest stay. A deleted git source is not one of them while uv's cache holds its
commit: verify round 2 measured a deleted repository at a cached commit carried
(P3b, dash and bash). Its line says why, on a line of its own after uv's message
(uv's build-failure hint ends with no newline, and verify round 3 F6 found the line
glued to it; a line break to stderr now comes first, as before the line that says
the reinstall with the carried requirements failed and the one that says a kept
reinstall failed; `test_step5_starts_the_failed_line_on_a_line_of_its_own` and
`test_step5_starts_the_line_after_a_failed_kept_reinstall_on_its_own`, under sh and
the three pwsh modes): `not carried: X
- uv could not install it with aelix <version> (its message is above)`, and it
gives a command only where one can work: for an index install, whose index may be
one the installer does not use, `aelix extension install 'name==version'` with
the `--index-url` note; a git commit, a directory or a file fails the same way
again, so its line says there is no command (verify round 2 N1: the collect-time
command printed for a build that failed exited 1 with the same build error;
`test_a_failed_line_says_why_and_gives_a_command_only_where_one_can_work`). Measured in
Linux docker with uv 0.13.10, 0.1.0 to 0.1.0: a git, a wheel and an index
extension whose index the installer did not see gave five installs in 5 s (the
full set, aelix alone, then one at a time), and the git and wheel ones were in the
receipt and loaded after it. A build that fails removes the environment, where a
resolution failure and a corrupt wheel leave it as it was (uv 0.11.19 and 0.13.10,
measured with a tool, one extension and a third requirement that fails), so when the last one tried fails, the set
that went in is installed again. With one carried item there is nothing to try
alone: it is named after aelix installs alone. An extension never fails the update;
aelix itself failing to install still does, with the message it always had.
install.ps1 runs each native program through `Invoke-Native`, a function whose own
`$PSNativeCommandUseErrorActionPreference` is off, so pwsh 7.3+ with that
preference on does not raise on uv's exit code before `$LASTEXITCODE` is read
(Codex review round 2 S2: one extension uv could not install stopped the update
with aelix left at the old version); the session's own value is left as it was.
`tests/packaging_gate/test_install_carry.py` runs Step 5's 19 cases under pwsh in
the default mode, in Legacy argument passing and with that preference on.

### D2. Trust

The carried requirements reproduce the commit, directory, file or version that was
installed, but aelix's pin gate does not run inside the installer. Three things
stand in for it:

1. The content check of D1, on the bytes on disk. The case it is for: an
   extension installed from a private index, carried as `name==version`, which
   the installer's uv resolves from PyPI, where anyone may publish that name.
   Different bytes are taken out (`test_check_takes_out_an_extension_whose_files_differ`,
   `test_check_takes_out_a_wheel_whose_record_claims_the_old_bytes`, and live with
   an impostor wheel on an extra index: `not carried: idxext-926 0.1.0 - its files
   differ in the new environment`). So are the same files with other entry points
   or other dependencies: a same-version wheel without `entry_points.txt` (Codex
   review round 2 M3: carried, and `every carried extension binds` printed over 0
   endpoints) and one whose `METADATA` adds a `Requires-Dist` (verify round 2 N2:
   `evil-926` was installed beside it)
   (`test_check_takes_out_a_wheel_that_keeps_the_files_and_drops_its_entry_points`,
   `test_check_takes_out_a_wheel_whose_metadata_pulls_in_more`). So an extension
   that is kept has the entry points it had, and the verdict of item 3 is about
   them. What it does not see: a file the impostor's `RECORD` leaves out (a `.pth`
   in site-packages runs at the next start), the other files of `*.dist-info`
   (`WHEEL`, `INSTALLER`, `direct_url.json`, `RECORD` itself) and the other
   `METADATA` fields; only the files the old and the new `RECORD` list are
   compared, and a different list is a difference.
2. Non-extension receipt entries (for example `--with pip`) are carried as uv
   recorded them, which is what `uv tool upgrade` would do with them anyway.
3. After the install, the installer runs the new aelix's `aelix extension verify`
   (present since 0.1.0) and prints its verdict. Lane's call: on exit 1 (an
   endpoint that does not bind) the update is kept and the line names
   `aelix extension remove <name>`. Undoing what the user asked for is its own
   defect (#154 made the same call for `install`), and the loader already leaves
   out what does not bind.

### D3. The restore ledger is the install record, not the pin store (lane's call)

The brief's "a pinned extension that is not installed" would also reinstall what
the user removed and what was never installed: `aelix extension remove` drops the
install record and keeps the pin (`_cmd_remove`), and strict mode provisions pins
out of band for sources not yet installed (ADR-0187). So a recorded extension is an
`extensionSources` record that an install wrote: kind `git`, `path` or `pypi`
(ADR-0186), with the name of the distribution it installed (`_record_install` has
written one since 0.1.0b1). `aelix extension source add` registers a source with no
name and installs nothing, so a source that was only registered stays uninstalled,
even with a pin provisioned for it (Codex review round 2 M1: an interactive start
installed and loaded such a wheel;
`test_a_source_only_registered_stays_uninstalled_at_start_and_by_restore`). It is
missing when no distribution of that name is installed: by the name only, not by
where another distribution came from, because the source key read from PEP 610's
`direct_url.json` was its `url`, which leaves the `subdirectory` field out, so two
extensions of one commit read as one (Codex review round 2
M5: `restore --all` restored one and `restore <the other>` said it was installed;
`test_two_extensions_of_one_commit_at_two_subdirectories_are_two`). It is
restorable only at an exact pin:

| Record | Restorable when | Restored as |
| --- | --- | --- |
| `git+URL@<40-hex>` | the pin for that repository holds that commit | the record, through the git pin check |
| `git+URL@<40-hex>`, pin at another commit | never: it is not the latest install (below) | named with `aelix extension install git+URL@<the pinned commit>` |
| a file path | the file exists and a path pin holds its sha256 | the file, staged and hashed against the pin |
| a package name | the pin holds a version and its sha256 (`--verify-pypi`), and the environment has pip | `name==version`, download-verified; without pip (the uv backend cannot download, #113) it is named with `uv pip install --python <python> pip, then aelix extension restore <name>` |
| a branch or tag, a directory, an unpinned package | never | named with `aelix extension install …` |

Never an older version than the user last installed (verify round 3 F1). An
automatic restore (the start and `restore --all`) installs nothing it cannot show is
the latest install of that extension:

- An install over an installed distribution adds none, and `_record_install`, which
  names what the install added, wrote its record with no name (0.1.0b2 and 0.1.0
  alike: `install A` then `install B --repin` of one repository, a newer wheel over
  an older one under 0.1.0b2, git after a wheel under 0.1.0). The record without a
  name was not listed, so restore took the older named one: it offered commit A
  with `--repin`, which moved the pin back, and it installed the older wheel with no
  prompt. Since this round such a record names the extension distribution the
  target is: a wheel by its filename (0.1.0 did that already), a git or directory
  target by the installed extension distribution whose PEP 610 `direct_url.json`
  records its `url`, its commit (git) and its `subdirectory`; and a named record
  supersedes the other records of its name, its own earlier copy too, so one record
  of a name is left, the latest install, and it is written last
  (`test_an_install_over_an_installed_extension_names_it_and_keeps_one_record`,
  the three flows through `aelix extension install`;
  `test_the_distribution_a_git_target_is_matches_url_commit_and_subdirectory`).
- Only the last record of a name (list order) is used. On the records 0.1.0 and
  earlier wrote, it is not read as the latest install when a later record with no
  name is one of its extension (the same git repository, or a wheel whose filename
  names its distribution). On any git record, it is also not read as the latest
  when its pin holds another commit: the pin moves with a verified install of
  another commit, so aelix takes the pinned commit for the later install (an
  install with `--no-verify` leaves the pin behind; see Known limits). Such a
  record is not restored, and `list`, `restore` and the start name it with the
  command for the record they read as the latest: the later record's spec (which
  may need `--repin`), or `git+URL@<the pinned commit>` (which passes the pin gate
  with no `--repin`); `restore --repin` does not bring back the older commit
  (`test_a_repository_installed_at_two_commits_is_listed_once_with_the_later_one`,
  which replaces a test that seeded two named records of one repository, a state
  no install writes; `test_a_git_record_whose_pin_holds_another_commit_is_not_its_latest_install`;
  `test_a_wheel_record_a_later_wheel_of_its_distribution_replaced_is_not_restored`).
- A later git or path record with no name that is one of no record's extension (a
  git install of a distribution an earlier wheel installed, under 0.1.0, or a
  source only registered) may be a newer install of any earlier one, so the
  automatic restore leaves every earlier record alone (lane's call: nothing on disk
  tells which it replaced). Each is listed with one line that says why and names
  `aelix extension restore <name>`, which restores it and first prints that a later
  record with no name exists
  (`test_a_later_record_tied_to_nothing_keeps_the_automatic_restore_off_earlier_ones`,
  `test_what_ties_a_later_record_and_what_it_holds`).

### D4. `extension list` and `extension restore`

`aelix extension list` ends with a "Recorded but not installed" section: each
record, its identity, and either `restore at its pin: aelix extension restore
<name>` or why it is not restorable and the install command. It reads the settings
file without a lock: `SettingsManager.create` loads through
`FileSettingsStorage.with_lock`, which, when `settings.json` exists, opens it
read-write and holds an exclusive `flock` on it while it reads (POSIX; Windows
takes none), and, when it does not, reads nothing and creates nothing (verify
round 1 F3 measured `dir exists False settings.json False` after a `create` on a
missing agent dir). `read_only_settings` reads the text the same way and parses it
in memory, so a listing or a start takes no lock and opens nothing for writing.
`aelix extension verify` (for every extension, not `verify <name>`, after either
verdict) and `aelix status` (when it looks at extensions) add one line naming the
recorded extensions that are not installed and pointing to `aelix extension
restore`; verify's exit code stays its inventory's. `aelix status --json` carries
the same names as `recorded_not_installed`: a list (empty when none is missing), or
`null` when the records cannot be read (verify round 2 N4;
`test_status_reports_no_list_when_the_records_cannot_be_read`).

`aelix extension restore [--all | <name>] [--yes] [--offline] [--repin]` reinstalls
through `install_extension`, in aelix's installer directory (a record, ADR-0255
§15), with its consent prompt and pin gate: a file whose bytes changed, or a
package whose download differs, is refused without `--repin`
(`test_restore_refuses_a_file_whose_bytes_changed_without_repin`). A record that
is not its extension's latest install (D3) is not restored, with or without
`--repin`: `restore` prints `Not restoring <name> (…): it is not the latest install
(…)` and `Install the latest with: aelix extension install <latest>`, and `list`
prints `not its latest install:` and `install the latest:` in place of the restore
line (round 1 F6 had made `restore --repin` bring back the recorded commit; round 3
F1 showed that commit is the older one). `restore --all` leaves a held record (D3)
alone with its line, and `restore <name>` restores it. Exit codes are
`update`'s: 0, 1 an installer failed, 2 one did not run (not restorable, refused,
declined), 3 restored but not bound; the hardest wins. It takes the index rules of
`extension install`: the registered indexes resolve a package name and, with
`--offline`, are the dependency index of a git or path restore too (Codex review
round 2 S3: it passed `--no-index` where `install --offline` used the registered
index; `test_restore_offline_takes_the_registered_index_as_install_does`). When the
settings file cannot be read, or is not JSON, it exits 2 and says it cannot tell
what to restore (Codex review round 2 S4: it printed `every recorded extension is
installed` and exited 0;
`test_restore_with_a_settings_file_it_cannot_read_says_so_and_fails`).

### D5. The start restores, interactively (owner: "시작 시 복원")

Before the harness is built (which loads the extensions), `restore_at_start`:

- does nothing in a delegated agent, under `--no-extensions`, or outside a `uv
  tool` environment (lane's call: only an environment rebuilt from a receipt loses
  extensions this way; a `pip install` venv keeps them on an upgrade, and a
  checkout's `.venv` belongs to `uv sync`);
- headless (`-p`, `--mode json|rpc`) installs nothing and prints one stderr line
  naming `aelix extension restore` (the lead's proposal, kept: a scripted run did
  not ask for a network install that runs build code);
- `--offline` (or `PI_OFFLINE` / `AELIX_OFFLINE`) skips with a line naming the
  command;
- otherwise restores each restorable record with no prompt and never with
  `--repin` (the bytes are the ones already consented to and pinned), its
  installer output captured, one line for what is restored and one per result; a
  failure is a line with the installer's error and the retry command, and the
  start goes on. A record that is not restorable gets a line with its command, at
  every start while it stays that way: each such line names the step that makes it
  restorable or the install command, so it points at its own fix. A record that is
  not read as its latest install gets the command for the record aelix reads as the
  latest, and a held one `aelix extension
  restore <name>` (D3); neither is installed. The lines are
  broken at spaces to the terminal's width (a terminal broke them inside a word),
  and a command is never broken: one in backquotes keeps its spaces, and the one to
  run gets a line of its own (verify round 2 N6: copying the visible lines split
  `aelix extension install … --repin` in two;
  `test_a_printed_command_is_never_broken_across_lines`).
- each installer child runs through `run_contained` (#221): no stdin, a session
  (POSIX) or a process group and job (win32) of its own, and its whole tree is
  ended at the 120 s bound, so the `git` a `uv pip install` of a git source starts
  does not outlive it (verify round 1 F9: it ran on at ppid 1;
  `test_the_start_up_child_ends_its_whole_tree_on_the_timeout`).
- Ctrl+C during the restore stops it and the start goes on (lane's call). The
  restore runs before the TUI exists, and what the user interrupts is a wait they
  did not ask for, not aelix; a second Ctrl+C in the TUI quits as usual. Under
  `asyncio.run` the first Ctrl+C only cancels the main task at its next `await`,
  after the child has run to its end, and then ended the start in a traceback (3
  of 3 runs, verify round 1 F4), so the restore puts Python's default SIGINT
  handler in for its loop and the previous one back after it; the child, in its
  own session or group, is ended by `run_contained`'s interrupt leg, and one line
  names what was not restored (`test_ctrl_c_during_the_start_up_restore_stops_it_and_the_start_goes_on`).

### D6. `aelix extension install` does not write the receipt (lane's call)

The only uv command that adds a requirement to a receipt is `uv tool install
--force … --with`, which rebuilds the environment of the interpreter that runs it.
On macOS that works mechanically (measured: a tool's own python ran it, its prefix
came back with a new inode, and the new and the kept packages imported), but it
re-resolves aelix and every dependency from the index rather than the
checksum-verified wheels, the receipt's `--find-links` directory is the installer's
deleted temporary one, and on Windows the running interpreter is held open (not
measured here). Writing `uv-receipt.toml` directly would edit another tool's
private file. Neither is done. The net is D1 at the next update and D5 at the next
start; `uv tool upgrade aelix` does remove an extension added since the last
update (measured: `aelix-ctrl`, installed after the carry, was gone after the
upgrade, while the two carried ones stayed).

### D7. The in-place option (case D) is not used

`uv pip install` of the new aelix into the existing environment kept the extension,
but the receipt still says `==0.1.0`: uv's own record is then wrong about the
version, and the next `uv tool` operation rebuilds the old one. The owner chose
the receipt route over it.

## pi

pi solves the same job in `resolve()`: a configured package that is not installed
is installed while resources resolve, skipped offline, and a failure is reported
without aborting the start (`packages/coding-agent/src/core/package-manager.ts:1482-1503`
at `29e3fccf7`). Its packages live under `<agentDir>/npm` (`getNpmInstallRoot`,
`:2320-2329`), outside pi's own install, so reinstalling pi never removes them.
aelix diverges because an extension is a Python distribution that must import in
aelix's own interpreter, so it lives in the environment a reinstall rebuilds: the
installer carries it (D1) and the start restores it (D5). The restore also takes
only exact pins, where pi installs the configured source, because ADR-0187's pins
are aelix's install-time trust check. This lane did not check whether every pi mode
reaches `resolve()`.

## Known limits

- An index install is carried as `name==version` from the installer's own index
  configuration (PyPI unless uv is configured otherwise), not from an index
  registered with `aelix extension source add`, and not with the installer
  widening its own index list to one: that would let such an index answer for
  aelix's dependencies too. When the installer's index answers other bytes, or
  nothing, the extension is not carried and is named with its command.
- The one-at-a-time fallback runs one more install per carried requirement, only
  after the full set failed.
- `uv tool upgrade aelix` over an install-line install still exits 1 while the
  receipt names the installer's deleted `--find-links` directory (RELEASING.md,
  #383's U2); it keeps the extensions by changing nothing. CI makes the directory
  again, empty, to see the upgrade sync and keep them.
- A distribution restored at start is importable in that start, but a `.pth` file it
  ships is read only at the next start.
- What is restored, and what is not (D3). Restored: the last record of a name, when
  it is at its pin (a git commit the pin holds, a file whose sha256 it holds, a
  package version and sha256 where pip runs) and no later record could be a newer
  install of it. Not restored: a record with no name (what `source add` writes, and
  what 0.1.0 and earlier wrote for an install over an installed distribution); a
  named record that a later record of its extension, or a moved git pin, shows is
  not the latest (named with the command for the record aelix reads as the
  latest); every named record before a
  record with no name that is one of no record's extension (named with `aelix
  extension restore <name>`; lane's call). The last is conservative: a source only
  registered after an install holds that install back too.
- The command printed for a record that is not restored is the one for the record
  aelix reads as the latest. Verify round 4 ran three flows where that reading is
  wrong, each after a `uv tool upgrade aelix` wipe; in none is anything installed
  by itself:
  - **`--no-verify` (records this build writes).** `install A`, then `install B
    --no-verify` (the pin stays at A), leaves one record named B. aelix prints "its
    pin holds A, a commit installed after this record" with `aelix extension
    install …@A`, and following it installs the older A.
  - **A registered source.** `install A`, then `source add` of the same repository
    at commit B (a record with no name), reads B as a later install. A is not
    restored, and the printed command is B's, which needs `--repin`. `restore
    <name>` does not restore A either (verify round 4's report; the code's stale
    branch ignores the name).
  - **0.1.0's records.** `install A`, `install B --repin`, `install A --repin`
    leaves the same records and pin as 0.1.0's `install A`, `install B --no-verify`
    (A named, B with no name, the pin at A), so no rule gets both. aelix reads B as
    the latest and prints B's command, which needs `--repin`, though A is the
    latest.
  In each, reinstall the commit you want with `aelix extension install
  git+URL@<commit> --repin` (not run from these states).
- List order is the order a record was first written: `_upsert_source` leaves a
  record where it is when the same source is installed again (read from the code,
  not run). So for records 0.1.0 and earlier wrote after `install A`, `install B`
  (another source of the same distribution, with A removed by hand in between) and
  `install A` again, the last record is B's, and B's command is the one printed,
  though A is the latest. Since this round a named install drops the other records
  of its name, its own earlier one too, so its record is written last (D3;
  `test_a_reinstall_writes_its_record_last`).
- An install over an installed distribution still writes a record with no name when
  no installed extension distribution's `direct_url.json` matches the target: a
  source archive (`.tar.gz`), which aelix stages for its pin as it does a wheel, so
  its record names the staged copy, or a git or directory target that installs no
  extension itself (a bundle). Both are read from the code, not run; such a record
  holds the earlier ones back as above.
- An editable extension the receipt asked for with extras is carried without them,
  and its line says so.
- Windows PowerShell 5.1 was not run. Its argument passing was reproduced with
  pwsh 7's `$PSNativeCommandArgumentPassing = 'Legacy'` on macOS, in the tests and
  in a full install.ps1 run with real uv.
- install.ps1 was not executed on Windows here; its Step 5 lines run under pwsh 7
  on macOS (and on CI's ubuntu image) with stub programs in
  `tests/packaging_gate/test_install_carry.py`, and the `install.ps1 e2e` jobs run
  the whole script on Windows.

## Measured on the way (not changed here)

On macOS with Homebrew uv 0.11.19, `AELIX_VERSION=v0.1.0-beta.2` fails on the base
install.sh too: `aelix-agent-core was requested with a pre-release marker …, but
pre-releases weren't enabled`, since PyPI has `aelix-agent-core` 0.1.0 beside
0.1.0b2. With uv 0.13.10 (what the CI jobs bootstrap) the same line installs
0.1.0b2, and the carry took both test extensions into it.

## Tests

- `tests/packaging_gate/test_install_carry.py`: the two copies equal and the
  program clean under the repository's ruff configuration; every PEP 610 and
  receipt shape (receipt extras and markers too), and each input in a shape it
  does not expect (a `METADATA` that cannot be read too); the check on the bytes on
  disk and on the entry points and dependencies; the line for one uv could not
  install; both installers' Step 5 lines (install.sh under sh, install.ps1 under
  pwsh in its default mode, in Legacy argument passing and with
  `$PSNativeCommandUseErrorActionPreference` on) with a stub uv on every path
  (carried, taken out, one of several failing, the last one failing, all failing,
  nothing to carry, an environment that cannot be read, aelix itself failing).
- `tests/cli/test_extension_restore_926.py`: which records are missing and
  restorable (a registered source is not; two subdirectories of one commit are two;
  a package pin only where the installer downloads; the last record of a name; one
  a later record or a moved pin shows is not the latest; one a later record tied to
  nothing holds), what an install over an installed extension records (the
  verifier's three flows, the PEP 610 match), `list`, `restore` (pin match,
  mismatch refused, `--repin`, a moved git pin, a package pin without pip, not
  pinned, by name or source, `--offline`'s index and none online, an unreadable
  settings file),
  `verify` (both verdicts) and `status` (text, JSON, records it cannot read), and
  the start (interactive, failure, headless, offline, unpinned, delegated,
  `--no-extensions`, outside a tool environment, never `--repin`, Ctrl+C and what it
  names, the contained child and its timeout, wrapped lines and whole commands,
  never raises).
- `tests/test_ci_install_extension_926.py`: both e2e jobs add an extension before
  the re-run and assert it after; post-conditions 5 and 6 of `assert-install-sh.sh`
  fire.
