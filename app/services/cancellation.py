"""
Cooperative cancellation for in-flight pipeline runs (Recycle Bin: deleting a
project that is currently producing must cancel it cleanly first - see
docs/DECISIONS_V3.md and app/services/project_deletion.py).

There is no way to force-kill a render thread mid-ffmpeg-encode, and nothing
in this codebase tries to. Instead, cancel_requested is a flag on the project
row that pipeline checkpoints (app/agents/orchestrator.py, app/agents/producer.py)
check between units of work - "wait for the current stage to stop" means wait
for the in-progress LLM call or render to actually return, then stop before
starting the next one, rather than interrupting it.
"""

from app.db import session_scope
from app.db.models import VideoProject


class PipelineCancelled(Exception):
    """Raised at a checkpoint that found cancel_requested=True on the project."""


def request_cancel(project_id: int) -> None:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        project.cancel_requested = True
        session.add(project)
        session.commit()


def is_cancel_requested(project_id: int) -> bool:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        return bool(project and project.cancel_requested)


def raise_if_cancelled(project_id: int) -> None:
    if is_cancel_requested(project_id):
        raise PipelineCancelled(f"project {project_id} cancellation requested")
