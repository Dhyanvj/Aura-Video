import { useEffect, useRef, useState } from "react";
import { api, Project } from "../api";

const AUTOSAVE_DEBOUNCE_MS = 800;

export default function ScriptReviewPanel({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [title, setTitle] = useState(project.brief?.metadata_draft.working_title || "");
  const [script, setScript] = useState(project.brief?.script || "");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const notesRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setTitle(project.brief?.metadata_draft.working_title || "");
    setScript(project.brief?.script || "");
    setSaveStatus("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, project.script_revision_count]);

  // Autosave (mirrors Final Review's .../metadata pattern): debounced so
  // typing doesn't fire a request per keystroke, and skipped right after the
  // effect above resets these fields to the server's own values.
  useEffect(() => {
    const packageTitle = project.brief?.metadata_draft.working_title ?? "";
    const packageScript = project.brief?.script ?? "";
    if (title === packageTitle && script === packageScript) return;

    setSaveStatus("saving");
    const projectId = project.id;
    const timer = setTimeout(() => {
      api
        .updateScript(projectId, { title, script })
        .then(() => setSaveStatus("saved"))
        .catch((e) => {
          setError(String(e));
          setSaveStatus("idle");
        });
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, script]);

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.approveScript(project.id);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.regenerateScript(project.id, notes);
      setNotes("");
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const rejectTopic = async () => {
    if (!window.confirm("Reject this topic and return to idea stage? The current script is preserved under revisions.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.rejectScript(project.id, notes);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const brief = project.brief;
  const hook = brief?.metadata_draft.hook_variants[0];

  return (
    <section className="rounded-lg border-2 border-fuchsia-700 bg-panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-fuchsia-400">Script Review — approve before production begins</h2>
        <span className="text-xs text-slate-500 dark:text-slate-500">
          {saveStatus === "saving" && "Saving..."}
          {saveStatus === "saved" && "Saved"}
        </span>
      </div>

      {error && <div className="mb-3 rounded bg-rose-100 dark:bg-rose-950/50 p-2 text-xs text-rose-700 dark:text-rose-300">{error}</div>}

      {project.research_evidence && (
        <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
          Topic: <span className="text-slate-800 dark:text-slate-200">{project.research_evidence.topic}</span>
          {project.research_evidence.why_now && ` — ${project.research_evidence.why_now}`}
        </p>
      )}

      <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Title</label>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className="mb-3 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
      />

      {hook && (
        <div className="mb-2 rounded bg-fuchsia-950/30 p-2 text-xs text-fuchsia-300">
          <span className="font-semibold">Hook:</span> {hook}
        </div>
      )}

      <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Script</label>
      <textarea
        value={script}
        onChange={(e) => setScript(e.target.value)}
        rows={8}
        className="mb-3 w-full whitespace-pre-wrap rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
      />

      {brief && brief.search_terms.length > 0 && (
        <div className="mb-3">
          <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Scene plan (search terms)</label>
          <div className="flex flex-wrap gap-1.5">
            {brief.search_terms.map((term, i) => (
              <span key={i} className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400">
                {term}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mb-3 flex gap-2">
        <button
          onClick={approve}
          disabled={busy}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          Approve &amp; Continue
        </button>
      </div>

      <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
        Notes (for Regenerate or Reject topic)
      </label>
      <textarea
        ref={notesRef}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={2}
        placeholder="What should change?"
        className="mb-2 w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={regenerate}
          disabled={busy}
          className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
        >
          Regenerate with notes
        </button>
        <button
          onClick={rejectTopic}
          disabled={busy}
          className="rounded bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
        >
          Reject topic
        </button>
        <span className="text-xs text-slate-500 dark:text-slate-500">
          {project.script_revision_count} regeneration(s) so far
        </span>
      </div>
    </section>
  );
}
