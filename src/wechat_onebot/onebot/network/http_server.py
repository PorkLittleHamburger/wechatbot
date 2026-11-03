from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from ...protocol import failure
from ...service import OneBotService
from .config import HttpServerEndpoint


logger = logging.getLogger("wechat-onebot.network.http-server")


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _authorized(endpoint: HttpServerEndpoint, request: Request) -> bool:
    if not endpoint.access_token:
        return True
    presented = _bearer(request.headers.get("authorization")) or request.query_params.get("access_token", "")
    return hmac.compare_digest(presented, endpoint.access_token)


class HttpServerAdapter:
    def __init__(self, service: OneBotService, endpoint: HttpServerEndpoint):
        self.service = service
        self.endpoint = endpoint
        self.app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._started = False

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=f"WeChat OneBot HTTP {self.endpoint.name}")

        async def execute(request: Request):
            if not _authorized(self.endpoint, request):
                return JSONResponse(failure(1403, "access token 无效"), status_code=401)
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse(failure(1400, "请求体必须是 JSON"))
            if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
                return JSONResponse(failure(1400, "请求必须包含 action"))
            return JSONResponse(await self.service.dispatch(payload["action"], payload.get("params"), payload.get("echo")))

        @app.post(self.endpoint.path)
        async def post_api(request: Request):
            return await execute(request)

        @app.post("/{action}")
        async def post_action(action: str, request: Request):
            if not _authorized(self.endpoint, request):
                return JSONResponse(failure(1403, "access token 无效"), status_code=401)
            try:
                body = await request.body()
                params = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                return JSONResponse(failure(1400, "请求体必须是 JSON"))
            if isinstance(params, dict) and isinstance(params.get("action"), str):
                return JSONResponse(await self.service.dispatch(params["action"], params.get("params"), params.get("echo")))
            return JSONResponse(await self.service.dispatch(action, params, None))

        @app.get("/healthz")
        async def healthz():
            return {"ok": True, "adapter": "http-server", "name": self.endpoint.name,
                    "online": self.service.driver.online}

        @app.get("/media/{media_id}/{name}")
        async def media(media_id: str, name: str):
            record = self.service.media.get(media_id)
            if record is None:
                return JSONResponse(failure(1404, "媒体不存在"), status_code=404)
            return FileResponse(record.path, media_type=record.content_type, filename=record.name)

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
        self._task = asyncio.create_task(self._serve(), name=f"http-server:{self.endpoint.name}")
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
        raise RuntimeError(f"HTTP 服务启动失败: {self.endpoint.host}:{self.endpoint.port}")

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except SystemExit as error:
            raise RuntimeError(f"HTTP 服务启动失败: {self.endpoint.host}:{self.endpoint.port}") from error

    async def stop(self) -> None:
        self._started = False
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._server = None

    def snapshot(self) -> dict[str, Any]:
        return {"kind": "http-server", "name": self.endpoint.name, "host": self.endpoint.host,
                "port": self.endpoint.port, "path": self.endpoint.path, "started": self.started}
