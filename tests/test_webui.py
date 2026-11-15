from __future__ import annotations

import json
import logging
import socket
import threading
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
from wechat_onebot.webui import startup as webui_startup
from wechat_onebot.core import runtime as runtime_module
import pytest


API_TOKEN = "Xk9mP2vL8qT4wZ7c"
ROTATED_TOKEN = "Rt7Vb3Nc9Mx2Kp5Q"


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
        assert client.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
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
        assert webui_startup.load_last_bridge(config.storage.data_dir) == "wxid_robot"
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
                                   "path": "/", "accessToken": API_TOKEN, "enabled": True, "maxBodyBytes": 104857600}]
        added = client.put(base, json=payload).json()
        assert added["saved"] is True and added["applied"] is True
        assert manager.get("wxid_robot") is runtime and runtime.driver is driver and driver.online
        first_adapter = runtime.network.http_servers[0]
        with httpx.Client(base_url=f"http://127.0.0.1:{http_port}", timeout=3) as http:
            response = http.post("/", headers={"Authorization": "Bearer " + API_TOKEN},
                                 json={"action": "get_status"})
            assert response.json()["status"] == "ok"

        payload["wsServers"] = [{"name": "events", "host": "127.0.0.1", "port": ws_port,
                                 "path": "/ws", "accessToken": API_TOKEN, "enabled": True, "maxMessageBytes": 134217728}]
        assert client.put(base, json=payload).json()["applied"] is True
        assert runtime.network.http_servers[0] is first_adapter
        assert runtime.driver is driver and driver.online
        with connect(f"ws://127.0.0.1:{ws_port}/ws?access_token={API_TOKEN}") as ws:
            assert json.loads(ws.recv(timeout=3))["meta_event_type"] == "lifecycle"
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                bad_port = occupied.getsockname()[1]
                invalid = {**payload, "httpServers": payload["httpServers"] + [
                    {"name": "busy", "host": "127.0.0.1", "port": bad_port,
                     "path": "/", "accessToken": API_TOKEN, "enabled": True, "maxBodyBytes": 104857600}]}
                failed = client.put(base, json=invalid).json()
                assert failed["saved"] is False and failed["applied"] is False
            assert runtime.network.http_servers[0] is first_adapter
            assert runtime.driver is driver and driver.online
            assert client.get(base).json() == payload

            payload["httpServers"][0]["accessToken"] = ROTATED_TOKEN
            assert client.put(base, json=payload).json()["applied"] is True
            assert runtime.network.http_servers[0] is not first_adapter
            with httpx.Client(base_url=f"http://127.0.0.1:{http_port}", timeout=3) as http:
                assert http.post("/", headers={"Authorization": "Bearer " + API_TOKEN},
                                 json={"action": "get_status"}).status_code == 403
                assert http.post("/", headers={"Authorization": "Bearer " + ROTATED_TOKEN},
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
                                   "path": "/", "accessToken": API_TOKEN, "enabled": True, "maxBodyBytes": 104857600}]
        payload["wsServers"] = [{"name": "offline-ws", "host": "127.0.0.1", "port": ws_port,
                                 "path": "/ws", "accessToken": API_TOKEN, "enabled": True, "maxMessageBytes": 134217728}]
        saved = client.put("/api/accounts/wxid_robot/network", json=payload).json()
        assert saved["saved"] is True and saved["applied"] is True
        runtime = manager.get("wxid_robot")
        assert runtime.started is False and runtime.network.started is True
        assert runtime.service.self_id > 0
        with (httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as http,
              connect(f"ws://127.0.0.1:{ws_port}/ws?access_token={API_TOKEN}") as ws):
            assert json.loads(ws.recv(timeout=3))["meta_event_type"] == "lifecycle"
            def online():
                response = http.post("/", headers={"Authorization": "Bearer " + API_TOKEN},
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
            response = http.post("/", headers={"Authorization": "Bearer " + API_TOKEN},
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


def test_sdk_driver_process_scan_uses_framework_discovery(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="sdk", wechat_pid=321))
    manager = RuntimeManager(config)
    calls = []

    def discover_sdk(cfg):
        calls.append(cfg)
        return [WeChatLogin(pid=321, wxid="wxid_robot", nickname="SDK Robot",
                            version="4.2.0.52")]

    monkeypatch.setattr(webui_server, "discover_sdk_accounts", discover_sdk)
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        login_and_set_password(client)
        scanned = client.get("/api/processes?scan=true").json()
    assert len(calls) == 1
    row = scanned["processes"][0]
    assert row["pid"] == 321
    assert row["nickname"] == "SDK Robot"
    assert row["configured"] is True


def test_sdk_driver_account_selection_uses_framework_discovery(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="sdk", wechat_account="", wechat_pid=None))
    config.path.write_text(json.dumps({"driver": {"type": "sdk", "wechat_account": None,
                                                  "wechat_pid": None, "nickname": ""}}), encoding="utf-8")
    manager = RuntimeManager(config)
    monkeypatch.setattr(webui_server, "discover_sdk_accounts", lambda cfg: [
        WeChatLogin(pid=654, wxid="wxid_sdk", nickname="SDK Login")])
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        login_and_set_password(client)
        response = client.post("/api/account-selection", json={"account": "wxid_sdk"})
        assert response.status_code == 200
        assert response.json()["account"] == "wxid_sdk"
        stored = json.loads(config.path.read_text(encoding="utf-8"))
        assert stored["driver"]["wechat_account"] == "wxid_sdk"
        assert stored["driver"]["wechat_pid"] == 654


def test_process_scan_keeps_known_nickname_when_discovery_name_is_empty(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper", wechat_account="wxid_robot",
                                            wechat_pid=123, nickname="墨染青花"))
    manager = RuntimeManager(config)
    monkeypatch.setattr(webui_server, "discover_logged_accounts",
                        lambda root: [WeChatLogin(pid=123, wxid="wxid_robot", nickname="")])
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        login_and_set_password(client)
        row = client.get("/api/processes?scan=true").json()["processes"][0]
    assert row["account"] == "wxid_robot"
    assert row["nickname"] == "墨染青花"


def test_helper_config_selection_falls_back_to_sdk_account(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper",
                                            wechat_account="", wechat_pid=None))
    config.path.write_text(json.dumps({"driver": {"type": "helper", "wechat_account": None,
                                                  "wechat_pid": None, "nickname": ""}}), encoding="utf-8")
    manager = RuntimeManager(config)
    monkeypatch.setattr(webui_server, "discover_logged_accounts", lambda root: [])
    monkeypatch.setattr(webui_server, "discover_sdk_accounts", lambda cfg: [
        WeChatLogin(pid=654, wxid="wxid_sdk", nickname="SDK Login")])
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        login_and_set_password(client)
        response = client.post("/api/account-selection", json={"account": "wxid_sdk"})
        assert response.status_code == 200
        assert response.json()["account"] == "wxid_sdk"
        assert response.json()["pid"] == 654
        stored = json.loads(config.path.read_text(encoding="utf-8"))
        assert stored["driver"]["type"] == "helper"
        assert stored["driver"]["wechat_account"] == "wxid_sdk"
        assert stored["driver"]["wechat_pid"] == 654


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
        assert client.get("/api/status").json()["configuredNickname"] == "Selected"
        assert client.get("/api/status").json()["configuredPid"] == 456
        assert manager.snapshot() == []
        monkeypatch.setattr(manager, "snapshot", lambda: [
            {"account": "wxid_robot", "pid": 123, "started": False, "online": False},
            {"account": "wxid_selected", "pid": None, "started": False, "online": False},
        ])
        rows = {row["account"]: row for row in client.get("/api/processes").json()["processes"]}
        assert rows["wxid_selected"]["configured"] is True
        assert rows["wxid_selected"]["pid"] == 456
        assert rows["wxid_selected"]["nickname"] == "Selected"
        assert rows["wxid_robot"]["configured"] is False
        saved = json.loads(config.path.read_text(encoding="utf-8"))["driver"]
        assert (saved["wechat_account"], saved["wechat_pid"], saved["nickname"]) == (
            "wxid_selected", 456, "Selected")


def test_autostart_setting_and_last_bridge_are_managed_through_webui(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper"))
    manager = RuntimeManager(config)
    enabled = False
    actions = []

    def task(action):
        nonlocal enabled
        actions.append(action)
        if action == "install":
            enabled = True
        elif action == "remove":
            enabled = False
        return {"enabled": enabled, "managed": enabled, "resumesBridge": enabled}

    monkeypatch.setattr(webui_server, "autostart_task", task)
    webui_startup.save_last_bridge(config.storage.data_dir, "wxid_robot")
    with TestClient(create_webui_app(config, manager)) as client:
        assert client.get("/api/autostart").status_code == 401
        login_and_set_password(client)
        assert client.post("/api/autostart", json={"enabled": "yes"}).status_code == 400
        initial = client.get("/api/autostart").json()
        assert initial == {"enabled": False, "managed": False, "resumesBridge": False,
                           "lastAccount": "wxid_robot"}
        assert client.post("/api/autostart", json={"enabled": True}).json()["enabled"] is True
        assert client.post("/api/autostart", json={"enabled": False}).json()["enabled"] is False
        assert actions == ["status", "install", "remove"]


def test_autostart_resumes_last_bridged_account(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    webui_startup.save_last_bridge(config.storage.data_dir, "wxid_previous")
    resumed = threading.Event()
    accounts = []

    async def start_account(account, pid=None):
        accounts.append(account)
        resumed.set()

    monkeypatch.setattr(manager, "start_account", start_account)
    with TestClient(create_webui_app(config, manager, start_runtime=True)) as client:
        assert client.get("/healthz").status_code == 200
        assert resumed.wait(2)
    assert accounts == ["wxid_previous"]


def test_autostart_connects_as_soon_as_last_account_logs_in(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper"))
    manager = RuntimeManager(config)
    webui_startup.save_last_bridge(config.storage.data_dir, "wxid_previous")
    monkeypatch.setattr(webui_server, "RESUME_CHECK_SECONDS", 0.02)
    scans = []
    started = threading.Event()
    accounts = []

    def scan(_root):
        scans.append(1)
        if len(scans) < 3:
            return []
        return [WeChatLogin(pid=456, wxid="wxid_previous", nickname="Previous")]

    async def start_account(account, pid=None):
        accounts.append(account)
        started.set()

    monkeypatch.setattr(webui_server, "discover_logged_accounts", scan)
    monkeypatch.setattr(manager, "start_account", start_account)
    with TestClient(create_webui_app(config, manager, start_runtime=True)) as client:
        assert started.wait(1)
        assert client.get("/healthz").json()["lastError"] == ""
    assert len(scans) == 3
    assert accounts == ["wxid_previous"]


def test_waiting_resume_is_authenticated_cancellable_and_survives_restart(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper"))
    webui_startup.save_last_bridge(config.storage.data_dir, "wxid_previous")
    monkeypatch.setattr(webui_server, "discover_logged_accounts", lambda _: [])
    monkeypatch.setattr(webui_server, "RESUME_CHECK_SECONDS", 0.01)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager, start_runtime=True)
    with TestClient(app) as client:
        assert client.post("/api/accounts/wxid_previous/stop").status_code == 401
        assert app.state.bridge_resume.snapshot()["desired"] == "running"
        login_and_set_password(client)
        snapshot = client.get("/api/status").json()["bridgeResume"]
        assert snapshot["state"] == "waiting_login"
        assert snapshot["supported"] and snapshot["version"] == 1
        assert client.post("/api/accounts/wxid_previous/stop").json() == {"ok": True}
        assert client.get("/api/status").json()["bridgeResume"]["state"] == "stopped"
    second = create_webui_app(config, RuntimeManager(config), start_runtime=True)
    with TestClient(second):
        assert second.state.bridge_resume.snapshot()["state"] == "stopped"
        assert second.state.bridge_resume.snapshot()["account"] == "wxid_previous"


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


def test_gateway_log_level_and_file_endpoints(tmp_path: Path):
    from wechat_onebot import logging_setup

    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    old_level = logger.level
    logging_setup._close_file_handler()
    try:
        assert logging_setup.configure_logging(config)["enabled"] is True
        with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
            assert client.get("/api/logs/level").status_code == 401
            assert client.get("/api/logs/file").status_code == 401
            assert client.get("/api/logs/download").status_code == 401
            login_and_set_password(client)
            payload = client.get("/api/logs/level").json()
            assert payload["level"] == "INFO"
            assert payload["levels"] == list(logging_setup.LOG_LEVELS)
            assert payload["persisted"] is False
            assert client.post("/api/logs/level", json={"level": "VERBOSE"}).status_code == 400
            assert client.post("/api/logs/level", json={}).status_code == 400
            updated = client.post("/api/logs/level", json={"level": "DEBUG"}).json()
            assert updated == {"ok": True, "level": "DEBUG", "previous": "INFO",
                               "persisted": False}
            logging.getLogger("wechat-onebot.network").debug(
                "OneBot 动作失败: %s", "probe detail")
            tail = client.get("/api/logs/file", params={"tail": 10}).json()
            assert tail["exists"] is True
            assert any("OneBot 动作失败" in line for line in tail["lines"])
            assert client.get("/api/logs/file", params={"tail": 0}).status_code == 400
            assert client.get("/api/logs/file", params={"tail": "nope"}).status_code == 400
            status = client.get("/api/status").json()
            assert status["logLevel"] == "DEBUG"
            assert status["logFile"].endswith("gateway.log")
            assert status["logFileEnabled"] is True
            download = client.get("/api/logs/download")
            assert download.status_code == 200
            assert "OneBot 动作失败" in download.text
    finally:
        logging_setup._close_file_handler()
        logger.setLevel(old_level)


def test_friend_requests_endpoint_is_readonly_and_masks_flags(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    request_id = "fr_" + "a1b2c3d4" * 4
    with TestClient(create_webui_app(config, manager, start_runtime=False)) as client:
        assert client.get("/api/accounts/wxid_robot/friend-requests").status_code == 401
        login_and_set_password(client)
        missing = client.get("/api/accounts/wxid_robot/friend-requests")
        assert missing.status_code == 400 and missing.json()["ok"] is False
        assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
        runtime = manager.get("wxid_robot")
        runtime.driver.friend_request_rows = [{
            "request_id": request_id, "sender_id": "wxid_alice", "nickname": "Alice",
            "verification": "请通过", "scene": 10, "status": "pending",
            "ticket": "SECRET-TICKET", "encryptusername": "SECRET-ENC",
            "raw_xml": "<xml>SECRET</xml>",
        }]
        response = client.get("/api/accounts/wxid_robot/friend-requests")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        row = payload["requests"][0]
        assert row["flag"].endswith("…") and row["flag"] != request_id
        assert request_id not in response.text
        assert "SECRET-TICKET" not in response.text
        assert "encryptusername" not in response.text and "ticket" not in response.text
        assert "approve" not in row and "action" not in row
        assert client.get("/api/accounts/wxid_robot/friend-requests",
                          params={"count": 0}).status_code == 400
        assert client.get("/api/accounts/wxid_robot/friend-requests",
                          params={"count": 101}).status_code == 400
        assert client.get("/api/accounts/wxid_robot/friend-requests",
                          params={"count": "nope"}).status_code == 400


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


def test_exposure_hardening_headers_docs_and_body_limits(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    with TestClient(create_webui_app(config, manager)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        for path in ("/", "/healthz", "/api/status"):
            response = client.get(path)
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        login_and_set_password(client)
        # Login/credential bodies are capped at 16 KiB.
        too_large_login = client.post(
            "/api/login", headers={"Content-Type": "application/json"},
            content=b'{"password":"' + b"a" * 17_000 + b'"}')
        assert too_large_login.status_code == 413
        assert too_large_login.headers["x-frame-options"] == "DENY"
        # Other JSON APIs are capped at 1 MiB.
        too_large_api = client.post(
            "/api/account-selection", headers={"Content-Type": "application/json"},
            content=b'{"account":"' + b"a" * (1024 * 1024) + b'"}')
        assert too_large_api.status_code == 413
        # The OneBot action proxy keeps the 16 MiB media exception.
        action_body = (b'{"action":"get_status","padding":"'
                       + b"a" * (2 * 1024 * 1024) + b'"}')
        action = client.post("/api/accounts/wxid_robot/action",
                             headers={"Content-Type": "application/json"},
                             content=action_body)
        assert action.status_code == 200
        assert action.json()["status"] == "failed" and action.json()["retcode"] == 1404


def test_login_rate_limit_window_and_success_reset(tmp_path: Path):
    from wechat_onebot.webui.rate_limit import LoginRateLimiter

    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    config = make_config(tmp_path)
    app = create_webui_app(config, RuntimeManager(config), login_limiter=limiter)
    with TestClient(app) as client:
        for _ in range(4):
            assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200
        for _ in range(4):
            assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200
        for _ in range(5):
            assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        blocked = client.post("/api/login", json={"token": "webui-token"})
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "60"
        now[0] += 61
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200


def test_token_save_guard_for_loopback_and_weak_tokens(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    base = "/api/accounts/wxid_robot/network"
    with TestClient(create_webui_app(config, manager),
                    client=("127.0.0.1", 5555)) as client:
        login_and_set_password(client)
        payload = client.get(base).json()
        payload["httpServers"] = [{
            "name": "api", "host": "127.0.0.1", "port": 6700, "path": "/",
            "accessToken": API_TOKEN, "enabled": False, "maxBodyBytes": 104857600,
        }]
        assert client.put(base, json=payload).json()["saved"] is True
        # A non-loopback bind cannot drop its token, even from a local client.
        payload["httpServers"][0]["host"] = "0.0.0.0"
        payload["httpServers"][0]["accessToken"] = ""
        rejected = client.put(base, json=payload).json()
        assert rejected["saved"] is False and "非 loopback" in rejected["message"]
        # Loopback bind + loopback client may clear the token.
        payload["httpServers"][0]["host"] = "127.0.0.1"
        assert client.put(base, json=payload).json()["saved"] is True
        assert client.get(base).json()["httpServers"][0]["maxBodyBytes"] == 104857600
        # Moving an existing empty-token endpoint to a non-loopback bind is
        # rejected even though the token itself did not change.
        payload["httpServers"][0]["host"] = "0.0.0.0"
        host_change = client.put(base, json=payload).json()
        assert host_change["saved"] is False and "非 loopback" in host_change["message"]
        payload["httpServers"][0]["host"] = "127.0.0.1"
        # Weak changed tokens are rejected; unchanged/rotated strong ones pass.
        payload["httpServers"][0]["accessToken"] = "onebot-token"
        weak = client.put(base, json=payload).json()
        assert weak["saved"] is False and "至少需要 16" in weak["message"]
        payload["httpServers"][0]["accessToken"] = ROTATED_TOKEN
        assert client.put(base, json=payload).json()["saved"] is True
    # A non-loopback management client cannot save an empty token either.
    payload["httpServers"][0]["accessToken"] = ""
    with TestClient(create_webui_app(config, RuntimeManager(config))) as client:
        assert client.post("/api/login", json={
            "password": "correct horse battery staple"}).status_code == 200
        rejected = client.put(base, json=payload).json()
        assert rejected["saved"] is False and "非 loopback" in rejected["message"]
