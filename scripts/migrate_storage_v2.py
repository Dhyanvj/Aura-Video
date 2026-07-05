"""
One-time backfill for docs/DECISIONS_V3.md §1: gives existing Agent-Studio
projects (VideoProject rows with a task_id, created before storage v2) a
canonical storage/projects/{content-type}/{date}-{slug}-{shortid}/ folder,
without touching storage/tasks/{task_id}/ or breaking anything that still
serves from it.

Safe by construction:
  * --dry-run (the default) only prints the plan - no disk or DB writes.
  * --apply COPIES canonical files into the new folder (never moves/deletes
    the original storage/tasks/{task_id}/ directory), so a botched run can't
    lose data; a separate manual prune is a later, explicit step.
  * Idempotent: a project that already has storage_path is skipped.
  * Legacy task-only renders with no VideoProject row (the original
    MoneyPrinterTurbo API, never part of the Agent Studio) are out of scope
    by design and untouched.

Usage:
    python scripts/migrate_storage_v2.py            # dry run (default)
    python scripts/migrate_storage_v2.py --apply     # actually migrate
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import select

import app.db.session as db_session
from app.db import session_scope
from app.db.models import VideoProject
from app.services import project_storage
from app.utils import utils


def _candidates() -> list:
    with session_scope() as session:
        rows = session.exec(
            select(VideoProject)
            .where(VideoProject.task_id.is_not(None))
            .where(VideoProject.storage_path.is_(None))
        ).all()
        # Detach the small set of fields the report needs before the session closes.
        return [(p.id, p.task_id, p.topic, p.niche, p.content_type_id, p.created_at) for p in rows]


def _preview_one(project_id: int, task_id: str, topic: str, niche: str, content_type_id: str, created_at) -> dict:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        name, content_type = project_storage.folder_name_and_content_type(project)
    relative = os.path.join("projects", content_type, name)

    task_dir = utils.task_dir(task_id)
    found = [
        fname
        for fname in ("audio.mp3", "subtitle.srt")
        if os.path.isfile(os.path.join(task_dir, fname))
    ]
    return {
        "project_id": project_id,
        "task_id": task_id,
        "target": relative,
        "task_dir_files_found": found,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually migrate (default is dry-run only).")
    args = parser.parse_args()

    # Ensures storage_path (and any other additive column) exists before this
    # script queries it - normally added by the app's own startup, but this
    # script can run standalone before the app has ever started.
    db_session.init_db()

    candidates = _candidates()
    if not candidates:
        print("No projects need migration - all Agent Studio projects already have a storage_path (or none exist).")
        return

    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(candidates)} project(s) to migrate\n")

    for project_id, task_id, topic, niche, content_type_id, created_at in candidates:
        preview = _preview_one(project_id, task_id, topic, niche, content_type_id, created_at)
        print(f"  project {project_id} (topic={topic!r}) -> storage/{preview['target']}/")
        print(f"    task dir: storage/tasks/{task_id}/  (found: {preview['task_dir_files_found'] or 'nothing yet'})")

        if args.apply:
            result = project_storage.materialize_project(project_id)
            print(f"    migrated -> storage/{result}/" if result else "    SKIPPED (materialize returned no path)")

    if not args.apply:
        print("\nDry run only - no files were copied and no DB rows were changed. Re-run with --apply to migrate.")


if __name__ == "__main__":
    main()
