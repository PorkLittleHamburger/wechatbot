from __future__ import annotations

import asyncio
import hmac
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from wechat_onebot.config import ConfigError
from wechat_onebot.onebot.network import (
    AccountNetworkConfig, HttpClientAdapter, HttpClientEndpoint, HttpServerAdapter,
    HttpServerEndpoint, OneBotNetworkManager, WsClientAdapter, WsClientEndpoint,
    WsServerAdapter, WsServerEndpoint,
)
from wechat_onebot.onebot.network.events import EventConnection, EventHub


UNSET = object()


class Service:
    self_id = 1234
    driver = SimpleNamespace(online=True)

    def __init__(self):
        self.calls = []
        self.operations = []

    async def dispatch(self, action, params=None, echo=UNSET):
        self.calls.append((action, params, echo))
        result = {"status": "ok", "retcode": 0, "data": params}
        if action == "get_msg":
            result.update(status="failed", retcode=1404)
        if echo is not UNSET:
            result["echo"] = echo
        return result

    async def handle_quick_operation(self, event, operations):
        await asyncio.sleep(0)
        self.operations.append((event, operations))

    def lifecycle_event(self, sub_type):
        return {"self_id": self.self_id, "post_type": "meta_event",
                "meta_event_type": "lifecycle", "sub_type": sub_type}

    def set_event_sink(self, sink):
        self.sink = sink


def http_adapter(token=""):
    service = Service()
    return service, HttpServerAdapter(service, HttpServerEndpoint("test", "localhost", 6700, "/", token))


@pytest.mark.parametrize("method,kwargs,expected", [
    ("GET", {"params": {"user_id": "42", "message": "你好 & +"}}, {"user_id": "42", "message": "你好 & +"}),
    ("POST", {"params": {"user_id": "42", "message": "你好 & +"}}, {"user_id": "42", "message": "你好 & +"}),
    ("POST", {"data": {"user_id": "42", "message": "你好 & +"}}, {"user_id": "42", "message": "你好 & +"}),
    ("POST", {"json": {"user_id": 42, "message": [{"type": "text", "data": {"text": "hi"}}]}},
     {"user_id": 42, "message": [{"type": "text", "data": {"text": "hi"}}]}),
])
async def test_http_parameter_encodings(method, kwargs, expected):
    service, adapter = http_adapter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app), base_url="http://test") as client:
        response = await client.request(method, "/send_private_msg/", **kwargs)
    assert response.status_code == 200
    assert service.calls == [("send_private_msg", expected, UNSET)]


async def test_http_errors_auth_and_action_failure_are_distinct():
    service, adapter = http_adapter("secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app), base_url="http://test") as client:
        assert (await client.get("/get_status")).status_code == 401
        assert (await client.get("/get_status?access_token=wrong")).status_code == 403
        assert (await client.get("/get_status", headers={"Authorization": "Basic wrong"})).status_code == 403
        client.headers["Authorization"] = "Bearer secret"
        assert (await client.post("/get_status", content="hello")).status_code == 406
        assert (await client.post("/get_status", content="{", headers={"Content-Type": "application/json"})).status_code == 400
        assert (await client.post("/get_status", json=[])).status_code == 400
        assert (await client.get("/not_an_action")).status_code == 404
        assert (await client.get("/get_msg?message_id=1")).status_code == 200
        assert (await client.get("/get_status_async")).status_code == 200
        response = await client.post("/", json={"action": "get_status", "echo": None})
        assert "echo" in response.json() and response.json()["echo"] is None
        # Path action can't be replaced by an unrelated body field.
        await client.post("/get_status?arg=query", json={"action": "get_login_info", "arg": "body"})
        assert service.calls[-1][:2] == ("get_status", {"action": "get_login_info", "arg": "body"})
        assert (await client.get("/healthz")).json()["adapter"] == "http-server"
    assert all(call[1] is None or "access_token" not in call[1] for call in service.calls)


@pytest.mark.parametrize("secret", ["", "签名密钥"])
async def test_http_post_signature_and_quick_operation(secret):
    service = Service()
    event = {"self_id": 1234, "post_type": "message", "message_type": "private", "message": "你好"}
    requests = []

    async def receive(request):
        requests.append(request)
        return httpx.Response(200, text='{"reply":"hi"}', headers={"Content-Type": "text/plain"})

    adapter = HttpClientAdapter(HttpClientEndpoint("post", "http://test/report", "token", secret=secret),
                                service.handle_quick_operation)
    await adapter.start()
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    try:
        await adapter.send_event(event)
        await asyncio.wait_for(adapter.flush(), 2)
        assert service.operations == [(event, {"reply": "hi"})]
        request = requests[0]
        assert request.headers["X-Self-ID"] == "1234"
        assert request.headers["Authorization"] == "Bearer token"
        assert json.loads(request.content) == event
        if secret:
            assert request.headers["X-Signature"] == "sha1=" + hmac.new(secret.encode(), request.content, "sha1").hexdigest()
        else:
            assert "X-Signature" not in request.headers
    finally:
        await adapter.stop()


async def test_http_post_bad_response_does_not_stop_worker(caplog):
    service = Service()
    responses = iter([httpx.Response(200, text="bad"), httpx.Response(200, json=[]),
                      httpx.Response(204), httpx.Response(200, text="  "),
                      httpx.Response(500, json={"reply": "never"}),
                      httpx.Response(200, json={"reply": "ok"})])
    adapter = HttpClientAdapter(HttpClientEndpoint("post", "http://test", ""), service.handle_quick_operation)
    await adapter.start()
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses)))
    try:
        for i in range(6):
            await adapter.send_event({"self_id": 1234, "sequence": i})
        await asyncio.wait_for(adapter.flush(), 2)
        assert service.operations == [({"self_id": 1234, "sequence": 5}, {"reply": "ok"})]
        assert "HTTP 上报失败" in caplog.text
    finally:
        await adapter.stop()


@pytest.mark.parametrize("path", ["/", "/ws", "/ws/", "/api", "/api/", "/ws/api/"])
def test_ws_api_and_universal_echo(path):
    service = Service()
    adapter = WsServerAdapter(service, EventHub(), WsServerEndpoint("ws", "localhost", 6701, "/ws", ""))
    with TestClient(adapter.app) as client:
        with client.websocket_connect(path) as ws:
            assert adapter.hub.count == (0 if "api" in path else 1)
            if "api" not in path:
                assert ws.receive_json()["sub_type"] == "connect"
            for echo in [None, False, 0, "", [], {"sequence": 2}]:
                ws.send_json({"action": "get_status", "echo": echo})
                assert ws.receive_json()["echo"] == echo
            ws.send_json({"action": "get_status"})
            assert "echo" not in ws.receive_json()
            ws.send_json({"echo": None})
            assert ws.receive_json()["echo"] is None
            ws.send_text("{")
            assert ws.receive_json()["retcode"] == 1400


@pytest.mark.parametrize("path", ["/event", "/event/", "/ws/event"])
def test_ws_event_ignores_actions(path):
    service = Service()
    hub = EventHub()
    adapter = WsServerAdapter(service, hub, WsServerEndpoint("ws", "localhost", 6701, "/ws", ""))
    with TestClient(adapter.app) as client:
        with client.websocket_connect(path) as ws:
            assert ws.receive_json()["sub_type"] == "connect"
            ws.send_json({"action": "get_status"})
            client.portal.call(hub.broadcast, {"post_type": "notice"})
            assert ws.receive_json() == {"post_type": "notice"}
    assert service.calls == []
    assert hub.count == 0


@pytest.mark.parametrize("role", ["Universal", "API", "Event"])
async def test_reverse_ws_roles_headers_and_events(role, monkeypatch):
    service, hub = Service(), EventHub()
    sent, headers, counts = [], [], []
    connected = asyncio.Event()
    release = asyncio.Event()

    class Socket:
        async def send(self, text):
            sent.append(json.loads(text))

        async def close(self):
            pass

        async def __aiter__(self):
            connected.set()
            counts.append(hub.count)
            yield json.dumps({"action": "get_status", "echo": None})
            await release.wait()

    async def connect(url, **kwargs):
        headers.append(kwargs["additional_headers"])
        return Socket()

    monkeypatch.setattr("wechat_onebot.onebot.network.ws_client.websockets.connect", connect)
    adapter = WsClientAdapter(service, hub, WsClientEndpoint("ws", "ws://test", "token", role=role))
    adapter.start()
    try:
        await asyncio.wait_for(connected.wait(), 2)
        assert headers[0]["X-Client-Role"] == role
        assert headers[0]["X-Self-ID"] == "1234"
        assert counts == [0 if role == "API" else 1]
        if role == "API":
            assert len(sent) == 1 and sent[0]["echo"] is None
        elif role == "Event":
            assert len(sent) == 1 and sent[0]["sub_type"] == "connect"
            assert not service.calls
        else:
            assert sent[0]["sub_type"] == "connect" and sent[1]["echo"] is None
    finally:
        await adapter.stop()
    assert hub.count == 0


async def test_manager_lifecycle_order_and_rate_limit(monkeypatch):
    service = Service()
    timeline = []
    endpoint = HttpClientEndpoint("report", "http://test", "")
    config = AccountNetworkConfig(30, (), (endpoint,), (), (), rate_limit_interval_ms=250)
    network = OneBotNetworkManager(service, config)

    async def receive(request):
        event = json.loads(request.content)
        timeline.append(event.get("sub_type", "http"))
        return httpx.Response(200, json={"reply": "hi"} if event["post_type"] == "message" else {})

    # Patch just this adapter's startup, preserving the real queue and worker.
    adapter = network.http_clients[0]
    original_start = adapter.start

    async def start():
        await original_start()
        await adapter._client.aclose()
        adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(receive))

    monkeypatch.setattr(adapter, "start", start)

    async def broadcast(event):
        assert service.operations
        timeline.append("ws")

    monkeypatch.setattr(network.hub, "broadcast", broadcast)
    await network.start()
    assert service.rate_limit_interval_ms == 250
    await network.emit({"post_type": "message", "self_id": 1234})
    assert timeline == ["enable", "http", "ws"]
    await network.reconfigure(replace(config, rate_limit_interval_ms=700))
    assert service.rate_limit_interval_ms == 700
    await network.stop()
    assert timeline == ["enable", "http", "ws", "disable"]


def test_transport_config_roundtrip_and_validation():
    config = AccountNetworkConfig.from_dict({
        "httpClients": [{"name": "report", "url": "http://test", "secret": "hmac"}],
        "wsClients": [{"name": "api", "url": "ws://test", "role": "API"}],
        "rateLimitIntervalMs": 125,
    })
    assert AccountNetworkConfig.from_dict(config.to_dict()) == config
    assert config.http_clients[0].secret == "hmac"
    assert config.ws_clients[0].role == "API"
    assert AccountNetworkConfig.from_dict({}).rate_limit_interval_ms == 500
    for value in [True, -1, "500"]:
        with pytest.raises(ConfigError):
            AccountNetworkConfig.from_dict({"rateLimitIntervalMs": value})
    with pytest.raises(ConfigError):
        HttpClientEndpoint.from_dict({"name": "test", "url": "http://test", "secret": 1})
    with pytest.raises(ConfigError):
        WsClientEndpoint.from_dict({"name": "test", "url": "ws://test", "role": "wrong"})


async def test_reverse_ws_clean_close_waits_before_reconnecting(monkeypatch):
    service, hub = Service(), EventHub()
    calls = []
    second_connection = asyncio.Event()

    class Socket:
        async def send(self, text):
            pass

        async def close(self):
            pass

        async def __aiter__(self):
            if False:
                yield ""

    async def connect(url, **kwargs):
        calls.append(asyncio.get_running_loop().time())
        if len(calls) >= 2:
            second_connection.set()
        return Socket()

    monkeypatch.setattr("wechat_onebot.onebot.network.ws_client.websockets.connect", connect)
    adapter = WsClientAdapter(service, hub, WsClientEndpoint("ws", "ws://test", "", reconnect_seconds=0.05))
    adapter.start()
    try:
        await asyncio.wait_for(second_connection.wait(), 2)
        assert calls[1] - calls[0] >= 0.04
    finally:
        await adapter.stop()
    assert hub.count == 0


async def test_unresponsive_unlimited_http_report_cannot_lock_start_stop_or_removal(monkeypatch):
    from dataclasses import replace

    for remove in (False, True):
        endpoint = HttpClientEndpoint("blocked", "http://test", "", timeout_seconds=0)
        config = AccountNetworkConfig(30, (), (endpoint,), (), ())
        network = OneBotNetworkManager(Service(), config)
        adapter = network.http_clients[0]
        original_start = adapter.start
        entered = asyncio.Event()

        async def block(request):
            entered.set()
            await asyncio.Event().wait()

        async def start():
            await original_start()
            await adapter._client.aclose()
            adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(block), timeout=None)

        monkeypatch.setattr(adapter, "start", start)
        await asyncio.wait_for(network.start(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        pending_event = asyncio.create_task(network.emit({"post_type": "message", "self_id": 1234}))
        await asyncio.sleep(0)
        if remove:
            await asyncio.wait_for(network.reconfigure(replace(config, http_clients=())), 3)
        else:
            await asyncio.wait_for(network.stop(), 3)
        await asyncio.wait_for(pending_event, 1)
        assert adapter._task is None and not network._lock.locked()
        await network.stop()
