from __future__ import annotations

import json
from typing import Any

from ...protocol import failure


async def websocket_response(service, raw: str | bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return failure(1400, "消息必须是 JSON")
    echo = {"echo": payload["echo"]} if isinstance(payload, dict) and "echo" in payload else {}
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        response = failure(1400, "消息必须包含 action")
        response.update(echo)
        return response
    return await service.dispatch(payload["action"], payload.get("params"), **echo)
