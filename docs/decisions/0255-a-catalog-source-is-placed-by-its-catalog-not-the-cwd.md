# 0255. A catalog entry's source is placed by its catalog, not by the working directory

Status: Accepted (2026-10-06; revised 2026-10-07 after review rounds 1, 2, 3, 4, 5 and 6 and verify round 7, §7, §8, §9, §10, §11, §13, §14; threat model §12) — §12 amended 2026-10-07 by #392 (§15) and corrected 2026-10-08 by its review rounds 2, 3, 4 and 5: `discover install` and every `update` run the installer in aelix's installer directory, whose `pyproject.toml` (no `[project]`) and empty `uv.toml` make uv read no project configuration — none from the cwd, its parents, or the agent dir's ancestors; that directory and its two files are refused when they are links at preparation time, every runner is handed the directory, the user's own environment and user/system installer configuration reach the installer as set (inside the trust boundary, §12), and the one variable checked is `UV_CONFIG_FILE`, refused for those installs unless it is a bare absolute path; the directory follows the target's origin (a `ResolvedPath` or `CatalogSpec` from the resolver), not a caller flag; a typed `extension install` keeps running in the cwd (a stated limit) — amended 2026-10-08 by #405 (§16): a catalog or record spec (`CatalogSpec`) is classified by its spelling, never by what the cwd holds, so `update` and `install_extension()` no longer install a cwd entry named like a recorded package; one spelled as a relative path, and a relative path record, are refused; `discover install`'s cwd-collision refusal (§2 (5)) is gone — the package is installed; corrected 2026-10-08 by #405 review round 2 (§16): a caller-supplied kind that disagrees with a `CatalogSpec`'s or `ResolvedPath`'s spelling is refused (`verify_and_pin`, `build_pip_args`), so is a relative `ResolvedPath`, a PEP 508 requirement is a package whatever its marker holds, and a path the user TYPES as `update`'s filter is that path, resolved as typed; corrected 2026-10-08 by #405 review round 3 (§16): round 2's whitespace refusal is replaced by one normalisation — a `CatalogSpec` that is not spelled as a path (package, URL, git) is stripped once where it is built and every check and the installation read that string, while a path spelling keeps its exact string and is judged absolute or relative on it; a requirement with a version specifier, marker or URL is a package before the bare-archive test (`x==1.0+v.whl`); the relative-path check runs whatever kind a caller passes; a typed `update` filter is never stripped and is resolved by the typed install's own function; the printed `source remove` advice says it matches by name too; corrected 2026-10-08 by #405 review round 4 (§16): a requirement with a version specifier or a marker is a package before the `.git`-suffix git reading (`probe405==1.0+vendor.git`; a bare `acme.git` stays git, and the resolver now refuses only that bare form), a `ResolvedPath` must be absolute exactly as written (`~` is not expanded) and is refused, never raised, by all three Python entry points, the `source remove` advice says it matches by path too, and the relative-path refusal is stated with its exception (`file:x`, `name @ ./x` in a record or a Python `CatalogSpec`)
Date: 2026-10-06
Amends: **ADR-0188** §2 (`discover install` no longer hands `entry.source` to the
installer unchanged; a dated note there points here) and the #68 `extension index
--relative` contract (bare filenames measured from `<dir>` become `./`-prefixed paths
measured from the catalog file's directory).
Relates: ADR-0188 §4(b) (consent sees the RESOLVED spec — it still does, now an
absolute path for a path entry), ADR-0192 (the default catalog is https, so it can
never carry a usable relative source), ADR-0235 (pi is the reference, not a parity
mandate).
Issue: #131 (P0, beta.3 exit condition 1). Decisions: the four in the issue's batch
brief (2026-10-06), §2; the ones the brief left open were made by the lane, §5; the
round-1 review decisions (2026-10-07), §7; the round-2 main-loop decision (an allowlist —
refuse, don't rewrite; 2026-10-07), §2 and §8; the round-3 main-loop decisions (the
allowlist stays; a path and its extras travel as two values; 2026-10-07), §9; the
round-4 fixes (2026-10-07), §10; the round-5 main-loop decisions (a resolved path reaches
the installer as a `file://` URI; a scheme not in lowercase is refused; `name @ git+…` is
never re-prefixed; 2026-10-07), §2 and §11.
pi: `b223082bb`, §4.
Tests: `tests/cli/test_catalog_relative_source_131.py`, `tests/cli/test_extension_index.py`
(the `--relative` rows), `tests/cli/test_extension_discover.py` (the `-`-leading row),
`tests/cli/test_record_spec_never_read_from_cwd_405.py` (#405, §16).

## 1. What was measured

On `aab1f210`, the real CLI, an isolated agent dir and settings file. A local catalog
at `catdir/catalog.json` lists `./local-ext`, a directory beside it; the command runs
from a sibling `elsewhere/`. The middle column is the route `aelix` printed with stdin
closed (consent aborts; `.omc/probes/131-live/impl/repro-before-aab1f210.txt`); the
right one is what the installer then did with `--yes` and `--index-url` pointing at a
local request recorder (`.omc/probes/131-live/verify/out-reach-before.txt`):

| Entry `source` | cwd | `aab1f210` printed | the installer (uv) did |
| --- | --- | --- | --- |
| `./local-ext` | `elsewhere/` | `Install extension from pypi: ./local-ext` | read `file:///…/elsewhere/local-ext` — the cwd; no index request |
| `./local-ext` | `catdir/` | `Install extension from path: ./local-ext` | the copy beside the catalog |
| `local_ext-0.1.0-py3-none-any.whl` (beside the catalog) | `elsewhere/` | `from pypi` | read it from the cwd; no index request |
| `local-ext` (a directory beside the catalog) | `elsewhere/` | `from pypi: local-ext` | `GET /simple/local-ext/` — an index lookup |
| `./not-there` | `elsewhere/` | `from pypi: ./not-there` | read it from the cwd; no index request |

So the hazard of a relative path was **cwd substitution**: pip and uv read `./x`
(and `file:x`, `x.whl`) from the process cwd, so the entry installed whatever that
directory held — the catalog's copy only when run from the catalog's directory —
while `aelix` labelled and gated it as a package. Only a bare name reached the index.
The same entry served from a local TLS server (`https-before-aab1f210.txt`) was `from
path` when the cwd happened to hold a `./local-ext` and `from pypi` otherwise. The
cause is one line: `classify_target` calls a target a path only if
`Path(target).exists()` from the process cwd, and `discover install` passed it the raw
`source`. The caveat was documented (the guide, `build_index_catalog`'s docstring, a
comment in `_cmd_index`), not fixed.

Round 3 measured the same hazard in the shapes the second review found, on the uv
backend (uv 0.11.19, `uv pip install --dry-run --offline --no-deps`, run from
`elsewhere/` holding a decoy wheel of the same file name;
`.omc/probes/131-live/fix3/uv-measure.txt`): `name @ ./x.whl`, `name@./x.whl`,
`name @ x.whl`, `name @ file:x.whl` and `file:x.whl` all resolve to
`file:///…/elsewhere/x.whl` — the cwd — with no error, and `name @ file:` reads the cwd
itself. pip 26.2.1 rejects the scheme-less forms (`Invalid URL … No scheme supplied`,
`.omc/probes/131-live/verify2/EVIDENCE.txt`), so the hazard is the uv backend — the one
an official `uv tool install` has.

After review round 3 (`.omc/probes/131-live/fix4/uv-measure.txt`, the same uv and
flags): a host-form `file://local_ext-0.1.0-py3-none-any.whl` resolves to
`file:///…/elsewhere/local_ext-0.1.0-py3-none-any.whl` — uv reads the host as a path
segment under the cwd (verify round 3 measured `file://local-ext`,
`file://localhost.evil/x` and `file://./x` the same way). And
`<abs>/cat/x-1.0.whl[feature]`, with a sibling LITERALLY named `x-1.0.whl[feature]` (a
symlink to a 9.0 wheel), installs `<abs>/cat/x-1.0.whl`: uv, like pip's
`_strip_extras`, splits the trailing `[…]` whatever exists.

Review round 5 measured the hand-off itself (uv 0.11.19 and pip 26.2.1, offline, real
installs into fresh environments, a decoy at every sibling a re-parse could open;
`.omc/probes/131-live/fix6/uri-matrix.txt`, 558 installs). Given the resolved path as a
bare string, the backends parse it AGAIN: uv opened `<dir>/trusted` for a directory named
`trusted#release` (a `#` starts a fragment), `trusted[]`, `trusted[x]` or `trusted ` (a
trailing space), pip did the same for `trusted[x]` and `trusted `, and pip refused a path
holding `;` (a marker). Given `Path.as_uri()` — `[`, `]`, spaces, `;`, `%`, `?`, `@`, a
backslash, a tab, non-ASCII percent-encoded — both installed the named copy for a wheel,
an sdist and a project directory, every time but one: uv decodes `%23` and cuts at the
`#` all the same, so a path holding `#` installs the stripped sibling in every spelling
uv takes (`.omc/probes/131-live/fix6/lab/hash_probe.py`). Extras do not ride on an unnamed
URL for pip (`file:///…/x.whl[feature]` is a file of that name to it); `name[extras] @
file:///…` is accepted by both. uv also took `local-ext @ FILE:///<abs>/x.whl` for the
relative path `<cwd>/FILE:/<abs>/x.whl` and installed the cwd's decoy (Codex pass 5). For
a git repository after `name @` (`.omc/probes/131-live/fix6/measure-git.txt`, a local
dumb-HTTP server): `local-ext @ git+http://127.0.0.1:24300/r.git` (also `@<sha>`, also
with extras) installs on both; `local-ext @ http://…/r.git` installs on uv (it clones)
and fails on pip ("Cannot determine archive format"); and the `git+local-ext @ …` string
the installer used to build is a parse error on uv and, on pip, a PATH under the cwd —
verify round 5 built the cwd's decoy that way (`.omc/probes/131-live/verify5/
gitname-head.txt`).

## 2. Decision

`discover install` turns an entry's `source` into an install target with
`extension_catalog.resolve_entry_target` before the installer sees it (a spec string,
or for (C) a `ResolvedPath`; `resolve_entry_source` is its `(spec, is_path)` view). Each entry
carries `catalog_location` (not serialized, not part of equality), the location of the
catalog it was parsed from. The decision is an **allowlist: a source is accepted in
exactly these forms, and every other one is refused — never rewritten** (revised in
round 2, §8; the first two versions rewrote relative forms, which lost URL fragments
and missed shapes):

- **(A) A package requirement without a direct reference** — a name, optionally with
  `[extras]` and a version specifier (`acme-notes`, `acme-notes==1.4.0`,
  `acme-notes[x]>=1,<2`; parsed with `packaging.requirements.Requirement`, `url` and
  `marker` both absent) — is handed on unchanged, to the index. A name ending in `.git`
  is refused (round 3): the installer routes such a target as git (`git+acme.git`, which
  no backend can fetch), so "to the index" would be false for it. Since #405 review
  round 4 (§16) only a bare name: one with a version specifier (`x==1.0+vendor.git`) is
  a package to the installer too, and goes to the index.
- **(B) An absolute URL, its scheme written in lowercase,** is handed on UNCHANGED,
  fragments (`#sha256=`, `#subdirectory=`, `#egg=`) and extras byte-for-byte as written:
  `https://host/…`,
  `http://host/…`, `git+https|http|ssh|git://host/…`, `git+file:///…`, `file:///…`,
  `file://localhost/…` (since round 6 byte-exact: the host empty or `localhost` in
  lowercase, and no `%` in the path before any `#` — §13), and the git transports
  `classify_target` already routes as git on their own — `git://host/…`, `ssh://host/…`,
  scp-style `<user>@host:path` with any user (`git@…`, `deploy@…`;
  `extension_catalog.is_scp_git`), which the installer's own `_normalize_git_spec`
  turns into `git+ssh://`, as for a typed one.
  So is `name @ <an https, http, git+ or file:///… / file://localhost/… URL>`, a marker
  after it included. Plain `http://` is accepted for an ENTRY source (the https-only rule
  is for the catalog LOCATION, ADR-0188). A `file://` URL with any other host —
  `file://x`, `file://localhost.evil/x`, `file://./x` — is refused (2): uv reads the host
  as a directory under the cwd (§1). The installer, not the resolver, then gives the git
  transports their `git+` prefix exactly as for a typed target: `<user>@host:path` becomes
  `git+ssh://<user>@host/path` (the user part kept), `git://…` and `ssh://…` become
  `git+git://…` and `git+ssh://…`, and an `http(s)` URL whose path ends in `.git`
  becomes `git+https://…`. Since round 5 a direct reference is never re-prefixed:
  `name @ git+https://…` reaches the installer as written, routed as git, its pin
  identity and PEP 610 source key read from the URL after `@`
  (`extension_catalog.direct_reference_url`; the installer used to build `git+name @ …`,
  §1). `name @ <an http(s) URL whose path ends in .git>` without `git+` is refused with
  the `name @ git+…` spelling to use — the two backends disagree on it (§1). A scheme
  written with any upper-case letter (`FILE:`, `Https:`, `GIT+https:`, `git+HTTPS:`,
  `SSH:`), whole or after `name @`, is refused with a message to write it in lowercase:
  a URL is passed on exactly as written, and uv does not read `FILE:` as a scheme (§1).
- **(C) A path**: an absolute path, a `~` path, or a relative path that STARTS with
  `./` or `../` (or `.\` / `..\`), optionally followed by pip's `[extras]`; and a bare
  archive file name counts as a relative one — any name with no `/` or `\` that ends in
  `.whl`, `.zip`, `.tar.gz`, `.tgz`, `.tar`, `.tar.bz2`, `.tbz`, `.tar.xz`, `.txz`,
  `.tlz`, `.tar.lz` or `.tar.lzma` (pip's `ARCHIVE_EXTENSIONS` plus `.whl`), spaces
  included, unless it also reads as a URL (a scheme) or a `name @` reference, which (B)
  and the refusals decide. That is every name `scan_artifacts` lists and an older
  `index --relative` wrote bare (round 3 kept a character whitelist that refused
  `team notes-1.0.tar.gz`, §9). The extras are split off ONCE, here, with pip's own
  `strip_extras` pattern and, as pip does, the path part right-stripped
  (`./x.whl [feature]` is `./x.whl`, §10); the path is checked without them. A path must exist; it is
  resolved ABSOLUTE with `Path.resolve()`, which reads `link/..` the way the OS opens
  that spelling (the join is not normalized lexically): on POSIX it follows `link` before
  `..`; on Windows the Win32 path layer itself collapses `link\..` before it looks at a
  link, and `Path.resolve()` agrees with it (windows CI 37561017353, where the OS opened
  `catdir\real-ext` for `./link/../real-ext`), and handed to the installer
  as a `ResolvedPath(path, extras)` — two values that the installer, the verify-and-stage
  copy, the pin and the install record use as given, never re-split from a joined
  string (§9). Since round 5 the installer receives it as a percent-encoded absolute
  `file://` URI (`ResolvedPath.installer_arg`, `Path.as_uri()`), never as a path string a
  backend would parse again (§1): with extras as `name[extras] @ file:///…`, the name
  read from a wheel's or sdist's file name or a directory's `pyproject.toml` `[project]
  name` (`extension_catalog.local_project_name`); default verification's staged copy
  travels the same way. So a resolved name ending in `[…]` (refused in rounds 3 and 4)
  installs as named, and `[]`, a trailing space, `;` and a backslash are carried
  literally. Refused instead: a resolved path holding `#` anywhere (uv cuts there in every
  spelling, §1), and extras on a path whose project name cannot be read (a `setup.py`-only
  directory). The `Resolved` and `Install extension from` lines print the path the URI
  names.
  - A relative one resolves against the PHYSICAL directory of the LOCAL catalog file
    (a bare absolute location or a `file://` URL, `Path.resolve()`-d, so a symlinked
    catalog resolves beside the file it points to — the directory `extension index
    --relative` measures from), never the cwd. In an `https` catalog, a git catalog
    (its clone is discarded after the read) or a cached catalog whose location is
    itself relative it is refused, naming the entry, the catalog and which of those
    it is.
  - An absolute or `~` path keeps the treatment it had since round 1: it depends on
    neither the cwd nor the catalog's location, so it is accepted from ANY catalog,
    local, https or git (a served catalog can point at a shared mount); `~` expands to
    the home directory of the user running the install.

**Refused** with an error that names the entry and the catalog and lists the accepted
forms (`ACCEPTED_SOURCE_FORMS`):

1. `name @ <relative path or bare word>` — `name @ ./x`, `name@./x`, `name @ x`,
   `name @ ../x` (uv reads them from the cwd, §1);
2. every `file:` URL that is not `file:///…` / `file://localhost/…` — `file:x`,
   `file:./x`, `file:`, `file:#subdirectory=x`, `file:/abs`, and the host forms
   `file://x`, `file://localhost.evil/x`, `file://./x`, since round 6 also
   `file://LOCALHOST/…` (the host is compared byte-exact) and any `file:` URL whose path
   holds a `%`-escape (§13) — and `name @` one of them, with
   or without fragments; likewise `git+file:x` and a URL with no host (`https:x`,
   `git+https:///x`). Each message is true of its spelling (round 5): a relative one is
   read from the cwd by uv; a host form names a host aelix does not accept (uv reads it
   as a directory under the cwd); `file:/abs` is a spelling aelix does not accept —
   write `file:///abs` — it names no cwd;
3. a source starting with `-` — the installer's argv has no `--`, so pip and uv would
   read `-e ./x` or `--index-url=…` as an OPTION (no PEP 508 name starts with `-`;
   #11's `--` guard only keeps `discover install`'s own parser from misreading it);
4. a relative path without the `./` prefix (`wheels/x.whl`, `.`), a requirement with
   an environment marker, a package name ending in `.git` (a bare name — #405 r4), a URL scheme not written in
   lowercase and `name @ <an http(s) URL ending in .git>` (B), a resolved path holding
   `#` and extras on a path with no readable project name (C), and anything else not in
   (A)-(C);
5. kept from round 1: a bare package name that also names a file or directory beside
   a local catalog (ambiguous — write `./name`; since round 3 also a bare name with
   `[extras]` whose name does, `local-ext[extra]` beside `./local-ext` — write
   `./local-ext[extra]`; a version specifier says "package" and is not checked — since
   round 5 only the NAME of a requirement without one is compared, so a neighbouring file
   literally named `local-ext==1.0` no longer refuses `local-ext==1.0`), and — checked by asking
   `classify_target` itself and requiring its answer to match the resolver's — a
   package spec that names a file in the cwd, which aelix's installer would install
   instead — it is `classify_target` that takes a target existing on disk for a path;
   pip and uv ignore a literal `review-ext[feature]` directory (round 6, the message
   says so) — ("Run the command from another directory", true because only a package
   spec reaches it), or a resolved path that vanished before the installer looked
   (asked of the `ResolvedPath` directly since round 3: it is not re-classified).
   *Amended 2026-10-08 by #405 (§16):* the cwd-collision refusal is gone —
   `classify_target` reads a `CatalogSpec` by its spelling, so a package spec that
   names a cwd entry is installed as the package; the vanished-path refusal stays.

Also part of the decision:

6. **What is shown is terminal-safe.** The `Resolved` line, the `Install extension
   from` line, the no-backend message, the refusals, the refresh's anchoring notice and
   — since round 3 — every line the integrity gate prints (`ⓘ verify: …`, `Verification
   refused / error`, `integrity verification skipped`, `could not record integrity pin`,
   `could not record install source`, the pypi download line) print through
   `aelix_ai.utils.terminal_text.safe_for_terminal`: a resolved path can carry control
   bytes the catalog never wrote (a symlink target's name; round 2 measured an OSC 52
   clipboard write, round 3 the same bytes in the post-consent first-acquisition line,
   which names the target's basename). The installer still gets the real path; the argv line under the
   announcement escapes it with `_printable`, as before. (A control byte in the
   `source` itself never gets this far: `CatalogEntry.from_json` skips such an entry.)
7. **`extension index --relative`** emits `./`- or `../`-prefixed POSIX paths measured
   from the physical directory the catalog FILE is written to, and prints that file's
   physical `file://` URL to register. `--out -` writes no file, so it measures from the
   scanned directory itself (resolved) — not from wherever a `<dir>/catalog.json`
   symlink there points (round 3). Absolute stays the
   default, because a relative source is refused once a catalog is served.
8. **What the user types keeps its meaning**, with one change: `classify_target` reads
   a path-shaped target with pip's `[extras]` (`./x.whl[feature]`) as a path when the
   part before the extras exists. pip already installed that file; `aelix` used to
   label it `pypi` (offline refusal, pypi-style pin key). A bare `name[extra]` stays a
   package spec even beside a `./name`, as it does for pip. The path helpers that touch
   the filesystem (`_install_spec`, `_pin_identity`, the verify-and-stage copy,
   `_target_dist_hint`, `_target_source_key`) split the extras off the same way, so the
   wheel is hashed and staged and the extras go back on the staged argv. The split is
   the backend's: since round 3 a sibling LITERALLY named `x.whl[feature]` does not win
   it (pip and uv open `x.whl` whatever exists, §1), so the file hashed and pinned is
   the one installed.
   (`source_looks_like_path` is this typed-target predicate; it no longer decides a
   catalog source.)
9. **A relative catalog LOCATION is anchored at refresh.** `source add --catalog`
   already stores a path absolute (from the cwd where it was typed). A relative path
   left in a hand-edited settings file is read by `discover --refresh` from the cwd
   anyway; the refresh now says so (`ⓘ catalog location 'catalog.json' is a relative
   path: read it from the current directory as '<abs>' …`), caches that absolute
   location — so the entries resolve beside the file that was actually read — and
   caches the spec as registered beside it (`registeredAs`), so `discover install
   --catalog catalog.json` still selects that catalog from any cwd (the absolute
   location and the document `name` match too). Since round 3 a RELATIVE `--catalog`
   selector also matches a local catalog whose file it names from the current directory
   (`extension_catalog.select_catalogs`): `source add --catalog catalog.json` stores the
   absolute path, and `--catalog catalog.json` typed beside the file, or
   `--catalog ../catalog.json` from below it, selects it; since round 4 an absolute one
   is compared by the file it names too, so the symlinked spelling `source add` was
   given (stored resolved) selects it. "Registered" is asked of the registered sources,
   not of the cache (round 4, §10): a selector that names a registered catalog the cache
   has no copy of — not fetched since `source add`, skipped by an offline refresh, or
   cached before `registeredAs` — says exactly that and how to fetch it (since round 5:
   it names the offline skip, and while `AELIX_OFFLINE` / `PI_OFFLINE` is set and the
   catalog is a network one it says to unset them and refresh online, never a
   `--refresh` that would skip it again); a selector that names a relative registration
   whose cached copy was read from another directory says where that copy came from and
   how to select it (it said "no copy recorded", round 5); one
   that picks only catalogs whose last refresh failed says that, with the recorded
   error; only a selector that names no registered catalog says so (adding, when some
   registered catalog has no fetched copy, that its name is not known until a refresh).
   A cache written before this change,
   with a relative location, is refused with a message that names the fix
   (`discover --refresh`).

The resolved spec is what consent shows (ADR-0188 §4(b)), and the `Resolved` line adds
`the catalog says '<raw>'` when the two differ.

## 3. Sweep — every site that reaches the decision

| Site | Before | After |
| --- | --- | --- |
| `_cmd_discover_install` → `_cmd_install([… "--", resolved.source])` | cwd-classified | target from `resolve_entry_target`; a path passed as `resolved_path=ResolvedPath(...)`, the argv carrying only its display form; refusals before consent; shown terminal-safe |
| `_cmd_install` announcement (`Install extension from <kind>: …`) and the no-backend message | the target raw | through `safe_for_terminal` (typed targets too) |
| `_print_verify` and the gate's refusal / error / warning lines | raw (a symlink target's basename reached the first-acquisition line) | through `safe_for_terminal` (6) |
| `classify_target` (`extension install`, `update`, `_normalize_catalog_spec`, `install_extension`, `_attributed_dists`) | cwd-relative for typed targets | unchanged for typed targets except (8); a catalog source no longer reaches it raw; since #405 (§16) a `CatalogSpec` is classified by its spelling, never against the cwd |
| `_install_spec` / `build_pip_args` / `_pin_identity` / `verify_and_pin` path branch / `_target_dist_hint` / `_target_source_key` | `Path(target)` with any `[extras]` inside the file name | a `ResolvedPath`'s two values as given; a typed string split the backend's way (8); since round 5 `build_pip_args` and the staged copy hand a `ResolvedPath` over as `ResolvedPath.installer_arg()` (a `file://` URI, `name[extras] @ uri` with extras) — a typed path keeps its path string |
| `classify_target` / `_normalize_git_spec` / `_pin_identity` / `_target_source_key` (git) | `name @ <url>.git` → git, prefixed `git+name @ …`; `name @ git+…@<sha>` → pypi | `name @ git+…` → git, never re-prefixed, pin and source key read from the URL (round 5) |
| `_record_install` → `_install_spec` | records `str(Path(target).resolve())` — the cwd's reading | records the already-absolute spec; since round 6 a catalog path as its installer URI (`_path_record_spec`) |
| `_cmd_update` → `_upgrade_source` (path records) | the recorded string, re-parsed by the backend | a `ResolvedPath` read back from the URI or re-derived from a plain path (`_recorded_path_target`), handed over as the URI (round 6) |
| `build_index_catalog(relative_to=…)` / `_cmd_index --relative` | bare filenames from `<dir>` | `./…` / `../…` from the catalog file's physical directory; from the scanned directory for `--out -` |
| `_cmd_discover --refresh` → `fetch_all` bare-path location | `Path(loc)` from cwd, cached relative | anchored at the cwd with a notice, cached absolute with `registeredAs` (9) |
| `resolve_entry(…, catalog=…)` (`discover install --catalog`) → `select_catalogs` | label or location | label, location, `registeredAs`, or the local file a relative selector names from the cwd (9) |
| TUI Discover tab, `discover` listing | display only | unchanged (shows the catalog's raw `source`) |

## 4. What pi does

pi has no discover catalog (ADR-0188's finding still holds at `b223082bb`;
`remote-catalog-provider.ts` is a MODEL catalog). The nearest surface is its package
sources in settings: `package-manager.ts` `parseSource` treats anything without an
`npm:` / `git:` / `http(s):` / `ssh:` prefix as a LOCAL path — `isLocalPath` is asked
FIRST, so `./https://local-ext` is local (Codex probe,
`.omc/probes/131-live/codex/pi-probe.out`) — and `getBaseDirForScope` resolves it
against the settings file's own scope directory (the agent dir for user scope,
`<cwd>/.pi` for project scope): the file that holds the source, as here. pi cannot fall
through to a registry at all: a registry package needs an explicit `npm:` prefix.
Aelix's catalog format has no such prefix (ADR-0188 §1, a bare name IS a package), so
the equivalent guarantee is the allowlist: a relative path must be spelled `./`, is
read beside the catalog file and never sent to an index, and every spelling the
installer would read from the cwd is refused. Knowing divergences: pi joins with
Node's `path.resolve`, which collapses `link/..` lexically, where aelix follows the
filesystem (C) — the installer, not aelix, opens the path, and on POSIX it follows the
link (on Windows the OS collapses `link\..` itself, and aelix hands on what it opens);
pi reads a bare relative path (`sub/x`) as local, where aelix refuses it (a bare word
is a package name here, so the `./` spelling is what tells the two apart); and pi
reads `file:x` as a path named `file:x`, where aelix refuses it (pip and uv read it as
the relative path `x` from the cwd).

## 5. Choices the brief left open

- **Refuse, don't rewrite (round 2, main loop).** The first two versions turned
  `file:x`, `name @ file:x` and relative paths into absolute ones. Rewriting a URL lost
  its `#sha256=` (the installer's own hash check) and `#subdirectory=` fragments, and
  every shape the rewriter did not know (`name @ ./x`, `file:`) still reached the
  installer raw. An allowlist fails closed on an unknown shape instead.
- **The git transports beyond `git+`.** `git://`, `ssh://` and scp-style
  `<user>@host:path` (any user, as on `aab1f210`) are accepted in (B): they are absolute remote URLs that never read
  the cwd, `classify_target` already routes them as git, and refusing them would break
  catalogs that use them. Inside `name @ …` only the PEP 508 URL forms are accepted.
- **Markers are refused on a bare requirement.** (A) is "a name, extras, a version
  specifier"; a marker adds nothing a catalog entry needs. A marker after an accepted
  absolute direct reference is carried through untouched (the URL is what matters).
- **The cwd-collision refusal (5, second half).** Before, a package entry `foo` while
  standing in a directory holding `./foo` installed that directory. Refusing is stricter
  than the brief's decisions; the alternative is threading a forced kind through
  `_cmd_install` → `install_extension` → `verify_and_pin`, which all re-classify.
  *Amended 2026-10-08 by #405 (§16):* that alternative is what #405 did, by the
  target's type rather than a forced kind — a `CatalogSpec` is classified by its
  spelling everywhere — so the refusal is gone and the package is installed.
- **Beside-the-catalog ambiguity (5, first half) — kept.** A hand-written `"source":
  "local-ext"` meant as the neighbouring directory is the issue's literal
  dependency-confusion shape; a bare name that is ALSO a neighbouring file is refused
  rather than read either way.
- **Bare archive names are relative paths — kept.** Needed for catalogs an older
  `--relative` wrote (bare `name-1.0-py3-none-any.whl`); a PyPI project named `x.zip`
  would now need a different catalog spelling. pip and uv read such a name as a file
  too (`.omc/probes/131-live/verify/out-archive-name.txt`). One test row per suffix
  pins the list (round 2: a three-suffix list passed every row). Round 3 widened the
  name to what the old generator could write (any file name, spaces included) and the
  suffixes to pip's whole archive list, so `x.tar.lz` in an https catalog is refused
  rather than handed to uv, which opened it from the cwd.
- **Two values, not a re-split (round 3, main loop).** The brief offered refusing a
  catalog path whose literal bracketed sibling exists as ambiguous. The resolver's
  `(path, extras)` travel as a `ResolvedPath` instead: a refusal would still re-read a
  joined string downstream and leave a window between the check and the installer's
  own split. The typed path's re-split follows the backend for the same reason.
- **A `.git`-ending package name is refused, not passed (round 3).** The installer
  turns it into `git+acme.git`, which uv rejects as unparseable; refusing names the
  real cause and keeps (A)'s "to the index" true.
- **`~` is allowed in a remote catalog.** It does not depend on the catalog's location
  or the cwd, only on the installing user, like an absolute path (which remote
  catalogs already carry for shared mounts).
- **Resolution happens at install time, not at `--refresh`.** The cache keeps the
  document as published, so caches written before this change are covered too. Only a
  relative catalog LOCATION is anchored at refresh (9), because that is the moment the
  file is read.
- **A relative registration is anchored, not refused (9).** Refusing would break a
  catalog that `discover --refresh` has just read successfully; anchoring it at the
  directory the refresh read it from records exactly the file that was read, the
  notice tells the user to register the absolute path, and `registeredAs` keeps the
  `--catalog` selector the user knows working.
- **One hand-off for a path, not a list of refusals (round 5, main loop).** Each round
  had found another character a backend re-parses in a path string (`[…]`, then `#`,
  `[]`, a trailing space, a backslash). Refusing them one by one leaves the next; a
  `file://` URI is parsed by both backends as the path it names (measured, §1), so the
  class closes where the string is built. `#` is the one character it cannot carry on
  uv, so a path holding one is refused — a rename is the only spelling that works.
  Typed paths (`aelix extension install ./x`) still hand over the path string — the
  user's own string, read as the backend reads it. `extension update` of a recorded
  path no longer does (round 6, §13): a catalog path is recorded as its URI, and any
  path record — an older version's plain path, a typed install's, `source add`'s — is
  turned into one when `update` reads it.
- **Extras need the project's name.** pip takes extras on a local URL only in PEP 508's
  `name[extras] @ url`; aelix reads the name from the artifact (wheel or sdist file name,
  `pyproject.toml` `[project] name`) rather than from the catalog entry's `name`, which
  is a display name. When it cannot, it refuses rather than guess; the backend checks the
  name against the metadata it builds.
- **A scheme must be lowercase (round 5, main loop).** The simplest safe rule: aelix
  compares a scheme case-insensitively only to REFUSE one that is not already lowercase,
  and passes through only lowercase-scheme URLs exactly as written — no rewriting
  (lowercasing it would re-open "refuse, don't rewrite", §2).
- **The neighbour guard reads the name of an unversioned requirement (round 5).** The
  guard exists because a bare `local-ext` may mean the neighbouring `./local-ext`; a
  version specifier says "package", as (5) already said, so a versioned requirement is
  not checked, and an unversioned one is compared by its NAME — never by the whole
  string (a file literally named `local-ext==1.0` refused `local-ext==1.0`).

## 6. Consequences

- A catalog that relied on being run from its own directory with a `./` source now
  works from anywhere; one that relied on a relative source in an https or git catalog
  now fails loudly where it used to install whatever the cwd held (or, for a bare
  name, look it up in an index).
- A catalog that wrote a relative path without `./` (`wheels/x.whl`), a relative
  `file:` URL, `name @ ./x`, or a `-`-leading source now fails loudly with the accepted
  forms in the message; the fix is a one-character `./` or an absolute URL. #11's
  `-weird-pkg` row now asserts the refusal.
- `--relative` output changes shape (`./x.whl`); old bare-filename catalogs still
  install from a local catalog file.
- Windows: `os.path.relpath` across drives raises; `index --relative` reports it as a
  usage error (exit 2) and writes nothing. An absolute path is `Path.is_absolute()` on
  the installing OS, so `C:\x` is a path on Windows and refused on POSIX; a
  drive-relative `C:x` or root-relative `\x` is refused.
- `discover install --catalog <spec>` matches a relative registration by the spec as
  registered, by its anchored absolute location, or by the catalog's `name`; and a local
  catalog by the file a relative selector names from the cwd.
- A catalog entry `./x.whl[feature]` installs, hashes, stages and pins `x.whl` even when
  a file or symlink literally named `x.whl[feature]` sits beside it; a typed
  `./x.whl[feature]` does the same.
- Since round 5 the installer's argv carries `file:///…` (or `name[extras] @ file:///…`)
  for a catalog path; the consent line above it still names the path. A catalog that
  wrote a URL scheme with a capital letter, kept a pack under a directory whose name
  holds `#`, asked a `setup.py`-only directory for extras, or wrote `name @ https://…/r.git`
  without `git+` now fails loudly with the fix in the message; the last one never
  installed on pip. A resolved name ending in `[…]`, refused in rounds 3 and 4, installs.
  On Windows `Path.as_uri()` gives `file:///C:/…`, which both backends read; not measured
  on a Windows host here.

## 7. Review round 1 (2026-10-07)

Verify and a Codex cross-review of the first commit found, each reproduced on it
(`.omc/probes/131-live/fix2/repro-before-5f7051fa.txt`): relative `file:` URLs handed
on raw (pip/uv then read them from the cwd); `./https://local-ext` taken for a URL and
handed on raw (from an https catalog too); `./link/../real-ext` normalized lexically
before the filesystem saw it (refused, or the wrong copy installed); `./x.whl[feature]`
checked for existence with the extras in the file name (refused); a relative
registration refused with a false "not a local file"; `index --relative` and the
resolver measuring a symlinked catalog from different directories; a cwd-first
resolver passing every row (no row had a competing copy in the cwd); and text claiming
`./acme-notes` "became an index lookup" — the installer read it from the cwd (§1). The
decisions in §2 (then rules (1)-(3), (7)-(9); rules (1)-(2), which rewrote relative
`file:` URLs, were replaced in round 2, §8) and this section's corrections answer them.

## 8. Review round 2 (2026-10-07)

Verify (FAIL, one blocking item) and a second Codex cross-review (three P1s, two of
them regressions of the rewrite approach) of the round-1 revision found, each
reproduced on it first (`.omc/probes/131-live/fix3/repro-before-73ab3c18.txt`, the
in-process CLI with a recording runner and a decoy of every relative source in the
cwd):

- `name @ ./x`, `name@./x`, `name @ x`, `name @ ../x`, `name @ ./x.whl` were taken for
  URL direct references and handed on raw, from a local and an https catalog alike;
  uv installed the cwd's decoy wheel (verify, `out-live-x-head.txt`).
- `file:` and `name @ file:` (an empty path) and `file:#subdirectory=x` were handed on
  raw; uv reads the cwd.
- Rewriting `name @ file:x.whl#sha256=<h>` into an absolute `file:///` URL dropped the
  fragment — the installer's own hash check — and `#subdirectory=` with it.
- `file:x[extra]` was refused (existence checked with the extras).
- `-e local-ext` reached uv as one argv element, read as an editable install of the
  cwd's `local-ext`.
- A symlink target whose name held an OSC 52 sequence reached the Resolved and Install
  lines raw.
- A relative registration refreshed from its directory could no longer be selected
  with `--catalog catalog.json`, and the error said "try … --refresh" right after a
  refresh that listed it.
- `_ARCHIVE_SUFFIXES` cut to `.whl`/`.zip`/`.tar.gz` passed every row.
- `_target_dist_hint` / `_target_source_key` keeping the extras passed every row.
- The CHANGELOG said an absolute or `~` path in an https or git catalog was refused;
  it is accepted.

The main loop replaced the rewrite approach with the allowlist in §2 (refuse, don't
rewrite). `file:x[extra]` is now refused with every other relative `file:` URL rather
than fixed; the accepted spelling is `./x[extra]`. The relative-registration case
caches `registeredAs` (9). Rows: every refused form from a local and an https catalog
(nothing reaches the installer, the message names the entry, the catalog and the
accepted forms); every accepted URL form passed byte-identical; absolute and `~`
paths from local, https and git catalogs; one row per archive suffix plus one pinning
the tuple; the terminal-safe Resolved / Install / refusal lines; the selector; the two
post-install helpers; and the uv measurement itself (skipped when `uv` is not on PATH).

## 9. Review round 3 (2026-10-07)

Verify (FAIL, one blocking item) and a third Codex cross-review (one P1, three P2s) of
the round-2 revision (`751ec582`), each reproduced on it first
(`.omc/probes/131-live/fix4/repro-before-751ec582.txt`, the in-process CLI with a
recording runner and a decoy in the cwd):

- **Codex P1 — the extras collision.** A catalog `./x-1.0.whl[feature]` resolved the
  1.0 wheel, but the installer's `_path_extras` re-split the joined string and, finding
  a sibling LITERALLY named `x-1.0.whl[feature]` (a symlink to a 9.0 wheel in the cwd),
  installed that, without the extras; default verification hashed, staged and pinned
  the 9.0 wheel while the Resolved and Install lines named 1.0 (a directory `tree[extra]`
  likewise). Now the resolver's `(path, extras)` travel as a `ResolvedPath` (§2 (C),
  §5) and the typed re-split follows the backend (8). The staged argv also kept no
  extras in that case; it does now.
- **Codex P2 — `index --relative --out -`** measured from a `<dir>/catalog.json`
  symlink's target (`../../wheels/x.whl`); it measures from the scanned directory (7).
- **Codex P2 — a spaced bare archive name** (`team notes-1.0.tar.gz`, which the old
  generator wrote and uv installs) was refused by round 3's character whitelist; the
  rule is now scan_artifacts' own (§2 (C)).
- **Codex P2 — the first-acquisition line** printed a symlink target's basename raw (an
  OSC 52 sequence) after consent; every verify line is filtered now (6).
- **Codex cat 4 / verify** — no row had an http entry source (an https-only mutant
  passed), a host-form `file://` URL (`startswith("file://")`, `file://localhost`
  without its slash, and a direct reference accepted when its target contains `://`
  all passed — verify reinstalled the cwd's decoy with the first on the real CLI), the
  no-backend message or the anchoring notice raw, or a bare `x.whl[feature]`; `x.tar.lz`,
  `x.tlz` and `x.tar.lzma` were accepted as (A) package names though uv opens them as
  files from the cwd; `acme.git` went to the installer as git; the guide's (B) row named only the scp
  re-prefixing; and `source add --catalog catalog.json` followed by `discover install
  --catalog catalog.json` said "try … --refresh". Each now has a row, a fix or a stated
  rule (§2, (9)).

The allowlist stays as decided in round 2.

## 10. Review round 4 (2026-10-07)

Verify (FAIL, one blocking item) of the round-3 revision (`808ac7ae`); Codex pass 4
stopped on its provider's content filter before running a probe (a review gap, not a
clean result). Reproduced on it first (`.omc/probes/131-live/fix5/repro-before-808ac7ae.txt`,
the in-process CLI):

- **verify B — a regression of round 3's selector message.** "`--catalog '<sel>'`
  matches no registered catalog" was decided from the CACHE, so it fired for registered
  catalogs whenever the cache lagged: after `source add --catalog /abs/catalog.json`
  and before `discover --refresh`, for an https catalog an offline refresh skipped, and
  for a hand-edited relative registration cached before `registeredAs`; `--catalog
  <dir>/link/catalog.json` (the path given to `source add`, stored resolved) got it even
  after a refresh. Now the registered sources decide (`extension_install.
  _catalog_selector_problem`, `extension_catalog.location_matches_selector`,
  `cached_copy`), an absolute selector is compared by the file it names, and a catalog
  whose last refresh failed is named with its error (9).
- **verify NBs.** The scp rewrite is `git+ssh://git@host/path` (§2 (B), the guide).
  `name @ <URL ending in .git>` gives a broken argv (`git+name @ …`): older than this
  ADR; not fixed here, because the same string also keys the git pin identity and the
  recorded source, so a correct fix changes three consumers and needs its own rows —
  stated in (B) and the guide, left to a follow-up issue. (Round 5 corrected this: the
  string was not harmless — pip reads it as a path under the cwd — and fixed it, §11.) `split_path_extras` now
  right-strips the path part as pip 26.2.1's `strip_extras` does (§2 (C)); uv rejects
  the spaced spelling, and aelix hands it the rejoined `<abs>/x.whl[feature]`. Rows for
  the verify lane's surviving mutants: a backslash in a bare archive name, an
  upper-case archive suffix, `ACME.GIT`, the generic hint with no `--catalog`, and the
  six verify / record lines that print an exception or a spec (each fed an OSC 52
  payload).

## 11. Review round 5 (2026-10-07)

Verify (FAIL, one blocking item) and Codex pass 5 (two P1s, a P2, and a surviving mutant)
of the round-4 revision (`3374439f`), each reproduced on it first
(`.omc/probes/131-live/fix6/repro-before-3374439f.txt`, the in-process CLI handing its argv
to real uv 0.11.19 and pip 26.2.1, offline, a decoy where a misreading would land). The
main loop's diagnosis: every remaining P1 was one class — a source resolved correctly,
then handed over as a STRING the backend parses again. Its decisions are applied (§2,
§5):

- **Codex P1 — re-parsed paths.** `./link` to `trusted#release`, `trusted[]` or
  `trusted ` installed the decoy `trusted` (uv; `trusted ` on pip too) while the Resolved
  line named the right directory. A path now reaches the installer as a `file://` URI;
  the three install the named copy on both backends (`repro-after.txt`), a `#` is
  refused (uv cuts it in every spelling, §1).
- **Codex P1 — `local-ext @ FILE:///<abs>`** was accepted after lowercasing the scheme for
  the check and handed on as written; uv installed `<cwd>/FILE:/<abs>`. A scheme not in
  lowercase is refused.
- **verify (blocking) — `name @ <URL ending in .git>`.** §10 called the `git+name @ …`
  argv harmless; pip read it as a path under the cwd and built the cwd's decoy. Never
  re-prefixed now; `name @ git+…` passes unchanged (measured on both backends, §1),
  `name @ https://…/r.git` is refused with the `git+` spelling.
- **Codex — untrue text.** The `file:/abs` refusal claimed the installer would read the
  cwd; each `file:` refusal now says what is true of its spelling (§2, refused 2). The
  not-fetched message advised `discover --refresh` under `AELIX_OFFLINE=1`, which skips
  a network catalog again; it names the offline skip and says how to refresh online.
  `--catalog ./catalog.json` from another directory, for a relative registration whose
  copy was read elsewhere, said "no copy recorded"; it names where the copy came from.
- **Codex P2 — the neighbour guard** compared the whole versioned spec with the file
  names beside the catalog (a file named `local-ext==1.0` refused `local-ext==1.0`); it
  compares the name of an unversioned requirement (§5).
- **Codex cat 4 / verify survivors.** A `_place_path` that turned `\` into `/` passed every
  row (`./team\notes` then followed `team` to the cwd's decoy); a row pins the name. Rows
  now pin verify's surviving mutants of round 4's code: the four terminal-safe values in
  the selector diagnosis (the failed catalog's location and error, the registered
  location, the selector), the `None == None` guards of `location_matches_selector` and
  `cached_copy`, a healthy-plus-failed selection, the built-in default in the
  unfetched-catalog note, a blank selector, and `index '~/wheels' --relative --out -`
  with an unexpanded `~` — round 4 had called that last mutant (W23) equivalent; it
  is not (`.omc/probes/131-live/verify5/w23-v07.txt`).

Rows for the hand-off run the real backends offline (uv on PATH; pip through
`AELIX_TEST_PIP_PYTHON` or an interpreter that has it, else skipped): a wheel, an sdist
and a project directory, with and without extras, under `[]`, `[x]`, `;` and a
trailing space, and the staged copy of default verification; plus the bare-path
measurement that motivates them and the `#` measurement that motivates its refusal.

## 12. Threat model

What #131 guarantees: for a **trusted** catalog, aelix never resolves an entry's
SOURCE against the **current directory**, and never hands the installer a string the
installer would read from there — a package name goes on as a name (whatever the cwd
holds since #405, §16; before, one that also named something in the cwd was refused,
(5)), a URL as an absolute URL, a path as an absolute `file://` URI (a relative one
placed beside the catalog). Since #405 the same holds for an install RECORD that
`update` re-installs and for whatever the resolver hands the Python API: a
`CatalogSpec` is classified by its own spelling, never by asking the cwd, and a
relative path in one (or in a `path` record) is refused (§16). That closes the cwd-substitution
shape the issue names: an entry its author meant benignly installed whatever sat in
the directory `aelix` runs in (a cloned repository, a download folder).

Installer CONFIGURATION the backend discovers by itself (verify round 7, §14; closed
for catalog installs by #392, §15). uv reads a `uv.toml` or a `pyproject.toml`
`[tool.uv]` from its working directory or a parent (and the user's `uv.toml`), and
aelix honours uv's own configuration on purpose (ADR-0200, `_uv_config_files`). Run in
the cwd, a cloned repository's `find-links` or index settings satisfied a trusted
catalog's benign PACKAGE entry from that repository — measured: `local-ext` installed
`CWD-DECOY` 9.9 from `./w` on uv, pip read neither file and installed nothing
(`.omc/probes/131-live/verify7/uvconfig-head.txt`). Since #392 `discover install` and
every `update` run the installer in `<agent dir>/installer-cwd`. uv first discovers a
PROJECT (the nearest `pyproject.toml`, up to its workspace root) and reads configuration
from there, so that directory holds two aelix files: a `pyproject.toml` with no
`[project]` (uv finds no project, and does not go on to one above) and a `uv.toml` that
sets nothing (the first configuration file uv then finds). No project configuration in
the cwd, a parent of it, or above the agent dir is read — measured for every ancestor
shape on uv 0.11.14, 0.11.19 and 0.12.23 (§15, review round 2); the user-level and
system `uv.toml`, `UV_CONFIG_FILE` and `UV_*` still are.

#392's threat model (review round 5, owner decision 2026-10-08): the protection is
against the **current directory** — a cloned repository's project configuration
(`uv.toml`, `[tool.uv]`, a workspace above it). The user's own environment variables
and user- or system-level installer configuration (uv's user and system `uv.toml`,
pip's `pip.conf`) are inside the trust boundary: a repository cannot set them (a
project `.env` is admitted default-deny, ADR-0203), and they keep applying exactly as
uv and pip read them. One variable is checked: for these installs on uv, a set
`UV_CONFIG_FILE` must be a bare absolute path exactly as uv reads it (no spaces
stripped, no `~` expanded, no `file:` URL, not empty), else the install is refused
before anything runs ("Set UV_CONFIG_FILE to an absolute path.") — a relative one
silently reads aelix's own empty `uv.toml` in the installer directory instead of the
org's file.

What it does not guarantee: safety against a catalog whose own content is hostile. A
catalog can already name any package or URL to install, so it needs no trick spelling
to do harm; trusting a catalog is a decision the user makes when registering it.
Adversarial spellings are refused where a simple rule
does it (rounds 1-6) — hardening, outside the guarantee. Known limits the review
rounds found and leave open:

- a `./` path is followed through symlinks wherever they point (the filesystem's
  reading, §2 (C)); an absolute or `~` path is accepted from any catalog;
- `name @ <URL>` and git URLs are fetched as written: `#subdirectory=`, `#egg=`, a git
  ref, a marker and a `%`-escape in a fragment are the catalog's choice;
- a path can be swapped by whoever can write beside the catalog between the check and
  the installer — default verification installs the staged copy it hashed,
  `--no-verify` the path itself;
- a relative catalog LOCATION left in a hand-edited settings file is read from the
  directory `discover --refresh` runs in (anchored there with a notice, (9));
- a typed `aelix extension install <target>` keeps its meaning from the cwd (8), and a
  typed path still reaches the backend as the user's string (on `update` too, when
  the `file://` URI cannot carry it, §14);
- a typed `aelix extension install <spec>` runs its installer in the cwd, where the
  user typed it (a relative path in it means that directory): there uv still reads the
  cwd's and its parents' `uv.toml` / `[tool.uv]`, as ADR-0200 intends, so a cloned
  repository's `find-links` can still answer a package name the user types in it, and
  `python -m pip` still imports a `pip/` package from it first — for a typed install
  and for `extension remove` (issue #394, §15);
- a RELATIVE path in the user's own installer configuration resolves against the
  installer directory for a catalog install and `update` (#392): an environment value
  such as `PIP_FIND_LINKS=./w` or `file:w`, `UV_FIND_LINKS`, `UV_INDEX_URL=./simple`,
  `PIP_TARGET`, `UV_PROJECT` or `UV_WORKING_DIRECTORY`, `PIP_CONFIG_FILE=pip.conf`
  (on uv too: the pip.conf translation opens a relative value in the installer
  directory, where pip would — review round 5b, §15), or a relative `find-links` in
  `pip.conf`. Some fail loudly there (uv: "Project directory `./proj` does not
  exist"); pip drops a missing find-links location with a WARNING and installs from
  the default index. Use absolute paths (only `UV_CONFIG_FILE` is refused, above);
- the installer directory and its two files are checked when aelix prepares them; a
  link swapped in between that check and the installer's start — the consent prompt
  sits between — by someone who can already write the agent dir is followed (§15);
- `_uv_project_config` models one way uv rejects a `pyproject.toml` (a `[project]`
  name that is not a valid package name), not others: when `UV_PROJECT` /
  `UV_WORKING_DIR(ECTORY)` names a project whose file uv rejects for another reason (an
  invalid `requires-python`, say), the model still counts its `[tool.uv]` index, so the
  pip.conf translation is skipped while uv reads no index there and fails loudly ("No
  solution found"; 61f03b67, which ignored `UV_PROJECT`, translated — §15);
- on Windows the URI hand-off (`file:///C:/…`) and the `\` separator rules are not
  measured on a Windows host.

## 13. Review round 6 (2026-10-07) — the final round

Verify (FAIL, two blocking items) and Codex pass 6 (two P1s, three untrue texts, a
surviving mutant) of the round-5 revision (`1fcd4704`), each reproduced on it first with
the real CLI and real uv 0.11.19 / pip 26.2.1, offline, a decoy where a misreading
lands (`.omc/probes/131-live/fix7/repro-before-1fcd4704.txt`). Owner decision
(2026-10-07): the final round — simple refuse-rules, the threat model written down
(§12), then merge.

- **verify B1 — a round-5 regression.** Round 5 kept only `git@` out of the direct
  reference reading, so `alice@h.example:o/r.git` (typed, or from a catalog) became
  `alice @ h.example:o/r.git` — kind pypi, raw to the backend, which read the part after
  `@` as a path in the cwd — and `source add deploy@git.corp:team/ext.git` was refused.
  Any `<user>@host:path` is git again (`is_scp_git`), as on `aab1f210`, now also
  without a `.git` suffix; a host spelled like a URL scheme (`name@file:x`) stays the
  refused `name @ file:x`.
- **verify B2 — update re-parsed a recorded path.** `discover install` handed the URI,
  but the install record kept `<dir>/trusted[]` / `<dir>/trusted ` and `update` handed
  that string to the backend, which installed the sibling `trusted` (uv both, pip the
  trailing space). A catalog path is recorded as its URI; `update` reads a URI record
  back into a `ResolvedPath` and turns an older plain-path record into one (the whole
  string when it exists, else `[extras]` split off when the rest exists).
- **Codex P1 — `file://LOCALHOST/<abs>`** (also `LocalHost`) was accepted (the check
  lowercased the URL); uv installed `<cwd>/LOCALHOST/<abs>`. The host must be empty or
  `localhost` byte-exact; the message says to write `file:///…`.
- **Codex P1 — `file:///<dir>/trusted%23release`** (bare or after `name @`) was accepted;
  uv decoded `%23` and installed the sibling `trusted`. A `file:` (or `git+file:`) URL
  whose path holds any `%` is refused — one rule for every escape — and the message
  says to name the path directly (aelix builds the URI itself).
- **Codex — untrue texts.** The cwd-collision refusal said the package "would be
  installed from there" for `review-ext[feature]` beside a literal directory of that
  name; pip and uv ignore it — aelix's own `classify_target` would not, and the message
  now says that (the refusal stays: (5); *removed 2026-10-08 by #405, §16* — the
  installer no longer reads a `CatalogSpec` from the cwd). The extras refusal said pip takes extras on a
  local path only as `name[extras] @ file:///…` (both take `<abs>[feature]`); it now
  says aelix hands paths over as URIs and needs the name for that. The guide's
  transcript showed a bare path in the installer argv; it shows the URI. verify's
  "(a URL needs a host)" was said of `ftp://h/x`, `name @ ssh://h/x` and `git+file:x`;
  each now says what is wrong (an unaccepted scheme, a scheme `name @` may not use, a
  relative `git+file:`, a missing host).
- **Codex cat 4 / verify survivors.** `local_project_name` with `split("-", 1)` for an
  sdist passed every row (it asked for `review[feature]` for `review-ext-1.0.tar.gz`);
  a row pins the dashed name. verify6's X39 (a failed copy read under another file not
  routed to the failed-refresh message) and X46 (a `name @ git+…` reference followed by
  a marker read as a package) are pinned.
- **Not changed:** Codex's legacy-project row (a `[build-system]`-only directory with
  extras, refused since round 5) is the refusal §2 (C) records; its message is now true.

## 14. Verify round 7 (2026-10-07)

The independent verify of the round-6 revision (`7dc0e05d`, rebased on `00667c94`)
found four blocking items; each was reproduced on it first with the real CLI, uv
0.11.19 and pip 26.2.1, offline (`.omc/probes/131-live/fix7b/repro-before-7dc0e05d.txt`).
Owner decision (2026-10-07): finish #131 in this round — exactly these four.

- **A regression in `update` (item 1).** §13's B2 fix turned EVERY plain path record
  into a `ResolvedPath`, so a typed `aelix extension install ./legacy[feature]` on a
  directory with no `[project] name` — accepted, since a typed path reaches the
  backend as the user's string — made `update` raise `CatalogError` from
  `installer_arg` uncaught: a traceback, rc 1, and every later extension skipped. A
  plain record (typed, an older version's, or `source add <path>`) is the user's own
  string: it gets the URI when the URI can carry it and otherwise keeps the hand-off
  it was installed with and round 6 gave it — the absolute path with its extras
  (`_recorded_path_target`). A catalog record (the URI written since round 7) keeps the
  URI hand-off, always. And `_cmd_update` no longer lets one extension's error stop the
  others: it says which one and why, goes on, and exits 2 for it (not run).
- **A false claim (item 2).** §12, the guide's "What this protects against" and the
  CHANGELOG said a trusted catalog's benign entry, a package name included, is never
  satisfied from the directory aelix runs in. On uv a `uv.toml` / `[tool.uv]` in the
  cwd or a parent is read — on purpose, ADR-0200 — and redirected a package entry. The
  claim is narrowed to what #131 does (§12), and that is a stated known limit with a
  follow-up issue.
- **False texts (item 3).** The `git+file:` refusals reused the `file:` reasons ("uv
  reads such a host as a directory under the current directory", "uv reads '%23' as
  '#'"), measured false for `git+file:` (`.omc/probes/131-live/verify7/
  gitfile-measure.txt`): git ignores the host — both backends cloned `<abs>` for
  `git+file://h<abs>` and `git+file://LOCALHOST<abs>` — and for `%23` it was pip, not
  uv, that cut the path and cloned the sibling. The refusals stay; `git+file:` has its
  own words: a host is "an unsupported spelling", a `%`-escape is one "pip cuts at a
  '%23' and clones another repository"; both say to write `git+file:///<absolute path>`.
- **A test gap (item 4).** No row had an scp user holding `.`, `_` or `~`, so dropping
  `.` from `_SCP_RE`'s user class passed. Rows for `first.last@git.corp…:team/ext.git`
  (with and without `.git`) and `a_b~c@…`: a typed install, `source add` and a catalog
  source.

## 15. #392 (2026-10-07) — a catalog install's installer runs in aelix's directory

**Measured before the change** (402a8013, uv 0.11.19, a real `aelix` CLI in a throwaway
venv, offline; `.omc/probes/392-live/impl/repro-head.txt`): a trusted local catalog
lists `probe` → `local-ext`; `discover install probe` from a cloned repository whose
`pyproject.toml` `[tool.uv]` says `find-links = ["./w"]` installed `local-ext` 9.9
(CWD-DECOY) from `./w`, exit 0. Same with a `uv.toml` `find-links`, a `uv.toml`
`[[index]]` (`format = "flat"`), and a `uv.toml` one directory ABOVE the cwd. pip read
none of them. `update` of that record from the same directory did the same
(`forms-head.txt`).

**Owner decision (2026-10-07, option a of the issue).** An install that comes from a
catalog runs its installer child in a neutral directory aelix owns, so project uv
configuration in the cwd and its ancestors is never read; the user-level uv
configuration and `UV_*` stay honoured (ADR-0200's org index pin keeps working).
`_uv_has_own_index_config` decides from the same directory. A typed
`aelix extension install <spec>` keeps running in the cwd (a known limit, §12).

**The directory.** `<agent dir>/installer-cwd` (`~/.aelix/agent/installer-cwd` by
default; `catalog_installer_cwd`), created `0700` on POSIX (an existing one is
`chmod`ed back; Windows has no such mode — `os.chmod` there only toggles the read-only
bit, and the directory inherits the agent dir's ACL), holding two files aelix writes —
comments only, so they set nothing — and replaces atomically (a new file created
`O_EXCL` under a temporary name, renamed over the old one — review round 5) whenever
their text is not exactly aelix's: `pyproject.toml`, with no
`[project]` and no `[tool.uv]`, and `uv.toml`. The directory must be a real directory
and each file a regular file: a link there (a symlink; on Windows a name-surrogate
reparse point — a symlink or a junction; other reparse points, such as cloud-file
placeholders, are followed by `lstat` and are not links) or an entry of another kind,
found there when aelix prepares the directory, is refused — never followed, repaired
or replaced (review round 3, below; a link swapped in later is the limit in §12). Round 1 wrote only the `uv.toml`, on
the measurement below; review round 2 found uv's project discovery past it (see
"Review round 2" at the end of this section, which holds the full matrix). Round 1's
measurement from `<home>/.aelix/agent/installer-cwd/run` (`uv-discovery.txt`), each
config source pointing `find-links` at its own wheel — every ancestor here a bare
directory or one holding only a `uv.toml` / `[tool.uv]`, never a `[project]`:

| present | without aelix's `uv.toml` | with it |
| --- | --- | --- |
| `uv.toml` in an ancestor (`~/.aelix`) | read | not read |
| `[tool.uv]` in an ancestor `pyproject.toml` | — | not read |
| `~/pyproject.toml` `[tool.uv]` | read | not read |
| `~/uv.toml` | read | not read |
| user `$XDG_CONFIG_HOME/uv/uv.toml` | read | read |
| system `$XDG_CONFIG_DIRS/uv/uv.toml` | — | read |
| `UV_CONFIG_FILE` | — | read |

So the system temp dir was not the only wrong place: with no aelix files, `~/uv.toml`
and `~/pyproject.toml` — and, for an agent dir moved under `/tmp`, a world-writable
ancestor — would still be read. With both files none is, whatever the ancestors hold:
a `uv.toml` or `pyproject.toml` in the home directory is PROJECT configuration to uv
and no longer applies to a catalog install (an org pin belongs in the user-level file,
the system file, `UV_*` or `UV_CONFIG_FILE`). The directory cannot be prepared → the
install is refused (exit 2) — never a fallback to the cwd.

**What uv reads, and `_uv_has_own_index_config`.** `_uv_config_files` listed every
ancestor file (ADR-0200 §9 item 3), so from the installer directory a
`~/pyproject.toml` `[tool.uv]` index — which that uv never reads — would have switched
the pip.conf translation off, and the catalog name would have gone to uv's default
index instead of the org's mirror. It now returns what uv reads, asked from the
directory the uv child runs in (`cwd=`, threaded through `uv_ambient_index_env` →
`uv_ambient_index_config` → `_uv_has_own_index_config`; for a typed install still the
process cwd): `UV_CONFIG_FILE` alone if set (a relative value joined to the child's
cwd, where uv opens it — review round 3); nothing under `UV_NO_CONFIG`; else the
project file (`_uv_project_config`), the user file (`_uv_user_config_file`) and the
system file when it exists (`_uv_system_config_file`). The project file follows uv's
own code (`crates/uv/src/lib.rs` settings load, `Workspace::discover`,
`FilesystemOptions::find`; identical in 0.11.14 and 0.12.23 apart from logging):
`_uv_settings_root` finds the nearest `pyproject.toml` — an explicit
`[tool.uv.workspace]` root starts the search there; a `[project]` starts it at the
first ancestor `pyproject.toml` when that is a workspace whose `members` include it
and `exclude` does not, else at the project (the first `pyproject.toml` above ends
that search whatever it holds — measured, review round 3); no `[project]`, `managed =
false`, a file that is not TOML or a `[project]` whose `name` is not a valid package
name (uv's `PackageName` rejects it; review round 3) starts it at the cwd — and from
there the first `uv.toml` (any) or
`[tool.uv]` pyproject wins (a `uv.toml` beats the pyproject beside it; a pyproject
without `[tool.uv]` or one uv cannot parse is passed over). Round 1's nearest-file walk
started at the cwd and named `proj/sub/uv.toml` where uv read `proj/pyproject.toml`
(`.omc/probes/392-live/verify/uv-subdir.txt`). The user file is uv's `etcetera` base
strategy: `%APPDATA%\uv\uv.toml` on Windows, else `$XDG_CONFIG_HOME/uv/uv.toml` (an
absolute value only) or `~/.config/uv/uv.toml` — macOS included. The system file is
the first existing `$XDG_CONFIG_DIRS/uv/uv.toml` (default `/etc/xdg`), else
`/etc/uv/uv.toml`; `%SYSTEMDRIVE%\ProgramData\uv\uv.toml` on Windows. An empty
`XDG_CONFIG_DIRS` entry is skipped, as uv 0.12 (CI's pin) does; uv 0.11 stopped at the
first empty entry (measured: `none::sys` — 0.12.23 read `sys`'s pin, 0.11.19 read no
system file, `.omc/probes/392-live/r3/xdg-config-dirs-empty-entry.txt`).

**Which installs.** Every install of a target whose ORIGIN is a catalog or a record —
the installer cwd follows the target's type, not a caller flag (review round 4): what
`extension_catalog.resolve_entry_target` returns (a `ResolvedPath`, or a `CatalogSpec`
— a `str` subclass — for a name or URL) runs there whoever calls `install_extension`;
so does `discover install` (every entry form; `_cmd_install(..., resolved_target=)`
hands the resolver's object on, not the argv string), and EVERY `update`
(`_upgrade_and_report` wraps each record as a `CatalogSpec` unless it is already a
`ResolvedPath`), including `update <name>` for a name never recorded. Install
records do not say whether a catalog chose their source (`ExtensionSourceObject` is
`spec`/`kind`/`name`), and none was chosen in the directory `update` happens to run in;
every recorded spec reaches the installer absolute (a path record as its `file://` URI
or absolute path, §14 — a relative one is refused since #405, §16; a git URL — a
relative `git+file:./x` installs on neither backend, measured, `relative-git.txt`; a
package name — classified by its spelling since #405, never against the cwd), so the
typed-install reason does not apply to `update`. Adding an origin field was rejected: records written before it would
stay exposed.

**pip.** pip reads no project configuration, but `python -m pip` puts its working
directory first on `sys.path`: measured (`pipshadow.txt`), a cwd holding
`pip/__main__.py` ran that file as the installer (rc 0, nothing installed) for a
catalog install on 402a8013 and for a typed install still. The pip backend therefore
runs in the same directory — every child of the call, the install and the verify gate's
`pip download` alike — and the catalog install now installs the catalog's directory.

**What still works.** Every source reaching the installer was already absolute after
#131 (§2, §11), so the cwd change breaks no catalog form. Measured on both backends from
the hostile cwd (`forms-fixed.txt`): a package name (with an org pin in the USER-level
`uv.toml` for uv, `PIP_FIND_LINKS` for pip — ORG-PIN 1.0 installed, and kept by
`update`), `./local-ext`, `./local-ext[feature]` (the extra's dependency installed), a
`./….whl`, `name @ git+file:///…`, and an `http://127.0.0.1:23110/….whl` URL. The one
value a user TYPES that may be relative, `--index-url` on `discover install`, is anchored
at the cwd before the installer sees it (`_anchor_typed_index_url`): uv reads
`--index-url ./simple` against its own cwd (measured), pip refuses a relative one
(`ValueError: Can't mix absolute and relative paths`). A value with a URL scheme
(RFC 3986 `scheme ":"`, two characters or more — `file:/abs`, `file:///abs`,
`file://localhost/abs`, `https://…`) is a URL and is never anchored; `C:\x` is a
Windows drive path, not a one-letter scheme (review round 3). The consent block names
the directory the installer runs in.

**What pi does.** pi runs `npm install <spec> --prefix <installRoot>` with no `cwd`
(`packages/coding-agent/src/core/package-manager.ts`, `getNpmInstallArgs`,
`installNpm`, snapshot 27c7b6ff4); `installRoot` is `<agentDir>/npm` for a user-scope
install and `<cwd>/.pi/npm` for a project-scope one (which needs project trust). npm
takes its project `.npmrc` from the `--prefix` root, not the cwd — measured with npm
11.19.0: `npm config get registry` in a directory whose `.npmrc` names a decoy
registry printed the decoy, and with `--prefix <a root holding another .npmrc>` that
root's, and with `--prefix <an empty dir>` the default
(`.omc/probes/392-live/impl/npm-prefix.txt`). So a project `.npmrc` cannot redirect a
pi user-scope package install; aelix's installer directory is the same move.

**Sweep — every site that reaches "where does the installer child run, and whose uv
config counts"** (as of review round 5; every installer child goes through
`install_extension`'s `run` or `verify_and_pin`'s download — the runner, default or
injected through `run_extension_command[_async](..., runner=)`,
`install_extension(runner=)` or `verify_and_pin(runner=)`, called
`runner(argv, cwd=<installer-cwd>)` for the rows marked "installer directory"; a runner
that takes no `cwd` keyword is refused there, exit 2 / `VerifyRefusal`, before anything
runs). The public and semi-public entry points (`__all__`) and their cwd:
`install_extension(target)` — installer directory when `target` is a `ResolvedPath` or
`CatalogSpec`, the caller's cwd for a plain `str` (round 3's `neutral_cwd=False`
default ran a resolver's `ResolvedPath` in the caller's cwd — Codex); `verify_and_pin`
— the same rule for its `pip download`; `run_extension_command` /
`run_extension_command_async` — `discover install` and `update` in the installer
directory, `install` and `remove` in the cwd, every other verb starts no installer;
`build_pip_args` / `build_download_args` / `display_argv` / `classify_*` /
`resolve_install_backend` (no child: `find_spec("pip")` and `shutil.which("uv")`) — no
child; `uv_ambient_index_config` / `uv_ambient_index_env` (`cwd=`, default the process
cwd) — a model, no child; `read_pip_index_config` — reads pip's files, no child. What
the catalog module hands out (review round 5, Codex r4: `resolve_entry_source` returned
a plain `str`, so `install_extension(resolve_entry_source(entry)[0])` ran in the
caller's cwd and installed the repository's CWD-DECOY):
`extension_catalog.resolve_entry_target` — a `CatalogSpec` or a `ResolvedPath`, both
run in the installer directory; `resolve_entry_source` — `(CatalogSpec, is_path)` for
every form (a path as its absolute string), so its spec runs there too;
`resolved_path_from_installer_arg` (an install record's string) — a `ResolvedPath` or
`None`; `resolve_entry` / `search_entries` / `select_catalogs` return
`CatalogEntry` objects, not sources; `split_path_extras`, `source_looks_like_path`
and `anchor_catalog_location` return plain strings or a flag and start nothing. A
`CatalogEntry.source` read raw is the catalog's unresolved text — a plain `str`, which
`install_extension` treats as typed (the #131 contract: resolve it first).

1. `discover install` → `_cmd_install` → `install_extension` → the runner (CLI
   `aelix extension discover install` and the async/sync API alike):
   cwd → installer directory.
2. the verify gate's `pip download` (`verify_and_pin`, pip, `--verify-pypi` / `--strict`
   / `--require-signature` on a package): cwd → installer directory (same `run`).
3. `update` of a git / path / pypi record (`_upgrade_source` → `_upgrade_and_report`):
   cwd → installer directory.
4. `update <unrecorded name>` (`_upgrade_pypi_name`): cwd → installer directory.
5. `uv_ambient_index_env` → `_uv_has_own_index_config` → `_uv_config_files`: every
   ancestor of the process cwd → the file uv reads from the child's cwd (project-root
   discovery first, round 2), the user file per platform and the system file.
6. typed `extension install`: cwd, unchanged (§12 limit); the translation decision is
   now uv's own rule there too.
7. `extension remove` (`_cmd_remove` → `uninstall_args` → `runner(argv)`): cwd,
   unchanged — `uv pip uninstall` resolves
   nothing from an index; on pip, `python -m pip uninstall` imports a cwd `pip/` first
   (the typed class; issue #394).
8. git catalog fetch (`extension_catalog._git_clone_bytes`): cwd, unaffected — git
   clone did not read the cwd repository's `.git/config` (`url.….insteadOf` mapping a
   decoy: cloned the real repository; the same mapping via `-c` cloned the decoy; git
   2.54.0, `gitcfg.txt`).
9. aelix's own `classify_target` existence test in `discover install` (a package spec
   naming a cwd file is refused, §2 (5)): unchanged — a refusal, not a redirect. *Not
   true of `update` or the Python API (#405): there the same test turned a recorded
   package name into the cwd entry's path. Since #405 (§16) no `CatalogSpec` reaches
   that test, and this refusal is gone.*

**Known limits.**

- a typed `extension install` still runs in the cwd: uv reads that directory's project
  configuration there, and on pip a cwd `pip/` package runs as the installer — for a
  typed install and for `extension remove` (issue #394; §12);
- the user's own installer environment and user/system configuration are not policed
  (review round 5, owner decision): they reach uv and pip exactly as set, and a
  RELATIVE path among them resolves against the installer directory for these
  installs (the limit in §12) — set it absolute. The one exception is
  `UV_CONFIG_FILE`, which must be a bare absolute path. What a relative value does
  there, measured: uv fails loudly for `UV_PROJECT` / `UV_WORKING_DIRECTORY`
  ("Project directory `./proj` does not exist", "No such file or directory"),
  `UV_FIND_LINKS` ("Failed to read `--find-links` directory") and the
  constraint/override files ("File not found"); uv skips a missing local index
  (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_INDEX`, `UV_EXTRA_INDEX_URL`) and pip a
  missing find-links or index location (a WARNING), both then using the default
  index; a relative `PIP_TARGET` / `PIP_PREFIX` / `PIP_ROOT` installs under the
  installer directory; `PIP_CONFIG_FILE=pip.conf` names no file there — for pip, and
  on uv for aelix's pip.conf translation since review round 5b (rounds 3 and 4,
  `before-env-*.txt`; `r5b/pipconf-*.txt`). `UV_CACHE_DIR` / `PIP_CACHE_DIR` / `PIP_LOG` / `PIP_REPORT`
  only move a cache or a log; `UV_PYTHON` loses to the explicit `--python` aelix
  passes (`r4/pippy.txt`). An ABSOLUTE `UV_PROJECT` / `UV_WORKING_DIR` is the user's
  own choice: uv reads that project's configuration from the installer directory
  too, and the model follows it (round 4);
- `_uv_project_config` models one way uv's `pyproject.toml` schema rejects a file (a
  `[project]` whose `name` is not a valid package name); others (an invalid
  `requires-python`, say) are not modeled — for a typed install they change only the
  translation decision; for a catalog install they matter only when `UV_PROJECT` /
  `UV_WORKING_DIR(ECTORY)` names such a project: the model counts its `[tool.uv]`
  index, uv reads none, the translation is skipped and uv fails loudly ("No solution
  found"; Codex r4 `invalid-schema`, where 61f03b67 translated — §12);
- `_uv_config_files` does not model uv 0.12's `UV_NO_SYSTEM_CONFIG` (uv 0.11 ignores
  it), nor uv's fallback when a discovered workspace's member collection fails (a
  member directory without a `pyproject.toml`: uv then searches from the cwd); neither
  changes what is read from the installer directory, where no project is found;
- the directory's other entries are not policed: uv reads none of them (`--python` is
  explicit, so no `.venv` / `.python-version` there either), but pip imports a `pip/`
  package from its cwd — only the user (or an agent dir placed inside someone else's
  checkout, which already hands that checkout aelix's settings and extensions) can
  write there;
- `0700` is POSIX-only; on Windows the directory has the agent dir's ACL. On POSIX the
  directory is opened `O_NOFOLLOW` and each file step runs relative to that handle (a
  file is read `O_NOFOLLOW` and must be the one `lstat` saw); on Windows (no
  `O_NOFOLLOW`, no `dir_fd`) the checks are `lstat`s before use. On both, a new file
  is created `O_EXCL` under a temporary name and renamed over the old one (a rename
  replaces a link there, never writes through it), and the child is started by path,
  so a link swapped in between the check and the installer's start — the consent
  prompt sits between — by someone who can already write the agent dir is followed
  (Codex r4 `consent_swap.py`: rc 0, the cwd's decoy) — a documented limit (§12);
- another aelix preparing the directory at the same moment is not a refusal (review
  round 5): each write is atomic and a write that fails is followed by another look,
  so the other process's identical file is success. On Windows, replacing a file
  another process holds open fails; aelix tries ten times (about a quarter of a
  second) before refusing. A temporary file left by a killed process
  (`.uv.toml.<pid>-<hex>.tmp`) stays in the directory; uv reads none of them;
- the system-file model follows uv 0.12: with uv 0.11 and an empty `XDG_CONFIG_DIRS`
  entry before the system pin, the model counts a file uv 0.11 does not read (the
  translation is then off while uv 0.11 reads no pin);
- the Windows legs are not measured on a Windows host. The real-uv rows run there
  under CI's pinned uv (0.12.23 since issue #393; 0.11.14 before it): they
  write the user pin to both `$XDG_CONFIG_HOME/uv` and `%APPDATA%\uv` (sandbox_home
  points `APPDATA` into the sandbox) and skip the system-file row, whose Windows path
  (`%SYSTEMDRIVE%\ProgramData\uv\uv.toml`) is machine-wide.

**Review round 2 (2026-10-08): uv discovers a project first.** The independent verify
of round 1 (`.omc/probes/392-live/verify/`) measured the `uv.toml` sentinel passed over:
with the agent dir under a directory whose `pyproject.toml` has `[project]` +
`[tool.uv]`, `discover install` installed that file's `find-links` wheel (9.7) over the
user pin; the same for `~/pyproject.toml` `[project]` + `[tool.uv]`, for
`~/pyproject.toml` `[project]` beside `~/uv.toml`, and for a RELATIVE
`AELIX_CODING_AGENT_DIR` inside a cloned repository with `[project]` + `[tool.uv]`
(CWD-DECOY). Reproduced on round 1 (b9314a0a) with the real CLI on all three uv
versions before the fix (`.omc/probes/392-live/r2/defect-r1.txt`,
`defect-other-uv.txt`: `[project]` + `[tool.uv]` above, `[project]` + `uv.toml`
above, a virtual workspace root above, a `[project]` in the agent dir itself, and the
relative agent dir under a `[project]` repository → the hostile wheel, for
`discover install` and `update`).

Cause, in uv's source (`crates/uv/src/lib.rs`, 0.11.14 and 0.12.23): for `uv pip`,
settings come from `FilesystemOptions::find(workspace.install_path())` when
`Workspace::discover(cwd)` succeeds — the nearest `pyproject.toml` with a `[project]`
(or an explicit workspace root) — and from `FilesystemOptions::find(cwd)` only when it
fails. A pyproject with neither a `[project]` nor `[tool.uv.workspace]` fails it at
once (`MissingProject`), without looking further up.

Mechanisms considered, by measurement — uv run as `uv pip install --dry-run --offline
local-ext` from `<home>/.aelix/agent/installer-cwd` prepared per candidate, the hostile
ancestor offering 9.1, the org pin 1.0 in the USER `uv.toml` (and, separately, in the
SYSTEM `uv.toml` — identical results); PIN = only user/system config read, HOSTILE = the
ancestor read, none = nothing read (`.omc/probes/392-live/r2/matrix_probe.py`,
`matrix-0.11.14.txt`, `matrix-0.11.19.txt`, `matrix-0.12.23.txt` — byte-identical
results on all three versions, no `warning:` line in any run):

| ancestor shape (in `~`, the agent dir at `~/.aelix/agent`) | bare dir | R1 `uv.toml` (round 1) | S2 `pyproject.toml` only | S1 `uv.toml` + `pyproject.toml` (chosen) | S3 `--no-config` |
| --- | --- | --- | --- | --- | --- |
| a. nothing (control) | PIN | PIN | PIN | PIN | none |
| b. `pyproject.toml` `[project]` + `[tool.uv]` | HOSTILE | HOSTILE | HOSTILE | PIN | none |
| c. `pyproject.toml` `[tool.uv]` only | HOSTILE | PIN | HOSTILE | PIN | none |
| d. `uv.toml` | HOSTILE | PIN | HOSTILE | PIN | none |
| e. `pyproject.toml` `[project]` + `uv.toml` | HOSTILE | HOSTILE | HOSTILE | PIN | none |
| f. workspace root `[project]`, `members` globs matching the agent dir path (`.aelix/*`, `.aelix/agent/*`), `[tool.uv]` | HOSTILE | PIN¹ | HOSTILE | PIN | none |
| g. virtual workspace root (`members = ["*"]`, no `[project]`) + `[tool.uv]` | HOSTILE | HOSTILE | HOSTILE | PIN | none |
| h. `~/.aelix/agent/pyproject.toml` `[project]` + `[tool.uv]` (the agent dir itself) | HOSTILE | HOSTILE | HOSTILE | PIN | none |
| i. `[project]` + `[tool.uv] managed = false` | HOSTILE | PIN | HOSTILE | PIN | none |
| j. unparsable `pyproject.toml` + `uv.toml` | HOSTILE | PIN | HOSTILE | PIN | none |

¹ uv's member collection failed on a matched directory without a `pyproject.toml` and
fell back to searching from the cwd — luck, not a property of R1.

Chosen: S1. It is the only candidate that reads no ancestor in any shape and keeps the
user and system pins. `--no-config` drops the user and system files with the project
ones (and `--config-file` takes one file, which uv then reads ALONE — it cannot carry
both the user and the system file); `--project <dir>` only names the directory
discovery starts from, which is already the installer directory. Through the real CLI
(`defect-r2.txt`, `defect-other-uv.txt`, uv 0.11.14 / 0.11.19 / 0.12.23): all twelve
round-1 shapes — the seven above the agent dir and five under a relative agent dir —
install ORG-PIN on `discover install` and on `update`.

The matrix is in `tests/cli/test_catalog_install_neutral_cwd_392.py` where CI runs it
with its pinned uv (rows skip when no absolute `uv` is on PATH):
`test_real_uv_no_ancestor_shape_reaches_a_catalog_install` — ten ancestor shapes
(`uv.toml`; `[tool.uv]` only; `[project]` + `[tool.uv]`; `[project]` + `uv.toml`; a
workspace root with `members = ["packages/*"]`; the virtual one; a workspace root
whose `members` globs match only the installer directory (`.aelix/agent/installer-*`,
`agentrel/installer-*` — the row that catches a sentinel declaring a `[project]`); a
`[project]` in the agent dir; `managed = false`; an unparsable
pyproject + `uv.toml`) × two placements (above the agent dir; a relative
`AELIX_CODING_AGENT_DIR` in the cloned repository that is the cwd), each through the
real CLI, `discover install` then `update`, user pin required; plus the system-file pin
(POSIX) and the `UV_CONFIG_FILE` pin past a `[project]` ancestor; and
`test_real_uv_reads_the_project_file_the_model_names` — twelve layouts where uv's
`--dry-run` pick must be the file `_uv_project_config` names (a project subdir, a
`[project]` without `[tool.uv]` under a `[tool.uv]` pyproject, a workspace member, an
excluded member, a non-member, `managed = false` and its managed twin, a virtual root,
a no-project pyproject, an unparsable pyproject above a `uv.toml`, a `[project]` with
no `name`, the installer directory under a `[project]`). The file has 87 rows, green on
uv 0.11.14, 0.11.19 and 0.12.23 (`green-r2-three-uv.txt`); on round 1 (b9314a0a) 41 of
them fail, the same 41 on each of the three (`red-on-r1.txt`) — among them the ten
`[project]` / workspace-root / agent-dir real-CLI rows and the system-pin row; on
402a8013 (before #392) 82 fail and 5 pass (`red-on-parent.txt`: the three typed-install
and user-pin control rows, the `UV_CONFIG_FILE` row — uv reads that file alone from any
directory — and the relative-agent-dir row whose `[project]` sits in the agent dir, which
uv run in the cwd never reaches). Fifteen mutants in a throwaway worktree — round 1's
`uv.toml` alone, the `pyproject.toml` alone, a sentinel that declares a `[project]`, no
rewrite, a write through a link, `--no-config` on the neutral child, round 1's consent
text, and eight model regressions (round 1's walk, no workspace join, no `exclude`, no
`managed = false`, a relative `XDG_CONFIG_HOME`, no system file, no `UV_NO_CONFIG`, an
unparsable pyproject taken as the root) — each turn at least one row red
(`sabotage.txt`).

**Review round 3 (2026-10-08).** The independent verify of round 2 failed on one
blocking item; Codex's round-2 cross-review reported five. Each was reproduced on the
round-2 code before the fix (`.omc/probes/392-live/r3/`), and every real-uv measurement
below ran on uv 0.12.23 (CI's pin since issue #393) and 0.11.19 with identical results
(0.11.14's earlier rows stay as history).

1. *A relative `UV_CONFIG_FILE`.* uv opens it in its own working directory — in the
   installer directory, `UV_CONFIG_FILE=uv.toml` (or `./uv.toml`, `pyproject.toml`) is
   aelix's empty file, and uv said nothing. Any `UV_CONFIG_FILE` value turns the pip.conf
   translation off (`_UV_INDEX_ENV`, ADR-0200 §5), so neither the uv pin nor the pip
   pin applied; round 2's text said "uv fails on the missing file". The sweep of the
   other path-valued variables, real CLI, a trusted catalog naming `local-ext`, the
   user's cwd holding the org's files (`before-env-uv-0.12.23.txt`,
   `before-env-uv-0.11.19.txt`, `before-env-pip.txt`), each for `discover install` and
   `update`:

   | variable (relative) | backend | before (round 2) |
   | --- | --- | --- |
   | `UV_CONFIG_FILE=uv.toml` / `./uv.toml` / `pyproject.toml` | uv | silent: default index ("not found in the cache"), rc 1 |
   | `UV_CONFIG_FILE=pinned.toml` (absent there) | uv | loud: "failed to open file" |
   | `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_INDEX` (`./x`, `org=./x`), `UV_EXTRA_INDEX_URL` | uv | silent: the missing local index skipped, default index |
   | `UV_FIND_LINKS=./w` | uv | loud: "Failed to read `--find-links` directory" |
   | `UV_CONSTRAINT`, `UV_OVERRIDE`, `UV_BUILD_CONSTRAINT` | uv | loud: "File not found" |
   | `PIP_CONFIG_FILE=pip.conf` | uv | read by aelix in the USER's cwd and translated — the cwd's `pip.conf` chose the index (rc 0) |
   | `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL` (`./simple`) | uv | silent: not a URL, dropped by the translation |
   | `PIP_FIND_LINKS`, `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL` | pip | a WARNING ("Location './w' is ignored"), then the default index |
   | `PIP_CONFIG_FILE=pip.conf` | pip | silent: no file there, nothing read |
   | `PIP_CONSTRAINT`, `PIP_REQUIREMENT` | pip | loud: the installer fails |

   Decision (main loop; withdrawn in review round 5 except for `UV_CONFIG_FILE`,
   below): for a catalog install and `update`, a relative value in any of
   these — plus `SSL_CERT_FILE` / `SSL_CLIENT_CERT` (uv) and `PIP_CERT` /
   `PIP_CLIENT_CERT` (pip), which choose whom the installer trusts — is REFUSED before
   anything runs (exit 2; "Error: UV_CONFIG_FILE holds a relative path ('uv.toml') —
   refusing to install 'local-ext'. … Set UV_CONFIG_FILE to an absolute path (or a
   URL)."), loud ones included, so the rule is one rule (`_INSTALLER_PATH_ENV`,
   `_relative_installer_env`; lists split as uv/pip split them — `UV_FIND_LINKS` on
   commas, measured, `uv-env-delimiters.txt`; `name=` stripped from a `UV_INDEX` entry;
   `PIP_CONFIG_FILE=os.devnull` passes). After: 32 of 48 uv rows and 12 of 48 pip rows
   are refused with that message; on uv the four absolute / URL controls
   (`UV_CONFIG_FILE`, `UV_FIND_LINKS`, `UV_INDEX_URL=file:…`, `PIP_CONFIG_FILE`) install
   the org's wheel (8 rows, rc 0), on pip the absolute `PIP_FIND_LINKS` does (2 rows; the
   probe's own dead `PIP_INDEX_URL` outranks an absolute `PIP_CONFIG_FILE` there, env
   over file); the rest are variables the other backend does not read
   (`after-env-*.txt`; 0.11.19 identical to 0.12.23). A typed install keeps the value — its child runs in the cwd,
   where the value means what it says. `_uv_config_files` now joins a relative
   `UV_CONFIG_FILE` to the CHILD's cwd (round 2 returned the bare name, which this
   process opened in the user's cwd).
2. *A linked installer directory.* `installer-cwd` as a symlink to the user's cwd was
   accepted: the installer ran there (a cwd `pip/` ran) and the cwd's `pyproject.toml`
   / `uv.toml` were overwritten with sentinels (Codex, reproduced:
   `before-codex-items.txt`). Now `catalog_installer_cwd` `lstat`s: a link (or, on
   Windows, a name-surrogate reparse point — a symlink or a junction) or a non-directory there, and a link or non-regular
   file in place of either sentinel, raise `InstallerDirRefused` → exit 2. A sentinel
   link whose target already holds the exact sentinel text is refused too (Codex's
   content-only mutant: the target, rewritten during consent, pointed uv at a decoy).
   After: rc 2, the cwd's `pyproject.toml` unchanged; the sentinel-link probe rc 2
   (`after-codex-items.txt`).
3. *An injected runner.* `run_extension_command_async([... discover install ...],
   runner=…)` and `update` with one handed the runner argv only — its child ran in
   the cwd. `PipRunner` is now a protocol with a `cwd` keyword; `install_extension`
   calls the runner — default or injected — as `runner(argv, cwd=<installer-cwd>)`
   for every child of a catalog install or update (the verify gate's `pip download`
   too), and refuses a runner that takes no `cwd` before anything runs. A typed install
   and `extension remove` still call `runner(argv)`. Every entry point is in the sweep
   above.
4. *The consent line* said "not the current directory" while the installer ran in it.
   It now reads "the installer runs in <dir> (aelix's installer directory)" — the
   directory the child is started in.
5. *`--index-url file:/abs`* (one slash) was anchored under the cwd and the catalog
   install found nothing (rc 1; 402a8013 installed it). Any RFC 3986 scheme is now a
   URL; after: the catalog install installs from that index (rc 0,
   `after-codex-items.txt`), and real-uv rows install the org's wheel through
   `file:/`, `file:///` and `file://localhost/` indexes.
6. *Model rows* for the mutants that survived round 2, each checked against the real
   uv's pick: a `uv.toml` beside a `[tool.uv]` pyproject (bare, and with a `[project]`)
   — `uv.toml` wins (uv warns that it ignores the `[tool.uv]` fields); a project
   inside a non-member project inside a workspace, and a project under a no-`[project]`
   `[tool.uv]` pyproject inside a workspace — the first `pyproject.toml` above ends the
   search, whatever it holds; a `[project]` whose name is not a valid package name — no
   project (the model was wrong here and is fixed; that file's `[tool.uv]` is still
   read when the search reaches it), and a workspace member under such a file; a
   system `uv.toml` index suppresses the translation; an empty `XDG_CONFIG_DIRS` entry
   (0.12's reading, a real-uv row that skips on uv < 0.12). The layout count is
   nineteen (ADR-0200 §12 said ten; round 2 had twelve).

Rows: `tests/cli/test_catalog_install_neutral_cwd_392.py` has 187 (+100): green on uv
0.12.23 (187 passed) and 0.11.19 (186 passed, the XDG row skipped)
(`green-392-two-uv.txt`); on the round-2 code 68 fail on each version
(`red-on-round2.txt`). Twenty mutants in a throwaway worktree — no refusal, comma vs.
whitespace for `UV_FIND_LINKS`, `name=` kept, the `os.devnull` exemption dropped, no
runner check, the bound runner dropping `cwd`, the verify download unbound, round 2's
link replacement, Codex's content-only check, a linked directory accepted, a
non-regular sentinel accepted, round 2's consent text, round 2's `://` test, a
one-letter scheme, round 2's bare `UV_CONFIG_FILE`, an invalid name taken as a
project, `[tool.uv]` before `uv.toml`, the workspace search continuing past a
non-workspace ancestor, the system file dropped, uv 0.11's `XDG_CONFIG_DIRS` reading —
each turn at least one row red; the `dir_fd`-less (Windows) path forced on POSIX stays
green (`sabotage.txt`).

**Review round 4 (2026-10-08).** The independent verify of round 3 passed (four
non-blocking items); Codex's round-3 cross-review found five. Each was reproduced on the
round-3 code first, on uv 0.12.23 and 0.11.19 (`.omc/probes/392-live/r4/`,
`before-head-*.txt`, `before-env-*.txt`).

1. *A path-only variable that looks like a URL* (the list withdrawn in review round 5;
   the `UV_CONFIG_FILE` half stays). `UV_CONFIG_FILE=file:pin.toml` passed
   the relative guard (any RFC 3986 scheme passed), but uv reads the variable as a
   PATH: it opened `file:pin.toml` in the installer directory — a decoy there was
   installed (INSTALLER-CONFIG-DECOY, rc 0, `discover install` and `update`), and
   without one the install failed "failed to open file"; `UV_CONFIG_FILE=file:///abs`
   fails the same way, so round 3's "(or a URL)" advice was false. uv does not expand
   `~` either (`UV_CONFIG_FILE=~/pin.toml` opened `<cwd>/~/pin.toml`, both versions,
   `uv-tilde.txt`); pip's `path` option type does, for `--cert`, `--client-cert` and
   `--src` only (pip 26.2.1's own parser, `pip-env-tilde.txt`). Now the variables read
   only as a file path (`_INSTALLER_PATH_ONLY_ENV`: `UV_CONFIG_FILE`, `UV_PROJECT`,
   `UV_WORKING_DIR`, `UV_WORKING_DIRECTORY`, `SSL_CERT_FILE`, `SSL_CLIENT_CERT`,
   `PIP_CONFIG_FILE`, `PIP_TARGET`, `PIP_PREFIX`, `PIP_ROOT`, `PIP_SRC`, `PIP_CERT`,
   `PIP_CLIENT_CERT`) pass only as an absolute path (`_is_absolute_path`: on Windows
   one with a drive or a UNC share — a rooted `\x` is the current drive's), `~/x` only
   where pip expands it (`_TILDE_EXPANDED_ENV`), and the refusal says "Set <VAR> to an
   absolute path." — "or a URL" only for a variable that takes one. After, real CLI:
   `file:pinned.toml`, `~/pinned.toml`, `file:///abs` and `PIP_CONFIG_FILE=file:pip.conf`
   rc 2 on both uv versions and pip, the absolute control rc 0 (`after-env-*.txt`,
   `after-codex-scripts.txt`).
2. *The Python API.* `install_extension(resolve_entry_target(entry))` ran in the
   caller's cwd: round 3's `neutral_cwd` keyword defaulted to False, so a cwd `uv.toml`
   `find-links` chose a catalog wheel's dependency (CWD-DECOY). Owner decision: the
   installer cwd follows the TARGET'S ORIGIN, not a caller flag. The resolver returns
   a `ResolvedPath` for a path and, new, a `CatalogSpec` (a `str` subclass) for a name
   or URL; `install_extension` runs either in the installer directory and a plain `str`
   in the caller's cwd; `neutral_cwd` is gone (a caller passing it gets a `TypeError`);
   `discover install` hands the resolver's object on; `update` wraps each record as a
   `CatalogSpec`; `verify_and_pin` follows the same rule for its `pip download`. Every
   public entry point and its cwd is in the sweep above. After: the API row installs
   the dependency from the user's org pin (ORG-PIN); Codex's script, which has no pin,
   now finds no `local-ext` (rc 1) instead of the cwd's decoy.
3. *A regression against 61f03b67: `UV_PROJECT`.* uv starts project discovery at
   `UV_PROJECT` (and first moves to `UV_WORKING_DIR`, else `UV_WORKING_DIRECTORY` —
   `crates/uv/src/lib.rs`, both versions), so from the installer directory too it read
   the chosen project's `[tool.uv] index-url`; the model did not, translated a stale
   `PIP_INDEX_URL` over it, and round 3 installed STALE-PIP-PIN (or failed, rc 1, with
   an empty stale index) where 61f03b67 installed UV-PROJECT-ORG (Codex's
   `project_env_pin_regression.py`, both uv versions). The model now does what uv does
   (`_uv_working_dir`, `_uv_project_dir`: joined to the working directory, normalised
   lexically — `x/link/..` is `x`, measured — and a `pyproject.toml` path is its
   directory); a relative `UV_CONFIG_FILE` joins the directory uv WORKS in. Sixteen
   env shapes against the real uv, model and uv agree on every one uv accepts
   (`uv-project-env-after.txt`; before, the model named the installer directory's
   `uv.toml` for every row where uv read the chosen project, `uv-project-env-before.txt`);
   an empty value is refused by both versions ("a value is required"), and a
   `UV_PROJECT` that does not exist is refused by uv 0.12.23 and only warned about by
   0.11.19, which then reads the user pin — relative values are refused by aelix for
   these installs (item 5). After: Codex's script installs UV-PROJECT-ORG on both uv
   versions for the stale and the empty pip index.
4. *Rows for surviving mutants.* A named index with `-` (`UV_INDEX=team-a=file:///…`
   passes; `team-a=./simple` is refused); a hard-linked sentinel is replaced and its
   other name left unchanged (verify M11, `O_TRUNC` in place); a sentinel holding
   aelix's text plus a `find-links` line is replaced (M12); `UV_CONFIG_FILE=~/uv.toml`
   is refused (M17); a `runner(argv, *, cwd=None)` is accepted and given the directory
   (M33).
5. *Text and the rest of the sweep* (the refusals withdrawn in review round 5).
   "on Windows any reparse point" (twice above) said
   more than the code checks — a name-surrogate reparse point (a symlink or a
   junction); corrected. The variables the round-3 sweep left out are refused when
   relative too: `UV_PROJECT`, `UV_WORKING_DIR`, `UV_WORKING_DIRECTORY`, `UV_EXCLUDE`,
   `PIP_BUILD_CONSTRAINT`, `PIP_REQUIREMENTS_FROM_SCRIPT`, `PIP_TARGET`, `PIP_PREFIX`,
   `PIP_ROOT`, `PIP_SRC`. Measured on the round-3 code, real CLI: a relative
   `PIP_TARGET` / `PIP_PREFIX` / `PIP_ROOT` installed into `installer-cwd/tgt` (`pfx`,
   `root`), rc 0, silently; `UV_PROJECT=.` and `UV_WORKING_DIR=..` resolved in the
   installer directory, so the cwd's configuration they meant was not read (rc 1, "not
   found in the cache"); `UV_EXCLUDE`,
   `PIP_BUILD_CONSTRAINT` and `PIP_REQUIREMENTS_FROM_SCRIPT` failed loudly
   (`before-env-*.txt`). After: each rc 2 naming the variable; an absolute
   `UV_PROJECT` / `UV_WORKING_DIR` installs the org's wheel (rc 0); an absolute
   `PIP_TARGET` installs there (rc 0); `PIP_CERT=~/ca.pem` passes (round 3 refused it)
   (`after-env-*.txt`). What stays unpoliced is in the limits above.

Rows: `tests/cli/test_catalog_install_neutral_cwd_392.py` has 284 (+97). On the round-3
code 133 of them fail on each uv version (`red-on-round3.txt`; the 86 relative-env rows
among them include round 3's own rows, whose message no longer says "(or a URL)").
Twenty-three mutants in a throwaway worktree (`sabotage.txt`): twenty-two turn rows
red — the path-only exemption dropped, `~` expanded everywhere or nowhere, a rooted
Windows path taken as absolute, round 3's refusal text, `CatalogSpec` ignored, the
resolver returning a plain `str`, discover passing the argv string, update not
wrapping a record, the verify download unbound, `UV_PROJECT` ignored, the working
directory ignored, `UV_WORKING_DIRECTORY` before `UV_WORKING_DIR`, `UV_PROJECT`
resolved physically, a relative `UV_CONFIG_FILE` joined to the start directory, the
four new pip variables or `UV_PROJECT` / `UV_WORKING_DIR` / `UV_EXCLUDE` not refused,
the named-index regex without `-`, M11, M12 and M33; one is equivalent (a
`UV_PROJECT` naming a `pyproject.toml` file not reduced to its directory — discovery
from that path reaches the same directory first).

**Review round 5 (2026-10-08) — the final round, owner decision "narrow and finish".**
The independent verify of round 4 failed on two blocking items, both regressions
against 61f03b67 that round 3's and round 4's relative-path refusals had made: a named
`UV_DEFAULT_INDEX=corp=<url>` (the form uv's `UV_INDEX_CORP_USERNAME` / `_PASSWORD`
need) was refused as a relative path, and `PIP_FIND_LINKS=~/wheels` was refused though
pip expands `~` there. Codex r4 (filtered mid-run; its probe outputs re-measured here)
added a third (a relative `UV_WORKING_DIRECTORY` that uv ignores beside an absolute
`UV_WORKING_DIR` was refused), a leading-space `UV_CONFIG_FILE` that passed the guard
(it stripped the value; uv does not, and read a decoy at `installer-cwd/' /abs…'`), the
Python API's `resolve_entry_source` returning a plain `str` (its install ran in the
caller's cwd and installed the repository's CWD-DECOY), and an `_ensure_sentinel`
mutant without `O_NOFOLLOW` that no row caught; Claude's cross-review found
concurrent preparation refusing at random (unlink, then `O_EXCL` create; measured
here: 46 and 101 refusals in 120 process starts, first use and an older text) and a
`find-links` relative in pip's configuration silently re-pointed (now a stated limit,
§12). The owner narrowed
the issue to its threat model (§12): the current directory, not the user's own
environment. Each item was reproduced on the round-4 code first, on uv 0.12.23 and
0.11.19 (`.omc/probes/392-live/r5/`, `i1-r4-*`, `i2-r4-*`, `i3-r4-*`, `i4-r4.txt`).

1. *Dropped.* The relative-path refusals of rounds 3 and 4 for every installer
   variable but `UV_CONFIG_FILE` (`_INSTALLER_PATH_ENV`, `_INSTALLER_PATH_ONLY_ENV`,
   `_TILDE_EXPANDED_ENV`, the `UV_INDEX` name strip, `_relative_installer_env`):
   the user's environment and user/system configuration are inside the trust boundary
   — a repository cannot set them — and policing them broke legitimate shapes faster
   than it closed anything a repository controls. A relative path in them now resolves
   against the installer directory (the known limit in §12). After, real CLI: the named
   `UV_DEFAULT_INDEX` installs ORG-CORP-INDEX, `PIP_FIND_LINKS=~/wheels` ORG-VIA-TILDE,
   an absolute `UV_PROJECT` / `UV_WORKING_DIRECTORY` ORG-PROJECT, rc 0 on `discover
   install` and `update`; a relative `UV_PROJECT=./proj` / `UV_WORKING_DIRECTORY=./proj`
   fails loudly in uv (rc 1, "Project directory `./proj` does not exist" / "No such file
   or directory") — on both uv versions (`i1-r5-*.txt`). The model that honours
   `UV_PROJECT` / `UV_WORKING_DIR(ECTORY)` (round 4) stays.
2. *`UV_CONFIG_FILE`, the one rule kept* (`_uv_config_file_refusal`): set, it must be
   a bare absolute path exactly as uv reads it — nothing stripped. After, real CLI, both
   uv versions: `' /abs'` (round 4: CWD-DECOY from the decoy), `'/abs '`, `''`,
   `'   '`, `org.toml`, `~/org.toml` and `file:///abs` are refused (rc 2, "Set
   UV_CONFIG_FILE to an absolute path."); the absolute control installs ORG-PIN
   (`i2-r5-*.txt`). On the pip backend the variable is not read and not checked.
3. *The Python API.* `resolve_entry_source` returns `(CatalogSpec, is_path)` for every
   form (a path as its absolute string). After: `install_extension` given its spec runs
   uv in the installer directory and installs ORG-PIN where round 4 installed
   CWD-DECOY (`i3-*.txt`). The sweep above lists every public resolver and installer
   function and its cwd.
4. *Concurrency.* A sentinel is written to a new file (`O_EXCL`, `O_NOFOLLOW`, a name
   no other process uses) and renamed over the old one (`_write_sentinel`); a failed
   write is followed by another look, and the file holding aelix's text by then is
   success (`_ensure_sentinel`; on Windows, where replacing a file another process
   holds open fails, ten tries). A link or a non-regular entry found while preparing
   is still refused, and a file is read `O_NOFOLLOW` (a link swapped in after the
   `lstat` is refused, `ELOOP`). After: 8 processes at once, 15 rounds each, first use
   and an older text — 120 of 120 succeed both ways (round 4: 74 and 19,
   `i4-*.txt`); a reader polling `uv.toml` while it is rewritten 400 times sees only
   the old text or aelix's, never a missing or partial file.
5. *Text.* "never followed through a link" became what is true — refused when a link
   at preparation time; a link swapped in during the consent window is followed (§12).

Rows: `tests/cli/test_catalog_install_neutral_cwd_392.py` has 279 (round 4's
relative-env rows replaced by 44 `UV_CONFIG_FILE` refusal rows over both source forms,
30 pass-through rows and 17 rule rows; new rows for the resolver, the `verify_and_pin`
seam, the link swap, the junction branch, threads, processes, a reader and the named
default index on real uv). Green on uv 0.12.23 (279 passed) and 0.11.19 (278 passed,
the XDG row skipped); on the round-4 code 94 fail on each version (`red-on-r4-*.txt`; the 17 rule rows
because `_uv_config_file_refusal` is new, the 44 refusal rows on the message text
and the space and empty values round 4 let through).
Mutants (`sabotage.txt`, the 392 file and the #131 file, 737 rows): 21, 17 red —
the read without `O_NOFOLLOW`, `ELOOP` read past, round 4's unlink-then-`O_EXCL`
writer (with and without retries: only the reader row sees it — the re-check makes
the concurrency rows pass, while a reader still finds `uv.toml` missing), an in-place
`O_TRUNC` write, a prefix compare, `resolve_entry_source` returning `str`,
`verify_and_pin` without its anchor, the junction branch dropped (in `_is_link_like`
and in the directory check), `UV_CONFIG_FILE` stripped, unchecked, checked on pip
instead of uv, empty passing, `~` expanded, a trailing space passing, and round 4's
`UV_DEFAULT_INDEX` refusal put back; four equivalent on POSIX — no retry and no
re-check after a failed write (a rename does not fail there when another writer
races; they are the Windows path), no inode compare (`O_NOFOLLOW` already refuses the
swap it guards), a temporary file without `O_EXCL` (its name is unique).

**Review round 5b (2026-10-08) — three items from the round-5 verify, nothing else.**

1. *Windows rows.* `test_the_uv_config_file_rule_reads_the_value_exactly_as_uv_does`
   expected the POSIX literal `/srv/pin.toml` to pass the rule, which on win32 needs a
   drive or a share; its absolute rows now use a native path (`tmp_path`), and
   `test_uv_no_config_and_uv_config_file_are_modeled` no longer leans on
   `ntpath.isabs('/x')` (true only on Python <= 3.12). Every row of the rule tables,
   the 11 CLI refusal values and the absolute control, run with `sys.platform` set to
   `win32` and Windows-shaped paths: 23 of 23 as expected (`r5b/win-sim-r5b-py312.txt`).
2. *Text.* `verify_and_pin`'s docstring said it refused a relative installer variable;
   it refuses only a runner that takes no `cwd`. Its child is always pip, and the one
   variable check (`UV_CONFIG_FILE`, uv backend) runs in the install path.
3. *A relative `PIP_CONFIG_FILE` on uv.* The pip.conf -> uv translation opened it in
   aelix's own cwd — the user's repository — while the uv child ran in the installer
   directory, so a repository's `pip.conf` chose the index a catalog install was
   handed (verify r5: CWD-DECOY on both uv versions; the parent did the same).
   `read_pip_index_config(env, cwd)` now joins a relative value to the child's cwd,
   where pip would open it, as round 3 did for `UV_CONFIG_FILE`; a typed install
   (child in the user's cwd) is unchanged. Real CLI, uv 0.12.23 and 0.11.19
   (`r5b/pipconf-rel-r5b.txt`, `r5b/pipconf-child-r5b.txt`): before, catalog install
   and `update` CWD-DECOY with `UV_INDEX_URL=<decoy>` shown; after, no `UV_INDEX_URL`
   from the repository (no file in the installer directory; uv offline then finds
   nothing, rc 1), and with a `pip.conf` placed in the installer directory its index
   is the one passed (ORG-PIN); the typed install still reads the cwd's file
   (CWD-DECOY, as designed). Two rows, red on 1e51422a (`r5b/red-on-1e51422a.txt`).

## 16. #405 (2026-10-08) — a catalog or record spec is classified by its spelling

**Measured before the change** (8f7d98aa; the defect was already on 61f03b67, where #392
verify r4 found it — `.omc/probes/392-live/r4verify/side-classify-cwd-entry.txt`).
`classify_target` decided "is this a path?" by asking the PROCESS cwd whether the string
existed there — the right reading for a target the user types (§2 (8)), the wrong one
for a source a catalog or a record chose. `discover install` refused that case (§2 (5)),
but `update` (every record is re-installed as a `CatalogSpec` since #392) and the Python
API `install_extension(CatalogSpec)` did not. The real `aelix` CLI in a throwaway venv,
uv 0.11.19 and pip, offline, run from a "cloned repository" holding `local-ext` (a
symlink to its own 9.9 wheel, or a directory) with a pypi record `local-ext` and an org
pin offering 1.0 (`.omc/probes/405-live/impl/live-uv.txt`, `live-pip.txt`): `update` and
`update local-ext` — rc 0, `Upgrade extension from path: local-ext`, `uv pip install
--upgrade <repo>/w/local_ext-9.9-py3-none-any.whl`, CWD-NAMED-ENTRY installed (pip: the
same argv for the directory); a relative `path` record `local-ext` — the cwd's entry as a
`file://` URI; `discover install` — rc 2, the §2 (5) refusal. In process, with a recorder
(`repro-red-8f7d98aa.txt`): `install_extension` given `CatalogSpec("local-ext")`,
`resolve_entry_target(entry)` or `resolve_entry_source(entry)[0]` for an https catalog's
`local-ext`, and `update <a name never recorded>`, all got the cwd entry's absolute path.

**Decision (owner, 2026-10-08, the issue's first direction).** A source that carries a
catalog or record ORIGIN — `CatalogSpec` (what the resolver returns for a name or a URL,
`resolve_entry_source`'s spec, and every record `update` wraps) and `ResolvedPath` — is
classified from its own spelling and never by looking at the cwd: §2's allowlist applied
to records. `classify_target(CatalogSpec)`: spelled as a path
(`extension_catalog.spelled_as_path` — §2 (C)'s path form, i.e. an absolute or `~` path,
`./` / `../`, a bare archive file name, extras aside; anything else that starts like a
path, `.` / `..` / a rooted `\x` / a drive `C:x`; and a string with a separator that is no
URL, `name @ <url>`, git remote or — since review round 2 — PEP 508 requirement without
a URL, `a/b`, `sub\ext` on every platform) → `path`; otherwise git by its URL shape, else a
package spec — a `file:///` URL included, which goes on as the URL it is (§2 (B)). A
`CatalogSpec` spelled as a RELATIVE path has no reading that does not consult the cwd:
`install_extension` refuses it (exit 2) and `verify_and_pin` raises `VerifyRefusal`
before reading anything (`_origin_spec_problem`; absolute = `Path(p).expanduser()
.is_absolute()`, so `~/x` passes and, on Windows, needs a drive or a share; a
`ResolvedPath` is judged as written, `~` not expanded — review round 4). The one
exception (known limits below): a relative URL or direct reference — `file:x`,
`name @ ./x` — is not spelled as a path, so a record or a Python `CatalogSpec` holding
one is not refused; it reaches the installer as written, in the installer directory. A `path`
record that is not absolute — aelix records paths absolute (`_install_spec` resolves,
and so did the commit that introduced records, 5817d1ac, 2026-07-05, for the install
record and `source add` alike), so only a hand-edited settings file holds one — is the
case §12/§15 left to "older records": it names no fixed file, so `_recorded_path_target`
refuses it, never resolving it against the cwd; `update` reports it for that record
("Drop the record (aelix extension source remove -- '<spec>') and install it again by
its absolute path"), goes on with the others and exits 2 (§14's isolation). `discover install`'s
cwd-collision refusal is dead and removed: a package name is installed as the package
beside a same-named cwd entry. A TYPED `aelix extension install <spec>` keeps the cwd
reading (§2 (8)): `local-ext` beside a `./local-ext` is that directory.

**After** (same probes, the fix): `update` and `update local-ext` — rc 0, `Upgrade
extension from pypi: local-ext`, `--upgrade local-ext`, ORG-PIN 1.0 installed on uv and
pip; the relative path record — rc 2, refused, nothing installed; `discover install` —
rc 0, ORG-PIN; the typed `install local-ext` — still the cwd entry (CWD-NAMED-ENTRY on
uv), as designed (`repro-green.txt`).

**Sweep — every site that decides "is this install target a path?" or looks the target
up on disk** (`cli/extension_install.py`, `extension_catalog.py`, `extension_pins.py`):

| Site | Before | After |
| --- | --- | --- |
| `classify_target` | cwd existence test for every `str`, a `CatalogSpec` included | a `CatalogSpec` by spelling (`spelled_as_path`, then — review round 4 — a requirement with a version specifier or a marker is a package, then the git/package shapes); a typed `str` unchanged; a `ResolvedPath` is a path, as before |
| `_path_extras` (via `classify_target`, `_split_path_target`) | `Path(base).exists()` from the cwd | unchanged; reached for a `CatalogSpec` only once it is known to be absolute (no cwd lookup) |
| `install_extension` (CLI `discover install` / `update`, Python API) | classified a `CatalogSpec` from the cwd | refuses a relative-path `CatalogSpec` (2), then classifies by spelling |
| `verify_and_pin` (Python API; the gate inside `install_extension`) | the path branch `Path(path).resolve()` read a relative one from the cwd | `VerifyRefusal` before the pin store or a file is read for a relative-path `CatalogSpec` or `ResolvedPath` (review round 3: whatever `kind` the caller passes) and (review round 2) a caller's `kind` the spelling does not give |
| `build_pip_args` (Python API; takes `kind` from its caller) | `CatalogSpec("local-ext")` as `path` → `pip install <cwd>/…` | review round 2: `ValueError` for the same cases as `verify_and_pin` |
| `_pin_identity`, `_install_spec`, `_target_dist_hint`, `_target_source_key` | followed the cwd-derived kind (a package's pin keyed by the cwd path) | follow the spelling-derived kind; a path branch only sees absolute paths for an origin target |
| `_cmd_update` → `_upgrade_source` → `_recorded_path_target` | a plain path record: `Path(spec).exists()` / `.resolve()` from the cwd | a relative one refused (`CatalogError`, reported per record); an absolute one unchanged (§14); review round 4: a `~` record whose `resolve()` fails (a symlink loop) goes over expanded — it was `ResolvedPath('~/loop')`, whose hand-off failed with a `ValueError` (update reported it per record, exit 2) |
| `_cmd_update` → `_upgrade_pypi_name` (recorded and unrecorded names) → `_upgrade_and_report` | `CatalogSpec(name)` classified from the cwd | by spelling: a name is a package |
| `_cmd_update`'s unrecorded filter TYPED as a path (`update ./local-ext`) | a `CatalogSpec` classified from the cwd: the typed path, absolute | review round 2: the user's typed path, resolved from the cwd as typed (`_install_spec(…, "path")`) and handed over absolute — the 8f7d98aa result; the first round refused it as "from a catalog or an install record"; review round 3: never stripped (round 2 stripped it) |
| `_cmd_source remove` | dropped every argument starting with `-` | review round 2: `--` ends the options, so the printed `source remove -- <spec>` drops a record starting with `-`; it still matches by spec, name OR path (unchanged), which the printed advice now says (review round 3: spec and name; review round 4: the path too, naming it) |
| `CatalogSpec.__new__` (every wrap: the resolver, `resolve_entry_source`, `_upgrade_and_report`, a Python caller) | the string as given | review round 3: a package, URL or git spelling is stripped once; a path spelling is kept exact |
| `_upgrade_and_report` → `_attributed_dists(target, classify_target(target))` | cwd kind | spelling kind |
| `_cmd_discover_install` | `classify_target(target) != "path"` refusal (rc 2) | removed (dead: a `CatalogSpec` never reads as a cwd path); the vanished-`ResolvedPath` check stays |
| `_cmd_install` (`extension install`, typed; `discover install` via `resolved_target=`) | typed: cwd; resolver object: cwd-classified | typed: unchanged (§2 (8)); resolver object: spelling |
| `classify_source` / `_cmd_source add` / `_normalize_catalog_spec` (`source add`, `--catalog`) | cwd existence test | unchanged — a TYPED registration, stored absolute |
| `_source_identity` (record dedupe, `update <filter>` match, a row's label) | resolves a relative path record from the cwd | unchanged — a key and a label, never what is installed; the record is then refused |
| `_cmd_remove` | no target classification (uninstalls a distribution by name) | unchanged |
| `extension_catalog.resolve_entry_target` / `resolve_entry_source` / `_place_path` / `_exists` | relative paths placed beside the LOCAL catalog file only (§2 (C)); no cwd | unchanged, except (review round 2) a `CatalogSpec` is the stripped source the checks read; the resolver's own `_is_path_form` still reads `x==1.0+v.whl` as a bare archive name (review round 3 changed `spelled_as_path` only — a known limit below); review round 4: the refusal of a requirement ending in `.git` ("the installer takes it for a git URL") applies to a bare name only, since the installer now reads `x==1.0+vendor.git` as the package |
| `extension_catalog.resolved_path_from_installer_arg` | a recorded `file:///` URI → `ResolvedPath`; no lookup | unchanged |
| `extension_catalog` `_catalog_base_dir`, `anchor_catalog_location`, `_local_file` | the catalog's LOCATION (§2 (9)), not a source | unchanged |
| `extension_pins.py` (`find_top_level_artifact`'s `is_dir` / `is_file`) | the verify gate's own download directory (a temp dir), never the target string | unchanged |
| `extensions/loader.py` `_is_module_ref` (`-e` / configured paths) | cwd existence test | unchanged — a typed load path, not an install target |

**What pi does** (snapshot 1cedd3272, `packages/coding-agent/src/core/package-manager.ts`):
a source's kind is its spelling alone — `parseSource` reads `npm:` as a package, a git
URL as git, and everything else as a LOCAL path (`isLocalPath` in `utils/paths.ts` is a
prefix test, `npm:` / `git:` / `github:` / `http(s):` / `ssh:` / `builtin:`); nothing is
classified by whether it exists. A recorded package is `npm:<name>` and is updated as
that (`updateConfiguredSources` takes only npm and git sources; a local one is never
re-installed), so pi never resolves a recorded package name against the cwd. A local
path is resolved against the cwd only when TYPED (`install` → `resolvePath(…, this.cwd)`),
then stored relative to the settings scope's directory
(`normalizePackageSourceForSettings`) and read back against that directory
(`getBaseDirForScope`: the agent dir for user scope, `<cwd>/.pi` for a trusted project).
aelix's equivalent: a record is absolute, and one that is not is refused rather than
re-based — an aelix record has no scope directory to re-base it on (`extensionSources`
lives in the one user settings file). Divergence, knowingly: pi has no bare-name package
form, so it never needs the spelling rule for one; aelix's catalog format has (§2 (A)).

**Known limits (still).** A typed `aelix extension install <spec>` keeps the cwd reading
and runs in the cwd (§12). A `CatalogSpec` spelled `name @ ./x` or `file:x` is not a
path by its spelling (a direct reference, a URL): it goes to the installer as written, in the installer directory (#392), where it names nothing of the
cwd — the resolver refuses those forms from a catalog (§2), and a record holds one only
by a hand edit. `_source_identity` still resolves a relative path record from the cwd to
match it (the install itself is refused); `update` labels such a record as written
(review round 2). A path record with a TRAILING space is kept: `<dir>/trusted ` is an
absolute path to a real name an older record holds (§14, verify6), and a path
spelling with a leading space is relative and refused. Every other record, and every
non-path `CatalogSpec`, is stripped where it is wrapped (review round 3 — round 2
refused it, which broke the git record a typed `extension install
'git+https://…/r.git '` writes). The resolver's `_is_path_form` (§2 (C), #131) still
reads a catalog source `x==1.0+v.whl` as a bare archive name, placed beside a local
catalog or refused from an https one; only `spelled_as_path` (records and the API)
asks the requirement first. pip itself reads `probe405==1.0+vendor.whl` as a file name
(`WARNING: Requirement … looks like a filename, but the file does not exist`, exit 1),
in aelix's installer directory — never the cwd — exactly as on 8f7d98aa; uv installs it
(measured, `.omc/probes/405-live/r3/live-after-pip.txt`, `pip-local-version-after.txt`).
`source remove` has no way to drop one record exactly: it matches by spec, name or
path (a path record whose path is the spec read from the directory it runs in), and
the advice `update` prints says so and names that path (review round 4). `[extras]`
alone do not make an archive spelling a package: `x.whl[feature]` is a path spelling,
refused as relative (pip and uv open it as a file, §2 (C) "extras aside"). These are
known limits by the owner's decision (2026-10-08, "narrow and finish"): documented,
not chased.

**Tests and mutants.** `tests/cli/test_record_spec_never_read_from_cwd_405.py` (24
rows; one Windows-only) and two rewritten #131 rows
(`test_a_package_spec_named_like_a_cwd_entry_installs_the_package`,
`test_a_cwd_directory_named_like_a_spec_with_extras_changes_nothing`): 23 red on
8f7d98aa (`red-on-8f7d98aa.txt`; the typed-install control passes there, as it must).
Mutants (`sabotage.txt`, the 405, #131 and #392 files, 741 rows) — 11 of 11 red: the
`CatalogSpec` branch dropped from `classify_target`; no refusal in `install_extension`;
none in `verify_and_pin`; a relative path record resolved again; §2 (5)'s cwd refusal
put back; a `CatalogSpec` never a path; `spelled_as_path` replaced by the typed-target
`source_looks_like_path` (an scp remote `a_b~c@host:team/ext` read as a relative path);
its separator clause dropped; the absolute test without `expanduser`; the absolute test
as an existence test; the refusal only for `./` / `../`.

**Review round 2 (2026-10-08; verify and Codex on 709982a3).** Each item was reproduced
on 709982a3 first (`.omc/probes/405-live/r2/repro-red-709982a3.txt`).

1. *verify, blocking — a caller's kind.* `verify_and_pin(target, kind, …)` takes `kind`
   from its caller: `verify_and_pin(CatalogSpec("local-ext"), "path", …)` — exactly
   what a relative `path` record holds — hashed and staged the cwd's `local-ext` (a
   symlink to its own 9.9 wheel), returned `pip install <staged copy>` and a TOFI pin
   on `<cwd>/w/local_ext-9.9-…whl`; `build_pip_args(CatalogSpec("local-ext"), "path")`
   returned `pip install <cwd>/w/local_ext-9.9-…whl`, and a `ResolvedPath` built from a
   relative string raised `ValueError` out of `install_extension`. Now
   `_origin_spec_problem(target, kind)` derives the kind of a `CatalogSpec` (by
   spelling) or a `ResolvedPath` (`path`) and refuses a caller's kind that disagrees,
   and a relative `ResolvedPath` — `VerifyRefusal` before the pin store or a file is
   read, `ValueError` from `build_pip_args`, exit 2 from `install_extension`. The sweep:
   every function taking a `(target, kind)` pair — `verify_and_pin`, `build_pip_args`
   (public), and `_install_spec`, `_pin_identity`, `_attributed_dists`,
   `_target_dist_hint`, `_target_source_key`, `_record_install` (private, every caller passes
   `classify_target(target)`); `install_extension` derives its own.
2. *verify, blocking — a typed update filter.* Since #392 `update`'s unrecorded filter
   is wrapped as a `CatalogSpec`, so `aelix extension update ./local-ext` typed by the
   user was refused as "it came from a catalog or an install record"; 8f7d98aa
   upgraded `./local-ext` (rc 0). A filter spelled as a path is the user's typed path:
   resolved from the cwd as typed (`_install_spec(…, "path")`, the 8f7d98aa result) and
   handed over absolute, in aelix's installer directory as every update; a filter
   spelled as a name is the package. The guide and CHANGELOG say so.
3. *Codex — whitespace.* `CatalogSpec(" /abs/x.whl")`: `spelled_as_path` and the
   absolute test stripped it, the installer resolved the unstripped string, and
   `<cwd>/" /abs/x.whl"` (a decoy) was installed; a `path` record `" /abs/x.whl"` did the
   same through `update`. Every check now reads the string as given; a `CatalogSpec` with
   leading or trailing whitespace is refused, and the resolver returns the stripped
   source (`CatalogSpec(raw)`), so no catalog entry trips the rule. A path record is not a
   `CatalogSpec`: a leading space makes it relative and it is refused (the message says
   why); a trailing space is kept — `<dir>/trusted ` is a real absolute name an older
   record holds (§14). A git record a typed `extension install 'git+https://…/r.git '`
   wrote keeps the space and is now refused by `update` (measured, `git-trailing-space.txt`).
4. *Codex — the printed advice.* `aelix extension source remove '-local-ext'` failed as
   printed (`source remove` drops arguments starting with `-`). `source remove` now takes
   `--`, and the advice is `aelix extension source remove -- <spec>` quoted for a POSIX
   shell (`shlex.quote`); a row runs the printed command. `update` labels a relative path
   record as written, not as its cwd path.
5. *Codex — a marker holding `/`.* `CatalogSpec('probe405; platform_version == "…/RELEASE_ARM64_T6050"')`
   was refused as a relative path by the separator test (8f7d98aa installed it). A
   string that parses as a PEP 508 requirement with no URL (`packaging`) is a package;
   asked after the path forms (a bare `x.whl` parses as a requirement) and before the
   separator.
6. *Rows for surviving mutants:* `sub\ext` is a path on every platform (Codex: the
   backslash clause could be dropped), and an absolute path ending in `.git` is a path
   (verify M17: git asked first).
7. *Text:* "a much older build wrote a relative record" was unsupported — 5817d1ac,
   which introduced records, already resolved paths absolute; it now says a hand edit.
   The stale #131 refusal text in the ADR index row, §2's rationale bullet and §13's
   Codex note carry the #405 amendment.

Real CLI, throwaway venvs editable to the round-2 tree, uv 0.11.19 and pip, offline,
every proxy at a CONNECT recorder that logged nothing (`live-r2-uv.txt`,
`live-r2-pip.txt`): `update ./local-ext` typed — rc 0, `--upgrade <cwd>/w/local_ext-9.9-…whl`
for the symlink and `<cwd>/local-ext` for the directory, CWD-NAMED-ENTRY installed (pip
cannot build the directory offline — hatchling, as on 8f7d98aa); `update local-ext` typed
and a recorded package — ORG-PIN 1.0; a `-local-ext` record — rc 2, then the printed
`source remove -- -local-ext` rc 0 and no record left; `verify_and_pin(CatalogSpec("local-ext"),
"path")` — `VerifyRefusal`, no pin file; `CatalogSpec(" <abs wheel>")` with the decoy — rc 2,
nothing installed; the marker requirement — rc 0, ORG-PIN; a catalog source
`"  local-ext  "` through `discover install` — rc 0, ORG-PIN. Rows: 30 new, 28 red on
709982a3 (the two controls pass there). Mutants (`sabotage.txt`, the 405, #131 and #392
files): 14 of 15 red; the survivor (`spelled_as_path` strips again) is equivalent — every
entry point refuses a whitespace `CatalogSpec` before classifying it, and `update`
strips the typed filter.

**Review round 3 (2026-10-08; verify and Codex on e55a9fc8).** Each blocking item was
reproduced on e55a9fc8 first (`.omc/probes/405-live/r3/repro-red-e55a9fc8.txt`).

1. *verify — whitespace, a regression.* Round 2's refusal of any surrounding
   whitespace refused a git record aelix's own typed install writes: `aelix extension
   install 'git+file:///<repo> '` records the trailing space; 8f7d98aa upgraded it, and
   e55a9fc8 exited 2 with "Fix the catalog or the record" and no command to run. The
   refusal is replaced by one normalisation: `CatalogSpec.__new__` strips a source
   whose stripped form is not spelled as a path (package, URL, git), so the resolver,
   `resolve_entry_source`, `update`'s wrap and a Python caller all produce the one
   stripped string that every check and the installer read (this also closes round 1's
   "the check strips, the resolution does not" by construction); a path spelling keeps
   its exact string and `_names_no_cwd` judges that string — `" /abs/x.whl"` is relative
   and refused (the message says a leading space makes it relative), `"/abs/x.whl "` is
   that exact absolute name. `spelled_as_path` reads the SHAPE from the stripped string
   (`"x.whl "` is a path spelling, not the requirement `x.whl`). No whitespace refusal
   remains. The CHANGELOG's round-2 claim ("a record … with leading whitespace is
   refused") was false for pypi records anyway (`_upgrade_source` upgrades the name).
2. *Codex and verify — the typed update filter.* e55a9fc8 stripped it: with a
   directory named `" ."` here, `update " ./local-ext"` installed `./local-ext` (9.9)
   where 8f7d98aa installed `" ./local-ext"` (1.0). Never stripped now; resolved by
   `_install_spec(…, "path")`, the function a typed `extension install` uses. Rows kill
   verify's surviving mutants S14 (stripped filter) and S28 (`Path.resolve()` — no `~`
   expansion, no extras split off a symlink).
3. *Codex — a regression.* `CatalogSpec("path-probe==1.0+vendor.whl")`, a valid PEP 508
   requirement whose local version ends in `.whl`, was a bare archive name to
   `_is_path_form`, so a relative path, refused with a false message; 8f7d98aa
   installed it. `spelled_as_path` now asks, after "starts like a path" and before the
   path forms, whether the string parses as a requirement with a version specifier, a
   marker or a URL — then it is a package or URL. A bare token (a name, `[extras]`
   allowed: pip and uv open `x.whl[feature]` as a file, §2 (C) "extras aside") ending
   in an archive suffix stays a path spelling. Rows: `x==1.0+v.whl`, `x==1.0+v.tar.gz`,
   `x[e]`, `x; python_version>"3"`, `x.whl; python_version>"3"` → package; `x.whl`,
   `x.tar.gz`, `pkg-1.0-py3-none-any.whl`, `x.whl[feature]` → path, refused.
4. *Codex — the builder guard.* A `build_pip_args` that ran `_origin_spec_problem` only
   for a `ResolvedPath` or a kind mismatch passed both changed test files and built
   `pip install <cwd>/local-ext` for `build_pip_args(CatalogSpec("./local-ext"),
   "path")`. The code already runs it for every origin target; rows now pin it for both
   public functions (`./local-ext`, `local-ext/`, a bare wheel name, a leading-space
   absolute path, each passed as `path`).
5. *verify — the printed advice.* `source remove` matches a source by spec OR name
   (as before #405), so `aelix extension source remove -- local-ext` for a relative path
   record also drops a package record `local-ext`. `source remove` has no way to target
   one record exactly, so the message says so plainly ("That source remove matches a
   source by its spec OR its name, so it also drops any other source or record whose
   spec or name is 'local-ext' — a package record of that name included; install that
   one again afterwards"); a row runs the printed command and finds both gone.

Real CLI and Python API, throwaway venvs editable to e55a9fc8 and to this tree, uv
0.11.19 and pip, offline (find-links only), every proxy at a CONNECT recorder that
logged nothing (`live-{before,after}-{uv,pip}.txt`): the typed `install 'git+file:///<repo> '`
then `update` — e55a9fc8 rc 2 (the whitespace refusal), now rc 0, `--upgrade
git+file:///<repo>`, GIT-REPO 5.0 (uv; pip cannot build the repository offline — no
hatchling — on either build); typed `update ' ./local-ext'` beside `' ./local-ext'` (1.0)
and `./local-ext` (9.9) — e55a9fc8 installed 9.9, now 1.0 on uv and pip; typed `update
~/pack` — `$HOME/pack` on both; `install_extension(CatalogSpec("probe405==1.0+vendor.whl"))`
— e55a9fc8 rc 2 "is a relative path", now `KIND pypi`, uv rc 0 with 1.0+vendor.whl
installed (pip: the limit above); `update rel-ext` — the advice now carries the by-name
caveat, and the printed command removed 2 sources, as it says; the #405 core (`update`
beside a `local-ext` symlink) — ORG-PIN 1.0 on both builds and backends;
`CatalogSpec(" <abs wheel>")` with a cwd decoy — rc 2 on both, the message now says the
leading space makes it relative.

Rows: 28 new, replacing round 2's 5 whitespace-refusal rows (the 405 file has 77); 17
red on e55a9fc8 (`red-on-e55a9fc8.txt`) — the 11 that pass there are 7 spellings
e55a9fc8 already classified right (controls) and 4 that need a mutant (S28, three
builder-guard rows). Two new rows skip on Windows (a trailing space or `' .'` cannot
be a file name there). Mutants (`sabotage-r3.txt`, the 405, #131 and #392 files): 14 of 14 red —
S14, S28, Codex's builder guard and the same for `verify_and_pin`, `CatalogSpec`
never stripping, stripping a path spelling too, `spelled_as_path` on the exact
string, round 2's refusal put back, the absolute test on a stripped string, no
requirement test, extras counted as qualifying, the requirement test after the path
form, the advice without its caveat, no leading-space note.

**Review round 4 (2026-10-08; verify and Codex on d49f51f0, rebased onto dfb4ddcc —
#404's ADR is 0256).** Each item was reproduced first on the rebased commit, whose code
is d49f51f0's (`.omc/probes/405-live/r4/repro-red-ef0df230.txt`).

1. *Codex — a regression.* `CatalogSpec("probe405==1.0+vendor.git")`, a PEP 508
   requirement whose local version ends in `.git`, was `git` by `classify_target`'s
   `.git`-suffix test, so `build_pip_args(target, "pypi")` and `verify_and_pin(target,
   "pypi", …)` refused it ("is spelled as a git source", false) and
   `install_extension` built `pip install git+probe405==1.0+vendor.git`; 8f7d98aa
   installed it with kind `pypi` (Codex r3, `INSTALLED VERSION 1.0+vendor.git`). For a
   `CatalogSpec`, a requirement with no URL and a version specifier or a marker
   (`extension_catalog.is_qualified_package_requirement`) is now a package, asked after
   the path spelling and before every git shape; a bare `acme.git` keeps the git
   reading, and a typed string keeps today's. The false refusal text no longer occurs
   for it (it now reads "spelled as a package requirement or URL" when the caller passes
   `git`). The resolver refused any requirement ending in `.git` because the installer
   took it for git; that is now true of a bare name only, so a catalog may list
   `x==1.0+vendor.git`.
2. *Codex — raised, not refused.* `ResolvedPath("~/pack")` passed the origin check (it
   expanded `~`) and then raised `ValueError: relative path can't be expressed as a
   file URI` out of `build_pip_args` and `install_extension`; `verify_and_pin` returned
   `~/pack` unpinned. A `ResolvedPath` must now be absolute exactly as written
   (`_absolute_as_written`, no `~` expansion — its `file://` hand-off takes nothing
   else; the resolver expands `~` before it builds one): `install_extension` 2,
   `verify_and_pin` `VerifyRefusal`, `build_pip_args` `ValueError` saying "is a relative
   path (a '~' is not expanded in a path handed over as resolved)". Sweep: the other
   place a `ResolvedPath` is built from a `~` string, `_recorded_path_target`'s
   fallback when `resolve()` fails, failed the same way for a `~/loop` record (update's
   per-record handler printed the `ValueError` and exited 2 - measured); it now hands the expanded path over.
3. *Codex — a surviving mutant.* A `verify_and_pin` that ran the origin check only for
   a `CatalogSpec` or a kind mismatch passed both changed files. Rows now pin
   `ResolvedPath("probe-1.0-py3-none-any.whl")` with kind `path` (a cwd wheel of that
   name) → `VerifyRefusal`, nothing staged or pinned.
4. *verify — the advice understated.* Run where a relative path record `local-ext`
   failed, the printed `source remove -- local-ext` also dropped an ABSOLUTE path record
   `<cwd>/local-ext` (`_remove_source` matches the target's path read from the cwd) —
   verify r3 measured "Removed 3 source(s)". The advice now says it matches by spec,
   name OR path, names the path (`run here: '<cwd>/local-ext'`), and says to run it from
   another directory to keep such a record; a row runs it here (3 removed) and from
   another directory (the absolute record kept).
5. *Known limits written down (owner: not fixed).* The relative-path refusal is now
   stated with its exception (`file:x`, `name @ ./x` in a record or an API source,
   above); the resolver's `x==1.0+v.whl`, pip's filename reading and extras-alone are
   in the known limits.

Rows: 13 new or rewritten (12 new, the advice row rewritten; the 405 file has 89, one
of them Windows-only), 10 red on the rebased d49f51f0 code (`red-on-ef0df230.txt`; two
of them only because the helper is new — the marker row and the bare-name control); the
3 that pass there are controls (the ` /abs` and `pack` `ResolvedPath`s, already
refused) or need a mutant (the cat-4 row). Mutants
(`sabotage-r4.txt`, the 405, #131 and #392 files): 9 of 9 red — no requirement-first
branch, the old `~`-expanding `ResolvedPath` test, Codex's cat-4 `verify_and_pin` guard,
the unexpanded recorded-path fallback, the resolver's `.git` refusal for every
requirement, the advice without its path clause, URL requirements counted as
qualified, the marker dropped from the qualifier (killed only by the helper's own
assertion: a marker spelling never ends in `.git`, so its classification is equivalent),
and a `build_pip_args` guard for `CatalogSpec` only.
