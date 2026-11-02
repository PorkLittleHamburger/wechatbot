from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
from fastapi import FastAPI, Request

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, ReverseWsConfig, StorageConfig
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.onebot.network import HttpClientAdapter
from wechat_onebot.onebot.network import (
    AccountNetworkConfig,
    HttpClientEndpoint,
    HttpServerEndpoint,
    OneBotNetworkManager,
    WsServerEndpoint,
    load_or_create_account_network_config,
)
from wechat_onebot.service import OneBotService


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(host="127.0.0.1", port=6700, access_token="legacy-token"),
        reverse_ws=(ReverseWsConfig("ws://127.0.0.1:7000/ws", "reverse-token", 2.0, True),),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )


def test_account_network_config_migration(tmp_path: Path):
    config = make_config(tmp_path)
    path = tmp_path / "data" / "accounts" / "wxid_robot" / "onebot.json"
    network = load_or_create_account_network_config(path, config, "wxid_robot")
    assert path.is_file()
    assert network.http_servers[0].port == 6700
    assert network.ws_servers[0].port == 6701
    assert network.ws_clients[0].url == "ws://127.0.0.1:7000/ws"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["httpServers"][0]["accessToken"] == "legacy-token"
    assert raw["wsClients"][0]["reconnectSeconds"] == 2.0


@pytest.mark.asyncio
async def test_network_manager_serves_http_and_forward_websocket(tmp_path: Path):
    config = make_config(tmp_path)
    http_port, ws_port = free_port(), free_port()
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url=f"http://127.0.0.1:{http_port}")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    network_config = AccountNetworkConfig(
        heartbeat_interval_seconds=30.0,
        http_servers=(HttpServerEndpoint("http", "127.0.0.1", http_port, "/", "ob-token"),),
        http_clients=(),
        ws_servers=(WsServerEndpoint("ws", "127.0.0.1", ws_port, "/ws", "ob-token"),),
        ws_clients=(),
    )
    network = OneBotNetworkManager(service, network_config)
    await service.start()
    try:
        await network.start()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{http_port}", timeout=5) as client:
            unauthorized = await client.post("/", json={"action": "get_status"})
            assert unauthorized.status_code == 401
            response = await client.post("/", headers={"Authorization": "Bearer ob-token"},
                                         json={"action": "get_status"})
            assert response.json()["status"] == "ok"
        async with websockets.connect(f"ws://127.0.0.1:{ws_port}/ws?access_token=ob-token") as ws:
            lifecycle = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert lifecycle["meta_event_type"] == "lifecycle"
            await driver.inject_message(text="broadcast", chat_id="wxid_alice")
            event = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert event["post_type"] == "message"
            assert event["message"][0]["data"]["text"] == "broadcast"
    finally:
        await network.stop()
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_http_client_reports_events(tmp_path: Path):
    received: list[dict] = []
    app = FastAPI()

    @app.post("/report")
    async def report(request: Request):
        received.append(await request.json())
        return {"ok": True}

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                             log_level="warning", access_log=False, lifespan="off"))
    if hasattr(server, "install_signal_handlers"):
        server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    adapter = HttpClientAdapter(HttpClientEndpoint("report", f"http://127.0.0.1:{port}/report", "token", 2, True))
    await adapter.start()
    try:
        await adapter.send_event({"post_type": "meta_event", "meta_event_type": "heartbeat"})
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.02)
        assert received[0]["post_type"] == "meta_event"
    finally:
        await adapter.stop()
        server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)
