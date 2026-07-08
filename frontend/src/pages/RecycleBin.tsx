import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, RecycleBinItem } from "../api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function RecycleBin() {
  const [items, setItems] = useState<RecycleBinItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = () => {
    api.listRecycleBin().then(setItems).catch((e) => setError(String(e)));
  };

  useEffect(refresh, []);

  const restore = async (item: RecycleBinItem) => {
    setBusyId(item.id);
    try {
      await api.restoreProject(item.id);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  const purge = async (item: RecycleBinItem) => {
    const confirmed = window.confirm(
      `Permanently delete "${item.topic || `#${item.id}`}"? This cannot be undone - the folder and all DB records are removed.`,
    );
    if (!confirmed) return;
    setBusyId(item.id);
    try {
      await api.purgeProject(item.id);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <Link to="/settings" className="mb-4 inline-block text-sm text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200">
        &larr; Back to settings
      </Link>
      <h1 className="mb-1 text-xl font-semibold text-slate-900 dark:text-slate-100">Recycle Bin</h1>
      <p className="mb-6 text-sm text-slate-500 dark:text-slate-400">
        Deleted projects stay here until their retention window passes, then a scheduled job removes them for good.
      </p>

      {error && <div className="mb-4 rounded bg-rose-100 dark:bg-rose-950/50 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div>}

      {items.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">The Recycle Bin is empty.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((item) => (
            <div
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-panel2 p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                  {item.topic || `#${item.id}`}
                </div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  was {item.status_before_delete} &middot; {formatBytes(item.size_bytes)}
                  {item.days_remaining !== null && ` · ${item.days_remaining} day(s) remaining`}
                  {item.was_published && " · published (fingerprint retained)"}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={() => restore(item)}
                  disabled={busyId === item.id}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  Restore
                </button>
                <button
                  onClick={() => purge(item)}
                  disabled={busyId === item.id}
                  className="rounded bg-rose-600 px-3 py-1 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
                >
                  Delete Permanently
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
