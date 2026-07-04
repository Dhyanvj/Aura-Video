from fastapi import WebSocket, WebSocketDisconnect

from app.controllers.v1.base import new_router
from app.services.ws_manager import manager

router = new_router()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # The dashboard is a pure subscriber; any inbound message is just
            # a keepalive ping and can be discarded.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
