from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from wechat_onebot.bridge import WeChatLogin
from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.core import runtime as runtime_module
from wechat_onebot.core.runtime import AccountNotLoggedInError, AccountRuntime, RuntimeManager
from wechat_onebot.driver.base import Driver, DriverError
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.models import DriverStatus, LoginInfo


class FakeDriver(Driver):
    capabilities = frozenset({"text.receive", "text.send"})

    def __init__(self, config: AppConfig):
        self.config = config
        self._online = False

    @property
    def online(self) -> bool:
        return self._online

    async def start(self, emit) -> None:
        self._online = True

    async def stop(self) -> None:
        self._online = False

    async def send_text(self, chat_id, text):
        return True

    async def send_at_text(self, chat_id, text, user_ids, display_names=None):
        return True

    async def send_image(self, chat_id, path):
        return True

    async def send_file(self, chat_id, path):
        return True

    async def send_voice(self, chat_id, path):
        return True

    async def send_emoticon(self, chat_id, path):
        return True

    async def get_login_info(self) -> LoginInfo:
        return LoginInfo(external_user_id="wxid_robot", nickname="Robot")

    async def get_friend_list(self):
        return []

    async def get_group_list(self):
        return []

    async def get_group_member_list(self, group_id):
        return []

    async def get_status(self) -> DriverStatus:
        return DriverStatus(online=self._online)


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )


@pytest.mark.asyncio
async def test_account_runtime_uses_isolated_storage_and_is_idempotent(tmp_path: Path):
    config = make_config(tmp_path)
    account_dir = config.storage.data_dir / "accounts" / "wxid_robot"
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "onebot.json").write_text(json.dumps({
        "heartbeatIntervalSeconds": 5,
        "httpServers": [],
        "httpClients": [],
        "wsServers": [],
        "wsClients": [],
    }), encoding="utf-8")
    runtime = AccountRuntime.create(config, account="wxid_robot", pid=123)
    assert runtime.account_dir == tmp_path / "data" / "accounts" / "wxid_robot"
    assert runtime.media_dir == tmp_path / "media" / "wxid_robot"
    await runtime.start()
    assert runtime.driver.online is True
    await runtime.stop()
    await runtime.stop()
    assert runtime.driver.online is False
    assert runtime.network.started is True
    assert runtime.closed is False
    await runtime.close()
    await runtime.close()
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_network_only_runtime_rebinds_current_wechat_pid(tmp_path: Path):
    config = make_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="helper", wechat_pid=123))
    runtime = AccountRuntime.create(config, account="wxid_robot")
    try:
        assert isinstance(runtime.driver, HelperDriver)
        runtime.set_pid(456)
        assert runtime.pid == 456
        assert runtime.driver.config.driver.wechat_pid == 456
        assert runtime.service.config.driver.wechat_pid == 456
        assert runtime.started is False and runtime.driver.online is False
    finally:
        await runtime.close()


class FailingHelperDriver(FakeDriver):
    async def start(self, emit) -> None:
        raise DriverError("helper 启动失败: 目标进程登录的不是绑定的机器人账号，等待正确账号登录")


def make_manager_config(tmp_path: Path) -> AppConfig:
    config = make_config(tmp_path)
    config = replace(config, driver=replace(
        config.driver, type="helper", helper_root=tmp_path / "helper",
        wechat_account="wxid_robot", wechat_pid=None))
    config.path.write_text(json.dumps({
        "driver": {"type": "helper", "wechat_account": "wxid_robot",
                   "wechat_pid": None, "nickname": ""},
    }), encoding="utf-8")
    return config


def patch_discovery(monkeypatch, *, helper_rows=(), sdk_rows=(), diagnostics=()):
    monkeypatch.setattr(runtime_module, "discover_logged_accounts",
                        lambda root: list(helper_rows))
    monkeypatch.setattr(runtime_module, "discover_sdk_accounts",
                        lambda cfg: list(sdk_rows))
    monkeypatch.setattr(runtime_module, "sdk_discovery_report",
                        lambda cfg: (list(sdk_rows), list(diagnostics)))


def patch_driver(monkeypatch, created: list[str]):
    def factory(config: AppConfig) -> FakeDriver:
        created.append(config.driver.type)
        return FakeDriver(config)

    monkeypatch.setattr(runtime_module, "create_driver", factory)


@pytest.mark.asyncio
async def test_start_account_falls_back_to_sdk_for_unsupported_helper_build(tmp_path: Path, monkeypatch):
    config = make_manager_config(tmp_path)
    patch_discovery(
        monkeypatch,
        helper_rows=[],
        sdk_rows=[WeChatLogin(pid=31004, wxid="wxid_robot", nickname="Robot",
                              version="4.2.0.52")],
    )
    created: list[str] = []
    patch_driver(monkeypatch, created)
    manager = RuntimeManager(config)
    try:
        runtime = await manager.start_account("wxid_robot")
        assert created == ["sdk"]
        assert runtime.driver.online is True
        assert runtime.pid == 31004
        assert manager.config.driver.type == "sdk"
        stored = json.loads(config.path.read_text(encoding="utf-8"))["driver"]
        assert stored["type"] == "sdk"
        assert stored["wechat_pid"] == 31004
        assert stored["wechat_account"] == "wxid_robot"
    finally:
        await manager.stop_all()


@pytest.mark.asyncio
async def test_helper_start_rejection_falls_back_to_sdk(tmp_path: Path, monkeypatch):
    config = make_manager_config(tmp_path)
    patch_discovery(
        monkeypatch,
        helper_rows=[WeChatLogin(pid=28904, wxid="wxid_robot")],
        sdk_rows=[WeChatLogin(pid=31004, wxid="wxid_robot", nickname="墨染青花",
                              version="4.2.0.52")],
    )
    created: list[str] = []

    def factory(cfg: AppConfig) -> FakeDriver:
        created.append(cfg.driver.type)
        return FailingHelperDriver(cfg) if cfg.driver.type == "helper" else FakeDriver(cfg)

    monkeypatch.setattr(runtime_module, "create_driver", factory)
    manager = RuntimeManager(config)
    try:
        runtime = await manager.start_account("wxid_robot")
        assert created == ["helper", "sdk"]
        assert runtime.driver.online is True
        assert runtime.pid == 31004
        assert manager.config.driver.type == "sdk"
        stored = json.loads(config.path.read_text(encoding="utf-8"))["driver"]
        assert stored["type"] == "sdk"
        assert stored["wechat_pid"] == 31004
        assert stored["nickname"] == "墨染青花"
    finally:
        await manager.stop_all()


@pytest.mark.asyncio
async def test_configured_helper_driver_wins_when_it_matches(tmp_path: Path, monkeypatch):
    config = make_manager_config(tmp_path)
    patch_discovery(
        monkeypatch,
        helper_rows=[WeChatLogin(pid=123, wxid="wxid_robot")],
        sdk_rows=[WeChatLogin(pid=31004, wxid="wxid_robot", version="4.2.0.52")],
    )
    created: list[str] = []
    patch_driver(monkeypatch, created)
    manager = RuntimeManager(config)
    try:
        await manager.start_account("wxid_robot")
        assert created == ["helper"]
        assert json.loads(config.path.read_text(encoding="utf-8"))["driver"]["type"] == "helper"
    finally:
        await manager.stop_all()


@pytest.mark.asyncio
async def test_configured_sdk_driver_falls_back_to_helper(tmp_path: Path, monkeypatch):
    config = make_manager_config(tmp_path)
    config = replace(config, driver=replace(config.driver, type="sdk"))
    patch_discovery(
        monkeypatch,
        helper_rows=[WeChatLogin(pid=123, wxid="wxid_robot")],
        sdk_rows=[],
    )
    created: list[str] = []
    patch_driver(monkeypatch, created)
    manager = RuntimeManager(config)
    try:
        await manager.start_account("wxid_robot")
        assert created == ["helper"]
        assert manager.config.driver.type == "helper"
    finally:
        await manager.stop_all()


@pytest.mark.asyncio
async def test_start_account_error_reports_detected_build(tmp_path: Path, monkeypatch):
    config = make_manager_config(tmp_path)
    patch_discovery(
        monkeypatch,
        helper_rows=[],
        sdk_rows=[],
        diagnostics=[{"pid": "1", "error": "UNSUPPORTED_BUILD",
                      "version": "4.2.0.52", "detail": "no layout"}],
    )
    created: list[str] = []
    patch_driver(monkeypatch, created)
    manager = RuntimeManager(config)
    with pytest.raises(AccountNotLoggedInError, match="4.2.0.52"):
        await manager.start_account("wxid_robot")
    assert created == []
    stored = json.loads(config.path.read_text(encoding="utf-8"))["driver"]
    assert stored["type"] == "helper"
