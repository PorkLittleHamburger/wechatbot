from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable


class EventConnection:
    def __init__(self, send_text: Callable[[str], Awaitable[None]], label: str):
        self._send_text = send_text
        self.label = label
        self._lock = asyncio.Lock()
        self.closed = False

    async def send_event(self, event: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionError("connection closed")
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            await self._send_text(payload)

    async def close(self) -> None:
        self.closed = True


class EventHub:
    def __init__(self):
        self._connections: set[EventConnection] = set()
        self._lock = asyncio.Lock()

    async def add(self, connection: EventConnection) -> None:
        async with self._lock:
            self._connections.add(connection)

    async def remove(self, connection: EventConnection) -> None:
        connection.closed = True
        async with self._lock:
            self._connections.discard(connection)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            connections = tuple(self._connections)
        if not connections:
            return
        results = await asyncio.gather(*(connection.send_event(event) for connection in connections),
                                       return_exceptions=True)
        dead = [connection for connection, result in zip(connections, results) if isinstance(result, Exception)]
        if dead:
            async with self._lock:
                for connection in dead:
                    connection.closed = True
                    self._connections.discard(connection)

    @property
    def count(self) -> int:
        return len(self._connections)
