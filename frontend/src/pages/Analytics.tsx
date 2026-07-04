import { useEffect, useState } from "react";
import { Project } from "../api";
import { api } from "../api";

export default function Analytics() {
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    api.listProjects().then((all) => setProjects(all.filter((p) => p.status === "PUBLISHED" || p.status === "TRACKING")));
  }, []);

  const totalCost = projects.reduce((sum, p) => sum + p.cost_usd, 0);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Analytics</h1>
      <p className="mb-6 text-sm text-slate-400">
        Per-video view/like/comment tracking appears here once the Performance Analyst is configured with a YouTube
        Data API key.
      </p>

      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="text-2xl font-semibold text-slate-100">{projects.length}</div>
          <div className="text-xs text-slate-400">Published videos</div>
        </div>
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="text-2xl font-semibold text-slate-100">${totalCost.toFixed(2)}</div>
          <div className="text-xs text-slate-400">Total spend (published)</div>
        </div>
      </div>

      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-slate-500">
            <th className="py-2">Topic</th>
            <th className="py-2">Niche</th>
            <th className="py-2">Cost</th>
            <th className="py-2">Published</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id} className="border-b border-border text-slate-300">
              <td className="py-2">{p.topic}</td>
              <td className="py-2">{p.niche}</td>
              <td className="py-2">${p.cost_usd.toFixed(3)}</td>
              <td className="py-2">{new Date(p.updated_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
