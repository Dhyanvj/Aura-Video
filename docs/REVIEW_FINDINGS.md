# Aura-Video v3 — Running Review Findings

Anything weak found in architecture, performance, security, or code quality while implementing docs/DECISIONS_V3.md. Cheap wins are fixed on sight; structural changes are flagged here for a later pass rather than bundled silently into an unrelated milestone.

---

## Milestone 1: Storage v2

### [Fixed] Daemon-thread/temp-DB race corrupting unrelated tests (medium severity, test-infra only)

**Where:** `test/services/test_orchestrator_state_machine.py`, `test_approval_gate.py`, `test_content_types.py`, `test_producer.py`, `test_series.py` (6 files, same pattern).

**What:** Every test in these files spins up a temp SQLite file, points `db_session.engine` at it, runs an orchestrator pipeline (which fires off a `daemon=True` background thread), waits for a terminal DB status, then in `tearDown()` swaps `db_session.engine` back and calls `os.remove(self._db_path)`. `_wait_for_status` only guarantees the DB row reached a terminal status - it does not guarantee the background thread has fully returned. A straggling thread that reconnects to the now-deleted path after `tearDown` causes SQLite to transparently create a brand-new, empty, tableless file at that same path; the straggler's next query then fails with `no such table`/`no such column`, and - worse - if the *next* test's `setUp` hasn't yet repointed `db_session.engine` at its own fresh temp file, the straggler can write into (or be read as an error against) a different test's project row, since SQLite autoincrement restarts at `id=1` for every fresh temp DB.

**Root cause:** pre-existing test-isolation gap (threads aren't joined before teardown); not introduced by this pass. It became reliably reproducible once `_materialize_project_storage()` added real synchronous file I/O to the pipeline thread, widening the race window.

**Fix:** stopped deleting the temp DB file in `tearDown()` (kept the `engine` swap-back). A straggling thread now writes into an orphaned-but-schema-intact file nobody reads anymore, instead of a silently-recreated empty one. Confirmed: 3 consecutive full-suite runs, no flakiness (previously reproduced a spurious failure roughly 1 in 3 runs).

**Deferred:** the actual fix (giving orchestrator's fire-and-forget pipeline threads a handle tests can `join()`) would be the more correct long-term solution, but touches production orchestration code for a test-only problem — out of scope for this pass.

### [Found, not fixed] `ProjectDetail.tsx` video player isn't aspect-ratio aware

**Where:** `frontend/src/pages/ProjectDetail.tsx` — the `<video>` element uses a fixed `max-h-[480px]`.

**Severity:** low (cosmetic/UX, not correctness or security).

**Deferred to:** v3 Milestone 4 (UI v3), which already covers Final Review/mobile responsiveness. Noted here per the running-findings requirement so it doesn't get lost.

### [By design] Legacy task-only renders are out of migration scope

**Not a defect** — recorded here for traceability. `scripts/migrate_storage_v2.py` only migrates `VideoProject` rows (Agent Studio projects). Renders created via the original `POST /videos` API (no `VideoProject` row) are untouched, per docs/DECISIONS_V3.md §1 — they were never part of the Agent Studio and keep serving from `storage/tasks/{task_id}/` via the existing routes indefinitely.
