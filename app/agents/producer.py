import threading
import time
from typing import Optional

from loguru import logger

from app.db import session_scope
from app.db.models import AgentEvent, VideoProject
from app.models import const
from app.models.schema import VideoParams
from app.services import cancellation
from app.services import state as sm
from app.services import task as task_service
from app.services.ws_manager import broadcast_event
from app.utils import utils

# (progress_ceiling, stage label) — mirrors the checkpoints task.start() already
# reports via sm.state.update_task(progress=...). Producer polls task state and
# emits one AgentEvent per newly crossed threshold, rather than requiring
# task.py to know about agents/events.
_STAGE_THRESHOLDS = [
    (10, "script"),
    (20, "terms"),
    (30, "audio"),
    (40, "subtitle"),
    (50, "materials"),
    (100, "render"),
]


class Producer:
    """
    Not an LLM agent. Maps a project's VideoParams onto the existing render
    pipeline (app/services/task.py) and mirrors its progress into AgentEvents.
    """

    agent_name = "producer"

    def __init__(self, project_id: int):
        self.project_id = project_id

    def log_event(self, type_: str, message: str = "", payload: Optional[dict] = None) -> None:
        with session_scope() as session:
            session.add(
                AgentEvent(
                    project_id=self.project_id,
                    agent=self.agent_name,
                    type=type_,
                    message=message,
                    payload=payload,
                )
            )
            session.commit()
        broadcast_event(self.project_id, self.agent_name, type_, message)

    def run(self, params: VideoParams) -> dict:
        """
        Runs the render pipeline to completion. Returns the final task state
        dict on success, raises RuntimeError on failure.
        """
        task_id = utils.get_uuid()
        with session_scope() as session:
            project = session.get(VideoProject, self.project_id)
            project.task_id = task_id
            project.video_params = params.model_dump(mode="json")
            session.add(project)
            session.commit()

        self.log_event("output", message=f"Starting render for task {task_id}")
        sm.state.update_task(task_id)

        thread = threading.Thread(
            target=task_service.start, args=(task_id, params), kwargs={"stop_at": "video"}
        )
        thread.start()

        crossed = set()
        while thread.is_alive():
            task_state = sm.state.get_task(task_id) or {}
            progress = int(task_state.get("progress", 0))
            for ceiling, label in _STAGE_THRESHOLDS:
                if progress >= ceiling and ceiling not in crossed:
                    crossed.add(ceiling)
                    self.log_event(
                        "output", message=f"Stage complete: {label} ({ceiling}%)", payload={"progress": ceiling}
                    )
            if task_state.get("state") == const.TASK_STATE_FAILED:
                break
            time.sleep(2)
        thread.join()

        # Cancellation can't interrupt an in-progress render (no hook into
        # task_service.start for that) - "wait for the current stage to
        # stop" means wait for the thread we just joined to actually finish,
        # then refuse to treat its output as a usable render rather than
        # force-killing it mid-encode.
        if cancellation.is_cancel_requested(self.project_id):
            self.log_event("output", message=f"Render for task {task_id} finished after a cancellation request; discarding")
            raise cancellation.PipelineCancelled(f"project {self.project_id} cancelled during render")

        final_state = sm.state.get_task(task_id) or {}
        if final_state.get("state") == const.TASK_STATE_FAILED:
            # Some render stages (e.g. TTS audio validation) attach a
            # specific, actionable failure_reason to the task state; surface
            # it here so the project's Failed card shows that instead of a
            # generic message - the reason is otherwise lost, since nothing
            # else reads task state after this point.
            reason = final_state.get("failure_reason") or f"render pipeline failed for task {task_id}"
            self.log_event("error", message=f"Render failed for task {task_id}: {reason}")
            raise RuntimeError(reason)

        videos = final_state.get("videos") or []
        with session_scope() as session:
            project = session.get(VideoProject, self.project_id)
            project.video_path = videos[0] if videos else None
            session.add(project)
            session.commit()

        self.log_event("output", message="Render complete", payload={"videos": videos})
        return final_state
