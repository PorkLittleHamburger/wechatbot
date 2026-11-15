"""Shared HTTP exposure hardening: security headers and request-body limits.

Both middlewares are plain ASGI wrappers so they also cover responses produced
before FastAPI routing (for example 413 rejections) and do not depend on
Starlette's ``BaseHTTPMiddleware``.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI


SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Deliberately does not restrict scripts: the WebUI is a single inline-file UI.
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
}
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SecurityHeadersMiddleware:
    """Append the gateway security headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] in ("http.response.start", "websocket.http.response.start"):
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in SECURITY_HEADERS.items():
                    if name.lower().encode("ascii") not in present:
                        headers.append((name.encode("ascii"), value.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Reject oversized HTTP request bodies before the application sees them.

    The body is buffered only up to ``limit`` bytes; both Content-Length and
    chunked/streamed bodies are bounded by counting actual bytes.
    """

    def __init__(self, app, *, limit: int | None = None,
                 limit_for: Callable[[str, str], int | None] | None = None,
                 status_code: int = 413, body: bytes = b"",
                 content_type: str = "text/plain; charset=utf-8"):
        self.app = app
        self.limit = limit
        self.limit_for = limit_for
        self.status_code = status_code
        self.body = body
        self.content_type = content_type

    def _limit_for_request(self, method: str, path: str) -> int | None:
        if method not in _BODY_METHODS:
            return None
        if self.limit_for is not None:
            return self.limit_for(method, path)
        return self.limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for_request(scope.get("method", ""), scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return
        buffered = bytearray()
        pending: list[dict[str, Any]] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                pending.append(message)
                break
            buffered.extend(message.get("body", b""))
            if len(buffered) > limit:
                await self._reject(send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(buffered), "more_body": False}
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send) -> None:
        await send({
            "type": "http.response.start",
            "status": self.status_code,
            "headers": [(b"content-type", self.content_type.encode("latin-1")),
                        (b"content-length", str(len(self.body)).encode("ascii"))],
        })
        await send({"type": "http.response.body", "body": self.body})


def install_security_headers(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)


def install_body_limit(app: FastAPI, *, limit: int | None = None,
                       limit_for: Callable[[str, str], int | None] | None = None,
                       status_code: int = 413, body: bytes = b"",
                       content_type: str = "text/plain; charset=utf-8") -> None:
    app.add_middleware(BodyLimitMiddleware, limit=limit, limit_for=limit_for,
                       status_code=status_code, body=body, content_type=content_type)
