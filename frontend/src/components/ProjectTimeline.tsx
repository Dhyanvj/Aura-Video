import { AgentEventT } from "../api";

// Per-project timeline view (docs/DECISIONS_V3.md §5): idea -> published
// with timestamps. Derived entirely from the orchestrator's own AgentEvent
// log (agent="orchestrator" is where every major status transition is
// already logged) rather than a new backend field - no schema change needed.
export default function ProjectTimeline({ events }: { events: AgentEventT[] }) {
  const milestones = events
    .filter((e) => e.agent === "orchestrator")
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  if (milestones.length === 0) {
    return <p className="text-xs text-slate-500 dark:text-slate-400">No timeline events yet.</p>;
  }

  return (
    <ol className="flex flex-col gap-3">
      {milestones.map((event, i) => (
        <li key={event.id} className="relative flex gap-3 pl-1">
          <div className="flex flex-col items-center">
            <span
              className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${
                event.type === "error" ? "bg-rose-500" : "bg-accent"
              }`}
            />
            {i < milestones.length - 1 && <span className="w-px flex-1 bg-border" />}
          </div>
          <div className="min-w-0 pb-1">
            <p className={`text-xs ${event.type === "error" ? "text-rose-700 dark:text-rose-300" : "text-slate-700 dark:text-slate-300"}`}>
              {event.message}
            </p>
            <p className="text-[11px] text-slate-500 dark:text-slate-500">
              {new Date(event.created_at).toLocaleString()}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
