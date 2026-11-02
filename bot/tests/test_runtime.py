from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.core.runtime import AccountRuntime
from wechat_onebot.driver.helper import HelperDriver


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
