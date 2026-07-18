import { useEffect, useState } from "react";
import { api, Project, RescueEligibility } from "../api";

// Shown on a FAILED project's detail page: checks (fresh, not the cached
// badge) whether a usable render actually exists and offers either the
// override ("Mark as Successful") or a plain retry - never both implied at
// once, and never automatic.
export default function RescuePanel({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [eligibility, setEligibility] = useState<RescueEligibility | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedCandidate, setSelectedCandidate] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = () => {
    setLoading(true);
    setError(null);
    api
      .getRescueEligibility(project.id)
      .then((result) => {
        setEligibility(result);
        setSelectedCandidate(result.candidates[0]?.id);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(check, [project.id]);

  const override = async () => {
    if (!eligibility) return;
    const chosen = eligibility.candidates.find((c) => c.id === selectedCandidate) ?? eligibility.candidates[0];
    const confirmed = window.confirm(
      `Override this project's Failed status and send it to review?\n\n` +
        `Original failure reason:\n${project.failure_reason || "(none recorded)"}\n\n` +
        `Render to use: ${chosen.label}${chosen.recorded_at ? ` (${new Date(chosen.recorded_at).toLocaleString()})` : ""}\n\n` +
        `This does not approve anything - the project goes to the normal review flow, where you still Approve, ` +
        `Request Changes, or Reject it.`,
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await api.rescueProject(project.id, chosen.id);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.retryProject(project.id);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-border bg-panel p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Rescue</h2>
      {loading && <p className="text-xs text-slate-500 dark:text-slate-400">Checking for a usable render...</p>}
      {error && <div className="mb-2 rounded bg-rose-100 dark:bg-rose-950/50 p-2 text-xs text-rose-700 dark:text-rose-300">{error}</div>}

      {!loading && eligibility && eligibility.eligible && (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-slate-600 dark:text-slate-300">
            A usable rendered video exists for this project and passed a fresh technical re-check (audio present and
            audible, correct duration and resolution, file not corrupt).
          </p>
          {eligibility.candidates.length > 1 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                Which render? ({eligibility.candidates.length} valid options)
              </label>
              <div className="flex flex-col gap-1">
                {eligibility.candidates.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-xs text-slate-700 dark:text-slate-300">
                    <input
                      type="radio"
                      name="rescue-candidate"
                      checked={selectedCandidate === c.id}
                      onChange={() => setSelectedCandidate(c.id)}
                    />
                    {c.label}
                    {c.recorded_at && <span className="text-slate-500">({new Date(c.recorded_at).toLocaleString()})</span>}
                  </label>
                ))}
              </div>
            </div>
          )}
          <button
            onClick={override}
            disabled={busy}
            className="self-start rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            Override failure — send to review
          </button>
        </div>
      )}

      {!loading && eligibility && !eligibility.eligible && (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-rose-600 dark:text-rose-400">No usable render exists — {eligibility.reason}.</p>
          <button
            onClick={retry}
            disabled={busy}
            className="self-start rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
          >
            Retry production
          </button>
        </div>
      )}
    </section>
  );
}
