from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig, WebUiConfig
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.onebot.network.config import HttpClientEndpoint, HttpServerEndpoint, WsServerEndpoint
from wechat_onebot.onebot.network.http_client import HttpClientAdapter
from wechat_onebot.onebot.network.events import EventHub
from wechat_onebot.onebot.network.http_server import HttpServerAdapter
from wechat_onebot.onebot.network.ws_server import WsServerAdapter
from wechat_onebot.service import OneBotService


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
        webui=WebUiConfig(access_token="webui-token"),
    )


async def make_service(tmp_path: Path):
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    await service.start()
    return config, service, mapper


async def test_http_auth_and_action(tmp_path: Path):
    config, service, mapper = await make_service(tmp_path)
    adapter = HttpServerAdapter(service, HttpServerEndpoint("default", "127.0.0.1", 6700, "/", "test-token"))
    try:
        transport = httpx.ASGITransport(app=adapter.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/", json={"action": "get_status"})).status_code == 401
            response = await client.post("/", headers={"Authorization": "Bearer test-token"},
                                         json={"action": "get_status", "echo": 7})
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "ok"
            assert payload["echo"] == 7
            assert payload["data"]["online"] is True
    finally:
        await service.stop()
        mapper.close()


def test_forward_websocket_adapter(tmp_path: Path):
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    asyncio.run(service.start())
    adapter = WsServerAdapter(service, EventHub(), WsServerEndpoint("default", "127.0.0.1", 6701, "/ws", "test-token"))
    try:
        with TestClient(adapter.app) as client:
            with client.websocket_connect("/ws?access_token=test-token") as websocket:
                lifecycle = websocket.receive_json()
                assert lifecycle["post_type"] == "meta_event"
                websocket.send_json({"action": "get_login_info", "echo": "login"})
                response = websocket.receive_json()
                assert response["status"] == "ok"
                assert response["echo"] == "login"
    finally:
        asyncio.run(service.stop())
        mapper.close()


async def test_http_string_booleans_and_json_message_with_real_service(tmp_path: Path):
    _, service, mapper = await make_service(tmp_path)
    user_id = mapper.get_or_create("user", "wxid_alice")
    adapter = HttpServerAdapter(service, HttpServerEndpoint("api", "127.0.0.1", 6700, "/", ""))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app), base_url="http://test") as client:
            params = {"user_id": str(user_id), "message": "[CQ:at,qq=123]", "auto_escape": "true"}
            assert (await client.get("/send_private_msg", params=params)).json()["status"] == "ok"
            assert service.driver.sent[-1].payload["text"] == "[CQ:at,qq=123]"
            params["auto_escape"] = "false"
            params["message"] = "hello"
            assert (await client.post("/send_private_msg", data=params)).json()["status"] == "ok"
            params["auto_escape"] = "true"
            assert (await client.post("/send_private_msg", json=params)).json()["status"] == "ok"
            # Missing resources are action failures; unknown APIs are HTTP 404.
            response = await client.get("/get_msg?message_id=123")
            assert response.status_code == 200 and response.json()["status"] == "failed"
    finally:
        await service.stop()
        mapper.close()


async def test_http_post_real_quick_reply_and_unsupported_operation(tmp_path: Path, caplog):
    _, service, mapper = await make_service(tmp_path)
    user_id = mapper.get_or_create("user", "wxid_alice")
    event = {"self_id": service.self_id, "post_type": "message", "message_type": "private", "user_id": user_id}
    replies = iter([{"reply": "hello"}, {"reply": "must not send", "kick": True}, {"reply": "after failure"}])
    adapter = HttpClientAdapter(HttpClientEndpoint("post", "http://test", ""), service.handle_quick_operation)
    await adapter.start()
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(replies))))
    try:
        for _ in range(3):
            await adapter.send_event(event)
            await asyncio.wait_for(adapter.flush(), 2)
        assert [sent.payload["text"] for sent in service.driver.sent] == ["hello", "after failure"]
        assert "kick" in caplog.text
    finally:
        await adapter.stop()
        await service.stop()
        mapper.close()
