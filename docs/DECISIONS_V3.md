# Aura-Video v3 — Decision Memo

Status: **APPROVED** (2026-07-05), **all 7 milestones implemented and committed** (2026-07-06). §4's open question resolved: option 1 (lightweight clip-index bridge), confirmed by user. Rest of the memo approved as written. This is the memo required by the v3 brief (§0.1): storage layout, dedupe approach, learning-loop design, and provider picks with a cost table. Short by design — the full write-up for each area happens in code + tests, not here. See docs/REVIEW_FINDINGS.md for every deliberate scope decision and bug caught during implementation.

---

## 0. Baseline: what already exists (read before deciding anything below)

This isn't a greenfield ask. `docs/DESIGN_V2.md` was already approved and is **partially implemented**:

| v2 milestone | Status |
|---|---|
| M1: Series Bible, `ContentTypeTemplate`, New Video flow, publishing freeze | **Shipped** (`c4ccc7b`) |
| M6 (partial): Research layer — `Researcher` agent, Anthropic web search, per-type verification | **Shipped** (`ea0a6d3`, `478a515`) |
| M2–M5: Scene-based data model, Visual Director (stock candidate scoring + vision), ASS karaoke subtitles, AI-image + Ken Burns | **Not built.** No `ScenePlan`, no `VisualDirector`, no `ass_subtitle.py` anywhere in the tree. |
| M7: QA v2 (fact-check gate, pacing, loudness, continuity checks) | **Partially shipped** — fact-check-vs-dossier and quote-attribution checks exist in `quality_reviewer.py`; pacing/loudness/deterministic subtitle-sync checks do not. |

Concretely, today's render pipeline is still **flat search-terms → round-robin/shuffled stock clips**, not scene-aware. There is no per-scene asset record anywhere to point a "storyboard" UI at.

**Why this matters for v3 §4:** your ask for a storyboard strip where clicking a scene shows the Visual Director's scored candidates assumes the scene-based pipeline exists. It doesn't. I'm not going to silently build a fake storyboard on top of nothing, and I'm not going to smuggle all of v2's remaining milestones (M2–M5, a multi-week vision-scoring rewrite of the renderer) into v3 without you deciding that's what you want. See §4 below for the scoped alternative I'm proposing instead, and the explicit sign-off this needs.

Everything else in this memo assumes the rest of the current architecture as-is: 18-state `ProjectStatus` orchestrator, `VideoProject`/`Series`/`ContentTypeTemplate`/`AgentEvent` tables, SQLite with hand-rolled additive migrations (no Alembic), plain `unittest`, `publishing_enabled=false` gate already wired and left untouched.

---

## 1. Storage architecture v2

**Layout:** `storage/projects/{content-type}/{YYYY-MM-DD}-{slug}-{shortid}/` exactly as specified, containing `project.json`, `script.md`, `transcript.txt`, `voice.mp3`, `subtitles.srt`, `final-video.mp4`, `thumbnail.png` (+ `candidates/`), `title.txt`/`description.txt`/`tags.json`, `research.md`, `scene-plan.json` (see §4 note on what this holds today), `assets/`, `revisions/`.

**Deviation from your original spec (Pending/Approved/Published subfolders with physical moves): rejected, as you invited me to reconsider.** Reasoning already matches yours almost exactly — physical moves duplicate state that lives in the DB, race with open file handles/streaming, and risk partial-move corruption on crash. Implementing as: project folder path is stable for the project's life; `VideoProject.status` in the DB is the source of truth and is mirrored into `project.json` on every transition; the UI provides status-filtered views. `scripts/export_by_status.py` (symlink tree) ships as an optional convenience, not the canonical structure.

**Mechanics (this is the part your spec didn't fully specify, so I'm deciding it now):**

- `task.py`'s internal render mechanics (`storage/tasks/{task_id}/`: `temp-clip-N.mp4`, `combined-N.mp4`, ffmpeg concat list) **stay exactly as they are.** This is working, tested, tightly-coupled code (`test_task.py`, `test_video.py`) and the legacy non-Agent-Studio `POST /videos` API depends on it verbatim. Touching it is unjustified risk for a storage-layout change.
- A new `app/services/project_storage.py` **materializes** the project folder after a render succeeds: moves the canonical outputs (`audio.mp3`→`voice.mp3`, `subtitle.srt`→`subtitles.srt`, `final-{n}.mp4`→`final-video.mp4`, thumbnail candidates) out of the task scratch dir, and **writes new files that don't exist today** — `script.md` (from `CreativeBrief`), `transcript.txt` (from word timestamps), `title.txt`/`description.txt`/`tags.json` (from `PublishPackage`), `research.md` (from `ResearchDossier`), `project.json` (manifest: status, timestamps, `cost_usd`, `series_id`, version/approval history).
- `VideoProject` gains one new nullable column: `storage_path` (the relative folder path). **This is the dual-path serving trick that satisfies your "old project must still open" requirement without an all-or-nothing cutover:** if `storage_path` is set, new project-scoped routes serve from it; if null (pre-migration or legacy task-only rows), the existing `/stream/{file_path}` and `/download/{file_path}` task-id routes keep working exactly as today, untouched.
- New routes `GET /projects/{id}/files/{filename:path}` reuse `file_security.resolve_path_within_directory()` verbatim, anchored at that project's own folder (not a single global root) — same security primitive, narrower base directory. `TestSecurityControls` gets new tests for this route (traversal, symlink escape, wrong-project access via manipulated filename) alongside the existing suite, which is left green and unmodified.
- **Revisions:** before any canonical file is overwritten (QA revision loop, or a per-scene re-render, §4), the current version is moved into `revisions/{iso-timestamp}/` first. Never deleted.
- **shortid**: `f"{project.id:06x}"` — deterministic from the DB primary key, no new random state to track. **slug**: ASCII-slugified topic (fallback to `content-type-id` if empty/non-Latin after stripping).

**Migration (`scripts/migrate_storage_v2.py`):** `--dry-run` (default) prints the planned folder name + file moves per `VideoProject` row with a `task_id` and no `storage_path`, without touching disk or DB. `--apply` executes it, **copies** (not moves) canonical files into the new folder and backfills `storage_path`; the original `storage/tasks/{task_id}/` is left in place untouched (cheap insurance against a botched one-time migration of production data; a later `--prune` pass can reclaim the space once you've spot-checked results — not built in this pass). Legacy task-only renders (no `VideoProject` row at all, from the original MoneyPrinterTurbo API) are out of scope for migration by design — they were never part of the Agent Studio and keep working exactly as today via the untouched task-id routes.

---

## 2. Originality engine

**Embeddings:** `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim, CPU, ~90MB, free/local — one new dependency). New table `TopicEmbedding(project_id, content_type_id, series_id, niche, text, embedding JSON, created_at)`. Brute-force cosine at this scale (low hundreds of rows) — no FAISS/vector-DB needed, confirmed appropriate per your own framing.

**Flow at project creation:**
1. Embed `topic + hook + one-line angle`.
2. Compare against all prior embeddings for the same content type + niche (add `series_id` scoping too, consistent with the existing per-type/per-series dedupe split in `orchestrator._recent_topics`).
3. **Similarity > 0.92 (high band):** auto-reject, log an `AgentEvent(type="rejection", agent="trend_scout"|"researcher", payload={matched_project_id, similarity})`, and force regeneration of a different concept. This replaces (doesn't just supplement) the current exact-string `_RECENT_TOPICS_LIMIT=30` check, which stays as a cheap pre-filter before the embedding call, not as the final gate.
4. **0.80–0.92 (borderline band):** one Haiku-tier call, given the two summaries side by side, judges "same idea or genuinely new perspective" — cheap, per your instruction.
5. **< 0.80:** passes.

Thresholds are defaults to tune after real data, not hard commitments — flagged as such, not silently hardcoded without telling you.

**Per-type rules**, layered on top of the general embedding check:
- **Fun Facts:** fingerprint the specific fact (normalize + hash the core claim, not the topic string) in a `UsedFact(content_type_id, fact_hash, project_id)` table — a fact is used once, ever, regardless of embedding similarity band.
- **Motivational:** fingerprint the quote text itself the same way (once, ever); a lesson *theme* may repeat only if the borderline-band Claude check confirms a new angle.
- **AI/World News:** same-story-with-meaningful-update allowed — compare entities+dates extracted from `ResearchDossier` (already structured data, no new extraction work) against prior coverage of the same story; the new script must state what changed.
- **Trending:** trend timestamp must be newer than any prior coverage of that trend (reuses `TrendReport` data already collected).

**Hook/variety tracking:** `VideoProject` gains `hook_pattern` (enum: question/bold-claim/statistic/story-cold-open/...) and `opening_line`, populated by Creative Director's existing self-tagging at script time. Creative Director's prompt receives the last 10 used patterns for that content type. A lightweight n-gram overlap check (script vs. last 5 scripts of that type) triggers one revision if too similar — implemented as a deterministic check inside Quality Reviewer (same place `fact_check_flags` already lives), not a new agent.

---

## 3. Continuous learning loop

**Per-project retrospective:** one Haiku-tier Claude call at `AWAITING_HUMAN_APPROVAL`→terminal transition, reading QA reports, revision reasons, and (**new capture, doesn't exist today** — confirmed by direct grep, DESIGN_V2.md only planned it) a `human_edits: List[{field, before, after}]` diff captured when you edit metadata/script at Final Review. Output: `LessonLearned(project_id, agent, content_type, what_worked, what_failed, actionable_rule)` rows.

**Distillation (not raw injection — matches your bounded-growth requirement):** every 10 projects or weekly (whichever first), a separate Claude call curates accumulated `LessonLearned` rows into a `Playbook(agent, content_type, version, bullets JSON[≤15], created_at)` — a new versioned row per distillation, not an in-place edit, so rollback is "point at the previous version row," not a diff-revert.

- Injected into that agent's system prompt at call time (active version only).
- Settings UI: view/edit/disable individual bullets, full version history with one-click rollback (= set `active_version_id` back).
- A bullet whose presence correlates with worse average QA scores in the following 10 projects is flagged (not auto-deleted) for review at the next distillation pass — deletion is a human decision via the Settings UI, keeping this bounded and non-self-modifying per your explicit constraint.

---

## 4. Approval workflow — the storyboard/per-scene gap (needs your explicit sign-off)

Given §0: today there is no scene-level asset record to build a real storyboard against. Two honest options:

1. **Scope per-scene replacement to a lightweight "clip index" bridge** — record each downloaded stock clip already used in the current flat pipeline as an indexed, addressable unit (`ProjectClip(project_id, index, search_term, source, source_id, local_path, start_ms, end_ms)`, derived from data `material.py`/`task.py` already compute but don't currently persist). Final Review shows these as a storyboard strip; clicking one lets you search a replacement stock clip and trigger a **targeted re-render** (re-run only the assembly/concat step with the new clip swapped in, not the full pipeline). This ships in v3 Milestone 3, is additive, and is honest that it's clip-swapping, not the AI-scored Visual Director from DESIGN_V2.md.
2. **Pull the full DESIGN_V2.md M2–M5 (scene data model, vision-scored Visual Director, Ken Burns, ASS subtitles) forward into v3** — real "candidate assets the Visual Director scored" as originally envisioned, at the cost of a materially larger and slower v3 (this is 4 of v2's own 8 milestones, not a small add-on).

**My recommendation: option 1.** It satisfies the concrete Definition-of-Done line ("swap one scene's clip from the storyboard, re-render") without silently absorbing an unrelated, unapproved multi-week rewrite of the renderer into this pass. The full Visual Director stays a clearly-labeled future milestone, picked up separately once you want it. **If you want option 2 instead, say so when approving this memo** — I'm not deciding that trade-off for you.

**Mark as Published:** reuses existing `VideoProject.published_posts` (JSON list, already exists, already read by `PerformanceAnalyst`) and `published_at` (already exists) — no new columns needed. New endpoint `POST /projects/{id}/mark-published` accepts optional `{platform, url}` entries, sets `status=PUBLISHED` directly (skipping the unused `PUBLISHING`/`TRACKING` automation states, since no automated posting happened), appends to `published_posts` with `source="manual"`. Performance Analyst's existing checkpoint logic is verified (not assumed) to handle manually-sourced URLs the same as `upload_post`-sourced ones during implementation.

**Reject → editing, never deletes:** already effectively true at the DB/status level (`retry_with_revision` sends back to `SCRIPTING`); the gap is purely that the prior render wasn't preserved on disk. Closed by §1's `revisions/` mechanism.

---

## 5. UI v3

No new decisions needed beyond what's in the brief — Dashboard, Pipeline (renamed Kanban), global search/filters, dark/light mode (Tailwind `dark:` variants + a persisted toggle, since the palette is currently hardcoded dark-only with zero theme infrastructure per direct inspection), mobile-first Final Review. Flagging one thing found during research, not a decision: `ProjectDetail.tsx`'s video player uses a fixed `max-h-[480px]`, not aspect-ratio-locked — this needs to change regardless of the rest of Milestone 4, tracked in `REVIEW_FINDINGS.md` as a cheap fix.

---

## 6. Free/low-cost provider strategy

| Purpose | Pick | Free tier / cost | Status today | Notes |
|---|---|---|---|---|
| News/research (general) | Anthropic web search tool | Paid-but-cheap, already on the agent's API path | **Already wired** (`Researcher`, `ea0a6d3`) | No change; confirmed already the right call |
| News/research (AI News supplement) | RSS: Hacker News Algolia API, arXiv new-submissions, TechCrunch/Verge/Ars feeds | Free, no key | Not integrated | Add as a supplementary freshness signal, per DESIGN_V2.md's own (unimplemented) recommendation |
| News/research (World News supplement) | GDELT | Free | Not integrated | Same rationale |
| News (NewsAPI-class) | — | Free tier non-commercial only; paid ~$449/mo+ | N/A | **Recommend against**, as DESIGN_V2.md already concluded — web search + RSS covers this |
| Fact-checking | Google Fact Check Tools API | Free | Not integrated | Add as a signal alongside existing ≥2-source verification, not a replacement |
| TTS — Budget | Edge TTS | Free | **Already default** | Fragile (documented in config comments already); keep as zero-config default |
| TTS — Budget (self-hosted alt.) | Kokoro (via an OpenAI-compatible server, e.g. kokoro-fastapi) | Free, local compute only | Not integrated | Reuses the **existing** Chatterbox generic OpenAI-compatible adapter pattern almost unchanged — new config preset, not a new provider class |
| TTS — Standard | Azure Cognitive V2 | ~$0.01/video | **Already implemented**, unused as a default | Config-default change only, per DESIGN_V2.md's own finding |
| TTS — Cinematic | ElevenLabs | ~$0.15–0.30/video | **Already implemented** | No change |
| STT / word timestamps | faster-whisper | Free, local | **Already the standard path** | No change needed — confirmed via direct read of `subtitle.py` |
| Stock media | Pexels (primary), Pixabay, Coverr (landscape-biased) | All free | **Already integrated**, all three | No change; quality notes already documented in `config.example.toml` |
| AI images — Budget | Pollinations image endpoint | Free, no key | Text-only today; image endpoint **not** integrated | New: opt-in per content type (reuses `ContentTypeTemplate.visual_strategy.ai_gen_allowed`, which already exists as a field but is unread by any code today) |
| AI images — Cinematic | Flux (fal.ai) | ~$0.003–0.02/image | Not integrated | Opt-in, requires a key; graceful no-op if unconfigured |
| Trends | YouTube Data API + pytrends | Free (quota-limited) / free (unofficial) | **Already integrated**, both | No change |

**Est. cost/video after these picks:** Budget ≈ $0.02–0.05 (Edge/Kokoro + stock + Anthropic-agent-calls-only), Standard ≈ $0.15–0.25 (Azure V2 + stock, occasional Pollinations image), Cinematic ≈ $0.40–0.70 (ElevenLabs + stock + paid AI images) — **planning estimates**, to be measured against real runs before locking budget caps, same honesty caveat DESIGN_V2.md used.

**Rule enforcement:** every provider above already degrades gracefully today (missing key → stock-only / Edge fallback, silent-single-point-of-failure explicitly avoided) except the two genuinely new integrations (Pollinations image, RSS/GDELT/Fact-Check), which get the same treatment: missing/failing → visible UI notice, never a hard crash, never silently skipped without a trace.

---

## 7. Milestone order (confirming §9 of the brief, adjusted per §4 above)

1. Storage v2 + migration + path-guard tests
2. Originality engine (embeddings, per-type rules, hook variety, script-repetition check)
3. Approval workflow completion — **per the scoped clip-index bridge in §4, option 1**, + Mark-as-Published
4. UI v3 (Dashboard, search/filters, light mode, mobile-first Final Review)
5. Learning loop (retrospectives, playbooks + Settings editor with versioning)
6. Provider integrations per the table in §6
7. Final pass: `docs/REVIEW_FINDINGS.md` closed out, full suite green

One commit per milestone, why-focused messages, app runnable after each — matching how M1–M8 and the v2 milestones already in this repo were done.

---

## Approval record

Approved 2026-07-05: §4 option 1 (lightweight clip-index bridge) confirmed; rest of memo approved as written. Implementation proceeds in the milestone order in §7.
