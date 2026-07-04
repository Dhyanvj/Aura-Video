import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, SeriesT } from "../api";

export default function SeriesPage() {
  const navigate = useNavigate();
  const [seriesList, setSeriesList] = useState<SeriesT[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api.listSeries().then(setSeriesList).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    refresh();
  }, []);

  const toggleExpand = async (id: number) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    const detail = await api.getSeries(id);
    setSeriesList((prev) => prev.map((s) => (s.id === id ? detail : s)));
    setExpanded(id);
  };

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Series</h1>
      <p className="mb-6 text-sm text-slate-400">
        Recurring characters, voice, and style locked across episodes via each series' Bible.
      </p>

      {error && <div className="mb-4 rounded bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>}

      {seriesList.length === 0 && !error && (
        <p className="text-slate-400">
          No series yet. Start one from the New Video flow by choosing "Start a new series" for a series-capable
          content type.
        </p>
      )}

      <div className="flex flex-col gap-4">
        {seriesList.map((s) => (
          <div key={s.id} className="rounded-lg border border-border bg-panel p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">{s.title}</h2>
                <p className="text-xs text-slate-400">
                  {s.content_type_id} &middot; {s.episode_count} episode{s.episode_count === 1 ? "" : "s"} &middot;
                  voice: {s.voice_id || "not locked yet"}
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => toggleExpand(s.id)}
                  className="rounded border border-border bg-panel2 px-3 py-1 text-xs text-slate-300 hover:border-accent"
                >
                  {expanded === s.id ? "Hide episodes" : "Show episodes"}
                </button>
                <button
                  onClick={() => navigate(`/new?series_id=${s.id}`)}
                  className="rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500"
                >
                  + Next Episode
                </button>
              </div>
            </div>

            {s.rolling_summary && (
              <p className="mb-2 whitespace-pre-wrap rounded bg-panel2 p-2 text-xs text-slate-400">
                {s.rolling_summary}
              </p>
            )}

            {expanded === s.id && (
              <div className="mt-2 flex flex-col gap-1">
                {(s.episodes || []).map((ep) => (
                  <Link
                    key={ep.id}
                    to={`/projects/${ep.id}`}
                    className="flex items-center justify-between rounded border border-border bg-panel2 px-3 py-1.5 text-xs text-slate-300 hover:border-accent"
                  >
                    <span>
                      Episode {ep.episode_number}: {ep.topic || "(topic pending)"}
                    </span>
                    <span className="text-slate-500">{ep.status}</span>
                  </Link>
                ))}
                {(s.episodes || []).length === 0 && <div className="text-xs text-slate-600">No episodes yet.</div>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
