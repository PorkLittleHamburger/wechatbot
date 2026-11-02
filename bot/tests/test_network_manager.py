from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
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
    HttpServerAdapter,
    OneBotNetworkManager,
    WsServerEndpoint,
    WsClientEndpoint,
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


def test_new_account_network_has_no_default_endpoints(tmp_path: Path):
    config = make_config(tmp_path)
    path = tmp_path / "data" / "accounts" / "wxid_robot" / "onebot.json"
    network = load_or_create_account_network_config(path, config, "wxid_robot")
    assert path.is_file()
    assert network.http_servers == ()
    assert network.http_clients == ()
    assert network.ws_servers == ()
    assert network.ws_clients == ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert all(raw[kind] == [] for kind in ("httpServers", "httpClients", "wsServers", "wsClients"))


def test_saved_account_network_is_preserved(tmp_path: Path):
    config = make_config(tmp_path)
    path = tmp_path / "data" / "accounts" / "wxid_robot" / "onebot.json"
    path.parent.mkdir(parents=True)
    existing = {
        "heartbeatIntervalSeconds": 5,
        "httpServers": [{"name": "saved", "host": "127.0.0.1", "port": 6700,
                         "path": "/", "accessToken": "legacy-token", "enabled": True}],
        "httpClients": [], "wsServers": [], "wsClients": [],
    }
    path.write_text(json.dumps(existing), encoding="utf-8")
    network = load_or_create_account_network_config(path, config, "wxid_robot")
    assert network.http_servers[0].name == "saved"
    assert json.loads(path.read_text(encoding="utf-8")) == existing


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
async def test_same_port_update_restores_old_server_on_failure(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    port = free_port()
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url=f"http://127.0.0.1:{port}")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    endpoint = HttpServerEndpoint("api", "127.0.0.1", port, "/", "original-token")
    network = OneBotNetworkManager(service, AccountNetworkConfig(30, (endpoint,), (), (), ()))
    await service.start()
    await network.start()
    original_adapter = network.http_servers[0]
    original_start = HttpServerAdapter.start

    async def fail_new_adapter(self):
        if self.endpoint.access_token == "broken-token":
            raise RuntimeError("synthetic bind failure")
        await original_start(self)

    monkeypatch.setattr(HttpServerAdapter, "start", fail_new_adapter)
    try:
        changed = AccountNetworkConfig(30, (replace(endpoint, access_token="broken-token"),), (), (), ())
        with pytest.raises(RuntimeError, match="synthetic bind failure"):
            await network.reconfigure(changed)
        assert network.http_servers[0] is original_adapter
        assert original_adapter.started and driver.online
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
            response = await client.post("/", headers={"Authorization": "Bearer original-token"},
                                         json={"action": "get_status"})
            assert response.json()["status"] == "ok"
    finally:
        await network.stop()
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_reverse_websocket_client_stays_connected_when_driver_stops(tmp_path: Path):
    received: asyncio.Queue[dict] = asyncio.Queue()

    async def receiver(ws):
        async for raw in ws:
            await received.put(json.loads(raw))

    port = free_port()
    server = await websockets.serve(receiver, "127.0.0.1", port)
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media, account="wxid_robot")
    service.self_id = mapper.get_or_create("user", "wxid_robot")
    endpoint = WsClientEndpoint("reverse", f"ws://127.0.0.1:{port}/ws", "", 1.0, True)
    network = OneBotNetworkManager(service, AccountNetworkConfig(30, (), (), (), (endpoint,)))
    try:
        await network.start()
        lifecycle = await asyncio.wait_for(received.get(), 3)
        assert lifecycle["meta_event_type"] == "lifecycle"
        assert lifecycle["self_id"] == service.self_id
        assert driver.online is False
        await service.start()
        await network.emit({"post_type": "notice", "self_id": service.self_id, "notice_type": "started"})
        assert (await asyncio.wait_for(received.get(), 3))["notice_type"] == "started"
        await service.stop()
        assert network.ws_clients[0].snapshot()["started"] is True
        await network.emit({"post_type": "notice", "self_id": service.self_id, "notice_type": "stopped"})
        assert (await asyncio.wait_for(received.get(), 3))["notice_type"] == "stopped"
    finally:
        await network.stop()
        await service.stop()
        mapper.close()
        server.close()
        await server.wait_closed()


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
