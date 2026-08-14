# Security Policy

## Reporting a vulnerability

**Please do not put vulnerability details in a public issue.**

This repository does **not** yet have a private disclosure channel. GitHub's
private vulnerability reporting is turned off here, so there is no **Report a
vulnerability** button on the Security tab, and Aelix has no published security
e-mail address. Rather than send you to a button that is not there, here is the
route that works today.

### How to report, right now

1. Open a **public issue** that contains **no technical detail** — no
   reproduction, no payload, no affected code path. A title and one line is
   enough, for example:

   > *Security report — requesting a private channel*
   >
   > I have a security finding for Aelix and would like a private channel to
   > send it to. No details here.

2. The maintainer will reply on that issue with a private channel to continue
   in, and the details go there — not in the issue.

That first issue is deliberately content-free, so filing it discloses nothing.
It is a knock on the door, not the report.

If you would rather not open an issue at all, the maintainer's GitHub profile is
<https://github.com/handochan>; anything reachable from there that lets you ask
for a private channel is fine. Please still keep the details out of the first
message.

> **Owner action — recommended next step.** Enabling *Settings → Code security →
> Private vulnerability reporting* on this repository would replace the whole
> dance above with GitHub's built-in private advisory flow, which is the right
> long-term route. It is a repository setting, so it cannot be fixed in this
> file. Until it is enabled, the steps above are what actually works, and this
> section should be rewritten the day it changes.
>
> A dedicated security e-mail address has not been chosen either. When one
> exists it belongs here, replacing this note.

### What helps most in a report

Once you are in a private channel:

- The commit SHA or version (`aelix --version`).
- The model provider in use, if the issue involves a provider path.
- A minimal reproduction, and what an attacker gains.

## Scope: what this project is

Aelix is a coding agent. **It runs commands and edits files on your machine because
that is its purpose.** The following are working as designed and are not
vulnerabilities on their own — though a way to trigger them *without* the operator's
consent very much is:

- **The agent executes shell commands, reads and writes files, and installs
  extensions**, driven by a language model's output. A model that is prompted (or
  prompt-injected) into destructive behaviour can act on that within whatever
  permissions the operator granted.
- **Headless modes auto-approve mutating tools by default, including `bash`.**
  `--print`, `--mode json` and RPC mode have no interactive approval prompt to show,
  so the built-in permission gate falls through to allow
  (`builtin/permission.py`, `headless_default = "allow"`). Do not point a headless
  Aelix at untrusted input on a machine you care about.
- **`AGENTS.md` project context is loaded regardless of project trust, and no mode
  asks you first.** Aelix walks from the working directory up to the *filesystem
  root*, reads every `AGENTS.md` it finds, and puts the text into the system prompt.
  A repository you have just cloned and declined to trust therefore still gets to
  write system-level instructions for the model, alongside the `read` / `write` /
  `bash` tools. There is no confirmation step for this in any mode — an interactive
  session gets one after-the-fact `[Context]` line in the startup banner, and headless
  modes get nothing. This follows pi by decision, not by omission
  ([ADR-0217](docs/decisions/0217-project-context-files-follow-pis-published-policy-and-forward-sync-to-its-fence.md)):
  pi's own security documentation says context files "are loaded regardless of project
  trust unless context loading is disabled", and that prompt injection from context
  files "is expected local-agent risk and cannot be reliably prevented by pi". Two
  consequences worth spelling out. Because the walk does not stop at a repository
  boundary, an `AGENTS.md` in `$HOME` reaches **every** project beneath it. And
  because Aelix wraps each file in pi's `<project_context>` /
  `<project_instructions path="…">` fence with both the content and the path
  XML-escaped, tags inside a hostile file become inert text rather than a way to close
  the boundary early and have the rest read as though it came from outside project
  context — but that is a bound on the *labelling* only. The file is still an
  arbitrary-instruction channel by design, and it can simply ask the model, in prose,
  for whatever it wants. Treat an unreviewed `AGENTS.md` as untrusted input the model
  will read and may act on.
- **What that sends to your provider, and what `--no-context-files` does not stop.**
  The project-context block is part of the system prompt, so the full text of every
  discovered `AGENTS.md` **and each file's absolute path** goes to whichever model
  provider you configured. `--no-context-files` / `-nc` removes all of that: the file
  contents, the fence, and the `path=` attributes. It is **not** a privacy opt-out.
  The flag *is* inherited by delegated subagents, so a child spawned mid-session does
  not re-discover the files you suppressed.
- **The base system prompt sends six absolute paths on every turn, including your
  OS username, and no flag suppresses them.** `--no-context-files` removes none of
  them, because they are in the base prompt rather than in the context chunk.
  Measured on a default run, they are: the working directory
  (`- Working directory: /abs/path`); the two extension write targets the
  self-extension block names (`<agent dir>/extensions/<name>.py` and
  `<cwd>/.aelix/extensions/<name>.py`); the two files inside the installed package
  the block tells the model to read (`examples/echo/echo.py` and
  `extensions/api.py`); and the bundled documentation directory. The first is
  wherever you started Aelix, and on a normal `uv tool install` the agent dir and
  the install prefix are both under `$HOME` — so **the account name of the user
  running Aelix reaches the configured provider on every request**, together with
  the shape of the directory layout.

  This is deliberate and it is what pi does: pi's own prompt emits
  `getReadmePath()` / `getDocsPath()` / `getExamplesPath()` and
  `Current working directory: …` as absolute paths, with no `~` abbreviation
  anywhere. Aelix keeps that (issue #162). Absolute paths are also load-bearing
  rather than incidental — a bare `python` inside the `bash` tool resolves through
  `PATH` to a different interpreter than the `uv tool install` virtualenv, so a
  relative or `~`-shortened pointer is not equivalent for every consumer.

  If the directory layout or the account name is sensitive, no flag will help you:
  run somewhere else, run as a different user, or redact the outbound payload from
  an extension — the `before_provider_payload` hook can rewrite it before it
  leaves the process.
- **Extensions are Python, loaded in-process, with the full privileges of the
  Aelix process.** Installing an extension is equivalent to running its author's
  code. `aelix extension install --require-signature` enforces a valid trusted
  Ed25519 signature; without it, unsigned sources are accepted on first use.
  Note that `FIRST_PARTY_KEYS` ships empty, so signature enforcement is
  entirely opt-in today — the README and the website say the same.
- **Two third-party binaries are downloaded on first use.** The first `grep` or
  `find` fetches `ripgrep` (BurntSushi/ripgrep) and `fd` (sharkdp/fd) from their
  upstream GitHub Releases into `~/.aelix/agent/bin`, extracts them with Python's
  stdlib safe extractors, and marks them executable — this is what lets both tools
  honour `.gitignore` (`util/tools_manager.py`). A copy already on `PATH` is
  preferred, and `--offline` (or `PI_OFFLINE=1`) skips the download entirely.
  These archives are **not** checksum-pinned by Aelix; they are fetched over HTTPS
  from the upstream project's release assets.

Reports we do want, among others: a way to escape or bypass the permission gate in
an interactive session, path-traversal out of the workspace, credential or
transcript disclosure (settings files, provider keys, session logs), signature
verification that can be spoofed, or code execution triggered by merely *reading*
untrusted content.

## Supported versions

Aelix is pre-1.0 and currently at `0.1.0b1`, with no tagged release yet. There are
no maintained release branches and no backports: **security fixes land on `main`**,
and the fix is the upgrade path. A supported-versions matrix will appear here once
there is more than one release to support.

## Response expectations

This is a small project. Reports are triaged when the maintainer sees them; there is
deliberately **no response-time commitment** here, because promising one that is not
staffed would be its own kind of dishonesty. For the same reason the two lines below
are stated as intent rather than as guarantees — a single unstaffed maintainer
cannot promise either, and advisory credit additionally depends on advisories being
published at all, which needs the repository setting noted above:

- The maintainer *aims* to acknowledge every report.
- Reporters *will be* credited in any advisory that does get published, unless you
  ask not to be.

Please give us a reasonable chance to ship a fix before disclosing publicly.
