import { Link } from "react-router-dom";
import { Project } from "../api";
import StatusBadge from "./StatusBadge";

export default function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="block rounded-lg border border-border bg-panel2 p-3 transition-colors hover:border-accent"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <span className="text-sm font-medium leading-snug text-slate-100">
          {project.topic || "(topic pending)"}
        </span>
      </div>
      <div className="mb-2 flex items-center justify-between">
        <StatusBadge status={project.status} />
        <span className="text-xs text-slate-500">#{project.id}</span>
      </div>
      {(project.content_type_id || project.episode_number) && (
        <div className="mb-2 flex flex-wrap gap-1">
          {project.content_type_id && (
            <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] text-slate-400">
              {project.content_type_id}
            </span>
          )}
          {project.episode_number && (
            <span className="rounded bg-indigo-950/50 px-1.5 py-0.5 text-[10px] text-indigo-300">
              Ep {project.episode_number}
            </span>
          )}
        </div>
      )}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>{project.niche || "no niche"}</span>
        <span>${project.cost_usd.toFixed(3)}</span>
      </div>
      {project.failure_reason && (
        <div className="mt-2 line-clamp-2 rounded bg-rose-950/50 p-1.5 text-xs text-rose-300">
          {project.failure_reason}
        </div>
      )}
    </Link>
  );
}
