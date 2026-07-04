"""Application implementation - ASGI."""

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents.orchestrator import resume_incomplete_projects
from app.config import config
from app.db import init_db
from app.models.exception import HttpException
from app.router import root_api_router
from app.services import singleton_lock
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.ws_manager import manager as ws_manager
from app.utils import utils


def exception_handler(request: Request, e: HttpException):
    return JSONResponse(
        status_code=e.status_code,
        content=utils.get_response(e.status_code, e.data, e.message),
    )


def validation_exception_handler(request: Request, e: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content=utils.get_response(
            status=400, data=e.errors(), message="field required"
        ),
    )


# Path prefixes that are real backend routes, not dashboard client-side routes.
_NON_DASHBOARD_PREFIXES = ("/api", "/tasks", "/docs", "/redoc", "/openapi.json")


def spa_fallback_handler(request: Request, e: StarletteHTTPException):
    # The dashboard is a client-side-routed SPA (React Router). A direct
    # navigation or refresh on e.g. /settings or /projects/5 is a real backend
    # 404 (StaticFiles found no such file) unless we fall back to index.html
    # and let the client-side router take over, same as any SPA host would.
    if e.status_code == 404 and not request.url.path.startswith(_NON_DASHBOARD_PREFIXES):
        index_file = os.path.join(utils.public_dir(), "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
    return JSONResponse(status_code=e.status_code, content={"detail": e.detail})


def get_application() -> FastAPI:
    """Initialize FastAPI application.

    Returns:
       FastAPI: Application object instance.

    """
    instance = FastAPI(
        title=config.project_name,
        description=config.project_description,
        version=config.project_version,
        debug=False,
    )
    instance.include_router(root_api_router)
    instance.add_exception_handler(HttpException, exception_handler)
    instance.add_exception_handler(RequestValidationError, validation_exception_handler)
    instance.add_exception_handler(StarletteHTTPException, spa_fallback_handler)
    return instance


app = get_application()

# Configures the CORS middleware for the FastAPI app
cors_allowed_origins_str = os.getenv("CORS_ALLOWED_ORIGINS", "")
origins = cors_allowed_origins_str.split(",") if cors_allowed_origins_str else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

task_dir = utils.task_dir()
app.mount(
    "/tasks", StaticFiles(directory=task_dir, html=True, follow_symlink=True), name=""
)

public_dir = utils.public_dir()
app.mount("/", StaticFiles(directory=public_dir, html=True), name="")


@app.on_event("shutdown")
def shutdown_event():
    logger.info("shutdown event")
    stop_scheduler()
    singleton_lock.release()


@app.on_event("startup")
def startup_event():
    logger.info("startup event")
    ws_manager.set_loop(asyncio.get_event_loop())
    init_db()

    # uvicorn runs this lifespan startup before it binds the listen socket, so
    # a second `python main.py` on an already-used port would otherwise still
    # resume in-flight projects and start the scheduler - racing real agent
    # work against whatever instance is actually running - before failing to
    # bind and exiting. Skip those side effects if another instance is alive.
    if not singleton_lock.acquire():
        logger.warning("startup side effects skipped (see error above); this process will likely fail to bind")
        return

    resume_incomplete_projects()
    start_scheduler()
