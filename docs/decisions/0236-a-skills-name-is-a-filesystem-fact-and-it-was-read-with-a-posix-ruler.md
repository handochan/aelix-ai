# 0236. A skill's name is a filesystem fact, and it was read with a POSIX ruler

Status: Accepted (2026-09-03).
Date: 2026-09-03
Relates: ADR-0069 (the skills port, whose Not-done list booked this as
"P-230 — Windows path normalisation, Linux-target sprint"),
ADR-0034 / ADR-0235 (the Pi pin, and the retirement of the rule that made
divergence from it a defect),
ADR-0197 (the 3-band rule this kernel change is an exception to).

The skills subsystem did not degrade on Windows. It was off. Packaged, project
and user tiers all loaded zero skills, and the feature was indistinguishable
from not shipping one — including the two skills the wheel installs so that a
fresh user has a non-empty catalogue at all.

## What was broken, measured on darwin

Three helpers in the kernel's skills loader split paths on a literal `/`. A
Windows path has none, so on the path a Windows install actually hands the
loader — `C:\Users\me\AppData\Roaming\aelix\skills\extending-aelix\SKILL.md`:

```
_dirname(p)                            -> '/'
_basename(_dirname(p))                 -> ''
_validate_name('extending-aelix', '')  -> ['name "extending-aelix" does not
                                            match parent directory ""']
format_skill_invocation(skill)         -> 'References are relative to /.'
_relative_path(root, skill_dir)        -> 'C:\sk\extending-aelix'
```

Four consequences, in descending order of how loudly they fail:

1. **Every skill is rejected.** The name is validated against its parent
   directory's name, which came out empty, so every skill on the machine drew
   an `invalid_metadata` diagnostic.
2. **Every skill without a frontmatter `name` is named the empty string**, since
   the parent directory name is also the fallback.
3. **The model is told its references resolve at `/`**, which is not the skill's
   directory on any machine — so a skill that survived would still send the
   read tool to the wrong place.
4. **Ignore files stop being honoured** — silently, and in the permissive
   direction. `.gitignore` / `.ignore` / `.fdignore` were read and prefixed, but
   the path they were matched against was the whole absolute path rather than a
   relative one, so no pattern could match it.

None of this needs a Windows runner to observe: the failure is driven by the
separator in the string, not by `sys.platform`.

## Why the helpers were POSIX-only, and why that was never parity

They mirror Pi's `dirnameEnvPath` / `basenameEnvPath` / `relativeEnvPath`, which
split on `/` correctly, because they address an **ExecutionEnv path** — the
POSIX-shaped virtual path space Pi keeps so one loader can run in a browser and
in Node. ADR-0069 dropped that abstraction on purpose ("Pi `ExecutionEnv` →
`pathlib` directly. Pi's `ExecutionEnv` exists for browser/Node interop; Aelix
is Python-only"), and what reaches these helpers is therefore a native
filesystem path from `pathlib`.

So the string helpers outlived the path space they were written for. The same
ADR says as much in its own Not-done list — "P-230 (Windows path normalisation
— Linux-target sprint)" — which makes this a **tracked deferral, not a parity
contract**. Nothing in `tests/pi_parity/` pins these helpers; the only pinned
behaviour that touches them is the wire line `References are relative to /p/ex.`
for a POSIX input, which is byte-for-byte unchanged here and stays so on a
Windows runner too. ADR-0235 had already retired the rule that would have
required this paragraph.

## The split that is real, and the one that is not

The question worth asking was whether the logical skill id needs POSIX semantics
while the filesystem lookup needs native ones. For the name and the directory:
**no**. The name is the containing directory's name — a filesystem fact, read on
whatever separator the filesystem in question uses — and the wire line names a
directory the model is expected to open, so it must stay openable on the machine
it names. Both helpers now read either separator, on every platform, which is
what `tools/bash.shell_basename` already does and for the same stated reason:
the caller may be reasoning about a Windows path while running on POSIX.

For the ignore path: **yes**, and it is the only place the split exists.
Subtracting `root` from `target` is a filesystem question — on Windows it has to
know that `C:\a` is the parent of `C:\a\b` — while the string that comes out is
a logical path handed to `pathspec`, whose `gitwildmatch` patterns are
`/`-separated wherever they run, and which the nested-ignore-file prefixing also
builds with `/`. So that helper relativises with `pathlib` and emits with
`as_posix()`: native in, POSIX out, by construction rather than by luck.

## The kernel band

`packages/aelix-agent-core` is the kernel and is frozen by exception
(ADR-0197). This is an exception of exactly the kind that gate's own docstring
anticipates — "repairing a pi-parity regression the kernel itself claims in a
comment to implement". The defective code IS the kernel's skills loader, so the
fix can live nowhere else. It adds no `aelix_agents` import, no spawn site, no
cap, no consent path and no registry surface; `test_kernel_has_no_subagent_surface`
is unaffected. Delegation did not create the requirement — a platform did.

## Verification

`tests/harness/test_skills_win32.py` pins all four consequences with no
`skipif`, on Windows-shaped strings and `PureWindowsPath`, so the cases run on
the platforms we can actually run on. Reverted against the fix they fail 8 of
10; the 2 that survive are the POSIX parameters, which is the point of carrying
them.

## Not done here

- **The scan is still native-only in one respect nobody has measured**: a
  case-insensitive filesystem can hand the loader a directory whose name differs
  in case from the frontmatter `name`, and the validator compares exactly. That
  is a real Windows and macOS question, it is not this defect, and inventing a
  case-folding rule for skill ids without measuring one is how a second bug gets
  shipped inside a fix for the first.
- The `pathspec` `gitwildmatch` deprecation warning ADR-0069 booked as LOW-3 is
  still open, and is still not this.
