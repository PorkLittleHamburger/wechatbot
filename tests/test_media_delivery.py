from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from urllib.parse import urlparse

import httpx
import pytest
import uvicorn
import websockets

from test_webui import make_config
from wechat_onebot.core.runtime import RuntimeManager, account_media_key
from wechat_onebot.models import IncomingMessage, Segment
from wechat_onebot.onebot.network import AccountNetworkConfig
from wechat_onebot.webui import create_webui_app


@pytest.mark.asyncio
async def test_ws_only_quoted_image_can_be_downloaded_over_real_http_without_management_cookie(tmp_path):
    config = make_config(tmp_path)
    connected = asyncio.Event()
    quoted_result = asyncio.get_running_loop().create_future()

    async def standard_client(websocket):
        assert websocket.request.headers["X-Client-Role"] == "Universal"
        async for raw in websocket:
            payload = json.loads(raw)
            if payload.get("meta_event_type") == "lifecycle":
                connected.set()
            elif payload.get("post_type") == "message":
                assert payload["message"][0]["type"] == "reply"
                await websocket.send(json.dumps({"action": "get_msg", "echo": "quote",
                    "params": {"message_id": payload["message"][0]["data"]["id"]}}))
            elif payload.get("echo") == "quote" and not quoted_result.done():
                quoted_result.set_result(payload)

    reverse_server = await websockets.serve(standard_client, "127.0.0.1", 0)
    reverse_port = reverse_server.sockets[0].getsockname()[1]
    network = AccountNetworkConfig.from_dict({"groupMentionOnly": True, "wsClients": [
        {"name": "standard-client", "url": f"ws://127.0.0.1:{reverse_port}"}]})
    account_network = config.storage.data_dir / "accounts" / "wxid_robot" / "onebot.json"
    account_network.write_text(json.dumps(network.to_dict()), encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = replace(config, webui=replace(config.webui, port=port))
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="error", access_log=False))
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.02)
        assert server.started
        # No OneBot HTTP endpoint is configured; no legacy API port is opened.
        runtime = await manager.start_account("wxid_robot")
        assert runtime.network.config.http_servers == ()
        await asyncio.wait_for(connected.wait(), 3)
        active_base = runtime.media.public_base_url
        image = runtime.media_dir / "inbound" / "same.png"
        body = b"\x89PNG\r\n\x1a\nfixture"
        image.write_bytes(body)
        runtime.media.public_base_url = "http://127.0.0.1:6700"
        await runtime.service._on_driver_event(IncomingMessage("g@chatroom", "author",
            (Segment("image", {"file": str(image)}),), external_message_id="9001", is_group=True))
        runtime.media.public_base_url = active_base
        await runtime.service._on_driver_event(IncomingMessage("g@chatroom", "asker",
            (Segment("at", {"qq": "wxid_robot"}), Segment("text", {"text": "evaluate"})),
            external_message_id="9002", is_group=True,
            raw={"reply": {"native_message_id": "9001", "sender_id": "author", "type": 3}}))
        response = await asyncio.wait_for(quoted_result, 3)
        assert response["status"] == "ok"
        reference = response["data"]
        url = reference["message"][0]["data"]["url"]
        assert urlparse(url).port == port
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(url)
            assert response.status_code == 200 and response.content == body
            assert response.headers["content-type"].startswith("image/")
            assert (await client.get(f"http://127.0.0.1:{port}/api/status")).status_code == 401
            other = url.replace(account_media_key("wxid_robot"), account_media_key("other"))
            assert (await client.get(other)).status_code == 404
            assert (await client.get(url.replace(reference["message"][0]["data"]["file"], "0" * 32))).status_code == 404
        # Switching API listeners changes publication to their actual address.
        from wechat_onebot.core.runtime import _network_public_base_url
        network = AccountNetworkConfig.from_dict({"httpServers": [
            {"name": "api", "host": "::1", "port": 6708}]})
        assert _network_public_base_url(network, config, "wxid_robot") == "http://[::1]:6708"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        reverse_server.close()
        await reverse_server.wait_closed()


@pytest.mark.asyncio
async def test_account_media_route_never_creates_runtime_or_serves_unregistered_paths(tmp_path):
    from fastapi.testclient import TestClient

    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    with TestClient(create_webui_app(config, manager)) as client:
        for url in ("/accounts/not-an-account/media/file/name", "/accounts/秘密/media/123/name"):
            assert client.get(url).status_code == 404
        assert manager.snapshot() == []


@pytest.mark.asyncio
async def test_forward_reads_refresh_nested_media_and_compact_quote_preserves_complete_snapshot(tmp_path):
    from test_service import make_service

    service, _, mapper, events = await make_service(tmp_path)
    try:
        image = service.media.media_dir / "inbound" / "quote.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nquote")
        reference = {"native_message_id": "9001", "sender_id": "author", "type": 49,
            "is_forward": True, "records": [{"kind": "image", "sender_name": "Author",
                "image_status": "ready", "image_path": str(image)}]}
        async def quote(native_id, metadata):
            await service._on_driver_event(IncomingMessage("g@chatroom", "asker",
                (Segment("text", {"text": "evaluate"}),), external_message_id=native_id,
                is_group=True, raw={"reply": metadata}))
        await quote("9002", reference)
        mid = events[-1]["message"][0]["data"]["id"]
        original = (await service.dispatch("get_msg", {"message_id": mid}))["data"]
        fid = original["message"][0]["data"]["id"]
        service.media.public_base_url = "http://127.0.0.1:5199/accounts/account-key"
        forward = (await service.dispatch("get_forward_msg", {"id": fid}))["data"]
        for content in (forward["message"][0]["data"]["content"], forward["messages"][0]["content"]):
            assert content[0]["data"]["url"].startswith(service.media.public_base_url)
        await quote("9003", {**reference, "forwarded_availability": "unavailable", "records": [
            {"kind": "forwarded", "text": "引用只包含聊天记录卡片，原记录未缓存",
             "availability": "unavailable", "failure_reason": "missing_container"}]})
        assert events[-1]["message"][0]["data"]["id"] == mid
        assert (await service.dispatch("get_forward_msg", {"id": fid}))["data"]["status"] == "ready"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_history_refresh_deduplicates_validation_and_does_not_block_event_loop(tmp_path, monkeypatch):
    import threading
    from test_service import make_service

    service, _, mapper, events = await make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    request = None
    try:
        image = service.media.media_dir / "inbound" / "repeated.png"
        image.write_bytes(b"image fixture")
        for index in range(10):
            await service._on_driver_event(IncomingMessage("g@chatroom", "author",
                (Segment("image", {"file": str(image)}),), external_message_id=str(index + 1), is_group=True))
        original = service.media._fingerprint
        calls = []

        def fingerprint(path):
            calls.append(path)
            entered.set()
            assert release.wait(3)
            return original(path)

        monkeypatch.setattr(service.media, "_fingerprint", fingerprint)
        request = asyncio.create_task(service.dispatch("get_group_msg_history", {
            "group_id": events[-1]["group_id"], "count": 100}))
        assert await asyncio.to_thread(entered.wait, 2)
        assert not request.done()  # Event loop remains responsive while hashing is gated.
        release.set()
        response = await asyncio.wait_for(request, 2)
        assert response["status"] == "ok" and len(response["data"]) == 10
        assert len(calls) == 1
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()
        mapper.close()
