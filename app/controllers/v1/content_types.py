from typing import Optional

from fastapi import Path, Request
from pydantic import BaseModel
from sqlmodel import select

from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import ContentTypeTemplate
from app.models.exception import HttpException
from app.utils import utils

router = new_router()


class UpdateContentTypeRequest(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    default_duration_s: Optional[int] = None
    scriptcraft_overrides: Optional[dict] = None
    visual_strategy: Optional[dict] = None
    voice_style: Optional[str] = None
    subtitle_theme: Optional[str] = None
    music_palette: Optional[str] = None
    research_required: Optional[bool] = None
    freshness_window_hours: Optional[int] = None
    series_capable: Optional[bool] = None
    default_quality_preset: Optional[str] = None


@router.get("/content-types", summary="List content-type templates (New Video cards)")
def list_content_types(request: Request):
    with session_scope() as session:
        templates = session.exec(select(ContentTypeTemplate).order_by(ContentTypeTemplate.label)).all()
        data = [_template_summary(t) for t in templates]
    return utils.get_response(200, {"content_types": data})


@router.put("/content-types/{content_type_id}", summary="Edit a content-type template")
def update_content_type(request: Request, body: UpdateContentTypeRequest, content_type_id: str = Path(...)):
    with session_scope() as session:
        template = session.get(ContentTypeTemplate, content_type_id)
        if template is None:
            raise HttpException(task_id="", status_code=404, message=f"content type {content_type_id!r} not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(template, field, value)
        from app.db.models import utcnow

        template.updated_at = utcnow()
        session.add(template)
        session.commit()
        session.refresh(template)
        data = _template_summary(template)
    return utils.get_response(200, data)


def _template_summary(template: ContentTypeTemplate) -> dict:
    return {
        "id": template.id,
        "label": template.label,
        "description": template.description,
        "default_duration_s": template.default_duration_s,
        "scriptcraft_overrides": template.scriptcraft_overrides,
        "visual_strategy": template.visual_strategy,
        "voice_style": template.voice_style,
        "subtitle_theme": template.subtitle_theme,
        "music_palette": template.music_palette,
        "research_required": template.research_required,
        "freshness_window_hours": template.freshness_window_hours,
        "series_capable": template.series_capable,
        "default_quality_preset": template.default_quality_preset,
    }
