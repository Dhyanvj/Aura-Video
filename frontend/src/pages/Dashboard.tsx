import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Analytics, Project, Settings } from "../api";
import { useLiveUpdates } from "../ws";
import ProjectCard from "../components/ProjectCard";
import StatusBadge from "../components/StatusBadge";

function isThisMonth(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-panel2 p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-500">{sub}</div>}
    </div>
  );
}

// Free, dependency-free sparkline - a handful of <rect> bars scaled to the
// max value in the series. Not worth adding a charting library for one
// compact chart.
function Sparkline({ values }: { values: number[] }) {
  if (values.length === 0) return <div className="text-xs text-slate-500">No cost data yet</div>;
  const max = Math.max(...values, 0.0001);
  const width = 200;
  const height = 40;
  const barWidth = width / values.length;
  return (
    <svg width={width} height={height} className="overflow-visible">
      {values.map((v, i) => {
        const barHeight = Math.max(2, (v / max) * height);
        return (
          <rect
            key={i}
            x={i * barWidth}
            y={height - barHeight}
            width={Math.max(2, barWidth - 2)}
            height={barHeight}
            className="fill-accent"
            opacity={0.4 + 0.6 * (i / values.length)}
          />
        );
      })}
    </svg>
  );
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = () => {
    api.listProjects().then(setProjects).catch(() => {});
    api.getAnalytics().then(setAnalytics).catch(() => {});
    api.getSettings().then(setSettings).catch(() => {});
  };

  useEffect(refresh, []);
  useLiveUpdates(
    () => refresh(),
    () => refresh(),
  );

  const videosThisMonth = projects.filter((p) => isThisMonth(p.created_at)).length;
  const failedThisMonth = projects.filter((p) => isThisMonth(p.created_at) && p.status === "FAILED").length;
  const failureRate = videosThisMonth > 0 ? Math.round((failedThisMonth / videosThisMonth) * 100) : 0;
  const awaitingApproval = projects.filter(
    (p) => p.status === "AWAITING_HUMAN_APPROVAL" || p.status === "NEEDS_HUMAN_REVIEW",
  );
  const awaitingScriptApproval = projects.filter((p) => p.status === "AWAITING_SCRIPT_APPROVAL");
  const readyToPublish = projects.filter((p) => p.status === "APPROVED");
  const rescuableFailed = projects.filter((p) => p.status === "FAILED" && p.rescue_eligible);
  const recent = [...projects]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 6);
  const sparklineValues = [...projects]
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    .slice(-12)
    .map((p) => p.cost_usd);

  const inlineApprove = async (id: number) => {
    setBusyId(id);
    try {
      await api.approveProject(id, []);
      refresh();
    } catch {
      // Surfacing failures here is a Milestone 4 polish item (toast/error
      // banner) - the Approval Queue page always has the full error path.
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {settings?.publishing_enabled === false && "Publishing is paused - approvals stop at \"ready to publish.\""}
        </p>
      </div>

      {rescuableFailed.length > 0 && (
        <div className="rounded-lg border border-emerald-700/50 bg-emerald-950/20 p-3 text-sm text-emerald-300">
          {rescuableFailed.length} failed project{rescuableFailed.length === 1 ? "" : "s"} {rescuableFailed.length === 1 ? "has" : "have"}{" "}
          usable renders —{" "}
          <Link to="/pipeline" className="underline hover:text-emerald-200">
            review them?
          </Link>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Videos this month" value={String(videosThisMonth)} />
        <StatCard
          label="Spend vs budget"
          value={analytics ? `$${analytics.monthly_spend_usd.toFixed(2)}` : "-"}
          sub={analytics && analytics.monthly_budget_cap_usd > 0 ? `of $${analytics.monthly_budget_cap_usd.toFixed(2)}` : "no cap set"}
        />
        <StatCard label="Script review queue" value={String(awaitingScriptApproval.length)} />
        <StatCard label="Approval queue" value={String(awaitingApproval.length)} />
        <StatCard label="Failure rate" value={`${failureRate}%`} sub="this month" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Recent projects</h2>
            <Link to="/pipeline" className="text-xs text-accent hover:underline">
              View all
            </Link>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {recent.map((p) => (
              <ProjectCard key={p.id} project={p} onDeleted={refresh} onChanged={refresh} />
            ))}
            {recent.length === 0 && <p className="text-sm text-slate-500 dark:text-slate-400">No projects yet.</p>}
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <div className="rounded-lg border border-border bg-panel2 p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Script review</h2>
            </div>
            {awaitingScriptApproval.length === 0 && (
              <p className="text-xs text-slate-500 dark:text-slate-400">Nothing waiting.</p>
            )}
            <div className="flex flex-col gap-2">
              {awaitingScriptApproval.slice(0, 4).map((p) => (
                <Link
                  key={p.id}
                  to={`/projects/${p.id}`}
                  className="flex items-center justify-between gap-2 rounded border border-border bg-panel p-2 text-xs text-slate-700 hover:border-accent dark:text-slate-300"
                >
                  <span className="min-w-0 flex-1 truncate">{p.topic || `#${p.id}`}</span>
                  <span className="shrink-0 rounded bg-fuchsia-700 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    review
                  </span>
                </Link>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-panel2 p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Pending approvals</h2>
              <Link to="/approvals" className="text-xs text-accent hover:underline">
                Open queue
              </Link>
            </div>
            {awaitingApproval.length === 0 && <p className="text-xs text-slate-500 dark:text-slate-400">Nothing waiting.</p>}
            <div className="flex flex-col gap-2">
              {awaitingApproval.slice(0, 4).map((p) => (
                <div key={p.id} className="flex items-center justify-between gap-2 rounded border border-border bg-panel p-2">
                  <Link to={`/projects/${p.id}`} className="min-w-0 flex-1 truncate text-xs text-slate-700 dark:text-slate-300">
                    {p.topic || `#${p.id}`}
                  </Link>
                  <button
                    onClick={() => inlineApprove(p.id)}
                    disabled={busyId === p.id}
                    className="shrink-0 rounded bg-emerald-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-panel2 p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Ready to publish</h2>
              <Link to="/approvals" className="text-xs text-accent hover:underline">
                Open queue
              </Link>
            </div>
            {readyToPublish.length === 0 && <p className="text-xs text-slate-500 dark:text-slate-400">Nothing approved yet.</p>}
            <div className="flex flex-col gap-1">
              {readyToPublish.slice(0, 4).map((p) => (
                <div key={p.id} className="flex items-center justify-between gap-2 text-xs">
                  <Link to={`/projects/${p.id}`} className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-300">
                    {p.topic || `#${p.id}`}
                  </Link>
                  <StatusBadge status={p.status} />
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-panel2 p-4">
            <h2 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-100">Cost per video (recent)</h2>
            <Sparkline values={sparklineValues} />
          </div>
        </div>
      </div>
    </div>
  );
}
