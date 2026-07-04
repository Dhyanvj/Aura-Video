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
  created_at: string;
  updated_at: string;
  events?: AgentEventT[];
}

export type QualityPreset = "budget" | "standard" | "cinematic";

export interface ContentTypeTemplate {
  id: string;
  label: string;
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

export interface CreateProjectPayload {
  topic?: string;
  niche?: string;
  audience?: string;
  content_type_id?: string;
  quality_preset?: QualityPreset;
  series_mode?: "none" | "new" | "continue";
  series_title?: string;
  series_id?: number;
}

export interface Settings {
  niche: string;
  audience: string;
  autopilot_level: string;
  max_revisions: number;
  schedule_enabled: boolean;
  videos_per_day: number;
  run_at: string;
  default_platforms: string[];
  monthly_budget_usd: number;
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
