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
        self._lock = asyncio.Lock()
        self.started = False

    async def start(self) -> None:
        async with self._lock:
            await self._start()

    async def _start(self) -> None:
        if self.started:
            return
        self.service.set_event_sink(self.emit)
        started_servers: list[Any] = []
        try:
            for adapter in self.http_servers:
                started_servers.append(adapter)
                await adapter.start()
            for adapter in self.ws_servers:
                started_servers.append(adapter)
                await adapter.start()
            for adapter in self.http_clients:
                await adapter.start()
            for adapter in self.ws_clients:
                adapter.start()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="onebot-heartbeat")
            self.started = True
            await self.emit(self.service.lifecycle_event("connect"))
        except Exception:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                self._heartbeat_task = None
            self.started = False
            for adapter in reversed(started_servers):
                await adapter.stop()
            for adapter in self.http_clients:
                await adapter.stop()
            for adapter in self.ws_clients:
                await adapter.stop()
            self.service.set_event_sink(None)
            raise

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
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

    async def reconfigure(self, config: AccountNetworkConfig) -> None:
        async with self._lock:
            new_http_servers, add_http_servers, drop_http_servers = self._reconcile(
                self.http_servers, config.http_servers, lambda endpoint: HttpServerAdapter(self.service, endpoint))
            new_ws_servers, add_ws_servers, drop_ws_servers = self._reconcile(
                self.ws_servers, config.ws_servers, lambda endpoint: WsServerAdapter(self.service, self.hub, endpoint))
            new_http_clients, add_http_clients, drop_http_clients = self._reconcile(
                self.http_clients, config.http_clients, HttpClientAdapter)
            new_ws_clients, add_ws_clients, drop_ws_clients = self._reconcile(
                self.ws_clients, config.ws_clients, lambda endpoint: WsClientAdapter(self.service, self.hub, endpoint))

            if self.started:
                added_servers = add_http_servers + add_ws_servers
                removed_servers = drop_http_servers + drop_ws_servers
                conflicts = [old for old in removed_servers if any(
                    self._binds_conflict(old.endpoint, new.endpoint) for new in added_servers)]
                stopped_conflicts = []
                try:
                    for adapter in added_servers:
                        if not any(self._binds_conflict(adapter.endpoint, old.endpoint) for old in conflicts):
                            await adapter.start()
                    for adapter in conflicts:
                        await adapter.stop()
                        stopped_conflicts.append(adapter)
                    for adapter in added_servers:
                        if not adapter.started:
                            await adapter.start()
                    for adapter in add_http_clients:
                        await adapter.start()
                    for adapter in add_ws_clients:
                        adapter.start()
                except Exception:
                    await asyncio.gather(*(adapter.stop() for adapter in add_ws_clients), return_exceptions=True)
                    await asyncio.gather(*(adapter.stop() for adapter in add_http_clients), return_exceptions=True)
                    await asyncio.gather(*(adapter.stop() for adapter in added_servers), return_exceptions=True)
                    for adapter in stopped_conflicts:
                        await adapter.start()
                    raise
                for adapter in removed_servers:
                    if adapter not in stopped_conflicts:
                        await adapter.stop()
                await asyncio.gather(*(adapter.stop() for adapter in drop_http_clients))
                await asyncio.gather(*(adapter.stop() for adapter in drop_ws_clients))
                if config.heartbeat_interval_seconds != self.config.heartbeat_interval_seconds:
                    self._heartbeat_task.cancel()
                    await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="onebot-heartbeat")

            self.http_servers = new_http_servers
            self.ws_servers = new_ws_servers
            self.http_clients = new_http_clients
            self.ws_clients = new_ws_clients
            self.config = config

    @staticmethod
    def _reconcile(existing, endpoints, factory):
        by_endpoint = {adapter.endpoint: adapter for adapter in existing}
        updated = [by_endpoint.get(endpoint) or factory(endpoint) for endpoint in endpoints if endpoint.enabled]
        added = [adapter for adapter in updated if adapter not in existing]
        removed = [adapter for adapter in existing if adapter not in updated]
        return updated, added, removed

    @staticmethod
    def _binds_conflict(left, right) -> bool:
        if left.port != right.port:
            return False
        return (left.host == right.host or left.host in ("0.0.0.0", "::")
                or right.host in ("0.0.0.0", "::"))

    async def emit(self, event: dict[str, Any]) -> None:
        await self.hub.broadcast(event)
        await asyncio.gather(*(adapter.send_event(event) for adapter in self.http_clients), return_exceptions=True)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            event = self.service.heartbeat_event()
            event["interval"] = int(self.config.heartbeat_interval_seconds * 1000)
            await self.emit(event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "connections": self.hub.count,
            "httpServers": [adapter.snapshot() for adapter in self.http_servers],
            "httpClients": [adapter.snapshot() for adapter in self.http_clients],
            "wsServers": [adapter.snapshot() for adapter in self.ws_servers],
            "wsClients": [adapter.snapshot() for adapter in self.ws_clients],
        }
