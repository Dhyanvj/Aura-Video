import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { api, Project, taskFileUrl } from "../api";
import { useLiveUpdates } from "../ws";
import StatusBadge from "../components/StatusBadge";
import ProjectTimeline from "../components/ProjectTimeline";
import ScriptReviewPanel from "../components/ScriptReviewPanel";

export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api.getProject(projectId).then(setProject).catch((e) => setError(String(e)));
  };

  useEffect(refresh, [projectId]);
  useLiveUpdates(
    (msg) => {
      if (msg.project_id === projectId) refresh();
    },
    () => refresh(),
  );

  if (error) return <div className="rounded bg-rose-100 dark:bg-rose-950/50 p-4 text-rose-700 dark:text-rose-300">{error}</div>;
  if (!project) return <div className="text-slate-500 dark:text-slate-400">Loading...</div>;

  const videoUrl = taskFileUrl(project.task_id, project.video_path);

  const deleteProject = async (permanent: boolean) => {
    const publishedWarning =
      project.status === "PUBLISHED" || project.status === "TRACKING"
        ? "\n\nThis project is published - performance tracking for it will stop."
        : "";
    const confirmed = permanent
      ? window.confirm(
          `Permanently delete "${project.topic || `#${project.id}`}"? This cannot be undone.${publishedWarning}`,
        )
      : window.confirm(`Move "${project.topic || `#${project.id}`}" to the Recycle Bin? You can restore it later.${publishedWarning}`);
    if (!confirmed) return;
    try {
      await api.deleteProject(project.id, permanent);
      navigate("/pipeline");
    } catch (e) {
      window.alert(String(e));
    }
  };

  return (
    <div>
      <Link
        to="/pipeline"
        className="mb-4 inline-block text-sm text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
      >
        &larr; Back to pipeline
      </Link>

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">{project.topic || "(topic pending)"}</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            #{project.id} &middot; {project.niche || "no niche"}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-600 dark:text-slate-300">
          <StatusBadge status={project.status} />
          <span>${project.cost_usd.toFixed(3)}</span>
          <span>revision {project.revision_count}</span>
          <button
            onClick={() => deleteProject(false)}
            className="rounded border border-border px-2 py-1 text-xs text-slate-600 hover:border-amber-500 hover:text-amber-500 dark:text-slate-300"
          >
            Move to Recycle Bin
          </button>
          <button
            onClick={() => deleteProject(true)}
            className="rounded border border-border px-2 py-1 text-xs text-rose-600 hover:border-rose-500 dark:text-rose-400"
          >
            Delete Permanently
          </button>
        </div>
      </div>

      {project.failure_reason && (
        <div className="mb-6 rounded bg-rose-100 dark:bg-rose-950/50 p-3 text-sm text-rose-700 dark:text-rose-300">{project.failure_reason}</div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 flex flex-col gap-6">
          {project.status === "AWAITING_SCRIPT_APPROVAL" && (
            <ScriptReviewPanel project={project} onChanged={refresh} />
          )}

          <section className="rounded-lg border border-border bg-panel p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Timeline</h2>
            <ProjectTimeline events={project.events || []} />
          </section>

          {videoUrl && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Rendered Video</h2>
              {/* docs/REVIEW_FINDINGS.md: was a fixed max-h-[480px]. These are
                  9:16 vertical shorts (VideoAspect.portrait) - aspect-[9/16]
                  keeps it correctly proportioned at any viewport, including
                  the 390px mobile width Final Review needs to work at. */}
              <video
                src={videoUrl}
                controls
                className="mx-auto aspect-[9/16] max-h-[70vh] w-full max-w-xs rounded bg-black"
              />
            </section>
          )}

          {project.research_evidence && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Researcher — Evidence</h2>
                {project.research_evidence.reduced_verification && (
                  <span className="rounded bg-rose-700 px-2 py-0.5 text-xs font-medium text-white">
                    reduced verification
                  </span>
                )}
              </div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{project.research_evidence.topic}</p>
              {project.research_evidence.why_now && (
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{project.research_evidence.why_now}</p>
              )}
              {project.research_evidence.key_facts.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1.5 text-xs">
                  {project.research_evidence.key_facts.map((fact, i) => (
                    <li key={i} className="rounded border border-border bg-panel2 p-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-slate-800 dark:text-slate-200">{fact.statement}</span>
                        <span
                          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            fact.confidence === "verified"
                              ? "bg-emerald-700 text-white"
                              : fact.confidence === "myth" || fact.confidence === "disputed"
                                ? "bg-rose-700 text-white"
                                : "bg-amber-700 text-white"
                          }`}
                        >
                          {fact.confidence}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {project.research_evidence.disputed_points.length > 0 && (
                <div className="mt-2 text-xs text-amber-300">
                  Disputed: {project.research_evidence.disputed_points.join("; ")}
                </div>
              )}
              {project.research_evidence.sources.length > 0 && (
                <div className="mt-3 flex flex-col gap-1 text-xs text-slate-500 dark:text-slate-400">
                  {project.research_evidence.sources.map((source, i) => (
                    <a
                      key={i}
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="truncate text-sky-400 hover:underline"
                    >
                      {source.title || source.url}
                      {source.published_or_accessed ? ` — ${source.published_or_accessed}` : ""}
                    </a>
                  ))}
                </div>
              )}
            </section>
          )}

          {project.trend_report && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Trend Scout — Ranked Ideas</h2>
              <div className="flex flex-col gap-2">
                {project.trend_report.ideas
                  .slice()
                  .sort((a, b) => b.opportunity_score - a.opportunity_score)
                  .map((idea, i) => (
                    <div key={i} className="rounded border border-border bg-panel2 p-3">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-900 dark:text-slate-100">{idea.title}</span>
                        <span className="text-xs text-slate-500 dark:text-slate-400">score {idea.opportunity_score}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{idea.why_trending}</p>
                      <div className="mt-1 flex gap-2 text-xs text-slate-500 dark:text-slate-500">
                        <span>{idea.suggested_format}</span>
                        <span>&middot;</span>
                        <span>{idea.estimated_competition} competition</span>
                        <span>&middot;</span>
                        <span>{idea.target_emotion}</span>
                      </div>
                    </div>
                  ))}
              </div>
            </section>
          )}

          {project.brief && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Creative Director — Script</h2>
              <p className="whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">{project.brief.script}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {project.brief.search_terms.map((term, i) => (
                  <span key={i} className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {term}
                  </span>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500 dark:text-slate-400">
                <div>Voice: {project.brief.voice_recommendation}</div>
                <div>Music: {project.brief.music_direction}</div>
                <div>Subtitles: {project.brief.subtitle_style}</div>
                <div>BGM file: {project.brief.bgm_file || "random"}</div>
              </div>
            </section>
          )}

          {project.qa_reports && project.qa_reports.length > 0 && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Quality Reviewer</h2>
              <div className="flex flex-col gap-3">
                {project.qa_reports.map((report, i) => (
                  <div key={i} className="rounded border border-border bg-panel2 p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs text-slate-500 dark:text-slate-500">Review {i + 1}</span>
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          report.overall === "pass"
                            ? "bg-emerald-700 text-white"
                            : report.overall === "fail"
                              ? "bg-rose-700 text-white"
                              : "bg-amber-700 text-white"
                        }`}
                      >
                        {report.overall}
                      </span>
                    </div>
                    <ul className="mb-2 flex flex-col gap-1 text-xs">
                      {report.technical_checks.map((check, j) => (
                        <li key={j} className={check.passed ? "text-emerald-400" : "text-rose-400"}>
                          {check.passed ? "✓" : "✗"} {check.name}: {check.detail}
                        </li>
                      ))}
                    </ul>
                    {report.revision_notes && (
                      <p className="text-xs text-amber-300">
                        Revision target: {report.revision_target} — {report.revision_notes}
                      </p>
                    )}
                    {report.fact_check_flags?.filter((f) => !f.supported).length > 0 && (
                      <ul className="mt-2 flex flex-col gap-1 text-xs text-rose-700 dark:text-rose-300">
                        {report.fact_check_flags
                          .filter((f) => !f.supported)
                          .map((f, j) => (
                            <li key={j}>
                              Unsupported: "{f.sentence}"{f.note ? ` — ${f.note}` : ""}
                            </li>
                          ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {project.publish_package && (
            <section className="rounded-lg border border-border bg-panel p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Publisher — Package</h2>
              <ul className="mb-2 list-disc pl-5 text-sm text-slate-800 dark:text-slate-200">
                {project.publish_package.title_options.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
              <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">{project.publish_package.description}</p>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {project.publish_package.tags.map((tag, i) => (
                  <span key={i} className="rounded bg-panel2 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400">
                    #{tag}
                  </span>
                ))}
              </div>
              {project.publish_package.thumbnail_candidates?.length > 0 && (
                <div className="flex gap-3">
                  {project.publish_package.thumbnail_candidates.map((path, i) => (
                    <img
                      key={i}
                      src={taskFileUrl(project.task_id, path)}
                      className="h-40 rounded border border-border object-cover"
                    />
                  ))}
                </div>
              )}
              {project.published_posts && (
                <div className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                  Published: {JSON.stringify(project.published_posts)}
                </div>
              )}
            </section>
          )}
        </div>

        <div>
          <section className="rounded-lg border border-border bg-panel p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Agent Activity</h2>
            <div className="flex max-h-[70vh] flex-col gap-2 overflow-y-auto pr-1">
              {(project.events || []).map((event) => (
                <div key={event.id} className="rounded border border-border bg-panel2 p-2 text-xs">
                  <div className="mb-1 flex items-center justify-between text-slate-500 dark:text-slate-500">
                    <span className="font-medium text-slate-600 dark:text-slate-300">{event.agent}</span>
                    <span>{new Date(event.created_at).toLocaleTimeString()}</span>
                  </div>
                  <p className={event.type === "error" ? "text-rose-400" : "text-slate-600 dark:text-slate-300"}>{event.message}</p>
                  {(event.tokens_in || event.cost_usd) && (
                    <p className="mt-1 text-slate-600">
                      {event.tokens_in ?? 0} in / {event.tokens_out ?? 0} out tokens
                      {event.cost_usd ? ` — $${event.cost_usd.toFixed(4)}` : ""}
                    </p>
                  )}
                </div>
              ))}
              {(!project.events || project.events.length === 0) && (
                <div className="text-xs text-slate-600">No activity yet.</div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
