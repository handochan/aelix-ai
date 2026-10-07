# Running a Private or Air-Gapped Catalog

Status: Accepted

A **catalog** is how `aelix` learns what extensions exist. It is a single JSON
document listing packs and where to get them, and it is deliberately boring:
a file you host, on infrastructure you already control. There is no registry
service to run, no account to create, and nothing that has to reach the public
internet.

That makes it work on a closed intranet, and in the hardest case — no server at
all — from a directory on a shared drive.

This guide sets one up end to end. If you only want to browse the public
marketplace, you already have it: `aelix extension discover` works out of the
box against the built-in default catalog.

## What a catalog is and is not

The catalog is **advisory**. It only chooses *what* to install. Every entry's
`source` is handed to the installer, which still runs its own consent prompt and
its own verification — so registering a catalog is not a decision to trust its
contents, and a catalog can never install anything by itself. On the way, a
`source` is checked against a short list of accepted forms, and a relative path
is read from the catalog file's own directory, never from wherever you ran
`aelix`; anything else is refused, never rewritten (see
[What `source` may be](#what-source-may-be) below).

For the same reason, an entry's `sha256` is **display-only**. It tells a human
which bytes an entry describes; it is never used as an integrity check and never
seeds the pin store. Pinning and signature verification are separate mechanisms
that belong to the installer (see [ADR-0188](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0188-issue65-discover-catalog.md)
for why the two are kept apart).

## The shortest path: a directory of wheels

Say you have built the packs your organisation is allowed to use, and dropped
them in one directory:

```
/srv/aelix/wheelhouse/
  acme_notes-1.4.0-py3-none-any.whl
  acme_deploy-0.3.2-py3-none-any.whl
```

Generate the catalog from that directory:

```console
$ aelix extension index /srv/aelix/wheelhouse --name "Acme internal"
Wrote /srv/aelix/wheelhouse/catalog.json (2 extensions from 2 artifacts).
Register it with:
  aelix extension source add --catalog file:///srv/aelix/wheelhouse/catalog.json
```

`index` reads each artifact's own metadata — name, version, description — and
hashes its bytes, so the document cannot disagree with the wheels it describes.
Re-run it after every build; that is the whole maintenance story.

Register it and browse:

```console
$ aelix extension source add --catalog file:///srv/aelix/wheelhouse/catalog.json
Registered source: [catalog] file:///srv/aelix/wheelhouse/catalog.json
  Browse it with: aelix extension discover

$ aelix extension discover --offline --refresh
Discover (2 matches):
  acme-notes 1.4.0  — Shared team notes   (catalog: Acme internal)
  acme-deploy 0.3.2  — Deploy helpers   (catalog: Acme internal)
```

`--refresh` is the only command that re-reads a catalog; without it `discover`
answers from the local cache. `--offline` refuses any network access, which is
what you want on an air-gapped host — it turns "quietly fell back to the
internet" into an error.

Install by name:

```console
$ aelix extension discover install acme-notes --offline
```

## `aelix extension index`

```
aelix extension index <dir> [--out FILE] [--name NAME] [--relative]
```

| Flag | Effect |
| --- | --- |
| *(none)* | Writes `<dir>/catalog.json`. |
| `--out FILE` | Writes elsewhere. `--out -` prints to stdout. |
| `--name NAME` | Sets the catalog's display name, shown beside each entry. |
| `--relative` | Emits `./name.whl`-style paths, relative to the catalog file, instead of absolute ones. |

It indexes `*.whl` and `*.tar.gz` directly inside `<dir>` — not recursively, so
a neighbouring build tree is never swept in. An archive it cannot read metadata
from is skipped and reported, never fatal.

Several versions of one pack collapse into a **single entry**: the newest
version becomes the entry, and every version found is listed under `versions`.
This is not cosmetic. Name resolution refuses an ambiguous match rather than
guessing, so two entries called `acme-notes` would make the pack impossible to
install by name.

### About `--relative`

A relative `source` is read from the directory of the **catalog file** that
lists it — not from the directory you run `aelix` in — so a catalog written with
`--relative` travels with its wheels. `--relative` measures each path from
wherever the catalog is written: `<dir>` by default, the `--out` file's directory
otherwise, which can give `../wheels/…` paths. `--out -` writes no file, so it
measures from `<dir>` itself — save its output beside the wheels. When the
catalog file is a symlink, both sides use the directory of the file it points
to — the one `index` prints in its `Register it with:` line — so registering
either the link or that printed path works.

```console
$ aelix extension index /mnt/share/wheelhouse --relative --name "Acme portable"
Wrote /mnt/share/wheelhouse/catalog.json (1 extension from 1 artifacts).
Register it with:
  aelix extension source add --catalog file:///mnt/share/wheelhouse/catalog.json
$ aelix extension source add --catalog file:///mnt/share/wheelhouse/catalog.json
Registered source: [catalog] file:///mnt/share/wheelhouse/catalog.json
  Browse it with: aelix extension discover
$ aelix extension discover --offline --refresh
Discover (1 match):
  acme-notes 1.4.0  — Shared team notes   (catalog: Acme portable)
$ cd ~ && aelix extension discover install acme-notes --offline
Resolved acme-notes -> /mnt/share/wheelhouse/acme_notes-1.4.0-py3-none-any.whl (from catalog Acme portable; the catalog says './acme_notes-1.4.0-py3-none-any.whl')
Install extension from path: /mnt/share/wheelhouse/acme_notes-1.4.0-py3-none-any.whl
  → … install … file:///mnt/share/wheelhouse/acme_notes-1.4.0-py3-none-any.whl
  pip will run the package's build/setup code. Only install sources you trust.
Proceed? [y/N]
```

(The `→` line names your installer — `pip`, or `uv pip` and its interpreter — and
hands it the path as a `file://` URI, below.)

Absolute paths stay the default because a relative one only works while the
catalog is a **local file**. Serve the same document over HTTPS or from a git
repository and there is no directory to read it from: `discover install` then
refuses the entry rather than guess. Re-run `index` without `--relative` for a
catalog you intend to serve.

Catalogs written by an older `aelix` with `--relative` hold bare filenames
(`acme_notes-1.4.0-py3-none-any.whl`, `team notes-1.0.tar.gz`). Those still
install from a local catalog file — a bare file name (no `/` or `\`, spaces
allowed) ending in `.whl`, `.zip`, `.tar.gz`, `.tgz`, `.tar`, `.tar.bz2`, `.tbz`,
`.tar.xz`, `.txz`, `.tlz`, `.tar.lz` or `.tar.lzma` is read as a path beside the
catalog, unless it also reads as a URL (`file:x.whl`) or as `name @ …`, which are
refused — but re-running `index` gives you the `./` form.

## The catalog document

You can also write the document by hand. It is a plain JSON object; the parser
is lenient and forward-compatible, so unknown keys are preserved and ignored
rather than rejected.

```json
{
  "schemaVersion": 1,
  "name": "Acme internal",
  "updated": "2026-08-07T05:10:09+00:00",
  "extensions": [
    {
      "name": "acme-notes",
      "source": "/srv/aelix/wheelhouse/acme_notes-1.4.0-py3-none-any.whl",
      "description": "Shared team notes",
      "version": "1.4.0",
      "versions": ["1.4.0", "1.3.0"],
      "sha256": "867efc1f669836c6b539a1125ddc0d7b4d257d42a6d05d1a0940eb810ee0daac",
      "homepage": "https://intranet.acme.test/aelix"
    }
  ]
}
```

Only `extensions` is required at the top level, and only `name` and `source` are
required per entry — everything else is display. An entry missing either is
skipped, and the rest of the catalog still loads.

### What `source` may be

`discover install` checks a `source` against this list before the installer sees
it. Only these forms are accepted; anything else is **refused** with an error
that names the entry, the catalog and these forms — it is never rewritten into
something else:

| `source` | What happens |
| --- | --- |
| a **package name**, optionally with `[extras]` and a version specifier (`acme-notes`, `acme-notes==1.4.0`, `acme-notes[extra]>=1,<2`) | Handed on unchanged and resolved through your index — unless it has no version specifier and a file or directory named like the package (the name alone: for `acme-notes[extra]`, `acme-notes`) sits beside a local catalog file (refused: write `./acme-notes` or `./acme-notes[extra]` if that is what you meant; `acme-notes==1.4.0` says "package" and is not checked), or the whole spec names a file or directory in the directory you run `aelix` from (refused: aelix's installer takes a target that exists on disk for a local path and would install that instead — run it from a directory without one to get the package). A name ending in `.git` (`acme.git`) is refused: the installer would take it for a git URL (`git+acme.git`) and no installer can fetch that — write the repository's full git URL. |
| an **absolute URL**, its scheme written in lowercase: `https://…`, `http://…`, `git+https://…` (or `git+ssh://`, `git+http://`, `git+git://`, `git+file:///…`), `git://…`, `ssh://…`, scp-style `user@host:owner/repo.git` (any user: `git@…`, `deploy@…`), `file:///…`, `file://localhost/…` (the host empty or `localhost` in lowercase, and no `%`-escape in the path); or `name @ <an https, http, git+ or file:/// URL>` — a git repository there as `name @ git+https://…` | Handed on **exactly as written** — `#sha256=…`, `#subdirectory=…`, `#egg=…` and `[extras]` included. Plain `http://` is fine here (only the catalog's own location must be HTTPS). The installer then adds the `git+` that pip and uv need to a bare URL, as it does for a URL you type: `user@host:path` becomes `git+ssh://user@host/path`, `git://…` becomes `git+git://…`, `ssh://…` becomes `git+ssh://…`, and an `https://…` URL whose path ends in `.git` becomes `git+https://…`. A `name @ git+…` reference is never touched. A scheme with a capital letter (`FILE:///…`, `Https://…`, `GIT+https://…`) is refused — write it in lowercase: aelix passes a URL on as written, and uv does not read `FILE:` as a scheme (it opened `FILE:/…` as a directory under your current one). So is `name @ https://host/o/r.git` without `git+` — uv clones it, pip downloads it as an archive and fails — with the `name @ git+https://…` spelling to use instead. |
| a **path starting with `./` or `../`** (or `.\` / `..\`), optionally followed by `[extras]` (a space before them is dropped, as pip drops it); or a bare archive file name (`acme_notes-1.4.0-py3-none-any.whl`, `acme_notes-1.4.0-py3-none-any.whl[feature]`) | Resolved against the directory of the **local** catalog file (the file a symlink points to), never your current directory, and installed as that absolute path, `[extras]` kept. The path is checked, hashed and pinned without the extras — a file literally named `x.whl[feature]` beside `x.whl` changes nothing, as for pip and uv. Refused, naming the entry and the catalog, when the catalog is served over HTTPS or git — there is no local directory to resolve it against. |
| an **absolute path** or a **`~` path** | Taken from any catalog, local or served: it names neither your current directory nor the catalog's, and `~` is the home directory of whoever runs the install. Installed as the resolved absolute path. |

Whichever path form it came from, the installer receives the resolved path as a
`file://` URI (`file:///srv/aelix/wheelhouse/acme_notes-1.4.0-py3-none-any.whl`),
never as a bare path it would parse again: given the bare path, uv cut a name at
`#`, dropped a `[]` or `[x]` and a trailing space (pip a trailing space and `[x]`
too) and installed a different directory, and pip split it at `;`. With
`[extras]` it is `name[extras] @ file:///…`, the name read from the wheel or sdist
file name or from a directory's `pyproject.toml` `[project] name` — a directory
without one cannot take extras and is refused. A resolved path containing `#`
anywhere is refused: uv cuts it there even as `%23` in the URI (rename it). The
`Resolved` and `Install extension from` lines show the path itself. The install
record keeps that URI, so `aelix extension update` re-installs the same path the
same way; a record an older `aelix` wrote (the plain path) is turned into the URI
when `update` reads it, unless the URI cannot carry it (a `#` in the path, or
extras on a project with no `[project] name`) - then it is updated as it was
installed. A path you typed yourself keeps your own hand-off when the
URI cannot carry it: `aelix extension install ./legacy[feature]` on a directory
with no `[project] name` is updated as the absolute path with `[feature]`, as it
was installed. If one recorded extension cannot be updated, `update` says why and
goes on with the others, and exits non-zero.

Any path must exist: a missing one is refused, never retried as a package name
and never looked for in your current directory. Refused outright:

- `name @ ./x`, `name@./x`, `name @ x`, `name @ ../x` — a direct reference to a
  relative path or a bare word, which uv opens from your current directory;
- `file:x`, `file:./x`, `file:`, `file:#subdirectory=x`, `name @ file:x`, and
  `file://x`, `file://localhost.evil/x`, `file://./x` — any `file:` URL that is
  not `file:///…` or `file://localhost/…` (uv reads all of them from your current
  directory, a host name as a directory there); and `file:/srv/x` with one slash,
  a spelling aelix does not take — write `file:///srv/x`;
- `file://LOCALHOST/…`, `file://LocalHost/…` — the host must be empty or
  `localhost` in lowercase, exactly (uv read `LOCALHOST` as a directory under your
  current one); write `file:///…`;
- a `file:` URL (or `name @` one) whose path holds a `%`-escape —
  `file:///srv/a%23b`, `file:///srv/a%20b`: the installer decodes it, and uv cut
  `%23` as a `#` and installed a neighbouring directory. Name the path directly
  (an absolute path, or `./…` beside the catalog) — aelix encodes it itself;
- `git+file://<host>/…` with a host other than empty or `localhost` (in
  lowercase), and a `git+file:` URL whose path holds a `%`-escape — spellings
  aelix does not take (git ignores the host; pip cut the path at a `%23` and
  cloned another repository): write `git+file:///<absolute path>`;
- a URL whose scheme has a capital letter, and `name @ <an https URL ending in
  .git>` without `git+` (both above);
- anything starting with `-` (`-e ./x` would reach the installer as an option);
- a relative path without the `./` prefix (`wheels/x.whl`), and a package name
  with an environment marker (`acme-notes; python_version >= "3.11"`) — neither
  is one of the forms above.

**On Windows, a `git+file:` source needs uv 0.11.27 or newer** when aelix
installs with uv — which it does after `install.sh` or `install.ps1`, whose
environment has no pip. A local repository there is `git+file:///C:/…` (any
drive letter), and uv before 0.11.27 crashes on that URL, `name @
git+file:///C:/…` included, whether it comes from a catalog or from `aelix
extension install`: it prints
`Git URL is invalid: AmbiguousAuthority` and `error: The channel closed
unexpectedly`, and exits 2 — the `:` of the drive letter followed by the
`@<commit>` uv adds trips its URL parser
([astral-sh/uv#19887](https://github.com/astral-sh/uv/issues/19887), fixed in
0.11.27; #393). aelix hands the source on as written and does not check your uv's
version. `uv --version` shows yours; `uv self update` upgrades the standalone uv
that the installers bootstrap (installed another way, upgrade it that way). The
installers bootstrap uv only when none is on `PATH` (the newest by default, or the
version `UV_VERSION` names); one already there is kept.

This closes a trap (#131). The installer used to receive a hand-written
`"source": "./acme-notes"` as written, so pip read `./acme-notes` from wherever
you ran `aelix`: the directory beside the catalog only when you stood in it, and
from anywhere else whatever `./acme-notes` that directory held — or nothing.
A bare `"source": "acme-notes"` meant as the neighbouring directory went to the
package index from everywhere but the catalog's directory. What you type
yourself is unchanged — `aelix extension install ./acme-notes` still means
`./acme-notes` from where you are — except that `./x.whl[feature]` now counts as
a path when `./x.whl` exists, which is how pip already read it. The
`Resolved` and `Install extension from` lines, the refusals and every `verify`
line print a path through the terminal-safe filter, so a control character in a
directory or file name (a symlink target, say) is printed inert; the command
line under them escapes it.
The decision is recorded in
[ADR-0255](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0255-a-catalog-source-is-placed-by-its-catalog-not-the-cwd.md).

### What this protects against

The guarantee is about the **directory you run `aelix` in**, for a catalog you
trust: aelix never resolves an entry's `source` against your current directory,
and never hands the installer a string it would read from there — a package name
goes on as a name, a URL as an absolute URL, a path beside the catalog as an
absolute `file://` URI. That is the shape of #131: a `./acme-notes` or
`acme-notes` entry installed a same-named directory from wherever you stood.

The installer does not run there either. With the uv backend, a `uv.toml` or a
`pyproject.toml` `[tool.uv]` table in the directory uv runs in, or in a parent of
it, is uv's own project configuration — in a cloned repository, its `find-links`
or index settings made a trusted catalog's package name install a wheel from that
repository (#392). So `discover install` and `aelix extension update` run the
installer, uv or pip, in aelix's own installer directory,
`~/.aelix/agent/installer-cwd` (under your agent dir; on macOS and Linux only you
can read it). It holds a `pyproject.toml` and a `uv.toml` that aelix writes and
that set nothing. uv looks for a project first — the nearest `pyproject.toml` —
and reads configuration from there; the `pyproject.toml` in the installer
directory declares no project, so uv does not go on to one above it, and the
`uv.toml` is then the first configuration file it finds. So no project
configuration is read at all: none from your current directory, its parents, or
the directories above the agent dir, even a `pyproject.toml` with a `[project]`
table. The install's consent block says where it runs. What still applies, as
[ADR-0200](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0200-catalog-fetch-and-installer-backend.md)
intends: your user-level `uv.toml` (`~/.config/uv/uv.toml`, or
`%APPDATA%\uv\uv.toml` on Windows), the system one, your `pip.conf`, and your
environment variables (`UV_*`, `PIP_*`) — put an organisation's index pin there. A
project you choose yourself with `UV_PROJECT` or `UV_WORKING_DIR` is read as uv
reads it. A `uv.toml` or `pyproject.toml` in your home directory is project
configuration to uv and does not apply to these installs.

What this protects against is your current directory — a repository you cloned.
Your own environment and your user- and system-level installer configuration are
yours: a repository cannot set them (from a project `.env` aelix admits only
provider credentials and a short checked list), so aelix passes them to uv and pip exactly as you set them. One
consequence: a RELATIVE path in them — `PIP_FIND_LINKS=./wheels` or `file:wheels`,
`UV_FIND_LINKS`, `UV_INDEX_URL=./simple`, `PIP_TARGET`, `UV_PROJECT`,
`UV_WORKING_DIRECTORY`, `PIP_CONFIG_FILE=pip.conf`, or a relative `find-links` in
`pip.conf` — is read from the installer directory for these installs, not from where
you are. That holds on uv too, where aelix reads your `pip.conf` to pass its index on
to uv: a relative `PIP_CONFIG_FILE` is opened in the installer directory, where pip
itself would open it. Some of those fail
loudly; pip skips a find-links location it cannot find with a warning and installs
from the default index. Use absolute paths. aelix checks one variable:
`UV_CONFIG_FILE`, which uv reads instead of every other configuration file, must be
an absolute path exactly as uv reads it — no leading or trailing space, no `~` (uv
does not expand it), no `file:` URL — or these installs are refused with "Set
UV_CONFIG_FILE to an absolute path.", because a relative one would quietly read
aelix's own empty `uv.toml` there. A relative `--index-url ./simple` you type on
`discover install` is read from where you typed it; a `file:/…` URL passes as
written. If the installer directory or either file in it is a link when aelix
prepares it, the install is refused. (A link someone swaps in after that check, while
the consent prompt waits, is not caught — only someone who can already write your
agent dir can do that.)

A typed `aelix extension install <spec>` is different: you typed it in your
current directory, so its installer runs there, where uv reads that directory's
`uv.toml` / `[tool.uv]` (and `python -m pip` imports a `pip/` package from it
first — for `aelix extension remove` too; tracked as issue #394). Install from a
directory you trust, or install through a catalog.

It does not make a hostile catalog safe. A catalog can already name any package
or URL to install, so a catalog whose own content is hostile does not need a
trick spelling to hurt you — trust a catalog before you register it. Odd
spellings in a catalog are refused where that is cheap, but that is hardening,
not part of the guarantee. Known limits, found in the review rounds
and left as they are: a catalog may point a `./` path through a symlink anywhere
(it is followed, as the filesystem does); an absolute or `~` path is taken from
any catalog; a `name @ <URL>` or a git URL is fetched as written, so a
`#subdirectory=` or a git ref is the catalog's choice; whoever can write beside
the catalog can swap a path between the check and the install (default
verification installs the copy it hashed; `--no-verify` does not); a relative
catalog location left in a hand-edited settings file is read from where you run
`discover --refresh` (it says so); and a typed `aelix extension install <target>`
still means what it means from where you are.

The contract is defined by [ADR-0188](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0188-issue65-discover-catalog.md);
[ADR-0207](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0207-a-catalog-listed-pack-must-bind-a-manifest.md) covers what
makes a listed pack's manifest actually bind once installed.

Documents are capped at 2 MB and 5000 entries.

## Where a catalog can live

Register any of these with `source add --catalog`:

| Location | Example | Notes |
| --- | --- | --- |
| Local path | `/srv/aelix/catalog.json` | Stored absolute, even when you type it relative. `discover install --catalog` accepts its name, the absolute path (or another spelling of it, through a symlink), or a relative path that names the same file from where you run it (`--catalog catalog.json` beside it, `--catalog ../catalog.json` from below it). Its name is known only after `discover --refresh` has fetched it; until then — or when its last refresh failed — `--catalog` with its path says so and tells you to refresh. A relative path left in the settings file by hand is read from the directory you run `discover --refresh` in; the refresh says so and records that absolute path, which its entries' relative sources then resolve against, and `--catalog` with the path exactly as written there works from any directory; a `--catalog` path that names another file under the same relative spelling (the same `catalog.json` read from elsewhere) says which file the cached copy was read from. |
| `file://` URL | `file:///srv/aelix/catalog.json` | Same thing, explicit. |
| HTTPS | `https://intranet.acme.test/catalog.json` | TLS required. A refresh while offline (`--offline`, `AELIX_OFFLINE`, `PI_OFFLINE`) skips it; until it is fetched, `--catalog` with its URL says so and, while offline, how to refresh online. |
| Git repo | `git+ssh://git@git.acme.test/acme/catalog.git` | Reads `catalog.json` at the repo root. |

A git source is cloned non-interactively on macOS and Linux: it runs in a
session of its own and has no terminal, so git and ssh cannot ask you anything
on it. Configure a credential helper (https) or load the key into your ssh
agent and add the host to `known_hosts` (ssh) — Aelix will not prompt on your
terminal for a password, passphrase, or host-key confirmation; a clone that
tries to fails at once with git's or ssh's own message. On Windows there is no
session to take away — the clone is held by a new process group and a job
object and keeps the console Aelix was started from, so a prompt can still
appear there, and one nobody answers costs the full 60 s clone timeout (Windows
is not a supported host; see the README). An **askpass** program still works and
can still ask: if `GIT_ASKPASS` or `SSH_ASKPASS` is set — VS Code exports
`GIT_ASKPASS` unconditionally in its integrated terminal — git calls it, and a
dialog nobody answers costs the full 60 s clone timeout before the clone fails.
Unset those variables for an unattended run. Stopping a refresh by hand now
takes two `^C`: the clone runs outside your terminal's foreground group and the
first one is swallowed by the CLI's asyncio runner — measured, one `^C` left the
clone running to its full bound (the timeout ladder ended it) and two ran the
interrupt ladder at 0.25 s.

Plain `http://` is refused: an unauthenticated document over a rewritable
transport decides what your users install. `file://` and git `ssh`/`file`
transports have no such requirement, so a closed network stays fully supported.

Registering several catalogs merges them. Each entry displays which catalog it
came from, and `discover install --catalog <name>` disambiguates when two
catalogs list the same name.

```console
$ aelix extension source list
Extension sources:
  [catalog] https://handochan.github.io/aelix-marketplace/catalog.json  (built-in default — present)
  [catalog] file:///srv/aelix/wheelhouse/catalog.json

$ aelix extension source remove file:///srv/aelix/wheelhouse/catalog.json
Removed 1 source(s).
```

## Replacing or removing the built-in default

Out of the box a built-in default catalog points at the public marketplace. Two
ways to change that.

**Per run, with an environment variable.** `AELIX_DEFAULT_CATALOG` repoints the
default:

```console
$ AELIX_DEFAULT_CATALOG=file:///srv/aelix/wheelhouse/catalog.json \
    aelix extension discover --offline --refresh
```

Setting it to the empty string removes the default entirely, leaving only the
catalogs you registered. Useful in a locked-down image:

```console
$ export AELIX_DEFAULT_CATALOG=""
```

**Persistently, by opting out.** Removing it as a source records the opt-out:

```console
$ aelix extension source remove https://handochan.github.io/aelix-marketplace/catalog.json
(built-in default opted out)

$ aelix extension source list
Extension sources:
  [catalog] https://handochan.github.io/aelix-marketplace/catalog.json  (built-in default — suppressed)
```

It stays listed, marked `suppressed`, rather than vanishing — so a later reader
can tell "we turned this off" from "this was never here". `--no-default-catalog`
skips it for a single `discover`.

## A fully air-gapped setup

On a host with no route to the internet:

1. **On a connected machine**, build or download the wheels you need, including
   each pack's dependencies, into one directory.
2. Run `aelix extension index <dir> --name "<your org>"`.
3. **Move the directory** — the wheels and the generated `catalog.json` together
   — onto the air-gapped host or a share it can read.
4. On the air-gapped host:

```console
$ export AELIX_DEFAULT_CATALOG=""          # no public default to attempt
$ export PIP_NO_INDEX=1                    # dependencies never reach PyPI
$ export PIP_FIND_LINKS=/mnt/share/wheelhouse

$ aelix extension source add --catalog file:///mnt/share/wheelhouse/catalog.json
$ aelix extension discover --offline --refresh
$ aelix extension discover install acme-notes --offline
```

The catalog decides *what* to install; **pip's own configuration decides where
dependencies come from**. `PIP_NO_INDEX` and `PIP_FIND_LINKS` (or the equivalent
`no-index` / `find-links` in `pip.conf`) are inherited by the installer, so a
pack whose dependencies are also in the wheelhouse resolves entirely offline.
Use `--index-url` only for a real PEP 503 index — an internal devpi or
Artifactory mirror — not for a flat directory of wheels.

If the directory lands at a different path than it was indexed at, re-run
`index` there — absolute paths are recorded at generation time — or index it
with `--relative` in the first place, which records paths from the catalog file
and so survives the move. Regenerating is cheap and is always the right fix.

Keep `--offline` on every command. Without it a missing dependency is a silent
attempt to reach PyPI, and on an air-gapped host that is a timeout you will
spend an afternoon on.

## Verifying what you publish

A pack can install cleanly and still contribute nothing: if its
`aelix-plugin.toml` does not ship inside the built distribution, the host has no
manifest to read and every declared tool, command, theme and MCP server is
silently inert.

Check before you publish, not after:

```console
$ aelix extension verify acme-notes
```

The exit status is stable for CI — `0` only when every manifest binds — so this
is the gate to put in front of a catalog. `aelix extension list` annotates the
same verdict for everything currently installed, and the `/extension` manager in
the TUI shows any pack that was found and refused.

See [extension-authoring.md](extension-authoring.md#packaging-your-extension)
for the packaging rules that decide whether a manifest ships.

## Related

- [extension-authoring.md](extension-authoring.md) — writing the packs you list.
- [ADR-0188](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0188-issue65-discover-catalog.md) — the catalog format
  and why it is advisory.
- [ADR-0192](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0192-issue76-default-catalog-opt-out.md) — the built-in
  default and its opt-out.
- [ADR-0207](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0207-a-catalog-listed-pack-must-bind-a-manifest.md) — manifest
  binding for installed packs.
- [ADR-0255](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0255-a-catalog-source-is-placed-by-its-catalog-not-the-cwd.md) — how a
  relative `source` is placed, and what is refused.
