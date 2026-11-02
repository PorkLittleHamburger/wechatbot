from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import HttpClientEndpoint


logger = logging.getLogger("wechat-onebot.network.http-client")


class HttpClientAdapter:
    def __init__(self, endpoint: HttpClientEndpoint):
        self.endpoint = endpoint
        self._client: httpx.AsyncClient | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=self.endpoint.timeout_seconds, follow_redirects=True)
        self._queue = asyncio.Queue(maxsize=1024)
        self._task = asyncio.create_task(self._run(), name=f"http-client:{self.endpoint.name}")

    async def send_event(self, event: dict[str, Any]) -> None:
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("HTTP 上报队列已满，丢弃事件: %s", self.endpoint.name)

    async def _run(self) -> None:
        assert self._queue is not None
        assert self._client is not None
        while True:
            event = await self._queue.get()
            try:
                headers = {"Content-Type": "application/json"}
                if self.endpoint.access_token:
                    headers["Authorization"] = f"Bearer {self.endpoint.access_token}"
                response = await self._client.post(self.endpoint.url, json=event, headers=headers)
                response.raise_for_status()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("HTTP 上报失败 %s: %s", self.endpoint.name, error)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
        self._queue = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def snapshot(self) -> dict[str, Any]:
        return {"kind": "http-client", "name": self.endpoint.name, "url": self.endpoint.url,
                "started": self._task is not None, "queued": self._queue.qsize() if self._queue else 0}
