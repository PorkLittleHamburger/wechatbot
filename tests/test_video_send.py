"""U-02 outbound video: local validation, capability gates and v1 forward policy."""
from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_service import make_config, make_service
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.driver.base import DriverError, DriverUnsupported
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.service import OneBotService

ROOT = Path(__file__).resolve().parents[1]


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def write_mp4(path: Path, *, duration: int = 2) -> Path:
    """Minimal ISO-BMFF with ftyp/moov/mvhd/trak/mdia/hdlr(vide)."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x00\x00")
    mvhd = _box(b"mvhd", struct.pack(">B3xIIII", 0, 0, 0, 1, duration)
                + b"\x00" * 80)
    hdlr = _box(b"hdlr", struct.pack(">II4s", 0, 0, b"vide") + b"\x00" * 12)
    trak = _box(b"trak", _box(b"mdia", hdlr))
    moov = _box(b"moov", mvhd + trak)
    path.write_bytes(ftyp + moov)
    return path


@pytest.mark.asyncio
async def test_mock_video_only_segment_is_sent_and_stored(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    video = write_mp4(tmp_path / "clip.mp4")
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "video", "data": {"file": str(video)}}],
        })
        assert response["status"] == "ok"
        record = driver.sent[-1]
        assert record.kind == "video"
        assert Path(record.payload["path"]).is_file()
        stored = await service.dispatch("get_msg", response["data"])
        assert stored["status"] == "ok"
        segment = stored["data"]["message"][0]
        assert segment["type"] == "video"
        assert set(segment["data"]) == {"file", "url"}
        assert "video.send" in driver.capabilities
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_video_input_errors_are_explicit_and_atomic(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    oversized = tmp_path / "oversize.mp4"
    oversized.write_bytes(b"\x00" * (1024 * 1024 + 1))
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        missing = await service.dispatch("send_group_msg", {
            "group_id": group, "message": [{"type": "video", "data": {}}]})
        assert missing["status"] == "failed" and missing["retcode"] == 1404
        assert "video 段缺少 file" in missing["message"]
        for path in (empty, oversized, tmp_path / "missing.mp4"):
            response = await service.dispatch("send_group_msg", {
                "group_id": group,
                "message": [{"type": "video", "data": {"file": str(path)}}]})
            assert response["status"] == "failed" and response["retcode"] == 1400
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_video_inside_forward_extension_is_rejected_without_sends(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    video = write_mp4(tmp_path / "clip.mp4")
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        response = await service.dispatch("send_group_forward_msg", {
            "group_id": group,
            "messages": [{"type": "node", "data": {"content": [
                {"type": "text", "data": {"text": "before"}},
                {"type": "video", "data": {"file": str(video)}},
            ]}}],
        })
        assert response["status"] == "failed" and response["retcode"] == 1404
        assert "转发发送暂不支持 video 段" in response["message"]
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_helper_video_capability_is_build_gated_and_native_is_not_called(tmp_path: Path,
                                                                         monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "vendor" / "helper"))
    video = write_mp4(tmp_path / "clip.mp4")
    driver = HelperDriver(make_config(tmp_path))
    calls: list[tuple[str, str]] = []
    driver._online = True
    driver._service = SimpleNamespace(
        send_video=lambda chat, path: calls.append((chat, path)) or True)
    driver._backend = SimpleNamespace(capabilities=frozenset({"video.send"}))
    assert "video.send" in driver.capabilities
    assert await driver.send_video("10001@chatroom", str(video)) is True
    assert calls == [("10001@chatroom", str(video))]
    # 4.2.0.11-style backend does not declare video.send.
    driver._backend = SimpleNamespace(capabilities=frozenset({"text.send"}))
    assert "video.send" not in driver.capabilities
    with pytest.raises(DriverUnsupported):
        await driver.send_video("10001@chatroom", str(video))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_helper_video_dispatch_fails_1404_before_native_send(tmp_path: Path,
                                                                  monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "vendor" / "helper"))
    caller = HelperDriver(make_config(tmp_path))
    video = write_mp4(tmp_path / "clip.mp4")
    called: list[tuple[str, str]] = []
    caller._online = True
    caller._service = SimpleNamespace(
        send_video=lambda chat, path: called.append((chat, path)) or True)
    caller._backend = SimpleNamespace(capabilities=frozenset({"text.send"}))
    config = make_config(tmp_path)
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    store = MessageStore(":memory:")
    service = OneBotService(config, caller, mapper, media,
                            message_store=store, account="wxid_robot")
    service.self_id = mapper.get_or_create("user", "wxid_robot")
    group = mapper.get_or_create("group", "10001@chatroom")
    try:
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "video", "data": {"file": str(video)}}]})
        assert response["status"] == "failed" and response["retcode"] == 1404
        assert "不支持发送视频" in response["message"]
        assert called == []
    finally:
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_helper_42012_video_route_sends_once(tmp_path: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "vendor" / "helper"))
    driver = HelperDriver(make_config(tmp_path))
    video = write_mp4(tmp_path / "clip.mp4")
    calls: list[tuple[str, str]] = []
    driver._online = True
    driver._service = SimpleNamespace(
        send_video=lambda chat, path: calls.append((chat, path)) or True)
    driver._backend = SimpleNamespace(capabilities=frozenset({"video.send"}))
    config = make_config(tmp_path)
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    store = MessageStore(":memory:")
    service = OneBotService(config, driver, mapper, media,
                            message_store=store, account="wxid_robot")
    service.self_id = mapper.get_or_create("user", "wxid_robot")
    group = mapper.get_or_create("group", "10001@chatroom")
    try:
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "video", "data": {"file": str(video)}}]})
        assert response["status"] == "ok"
        assert calls == [("10001@chatroom", str(video))]
    finally:
        mapper.close()
        store.close()


@pytest.mark.asyncio
async def test_helper_validate_media_rejects_non_mp4(tmp_path: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "vendor" / "helper"))
    driver = HelperDriver(make_config(tmp_path))
    driver._online = True
    driver._service = SimpleNamespace()
    valid = write_mp4(tmp_path / "clip.mp4")
    invalid = tmp_path / "not-video.mp4"
    invalid.write_bytes(b"not-an-mp4")
    await driver.validate_media("video", str(valid))
    with pytest.raises(ValueError):
        await driver.validate_media("video", str(invalid))


@pytest.mark.asyncio
async def test_video_send_failure_keeps_explicit_1500(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    video = write_mp4(tmp_path / "clip.mp4")

    async def unsupported(chat_id: str, path: str) -> bool:
        raise DriverUnsupported("当前微信构建的 helper 桥接不支持发送视频")

    driver.send_video = unsupported
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "video", "data": {"file": str(video)}}]})
        assert response["status"] == "failed" and response["retcode"] == 1404
        assert "不支持发送视频" in response["message"]
    finally:
        await service.stop()
        mapper.close()
