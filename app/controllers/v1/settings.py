from typing import List, Literal, Optional

from fastapi import Path, Query, Request
from pydantic import BaseModel

from app.config import config
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.services import playbook, rescue, scheduler
from app.utils import utils

router = new_router()


class UpdateSettingsRequest(BaseModel):
    niche: Optional[str] = None
    audience: Optional[str] = None
    approval_mode: Optional[Literal["manual", "automatic"]] = None
    schedule_enabled: Optional[bool] = None
    videos_per_day: Optional[int] = None
    run_at: Optional[str] = None
    default_platforms: Optional[List[str]] = None
    monthly_budget_usd: Optional[float] = None
    recycle_bin_retention_days: Optional[int] = None


@router.get("/settings", summary="Get studio settings (secrets are never returned)")
def get_settings(request: Request):
    return utils.get_response(200, _current_settings())


@router.put("/settings", summary="Update studio settings and restart the scheduler")
def update_settings(request: Request, body: UpdateSettingsRequest):
    if body.niche is not None:
        config.trends["niche"] = body.niche
    if body.audience is not None:
        config.trends["audience"] = body.audience
    if body.approval_mode is not None:
        config.agents["approval_mode"] = body.approval_mode
    if body.schedule_enabled is not None:
        config.schedule["enabled"] = body.schedule_enabled
    if body.videos_per_day is not None:
        config.schedule["videos_per_day"] = body.videos_per_day
    if body.run_at is not None:
        config.schedule["run_at"] = body.run_at
    if body.default_platforms is not None:
        config.app["upload_post_platforms"] = body.default_platforms
    if body.monthly_budget_usd is not None:
        config.agents["monthly_budget_usd"] = body.monthly_budget_usd
    if body.recycle_bin_retention_days is not None:
        config.storage["recycle_bin_retention_days"] = body.recycle_bin_retention_days

    config.save_config()
    scheduler.stop_scheduler()
    scheduler.start_scheduler()
    return utils.get_response(200, _current_settings())


def _current_settings() -> dict:
    from app.agents import orchestrator  # local import: avoid a circular import at module load time

    return {
        "niche": config.trends.get("niche", ""),
        "audience": config.trends.get("audience", ""),
        # Migrates the old, never-actually-enforced autopilot_level if
        # approval_mode itself hasn't been set yet - see
        # orchestrator._resolve_approval_mode.
        "approval_mode": orchestrator._resolve_approval_mode(),
        "max_revisions": config.agents.get("max_revisions", 2),
        "max_script_regenerations": config.agents.get("max_script_regenerations", 5),
        "schedule_enabled": config.schedule.get("enabled", False),
        "videos_per_day": config.schedule.get("videos_per_day", 1),
        "run_at": config.schedule.get("run_at", "09:00"),
        "default_platforms": config.app.get("upload_post_platforms", []),
        "monthly_budget_usd": config.agents.get("monthly_budget_usd", 0),
        "recycle_bin_retention_days": config.storage.get("recycle_bin_retention_days", 7),
        "anthropic_configured": bool(config.agents.get("anthropic_api_key")),
        "youtube_configured": bool(config.trends.get("youtube_api_key")),
        "upload_post_configured": bool(
            config.app.get("upload_post_api_key") and config.app.get("upload_post_username")
        ),
        # Publishing is on hold for the v2 quality redesign - approving a
        # project marks it complete instead of publishing anywhere. See
        # [features].publishing_enabled in config.toml.
        "publishing_enabled": config.features.get("publishing_enabled", False),
    }


class UpdateBulletRequest(BaseModel):
    enabled: Optional[bool] = None
    text: Optional[str] = None


def _playbook_summary(pb) -> dict:
    return {
        "id": pb.id,
        "agent": pb.agent,
        "content_type_id": pb.content_type_id,
        "version": pb.version,
        "bullets": pb.bullets,
        "is_active": pb.is_active,
        "created_at": pb.created_at.isoformat(),
    }


@router.get("/playbooks", summary="List the active playbook for every (agent, content type) pair that has one")
def list_playbooks(request: Request):
    return utils.get_response(200, {"playbooks": [_playbook_summary(p) for p in playbook.list_all_playbooks()]})


@router.get("/playbooks/versions", summary="Full version history for one (agent, content type) pair")
def get_playbook_versions(request: Request, agent: str = Query(...), content_type_id: Optional[str] = Query(None)):
    versions = playbook.list_versions(agent, content_type_id)
    return utils.get_response(200, {"versions": [_playbook_summary(p) for p in versions]})


@router.patch(
    "/playbooks/{playbook_id}/bullets/{bullet_index}",
    summary="Edit or disable one playbook bullet - creates a new version",
)
def update_playbook_bullet(
    request: Request, body: UpdateBulletRequest, playbook_id: int = Path(...), bullet_index: int = Path(...)
):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        updated = playbook.update_bullet(playbook_id, bullet_index, **fields)
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    return utils.get_response(200, _playbook_summary(updated))


@router.post("/playbooks/{playbook_id}/rollback", summary="Reactivate a prior playbook version")
def rollback_playbook(request: Request, playbook_id: int = Path(...)):
    try:
        restored = playbook.rollback_to(playbook_id)
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    return utils.get_response(200, _playbook_summary(restored))


@router.post(
    "/maintenance/rescue-scan",
    summary="Scan every Failed project for a usable render and cache the result (surfacing only, never auto-rescues)",
)
def run_rescue_scan(request: Request):
    return utils.get_response(200, rescue.backfill_scan())
