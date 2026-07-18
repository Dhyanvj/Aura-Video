from typing import List, Optional

from loguru import logger

from app.agents.base import BaseAgent
from app.agents.schemas import PublishPackage
from app.services import qa as qa_service
from app.services import thumbnails as thumbnails_service
from app.services import upload_post
from app.utils import utils

_SYSTEM_PROMPT = """You are the Publisher for a short-form vertical video channel.
Given the video's script and niche, prepare a publish package: exactly 3 title
options (each <=100 characters, hook-driven), a description with hashtags,
10-15 tags, and a category suggestion.

Also produce per-platform caption variants for YouTube Shorts, Instagram Reels,
and TikTok, respecting each platform's norms (YouTube Shorts descriptions can be
longer and SEO-oriented; Instagram and TikTok captions should be punchier and
front-load the hook, well under 2200 characters).

Suggest a posting time (as a human-readable string, e.g. "Weekday evenings,
6-8pm local time") based on typical norms for the given niche.

Flag anything that looks like a medical, financial, or legal claim, a
copyrighted-music reference, or content that could trip platform guidelines."""


class Publisher(BaseAgent):
    agent_name = "publisher"

    def prepare(self, script: str, niche: str, hook_text: str, video_path: str, task_id: str) -> dict:
        """
        Builds the publish package (metadata + thumbnail candidates). Never
        publishes anything - that only happens via publish() after a human
        clicks Approve.
        """
        payload = {"script": script, "niche": niche}
        package = self.call_json(system=_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=PublishPackage)

        _, duration = qa_service.run_technical_checks(video_path)
        out_dir = utils.task_dir(task_id)
        thumbnails = thumbnails_service.generate_thumbnail_candidates(
            video_path=video_path, video_duration=duration, hook_text=hook_text, out_dir=out_dir
        )
        self.log_event(
            "output",
            message=f"Prepared publish package with {len(thumbnails)} thumbnail candidates",
            payload={"thumbnails": thumbnails},
        )

        result = package.model_dump()
        result["thumbnail_candidates"] = thumbnails
        return result

    def publish(self, video_path: str, package: dict, platforms: List[str], thumbnail_path: Optional[str] = None) -> list:
        """
        Publishes to the given platforms via Upload-Post. Must only be called
        after a human has explicitly approved - the Orchestrator enforces this,
        never this method itself, but it's never wired to fire automatically.
        """
        if not upload_post.upload_post_service.is_configured():
            raise RuntimeError("Upload-Post is not configured (see [app] upload_post_* in config.toml)")

        title_options = package.get("title_options") or []
        title = title_options[0] if title_options else "Check this out!"

        youtube_extra = None
        variant = next(
            (v for v in package.get("platform_variants", []) if v.get("platform") == "youtube_shorts"), None
        )
        if variant and any(p.startswith("youtube") for p in platforms):
            youtube_extra = {
                "youtube_title": title[:100],
                "youtube_description": variant.get("caption", ""),
                "tags": package.get("tags", []),
                "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
                "containsSyntheticMedia": True,
            }

        result = upload_post.upload_post_service.upload_video(
            video_path=video_path, title=title, platforms=platforms, youtube_extra=youtube_extra
        )
        self.log_event(
            "output",
            message="Published" if result.get("success") else f"Publish failed: {result.get('error')}",
            payload=result,
        )
        if not result.get("success"):
            logger.warning(f"publish failed for {video_path}: {result}")
        return [result]
