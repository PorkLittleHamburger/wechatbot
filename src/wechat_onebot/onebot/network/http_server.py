from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any
from urllib.parse import parse_qsl

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from ...protocol import failure
from ..actions import ACTION_REGISTRY
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
    return hmac.compare_digest(presented.encode("utf-8"), endpoint.access_token.encode("utf-8"))


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

        async def execute(request: Request, action: str | None = None):
            if not _authorized(self.endpoint, request):
                presented = bool(request.headers.get("authorization") or
                                 request.query_params.get("access_token"))
                code = 403 if presented else 401
                return JSONResponse(failure(1000 + code, "access token 无效"), status_code=code)
            params = dict(request.query_params)
            params.pop("access_token", None)
            if request.method == "POST":
                body = await request.body()
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                query_only = not body and not content_type
                if not query_only and content_type not in ("application/json", "application/x-www-form-urlencoded"):
                    return JSONResponse(failure(1406, "不支持的 Content-Type"), status_code=406)
                try:
                    if query_only:
                        parsed = {}
                    elif content_type == "application/json":
                        parsed = json.loads(body)
                        if not isinstance(parsed, dict):
                            raise ValueError("请求体必须是 JSON 对象")
                    else:
                        parsed = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True,
                                                encoding="utf-8", errors="strict"))
                    params.update(parsed)
                except (ValueError, UnicodeError):
                    return JSONResponse(failure(1400, "请求正文格式不正确"), status_code=400)
            # The configured endpoint retains the gateway's envelope extension.
            # /:action always uses its path action and the body as parameters.
            echo = {}
            if action is None:
                action = params.get("action")
                if not isinstance(action, str):
                    return JSONResponse(failure(1400, "请求必须包含 action"), status_code=400)
                if "echo" in params:
                    echo["echo"] = params["echo"]
                params = params.get("params")
            base_action = action
            for suffix in ("_async", "_rate_limited"):
                if base_action.endswith(suffix):
                    base_action = base_action[:-len(suffix)]
                    break
            if base_action not in ACTION_REGISTRY:
                response = failure(1404, f"不支持 OneBot 动作: {action}")
                response.update(echo)
                return JSONResponse(response, status_code=404)
            return JSONResponse(await self.service.dispatch(action, params, **echo))

        async def envelope_api(request: Request):
            return await execute(request)

        app.add_api_route(self.endpoint.path, envelope_api, methods=["GET", "POST"])

        async def action_api(action: str, request: Request):
            return await execute(request, action)

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

        # Register after utility routes so /healthz isn't interpreted as an action.
        app.add_api_route("/{action}", action_api, methods=["GET", "POST"])
        app.add_api_route("/{action}/", action_api, methods=["GET", "POST"])

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
