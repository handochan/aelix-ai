# Extension settings handoff — 2026-10-08

Base: origin/main 28dbcaf4a1a9a97e46d88cec4b90c1ddeb486321. Branch feat/extension-settings.
Issue #403 / PR #404, Project 1 In review. ADR-0256 and committed docs/verification/extension-settings-2026-10-08.md are the durable contract/evidence.

Implemented generic register_setting with owner persistence and guarded sync/async callbacks, live runner aggregation/menu rereading, label uniqueness and failed-lazy-activation rollback. Memory adapter on feat/global-memory-settings uses it optionally; global usage keeps knowledge project-isolated.

Gates: affected host TUI/extensions/harness/packaging/docs suite 2710 passed on integrated main; full ruff lint pass; check_types.py 287 files/zero errors/3 inverse assertions pass. Real PTY actual uv run aelix and installed Memory0.2.0 wheel passed OFF→ON and fresh other-project ON→OFF. Memory installed-wheel real semantic suite84 passed; live model9 fresh processes with isolated synthetic facts passed.

Delivery: host PR404; memory PR8 at b2080e4 passes18 core/installed-host OS/Python jobs and pins the original behaviorally identical bab77b2 host API implementation; catalog PR8 pins b2080e4 and passes parser/candidate CI. Host full CI caught2 metadata gates after13403 passes on Ubuntu3.12: add the ADR0256 authorization for the generic runner to the kernel allowlist and rederive53 shifted citations using the exact-anchor tool. Both reproduced first and repaired;64 combined kernel/citation/lazy/settings/UI/docs tests pass, lint/types remain clean. Remaining: confirm the rerun of full host CI on final corrected head, record independent review, and keep merge as an explicit authorization boundary for this new host change. Recheck all remote heads.

Do not repeat: NO_COLOR=1 breaks existing colour assertions; a memory entry point installed in the host unit-test venv adds unexpected discovery results; missing venv pip makes scaffold tests choose Python3.9. Keep installed-wheel integration separate. Host CI intentionally does not enforce whole-repo ruff format; preserve baseline manual layouts. No user memory/credentials/model weights/transcripts were committed. Original user/parallel-agent checkouts were not changed.
