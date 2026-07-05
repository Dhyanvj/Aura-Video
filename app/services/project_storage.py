"""
Storage v2 (docs/DECISIONS_V3.md §1): a human-browsable folder per project at
storage/projects/{content-type}/{YYYY-MM-DD}-{slug}-{shortid}/, containing
script.md, transcript.txt, voice.mp3, subtitles.srt, final-video.mp4,
thumbnail.png (+ candidates/), title.txt/description.txt/tags.json,
research.md, project.json, and revisions/ (prior renders, never deleted).

The folder path is stable for a project's life once assigned - status lives
in the DB (VideoProject.status) and is mirrored into project.json, never the
other way around. task.py's own render scratch directory
(storage/tasks/{task_id}/) is untouched by any of this; materialize_project
copies its outputs out after a successful render.
"""

import json
import os
import re
import shutil
from typing import Optional

from loguru import logger

from app.db import session_scope
from app.db.models import VideoProject, utcnow
from app.utils import utils

_SLUG_MAX_LEN = 40
_UNCATEGORIZED = "uncategorized"

# Top-level canonical files that a render can (re)produce. Anything in this
# list that already exists in a project's folder is moved into revisions/
# before being overwritten, so a superseded render/script is never lost.
_CANONICAL_FILES = (
    "script.md",
    "transcript.txt",
    "voice.mp3",
    "subtitles.srt",
    "final-video.mp4",
    "thumbnail.png",
    "title.txt",
    "description.txt",
    "tags.json",
    "research.md",
)


def projects_root(create: bool = False) -> str:
    return utils.storage_dir("projects", create=create)


def slugify(text: str) -> str:
    ascii_text = (text or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    slug = slug[:_SLUG_MAX_LEN].strip("-")
    return slug or "untitled"


def short_id(project_id: int) -> str:
    # Deterministic from the DB primary key - no extra random state to track
    # or persist just to name a folder.
    return f"{project_id:06x}"


def folder_name_and_content_type(project: VideoProject) -> tuple:
    date_str = (project.created_at or utcnow()).strftime("%Y-%m-%d")
    content_type = project.content_type_id or _UNCATEGORIZED
    slug = slugify(project.topic or project.niche or content_type)
    return f"{date_str}-{slug}-{short_id(project.id)}", content_type


def ensure_project_storage_path(project_id: int) -> str:
    """
    Idempotently computes and persists storage_path for a project, creating
    the folder on disk. Never changes an already-assigned storage_path, so a
    project's folder is stable for its life even if its topic is edited
    later.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if project.storage_path:
            relative = project.storage_path
        else:
            name, content_type = folder_name_and_content_type(project)
            relative = os.path.join("projects", content_type, name)
            project.storage_path = relative
            session.add(project)
            session.commit()

    abs_dir = os.path.join(utils.storage_dir(), relative)
    os.makedirs(abs_dir, exist_ok=True)
    os.makedirs(os.path.join(abs_dir, "assets"), exist_ok=True)
    os.makedirs(os.path.join(abs_dir, "revisions"), exist_ok=True)
    return relative


def project_abs_dir(project_id: int) -> Optional[str]:
    """Absolute path to a project's folder, or None if it has never been materialized."""
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None or not project.storage_path:
            return None
        relative = project.storage_path
    return os.path.join(utils.storage_dir(), relative)


def _archive_existing(abs_dir: str) -> None:
    existing = [name for name in _CANONICAL_FILES if os.path.isfile(os.path.join(abs_dir, name))]
    if not existing:
        return
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    dest = os.path.join(abs_dir, "revisions", stamp)
    os.makedirs(dest, exist_ok=True)
    for name in existing:
        shutil.move(os.path.join(abs_dir, name), os.path.join(dest, name))
    candidates_dir = os.path.join(abs_dir, "candidates")
    if os.path.isdir(candidates_dir):
        shutil.move(candidates_dir, os.path.join(dest, "candidates"))


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content or "")


def _copy_if_exists(src: Optional[str], dst: str) -> bool:
    if src and os.path.isfile(src):
        shutil.copy2(src, dst)
        return True
    return False


_SRT_INDEX_RE = re.compile(r"^\d+$")


def _srt_to_transcript(srt_path: str) -> str:
    lines_out = []
    with open(srt_path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or _SRT_INDEX_RE.match(stripped) or "-->" in stripped:
                continue
            lines_out.append(stripped)
    return " ".join(lines_out)


def _render_script_md(brief: dict) -> str:
    lines = ["# Script", "", brief.get("script", ""), ""]
    metadata = brief.get("metadata_draft") or {}
    if metadata.get("working_title"):
        lines += ["## Working title", "", metadata["working_title"], ""]
    if metadata.get("hook_variants"):
        lines += ["## Hook variants", ""] + [f"- {h}" for h in metadata["hook_variants"]] + [""]
    if brief.get("search_terms"):
        lines += ["## Search terms", ""] + [f"- {t}" for t in brief["search_terms"]] + [""]
    return "\n".join(lines)


def _render_research_md(dossier: dict) -> str:
    lines = [f"# Research: {dossier.get('topic', '')}", ""]
    if dossier.get("why_now"):
        lines += [dossier["why_now"], ""]
    if dossier.get("reduced_verification"):
        lines += ["**Reduced verification** - sources could not be fully corroborated.", ""]
    key_facts = dossier.get("key_facts") or []
    if key_facts:
        lines.append("## Key facts")
        lines.append("")
        for fact in key_facts:
            lines.append(f"- ({fact.get('confidence', '')}) {fact.get('statement', '')}")
            for citation in fact.get("citations") or []:
                title = citation.get("title") or citation.get("url", "")
                lines.append(f"  - [{title}]({citation.get('url', '')})")
        lines.append("")
    sources = dossier.get("sources") or []
    if sources:
        lines.append("## Sources")
        lines.append("")
        for source in sources:
            title = source.get("title") or source.get("url", "")
            lines.append(f"- [{title}]({source.get('url', '')})")
    return "\n".join(lines)


def _write_manifest(abs_dir: str, project_id: int, snapshot: dict) -> None:
    manifest_path = os.path.join(abs_dir, "project.json")
    history = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                history = json.load(fh).get("version_history", [])
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(
        {
            "version": len(history) + 1,
            "status": snapshot["status"],
            "revision_count": snapshot["revision_count"],
            "recorded_at": utcnow().isoformat(),
        }
    )
    manifest = {
        "project_id": project_id,
        "status": snapshot["status"],
        "series_id": snapshot["series_id"],
        "cost_usd": snapshot["cost_usd"],
        "created_at": snapshot["created_at"].isoformat() if snapshot["created_at"] else None,
        "updated_at": snapshot["updated_at"].isoformat() if snapshot["updated_at"] else None,
        "version_history": history,
    }
    _write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))


def materialize_project(project_id: int) -> Optional[str]:
    """
    Called after every successful render (initial or a revision loop).
    Archives whatever canonical files already exist into revisions/ first,
    then (re)writes the current render's outputs plus script.md/research.md/
    title.txt/description.txt/tags.json/project.json from the DB.

    Best-effort per source file - a missing input (e.g. no subtitle was
    generated, or a test double never wrote a real task directory) is
    skipped, not raised, since a storage-layout hiccup must never fail a
    project that otherwise rendered and passed QA. Unexpected errors DO
    propagate - the caller (orchestrator) logs them as a visible AgentEvent
    rather than this function swallowing them silently.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            return None
        snapshot = {
            "video_path": project.video_path,
            "task_id": project.task_id,
            "brief": project.brief,
            "research_evidence": project.research_evidence,
            "publish_package": project.publish_package,
            "status": project.status,
            "cost_usd": project.cost_usd,
            "series_id": project.series_id,
            "revision_count": project.revision_count,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        }

    relative = ensure_project_storage_path(project_id)
    abs_dir = os.path.join(utils.storage_dir(), relative)
    _archive_existing(abs_dir)

    task_dir = utils.task_dir(snapshot["task_id"]) if snapshot["task_id"] else None

    if not _copy_if_exists(snapshot["video_path"], os.path.join(abs_dir, "final-video.mp4")):
        logger.info(f"project {project_id}: no rendered video file yet, skipping final-video.mp4")

    if task_dir:
        _copy_if_exists(os.path.join(task_dir, "audio.mp3"), os.path.join(abs_dir, "voice.mp3"))
        srt_src = os.path.join(task_dir, "subtitle.srt")
        if _copy_if_exists(srt_src, os.path.join(abs_dir, "subtitles.srt")):
            _write_text(os.path.join(abs_dir, "transcript.txt"), _srt_to_transcript(srt_src))

    brief = snapshot["brief"] or {}
    if brief:
        _write_text(os.path.join(abs_dir, "script.md"), _render_script_md(brief))

    research = snapshot["research_evidence"]
    if research:
        _write_text(os.path.join(abs_dir, "research.md"), _render_research_md(research))

    package = snapshot["publish_package"] or {}
    if package:
        title_options = package.get("title_options") or []
        _write_text(os.path.join(abs_dir, "title.txt"), title_options[0] if title_options else "")
        _write_text(os.path.join(abs_dir, "description.txt"), package.get("description", ""))
        _write_text(
            os.path.join(abs_dir, "tags.json"),
            json.dumps(package.get("tags", []), ensure_ascii=False, indent=2),
        )

        thumbnails = [p for p in (package.get("thumbnail_candidates") or []) if os.path.isfile(p)]
        if thumbnails:
            candidates_dir = os.path.join(abs_dir, "candidates")
            os.makedirs(candidates_dir, exist_ok=True)
            _copy_if_exists(thumbnails[0], os.path.join(abs_dir, "thumbnail.png"))
            for extra in thumbnails[1:]:
                shutil.copy2(extra, os.path.join(candidates_dir, os.path.basename(extra)))

    _write_manifest(abs_dir, project_id, snapshot)
    return relative
