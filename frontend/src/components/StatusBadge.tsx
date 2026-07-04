const STATUS_COLORS: Record<string, string> = {
  IDEA_PENDING: "bg-slate-600",
  IDEA_READY: "bg-slate-600",
  SCRIPTING: "bg-sky-600",
  SCRIPT_READY: "bg-sky-600",
  PRODUCING: "bg-amber-600",
  RENDERED: "bg-amber-600",
  QA_REVIEW: "bg-amber-600",
  QA_PASSED: "bg-emerald-700",
  AWAITING_HUMAN_APPROVAL: "bg-fuchsia-700",
  APPROVED: "bg-emerald-700",
  PUBLISHING: "bg-emerald-700",
  PUBLISHED: "bg-emerald-600",
  TRACKING: "bg-emerald-600",
  ARCHIVED: "bg-slate-700",
  FAILED: "bg-rose-700",
  REJECTED: "bg-rose-700",
};

export default function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "bg-slate-600";
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium text-white ${color}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
