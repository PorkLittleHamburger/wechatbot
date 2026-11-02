from __future__ import annotations

import json
import logging
import socket
from dataclasses import replace
from pathlib import Path

import httpx
from websockets.sync.client import connect
from fastapi.testclient import TestClient

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig, WebUiConfig
from wechat_onebot.core.runtime import RuntimeManager
from wechat_onebot.webui import create_webui_app
from wechat_onebot.bridge.models import WeChatLogin
from wechat_onebot.webui import server as webui_server
from wechat_onebot.core import runtime as runtime_module
import pytest


def make_config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
        webui=WebUiConfig(access_token="webui-token"),
    )
    account_dir = config.storage.data_dir / "accounts" / "wxid_robot"
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "onebot.json").write_text(json.dumps({
        "heartbeatIntervalSeconds": 5,
        "httpServers": [],
        "httpClients": [],
        "wsServers": [],
        "wsClients": [],
    }), encoding="utf-8")
    return config


def login_and_set_password(client: TestClient) -> None:
    assert client.get("/api/auth-state").json() == {"setupRequired": True}
    login = client.post("/api/login", json={"token": "webui-token"})
    assert login.status_code == 200
    assert login.json()["setupRequired"] is True
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/setup-password", json={"password": "correct horse battery staple"}).status_code == 200
    assert client.get("/api/auth-state").json() == {"setupRequired": False}


def test_webui_login_status_and_account_action(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        assert manager.snapshot() == []
        login_and_set_password(client)
        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["ok"] is True
        assert payload["runtimes"] == []
        started = client.post("/api/accounts/wxid_robot/start")
        assert started.status_code == 200
        assert started.json()["ok"] is True
        payload = client.get("/api/status").json()
        assert payload["runtimes"][0]["account"] == "wxid_robot"
        assert payload["runtimes"][0]["network"]["httpServers"] == []
        process = client.get("/api/processes").json()["processes"][0]
        assert process["account"] == "wxid_robot"
        assert process["configured"] is True
        assert process["running"] is True
        assert process["started"] is True
        assert process["online"] is True
        with client.stream("GET", "/api/events") as stream:
            assert stream.status_code == 200
            first = next(stream.iter_lines())
            assert first.startswith("data: ")
        action = client.post("/api/accounts/wxid_robot/action", json={"action": "get_login_info"})
        assert action.status_code == 200
        assert action.json()["status"] == "ok"
        stopped = client.post("/api/accounts/wxid_robot/stop")
        assert stopped.status_code == 200
        offline = client.get("/api/status").json()["runtimes"][0]
        assert offline["started"] is False
        assert offline["online"] is False
        assert offline["network"]["started"] is True
        started = client.post("/api/accounts/wxid_robot/start")
        assert started.status_code == 200
        assert started.json()["ok"] is True
        assert client.get("/api/status").json()["runtimes"][0]["online"] is True
        events = client.get("/api/accounts/wxid_robot/events")
        assert events.status_code == 200
        assert events.json()["events"] == []
        storage = client.get("/api/accounts/wxid_robot/storage")
        assert storage.status_code == 200
        assert storage.json()["messages"]["message_count"] == 0
        network = client.get("/api/accounts/wxid_robot/network")
        assert network.status_code == 200
        assert network.json()["httpServers"] == []
        saved = client.put("/api/accounts/wxid_robot/network", json=network.json())
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        assert saved.json()["applied"] is True
        changed = {**network.json(), "heartbeatIntervalSeconds": 7}
        assert client.put("/api/accounts/wxid_robot/network", json=changed).json()["saved"] is True
        assert client.get("/api/accounts/wxid_robot/network").json()["heartbeatIntervalSeconds"] == 7
        invalid = client.put("/api/accounts/wxid_robot/network", json={"httpServers": [{"name": "bad", "port": 0}]})
        assert invalid.status_code == 200
        assert invalid.json()["saved"] is False


def test_network_save_applies_without_restarting_account(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    with TestClient(create_webui_app(config, manager)) as client:
        login_and_set_password(client)
        assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
        runtime = manager.get("wxid_robot")
        driver = runtime.driver
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            http_port = sock.getsockname()[1]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            ws_port = sock.getsockname()[1]
        base = "/api/accounts/wxid_robot/network"
        payload = client.get(base).json()
        payload["httpServers"] = [{"name": "api", "host": "127.0.0.1", "port": http_port,
                                   "path": "/", "accessToken": "onebot-token", "enabled": True}]
        added = client.put(base, json=payload).json()
        assert added["saved"] is True and added["applied"] is True
        assert manager.get("wxid_robot") is runtime and runtime.driver is driver and driver.online
        first_adapter = runtime.network.http_servers[0]
        with httpx.Client(base_url=f"http://127.0.0.1:{http_port}", timeout=3) as http:
            response = http.post("/", headers={"Authorization": "Bearer onebot-token"},
                                 json={"action": "get_status"})
            assert response.json()["status"] == "ok"

        payload["wsServers"] = [{"name": "events", "host": "127.0.0.1", "port": ws_port,
                                 "path": "/ws", "accessToken": "onebot-token", "enabled": True}]
        assert client.put(base, json=payload).json()["applied"] is True
        assert runtime.network.http_servers[0] is first_adapter
        assert runtime.driver is driver and driver.online
        with connect(f"ws://127.0.0.1:{ws_port}/ws?access_token=onebot-token") as ws:
            assert json.loads(ws.recv(timeout=3))["meta_event_type"] == "lifecycle"
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                bad_port = occupied.getsockname()[1]
                invalid = {**payload, "httpServers": payload["httpServers"] + [
                    {"name": "busy", "host": "127.0.0.1", "port": bad_port,
                     "path": "/", "accessToken": "onebot-token", "enabled": True}]}
                failed = client.put(base, json=invalid).json()
                assert failed["saved"] is False and failed["applied"] is False
            assert runtime.network.http_servers[0] is first_adapter
            assert runtime.driver is driver and driver.online
            assert client.get(base).json() == payload

            payload["httpServers"][0]["accessToken"] = "rotated-token"
            assert client.put(base, json=payload).json()["applied"] is True
            assert runtime.network.http_servers[0] is not first_adapter
            with httpx.Client(base_url=f"http://127.0.0.1:{http_port}", timeout=3) as http:
                assert http.post("/", headers={"Authorization": "Bearer onebot-token"},
                                 json={"action": "get_status"}).status_code == 401
                assert http.post("/", headers={"Authorization": "Bearer rotated-token"},
                                 json={"action": "get_status"}).json()["status"] == "ok"
            assert runtime.driver is driver and driver.online

            payload["httpServers"] = []
            assert client.put(base, json=payload).json()["applied"] is True
            assert runtime.network.http_servers == []
            assert runtime.network.ws_servers[0].started is True
            assert runtime.driver is driver and driver.online
            ws.send(json.dumps({"action": "get_status", "echo": "after-update"}))
            response = json.loads(ws.recv(timeout=3))
            assert response["status"] == "ok" and response["echo"] == "after-update"


def test_network_endpoint_runs_before_and_after_wechat_runtime(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        ws_port = sock.getsockname()[1]
    with TestClient(create_webui_app(config, manager)) as client:
        login_and_set_password(client)
        assert manager.snapshot() == []
        payload = client.get("/api/accounts/wxid_robot/network").json()
        payload["httpServers"] = [{"name": "offline-api", "host": "127.0.0.1", "port": port,
                                   "path": "/", "accessToken": "onebot-token", "enabled": True}]
        payload["wsServers"] = [{"name": "offline-ws", "host": "127.0.0.1", "port": ws_port,
                                 "path": "/ws", "accessToken": "onebot-token", "enabled": True}]
        saved = client.put("/api/accounts/wxid_robot/network", json=payload).json()
        assert saved["saved"] is True and saved["applied"] is True
        runtime = manager.get("wxid_robot")
        assert runtime.started is False and runtime.network.started is True
        assert runtime.service.self_id > 0
        with (httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as http,
              connect(f"ws://127.0.0.1:{ws_port}/ws?access_token=onebot-token") as ws):
            assert json.loads(ws.recv(timeout=3))["meta_event_type"] == "lifecycle"
            def online():
                response = http.post("/", headers={"Authorization": "Bearer onebot-token"},
                                     json={"action": "get_status"})
                assert response.json()["status"] == "ok"
                return response.json()["data"]["online"]

            assert online() is False
            assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
            assert manager.get("wxid_robot") is runtime and online() is True
            assert client.post("/api/accounts/wxid_robot/stop").json()["ok"] is True
            assert manager.get("wxid_robot") is runtime and online() is False
            assert runtime.network.http_servers[0].started is True
            ws.send(json.dumps({"action": "get_status", "echo": "after-stop"}))
            response = json.loads(ws.recv(timeout=3))
            assert response["echo"] == "after-stop"
            assert response["data"]["online"] is False

    restored = RuntimeManager(config)
    with TestClient(create_webui_app(config, restored)):
        runtime = restored.get("wxid_robot")
        assert runtime is not None and runtime.started is False
        assert runtime.network.http_servers[0].started is True
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as http:
            response = http.post("/", headers={"Authorization": "Bearer onebot-token"},
                                 json={"action": "get_status"})
            assert response.json()["data"]["online"] is False


def test_webui_password_persists_and_replaces_bootstrap_token(tmp_path: Path):
    config = make_config(tmp_path)
    with TestClient(create_webui_app(config, RuntimeManager(config))) as client:
        assert client.post("/api/setup-password", json={"password": "password123"}).status_code == 401
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200
        assert client.post("/api/setup-password", json={"password": "short"}).status_code == 400
        assert client.post("/api/setup-password", json={"password": "correct horse battery staple"}).status_code == 200
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 401
        assert client.post("/api/logout").status_code == 200
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "correct horse battery staple"}).status_code == 200
        assert client.post("/api/change-password", json={"currentPassword": "wrong", "newPassword": "new password"}).status_code == 401
        assert client.post("/api/change-password", json={"currentPassword": "correct horse battery staple", "newPassword": "new password"}).status_code == 200
        assert client.get("/api/status").status_code == 200
    saved = (config.storage.data_dir / "webui-auth.json").read_text(encoding="utf-8")
    assert "new password" not in saved
    with TestClient(create_webui_app(config, RuntimeManager(config))) as client:
        assert client.get("/api/auth-state").json() == {"setupRequired": False}
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 401
        assert client.post("/api/login", json={"password": "correct horse battery staple"}).status_code == 401
        assert client.post("/api/login", json={"password": "new password"}).status_code == 200
        assert client.get("/api/status").status_code == 200


def test_read_only_processes_auth_snapshot_and_explicit_scan(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper", wechat_pid=123))
    manager = RuntimeManager(config)
    calls = []

    def discover(root):
        calls.append(root)
        return [WeChatLogin(pid=123, wxid="wxid_robot", nickname="Robot"),
                WeChatLogin(pid=456, wxid="wxid_other", nickname="Other")]

    monkeypatch.setattr(webui_server, "discover_logged_accounts", discover)
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        assert client.get("/api/processes?scan=true").status_code == 401
        login_and_set_password(client)
        response = client.get("/api/processes")
        assert response.status_code == 200
        assert response.json() == {"processes": [{"account": "wxid_robot", "pid": 123,
                                                   "nickname": "", "configured": True,
                                                   "running": False, "online": False, "started": False}]}
        assert calls == []
        scanned = client.get("/api/processes?scan=true").json()
        assert len(calls) == 1
        assert scanned == {"processes": [
            {"account": "wxid_robot", "pid": 123, "nickname": "Robot", "configured": True,
             "running": False, "online": False, "started": False},
            {"account": "wxid_other", "pid": 456, "nickname": "Other", "configured": False,
             "running": False, "online": False, "started": False},
        ]}


def test_process_scan_error_retains_configured_account(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper"))
    manager = RuntimeManager(config)

    def fail_scan(_root):
        raise RuntimeError("secret-token and local path")

    monkeypatch.setattr(webui_server, "discover_logged_accounts", fail_scan)
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        login_and_set_password(client)
        response = client.get("/api/processes?scan=true")
        assert response.status_code == 200
        assert response.json()["error"] == "只读账号扫描失败"
        assert response.json()["processes"][0]["account"] == "wxid_robot"
        assert "secret-token" not in response.text


def test_webui_selects_fresh_logged_in_account_without_starting_runtime(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper", wechat_account="", wechat_pid=None))
    config.path.write_text(json.dumps({"driver": {"type": "helper", "wechat_account": None,
                                                 "wechat_pid": None, "nickname": ""}}), encoding="utf-8")
    manager = RuntimeManager(config)
    monkeypatch.setattr(webui_server, "discover_logged_accounts", lambda _root: [
        WeChatLogin(pid=456, wxid="wxid_selected", nickname="Selected")])
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        assert client.post("/api/account-selection", json={"account": "wxid_selected"}).status_code == 401
        login_and_set_password(client)
        assert client.post("/api/account-selection", json={"account": "wxid_missing"}).status_code == 409
        selected = client.post("/api/account-selection", json={"account": "wxid_selected"})
        assert selected.status_code == 200
        assert selected.json() == {"ok": True, "account": "wxid_selected", "pid": 456,
                                   "nickname": "Selected"}
        assert client.get("/api/status").json()["configuredAccount"] == "wxid_selected"
        assert manager.snapshot() == []
        saved = json.loads(config.path.read_text(encoding="utf-8"))["driver"]
        assert (saved["wechat_account"], saved["wechat_pid"], saved["nickname"]) == (
            "wxid_selected", 456, "Selected")


@pytest.mark.asyncio
async def test_runtime_uses_current_account_pid_after_reboot(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper", wechat_pid=123))
    monkeypatch.setattr(runtime_module, "discover_logged_accounts", lambda _root: [
        WeChatLogin(pid=456, wxid="wxid_robot")])
    observed = []

    class FakeRuntime:
        account = "wxid_robot"
        started = False

        class network:
            @staticmethod
            async def start():
                pass

        def set_pid(self, pid):
            observed.append(pid)

        async def start(self):
            self.started = True

        async def stop(self):
            self.started = False

        async def close(self):
            pass

    fake = FakeRuntime()
    monkeypatch.setattr(runtime_module.AccountRuntime, "create", classmethod(
        lambda _cls, _config, *, account, network_config=None: fake))
    manager = RuntimeManager(config)
    assert await manager.start_account("wxid_robot", pid=123) is fake
    assert observed == [456]
    await manager.stop_all()


def test_logs_auth_bounded_and_redacted(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    logger = logging.getLogger("wechat-onebot.network.ws-client")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
            assert client.get("/api/logs").status_code == 401
            login_and_set_password(client)
            for index in range(webui_server.LOG_LIMIT + 5):
                logger.warning("HTTP 上报失败 %s: %s", "secret-token", f"raw message {index}")
            logs = client.get("/api/logs").json()["logs"]
            assert len(logs) == webui_server.LOG_LIMIT
            assert set(logs[-1]) == {"time", "level", "scope", "message"}
            assert logs[-1]["level"] == "WARNING"
            assert logs[-1]["scope"] == "network.ws-client"
            assert logs[-1]["message"] == "HTTP 上报失败"
            assert "secret-token" not in str(logs)
            assert "raw message" not in str(logs)
            assert logs[0]["time"] <= logs[-1]["time"]
            logger.exception("unlisted private content: %s", "secret-token")
            assert client.get("/api/logs").json()["logs"][-1]["message"] == "运行异常"
    finally:
        logger.setLevel(old_level)
