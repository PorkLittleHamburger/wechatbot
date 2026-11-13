from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from wechat_onebot.bridge import WeChatLogin
from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.core.runtime import AccountNotLoggedInError
from wechat_onebot.webui.bridge_resume import BridgeResumeController
from wechat_onebot.webui import bridge_resume as module
from wechat_onebot.webui.startup import (last_bridge_path, load_bridge_intent,
                                        save_bridge_intent, save_last_bridge)


@pytest.fixture
def config(tmp_path):
    return AppConfig(path=tmp_path / "config.json", onebot=OneBotConfig(access_token="test"),
                     reverse_ws=(), driver=DriverConfig(type="helper", helper_root=tmp_path / "helper"),
                     storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024))


class Manager:
    def __init__(self):
        self.last_error = ""
        self.runtimes = {}
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = None

    def get(self, account):
        return self.runtimes.get(account)

    async def start_account(self, account, pid=None):
        self.calls.append(("start", account))
        self.entered.set()
        await self.release.wait()
        if self.failure:
            error, self.failure = self.failure, None
            raise error
        self.runtimes[account] = SimpleNamespace(started=True)

    async def stop_account(self, account):
        self.calls.append(("stop", account))
        current = self.get(account)
        if current:
            current.started = False


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def make_controller(config, manager, *, logged=True, enabled=True, discover=None):
    save_last_bridge(config.storage.data_dir, "robot")
    return BridgeResumeController(config, manager, enabled=enabled, check_seconds=0.005,
                                  discover=discover or (lambda _: [WeChatLogin(pid=123, wxid="robot")] if logged else []))


@pytest.mark.asyncio
async def test_helper_config_checks_sdk_discovery_before_waiting(config, monkeypatch):
    manager = Manager()
    monkeypatch.setattr(module, "SDK_PROBE_DELAY", 0.0)
    monkeypatch.setattr(module, "discover_sdk_accounts",
                        lambda cfg: [WeChatLogin(pid=456, wxid="robot")])
    controller = make_controller(config, manager, discover=lambda _: [])
    await controller.initialize()
    await controller.resume()
    await until(lambda: controller.state == "running")
    assert manager.calls == [("start", "robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_logged_in_starts_immediately_and_resume_is_idempotent(config):
    manager = Manager()
    controller = make_controller(config, manager)
    await controller.initialize()
    await controller.resume()
    await until(lambda: controller.state == "running")
    await controller.resume()
    await until(lambda: controller._task.done())
    assert manager.calls == [("start", "robot")]
    assert controller.snapshot() == {"supported": True, "version": 1, "enabled": True,
                                     "account": "robot", "desired": "running", "state": "running", "error": ""}
    await controller.close()


@pytest.mark.asyncio
async def test_only_matching_account_starts_after_login(config):
    manager = Manager()
    rows = [WeChatLogin(pid=111, wxid="other")]
    controller = make_controller(config, manager, discover=lambda _: list(rows))
    await controller.initialize()
    await asyncio.sleep(0.025)
    assert manager.calls == []
    assert controller.state == "waiting_login"
    assert not manager.last_error  # Waiting for login is a normal state, not an error toast.
    rows.append(WeChatLogin(pid=222, wxid="robot"))
    await until(lambda: controller.state == "running")
    assert manager.calls == [("start", "robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_disabled_startup_has_no_scan_or_injection(config):
    def scan(_):
        raise AssertionError("Disabled restoration must not scan")
    manager = Manager()
    controller = make_controller(config, manager, enabled=False, discover=scan)
    await controller.initialize()
    assert controller.state == "disabled"
    assert not controller.enabled
    assert manager.calls == []
    await controller.close()


@pytest.mark.asyncio
async def test_no_saved_target_does_not_guess_configured_or_logged_account(config):
    config = replace(config, driver=replace(config.driver, wechat_account="configured"))
    manager = Manager()
    controller = BridgeResumeController(config, manager, enabled=True)
    await controller.initialize()
    assert controller.state == "no_target"
    assert controller.account is None
    assert manager.calls == []
    await controller.close()


@pytest.mark.asyncio
async def test_stop_waiting_is_persistent_and_later_login_does_not_start(config):
    manager = Manager()
    rows = []
    controller = make_controller(config, manager, discover=lambda _: list(rows))
    await controller.initialize()
    await until(lambda: controller.state == "waiting_login")
    await controller.stop_account("robot")
    rows.append(WeChatLogin(pid=456, wxid="robot"))
    await asyncio.sleep(0.025)
    await controller.resume()
    assert manager.calls == [("stop", "robot")]
    assert controller.state == "stopped"
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "stopped")
    await controller.close()
    # A new process must respect the same manual stop.
    second = BridgeResumeController(config, manager, enabled=True)
    await second.initialize()
    assert second.state == "stopped"
    await second.close()


@pytest.mark.asyncio
async def test_stop_during_start_waits_for_native_work_then_stops(config):
    manager = Manager()
    manager.release.clear()
    controller = make_controller(config, manager)
    await controller.initialize()
    await manager.entered.wait()
    stop = asyncio.create_task(controller.stop_account("robot"))
    await until(lambda: controller.desired == "stopped")
    assert controller.state == "stopping"
    assert not stop.done()
    await controller.resume()  # Cannot bypass an in-progress stop.
    manager.release.set()
    await stop
    assert manager.calls == [("start", "robot"), ("stop", "robot")]
    assert controller.state == "stopped"
    assert not manager.get("robot").started
    await controller.close()


@pytest.mark.asyncio
async def test_shutdown_drains_native_start_without_clearing_saved_intent(config):
    manager = Manager()
    manager.release.clear()
    controller = make_controller(config, manager)
    await controller.initialize()
    await manager.entered.wait()
    close = asyncio.create_task(controller.close())
    await asyncio.sleep(0.01)
    assert not close.done()
    manager.release.set()
    await close
    assert manager.calls == [("start", "robot")]
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "running")
    with pytest.raises(RuntimeError, match="关闭"):
        await controller.resume()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("DLL integrity failed"), PermissionError("denied")])
async def test_native_failure_is_visible_and_not_retried_forever(config, error):
    manager = Manager()
    manager.failure = error
    controller = make_controller(config, manager)
    await controller.initialize()
    await until(lambda: controller.state == "failed")
    await asyncio.sleep(0.025)
    assert manager.calls == [("start", "robot")]
    assert controller.error == str(error) == manager.last_error
    await controller.resume()  # An explicit retry remains available.
    await until(lambda: controller.state == "running")
    assert manager.calls == [("start", "robot"), ("start", "robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_disappearance_between_discovery_and_start_returns_to_waiting(config):
    manager = Manager()
    manager.failure = AccountNotLoggedInError("logged out")
    controller = make_controller(config, manager)
    await controller.initialize()
    await until(lambda: controller.state == "running")
    assert manager.calls == [("start", "robot"), ("start", "robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_duplicate_account_identity_fails_without_native_start(config):
    manager = Manager()
    controller = make_controller(config, manager, discover=lambda _: [
        WeChatLogin(pid=1, wxid="robot"), WeChatLogin(pid=2, wxid="robot")])
    await controller.initialize()
    await until(lambda: controller.state == "failed")
    assert manager.calls == []
    assert "唯一" in controller.error
    await controller.close()


@pytest.mark.asyncio
async def test_scan_failure_is_visible_without_injection_retries(config):
    def scan(_):
        raise PermissionError("cannot inspect process")
    manager = Manager()
    controller = make_controller(config, manager, discover=scan)
    await controller.initialize()
    await until(lambda: controller.state == "failed")
    assert controller.error == "cannot inspect process"
    assert manager.calls == []
    await controller.close()


@pytest.mark.asyncio
async def test_manual_start_reenables_after_stop_and_uses_new_target(config):
    manager = Manager()
    controller = make_controller(config, manager, logged=False)
    await controller.initialize()
    await controller.stop_account("robot")
    await controller.start_account("new_robot")
    assert controller.state == "running"
    assert load_bridge_intent(config.storage.data_dir) == ("new_robot", "running")
    assert manager.calls == [("stop", "robot"), ("start", "new_robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_stopping_another_account_does_not_cancel_target_wait(config):
    manager = Manager()
    controller = make_controller(config, manager, logged=False)
    await controller.initialize()
    await controller.stop_account("other")
    assert controller.state == "waiting_login"
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "running")
    await controller.close()


@pytest.mark.asyncio
async def test_cancelled_manual_start_waiter_is_drained_before_close(config):
    manager = Manager()
    manager.release.clear()
    controller = make_controller(config, manager, enabled=False)
    await controller.initialize()
    request = asyncio.create_task(controller.start_account("robot"))
    await manager.entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    close = asyncio.create_task(controller.close())
    await asyncio.sleep(0.01)
    assert not close.done()
    manager.release.set()
    await close
    assert manager.get("robot").started
    assert manager.calls == [("start", "robot")]


@pytest.mark.asyncio
async def test_manual_stop_supersedes_inflight_manual_start(config):
    manager = Manager()
    manager.release.clear()
    controller = make_controller(config, manager, enabled=False)
    await controller.initialize()
    start = asyncio.create_task(controller.start_account("robot"))
    await manager.entered.wait()
    stop = asyncio.create_task(controller.stop_account("robot"))
    await until(lambda: controller.desired == "stopped")
    manager.release.set()
    with pytest.raises(RuntimeError, match="撤销"):
        await start
    await stop
    assert not manager.get("robot").started
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "stopped")
    await controller.close()


@pytest.mark.asyncio
async def test_stop_supersedes_manual_start_before_owned_work_runs(config):
    manager = Manager()
    controller = make_controller(config, manager, enabled=False)
    await controller.initialize()
    start = asyncio.create_task(controller.start_account("new_robot"))
    stop = asyncio.create_task(controller.stop_account("new_robot"))
    with pytest.raises(RuntimeError, match="撤销"):
        await start
    await stop
    assert manager.calls == [("stop", "new_robot")]
    assert load_bridge_intent(config.storage.data_dir) == ("new_robot", "stopped")
    await controller.close()


@pytest.mark.asyncio
async def test_stop_intent_is_not_lost_when_next_target_start_fails(config):
    manager = Manager()
    controller = make_controller(config, manager, logged=False)
    await controller.initialize()
    manager.failure = RuntimeError("new account cannot start")
    stop = asyncio.create_task(controller.stop_account("robot"))
    start = asyncio.create_task(controller.start_account("new_robot"))
    await stop
    with pytest.raises(RuntimeError, match="cannot start"):
        await start
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "stopped")
    assert controller.state == "failed"
    await controller.close()


@pytest.mark.asyncio
async def test_stop_saved_target_after_failed_switch_persists_without_altering_new_target(config):
    manager = Manager()
    controller = make_controller(config, manager, enabled=False)
    await controller.initialize()
    await controller.start_account("robot")
    manager.failure = RuntimeError("new account cannot start")
    with pytest.raises(RuntimeError, match="cannot start"):
        await controller.start_account("new_robot")
    await controller.stop_account("robot")
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "stopped")
    assert controller.account == "new_robot"
    assert controller.state == "failed"
    assert not manager.get("robot").started
    await controller.close()
    next_process = BridgeResumeController(config, manager, enabled=True)
    await next_process.initialize()
    assert next_process.state == "stopped"
    await next_process.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_cancelled_shutdown_still_drains_thread_initialization_before_runtime_cleanup(config, automatic):
    manager = Manager()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def initialize_native():
        entered.set()
        assert release.wait(3)
        finished.set()
    async def start(account, pid=None):
        manager.calls.append(("start", account))
        await asyncio.to_thread(initialize_native)
        manager.runtimes[account] = SimpleNamespace(started=True)
    manager.start_account = start
    controller = make_controller(config, manager, enabled=automatic)
    await controller.initialize()
    request = None if automatic else asyncio.create_task(controller.start_account("robot"))
    assert await asyncio.to_thread(entered.wait, 2)
    async def lifespan_shutdown():
        try:
            await controller.close()
        finally:
            assert finished.is_set()
            await manager.stop_account("robot")
    close = asyncio.create_task(lifespan_shutdown())
    try:
        await until(lambda: controller._close_task is not None)
        close.cancel()
        await asyncio.sleep(0.01)
        close.cancel()  # Repeated host cancellation must not reach the owned task.
        await asyncio.sleep(0.01)
        assert not close.done()
        assert not finished.is_set()
        assert manager.calls == [("start", "robot")]
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    if request:
        with pytest.raises(RuntimeError, match="撤销"):
            await request
    assert finished.is_set()
    assert not manager.get("robot").started
    assert manager.calls == [("start", "robot"), ("stop", "robot")]
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "running")


@pytest.mark.asyncio
async def test_stop_persistence_failure_still_stops_and_warns(config, monkeypatch):
    manager = Manager()
    controller = make_controller(config, manager, logged=False)
    await controller.initialize()
    def fail(*_):
        raise OSError("disk is read-only")
    monkeypatch.setattr(module, "save_bridge_intent", fail)
    warning = await controller.stop_account("robot")
    assert warning and "无法保存" in warning
    assert controller.state == "stopped"
    assert manager.calls == [("stop", "robot")]
    await controller.close()


@pytest.mark.asyncio
async def test_start_persistence_failure_reports_actual_running_state(config, monkeypatch):
    manager = Manager()
    controller = make_controller(config, manager, enabled=False)
    await controller.initialize()
    def fail(*_):
        raise OSError("disk is full")
    monkeypatch.setattr(module, "save_last_bridge", fail)
    warning = await controller.start_account("robot")
    assert warning and "无法保存" in warning
    assert controller.state == "running"
    assert manager.get("robot").started
    await controller.close()


@pytest.mark.parametrize("value", [[], {}, {"last_account": ""}, {"last_account": 123},
                                   {"last_account": "robot", "desired": True},
                                   {"last_account": "robot", "desired": []}])
def test_invalid_intent_is_rejected(config, value):
    path = last_bridge_path(config.storage.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_bridge_intent(config.storage.data_dir)


@pytest.mark.asyncio
async def test_malformed_state_does_not_crash_management_lifespan(config):
    path = last_bridge_path(config.storage.data_dir)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    manager = Manager()
    controller = BridgeResumeController(config, manager, enabled=True)
    await controller.initialize()
    assert controller.state == "failed"
    assert controller.error
    assert manager.calls == []
    await controller.close()


def test_legacy_last_account_defaults_to_running(config):
    path = last_bridge_path(config.storage.data_dir)
    path.parent.mkdir(parents=True)
    path.write_text('{"last_account":"robot"}', encoding="utf-8")
    assert load_bridge_intent(config.storage.data_dir) == ("robot", "running")


@pytest.mark.parametrize("desired", ["running", "stopped"])
def test_intent_roundtrip(config, desired):
    save_bridge_intent(config.storage.data_dir, "robot", desired)
    assert load_bridge_intent(config.storage.data_dir) == ("robot", desired)
