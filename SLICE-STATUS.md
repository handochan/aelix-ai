# Windows EXPERIMENTAL slice — status and remaining work

> **Superseded in part (2026-09-04).** The `windows-latest` leg this document
> calls "future" and "decision-gated" exists and **gates** since `#103` landed:
> run 33853043685 at `beffc2f` was 0 failed / 9338 passed / 71 skipped on
> py3.11 and py3.12, down from 433 at the first run. The burndown it predicted
> is recorded issue by issue (#205–#219, #203, #109 comments). The "Remaining"
> list below is kept as the record of what was known before the leg ran; items
> that survived it are #107, #108, #46, #201 on the board. #106 and #204
> closed on 2026-09-04: `install.ps1` now runs end to end in CI (the
> `install.ps1 e2e` jobs), and W2's blanket force-ASK is gone, replaced by
> per-dialect classifiers (ADR-0237). #202 closed on 2026-09-05: the three
> teardown sites outside `aelix_agents/` end a process TREE — a Job Object on
> Windows, a process group on POSIX (ADR-0238). The `aelix_agents` half of it
> followed on 2026-09-05 as #220 — the print-channel spawn, the reaper's Windows
> legs, `rpc_channel`'s reaper calls and the `SIGBREAK` handler `print_mode`
> lacked — so items 1 and 2 below are amended in place and marked landed rather
> than struck.
> Windows is still not a supported platform — the suite passing is not that
> claim (README, "Platform support").

Branch: `feat/windows-experimental-slice` (Scenario C — parallel, tag-optional).

**Windows is not a supported platform.** This slice lands the parts that are
verifiable on Linux and writes the first Windows-**asserting** tests, so that a
future `windows-latest` CI leg is meaningful instead of green-over-broken.

Before this slice the repository had **zero** Windows-asserting tests: all 12
`win32` markers were subtractive `skipif`. A Windows leg added then would have
skipped past the interesting cases and — because of W1 below — written the
runner's real user profile.

## Landed

| Item | What | Where |
| --- | --- | --- |
| W1 | `HOME`-only test sandboxing → `sandbox_home` (HOME + USERPROFILE + HOMEDRIVE/HOMEPATH + APPDATA/LOCALAPPDATA), 25 sites across 9 files | `tests/env_sandbox.py`, `tests/test_env_sandbox_windows.py` |
| W2 | `_resolve_shell` win32 arm + `ShellConfig(path, command_flag)`; AUTO mode force-ASK on a shell the bash grammar can't read — the force-ASK half is **superseded by #204 / ADR-0237**, which routes PowerShell and `cmd` to classifiers of their own instead of downgrading them | `tools/bash.py`, `builtin/bash_classifier.py`, `builtin/permission.py`, `builtin/shell_classifiers/` |
| W3 | win32-safe process-tree kill at the two owned spawn sites. The body moved to `aelix_ai/utils/_process_tree.py` in #202 so the `aelix-ai` sites could share it; `tools/_process_tree.py` is a re-export shim and `bash.py` / `_subprocess.py` are unchanged | `packages/aelix-ai/src/aelix_ai/utils/_process_tree.py`, `tools/_process_tree.py`, `tools/bash.py`, `tools/_subprocess.py` |
| W4 | RPC stdin thread-pump (`connect_read_pipe` is `NotImplementedError` on Windows) | `rpc/rpc_mode.py` |
| W5 | `install.ps1` at parity with `install.sh`'s checksum gate, now executed end to end by the `install.ps1 e2e (pwsh)` / `install.ps1 e2e (powershell)` CI jobs (#106) | `install.ps1`, `.github/workflows/ci.yml`, `tests/packaging_gate/test_install_ps1_parity.py` |

Two facts were measured rather than assumed, and both shaped the design:

- **`ntpath.expanduser` never reads `HOME`.** It reads `%USERPROFILE%`, then
  `%HOMEDRIVE%`+`%HOMEPATH%` (CPython `Lib/ntpath.py`). This is why W1 is the
  blocking prerequisite: on Windows the old fixtures sandboxed nothing.
- **Windows has no process group here.** CPython's Windows `_execute_child`
  names the parameter `unused_start_new_session`, so `start_new_session=True`
  is silently ignored and `proc.kill()` would orphan descendants. Hence a Job
  Object, with `taskkill /T /F` as the fallback (#202). W3 shipped the
  `taskkill` half alone, and that half is incomplete for a reason Pi measured
  before we did (#9129): `taskkill /T` follows LIVE parent links, so it cannot
  walk to a descendant whose parent has already exited — it kills the layers it
  can see, exits 0, and the leaves keep running. A job holds them regardless.

A third fact shaped the *tests*: `shutil.which` itself branches on
`sys.platform` and then calls `_winapi`, which is `None` off Windows. So
`monkeypatch.setattr(sys, "platform", "win32")` crashes inside the very PATH
probe under test. The win32 arms therefore take an injected `platform`
argument. Anyone extending this slice should follow that pattern rather than
re-discovering the crash.

## Remaining — required before a `windows-latest` leg can be trusted

1. **`preexec_fn` spawn-site guard — LANDED (#200, `4043d1c`).** Both
   delegation channels passed `preexec_fn=pdeathsig` to
   `create_subprocess_exec`, and it is not the hook that Windows refuses: CPython
   rejects a non-None `preexec_fn` in `Popen.__init__`, before a child exists.
   Both spawns now go through `reaper.pdeathsig_preexec()`, which hands back the
   hook on Linux and `None` everywhere else. What made the bug invisible is worth
   keeping: both call sites wrap the spawn in `except Exception` and turn a
   failure into an error envelope, which is the right shape for a failed spawn
   and the wrong shape for an impossible one — nothing crashed, every delegation
   simply came back `error`, and 52 of the 238 `windows-latest` failures then
   open were downstream of it. "Owned by another track" above was accurate; the
   track was #200.
2. **The third kill site in `aelix_agents/reaper.py` — LANDED (#220).**
   `kill_tree` named `signal.SIGKILL`, which does not exist on Windows, so the
   escalation raised `AttributeError` inside the handler that exists to do the
   killing. It now takes its signal from `reaper._kill_signal()`: `SIGTERM` on
   win32, which is not a downgrade — Windows `os.kill` is
   `TerminateProcess(handle, sig)` for every value that is not a console control
   event.
   It did **not** adopt W3's `kill_process_tree`, and that was decided rather
   than skipped. `reap`'s *first* leg is `os.kill` too, so on Windows the
   "cooperative" SIGTERM already terminates the tree root uncatchably and
   orphans its descendants before any escalation runs; a `taskkill /T` here
   would arrive after the root it must walk from is gone. Closing that needs
   process-group or job-object isolation at the spawn site, not a different
   signal in the reaper. Windows silently declines `start_new_session`; it does
   **not** decline a job object (#202 built it, #220 adopted it here).

   **Amended (2026-09-05).** The isolation now exists: #202 built
   `aelix_ai.utils._process_tree` — a Job Object on Windows, a process group on
   POSIX — and adopted it at the three sites outside `aelix_agents/`
   (ADR-0238). The reasoning above about `taskkill /T` arriving after a dead
   root still stands; the isolation clause did not, and is corrected in place —
   what Windows declines is `start_new_session`, and a job object is precisely
   what it accepts. This item is now missing only its adopter. Doing it here,
   together with `print_channel.py`'s spawn and the `SIGBREAK` handler
   `print_mode.py` still lacks, is **#220**.

   **Landed (2026-09-05, #220).** `reaper.reap` and `reaper.kill_tree` now take
   the `ProcessTree` the print channel attaches right after its spawn and, on
   Windows, drive both legs through it: the cooperative one is a
   `CTRL_BREAK_EVENT` to the child's own console group, the escalation is
   `taskkill /T /F` followed by `TerminateJobObject`. The objection above is
   answered rather than overruled — the job is what carries the escalation to a
   descendant whose parent is already gone, and `/T` is only the belt that can
   still walk a live parent link. The other two halves landed with it:
   `print_channel.py`'s spawn asks for the containment
   (`containment_spawn_kwargs(new_session=True)` plus a `kill_on_close=True`
   tree) and `print_mode.py` grew the `SIGBREAK` handler that gives the
   cooperative leg something to reach. ADR-0238's "What stays open" is amended
   accordingly; what is left there is #222 (#221's three
   `subprocess.run(timeout=)` sites landed on 2026-09-05 as `run_contained`).
3. **`#46` cross-process locking.** *Correction to the original brief:* both
   `fcntl` sites are already `None`-guarded
   (`aelix_ai/settings/storage.py:204`, `aelix_ai/oauth/auth_storage.py:184`),
   so they do **not** crash on Windows. They silently `return None` — no lock
   is taken and the cross-process write-safety guarantee is quietly lost. The
   fix is `msvcrt.locking` on the win32 arm; the risk is corruption under
   concurrent writers, not a traceback.
4. **`%APPDATA%` config dir.** `cli/config.py:92` hard-codes
   `Path.home()/".aelix"/"agent"`. Correct-ish on Windows once W1's variables
   are set, but not the platform convention (`%APPDATA%\aelix`). Decide
   deliberately: changing it is a migration, not a bug fix.
5. **`#108` F-3..F-6** — not investigated in this slice.
6. **Image-protocol probe.** ~~`tui/images.py:107` keys off `TERM_PROGRAM` /
   `KITTY_WINDOW_ID` / `LC_TERMINAL`; Windows Terminal sets none of them
   (`WT_SESSION`).~~ **Moot** — `tui/images.py` was removed in #163 (ADR-0223);
   nothing rendered inline images on any platform.
7. **stdout encoding.** Nothing calls `sys.stdout.reconfigure(encoding="utf-8")`.
   Windows consoles default to a legacy code page, so non-ASCII agent output
   (and the box-drawing in the TUI chrome) will mojibake or raise
   `UnicodeEncodeError` on `print`. Cheap to fix, worth doing early — it will
   otherwise look like a hundred unrelated failures.
8. **Ctrl+G external editor.** `tui/shell.py:2559` falls back to `vi`,
   which does not exist on stock Windows. `notepad` is the fallback there.
9. **`Operating System :: OS Independent` classifiers** — untouched on purpose;
   that is a tag-time decision owned elsewhere.
10. **`rpc_client.stop()` / `subprocess_hooks`** use `proc.terminate()` /
    `proc.kill()`. These do *not* crash on Windows, but with no process group
    they end only the direct child and orphan its descendants. Lower severity
    than W3; same remedy.

    **Superseded (2026-09-05).** Both adopted `aelix_ai.utils._process_tree` in
    #202, and so did `oauth/_resolve_config.py`, which this item never named.
    "Lower severity than W3" was wrong in one direction: on POSIX, where W3's
    sites were already contained, `rpc_client.stop()` left a grandchild alive
    on the owner's own macOS box (`grandchild alive after stop(): True`). What
    each site does now differs: `subprocess_hooks` soft-kills the group;
    `rpc_client.stop()` soft-kills the child and escalates to the group only if
    the child survives the grace (that is the POSIX shape — on Windows
    `CTRL_BREAK_EVENT` is group-wide by construction); `!command` has no
    cooperative rung at all and goes straight to the hard kill. ADR-0238.

## Then: the CI leg itself

Adding `windows-latest` is a **decision-gated** step and was deliberately not
done here. When it is added, expect it to be red and plan a burndown rather
than treating the first green as a milestone — several items above are
"silently wrong" rather than "loudly broken", and a green leg that skips them
is worse than no leg. Items 3, 6, 7 and 8 in particular fail quietly.

**Superseded (2026-09-04).** `install.ps1` is no longer unexecuted: the
`install.ps1 e2e (pwsh)` / `install.ps1 e2e (powershell)` jobs in
`.github/workflows/ci.yml` run it end to end on `windows-latest`, under both
pwsh and Windows PowerShell 5.1, and gate CI (#106).
`tests/packaging_gate/test_install_ps1_parity.py`
still only proves the script has not *drifted* from `install.sh` — same env
vars, same checksum gate, same `uv` flags — but the e2e job now proves the
script itself runs, which the drift check alone could not.

**CLOSED — the unpinned-version hole.** This slice originally shipped
`install.ps1` reproducing `install.sh`'s lack of a package-version pin, flagged
as a shared risk to be fixed in both files at once. The release-and-honesty
track closed it in `install.sh`; `install.ps1` now carries the same pin. Both
parse the exact version out of the verified `aelix-<VER>-py3-none-any.whl`
entry in SHA256SUMS and install `aelix[extras]==<VER>`, so only the
checksum-verified wheel can satisfy the requirement and a same-named PyPI
release can no longer outrank it. The version comes from the wheel FILENAME,
never the tag — a tag is `v0.1.0-beta.1` while PEP 440 normalizes the same
release to `0.1.0b1`. `tests/packaging_gate/test_install_ps1_parity.py` fails if
EITHER installer drops the pin.
