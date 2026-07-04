import asyncio
from typing import Optional, Set

from fastapi import WebSocket
from loguru import logger

from app.utils import utils


class ConnectionManager:
    """
    Broadcasts AgentEvents and project status changes to connected dashboard
    clients. Agent/orchestrator code runs on background threads, so broadcast()
    is thread-safe: it schedules the actual send onto the asyncio event loop
    captured at startup via run_coroutine_threadsafe.
    """

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def _broadcast_async(self, message: dict) -> None:
        data = utils.to_json(message)
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    def broadcast(self, message: dict) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast_async(message), self._loop)
        except Exception as exc:  # noqa: BLE001 - broadcasting must never break the pipeline
            logger.warning(f"websocket broadcast failed: {exc}")


manager = ConnectionManager()


def broadcast_status(project_id: int, status: str) -> None:
    manager.broadcast({"type": "project_status", "project_id": project_id, "status": status})


def broadcast_event(project_id: int, agent: str, event_type: str, message: str) -> None:
    manager.broadcast(
        {"type": "agent_event", "project_id": project_id, "agent": agent, "event_type": event_type, "message": message}
    )
