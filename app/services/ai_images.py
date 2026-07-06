"""
Budget-tier AI image generation (docs/DECISIONS_V3.md §6): Pollinations'
free image endpoint (no API key) turned into a short Ken-Burns video clip via
ffmpeg's zoompan filter, so it can drop into the existing renderer's clip
list exactly like any downloaded stock clip - no scene-timing model or
vision-scoring needed (that's the deferred, full DESIGN_V2.md Visual
Director), just a stock-fallback for search terms with no usable coverage.

Opt-in per content type (ContentTypeTemplate.visual_strategy.ai_gen_allowed)
and degrades silently to "no clip" on any failure - this is a fallback path,
never a hard dependency for a render to succeed.
"""

import os
import subprocess
from typing import Optional
from urllib.parse import quote

import requests
from loguru import logger

from app.utils import utils

_REQUEST_TIMEOUT = 30
_IMAGE_WIDTH = 1080
_IMAGE_HEIGHT = 1920


def _fetch_pollinations_image(prompt: str, save_path: str) -> bool:
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}"
    try:
        response = requests.get(
            url,
            params={"width": _IMAGE_WIDTH, "height": _IMAGE_HEIGHT, "nologo": "true"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(response.content)
        return os.path.getsize(save_path) > 0
    except Exception as exc:  # noqa: BLE001 - an AI-image fallback failing must never fail the render
        logger.warning(f"Pollinations image generation failed for prompt={prompt!r}: {exc}")
        return False


def generate_ai_image_clip(prompt: str, duration_seconds: float, save_dir: str) -> Optional[str]:
    """
    Generates a Pollinations image for `prompt` and turns it into a
    `duration_seconds`-long 1080x1920 video clip with a slow Ken Burns
    zoom, saved into save_dir. Returns the clip's local path, or None if
    generation/encoding failed at any step (caller falls back to skipping
    this clip, exactly as if a stock search had returned nothing).
    """
    if duration_seconds <= 0:
        return None

    os.makedirs(save_dir, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in prompt.lower())[:40].strip("-") or "ai-image"
    image_path = os.path.join(save_dir, f"ai-image-{slug}.jpg")
    clip_path = os.path.join(save_dir, f"ai-clip-{slug}.mp4")

    if not _fetch_pollinations_image(prompt, image_path):
        return None

    ffmpeg = utils.get_ffmpeg_binary()
    fps = 30
    total_frames = max(1, int(duration_seconds * fps))
    # Slow zoom-in over the clip's full duration - the simplest Ken Burns
    # treatment; direction/rate variation is Visual Director scope (deferred).
    zoompan = (
        f"scale=8000:-1,zoompan=z='min(zoom+0.0008,1.3)':d={total_frames}:"
        f"s={_IMAGE_WIDTH}x{_IMAGE_HEIGHT}:fps={fps}"
    )
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-loop", "1", "-i", image_path,
                "-vf", zoompan,
                "-t", f"{duration_seconds:.2f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                clip_path,
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except Exception as exc:  # noqa: BLE001 - an AI-image fallback failing must never fail the render
        logger.warning(f"Ken Burns encode failed for prompt={prompt!r}: {exc}")
        return None
    finally:
        if os.path.isfile(image_path):
            os.remove(image_path)

    return clip_path if os.path.isfile(clip_path) else None
