"""
Shared audio validation used right after TTS (task.py) and as a post-render
QA check (qa.py) - a single source of truth for "does this audio file
actually contain audible sound," measured from the real file via ffprobe/
ffmpeg rather than trusted from a TTS provider's own metadata.

Root cause this exists to close: TTS SubMaker objects carry word/sentence
boundary timing metadata that can be non-empty even if the actual audio
payload from the provider was empty, truncated, or corrupt - nothing
previously checked the real file, so a provider hiccup could silently
produce an unplayable or silent video.
"""

import os
import re
import shutil
import subprocess
from typing import Optional

from app.utils import utils

SILENCE_THRESHOLD_DB = -50.0
MIN_AUDIO_DURATION_SECONDS = 1.0


def _resolve_ffprobe_binary() -> str:
    ffmpeg_bin = utils.get_ffmpeg_binary()
    ffmpeg_dir = os.path.dirname(ffmpeg_bin)
    if ffmpeg_dir:
        candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe") or "ffprobe"


def probe_audio_duration(path: str) -> float:
    """Real, measured duration (seconds) of the first audio stream in `path`
    via ffprobe. Returns 0.0 if the file has no audio stream, doesn't exist,
    or can't be read - never raises, since this is used as a hard gate."""
    if not path or not os.path.isfile(path):
        return 0.0
    try:
        result = subprocess.run(
            [
                _resolve_ffprobe_binary(), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return 0.0


def measure_mean_volume_db(path: str) -> Optional[float]:
    """Mean volume in dBFS via ffmpeg's volumedetect filter. None if it can't
    be measured (missing file, no audio stream, or ffmpeg failure)."""
    if not path or not os.path.isfile(path):
        return None
    try:
        result = subprocess.run(
            [utils.get_ffmpeg_binary(), "-i", path, "-af", "volumedetect", "-vn", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr or "")
    return float(match.group(1)) if match else None


def check_audible(
    path: str,
    min_duration: float = MIN_AUDIO_DURATION_SECONDS,
    silence_threshold_db: float = SILENCE_THRESHOLD_DB,
) -> tuple[bool, str]:
    """
    Hard gate: (ok, reason). ok is True only if `path` has a real audio
    stream, that stream is at least `min_duration` seconds (measured from
    the file, not from any provider-supplied metadata), and its mean volume
    is above `silence_threshold_db` (i.e. not effectively silent).
    """
    duration = probe_audio_duration(path)
    if duration < min_duration:
        return False, f"audio duration is {duration:.2f}s (expected at least {min_duration:.0f}s)"
    mean_db = measure_mean_volume_db(path)
    if mean_db is None:
        return False, "could not measure audio volume (no audio stream or unreadable file)"
    if mean_db < silence_threshold_db:
        return False, f"audio is effectively silent (mean volume {mean_db:.1f} dB, threshold {silence_threshold_db:.0f} dB)"
    return True, ""
