from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ...protocol import failure
from ...service import OneBotService
from .config import WsServerEndpoint
from .events import EventConnection, EventHub


logger = logging.getLogger("wechat-onebot.network.ws-server")


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _authorized(endpoint: WsServerEndpoint, headers, query) -> bool:
    if not endpoint.access_token:
        return True
    presented = _bearer(headers.get("authorization")) or (query.get("access_token") or "")
    return hmac.compare_digest(presented, endpoint.access_token)


class WsServerAdapter:
    def __init__(self, service: OneBotService, hub: EventHub, endpoint: WsServerEndpoint):
        self.service = service
        self.hub = hub
        self.endpoint = endpoint
        self.app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._started = False

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=f"WeChat OneBot WS {self.endpoint.name}")

        @app.get("/healthz")
        async def healthz():
            return {"ok": True, "adapter": "ws-server", "name": self.endpoint.name,
                    "connections": self.hub.count, "online": self.service.driver.online}

        @app.websocket(self.endpoint.path)
        async def websocket_api(websocket: WebSocket):
            if not _authorized(self.endpoint, websocket.headers, websocket.query_params):
                await websocket.close(code=1008, reason="access token invalid")
                return
            await websocket.accept()
            connection = EventConnection(websocket.send_text, f"forward:{self.endpoint.name}:{websocket.client}")
            await self.hub.add(connection)
            try:
                await connection.send_event(self.service.lifecycle_event("connect"))
                while True:
                    raw = await websocket.receive_text()
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        await connection.send_event(failure(1400, "消息必须是 JSON"))
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("action"), str):
                        response = await self.service.dispatch(payload["action"], payload.get("params"), payload.get("echo"))
                        await connection.send_event(response)
                    else:
                        await connection.send_event(failure(1400, "消息必须包含 action"))
            except WebSocketDisconnect:
                pass
            finally:
                await self.hub.remove(connection)

        return app

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        config = uvicorn.Config(self.app, host=self.endpoint.host, port=self.endpoint.port,
                                log_level="warning", access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        if hasattr(self._server, "install_signal_handlers"):
            self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._serve(), name=f"ws-server:{self.endpoint.name}")
        for _ in range(100):
            if self._server.started:
                self._started = True
                return
            if self._task.done():
                break
            await asyncio.sleep(0.02)
        self._started = False
        if self._task.done():
            await self._task
        raise RuntimeError(f"WebSocket 服务启动失败: {self.endpoint.host}:{self.endpoint.port}{self.endpoint.path}")

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except SystemExit as error:
            raise RuntimeError(f"WebSocket 服务启动失败: {self.endpoint.host}:{self.endpoint.port}{self.endpoint.path}") from error

    async def stop(self) -> None:
        self._started = False
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._server = None

    def snapshot(self) -> dict[str, Any]:
        return {"kind": "ws-server", "name": self.endpoint.name, "host": self.endpoint.host,
                "port": self.endpoint.port, "path": self.endpoint.path,
                "started": self.started, "connections": self.hub.count}
