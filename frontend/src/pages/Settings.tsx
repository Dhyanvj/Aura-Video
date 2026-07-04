import { useEffect, useState } from "react";
import { api, Settings as SettingsT } from "../api";

const PLATFORMS = ["tiktok", "instagram", "youtube"];

function ConfiguredDot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />;
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsT | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings);
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

  if (!settings) return <div className="text-slate-400">Loading...</div>;

  const togglePlatform = (platform: string) => {
    const next = settings.default_platforms.includes(platform)
      ? settings.default_platforms.filter((p) => p !== platform)
      : [...settings.default_platforms, platform];
    save({ default_platforms: next });
  };

  return (
    <div className="max-w-2xl">
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Settings</h1>
      <p className="mb-6 text-sm text-slate-400">
        API keys are configured in config.toml and never shown here — only whether each is set.
      </p>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Channel</h2>
        <label className="mb-1 block text-xs text-slate-400">Niche</label>
        <input
          defaultValue={settings.niche}
          onBlur={(e) => save({ niche: e.target.value })}
          className="mb-3 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
        />
        <label className="mb-1 block text-xs text-slate-400">Audience</label>
        <input
          defaultValue={settings.audience}
          onBlur={(e) => save({ audience: e.target.value })}
          className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
        />
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Autopilot</h2>
        <div className="flex gap-4">
          {(["manual", "semi"] as const).map((level) => (
            <label key={level} className="flex items-center gap-2 text-sm text-slate-200">
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
        <p className="mt-2 text-xs text-slate-500">Final-video approval can never be disabled.</p>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Daily schedule</h2>
        <label className="mb-3 flex items-center gap-2 text-sm text-slate-200">
          <input
            type="checkbox"
            checked={settings.schedule_enabled}
            onChange={(e) => save({ schedule_enabled: e.target.checked })}
          />
          Automatically generate videos every day
        </label>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs text-slate-400">Videos per day</label>
            <input
              type="number"
              min={1}
              defaultValue={settings.videos_per_day}
              onBlur={(e) => save({ videos_per_day: Number(e.target.value) })}
              className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Run at (HH:MM)</label>
            <input
              defaultValue={settings.run_at}
              onBlur={(e) => save({ run_at: e.target.value })}
              className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
            />
          </div>
        </div>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Budget</h2>
        <label className="mb-1 block text-xs text-slate-400">Monthly budget cap (USD, 0 = no cap)</label>
        <input
          type="number"
          min={0}
          step="0.01"
          defaultValue={settings.monthly_budget_usd}
          onBlur={(e) => save({ monthly_budget_usd: Number(e.target.value) })}
          className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
        />
        <p className="mt-2 text-xs text-slate-500">
          When the cap is reached, the daily scheduler stops creating new auto-trend projects for the rest of the
          month. Manual project creation is never blocked.
        </p>
      </section>

      <section className="mb-6 rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Default platforms</h2>
        <div className="flex gap-4">
          {PLATFORMS.map((platform) => (
            <label key={platform} className="flex items-center gap-2 text-sm text-slate-200">
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

      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">API keys</h2>
        <div className="flex flex-col gap-2 text-sm text-slate-200">
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
        <p className="mt-3 text-xs text-slate-500">Set these in config.toml, then restart the server.</p>
      </section>

      {saving && <p className="mt-4 text-xs text-slate-500">Saving...</p>}
      {!saving && savedAt && <p className="mt-4 text-xs text-emerald-500">Saved.</p>}
    </div>
  );
}
