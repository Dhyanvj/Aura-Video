import { useEffect, useMemo, useRef, useState } from "react";
import { api, Finding, Project, taskFileUrl } from "../api";
import { useLiveUpdates } from "../ws";

const ALL_PLATFORMS = ["tiktok", "instagram", "youtube"];
const AUTOSAVE_DEBOUNCE_MS = 800;

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-rose-700",
  major: "bg-amber-600",
  minor: "bg-slate-600",
};

function EscalatedReviewCard({ project, onResolved }: { project: Project; onResolved: () => void }) {
  const latestReport = project.qa_reports?.[project.qa_reports.length - 1];
  const findings: Finding[] = latestReport?.findings ?? [];
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmPolicyRisk, setConfirmPolicyRisk] = useState(false);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const videoUrl = project.video_url || "";

  const toggle = (fingerprint: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fingerprint)) next.delete(fingerprint);
      else next.add(fingerprint);
      return next;
    });
  };

  const hasPolicyFindingSelected = findings.some((f) => selected.has(f.fingerprint) && f.category === "content_policy");

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onResolved();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-orange-700/50 bg-panel p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{project.topic || `#${project.id}`}</span>
        <span className="rounded bg-orange-700 px-2 py-0.5 text-xs font-medium text-white">Needs your review</span>
      </div>
      {project.escalation_reason && (
        <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">{project.escalation_reason}</p>
      )}
      {error && <div className="mb-2 rounded bg-rose-100 dark:bg-rose-950/50 p-2 text-xs text-rose-700 dark:text-rose-300">{error}</div>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          {videoUrl && (
            <video src={videoUrl} controls className="mx-auto max-h-[50vh] w-full rounded-lg border border-border bg-black" />
          )}
        </div>
        <div className="flex flex-col gap-3">
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
              QA findings ({findings.length}) - check any you want to approve despite
            </p>
            <div className="flex flex-col gap-1">
              {findings.map((f) => (
                <label
                  key={f.fingerprint}
                  className="flex items-start gap-2 rounded bg-panel2 p-1.5 text-xs text-slate-700 dark:text-slate-300"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    disabled={!f.overridable}
                    checked={selected.has(f.fingerprint)}
                    onChange={() => toggle(f.fingerprint)}
                  />
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold text-white ${SEVERITY_COLORS[f.severity] ?? "bg-slate-600"}`}>
                    {f.severity}
                  </span>
                  <span className="flex-1">
                    {f.message}
                    {!f.overridable && <span className="ml-1 italic text-rose-500">(requires a re-render, cannot be overridden)</span>}
                  </span>
                </label>
              ))}
              {findings.length === 0 && <p className="text-xs text-slate-500 dark:text-slate-400">No structured findings recorded.</p>}
            </div>
          </div>

          {hasPolicyFindingSelected && (
            <label className="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400">
              <input type="checkbox" checked={confirmPolicyRisk} onChange={(e) => setConfirmPolicyRisk(e.target.checked)} />
              I understand overriding a content-policy finding may risk platform strikes
            </label>
          )}

          <button
            onClick={() => run(() => api.approveDespiteFindings(project.id, Array.from(selected), confirmPolicyRisk))}
            disabled={busy}
            className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            Approve despite findings
          </button>

          <div>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Notes for a fresh revision attempt"
              className="w-full rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-900 dark:text-slate-100"
            />
            <div className="mt-1 flex gap-2">
              <button
                onClick={() => run(() => api.requestChangesFromReview(project.id, notes.trim()))}
                disabled={busy || !notes.trim()}
                className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
              >
                Request changes
              </button>
              <button
                onClick={() => run(() => api.rejectFromReview(project.id, notes.trim()))}
                disabled={busy}
                className="rounded bg-rose-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-600 disabled:opacity-50"
              >
                Reject project
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ApprovalQueue() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [escalatedProjects, setEscalatedProjects] = useState<Project[]>([]);
  const [readyToPublish, setReadyToPublish] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedTitle, setSelectedTitle] = useState("");
  const [selectedDescription, setSelectedDescription] = useState("");
  const [selectedThumb, setSelectedThumb] = useState<string | undefined>(undefined);
  const [platforms, setPlatforms] = useState<string[]>(["tiktok", "instagram"]);
  const [rejectNotes, setRejectNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [publishUrls, setPublishUrls] = useState<Record<number, { platform: string; url: string }>>({});
  const rejectNotesRef = useRef<HTMLTextAreaElement>(null);

  const refresh = () => {
    api
      .listProjects()
      .then((all) => {
        setProjects(all.filter((p) => p.status === "AWAITING_HUMAN_APPROVAL"));
        setEscalatedProjects(all.filter((p) => p.status === "NEEDS_HUMAN_REVIEW"));
        // docs/DECISIONS_V3.md §4: Approve stops at APPROVED while publishing
        // is frozen - this is where a human downloads + posts manually, then
        // records it via Mark as Published. Full "Approved" queue view
        // polish (search/filters, dedicated page) lands in v3 Milestone 4.
        setReadyToPublish(all.filter((p) => p.status === "APPROVED"));
      })
      .catch((e) => setError(String(e)));
  };

  useEffect(refresh, []);
  useLiveUpdates(
    () => refresh(),
    () => refresh(),
  );

  const selected = useMemo(() => projects.find((p) => p.id === selectedId) ?? projects[0], [projects, selectedId]);

  useEffect(() => {
    if (selected?.publish_package) {
      setSelectedTitle(selected.publish_package.title_options[0] || selected.topic || "");
      setSelectedDescription(selected.publish_package.description || "");
      setSelectedThumb(selected.publish_package.thumbnail_candidates?.[0]);
    }
    setSaveStatus("idle");
  }, [selected?.id]);

  // Autosave (docs/DECISIONS_V3.md §5, reduced clicks): debounced so typing
  // doesn't fire a request per keystroke, and skipped on the render right
  // after switching projects (the effect above already reset these fields
  // to the server's own values, so there's nothing to save there).
  useEffect(() => {
    if (!selected) return;
    const packageTitle = selected.publish_package?.title_options[0] ?? "";
    const packageDescription = selected.publish_package?.description ?? "";
    if (selectedTitle === packageTitle && selectedDescription === packageDescription) return;

    setSaveStatus("saving");
    const projectId = selected.id;
    const timer = setTimeout(() => {
      api
        .updateProjectMetadata(projectId, { title: selectedTitle, description: selectedDescription })
        .then(() => setSaveStatus("saved"))
        .catch(() => setSaveStatus("idle"));
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTitle, selectedDescription]);

  const togglePlatform = (platform: string) => {
    setPlatforms((prev) => (prev.includes(platform) ? prev.filter((p) => p !== platform) : [...prev, platform]));
  };

  const approve = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.approveProject(selected.id, platforms, selectedThumb);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const requestChanges = async () => {
    if (!selected || !rejectNotes.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.rejectProject(selected.id, rejectNotes.trim());
      setRejectNotes("");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Keyboard shortcuts on Final Review (docs/DECISIONS_V3.md §5): a=approve,
  // r=jump to the request-changes notes field. Ignored while typing in a
  // text field/textarea/select so normal typing (e.g. the title input, or
  // writing the notes themselves) never triggers a shortcut mid-sentence.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (isTyping || e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "a" && selected && !busy) {
        e.preventDefault();
        approve();
      } else if (e.key === "r") {
        e.preventDefault();
        rejectNotesRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, busy, platforms, selectedThumb]);

  const markPublished = async (projectId: number) => {
    setBusy(true);
    setError(null);
    try {
      const entry = publishUrls[projectId];
      const platformUrls = entry?.platform ? [{ platform: entry.platform, url: entry.url || undefined }] : [];
      await api.markPublished(projectId, platformUrls);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const readyToPublishSection = readyToPublish.length > 0 && (
    <div className="mb-6">
      <h2 className="mb-2 text-lg font-semibold text-slate-900 dark:text-slate-100">Ready to Publish ({readyToPublish.length})</h2>
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
        Publishing is paused - download the video, post it manually, then record it here.
      </p>
      <div className="flex flex-col gap-2">
        {readyToPublish.map((p) => {
          const entry = publishUrls[p.id] || { platform: "youtube", url: "" };
          const videoUrl = p.video_url || "";
          return (
            <div key={p.id} className="flex flex-wrap items-center gap-2 rounded border border-border bg-panel p-2">
              <span className="min-w-[10rem] text-sm text-slate-800 dark:text-slate-200">{p.topic || `#${p.id}`}</span>
              {videoUrl && (
                <a href={videoUrl} download className="text-xs text-accent hover:underline">
                  Download video
                </a>
              )}
              <select
                value={entry.platform}
                onChange={(e) => setPublishUrls((prev) => ({ ...prev, [p.id]: { ...entry, platform: e.target.value } }))}
                className="rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-800 dark:text-slate-200"
              >
                {["youtube", "tiktok", "instagram"].map((platform) => (
                  <option key={platform} value={platform}>
                    {platform}
                  </option>
                ))}
              </select>
              <input
                value={entry.url}
                onChange={(e) => setPublishUrls((prev) => ({ ...prev, [p.id]: { ...entry, url: e.target.value } }))}
                placeholder="Live URL (optional)"
                className="min-w-[12rem] flex-1 rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-900 dark:text-slate-100"
              />
              <button
                onClick={() => markPublished(p.id)}
                disabled={busy}
                className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                Mark as Published
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );

  const escalatedSection = escalatedProjects.length > 0 && (
    <div className="mb-6">
      <h2 className="mb-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
        Needs Your Review ({escalatedProjects.length})
      </h2>
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
        QA escalated these - the revision budget was exhausted, or a finding recurred that a revision can't fix.
        The render is preserved and playable; decide below.
      </p>
      <div className="flex flex-col gap-3">
        {escalatedProjects.map((p) => (
          <EscalatedReviewCard key={p.id} project={p} onResolved={refresh} />
        ))}
      </div>
    </div>
  );

  if (projects.length === 0) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-semibold text-slate-900 dark:text-slate-100">Approval Queue</h1>
        {escalatedSection}
        {readyToPublishSection}
        {readyToPublish.length === 0 && escalatedProjects.length === 0 && (
          <p className="text-slate-500 dark:text-slate-400">Nothing is waiting for approval right now.</p>
        )}
      </div>
    );
  }

  const videoUrl = selected ? selected.video_url || "" : "";
  const pkg = selected?.publish_package;

  return (
    <div>
      {escalatedSection}
      {readyToPublishSection}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
      <div className="lg:col-span-1">
        <h1 className="mb-4 text-xl font-semibold text-slate-900 dark:text-slate-100">Approval Queue ({projects.length})</h1>
        <div className="flex flex-col gap-2">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`rounded border p-2 text-left text-sm ${
                selected?.id === p.id
                  ? "border-accent bg-panel2 text-white"
                  : "border-border bg-panel text-slate-600 dark:text-slate-300 hover:border-accent"
              }`}
            >
              {p.topic || `#${p.id}`}
            </button>
          ))}
        </div>
      </div>

      {selected && (
        <div className="lg:col-span-3 grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            {videoUrl && (
              <video src={videoUrl} controls className="mx-auto max-h-[70vh] rounded-lg border border-border bg-black" />
            )}
          </div>

          <div className="flex flex-col gap-4">
            {error && <div className="rounded bg-rose-100 dark:bg-rose-950/50 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div>}

            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-500">
              <span>
                Shortcuts: <kbd className="rounded bg-panel2 px-1">a</kbd> approve &middot;{" "}
                <kbd className="rounded bg-panel2 px-1">r</kbd> request changes
              </span>
              <span>
                {saveStatus === "saving" && "Saving..."}
                {saveStatus === "saved" && "Saved"}
              </span>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Title</label>
              <input
                value={selectedTitle}
                onChange={(e) => setSelectedTitle(e.target.value)}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
              />
              {pkg && pkg.title_options.length > 1 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {pkg.title_options.map((t, i) => (
                    <button
                      key={i}
                      onClick={() => setSelectedTitle(t)}
                      className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400 hover:text-white"
                    >
                      Use option {i + 1}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {pkg && (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Description & tags</label>
                <textarea
                  value={selectedDescription}
                  onChange={(e) => setSelectedDescription(e.target.value)}
                  rows={4}
                  className="w-full rounded border border-border bg-panel2 p-2 text-xs text-slate-600 dark:text-slate-300"
                />
                <div className="mt-1 flex flex-wrap gap-1">
                  {pkg.tags.map((tag, i) => (
                    <span key={i} className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {pkg && pkg.thumbnail_candidates?.length > 0 && (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Thumbnail</label>
                <div className="flex gap-2">
                  {pkg.thumbnail_candidates.map((path, i) => {
                    const url = taskFileUrl(selected.task_id, path);
                    return (
                      <div key={i} className="flex flex-col items-center gap-1">
                        <img
                          src={url}
                          onClick={() => setSelectedThumb(path)}
                          className={`h-24 w-24 cursor-pointer rounded border-2 object-cover ${
                            selectedThumb === path ? "border-accent" : "border-transparent"
                          }`}
                        />
                        <a
                          href={url}
                          download={`thumbnail-${i + 1}.jpg`}
                          onClick={(e) => e.stopPropagation()}
                          className="text-[10px] text-accent hover:underline"
                        >
                          Download JPG
                        </a>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Platforms</label>
              <div className="flex gap-3">
                {ALL_PLATFORMS.map((platform) => (
                  <label key={platform} className="flex items-center gap-1.5 text-sm text-slate-800 dark:text-slate-200">
                    <input
                      type="checkbox"
                      checked={platforms.includes(platform)}
                      onChange={() => togglePlatform(platform)}
                    />
                    {platform}
                  </label>
                ))}
              </div>
            </div>

            <button
              onClick={approve}
              disabled={busy || platforms.length === 0}
              className="rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              Approve & Publish
            </button>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Request changes (notes)</label>
              <textarea
                ref={rejectNotesRef}
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
                rows={3}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
                placeholder="What should the Creative Director fix?"
              />
              <button
                onClick={requestChanges}
                disabled={busy || !rejectNotes.trim()}
                className="mt-2 rounded bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
              >
                Request Changes
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
