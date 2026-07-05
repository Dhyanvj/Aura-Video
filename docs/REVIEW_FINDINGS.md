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

---

## Milestone 3: Approval workflow completion

### [Fixed] Orchestrator tests were writing real project folders into this repo's actual `storage/` directory (medium severity, test-infra only)

**Where:** `test/services/test_orchestrator_state_machine.py` (`TestOrchestratorStateMachine`, `TestOrchestratorResearchWiring`), `test_originality_gate.py` (`TestOriginalityGate`), `test_approval_gate.py` (all three classes).

**What:** These tests swap `db_session.engine` to a temp SQLite file (correct, isolates the DB), but never isolated `utils.storage_dir()` — so any test that reaches a real `project_storage.materialize_project()` call (via the real orchestrator pipeline, `approve_and_publish`, or the new `mark_as_published`) wrote actual `storage/projects/{content-type}/{date}-{slug}-000001/` folders into *this developer's real repo*, not a throwaway location. Since every test's temp DB restarts project IDs at 1, repeated runs kept overwriting/accumulating the same handful of fake folders (`storage/projects/uncategorized/2026-07-05-a-topic-000001/`, etc.) on disk — never committed (`/storage/` is gitignored) but real, silent clutter on the machine running the suite.

**Root cause:** discovered here because Milestone 3 added a second call site (`approve_and_publish`/`mark_as_published`) that also materializes — until then only the render path did, and it was already happening in every Milestone 1/2 orchestrator test without being noticed since nothing was checking `storage/projects/` for pollution.

**Fix:** new `test/services/_test_helpers.py` (`IsolatedStorageDirMixin`) redirects `utils.storage_dir()` to a throwaway `tempfile.mkdtemp()` for the duration of the test, restored in `tearDown`. Applied to every test class that reaches a real `materialize_project()` call. Verified: 2 consecutive full-suite runs left `storage/projects/` untouched (previously recreated every run). The already-polluted folders from before this fix were deleted.

### [Deliberate scope decision] Clip-index bridge is not the DESIGN_V2.md Visual Director

Recorded for traceability, not a defect. `app/services/storyboard.py`'s `ProjectClip` model has no vision score, no timestamps, and no AI-generation escalation — it's the current flat search-terms renderer's clip list made addressable, per the option the user explicitly chose in docs/DECISIONS_V3.md §4. The full vision-scored Visual Director from docs/DESIGN_V2.md remains unbuilt and out of scope for this pass.
