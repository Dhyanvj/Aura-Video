import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.publisher import Publisher
from app.agents.schemas import PublishPackage
from app.utils import utils
from test.services._test_helpers import IsolatedStorageDirMixin


def _fake_package() -> PublishPackage:
    return PublishPackage(
        title_options=["a", "b", "c"],
        description="d",
        tags=["t"] * 10,
        category="c",
        platform_variants=[],
        suggested_posting_time="",
    )


class TestPublisherPrepare(IsolatedStorageDirMixin, unittest.TestCase):
    def setUp(self):
        self._start_isolated_storage_dir()

    def tearDown(self):
        self._stop_isolated_storage_dir()

    def test_thumbnail_out_dir_is_based_on_task_id_not_video_paths_directory(self):
        # Regression: out_dir used to be derived from video_path's parent
        # directory name, which broke for a rescued project whose video_path
        # points inside the project's own storage folder instead of
        # storage/tasks/{task_id}/ (see orchestrator.rescue_failed_project) -
        # thumbnails ended up written to (and later looked for at) an
        # entirely wrong, bogus location. video_path's directory here is
        # deliberately something other than task_id to prove out_dir no
        # longer depends on it.
        publisher = Publisher(project_id=None)
        with patch.object(publisher, "call_json", return_value=_fake_package()), patch(
            "app.agents.publisher.qa_service.run_technical_checks", return_value=([], 20.0)
        ), patch(
            "app.agents.publisher.thumbnails_service.generate_thumbnail_candidates", return_value=["/tmp/t1.jpg"]
        ) as mock_gen:
            publisher.prepare(
                script="s",
                niche="n",
                hook_text="hook",
                video_path="/some/where/storage/projects/motivational/2026-01-01-x-000001/final-video.mp4",
                task_id="real-task-id",
            )

        _, kwargs = mock_gen.call_args
        self.assertEqual(kwargs["out_dir"], utils.task_dir("real-task-id"))

    def test_prepare_returns_thumbnail_candidates_in_the_package(self):
        publisher = Publisher(project_id=None)
        with patch.object(publisher, "call_json", return_value=_fake_package()), patch(
            "app.agents.publisher.qa_service.run_technical_checks", return_value=([], 20.0)
        ), patch(
            "app.agents.publisher.thumbnails_service.generate_thumbnail_candidates",
            return_value=["/tmp/t1.jpg", "/tmp/t2.jpg"],
        ):
            package = publisher.prepare(
                script="s", niche="n", hook_text="hook", video_path="/tmp/v.mp4", task_id="task-1"
            )

        self.assertEqual(package["thumbnail_candidates"], ["/tmp/t1.jpg", "/tmp/t2.jpg"])
        self.assertEqual(package["title_options"], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
