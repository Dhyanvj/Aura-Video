import { useEffect, useState } from "react";
import { api, Analytics as AnalyticsT } from "../api";

export default function Analytics() {
  const [data, setData] = useState<AnalyticsT | null>(null);

  useEffect(() => {
    api.getAnalytics().then(setData);
  }, []);

  if (!data) return <div className="text-slate-400">Loading...</div>;

  const budgetUsed = data.monthly_budget_cap_usd > 0 ? data.monthly_spend_usd / data.monthly_budget_cap_usd : 0;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Analytics</h1>
      <p className="mb-6 text-sm text-slate-400">
        Per-video view/like/comment tracking from the Performance Analyst, checked at 24h and 72h post-publish.
      </p>

      {!data.youtube_configured && (
        <div className="mb-6 rounded bg-amber-950/40 p-3 text-sm text-amber-300">
          Analytics not configured — set [trends] youtube_api_key in config.toml to enable view/like/comment
          tracking.
        </div>
      )}

      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="text-2xl font-semibold text-slate-100">{data.videos.length}</div>
          <div className="text-xs text-slate-400">Tracked / archived videos</div>
        </div>
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="text-2xl font-semibold text-slate-100">${data.monthly_spend_usd.toFixed(2)}</div>
          <div className="text-xs text-slate-400">Spend this month</div>
        </div>
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="text-2xl font-semibold text-slate-100">
            {data.monthly_budget_cap_usd > 0 ? `${(budgetUsed * 100).toFixed(0)}%` : "No cap"}
          </div>
          <div className="text-xs text-slate-400">
            {data.monthly_budget_cap_usd > 0 ? `of $${data.monthly_budget_cap_usd.toFixed(2)} cap` : "Set a cap in Settings"}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        {data.videos.map((video) => (
          <div key={video.project_id} className="rounded-lg border border-border bg-panel p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-medium text-slate-100">{video.topic}</span>
              <span className="text-xs text-slate-500">
                {video.niche} &middot; ${video.cost_usd.toFixed(3)}
              </span>
            </div>
            <div className="flex gap-4">
              {video.checkpoints.map((cp, i) => (
                <div key={i} className="rounded border border-border bg-panel2 p-2 text-xs">
                  <div className="mb-1 font-medium text-slate-300">{cp.checkpoint_hours}h checkpoint</div>
                  <div className="text-slate-400">
                    {cp.views} views &middot; {cp.likes} likes &middot; {cp.comments} comments
                  </div>
                  {cp.note && <div className="mt-1 text-slate-500">{cp.note}</div>}
                </div>
              ))}
              {video.checkpoints.length === 0 && <div className="text-xs text-slate-600">No checkpoints yet.</div>}
            </div>
          </div>
        ))}
        {data.videos.length === 0 && <p className="text-sm text-slate-500">No published videos being tracked yet.</p>}
      </div>
    </div>
  );
}
