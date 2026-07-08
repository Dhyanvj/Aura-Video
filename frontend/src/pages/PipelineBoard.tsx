import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Project } from "../api";
import { useLiveUpdates } from "../ws";
import ProjectCard from "../components/ProjectCard";
import ProjectFilters, { applyProjectFilters, EMPTY_FILTERS, ProjectFilterState } from "../components/ProjectFilters";

const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  {
    key: "idea",
    label: "Idea & Script",
    statuses: ["IDEA_PENDING", "IDEA_READY", "SCRIPTING", "SCRIPT_READY", "AWAITING_SCRIPT_APPROVAL"],
  },
  { key: "producing", label: "Producing", statuses: ["PRODUCING", "RENDERED", "QA_REVIEW"] },
  { key: "approval", label: "Awaiting Approval", statuses: ["QA_PASSED", "AWAITING_HUMAN_APPROVAL"] },
  { key: "publishing", label: "Publishing", statuses: ["APPROVED", "PUBLISHING"] },
  { key: "published", label: "Published", statuses: ["PUBLISHED", "TRACKING", "ARCHIVED"] },
  { key: "failed", label: "Failed / Rejected", statuses: ["FAILED", "REJECTED", "CANCELLED"] },
];

export default function PipelineBoard() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [showNewForm, setShowNewForm] = useState(false);
  const [topic, setTopic] = useState("");
  const [niche, setNiche] = useState("");
  const [audience, setAudience] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<ProjectFilterState>(EMPTY_FILTERS);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const refresh = () => {
    api.listProjects().then(setProjects).catch((e) => setError(String(e)));
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]));
  };

  const bulkDelete = async (permanent: boolean) => {
    if (selectedIds.length === 0) return;
    const confirmed = window.confirm(
      permanent
        ? `Permanently delete ${selectedIds.length} project(s)? This cannot be undone.`
        : `Move ${selectedIds.length} project(s) to the Recycle Bin?`,
    );
    if (!confirmed) return;
    await api.bulkDeleteProjects(selectedIds, permanent);
    setSelectedIds([]);
    setSelectMode(false);
    refresh();
  };

  useEffect(refresh, []);

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

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Pipeline Board</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Every project, live, from idea to published post.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setSelectMode((v) => !v);
              setSelectedIds([]);
            }}
            className={`rounded border px-3 py-1.5 text-sm ${
              selectMode
                ? "border-accent bg-accent text-white"
                : "border-border bg-panel2 text-slate-600 dark:text-slate-300 hover:border-accent"
            }`}
          >
            {selectMode ? "Cancel select" : "Select"}
          </button>
          <button
            onClick={() => setShowNewForm((v) => !v)}
            className="rounded border border-border bg-panel2 px-3 py-1.5 text-sm text-slate-600 dark:text-slate-300 hover:border-accent hover:text-white"
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
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-500 dark:text-slate-500"
            />
            <input
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
              placeholder="Niche (optional, uses Settings default)"
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-500 dark:text-slate-500"
            />
            <input
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              placeholder="Audience (only used for auto-trend)"
              className="rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-500 dark:text-slate-500"
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

      {error && <div className="mb-4 rounded bg-rose-100 dark:bg-rose-950/50 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div>}

      {selectMode && (
        <div className="mb-4 flex items-center gap-3 rounded border border-border bg-panel p-2 text-sm">
          <span className="text-slate-600 dark:text-slate-300">{selectedIds.length} selected</span>
          <button
            onClick={() => bulkDelete(false)}
            disabled={selectedIds.length === 0}
            className="rounded bg-amber-600 px-3 py-1 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
          >
            Move to Recycle Bin
          </button>
          <button
            onClick={() => bulkDelete(true)}
            disabled={selectedIds.length === 0}
            className="rounded bg-rose-600 px-3 py-1 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
          >
            Delete Permanently
          </button>
        </div>
      )}

      <ProjectFilters filters={filters} onChange={setFilters} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-6">
        {COLUMNS.map((column) => {
          const items = applyProjectFilters(projects, filters).filter((p) => column.statuses.includes(p.status));
          return (
            <div key={column.key} className="rounded-lg border border-border bg-panel p-3">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{column.label}</h2>
                <span className="text-xs text-slate-500 dark:text-slate-500">{items.length}</span>
              </div>
              <div className="flex flex-col gap-2">
                {items.map((p) => (
                  <ProjectCard
                    key={p.id}
                    project={p}
                    selectable={selectMode}
                    selected={selectedIds.includes(p.id)}
                    onToggleSelect={toggleSelect}
                    onDeleted={refresh}
                  />
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
