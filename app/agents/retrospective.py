from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import RetrospectiveLesson, RetrospectiveResult
from app.utils import utils

_SYSTEM_PROMPT = """You are conducting a retrospective on one just-completed short-form video project, for a
system that keeps a compounding "what works" playbook per agent. You are given: the QA reports across every
revision attempt, the human's request-changes notes (if any), and a diff of every field a human edited at
Final Review before approving (the highest-value signal - it's a direct record of the AI getting something
wrong and a human fixing it).

Produce a short list of lessons, each attributed to exactly one agent: "trend_scout" (topic/idea selection),
"researcher" (fact/quote verification), "creative_director" (script/hook/structure), "quality_reviewer" (what
it caught or missed), or "publisher" (title/description/tags/thumbnail).

Only report a lesson when there's a genuine, specific, actionable signal - a project with no revisions and no
human edits may produce zero lessons; do not manufacture generic advice. Each actionable_rule must be concrete
enough to change a future prompt's instructions (e.g. "for fun_facts, avoid search terms naming a specific lab
apparatus, stock coverage is thin" - not "write better search terms")."""


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
    ) -> List[RetrospectiveLesson]:
        payload = {
            "content_type_id": content_type_id,
            "script": script,
            "qa_reports": qa_reports,
            "human_edits_at_final_review": human_edits,
            "human_request_changes_notes": revision_notes_history,
        }
        result = self.call_json(system=_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=RetrospectiveResult)
        return result.lessons
