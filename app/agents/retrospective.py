from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import RetrospectiveLesson, RetrospectiveResult
from app.utils import utils

_SYSTEM_PROMPT = """You are conducting a retrospective on one just-completed short-form video project, for a
system that keeps a compounding "what works" playbook per agent. You are given: the QA reports across every
revision attempt, the human's request-changes notes (if any), a diff of every field a human edited at Final
Review before approving (the highest-value signal - it's a direct record of the AI getting something wrong
and a human fixing it), any QA findings a human explicitly overrode (approved despite) at an escalated review
instead of treating as a real problem, and any times this project was manually rescued from a Failed status
because a usable render actually existed.

Produce a short list of lessons, each attributed to exactly one agent: "trend_scout" (topic/idea selection),
"researcher" (fact/quote verification), "creative_director" (script/hook/structure), "quality_reviewer" (what
it caught or missed), or "publisher" (title/description/tags/thumbnail).

If a QA finding was overridden, consider whether it was a genuine miss the human caught, or a sign the finding
type itself is miscalibrated (over-flagging something that's actually fine) - a lesson like "quality_reviewer:
humans keep overriding the 'frame slightly dark' finding as acceptable; consider whether this should be
downgraded or dropped" is exactly the kind of signal this system exists to surface, since one override alone
isn't proof but a pattern across several projects (visible once this lesson accumulates in the playbook) is.

If this project was rescued from Failed, the failure_reason recorded at rescue time tells you what the pipeline
thought went wrong - if a human keeps rescuing the same kind of failure (the same step crashing after a good
render, or the same stale Failed classification), that's a signal the failure itself should be handled
differently (e.g. made non-fatal) rather than a one-off; attribute this to "quality_reviewer" (failure
classification) unless the recorded reason clearly points to a specific other agent's step.

Only report a lesson when there's a genuine, specific, actionable signal - a project with no revisions, no
human edits, no overrides, and no rescues may produce zero lessons; do not manufacture generic advice. Each
actionable_rule must be concrete enough to change a future prompt's instructions (e.g. "for fun_facts, avoid
search terms naming a specific lab apparatus, stock coverage is thin" - not "write better search terms")."""


class Retrospective(BaseAgent):
    agent_name = "retrospective"

    def __init__(self, project_id: Optional[int] = None):
        super().__init__(project_id)
        # Cheap by design (docs/DECISIONS_V3.md §3: "one Claude call per
        # project (not per agent) - keep cost down"), regardless of the
        # configured agents.model used for creative work.
        self.model = "claude-haiku-4-5"

    def run(
        self,
        qa_reports: list,
        human_edits: list,
        revision_notes_history: List[str],
        script: str,
        content_type_id: Optional[str],
        overridden_findings: Optional[list] = None,
        rescue_history: Optional[list] = None,
    ) -> List[RetrospectiveLesson]:
        payload = {
            "content_type_id": content_type_id,
            "script": script,
            "qa_reports": qa_reports,
            "human_edits_at_final_review": human_edits,
            "human_request_changes_notes": revision_notes_history,
            "qa_findings_overridden_by_human": overridden_findings or [],
            "rescued_from_failed_history": rescue_history or [],
        }
        result = self.call_json(system=_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=RetrospectiveResult)
        return result.lessons
