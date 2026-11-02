from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_restart_waits_until_previous_bridge_has_stopped(tmp_path: Path):
    stopping = asyncio.Event()
    release = asyncio.Event()

    class SlowSession(MockBridgeSession):
        async def stop(self):
            stopping.set()
            await release.wait()
            await super().stop()

    manager = BridgeManager(make_config(tmp_path), session_factory=lambda *_: SlowSession())
    await manager.start_session("wxid_a", 111, lambda event: None)
    stop_task = asyncio.create_task(manager.stop_session("wxid_a"))
    await asyncio.wait_for(stopping.wait(), 1)
    start_task = asyncio.create_task(manager.start_session("wxid_a", 111, lambda event: None))
    await asyncio.sleep(0)
    assert not start_task.done()
    release.set()
    await asyncio.wait_for(stop_task, 1)
    restarted = await asyncio.wait_for(start_task, 1)
    assert restarted.session.online
    await manager.stop_session("wxid_a")


@pytest.mark.asyncio
async def test_failed_stop_keeps_pid_reserved_for_retry(tmp_path: Path):
    class FailingSession(MockBridgeSession):
        fail_once = True

        async def stop(self):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("cleanup busy")
            await super().stop()

    manager = BridgeManager(make_config(tmp_path), session_factory=lambda _, account, __:
                            FailingSession() if account == "wxid_a" else MockBridgeSession())
    await manager.start_session("wxid_a", 111, lambda event: None)
    with pytest.raises(RuntimeError, match="cleanup busy"):
        await manager.stop_session("wxid_a")
    assert manager.get("wxid_a") is not None
    with pytest.raises(RuntimeError, match="PID"):
        await manager.start_session("wxid_b", 111, lambda event: None)
    await manager.stop_session("wxid_a")
    await manager.start_session("wxid_b", 111, lambda event: None)
    await manager.stop_session("wxid_b")
