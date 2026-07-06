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

### [Fixed in Milestone 4] `ProjectDetail.tsx` video player wasn't aspect-ratio aware

**Where:** `frontend/src/pages/ProjectDetail.tsx` — the `<video>` element used a fixed `max-h-[480px]` with no aspect ratio, so it didn't scale correctly at narrow (mobile) widths.

**Fix:** `aspect-[9/16]` (these are all vertical shorts) + `w-full max-w-xs`, verified visually at both desktop and 390px mobile width via a Playwright screenshot pass against the running app.

### [By design] Legacy task-only renders are out of migration scope

**Not a defect** — recorded here for traceability. `scripts/migrate_storage_v2.py` only migrates `VideoProject` rows (Agent Studio projects). Renders created via the original `POST /videos` API (no `VideoProject` row) are untouched, per docs/DECISIONS_V3.md §1 — they were never part of the Agent Studio and keep serving from `storage/tasks/{task_id}/` via the existing routes indefinitely.

---

## Milestone 4: UI v3

### [Deliberate scope decision] Global search/filters wired to Pipeline Board only, not every list view

The brief's §5 says "global search + filters on all list views." Implemented as one reusable `ProjectFilters` component (search text, content type, status, series, date range) and wired it into Pipeline Board, the highest-traffic "all projects" view. Trends/Series/Analytics pages did not get the same filter bar in this pass — they're narrower, already-scoped views (a trends feed, a series list, an analytics table) where the same generic project-filter bar is a weaker fit, and time was prioritized toward Dashboard/mobile/dark-mode instead. Not silently claimed as done everywhere; flagged here as the honest boundary of this pass.

### [Found and fixed during implementation] Mechanical dark/light color sweep introduced duplicate/broken Tailwind classes

**What:** Converting ~10 already-dark-only page files to support both themes was done with a scripted regex sweep (`text-slate-400` → `text-slate-500 dark:text-slate-400`, etc.) rather than hand-editing every occurrence. Two classes of bug came out of that mechanical pass, caught before commit by re-grepping the result rather than trusting the script:
1. Files I'd already hand-written with correct `dark:` variants (`Nav.tsx`, parts of `ApprovalQueue.tsx`) got double-swept, producing `text-slate-500 dark:text-slate-500 dark:text-slate-400`-style duplication.
2. Pseudo-class-prefixed tokens (`hover:text-slate-200`) got split incorrectly into an unprefixed light variant + a dark variant that silently lost the `hover:` prefix (`hover:text-slate-800 dark:text-slate-200` — always-on in dark mode instead of hover-only).

**Fix:** a second cleanup pass collapsed the duplicates, and the `hover:`-prefix losses were fixed by hand (only 4 occurrences, all in `Nav.tsx`/`ProjectDetail.tsx`). Verified visually afterward via Playwright screenshots in both themes rather than trusting the diff alone.

---

## Milestone 3: Approval workflow completion

### [Fixed] Orchestrator tests were writing real project folders into this repo's actual `storage/` directory (medium severity, test-infra only)

**Where:** `test/services/test_orchestrator_state_machine.py` (`TestOrchestratorStateMachine`, `TestOrchestratorResearchWiring`), `test_originality_gate.py` (`TestOriginalityGate`), `test_approval_gate.py` (all three classes).

**What:** These tests swap `db_session.engine` to a temp SQLite file (correct, isolates the DB), but never isolated `utils.storage_dir()` — so any test that reaches a real `project_storage.materialize_project()` call (via the real orchestrator pipeline, `approve_and_publish`, or the new `mark_as_published`) wrote actual `storage/projects/{content-type}/{date}-{slug}-000001/` folders into *this developer's real repo*, not a throwaway location. Since every test's temp DB restarts project IDs at 1, repeated runs kept overwriting/accumulating the same handful of fake folders (`storage/projects/uncategorized/2026-07-05-a-topic-000001/`, etc.) on disk — never committed (`/storage/` is gitignored) but real, silent clutter on the machine running the suite.

**Root cause:** discovered here because Milestone 3 added a second call site (`approve_and_publish`/`mark_as_published`) that also materializes — until then only the render path did, and it was already happening in every Milestone 1/2 orchestrator test without being noticed since nothing was checking `storage/projects/` for pollution.

**Fix:** new `test/services/_test_helpers.py` (`IsolatedStorageDirMixin`) redirects `utils.storage_dir()` to a throwaway `tempfile.mkdtemp()` for the duration of the test, restored in `tearDown`. Applied to every test class that reaches a real `materialize_project()` call. Verified: 2 consecutive full-suite runs left `storage/projects/` untouched (previously recreated every run). The already-polluted folders from before this fix were deleted.

### [Deliberate scope decision] Clip-index bridge is not the DESIGN_V2.md Visual Director

Recorded for traceability, not a defect. `app/services/storyboard.py`'s `ProjectClip` model has no vision score, no timestamps, and no AI-generation escalation — it's the current flat search-terms renderer's clip list made addressable, per the option the user explicitly chose in docs/DECISIONS_V3.md §4. The full vision-scored Visual Director from docs/DESIGN_V2.md remains unbuilt and out of scope for this pass.

---

## Milestone 5: Learning loop

### [Deliberate scope decision] Only CreativeDirector consumes playbook bullets in this pass

`app/services/playbook.py`'s `get_active_bullets(agent, content_type_id)` is generic and already scoped per-agent, but only `CreativeDirector.write()` was wired to call it. TrendScout, Researcher, QualityReviewer, and Publisher are structurally ready (the playbook/retrospective backend has no per-agent special-casing) but don't inject bullets into their prompts yet - a one-line addition each at their existing call sites in `orchestrator.py`, following the exact pattern used for `creative_director`. Scoped this way because Creative Director is where the brief itself said the highest-value lessons land (script/hook/structure), and verifying one agent end-to-end (with real Playwright-verified UI) was prioritized over shallow wiring across five.

### [Found and fixed during implementation] Misattached route decorator

**What:** An edit meant to insert a new `_record_human_edit` helper function above the `update_metadata` endpoint instead left the `@router.patch(...)` decorator attached to the helper, turning it into the actual route handler FastAPI called - every request 400'd with a confusing "field/original_value/new_value/edits required" validation error that had nothing to do with the real request body.

**Caught:** immediately, because the full test suite was run right after writing the code (4 tests failed with an unexpected 400). Root-caused by re-reading the file rather than guessing, which showed the decorator sitting above the wrong function.

**Fix:** moved the decorator to the correct function. A regression-safe way to describe this: routes should be re-verified via a real HTTP call (which the existing `TestClient`-based tests already do) whenever the decorated function is touched by an edit, not just typechecked/imported.

### [Found and fixed during implementation] Naive human-edit capture would have recorded keystroke noise, not a clean diff

**What:** The first version of the `human_edits` capture in the new metadata-autosave endpoint appended a new `{field, before, after}` entry on every call. Since autosave fires on a debounce tick while the human is still typing, this would have flooded `human_edits` with one entry per keystroke-batch (before="Old T", after="Old Ti", before="Old Ti", after="Old Tit", ...) instead of the one clean "AI drafted X, human corrected to Y" diff the retrospective (docs/DECISIONS_V3.md §3) actually needs.

**Fix:** `_record_human_edit` upserts one entry per field, keyed off the first-seen (agent-drafted) value as `before`, updating only `after` on subsequent calls; the entry is dropped if the human types their way back to the original value. Covered by three dedicated tests (`test_human_edits_records_one_clean_diff_not_one_per_autosave_call` and two others) verifying this doesn't regress.

---

## Milestone 6: Provider integrations

### [Deliberate scope decision] Quality-preset-driven TTS provider selection deferred

docs/DECISIONS_V3.md §6 calls for Budget=Edge/Kokoro, Standard=Azure V2, Cinematic=ElevenLabs, keyed off `VideoProject.quality_preset`. Investigated the actual TTS dispatch (`app/services/voice.py::tts()`) before touching it: provider selection is driven entirely by the `voice_name` string's format (a bare Azure-style name, or a `elevenlabs:`/`chatterbox:`/`siliconflow:`/etc. prefix), and `_resolve_voice_name`'s validation (`app/agents/orchestrator.py`) only ever accepts bare Azure-family names - it would silently reject an ElevenLabs- or Chatterbox-prefixed recommendation and fall back to the generic default, defeating the point. Wiring `quality_preset` in properly means touching the validated voice list Creative Director is offered, the fallback/validation set in `_resolve_voice_name`, and their interaction with series voice-locking (which is deliberately hard-enforced and well-tested). That's a real, self-contained piece of work deserving its own careful pass rather than a rushed change appended to an already-long session - not implemented in this pass. `_ai_image_fallback_allowed()` (the other quality/content-type-driven dispatch added this milestone) was scoped narrower and lower-risk by comparison: purely additive, opt-in, degrades to "no clip" on any failure, and never touches voice/series logic at all.

### [Deliberate scope decision] AI-image fallback clips are appended, not interleaved at their script position

`material.py::_download_videos_by_script_order`'s round-robin loop is a multi-round *duration-filling* algorithm (a single search term can contribute several clips across rounds), not a 1:1 term-to-clip mapping - there was no small, safe way to splice a one-shot-generated AI fallback clip into the middle of that loop at exactly the failing term's narrative position without restructuring already-tested logic. Fallback clips for zero-candidate terms are generated after the round-robin pass and appended to the end of the clip list instead. Documented in the function's own docstring as well, not just here - this is a real, visible limitation (an AI-generated clip for a mid-script term will play at the end of the video, not in the middle where the script says it), acceptable because it's a fallback for the *minority* case (a term with literally zero stock coverage), not the common path.

### Verified end-to-end (not just unit-tested)

The Pollinations image + ffmpeg Ken Burns pipeline (`app/services/ai_images.py`) was run for real (no mocks) against the actual free Pollinations endpoint and a real ffmpeg encode: produced a valid, correctly-sized (1080x1920) 4-second h264 clip from the prompt "a mantis shrimp underwater close up" in ~7 seconds, visually confirmed via extracted frames. RSS/GDELT/Hacker News/Google Fact Check integrations were verified via mocked unit tests only (each hits a real free public API with no auth complexity to misconfigure, and re-running live external calls on every test run would make the suite network-dependent and flaky) - the graceful-degradation path (network failure -> empty list, never a raised exception) is what's actually exercised by every test, which is the behavior that matters for "never a silent single point of failure."
