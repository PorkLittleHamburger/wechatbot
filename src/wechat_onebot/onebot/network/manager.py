from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...service import OneBotService
from .config import AccountNetworkConfig
from .events import EventHub
from .http_client import HttpClientAdapter
from .http_server import HttpServerAdapter
from .ws_client import WsClientAdapter
from .ws_server import WsServerAdapter


logger = logging.getLogger("wechat-onebot.network")


class OneBotNetworkManager:
    """Owns all network adapters for one account/OneBot instance."""

    def __init__(self, service: OneBotService, config: AccountNetworkConfig):
        self.service = service
        self.config = config
        self.hub = EventHub()
        self.http_servers = [HttpServerAdapter(service, endpoint) for endpoint in config.http_servers if endpoint.enabled]
        self.ws_servers = [WsServerAdapter(service, self.hub, endpoint) for endpoint in config.ws_servers if endpoint.enabled]
        self.http_clients = [HttpClientAdapter(endpoint) for endpoint in config.http_clients if endpoint.enabled]
        self.ws_clients = [WsClientAdapter(service, self.hub, endpoint) for endpoint in config.ws_clients if endpoint.enabled]
        self._heartbeat_task: asyncio.Task | None = None
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        self.service.set_event_sink(self.emit)
        started_servers: list[Any] = []
        try:
            for adapter in self.http_servers:
                await adapter.start()
                started_servers.append(adapter)
            for adapter in self.ws_servers:
                await adapter.start()
                started_servers.append(adapter)
            for adapter in self.http_clients:
                await adapter.start()
            for adapter in self.ws_clients:
                adapter.start()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="onebot-heartbeat")
            self.started = True
            await self.emit(self.service.lifecycle_event("connect"))
        except Exception:
            for adapter in reversed(started_servers):
                await adapter.stop()
            for adapter in self.http_clients:
                await adapter.stop()
            for adapter in self.ws_clients:
                await adapter.stop()
            self.service.set_event_sink(None)
            raise

    async def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        await asyncio.gather(*(adapter.stop() for adapter in self.ws_clients), return_exceptions=True)
        await asyncio.gather(*(adapter.stop() for adapter in self.http_clients), return_exceptions=True)
        await asyncio.gather(*(adapter.stop() for adapter in self.ws_servers), return_exceptions=True)
        await asyncio.gather(*(adapter.stop() for adapter in self.http_servers), return_exceptions=True)
        self.service.set_event_sink(None)

    async def emit(self, event: dict[str, Any]) -> None:
        await self.hub.broadcast(event)
        await asyncio.gather(*(adapter.send_event(event) for adapter in self.http_clients), return_exceptions=True)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            await self.emit(self.service.heartbeat_event())

    def snapshot(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "connections": self.hub.count,
            "httpServers": [adapter.snapshot() for adapter in self.http_servers],
            "httpClients": [adapter.snapshot() for adapter in self.http_clients],
            "wsServers": [adapter.snapshot() for adapter in self.ws_servers],
            "wsClients": [adapter.snapshot() for adapter in self.ws_clients],
        }
