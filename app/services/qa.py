import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.services import subtitle as subtitle_service
from app.utils import utils

_MIN_DURATION_SECONDS = 15
_MAX_DURATION_SECONDS = 60
_EXPECTED_RESOLUTION = (1080, 1920)
_MIN_FILE_SIZE_BYTES = 10_000


@dataclass
class TechnicalCheckResult:
    name: str
    passed: bool
    detail: str


def _resolve_ffprobe_binary() -> str:
    ffmpeg_bin = utils.get_ffmpeg_binary()
    ffmpeg_dir = os.path.dirname(ffmpeg_bin)
    if ffmpeg_dir:
        candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    system_ffprobe = shutil.which("ffprobe")
    return system_ffprobe or "ffprobe"


def _srt_timestamp_to_seconds(timestamp: str) -> float:
    hours, minutes, rest = timestamp.strip().split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def probe_video(video_path: str) -> dict:
    ffprobe = _resolve_ffprobe_binary()
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", video_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def run_technical_checks(
    video_path: str, subtitle_path: Optional[str] = None, expected_audio_duration: Optional[float] = None
) -> Tuple[List[TechnicalCheckResult], float]:
    """
    Deterministic technical checks: duration, resolution, audio presence, file
    size, subtitle existence/coverage, and (if `expected_audio_duration` is
    given) that the final mux's audio stream duration is within 2% of the
    voiceover's - a truncated/dropped audio track during the final render
    would otherwise still pass "audio_present" (stream exists) undetected.
    Returns (checks, video_duration_seconds).
    """
    if not os.path.isfile(video_path):
        return [TechnicalCheckResult("file_exists", False, f"video file not found: {video_path}")], 0.0

    checks: List[TechnicalCheckResult] = []
    size_bytes = os.path.getsize(video_path)
    checks.append(
        TechnicalCheckResult("file_size", size_bytes > _MIN_FILE_SIZE_BYTES, f"{size_bytes} bytes")
    )

    try:
        probe = probe_video(video_path)
    except Exception as exc:  # noqa: BLE001 - ffprobe failures are a check result, not a crash
        checks.append(TechnicalCheckResult("ffprobe", False, str(exc)))
        return checks, 0.0

    duration = float(probe.get("format", {}).get("duration", 0))
    checks.append(
        TechnicalCheckResult(
            "duration_15_to_60s",
            _MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS,
            f"{duration:.1f}s",
        )
    )

    streams = probe.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream:
        width, height = video_stream.get("width"), video_stream.get("height")
        checks.append(
            TechnicalCheckResult(
                "resolution_1080x1920", (width, height) == _EXPECTED_RESOLUTION, f"{width}x{height}"
            )
        )
    else:
        checks.append(TechnicalCheckResult("resolution_1080x1920", False, "no video stream found"))

    checks.append(
        TechnicalCheckResult(
            "audio_present",
            audio_stream is not None,
            "audio stream found" if audio_stream else "no audio stream",
        )
    )

    if audio_stream is not None and expected_audio_duration:
        audio_duration = float(audio_stream.get("duration") or 0)
        tolerance = expected_audio_duration * 0.02
        checks.append(
            TechnicalCheckResult(
                "audio_duration_matches_voiceover",
                abs(audio_duration - expected_audio_duration) <= tolerance,
                f"final audio stream is {audio_duration:.1f}s, voiceover was {expected_audio_duration:.1f}s",
            )
        )

    if subtitle_path:
        checks.append(_check_subtitle_alignment(subtitle_path, duration))

    return checks, duration


def _check_subtitle_alignment(subtitle_path: str, video_duration: float) -> TechnicalCheckResult:
    if not os.path.isfile(subtitle_path):
        return TechnicalCheckResult("subtitle_alignment", False, f"subtitle file not found: {subtitle_path}")

    lines = subtitle_service.file_to_subtitles(subtitle_path)
    if not lines:
        return TechnicalCheckResult("subtitle_alignment", False, "subtitle file has no lines")

    _, times, _ = lines[-1]
    end_str = times.split("-->")[-1].strip()
    end_seconds = _srt_timestamp_to_seconds(end_str)
    coverage = end_seconds / video_duration if video_duration else 0
    passed = 0.5 <= coverage <= 1.1
    return TechnicalCheckResult(
        "subtitle_alignment", passed, f"subtitles cover {coverage:.0%} of the {video_duration:.1f}s video"
    )


def extract_frames(video_path: str, video_duration: float, count: int = 8) -> List[str]:
    """Extracts `count` evenly spaced frames as JPEGs into a fresh temp directory."""
    if video_duration <= 0:
        return []

    ffmpeg = utils.get_ffmpeg_binary()
    out_dir = tempfile.mkdtemp(prefix="qa_frames_")
    frame_paths = []
    for i in range(count):
        timestamp = video_duration * (i + 0.5) / count
        out_path = os.path.join(out_dir, f"frame-{i + 1}.jpg")
        subprocess.run(
            [ffmpeg, "-y", "-ss", f"{timestamp:.2f}", "-i", video_path, "-frames:v", "1", "-q:v", "2", out_path],
            capture_output=True,
            timeout=30,
        )
        if os.path.isfile(out_path):
            frame_paths.append(out_path)
    return frame_paths
