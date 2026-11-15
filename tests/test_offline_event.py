"""U-04 ``bot_offline``: local bridge-loss signal, not a network verdict."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_service import make_config
from wechat_onebot.config import AppConfig
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.driver.sdk import SdkDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.models import DriverOffline
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.onebot.network.config import AccountNetworkConfig, WsServerEndpoint
from wechat_onebot.onebot.network.manager import OneBotNetworkManager
from wechat_onebot.service import OneBotService


async def make_offline_service(tmp_path: Path, *, with_sink: bool = True):
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    store = MessageStore(":memory:")
    service = OneBotService(config, driver, mapper, media,
                            message_store=store, account="wxid_robot")
    events: list[dict] = []
    if with_sink:
        async def sink(event: dict) -> None:
            events.append(event)
        service.set_event_sink(sink)
    await service.start()
    return config, service, driver, mapper, store, events


@pytest.mark.asyncio
async def test_bot_offline_is_emitted_once_and_status_matches(tmp_path: Path):
    _, service, driver, mapper, store, events = await make_offline_service(tmp_path)
    try:
        await driver.go_offline("helper_disconnected", "receive thread stopped")
        notice = events[-1]
        assert notice["post_type"] == "notice"
        assert notice["notice_type"] == "bot_offline"
        assert notice["user_id"] == notice["self_id"] == service.self_id
        assert notice["tag"] == "helper_disconnected"
        assert "本地失联信号" in notice["message"]
        status = (await service.dispatch("get_status"))["data"]
        assert status["online"] is False
        assert service.heartbeat_event()["status"]["online"] is False
        count = len(events)
        # A repeated offline signal while already offline stays a single notice.
        await service._on_driver_event(DriverOffline("sdk_session_failed"))
        assert len(events) == count
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_unknown_offline_tag_is_sanitized(tmp_path: Path):
    _, service, driver, mapper, store, events = await make_offline_service(tmp_path)
    try:
        await driver.go_offline("server_kicked", "unmapped")
        assert events[-1]["tag"] == "driver_stopped"
        assert "driver_stopped" == events[-1]["tag"]
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_external_driver_recovery_rearms_the_next_offline_transition(tmp_path: Path):
    """A driver that is online again re-arms the gate; a heartbeat cannot recover it.

    The mock ``go_online`` hook simulates a restarted/externally recovered driver;
    the gateway's heartbeat only reports/propagates the already-true state.
    """
    _, service, driver, mapper, store, events = await make_offline_service(tmp_path)
    try:
        await driver.go_offline("helper_disconnected")
        assert len(events) == 1
        await driver.go_online()
        heartbeat = service.heartbeat_event()
        assert heartbeat["status"]["online"] is True
        assert len(events) == 1  # Recovery has no separate online notice.
        await driver.go_offline("sdk_session_failed")
        assert len(events) == 2
        assert events[-1]["tag"] == "sdk_session_failed"
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_normal_stop_and_restart_do_not_emit_bot_offline(tmp_path: Path):
    _, service, driver, mapper, store, events = await make_offline_service(tmp_path)
    try:
        await service.stop()
        assert events == []
        await service.start()
        assert driver.online is True
        await driver.go_offline("helper_disconnected")
        assert len(events) == 1  # Restart resets the online→offline gate.
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_missing_or_failing_event_sink_never_breaks_offline_handling(tmp_path: Path):
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    store = MessageStore(":memory:")
    service = OneBotService(config, driver, mapper, media,
                            message_store=store, account="wxid_robot")
    try:
        await service.start()
        await driver.go_offline("sdk_session_failed")
        recent = service.recent_events(10)
        assert any(row["notice_type"] == "bot_offline" for row in recent)

        async def broken_sink(event: dict) -> None:
            raise RuntimeError("adapter failed")

        service.set_event_sink(broken_sink)
        await service.emit_internal_event({"post_type": "notice", "notice_type": "test"})
        assert service.recent_events(1)[-1]["notice_type"] == "test"
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_bot_offline_reaches_forward_websocket_clients(tmp_path: Path):
    from fastapi.testclient import TestClient

    config, service, driver, mapper, store, _ = await make_offline_service(
        tmp_path, with_sink=False)
    network = OneBotNetworkManager(service, AccountNetworkConfig(
        heartbeat_interval_seconds=30.0,
        http_servers=(), http_clients=(),
        ws_servers=(WsServerEndpoint("forward", "127.0.0.1", 6701, "/ws", ""),),
        ws_clients=(),
    ))
    service.set_event_sink(network.emit)
    try:
        with TestClient(network.ws_servers[0].app) as client:
            with client.websocket_connect("/ws") as websocket:
                assert websocket.receive_json()["meta_event_type"] == "lifecycle"
                await driver.go_offline("sdk_session_failed", "PROCESS_EXITED")
                notice = websocket.receive_json()
                assert notice["notice_type"] == "bot_offline"
                assert notice["tag"] == "sdk_session_failed"
    finally:
        await service.stop()
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_helper_watchdog_reports_dead_receive_thread(tmp_path: Path):
    driver = HelperDriver(make_config(tmp_path))
    events = []

    async def emit(event) -> None:
        events.append(event)

    class DeadThread:
        def is_alive(self) -> bool:
            return False

    driver._online = True
    driver._emit = emit
    driver._backend = SimpleNamespace(raw=SimpleNamespace(last_error=None, _thread=DeadThread()))
    driver.OFFLINE_WATCH_SECONDS = 0.01
    await asyncio.wait_for(driver._watch_offline(), 2)
    assert driver.online is False
    assert len(events) == 1
    assert isinstance(events[0], DriverOffline)
    assert events[0].reason == "helper_disconnected"


@pytest.mark.asyncio
async def test_helper_error_signal_and_normal_stop(tmp_path: Path):
    driver = HelperDriver(make_config(tmp_path))

    class AliveThread:
        def is_alive(self) -> bool:
            return True

    driver._online = True
    driver._backend = SimpleNamespace(
        raw=SimpleNamespace(last_error=RuntimeError("native receive failed"), _thread=AliveThread()))
    assert driver._offline_reason() == ("helper_disconnected", "native receive failed")
    # Normal stop clears online before the receive thread exits: no signal.
    driver._online = False
    assert driver._offline_reason() is None
    events = []

    async def emit(event) -> None:
        events.append(event)

    driver._emit = emit
    await driver._watch_offline()
    assert events == []


@pytest.mark.asyncio
async def test_helper_stop_cancels_offline_watchdog(tmp_path: Path):
    driver = HelperDriver(make_config(tmp_path))
    events = []

    async def emit(event) -> None:
        events.append(event)

    class AliveThread:
        def is_alive(self) -> bool:
            return True

    driver._online = True
    driver._emit = emit
    driver._backend = SimpleNamespace(raw=SimpleNamespace(last_error=None, _thread=AliveThread()))
    driver.OFFLINE_WATCH_SECONDS = 5.0
    driver._offline_task = asyncio.create_task(driver._watch_offline())
    await asyncio.sleep(0)
    await driver._cancel_offline_task()
    assert driver._offline_task is None
    assert events == []


def _sdk_driver(tmp_path: Path, *, emit_events: list) -> SdkDriver:
    driver = SdkDriver(make_config(tmp_path))
    driver._online = True

    async def emit(event) -> None:
        emit_events.append(event)

    driver._emit = emit
    driver._loop = asyncio.get_running_loop()
    return driver


@pytest.mark.asyncio
async def test_sdk_consumer_failure_marks_offline_and_reports_once(tmp_path: Path):
    events: list = []
    driver = _sdk_driver(tmp_path, emit_events=events)

    class EndedSubscription:
        def get(self, timeout=None):
            raise StopIteration

    driver._subscription = EndedSubscription()
    driver._session = SimpleNamespace(status=lambda: SimpleNamespace(
        state="failed", last_error=SimpleNamespace(value="PROCESS_EXITED")))
    await asyncio.to_thread(driver._consume)
    await asyncio.sleep(0.05)
    assert driver.online is False
    assert len(events) == 1
    assert isinstance(events[0], DriverOffline)
    assert events[0].reason == "process_exited"
    driver._report_session_failed()
    await asyncio.sleep(0.05)
    assert len(events) == 1


@pytest.mark.asyncio
async def test_sdk_normal_stop_does_not_report_offline(tmp_path: Path):
    events: list = []
    driver = _sdk_driver(tmp_path, emit_events=events)
    driver._stop.set()
    driver._report_session_failed()
    await asyncio.sleep(0.05)
    assert events == []


@pytest.mark.asyncio
async def test_sdk_account_changed_maps_to_its_tag(tmp_path: Path):
    events: list = []
    driver = _sdk_driver(tmp_path, emit_events=events)
    driver._session = SimpleNamespace(status=lambda: SimpleNamespace(
        state="failed", last_error=SimpleNamespace(value="ACCOUNT_CHANGED")))
    driver._report_session_failed()
    await asyncio.sleep(0.05)
    assert len(events) == 1 and events[0].reason == "account_changed"
