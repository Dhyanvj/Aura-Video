import { useState } from "react";
import { Link } from "react-router-dom";
import { api, Project } from "../api";
import StatusBadge from "./StatusBadge";

interface ProjectCardProps {
  project: Project;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
  onDeleted?: () => void;
}

export default function ProjectCard({ project, selectable, selected, onToggleSelect, onDeleted }: ProjectCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const deleteProject = async (permanent: boolean) => {
    setMenuOpen(false);
    const publishedWarning =
      project.status === "PUBLISHED" || project.status === "TRACKING"
        ? "\n\nThis project is published - performance tracking for it will stop."
        : "";
    const confirmed = permanent
      ? window.confirm(
          `Permanently delete "${project.topic || `#${project.id}`}"? This cannot be undone - the folder and all DB records are removed.${publishedWarning}`,
        )
      : window.confirm(`Move "${project.topic || `#${project.id}`}" to the Recycle Bin? You can restore it later.${publishedWarning}`);
    if (!confirmed) return;
    try {
      await api.deleteProject(project.id, permanent);
      onDeleted?.();
    } catch (e) {
      window.alert(String(e));
    }
  };

  return (
    <div
      className={`relative block rounded-lg border p-3 transition-colors ${
        selected ? "border-accent bg-panel2" : "border-border bg-panel2 hover:border-accent"
      }`}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        {selectable && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.(project.id)}
            onClick={(e) => e.stopPropagation()}
            className="mt-0.5 shrink-0"
          />
        )}
        <Link to={`/projects/${project.id}`} className="min-w-0 flex-1">
          <span className="text-sm font-medium leading-snug text-slate-900 dark:text-slate-100">
            {project.topic || "(topic pending)"}
          </span>
        </Link>
        <div className="relative shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            aria-label="Project actions"
            className="rounded px-1.5 py-0.5 text-slate-500 hover:bg-panel hover:text-slate-900 dark:hover:text-white"
          >
            &#8942;
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-6 z-10 w-40 rounded border border-border bg-panel shadow-lg">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteProject(false);
                }}
                className="block w-full px-3 py-2 text-left text-xs text-slate-700 hover:bg-panel2 dark:text-slate-300"
              >
                Move to Recycle Bin
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteProject(true);
                }}
                className="block w-full px-3 py-2 text-left text-xs text-rose-600 hover:bg-panel2 dark:text-rose-400"
              >
                Delete Permanently
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="mb-2 flex items-center justify-between">
        <StatusBadge status={project.status} />
        <span className="text-xs text-slate-500 dark:text-slate-500">#{project.id}</span>
      </div>
      {project.status === "AWAITING_SCRIPT_APPROVAL" && (
        <div className="mb-2 inline-block rounded bg-fuchsia-700 px-1.5 py-0.5 text-[10px] font-semibold text-white">
          Needs your review
        </div>
      )}
      {(project.content_type_id || project.episode_number) && (
        <div className="mb-2 flex flex-wrap gap-1">
          {project.content_type_id && (
            <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] text-slate-500 dark:text-slate-400">
              {project.content_type_id}
            </span>
          )}
          {project.episode_number && (
            <span className="rounded bg-indigo-100 dark:bg-indigo-950/50 px-1.5 py-0.5 text-[10px] text-indigo-700 dark:text-indigo-300">
              Ep {project.episode_number}
            </span>
          )}
        </div>
      )}
      <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
        <span>{project.niche || "no niche"}</span>
        <span>${project.cost_usd.toFixed(3)}</span>
      </div>
      {project.failure_reason && (
        <div className="mt-2 line-clamp-2 rounded bg-rose-100 dark:bg-rose-950/50 p-1.5 text-xs text-rose-700 dark:text-rose-300">
          {project.failure_reason}
        </div>
      )}
    </div>
  );
}
