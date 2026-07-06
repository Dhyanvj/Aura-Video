import { useEffect, useState } from "react";
import { api, ContentTypeTemplate, Project, SeriesT } from "../api";

export interface ProjectFilterState {
  search: string;
  contentTypeId: string;
  status: string;
  seriesId: string;
  dateFrom: string;
  dateTo: string;
}

export const EMPTY_FILTERS: ProjectFilterState = {
  search: "",
  contentTypeId: "",
  status: "",
  seriesId: "",
  dateFrom: "",
  dateTo: "",
};

const ALL_STATUSES = [
  "IDEA_PENDING", "IDEA_READY", "RESEARCHING", "RESEARCH_READY", "SCRIPTING", "SCRIPT_READY",
  "PRODUCING", "RENDERED", "QA_REVIEW", "QA_PASSED", "AWAITING_HUMAN_APPROVAL", "APPROVED",
  "PUBLISHING", "PUBLISHED", "TRACKING", "ARCHIVED", "FAILED", "REJECTED",
];

// Global search + filters (docs/DECISIONS_V3.md §5): content type, status,
// series, date range, plus free-text search across topic/niche. Applied
// client-side against whatever project list the page already fetched - the
// project list sizes involved here don't need a server-side filter endpoint.
export function applyProjectFilters(projects: Project[], filters: ProjectFilterState): Project[] {
  return projects.filter((p) => {
    if (filters.search) {
      const haystack = `${p.topic ?? ""} ${p.niche ?? ""}`.toLowerCase();
      if (!haystack.includes(filters.search.toLowerCase())) return false;
    }
    if (filters.contentTypeId && p.content_type_id !== filters.contentTypeId) return false;
    if (filters.status && p.status !== filters.status) return false;
    if (filters.seriesId && String(p.series_id ?? "") !== filters.seriesId) return false;
    if (filters.dateFrom && new Date(p.created_at) < new Date(filters.dateFrom)) return false;
    if (filters.dateTo && new Date(p.created_at) > new Date(`${filters.dateTo}T23:59:59`)) return false;
    return true;
  });
}

export default function ProjectFilters({
  filters,
  onChange,
}: {
  filters: ProjectFilterState;
  onChange: (next: ProjectFilterState) => void;
}) {
  const [contentTypes, setContentTypes] = useState<ContentTypeTemplate[]>([]);
  const [series, setSeries] = useState<SeriesT[]>([]);

  useEffect(() => {
    api.listContentTypes().then(setContentTypes).catch(() => undefined);
    api.listSeries().then(setSeries).catch(() => undefined);
  }, []);

  const set = (patch: Partial<ProjectFilterState>) => onChange({ ...filters, ...patch });
  const hasActiveFilters = Object.values(filters).some(Boolean);

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <input
        value={filters.search}
        onChange={(e) => set({ search: e.target.value })}
        placeholder="Search topic or niche..."
        className="min-w-[10rem] flex-1 rounded border border-border bg-panel2 px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-500 dark:text-slate-100 dark:placeholder:text-slate-500"
      />
      <select
        value={filters.contentTypeId}
        onChange={(e) => set({ contentTypeId: e.target.value })}
        className="rounded border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-700 dark:text-slate-300"
      >
        <option value="">All content types</option>
        {contentTypes.map((ct) => (
          <option key={ct.id} value={ct.id}>
            {ct.label}
          </option>
        ))}
      </select>
      <select
        value={filters.status}
        onChange={(e) => set({ status: e.target.value })}
        className="rounded border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-700 dark:text-slate-300"
      >
        <option value="">All statuses</option>
        {ALL_STATUSES.map((s) => (
          <option key={s} value={s}>
            {s.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <select
        value={filters.seriesId}
        onChange={(e) => set({ seriesId: e.target.value })}
        className="rounded border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-700 dark:text-slate-300"
      >
        <option value="">All series</option>
        {series.map((s) => (
          <option key={s.id} value={String(s.id)}>
            {s.title}
          </option>
        ))}
      </select>
      <input
        type="date"
        value={filters.dateFrom}
        onChange={(e) => set({ dateFrom: e.target.value })}
        className="rounded border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-700 dark:text-slate-300"
      />
      <span className="text-xs text-slate-500">to</span>
      <input
        type="date"
        value={filters.dateTo}
        onChange={(e) => set({ dateTo: e.target.value })}
        className="rounded border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-700 dark:text-slate-300"
      />
      {hasActiveFilters && (
        <button onClick={() => onChange(EMPTY_FILTERS)} className="text-xs text-accent hover:underline">
          Clear filters
        </button>
      )}
    </div>
  );
}
