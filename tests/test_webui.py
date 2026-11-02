from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

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


def test_webui_login_status_and_account_action(tmp_path: Path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200
        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["ok"] is True
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
        assert client.get("/api/status").json()["runtimes"] == []
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
        assert saved.json()["applied"] is False
        changed = {**network.json(), "heartbeatIntervalSeconds": 7}
        assert client.put("/api/accounts/wxid_robot/network", json=changed).json()["saved"] is True
        assert client.get("/api/accounts/wxid_robot/network").json()["heartbeatIntervalSeconds"] == 7
        invalid = client.put("/api/accounts/wxid_robot/network", json={"httpServers": [{"name": "bad", "port": 0}]})
        assert invalid.status_code == 200
        assert invalid.json()["saved"] is False


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
        assert client.post("/api/login", json={"token": "webui-token"}).status_code == 200
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
        client.post("/api/login", json={"token": "webui-token"})
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
        client.post("/api/login", json={"token": "webui-token"})
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

        async def start(self):
            pass

        async def stop(self):
            pass

    fake = FakeRuntime()
    monkeypatch.setattr(runtime_module.AccountRuntime, "create", classmethod(
        lambda _cls, _config, *, account, pid: (observed.append((account, pid)), fake)[1]))
    manager = RuntimeManager(config)
    assert await manager.start_account("wxid_robot", pid=123) is fake
    assert observed == [("wxid_robot", 456)]
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
            client.post("/api/login", json={"token": "webui-token"})
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
