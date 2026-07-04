# Aura-Video v2 — Quality-First Redesign

Status: **DRAFT — awaiting approval**. No implementation code has been written. This document is Phase A only.

Mission shift: stop optimizing for "a video gets made" and start optimizing for "the video is worth watching." Success metrics become retention, visual relevance, narrative craft, and episode-to-episode consistency. Publishing stays frozen behind a feature flag for the entire duration of this redesign.

---

## 0. How to read this document

Each numbered section mirrors the brief you gave me (§2.1–§2.10, §3, §4). Inside each section I:
1. State the design.
2. Give schemas where asked.
3. Flag where I pushed back on the brief and why.
4. Call out open decisions that need your sign-off (collected again in §11).

Where the brief left something unspecified, I chose the option that most improves **final video quality per dollar** and recorded the rationale inline, per your instruction.

---

## 1. Current system — verified facts (baseline for everything below)

I re-read the code rather than trusting the brief's summary. It matches, with a few corrections/additions worth flagging because v2 depends on them:

- **`ProjectStatus` has 16 values today**, not a simple linear chain: `IDEA_PENDING → IDEA_READY → SCRIPTING → SCRIPT_READY → PRODUCING → RENDERED → QA_REVIEW → {QA_PASSED | FAILED}`, then `AWAITING_HUMAN_APPROVAL → APPROVED → PUBLISHING → PUBLISHED → TRACKING → ARCHIVED`, plus terminal `FAILED`/`REJECTED`. Revision loops send `QA_REVIEW` back to `SCRIPTING` (`revision_count`, capped by `config.agents.max_revisions=2`). Crash recovery resumes any project sitting in `IDEA_READY…QA_REVIEW`.
- **Only one DB table holds project state today**: `VideoProject` (21 fields, JSON blobs for `trend_report`/`brief`/`video_params`/`qa_reports`/`publish_package`/`published_posts`/`analytics`) plus `AgentEvent` (audit log with `tokens_in/out`, `cost_usd`). No `Series` table exists yet.
- **Cost tracking already exists** and is per-call: `app/agents/base.py` prices `claude-opus-4-8` / `claude-sonnet-5` / `claude-haiku-4-5` per-million-token, accumulates onto `VideoProject.cost_usd`, and `app/services/budget.py` sums month-to-date spend for a scheduler gate. v2 extends this table with image/video-gen spend — no new tracking mechanism needed, just new event rows.
- **The render pipeline (`app/services/task.py::start`) is a strict sequential 6-step pipeline**: script → terms → TTS (`voice.py`, 8 providers) → subtitles (Edge word-timing aggregation or Whisper+Levenshtein correction) → stock materials (`material.py`: Pexels/Pixabay/Coverr, with a `match_materials_to_script` round-robin mode that already exists and partially anticipates scene-ordering) → per-clip temp MP4 + single ffmpeg concat demuxer pass (`video.py`) with hardware-codec-then-libx264 fallback. This concat architecture is worth preserving — it's already efficient — we're changing *what a "clip" is*, not the assembly mechanism.
- **The agent framework is a clean base class** (`BaseAgent`) that all LLM agents inherit for `call_json()` + cost logging. Producer is the one non-LLM agent (thread-runs `task.py`, mirrors progress at 10/20/30/40/50/100% thresholds into `AgentEvent` rows). This pattern extends cleanly to new agents.
- **Frontend is a Vite+React+TS SPA** built to `resource/public`, single FastAPI process, WebSocket-with-polling-fallback (`ws.ts`) for live updates. Pages, nav, API client, and Tailwind dark palette are all in place and easy to extend — new pages are additive, not a rewrite.
- **Test suite**: plain `unittest`, temp-SQLite-per-test, integration tests gated on `MPT_RUN_INTEGRATION_TESTS`. No pytest anywhere. This constrains how I write new tests (§10).
- **`webui/.streamlit/config.toml`** is confirmed dead — one file, no app code behind it. Deleting it is a zero-risk cleanup in Milestone 1.
- **The approval gate is currently hardcoded**, not flag-gated (`orchestrator.py` checks `status == AWAITING_HUMAN_APPROVAL` directly before allowing publish). There is no `[features]` config section today. Freezing publishing means *adding* this section and threading a check through `Publisher.publish()` and the scheduler's auto-upload path — not deleting anything.

---

## 2.1 Core architectural change: scene-based pipeline

### The problem with today's model
`generate_terms()` produces one flat bag of 6-10 search terms for the whole script; `material.py` either shuffles them randomly or (if `match_materials_to_script=True`) round-robins them into rough narrative order. There is no concept of a "scene" with a duration, a purpose, or a matched asset — visuals are decoupled from meaning. This is the single biggest lever on quality, which is why the brief marks it §2.1 and I'm treating the Visual Director (§2.4) as the highest-priority new agent.

### `ScenePlan` schema

```python
class SceneAssetStrategy(str, Enum):
    STOCK = "stock"
    AI_IMAGE = "ai_image"       # + Ken Burns motion
    AI_VIDEO = "ai_video"

class SceneMotion(str, Enum):
    STATIC = "static"
    PAN = "pan"
    ZOOM = "zoom"

class SceneTransition(str, Enum):
    CUT = "cut"
    FADE = "fade"
    SLIDE = "slide"
    ZOOM_THROUGH = "zoom_through"

class SceneAsset(BaseModel):
    strategy: SceneAssetStrategy
    query_or_prompt: str
    source_id: Optional[str]        # e.g. "pexels:3129957" or "flux:seed123"
    local_path: Optional[str]
    vision_score: Optional[float]   # 0-10 composite, see §2.4
    vision_rationale: Optional[str]
    is_fallback: bool = False

class ScenePlan(BaseModel):
    index: int
    script_segment: str             # one sentence/beat of the VO
    start_ms: Optional[int] = None  # null until TTS locks real timestamps
    end_ms: Optional[int] = None
    visual_description: str         # what should be on screen
    shot_type: str                  # "close-up" | "wide" | "macro" | "abstract-metaphor" | ...
    camera_feel: str                # "static-tripod" | "handheld" | "drone" | "cinematic-glide"
    motion: SceneMotion
    emotional_tone: str
    is_abstract_concept: bool       # routes to metaphor library, see §2.4
    asset_strategy: SceneAssetStrategy
    chosen_asset: Optional[SceneAsset] = None
    fallback_assets: List[SceneAsset] = []
    transition_out: SceneTransition
    sfx_cue: Optional[str] = None
    music_intensity: str            # "low" | "build" | "peak" | "resolve"
    qa_visual_score: Optional[float] = None   # filled at QA time, see §2.8

class ScenePlanSet(BaseModel):
    scenes: List[ScenePlan]
    style_token_ref: str            # points at Series/one-off style guide, §2.4/§2.5
```

`ScenePlanSet` is stored as a new JSON column on `VideoProject` (`scene_plan`), the same pattern already used for `brief`/`trend_report`.

### Pipeline order (why scenes must be locked *after* TTS)

Scene boundaries are drafted by Creative Director as part of scriptcraft (§2.3, one boundary per pattern-interrupt beat), but `start_ms`/`end_ms` can't be final until the actual voiceover audio exists — TTS timing never matches word-count estimates exactly. So the extended Producer flow becomes:

1. Creative Director emits `ScenePlanSet` with `script_segment` per scene and *estimated* durations, no timestamps yet.
2. Producer synthesizes the full-script TTS once (not per-scene — see §2.6 on why per-scene TTS calls would break prosody), gets word-level timestamps.
3. Producer maps each scene's `script_segment` boundary to the nearest word timestamp, filling `start_ms`/`end_ms` for every scene. This reuses the existing punctuation-matching logic in `voice.py`'s `_build_subtitle_items_from_edge_cues` / legacy-submaker aggregation almost unchanged — it already does sentence-to-timestamp alignment, we're just also using it to cut scene boundaries instead of only subtitle-entry boundaries.
4. Visual Director resolves `chosen_asset` for every scene now that exact duration is known (needed for e.g. "does this stock clip have enough footage to trim to 6.2s").
5. Renderer assembles.

This keeps Visual Director as a step *inside* the existing `PRODUCING` status (invoked by Producer, logged as `AgentEvent(agent="visual_director")`), not a new Kanban column — consistent with how you scoped the roster in §2.9.

### Renderer changes

`combine_videos()`/`generate_video()` in `video.py` currently iterate a flat list of downloaded stock paths, subclip them to `max_clip_duration` chunks, and shuffle/sequence them. v2 changes the iteration unit from "downloaded video chunk" to "resolved `ScenePlan`":

- For each `ScenePlan`: open `chosen_asset.local_path`, trim/loop to exactly `end_ms - start_ms` (today's clips are cut to a max duration and concatenated until they cover total audio length — this becomes "cut to *exactly* this scene's duration," which is stricter but simpler).
- If `asset_strategy == AI_IMAGE`: apply Ken Burns via MoviePy's `resize`+`crop` interpolated over the scene duration (motion=`pan`/`zoom` picks direction/rate) — this is a static image turned into a synthetic clip, still emitted as a temp MP4 like every other clip today.
- Apply `transition_out` between scene *N* and *N+1* using the transition functions already in `video_effects.py` (fade/slide already exist; `zoom_through` is new and small).
- Write each scene's temp MP4 exactly as today (`_write_videofile_with_codec_fallback`, unchanged) — **the per-clip-temp-MP4 + single-ffmpeg-concat-demuxer architecture is preserved verbatim**, we only changed how the list of inputs to that concat is built.
- Subtitle burn-in changes per §2.7 but happens in the same final pass as today.

Net effect: `material.py`'s round-robin script-order mode is superseded by per-scene resolution and can be deleted once Milestone 4 lands (kept working until then for the legacy path, see §5/§10 backward-compatibility).

---

## 2.2 Research layer

### Design: `app/services/research.py` + `Researcher` agent

New Pydantic schemas:

```python
class SourceCitation(BaseModel):
    url: str
    title: str
    publisher: str
    published_at: Optional[datetime]
    accessed_at: datetime

class KeyFact(BaseModel):
    statement: str
    citations: List[SourceCitation]     # target >=2 independent sources
    confidence: Literal["verified", "single-source", "disputed"]
    is_numeric_or_date: bool

class ResearchDossier(BaseModel):
    topic: str
    generated_at: datetime
    freshness_window_hours: int         # 24 for news modes
    key_facts: List[KeyFact]
    disputed_points: List[str]
    suggested_angle: str
    sources: List[SourceCitation]
    reduced_verification: bool = False  # true if a source fetch failed / <2 sources found for a claim
```

`ResearchDossier` is stored as a new `research_dossier` JSON column on `VideoProject`, and its `sources` list is what the Final Review UI renders as clickable citations.

### Source options — recommendation

You asked me to evaluate four options. My recommendation, in priority order:

1. **Anthropic's built-in web search tool, as the primary and only source for v2.** The agents already run on the Anthropic API; this needs zero new API keys, zero new vendor integration, and zero new failure modes to handle. It returns cited URLs directly, which is exactly the citation format `SourceCitation` needs. **This is the option I'd ship first**, and I'd defer the others until real usage shows it's insufficient.
2. **RSS feeds per niche** (curated feed lists — TechCrunch AI, The Verge AI, Ars Technica, Hacker News via its Algolia API, arXiv new-submissions RSS) as a *supplementary* signal for AI News specifically, since it's cheap (free, no key) and gives clean timestamps for the freshness check. I'd add this in the same milestone as the web-search tool, not defer it, because it's near-zero cost and directly strengthens the ≤24h freshness requirement.
3. **NewsAPI-class services** (NewsAPI.org, GNews, etc.) — I recommend **not** adding one of these in v2. Free tiers are rate-limited (NewsAPI.org: 100 req/day, no commercial use) and paid tiers start at real money ($449/mo for NewsAPI.org's cheapest commercial plan) for a capability the web-search tool + RSS combo already covers reasonably well. This is a "pay for it only if the first two prove insufficient" call — flagged as an open question in §11.
4. **Existing YouTube/pytrends signals** feed **Trend Scout**, not Researcher — they answer "what's trending," not "what's true." Trend Scout's output topic is what Researcher then investigates. No change needed to how these are used.

### Fact-verification pass

A second, cheap Claude call (Haiku-tier) that runs **after** Creative Director drafts the final script, comparing every factual sentence against `ResearchDossier.key_facts`. I'm placing this inside **Quality Reviewer's QA v2 pass** (§2.8), not as a separate agent or pipeline stage — it's a mechanical, low-context comparison (N script sentences vs. a dossier already in the DB), and QA is already the natural checkpoint where verdicts (`pass`/`revise`/`fail`) get produced and routed. Output:

```python
class FactCheckFlag(BaseModel):
    sentence: str
    supported: bool
    matching_fact_index: Optional[int]
    note: str
```

`QAReport` gains `fact_check_flags: List[FactCheckFlag]`. For AI News / World News content types, **any unsupported flag or `reduced_verification=true` forces `overall="fail"` or `"revise"`** — per your §3 requirement that these types must show zero fact-check flags to pass.

---

## 2.3 Scriptcraft

### Retention structure (encoded directly into Creative Director's system prompt)

- Cold open hook, ≤2s of screen time, addresses the viewer directly (second person, a question, or a bold claim).
- One open loop, stated in the first 3-5s, explicitly paid off at the 80-90% mark of the script.
- One curiosity gap per 15s of runtime.
- A pattern interrupt (visual or tonal shift) roughly every 3-5s — **these interrupts are literally what define scene boundaries** in the `ScenePlanSet`, so for a 45s video you should expect ~9-13 scenes.
- Concrete language over abstract ("a $4 coffee habit costs you $1,460 a year," not "small expenses add up").
- CTA is woven into the loop's payoff, never appended as a separate closing line.

### Self-review rubric

After drafting, Creative Director scores its own script (a second internal call, still inside the same agent's execution — not a new Kanban stage) against:

```python
class ScriptSelfReview(BaseModel):
    hook_strength: int      # 1-5
    loop_payoff: int        # 1-5
    pacing: int             # 1-5 — even distribution of pattern interrupts
    clarity: int            # 1-5 — concrete vs. abstract language
    emotional_arc: int      # 1-5 — tension/release shape
    rationale: str
    rewritten: bool
```

If **any** dimension scores below 3, the agent rewrites once (hard cap: one rewrite, before any asset work begins — this is deliberately cheap since it's pure text, unlike the post-render QA revision loop which is expensive because it's already spent render+visual-scoring budget). This is a quality gate *inside* Creative Director, distinct from and prior to the existing QA revision loop.

---

## 2.4 Visual selection strategy (Visual Director)

This is the highest-priority new capability, per your framing, so I'm giving it the most detail.

### Per-scene candidate generation and scoring

1. Visual Director takes each scene's `visual_description` + `shot_type` + `camera_feel` and generates **2-3 concrete stock search queries** (translating "productivity" into filmable nouns — see the metaphor library below for the abstract case).
2. For each query, fetch the top 3-4 candidates' thumbnail/preview frames from Pexels/Pixabay (both already return thumbnail URLs in their API responses today — no new integration).
3. **One batched Claude vision call per scene** (not one call per candidate) scores all 6-12 collected thumbnails at once against the scene's `visual_description`, on four axes: relevance (0-10), quality (0-10), watermark risk (0-10, lower is better), vertical-crop safety (0-10). This batching is a deliberate cost control — it turns what could be 6-12 vision calls into 1 per scene.
4. Highest composite score wins; runners-up are stored as `fallback_assets` (used if the render step discovers the chosen clip is unusable, e.g. corrupt download).
5. **Scores are cached** keyed by `(asset_source_id, hash(visual_description))` in a small new table (`VisualScoreCache`), so re-running a scene during a revision loop doesn't re-spend vision-call budget on candidates it's already scored.

### Stock vs. AI-image vs. AI-video decision rule

- **Default: stock.** Always tried first, always free relative to generation.
- **AI image** (+ Ken Burns) when the best stock composite score falls below a threshold (recommend 6/10) **and** the content type's budget allows generation **and** the scene needs something stock structurally can't provide (named/specific concepts, stylized illustration, or a recurring character/environment per the Series Bible).
- **AI video** only when `motion` is `pan`/`zoom` *and* that motion is narratively essential (not decorative) *and* the quality preset is Cinematic. I'm recommending you **defer AI video generation entirely past this v2 scope** — see the challenge below.

### Providers and cost (per-asset)

| Purpose | Provider | Approx. cost | Notes |
|---|---|---|---|
| AI image | Flux schnell (via fal.ai) | ~$0.003/image | Budget-tier default |
| AI image | Flux pro / Google Imagen 3 | ~$0.02-0.04/image | Cinematic-tier default |
| AI image | OpenAI gpt-image-1 | ~$0.02-0.19/image (size/quality dependent) | Fallback option |
| AI video | Runway Gen-4 Turbo | ~$0.05/sec (~$0.25 for a 5s clip) | If enabled at all |
| AI video | Luma Dream Machine | ~$0.30+/5s clip | Alternative |

**Challenge to the brief (confirmed by user):** AI video is 10-50x the cost of AI image per second of screen time, adds a new provider integration and its own failure/retry surface, and the brief itself says "only when budget allows" and "essential" — i.e. it's expected to be rare. **AI image (with Ken Burns) is the only generative path in v2**; AI video is deferred until we have real data on whether AI-image scenes are actually the retention bottleneck for Cinematic-tier videos. The schema (`SceneAssetStrategy.AI_VIDEO`) stays defined so it's a drop-in addition later.

**Image-gen provider selection is also deferred** (confirmed by user — no provider key chosen yet). Milestone 5 ships the pluggable adapter interface and Ken Burns motion, and runs stock-only in practice until a provider is configured, following the same graceful-degradation pattern as any other missing key (§5). No per-video AI-asset budget cap is being added in v2 either — the existing monthly budget cap is the only spend control, revisited later once a provider is actually in use.

### Visual consistency

- **Style token**, persisted on the Series Bible (one-off videos get an ephemeral equivalent generated per-project): a palette (list of hex/descriptive colors), a grading hint ("warm teal-orange cinematic grade"), and an illustration-style prompt suffix appended to *every* AI-gen call for that series/video.
- For stock, when the provider API exposes contributor/photographer ID (Pexels does), Visual Director prefers same-contributor candidates when multiple tie on score, to reduce jarring lighting/style shifts between consecutive stock clips.
- **Character/environment consistency across episodes — being honest about the limits:** current diffusion models (Flux, Imagen, SDXL) do **not** reliably reproduce the same face across independent generations, even with reference/seed images or IP-Adapter-style techniques (~60-70% visual similarity at best, and that's before accounting for vertical-crop composition drift). Building real facial consistency would mean adopting a much heavier, more expensive pipeline (fine-tuned LoRA per series, or a service like build-your-own-character APIs) that isn't justified by the content types in scope. Pragmatic mitigations, in order of preference:
  1. **Consistent style over consistent face** — lock the grading/illustration style, not a character's appearance.
  2. **A recurring silhouette, icon, or mascot bumper** (a fixed non-photorealistic glyph/logo shown at intro/outro) gives series identity without needing per-frame facial consistency.
  3. **Text-and-scenery-led formats** for series where a "host" isn't structurally necessary — Motivational, Fun Facts, AI News, and World News all work narrator-voice + B-roll, with no character on screen at all. I'd steer series-capable content types toward this by default and treat persistent AI-generated human characters as explicitly out of scope for v2.

### B-roll for abstract concepts

A config-driven **concept → visual metaphor library** (`config/visual_metaphors.yaml`, e.g. `"productivity": ["organized desk overhead shot", "hourglass close-up", "checklist being checked off"]`). When a scene is flagged `is_abstract_concept=true` (Creative Director self-tags this during scene planning), Visual Director consults the library to generate concrete stock queries instead of literally searching the abstract word — this directly fixes the "productivity" search-term-mismatch failure mode your commit history shows you already hit once (`4f6bc16`, Creative Director search terms causing Pexels mismatches).

---

## 2.5 Series & continuity system

### `Series` entity (new SQLite table, alongside existing models)

```python
class Series(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    content_type: str                    # references a ContentTypeTemplate id, §3
    title: str
    style_guide: dict = Field(sa_column=Column(JSON))     # palette, grading_hint, illustration_suffix, subtitle theme ref
    voice_id: str                        # locked TTS voice, hard-enforced
    voice_delivery_settings: dict = Field(sa_column=Column(JSON))  # default pace/pitch
    music_palette: dict = Field(sa_column=Column(JSON))
    character_reference: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # descriptions, mascot info, seed image paths (best-effort only, see §2.4 honesty note)
    pronunciation_dictionary: dict = Field(default_factory=dict, sa_column=Column(JSON))  # term -> phonetic respelling / SSML phoneme
    episode_counter: int = 0
    rolling_summary: str = ""            # "previously on" text, updated after each episode airs
    status: Literal["active", "paused", "archived"] = "active"
    created_at: datetime
    updated_at: datetime

# VideoProject gains:
series_id: Optional[int] = Field(default=None, foreign_key="series.id")
episode_number: Optional[int] = None
```

### How Creative Director uses it

When writing Part N, Creative Director receives the full Series Bible in its prompt context:
- **Same narrator voice enforced by hard validation** — not the existing "fallback on hallucination" pattern (`_resolve_voice_name`), but a **hard rejection**: if `series_id` is set, the brief's `voice_recommendation` field is not even offered as a free choice to the LLM — it's injected as a constraint, and if the model still returns something different, it's silently overridden to `series.voice_id` and logged as a warning event (stricter than the current single-video fallback because episode-to-episode voice drift is a continuity failure, not just an invalid-value failure).
- **Callbacks to earlier parts** via `rolling_summary`.
- **Optional recap hook** (1 line) and **optional cliffhanger** to bridge to Part N+1 — both toggled by the content type template's `series_support` flag (Motivational and Fun Facts use both; AI/World News use neither, since they're not narrative-continuous).

### UI

Covered in §4 — a Series page with an episode list and a "+ Next Episode" action that pre-fills a new project's `series_id`, pulling forward style/voice/character reference automatically.

---

## 2.6 Voiceover quality

### Provider comparison ($/video basis: ~45s script, ~120 words / ~700 characters)

| Provider | Cost/video | Word timings | Emotional range | Recommendation |
|---|---|---|---|---|
| Edge TTS | Free | Native `WordBoundary` events (already implemented) | Low — robotic ceiling | Budget preset default |
| **Azure Cognitive V2** | ~$0.01 | Native word-boundary callback (already implemented!) | Medium | **Standard preset default** — this is an underused option already in the codebase; better quality than Edge's free consumer voices at near-zero marginal cost |
| ElevenLabs | ~$0.15-0.30 | Character-level via `/with-timestamps` endpoint (needs word aggregation, new work) | High | Cinematic preset default |

I'm recommending Azure V2 over Edge for Standard specifically because it's **already fully implemented and tested** in `voice.py` (`azure_tts_v2`, native word-boundary callback) — moving Standard to it is a config default change, not new code, and it closes most of the quality gap to ElevenLabs for a fraction of the cost.

### Per-scene delivery hints

Creative Director emits, per scene, `pace` (slow/normal/fast), `emphasis_words: List[str]`, and `pause_after_ms`. Translation per provider:
- **Azure (V1/V2)**: native SSML — `<prosody rate>` for pace, `<emphasis>` for emphasis words. Already SSML-capable; this is additive.
- **ElevenLabs**: no full SSML, but supports inline `<break time="0.3s"/>` and responds to punctuation/emphasis cues in plain text — approximate emphasis via punctuation/capitalization conventions their docs recommend.
- **Edge**: limited SSML support in the consumer endpoint; approximate via rate parameter per synthesis call and natural punctuation only. Because we synthesize the *whole script* in one call (see below), fine per-scene rate control on Edge is best-effort, not guaranteed.

**Design decision: one TTS call for the whole script, not one per scene.** Per-scene calls would break sentence-to-sentence prosody (breath, pacing continuity) and would multiply provider request counts. Scene boundaries are recovered by mapping word timestamps back onto scene boundaries (§2.1), exactly as the existing subtitle aggregation logic already does for sentence boundaries.

### Pronunciation dictionary

`Series.pronunciation_dictionary` maps a term to either an SSML `<phoneme>` tag (providers that support it) or a plain-text phonetic respelling substituted into the text before synthesis (providers that don't). Applied as a pre-synthesis text pass, independent of provider.

### Word-level timestamps by provider (feeds §2.7 directly)

- **Native**: Edge, Azure V1, Azure V2 — already implemented, unchanged.
- **Character-level, needs aggregation**: ElevenLabs — new code to bucket characters into words using the known text.
- **None — forced alignment required**: Gemini, MiMo, SiliconFlow, Chatterbox. Reuse the existing Whisper + Levenshtein correction pass (`subtitle.py`) exactly as it works today for the "no native timings" fallback path — no new alignment technology needed, just repointing an existing capability at a new set of providers.

---

## 2.7 Subtitle system

### Rendering approach: **ASS burned via ffmpeg/libass**, replacing per-word MoviePy `TextClip`s

Evaluated both, per your ask:

| | MoviePy `TextClip`s (current) | ASS + libass (recommended) |
|---|---|---|
| Word-level highlight | Requires one `TextClip` per word transition — for a 45s video with karaoke-style word highlighting, that's 100+ composited layers | Native `\k` karaoke tags — one subtitle track, no extra layers |
| Styling (color/scale on current word) | Manual PIL-based background clips, expensive per-frame compositing | Native ASS style + `\t` transform tags |
| Render speed | Slow — MoviePy composites every TextClip as a separate overlay | Fast — single `-vf subtitles=file.ass` burn-in pass |
| New dependency | None | None — ffmpeg is already a hard dependency, and libass ships in essentially all standard ffmpeg builds; no new Python package |
| Per-content-type themes | Ad hoc Python params | Native ASS style blocks, trivial to template per content type |

**Decision: ASS.** The karaoke requirement is the deciding factor — word-by-word highlighting via MoviePy compositing would be a real performance regression on top of being harder to style. Fallback: at startup, probe the ffmpeg binary for libass support the same way `video.py` already probes for hardware encoders; if absent, fall back to the current `TextClip` renderer and surface a UI notice (no hard failure).

### Migration notes

- New module `app/services/ass_subtitle.py`: takes the per-word timing data (already produced by `voice.py`/`subtitle.py`, unchanged) and emits a `.ass` file with karaoke tags, current-word emphasis (color+scale via `\t`), 1-2 lines max, 3-5 words/line, safe-area margins.
- `video.py::generate_video()` gets a new branch: if ASS rendering is available, skip `TextClip` compositing and instead add `-vf subtitles=path.ass` to the **final** single-pass ffmpeg encode (after concat, same place burn-in happens today) — this preserves "one final encode pass," it doesn't add one.
- `subtitle.py`'s Whisper/Edge timing extraction is **unchanged** — only the rendering consumer changes, from a `TextClip` generator to an `.ass` generator.
- Safe-area: subtitle vertical position at ~70-80% of frame height (center-lower), avoiding the bottom ~220px (platform UI: like/comment/share buttons) and top ~150px (profile bar) on 1080×1920 — encoded as `MarginV` in the ASS style block.
- Per-content-type themes (font/colors/emphasis style) live in the content type's `subtitle_theme` reference (§3) and, for series, are locked in `Series.style_guide`. Optional sparing emoji only for Fun Facts, inserted as literal characters in the subtitle text (no special ASS handling needed for most fonts with emoji glyphs).

---

## 2.8 QA v2

Extends the existing `QAReport` (technical checks + 8-frame vision review) with:

```python
class PacingFlag(BaseModel):
    scene_index: int
    duration_ms: int
    reason: str   # e.g. "static scene >7s with no visual change"

class QAReportV2(QAReport):  # additive fields
    fact_check_flags: List[FactCheckFlag] = []          # §2.2
    scene_visual_scores: List[float] = []                # carried forward from Visual Director cache, §2.4
    subtitle_sync_ok: bool = True                        # deterministic timestamp cross-check, no LLM
    pacing_flags: List[PacingFlag] = []
    loudness_lufs: Optional[float] = None                # integrated LUFS via ffmpeg ebur128
    loudness_ok: bool = True                              # target -14 LUFS ± 1
    continuity_ok: bool = True                            # voice_id + style_token match against Series Bible
```

- **Fact check**: from §2.2, mandatory-zero-flags gate for AI/World News.
- **Scene visual relevance**: reuses the Visual Director's cached score for the *chosen* asset (no re-spend), plus a cheap spot-check re-score of 2 random scenes per video to catch drift between planning-time and render-time (e.g. a fallback asset silently substituted).
- **Subtitle sync**: deterministic — compare the `.ass` file's word timestamps against the TTS/Whisper word timestamps used to generate it. No LLM call; this is a data-integrity check, not a judgment call.
- **Pacing**: deterministic histogram over `ScenePlan.end_ms - start_ms`; flag any `motion=static` scene over 7s with `asset_strategy=stock` and no transition — i.e., visual monotony.
- **Loudness**: deterministic ffmpeg `ebur128` filter measuring integrated LUFS on the mixed voice+BGM track, target -14 LUFS ±1. No LLM.
- **Continuity**: for series episodes only — `voice_id` used matches `Series.voice_id` (hard check, should never fail given §2.5's enforcement, but QA verifies it independently as a safety net) and the style token suffix was present on every AI-gen prompt for the episode (recorded as a boolean at generation time, checked not re-inferred).

Verdict logic, revision routing, and `max_revisions=2` are **unchanged** — new flags feed into the existing `revision_target: "creative_director" | "producer"` routing (fact/script issues → Creative Director; visual/pacing/loudness issues → Producer).

---

## 2.9 Agent roster — consolidated

### Your brainstorm vs. what I'm recommending

You listed ~16 candidate roles: Research, Fact Verification, Trend Detection, Script Writing, Storytelling, Scene Planning, Visual Search, Image Selection, Video Selection, AI Image Gen, AI Video Gen, Voiceover, Subtitle, QA, Continuity, Final Review.

**Final roster: 6 LLM agents + 1 non-LLM orchestrator + 1 shared-state entity.** Here's the mapping and why each consolidation is safe:

| Your candidate roles | Consolidated into | Why |
|---|---|---|
| Trend Detection | **Trend Scout** (existing, lightly upgraded) | Unchanged responsibility; now also reads Series rolling-summary to avoid proposing topics a series has already covered. |
| Research, Fact Verification | **Researcher** (new) does research; fact-check is a *pass inside Quality Reviewer* | Fact verification is a mechanical script-vs-dossier diff, not a creative decision — it doesn't need its own agent identity or pipeline stage; it belongs where verdicts already get produced (QA). |
| Script Writing, Storytelling, Scene Planning | **Creative Director** (existing, upgraded) | These three are one coherent creative decision, not three. Scene boundaries *are* the pattern-interrupt beats of the script — splitting scriptwriting from scene planning would force two agents to agree on beat placement across a handoff, which is exactly the kind of round-trip that causes drift and adds cost without adding quality. One agent, one context window, one self-review pass (§2.3) covering script + scene plan together. |
| Visual Search, Image Selection, Video Selection, AI Image Gen (prompting), AI Video Gen (prompting) | **Visual Director** (new) | These are all facets of one continuous decision per scene: "given this scene's description and budget, what's the best available asset and where does it come from." Splitting search/scoring/generation into separate agents means someone still has to own the stock-vs-AI escalation decision — better that it's the same agent that also does the scoring, since the score is the input to the escalation decision. |
| Voiceover, Subtitle | Absorbed into **Producer** (existing, extended) | These are deterministic service calls (TTS API, ASS file generation), not judgment calls — Producer already orchestrates non-LLM service calls in sequence; extending it to include Visual Director invocation, TTS, and subtitle generation keeps "agents that decide" separate from "services that execute," matching your own framing in §2.9. |
| QA | **Quality Reviewer** (existing, upgraded) | Unchanged identity, extended checks (§2.8). |
| Continuity | **Not an agent — the Series Bible (shared state)** | Continuity isn't a decision that needs an LLM call; it's a constraint (voice ID, style token) that Creative Director reads and Quality Reviewer verifies. Making it an agent would mean giving it something to decide, and there's nothing left to decide once the Bible exists. |
| Final Review | **Repurposed Approval Queue UI**, not an agent | This is a human action, not an agent action — see §4. |

Roster: **Trend Scout, Researcher, Creative Director, Visual Director, Quality Reviewer, Publisher (frozen), Performance Analyst**, orchestrated by **Producer** (non-LLM) and **Series** (shared state, not an agent).

This is fewer agents than you'd get by literally implementing your 16-item brainstorm, but it's *not* fewer than your own §2.9 expectation — it matches it almost exactly (you predicted "Researcher, Trend Scout, Creative Director, Visual Director, Producer, Quality Reviewer" with "continuity handled by shared state"). I'm confirming that instinct was right and giving the reasoning to back it.

### Agent I/O schemas (consolidated view)

| Agent | Input | Output |
|---|---|---|
| Trend Scout | `niche, audience, recent_topics[], performance_notes[], series_rolling_summary?` | `TrendReport` (unchanged) |
| Researcher | `topic, content_type.freshness_window_hours` | `ResearchDossier` (§2.2) |
| Creative Director | `topic, niche, revision_notes?, research_dossier?, series_bible?` | `CreativeBrief` (extended: `scene_plan: ScenePlanSet`, `script_self_review: ScriptSelfReview`) |
| Visual Director | `ScenePlanSet, style_token, budget_caps` | `ScenePlanSet` (with `chosen_asset`/`fallback_assets` filled) |
| Producer | `VideoParams, ScenePlanSet` | render output (unchanged shape) + locked `ScenePlanSet` (timestamps filled) |
| Quality Reviewer | `video_path, ScenePlanSet, research_dossier?, series_bible?` | `QAReportV2` (§2.8) |
| Publisher | `CreativeBrief, video_path` (frozen — no behavior change while `publishing_enabled=false`) | `PublishPackage` (unchanged) |

### Revised state machine

New statuses, minimal and conditional:

```
IDEA_PENDING → IDEA_READY
   → [content_type.research_required] → RESEARCHING → RESEARCH_READY
   → SCRIPTING → SCRIPT_READY
   → PRODUCING   (internally: lock TTS timestamps → Visual Director resolves scenes → per-scene assembly → concat)
   → RENDERED → QA_REVIEW
   → {QA_PASSED | revise-loop back to SCRIPTING, max 2 | FAILED}
   → AWAITING_HUMAN_APPROVAL   ("Final Review" in UI, §4)
   → [publishing frozen: APPROVED marks complete, download offered; PUBLISHING/PUBLISHED/TRACKING/ARCHIVED unreachable until publishing_enabled=true]
```

`RESEARCHING`/`RESEARCH_READY` are the only genuinely new Kanban states, and only entered when the content type template sets `research_required=true`. Everything else is internal to the existing `PRODUCING`/`QA_REVIEW` stages, which is why this is a moderate extension of the orchestrator rather than a rewrite.

---

## 2.10 The rest of the design

### Content-type templates — data-driven, not code branches

```python
class ContentTypeTemplate(BaseModel):
    id: str                              # "motivational" | "fun_facts" | "ai_news" | "world_news" | "trending_now"
    label: str
    default_duration_s: int
    scriptcraft_overrides: dict          # rubric weight tweaks, if any
    visual_strategy: dict                # {"stock_score_threshold": 6.0, "ai_gen_allowed": true, "ai_video_allowed": false}
    voice_style: str                     # descriptive hint fed to Creative Director's voice pick
    subtitle_theme: str                  # ref into theme registry
    music_palette: str
    research_required: bool
    freshness_window_hours: Optional[int]
    series_capable: bool
    default_quality_preset: Literal["budget", "standard", "cinematic"]
```

**Decision (confirmed by user, overriding my initial recommendation of static YAML)**: ship these as a DB table (`ContentTypeTemplate`, seeded with the 5 built-in types on first startup/migration) with a Settings UI to view and edit them. This means Milestone 1 needs a small template-editor form in Settings (fields matching the schema above) in addition to the New-Video card flow, and Milestone 8's "content-type templates" polish item expands to include edit/save validation. Built-in templates are still seeded from code so a fresh install always has sane defaults; users can then tweak per-field values (e.g. raise the stock-score threshold, change default duration) without a code change or redeploy.

The 5 types from your brief map directly onto this schema — I won't re-list all five since your spec (§3) already fully specifies their per-type behavior; the template schema is designed to hold exactly those fields.

**Daily News Mode**: reuses the existing `apscheduler` `CronTrigger` pattern in `scheduler.py` verbatim. New `[daily_news]` config section (`enabled`, `run_at`, `content_types: ["ai_news", "world_news"]`, `niche`) creates one project per configured content type per day, runs to `AWAITING_HUMAN_APPROVAL`, and surfaces an **in-app notification** (a badge/counter, no new external integration — email/Slack/webhook notification is explicitly out of scope for v2 since no such integration exists today and building one wasn't requested). No auto-publish, per §0.2.

### Model recommendations & cost per video (three presets, ~45s short)

These are estimates for planning purposes, not commitments — actual per-video cost should be measured against real runs before locking budget caps in Settings.

| Task | Budget | Standard | Cinematic |
|---|---|---|---|
| Script (draft + self-review) | Claude Haiku 4.5 | Claude Sonnet 5 | Claude Sonnet 5 (+ extra rewrite pass) |
| Research | Skipped (non-news types) | Web-search tool, Sonnet | Web-search tool + RSS cross-check, Sonnet |
| Fact-check pass | Skipped | Haiku | Haiku |
| Visual scoring | Haiku vision, batched | Sonnet vision, batched | Sonnet vision, more candidates/scene |
| Voice | Edge (free) | Azure V2 | ElevenLabs |
| Visuals | Stock only | Stock + 2-3 AI images (Flux schnell) | Stock + 5-6 AI images (Flux pro/Imagen) |
| QA | Haiku vision | Sonnet vision + fact-check | Sonnet vision + fact-check + continuity |
| **Est. cost/video (non-news)** | **~$0.10-0.15** | **~$0.35-0.45** | **~$1.00-1.20** |
| **Est. cost/video (news types)** | n/a (news requires research) | **~$0.55-0.70** | **~$1.30-1.50** |

(AI video, if you decide to keep it in scope despite my recommendation to defer it, adds ~$0.25-0.50 per clip on top of Cinematic figures — see §2.4.)

### Bottleneck analysis

- **Render time**: per-scene assembly doesn't fundamentally change clip count vs. today (still one temp-MP4 per unit + one concat pass); Ken Burns on AI-image scenes adds a `zoompan`-style filter per such scene — bounded and cheap. Mitigation: existing hardware-codec-with-libx264-fallback logic is untouched and continues to absorb most of the cost.
- **API rate limits**: stock provider key rotation already exists; the new load is vision-scoring calls to Anthropic — mitigated by the batched-per-scene scoring design (1 call/scene, not 1 call/candidate) and by the `VisualScoreCache` avoiding re-scoring on revision loops.
- **Vision-scoring cost**: same cache; cheapest viable model (Haiku) for Budget/Standard, Sonnet only for Cinematic.
- **Storage growth**: candidate thumbnails, non-chosen AI-gen assets, and research source snapshots accumulate. Mitigation: a retention job that deletes non-chosen candidate assets N days after a project reaches `ARCHIVED`/`AWAITING_HUMAN_APPROVAL` (dossier text/citations are cheap and kept indefinitely; final rendered videos follow existing retention behavior, unchanged).

### Data collection plan

Persist, per video: the full `ScenePlanSet` (already planned as a JSON column), per-scene QA scores (`QAReportV2.scene_visual_scores`), revision reasons (extending the existing `revision_notes` granularity to per-dimension), and — the highest-value addition — **human edit diffs captured at Final Review**: a new `human_edits: List[dict]` field on `VideoProject` recording `{field, before, after}` for anything a human changes (title, description, script line, thumbnail choice) before clicking Approve. This is free to capture (it's just diffing form state) and is the single most useful signal for future prompt tuning, since it's a direct record of "the AI got this wrong and a human fixed it." Retention/view data resumes mattering once publishing resumes (Performance Analyst is untouched and ready for that day).

---

## 3. Content types

Covered structurally in §2.10 (`ContentTypeTemplate` schema). Your five types (Motivational, Fun Facts, AI News, World News, Trending Now) map onto that schema's fields exactly as you specified them — `research_required`/`freshness_window_hours` gate the Researcher stage, `series_capable` gates recap/cliffhanger prompting, `visual_strategy` gates AI-gen escalation. I'm not re-deriving these since your spec already fully constrains them; implementation is a matter of writing five YAML files against the schema.

---

## 4. UI redesign

Single-port FastAPI + `resource/public` + WebSocket-with-polling-fallback architecture is unchanged — all additions below are new pages/components using the exact patterns already established (Tailwind dark palette, `api.ts` typed client, `StatusBadge`, `taskFileUrl()`).

- **New Video flow**: full-screen content-type cards (reading from the `ContentTypeTemplate` registry) → per-type options (topic-or-auto, series new/continue via a dropdown populated from `GET /series`, quality preset with a live cost estimate computed from the §2.10 cost table) → confirm. Replaces today's inline "+ New Video" form on Pipeline Board with a dedicated flow; Pipeline Board keeps a lightweight quick-create for power users.
- **Series page (new)**: list of series with bible summary (voice, style swatch, episode count), episode list (linking to each `VideoProject`), and a "+ Next Episode" button that pre-fills a new-video flow with `series_id` set.
- **Project Detail**: new **Storyboard** tab — horizontal scroll of scene cards (thumbnail from `chosen_asset`, `script_segment`, `start_ms`-`end_ms`, an asset-source badge stock/AI, `qa_visual_score`). The existing Agent Activity log moves behind a second tab in the same panel (currently always-visible right sidebar) — this declutters Project Detail now that there's more to show.
- **Final Review** (repurposed Approval Queue): keeps its existing two-pane layout (project list + detail pane) but the detail pane gains: the Storyboard strip, fact-check flags rendered with clickable source links (from `ResearchDossier.sources`), and editable metadata. **Approve** now marks the project complete and offers a download link (no platform toggles, no publish call) while `publishing_enabled=false`. The publish button/platform-toggle UI **remains in the code**, rendered disabled with a "publishing paused" tooltip, so re-enabling later is a config flip, not a UI rebuild.
- **Settings** gains: quality-preset defaults, per-content-type AI-generation toggles, asset budget per video (feeding the live cost estimate in New Video flow), and new provider key status dots (Flux/Imagen, ElevenLabs, NewsAPI-if-added) following the exact green/red `ConfiguredDot` pattern already in use.
- **Pipeline Board, Trends, Analytics** — unchanged, since none of the above changes their contract.
- **Delete `webui/.streamlit/config.toml`** and the now-empty `webui/` directory — confirmed dead in Milestone 1.

---

## 5. Constraints & non-negotiables — confirmed

- Single FastAPI process, single port, `resource/public` build target — unchanged by everything above.
- Graceful degradation: missing image-gen key → Visual Director silently stays stock-only with a one-time UI notice on the project; research source failures → dossier gets `reduced_verification=true`, which news content types treat as an automatic QA-fail per §2.2/§2.8; missing libass → subtitle renderer falls back to the current `TextClip` path with a UI notice, no crash.
- Every new agent call logs `AgentEvent` with tokens/cost exactly as today; image/video-gen spend logs as `AgentEvent(agent="visual_director", type="tool_call", cost_usd=...)` rows, which `VideoProject.cost_usd` and `budget.py`'s monthly sum already aggregate without changes to those functions.
- Backward compatibility: existing `VideoProject` rows have no `scene_plan`/`research_dossier`/`series_id` — all new columns are nullable, and the orchestrator's legacy path (flat search-terms, no scene plan) stays reachable for direct `POST /videos` API calls that bypass the Agent Studio entirely, per your explicit requirement.
- `publishing_enabled` config flag, defaulting to `false`, gates `Publisher.publish()`, the scheduler's auto-upload path, and the UI's publish button — Publisher agent code, `upload_post.py`, and the approval-gate logic are **not deleted**, only short-circuited.

---

## 6. Implementation milestones (Phase B, finalized)

One commit per milestone, app runnable after each, matching your suggested order with the AI-video deferral applied:

1. **Series Bible + content-type templates + new New-Video flow** (old pipeline still runs underneath). Delete `webui/.streamlit/`.
2. **Scene-based data model + Creative Director v2** (script + `ScenePlanSet` + self-review rubric). Scene plan generated but not yet consumed by the renderer — visible in a read-only Storyboard preview to validate quality before wiring it into rendering.
3. **TTS upgrade + word timestamps + karaoke subtitles** (ASS rendering, Azure V2/ElevenLabs provider defaults, pronunciation dictionary).
4. **Visual Director (stock candidate scoring) + per-scene renderer** — the scene plan now actually drives asset selection and the renderer's per-scene assembly.
5. **AI image generation path (opt-in) + Ken Burns + style tokens.** (AI video deliberately excluded here per §2.4 — schema stays ready for a future milestone.)
6. **Research layer + fact verification + AI News / World News types + Daily News Mode.**
7. **QA v2 + Storyboard UI polish + Final Review redesign** (fact-check flags, source links, publish-button-disabled state).
8. **Polish**: cost presets wired to live estimates, docs/README update, full `python -m unittest` pass.

---

## 7. Test plan

Matches existing conventions exactly — plain `unittest`, temp-SQLite-per-test, `MPT_RUN_INTEGRATION_TESTS` gating for anything hitting a live provider (Anthropic web search, Flux/fal.ai, ElevenLabs, NewsAPI if added):

- New state transitions (`RESEARCHING`/`RESEARCH_READY`, series-aware revision routing) covered in `test_orchestrator_state_machine.py`-style tests, reusing its `_wait_for_status`/`_fake_render_*` helper patterns.
- New QA checks (fact-check gate, pacing histogram, loudness) covered like `test_qa_technical_checks.py`, with synthetic fixture videos/audio generated in `setUpClass`.
- Renderer changes (per-scene trimming, Ken Burns, ASS burn-in) covered in `test_video.py`-style tests including a small generated sample MP4 fixture, mirroring the existing pattern of building fixtures in-test rather than committing binary assets.
- `Series` model and Visual Score cache get their own `test_series.py`/`test_visual_director.py` following the same temp-DB setUp/tearDown pattern as `test_orchestrator_state_machine.py`.

---

## 8. Backward compatibility

Existing projects open unchanged (all new columns nullable). The legacy non-scene pipeline remains reachable via direct `POST /videos` (the original MoneyPrinterTurbo-style API, untouched by the Agent Studio work) — this is the app's existing "escape hatch" API and nothing in this redesign removes it.

---

## 9. Risks & open questions

Resolved during Phase A review:

- **AI video generation**: deferred past v2 (confirmed). Schema stays defined for a later milestone.
- **Research sourcing**: Anthropic web-search tool + free RSS only, no paid NewsAPI-class service (confirmed).
- **Standard-tier voice**: Azure Cognitive V2 for Standard, ElevenLabs reserved for Cinematic (confirmed).
- **Content-type templates**: DB-backed and user-editable via Settings, not static YAML (confirmed — see §2.10 update). Milestone 1 scope grows slightly to include the template-editor form.

Also resolved:

- **AI image-gen provider**: not selected yet — deferred. Milestone 5 ships the Ken Burns motion pipeline and the pluggable `SceneAsset`/provider-adapter interface, but stays **stock-only in practice** until a provider key is configured (same graceful-degradation pattern as any other missing key, §5). Adding a provider later (Flux/fal.ai remains my recommendation when you're ready) is then a config change plus one adapter implementation, not a redesign.
- **Per-video AI-asset budget cap**: none in v2 — relying solely on the existing monthly budget cap (`config.agents.monthly_budget_usd`) rather than adding a new per-video cap mechanism. Since the image-gen provider itself is deferred, this has no near-term effect; revisit if/when a provider is added and real spend data suggests per-video caps are needed.
- **Daily News Mode notifications**: in-app badge only, confirmed. No webhook/email integration in v2.

---

## 10. Definition of done (Phase A)

This document answers every §2 item with concrete schemas, named providers with prices, and a justified roster. I'm now stopping for your approval and your decisions on §9 before writing any Phase B code.
