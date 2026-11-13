from __future__ import annotations

import wave
from pathlib import Path

import pytest

from test_service import make_service
from wechat_onebot import audio


def write_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * int(rate * seconds))
    return path


def music(audio_url: str = "", url: str = "", title: str = "Song") -> dict:
    return {"type": "music", "data": {
        "type": "custom", "title": title, "url": url, "audio": audio_url,
    }}


@pytest.mark.asyncio
async def test_overlong_record_is_delivered_as_a_file(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        source = write_wav(tmp_path / "long.wav", 61)
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "record", "data": {"file": str(source)}}],
        })
        assert response["status"] == "ok", response
        assert driver.sent[-1].kind == "file"
        assert Path(driver.sent[-1].payload["path"]) == source
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_astrbot_style_base64_overlong_record_falls_back_to_file(tmp_path: Path):
    import base64

    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        source = write_wav(tmp_path / "long.wav", 61)
        payload = "base64://" + base64.b64encode(source.read_bytes()).decode("ascii")
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "record", "data": {"file": payload}}],
        })
        assert response["status"] == "ok", response
        delivered = Path(driver.sent[-1].payload["path"])
        assert driver.sent[-1].kind == "file"
        assert delivered.read_bytes() == source.read_bytes()
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_custom_music_uses_voice_for_short_audio_and_file_for_long(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        user = mapper.get_or_create("user", "wxid_alice")
        short = write_wav(tmp_path / "short.wav", 0.2)
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": [music(audio_url=str(short))]})
        assert response["status"] == "ok", response
        assert driver.sent[-1].kind == "voice"
        assert Path(driver.sent[-1].payload["path"]).read_bytes().startswith(
            audio.TENCENT_SILK_HEADER)
        long = write_wav(tmp_path / "long.wav", 61)
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": [music(audio_url=str(long))]})
        assert response["status"] == "ok", response
        assert driver.sent[-1].kind == "file"
        assert Path(driver.sent[-1].payload["path"]) == long
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_music_without_audio_degrades_to_a_standard_link(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [music(url="https://example.invalid/song", title="标题")]})
        assert response["status"] == "ok", response
        assert driver.sent[-1].kind == "text"
        assert "标题" in driver.sent[-1].payload["text"]
        assert "https://example.invalid/song" in driver.sent[-1].payload["text"]
        failed = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "music", "data": {"type": "qq", "id": "1"}}]})
        assert failed["status"] == "failed" and failed["retcode"] == 1404
    finally:
        await service.stop()
        mapper.close()
