export interface AgentEventT {
  id: number;
  agent: string;
  type: string;
  message: string;
  payload: unknown;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  created_at: string;
}

export interface TrendIdea {
  title: string;
  why_trending: string;
  evidence: string[];
  target_emotion: string;
  estimated_competition: string;
  suggested_format: string;
  opportunity_score: number;
}

export interface TrendReport {
  ideas: TrendIdea[];
}

export interface SourceCitation {
  url: string;
  title: string;
  published_or_accessed: string;
}

export interface KeyFact {
  statement: string;
  citations: SourceCitation[];
  confidence: string; // verified | single-source | disputed | myth
}

export interface ResearchDossier {
  topic: string;
  why_now: string;
  key_facts: KeyFact[];
  disputed_points: string[];
  suggested_angle: string;
  sources: SourceCitation[];
  freshness_window_hours: number | null;
  reduced_verification: boolean;
}

export interface CreativeBrief {
  script: string;
  search_terms: string[];
  music_direction: string;
  bgm_file: string | null;
  voice_recommendation: string;
  subtitle_style: string;
  metadata_draft: { working_title: string; hook_variants: string[] };
}

export interface QAReport {
  overall: string;
  technical_checks: { name: string; passed: boolean; detail: string }[];
  frame_findings: { frame_index: number; matches_script: boolean; issues: string[]; notes: string }[];
  content_policy_flags: string[];
  revision_target: string | null;
  revision_notes: string | null;
  fact_check_flags: { sentence: string; supported: boolean; note: string }[];
}

export interface PublishPackage {
  title_options: string[];
  description: string;
  tags: string[];
  category: string;
  platform_variants: { platform: string; caption: string; hashtags: string[] }[];
  suggested_posting_time: string;
  content_policy_flags: string[];
  thumbnail_candidates: string[];
}

export interface Project {
  id: number;
  status: string;
  niche: string | null;
  topic: string | null;
  trend_report: TrendReport | null;
  research_evidence: ResearchDossier | null;
  brief: CreativeBrief | null;
  qa_reports: QAReport[] | null;
  publish_package: PublishPackage | null;
  published_posts: Record<string, unknown>[] | null;
  task_id: string | null;
  video_path: string | null;
  cost_usd: number;
  revision_count: number;
  failure_reason: string | null;
  content_type_id: string | null;
  quality_preset: string | null;
  series_id: number | null;
  episode_number: number | null;
  approval_mode: string | null;
  script_revision_count: number;
  created_at: string;
  updated_at: string;
  events?: AgentEventT[];
}

export interface RecycleBinItem {
  id: number;
  topic: string | null;
  status_before_delete: string | null;
  deleted_at: string;
  days_remaining: number | null;
  size_bytes: number;
  has_thumbnail: boolean;
  was_published: boolean;
}

export type QualityPreset = "budget" | "standard" | "cinematic";

export interface ContentTypeTemplate {
  id: string;
  label: string;
  description: string;
  default_duration_s: number;
  scriptcraft_overrides: Record<string, unknown>;
  visual_strategy: Record<string, unknown>;
  voice_style: string;
  subtitle_theme: string;
  music_palette: string;
  research_required: boolean;
  freshness_window_hours: number | null;
  series_capable: boolean;
  default_quality_preset: QualityPreset;
}

export interface SeriesEpisode {
  id: number;
  episode_number: number | null;
  topic: string | null;
  status: string;
  created_at: string;
}

export interface SeriesT {
  id: number;
  content_type_id: string;
  title: string;
  style_guide: Record<string, unknown>;
  voice_id: string;
  voice_delivery_settings: Record<string, unknown>;
  music_palette: Record<string, unknown>;
  character_reference: Record<string, unknown> | null;
  pronunciation_dictionary: Record<string, unknown>;
  episode_counter: number;
  episode_count: number;
  rolling_summary: string;
  status: string;
  created_at: string;
  updated_at: string;
  episodes?: SeriesEpisode[];
}

export type ApprovalMode = "manual" | "automatic";

export interface CreateProjectPayload {
  topic?: string;
  niche?: string;
  audience?: string;
  content_type_id?: string;
  quality_preset?: QualityPreset;
  series_mode?: "none" | "new" | "continue";
  series_title?: string;
  series_id?: number;
  approval_mode_override?: ApprovalMode;
}

export interface Settings {
  niche: string;
  audience: string;
  approval_mode: ApprovalMode;
  max_revisions: number;
  max_script_regenerations: number;
  schedule_enabled: boolean;
  videos_per_day: number;
  run_at: string;
  default_platforms: string[];
  monthly_budget_usd: number;
  recycle_bin_retention_days: number;
  anthropic_configured: boolean;
  youtube_configured: boolean;
  upload_post_configured: boolean;
  publishing_enabled: boolean;
}

export interface AnalyticsCheckpoint {
  checkpoint_hours: number;
  views: number;
  likes: number;
  comments: number;
  note: string | null;
}

export interface AnalyticsVideo {
  project_id: number;
  topic: string | null;
  niche: string | null;
  cost_usd: number;
  published_at: string | null;
  checkpoints: AnalyticsCheckpoint[];
}

export interface Analytics {
  youtube_configured: boolean;
  monthly_spend_usd: number;
  monthly_budget_cap_usd: number;
  videos: AnalyticsVideo[];
}

export interface PlaybookBullet {
  text: string;
  enabled: boolean;
  source_lesson_ids?: number[];
  flagged_for_review?: boolean;
}

export interface PlaybookT {
  id: number;
  agent: string;
  content_type_id: string | null;
  version: number;
  bullets: PlaybookBullet[];
  is_active: boolean;
  created_at: string;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.message || `request to ${path} failed with ${response.status}`);
  }
  return body.data as T;
}

export const api = {
  listProjects: () => apiFetch<{ projects: Project[] }>("/api/v1/projects").then((r) => r.projects),
  getProject: (id: number) => apiFetch<Project>(`/api/v1/projects/${id}`),
  createProject: (payload: CreateProjectPayload) =>
    apiFetch<{ project_id: number }>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  approveProject: (id: number, platforms: string[], thumbnailPath?: string) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ platforms, thumbnail_path: thumbnailPath }),
    }),
  rejectProject: (id: number, revisionNotes: string) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ revision_notes: revisionNotes }),
    }),
  retryProject: (id: number) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/retry`, { method: "POST" }),
  markPublished: (id: number, platformUrls: { platform: string; url?: string }[]) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/mark-published`, {
      method: "POST",
      body: JSON.stringify({ platform_urls: platformUrls }),
    }),
  updateProjectMetadata: (id: number, partial: { title?: string; description?: string }) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/metadata`, {
      method: "PATCH",
      body: JSON.stringify(partial),
    }),
  approveScript: (id: number) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/approve-script`, { method: "POST" }),
  rejectScript: (id: number, notes = "") =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/reject-script`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),
  regenerateScript: (id: number, notes = "") =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/regenerate-script`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),
  updateScript: (id: number, partial: { title?: string; script?: string }) =>
    apiFetch<{ project_id: number }>(`/api/v1/projects/${id}/script`, {
      method: "PATCH",
      body: JSON.stringify(partial),
    }),
  deleteProject: (id: number, permanent = false) =>
    apiFetch<{ project_id: number; permanent: boolean; warning?: string | null }>(`/api/v1/projects/${id}/delete`, {
      method: "POST",
      body: JSON.stringify({ permanent }),
    }),
  bulkDeleteProjects: (projectIds: number[], permanent = false) =>
    apiFetch<{ deleted: unknown[]; errors: { project_id: number; error: string }[] }>(
      "/api/v1/projects/bulk-delete",
      { method: "POST", body: JSON.stringify({ project_ids: projectIds, permanent }) },
    ),
  listRecycleBin: () => apiFetch<{ items: RecycleBinItem[] }>("/api/v1/recycle-bin").then((r) => r.items),
  restoreProject: (id: number) =>
    apiFetch<{ project_id: number; status: string }>(`/api/v1/recycle-bin/${id}/restore`, { method: "POST" }),
  purgeProject: (id: number) =>
    apiFetch<{ project_id: number; permanent: boolean }>(`/api/v1/recycle-bin/${id}/purge`, { method: "POST" }),
  listPlaybooks: () => apiFetch<{ playbooks: PlaybookT[] }>("/api/v1/playbooks").then((r) => r.playbooks),
  getPlaybookVersions: (agent: string, contentTypeId: string | null) =>
    apiFetch<{ versions: PlaybookT[] }>(
      `/api/v1/playbooks/versions?agent=${encodeURIComponent(agent)}${
        contentTypeId ? `&content_type_id=${encodeURIComponent(contentTypeId)}` : ""
      }`,
    ).then((r) => r.versions),
  updatePlaybookBullet: (playbookId: number, bulletIndex: number, partial: { enabled?: boolean; text?: string }) =>
    apiFetch<PlaybookT>(`/api/v1/playbooks/${playbookId}/bullets/${bulletIndex}`, {
      method: "PATCH",
      body: JSON.stringify(partial),
    }),
  rollbackPlaybook: (playbookId: number) =>
    apiFetch<PlaybookT>(`/api/v1/playbooks/${playbookId}/rollback`, { method: "POST" }),
  getSettings: () => apiFetch<Settings>("/api/v1/settings"),
  updateSettings: (partial: Partial<Settings>) =>
    apiFetch<Settings>("/api/v1/settings", { method: "PUT", body: JSON.stringify(partial) }),
  getAnalytics: () => apiFetch<Analytics>("/api/v1/analytics"),
  listContentTypes: () =>
    apiFetch<{ content_types: ContentTypeTemplate[] }>("/api/v1/content-types").then((r) => r.content_types),
  updateContentType: (id: string, partial: Partial<ContentTypeTemplate>) =>
    apiFetch<ContentTypeTemplate>(`/api/v1/content-types/${id}`, { method: "PUT", body: JSON.stringify(partial) }),
  listSeries: () => apiFetch<{ series: SeriesT[] }>("/api/v1/series").then((r) => r.series),
  getSeries: (id: number) => apiFetch<SeriesT>(`/api/v1/series/${id}`),
  createSeries: (content_type_id: string, title: string) =>
    apiFetch<{ series_id: number }>("/api/v1/series", {
      method: "POST",
      body: JSON.stringify({ content_type_id, title }),
    }),
};

// video_path/thumbnail paths from the API are absolute server-side filesystem
// paths; the static /tasks mount serves them by task_id + basename instead.
export function taskFileUrl(taskId: string | null | undefined, absolutePath: string | null | undefined): string {
  if (!taskId || !absolutePath) return "";
  const filename = absolutePath.split("/").pop();
  return `/tasks/${taskId}/${filename}`;
}
