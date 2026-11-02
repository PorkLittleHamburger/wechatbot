from __future__ import annotations

import json
from pathlib import Path

import pytest

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.core.runtime import AccountRuntime


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
    assert runtime.closed is True
