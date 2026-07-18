import os
import subprocess
from typing import List

from PIL import Image, ImageDraw, ImageFont

from app.utils import utils

# Fractions of the video's duration used for the hook / mid / climax candidate frames.
_CANDIDATE_TIMESTAMP_FRACTIONS = (0.1, 0.5, 0.85)


def generate_thumbnail_candidates(video_path: str, video_duration: float, hook_text: str, out_dir: str) -> List[str]:
    """
    Extracts 3 candidate frames (hook/mid/climax timestamps) via ffmpeg and
    overlays the hook text on each with Pillow. Returns the JPG file paths -
    JPEG rather than PNG so every candidate is directly downloadable/usable
    as a platform-ready thumbnail without a separate export step.
    """
    if video_duration <= 0:
        return []

    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = utils.get_ffmpeg_binary()
    candidates = []
    for index, fraction in enumerate(_CANDIDATE_TIMESTAMP_FRACTIONS):
        timestamp = video_duration * fraction
        raw_path = os.path.join(out_dir, f"thumb-raw-{index + 1}.jpg")
        subprocess.run(
            [ffmpeg, "-y", "-ss", f"{timestamp:.2f}", "-i", video_path, "-frames:v", "1", "-q:v", "2", raw_path],
            capture_output=True,
            timeout=30,
        )
        if not os.path.isfile(raw_path):
            continue
        final_path = os.path.join(out_dir, f"thumbnail-{index + 1}.jpg")
        _overlay_hook_text(raw_path, hook_text, final_path)
        os.remove(raw_path)
        candidates.append(final_path)
    return candidates


def _overlay_hook_text(image_path: str, text: str, out_path: str) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin = int(width * 0.08)
    font_size = int(width * 0.09)
    font = ImageFont.truetype(_bold_font_path(), font_size)

    lines = _wrap_text(text, draw, font, max_width=width - 2 * margin)
    line_height = int(font_size * 1.2)
    y = height - margin - line_height * len(lines)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill="#FFFFFF",
            stroke_width=max(2, font_size // 12),
            stroke_fill="#000000",
        )
        y += line_height

    image.save(out_path, "JPEG", quality=92)


def _wrap_text(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]  # cap at 3 lines to keep text within safe margins


def _bold_font_path() -> str:
    font_dir = utils.font_dir()
    for name in ("BeVietnamPro-Bold.ttf", "MicrosoftYaHeiBold.ttc"):
        candidate = os.path.join(font_dir, name)
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"no bold font found in {font_dir}")
