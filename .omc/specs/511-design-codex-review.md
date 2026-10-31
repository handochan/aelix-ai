Reviewed `e19ab55e`. No files changed. The owner decisions are treated as settled.

1. **High — §3.4: `storage.with_lock` does not provide the promised cross-session transaction.**  
   Evidence: [storage.py:239](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/storage.py:239). Existing files are locked by inode, then replaced at line 193. A waiter can hold the old inode while another writer locks the replacement. For a missing file, `fn(current)` runs **before** acquiring the lock. Windows acquires no lock at all (`:204`). Concurrent saves can therefore lose edits despite `edit_list` reading inside `with_lock`.  
   **Fix:** require a stable sidecar lock, acquired before reading or invoking `fn`, with a Windows implementation. Add real multiprocess tests for existing and missing files.

2. **High — §§3.4–3.6: the storage backend can hide read failures or publish damaged data.**  
   Evidence: [storage.py:161](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/storage.py:161) converts read errors into `None`, indistinguishable from a missing file. The writer (`:180–193`) uses a predictable `.tmp` path, follows existing symlinks, and ignores `os.write`’s returned byte count. Thus `edit_list` alone cannot guarantee “I/O errors raise” or preserve existing data.  
   **Fix:** introduce a strict storage operation that distinguishes absence from read failure, uses an exclusively created temporary file, and writes the complete payload. Test read failures, temporary-path symlinks and short writes.

3. **High — §3.4: an older queued settings write can overwrite the new disk-based edit.**  
   Evidence: [settings_manager.py:805](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/settings_manager.py:805) captures a snapshot before scheduling; `_write_task` later writes that snapshot, including complete modified lists (`:943–944`). The proposed primitive does not join this queue. An in-memory reproduction produced `['-original', '-panel']` after the fresh edit, then `['-queued']` after flushing the older setter.  
   **Fix:** serialize `edit_list` with existing per-scope writes, drain preceding writes, and reconcile modification tracking before subsequent snapshots. Test queued setters before and after a panel save, including RPC writers.

4. **High — §§3.3–3.6, §4: “fresh read of both files” contradicts the untrusted-scope contract.**  
   Evidence: [settings_manager.py:484](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/settings_manager.py:484) returns **before storage access** for an untrusted project; writes assert trust at `:632–636` and again inside queued execution at `:849–853`. Disabling the Project save target is insufficient if `snapshot`, post-save verification or `edit_list` directly opens both files.  
   **Fix:** make trust an enforced input to every selection read/write operation. Untrusted project evaluation must use an empty scope without opening or locking its settings file. Add read spies and FIFO/symlink cases, plus trust revocation between enqueue and execution.

5. **High — §§3.3, 3.8: extension filtering needs an explicit pre-trust phase.**  
   Evidence: [entry.py:2901](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:2901) imports user/global extensions for trust voting **before** project trust is resolved. The normal factory loads again at `:1964`; [status.py:338](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/status.py:338) is another production loader. None currently receives selection settings. Merely naming `_discover_entries` as the filter location leaves its policy inputs and bootstrap behavior unspecified.  
   **Fix:** define global-only filtering before trust voting, then trusted-project filtering after the decision. Wire every loader and the manifest scan explicitly. Test that a globally disabled extension neither imports nor votes, and that project entries cannot influence the vote-load.

6. **High — §§3.3, 3.5: an untrusted project can still change what `-e` loads.**  
   Evidence: [loader.py:685](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py:685) classifies a module-shaped string as a path when that path exists under `cwd`. The configured-path tier then loads it outside `no_project_local` gating (`:814–856`). Consequently `-e acme` can load a repository’s `acme/` instead of the installed module, even under `--no-approve`; the current warning does not prevent it.  
   **Fix:** compile settings paths against their settings-file base before passing them to the loader. For ambiguous CLI module references in an untrusted project, require explicit local-path intent or refuse the local interpretation. Add a module/path collision sentinel test.

7. **High — §3.8, §8: the proposed choke-point coverage is incomplete.**  
   All current production callers are:

   - `set_skills`: [entry.py:3764](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:3764); [agents/service.py:461](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/agents/service.py:461), `:743` (**rollback**).
   - Harness `set_active_tools`: `entry.py:1592,1598`; `agents/service.py:481`; [agent_session_runtime.py:1182](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:1182), `:1206`; [rpc/control.py:476](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/control.py:476), `:892,894`.
   - Extension API: [extensions/api.py:2362](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py:2362) calls the synchronous action, bound directly to `_action_set_active_tools` at [harness/core.py:738](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:738). Its other callers are constructor `:769` and public wrapper `:2938`.
   - Bypassing either setter: `_refresh_extension_tools` assigns registry/active state at `harness/core.py:1010,1031`; `set_tools` assigns both at `:3512–3513`; profile rollback assigns active state at `agents/service.py:737`. `set_tools` callers are [control_mcp.py:253](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/control_mcp.py:253), `:302`.

   Extension actions and registration can occur during a turn. A `set_skills` call-site AST gate will miss these paths.  
   **Fix:** specify one policy/commit boundary for setters **and registry mutations**, preserving CLI exclusions on registration, restoration and MCP attachment. Rollback must restore the previous applied snapshot rather than freshly resolve settings. Expand the AST gate accordingly. RPC currently has an idle guard (`control.py:261–297`); preserve it.

8. **High — §3.8: `wait_for_idle()` does not reserve an idle boundary.**  
   Evidence: [harness/core.py:3781](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:3781) only awaits an `Event`. A waiting apply task can wake, then run after another prompt has claimed the harness (`:1389–1405`). A pending task can also retain a harness that `/reload`, `/new` or `/resume` replaces.  
   **Fix:** add an exclusive commit operation coordinated with turn acquisition, recheck the live harness/generation, and cancel or rebind pending applies on replacement. Test a second turn starting before the waiter resumes, and replacement while an apply waits.

9. **High — §3.8: the prompt rebuild can apply saved state before the intended boundary, or retain old state after applying.**  
   Evidence: the prompt reads `skills_holder` through [entry.py:3605](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:3605), while the panel-facing registry is `harness.skills`. `set_skills` changes only the latter ([core.py:1314](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:1314)). Conversely, resolving fresh saved settings inside every rebuild would let an unrelated tool rebuild consume pending edits during a turn.  
   **Fix:** define separate raw inventory, saved selection and applied selection. Commit the applied skill objects and prompt-provider snapshot together; rebuilds must consume that applied snapshot. Test saving during a turn followed by an extension-triggered tool rebuild.

10. **High — §§3.6, 3.8: the proposed apply sequence cannot detect prompt-rebuild failure.**  
    Evidence: [harness/core.py:4499](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:4499) catches rebuild exceptions and retains the old prompt. Thus `set_skills → rebuild_system_prompt → resources_update` can report success while the prompt remains stale. Tool selection similarly mutates before rebuilding (`:4450–4455`).  
    **Fix:** provide a strict prepare/commit path that computes the replacement prompt before committing registries and applied state. Define `resources_update`’s dictionary payload, preserve existing resource buckets, and distinguish observer failure from application failure. Test a raising rebuilder and a raising event handler.

11. **High — §§3.7–3.8, #510: `/reload` can undo the newly resolved tool policy.**  
    Evidence: [agent_session_runtime.py:1170](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:1170) restores the old active selection and unions **all extension tools** into it. Meanwhile [entry.py:1880](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1880) deliberately constructs reloads without the normal initial tool filter. A factory-only resolver can therefore be overwritten after it runs.  
    **Fix:** define restoration precedence explicitly and run the final saved-selection/CLI-policy intersection after restoration and client MCP attachment. Test saved-off extension tools, CLI exclusions and newly registered tools across reload.

12. **Medium — §3.4: removing “every entry” can remove the source needed to rediscover an off item.**  
    Example: `skills: ["/opt/custom/pdf-tools"]`. Removing that plain source while adding `-pdf-tools` makes the skill disappear on restart, so the panel cannot turn it back on. Evidence: discovery depends on supplied roots in [skills.py:153](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py:153); extensions likewise expand configured sources at `loader.py:842–856`.  
    **Fix:** preserve plain discovery-source entries when editing selection overrides. Specify directory-source behavior and test disable → restart → re-enable for custom skill, prompt and extension paths.

13. **Medium — §§3.1–3.2, §4: `defaultTools` cannot use the generic resource grammar unchanged.**  
    The settled tool grammar allows plain **tool names to replace the default set**. Section 3.1 instead makes every plain entry a source path, and §3.2 defaults every item to on. Per-item `evaluate(...) -> bool | None` does not define the set-wide replacement behavior. Existing tool policy is also set-based ([entry.py:1587](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1587)).  
    **Fix:** specify a dedicated set-level `defaultTools` evaluator and editor, then adapt its result into the shared selection model. Test plain-name replacement, project additive/removal entries, empty lists and CLI precedence.

14. **Medium — §3.5: shared HOME does not mean children see the parent’s saved effective state.**  
    Evidence: [aelix_agents/trust.py:85](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_agents/trust.py:85) supplies `--no-approve` whenever trust-requiring resources exist, including project settings. Both child transports use it. Profile skill paths also become forced `--skill` arguments ([resolver.py:548](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/agents/resolver.py:548)), potentially overriding global off entries; persistent RPC children do not relaunch for each request.  
    **Fix:** state that children share global storage but resolve under their own trust, cwd, profile and launch time. Preserve child denial. Specify refresh behavior for reused children and test both transports with global off, project off and profile-added skills.

15. **Medium — §§3.1–3.2, §4: identity, provenance and path matching need separate fields.**  
    `ResourceItem` has one `key`, one `path` and no scope/base or alias set. That cannot unambiguously represent shadowed same-name skills or all extension identities. Existing entry-point metadata includes name, module and distribution ([loader.py:1374](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py:1374)). Also, resolving an absolute or `../` path against a scope’s base does **not** make it reach only that scope’s files.  
    **Fix:** distinguish stable row ID, invocation identity, match aliases, discovery scope and source base. Explicitly enforce the intended scope restriction for path overrides. Test duplicate names, alias collisions, absolute paths, `..`, symlinks and overlapping CLI/discovered sources.

16. **Medium — §§3.7–3.8, §4: #25 needs contribution-level state and a complete inventory.**  
    Evidence: manifest MCP connections are created once ([entry.py:3404](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:3404)); their tool wrappers are reused on every build (`:1861–1864`). Filtering the extension during reload therefore leaves those tools registered. Existing Installed output also includes refusals and live server status ([extension_manager.py:165](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/tui/extension_manager.py:165)). Boolean saved/applied values and kind-wide `"reload"` timing cannot express these cases.  
    **Fix:** retain pre-filter metadata inventory; add unavailable/refused reasons, owner links and per-contribution restart requirements. On reload, filter disabled owners’ tool exposure while retaining honest “connection restart pending” status. No hot-unload subsystem is required. Test both enabling and disabling manifest MCP contributions.

17. **Medium — §§3.5, 3.8, §8: prompt handling has an omitted CLI case and a stale-holder problem.**  
    Evidence: [entry.py:1529](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1529) supports `--prompt-template`, but the lock contract names only `--skill` and `-e`. An explicit template file is converted to its parent directory, which loads siblings. Prompt templates are then captured once (`:3463`) and reapplied from that unchanged result (`:3770`).  
    **Fix:** define explicit-template filtering/locks and file-versus-directory source identity. Add a mutable prompt inventory refreshed on `/reload`, and test changed content, added/removed sources, sibling templates and scope precedence.

18. **Medium — §1, §§3.4–3.6: RPC is an existing settings consumer/writer that the design misses.**  
    Evidence: [control.py:70](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/control.py:70) exposes skill/prompt paths; `settings/get` invokes getters dynamically (`:437`), and `settings/set` invokes setters dynamically (`:617`). Thus the static grep result is true, but “nothing reads them” is false. RPC also drains prior errors before writing, allowing an already-known load error to become a silently skipped “successful” setter.  
    **Fix:** route these RPC operations through the strict storage/selection service and update their timing metadata. Validate raw array types before getters convert strings into character lists; account for legacy skill-object migration, which currently removes malformed forms silently (`settings_manager.py:556–572`). Add RPC and malformed-value tests.

19. **Medium — §3.4, §8: idempotence does not establish the promised byte-identical double toggle.**  
    Evidence: existing serialization rewrites the complete document with `json.dumps` ([settings_manager.py:948](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/settings_manager.py:948)). Removing/reappending redundant overrides also changes ordering and explicit entries.  
    **Fix:** define the promise as toggling twice **before Enter**, remove that item from the edit delta, and skip the write entirely when no semantic edits remain. Preserve raw unrelated JSON keys. Test exact original bytes, formatting, redundant overrides and no-op Enter without creating a project file.

20. **Medium — §§3.4–3.6, §4: multi-scope saves have no partial-success contract.**  
    `save(..., edits: dict[Scope, ...])` permits both files to change, while `edit_list` locks/writes one scope. A successful global write followed by a failed project write cannot be reported simply as “save failed”; another session or child already sees the global change. Existing storage is strictly per-scope ([storage.py:226](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/storage.py:226)).  
    **Fix:** either restrict one save to one target scope and define scope-switch behavior, or return per-scope committed/failed results and retain only failed edits. Test failure on the second scope and cancellation after the first commit.

21. **Medium — §4, §6: the interactive-tab interface lacks save/close/error lifecycle support.**  
    Evidence: [context.py:1307](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/tui/context.py:1307) owns navigation and close bindings; Enter currently closes the viewer (`:1330`). [overlay.py:334](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/tui/overlay.py:334) unmounts automatically before `show_modal` returns. `render(width)`/`handle(key)` alone does not specify asynchronous save failure, retaining dirty state across tabs, or the promised atomic unmount-and-summary frame.  
    **Fix:** define handled/save/close outcomes, async save ownership, dirty-state lifetime, cancellation and deferred unmount. Test failed Enter remaining open, tab switching with edits, Ctrl-C/Ctrl-J, and close-frame behavior.

22. **Low — §§1, 3.3, 6: several measured descriptions need qualification.**
    - The stated `create → set → flush → drain_errors` malformed-file probe does **not** return an empty list unless the initial error was drained first. `from_storage` records it at [settings_manager.py:418](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-ai/src/aelix_ai/settings/settings_manager.py:418); an in-memory reproduction returned `JSONDecodeError`.
    - Modal height is not always rows − 5. [overlay.py:159](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/tui/overlay.py:159) reserves live input/footer height. Fixed opening height also needs a resize-down policy and tests.
    - `scan_extension_manifests` does not itself dial servers; `entry.py:3406` does.
    - Creating project settings makes trust evaluation applicable; it does not invariably prompt or require `--approve`. Saved decisions, extension votes and global defaults precede prompting ([project_trust.py:888](/Users/handochan/dev/aelix-ai-worktrees/claude-511/packages/aelix-coding-agent/src/aelix_coding_agent/cli/project_trust.py:888)).
    
    **Fix:** correct those descriptions. Add small-height, resize, multiline-footer and pre-existing-trust cases. The reported pty geometry and cache-miss assertion cannot be established solely by reading code.

Claims checked and found correct:

- Settings fields/getter locations and project-list replacement in `deep_merge_settings`.
- Existing setters replace modified lists from snapshots; load-error scopes silently skip enqueue.
- Skill discovery order, absent same-name deduplication, first-match `/skill:` expansion, and the misleading precedence comment.
- Skills/prompts load once today; `/reload` does not rescan them. `/agents use` rescans skills.
- Extension discovery is import-free; extension-contributed resource paths are collected without being loaded.
- Exact command/alias matching, read-only `/skills`, and the listed multiselect limitations.
- Read-tool gating of the catalog; neither setter emits `resources_update`; no production `set_resources` caller exists.
- `_run` snapshots tools separately for each run, while one logical prompt can retry; `wait_for_idle` covers the complete prompt lifecycle.
- ADR-0252’s existing project-settings read/write gates and settings-existence trust predicate.


