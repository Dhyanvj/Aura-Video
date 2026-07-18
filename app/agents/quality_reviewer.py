import base64
import shutil
from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import (
    FactCheckFlag,
    FactCheckResult,
    Finding,
    QAReport,
    QuoteOrLesson,
    ResearchDossier,
    TechnicalCheck,
    VisionReview,
)
from app.services import originality
from app.services import qa as qa_service
from app.utils import utils

_SYSTEM_PROMPT = """You are the Quality Reviewer for a short-form vertical video pipeline.
You are given several evenly-spaced frames from a rendered video, in chronological order,
along with the script that was used to produce it and a summary of automated technical
checks that already ran (duration, resolution, audio, subtitles).

For each frame, judge: does the visual roughly match what the script is saying around that
point in the video, is the frame black/broken/corrupted, does it contain a visible watermark,
and is any on-screen text readable. Also flag anything that looks like a medical, financial,
or legal claim, a copyrighted-music reference, or content that would violate typical
short-form platform guidelines (TikTok/YouTube Shorts/Instagram) in content_policy_flags.

Assign each frame_finding a severity - calibrate carefully, most videos should have few or no
major/critical findings, and polish preferences must never be inflated into real problems:
- "critical": the frame is black/broken/corrupted/unreadable, or shows a visible watermark -
  the video is unusable as rendered.
- "major": the visual clearly doesn't match what the script is saying at that point (wrong
  subject, wrong setting), or on-screen text/subtitles are genuinely hard to read - a real
  problem a revision could plausibly fix.
- "minor": a polish preference that does not block publishing - e.g. a frame is a bit darker
  than ideal, subtitle contrast could be better but the text is still legible, pacing could be
  tighter. List minor issues, don't escalate them just because there are several.
Give a one-line justification for each frame_finding's severity, and leave issues/severity at
their defaults (empty issues, "minor") for a frame with nothing worth flagging.

Give an overall verdict: "pass" if there are no major/critical findings, "revise" if there is at
least one major finding, "fail" if there is at least one critical finding. If not a clean pass,
set revision_target to "creative_director" for script/narrative problems or "producer" for
visual/material problems, and give concrete, actionable revision_notes."""


# Content-type-aware fact-check standards (incident fix §3). The default
# entry is intentionally strict-ish (treat any claim needing a source as
# needing dossier backing) but news types get the "every claim is critical"
# framing spelled out explicitly, and motivational gets the opposite
# framing: the exact bug in the incident was applying a news-grade "every
# claim needs evidence" standard to interpretive storytelling, which is the
# genre working as intended, not an unsupported claim.
_FACT_CHECK_SYSTEM_PROMPTS = {
    "ai_news": """Compare the video script against the verified research dossier a Researcher already
produced for this news story. This is a strict, news-grade standard: every sentence that states a
specific fact, name, date, number, or claim must be supported by the dossier's key_facts, or it is
unsupported. List every unsupported sentence as a flag with supported=false and a short note
explaining the mismatch; a sentence that's pure narration/framing/opinion with no factual claim
doesn't need a flag. If the dossier has reduced_verification=true or lists disputed_points, treat
any script sentence asserting one of those points as settled fact as unsupported too - the script
should hedge, not assert.""",
    "world_news": None,  # filled in below, identical standard to ai_news
    "motivational": """Compare the video script's QUOTE OR LESSON CENTERPIECE against the verified
research dossier a Researcher already produced. Only flag the centerpiece sentence(s) themselves if
they assert a specific fact that contradicts or isn't covered by the dossier (e.g. a fabricated date
or event tied to the quote's author). Do NOT flag the unpacking/interpretation sentences that
follow the centerpiece - explaining what a quote means in practice, drawing a life lesson from it,
or framing it with a relatable scenario is the genre working as intended, not an unsupported claim,
even though those sentences are naturally not independently sourced. Only flag a sentence outside
the centerpiece if it states a separate, checkable fact (a date, a statistic, a named external
event) that contradicts or isn't covered by the dossier.""",
    "fun_facts": """Compare the video script's central surprising fact against the verified research
dossier a Researcher already produced. The central fact itself must be supported by the dossier's
key_facts - flag it if it contradicts or isn't covered. Rhetorical color around it (framing,
enthusiasm, "isn't that wild") is allowed and should not be flagged as an unsupported claim.""",
    "trending_now": """Compare the video script against the verified research dossier a Researcher (or
Trend Scout signals reshaped into a dossier) already produced. Any DATED claim (a specific date,
recency claim like "just happened", or a statistic) must be supported by the dossier - flag it if
not. General commentary/reaction/opinion about the trend is allowed and should not be flagged.""",
}
_FACT_CHECK_SYSTEM_PROMPTS["world_news"] = _FACT_CHECK_SYSTEM_PROMPTS["ai_news"]
_DEFAULT_FACT_CHECK_SYSTEM_PROMPT = """Compare the video script against the verified research dossier a
Researcher already produced for this topic. For each sentence in the script that states a specific
fact, name, date, number, or claim, check whether it's actually supported by the dossier's
key_facts. List every unsupported sentence as a flag with supported=false and a short note
explaining the mismatch; a sentence that's just narration, framing, or opinion (no factual claim)
doesn't need a flag. If the dossier itself has reduced_verification=true or lists disputed_points,
treat any script sentence that states one of those points as settled fact as unsupported too - the
script should hedge, not assert."""


# Technical checks that only a script change can fix - routing these to
# "producer" would ask Producer to re-render with different footage, which
# does nothing about spoken duration and would just fail the same check
# again. duration_15_to_60s is a function of word count and speaking rate,
# not material selection.
_SCRIPT_ONLY_FIXABLE_CHECKS = {"duration_15_to_60s"}


class QualityReviewer(BaseAgent):
    agent_name = "quality_reviewer"

    def review(
        self,
        video_path: str,
        script: str,
        subtitle_path: Optional[str] = None,
        expected_audio_duration: Optional[float] = None,
        quote_or_lesson: Optional[QuoteOrLesson] = None,
        research_dossier: Optional[ResearchDossier] = None,
        prior_scripts: Optional[list] = None,
        content_type_id: Optional[str] = None,
    ) -> QAReport:
        technical_checks, duration = qa_service.run_technical_checks(
            video_path, subtitle_path, expected_audio_duration
        )
        self.log_event(
            "tool_call",
            message="Ran technical checks",
            payload={"checks": [c.__dict__ for c in technical_checks], "duration": duration},
        )

        findings: List[Finding] = self._technical_findings(technical_checks)

        frame_paths = qa_service.extract_frames(video_path, duration)
        self.log_event("tool_call", message=f"Extracted {len(frame_paths)} frames for vision review")

        try:
            if frame_paths:
                vision = self._run_vision_review(script, technical_checks, frame_paths)
            else:
                vision = VisionReview(
                    overall="revise",
                    frame_findings=[],
                    revision_target="producer",
                    revision_notes="No frames could be extracted from the rendered video.",
                )
        finally:
            self._cleanup(frame_paths)

        findings.extend(self._visual_findings(vision))

        if quote_or_lesson is not None and quote_or_lesson.is_quote:
            quote_finding = self._quote_attribution_finding(quote_or_lesson, research_dossier)
            if quote_finding is not None:
                findings.append(quote_finding)

        fact_check_flags: List[FactCheckFlag] = []
        if research_dossier is not None:
            fact_check_flags = self._run_fact_check(script, research_dossier, content_type_id)
            findings.extend(self._fact_check_findings(fact_check_flags, content_type_id))

        script_repetition_flag = None
        if prior_scripts:
            ratio, _ = originality.most_similar_script(script, prior_scripts)
            if ratio >= originality.SCRIPT_REPETITION_THRESHOLD:
                script_repetition_flag = (
                    f"{ratio:.0%} n-gram overlap with a recent script of this content type - too similar in "
                    "wording/structure."
                )
                findings.append(
                    Finding(
                        category="script_repetition",
                        fingerprint="script_repetition",
                        severity="major",
                        message=script_repetition_flag,
                        revision_target="creative_director",
                    )
                )

        overall = qa_service.aggregate_verdict(findings)
        revision_target = qa_service.pick_revision_target(findings) if overall in ("revise", "fail") else None
        revision_notes = self._build_revision_notes(findings, overall)

        report = QAReport(
            overall=overall,
            technical_checks=[
                TechnicalCheck(
                    name=c.name, passed=c.passed, detail=c.detail, severity=qa_service.severity_for_check(c.name)
                )
                for c in technical_checks
            ],
            frame_findings=vision.frame_findings,
            content_policy_flags=vision.content_policy_flags,
            revision_target=revision_target,
            revision_notes=revision_notes,
            fact_check_flags=fact_check_flags,
            script_repetition_flag=script_repetition_flag,
            findings=findings,
        )
        self.log_event("output", message=f"QA verdict: {report.overall}", payload=report.model_dump())
        return report

    @staticmethod
    def _technical_findings(technical_checks) -> List[Finding]:
        findings = []
        for c in technical_checks:
            if c.passed:
                continue
            script_only = c.name in _SCRIPT_ONLY_FIXABLE_CHECKS
            findings.append(
                Finding(
                    category="technical",
                    fingerprint=f"technical:{c.name}",
                    severity=qa_service.severity_for_check(c.name),
                    message=f"{c.name}: {c.detail}",
                    revision_target="creative_director" if script_only else "producer",
                    overridable=qa_service.is_overridable_check(c.name),
                )
            )
        return findings

    @staticmethod
    def _visual_findings(vision: VisionReview) -> List[Finding]:
        findings = []
        # Always trust the vision model's own overall/target even when its
        # per-frame detail doesn't carry issues text (e.g. the "no frames
        # extracted" fallback, or a loosely-populated frame_findings list) -
        # losing this signal would silently downgrade a real "revise"/"fail"
        # verdict to a clean pass.
        if vision.overall != "pass":
            findings.append(
                Finding(
                    category="visual",
                    fingerprint="visual:general",
                    severity="critical" if vision.overall == "fail" else "major",
                    message=vision.revision_notes or "Vision review flagged a problem with the rendered video.",
                    revision_target=vision.revision_target or "producer",
                )
            )
        for ff in vision.frame_findings:
            if not ff.issues:
                continue
            message = f"Frame {ff.frame_index}: {'; '.join(ff.issues)}"
            if ff.notes:
                message = f"{message} - {ff.notes}"
            findings.append(
                Finding(
                    category="visual",
                    fingerprint=f"visual:frame{ff.frame_index}:{qa_service.fingerprint_slug('-'.join(ff.issues))}",
                    severity=ff.severity or "minor",
                    message=message,
                    justification=ff.justification,
                    revision_target=vision.revision_target or "producer",
                )
            )
        for flag in vision.content_policy_flags:
            findings.append(
                Finding(
                    category="content_policy",
                    fingerprint=f"content_policy:{qa_service.fingerprint_slug(flag)}",
                    severity="critical",
                    message=flag,
                    revision_target="creative_director",
                )
            )
        return findings

    @staticmethod
    def _quote_attribution_finding(quote_or_lesson: QuoteOrLesson, research_dossier: Optional[ResearchDossier]) -> Optional[Finding]:
        if research_dossier is None:
            # Shouldn't normally happen (every content type with a quote/
            # lesson centerpiece requires research) - fail safe rather than
            # silently accepting an unverifiable quote.
            issue = ("no research dossier was available to verify this quote", "major")
        else:
            issue = qa_service.verify_quote_against_dossier(quote_or_lesson, research_dossier)
        if issue is None:
            return None
        reason, severity = issue
        return Finding(
            category="quote_attribution",
            fingerprint=f"quote_attribution:{qa_service.fingerprint_slug(quote_or_lesson.attribution or quote_or_lesson.text)}",
            severity=severity,
            message=f'"{quote_or_lesson.text}" attributed to {quote_or_lesson.attribution!r}: {reason}.',
            revision_target="researcher",
        )

    @staticmethod
    def _fact_check_findings(fact_check_flags: List[FactCheckFlag], content_type_id: Optional[str]) -> List[Finding]:
        severity = "critical" if content_type_id in qa_service.NEWS_CONTENT_TYPES else "major"
        findings = []
        for flag in fact_check_flags:
            if flag.supported:
                continue
            findings.append(
                Finding(
                    category="fact_check",
                    fingerprint=f"fact_check:{qa_service.fingerprint_slug(flag.sentence)}",
                    severity=severity,
                    message=f'"{flag.sentence}": {flag.note}' if flag.note else f'"{flag.sentence}"',
                    revision_target="researcher",
                )
            )
        return findings

    @staticmethod
    def _build_revision_notes(findings: List[Finding], overall: str) -> Optional[str]:
        if overall not in ("revise", "fail"):
            return None
        actionable = [f for f in findings if f.severity in ("critical", "major")]
        return " ".join(f.message for f in actionable) or None

    def _run_fact_check(
        self, script: str, research_dossier: ResearchDossier, content_type_id: Optional[str]
    ) -> list[FactCheckFlag]:
        system = _FACT_CHECK_SYSTEM_PROMPTS.get(content_type_id, _DEFAULT_FACT_CHECK_SYSTEM_PROMPT)
        payload = {"script": script, "research_dossier": research_dossier.model_dump()}
        result = self.call_json(system=system, user=utils.to_json(payload), response_model=FactCheckResult)
        self.log_event(
            "tool_call",
            message=f"Fact-check against research dossier: {len(result.flags)} flag(s)",
            payload={"flags": [f.model_dump() for f in result.flags]},
        )
        return result.flags

    def _run_vision_review(
        self,
        script: str,
        technical_checks,
        frame_paths: list[str],
    ) -> VisionReview:
        checklist = "\n".join(f"- {c.name}: {'PASS' if c.passed else 'FAIL'} ({c.detail})" for c in technical_checks)
        text = (
            f"Script:\n{script}\n\n"
            f"Automated technical checks:\n{checklist}\n\n"
            f"Below are {len(frame_paths)} evenly spaced frames from the video, in chronological "
            "order. Frame index in frame_findings must match their order here, starting at 0."
        )
        content = [{"type": "text", "text": text}]
        for index, frame_path in enumerate(frame_paths):
            content.append({"type": "text", "text": f"Frame {index}:"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": self._encode_image(frame_path),
                    },
                }
            )
        return self.call_json_with_content(system=_SYSTEM_PROMPT, user=content, response_model=VisionReview)

    @staticmethod
    def _encode_image(path: str) -> str:
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")

    @staticmethod
    def _cleanup(frame_paths: list[str]) -> None:
        for path in frame_paths:
            shutil.rmtree(path.rsplit("/", 1)[0], ignore_errors=True)
            break  # all frames share the same temp directory
