import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.agents.schemas import Finding, QuoteOrLesson, ResearchDossier
from app.services import subtitle as subtitle_service
from app.utils import utils

_MIN_DURATION_SECONDS = 15
_MAX_DURATION_SECONDS = 60
_EXPECTED_RESOLUTION = (1080, 1920)
_MIN_FILE_SIZE_BYTES = 10_000

# Content types held to a strict "every factual claim must be dossier-backed"
# standard, where a violation is critical rather than major (incident fix
# §3's per-content-type fact-check standard). Shared with orchestrator.py
# (which imports this rather than keeping its own copy) so the freshness/
# evidence gate and the QA fact-check severity never drift apart.
NEWS_CONTENT_TYPES = {"ai_news", "world_news"}


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


# Severity tiers per deterministic check (incident fix §1 - "map every
# deterministic check in qa.py to a severity in code", never inferred by an
# LLM). duration_15_to_60s stays "major" (not "critical"): a revision can
# plausibly fix it (shorter script), unlike a genuinely broken/unusable file.
CHECK_SEVERITY = {
    "file_exists": "critical",
    "file_size": "critical",
    "ffprobe": "critical",
    "duration_15_to_60s": "major",
    "resolution_1080x1920": "critical",
    "audio_present": "critical",
    "audio_duration_matches_voiceover": "critical",
    "subtitle_alignment": "major",
}

# "Hard technical criticals" (incident fix §2): missing/corrupt audio or a
# corrupted/unreadable file genuinely need a re-render - unlike every other
# finding, a human at NEEDS_HUMAN_REVIEW can never override these away.
HARD_TECHNICAL_CRITICAL_CHECKS = {"file_exists", "file_size", "ffprobe", "audio_present", "audio_duration_matches_voiceover"}


def severity_for_check(name: str) -> str:
    return CHECK_SEVERITY.get(name, "major")


def is_overridable_check(name: str) -> bool:
    return name not in HARD_TECHNICAL_CRITICAL_CHECKS


def aggregate_verdict(findings: List[Finding]) -> str:
    """
    Verdict mapping (incident fix §1): pass_with_warnings only when every
    finding is minor (minors never block/trigger a revision on their own),
    revise when at least one major finding exists (and no critical), fail
    when at least one critical finding exists.
    """
    severities = {f.severity for f in findings}
    if "critical" in severities:
        return "fail"
    if "major" in severities:
        return "revise"
    if "minor" in severities:
        return "pass_with_warnings"
    return "pass"


# Precedence used to pick ONE revision_target when several actionable
# (major/critical) findings coexist - (category, forced_target) pairs
# checked in order; the first match wins. Splitting "technical" into a
# creative_director bucket (script-only-fixable checks, e.g. duration) ahead
# of its producer bucket preserves the existing rule that a duration failure
# must never be routed to Producer, who can't fix spoken duration by
# swapping footage - even if the vision model's own guess (the "visual"
# bucket) said otherwise.
_TARGET_PRIORITY = [
    ("quote_attribution", None),
    ("fact_check", None),
    ("technical", "creative_director"),
    ("script_repetition", None),
    ("technical", "producer"),
    ("visual", None),
    ("content_policy", None),
]


def pick_revision_target(findings: List[Finding]) -> Optional[str]:
    actionable = [f for f in findings if f.severity in ("critical", "major")]
    for category, forced_target in _TARGET_PRIORITY:
        for f in actionable:
            if f.category != category:
                continue
            if forced_target is not None and f.revision_target != forced_target:
                continue
            if f.revision_target:
                return f.revision_target
    return next((f.revision_target for f in actionable if f.revision_target), None)


def fingerprint_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "unknown"


def _normalize_text(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _fuzzy_match(a: Optional[str], b: Optional[str], threshold: float = 0.85) -> bool:
    normalized_a, normalized_b = _normalize_text(a), _normalize_text(b)
    if not normalized_a or not normalized_b:
        return False
    return difflib.SequenceMatcher(None, normalized_a, normalized_b).ratio() >= threshold


def verify_quote_against_dossier(
    quote: QuoteOrLesson, dossier: ResearchDossier
) -> Optional[Tuple[str, str]]:
    """
    Incident fix §3: QA (and the script-approval gate) trust the Researcher's
    dossier rather than re-deriving their own opinion of a quote's accuracy -
    that re-litigation is exactly what let a correctly-attributed, already-
    verified quote get flagged "uncertain" post-render. Returns None if the
    dossier backs this quote (fuzzy-matched to tolerate punctuation/whitespace
    differences), else (reason, severity) - severity is "critical" only when
    the Researcher's own sources disputed/debunked this specific wording
    (closest thing to "a factually FALSE claim"), "major" for a merely
    unverified or mismatched quote per the Motivational content-type standard
    ("the quote itself must be verified (major if not)").
    """
    verified = dossier.verified_quote
    if verified is not None and verified.verification_status == "disputed" and _fuzzy_match(quote.text, verified.text):
        return (
            f'the Researcher\'s sources disputed this quote/attribution: "{quote.text}" - {quote.attribution!r}',
            "critical",
        )
    if verified is None or verified.verification_status != "verified":
        return (
            "the Researcher's dossier has no verified quote for this project; this quote/attribution is unconfirmed",
            "major",
        )
    if not _fuzzy_match(quote.text, verified.text):
        return (
            f"script wording {quote.text!r} doesn't match the Researcher's verified wording {verified.text!r}",
            "major",
        )
    if _normalize_text(quote.attribution) != _normalize_text(verified.attribution):
        return (
            f"attribution {quote.attribution!r} doesn't match the Researcher's verified attribution {verified.attribution!r}",
            "major",
        )
    return None


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
