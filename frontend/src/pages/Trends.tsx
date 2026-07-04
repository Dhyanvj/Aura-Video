import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Project } from "../api";

export default function Trends() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [busyTitle, setBusyTitle] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.listProjects().then((all) => setProjects(all.filter((p) => p.trend_report)));
  }, []);

  const promote = async (title: string, niche: string | null) => {
    setBusyTitle(title);
    try {
      const { project_id } = await api.createProject(title, niche || "", "");
      navigate(`/projects/${project_id}`);
    } finally {
      setBusyTitle(null);
    }
  };

  if (projects.length === 0) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-semibold text-slate-100">Trends</h1>
        <p className="text-slate-400">
          No trend reports yet. Start an auto-trend project from the Pipeline Board (leave the topic field empty).
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold text-slate-100">Trends</h1>
      <div className="flex flex-col gap-6">
        {projects.map((project) => (
          <section key={project.id} className="rounded-lg border border-border bg-panel p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-300">
              {project.niche || "General"} &middot; project #{project.id}
            </h2>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {project.trend_report!.ideas
                .slice()
                .sort((a, b) => b.opportunity_score - a.opportunity_score)
                .map((idea, i) => (
                  <div key={i} className="rounded border border-border bg-panel2 p-3">
                    <div className="mb-1 flex items-center justify-between">
                      <span className="font-medium text-slate-100">{idea.title}</span>
                      <span className="text-xs text-slate-400">score {idea.opportunity_score}</span>
                    </div>
                    <p className="mb-2 text-xs text-slate-400">{idea.why_trending}</p>
                    <div className="mb-2 flex flex-wrap gap-2 text-xs text-slate-500">
                      <span>{idea.suggested_format}</span>
                      <span>&middot;</span>
                      <span>{idea.estimated_competition} competition</span>
                      <span>&middot;</span>
                      <span>{idea.target_emotion}</span>
                    </div>
                    <button
                      onClick={() => promote(idea.title, project.niche)}
                      disabled={busyTitle === idea.title}
                      className="rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
                    >
                      {busyTitle === idea.title ? "Starting..." : "Promote to project"}
                    </button>
                  </div>
                ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
