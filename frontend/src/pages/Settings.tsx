import { useEffect, useState } from "react";
import { api, ContentTypeTemplate, Settings as SettingsT } from "../api";

const PLATFORMS = ["tiktok", "instagram", "youtube"];

function ConfiguredDot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />;
}

function ContentTypeRow({
  template,
  onSaved,
}: {
  template: ContentTypeTemplate;
  onSaved: (updated: ContentTypeTemplate) => void;
}) {
  const [open, setOpen] = useState(false);
  const [scriptcraftJson, setScriptcraftJson] = useState(JSON.stringify(template.scriptcraft_overrides, null, 2));
  const [visualJson, setVisualJson] = useState(JSON.stringify(template.visual_strategy, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);

  const save = async (partial: Partial<ContentTypeTemplate>) => {
    const updated = await api.updateContentType(template.id, partial);
    onSaved(updated);
  };

  const saveJsonField = async (field: "scriptcraft_overrides" | "visual_strategy", raw: string) => {
    try {
      const parsed = JSON.parse(raw);
      setJsonError(null);
      await save({ [field]: parsed } as Partial<ContentTypeTemplate>);
    } catch {
      setJsonError(`${field} is not valid JSON - not saved`);
    }
  };

  return (
    <div className="rounded border border-border bg-panel2 p-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left text-sm text-slate-800 dark:text-slate-200"
      >
        <span className="font-medium">{template.label}</span>
        <span className="text-xs text-slate-500 dark:text-slate-500">
          {template.id} &middot; ~{template.default_duration_s}s &middot; {template.default_quality_preset}
        </span>
      </button>
      {open && (
        <div className="mt-3 flex flex-col gap-3">
          <div>
            <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Description (shown on the New Video card)</label>
            <input
              defaultValue={template.description}
              onBlur={(e) => save({ description: e.target.value })}
              className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Label</label>
              <input
                defaultValue={template.label}
                onBlur={(e) => save({ label: e.target.value })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Default duration (s)</label>
              <input
                type="number"
                min={1}
                defaultValue={template.default_duration_s}
                onBlur={(e) => save({ default_duration_s: Number(e.target.value) })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Voice style</label>
              <input
                defaultValue={template.voice_style}
                onBlur={(e) => save({ voice_style: e.target.value })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Subtitle theme</label>
              <input
                defaultValue={template.subtitle_theme}
                onBlur={(e) => save({ subtitle_theme: e.target.value })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Music palette</label>
              <input
                defaultValue={template.music_palette}
                onBlur={(e) => save({ music_palette: e.target.value })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Default quality preset</label>
              <select
                defaultValue={template.default_quality_preset}
                onChange={(e) => save({ default_quality_preset: e.target.value as ContentTypeTemplate["default_quality_preset"] })}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 text-sm text-slate-900 dark:text-slate-100"
              >
                <option value="budget">budget</option>
                <option value="standard">standard</option>
                <option value="cinematic">cinematic</option>
              </select>
            </div>
          </div>

          <div className="flex flex-wrap gap-4 text-sm text-slate-800 dark:text-slate-200">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={template.research_required}
                onChange={(e) => save({ research_required: e.target.checked })}
              />
              Research required
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={template.series_capable}
                onChange={(e) => save({ series_capable: e.target.checked })}
              />
              Series-capable
            </label>
            {template.research_required && (
              <label className="flex items-center gap-2">
                Freshness window (hours)
                <input
                  type="number"
                  min={1}
                  defaultValue={template.freshness_window_hours ?? 24}
                  onBlur={(e) => save({ freshness_window_hours: Number(e.target.value) })}
                  className="w-20 rounded border border-border bg-panel px-2 py-1 text-sm text-slate-900 dark:text-slate-100"
                />
              </label>
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Scriptcraft overrides (JSON)</label>
              <textarea
                value={scriptcraftJson}
                onChange={(e) => setScriptcraftJson(e.target.value)}
                onBlur={(e) => saveJsonField("scriptcraft_overrides", e.target.value)}
                rows={4}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 font-mono text-xs text-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Visual strategy (JSON)</label>
              <textarea
                value={visualJson}
                onChange={(e) => setVisualJson(e.target.value)}
                onBlur={(e) => saveJsonField("visual_strategy", e.target.value)}
                rows={4}
                className="w-full rounded border border-border bg-panel px-2 py-1.5 font-mono text-xs text-slate-900 dark:text-slate-100"
              />
            </div>
          </div>
          {jsonError && <p className="text-xs text-rose-400">{jsonError}</p>}
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsT | null>(null);
  const [contentTypes, setContentTypes] = useState<ContentTypeTemplate[]>([]);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings);
    api.listContentTypes().then(setContentTypes);
  }, []);

  const save = async (partial: Partial<SettingsT>) => {
    setSaving(true);
    try {
      const updated = await api.updateSettings(partial);
      setSettings(updated);
      setSavedAt(Date.now());
    } finally {
      setSaving(false);
    }
  };

  if (!settings) return <div className="text-slate-500 dark:text-slate-400">Loading...</div>;

  const togglePlatform = (platform: string) => {
    const next = settings.default_platforms.includes(platform)
      ? settings.default_platforms.filter((p) => p !== platform)
      : [...settings.default_platforms, platform];
    save({ default_platforms: next });
  };

  return (
    <div className="max-w-2xl">
      <h1 className="mb-1 text-xl font-semibold text-slate-900 dark:text-slate-100">Settings</h1>
      <p className="mb-6 text-sm text-slate-500 dark:text-slate-400">
        API keys are configured in config.toml and never shown here — only whether each is set.
      </p>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Channel</h2>
        <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Niche</label>
        <input
          defaultValue={settings.niche}
          onBlur={(e) => save({ niche: e.target.value })}
          className="mb-3 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
        />
        <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Audience</label>
        <input
          defaultValue={settings.audience}
          onBlur={(e) => save({ audience: e.target.value })}
          className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
        />
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Autopilot</h2>
        <div className="flex gap-4">
          {(["manual", "semi"] as const).map((level) => (
            <label key={level} className="flex items-center gap-2 text-sm text-slate-800 dark:text-slate-200">
              <input
                type="radio"
                name="autopilot"
                checked={settings.autopilot_level === level}
                onChange={() => save({ autopilot_level: level })}
              />
              {level === "manual" ? "Manual (approve topic, script, video)" : "Semi-auto (approve final video only)"}
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-500">Final-video approval can never be disabled.</p>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Daily schedule</h2>
        <label className="mb-3 flex items-center gap-2 text-sm text-slate-800 dark:text-slate-200">
          <input
            type="checkbox"
            checked={settings.schedule_enabled}
            onChange={(e) => save({ schedule_enabled: e.target.checked })}
          />
          Automatically generate videos every day
        </label>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Videos per day</label>
            <input
              type="number"
              min={1}
              defaultValue={settings.videos_per_day}
              onBlur={(e) => save({ videos_per_day: Number(e.target.value) })}
              className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Run at (HH:MM)</label>
            <input
              defaultValue={settings.run_at}
              onBlur={(e) => save({ run_at: e.target.value })}
              className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
            />
          </div>
        </div>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Budget</h2>
        <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Monthly budget cap (USD, 0 = no cap)</label>
        <input
          type="number"
          min={0}
          step="0.01"
          defaultValue={settings.monthly_budget_usd}
          onBlur={(e) => save({ monthly_budget_usd: Number(e.target.value) })}
          className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
        />
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-500">
          When the cap is reached, the daily scheduler stops creating new auto-trend projects for the rest of the
          month. Manual project creation is never blocked.
        </p>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Default platforms</h2>
        <div className="flex gap-4">
          {PLATFORMS.map((platform) => (
            <label key={platform} className="flex items-center gap-2 text-sm text-slate-800 dark:text-slate-200">
              <input
                type="checkbox"
                checked={settings.default_platforms.includes(platform)}
                onChange={() => togglePlatform(platform)}
              />
              {platform}
            </label>
          ))}
        </div>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-1 text-sm font-semibold text-slate-800 dark:text-slate-200">Content types</h2>
        <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
          The New Video presets shown as cards. Built-in defaults, editable here without a code change.
        </p>
        <div className="flex flex-col gap-2">
          {contentTypes.map((t) => (
            <ContentTypeRow
              key={t.id}
              template={t}
              onSaved={(updated) => setContentTypes((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))}
            />
          ))}
          {contentTypes.length === 0 && <p className="text-xs text-slate-500 dark:text-slate-500">Loading...</p>}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">API keys</h2>
        <div className="flex flex-col gap-2 text-sm text-slate-800 dark:text-slate-200">
          <div className="flex items-center gap-2">
            <ConfiguredDot ok={settings.anthropic_configured} /> Anthropic (required for all agents)
          </div>
          <div className="flex items-center gap-2">
            <ConfiguredDot ok={settings.youtube_configured} /> YouTube Data API (optional — trends & analytics)
          </div>
          <div className="flex items-center gap-2">
            <ConfiguredDot ok={settings.upload_post_configured} /> Upload-Post (required to publish)
          </div>
        </div>
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-500">Set these in config.toml, then restart the server.</p>
        {!settings.publishing_enabled && (
          <p className="mt-2 rounded bg-amber-950/50 p-2 text-xs text-amber-300">
            Publishing is paused ([features].publishing_enabled=false in config.toml). Approving a project marks it
            complete and offers a download instead of publishing anywhere.
          </p>
        )}
      </section>

      {saving && <p className="mt-4 text-xs text-slate-500 dark:text-slate-500">Saving...</p>}
      {!saving && savedAt && <p className="mt-4 text-xs text-emerald-500">Saved.</p>}
    </div>
  );
}
