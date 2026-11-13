from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from test_service import make_service


def write_wav(path: Path, seconds: float = 0.2, rate: int = 24000) -> Path:
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(
            struct.pack("<h", int(2500 * math.sin(2 * math.pi * 440 * index / rate)))
            for index in range(frames)))
    return path


@pytest.mark.asyncio
async def test_get_record_decodes_stored_silk_before_export(tmp_path: Path):
    from wechat_onebot import audio

    service, _, mapper, _ = await make_service(tmp_path)
    try:
        source = write_wav(tmp_path / "received.wav")
        silk = audio.ensure_silk(source, cache_dir=tmp_path / "voice-cache")
        record = service.media.register(silk)
        response = await service.dispatch("get_record", {"file": record.media_id,
                                                          "out_format": "ogg"})
        assert response["status"] == "ok", response
        output = Path(response["data"]["file"])
        assert output.is_file() and output.suffix == ".ogg" and output.stat().st_size > 0
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_get_image_returns_registered_media_path(tmp_path: Path):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        image = tmp_path / "received.png"
        image.write_bytes(b"fake-png-for-registry")
        record = service.media.register(image)
        response = await service.dispatch("get_image", {"file": record.media_id})
        assert response["status"] == "ok"
        assert Path(response["data"]["file"]).read_bytes() == image.read_bytes()
        missing = await service.dispatch("get_image", {"file": "not-registered"})
        assert missing["status"] == "failed" and missing["retcode"] == 1404
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("out_format", ["mp3", "amr"])
async def test_get_record_converts_registered_voice_to_requested_format(tmp_path: Path, out_format):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        source = write_wav(tmp_path / "received.wav")
        record = service.media.register(source)
        response = await service.dispatch("get_record", {"file": record.media_id,
                                                          "out_format": out_format})
        assert response["status"] == "ok", response
        output = Path(response["data"]["file"])
        assert output.is_file() and output.suffix == f".{out_format}" and output.stat().st_size > 0
        unsupported = await service.dispatch("get_record", {"file": record.media_id,
                                                             "out_format": "exe"})
        assert unsupported["status"] == "failed" and unsupported["retcode"] == 1400
        missing = await service.dispatch("get_record", {"file": "not-registered",
                                                         "out_format": "wav"})
        assert missing["status"] == "failed" and missing["retcode"] == 1404
    finally:
        await service.stop()
        mapper.close()
