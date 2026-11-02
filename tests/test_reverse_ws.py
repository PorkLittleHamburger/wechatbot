from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import websockets

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.onebot.network.config import WsClientEndpoint
from wechat_onebot.onebot.network.events import EventHub
from wechat_onebot.onebot.network.ws_client import WsClientAdapter
from wechat_onebot.service import OneBotService


@pytest.mark.asyncio
async def test_reverse_websocket_adapter_receives_lifecycle_and_actions(tmp_path: Path):
    config = AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    await service.start()
    messages: asyncio.Queue[dict] = asyncio.Queue()

    expected_self_id = str(service.self_id)
    group_id = mapper.get_or_create("group", "10001@chatroom")
    user_id = mapper.get_or_create("user", "wxid_alice")

    async def handler(websocket):
        assert websocket.request.headers["X-Self-ID"] == expected_self_id
        assert websocket.request.headers["X-Client-Role"] == "Universal"
        assert websocket.request.headers["Authorization"] == "Bearer test-token"
        await messages.put(json.loads(await websocket.recv()))
        await websocket.send(json.dumps({"action": "get_status", "echo": "reverse"}))
        await messages.put(json.loads(await websocket.recv()))
        await websocket.send(json.dumps({
            "action": "send_group_msg", "echo": {"seq": 2},
            "params": {"group_id": group_id, "message": [
                {"type": "reply", "data": {"id": "-183402341"}},
                {"type": "at", "data": {"qq": user_id, "name": "Alice"}},
                {"type": "text", "data": {"text": "\n今日人品值：87 / 100"}},
            ]},
        }))
        await messages.put(json.loads(await websocket.recv()))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    adapter = WsClientAdapter(service, EventHub(), WsClientEndpoint(
        "test", f"ws://127.0.0.1:{port}", "test-token", 0.5, True))
    adapter.start()
    try:
        lifecycle = await asyncio.wait_for(messages.get(), timeout=3)
        response = await asyncio.wait_for(messages.get(), timeout=3)
        sent = await asyncio.wait_for(messages.get(), timeout=3)
        assert lifecycle["post_type"] == "meta_event"
        assert lifecycle["meta_event_type"] == "lifecycle"
        assert response["status"] == "ok"
        assert response["echo"] == "reverse"
        assert response["data"]["online"] is True
        assert sent["status"] == "ok" and sent["echo"] == {"seq": 2}
        assert driver.sent[-1].kind == "at_text"
        assert driver.sent[-1].payload["user_ids"] == ["wxid_alice"]
    finally:
        await adapter.stop()
        server.close()
        await server.wait_closed()
        await service.stop()
        mapper.close()
