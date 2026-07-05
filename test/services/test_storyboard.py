import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.db import session_scope
from app.db.models import ProjectClip, VideoProject
from app.models.schema import MaterialInfo, VideoParams
from app.services import storyboard


class TestStoryboardDbBacked(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: see docs/REVIEW_FINDINGS.md.

    def _create_project(self, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(**fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_record_clips_persists_in_order(self):
        project_id = self._create_project(topic="t")
        storyboard.record_clips(
            project_id,
            [
                {"search_term": "a", "provider": "pexels", "url": "https://x/a.mp4", "local_path": "/tmp/a.mp4"},
                {"search_term": "b", "provider": "pixabay", "url": "https://x/b.mp4", "local_path": "/tmp/b.mp4"},
            ],
        )
        clips = storyboard.list_clips(project_id)
        self.assertEqual([c.index for c in clips], [0, 1])
        self.assertEqual([c.search_term for c in clips], ["a", "b"])

    def test_record_clips_replaces_stale_rows_from_a_prior_render(self):
        project_id = self._create_project(topic="t")
        storyboard.record_clips(
            project_id, [{"search_term": "a", "provider": "pexels", "url": "u", "local_path": "/tmp/a.mp4"}]
        )
        storyboard.record_clips(
            project_id, [{"search_term": "z", "provider": "pexels", "url": "u2", "local_path": "/tmp/z.mp4"}]
        )
        clips = storyboard.list_clips(project_id)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].search_term, "z")

    def test_record_clips_scoped_per_project(self):
        project_a = self._create_project(topic="a")
        project_b = self._create_project(topic="b")
        storyboard.record_clips(project_a, [{"search_term": "a", "provider": "pexels", "url": "u", "local_path": "p"}])
        self.assertEqual(len(storyboard.list_clips(project_b)), 0)
        self.assertEqual(len(storyboard.list_clips(project_a)), 1)

    def test_replace_clip_and_rerender_requires_a_rendered_project(self):
        project_id = self._create_project(topic="t")  # no task_id/video_params yet
        with self.assertRaises(storyboard.ProjectNotRenderedError):
            storyboard.replace_clip_and_rerender(project_id, 0, MaterialInfo(provider="pexels", url="https://x/new.mp4"))

    def test_replace_clip_and_rerender_requires_an_existing_clip_index(self):
        project_id = self._create_project(
            topic="t", task_id="task-1", video_params=VideoParams(video_subject="t").model_dump(mode="json")
        )
        storyboard.record_clips(project_id, [{"search_term": "a", "provider": "pexels", "url": "u", "local_path": "/tmp/a.mp4"}])
        with self.assertRaises(storyboard.ClipNotFoundError):
            storyboard.replace_clip_and_rerender(project_id, 5, MaterialInfo(provider="pexels", url="https://x/new.mp4"))

    def test_replace_clip_and_rerender_swaps_clip_and_updates_video_path(self):
        project_id = self._create_project(
            topic="t", task_id="task-1", video_params=VideoParams(video_subject="t").model_dump(mode="json")
        )
        storyboard.record_clips(
            project_id,
            [
                {"search_term": "a", "provider": "pexels", "url": "u1", "local_path": "/tmp/a.mp4"},
                {"search_term": "b", "provider": "pexels", "url": "u2", "local_path": "/tmp/b.mp4"},
            ],
        )
        candidate = MaterialInfo(provider="pixabay", url="https://x/new.mp4", duration=5)

        with patch.object(storyboard.material_service, "save_video", return_value="/tmp/new-clip.mp4"), patch(
            "app.services.task.generate_final_videos", return_value=(["/tmp/final-1.mp4"], ["/tmp/combined-1.mp4"])
        ) as mock_generate, patch.object(storyboard.project_storage, "materialize_project"):
            new_path = storyboard.replace_clip_and_rerender(project_id, 1, candidate)

        self.assertEqual(new_path, "/tmp/final-1.mp4")
        # The re-render must reuse the FULL ordered clip list with the swap applied.
        ordered_paths_arg = mock_generate.call_args[0][2]
        self.assertEqual(ordered_paths_arg, ["/tmp/a.mp4", "/tmp/new-clip.mp4"])

        clips = storyboard.list_clips(project_id)
        self.assertEqual(clips[1].local_path, "/tmp/new-clip.mp4")
        self.assertEqual(clips[1].provider, "pixabay")

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
        self.assertEqual(project.video_path, "/tmp/final-1.mp4")

    def test_replace_clip_and_rerender_archives_prior_render_via_project_storage(self):
        # Confirms this reuses Milestone 1's revisions/ mechanism rather than
        # needing its own - materialize_project must actually be called.
        project_id = self._create_project(
            topic="t", task_id="task-1", video_params=VideoParams(video_subject="t").model_dump(mode="json")
        )
        storyboard.record_clips(project_id, [{"search_term": "a", "provider": "pexels", "url": "u", "local_path": "/tmp/a.mp4"}])
        candidate = MaterialInfo(provider="pixabay", url="https://x/new.mp4")

        with patch.object(storyboard.material_service, "save_video", return_value="/tmp/new-clip.mp4"), patch(
            "app.services.task.generate_final_videos", return_value=(["/tmp/final-1.mp4"], ["/tmp/combined-1.mp4"])
        ), patch.object(storyboard.project_storage, "materialize_project") as mock_materialize:
            storyboard.replace_clip_and_rerender(project_id, 0, candidate)

        mock_materialize.assert_called_once_with(project_id)


class TestSearchCandidatesDispatch(unittest.TestCase):
    def test_dispatches_to_the_requested_provider(self):
        with patch.object(storyboard.material_service, "search_videos_pixabay", return_value=["fake"]) as mock_search:
            result = storyboard.search_candidates("pixabay", "octopus")
        mock_search.assert_called_once()
        self.assertEqual(result, ["fake"])

    def test_unknown_provider_falls_back_to_pexels(self):
        with patch.object(storyboard.material_service, "search_videos_pexels", return_value=["fake"]) as mock_search:
            result = storyboard.search_candidates("not-a-real-provider", "octopus")
        mock_search.assert_called_once()
        self.assertEqual(result, ["fake"])


if __name__ == "__main__":
    unittest.main()
