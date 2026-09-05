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
`source` is handed unchanged to the installer, which still runs its own consent
prompt and its own verification — so registering a catalog is not a decision to
trust its contents, and a catalog can never install anything by itself.

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
| `--relative` | Emits bare filenames as `source` instead of absolute paths. |

It indexes `*.whl` and `*.tar.gz` directly inside `<dir>` — not recursively, so
a neighbouring build tree is never swept in. An archive it cannot read metadata
from is skipped and reported, never fatal.

Several versions of one pack collapse into a **single entry**: the newest
version becomes the entry, and every version found is listed under `versions`.
This is not cosmetic. Name resolution refuses an ambiguous match rather than
guessing, so two entries called `acme-notes` would make the pack impossible to
install by name.

### About `--relative`

Read this before using it. A `source` path is resolved against the **process
working directory** — the directory the user ran `aelix` from — not against the
catalog file's own location. A relative `source` therefore only resolves for
someone standing in the wheel directory.

From anywhere else it does not fail loudly. A path that does not exist falls
through to being treated as a package name, and the install becomes a public
PyPI lookup for whatever is called `acme-notes` — someone else's package, if
they registered the name first. That is why absolute paths are the default.

Use `--relative` only when the directory travels together with its catalog (a
mounted share, a copied USB drive) *and* installs are run from inside it:

```console
$ cd /mnt/share/wheelhouse
$ aelix extension index . --relative --name "Acme portable"
$ aelix extension source add --catalog file:///mnt/share/wheelhouse/catalog.json
$ aelix extension discover install acme-notes --offline
```

The catalog survives the directory moving to a new path. Nothing else about it
does, so re-run `index` rather than hand-editing paths.

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

`source` accepts anything the installer accepts:

- a **path** to a wheel, sdist, or source directory;
- a **git URL** (`git+https://…@<40-hex-sha>` to pin it);
- a **package name** (`acme-notes==1.4.0`) resolved through your index.

The contract is defined by [ADR-0188](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0188-issue65-discover-catalog.md);
[ADR-0207](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0207-a-catalog-listed-pack-must-bind-a-manifest.md) covers what
makes a listed pack's manifest actually bind once installed.

Documents are capped at 2 MB and 5000 entries.

## Where a catalog can live

Register any of these with `source add --catalog`:

| Location | Example | Notes |
| --- | --- | --- |
| Local path | `/srv/aelix/catalog.json` | |
| `file://` URL | `file:///srv/aelix/catalog.json` | Same thing, explicit. |
| HTTPS | `https://intranet.acme.test/catalog.json` | TLS required. |
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
`index` there — absolute paths are recorded at generation time. Regenerating is
cheap and is always the right fix.

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
