from __future__ import annotations

from pathlib import Path

import pytest

from wechat_onebot.bridge import BridgeManager
from wechat_onebot.bridge.session import MockBridgeSession
from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_a"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )


@pytest.mark.asyncio
async def test_bridge_manager_enforces_account_and_pid_isolation(tmp_path: Path):
    manager = BridgeManager(make_config(tmp_path), session_factory=lambda *_: MockBridgeSession())
    events: list[tuple[str, str, int | None]] = []

    class Listener:
        def started(self, account, session):
            events.append(("started", account, None))

        def closed(self, account, session, pid):
            events.append(("closed", account, pid))

    manager.subscribe(Listener())
    await manager.start_session("wxid_a", 111, lambda event: None)
    with pytest.raises(RuntimeError):
        await manager.start_session("wxid_a", 222, lambda event: None)
    with pytest.raises(RuntimeError):
        await manager.start_session("wxid_b", 111, lambda event: None)
    assert manager.snapshot()[0]["account"] == "wxid_a"
    await manager.stop_session("wxid_a")
    assert events == [("started", "wxid_a", None), ("closed", "wxid_a", 111)]
