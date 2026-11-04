from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from .config import HttpClientEndpoint


logger = logging.getLogger("wechat-onebot.network.http-client")


class HttpClientAdapter:
    def __init__(self, endpoint: HttpClientEndpoint,
                 quick_operation: Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]] | None = None):
        self.endpoint = endpoint
        self._quick_operation = quick_operation
        self._client: httpx.AsyncClient | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=self.endpoint.timeout_seconds or None, follow_redirects=True)
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
                body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                headers = {"Content-Type": "application/json", "X-Self-ID": str(event.get("self_id", ""))}
                if self.endpoint.secret:
                    headers["X-Signature"] = "sha1=" + hmac.new(
                        self.endpoint.secret.encode("utf-8"), body, "sha1").hexdigest()
                if self.endpoint.access_token:
                    headers["Authorization"] = f"Bearer {self.endpoint.access_token}"
                response = await self._client.post(self.endpoint.url, content=body, headers=headers)
                response.raise_for_status()
                if response.status_code != 204 and response.content.strip():
                    operations = response.json()
                    if not isinstance(operations, dict):
                        raise ValueError("快速操作响应必须是 JSON 对象")
                    if operations:
                        if self._quick_operation is None:
                            raise RuntimeError("未配置快速操作处理器")
                        result = await self._quick_operation(event, operations)
                        if isinstance(result, dict) and result.get("status") == "failed":
                            logger.warning("HTTP 快速操作失败 %s: %s", self.endpoint.name, result)
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

    async def flush(self) -> None:
        if self._queue is not None:
            await self._queue.join()

    def snapshot(self) -> dict[str, Any]:
        return {"kind": "http-client", "name": self.endpoint.name, "url": self.endpoint.url,
                "started": self._task is not None, "queued": self._queue.qsize() if self._queue else 0}
