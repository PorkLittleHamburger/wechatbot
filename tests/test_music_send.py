from __future__ import annotations

import base64
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


def voice_paths(driver) -> list[Path]:
    return [Path(row.payload["path"]) for row in driver.sent if row.kind == "voice"]


def assert_chunked_voice(driver, *, minimum_chunks: int, minimum_total_ms: int) -> list[Path]:
    paths = voice_paths(driver)
    assert len(paths) >= minimum_chunks
    durations = []
    for path in paths:
        data = path.read_bytes()
        assert data.startswith(audio.TENCENT_SILK_HEADER)
        _, duration = audio._parse_silk(data)
        assert 0 < duration <= 60_000
        durations.append(duration)
    assert sum(durations) >= minimum_total_ms
    return paths


@pytest.mark.asyncio
async def test_overlong_record_is_split_into_voice_bubbles(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        source = write_wav(tmp_path / "long.wav", 61)
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "record", "data": {"file": str(source)}}],
        })
        assert response["status"] == "ok", response
        assert_chunked_voice(driver, minimum_chunks=2, minimum_total_ms=60_000)
        assert all(row.kind == "voice" for row in driver.sent)
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_astrbot_style_base64_overlong_record_is_split(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        service.media.max_bytes = 4 * 1024 * 1024
        source = write_wav(tmp_path / "long.wav", 125)
        payload = "base64://" + base64.b64encode(source.read_bytes()).decode("ascii")
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "record", "data": {"file": payload}}],
        })
        assert response["status"] == "ok", response
        assert_chunked_voice(driver, minimum_chunks=3, minimum_total_ms=120_000)
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_custom_music_uses_voice_for_short_audio_and_chunks_for_long(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        user = mapper.get_or_create("user", "wxid_alice")
        short = write_wav(tmp_path / "short.wav", 0.2)
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": [music(audio_url=str(short))]})
        assert response["status"] == "ok", response
        assert len(voice_paths(driver)) == 1
        driver.sent.clear()
        long = write_wav(tmp_path / "long.wav", 61)
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": [music(audio_url=str(long))]})
        assert response["status"] == "ok", response
        assert_chunked_voice(driver, minimum_chunks=2, minimum_total_ms=60_000)
        assert all(row.kind == "voice" for row in driver.sent)
    finally:
        await service.stop()
        mapper.close()


def write_stereo_wav(path: Path, seconds: float, rate: int = 44100) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0\0\0" * int(rate * seconds))
    return path


@pytest.mark.asyncio
async def test_high_bitrate_stereo_record_is_downsampled_and_split(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        service.media.max_bytes = 20 * 1024 * 1024
        source = write_stereo_wav(tmp_path / "stereo.wav", 61)
        assert source.stat().st_size > 10 * 1024 * 1024
        response = await service.dispatch("send_private_msg", {
            "user_id": mapper.get_or_create("user", "wxid_alice"),
            "message": [{"type": "record", "data": {"file": str(source)}}],
        })
        assert response["status"] == "ok", response
        assert_chunked_voice(driver, minimum_chunks=2, minimum_total_ms=60_000)
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
