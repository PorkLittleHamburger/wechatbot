from __future__ import annotations

import asyncio
import logging
from typing import Any

import websockets

from ...service import OneBotService
from .config import WsClientEndpoint
from .events import EventConnection, EventHub
from .requests import websocket_response


logger = logging.getLogger("wechat-onebot.network.ws-client")


class WsClientAdapter:
    def __init__(self, service: OneBotService, hub: EventHub, endpoint: WsClientEndpoint):
        self.service = service
        self.hub = hub
        self.endpoint = endpoint
        self._stopped = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._stopped = False
            self._task = asyncio.create_task(self._run(), name=f"ws-client:{self.endpoint.name}")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _connect(self):
        headers = {
            "X-Self-ID": str(self.service.self_id),
            "X-Client-Role": self.endpoint.role,
        }
        if self.endpoint.access_token:
            headers["Authorization"] = f"Bearer {self.endpoint.access_token}"
        kwargs = dict(ping_interval=20, ping_timeout=20, max_size=16 * 1024 * 1024)
        try:
            return await websockets.connect(self.endpoint.url, additional_headers=headers, **kwargs)
        except TypeError:
            return await websockets.connect(self.endpoint.url, extra_headers=headers, **kwargs)

    async def _run(self) -> None:
        delay = self.endpoint.reconnect_seconds
        while not self._stopped:
            try:
                ws = await self._connect()
                connection = EventConnection(ws.send, f"reverse:{self.endpoint.name}")
                if self.endpoint.role != "API":
                    await self.hub.add(connection)
                logger.info("反向 WebSocket 已连接: %s", self.endpoint.name)
                delay = self.endpoint.reconnect_seconds
                try:
                    if self.endpoint.role != "API":
                        await connection.send_event(self.service.lifecycle_event("connect"))
                    async for raw in ws:
                        await self._handle_message(raw, connection)
                finally:
                    await self.hub.remove(connection)
                    await ws.close()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("反向 WebSocket 连接失败 %s: %s；%.1fs 后重试",
                               self.endpoint.name, error, delay)
            # A clean peer close also needs the configured reconnect delay.
            await asyncio.sleep(delay)

    async def _handle_message(self, raw: str | bytes, connection: EventConnection) -> None:
        if self.endpoint.role == "Event":
            return
        await connection.send_event(await websocket_response(self.service, raw))

    def snapshot(self) -> dict[str, Any]:
        return {"kind": "ws-client", "name": self.endpoint.name, "url": self.endpoint.url,
                "role": self.endpoint.role,
                "started": self._task is not None and not self._task.done()}
