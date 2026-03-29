from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from jhsymphony.dashboard.routes import issues, runs, stats
from jhsymphony.dashboard.ws import EventHub
from jhsymphony.storage.base import Storage


def create_app(storage: Storage) -> FastAPI:
    app = FastAPI(title="JHSymphony Dashboard", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.state.storage = storage
    app.state.event_hub = EventHub()

    app.include_router(issues.router)
    app.include_router(runs.router)
    app.include_router(stats.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.websocket("/ws/events")
    async def websocket_events(ws: WebSocket):
        hub: EventHub = app.state.event_hub
        await hub.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(ws)

    return app
