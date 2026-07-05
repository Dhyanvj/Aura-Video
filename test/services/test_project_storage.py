import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.agents.schemas import CreativeBrief, MetadataDraft, ResearchDossier
from app.controllers.v1 import pipeline as pipeline_controller
from app.db import session_scope
from app.db.models import VideoProject
from app.models.exception import HttpException
from app.services import project_storage


class TestSlugAndFolderNaming(unittest.TestCase):
    def test_slugify_lowercases_and_hyphenates(self):
        self.assertEqual(project_storage.slugify("Mantis Shrimp Facts!"), "mantis-shrimp-facts")

    def test_slugify_falls_back_when_empty_after_stripping(self):
        # Non-ASCII-only input (e.g. a topic in a non-Latin script) strips to
        # nothing - must not produce an empty/invalid folder segment.
        self.assertEqual(project_storage.slugify("你好世界"), "untitled")
        self.assertEqual(project_storage.slugify(""), "untitled")

    def test_slugify_truncates_long_topics(self):
        long_topic = "a " * 40
        self.assertLessEqual(len(project_storage.slugify(long_topic)), project_storage._SLUG_MAX_LEN)

    def test_short_id_is_deterministic_from_project_id(self):
        self.assertEqual(project_storage.short_id(42), project_storage.short_id(42))
        self.assertNotEqual(project_storage.short_id(42), project_storage.short_id(43))


class TestMaterializeProject(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

        self._storage_tmpdir = tempfile.mkdtemp()
        self._original_storage_dir = project_storage.utils.storage_dir
        project_storage.utils.storage_dir = lambda sub_dir="", create=False: self._fake_storage_dir(sub_dir, create)

    def _fake_storage_dir(self, sub_dir="", create=False):
        d = os.path.join(self._storage_tmpdir, sub_dir) if sub_dir else self._storage_tmpdir
        if create and not os.path.exists(d):
            os.makedirs(d)
        return d

    def tearDown(self):
        db_session.engine = self._original_engine
        # Temp db file is intentionally not deleted: a still-running daemon
        # thread from this test can otherwise reconnect after deletion and
        # silently recreate an empty, tableless file at the same path,
        # corrupting the next test.
        project_storage.utils.storage_dir = self._original_storage_dir
        import shutil

        shutil.rmtree(self._storage_tmpdir, ignore_errors=True)

    def _create_project(self, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(**fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_ensure_project_storage_path_is_stable_across_calls(self):
        project_id = self._create_project(topic="mantis shrimp", content_type_id="fun_facts")
        first = project_storage.ensure_project_storage_path(project_id)
        second = project_storage.ensure_project_storage_path(project_id)
        self.assertEqual(first, second)
        self.assertTrue(os.path.isdir(os.path.join(self._storage_tmpdir, first)))

    def test_uncategorized_bucket_for_missing_content_type(self):
        project_id = self._create_project(topic="a pre-v2 project", content_type_id=None)
        relative = project_storage.ensure_project_storage_path(project_id)
        self.assertIn(f"projects{os.sep}uncategorized{os.sep}", relative)

    def test_materialize_writes_manifest_script_and_metadata(self):
        brief = CreativeBrief(
            script="A short punchy script.",
            search_terms=["clip a", "clip b"],
            music_direction="upbeat",
            bgm_file=None,
            voice_recommendation="en-US-GuyNeural-Male",
            subtitle_style="bottom, bold",
            metadata_draft=MetadataDraft(working_title="Great Title", hook_variants=["hook one"]),
        )
        dossier = ResearchDossier(topic="mantis shrimp punch speed", why_now="it's a wild fact")
        package = {
            "title_options": ["Title A", "Title B", "Title C"],
            "description": "A great description.",
            "tags": ["fact", "ocean"],
            "thumbnail_candidates": [],
        }
        project_id = self._create_project(
            topic="mantis shrimp",
            content_type_id="fun_facts",
            brief=brief.model_dump(),
            research_evidence=dossier.model_dump(),
            publish_package=package,
            video_path=None,
        )

        relative = project_storage.materialize_project(project_id)
        abs_dir = os.path.join(self._storage_tmpdir, relative)

        self.assertTrue(os.path.isfile(os.path.join(abs_dir, "script.md")))
        with open(os.path.join(abs_dir, "script.md"), encoding="utf-8") as fh:
            self.assertIn("A short punchy script.", fh.read())

        self.assertTrue(os.path.isfile(os.path.join(abs_dir, "research.md")))
        with open(os.path.join(abs_dir, "research.md"), encoding="utf-8") as fh:
            self.assertIn("mantis shrimp punch speed", fh.read())

        with open(os.path.join(abs_dir, "title.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Title A")
        with open(os.path.join(abs_dir, "tags.json"), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.read()), ["fact", "ocean"])

        with open(os.path.join(abs_dir, "project.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["project_id"], project_id)
        self.assertEqual(len(manifest["version_history"]), 1)

    def test_materialize_is_tolerant_of_missing_render_outputs(self):
        # No video_path, no task_id: nothing to copy for final-video.mp4/
        # voice.mp3/subtitles.srt. Must not raise - a storage hiccup must
        # never fail a project that otherwise rendered fine.
        project_id = self._create_project(topic="no render yet", content_type_id="fun_facts")
        relative = project_storage.materialize_project(project_id)
        self.assertIsNotNone(relative)
        abs_dir = os.path.join(self._storage_tmpdir, relative)
        self.assertFalse(os.path.isfile(os.path.join(abs_dir, "final-video.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(abs_dir, "project.json")))

    def test_second_materialize_archives_previous_render_into_revisions(self):
        project_id = self._create_project(topic="revised project", content_type_id="fun_facts")
        relative = project_storage.materialize_project(project_id)
        abs_dir = os.path.join(self._storage_tmpdir, relative)

        # Simulate a first real render producing a script.
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.brief = {"script": "first draft", "search_terms": []}
            session.add(project)
            session.commit()
        project_storage.materialize_project(project_id)
        with open(os.path.join(abs_dir, "script.md"), encoding="utf-8") as fh:
            self.assertIn("first draft", fh.read())

        # A revision loop produces a new script - the old one must survive
        # under revisions/, not be lost.
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.brief = {"script": "revised draft", "search_terms": []}
            session.add(project)
            session.commit()
        project_storage.materialize_project(project_id)

        with open(os.path.join(abs_dir, "script.md"), encoding="utf-8") as fh:
            self.assertIn("revised draft", fh.read())

        revisions_dir = os.path.join(abs_dir, "revisions")
        archived = list(os.walk(revisions_dir))
        archived_scripts = [
            os.path.join(root, f) for root, _, files in archived for f in files if f == "script.md"
        ]
        self.assertEqual(len(archived_scripts), 1)
        with open(archived_scripts[0], encoding="utf-8") as fh:
            self.assertIn("first draft", fh.read())


class TestProjectFileRouteSecurity(unittest.TestCase):
    """
    Extends the path-traversal guarantees TestSecurityControls (test_video.py)
    already covers for storage/tasks/ to the new storage/projects/ root: the
    same file_security.resolve_path_within_directory primitive, anchored at
    one specific project's own folder rather than the whole projects tree.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

        self._storage_tmpdir = tempfile.mkdtemp()
        self._original_storage_dir = project_storage.utils.storage_dir
        project_storage.utils.storage_dir = lambda sub_dir="", create=False: self._fake_storage_dir(sub_dir, create)

    def _fake_storage_dir(self, sub_dir="", create=False):
        d = os.path.join(self._storage_tmpdir, sub_dir) if sub_dir else self._storage_tmpdir
        if create and not os.path.exists(d):
            os.makedirs(d)
        return d

    def tearDown(self):
        db_session.engine = self._original_engine
        # Temp db file is intentionally not deleted: a still-running daemon
        # thread from this test can otherwise reconnect after deletion and
        # silently recreate an empty, tableless file at the same path,
        # corrupting the next test.
        project_storage.utils.storage_dir = self._original_storage_dir
        import shutil

        shutil.rmtree(self._storage_tmpdir, ignore_errors=True)

    def _create_materialized_project(self, topic: str) -> int:
        with session_scope() as session:
            project = VideoProject(topic=topic, content_type_id="fun_facts")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id
        project_storage.materialize_project(project_id)
        return project_id

    def test_valid_filename_resolves_inside_project_folder(self):
        project_id = self._create_materialized_project("a topic")
        path = pipeline_controller._resolve_project_file_path(project_id, "project.json", "req-1")
        self.assertTrue(os.path.isfile(path))

    def test_traversal_outside_project_folder_is_rejected(self):
        # Matches the existing convention in video.py's _resolve_path_within_directory:
        # "resolves outside the allowed directory" -> 403, "resolves inside but
        # missing" -> 404.
        project_id = self._create_materialized_project("a topic")
        with self.assertRaises(HttpException) as ctx:
            pipeline_controller._resolve_project_file_path(project_id, "../../../etc/passwd", "req-2")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_reaching_into_a_sibling_project_folder_is_rejected(self):
        project_a = self._create_materialized_project("project a")
        project_b = self._create_materialized_project("project b")
        with session_scope() as session:
            a_storage_path = session.get(VideoProject, project_a).storage_path
            b_storage_path = session.get(VideoProject, project_b).storage_path

        # A path that, relative to project A's own folder, climbs back out
        # and into project B's folder - must be rejected even though both
        # folders are legitimately under storage/projects/.
        traversal_attempt = os.path.relpath(
            os.path.join(self._storage_tmpdir, b_storage_path),
            os.path.join(self._storage_tmpdir, a_storage_path),
        )
        with self.assertRaises(HttpException) as ctx:
            pipeline_controller._resolve_project_file_path(
                project_a, os.path.join(traversal_attempt, "project.json"), "req-3"
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_storage_folder_returns_404(self):
        with session_scope() as session:
            project = VideoProject(topic="never rendered", content_type_id="fun_facts")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id
        with self.assertRaises(HttpException) as ctx:
            pipeline_controller._resolve_project_file_path(project_id, "project.json", "req-4")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
