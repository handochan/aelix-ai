# 0255. A catalog entry's source is placed by its catalog, not by the working directory

Status: Accepted (2026-10-06; revised 2026-10-07 after review rounds 1, 2, 3, 4, 5 and 6 and verify round 7, §7, §8, §9, §10, §11, §13, §14; threat model §12)
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
(the `--relative` rows), `tests/cli/test_extension_discover.py` (the `-`-leading row).

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
  no backend can fetch), so "to the index" would be false for it.
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
   an environment marker, a package name ending in `.git`, a URL scheme not written in
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
| `classify_target` (`extension install`, `update`, `_normalize_catalog_spec`, `install_extension`, `_attributed_dists`) | cwd-relative for typed targets | unchanged for typed targets except (8); a catalog source no longer reaches it raw |
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
installer would read from there — a package name goes on as a name (one that also
names something in the cwd is refused, (5)), a URL as an absolute URL, a path as an
absolute `file://` URI (a relative one placed beside the catalog). That closes the cwd-substitution
shape the issue names: an entry its author meant benignly installed whatever sat in
the directory `aelix` runs in (a cloned repository, a download folder).

What it does not cover (verify round 7, §14): installer CONFIGURATION the backend
discovers by itself. uv reads a `uv.toml` or a `pyproject.toml` `[tool.uv]` from the
cwd or a parent (and the user's `uv.toml`), and aelix honours that on purpose
(ADR-0200, `_uv_config_files`). In a cloned repository its `find-links` or index
settings can satisfy a trusted catalog's benign PACKAGE entry from that repository —
measured: `local-ext` installed `CWD-DECOY` 9.9 from `./w` on uv, pip read neither
file and installed nothing (`.omc/probes/131-live/verify7/uvconfig-head.txt`). That
is a known limit of #131, left to a follow-up issue (uv configuration from the cwd
can redirect a catalog package install), not part of this guarantee.

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
- uv's own project configuration in the cwd or a parent (`uv.toml`, `[tool.uv]`) is
  read by uv, as ADR-0200 intends — see above;
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
  now says that (the refusal stays: (5)). The extras refusal said pip takes extras on a
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
