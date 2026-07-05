import base64
import shutil
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.schemas import QAReport, QuoteOrLesson, TechnicalCheck, VisionReview
from app.services import qa as qa_service

_SYSTEM_PROMPT = """You are the Quality Reviewer for a short-form vertical video pipeline.
You are given several evenly-spaced frames from a rendered video, in chronological order,
along with the script that was used to produce it and a summary of automated technical
checks that already ran (duration, resolution, audio, subtitles).

For each frame, judge: does the visual roughly match what the script is saying around that
point in the video, is the frame black/broken/corrupted, does it contain a visible watermark,
and is any on-screen text readable. Also flag anything that looks like a medical, financial,
or legal claim, a copyrighted-music reference, or content that would violate typical
short-form platform guidelines (TikTok/YouTube Shorts/Instagram).

Give an overall verdict: "pass" if everything looks acceptable, "revise" if there are fixable
problems, "fail" if the video is unusable. If "revise", set revision_target to
"creative_director" for script/narrative problems or "producer" for visual/material problems,
and give concrete, actionable revision_notes."""

_QUOTE_CHECK_INSTRUCTION = """
You are also given a quote this video attributes to a named person. From your own training
knowledge (no external lookup available), assess whether this wording and attribution pairing
is accurate. Set quote_attribution_check to "correct" only if you're confident both the exact
wording and the named author are right; "incorrect" if you're confident it's wrong or the quote
is commonly misattributed to this person; "uncertain" if you don't have enough confidence
either way. Err toward "uncertain" rather than "correct" when in doubt - a wrongly-confirmed
misattribution is worse than a false alarm."""


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
    ) -> QAReport:
        technical_checks, duration = qa_service.run_technical_checks(
            video_path, subtitle_path, expected_audio_duration
        )
        self.log_event(
            "tool_call",
            message="Ran technical checks",
            payload={"checks": [c.__dict__ for c in technical_checks], "duration": duration},
        )

        frame_paths = qa_service.extract_frames(video_path, duration)
        self.log_event("tool_call", message=f"Extracted {len(frame_paths)} frames for vision review")

        try:
            if frame_paths:
                vision = self._run_vision_review(script, technical_checks, frame_paths, quote_or_lesson)
            else:
                vision = VisionReview(
                    overall="revise",
                    frame_findings=[],
                    revision_target="producer",
                    revision_notes="No frames could be extracted from the rendered video.",
                )
        finally:
            self._cleanup(frame_paths)

        overall = vision.overall
        revision_target = vision.revision_target
        revision_notes = vision.revision_notes
        failed_checks = {c.name for c in technical_checks if not c.passed}

        if failed_checks and overall == "pass":
            overall = "revise"
            revision_target = revision_target or "producer"
            note = f"Automated technical checks failed: {', '.join(failed_checks)}."
            revision_notes = f"{revision_notes} {note}".strip() if revision_notes else note

        if failed_checks & _SCRIPT_ONLY_FIXABLE_CHECKS:
            # Overrides whatever the vision model guessed: it isn't told which
            # failures are script-only vs. material-only, so it can (and did,
            # in practice) label a duration overrun "producer" - which sends
            # the revision to the one agent that can't fix it.
            revision_target = "creative_director"

        if quote_or_lesson is not None and quote_or_lesson.is_quote and vision.quote_attribution_check in (
            "incorrect",
            "uncertain",
        ):
            # A misattributed quote is a hard fail per spec, not a fixable
            # detail Producer can patch with different footage - only a
            # rewrite (a different quote, or falling back to a life lesson)
            # can fix this, so it always routes to creative_director. There's
            # no independent source lookup yet (that's the Researcher agent),
            # so this is the model's own knowledge - "uncertain" is treated
            # as unsafe to publish, same as a confirmed wrong attribution.
            overall = "fail" if vision.quote_attribution_check == "incorrect" else "revise"
            revision_target = "creative_director"
            note = (
                f"Quote attribution check: {vision.quote_attribution_check} - "
                f'"{quote_or_lesson.text}" attributed to {quote_or_lesson.attribution!r} '
                "could not be confirmed as accurate."
            )
            revision_notes = f"{revision_notes} {note}".strip() if revision_notes else note

        report = QAReport(
            overall=overall,
            technical_checks=[TechnicalCheck(name=c.name, passed=c.passed, detail=c.detail) for c in technical_checks],
            frame_findings=vision.frame_findings,
            content_policy_flags=vision.content_policy_flags,
            revision_target=revision_target,
            revision_notes=revision_notes,
        )
        self.log_event("output", message=f"QA verdict: {report.overall}", payload=report.model_dump())
        return report

    def _run_vision_review(
        self,
        script: str,
        technical_checks,
        frame_paths: list[str],
        quote_or_lesson: Optional[QuoteOrLesson] = None,
    ) -> VisionReview:
        checklist = "\n".join(f"- {c.name}: {'PASS' if c.passed else 'FAIL'} ({c.detail})" for c in technical_checks)
        system = _SYSTEM_PROMPT
        text = (
            f"Script:\n{script}\n\n"
            f"Automated technical checks:\n{checklist}\n\n"
            f"Below are {len(frame_paths)} evenly spaced frames from the video, in chronological "
            "order. Frame index in frame_findings must match their order here, starting at 0."
        )
        if quote_or_lesson is not None and quote_or_lesson.is_quote:
            system += _QUOTE_CHECK_INSTRUCTION
            text += (
                f'\n\nQuote to check: "{quote_or_lesson.text}" - attributed to '
                f"{quote_or_lesson.attribution!r}."
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
        return self.call_json_with_content(system=system, user=content, response_model=VisionReview)

    @staticmethod
    def _encode_image(path: str) -> str:
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")

    @staticmethod
    def _cleanup(frame_paths: list[str]) -> None:
        for path in frame_paths:
            shutil.rmtree(path.rsplit("/", 1)[0], ignore_errors=True)
            break  # all frames share the same temp directory
