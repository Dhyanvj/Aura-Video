import { useEffect, useMemo, useState } from "react";
import { api, Project, taskFileUrl } from "../api";
import { useLiveUpdates } from "../ws";

const ALL_PLATFORMS = ["tiktok", "instagram", "youtube"];

export default function ApprovalQueue() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [readyToPublish, setReadyToPublish] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedTitle, setSelectedTitle] = useState("");
  const [selectedThumb, setSelectedThumb] = useState<string | undefined>(undefined);
  const [platforms, setPlatforms] = useState<string[]>(["tiktok", "instagram"]);
  const [rejectNotes, setRejectNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [publishUrls, setPublishUrls] = useState<Record<number, { platform: string; url: string }>>({});

  const refresh = () => {
    api
      .listProjects()
      .then((all) => {
        setProjects(all.filter((p) => p.status === "AWAITING_HUMAN_APPROVAL"));
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
      setSelectedThumb(selected.publish_package.thumbnail_candidates?.[0]);
    }
  }, [selected?.id]);

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
      <h2 className="mb-2 text-lg font-semibold text-slate-100">Ready to Publish ({readyToPublish.length})</h2>
      <p className="mb-3 text-xs text-slate-400">
        Publishing is paused - download the video, post it manually, then record it here.
      </p>
      <div className="flex flex-col gap-2">
        {readyToPublish.map((p) => {
          const entry = publishUrls[p.id] || { platform: "youtube", url: "" };
          const videoUrl = taskFileUrl(p.task_id, p.video_path);
          return (
            <div key={p.id} className="flex flex-wrap items-center gap-2 rounded border border-border bg-panel p-2">
              <span className="min-w-[10rem] text-sm text-slate-200">{p.topic || `#${p.id}`}</span>
              {videoUrl && (
                <a href={videoUrl} download className="text-xs text-accent hover:underline">
                  Download video
                </a>
              )}
              <select
                value={entry.platform}
                onChange={(e) => setPublishUrls((prev) => ({ ...prev, [p.id]: { ...entry, platform: e.target.value } }))}
                className="rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-200"
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
                className="min-w-[12rem] flex-1 rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-100"
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

  if (projects.length === 0) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-semibold text-slate-100">Approval Queue</h1>
        {readyToPublishSection}
        {readyToPublish.length === 0 && <p className="text-slate-400">Nothing is waiting for approval right now.</p>}
      </div>
    );
  }

  const videoUrl = selected ? taskFileUrl(selected.task_id, selected.video_path) : "";
  const pkg = selected?.publish_package;

  return (
    <div>
      {readyToPublishSection}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
      <div className="lg:col-span-1">
        <h1 className="mb-4 text-xl font-semibold text-slate-100">Approval Queue ({projects.length})</h1>
        <div className="flex flex-col gap-2">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`rounded border p-2 text-left text-sm ${
                selected?.id === p.id
                  ? "border-accent bg-panel2 text-white"
                  : "border-border bg-panel text-slate-300 hover:border-accent"
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
            {error && <div className="rounded bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>}

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Title</label>
              <input
                value={selectedTitle}
                onChange={(e) => setSelectedTitle(e.target.value)}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
              />
              {pkg && pkg.title_options.length > 1 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {pkg.title_options.map((t, i) => (
                    <button
                      key={i}
                      onClick={() => setSelectedTitle(t)}
                      className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-400 hover:text-white"
                    >
                      Use option {i + 1}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {pkg && (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-400">Description & tags</label>
                <p className="rounded border border-border bg-panel2 p-2 text-xs text-slate-300">{pkg.description}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {pkg.tags.map((tag, i) => (
                    <span key={i} className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-400">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {pkg && pkg.thumbnail_candidates?.length > 0 && (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-400">Thumbnail</label>
                <div className="flex gap-2">
                  {pkg.thumbnail_candidates.map((path, i) => {
                    const url = taskFileUrl(selected.task_id, path);
                    return (
                      <img
                        key={i}
                        src={url}
                        onClick={() => setSelectedThumb(path)}
                        className={`h-24 cursor-pointer rounded border-2 object-cover ${
                          selectedThumb === path ? "border-accent" : "border-transparent"
                        }`}
                      />
                    );
                  })}
                </div>
              </div>
            )}

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Platforms</label>
              <div className="flex gap-3">
                {ALL_PLATFORMS.map((platform) => (
                  <label key={platform} className="flex items-center gap-1.5 text-sm text-slate-200">
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
              <label className="mb-1 block text-xs font-medium text-slate-400">Request changes (notes)</label>
              <textarea
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
                rows={3}
                className="w-full rounded border border-border bg-panel2 px-3 py-2 text-sm text-slate-100"
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
