import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Project, Settings } from "../api";
import { useLiveUpdates } from "../ws";
import ProjectCard from "../components/ProjectCard";

const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "idea", label: "Idea & Script", statuses: ["IDEA_PENDING", "IDEA_READY", "SCRIPTING", "SCRIPT_READY"] },
  { key: "producing", label: "Producing", statuses: ["PRODUCING", "RENDERED", "QA_REVIEW"] },
  { key: "approval", label: "Awaiting Approval", statuses: ["QA_PASSED", "AWAITING_HUMAN_APPROVAL"] },
  { key: "publishing", label: "Publishing", statuses: ["APPROVED", "PUBLISHING"] },
  { key: "published", label: "Published", statuses: ["PUBLISHED", "TRACKING", "ARCHIVED"] },
  { key: "failed", label: "Failed / Rejected", statuses: ["FAILED", "REJECTED"] },
];

export default function PipelineBoard() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [topic, setTopic] = useState("");
  const [niche, setNiche] = useState("");
  const [audience, setAudience] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api.listProjects().then(setProjects).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    refresh();
    api.getSettings().then(setSettings).catch(() => undefined);
  }, []);

  useLiveUpdates(
    () => refresh(),
    () => refresh(),
  );

  const quickCreateProject = async () => {
    setCreating(true);
    setError(null);
    try {
      await api.createProject({ topic: topic.trim(), niche: niche.trim(), audience: audience.trim() });
      setTopic("");
      setShowNewForm(false);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  const toggleAutopilot = async () => {
    if (!settings) return;
    const next = settings.autopilot_level === "manual" ? "semi" : "manual";
    const updated = await api.updateSettings({ autopilot_level: next });
    setSettings(updated);
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Pipeline Board</h1>
          <p className="text-sm text-slate-400">Every project, live, from idea to published post.</p>
        </div>
        <div className="flex items-center gap-3">
          {settings && (
            <button
              onClick={toggleAutopilot}
              className="rounded border border-border bg-panel2 px-3 py-1.5 text-sm text-slate-200 hover:border-accent"
              title="manual: approve topic, script, and video. semi: approve only the final video."
            >
              Autopilot: <span className="font-semibold">{settings.autopilot_level}</span>
            </button>
          )}
          <button
            onClick={() => setShowNewForm((v) => !v)}
            className="rounded border border-border bg-panel2 px-3 py-1.5 text-sm text-slate-300 hover:border-accent hover:text-white"
            title="Skip the content-type flow: just a topic, niche, and audience"
          >
            Quick create
          </button>
          <button
            onClick={() => navigate("/new")}
            className="rounded bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            + New Video
          </button>
        </div>
      </div>

      {showNewForm && (
        <div className="mb-6 rounded-lg border border-border bg-panel p-4">
          <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-3">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Topic (leave empty for auto-trend)"
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
            />
            <input
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
              placeholder="Niche (optional, uses Settings default)"
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
            />
            <input
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              placeholder="Audience (only used for auto-trend)"
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
            />
          </div>
          <button
            onClick={quickCreateProject}
            disabled={creating}
            className="rounded bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {creating ? "Starting..." : topic.trim() ? "Start from topic" : "Let Trend Scout pick a topic"}
          </button>
        </div>
      )}

      {error && <div className="mb-4 rounded bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-6">
        {COLUMNS.map((column) => {
          const items = projects.filter((p) => column.statuses.includes(p.status));
          return (
            <div key={column.key} className="rounded-lg border border-border bg-panel p-3">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-200">{column.label}</h2>
                <span className="text-xs text-slate-500">{items.length}</span>
              </div>
              <div className="flex flex-col gap-2">
                {items.map((p) => (
                  <ProjectCard key={p.id} project={p} />
                ))}
                {items.length === 0 && <div className="text-xs text-slate-600">No projects</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
