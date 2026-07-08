import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApprovalMode, ContentTypeTemplate, QualityPreset, Settings, SeriesT } from "../api";

// Illustrative per-video cost ranges from docs/DESIGN_V2.md §2.10 - not a
// live calculation, just enough to compare presets before creating a video.
const COST_ESTIMATES: Record<QualityPreset, { base: string; withResearch: string }> = {
  budget: { base: "$0.10-0.15", withResearch: "n/a - news types need Standard+" },
  standard: { base: "$0.35-0.45", withResearch: "$0.55-0.70" },
  cinematic: { base: "$1.00-1.20", withResearch: "$1.30-1.50" },
};

export default function NewVideo() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselectedSeriesId = searchParams.get("series_id");

  const [contentTypes, setContentTypes] = useState<ContentTypeTemplate[]>([]);
  const [seriesList, setSeriesList] = useState<SeriesT[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selectedType, setSelectedType] = useState<ContentTypeTemplate | null>(null);
  const [topic, setTopic] = useState("");
  const [niche, setNiche] = useState("");
  const [audience, setAudience] = useState("");
  const [seriesMode, setSeriesMode] = useState<"none" | "new" | "continue">("none");
  const [seriesTitle, setSeriesTitle] = useState("");
  const [seriesId, setSeriesId] = useState<number | "">("");
  const [qualityPreset, setQualityPreset] = useState<QualityPreset>("standard");
  const [overrideApprovalMode, setOverrideApprovalMode] = useState(false);
  const [approvalModeOverride, setApprovalModeOverride] = useState<ApprovalMode>("manual");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listContentTypes().then(setContentTypes).catch((e) => setError(String(e)));
    api.listSeries().then(setSeriesList).catch(() => undefined);
    api.getSettings().then((s) => {
      setSettings(s);
      setApprovalModeOverride(s.approval_mode);
    }).catch(() => undefined);
  }, []);

  // Arriving from a Series page's "+ Next Episode" button: pre-select that
  // series' content type and switch straight to continue-mode once both the
  // content types and series lists have loaded.
  useEffect(() => {
    if (!preselectedSeriesId || contentTypes.length === 0 || seriesList.length === 0) return;
    const series = seriesList.find((s) => s.id === Number(preselectedSeriesId));
    if (!series) return;
    const type = contentTypes.find((t) => t.id === series.content_type_id);
    if (!type) return;
    setSelectedType(type);
    setQualityPreset(type.default_quality_preset);
    setSeriesMode("continue");
    setSeriesId(series.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselectedSeriesId, contentTypes, seriesList]);

  const selectType = (t: ContentTypeTemplate) => {
    setSelectedType(t);
    setQualityPreset(t.default_quality_preset);
    setSeriesMode("none");
    setSeriesId("");
  };

  const eligibleSeries = useMemo(
    () => (selectedType ? seriesList.filter((s) => s.content_type_id === selectedType.id) : []),
    [seriesList, selectedType],
  );

  const costEstimate = useMemo(() => {
    if (!selectedType) return null;
    const row = COST_ESTIMATES[qualityPreset];
    return selectedType.research_required ? row.withResearch : row.base;
  }, [selectedType, qualityPreset]);

  const confirmDisabled =
    creating ||
    !selectedType ||
    (seriesMode === "new" && !seriesTitle.trim()) ||
    (seriesMode === "continue" && seriesId === "");

  const confirm = async () => {
    if (!selectedType) return;
    setCreating(true);
    setError(null);
    try {
      const { project_id } = await api.createProject({
        topic: topic.trim(),
        niche: niche.trim(),
        audience: audience.trim(),
        content_type_id: selectedType.id,
        quality_preset: qualityPreset,
        series_mode: seriesMode,
        series_title: seriesMode === "new" ? seriesTitle.trim() : undefined,
        series_id: seriesMode === "continue" && seriesId !== "" ? Number(seriesId) : undefined,
        approval_mode_override: overrideApprovalMode ? approvalModeOverride : undefined,
      });
      navigate(`/projects/${project_id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-900 dark:text-slate-100">New Video</h1>
      <p className="mb-6 text-sm text-slate-500 dark:text-slate-400">Pick a content type, then fill in the details.</p>

      {error && <div className="mb-4 rounded bg-rose-100 dark:bg-rose-950/50 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div>}

      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {contentTypes.map((t) => (
          <button
            key={t.id}
            onClick={() => selectType(t)}
            className={`rounded-lg border p-4 text-left transition-colors ${
              selectedType?.id === t.id ? "border-accent bg-panel2" : "border-border bg-panel hover:border-accent"
            }`}
          >
            <div className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-100">{t.label}</div>
            {t.description && <p className="mb-1 text-xs text-slate-500 dark:text-slate-400">{t.description}</p>}
            <div className="text-xs text-slate-500 dark:text-slate-500">
              ~{t.default_duration_s}s &middot; {t.default_quality_preset}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {t.research_required && (
                <span className="inline-block rounded bg-amber-950/50 px-1.5 py-0.5 text-[10px] text-amber-300">
                  research, &le;{t.freshness_window_hours}h sources
                </span>
              )}
              {t.series_capable && (
                <span className="inline-block rounded bg-indigo-100 dark:bg-indigo-950/50 px-1.5 py-0.5 text-[10px] text-indigo-700 dark:text-indigo-300">
                  series-capable
                </span>
              )}
            </div>
          </button>
        ))}
        {contentTypes.length === 0 && !error && <div className="text-sm text-slate-500 dark:text-slate-500">Loading content types...</div>}
      </div>

      {selectedType && (
        <div className="max-w-2xl rounded-lg border border-border bg-panel p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">{selectedType.label} options</h2>

          <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Topic (leave empty for auto-trend)</label>
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            className="mb-3 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
          />
          <div className="mb-3 grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Niche</label>
              <input
                value={niche}
                onChange={(e) => setNiche(e.target.value)}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Audience (auto-trend only)</label>
              <input
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
          </div>

          {selectedType.series_capable && (
            <div className="mb-3">
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Series</label>
              <div className="flex flex-wrap gap-4 text-sm text-slate-800 dark:text-slate-200">
                {(["none", "new", "continue"] as const).map((mode) => (
                  <label key={mode} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="series_mode"
                      checked={seriesMode === mode}
                      onChange={() => setSeriesMode(mode)}
                    />
                    {mode === "none" ? "One-off video" : mode === "new" ? "Start a new series" : "Continue a series"}
                  </label>
                ))}
              </div>
              {seriesMode === "new" && (
                <input
                  value={seriesTitle}
                  onChange={(e) => setSeriesTitle(e.target.value)}
                  placeholder="Series title"
                  className="mt-2 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-500 dark:text-slate-500"
                />
              )}
              {seriesMode === "continue" && (
                <select
                  value={seriesId}
                  onChange={(e) => setSeriesId(e.target.value ? Number(e.target.value) : "")}
                  className="mt-2 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
                >
                  <option value="">Select a series...</option>
                  {eligibleSeries.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title} (episode {s.episode_counter + 1} next)
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Quality preset</label>
          <div className="mb-1 flex gap-4 text-sm text-slate-800 dark:text-slate-200">
            {(["budget", "standard", "cinematic"] as const).map((preset) => (
              <label key={preset} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="preset"
                  checked={qualityPreset === preset}
                  onChange={() => setQualityPreset(preset)}
                />
                {preset}
              </label>
            ))}
          </div>
          <p className="mb-4 text-xs text-slate-500 dark:text-slate-500">
            Estimated cost per video: <span className="text-slate-600 dark:text-slate-300">{costEstimate}</span> (illustrative estimate,
            see docs/DESIGN_V2.md)
          </p>

          {settings && (
            <div className="mb-4">
              <label className="flex items-center gap-2 text-sm text-slate-800 dark:text-slate-200">
                <input
                  type="checkbox"
                  checked={overrideApprovalMode}
                  onChange={(e) => setOverrideApprovalMode(e.target.checked)}
                />
                Override approval mode for this project (currently: {settings.approval_mode})
              </label>
              {overrideApprovalMode && (
                <div className="mt-2 flex gap-4 text-sm text-slate-800 dark:text-slate-200">
                  {(["manual", "automatic"] as const).map((mode) => (
                    <label key={mode} className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="approval_mode_override"
                        checked={approvalModeOverride === mode}
                        onChange={() => setApprovalModeOverride(mode)}
                      />
                      {mode}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          <button
            onClick={confirm}
            disabled={confirmDisabled}
            className="rounded bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {creating ? "Starting..." : "Create video"}
          </button>
        </div>
      )}
    </div>
  );
}
