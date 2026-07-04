import base64
import shutil
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.schemas import QAReport, TechnicalCheck, VisionReview
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


class QualityReviewer(BaseAgent):
    agent_name = "quality_reviewer"

    def review(self, video_path: str, script: str, subtitle_path: Optional[str] = None) -> QAReport:
        technical_checks, duration = qa_service.run_technical_checks(video_path, subtitle_path)
        self.log_event(
            "tool_call",
            message="Ran technical checks",
            payload={"checks": [c.__dict__ for c in technical_checks], "duration": duration},
        )

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

        overall = vision.overall
        revision_target = vision.revision_target
        revision_notes = vision.revision_notes
        if any(not c.passed for c in technical_checks) and overall == "pass":
            overall = "revise"
            revision_target = revision_target or "producer"
            failed = ", ".join(c.name for c in technical_checks if not c.passed)
            note = f"Automated technical checks failed: {failed}."
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

    def _run_vision_review(self, script: str, technical_checks, frame_paths: list[str]) -> VisionReview:
        checklist = "\n".join(f"- {c.name}: {'PASS' if c.passed else 'FAIL'} ({c.detail})" for c in technical_checks)
        content = [
            {
                "type": "text",
                "text": (
                    f"Script:\n{script}\n\n"
                    f"Automated technical checks:\n{checklist}\n\n"
                    f"Below are {len(frame_paths)} evenly spaced frames from the video, in chronological "
                    "order. Frame index in frame_findings must match their order here, starting at 0."
                ),
            }
        ]
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
